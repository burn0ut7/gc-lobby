"""Prepare Roulette's shared authored data and native static briefings."""
import argparse
import json
from pathlib import Path
import re

from briefings import ALLOWED, STANDARD_DESCRIPTION, block_end, page, properties, render
from catalog import REF, Snapshot


PREFIX = 'worlds/Burn0ut7/Roulette/'
PREFABS = PREFIX + 'Prefabs/'
MODE = PREFABS + 'Roulette_GameMode.et'
MANAGER = PREFABS + 'RouletteManager.et'
MODE_PARENT = '{D853E37AB4CCB3A3}' + MODE
LEGACY_LOBBY = '{9C2001FE7A2F2281}Prefabs/MP/Modes/PS_GameMode_Lobby.et'
MANAGER_PARENT = '{6E2C9E0BE9F314EC}' + MANAGER
SCENARIO = re.compile(r'GC_R_AttackDefend "(\{[A-F0-9]{16}\})" \{')
SCENARIO_OVERRIDES = {'m_minAOSize'}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', type=Path, required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if any(output.is_relative_to(root) for root in (args.missions.resolve(), Path(__file__).resolve().parents[3])):
        parser.error('stage Roulette outside the checkouts')
    report, briefings = prepare(Snapshot(args.missions, args.source))
    output.mkdir(parents=True, exist_ok=False)
    for name, contents in briefings.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding='utf-8', newline='\n')
    (output / 'roulette.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f"Prepared {len(briefings)} briefing presets and {len(report['scenarios'])} shared scenarios.")


if __name__ == '__main__':
    main()
