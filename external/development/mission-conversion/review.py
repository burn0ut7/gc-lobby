"""Review legacy and Dev mission files from Git without opening Workbench."""
import argparse
from collections import Counter, defaultdict
from copy import copy
from difflib import unified_diff
from functools import lru_cache
from hashlib import file_digest, sha1, sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from briefings import block_end, properties
from catalog import GC_HEADER, OBSOLETE_NAVMESH, REF, SOURCE_TEXT, Snapshot, TYPES
from dependencies import database_project, dependency_resources, pinned_databases
from events import EVENT
from install_batch import resource_databases, unregistered_references
from launchers import META_NAME, WORLD_REF, authored_header_text, launcher, registered_identity, required_addon_versions
from scenery import StockPrefabs
from terrain_parents import LONGMOOR_PARENT, LONGMOOR_PROVIDER, has_reviewed_longmoor, is_longmoor_subscene
from vehicle_crew import ZAGORIA_BMP2

LEGACY = re.compile(r'^(?:TILW|PS)_')
UNRESOLVED_OUTCOME_FLAG = re.compile(r'unresolved or unsupported outcome flag ("[^"]*")')
SERIALIZED_FLAG_FIELD = re.compile(r'(m_\w*[Ff]lag\w*) ("[^"]+")')
MISSION_OWNED_SLOT = re.compile(r'mission-owned slot ([0-9A-F]{16})')
SCRIPT_HOOK = re.compile(r'^ (userScript|FrameworkScript[1-5]) "')
SCRIPT_INSTRUCTION = re.compile(r'^    TILW_RunScriptInstruction "\{([\da-fA-F]{16})\}" \{$')
SCRIPT_FIELDS = {'m_iScriptNumber', 'm_executionDelay', 'm_bClientScript', 'm_bAllowRerun'}
ZAGORIA_DRIVETRAIN_FAILURE = {
    'b2dbad72f17557b06f7b933aa21b2c1bacccca0b36094fb4e022697bcc0c43ae': '1.0.23',
    '37898175d32ccab770d122a90c35183cd0009a56d9411303974401e55d823ab6': '1.0.25',
    '34bdc51bf145db76207ac0e04b6225168928765a157517e7f7b851b8dc87de7c': '1.0.26',
}
ENGINE_19037_DATABASE = 'd65ebb14b5c9b2baaef1dfafd9b653f51a6ce11f9815585cf8b5ada88dd4e58a'
BOLT_GUNS_57_ARCHIVE = 'd262083e654a4d4952b88c901d257377de03329b6df0c7204359dcc8a38e5550'
RUHA_032_REGISTRY = 'fb4911e6b81561933d73cbb1eeb734e97d1fe20bd08bb8f6797949e28f77a8b4'
RUHA_WORLD = ('31A1248EA401F22C', 'worlds/Ruha.ent')
SCREENING_RUNTIME_LAYER = 'worlds/HitTheMan/Screening The Orb/ScreeningTheOrb_Layers/default.layer'
SCREENING_RUNTIME_BLOB = '3eac40a7befe19671fbd9abb4e8e78714a804dd0'
LEGACY_PRESENTATION_RESOURCES = {
    ('3136BE42592F3B1B', 'PrefabsEditable/MissionDescription/EditableMissionDescription.et'),
    ('CD85ADE9E0F54679', 'PrefabsEditable/Markers/EditableMarker.et'),
}
AREAS = {
    'mode': re.compile(r'^(?:\$grp )?(?:\w*GameMode\w*|\w*FactionManager\w*)\b|\bm_i\w*Freeze\w*\b'),
    'briefing': re.compile(r'\b(?:PS_MissionDescription|GC_Briefing)\b|\bm_[a-z]VisibleForFactions\b'),
    'roster': re.compile(r'\b(?:SCR_AIGroup|PS_PlayableComponent)\b|\bm_(?:aUnitPrefabSlots|\w*Callsign\w*|bPlayable)\b'),
    'map': re.compile(r'\b(?:PS_ManualMarker|GC_BriefingMarkerComponent|\w*MapShape\w*|\w*AOLimit\w*|ShapePoint)\b'),
    'respawn': re.compile(r'\b(?:PS_FactionRespawnCount|\w+Respawn\w*|m_\w+Tickets\w*)\b'),
    'events': re.compile(r'\b(?:TILW_\w+(?:TriggerEntity|Instruction|Term|Flag\w*)|TILW_MissionEvent|GC_Event|GC_AreaFlagObserver|GC_\w+(?:Action|Condition))\b|\bm_\w*Delay\b'),
    'weather': re.compile(r'\b(?:\w*TimeAndWeather\w*|\w*StartingWeather\w*|m_\w*(?:StartingHour|StartingDay|DateOverride|WindSpeed)\w*)\b'),
    'vehicles': re.compile(r'^(?:\$grp )?Vehicle\b|\bm_sAttachmentGroupName\b'),
}


