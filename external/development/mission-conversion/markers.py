"""Port standalone legacy icon layers whose visual and audience fields are understood."""
from decimal import Decimal
import json
from pathlib import Path
import re
from briefings import block_end, guid, properties
from launchers import META_NAME
from scenery import FIELD, NUMBER

BASE = 'CD85ADE9E0F54679'
LEGACY = re.compile(r'^(?P<group>\$grp )?PS_ManualMarker(?: (?P<name>[\w.\-]+))?'
                    r' : "\{(?P<guid>[A-Fa-f0-9]{16})\}(?P<path>[^"]+)" \{$')
PRESETS = json.loads(Path(__file__).with_name('marker_presets.json').read_text(encoding='utf-8'))
DEST = '{924D4B5FA0B24B22}Prefabs/Logic/Mission/MapMarker.et'
HOVER_CAMERA = re.compile(r'PS_ManuaMarkerRender(?: (?P<name>[\w.-]+))? : '
                          r'"\{88D799C5DED9EC70\}PrefabsEditable/Markers/EditableMarkerRender.et" \{')
CAMERA_ANCHOR = re.compile(
    r' \{\n  GenericEntity \{\n   ID "5F36D427DE745527"\n'
    r'(?P<transform>(?:   (?:coords|angles|angle[XYZ]) [^\n]+\n)*)'
    r'   \{\n    SCR_CameraBase "[\da-fA-F]{16}" \{\n     ID "5F36D427B0D92373"\n'
    r'     coords [^\n]+\n    \}\n   \}\n  \}\n \}')
FIELDS = {
    'm_sImageSet': 'm_sImageSetOrImage', 'm_sImageSetGlow': 'm_sHighlightImageSet',
    'm_sQuadName': 'm_sQuadName', 'm_MarkerColor': 'm_Color',
    'm_fWorldSize': 'm_fSize', 'm_bUseWorldScale': 'm_bWorldScale',
    'm_sDescription': 'm_sDescription',
}
TRANSFORM = {'coords', 'angles', 'angleX', 'angleY', 'angleZ', 'scale'}
AUDIENCE = {'m_aVisibleForFactions', 'm_bVisibleForEmptyFaction', 'm_bShowForAnyFaction', 'm_aHideOnGameModeStates'}
ALLOWED = TRANSFORM | AUDIENCE | set(FIELDS) | {'m_bringToFront'}
DEFAULTS = {
    'coords': '1060.328 39.01 2682.178', 'angleY': '90',
    'm_sImageSet': '"{E23427CAC80DA8B7}UI/Textures/Icons/icons_mapMarkersUI.imageset"',
    'm_sImageSetGlow': '"{67B3A6DC2D712B52}UI/Textures/Icons/icons_mapMarkersUI-glow.imageset"',
    'm_sQuadName': '"circle-2"', 'm_fWorldSize': '20', 'm_bUseWorldScale': '1',
    'm_MarkerColor': '1 1 1 1', 'm_sDescription': '""'
}
PHASES = ('PREGAME', 'SLOTTING', 'BRIEFING', 'GAME', 'POSTGAME')
LEGACY_STATES = {'SLOTSELECTION': 'SLOTTING', '1': 'GAME', '5': 'BRIEFING'}
UNUSED_STATES = {'CUTSCENE', 'DEBRIEFING', 'NULL'}
WIDE_COVER_FIELDS = {
    'm_sImageSet': '"{3B62608C6AB716B9}Covermap/UCAMF_CoverMap.imageset"',
    'm_sQuadName': '"8x4"',
    'm_bUseWorldScale': '1',
    'm_fWorldSize': '128000',
}
EMPTY_CLEANUP_PROTECTION = re.compile(r'\s*PK_EntityProtectorComponent "(\{[A-Fa-f0-9]{16}\})" \{')
BAKED_MARKER_GENERATOR = re.compile(
    r'PolylineShapeEntity \{\n coords (?P<origin>[^\n]+)\n Points \{\n'
    r'(?:  ShapePoint "\{[\da-fA-F]{16}\}" \{\n   Position [^\n]+\n  \}\n)+'
    r' \}\n \{\n'
    r'  PrefabGeneratorEntity : "\{0555ABAB329C6DFB\}Prefabs/WEGenerators/Markers/PG_MarkerGenerator_Base.et" \{\n'
    r'   Flags 0x100003 0\n   m_iSeed \d+\n   m_PrefabNames \{\n'
    r'    (?P<reference>"\{(?P<guid>[\da-fA-F]{16})\}(?P<path>[^"\n]+\.et)")\n   \}\n'
    r'   m_fDistance [\d.]+\n   \{\n    \$grp PS_ManualMarker : (?P=reference) \{\n'
    r'(?P<members>.*?)\n    \}\n   \}\n  \}\n \}\n\}', re.S)
