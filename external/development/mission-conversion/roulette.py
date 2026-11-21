"""Prepare Roulette's shared authored data and native static briefings."""
import argparse
import json
from pathlib import Path
import re

from briefings import ALLOWED, STANDARD_DESCRIPTION, block_end, guid, page, properties, render
from boundaries import DEST as AO_PREFAB
from catalog import REF, Snapshot
from dependencies import dependency_resources
from groups import inheritance, scalar
from install_batch import resource_databases, unregistered_references
from launchers import registered_identity


PREFIX = 'worlds/Burn0ut7/Roulette/'
PREFABS = PREFIX + 'Prefabs/'
MODE = PREFABS + 'Roulette_GameMode.et'
MANAGER = PREFABS + 'RouletteManager.et'
MODE_PARENT = '{D853E37AB4CCB3A3}' + MODE
LEGACY_LOBBY = '{9C2001FE7A2F2281}Prefabs/MP/Modes/PS_GameMode_Lobby.et'
MANAGER_PARENT = '{6E2C9E0BE9F314EC}' + MANAGER
SCENARIO = re.compile(r'GC_R_AttackDefend "(\{[A-F0-9]{16}\})" \{')
SCENARIO_OVERRIDES = {'m_minAOSize'}
DRONE_CRATE = PREFABS + 'Roulette_DroneCrate.et'
DRONE_CRATE_ID = '06CEA02208872944'
DRONE_CRATE_BLOB = '37ae04c727a5a8a5b028789d4576013005a908cb'
DRONE_ID = '77CA4803A4A2B8E3'
DRONE_PATH = 'Prefabs/Drones/FPVBase/FPVRPG.et'
DEV_DRONE_PATH = 'Prefabs/Drones/FPVBase/FPVRpg.et'
CAF_GROUPS = {
    '0C17A1C879905D5A': 'RifleSection',
    '4B540935B956E11F': 'CompanyHQ',
    '977831D0F745FF87': 'PlatoonHQ',
}
CAF_PATH = 'Prefabs/Groups/BLUFOR/GC_CAF/1987 Canadian Army/'
PAPER_MAP = 'Prefabs/Items/Equipment/Maps/PaperMap_01_folded.et'
PAPER_MAP_BLOB = 'ef2d6793df224c5617f480f3baf93bdacb4e4740'
PAPER_MAP_META = '62e50ac09ac7293fd1201befdaa3e46c34def4a8'
CATALOGUE = PREFIX + 'RouletteCatalogue.conf'
TYPES = {
    'GC_R_Team': 'GC_RouletteTeam',
    'GC_R_Company': 'GC_RouletteCompany',
    'GC_R_Platoon': 'GC_RoulettePlatoon',
    'GC_R_Squad': 'GC_RouletteSquad',
    'GC_R_Briefing': 'GC_RouletteBriefing',
}


def entity(lines, kind, *, required=False):
    definition = re.compile(rf'{kind}(?: : {REF.pattern})? \{{')
    matches = [i for i, line in enumerate(lines) if definition.fullmatch(line.strip())]
    if len(matches) > 1 or (required and not matches):
        raise ValueError(f'expected a unique {kind} entity')
    if matches:
        start = matches[0]
        return start, lines[start + 1:block_end(lines, start)]
    return None, []


def scenarios(lines):
    starts = [index for index, line in enumerate(lines) if line.strip() == 'm_scenarios {']
    if not starts:
        return {}
    if len(starts) != 1:
        raise ValueError('multiple Roulette scenario arrays')
    outer = starts[0]
    end = block_end(lines, outer)
    result = {}
    index = outer + 1
    while index < end:
        entry = SCENARIO.fullmatch(lines[index].strip())
        if not entry or entry[1] in result:
            raise ValueError(f'unsupported or duplicate scenario entry: {lines[index]}')
        stop = block_end(lines, index)
        indent = len(lines[index]) - len(lines[index].lstrip())
        result[entry[1]] = [line[indent + 1:] for line in lines[index + 1:stop]]
        index = stop + 1
    return result


