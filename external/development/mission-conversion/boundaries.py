"""Translate authored instances of the common legacy AO and map shape prefabs."""
import math
import re
from struct import pack, unpack

from briefings import block_end, guid, properties
from markers import LEGACY as MARKER, instance as render_marker, marker_fields

ROOT = re.compile(r'^(?P<group>\$grp )?PolylineShapeEntity(?: (?P<name>[\w.-]+))? : "\{85222A2744768C81\}[^"]+" \{$')
STANDALONE_SHAPE = re.compile(
    r'^(?P<group>\$grp )?PolylineShapeEntity(?: (?P<name>[\w.-]+))? : '
    r'"\{A0468F86F76FCEE7\}Prefabs/Logic/Map/TILW_MapShape.et" \{$')
BARE_SHAPE = re.compile(r'^(?P<group>\$grp )?PolylineShapeEntity(?: (?P<name>[\w.-]+))? \{$')
PASSIVE_COVER = re.compile(
    r'^(?P<group>\$grp )?PolylineShapeEntity(?: (?P<name>[\w.-]+))? : '
    r'"\{25C6E66E6C14258D\}Prefabs/Logic/Map/Cover_Map.et" \{$')
DEST = '{8F470B543A20D944}Prefabs/Logic/Mission/AOLimit.et'
MAP_SHAPE = '{8F470B543A20D963}Prefabs/Logic/Mission/MapShape.et'
REVIEWED_BOUNDARY_CROSSINGS = {
    'd140047c759fdf8f7bda1e1a11689c2659d14142': (49, 'Two Bloody Miles/outer AO/', 0, 5, 0.005),
}
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


def rotate_point(point, yaw, pitch, roll):
    x, y, z = point
    # Match the Enfusion AnglesToMatrix basis vectors (yaw, pitch, roll).
    yaw, pitch, roll = map(math.radians, (yaw, -pitch, -roll))
    x, y = x * math.cos(roll) - y * math.sin(roll), x * math.sin(roll) + y * math.cos(roll)
    y, z = y * math.cos(pitch) - z * math.sin(pitch), y * math.sin(pitch) + z * math.cos(pitch)
    return x * math.cos(yaw) + z * math.sin(yaw), y, -x * math.sin(yaw) + z * math.cos(yaw)


def validate_polygon(vertices, origin, yaw=0, pitch=0, roll=0):
    if not 3 <= len(vertices) <= 128:
        raise ValueError('native polygon requires 3 to 128 points')
    if pitch or roll:
        ring = [(single_precision(origin[0] + x), single_precision(origin[2] + z))
                for x, _, z in (rotate_point(point, yaw, pitch, roll) for point in vertices)]
    else:
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


