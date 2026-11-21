"""Port standalone legacy icon layers whose visual and audience fields are understood."""
import json
from pathlib import Path
import re
from briefings import block_end, properties
from launchers import META_NAME

BASE = 'CD85ADE9E0F54679'
LEGACY = re.compile(r'^(?P<group>\$grp )?PS_ManualMarker(?: (?P<name>[\w.\-]+))?'
                    r' : "\{(?P<guid>[A-Fa-f0-9]{16})\}(?P<path>[^"]+)" \{$')
PRESETS = json.loads(Path(__file__).with_name('marker_presets.json').read_text(encoding='utf-8'))
DEST = '{924D4B5FA0B24B22}Prefabs/Logic/Mission/MapMarker.et'
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
    keys = re.findall(r'"([^"]+)"', factions)
    phase = phase_fields(values.get('m_aHideOnGameModeStates', ''))
    if any_side not in ('0', '1') or unassigned not in ('0', '1'):
        raise ValueError('invalid marker audience')
    if factions and (not re.fullmatch(r'(?:\s*"[^"]+"\s*)+', factions)
                     or len(set(keys)) != len(keys) or len(keys) > 16):
        raise ValueError('unsupported faction list')
    merged = {**DEFAULTS, **values}
    if 'angles' in values:
        if any(k in values for k in ('angleX', 'angleY', 'angleZ')):
            raise ValueError('mixed angle representations')
        merged.pop('angleY')
    if len(merged['m_sDescription'].encode()) - 2 > 512 or len(name.encode()) > 128:
        raise ValueError('marker exceeds native Lobby snapshot limits')
    if not 0.01 <= float(merged['m_fWorldSize']) <= 100000:
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
    output.extend(['  }', ' }'])
    output.extend(f' {k} {v}' for k, v in merged.items() if k in TRANSFORM)
    return '\n'.join(output + ['}'])


def relocate_images(text, dev):
    pattern = re.compile(r'(?m)^(   m_s(?:ImageSetOrImage|HighlightImageSet) "\{)([\da-fA-F]{16})(\})([^"\n]+)(")$')

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


def convert_layer(text, dev=None, *, draw_order=False):
    """Process complete icon-only layers. Mixed layers keep their original ordering for review."""
    if not re.search(r'^(?:\$grp )?PS_ManualMarker\b', text, re.M):
        return None
    lines = text.splitlines()
    result = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        match = LEGACY.fullmatch(lines[i])
        if not match:
            raise ValueError(f'line {i + 1} mixed or custom placement')
        inherited = None
        if match['guid'].upper() != BASE:
            preset = PRESETS.get(match['guid'].upper())
            if not preset or preset['path'] != match['path']:
                raise ValueError(f'line {i + 1} inherited marker preset {match["guid"]}')
            inherited = preset['fields']
        start = i
        stop = block_end(lines, i)
        try:
            if match['group']:
                i += 1
                while i < stop:
                    head = re.fullmatch(r' (?:(?P<name>[\w.\-]+) )?\{', lines[i])
                    if not head:
                        raise ValueError('custom grouped instance')
                    end = block_end(lines, i)
                    result.append(instance(lines[i + 1:end], (' ' + head['name']) if head['name'] else '', inherited,
                                           draw_order=draw_order))
                    i = end + 1
            else:
                result.append(instance(lines[i + 1:stop], (' ' + match['name']) if match['name'] else '', inherited,
                                       draw_order=draw_order))
        except ValueError as exc:
            raise ValueError(f'line {i + 1 if match["group"] else start + 1} {exc}') from exc
        i = stop + 1
    output = '\n'.join(result) + '\n'
    return (relocate_images(output, dev) if dev else output), len(result)
