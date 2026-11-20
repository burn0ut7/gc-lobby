"""Apply common migration rules to legacy missions and report unsupported authoring."""
from collections import Counter
from hashlib import sha256
from pathlib import PurePosixPath
import re

from arc_capture import capture_instance
from boundaries import convert_boundaries
from briefings import block_end, guid, properties
from catalog import REF, SOURCE_TEXT
from extraction import condition as extraction_condition
from launchers import META_NAME, launcher
from markers import convert_layer

LEGACY = re.compile(r'\b(?:TILW|PS)_\w+')
MODE = re.compile(r'^PS_GameModeCoop(?: [\w.-]+)? : "\{4CFD54745CD45673\}[^"]+" \{$')
NATIVE = re.compile(r'^(?:\$grp )?(?:Vehicle|SCR_AIWorld|GenericEntity|GenericTerrainEntity|StaticModelEntity|Tree|'
                    r'DecalEntity|SCR_\w*(?:Waypoint|BuildingEntity|EnvironmentalEntity|RestrictionZoneEntity)|'
                    r'AIWaypointCycle|PolylineShapeEntity|SCR_MapEntity|ACE_TacticalLadderEntity)\b')
WEATHER = re.compile(r'  SCR_TimeAndWeatherHandlerComponent "\{5EE3229927D4D2F5\}" \{')
PERCEPTION = re.compile(r'^PerceptionManager(?: [\w.-]+)? : "\{028DAEAD63E056BE\}[^"]+" \{$')


def blocks(text):
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        if lines[index][0].isspace() or not lines[index].endswith(' {'):
            raise ValueError('unsupported layer syntax at line ' + str(index + 1))
        end = block_end(lines, index)
        yield index + 1, lines[index:end + 1]
        index = end + 1


TRIGGER = re.compile(r'(?P<group>\$grp )?TILW_(?P<kind>Extraction|Faction)TriggerEntity(?: [\w.-]+)?'
                     r' : "\{(?P<guid>192ABD6B9232E064|8C576D46F6579229)\}[^"\n]+" \{')


def area_rules(source, world):
    conditions, placements = {}, {}
    for layer in source.layers[world.lower()]:
        text = source.read(layer)
        if 'TILW_ExtractionTriggerEntity' not in text and 'TILW_FactionTriggerEntity' not in text:
            continue
        for line, block in blocks(text):
            match = TRIGGER.fullmatch(block[0])
            if not match:
                continue
            members = [block[1:-1]]
            if match['group']:
                members = []
                index = 1
                while index < len(block) - 1:
                    if not re.fullmatch(r' (?:(?:[\w.-]+) )?\{', block[index]):
                        raise ValueError('unsupported grouped area placement')
                    end = block_end(block, index)
                    members.append(block[index + 1:end])
                    index = end + 1
            result, areas = {}, []
            try:
                for fields in members:
                    flag_field = next(value.strip() for value in fields if value.strip().startswith('m_flagName '))
                    flag = flag_field.removeprefix('m_flagName ')
                    if not re.fullmatch(r'"\w+"', flag) or flag in conditions or flag in result:
                        raise ValueError('invalid or duplicate area flag')
                    area_name = 'GC_MigrationArea_' + flag[1:-1]
                    if match['kind'] == 'Extraction' and match['guid'] == '192ABD6B9232E064':
                        rule, area = extraction_rule(world, area_name, flag, fields)
                    elif match['kind'] == 'Faction' and match['guid'] == '8C576D46F6579229':
                        built, area, details = capture_instance(world, area_name, fields)
                        if not (0 < details['radiusMeters'] < float('inf') and 0.1 <= details['seconds'] <= 86400):
                            raise ValueError('invalid faction capture settings')
                        rule = built[flag]
                    else:
                        raise ValueError('inconsistent trigger identity')
                    result[flag] = rule
                    areas.append(area)
            except (StopIteration, ArithmeticError, KeyError, ValueError):
                continue
            conditions.update(result)
            placements[(layer, line)] = ({flag[1:-1] for flag in result}, ''.join(areas))
    return conditions, placements


