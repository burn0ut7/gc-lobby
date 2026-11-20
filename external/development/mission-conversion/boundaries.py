"""Translate standalone instances of the common legacy AO prefab."""
import math
import re
from struct import pack, unpack

from briefings import block_end, properties

ROOT = re.compile(r'^(?P<group>\$grp )?PolylineShapeEntity(?: (?P<name>[\w.-]+))? : "\{85222A2744768C81\}[^"]+" \{$')
DEST = '{8F470B543A20D944}Prefabs/Logic/Mission/AOLimit.et'
MAP_SHAPE = '{8F470B543A20D963}Prefabs/Logic/Mission/MapShape.et'
ALLOWED = {'m_killTimer', 'm_factionKeys', 'm_ignoredVehicles', 'm_visibility', 'm_defaultColor',
           'm_useFactionColor', 'm_lineWidth', 'Enabled'}
COMPONENTS = {
    'TILW_AOLimitComponent': '6217617AC21DEB7B',
    'TILW_MapShapeComponent': '6508F54F9B461198',
    'TILW_CoverMapComponent': '6473480FCAB7760B',
}


def floats(value, count):
    result = tuple(map(float, value.split()))
    if len(result) != count or not all(map(math.isfinite, result)):
        raise ValueError('invalid numeric property')
    return result


