"""Generate, audit and prioritize GC Missions conversion work in one pass."""
import argparse
from collections import Counter, defaultdict
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
import subprocess
import sys

from dependencies import database_project, installed_version
from review import COBRA_WORLD, cobra_aircraft_loadout_changes, known_compile_failure, known_zagoria_failure
from scenery import StockPrefabs
from vehicle_crew import ZAGORIA_BMP2


REVIEWED_SUCCESSORS = {
    ('6730D59067916E3D', '1.0.46'): {
        'legacy': '1EF865DC4E3F7E0B',
        'replacement': ('D0C221AB104B740A', 'IFV/BMP2/BMP2_Olive_taloshatchtest.et'),
        'packageSha256': 'bcb942a6d915851f5f252fcac2248a15c493e651068167090334740fec41aee6',
        'gameVersion': '1.9.0.37',
        'findings': [
            'The two turret stations appear in reverse runtime order.',
            'The hull inventory retains the magazine counts but changes the armor-piercing round effects.',
            'The 1,500-health Main physical hit zone is absent.',
        ],
        'sessions': ['logs_2026-11-22_20-03-55', 'logs_2026-11-22_20-04-06'],
    },
    ('DEADC0DE00000003', '1.6.1'): {
        'legacy': '04227AB09A9C86D2',
        'replacement': ('34FC742220830895',
                        'Prefabs/Vehicles/Wheeled/TIGR/AGS/GAZ_TIGR_v2_AGS17_PAG17_Tan.et'),
        'packageSha256': 'bb6919f63ab887967c20d224a6fdd67395d4cc142d6ecaf3efdb69e66f78c859',
        'comparisonBasis': 'a prefab and native comparison against Tactical Flava 1.3.5',
        'gameVersion': '1.9.0.37',
        'findings': [
            'The tan turret and its three inherited prefab bodies match the archive.',
            'The hull changes affiliation from USSR to TF_RF.',
            'Both variants expose the same seven named compartments in the same order.',
            'The archived hull starts with 219 of 267 litres across three nodes. The current hull starts with 146 of 178 across two.',
            'The current inherited rear-left passenger point has the same local orientation as the explicit archived point.',
            'Driving and vehicle interactions remain unverified.',
        ],
        'sessions': ['logs_2026-11-23_08-16-44', 'logs_2026-11-23_08-16-53'],
    },
}


def run_tool(name, args):
    script = Path(__file__).with_name(name + '.py')
    subprocess.run([sys.executable, '-B', str(script), *map(str, args)], check=True)


def specific_blocker(blocker):
    rule = blocker['rule']
    detail = blocker.get('detail')
    if not isinstance(detail, str) or not detail:
        return rule
    detail = re.sub(r'^line \d+ ', '', detail)
    if detail.startswith('game mode field or child requires a conversion rule: '):
        field = detail.rsplit(': ', 1)[1].split()[0]
        return f'{rule} · {field}'
    if detail.startswith('unresolved or unsupported outcome flag '):
        return f'{rule} · unresolved flag producer'
    return f'{rule} · {detail}'


def runtime_checks(drafts):
    remaining = {gap for draft in drafts for gap in draft['behaviorGaps']}
    checks = []
    while remaining:
        draft = min(drafts, key=lambda entry: (
            -len(remaining.intersection(entry['behaviorGaps'])), entry['world'], entry['launchers'][0]))
        covered = remaining.intersection(draft['behaviorGaps'])
        checks.append({'world': draft['world'], 'launcher': draft['launchers'][0],
                       'checks': sorted(covered)})
        remaining.difference_update(covered)
    return checks


def resource_recovery_queue(resources, held):
    """Rank resource leads by the complete world drafts they could release."""
    references = {f"{{{entry['guid']}}}{entry['path']}": entry for entry in resources}
    single_asset_worlds = defaultdict(set)
    affected_worlds = defaultdict(set)
    for draft in held:
        missing = {issue for issue in draft['issues'] if issue in references}
        for reference in missing:
            affected_worlds[reference].add(draft['world'])
        other_issues = [issue for issue in draft['issues'] if issue not in missing
                        and not issue.endswith(' — Unresolved external resource references')]
        if len(missing) == 1 and not other_issues:
            single_asset_worlds[next(iter(missing))].add(draft['world'])
    return [
        {'reference': reference, 'worlds': sorted(affected_worlds[reference]),
         'onlyMissingAsset': sorted(single_asset_worlds[reference]),
         'registeredInDevGraph': any(entry['declaredDependency'] for entry in
                                    references[reference]['registrations']),
         'alternateRegisteredPaths': [entry for entry in references[reference]['registrations']
                                      if entry['path'] != references[reference]['path']]}
        for reference in sorted(affected_worlds, key=lambda ref: (
            -len(single_asset_worlds[ref]), -len(affected_worlds[ref]), ref))
    ]


def dependency_recovery_queue(resources, held):
    """Find installed packages that could resolve several exact references together."""
    providers_by_ref = {
        f"{{{entry['guid']}}}{entry['path']}": {
            registration['provider'] for registration in entry['registrations']
            if registration['path'] == entry['path'] and not registration['declaredDependency']
        }
        for entry in resources
    }
    packages = {}
    for draft in held:
        blockers = {issue for issue in draft['issues']
                    if not issue.endswith(' — Unresolved external resource references')}
        references_by_provider = defaultdict(set)
        for reference in blockers:
            for provider in providers_by_ref.get(reference, ()):
                references_by_provider[provider].add(reference)
        for provider, references in references_by_provider.items():
            package = packages.setdefault(provider, {
                'provider': provider, 'references': set(), 'worlds': [], 'onlyMissingPackage': [],
            })
            package['references'].update(references)
            package['worlds'].append(draft['world'])
            if references == blockers:
                package['onlyMissingPackage'].append(draft['world'])
    for package in packages.values():
        package['references'] = sorted(package['references'])
    return sorted(packages.values(), key=lambda package: (
        -len(package['onlyMissingPackage']), -len(package['worlds']), package['provider']))


