"""Port extraction spheres spawned by legacy events."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
import re

from briefings import block_end, guid, properties
from catalog import REF, SOURCE_TEXT
from event_actions import SPAWN
from events import EVENT, LITERAL
from extraction import condition
from launchers import META_NAME, replace_reference
from markers import convert_layer

EXTRACTION = re.compile(r'TILW_ExtractionTriggerEntity : "\{192ABD6B9232E064\}[^"]+" \{')
KEY = r'"[\w.-]+"'
NUMBER = r'\d+(?:\.\d+)?'
LOST_AND_FOUND = 'worlds/FloridaMan/worlds/LostAndFound/LostAndFound.ent'
EXTRACT_POINT = 'worlds/FloridaMan/Prefabs/Setpeice/BLUFOR_Extract_Point.et'
EXTRACT_POINT_ID = 'B9CFA890D70B27DE'


@dataclass
class DelayedExtraction:
    assets: dict = field(default_factory=dict)
    spawns: dict = field(default_factory=dict)
    conditions: dict = field(default_factory=dict)
    startup_consumers: dict = field(default_factory=dict)


def sampled_extraction(source, world):
    layer = world.removesuffix('.ent') + '_Layers/default.layer'
    anchors = world.removesuffix('.ent') + '_Layers/Setpeices.layer'
    expected = {layer: 'd1b502d87d4664112e0e1260fcc5a81a8fdd2b84',
                anchors: '6fd8ec7b5bbedc002e8aa4df76a3315a64a894e2',
                EXTRACT_POINT: 'eb9247cf65f7f174bd497fc6905b46c0b18b23ef'}
    if (any(source.files.get(path) != blob for path, blob in expected.items())
            or source.resources.get(EXTRACT_POINT_ID) != [EXTRACT_POINT]):
        raise ValueError('reviewed sampled extraction files changed')
    result = DelayedExtraction(startup_consumers={'USAF_Extracted': 2, 'US_Extracted': 1})
    lines = source.read(EXTRACT_POINT).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('  PS_ManualMarker '))
    root, identity, *fields = [line[2:] for line in lines[start:block_end(lines, start) + 1]]
    marker, count = convert_layer('\n'.join([root, *fields]) + '\n', draw_order=True, source=source)
    if count != 1:
        raise ValueError('sampled extraction needs its authored BLUFOR marker')
    marker_fields = marker.splitlines()
    marker_fields.insert(1, identity)
    meta = source.read(EXTRACT_POINT + '.meta')
    old_identity = META_NAME.search(meta)
    if not old_identity or old_identity[2].upper() != EXTRACT_POINT_ID or old_identity[3] != EXTRACT_POINT:
        raise ValueError('sampled extraction prefab registration changed')
    targets = []
    for flag, faction, radius, ratio in (('USAF_Extracted', 'RHS_USAF', 80, '0.9'), ('US_Extracted', 'US', 30, '1')):
        primary = faction == 'RHS_USAF'
        asset = EXTRACT_POINT if primary else EXTRACT_POINT.removesuffix('.et') + '_Pilots.et'
        resource = EXTRACT_POINT_ID if primary else guid(asset)
        if not primary and (asset in source.files or resource in source.resources):
            raise ValueError('sampled extraction resource identity conflicts')
        area = 'GC_MigrationExtraction_' + guid(world + '/' + flag)
        shape = ['GC_RadiusAreaEntity : "{2C4447C53A1E450B}Prefabs/Logic/Mission/RadiusArea.et" {',
                 f' m_fRadius {radius}']
        if primary:
            shape.extend([' {', *('  ' + line for line in marker_fields), ' }'])
        result.assets[asset] = '\n'.join([*shape, '}', ''])
        result.assets[asset + '.meta'] = replace_reference(meta, META_NAME, resource, asset)
        check = condition(world, flag, area, faction, ratio, whole_number_quotient=True)
        result.conditions[f'"{flag}"'] = ['      ' + line for line in check.splitlines()]
        targets.append({'prefab': f'"{{{resource}}}{asset}"', 'area': area, 'delay': '0', 'flag': flag})
    lines = source.read(layer).splitlines()
    for index, line in enumerate(lines):
        if not (event := EVENT.fullmatch(line)):
            continue
        fields = lines[index + 1:block_end(lines, index)]
        for slot, instruction in enumerate(fields):
            if not (spawn := SPAWN.fullmatch(instruction)):
                continue
            data = properties(fields[slot + 1:block_end(fields, slot)],
                              {'m_prefab', 'm_locationName', 'm_executionDelay'})
            if data.get('m_prefab') != f'"{{{EXTRACT_POINT_ID}}}{EXTRACT_POINT}"':
                continue
            anchor = re.fullmatch(r'"extract([1-5])"', data.get('m_locationName', ''))
            if not anchor or data.get('m_executionDelay') != '0' or '   m_pregameEvent 1' not in fields:
                raise ValueError('unverified sampled extraction spawn')
            sample = 'ExtractPoint' + anchor[1]
            result.spawns[event[1] + '/' + spawn[1]] = [
                {**target, 'anchor': anchor[0][1:-1], 'sample': sample} for target in targets]
    if sorted(pair[0]['sample'] for pair in result.spawns.values()) != [f'ExtractPoint{i}' for i in range(1, 6)]:
        raise ValueError('sampled extraction needs each original spawn')
    return result


def read_prefab(source, path, asset, key):
    text = source.read(path)
    lines = text.splitlines()
    if not lines or not EXTRACTION.fullmatch(lines[0]) or block_end(lines, 0) != len(lines) - 1:
        raise ValueError('nonstandard extraction prefab')
    data = properties(lines[1:-1], {'ID', 'm_queryRadius', 'm_captureTime', 'm_sendStatusMessages',
                                    'm_locationName', 'm_flagName', 'm_skipFirstQuery', 'm_factionKey',
                                    'm_ratioThreshold', 'm_playersOnly', 'm_stopAfterFirstChange'})
    flag, faction = data.get('m_flagName', ''), data.get('m_factionKey', '')
    hold, radius, ratio = (data.get(key, '') for key in ('m_captureTime', 'm_queryRadius', 'm_ratioThreshold'))
    messages = data.get('m_sendStatusMessages', '0')
    location = data.get('m_locationName', '"the location"')
    latch = data.get('m_stopAfterFirstChange', '0')
    if (not re.fullmatch(KEY, flag) or not re.fullmatch(KEY, faction) or data.get('m_playersOnly', '1') != '1'
            or data.get('m_skipFirstQuery') != '1' or not all(re.fullmatch(NUMBER, v) for v in (hold, radius, ratio))
            or not Decimal('0.1') <= Decimal(hold) <= 86400 or not Decimal('0.1') <= Decimal(radius) <= 100000
            or not 0 <= Decimal(ratio) <= 1 or messages not in ('0', '1') or latch not in ('0', '1')
            or not re.fullmatch(r'"[\w .-]{1,80}"', location)):
        raise ValueError('unreviewed dynamic extraction configuration')
    meta = source.read(path + '.meta')
    identity = META_NAME.search(meta)
    if not identity or identity[2].upper() != asset:
        raise ValueError('dynamic extraction resource metadata differs')
    meta = replace_reference(meta, META_NAME, asset, path)
    area = 'GC_MigrationExtraction_' + guid(key)
    shape = f'''GC_RadiusAreaEntity : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
 m_fRadius {radius}
}}
'''
    check = condition(key, flag[1:-1], area, faction, ratio, hold, skip_discovery=True, latch=latch == '1',
                      location=location if messages == '1' else None)
    return flag, area, shape, meta, check.splitlines()


def collect(source, world):
    if world == LOST_AND_FOUND:
        return sampled_extraction(source, world)
    result = DelayedExtraction()
    groups = defaultdict(list)
    prefabs = defaultdict(set)
    for path in source.layers[world.lower()]:
        lines = source.read(path).splitlines()
        for index, line in enumerate(lines):
            event = EVENT.fullmatch(line)
            if not event:
                continue
            end = block_end(lines, index)
            body = lines[index + 1:end]
            start = next((i for i, field in enumerate(body) if LITERAL.fullmatch(field)), None)
            if (start is None or body[start + 1:block_end(body, start)] != ['    m_invertTerm 1']
                    or '   m_pregameEvent 1' in body):
                continue
            for pos, instruction in enumerate(body):
                spawn = SPAWN.fullmatch(instruction)
                if not spawn:
                    continue
                data = properties(body[pos + 1:block_end(body, pos)], {'m_executionDelay', 'm_prefab',
                                                                       'm_locationName', 'm_setEntityName'})
                ref = REF.fullmatch(data.get('m_prefab', ''))
                if not ref or source.resources.get(ref[1].upper()) != [ref[2]]:
                    continue
                asset, resource_path = ref[1].upper(), ref[2]
                if not resource_path.lower().endswith('.et') or not source.read(resource_path).startswith('TILW_ExtractionTriggerEntity'):
                    continue
                anchor, delay = data.get('m_locationName', ''), data.get('m_executionDelay', '5')
                if (not re.fullmatch(KEY, anchor) or not re.fullmatch(NUMBER, delay) or Decimal(delay) > 86400
                        or data.get('m_setEntityName', '""') != '""'):
                    raise ValueError('unreviewed dynamic extraction placement')
                child = event[1] + '/' + spawn[1]
                key = world + '/' + child
                flag, area, shape, meta, check = read_prefab(source, resource_path, asset, key)
                if child in result.spawns:
                    raise ValueError('duplicate dynamic extraction instruction')
                result.assets[resource_path] = shape
                result.assets[resource_path + '.meta'] = meta
                result.spawns[child] = [{'prefab': ref[0], 'anchor': anchor[1:-1], 'area': area, 'delay': delay,
                                         'flag': flag[1:-1]}]
                groups[flag].append(check)
                prefabs[flag].add(resource_path)
    if not groups:
        return result
    closure = [source.read(path) for path in source.closure(world)
               if '.' + path.rsplit('.', 1)[-1].lower() in SOURCE_TEXT]
    for ref, count in Counter(entry['prefab'] for group in result.spawns.values() for entry in group).items():
        if sum(text.count(ref) for text in closure) != count:
            raise ValueError('dynamic extraction prefab is used outside supported spawn instructions')
    for flag, checks in groups.items():
        # Only a single positive one-shot consumer can treat the shared legacy output as an OR.
        event_label = re.compile(r'^   m_name ' + re.escape(flag) + r'$', re.M)
        if sum(event_label.sub('', text).count(flag) for text in closure) != len(prefabs[flag]) + 1:
            raise ValueError('dynamic extraction flag has additional producers or consumers: ' + flag)
        body = [f'      GC_ConditionGroup "{{{guid(world + "/delayed/" + flag)}}}" {{',
                '       m_iMatch 1', '       m_aConditions {']
        for check in checks:
            body.extend('        ' + line for line in check)
        result.conditions[flag] = [*body, '       }', '      }']
    return result