def settings(lines, extended_shapes, standalone=False, passive_cover=False, *, bare=False):
    result, overlay, cover = {}, {}, {}
    navmesh = []
    ids = {} if standalone else COMPONENTS
    if passive_cover:
        ids = {'TILW_MapShapeComponent': '6508F5522A2E1971'}
    seen = set()
    i = 0
    while i < len(lines):
        head = re.fullmatch(r'(\w+) "\{([A-Fa-f0-9]{16})\}" \{', lines[i].strip())
        if not head or head[1] in seen:
            raise ValueError('custom AO component')
        seen.add(head[1])
        end = block_end(lines, i)
        body = lines[i+1:end]
        if passive_cover and head[1] == 'TILW_CoverMapComponent':
            if head[2].upper() != '64734633ABA43B46' or properties(body, {'Enabled'}) != {'Enabled': '0'}:
                raise ValueError('cover-map framing must be explicitly disabled')
        elif extended_shapes and standalone and head[1] == 'TILW_NavmeshRegenComponent' and not body:
            navmesh.extend([f'  GC_NavmeshRebuildComponent "{{{head[2]}}}" {{', '  }'])
        elif extended_shapes and standalone and head[1] == 'Hierarchy' and not body:
            navmesh.extend([f'  Hierarchy "{{{head[2]}}}" {{', '  }'])
        elif bare and head[1] == 'RplComponent' and not body:
            # The destination map and AO prefabs provide their own replication component.
            pass
        elif extended_shapes and standalone and head[1] == 'TILW_AOLimitComponent':
            result = {'Enabled': '1', **properties(body, ALLOWED)}
            if result['Enabled'] not in ('0', '1'):
                raise ValueError('unsupported added AO state')
        elif ids.get(head[1]) != head[2].upper() and not (
                standalone and head[1] == 'TILW_MapShapeComponent'
                and (bare or head[2].upper() == '6508F5526836BC09')):
            raise ValueError('custom AO component')
        elif head[1] == 'TILW_AOLimitComponent':
            result = properties(body, ALLOWED)
        # The standard legacy AO prefab disables both visual components.
        # An instance can store style values without enabling either component.
        elif standalone or any(l.strip().startswith('Enabled ') and l.strip() != 'Enabled 0' for l in body):
            if head[1] == 'TILW_CoverMapComponent':
                if not extended_shapes:
                    raise ValueError('active cover map needs separate conversion')
                cover = properties(body, {'Enabled', 'm_ratioWidth'})
                if cover.get('Enabled') != '1':
                    raise ValueError('unsupported active cover map')
            else:
                overlay = properties(body, {'Enabled', 'm_drawArea', 'm_drawLine', 'm_color', 'm_invert',
                                            'm_visibleForEmptyFaction', 'm_factionKeys', 'm_lineWidth', 'm_lineColor'})
                if overlay.get('Enabled', '1' if standalone else '0') not in (('0', '1') if standalone else ('1',)):
                    raise ValueError('unsupported auxiliary map shape')
        i = end+1
    if passive_cover:
        if 'TILW_CoverMapComponent' not in seen or overlay.get('Enabled') != '1':
            raise ValueError('passive cover requires a disabled frame and an enabled map shape')
        overlay.setdefault('m_invert', '1')
    if not standalone and not extended_shapes and not cover and overlay.get('m_drawLine', '0') != '0':
        raise ValueError('zoom-dependent auxiliary outline needs separate conversion')
    return result, overlay, cover, navmesh


def convert_passive(name, placement, overlay, *, auto_frame=False, standalone=False, world_width=False,
                    navmesh=(), require_assigned=False, open_fill=False):
    keys = overlay.get('m_factionKeys', '')
    if '""' in keys and re.fullmatch(r'(?:\s*"[\w.-]*"\s*)+', keys):
        assigned = re.findall(r'"[\w.-]+"', keys)
        if assigned:
            # The legacy component gates empty factions separately from its inclusion list.
            keys = ' '.join(assigned)
    empty = overlay.get('m_visibleForEmptyFaction', '1')
    color = overlay.get('m_color', '0 0 0 0.75')
    invert = overlay.get('m_invert', '0' if standalone else '1')
    fill = overlay.get('m_drawArea', '1')
    outline = overlay.get('m_drawLine', '0')
    line_color = overlay.get('m_lineColor', '0 0 0 1') if outline == '1' else '0 0 0 0'
    line_width = overlay.get('m_lineWidth', '5')
    world_width = world_width or standalone
    hidden = standalone and fill == '0' and outline == '0'
    if ((not overlay and not standalone) or fill not in ('0', '1')
            or (fill == '0' and not hidden and (not world_width or outline != '1')) or empty not in ('0', '1')
            or (not keys and empty == '0' and not require_assigned and not hidden) or invert not in ('0', '1')
            or outline not in ('0', '1') or not all(0 <= x <= 1 for x in floats(color, 4))
            or (outline == '1' and (not 1 <= float(line_width) <= (math.inf if world_width else 20)
                                   or not math.isfinite(float(line_width))
                                   or not all(0 <= x <= 1 for x in floats(line_color, 4))))):
        raise ValueError('unsupported passive map fill')
    if keys and not re.fullmatch(r'(?:\s*"[\w.-]+"\s*)+', keys):
        raise ValueError('custom passive shape faction list')
    lines = [f'PolylineShapeEntity{name} : "{MAP_SHAPE}" {{', ' components {',
             '  GC_MapShapeComponent "{8F470B543A20D962}" {', '   m_LineColor ' + line_color,
             '   m_bFillEnabled ' + fill, '   m_FillColor ' + color, '   m_bInvertFill ' + invert]
    if hidden or overlay.get('Enabled') == '0':
        lines.append('   m_bEnabled 0')
    if open_fill:
        lines.append('   m_bFillOpenShape 1')
    if outline == '1':
        lines.append('   m_fLineWidth ' + line_width)
        if world_width:
            lines.append('   m_bWorldLineWidth 1')
    if auto_frame:
        lines.append('   m_bAutoFrameMap 1')
    if keys:
        lines.extend(['   m_aFactionKeys {', '    ' + keys.replace('\n', '\n    '), '   }',
                      '   m_bVisibleWithoutFaction ' + empty])
    elif empty == '0':
        lines.append('   m_bRequireFaction 1')
    return '\n'.join([*lines, '  }', *navmesh, ' }', *placement, '}'])