ROSTER_RESOURCE_BLOCKER = re.compile(
    r'SCR_AIGroup · (?:unresolved or duplicate Units prefab|unresolved character prefab) ([0-9A-F]{16})')


def conversion_resource_queue(resources, loadout_parents, headers, blockers_by_world):
    """Match unresolved roster checks to exact legacy resource references."""
    worlds_by_header = {header: row.get('destinationWorld', '').partition('}')[2]
                        for header, row in headers.items()}
    candidates = defaultdict(set)
    for entry in resources:
        reference = f"{{{entry['guid']}}}{entry['path']}"
        for header in entry['launchers']:
            candidates[worlds_by_header.get(header), entry['guid']].add(reference)

    affected = defaultdict(set)
    sole = defaultdict(set)
    for world, blockers in blockers_by_world.items():
        for blocker in blockers:
            match = ROSTER_RESOURCE_BLOCKER.fullmatch(blocker)
            if not match or len(refs := candidates[world, match[1]]) != 1:
                continue
            reference = next(iter(refs))
            affected[reference].add(world)
            if len(blockers) == 1:
                sole[reference].add(world)

    for parent in loadout_parents:
        reference = f"{{{parent['guid']}}}{parent['path']}"
        affected[reference].update(parent['worlds'])
        sole[reference].update(world for world, result in parent['worlds'].items()
                               if result['onlyReportedBlocker'])
    return [{'reference': ref, 'worlds': sorted(affected[ref]), 'onlyReportedBlocker': sorted(sole[ref])}
            for ref in sorted(affected, key=lambda ref: (-len(sole[ref]), -len(affected[ref]), ref))]


def historical_provider_candidates(plan, roots, base):
    """Find exact and alternate archived registrations for known missing mission inputs."""
    drafts = {entry['reference']: entry['worlds'] for entry in plan['resourceRecoveryQueue']}
    zagoria = f"{{{ZAGORIA_BMP2[0]}}}{ZAGORIA_BMP2[1]}"
    failed_zagoria_worlds = {entry['world'] for entry in plan['heldDrafts']
                             if any('fails the verified drivetrain test' in issue for issue in entry['issues'])}
    if failed_zagoria_worlds:
        drafts[zagoria] = sorted(set(drafts.get(zagoria, ())) | failed_zagoria_worlds)
    conversion = {entry['reference']: entry for entry in plan.get('conversionResourceQueue', ())}
    references = drafts.keys() | conversion.keys()
    if not references:
        return []
    held = {}
    for entry in plan['heldDrafts']:
        held[entry['world']] = {
            issue for issue in entry['issues'] if not issue.endswith(' — Unresolved external resource references')
        }
    databases = {path.resolve() for root in roots for path in root.glob('*/resourceDatabase.rdb')}
    candidates = []
    for database in sorted(databases):
        stock = StockPrefabs(database)
        matched = set()
        alternates = []
        for ref in sorted(references):
            identity, path = ref[1:17], ref[18:]
            if stock.contains(identity, path):
                matched.add(ref)
                continue
            paths = stock.registered_paths(identity)
            identities = stock.registered_ids(path)
            if paths or identities:
                alternates.append({'reference': ref, 'sameIdPaths': paths, 'samePathIds': identities})
        provider = database_project(database)
        if not provider:
            continue
        addon_id, dependencies = provider
        if not matched and not alternates and not any(addon_id == reviewed for reviewed, _ in REVIEWED_SUCCESSORS):
            continue
        if not (version := installed_version(database, addon_id)):
            continue
        successors = []
        if (spec := REVIEWED_SUCCESSORS.get((addon_id, version))) and stock.contains(*spec['replacement']):
            reference = next((ref for ref in sorted(references) if ref[1:17] == spec['legacy']), None)
            package = database.with_name('data.pak')
            if reference and package.is_file():
                with package.open('rb') as archive:
                    verified = file_digest(archive, 'sha256').hexdigest() == spec['packageSha256']
                if verified:
                    identity, path = spec['replacement']
                    related = {launcher for lead in plan.get('sourceDependencyLeads', ())
                               if lead['guid'] == spec['legacy'] for launcher in lead['launchers']}
                    related.update(launcher for draft in plan['heldDrafts'] if draft['world'] in drafts.get(reference, ())
                                   for launcher in draft['launchers'])
                    successors.append({
                        'reference': reference, 'replacement': f'{{{identity}}}{path}',
                        'sourceLaunchers': sorted(related), 'findings': spec['findings'],
                        **{key: spec[key] for key in ('gameVersion', 'sessions', 'comparisonBasis') if key in spec},
                        'packageSha256': spec['packageSha256'],
                    })
        if not matched and not alternates and not successors:
            continue
        runtime_blocked = {zagoria} if zagoria in matched and known_zagoria_failure(base, [database]) else set()
        compile_failure = known_compile_failure(base, [database])
        if compile_failure:
            runtime_blocked = matched
        cobra_references = {ref for ref in matched if COBRA_WORLD in drafts.get(ref, ())}
        loadout_changes = cobra_aircraft_loadout_changes([database], {ref[1:17] for ref in cobra_references})
        changed_references = {ref: loadout_changes[ref[1:17]] for ref in sorted(cobra_references)
                              if ref[1:17] in loadout_changes}
        usable_matches = matched - runtime_blocked
        exact_worlds = {world for ref in matched for world in drafts.get(ref, ())}
        conversion_worlds = {world for ref in matched for world in conversion.get(ref, {}).get('worlds', ())}
        sole_conversion = {world for ref in usable_matches
                           for world in conversion.get(ref, {}).get('onlyReportedBlocker', ())
                           if world != COBRA_WORLD or ref not in changed_references}
        review_worlds = {world for entry in alternates for world in (
            *drafts.get(entry['reference'], ()), *conversion.get(entry['reference'], {}).get('worlds', ()))}
        candidates.append({
            'addonId': addon_id, 'version': version, 'projectDirectory': str(database.parent),
            'databaseSha256': sha256(stock.data).hexdigest(), 'directDependencies': list(dependencies),
            'exactReferences': sorted(matched), 'worlds': sorted(exact_worlds),
            'conversionWorlds': sorted(conversion_worlds), 'onlyReportedConversionBlocker': sorted(sole_conversion),
            'alternateRegistrations': alternates, 'reviewWorlds': sorted(review_worlds),
            **({'reviewedSuccessors': successors} if successors else {}),
            'runtimeBlockedReferences': sorted(runtime_blocked),
            **({'loadoutChanges': changed_references} if changed_references else {}),
            **({'runtimeFailure': compile_failure} if compile_failure else {}),
            'onlyMissingPackage': sorted(world for world in exact_worlds if held[world] <= usable_matches
                                         and (world != COBRA_WORLD or not held[world] & changed_references.keys())),
        })
    return sorted(candidates, key=lambda row: (
        -len(row['onlyMissingPackage']), -len(row['onlyReportedConversionBlocker']),
        -len(row['worlds']), -len(row['conversionWorlds']), row['addonId'],
        row['version'], row['projectDirectory']))