def closure(snapshot, world, header):
    files = snapshot.closure(world) if world in snapshot.files else set()
    if header in snapshot.files:
        files.add(header)
    files.update(path + '.meta' for path in list(files) if path + '.meta' in snapshot.files)
    return files


def known_zagoria_failure(stock, providers):
    if sha256(stock.data).hexdigest() != ENGINE_19037_DATABASE:
        return None
    for database in providers:
        package = database.with_name('data.pak')
        if database.parent.name != 'Zagoria89BMP2_6730D59067916E3D' or not package.is_file():
            continue
        with package.open('rb') as native:
            return ZAGORIA_DRIVETRAIN_FAILURE.get(file_digest(native, 'sha256').hexdigest())
    return None


def known_compile_failure(stock, providers):
    if sha256(stock.data).hexdigest() != ENGINE_19037_DATABASE:
        return None
    for database in providers:
        package = database.with_name('data.pak')
        if database.parent.name != 'ChungusBoltGuns_61BD6595183FCEBD' or not package.is_file():
            continue
        with package.open('rb') as native:
            if file_digest(native, 'sha256').hexdigest() == BOLT_GUNS_57_ARCHIVE:
                return 'Chungus Bolt Guns 1.0.57 fails script compilation on Reforger 1.9.0.37'
    return None


def known_terrain_failure(stock, providers, references):
    if RUHA_WORLD not in references or sha256(stock.data).hexdigest() != ENGINE_19037_DATABASE:
        return None
    for database in providers:
        if database.parent.name == 'Ruha_653CB36244ADBE0F':
            with database.open('rb') as native:
                if file_digest(native, 'sha256').hexdigest() == RUHA_032_REGISTRY:
                    return 'Ruha 0.3.2 navigation meshes are obsolete on Reforger 1.9.0.37'
    return None


@lru_cache(maxsize=16384)
def facts(snapshot, path):
    if PurePosixPath(path).suffix.lower() not in SOURCE_TEXT:
        return {}, [], []
    text = snapshot.read(path)
    if path.endswith('.conf'):
        text = authored_header_text(text)
    categories = {}
    for no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(('"', '//')):
            continue
        for area, pattern in AREAS.items():
            if pattern.search(line):
                categories.setdefault(area, []).append(no)
    return categories, sorted(set(TYPES.findall(text))), REF.findall(text)


def inspect(snapshot, paths):
    areas, declarations, references = {}, set(), {}
    for path in sorted(paths):
        categories, kinds, refs = facts(snapshot, path)
        for area, lines in categories.items():
            areas.setdefault(area, {})[path] = lines
        declarations.update(kinds)
        for resource, name in refs:
            references.setdefault((resource.upper(), name), set()).add(path)
    return areas, declarations, references