def passive_shape_component(lines, key, *, spline=False):
    values, overlay, cover, extra = settings(lines, True, standalone=True, bare=True)
    if values or cover or extra:
        raise ValueError('custom components on passive map shape')
    if spline and (overlay.get('m_drawArea') != '0' or overlay.get('m_drawLine') != '1'
                   or overlay.get('m_invert', '0') != '0'):
        raise ValueError('spline map shape needs a passive outline')
    converted = convert_passive('', [], overlay, standalone=True, require_assigned=True).splitlines()
    converted[2] = f'  GC_MapShapeComponent "{{{key}}}" {{'
    return converted[1:-1]


def render_cover(coords, vertices, yaw, cover, pitch=0, roll=0, *, name='', preserve_origin=False):
    width = float(cover.get('m_ratioWidth', '1.778'))
    if not 1 <= width < float('inf'):
        raise ValueError('invalid cover aspect ratio')
    cosine, sine = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    center = [sum(p[axis] for p in vertices) / len(vertices) for axis in range(3)]
    radius = max(math.dist(center, point) for point in vertices)
    if pitch or roll:
        origin = tuple(offset + local for offset, local in zip(coords, rotate_point(center, yaw, pitch, roll)))
    else:
        x, y, z = center
        origin = (coords[0] + x*cosine + z*sine, coords[1] + y, coords[2] - x*sine + z*cosine)
    half_width = width * radius
    corners = [(-half_width, 0, -radius), (half_width, 0, -radius),
               (half_width, 0, radius), (-half_width, 0, radius)]
    validate_polygon(corners, origin)
    key = 'map cover/' + ' '.join(f'{value:.6f}' for value in origin)
    placement_origin = coords if preserve_origin else origin
    placement = [' coords ' + ' '.join(f'{value:.6f}' for value in placement_origin), ' Points {']
    for i, corner in enumerate(corners):
        if preserve_origin:
            corner = tuple(axis + offset - anchor for axis, offset, anchor in zip(corner, origin, coords))
        placement.extend([f'  ShapePoint "{{{guid(key + "/" + str(i))}}}" {{',
                          '   Position ' + ' '.join(f'{value:.6f}' for value in corner), '  }'])
    placement.extend([' }', ' IsClosed 1'])
    return convert_passive(name, placement, {'m_color': '0 0 0 1', 'm_invert': '1'}, auto_frame=True)


