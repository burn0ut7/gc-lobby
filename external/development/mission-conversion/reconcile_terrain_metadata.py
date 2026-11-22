"""Record saved subscene terrain providers in existing native mission snapshots."""
import argparse
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess

from briefings import block_end, properties
from catalog import REF, Snapshot
from dependencies import BASE_GAME, database_project, dependency_selection, pinned_databases
from install_batch import resource_databases
from install_metadata import apply_changes, require_clean
from launchers import GENERATED, WORLD_REF, required_addon_versions
from scenery import StockPrefabs
from terrain_parents import LONGMOOR_PARENT, LONGMOOR_PROVIDER, has_reviewed_longmoor, is_longmoor_subscene

SUBSCENE = re.compile(r'\s*SubScene \{\s*Parent ' + REF.pattern + r'\s*\}\s*')
ADDON_ROW = re.compile(r'   GC_MissionMetadataAddon(?: "[^"]+")? \{')
ADDON_ID = re.compile(r'"([A-Fa-f0-9]{16})"')


@lru_cache(maxsize=128)
def registry(path):
    return StockPrefabs(path)


@lru_cache(maxsize=16)
def compatible_longmoor(providers):
    return has_reviewed_longmoor(providers)


def terrain_provider(dev, world, checkouts, providers):
    visited = set()
    while world in dev.files:
        if world in visited:
            raise ValueError(f'cyclic saved terrain ancestry at {world}')
        visited.add(world)
        body = dev.read(world)
        ref = SUBSCENE.fullmatch(body)
        if not ref:
            if body.lstrip().startswith('SubScene'):
                raise ValueError(f'unrecognized subscene parent in {world}')
            return None
        guid, path = ref[1].upper(), ref[2]
        if dev.resources.get(guid) == [path]:
            world = path
            continue
        owners = {identity for identity, checkout in checkouts if checkout.resources.get(guid) == [path]}
        for index, database in enumerate(providers):
            if registry(database).contains(guid, path):
                project = database_project(database)
                if project:
                    owners.add(project[0])
                elif index == 0:
                    owners.add(BASE_GAME)
        if (not owners and (guid, path) == LONGMOOR_PARENT and is_longmoor_subscene(world, body)
                and compatible_longmoor(tuple(providers))):
            owners.update(database_project(db)[0] for db in providers if db.parent.name == LONGMOOR_PROVIDER)
        if len(owners) != 1:
            raise ValueError(f'{world} needs one exact terrain provider for {{{guid}}}{path}: {sorted(owners)}')
        owner, = owners
        return None if owner in (BASE_GAME, '5614BBCCBB55ED1C') else (owner, f'{{{guid}}}{path}')
    raise ValueError(f'saved terrain world missing: {world}')


def add_captured_provider(body, addon, world):
    """Edit only the captured reference array, using the name recorded by Workbench."""
    lines = body.decode('utf-8').splitlines(keepends=True)
    text = [line.rstrip('\r\n') for line in lines]
    start, = [i for i, line in enumerate(text) if GENERATED.fullmatch(line)]
    stop = block_end(text, start)
    metadata = text[start + 1:stop]
    if ('  m_iSchemaVersion 2' not in metadata or f'  m_sWorld "{world}"' not in metadata
            or '  m_sResourceCoverage "authored_container_graph_partial"' not in metadata):
        raise ValueError('snapshot schema, world or resource coverage does not match the launcher')
    table, = [i for i in range(start + 1, stop) if text[i] == '  m_aLoadedAddons {']
    references, = [i for i in range(start + 1, stop) if text[i] == '  m_aReferencedAddonIds {']
    end = block_end(text, references)
    row = table + 1
    table_end = block_end(text, table)
    names = {}
    while row < table_end:
        if not ADDON_ROW.fullmatch(text[row]):
            raise ValueError('unrecognized captured addon record')
        close = block_end(text, row)
        data = properties(text[row + 1:close], {
            'm_sId', 'm_sGuid', 'm_sTitle', 'm_bVanilla', 'm_bReferencedByPlacedPrefab', 'm_bReferencedByResource',
        })
        guid = ADDON_ID.fullmatch(data.get('m_sGuid', ''))
        name = data.get('m_sId', '')
        if not guid or not re.fullmatch(r'"[^"\r\n]+"', name) or guid[1].upper() in names or name in names.values():
            raise ValueError('ambiguous captured addon identity')
        names[guid[1].upper()] = name
        row = close + 1
    if addon not in names:
        raise ValueError(f'terrain addon {addon} is missing from the Workbench snapshot')
    values = ' '.join(text[i].strip() for i in range(references + 1, end))
    entries = re.findall(r'"[^"\r\n]+"', values)
    if (re.sub(r'"[^"\r\n]+"', '', values).strip() or len(set(entries)) != len(entries)
            or set(entries) - set(names.values())):
        raise ValueError('invalid captured addon references')
    if names[addon] in entries:
        return body
    newline = '\r\n' if lines[references].endswith('\r\n') else '\n'
    lines.insert(end, '   ' + names[addon] + newline)
    return ''.join(lines).encode('utf-8')


