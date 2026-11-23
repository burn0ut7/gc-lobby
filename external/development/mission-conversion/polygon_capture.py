"""Read legacy capture and spawning polygons and preserve their placements."""
from hashlib import sha256
import re

from boundaries import floats, passive_shape_component, validate_polygon
from briefings import block_end, guid, properties
from catalog import REF

SHAPE = re.compile(r'(?P<group>\$grp )?PolylineShapeEntity(?: (?P<name>[\w.-]+))? \{')
MEMBER = re.compile(r' (?:([\w.-]+) )?\{')
QUOTED_MEMBER = re.compile(r' ("[\w.-]+") \{')
POINT = re.compile(r'ShapePoint "\{[\da-fA-F]{16}\}" \{')
COMPONENT = re.compile(r'  (TILW_MapShapeComponent|RplComponent) "\{([\da-fA-F]{16})\}" \{')
STARTUP_AREA = '$grp PolylineShapeEntity : "{CAAEFE4F33007CA6}Prefabs/Utils/TILW_AreaPrefabSpawner.et" {'
RECON_STARTUP_AREA = '9f12aa276cc2141b026c53f8477773cfb72c4430b464ad94034be5e7943f5111'


def members(block, grouped, name, *, allow_quoted=False):
    if not grouped:
        return [(name, block[1:-1])]
    result = []
    start = 1
    while start < len(block) - 1:
        member = MEMBER.fullmatch(block[start])
        if not member and allow_quoted:
            member = QUOTED_MEMBER.fullmatch(block[start])
        if not member:
            raise ValueError('unsupported grouped area placement')
        end = block_end(block, start)
        # Remove the group member's extra indent for the common parser.
        result.append((member[1], [line[1:] for line in block[start + 1:end]]))
        start = end + 1
    return result


def read(lines, trigger_pattern, *, startup_prefab=None):
    origin, vertices, child = None, None, None
    seen = set()
    yaw = 0
    placement = []
    index = 0
    while index < len(lines):
        line = lines[index]
        field, _, value = line.strip().partition(' ')
        if field in seen:
            raise ValueError('duplicate polygon capture field ' + field)
        seen.add(field)
        end = index
        if field == 'coords':
            origin = floats(value, 3)
        elif field in ('angles', 'angleY'):
            if {'angleY', 'angles'} <= seen:
                raise ValueError('duplicate polygon orientation')
            if field == 'angleY':
                yaw, = floats(value, 1)
            else:
                pitch, yaw, roll = floats(value, 3)
                if pitch or roll:
                    raise ValueError('tilted capture polygon needs world-space validation')
        elif field == 'Points' and value == '{':
            vertices = []
            end = block_end(lines, index)
            point = index + 1
            while point < end:
                if not POINT.fullmatch(lines[point].strip()):
                    raise ValueError('unsupported polygon point')
                last = block_end(lines, point)
                position = properties(lines[point + 1:last], {'Position'}).get('Position', '0 0 0')
                vertices.append(floats(position, 3))
                point = last + 1
        elif field == 'LineColor':
            floats(value, 4)
        elif field == 'IsClosed' and value == '1':
            pass
        elif field == 'components' and value == '{':
            end = block_end(lines, index)
            placement.extend(polygon_components(lines[index + 1:end], startup_prefab=startup_prefab))
            index = end + 1
            continue
        elif line == ' {':
            end = block_end(lines, index)
            head = trigger_pattern and trigger_pattern.fullmatch(lines[index + 1].strip())
            if (not head or head['group'] or block_end(lines, index + 1) != end - 1):
                raise ValueError('polygon requires one capture trigger child')
            child = (head, [item[1:] for item in lines[index + 2:end - 1]])
            index = end + 1
            continue
        else:
            raise ValueError('unsupported polygon capture field ' + field)
        placement.extend(lines[index:end + 1])
        index = end + 1
    if origin is None or not vertices or (trigger_pattern and not child):
        raise ValueError('capture requires a polygon, explicit origin and one trigger child')
    if 'IsClosed' not in seen:
        # Legacy capture uses the full ring whether its editor shape is closed or not.
        # Closing the native collision area is harmless to a fill-only map overlay.
        if any(line.strip().startswith('m_LineColor ') and floats(line.strip().partition(' ')[2], 4)[3] != 0
               for line in placement):
            raise ValueError('implicit capture closure needs review for a visible outline')
        placement.append(' IsClosed 1')
    validate_polygon(vertices, origin, yaw)
    return placement, child, len(vertices)