BAKED_MARKER_MEMBER = re.compile(r'     \{\n      coords ([^\n]+)\n      angles ([^\n]+)\n     \}')
WALL_MARKER_SHAPE = re.compile(
    rf'PolylineShapeEntity \{{\n coords (?P<origin>{NUMBER} {NUMBER} {NUMBER})\n Points \{{\n'
    rf'(?:  ShapePoint "\{{[\da-fA-F]{{16}}\}}" \{{\n   Position {NUMBER} {NUMBER} {NUMBER}\n  \}}\n)+'
    rf' \}}\n \{{\n  WallGeneratorEntity \{{\n   Flags 0x100003 0\n   m_iSeed \d+\n'
    rf'   PrePadding {NUMBER}\n   PostPadding {NUMBER}\n(?:   m_fOvershoot {NUMBER}\n)?'
    r'   m_aWallGroups \{\n    WallLengthGroup "\{[\da-fA-F]{16}\}" \{\n     m_aWallPrefabs \{\n'
    r'      WallWeightPair "\{[\da-fA-F]{16}\}" \{\n'
    r'       m_sWallAsset (?P<reference>"\{(?P<guid>[\da-fA-F]{16})\}(?P<path>[^"\n]+\.et)")\n'
    rf'       m_fPrePadding {NUMBER}\n       m_fPostPadding {NUMBER}\n'
    r'      \}\n     \}\n    \}\n   \}\n(?:   UseXAsForward [01]\n)?'
    r'   \{\n    \$grp PS_ManualMarker : (?P=reference) \{\n(?P<members>.*?)\n    \}\n   \}\n  \}\n \}\n\}', re.S)
WALL_MARKER_MEMBER = re.compile(r'     \{\n      coords ([^\n]+)(?:\n      angles ([^\n]+))?\n     \}')
BAKED_OPFOR_LIMIT = re.compile(
    r'PolylineShapeEntity FLET \{\n coords (?P<origin>[^\n]+)\n Points \{\n'
    r'(?:  ShapePoint "\{[\da-fA-F]{16}\}" \{\n   Position [^\n]+\n  \}\n)+'
    r' \}\n LineColor 0 0 0 0\n \{\n'
    r'  PrefabGeneratorEntity : "\{6B59CE9ABF067E0A\}Prefabs/WEGenerators/Markers/PG_MarkerGenerator_AOLimit_OPFOR.et" \{\n'
    r'   Flags 1048579\n   coords 0 0 0\n   \{\n'
    r'    \$grp PS_ManualMarker : "\{5F773B024F23FD0F\}PrefabsEditable/Markers/AOLimit/Marker_AOLimit_OPFOR.et" \{\n'
    r'(?P<members>.*?)\n    \}\n   \}\n  \}\n \}\n\}', re.S)
BAKED_OPFOR_LIMIT_MEMBER = re.compile(r'     \{\n      coords ([^\n]+)\n      m_sDescription ("FLET")\n     \}')
# TilW ef141d7 removed the generator and both marker templates. These are their inherited display fields.
OPFOR_LIMIT_FIELDS = {'m_sImageSetGlow': '""', 'm_MarkerColor': '0.502 0 0 1', 'm_sQuadName': '"dot"',
                      'm_fWorldSize': '75', 'm_bVisibleForEmptyFaction': '1', 'm_bShowForAnyFaction': '1'}


def convert_baked_marker_generator(block, source, dev):
    """Keep saved runtime arrows from a native editor-only shape and generator."""
    match = BAKED_MARKER_GENERATOR.fullmatch('\n'.join(block))
    if not match:
        limit = BAKED_OPFOR_LIMIT.fullmatch('\n'.join(block))
        if limit:
            return render_baked_markers(limit, BAKED_OPFOR_LIMIT_MEMBER, 'm_sDescription', OPFOR_LIMIT_FIELDS, dev)
        raise ValueError('unverified baked marker generator')
    inherited = local_marker_fields(source, match['guid'].upper(), match['path'])
    if TRANSFORM.intersection(inherited) != {'coords', 'angles'}:
        raise ValueError('baked marker template has additional transforms')
    return render_baked_markers(match, BAKED_MARKER_MEMBER, 'angles', inherited, dev)