def convert_instance(lines, name, passive_only=False, allow_public_faction_color=False, split_fill=True,
                     extended_shapes=False, standalone=False, parent_origin=(0, 0, 0), parent_yaw=0,
                     parent_scale=1, passive_cover=False, bare=False, nested=False):
    values, overlay, cover = {}, {}, {}
    navmesh = []
    placement = []
    vertices = []
    coords = None
    angles = {}
    scale = None
    closed = None
    i = 0
    while i < len(lines):
        field = lines[i].strip()
        if field == 'components {':
            if passive_cover and overlay:
                raise ValueError('duplicate passive cover component list')
            end = block_end(lines, i)
            values, overlay, cover, navmesh = settings(lines[i+1:end], extended_shapes, standalone,
                                                       passive_cover, bare=bare)
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
            if axis in angles or 'angles' in angles or (axis == 'angles' and angles):
                raise ValueError('mixed or duplicate rotation representations')
            if axis == 'angles':
                angles[axis] = floats(value, 3)
            else:
                angles[axis], = floats(value, 1)
            placement.append(' '+field)
        elif extended_shapes and field.startswith('scale '):
            if scale is not None:
                raise ValueError('duplicate boundary scale')
            scale, = floats(field.removeprefix('scale '), 1)
            if scale <= 0:
                raise ValueError('invalid boundary scale')
            placement.append(' '+field)
        elif field in ('IsClosed 1', 'IsClosed 0'):
            if closed is not None:
                raise ValueError('invalid shape closure setting')
            closed = field == 'IsClosed 1'
            placement.append(' '+field)
        elif field.startswith('LineColor '):
            placement.append(' '+field)
        else:
            raise ValueError('unsupported boundary property ' + field.split(' ')[0])
        i += 1
    if passive_cover and not overlay:
        raise ValueError('passive cover requires explicit component overrides')
    if bare and not (overlay or values):
        raise ValueError('bare map shape needs authored map or AO settings')
    if coords is None and not (extended_shapes and (standalone or nested)):
        raise ValueError('missing explicit AO transform')
    origin = floats(coords, 3) if coords is not None else (0, 0, 0)
    pitch, yaw, roll = angles.get('angles', tuple(angles.get(axis, 0) for axis in ('angleX', 'angleY', 'angleZ')))
    if (pitch or roll) and not extended_shapes:
        raise ValueError('tilted AO needs world-space validation')
    world_scale = parent_scale * (scale if scale is not None else 1)
    if world_scale != 1:
        vertices = [tuple(axis * world_scale for axis in point) for point in vertices]
    if cover and values.get('Enabled') == '0' and not overlay and not passive_only:
        if nested or pitch or yaw or roll or world_scale != 1:
            raise ValueError('cover without AO needs a top-level, unrotated shape at unit scale')
        if len(vertices) < 3:
            raise ValueError('cover needs at least three source points')
        # The source points determine the frame radius. Only the emitted rectangle is rendered.
        return render_cover(origin, vertices, yaw, cover, name=name, preserve_origin=True), len(vertices)
    passive_overlay = extended_shapes and values.get('Enabled') == '0' and bool(overlay)
    standalone_ao = standalone and values.get('Enabled') == '1'
    visible_standalone = (overlay.get('Enabled') != '0'
                          and (overlay.get('m_drawArea', '1'), overlay.get('m_drawLine', '0')) != ('0', '0'))
    if (standalone and not standalone_ao and closed is not False and len(vertices) == 2
            and overlay.get('m_drawArea') == '0' and overlay.get('m_drawLine') == '1'):
        # The legacy renderer adds a closing segment only for outlines with more than two points.
        if ' IsClosed 1' in placement:
            placement.remove(' IsClosed 1')
        placement.append(' IsClosed 0')
        closed = False
    open_fill = (extended_shapes and closed is False and not cover and not standalone_ao
                 and (standalone or passive_overlay) and overlay.get('m_drawArea', '1') == '1'
                 and overlay.get('m_drawLine') == '1' and overlay.get('m_invert', '0' if standalone else '1') == '0')
    if closed is not False or standalone_ao or open_fill:
        relative = tuple(axis * parent_scale for axis in origin)
        if parent_yaw:
            relative = rotate_point(relative, parent_yaw, 0, 0)
        world_origin = tuple(local + parent for local, parent in zip(relative, parent_origin))
        validate_polygon(vertices, world_origin, yaw + parent_yaw, pitch, roll)
    if (closed is False and not open_fill and not (standalone_ao and not visible_standalone)
            and (not (standalone or passive_overlay) or cover
                 or not 2 <= len(vertices) <= 128 or overlay.get('m_drawArea', '1') != '0')):
        raise ValueError('open map shape requires a passive outline with 2 to 128 points')
    standalone_overlay = ''
    if standalone:
        if not any(line.startswith(' LineColor ') for line in placement):
            placement.append(' LineColor 1 0 0 1')
        if not standalone_ao:
            return convert_passive(name, placement, overlay, standalone=True, navmesh=navmesh,
                                   open_fill=open_fill), len(vertices)
        if visible_standalone:
            standalone_overlay = convert_passive('', placement, overlay, standalone=True) + '\n'
        if closed is False:
            # The legacy AO closes the polygon for containment and joins the last outline point to the first.
            placement[placement.index(' IsClosed 0')] = ' IsClosed 1'
        overlay = {}
    cover_shape = render_cover(origin, vertices, yaw, cover, pitch, roll) + '\n' if cover else ''
    if passive_only or (passive_overlay and not standalone_ao):
        if values.get('Enabled') != '0':
            raise ValueError('passive shape requires a disabled AO')
        return cover_shape + convert_passive(name, placement, overlay, world_width=extended_shapes,
                                             open_fill=open_fill), len(vertices)
    keys = values.get('m_factionKeys','')
    vehicles = values.get('m_ignoredVehicles','')
    if keys and not re.fullmatch(r'(?:\s*"[\w.-]+"\s*)+', keys):
        raise ValueError('custom faction list')
    if vehicles and not re.fullmatch(r'(?:\s*"\{[\da-fA-F]{16}\}[^"]+\.et"\s*)+', vehicles):
        raise ValueError('custom vehicle filter')
    visibility = values.get('m_visibility','ALL')
    faction_color = values.get('m_useFactionColor','1')
    grace = values.get('m_killTimer','20')
    grace_seconds = float(grace)
    width = values.get('m_lineWidth','3')
    color = values.get('m_defaultColor','0 0 0 1')
    if (visibility not in ('ALL','FACTION','NONE') or faction_color not in ('0','1')
            or not (grace_seconds == 0 or 0.1 <= grace_seconds <= 3600) or not 1 <= float(width) <= 20
            or values.get('Enabled','1') != '1' or not all(0 <= x <= 1 for x in floats(color,4))):
        raise ValueError('unsupported boundary settings')
    public_tint = bool(keys) and faction_color == '1' and visibility == 'ALL'
    if public_tint and not allow_public_faction_color:
        raise ValueError('public outline colored by a restricted faction')
    visible = visibility == 'ALL' or (visibility == 'FACTION' and bool(keys))
    map_keys = keys if visibility == 'FACTION' else ''
    use_color = faction_color if visible and map_keys else '0'
    fill = overlay.get('m_drawArea', '1') if overlay else '0'
    if fill not in ('0', '1'):
        raise ValueError('unsupported map fill')
    fill_color = overlay.get('m_color', '0 0 0 0.75')
    invert = overlay.get('m_invert', '1')
    passive_fill = ''
    if (cover or extended_shapes) and overlay.get('m_drawLine') == '1':
        passive_fill = convert_passive('', placement, overlay, world_width=extended_shapes) + '\n'
        fill = '0'
    if fill == '1':
        fill_keys = overlay.get('m_factionKeys', '')
        empty = overlay.get('m_visibleForEmptyFaction', '1')
        if fill_keys and not re.fullmatch(r'(?:\s*"[\w.-]+"\s*)+', fill_keys):
            raise ValueError('invalid map fill audience')
        if invert not in ('0', '1') or not all(0 <= x <= 1 for x in floats(fill_color, 4)):
            raise ValueError('invalid map fill style')
        different_factions = visible and set(re.findall(r'"([^"]+)"', fill_keys)) != set(re.findall(r'"([^"]+)"', map_keys))
        different_unassigned = empty != ('0' if fill_keys else '1')
        if not visible:
            color, map_keys = '0 0 0 0', fill_keys
        if different_factions or different_unassigned:
            if not split_fill:
                raise ValueError('map fill and outline target different factions' if different_factions
                                 else 'separate unassigned map fill audience')
            passive_fill = convert_passive('', placement, overlay) + '\n'
            fill = '0'
    lines = [f'GC_AOLimitEntity{name} : "{DEST}" {{',' components {',
             '  GC_BoundaryWallComponent "{6A4D831C2E5079C1}" {','   m_bEnabled 0','  }',
             '  GC_MapShapeComponent "{8F470B543A20D964}" {', f"   m_bEnabled {int(visible or fill == '1')}",
             f'   m_bFillEnabled {fill}', f'   m_LineColor {color}', f'   m_fLineWidth {width}']
    if fill == '1':
        lines.extend(['   m_FillColor '+fill_color, '   m_bInvertFill '+invert])
    if map_keys:
        lines.extend(['   m_aFactionKeys {','    '+map_keys,'   }', '   m_bFactionColor '+use_color])
    if public_tint:
        first_key = re.search(r'"[\w.-]+"', keys)[0]
        lines.extend(['   m_bFactionColor 1', '   m_sOutlineFactionKey ' + first_key])
    lines.extend(['  }', *navmesh, ' }',' m_eBehavior SOFT_KILL',' m_fGraceSeconds '+grace])
    if keys:
        lines.extend([' m_aFactionKeys {','  '+keys,' }'])
    if vehicles:
        lines.extend([' m_eVehicleMode EXCEPT_MATCHING',' m_aVehiclePrefabs {','  '+vehicles,' }'])
    return cover_shape + standalone_overlay + passive_fill + '\n'.join([*lines,*placement,'}']), len(vertices)