def compare_mission(source, target, header, notes, dependencies, stock, unresolved=None, longmoor=False,
                    provider_issue=None, zagoria_failure=None):
    issues = [provider_issue] if provider_issue else []
    try:
        world, resource, _ = launcher(source, target, header)
    except ValueError as exc:
        return {'status': 'review', 'issues': [str(exc)], 'files': {}}
    incoming = closure(source, world, header)
    outgoing = closure(target, world, header)
    if header not in target.files:
        issues.append('Dev launcher missing')
    else:
        ref = WORLD_REF.search(target.read(header))
        if not ref or ref[2].upper() != resource or ref[3] != world:
            issues.append('Dev launcher points to a different world identity or path')
    world_registered = registered_identity(target, resource, world)
    if world not in target.files:
        issues.append('Dev world missing')
    elif not world_registered:
        issues.append('Dev world metadata does not uniquely register the launcher identity and path')
    before, _, _ = inspect(source, incoming)
    after, kinds, refs = inspect(target, outgoing)
    legacy = sorted(kind for kind in kinds if LEGACY.match(kind))
    if legacy:
        issues.append('Legacy declarations remain in Dev')
    if world in target.files and not any(k.startswith('GC_') and 'Mode' in k for k in kinds):
        issues.append('GC Lobby mode placement or native-mode exception needs review')
    for mesh, blob in OBSOLETE_NAVMESH.items():
        if mesh in outgoing and target.files[mesh] == blob:
            issues.append('Dev still references an obsolete compiled navigation mesh: ' + mesh)
        if mesh in outgoing and not target.rebuilt_navmesh_is_complete(mesh):
            issues.append('Dev regenerated navigation mesh is missing matching tile data: ' + mesh)
    local_refs = []
    missing = []
    path_resolved_parents = []
    for (guid, path), owners in sorted(refs.items()):
        owned = target.resources.get(guid, [])
        target_paths = owned or dependencies.get(guid, [])
        if (target_paths or guid in source.resources) and target_paths != [path]:
            if not owned and ((unresolved is not None and (guid, path) not in unresolved)
                              or (stock and stock.contains(guid, path))):
                continue
            local_refs.append({'guid': guid, 'path': path, 'devPaths': target_paths, 'referencedBy': sorted(owners)})
        elif unresolved is not None and (guid, path) in unresolved:
            reference = {'guid': guid, 'path': path, 'referencedBy': sorted(owners)}
            if (longmoor and (guid, path) == LONGMOOR_PARENT and owners == {world}
                    and is_longmoor_subscene(world, target.read(world))):
                path_resolved_parents.append({**reference, 'provider': LONGMOOR_PROVIDER, 'version': '3.2.1'})
            else:
                missing.append(reference)
    if local_refs:
        issues.append('Mission-owned resource identities or paths need review')
    if missing:
        issues.append('Unresolved external resource references')
    failed_vehicle_refs = []
    if zagoria_failure:
        for owner in sorted(refs.get(ZAGORIA_BMP2, ())):
            lines = target.read(owner).splitlines()
            for index, text in enumerate(lines):
                if not any((guid.upper(), path) == ZAGORIA_BMP2 for guid, path in REF.findall(text)):
                    continue
                field = text.lstrip()
                placed = owner.endswith(('.layer', '.ent')) and re.match(r'^(?:\$grp )?(?:Vehicle|Tank)\b', field)
                spawned = False
                if field.startswith('m_sPrefab '):
                    indent = len(text) - len(field)
                    parent = next((row.strip() for row in reversed(lines[:index])
                                   if row.strip() and len(row) - len(row.lstrip()) < indent), '')
                    spawned = re.match(r'm_Spawn GC_(?:PrefabSpawn|CrewedVehicleSpawn)\b', parent)
                if placed or spawned:
                    failed_vehicle_refs.append({'file': owner, 'line': index + 1})
        if failed_vehicle_refs:
            issues.append(f'Zagoria BMP-2 {zagoria_failure} fails the verified drivetrain test on Reforger 1.9.0.37')
    shared = incoming & outgoing
    files = {
        'same': sorted(p for p in shared if source.files[p] == target.files[p]),
        'changed': sorted(p for p in shared if source.files[p] != target.files[p]),
        'sourceOnly': sorted(incoming - outgoing),
        'devOnly': sorted(outgoing - incoming),
    }
    notes_for_world = notes.get(world, {})
    metadata = {}
    if header in target.files:
        body = target.read(header)
        identity = META_NAME.search(target.read(header + '.meta')) if header + '.meta' in target.files else None
        metadata = {
            'registeredLauncher': bool(identity and registered_identity(target, identity[2], header)),
            'registeredWorld': world_registered,
            'gcMissionHeader': bool(GC_HEADER.match(body)),
            'generatedSnapshot': bool(re.search(
                r'^\s*m_GC_GeneratedMetadata\s+GC_GeneratedMissionMetadata(?:\s+"[^"]+")?\s*\{', body, re.M)),
            'catalogueMetadata': bool(re.search(
                r'^\s*m_GC_Catalog\s+GC_CatalogMissionMetadata(?:\s+"[^"]+")?\s*\{', body, re.M)),
            'explicitRequiredAddons': bool(re.search(r'^\s*m_aRequiredAddonIds\s*\{\s*"', body, re.M)),
            'portableLaunchMetadata': any(path in target.files for path in (
                header + '.launch.json', header.removesuffix('.conf') + '.launch.json')),
        }
    return {
        'world': world,
        'status': 'missing' if header not in target.files or world not in target.files else 'review' if issues else 'compare',
        'issues': issues, 'files': files, 'legacyDeclarations': legacy, 'missionResourceReferences': local_refs,
        'unresolvedExternalReferences': missing,
        'launcherMetadata': metadata,
        **({'pathResolvedTerrainParents': path_resolved_parents} if path_resolved_parents else {}),
        **({'failedVehicleRuntimeReferences': failed_vehicle_refs} if failed_vehicle_refs else {}),
        'areas': {area: {'source': before.get(area, {}), 'dev': after.get(area, {})}
                  for area in AREAS if area in before or area in after},
        'documentedGaps': [
            *notes_for_world.get('playtestCandidate', {}).get('remainingBehavior', []),
            *notes_for_world.get('batchDraft', {}).get('remainingBehavior', []),
            *(['Compare the authored locations against the historical Longmoor terrain.']
              if path_resolved_parents else []),
            *(['Compare startup cleanup targets and confirm protected entities survive.']
              if {'GC_StartupCleanupEntity', 'GC_StartupCleanupProtectionComponent'} <= kinds else []),
            *(['Reproduce the Screening The Orb native startup crash on the retained addon graph.']
              if target.files.get(SCREENING_RUNTIME_LAYER) == SCREENING_RUNTIME_BLOB and SCREENING_RUNTIME_LAYER in outgoing
              else []),
            *(['Verify Screening The Orb exterior reinforcements at 2 and 12 seconds after the shared area flag changes.']
              if SCREENING_RUNTIME_LAYER in outgoing else []),
            *(['Compare issued equipment and slot presentation for the restored historical MSV80 roles.']
              if any('/MotorRifle_MSV80/Legacy/Character_USSR_Legacy_MSV80_' in path for _, path in refs) else []),
        ],
    }