def startup_areas(block, source_location, resolve_spawn):
    # Only these measured group prefabs have a confirmed point-sized initial footprint.
    if sha256('\n'.join(block).encode()).hexdigest() != RECON_STARTUP_AREA:
        raise ValueError('startup spawn needs reviewed prefab bounds and placement')
    output = []
    for area_index, (name, fields) in enumerate(members(block, True, None)):
        component_key = '68FF32617B5D1B1F'
        if name or fields[:2] != [' components {', f'  TILW_AreaSpawningComponent "{{{component_key}}}" {{']:
            raise ValueError('unexpected startup spawn component')
        end = block_end(fields, 0)
        settings = properties(fields[2:end - 1], {'m_prefabs', 'm_bRandomYaw', 'm_bContactCheck'})
        if settings.get('m_bRandomYaw') != '0' or settings.get('m_bContactCheck') != '1':
            raise ValueError('startup spawn contact or rotation needs review')
        references = settings.get('m_prefabs', '')
        if REF.sub('', references).strip():
            raise ValueError('invalid startup spawn prefab roster')
        placement, _, _ = read(fields[end + 1:], None)
        entries = []
        for index, (resource, path) in enumerate(REF.findall(references)):
            if resolve_spawn(resource, path, group=True) != path:
                raise ValueError('unverified startup spawn group ' + resource)
            entry = guid(f'{source_location}/{area_index}/{index}')
            entries.extend([f'    GC_AreaSpawnEntry "{{{entry}}}" {{',
                            f'     m_sPrefab "{{{resource}}}{path}"', '    }'])
        if not entries:
            raise ValueError('empty startup spawn roster')
        output.extend(['PolylineShapeEntity {', ' components {',
                       f'  GC_AreaPrefabSpawnerComponent "{{{component_key}}}" {{',
                       '   m_aPrefabs {', *entries, '   }',
                       '   m_bAvoidWater 0', '   m_bRandomYaw 0', '   m_bCheckRootBoundsAtPoint 1',
                       '   m_iAttemptsPerObject 256', '  }', ' }',
                       *placement, ' LineColor 1 0 0 1', '}'])
    return '\n'.join(output) + '\n'


def polygon_components(lines, *, startup_prefab=None):
    result = [' components {']
    if startup_prefab:
        component, entry, prefab = startup_prefab
        legacy = [f'  TILW_AreaSpawningComponent "{{{component}}}" {{',
                  '   m_prefabs {', f'    {prefab}', '   }', '  }']
        if lines[:len(legacy)] != legacy:
            raise ValueError('reviewed polygon startup spawn fields changed')
        result.extend([f'  GC_AreaPrefabSpawnerComponent "{{{component}}}" {{',
                       '   m_aPrefabs {', f'    GC_AreaSpawnEntry "{{{entry}}}" {{',
                       f'     m_sPrefab {prefab}', '    }', '   }',
                       '   m_bAvoidWater 0', '  }'])
        lines = lines[len(legacy):]
    seen = set()
    index = 0
    while index < len(lines):
        match = COMPONENT.fullmatch(lines[index])
        if not match:
            raise ValueError('unsupported polygon capture component ' + lines[index].strip().split()[0])
        kind, key = match.groups()
        if kind in seen:
            raise ValueError('duplicate polygon capture component ' + kind)
        seen.add(kind)
        end = block_end(lines, index)
        block = lines[index:end + 1]
        if kind == 'TILW_MapShapeComponent':
            result.extend(passive_shape_component(block, key)[1:-1])
        else:
            if block[1:-1] not in ([], ['   Streamable Disabled']):
                raise ValueError('unsupported polygon replication fields')
            result.extend(block)
        index = end + 1
    return [*result, ' }']