def extraction_rule(world, area_name, flag, fields):
    data = properties(fields, {'coords', 'm_queryRadius', 'm_captureTime', 'm_flagName',
                               'm_stopAfterFirstChange', 'm_factionKey', 'm_ratioThreshold', 'm_playersOnly'})
    if data.get('m_stopAfterFirstChange') != '1' or data.get('m_playersOnly', '1') != '1':
        raise ValueError('unsupported extraction settings')
    faction, coords, radius = data['m_factionKey'], data['coords'], data['m_queryRadius']
    hold, ratio = data.get('m_captureTime', '0'), data['m_ratioThreshold']
    if not (re.fullmatch(r'"[\w.-]+"', faction) and 0 < float(radius) < float('inf')
            and (float(hold) == 0 or 0.1 <= float(hold) <= 86400)
            and 0 <= float(ratio) <= 1 and re.fullmatch(r'[-+\d.]+ [-+\d.]+ [-+\d.]+', coords)):
        raise ValueError('invalid extraction settings')
    check = extraction_condition(world, flag[1:-1], area_name, faction, ratio, hold if float(hold) else None)
    area = f'''GC_RadiusAreaEntity {area_name} : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
 coords {coords}
 m_fRadius {radius}
}}
'''
    return ['      ' + value for value in check.splitlines()], area


def restriction_zones(block, base):
    header = block[0].removeprefix('$grp ')
    resource = re.fullmatch(r'SCR_EditorRestrictionZoneEntity(?: ([\w.-]+))? : ("[^"\n]+") \{', header)
    if not resource:
        raise ValueError('unsupported restriction zone prefab')
    if not block[0].startswith('$grp '):
        name = resource[1] or f'GC_MigrationStartingZone{base}'
        output = '\n'.join([f'SCR_EditorRestrictionZoneEntity {name} : {resource[2]} {{', *block[1:]])
        return [name], output + '\n'
    names, output = [], []
    index = 1
    while index < len(block) - 1:
        head = re.fullmatch(r' (?:(\w+) )?\{', block[index])
        if not head:
            raise ValueError('unsupported grouped restriction zone')
        end = block_end(block, index)
        name = head[1] or f'GC_MigrationStartingZone{base + len(names)}'
        names.append(name)
        output.extend([f'SCR_EditorRestrictionZoneEntity {name} : {resource[2]} {{',
                       *(line[1:] for line in block[index + 1:end + 1])])
        index = end + 1
    return names, '\n'.join(output) + '\n'


def faction_overrides(lines):
    if (len(lines) < 7 or not re.fullmatch(r'  SCR_FactionManager "[\da-fA-F]{16}" \{', lines[1])
            or lines[2:4] != ['   ID "5ED9088735FF2D8B"', '   Factions {']
            or lines[-3:] != ['   }', '  }', ' }']):
        raise ValueError('custom legacy mode child entities')
    records = '\n'.join(lines[4:-3])
    if not records or LEGACY.search(records):
        raise ValueError('custom legacy faction overrides')
    return f''' {{
  SCR_FactionManager {{
   ID "6ADC9BB66394BE46"
   Factions + {{
{records}
   }}
  }}
 }}
'''


def render_mode(world, root, preset, additional_rules, zones):
    if not MODE.fullmatch(root[0]):
        raise ValueError('custom legacy game mode')
    weather, coords, freeze, children = '', None, 0, ''
    index = 1
    while index < len(root) - 1:
        line = root[index]
        if line == ' components {':
            end = block_end(root, index)
            if (end <= index + 2 or not WEATHER.fullmatch(root[index + 1])
                    or block_end(root, index + 1) != end - 1):
                raise ValueError('custom legacy game mode components')
            weather = '\n'.join(root[index + 1:end]) + '\n'
            for old, new in (('5EE3229927D4D2F5', '6B71E9A8B1A8C501'), ('m_bUseSpecifiedDate', 'm_bDateOverride'),
                             ('m_iSetDay', 'm_iDay'), ('m_iSetMonth', 'm_iMonth'), ('m_iSetYear', 'm_iYear')):
                weather = weather.replace(old, new)
            if LEGACY.search(weather) or re.search(r'\bm_f(?:FogDensity|FogHeight|Rain|Wind)\b|\bm_bOverrideEnvironment\b', weather):
                raise ValueError('custom legacy weather overrides')
            index = end
        elif re.fullmatch(r' coords [-+\d.]+ [-+\d.]+ [-+\d.]+', line):
            coords = line
        elif re.fullmatch(r' m_iFreezeTime \d+', line):
            freeze, fraction = divmod(int(line.split()[-1]), 1000)
            if fraction:
                raise ValueError('nonintegral staging seconds')
        elif line == ' {' and not children:
            end = block_end(root, index)
            children = faction_overrides(root[index:end + 1])
            index = end
        else:
            raise ValueError('game mode field or child requires a conversion rule: ' + line.strip())
        index += 1
    if not coords:
        raise ValueError('missing authored game mode position')
    if bool(freeze) != bool(zones) or len(set(zones)) != len(zones):
        raise ValueError('staging duration requires uniquely named restriction zones')
    resource = guid(world + '/resource')
    settings = ''
    if additional_rules:
        settings += f'''  GC_PeakFactionPopulationComponent "{{{guid(world + '/population')}}}" {{
  }}
'''
    if zones:
        key = world + '/release staging zones'
        names = '\n'.join(f'        "{name}"' for name in zones)
        settings += f'''  SCR_PlayersRestrictionZoneManagerComponent "{{{guid(key + '/manager')}}}" {{
  }}
  GC_LobbyFreezeComponent "{{E81E75BC287148D0}}" {{
   m_iAuthoredCountdownSeconds {freeze}
  }}
'''
        additional_rules += f'''    GC_Event "{{{guid(key)}}}" {{
     m_sName "Release staging zones"
     m_aConditions {{
      GC_TimerCondition "{{{guid(key + '/timer')}}}" {{
       m_fSeconds {freeze}
      }}
     }}
     m_aActions {{
      GC_DeleteEntitiesAction "{{{guid(key + '/delete')}}}" {{
       m_aEntities {{
{names}
       }}
      }}
     }}
    }}
'''
    if additional_rules:
        settings += f'''
  GC_LobbyManager "{{6A137A39106A4973}}" {{
   m_aMissionEvents + {{
{additional_rules}   }}
  }}
'''
    return f'''GC_LobbyGameMode GC_MigratedMission : "{{{resource}}}{preset}" {{
 components {{
{weather}{settings} }}
{coords}
{children}}}
'''