def prepare(source):
    base_mode = source.read(MODE).splitlines()
    _, base_briefing = entity(base_mode, 'PS_MissionDescription', required=True)
    shared = scenarios(source.read(MANAGER).splitlines())
    if not shared:
        raise ValueError('no shared Roulette scenarios')
    result, briefings = [], {}
    for header in sorted(source.headers()):
        if not header.startswith('Missions/Burn0ut7/Roulette'):
            continue
        world_ref = re.search(r'^\s*World (' + REF.pattern + r')$', source.read(header), re.M)
        if not world_ref:
            raise ValueError(f'missing world for {header}')
        world = world_ref[3]
        if not world.startswith(PREFIX) or source.resources.get(world_ref[2]) != [world]:
            raise ValueError(f'inconsistent world registration in {header}')
        layers = sorted(source.layers[world.lower()])
        defaults = [name for name in layers if name.endswith('/default.layer')]
        if len(defaults) != 1:
            raise ValueError(f'expected a unique default layer for {world}')
        layer = defaults[0]
        lines = source.read(layer).splitlines()
        modes = [match for line in lines if
                 (match := re.fullmatch(r'PS_GameModeCoop \w+ : "([^"]+)" \{', line))]
        if len(modes) != 1 or modes[0][1] not in (MODE_PARENT, LEGACY_LOBBY):
            raise ValueError(f'unsupported Roulette mode in {layer}')
        uses_shared_mode = modes[0][1] == MODE_PARENT
        manager_index, manager = entity(lines, 'GC_R_Manager', required=not uses_shared_mode)
        if not uses_shared_mode and f'"{MANAGER_PARENT}"' not in lines[manager_index]:
            raise ValueError(f'unsupported manager parent in {layer}')
        overrides = scenarios(manager)
        if not overrides.keys() <= shared.keys():
            raise ValueError(f'unknown scenario overlay in {layer}')
        overrides = {key: properties(fields, SCENARIO_OVERRIDES) for key, fields in overrides.items()}
        index, local_briefing = entity(lines, 'PS_MissionDescription', required=not uses_shared_mode)
        if not uses_shared_mode:
            if f'"{STANDARD_DESCRIPTION}"' not in lines[index]:
                raise ValueError(f'unsupported briefing parent in {layer}')
            briefing_fields = local_briefing
        else:
            # Keep the inherited description with local overrides of its named fields.
            merged = properties(base_briefing, ALLOWED) | properties(local_briefing, ALLOWED)
            briefing_fields = '\n'.join(f'{key} {value}' for key, value in merged.items()).splitlines()
        origin = f'{layer}:{index + 1}' if index is not None else MODE
        command_page = page(briefing_fields, origin)
        briefings[world.removesuffix('.ent') + '_GC_Settings.et'], _ = render(world, [command_page])
        result.append({
            'header': header,
            'world': world_ref[1][1:-1],
            'sourceBlobs': {name: source.files[name] for name in [header, world, *layers]},
            'scenarioOverrides': overrides,
        })
    if not result:
        raise ValueError('no authored Roulette launchers')
    report = {
        'source': source.commit,
        'sharedManagerBlob': source.files[MANAGER],
        'sharedModeBlob': source.files[MODE],
        'scenarios': [{'id': key, 'authoredFields': '\n'.join(fields),
                       'resourceReferences': sorted({f'{{{guid}}}{path}' for guid, path in
                                                     REF.findall('\n'.join(fields))})}
                      for key, fields in shared.items()],
        'missions': result,
    }
    return report, briefings