def markdown(source, target, missions):
    counts = Counter(row['status'] for row in missions.values())
    lines = ['# GC Missions file review', '', f'Legacy revision `{source.commit}`', f'Dev revision `{target.commit}`', '',
             f'{len(missions)} launchers. {counts["missing"]} missing on Dev, {counts["review"]} with file checks to resolve, '
             f'{counts["compare"]} ready for source/Dev comparison.', '',
             '| Launcher | Status | Changed | Source only | Dev only | File checks |',
             '| --- | --- | ---: | ---: | ---: | --- |']
    for name, row in missions.items():
        files = row['files']
        issues = ', '.join(row['issues']).replace('|', '\\|')
        if row.get('pathResolvedTerrainParents'):
            issues = ', '.join(filter(None, [issues, 'Longmoor 3.2.1 terrain loads by path']))
        lines.append(f'| `{name}` | {row["status"]} | {len(files.get("changed", []))} | '
                     f'{len(files.get("sourceOnly", []))} | {len(files.get("devOnly", []))} | {issues} |')
    return '\n'.join(lines) + '\n'


def overlay(target, stage, manifest):
    result = copy(target)
    result.__dict__.pop('layers', None)
    result.files = dict(target.files)
    result.blobs = dict(target.blobs)
    result.resources = defaultdict(list, {key: list(paths) for key, paths in target.resources.items()})
    stage = stage.resolve()
    for path, digest in manifest.items():
        file = (stage / path).resolve()
        if not file.is_relative_to(stage):
            raise ValueError('draft path escapes staging: ' + path)
        body = file.read_bytes()
        if sha256(body).hexdigest() != digest:
            raise ValueError('draft changed since generation: ' + path)
        oid = sha1(f'blob {len(body)}\0'.encode() + body).hexdigest()
        result.files[path] = oid
        result.blobs[oid] = body
        if path.endswith('.meta'):
            resource = path[:-5]
            for paths in result.resources.values():
                if resource in paths:
                    paths.remove(resource)
            entry = META_NAME.search(body.decode('utf-8-sig'))
            if entry:
                result.resources[entry[2].upper()].append(resource)
    return result