def group_members(lines, start, stop, root):
    if not root['group']:
        yield start, stop, root['name']
        return
    indent = len(lines[start]) - len(lines[start].lstrip()) + 1
    index = start + 1
    while index < stop:
        head = re.fullmatch(' ' * indent + r'(?:([\w.-]+) )?\{', lines[index])
        if not head:
            raise ValueError('unsupported grouped map entity')
        end = block_end(lines, index)
        if end >= stop:
            raise ValueError('group member extends outside the map entity group')
        yield index, end, head[1]
        index = end + 1


def convert_hierarchy(lines, name, options, parent_origin=(0, 0, 0), depth=0, parent_yaw=0,
                      *, marker=None, area_children=None, parent_scale=1):
    child_start = next((i for i, line in enumerate(lines) if line.strip() == '{'), None)
    # Only a brace at the root-property indent opens the entity's child list.
    if child_start is not None:
        indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
        child_start = next((i for i, line in enumerate(lines) if line == ' ' * indent + '{'), None)
    field_end = child_start if options['extended_shapes'] else None
    if marker:
        body = render_marker(lines[:field_end], name, marker_fields(marker), draw_order=True)
        count = 0
    else:
        body, count = convert_instance(lines[:field_end], name, parent_origin=parent_origin,
                                       parent_yaw=parent_yaw, parent_scale=parent_scale, nested=depth > 0, **options)
    if child_start is None or not options['extended_shapes']:
        return body, count
    if (depth >= 16 or child_start == len(lines) - 2
            or block_end(lines, child_start) != len(lines) - 1):
        raise ValueError('unsupported boundary child list')
    if marker:
        fields = [line[1:] for line in body.splitlines() if line.startswith(' ') and not line.startswith('  ')]
    else:
        fields = [line.strip() for line in lines[:child_start] if line.startswith(' ' * indent)
                  and not line.startswith(' ' * (indent + 1))]
    if any(field.startswith(('angleX ', 'angleZ ')) for field in fields):
        raise ValueError('nested boundary requires a parent with yaw-only rotation')
    scales = [floats(field[6:], 1)[0] for field in fields if field.startswith('scale ')]
    if len(scales) > 1 or any(scale <= 0 for scale in scales):
        raise ValueError('invalid nested boundary scale')
    world_scale = parent_scale * (scales[0] if scales else 1)
    yaw = 0
    for field in fields:
        if field.startswith('angles '):
            pitch, yaw, roll = floats(field[7:], 3)
            if pitch or roll:
                raise ValueError('nested boundary requires a parent with yaw-only rotation')
        elif field.startswith('angleY '):
            yaw, = floats(field[7:], 1)
    position = [floats(field[7:], 3) for field in fields if field.startswith('coords ')]
    if not position and (options['standalone'] or depth > 0):
        position = [(0, 0, 0)]
    if len(position) != 1:
        raise ValueError('nested boundary requires one explicit parent origin')
    relative = tuple(axis * parent_scale for axis in position[0])
    if parent_yaw:
        relative = rotate_point(relative, parent_yaw, 0, 0)
    origin = tuple(local + parent for local, parent in zip(relative, parent_origin))
    if not marker and not options['standalone'] and not re.search(r'^GC_AOLimitEntity\b', body, re.M):
        raise ValueError('nested boundary needs an active AO, standalone map shape, or marker')
    children = []
    index = child_start + 1
    while index < len(lines) - 1:
        header = lines[index][indent + 1:]
        passive_cover = PASSIVE_COVER.fullmatch(header)
        standalone = passive_cover or STANDALONE_SHAPE.fullmatch(header)
        child_marker = MARKER.fullmatch(header)
        match = standalone or ROOT.fullmatch(header) or child_marker
        if lines[index][:indent + 1] != ' ' * (indent + 1):
            raise ValueError('invalid nested map entity indentation')
        stop = block_end(lines, index)
        if stop >= len(lines) - 1:
            raise ValueError('nested entity lies outside boundary child list')
        if match:
            for start, end, member_name in group_members(lines, index, stop, match):
                child_name = ' ' + member_name if member_name else ''
                child, vertices = convert_hierarchy(
                    lines[start + 1:end], child_name,
                    {**options, 'standalone': bool(standalone), 'passive_cover': bool(passive_cover), 'bare': False},
                    origin, depth + 1, yaw + parent_yaw, marker=child_marker, area_children=area_children,
                    parent_scale=world_scale)
                children.extend('  ' + line for line in child.splitlines())
                count += vertices
        elif area_children and name and options['standalone'] and 'IsClosed 0' not in fields:
            if world_scale != 1:
                raise ValueError('scaled boundary with gameplay children needs review')
            child_block = [line[indent + 1:] for line in lines[index:stop + 1]]
            children.extend('  ' + line for line in area_children(name.strip(), child_block))
        else:
            raise ValueError('unrecognized nested boundary entity')
        index = stop + 1
    return '\n'.join([body.rsplit('\n', 1)[0], ' {', *children, ' }', '}']), count