def compare_provider_plans(baseline, candidate):
    if any(baseline[key] != candidate[key] for key in ('source', 'target')):
        raise ValueError('provider comparison requires matching legacy and Dev mission revisions')

    def ready_drafts(plan):
        return {header for key in ('suggestedBatch', 'additionalDrafts')
                for draft in plan[key] for header in draft['launchers']}

    before = set(baseline['committedLauncherMetadata'])
    after = set(candidate['committedLauncherMetadata'])
    before_drafts, after_drafts = ready_drafts(baseline), ready_drafts(candidate)
    return {
        'committedGains': sorted(after - before),
        'committedLosses': sorted(before - after),
        'draftGains': sorted(after_drafts - before_drafts),
        'draftLosses': sorted(before_drafts - after_drafts),
    }


def authored_file_note(note):
    # Carry forward file evidence from older plans that put it in the runtime queue.
    if note in {
        'The placed editor faction manager is inactive in the original layer order.',
        'The two BRDMs wrote unused recon damage flags in the original mission.',
        'The five generator light cleanup spawns miss the original world-postprocess callback. '
        'The generator flags and messages remain active.',
    }:
        return True
    return re.fullmatch(
        r'The original (?:reinforcement waypoint .+ has no authored target'
        r'|reinforcement waypoint .+ names its spawner and leaves orders unchanged'
        r'|static waypoint link to .+ has no authored target'
        r'|static waypoint link to .+ names a group and leaves orders unchanged'
        r'|assignment refers to the waypoint .+ as its group and leaves orders unchanged'
        r'|waypoint assignment for .+ has no authored group and does not run'
        r'|waypoint assignment for .+ names a spawner and does not run'
        r'|static vehicle link to .+ has no mission-layer target'
        r'|spawn at .+ has no authored anchor and does not run'
        r'|TrainArea door opener has no runtime component in the published legacy addon'
        r'|(?:template|Summer Days) framework is empty and runs no mission events'
        r'|random flag block has no named flag to set'
        r'|notification [\dA-Fa-f]+ in .+ has no eligible recipient and does not display'
        r'|event .+ has no authored condition and does not run)\.', note
    ) is not None


def shared_conversion_queue(blockers_by_world):
    """Schedule shared rule bundles by worlds moved to the next checks per fix."""
    pending = dict(blockers_by_world)
    queue = []
    while pending:
        covered = defaultdict(list)
        for world, blockers in pending.items():
            covered[blockers].append(world)
        candidates = []
        for blockers in sorted(covered):
            worlds = sorted(world for required, members in covered.items()
                            if set(required).issubset(blockers) for world in members)
            if len(worlds) > 1:
                candidates.append((blockers, worlds))
        if not candidates:
            break
        blockers, worlds = min(candidates, key=lambda entry: (
            -len(entry[1]) / len(entry[0]), -len(entry[1]), entry[0]))
        queue.append({'blockers': list(blockers), 'worlds': worlds})
        pending = {world: remaining for world, prior in pending.items() if world not in worlds
                   if (remaining := tuple(rule for rule in prior if rule not in blockers))}
    return queue