def resolve_prefabs(report, source, target, legacy_units, units, checkouts, databases):
    """Check generated references and stage the reviewed mission-owned asset."""
    resources, providers, unavailable = dependency_resources(target, [units, *checkouts], databases)
    if unavailable:
        raise ValueError('unavailable Dev addon IDs: ' + ', '.join(unavailable))
    if (source.files.get(DRONE_CRATE) != DRONE_CRATE_BLOB
            or not registered_identity(source, DRONE_CRATE_ID, DRONE_CRATE)):
        raise ValueError('the native Roulette inventory crate changed')

    aliases = {}
    for guid, name in CAF_GROUPS.items():
        path = f'{CAF_PATH}Group_CAF_87_{name}.et'
        if legacy_units.resources.get(guid) != [path] or not registered_identity(units, guid, path):
            raise ValueError(f'Canadian group registration changed for {path}')
        old_closure = legacy_units.closure(path)
        if (units.read(path).replace(' m_sGC_Description ', ' m_sCustomNameSet ') != legacy_units.read(path)
                or units.closure(path) != old_closure | {PAPER_MAP}
                or any(legacy_units.files[p] != units.files[p] for p in old_closure - {path})
                or units.files.get(PAPER_MAP) != PAPER_MAP_BLOB
                or units.files.get(PAPER_MAP + '.meta') != PAPER_MAP_META):
            raise ValueError(f'Canadian group roster or inherited prefab changed for {path}')
        aliases[guid, f'{CAF_PATH}Group_CAF_{name}_P.et'] = path

    def resolve(match):
        guid, path = match.groups()
        return f'"{{{guid}}}{aliases.get((guid, path), path)}"'

    drone_ref = f'{{{DRONE_ID}}}{DRONE_PATH}'.encode()
    crate = source.read_raw(DRONE_CRATE)
    if crate.count(drone_ref) != 1:
        raise ValueError('expected one authored drone inventory reference')
    crate = crate.replace(drone_ref, f'{{{DRONE_ID}}}{DEV_DRONE_PATH}'.encode())
    refs = set(REF.findall(crate.decode('utf-8-sig')))
    for row in report['scenarios']:
        row['devFields'] = REF.sub(resolve, row['authoredFields'])
        refs.update(REF.findall(row['devFields']))
    staged = {(DRONE_CRATE_ID, DRONE_CRATE)}
    owned_files = {DRONE_CRATE: crate, DRONE_CRATE + '.meta': source.read_raw(DRONE_CRATE + '.meta')}
    if DRONE_CRATE_ID in target.resources and not registered_identity(target, DRONE_CRATE_ID, DRONE_CRATE):
        raise ValueError(f'Dev registration conflicts with {DRONE_CRATE}')
    if any(p in target.files and target.read_raw(p) != contents for p, contents in owned_files.items()):
        raise ValueError(f'Dev already contains different files for {DRONE_CRATE}')
    unresolved = unregistered_references(
        {(guid, path) for guid, path in refs - staged if path not in resources.get(guid, ())
         and path not in target.resources.get(guid, ())}, providers)
    if unresolved:
        raise ValueError('unresolved generated prefab references: ' + ', '.join(
            f'{{{guid}}}{path}' for guid, path in sorted(unresolved)))
    report['devPrefabAudit'] = {
        'target': target.commit,
        'legacyUnits': legacy_units.commit,
        'units': units.commit,
        'dependencyRevisions': {snapshot.repo.name: snapshot.commit for snapshot in checkouts},
        'resourceCount': len(refs),
        'aliases': [{'guid': guid, 'sourcePath': path, 'devPath': replacement}
                    for (guid, path), replacement in sorted(
                        {**aliases, (DRONE_ID, DRONE_PATH): DEV_DRONE_PATH}.items())],
        'missionOwnedSourceBlobs': {p: source.files[p] for p in owned_files},
    }
    return owned_files


def native_configuration(report, units):
    files = {}

    def add_resource(path, lines, kind='ResourceClass'):
        identity = guid('roulette/config/' + path)
        files[path] = '\n'.join([*lines, '']).encode()
        files[path + '.meta'] = (
            'MetaFileClass {\n'
            f' Name "{{{identity}}}{path}"\n'
            ' Configurations { ' + ' '.join(
                f'{kind} {platform} {{}}' for platform in
                ('PC', 'XBOX_ONE : PC', 'XBOX_SERIES : PC', 'PS4 : PC', 'PS5 : PC', 'HEADLESS : PC'))
            + ' }\n'
            '}\n').encode()
        return f'{{{identity}}}{path}'

    catalogue = ['GC_RouletteCatalogue {', ' m_aScenarios {']
    indices = {}
    playable_wrappers = {}
    for index, row in enumerate(report['scenarios']):
        indices[row['id']] = index
        catalogue.append(f'  GC_RouletteScenario "{row["id"]}" {{')
        for line in row['devFields'].splitlines():
            match = re.match(r'^(\s*)(GC_R_\w+)(?= "\{[A-F0-9]{16}\}" \{$)', line)
            if match:
                if match[2] not in TYPES:
                    raise ValueError('unknown native Roulette type: ' + match[2])
                line = match[1] + TYPES[match[2]] + line[match.end():]
            elif line.lstrip().startswith('m_prefab "{'):
                reference = REF.search(line)
                if not reference or units.resources.get(reference[1]) != [reference[2]]:
                    raise ValueError('cannot verify generated group identity: ' + line)
                if scalar(inheritance(units, reference[1]), 'm_bPlayable') != 1:
                    if reference[0] not in playable_wrappers:
                        wrapper = PREFABS + 'Playable/' + reference[1] + '.et'
                        playable_wrappers[reference[0]] = add_resource(
                            wrapper, [f'SCR_AIGroup : {reference[0]} {{', ' m_bPlayable 1', '}'],
                            'EntityTemplateResourceClass')
                    line = line[:reference.start()] + f'"{playable_wrappers[reference[0]]}"' + line[reference.end():]
            catalogue.append('   ' + line)
        catalogue.append('  }')
    catalogue_ref = add_resource(CATALOGUE, [*catalogue, ' }', '}'])
    for mission in report['missions']:
        path = mission['world'][18:].removesuffix('.ent') + '_Roulette.conf'
        lines = ['GC_RouletteMap {', f' m_sCatalogue "{catalogue_ref}"']
        if mission['scenarioOverrides']:
            lines.append(' m_aOverrides {')
            for identity, fields in mission['scenarioOverrides'].items():
                lines.extend([f'  GC_RouletteScenarioOverride "{identity}" {{',
                              f'   m_iScenarioIndex {indices[identity]}'])
                lines.extend(f'   {name} {value}' for name, value in fields.items())
                lines.append('  }')
            lines.append(' }')
        mission['nativeConfig'] = add_resource(path, [*lines, '}'])
    report['playableGroupWrappers'] = {source[1:-1]: native for source, native in playable_wrappers.items()}
    return files