def draft_diff(source, draft, paths):
    result = []
    for path in sorted(paths):
        before = source.read_raw(path) if path in source.files else b''
        after = draft.read_raw(path) if path in draft.files else b''
        try:
            old, new = before.decode('utf-8-sig'), after.decode('utf-8-sig')
        except UnicodeDecodeError:
            result.append(f'Binary file {path} differs\n')
            continue
        old_name = 'a/' + path if path in source.files else '/dev/null'
        new_name = 'b/' + path if path in draft.files else '/dev/null'
        for line in unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), old_name, new_name):
            result.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    return ''.join(result).encode()


def external_resource_review(missions, databases, providers, *, reference_field='unresolvedExternalReferences'):
    """Group unresolved references and show alternate installed registrations."""
    references = defaultdict(dict)
    for launcher_path, mission in missions.items():
        for entry in mission.get(reference_field, []):
            references[entry['guid'], entry['path']][launcher_path] = entry['referencedBy']
    if not references:
        return []
    guids = sorted({guid for guid, _ in references})
    paths = sorted({path for _, path in references})
    registrations = defaultdict(list)
    same_path = defaultdict(list)
    for index, registry in enumerate(databases):
        try:
            database = StockPrefabs(registry)
        except ValueError:
            continue
        context = {'provider': 'Base game' if index == 0 else registry.parent.name,
                   'declaredDependency': registry in providers}
        for guid in guids:
            registrations[guid].extend({**context, 'path': alternate}
                                       for alternate in database.registered_paths(guid))
        for path in paths:
            same_path[path].extend({**context, 'guid': guid} for guid in database.registered_ids(path))
    return [{'guid': guid, 'path': path, 'launchers': owners, 'registrations': registrations[guid],
             'samePathRegistrations': [entry for entry in same_path[path] if entry['guid'] != guid]}
            for (guid, path), owners in sorted(references.items(), key=lambda entry: (-len(entry[1]), *entry[0]))]


def source_dependency_leads(source, missions, notes, dependencies, databases, providers):
    """Report unregistered legacy inputs while their worlds still have conversion blockers."""
    by_launcher = {}
    refs = set()
    for header, mission in missions.items():
        world = mission.get('world')
        if notes.get(world, {}).get('batchDraft', {}).get('status') != 'blocked by conversion rules':
            continue
        missing = defaultdict(set)
        for owner in closure(source, world, header):
            for guid, path in facts(source, owner)[2]:
                guid = guid.upper()
                if (guid in source.resources or path in dependencies.get(guid, ())
                        or (guid, path) in LEGACY_PRESENTATION_RESOURCES):
                    continue
                if re.search(r'(?:^|/)(?:PS_|TILW_)[^/]+$', path, re.I):
                    continue
                missing[guid, path].add(owner)
        if missing:
            by_launcher[header] = missing
            refs.update(missing)
    unresolved = unregistered_references(refs, providers)
    rows = {header: {'legacyInputs': [
        {'guid': guid, 'path': path, 'referencedBy': sorted(owners)}
        for (guid, path), owners in sorted(missing.items()) if (guid, path) in unresolved]}
        for header, missing in by_launcher.items()}
    return external_resource_review(rows, databases, providers, reference_field='legacyInputs')