def world_draft(source, dev, world, row, staged, area_placements):
    draft, blockers = {}, []
    if row.get('playtestCandidate') or row.get('raceConversion'):
        return {}, {'status': 'explicit recipe'}
    if not row['headers']:
        return {}, {'status': 'no launcher'}
    if world in dev.files:
        return {}, {'status': 'existing Dev world', 'review': row['headers']}
    preset = row.get('briefingModePreset')
    if not preset or row['briefingGaps']:
        blockers.append({'rule': 'briefing preset', 'file': world, 'detail': row['briefingGaps']})
    if row.get('respawn'):
        blockers.append({'rule': 'respawn', 'file': world})
    outcome = row.get('outcomeCandidate', {})
    rules = outcome.get('events', {})
    framework_supported = (len(rules) == row['serializedTypes'].get('TILW_MissionEvent', 0)
                           and not outcome.get('gap') and all('gap' not in event for event in rules.values()))
    rule_text = staged.get(outcome.get('stagedFragment'), '')
    used_flags = {event.get('flag') for event in rules.values() if 'gap' not in event}
    mode = None
    mode_index = None
    zones = []
    parts = {}
    boundaries = {}
    reused_layers = set()
    for layer in source.layers[world.lower()]:
        if layer in staged:
            parts[layer] = [staged[layer]]
            continue
        result = []
        parts[layer] = result
        try:
            for line, block in blocks(source.read(layer)):
                head = block[0]
                body = '\n'.join(block) + '\n'
                kind = head.removeprefix('$grp ').split()[0]
                if kind == 'PS_GameModeCoop':
                    if mode is not None:
                        raise ValueError('multiple legacy game mode placements')
                    mode, mode_index = block, (layer, len(result))
                    result.append('')
                elif kind == 'PS_MissionDescription' and preset and not row['briefingGaps']:
                    continue
                elif kind == 'TILW_MissionFrameworkEntity' and framework_supported and rule_text:
                    roots = [field.strip().split()[0] for field in block[1:-1]
                             if field.startswith(' ') and not field.startswith('  ') and field.strip() != '}']
                    if set(roots) - {'coords', 'm_missionEvents', 'm_casualtyFlags', 'm_suppressGameEnd'}:
                        blockers.append({'rule': 'framework settings', 'file': layer, 'line': line, 'detail': roots})
                elif (layer, line) in area_placements and area_placements[(layer, line)][0] <= used_flags:
                    result.append(area_placements[(layer, line)][1])
                elif kind == 'PS_ManualMarker':
                    result.append(convert_layer(body)[0])
                elif 'TILW_AOLimit.et' in head or 'TILW_MapShape.et' in head:
                    if 'TILW_CoverMapComponent' in body:
                        blockers.append({'rule': 'cover-map overrides', 'file': layer, 'line': line})
                        continue
                    converted, audit = convert_boundaries(body, allow_public_faction_color=True)
                    if not converted or any('gap' in item for item in audit.values()):
                        blockers.append({'rule': 'boundary or map shape', 'file': layer, 'line': line, 'detail': audit})
                    else:
                        boundaries.setdefault(layer, []).append(converted)
                elif PERCEPTION.fullmatch(head) and len(block) == 3 and block[1].startswith(' coords '):
                    continue
                elif kind == 'SCR_EditorRestrictionZoneEntity' and not LEGACY.search(body):
                    names, converted = restriction_zones(block, len(zones) + 1)
                    zones.extend(names)
                    result.append(converted)
                elif NATIVE.match(head) and not LEGACY.search(body):
                    result.append(body)
                else:
                    rule = 'framework events' if kind == 'TILW_MissionFrameworkEntity' else kind
                    blockers.append({'rule': rule, 'file': layer, 'line': line})
        except ValueError as exc:
            blockers.append({'rule': 'layer conversion', 'file': layer, 'detail': str(exc)})

    if mode is None:
        blockers.append({'rule': 'mode placement', 'file': world})
    elif preset:
        try:
            layer, index = mode_index
            parts[layer][index] = render_mode(world, mode, preset, rule_text, zones)
        except ValueError as exc:
            blockers.append({'rule': 'mode settings', 'file': mode_index[0], 'detail': str(exc)})
    for path, blocks_in_layer in parts.items():
        if ''.join(blocks_in_layer).strip():
            draft[path] = ''.join(blocks_in_layer)
    for layer, converted in boundaries.items():
        sibling = layer.removesuffix('.layer') + '_GC_Boundaries.layer'
        if sibling not in dev.files:
            draft[sibling] = ''.join(converted)
            continue
        try:
            remaining = ['\n'.join(block) for _, block in blocks(''.join(converted))]
            for _, existing in blocks(dev.read(sibling)):
                remaining.remove('\n'.join(existing))
            reused_layers.add(sibling)
            if remaining:
                supplemental = layer.removesuffix('.layer') + '_GC_AdditionalBoundaries.layer'
                draft[supplemental] = '\n'.join(remaining) + '\n'
        except ValueError:
            blockers.append({'rule': 'existing Dev boundary differs', 'file': sibling})
    for layer in dev.layers[world.lower()]:
        if layer not in draft and layer not in reused_layers:
            blockers.append({'rule': 'additional Dev layer', 'file': layer})
    if preset:
        for path in (preset, preset + '.meta'):
            draft[path] = staged[path]
    body = source.read(world)
    if not re.fullmatch(r'\s*SubScene \{\s*Parent "\{[A-Fa-f0-9]{16}\}[^"]+"\s*}\s*', body):
        blockers.append({'rule': 'terrain and subscene', 'file': world})
    draft[world] = body
    for header in row['headers']:
        try:
            _, _, files = launcher(source, dev, header)
            draft.update(files)
        except ValueError as exc:
            blockers.append({'rule': 'launcher', 'file': header, 'detail': str(exc)})
    referenced_files = list(draft)
    for owner in referenced_files:
        if PurePosixPath(owner).suffix.lower() not in SOURCE_TEXT | {'.imageset'}:
            continue
        text = draft[owner]
        for resource, path in REF.findall(text):
            paths = source.resources.get(resource.upper(), [])
            if not paths or path in draft or path in dev.files:
                continue
            meta = path + '.meta'
            key = META_NAME.search(source.read(meta)) if meta in source.files else None
            if paths == [path] and key and key[3] == path and PurePosixPath(path).suffix.lower() in ('.edds', '.imageset'):
                image = source.read_raw(path) if path.endswith('.edds') else source.read_raw(path).decode('utf-8-sig')
                draft[path] = image
                draft[meta] = source.read(meta)
                referenced_files.append(path)
            else:
                blockers.append({'rule': 'mission-owned resource', 'file': owner, 'detail': path})
    for path, text in draft.items():
        if path not in dev.files:
            continue
        data = dev.read_raw(path) if isinstance(text, bytes) else dev.read_raw(path).decode('utf-8-sig').replace('\r\n', '\n')
        if data.strip() != text.strip():
            blockers.append({'rule': 'existing Dev file differs', 'file': path})
    return ({} if blockers else draft), {
        'status': 'blocked by conversion rules' if blockers else 'generated for file review',
        'blockers': blockers, 'files': sorted(draft),
    }


def plan(source, dev, report, staged, area_placements):
    results, emitted, totals, families = {}, {}, Counter(), Counter()
    for world, row in report['worlds'].items():
        draft, result = world_draft(source, dev, world, row, staged, area_placements.get(world, {}))
        results[world] = result
        totals[result['status']] += 1
        families.update({blocker['rule'] for blocker in result.get('blockers', [])})
        for path, text in draft.items():
            if path in emitted and emitted[path] != text:
                raise ValueError('batch drafts disagree on ' + path)
        emitted.update(draft)
    digests = {path: sha256(text if isinstance(text, bytes) else text.encode()).hexdigest() for path, text in emitted.items()}
    grouped = dict(sorted(families.items(), key=lambda item: (-item[1], item[0])))
    report['batchMigration'] = {'totals': dict(totals), 'blockedWorldsByRule': grouped,
                                'worlds': results, 'sha256': digests}
    return emitted