def attach_startup_components(report, files):
    suffix = b' }\n}\n'
    for mission in report['missions']:
        world = mission['world'][18:]
        path = world.removesuffix('.ent') + '_GC_Settings.et'
        if not files[path].endswith(suffix):
            raise ValueError('unexpected Roulette settings layout: ' + path)
        command_order = b'     m_iOrder 0\n'
        if files[path].count(command_order) != 1:
            raise ValueError('expected one authored command page: ' + path)
        settings = files[path].replace(command_order, b'     m_iOrder 31\n')
        component_id = guid(world + '/roulette/setup')
        observer_id = guid(world + '/roulette/casualties')
        component = (f'  GC_RouletteSetupComponent "{{{component_id}}}" {{\n'
                     f'   m_sMapConfig "{mission["nativeConfig"]}"\n'
                     '  }\n'
                     f'  GC_PeakFactionPopulationComponent "{{{observer_id}}}" {{\n'
                     '  }\n').encode()
        boundary = [
            f'GC_AOLimitEntity GC_RouletteBoundary : "{AO_PREFAB}" {{',
            ' components {',
            '  GC_BoundaryWallComponent "{6A4D831C2E5079C1}" {',
            '   m_bEnabled 0',
            '  }',
            '  GC_MapShapeComponent "{8F470B543A20D964}" {',
            '   m_bEnabled 0',
            '   m_bFillEnabled 1',
            '   m_LineColor 0 0 0 1',
            '   m_fLineWidth 3',
            '   m_FillColor 0 0 0 0.75',
            '   m_bInvertFill 1',
            '  }',
            ' }',
            ' m_bEnabled 0',
            ' Points {',
        ]
        for index, position in enumerate(('0 0 0', '0 0 10', '10 0 10', '10 0 0')):
            point_id = guid(f'{world}/roulette/ao/{index}')
            boundary.extend([f'  ShapePoint "{{{point_id}}}" {{', f'   Position {position}', '  }'])
        boundary.extend([' }', '}', ''])
        boundary_path = world.removesuffix('.ent') + '_Layers/GC_RouletteBoundary.layer'
        files[boundary_path] = '\n'.join(boundary).encode()
        files[path] = settings[:-len(suffix)] + component + suffix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', type=Path, required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-database', type=Path)
    parser.add_argument('--workshop-addons', type=Path)
    parser.add_argument('--units', type=Path)
    parser.add_argument('--units-source')
    parser.add_argument('--dependency', type=Path, action='append', default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    if any(output.is_relative_to(root) for root in (args.missions.resolve(), Path(__file__).resolve().parents[3])):
        parser.error('stage Roulette outside the checkouts')
    if bool(args.base_database) != bool(args.units and args.units_source) or (
            not args.base_database and (args.units or args.units_source or args.workshop_addons or args.dependency)):
        parser.error('use --base-database, --units and --units-source together for prefab checks')
    source = Snapshot(args.missions, args.source)
    report, briefings = prepare(source)
    files = {path: text.encode('utf-8') for path, text in briefings.items()}
    if args.base_database:
        databases = resource_databases(args.base_database, args.workshop_addons)
        units = Snapshot(args.units, 'HEAD')
        files.update(resolve_prefabs(
            report, source, Snapshot(args.missions, 'HEAD'), Snapshot(args.units, args.units_source),
            units, [Snapshot(repo, 'HEAD') for repo in args.dependency], databases))
        files.update(native_configuration(report, units))
        attach_startup_components(report, files)
    output.mkdir(parents=True, exist_ok=False)
    for name, contents in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    (output / 'roulette.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f"Prepared {len(briefings)} briefing presets and {len(report['scenarios'])} shared scenarios.")


if __name__ == '__main__':
    main()