def local_marker_fields(source, identity, path):
    if source.resources.get(identity) != [path]:
        raise ValueError('unverified local marker template')
    template = source.read(path).splitlines()
    if len(template) < 3:
        raise ValueError('baked marker template has no authored fields')
    root = LEGACY.fullmatch(template[0])
    if (not root or root['guid'].upper() != BASE or root['group'] or root['name'] or template[-1] != '}'
            or not re.fullmatch(r' ID "[\da-fA-F]{16}"', template[1])):
        raise ValueError('unsupported baked marker template')
    return properties(template[2:-1], ALLOWED)


def convert_wall_markers(block, source, dev):
    shapes = []
    if block[0] == '$grp PolylineShapeEntity {':
        start = 1
        while start < len(block) - 1:
            if block[start] != ' {':
                raise ValueError('unsupported wall marker shape member')
            stop = block_end(block, start)
            if stop >= len(block) - 1 or not all(line.startswith(' ') for line in block[start + 1:stop]):
                raise ValueError('invalid wall marker shape member')
            shapes.append('\n'.join(['PolylineShapeEntity {', *(line[1:] for line in block[start + 1:stop]), '}']))
            start = stop + 1
    else:
        shapes.append('\n'.join(block))
    if not shapes:
        raise ValueError('wall marker generator has no shapes')
    result = []
    for shape in shapes:
        match = WALL_MARKER_SHAPE.fullmatch(shape)
        if not match:
            raise ValueError('unsupported wall marker generator')
        inherited = local_marker_fields(source, match['guid'].upper(), match['path'])
        if TRANSFORM.intersection(inherited) - {'coords', 'angles'}:
            raise ValueError('wall marker template has additional transforms')
        result.append(render_baked_markers(match, WALL_MARKER_MEMBER, 'angles', inherited, dev))
    return ''.join(result)


def render_baked_markers(match, pattern, member_field, inherited, dev):
    origin = match['origin']
    members = match['members']
    if not FIELD.fullmatch('coords ' + origin) or pattern.sub('', members).strip():
        raise ValueError('invalid saved marker transforms')
    output = []
    for coords, value in pattern.findall(members):
        if not FIELD.fullmatch('coords ' + coords) or (value and member_field == 'angles'
                                                       and not FIELD.fullmatch('angles ' + value)):
            raise ValueError('invalid saved marker transforms')
        absolute = ' '.join(format(Decimal(parent) + Decimal(child), 'f')
                            for parent, child in zip(origin.split(), coords.split()))
        placement = [' coords ' + absolute]
        if value:
            placement.append(f' {member_field} {value}')
        output.append(instance(placement, '', inherited, draw_order=True))
    if not output:
        raise ValueError('baked marker generator has no saved markers')
    return relocate_images('\n'.join(output) + '\n', dev)


def phase_fields(value):
    """Project legacy visibility onto the corresponding Lobby phases."""
    hidden = {LEGACY_STATES.get(state, state) for state in value.split()} - UNUSED_STATES
    if hidden - set(PHASES):
        raise ValueError('unknown marker phase: ' + ', '.join(sorted(hidden - set(PHASES))))
    if not hidden:
        return []
    if hidden == set(PHASES):
        # Invert the first phase threshold, so the marker is never shown.
        return ['   m_eShowAtGameModeState PREGAME', '   m_bInvertGameModeState 1']
    for index, phase in enumerate(PHASES[1:], 1):
        if hidden == set(PHASES[:index]):
            return ['   m_eShowAtGameModeState ' + phase]
        if hidden == set(PHASES[index:]):
            return ['   m_eShowAtGameModeState ' + phase, '   m_bInvertGameModeState 1']
    return ['   m_aHiddenPhases {', *('    ' + phase for phase in PHASES if phase in hidden), '   }']