def summarize(inventory, review, limit, previous_checks=None, previous_notes=None):
    migration = inventory['batchMigration']
    candidates = defaultdict(list)
    committed = set()
    for header, row in inventory['headers'].items():
        destination = row.get('destinationWorld', '').partition('}')[2]
        if row['devHeaderAtSamePath'] and row.get('devWorldAtSamePath'):
            committed.add(header)
        if migration['worlds'].get(destination, {}).get('status') == 'generated for file review':
            candidates[destination].append(header)

    ready, held = [], []
    held_issues_by_header = {}
    for world, headers in sorted(candidates.items()):
        draft = migration['worlds'][world]
        issues = []
        issues_by_header = {}
        gaps = set(draft.get('remainingBehavior', []))
        remaining_resources = set()
        layer_repairs = set(draft['files']) if draft.get('existingLayerRepairs') else set()
        for header in sorted(headers):
            comparison = review['missions'][header]
            gaps.update(comparison.get('documentedGaps', []))
            header_issues = [issue for issue in comparison['issues']
                             if not layer_repairs or issue != 'Unresolved external resource references']
            for entry in comparison.get('unresolvedExternalReferences', []):
                reference = f"{{{entry['guid']}}}{entry['path']}"
                if layer_repairs and layer_repairs.isdisjoint(entry['referencedBy']):
                    remaining_resources.add(reference)
                else:
                    header_issues.append(reference)
            if comparison['status'] != 'compare' and not comparison['issues']:
                header_issues.append('comparison incomplete')
            advertised = draft.get('advertisedSlots', {}).get(header)
            if (advertised is not None and advertised != draft['verifiedPlayableSlots']
                    and header not in draft.get('reviewedCapacity', {})):
                header_issues.append(f"{advertised} advertised slots, {draft['verifiedPlayableSlots']} converted")
            issues_by_header[header] = sorted(set(header_issues))
            issues.extend(issue if issue.startswith('{') else f'{header} — {issue}'
                          for issue in header_issues)
        record = {'world': world, 'launchers': sorted(headers),
                  'behaviorGaps': sorted(gaps)}
        if draft.get('reviewedCapacity'):
            record['reviewedCapacity'] = draft['reviewedCapacity']
        if draft.get('reviewedReplacements'):
            record['reviewedReplacements'] = sorted(draft['reviewedReplacements'])
        if remaining_resources:
            record['remainingResources'] = sorted(remaining_resources)
        if issues:
            held.append({**record, 'issues': sorted(set(issues))})
            held_issues_by_header.update(issues_by_header)
        else:
            ready.append(record)

    priority = defaultdict(list)
    specific = defaultdict(list)
    bundles = defaultdict(list)
    blockers_by_world = {}
    for world, row in sorted(migration['worlds'].items()):
        if row['status'] != 'blocked by conversion rules':
            continue
        by_rule = {}
        by_detail = {}
        for blocker in row['blockers']:
            by_rule.setdefault(blocker['rule'], blocker)
            if blocker['rule'] == 'framework events' and 'detail' not in blocker:
                outcome = inventory['worlds'][world].get('outcomeCandidate', {})
                gaps = [{'rule': blocker['rule'], 'file': location.rsplit(':', 1)[0],
                         'line': int(location.rsplit(':', 1)[1]), 'detail': event['gap']}
                        for location, event in outcome.get('events', {}).items() if 'gap' in event]
                if 'gap' in outcome:
                    gaps.append({**blocker, 'detail': outcome['gap']})
            else:
                gaps = []
            for gap in gaps or [blocker]:
                by_detail.setdefault(specific_blocker(gap), gap)
        for rule, example in by_rule.items():
            priority[rule].append((len(by_rule) == 1, {'world': world, **example}))
        for kind, example in by_detail.items():
            specific[kind].append((len(by_detail) == 1, {'world': world, **example}))
        if by_detail:
            blockers = tuple(sorted(by_detail))
            bundles[blockers].append(world)
            blockers_by_world[world] = blockers

    def ranked_blockers(blockers, label):
        rows = []
        for blocker, entries in blockers.items():
            ranked = sorted(entries, key=lambda entry: not entry[0])
            rows.append({label: blocker, 'worldsAffected': len(entries),
                         'soleBlockerWorlds': sum(sole for sole, _ in entries),
                         'examples': [example for _, example in ranked[:3]]})
        return sorted(rows, key=lambda row: (-row['soleBlockerWorlds'], -row['worldsAffected'], row[label]))

    selected = ready[:limit]
    draft_checks = {header: entry['behaviorGaps'] for entry in ready for header in entry['launchers']}
    recorded_checks = dict(previous_checks or {})
    file_notes = dict(previous_notes or {})
    runtime_candidates = []
    committed_metadata = {}
    remaining_launchers = {}
    for header, row in sorted(review['missions'].items()):
        world = row['world']
        gaps = sorted(set(recorded_checks.get(header, ()))
                      | set(row.get('documentedGaps', ())) | set(draft_checks.get(header, ())))
        notes = {note for note in gaps if authored_file_note(note)}
        if notes:
            file_notes[header] = sorted(notes.union(file_notes.get(header, ())))
        checks = sorted(set(gaps) - notes)
        if header in recorded_checks:
            recorded_checks[header] = checks
        if header in held_issues_by_header:
            issues = held_issues_by_header[header] or ['Another launcher for this world is held']
            remaining = {'world': world, 'stage': 'Draft held', 'issues': issues}
            roster_refs = [entry for entry in row.get('unresolvedExternalReferences', ())
                           if entry.get('orderedRosterSlots')]
            if roster_refs:
                remaining['orderedRosterBlockers'] = roster_refs
            remaining_launchers[header] = remaining
            continue
        if row['status'] != 'compare':
            blockers = migration['worlds'].get(world, {}).get('blockers', [])
            remaining_launchers[header] = {
                'world': world, 'stage': 'Conversion blocked' if blockers else 'File review',
                'issues': row['issues'], 'conversionBlockers': blockers,
            }
            continue
        if header in committed and header not in draft_checks:
            committed_metadata[header] = row.get('launcherMetadata', {})
        else:
            remaining_launchers[header] = {'world': world, 'stage': 'Ready to install', 'issues': []}
        if checks:
            recorded_checks[header] = checks
            runtime_candidates.append({'world': row['world'], 'launchers': [header],
                                       'behaviorGaps': checks})
    vehicle_group_links = {}
    for header, row in sorted(inventory['headers'].items()):
        worlds = row['worlds']
        if len(worlds) == 1 and (links := inventory['worlds'].get(worlds[0], {}).get('legacyVehicleGroupLinks')):
            vehicle_group_links[header] = links
    return {
        'source': inventory['source'], 'target': inventory['target'],
        'committedLaunchersWithWorld': len(committed),
        'committedLaunchersCleared': len(committed_metadata),
        'committedLauncherMetadata': committed_metadata,
        'remainingLaunchers': remaining_launchers,
        'auditedAddonVersionsByLauncher': {header: row['auditedAddonVersions']
                                           for header, row in review['missions'].items()
                                           if row['status'] == 'compare' and row.get('auditedAddonVersions')},
        'legacyLaunchers': len(inventory['headers']),
        'launcherFileChecks': dict(Counter(row['status'] for row in review['missions'].values())),
        'worldConversionChecks': migration['totals'],
        'suggestedBatch': selected, 'additionalDrafts': ready[limit:], 'heldDrafts': held,
        'runtimeChecksByLauncher': recorded_checks,
        'runtimeChecks': runtime_checks(runtime_candidates),
        'authoredFileNotesByLauncher': file_notes,
        'legacyVehicleGroupLinksByLauncher': vehicle_group_links,
        'unresolvedResources': review.get('unresolvedResources', []),
        'sourceDependencyLeads': review.get('sourceDependencyLeads', []),
        'outcomeFlagLeads': review.get('outcomeFlagLeads', {}),
        'scriptedInstructionLeads': review.get('scriptedInstructionLeads', {}),
        'resourceRecoveryQueue': resource_recovery_queue(review.get('unresolvedResources', []), held),
        'conversionResourceQueue': conversion_resource_queue(
            review.get('sourceDependencyLeads', ()), review.get('blockedLoadoutParents', ()),
            inventory['headers'], blockers_by_world),
        'dependencyRecoveryQueue': dependency_recovery_queue(review.get('unresolvedResources', []), held),
        'blockedLoadoutParents': review.get('blockedLoadoutParents', []),
        'unavailableDependencyIds': review.get('unavailableDependencyIds', []),
        'rules': ranked_blockers(priority, 'rule'),
        'specificBlockers': ranked_blockers(specific, 'blocker'),
        'sharedConversionQueue': shared_conversion_queue(blockers_by_world),
        'blockerBundles': [{'blockers': kinds, 'worlds': worlds} for kinds, worlds in
                           sorted(bundles.items(), key=lambda pair: (-len(pair[1]), len(pair[0]), pair[0]))],
    }