def blocked_loadout_parents(source, missions, notes, leads):
    """Tie an unresolved direct character parent to the roster checks it stopped."""
    rows = []
    for lead in leads:
        parent = (lead['guid'], lead['path'])
        impacts = {}
        for header, owners in lead['launchers'].items():
            world = missions[header]['world']
            blockers = notes[world]['batchDraft'].get('blockers', [])
            slots = {}
            for owner in owners:
                head = source.read(owner).partition('\n')[0]
                reference = REF.search(head)
                if (not head.startswith('SCR_ChimeraCharacter : ') or not reference
                        or (reference[1].upper(), reference[2]) != parent):
                    continue
                if owner + '.meta' not in source.files:
                    continue
                identity = META_NAME.search(source.read(owner + '.meta'))
                if identity and source.resources.get(identity[2].upper()) == [owner]:
                    slots[identity[2].upper()] = owner
            matched = [blocker for blocker in blockers if blocker['rule'] == 'SCR_AIGroup'
                       and isinstance(blocker.get('detail'), str)
                       and (match := MISSION_OWNED_SLOT.fullmatch(blocker.get('detail', '')))
                       and match[1] in slots]
            if matched:
                impacts[world] = {'rolePrefabs': sorted({slots[b['detail'].split()[-1]] for b in matched}),
                                  'onlyReportedBlocker': len(matched) == len(blockers)}
        if impacts:
            rows.append({'guid': lead['guid'], 'path': lead['path'], 'worlds': impacts,
                         'registrations': lead['registrations'], 'samePathRegistrations': lead['samePathRegistrations']})
    return sorted(rows, key=lambda row: (-sum(w['onlyReportedBlocker'] for w in row['worlds'].values()),
                                         -len(row['worlds']), row['path']))


def scripted_instruction_leads(source, notes, worlds):
    """Locate numbered legacy script bodies and the events that schedule them."""
    results = {}
    for world in sorted(worlds):
        if notes.get(world, {}).get('batchDraft', {}).get('status') != 'blocked by conversion rules':
            continue
        locations = []
        for path in sorted(source.closure(world)):
            if PurePosixPath(path).suffix.lower() not in ('.layer', '.et'):
                continue
            text = source.read(path)
            if 'TILW_RunScriptInstruction' not in text:
                continue
            lines = text.splitlines()
            hooks = defaultdict(list)
            calls = []
            for index, line in enumerate(lines):
                if hook := SCRIPT_HOOK.match(line):
                    end = index
                    while lines[end].endswith('\\') and end + 1 < len(lines):
                        end += 1
                    hooks[hook[1]].append({'firstLine': index + 1, 'lastLine': end + 1})
                if not (event := EVENT.fullmatch(line)):
                    continue
                try:
                    end = block_end(lines, index)
                except ValueError:
                    continue
                name = next((field.removeprefix('   m_name ') for field in lines[index + 1:end]
                             if field.startswith('   m_name ')), '""')
                for call in range(index + 1, end):
                    if not (instruction := SCRIPT_INSTRUCTION.fullmatch(lines[call])):
                        continue
                    details = {'line': call + 1, 'event': name[1:-1], 'eventId': event[1],
                               'instructionId': instruction[1]}
                    try:
                        fields = properties(lines[call + 1:block_end(lines, call)], SCRIPT_FIELDS)
                        details.update(scriptNumber=fields.get('m_iScriptNumber', '1'),
                                       delaySeconds=fields.get('m_executionDelay', '5'),
                                       clientScript=fields.get('m_bClientScript', '0'),
                                       allowRerun=fields.get('m_bAllowRerun', '0'))
                    except ValueError as exc:
                        details['gap'] = str(exc)
                    calls.append(details)
            if calls:
                locations.append({'file': path, 'sourceBlob': source.files[path], 'hooks': dict(hooks), 'calls': calls})
        if locations:
            results[world] = locations
    return results


