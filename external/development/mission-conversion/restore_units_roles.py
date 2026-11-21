"""Stage previously shipped Units loadouts with their original equipment and native parents."""
import argparse
from pathlib import Path
import re
from subprocess import CalledProcessError

from briefings import guid
from catalog import REF, Snapshot
from groups import only_path, stock_character
from install_batch import resource_databases, unresolved_resources
from launchers import META_NAME, replace_reference
from local_loadouts import CHARACTER, LEGACY, migrate_playability, unchanged_units_parent
from scenery import StockPrefabs


ARCHIVE = 'cda6d96940cd5fca0984087f7d2e58553d9bacb3'
MSV85_ARCHIVE = '08b79772e2f628b8d52dd6c1b5a0321e2d9ef6cc'
MSV85_DIRECTORY = 'Prefabs/Characters/Factions/OPFOR/USSR_Army/MotorRifle_MSV85A/'
DIRECTORY = 'Prefabs/Characters/Factions/OPFOR/USSR_Army/MotorRifle_MSV80/Legacy/'
DEPUTY = '07299990D00B0823'
MEDIC = 'D1BA3D1164545A8D'
OLD_LEADER = 'DF8E65CA90B55E3B'
AUTHORED_DIRECTORY = 'Prefabs/Characters/Factions/OPFOR/USSR_Army/MotorRifle_1980/'
AUTHORED_PATHS = {
    OLD_LEADER: AUTHORED_DIRECTORY + 'Character_USSR_MSV80_PL_P.et',
    DEPUTY: AUTHORED_DIRECTORY + 'Character_USSR_MSV80_DPL_P.et',
    MEDIC: AUTHORED_DIRECTORY + 'Character_USSR_MSV80_Medic_P.et',
}
RESTORED = {
    OLD_LEADER: (guid('Units/legacy/' + OLD_LEADER), DIRECTORY + 'Character_USSR_Legacy_MSV80_PL.et'),
    DEPUTY: (DEPUTY, DIRECTORY + 'Character_USSR_Legacy_MSV80_DPL.et'),
    MEDIC: (MEDIC, DIRECTORY + 'Character_USSR_Legacy_MSV80_Medic.et'),
}


def historical_prefabs(repo, stock):
    archive = Snapshot(repo, ARCHIVE)
    files = {}
    for original, (resource, destination) in RESTORED.items():
        path = only_path(archive, original)
        body = archive.read(path)
        header = body.partition('\n')[0]
        if not CHARACTER.fullmatch(header):
            raise ValueError('unsupported archived role ' + original)
        parent = REF.search(header)
        if original == DEPUTY and parent[1].upper() == OLD_LEADER:
            native = RESTORED[OLD_LEADER]
        else:
            native = stock_character(parent[1], parent[2], stock)
        if not native:
            raise ValueError('unverified archived role parent ' + parent[2])
        updated, _ = migrate_playability(body + ('\n' if not body.endswith('\n') else ''), inherited_playable=True)
        if LEGACY.search(updated):
            raise ValueError('unsupported legacy role behavior ' + original)
        files[destination] = updated.replace(parent[0], f'"{{{native[0]}}}{native[1]}"', 1)
        files[destination + '.meta'] = replace_reference(archive.read(path + '.meta'), META_NAME, resource, destination)
    return files


def verified_roles(units, stock):
    expected = {resource: path for resource, path in RESTORED.values()}
    if not stock or any(units.resources.get(guid) != [path] for guid, path in expected.items()):
        return {}
    for path, body in historical_prefabs(units.repo, stock).items():
        if path not in units.files or units.read(path) != body:
            raise ValueError('archived loadout differs from the reviewed recovery: ' + path)
    return {(original, path): RESTORED[original] for original, path in AUTHORED_PATHS.items()}


def verified_msv85_wrappers(source, units):
    archive = Snapshot(units.repo, MSV85_ARCHIVE)
    result = {}
    for resource, paths in archive.resources.items():
        if len(paths) != 1:
            continue
        path, = paths
        if not path.startswith(MSV85_DIRECTORY + 'P/') or not path.endswith('_P.et'):
            continue
        body = archive.read(path)
        header, _, fields = body.partition('\n')
        if not CHARACTER.fullmatch(header) or not re.fullmatch(
                r' ID "[\dA-F]{16}"\n components \{\n'
                r'  PS_PlayableComponent "\{5EFCC1946F475F15\}" \{\n'
                r'   m_bIsPlayable 1\n  \}\n \}\n\}', fields.strip('\n')):
            continue
        parent = REF.search(header)[1].upper()
        # The wrapper only opted the existing loadout into slot selection.
        # Require the archived, published Units and Dev loadout chains to agree.
        if not (unchanged_units_parent(archive, units, parent) and unchanged_units_parent(source, units, parent)):
            continue
        native = (parent, only_path(units, parent))
        meta = META_NAME.search(archive.read(path + '.meta'))
        if not meta or meta[2].upper() != resource:
            raise ValueError('unexpected archived role metadata: ' + path)
        for authored in (path, MSV85_DIRECTORY + Path(path).name, meta[3]):
            result[(resource, authored)] = native
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--units', required=True, type=Path)
    parser.add_argument('--base-database', required=True, type=Path)
    parser.add_argument('--workshop-addons', required=True, type=Path)
    parser.add_argument('--dependency', action='append', type=Path, default=[])
    parser.add_argument('--stage', required=True, type=Path)
    args = parser.parse_args()
    try:
        units = Snapshot(args.units, 'HEAD')
        stock = StockPrefabs(args.base_database)
        output = {path: text.encode() for path, text in historical_prefabs(args.units, stock).items()}
        for resource, path in RESTORED.values():
            if units.resources.get(resource) not in (None, [path]):
                raise ValueError('Units already registers ' + resource + ' at another path')
        for path, body in output.items():
            if path in units.files and units.read(path).encode() != body:
                raise ValueError('Units already has a differing ' + path)
        checkouts = [Snapshot(path, 'HEAD') for path in args.dependency]
        missing = unresolved_resources(output, units, checkouts,
                                       resource_databases(args.base_database, args.workshop_addons))
        if missing:
            raise ValueError('unresolved archived loadout references: '
                             + ', '.join(f'{{{guid}}}{path}' for guid, path in sorted(missing)))
        stage = args.stage.resolve()
        if stage.is_relative_to(args.units.resolve()):
            raise ValueError('use a staging directory outside the Units addon')
        for path, body in output.items():
            target = stage / path
            if target.exists() and target.read_bytes() != body:
                raise ValueError('staging directory already contains a differing ' + path)
        for path, body in output.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        print(f'Checked and staged {len(output) // 2} historical Units prefabs')
    except (OSError, ValueError, CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