def single_precision(value):
    return unpack('<f', pack('<f', value))[0]


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def on_edge(a, b, p):
    return (abs(cross(a, b, p)) <= 0.00001 and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def validate_polygon(vertices, origin, yaw=0):
    if not 3 <= len(vertices) <= 128:
        raise ValueError('native polygon requires 3 to 128 points')
    cosine, sine = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    ring = [(single_precision(origin[0] + p[0]*cosine + p[2]*sine),
             single_precision(origin[2] - p[0]*sine + p[2]*cosine)) for p in vertices]
    edges = list(zip(ring, ring[1:] + ring[:1]))
    if abs(sum(cross(ring[0], a, b) for a, b in edges)) <= 0.01:
        raise ValueError('zero polygon area')
    for i, (a, b) in enumerate(edges):
        c = ring[(i+2) % len(ring)]
        if math.dist(a, b) <= 0.0001 or (abs(cross(a, b, c)) <= 0.00001 and
                (b[0]-a[0])*(c[0]-b[0]) + (b[1]-a[1])*(c[1]-b[1]) < 0):
            raise ValueError('degenerate or backtracking boundary edge')
        for j in range(i+2, len(ring)):
            if i == 0 and j == len(ring)-1:
                continue
            c, d = edges[j]
            if ((cross(a,b,c)*cross(a,b,d) < 0 and cross(c,d,a)*cross(c,d,b) < 0)
                    or on_edge(a,b,c) or on_edge(a,b,d) or on_edge(c,d,a) or on_edge(c,d,b)):
                raise ValueError('crossing or touching polygon edges')


def settings(lines):
    result, overlay = {}, {}
    seen = set()
    i = 0
    while i < len(lines):
        head = re.fullmatch(r'(\w+) "\{([A-Fa-f0-9]{16})\}" \{', lines[i].strip())
        if not head or COMPONENTS.get(head[1]) != head[2].upper() or head[1] in seen:
            raise ValueError('custom AO component')
        seen.add(head[1])
        end = block_end(lines, i)
        body = lines[i+1:end]
        if head[1] == 'TILW_AOLimitComponent':
            result = properties(body, ALLOWED)
        elif any(l.strip().startswith('Enabled ') and l.strip() != 'Enabled 0' for l in body):
            if head[1] != 'TILW_MapShapeComponent':
                raise ValueError('active cover map needs separate conversion')
            overlay = properties(body, {'Enabled', 'm_drawArea', 'm_drawLine', 'm_color', 'm_invert',
                                        'm_visibleForEmptyFaction', 'm_factionKeys', 'm_lineWidth', 'm_lineColor'})
            if overlay.get('Enabled') != '1' or overlay.get('m_drawLine', '0') != '0':
                raise ValueError('zoom-dependent auxiliary outline needs separate conversion')
        i = end+1
    return result, overlay


def convert_passive(name, placement, overlay):
    keys = overlay.get('m_factionKeys', '')
    empty = overlay.get('m_visibleForEmptyFaction', '1')
    color = overlay.get('m_color', '0 0 0 0.75')
    invert = overlay.get('m_invert', '1')
    if (not overlay or overlay.get('m_drawArea', '1') != '1' or empty not in ('0', '1')
            or (not keys and empty == '0') or invert not in ('0', '1')
            or not all(0 <= x <= 1 for x in floats(color, 4))):
        raise ValueError('unsupported passive map fill')
    if keys and not re.fullmatch(r'(?:\s*"[\w.-]+"\s*)+', keys):
        raise ValueError('custom passive shape faction list')
    lines = [f'PolylineShapeEntity{name} : "{MAP_SHAPE}" {{', ' components {',
             '  GC_MapShapeComponent "{8F470B543A20D962}" {', '   m_LineColor 0 0 0 0',
             '   m_bFillEnabled 1', '   m_FillColor ' + color, '   m_bInvertFill ' + invert]
    if keys:
        lines.extend(['   m_aFactionKeys {', '    ' + keys.replace('\n', '\n    '), '   }',
                      '   m_bVisibleWithoutFaction ' + empty])
    return '\n'.join([*lines, '  }', ' }', *placement, '}'])


def convert_instance(lines, name, passive_only=False):
    values, overlay = {}, {}
    placement = []
    vertices = []
    coords = None
    yaw = None
    i = 0
    while i < len(lines):
        field = lines[i].strip()
        if field == 'components {':
            end = block_end(lines, i)
            values, overlay = settings(lines[i+1:end])
            i = end
        elif field == 'Points {':
            end = block_end(lines, i)
            j = i+1
            while j < end:
                if not re.fullmatch(r'ShapePoint "\{[\da-fA-F]{16}\}" \{', lines[j].strip()):
                    raise ValueError('custom shape point')
                last = block_end(lines, j)
                data = properties(lines[j+1:last], {'Position'})
                vertices.append(floats(data.get('Position','0 0 0'), 3))
                j = last+1
            shift = len(lines[i])-len(lines[i].lstrip())-1
            placement.extend(l[shift:] for l in lines[i:end+1])
            i = end
        elif field.startswith('coords '):
            coords = field[7:]
            placement.append(' '+field)
        elif field.startswith(('angleY ', 'angles ', 'angleX ', 'angleZ ')):
            axis, value = field.split(' ', 1)
            if axis == 'angles':
                pitch, heading, roll = floats(value, 3)
                if pitch or roll:
                    raise ValueError('tilted AO needs world-space validation')
            else:
                heading, = floats(value, 1)
                if axis != 'angleY' and heading:
                    raise ValueError('tilted AO needs world-space validation')
            if axis in ('angles', 'angleY'):
                if yaw is not None:
                    raise ValueError('mixed rotation representations')
                yaw = heading
            placement.append(' '+field)
        elif field == 'IsClosed 1' or field.startswith('LineColor '):
            placement.append(' '+field)
        else:
            raise ValueError('unsupported boundary property ' + field.split(' ')[0])
        i += 1
    if coords is None:
        raise ValueError('missing explicit AO transform')
    validate_polygon(vertices, floats(coords,3), yaw or 0)
    if passive_only:
        if values.get('Enabled') != '0':
            raise ValueError('passive shape requires a disabled AO')
        return convert_passive(name, placement, overlay), len(vertices)
    keys = values.get('m_factionKeys','')
    vehicles = values.get('m_ignoredVehicles','')
    if keys and not re.fullmatch(r'(?:\s*"[\w.-]+"\s*)+', keys):
        raise ValueError('custom faction list')
    if vehicles and not re.fullmatch(r'(?:\s*"\{[\da-fA-F]{16}\}[^"]+\.et"\s*)+', vehicles):
        raise ValueError('custom vehicle filter')
    visibility = values.get('m_visibility','ALL')
    faction_color = values.get('m_useFactionColor','1')
    grace = values.get('m_killTimer','20')
    width = values.get('m_lineWidth','3')
    color = values.get('m_defaultColor','0 0 0 1')
    if (visibility not in ('ALL','FACTION','NONE') or faction_color not in ('0','1')
            or not 0.1 <= float(grace) <= 3600 or not 1 <= float(width) <= 20
            or values.get('Enabled','1') != '1' or not all(0 <= x <= 1 for x in floats(color,4))):
        raise ValueError('unsupported boundary settings')
    if keys and faction_color == '1' and visibility == 'ALL':
        raise ValueError('public outline colored by a restricted faction')
    visible = visibility == 'ALL' or (visibility == 'FACTION' and bool(keys))
    map_keys = keys if visibility == 'FACTION' else ''
    use_color = faction_color if visible and map_keys else '0'
    fill = overlay.get('m_drawArea', '1') if overlay else '0'
    if fill not in ('0', '1'):
        raise ValueError('unsupported map fill')
    fill_color = overlay.get('m_color', '0 0 0 0.75')
    invert = overlay.get('m_invert', '1')
    if fill == '1':
        fill_keys = overlay.get('m_factionKeys', '')
        empty = overlay.get('m_visibleForEmptyFaction', '1')
        if (empty != ('0' if fill_keys else '1') or
                (fill_keys and not re.fullmatch(r'(?:\s*"[\w.-]+"\s*)+', fill_keys))):
            raise ValueError('separate unassigned map fill audience')
        if visible and set(re.findall(r'"([^"]+)"', fill_keys)) != set(re.findall(r'"([^"]+)"', map_keys)):
            raise ValueError('map fill and outline target different factions')
        if not visible:
            color, map_keys = '0 0 0 0', fill_keys
        if invert not in ('0', '1') or not all(0 <= x <= 1 for x in floats(fill_color, 4)):
            raise ValueError('invalid map fill style')
    lines = [f'GC_AOLimitEntity{name} : "{DEST}" {{',' components {',
             '  GC_BoundaryWallComponent "{6A4D831C2E5079C1}" {','   m_bEnabled 0','  }',
             '  GC_MapShapeComponent "{8F470B543A20D964}" {', f"   m_bEnabled {int(visible or fill == '1')}",
             f'   m_bFillEnabled {fill}', f'   m_LineColor {color}', f'   m_fLineWidth {width}']
    if fill == '1':
        lines.extend(['   m_FillColor '+fill_color, '   m_bInvertFill '+invert])
    if map_keys:
        lines.extend(['   m_aFactionKeys {','    '+map_keys,'   }', '   m_bFactionColor '+use_color])
    lines.extend(['  }',' }',' m_eBehavior SOFT_KILL',' m_fGraceSeconds '+grace])
    if keys:
        lines.extend([' m_aFactionKeys {','  '+keys,' }'])
    if vehicles:
        lines.extend([' m_eVehicleMode EXCEPT_MATCHING',' m_aVehiclePrefabs {','  '+vehicles,' }'])
    return '\n'.join([*lines,*placement,'}']), len(vertices)


def convert_boundaries(text):
    lines = text.splitlines()
    emitted, review = [], {}
    for i,line in enumerate(lines):
        root = ROOT.fullmatch(line)
        if not root:
            continue
        end = block_end(lines,i)
        instances = []
        if root['group']:
            j = i+1
            while j < end:
                head = re.fullmatch(r' (?:([\w.-]+) )?\{',lines[j])
                if not head:
                    review[str(j+1)] = {'gap':'unsupported grouped boundary'}
                    break
                last = block_end(lines,j)
                instances.append((j,last,head[1]))
                j = last+1
        else:
            instances.append((i,end,root['name']))
        for start, stop, name in instances:
            try:
                body,count = convert_instance(lines[start+1:stop], ' '+name if name else '')
                emitted.append(body)
                review[str(start+1)] = {'vertices':count,'name':name,'lastLine':stop+1}
            except ValueError as error:
                review[str(start+1)] = {'gap':str(error)}
    return '\n'.join(emitted)+'\n' if emitted else '', review