def outcome_flag_leads(source, notes, worlds):
    """Locate literal source references for the flags that stopped outcome conversion."""
    results = {}
    for world in sorted(worlds):
        note = notes.get(world, {})
        if note.get('batchDraft', {}).get('status') != 'blocked by conversion rules':
            continue
        events = note.get('outcomeCandidate', {}).get('events', {})
        flags = defaultdict(lambda: {'outcomes': [], 'sourceFields': [], 'scriptLiterals': []})
        for location, event in events.items():
            if match := UNRESOLVED_OUTCOME_FLAG.fullmatch(event.get('gap', '')):
                flags[match[1]]['outcomes'].append(location)
        if not flags:
            continue
        for path in sorted(source.closure(world)):
            if PurePosixPath(path).suffix.lower() not in SOURCE_TEXT:
                continue
            owners = []
            for number, line in enumerate(source.read(path).splitlines(), 1):
                token = line.strip()
                if token.endswith('{'):
                    owner = next((word for word in token.split() if LEGACY.match(word)), '')
                    owners.append(owner or (owners[-1] if owners else ''))
                elif token == '}' and owners:
                    owners.pop()
                elif (match := SERIALIZED_FLAG_FIELD.fullmatch(token)) and match[2] in flags:
                    flags[match[2]]['sourceFields'].append({
                        'file': path, 'line': number, 'owner': owners[-1] if owners else '', 'field': match[1],
                    })
        results[world] = flags

    by_literal = defaultdict(list)
    for flags in results.values():
        for literal, lead in flags.items():
            if literal != '""':
                by_literal[literal].append(lead)
    if by_literal:
        for path in sorted(source.files):
            if not path.lower().endswith('.c'):
                continue
            for number, line in enumerate(source.read_raw(path).decode('utf-8-sig').splitlines(), 1):
                for literal in set(re.findall(r'"[^"\\]*"', line)).intersection(by_literal):
                    for lead in by_literal[literal]:
                        lead['scriptLiterals'].append({'file': path, 'line': number})
    return {world: {literal[1:-1]: lead for literal, lead in sorted(flags.items())}
            for world, flags in results.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--source', default='origin/master')
    parser.add_argument('--dev', default='origin/dev')
    parser.add_argument('--inventory', type=Path, help='optional convert.py comparison for the same commits')
    parser.add_argument('--dependency', action='append', type=Path, default=[], help='addon checkout at HEAD, such as Units')
    parser.add_argument('--base-database', type=Path, help='check stock resource aliases before reporting dependency mismatches')
    parser.add_argument('--workshop-addons', type=Path, help='report missing refs against the same provider databases as the installer')
    parser.add_argument('--historical-workshop', action='append', type=Path, default=[],
                        help='resolve declared version pins from an archived Workshop directory')
    parser.add_argument('--report', required=True, type=Path, help='JSON file with per-mission paths and line numbers')
    parser.add_argument('--summary', type=Path, help='optional Markdown table')
    parser.add_argument('--mission', action='append', help='exact legacy launcher path, repeat to select a batch')
    parser.add_argument('--patch', type=Path, help='write the Git diff for the selected missions')
    parser.add_argument('--draft-stage', type=Path, help='overlay and verify a batch staging directory using --inventory')
    args = parser.parse_args()
    if args.patch and not args.mission:
        parser.error('--patch requires at least one --mission')
    if args.draft_stage and not args.inventory:
        parser.error('--draft-stage requires the batch --inventory')
    if args.workshop_addons and not args.base_database:
        parser.error('--workshop-addons requires --base-database')
    if args.historical_workshop and not args.workshop_addons:
        parser.error('--historical-workshop requires --workshop-addons')
    source = Snapshot(args.missions, args.source)
    target = Snapshot(args.missions, args.dev)
    notes = {}
    if args.inventory:
        inventory = json.loads(args.inventory.read_text(encoding='utf-8'))
        if (inventory.get('source'), inventory.get('target')) != (source.commit, target.commit):
            parser.error('the inventory describes different commits')
        notes = {world: {**row, 'batchDraft': inventory.get('batchMigration', {}).get('worlds', {}).get(world, {})}
                 for world, row in inventory['worlds'].items()}
    if args.draft_stage:
        try:
            target = overlay(target, args.draft_stage, inventory['batchMigration']['sha256'])
        except (OSError, KeyError, ValueError) as exc:
            parser.error(str(exc))
    headers = set(source.headers())
    selected = set(args.mission) if args.mission else headers
    if selected - headers:
        parser.error('unknown launcher: ' + ', '.join(sorted(selected - headers)))
    dependencies = defaultdict(list)
    checkouts = [Snapshot(addon, 'HEAD') for addon in args.dependency]
    stock = StockPrefabs(args.base_database) if args.base_database else None
    missions = {}
    if args.workshop_addons:
        try:
            databases = resource_databases(args.base_database, args.workshop_addons)
            dependencies, providers, unavailable = dependency_resources(target, checkouts, databases)
            cohorts = defaultdict(list)
            for header in selected:
                try:
                    pins = required_addon_versions(target.read(header)) if header in target.files else ()
                except ValueError as exc:
                    missions[header] = compare_mission(source, target, header, notes, dependencies, stock,
                                                       provider_issue=str(exc))
                else:
                    cohorts[pins].append(header)
            for pins, group in sorted(cohorts.items()):
                chosen_dependencies, chosen_providers = dependencies, providers
                provider_issue = None
                if pins:
                    try:
                        chosen = pinned_databases(databases, args.historical_workshop, pins)
                        chosen_dependencies, chosen_providers, missing = dependency_resources(
                            target, checkouts, chosen, additional_roots=tuple(dict(pins)))
                        mounted = {project[0] for path in chosen_providers if (project := database_project(path))}
                        if inactive := set(dict(pins)) - mounted:
                            raise ValueError('pinned addon outside the reachable dependency graph: ' + ', '.join(sorted(inactive)))
                        if newly_missing := set(missing) - set(unavailable):
                            raise ValueError('pinned graph has unavailable dependencies: ' + ', '.join(sorted(newly_missing)))
                    except (OSError, ValueError) as exc:
                        provider_issue = str(exc)
                        chosen_dependencies, chosen_providers = dependencies, providers
                references = set()
                for header in group:
                    try:
                        world, _, _ = launcher(source, target, header)
                    except ValueError:
                        continue
                    if header in target.files and world in target.files:
                        for path in closure(target, world, header):
                            references.update((guid.upper(), name) for guid, name in facts(target, path)[2])
                unresolved = unregistered_references(
                    {(guid, path) for guid, path in references
                     if path not in (target.resources.get(guid) or chosen_dependencies.get(guid, []))}, chosen_providers)
                longmoor = LONGMOOR_PARENT in unresolved and has_reviewed_longmoor(chosen_providers)
                zagoria_failure = None
                if ZAGORIA_BMP2 in references and ZAGORIA_BMP2 not in unresolved:
                    zagoria_failure = known_zagoria_failure(stock, chosen_providers)
                if pins and not provider_issue:
                    provider_issue = known_compile_failure(stock, chosen_providers)
                for header in group:
                    terrain_issue = None
                    if not provider_issue and pins:
                        world_match = WORLD_REF.search(target.read(header))
                        if world_match and (world := world_match[3]) in target.files:
                            parent_refs = {(guid.upper(), path) for guid, path in REF.findall(target.read(world))}
                            terrain_issue = known_terrain_failure(stock, chosen_providers, parent_refs)
                    issue = provider_issue or terrain_issue
                    missions[header] = compare_mission(source, target, header, notes, chosen_dependencies, stock,
                                                       unresolved, longmoor, issue, zagoria_failure)
                    if pins and not issue:
                        missions[header]['auditedAddonVersions'] = dict(pins)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    else:
        for checkout in checkouts:
            for resource, paths in checkout.resources.items():
                dependencies[resource].extend(path for path in paths if path not in dependencies[resource])
        missions = {header: compare_mission(source, target, header, notes, dependencies, stock)
                    for header in selected}
    missions = dict(sorted(missions.items()))
    report = {'source': source.commit, 'target': target.commit, 'missions': missions}
    if notes:
        worlds = {mission['world'] for mission in missions.values() if mission.get('world')} if args.mission else notes
        report['outcomeFlagLeads'] = outcome_flag_leads(source, notes, worlds)
        report['scriptedInstructionLeads'] = scripted_instruction_leads(source, notes, worlds)
    if args.draft_stage:
        report['includesDraft'] = True
    if args.workshop_addons:
        report['unavailableDependencyIds'] = unavailable
        report['unresolvedResources'] = external_resource_review(missions, databases, providers)
        if notes:
            leads = source_dependency_leads(source, missions, notes, dependencies, databases, providers)
            report['sourceDependencyLeads'] = leads
            report['blockedLoadoutParents'] = blocked_loadout_parents(source, missions, notes, leads)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if args.summary:
        args.summary.write_text(markdown(source, target, missions), encoding='utf-8')
    if args.patch:
        paths = {path for mission in missions.values() for status, files in mission['files'].items()
                 if status != 'same' for path in files}
        patch = b''
        if paths and args.draft_stage:
            patch = draft_diff(source, target, paths)
        elif paths:
            patch = subprocess.check_output(['git', '-C', str(args.missions), '--literal-pathspecs', 'diff',
                                             '--no-ext-diff', source.commit, target.commit, '--', *sorted(paths)])
        args.patch.write_bytes(patch)
    print(f'Reviewed file inventory for {len(missions)} launchers')


if __name__ == '__main__':
    main()