def markdown(plan):
    lines = ['# GC Missions batch plan', '', f"Legacy revision `{plan['source']}`",
             f"Dev revision `{plan['target']}`", '',
             f"{plan['committedLaunchersWithWorld']} of {plan['legacyLaunchers']} legacy launchers "
             'have a launcher and world in the committed Dev snapshot.', '',
             f"{plan['committedLaunchersCleared']} committed launchers cleared the file audit "
             'without relying on a staged draft.', '',
             '## Launcher file audit including staged drafts', '']
    lines.extend(f"- {status} · {count}" for status, count in sorted(plan['launcherFileChecks'].items()))
    if remaining := plan.get('remainingLaunchers'):
        counts = Counter(entry['stage'] for entry in remaining.values())
        lines.extend(['', '## Remaining launchers', ''])
        lines.extend(f'- {stage} · {count}' for stage, count in sorted(counts.items()))
        lines.extend(['', 'Each launcher appears once. The same world can have multiple launchers. '
                      'See `remainingLaunchers` in `plan.json` for the full issues and source locations.', '',
                      '| Launcher | Stage | File issues or conversion rules |', '| --- | --- | --- |'])
        for header, entry in remaining.items():
            blockers = entry.get('conversionBlockers', ())
            findings = sorted({specific_blocker(blocker) for blocker in blockers}) or entry['issues']
            resource_count = sum(finding.startswith('{') for finding in findings)
            if resource_count:
                findings = [finding for finding in findings if not finding.startswith('{')
                            and finding != 'Unresolved external resource references']
                findings.append(f'{resource_count} unresolved external resource(s)')
            details = '<br>'.join(str(finding).replace('|', '&#124;') for finding in findings)
            lines.append(f"| `{header}` | {entry['stage']} | {details} |")
    if metadata := plan.get('committedLauncherMetadata'):
        lines.extend(['', '## GC launcher metadata on file-cleared Dev missions', '',
                      '| Metadata | Launchers |', '| --- | ---: |'])
        for field, label in (
            ('registeredLauncher', 'Unique launcher resource registration'),
            ('registeredWorld', 'Unique world resource registration'),
            ('gcMissionHeader', 'Native GC mission header'),
            ('generatedSnapshot', 'GC generated mission snapshot'),
            ('catalogueMetadata', 'GC catalogue metadata'),
            ('explicitRequiredAddons', 'Manual catalogue addon IDs'),
            ('portableLaunchMetadata', 'Portable launch metadata file committed in Dev'),
        ):
            count = sum(row.get(field, False) for row in metadata.values())
            lines.append(f'| {label} | {count} of {len(metadata)} |')
        lines.extend(['', 'The file audit checks resource references against the addon dependency graph. '
                      'The direct GC mission launcher also requires a generated header snapshot or portable metadata. '
                      'The launcher resolves addon names in the snapshot to GUIDs and includes any manual catalogue IDs. '
                      'Portable files exported outside the checkout are counted by the exporter. '
                      'The published build still needs its own launch verification.'])
        missing_snapshots = [header for header, checks in metadata.items()
                             if not checks.get('generatedSnapshot')]
        if missing_snapshots:
            lines.extend(['', '### File-cleared launchers awaiting native snapshots', ''])
            lines.extend(f'- `{header}`' for header in missing_snapshots)
        differences = [(header, advertised, captured) for header, checks in metadata.items()
                       if (advertised := checks.get('advertisedPlayerSlots')) is not None
                       and (captured := checks.get('snapshotPlayerSlots')) is not None
                       and advertised != captured]
        if differences:
            lines.extend(['', '### Snapshot and advertised player counts', '',
                          'Compare these rows with the authored roster and any reviewed capacity differences. '
                          'The advertised count is the launcher listing.', '',
                          '| Launcher | Advertised | Captured |', '| --- | ---: | ---: |'])
            lines.extend(f'| `{header}` | {advertised} | {captured} |'
                         for header, advertised, captured in differences)
    if pinned := plan.get('auditedAddonVersionsByLauncher'):
        lines.extend(['', '## Audited launcher version pins', '',
                      '| Launcher | Required addon versions |', '| --- | --- |'])
        for header, versions in pinned.items():
            values = ', '.join(f'`{addon}={version}`' for addon, version in versions.items())
            lines.append(f'| `{header}` | {values} |')
    if plan['sharedConversionQueue']:
        lines.extend(['', '## Shared conversion work', '',
                      'Work down this list before taking on mission-specific ports. Each estimate '
                      'counts worlds advanced to their next file checks and assumes earlier rows '
                      'are complete. Rerun the audit after each bundle to refresh the queue.', '',
                      '| Worlds advanced | Reported blockers to address together |',
                      '| ---: | --- |'])
        for entry in plan['sharedConversionQueue']:
            blockers = '<br>'.join(blocker.replace('|', '\\|') for blocker in entry['blockers'])
            lines.append(f"| {len(entry['worlds'])} | {blockers} |")
    lines.extend(['', '## Drafts ready for source and Dev file review', ''])
    selected = plan['suggestedBatch']
    if selected:
        for entry in selected:
            lines.append(f"- `{entry['world']}`")
            lines.extend(f'  - `{header}`' for header in entry['launchers'])
            lines.extend(f'  - Reviewed Dev file update in `{path}`'
                         for path in entry.get('reviewedReplacements', []))
            lines.extend(f'  - Unresolved reference in other layers or resources: `{resource}`'
                         for resource in entry.get('remainingResources', []))
            lines.extend(f'  - {note}' for note in entry.get('reviewedCapacity', {}).values())
    else:
        lines.append('No drafts cleared the file checks.')
    lines.extend(['', f"Additional drafts that cleared file checks · {len(plan['additionalDrafts'])}",
                  '', '## Representative Workbench checks across file-cleared missions', '',
                  'After bulk installation, compile once and run one lobby-to-after-action smoke test. '
                  'Use that run for as many shared behavior checks as possible, then run up to four '
                  'more targeted representatives. Review the results before opening further worlds. '
                  'Run additional checks for release-critical behavior that the first pass did not cover '
                  'and record the remaining gaps.', ''])
    first_pass = plan['runtimeChecks'][:5]
    for entry in first_pass:
        lines.append(f"- `{entry['launcher']}`")
        lines.extend(f'  - {check}' for check in entry['checks'])
    if not first_pass:
        lines.append('The file-cleared missions reported no additional behavior checks.')
    if plan['runtimeChecks'][5:]:
        lines.extend(['', f"{len(plan['runtimeChecks']) - len(first_pass)} additional representatives "
                      'for uncovered behaviors are listed in `plan.json`.'])
    if plan['authoredFileNotesByLauncher']:
        lines.extend(['', '## Inactive behavior confirmed in source files', ''])
        for header, notes in sorted(plan['authoredFileNotesByLauncher'].items()):
            lines.append(f'- `{header}`')
            lines.extend(f'  - {note}' for note in notes)
    if links := plan['legacyVehicleGroupLinksByLauncher']:
        lines.extend(['', '## Deferred lobby vehicle features', '',
                      f'{len(links)} launchers have legacy vehicle-to-group fields. '
                      'Role-selection vehicle previews and removal of vehicles for locked groups are deferred. '
                      '`legacyVehicleGroupLinksByLauncher` in `plan.json` lists the original file, line, and group name.'])
    lines.extend(['', '## Drafts held for file checks', ''])
    for entry in plan['heldDrafts']:
        lines.append(f"- `{entry['world']}`")
        lines.extend(f'  - {issue}' for issue in entry['issues'])
        lines.extend(f'  - Unresolved reference in other layers or resources: `{resource}`'
                     for resource in entry.get('remainingResources', []))
    if not plan['heldDrafts']:
        lines.append('None.')
    if plan['dependencyRecoveryQueue']:
        lines.extend(['', '## Installed packages outside the Dev dependency graph', '',
                      'These packages register exact IDs and paths. Verify their contents and '
                      'dependencies before changing the Dev project.', '',
                      '| Package | Worlds with only this package missing | Held worlds | References |',
                      '| --- | ---: | ---: | ---: |'])
        for entry in plan['dependencyRecoveryQueue']:
            lines.append(f"| `{entry['provider']}` | {len(entry['onlyMissingPackage'])} | "
                         f"{len(entry['worlds'])} | {len(entry['references'])} |")
    if plan['resourceRecoveryQueue']:
        lines.extend(['', '## Dependency recovery priorities for complete drafts', '',
                      '| Resource | Only missing asset | Held worlds | ID registered in Dev graph |',
                      '| --- | ---: | ---: | --- |'])
        for entry in plan['resourceRecoveryQueue']:
            registered = 'Yes' if entry['registeredInDevGraph'] else 'No'
            lines.append(f"| `{entry['reference']}` | {len(entry['onlyMissingAsset'])} | "
                         f"{len(entry['worlds'])} | {registered} |")
        aliases = [(entry['reference'], registration)
                   for entry in plan['resourceRecoveryQueue']
                   for registration in entry.get('alternateRegisteredPaths', ())]
        if aliases:
            lines.extend(['', '### Installed paths for the original resource IDs', '',
                          'Compare the registered asset with the authors’ intended behavior before applying a path change.', '',
                          '| Legacy resource | Registered path | Provider | In Dev graph |',
                          '| --- | --- | --- | --- |'])
            for reference, registration in aliases:
                in_graph = 'Yes' if registration['declaredDependency'] else 'No'
                lines.append(f"| `{reference}` | `{registration['path']}` | "
                             f"`{registration['provider']}` | {in_graph} |")
    if conversions := plan.get('conversionResourceQueue'):
        lines.extend(['', '## Missing resources reported by roster conversion', '',
                      '| Legacy resource | Only reported blocker | Affected worlds |',
                      '| --- | ---: | ---: |'])
        for entry in conversions:
            lines.append(f"| `{entry['reference']}` | {len(entry['onlyReportedBlocker'])} | {len(entry['worlds'])} |")
    if archives := plan.get('historicalAddonAlternatives'):
        lines.extend(['', '## Historical addon versions for missing resources', '',
                      'Package coverage counts exact resource IDs and paths. '
                      'Alternate registrations require asset comparison. '
                      'Exact matches with a verified runtime failure or loadout change are excluded from package coverage. '
                      'Audit the full dependency graph with the selected version before delivery.', '',
                      '| Addon | Version | Drafts with all blockers covered | Drafts with exact matches | Blocked conversions with exact matches | Alternatives to verify |',
                      '| --- | --- | ---: | ---: | ---: | ---: |'])
        for entry in archives:
            lines.append(f"| `{entry['addonId']}` | {entry['version']} | {len(entry['onlyMissingPackage'])} | "
                         f"{len(entry['worlds'])} | {len(entry['conversionWorlds'])} | "
                         f"{len(entry['alternateRegistrations'])} |")
        for entry in archives:
            for reference in entry['runtimeBlockedReferences']:
                lines.extend(['', f"- `{entry['addonId']} {entry['version']}` · `{reference}` failed runtime verification."])
            for reference, change in entry.get('loadoutChanges', {}).items():
                lines.extend(['', f"- `{entry['addonId']} {entry['version']}` · `{reference}` · {change}"])
            for alternate in entry['alternateRegistrations']:
                label = f"`{entry['addonId']} {entry['version']}` for `{alternate['reference']}`"
                details = [f"original ID at `{path}`" for path in alternate['sameIdPaths']]
                details.extend(f"original path under ID `{identity}`" for identity in alternate['samePathIds'])
                lines.extend(['', f"- {label} has " + ', '.join(details) + '.'])
            for successor in entry.get('reviewedSuccessors', ()):
                basis = successor.get('comparisonBasis')
                if not basis:
                    basis = f"tests on Reforger {successor['gameVersion']}"
                lines.extend(['', f"- `{entry['addonId']} {entry['version']}` · "
                              f"`{successor['replacement']}` is a reviewed successor for "
                              f"`{successor['reference']}` with differences found during {basis}. "
                              + ' '.join(successor['findings'])])
                for launcher in successor['sourceLaunchers']:
                    lines.append(f'  - `{launcher}` references the original asset.')
    if comparison := plan.get('providerComparison'):
        lines.extend(['', '## File clearance compared with the baseline provider graph', ''])
        for key, label in (('committedGains', 'Committed launchers cleared'),
                           ('committedLosses', 'Committed launchers newly held'),
                           ('draftGains', 'Draft launchers cleared'), ('draftLosses', 'Draft launchers newly held')):
            lines.extend([f'### {label}', ''])
            lines.extend([f'- `{header}`' for header in comparison[key]] or ['None.'])
            lines.append('')
    if parents := plan.get('blockedLoadoutParents'):
        lines.extend(['', '## Missing parents of mission loadouts', '',
                      'These exact parent references occur in the local character prefabs named by failed roster checks. '
                      'Confirm the original equipment and parent ancestry before approving an alternative registration.', '',
                      '| Legacy parent | Worlds with only this reported blocker | Affected worlds | Installed same-ID paths |',
                      '| --- | ---: | ---: | ---: |'])
        for entry in parents:
            only = sum(world['onlyReportedBlocker'] for world in entry['worlds'].values())
            lines.append(f"| `{{{entry['guid']}}}{entry['path']}` | {only} | {len(entry['worlds'])} | "
                         f"{len(entry['registrations'])} |")
    resources = plan['unresolvedResources']
    if resources:
        lines.extend(['', '## Unresolved resource registrations', '',
                      'Alternate paths show where an installed database registers the same resource ID. '
                      'Exact paths under a different ID are leads for asset comparison. '
                      'Verify content and dependency coverage before approving a replacement.', ''])
        for entry in resources:
            lines.append(f"- `{{{entry['guid']}}}{entry['path']}` · {len(entry['launchers'])} launcher(s)")
            lines.extend(f"  - {registration['provider']} · `{registration['path']}`"
                         + ('' if registration['declaredDependency'] else ' · outside the Dev dependency graph')
                         for registration in entry['registrations'])
            if not entry['registrations']:
                lines.append('  - No installed registration found for this ID.')
            lines.extend(f"  - {registration['provider']} · exact path under ID `{registration['guid']}`"
                         + ('' if registration['declaredDependency'] else ' · outside the Dev dependency graph')
                         for registration in entry.get('samePathRegistrations', []))
    if plan['unavailableDependencyIds']:
        lines.extend(['', '## Dev dependencies without a local provider', '',
                      *(f'- `{guid}`' for guid in plan['unavailableDependencyIds'])])
    if leads := plan.get('sourceDependencyLeads'):
        lines.extend(['', '## Dependency leads for worlds still in conversion', '',
                      'These references come from legacy files. A conversion rule may rewrite or remove them. '
                      'Check the registered provider and body before using a lead. The complete list and source '
                      'locations are in `plan.json`.', '',
                      '| Legacy resource | Launchers | Installed same-ID registrations |',
                      '| --- | ---: | ---: |'])
        for entry in leads[:15]:
            lines.append(f"| `{{{entry['guid']}}}{entry['path']}` | {len(entry['launchers'])} | "
                         f"{len(entry['registrations'])} |")
    lines.extend(['', '## Highest-priority specific blockers', '',
                  '| Blocker | Only reported blocker | Worlds affected |',
                  '| --- | ---: | ---: |'])
    for entry in plan['specificBlockers'][:25]:
        blocker = entry['blocker'].replace('|', '\\|')
        lines.append(f"| {blocker} | {entry['soleBlockerWorlds']} | {entry['worldsAffected']} |")
    if flag_leads := plan.get('outcomeFlagLeads'):
        owners = Counter()
        for flags in flag_leads.values():
            owners.update({site['owner'] for lead in flags.values() for site in lead['sourceFields']
                           if site['owner'] and site['owner'] != 'TILW_LiteralTerm'})
        lines.extend(['', '## Source leads for unresolved outcome flags', '',
                      f'The JSON plan lists exact flag references for {len(flag_leads)} worlds, with '
                      'event locations, serialized fields and matching script literals. '
                      'A reference can read or write the flag and must be checked in context.', '',
                      '| Serialized owner outside literal conditions | Worlds |', '| --- | ---: |'])
        for owner, count in sorted(owners.items(), key=lambda entry: (-entry[1], entry[0])):
            lines.append(f'| `{owner}` | {count} |')
    if scripts := plan.get('scriptedInstructionLeads'):
        lines.extend(['', '## Legacy inline script calls', '',
                      'The JSON plan records the original script declarations, event and instruction IDs, '
                      'delays, and client and rerun settings. The worlds stay in review.', '',
                      '| World | Script calls |', '| --- | ---: |'])
        for world, files in scripts.items():
            lines.append(f"| `{world}` | {sum(len(file['calls']) for file in files)} |")
    lines.extend(['', '## Shared blocker combinations', '',
                  'Each row lists blockers currently reported together. Clearing them moves the listed '
                  'worlds to the next file checks.', '',
                  '| Worlds | Reported blockers |', '| ---: | --- |'])
    for entry in plan['blockerBundles'][:15]:
        blockers = '<br>'.join(blocker.replace('|', '\\|') for blocker in entry['blockers'])
        lines.append(f"| {len(entry['worlds'])} | {blockers} |")
    lines.extend(['', '## Conversion rules to investigate', '',
                  '| Rule | Only reported blocker | Worlds affected |',
                  '| --- | ---: | ---: |'])
    for row in plan['rules']:
        rule = row['rule'].replace('|', '\\|')
        lines.append(f"| {rule} | {row['soleBlockerWorlds']} | {row['worldsAffected']} |")
    lines.extend(['', 'The JSON plan lists the worlds in each combination and source examples for each rule.'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--units', required=True, type=Path)
    parser.add_argument('--source', default='origin/master')
    parser.add_argument('--dev', default='HEAD')
    parser.add_argument('--units-source', default='origin/master')
    parser.add_argument('--units-dev', default='HEAD')
    parser.add_argument('--dependency', action='append', default=[], type=Path)
    parser.add_argument('--base-database', required=True, type=Path)
    parser.add_argument('--workshop-addons', required=True, type=Path)
    parser.add_argument('--historical-workshop', action='append', default=[], type=Path,
                        help='search an additional Workshop addon directory for exact historical registrations')
    parser.add_argument('--compare-provider-plan', type=Path,
                        help='report gained and lost file clearance against a plan for the same mission revisions')
    parser.add_argument('--output', required=True, type=Path, help='output directory outside the addons')
    parser.add_argument('--batch-size', default=10, type=int, help='maximum worlds in the suggested batch')
    parser.add_argument('--zagoria-stage', type=Path, help='stage containing the reviewed original-hull BMP-2 child')
    parser.add_argument('--previous-plan', type=Path, help='carry forward runtime checks from the previous batch')
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('--batch-size must be positive')
    lobby = Path(__file__).resolve().parents[3]
    dependencies = [lobby, args.units, *args.dependency]
    output = args.output.resolve()
    if any(output.is_relative_to(addon.resolve()) for addon in [args.missions, *dependencies]):
        parser.error('use an output directory outside the addons')
    try:
        for root in args.historical_workshop:
            if not root.is_dir():
                raise ValueError(f'historical Workshop directory does not exist: {root}')
        units_head = subprocess.check_output(
            ['git', '-C', str(args.units), 'rev-parse', 'HEAD', args.units_dev + '^{commit}'], text=True
        ).splitlines()
        if len(set(units_head)) != 1:
            parser.error('check out the selected Units Dev revision for the resource audit')
        output.mkdir(parents=True, exist_ok=True)
        stage = output / 'drafts'
        inventory = output / 'inventory.json'
        review = output / 'review.json'
        common = ['--missions', args.missions, '--source', args.source, '--dev', args.dev,
                  '--base-database', args.base_database, '--workshop-addons', args.workshop_addons]
        archives = [item for path in args.historical_workshop for item in ('--historical-workshop', path)]
        zagoria = ['--zagoria-stage', args.zagoria_stage] if args.zagoria_stage else []
        run_tool('convert', [*common, '--batch', '--units', args.units, '--units-source', args.units_source,
                             '--units-dev', args.units_dev, *archives, *zagoria,
                             '--stage', stage, '--report', inventory])
        providers = [item for path in dependencies for item in ('--dependency', path)]
        run_tool('review', [*common, *providers, *archives, '--inventory', inventory, '--draft-stage', stage,
                            '--report', review, '--summary', output / 'review.md'])
        inventory_bytes = inventory.read_bytes()
        inventory_data = json.loads(inventory_bytes)
        prior = {}
        if args.previous_plan:
            prior = json.loads(args.previous_plan.read_text(encoding='utf-8'))
            if prior.get('source') != inventory_data['source']:
                parser.error('the previous plan describes a different legacy revision')
        plan = summarize(inventory_data, json.loads(review.read_text(encoding='utf-8')),
                         args.batch_size, prior.get('runtimeChecksByLauncher'),
                         prior.get('authoredFileNotesByLauncher'))
        if args.historical_workshop:
            plan['historicalAddonAlternatives'] = historical_provider_candidates(
                plan, args.historical_workshop, StockPrefabs(args.base_database))
        if args.compare_provider_plan:
            baseline = json.loads(args.compare_provider_plan.read_text(encoding='utf-8'))
            plan['providerComparison'] = compare_provider_plans(baseline, plan)
        plan['inventorySha256'] = sha256(inventory_bytes).hexdigest()
        (output / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (output / 'plan.md').write_text(markdown(plan), encoding='utf-8')
        print(f"{len(plan['suggestedBatch'])} suggested worlds, {len(plan['heldDrafts'])} held drafts. "
              f"See {output / 'plan.md'}")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