def instance(lines, name, inherited=None, *, draw_order=False):
    protection = []
    authored_components = [index for index, line in enumerate(lines) if line.strip() == 'components {']
    if len(authored_components) == 1:
        start, = authored_components
        stop = block_end(lines, start)
        if stop == start + 3 and (component := EMPTY_CLEANUP_PROTECTION.fullmatch(lines[start + 1])):
            protection = [f'  GC_StartupCleanupProtectionComponent "{component[1]}" {{', '  }']
            lines = [*lines[:start], *lines[stop + 1:]]
    placed = properties(lines, ALLOWED | {'m_iZOrder'} if draw_order else ALLOWED)
    values = {**(inherited or {}), **placed}
    z_order = values.get('m_iZOrder', '0')
    raise_on_hover = values.get('m_bringToFront', '1')
    if not re.fullmatch(r'-?\d+', z_order) or not -2147483648 <= int(z_order) <= 2147483647:
        raise ValueError('invalid marker draw order')
    if raise_on_hover not in ('0', '1'):
        raise ValueError('invalid marker hover priority')
    if not draw_order and values.get('m_bringToFront', '0') != '0':
        raise ValueError('explicit marker layering priority')
    hover_order = '10000' if raise_on_hover == '1' else '-1'
    any_side = values.get('m_bShowForAnyFaction', '0')
    unassigned = values.get('m_bVisibleForEmptyFaction', '0')
    factions = values.get('m_aVisibleForFactions', '')
    keys = [key for key in re.findall(r'"([^"]*)"', factions) if key]
    phase = phase_fields(values.get('m_aHideOnGameModeStates', ''))
    if any_side not in ('0', '1') or unassigned not in ('0', '1'):
        raise ValueError('invalid marker audience')
    if factions and (not re.fullmatch(r'(?:\s*"[^"]*"\s*)+', factions)
                     or len(set(keys)) != len(keys) or len(keys) > 16):
        raise ValueError('unsupported faction list')
    if '""' in factions:
        # Legacy unassigned visibility comes from its dedicated field. Blank
        # entries in the faction list do not name a registered faction.
        factions = ' '.join(f'"{key}"' for key in keys)
    merged = {**DEFAULTS, **values}
    if 'angles' in values:
        if any(k in values for k in ('angleX', 'angleY', 'angleZ')):
            raise ValueError('mixed angle representations')
        merged.pop('angleY')
    if len(merged['m_sDescription'].encode()) - 2 > 512 or len(name.encode()) > 128:
        raise ValueError('marker exceeds native Lobby snapshot limits')
    wide_cover = all(merged[field] == value for field, value in WIDE_COVER_FIELDS.items())
    if not wide_cover and not 0.01 <= float(merged['m_fWorldSize']) <= 100000:
        raise ValueError('unsupported marker size')
    output = [f'GenericEntity{name} : "{DEST}" {{', ' components {',
              '  GC_BriefingMarkerComponent "{00C8292F836D4D6F}" {']
    for old, new in FIELDS.items():
        output.append(f'   {new} ' + merged[old].replace('\n', '\n   '))
    if draw_order:
        output.extend([f'   m_iZOrder {z_order}', f'   m_iHoverZOrder {hover_order}'])
    if any_side == '0' and keys:
        output.extend(['   m_aFactionKeys {', '    ' + factions, '   }'])
    explicit_list = any_side == '0' and (unassigned == '1' or not keys)
    if explicit_list:
        output.append('   m_bRestrictToListedFactions 1')
    if unassigned == '0' and (any_side == '1' or explicit_list):
        output.append('   m_bHideWithoutFaction 1')
    output.extend(phase)
    output.extend(['  }', *protection, ' }'])
    output.extend(f' {k} {v}' for k, v in merged.items() if k in TRANSFORM)
    return '\n'.join(output + ['}'])


def convert_hover_camera(block, source_key):
    root = HOVER_CAMERA.fullmatch(block[0])
    if not root:
        raise ValueError('unverified hover-camera marker prefab')
    name = root['name'] or ('GC_HoverMarker_' + guid(source_key))
    fields = block[1:-1]
    anchor = []
    if ' {' in fields:
        split = fields.index(' {')
        child = CAMERA_ANCHOR.fullmatch('\n'.join(fields[split:]))
        if not child or not all(FIELD.fullmatch(line.strip()) for line in child['transform'].splitlines()):
            raise ValueError('unverified hover-camera anchor override')
        fields, anchor = fields[:split], child['transform'].splitlines()
    marker = instance(fields, ' ' + name, draw_order=True)
    marker = marker.replace('  GC_BriefingMarkerComponent "{00C8292F836D4D6F}" {\n',
                            '  GC_BriefingMarkerComponent "{00C8292F836D4D6F}" {\n   m_bPreviewOnHover 1\n', 1)
    # The legacy render handler uses the transform of the anchor, ignoring its camera child.
    return '\n'.join([marker.rsplit('\n}', 1)[0], ' {', '  GenericEntity {',
                      *anchor, '  }', ' }', '}', ''])


