"""Port extraction spheres spawned by unconditional legacy Match-start events."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
import re

from briefings import block_end, guid, properties
from catalog import REF, SOURCE_TEXT
from events import EVENT, LITERAL
from extraction import condition
from launchers import META_NAME, replace_reference

SPAWN = re.compile(r'    TILW_SpawnPrefabInstruction "\{([\da-fA-F]{16})\}" \{')
EXTRACTION = re.compile(r'TILW_ExtractionTriggerEntity : "\{192ABD6B9232E064\}[^"]+" \{')
KEY = r'"[\w.-]+"'
NUMBER = r'\d+(?:\.\d+)?'


@dataclass
class DelayedExtraction:
    assets: dict = field(default_factory=dict)
    spawns: dict = field(default_factory=dict)
    conditions: dict = field(default_factory=dict)


def render_spawn(details, key):
    return Decimal(details['delay']), [
        f'      GC_SpawnAction "{{{guid(key)}}}" {{',
        f'       m_Spawn GC_PrefabSpawn "{{{guid(key + "/spawn")}}}" {{',
        f'        m_sLocation "{details["anchor"]}"',
        f'        m_sPrefab {details["prefab"]}',
        f'        m_sEntityName "{details["area"]}"',
        '       }', '      }',
    ]


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
                result.spawns[child] = {'prefab': ref[0], 'anchor': anchor[1:-1], 'area': area, 'delay': delay,
                                        'flag': flag[1:-1]}
                groups[flag].append(check)
                prefabs[flag].add(resource_path)
    if not groups:
        return result
    closure = [source.read(path) for path in source.closure(world)
               if '.' + path.rsplit('.', 1)[-1].lower() in SOURCE_TEXT]
    for ref, count in Counter(entry['prefab'] for entry in result.spawns.values()).items():
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