def bridge_polygon(lines, name, first, second, metres):
    """Join the lobes of a reviewed single crossing while retaining every source vertex."""
    start = next((i for i, line in enumerate(lines) if line.strip() == 'Points {'), None)
    if start is None:
        raise ValueError('reviewed polygon is missing its point list')
    stop = block_end(lines, start)
    fields = lines[start + 1:stop]
    indent = ' ' * (len(lines[start]) - len(lines[start].lstrip()) + 1)
    if (len(fields) % 3 or not 0 <= first < second < len(fields) // 3 - 1
            or any(not re.fullmatch(indent + r'ShapePoint "\{[\dA-Fa-f]{16}\}" \{', fields[i])
                   or not fields[i + 1].startswith(indent + ' Position ') or fields[i + 2] != indent + '}'
                   for i in range(0, len(fields), 3))):
        raise ValueError('reviewed crossing point list changed')
    authored = [fields[i:i + 3] for i in range(0, len(fields), 3)]
    points = [floats(point[1].strip().removeprefix('Position '), 3) for point in authored]
    a, b, c, d = (points[index] for index in (first, first + 1, second, second + 1))
    ux, uz, vx, vz = b[0] - a[0], b[2] - a[2], d[0] - c[0], d[2] - c[2]
    determinant = ux * vz - uz * vx
    if not determinant:
        raise ValueError('reviewed polygon edges no longer cross')
    t = ((c[0] - a[0]) * vz - (c[2] - a[2]) * vx) / determinant
    u = ((c[0] - a[0]) * uz - (c[2] - a[2]) * ux) / determinant
    dt, du = metres / math.hypot(ux, uz), metres / math.hypot(vx, vz)
    if not (0 < t - dt < t + dt < 1 and 0 < u - du < u + du < 1):
        raise ValueError('reviewed crossing cannot fit its bridge within both edges')

    def nearby(label, start, end, fraction):
        position = ' '.join(f'{a + fraction * (b - a):.5f}' for a, b in zip(start, end))
        return [indent + f'ShapePoint "{{{guid(name + label)}}}" {{',
                indent + ' Position ' + position, indent + '}']

    ordered = [*authored[:first + 1], nearby('A', a, b, t - dt), nearby('C', c, d, u - du),
               *reversed(authored[first + 1:second + 1]), nearby('B', a, b, t + dt), nearby('D', c, d, u + du),
               *authored[second + 1:]]
    return [*lines[:start + 1], *(line for point in ordered for line in point), *lines[stop:]]