def relocate_images(text, dev):
    pattern = re.compile(r'(?m)^( {3,}m_s(?:ImageSetOrImage|HighlightImageSet) "\{)([\da-fA-F]{16})(\})([^"\n]+)(")$')

    def resolve(match):
        guid = match[2].upper()
        paths = dev.resources.get(guid, [])
        if len(paths) != 1 or paths[0] == match[4]:
            return match[0]
        path = paths[0]
        identity = META_NAME.search(dev.read(path + '.meta'))
        if not identity or identity[3] != path or not path.endswith(('.imageset', '.edds')):
            return match[0]
        return match[1] + match[2] + match[3] + path + match[5]

    return pattern.sub(resolve, text)


def marker_fields(match, source=None):
    guid = match['guid'].upper()
    if guid == BASE:
        return None
    preset = PRESETS.get(guid)
    if not preset and source and source.resources.get(guid) == [match['path']]:
        return local_marker_fields(source, guid, match['path'])
    if not preset or preset['path'] != match['path']:
        raise ValueError(f'inherited marker preset {guid}')
    return preset['fields']


def convert_instance(lines, name, inherited, draw_order, depth=0):
    # Preserve parent-relative coordinates and rotation by keeping the same hierarchy.
    if not lines or lines[-1].strip() != '}':
        return instance(lines, name, inherited, draw_order=draw_order), 1
    indent = ' ' * (len(lines[-1]) - len(lines[-1].lstrip()))
    start = next((i for i, line in enumerate(lines) if line == indent + '{'), None)
    if start is None:
        return instance(lines, name, inherited, draw_order=draw_order), 1
    stop = block_end(lines, start)
    if stop != len(lines) - 1 or depth >= 16:
        raise ValueError('unsupported marker hierarchy')
    marker = instance(lines[:start], name, inherited, draw_order=draw_order).splitlines()
    children = []
    count = 1
    index = start + 1
    while index < stop:
        head = lines[index].removeprefix(indent + ' ')
        child = LEGACY.fullmatch(head)
        if not child:
            raise ValueError('unsupported child of marker: ' + head.strip().partition(' ')[0])
        end = block_end(lines, index)
        if end >= stop:
            raise ValueError('marker placement extends outside the child list')
        nested, nested_count = convert_placement(lines, index, end, child, draw_order, depth + 1)
        children.extend('  ' + line for line in nested.splitlines())
        count += nested_count
        index = end + 1
    return '\n'.join([*marker[:-1], ' {', *children, ' }', marker[-1]]), count


def convert_placement(lines, start, stop, match, draw_order, depth=0, *, source=None):
    inherited = marker_fields(match, source)
    if not match['group']:
        name = ' ' + match['name'] if match['name'] else ''
        return convert_instance(lines[start + 1:stop], name, inherited, draw_order, depth)
    indent = len(lines[start]) - len(lines[start].lstrip())
    member = re.compile(r' ' * (indent + 1) + r'(?:(?P<name>[\w.\-]+) )?\{')
    result = []
    count = 0
    index = start + 1
    while index < stop:
        head = member.fullmatch(lines[index])
        if not head:
            raise ValueError('custom grouped marker instance')
        end = block_end(lines, index)
        if end >= stop:
            raise ValueError('marker placement extends outside the group')
        name = ' ' + head['name'] if head['name'] else ''
        converted, members = convert_instance(lines[index + 1:end], name, inherited, draw_order, depth)
        result.append(converted)
        count += members
        index = end + 1
    if not result:
        raise ValueError('empty marker group')
    return '\n'.join(result), count


def convert_layer(text, dev=None, *, draw_order=False, source=None):
    """Process complete icon-only layers. Mixed layers keep their original ordering for review."""
    if not re.search(r'^(?:\$grp )?PS_ManualMarker\b', text, re.M):
        return None
    lines = text.splitlines()
    result = []
    count = 0
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        match = LEGACY.fullmatch(lines[i])
        if not match:
            raise ValueError(f'line {i + 1} mixed or custom placement')
        stop = block_end(lines, i)
        try:
            rendered, converted = convert_placement(lines, i, stop, match, draw_order, source=source)
        except ValueError as exc:
            raise ValueError(f'line {i + 1} {exc}') from exc
        result.append(rendered)
        count += converted
        i = stop + 1
    output = '\n'.join(result) + '\n'
    return (relocate_images(output, dev) if dev else output), count
