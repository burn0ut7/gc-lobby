"""Port standalone legacy icon layers whose visual and audience fields are understood."""
import re
from briefings import block_end, properties
from launchers import META_NAME

BASE = 'CD85ADE9E0F54679'
LEGACY = re.compile(r'^(?P<group>\$grp )?PS_ManualMarker(?: (?P<name>[\w.\-]+))? : "\{(?P<guid>[A-Fa-f0-9]{16})\}[^"]+" \{$')
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


def instance(lines, name, inherited=None):
    placed = properties(lines, ALLOWED)
    values = {**(inherited or {}), **placed}
    if values.get('m_bringToFront', '0') != '0':
        raise ValueError('explicit marker layering priority')
    any_side = values.get('m_bShowForAnyFaction', '0')
    unassigned = values.get('m_bVisibleForEmptyFaction', '0')
    factions = values.get('m_aVisibleForFactions', '')
    keys = re.findall(r'"([^"]+)"', factions)
    hidden = set(values.get('m_aHideOnGameModeStates', '').split())
    before_briefing = {'PREGAME', 'SLOTSELECTION'}
    from_briefing = {'BRIEFING', 'GAME', 'POSTGAME', 'CUTSCENE', 'DEBRIEFING'}
    if hidden and hidden not in (before_briefing, from_briefing):
        raise ValueError('marker phase set needs review')
    if any_side not in ('0', '1') or unassigned not in ('0', '1') or (any_side == '0' and (unassigned == '1' or not keys)):
        raise ValueError('separate unassigned audience')
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
    if any_side == '0':
        output.extend(['   m_aFactionKeys {', '    ' + factions, '   }'])
    elif unassigned == '0':
        output.append('   m_bHideWithoutFaction 1')
    if hidden:
        output.append('   m_eShowAtGameModeState BRIEFING')
        if hidden == from_briefing:
            output.append('   m_bInvertGameModeState 1')
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


def convert_layer(text, dev=None):
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
        if match['guid'].upper() != BASE:
            raise ValueError(f'line {i + 1} inherited marker preset {match["guid"]}')
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
                    result.append(instance(lines[i + 1:end], (' ' + head['name']) if head['name'] else ''))
                    i = end + 1
            else:
                result.append(instance(lines[i + 1:stop], (' ' + match['name']) if match['name'] else ''))
        except ValueError as exc:
            raise ValueError(f'line {i + 1 if match["group"] else start + 1} {exc}') from exc
        i = stop + 1
    output = '\n'.join(result) + '\n'
    return (relocate_images(output, dev) if dev else output), len(result)
