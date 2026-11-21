"""Generate, audit and prioritize GC Missions conversion work in one pass."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys


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


def summarize(inventory, review, limit, previous_checks=None):
    migration = inventory['batchMigration']
    candidates = defaultdict(list)
    for header, row in inventory['headers'].items():
        destination = row.get('destinationWorld', '').partition('}')[2]
        if migration['worlds'].get(destination, {}).get('status') == 'generated for file review':
            candidates[destination].append(header)

    ready, held = [], []
    for world, headers in sorted(candidates.items()):
        draft = migration['worlds'][world]
        issues = []
        gaps = set(draft.get('remainingBehavior', []))
        remaining_resources = set()
        layer_repairs = set(draft['files']) if draft.get('existingLayerRepairs') else set()
        for header in sorted(headers):
            comparison = review['missions'][header]
            gaps.update(comparison.get('documentedGaps', []))
            issues.extend(f'{header} — {issue}' for issue in comparison['issues']
                          if not layer_repairs or issue != 'Unresolved external resource references')
            for entry in comparison.get('unresolvedExternalReferences', []):
                reference = f"{{{entry['guid']}}}{entry['path']}"
                if layer_repairs and layer_repairs.isdisjoint(entry['referencedBy']):
                    remaining_resources.add(reference)
                else:
                    issues.append(reference)
            if comparison['status'] != 'compare' and not comparison['issues']:
                issues.append(f'{header} — comparison incomplete')
            advertised = draft.get('advertisedSlots', {}).get(header)
            if (advertised is not None and advertised != draft['verifiedPlayableSlots']
                    and header not in draft.get('reviewedCapacity', {})):
                issues.append(f"{header} — {advertised} advertised slots, "
                              f"{draft['verifiedPlayableSlots']} converted")
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
        else:
            ready.append(record)

    priority = defaultdict(lambda: {'worldsAffected': 0, 'soleBlockerWorlds': 0, 'examples': []})
    specific = defaultdict(lambda: {'worldsAffected': 0, 'soleBlockerWorlds': 0, 'examples': []})
    bundles = defaultdict(list)
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
            entry = priority[rule]
            entry['worldsAffected'] += 1
            entry['soleBlockerWorlds'] += int(len(by_rule) == 1)
            if len(entry['examples']) < 3:
                entry['examples'].append({'world': world, **example})
        for kind, example in by_detail.items():
            entry = specific[kind]
            entry['worldsAffected'] += 1
            entry['soleBlockerWorlds'] += int(len(by_detail) == 1)
            if len(entry['examples']) < 3:
                entry['examples'].append({'world': world, **example})
        if by_detail:
            bundles[tuple(sorted(by_detail))].append(world)
    rules = sorted(priority, key=lambda rule: (
        -priority[rule]['soleBlockerWorlds'], -priority[rule]['worldsAffected'], rule))
    details = sorted(specific, key=lambda kind: (
        -specific[kind]['soleBlockerWorlds'], -specific[kind]['worldsAffected'], kind))
    selected = ready[:limit]
    draft_checks = {header: entry['behaviorGaps'] for entry in ready for header in entry['launchers']}
    held_headers = {header for entry in held for header in entry['launchers']}
    recorded_checks = dict(previous_checks or {})
    runtime_candidates = []
    for header, row in sorted(review['missions'].items()):
        gaps = sorted(set(recorded_checks.get(header, ()))
                      | set(row.get('documentedGaps', ())) | set(draft_checks.get(header, ())))
        if row['status'] != 'compare' or header in held_headers or not gaps:
            continue
        recorded_checks[header] = gaps
        runtime_candidates.append({'world': row['world'], 'launchers': [header], 'behaviorGaps': gaps})
    return {
        'source': inventory['source'], 'target': inventory['target'],
        'launcherFileChecks': dict(Counter(row['status'] for row in review['missions'].values())),
        'worldConversionChecks': migration['totals'],
        'suggestedBatch': selected, 'additionalDrafts': ready[limit:], 'heldDrafts': held,
        'runtimeChecksByLauncher': recorded_checks,
        'runtimeChecks': runtime_checks(runtime_candidates),
        'unresolvedResources': review.get('unresolvedResources', []),
        'unavailableDependencyIds': review.get('unavailableDependencyIds', []),
        'rules': [{'rule': rule, **priority[rule]} for rule in rules],
        'specificBlockers': [{'blocker': kind, **specific[kind]} for kind in details],
        'blockerBundles': [{'blockers': kinds, 'worlds': worlds} for kinds, worlds in
                           sorted(bundles.items(), key=lambda pair: (-len(pair[1]), len(pair[0]), pair[0]))],
    }


def markdown(plan):
    lines = ['# GC Missions batch plan', '', f"Legacy revision `{plan['source']}`",
             f"Dev revision `{plan['target']}`", '',
             '## Launcher file audit', '']
    lines.extend(f"- {status} · {count}" for status, count in sorted(plan['launcherFileChecks'].items()))
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
                  'Run these after assembling the conversion batch. A representative in an additional '
                  'draft is playable after its installation.', ''])
    for entry in plan['runtimeChecks']:
        lines.append(f"- `{entry['launcher']}`")
        lines.extend(f'  - {check}' for check in entry['checks'])
    if not plan['runtimeChecks']:
        lines.append('The file-cleared missions reported no additional behavior checks.')
    lines.extend(['', '## Drafts held for file checks', ''])
    for entry in plan['heldDrafts']:
        lines.append(f"- `{entry['world']}`")
        lines.extend(f'  - {issue}' for issue in entry['issues'])
        lines.extend(f'  - Unresolved reference in other layers or resources: `{resource}`'
                     for resource in entry.get('remainingResources', []))
    if not plan['heldDrafts']:
        lines.append('None.')
    resources = plan['unresolvedResources']
    if resources:
        lines.extend(['', '## Unresolved resource registrations', '',
                      'Alternate paths show where an installed database registers the same resource ID. '
                      'Verify content and dependency coverage before approving a replacement.', ''])
        for entry in resources:
            lines.append(f"- `{{{entry['guid']}}}{entry['path']}` · {len(entry['launchers'])} launcher(s)")
            lines.extend(f"  - {registration['provider']} · `{registration['path']}`"
                         + ('' if registration['declaredDependency'] else ' · outside the Dev dependency graph')
                         for registration in entry['registrations'])
            if not entry['registrations']:
                lines.append('  - No installed registration found for this ID.')
    if plan['unavailableDependencyIds']:
        lines.extend(['', '## Dev dependencies without a local provider', '',
                      *(f'- `{guid}`' for guid in plan['unavailableDependencyIds'])])
    lines.extend(['', '## Highest-priority specific blockers', '',
                  '| Blocker | Only reported blocker | Worlds affected |',
                  '| --- | ---: | ---: |'])
    for entry in plan['specificBlockers'][:25]:
        blocker = entry['blocker'].replace('|', '\\|')
        lines.append(f"| {blocker} | {entry['soleBlockerWorlds']} | {entry['worldsAffected']} |")
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
    parser.add_argument('--output', required=True, type=Path, help='output directory outside the addons')
    parser.add_argument('--batch-size', default=10, type=int, help='maximum worlds in the suggested batch')
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
        run_tool('convert', [*common, '--batch', '--units', args.units, '--units-source', args.units_source,
                             '--units-dev', args.units_dev, '--stage', stage, '--report', inventory])
        providers = [item for path in dependencies for item in ('--dependency', path)]
        run_tool('review', [*common, *providers, '--inventory', inventory, '--draft-stage', stage,
                            '--report', review, '--summary', output / 'review.md'])
        inventory_bytes = inventory.read_bytes()
        inventory_data = json.loads(inventory_bytes)
        previous_checks = None
        if args.previous_plan:
            prior = json.loads(args.previous_plan.read_text(encoding='utf-8'))
            if prior.get('source') != inventory_data['source']:
                parser.error('the previous plan describes a different legacy revision')
            previous_checks = prior.get('runtimeChecksByLauncher')
        plan = summarize(inventory_data, json.loads(review.read_text(encoding='utf-8')),
                         args.batch_size, previous_checks)
        plan['inventorySha256'] = sha256(inventory_bytes).hexdigest()
        (output / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (output / 'plan.md').write_text(markdown(plan), encoding='utf-8')
        print(f"{len(plan['suggestedBatch'])} suggested worlds, {len(plan['heldDrafts'])} held drafts. "
              f"See {output / 'plan.md'}")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