def reconcile(dev, plan, checkouts, databases, archives):
    require_clean(dev)
    if plan['target'] != dev.commit:
        raise ValueError('the terrain audit describes a different Missions revision')
    scopes = {}
    changes = {}
    reviewed = {}
    root = dev.repo.resolve()
    for header, checks in sorted(plan['committedLauncherMetadata'].items()):
        if not checks['generatedSnapshot']:
            continue
        try:
            body = dev.read(header)
            world = WORLD_REF.search(body)
            if not world or not checks['registeredLauncher'] or not checks['registeredWorld'] or not checks['gcMissionHeader']:
                raise ValueError('unverified native launcher or world')
            pins = required_addon_versions(body)
            if pins not in scopes:
                scoped = pinned_databases(databases, archives, pins) if pins else databases
                local, providers, _ = dependency_selection(dev, checkouts, scoped, additional_roots=tuple(dict(pins)))
                scopes[pins] = local, providers
            result = terrain_provider(dev, world[3], *scopes[pins])
            if not result:
                continue
            addon, parent = result
            path = root / header
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('invalid checkout path')
            original = path.read_bytes()
            if original.replace(b'\r\n', b'\n') != dev.read_raw(header).replace(b'\r\n', b'\n'):
                raise ValueError('header differs from the audited commit')
            updated = add_captured_provider(original, addon, f'{{{world[2]}}}{world[3]}')
            reviewed[header] = {'world': world[3], 'parent': parent, 'addon': addon, 'changed': updated != original}
            if updated != original:
                changes[path] = original, updated
        except (KeyError, ValueError) as exc:
            raise ValueError(f'{header}: {exc}') from exc
    return changes, reviewed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--base-database', required=True, type=Path)
    parser.add_argument('--workshop-addons', required=True, type=Path)
    parser.add_argument('--dependency', action='append', default=[], type=Path)
    parser.add_argument('--historical-workshop', action='append', default=[], type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        dev = Snapshot(args.missions, 'HEAD')
        plan = json.loads(args.audit.read_text(encoding='utf-8'))
        databases = resource_databases(args.base_database, args.workshop_addons)
        checkouts = [Snapshot(path, 'HEAD') for path in args.dependency]
        changes, reviewed = reconcile(dev, plan, checkouts, databases, args.historical_workshop)
        if args.report:
            if args.report.resolve().is_relative_to(dev.repo.resolve()):
                raise ValueError('write the report outside the mission checkout')
            args.report.write_text(json.dumps({'target': dev.commit, 'terrains': reviewed}, indent=2) + '\n',
                                   encoding='utf-8')
        if args.apply:
            apply_changes(dev, changes)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(f'{len(reviewed)} external terrain providers verified; {len(changes)} snapshots '
          f'{"updated" if args.apply else "would change"}.')


if __name__ == '__main__':
    main()