def convert_boundaries(text, allow_public_faction_color=False, split_fill=True, *, extended_shapes=False,
                       area_children=None, allow_bare=False, source_blob=None, source_line=1):
    lines = text.splitlines()
    emitted, review = [], {}
    bridge = REVIEWED_BOUNDARY_CROSSINGS.get(source_blob)
    for i,line in enumerate(lines):
        passive_cover = PASSIVE_COVER.fullmatch(line) if extended_shapes else None
        bare = BARE_SHAPE.fullmatch(line) if allow_bare else None
        standalone = bare or passive_cover or (STANDALONE_SHAPE.fullmatch(line) if extended_shapes else None)
        root = standalone or ROOT.fullmatch(line)
        if not root:
            continue
        end = block_end(lines,i)
        try:
            instances = list(group_members(lines, i, end, root))
        except ValueError as error:
            review[str(i+1)] = {'gap': str(error)}
            continue
        for start, stop, name in instances:
            try:
                fields = lines[start + 1:stop]
                bridge_here = bridge and start + source_line == bridge[0]
                if bridge_here:
                    fields = bridge_polygon(fields, *bridge[1:])
                body,count = convert_hierarchy(fields, ' '+name if name else '',
                                               {'allow_public_faction_color': allow_public_faction_color,
                                                'split_fill': split_fill, 'extended_shapes': extended_shapes,
                                                'standalone': bool(standalone), 'passive_cover': bool(passive_cover),
                                                'bare': bool(bare)},
                                               area_children=area_children)
                emitted.append(body)
                review[str(start+1)] = {'vertices':count,'name':name,'lastLine':stop+1}
                if bridge_here:
                    review[str(start+1)]['bridgeMetres'] = bridge[-1]
            except (ArithmeticError, ValueError) as error:
                review[str(start+1)] = {'gap':str(error)}
    return '\n'.join(emitted)+'\n' if emitted else '', review
