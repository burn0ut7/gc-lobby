"""Translate placed mission descriptions to GC Lobby mode-prefab overrides."""
from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha256
import re


# The Core overlay for the common description sets only the standard layout.
BASE = '{3136BE42592F3B1B}'
LAYOUT = '{B6DE04F7CF7A95DA}'
STANDARD_LAYOUTS = {
    LAYOUT + 'UI/MissionDescription/DescriptionEditableScrollable.layout',
    '{41DAF7B7061DC0BC}UI/MissionDescription/DescriptionEditable.layout',
}
MODE = '{A79C4E0A43DEB821}Prefabs/MP/Modes/GC_LobbyGameMode.et'
QUOTED = r'"(?:[^"\\\n]|\\.|"")*"'
DESCRIPTOR = re.compile(r'components \{\n'
                        r'PS_EditableMissionDescriptionComponent "\{5EE541289B95FCEA\}" \{\n'
                        r'm_UIInfo SCR_EditableDescriptorUIInfo "\{5EE5412857A8E166\}" \{\n'
                        rf'Name {QUOTED}\n\}}\n\}}\n\}}')
PLACEMENT = re.compile(r'^(?P<group>\$grp )?PS_MissionDescription\b(?P<head>[^\n]*)\{[ \t]*$', re.M)
ALLOWED = set('coords angles angleX angleY angleZ scale ID m_sTitle m_sTextData m_iOrder m_aVisibleForFactions m_bEmptyFactionVisibility m_bShowForAnyFaction m_sDescriptionLayout'.split())


class BriefingAudience(IntEnum):
    # GC_EBriefingAudience values stored in authored prefabs.
    DEFAULT = 0
    LISTED_AND_UNASSIGNED = 1
    ASSIGNED = 2
    HIDDEN = 3


def guid(key):
    return sha256(('gc-missions-dev/briefings/v1/' + key).encode()).hexdigest()[:16].upper()


@dataclass
class Page:
    order: int
    title: str
    body: str
    factions: str
    source: str
    audience: BriefingAudience = BriefingAudience.DEFAULT
    name: str = ''


def block_end(lines, start):
    # Enfusion writes structural closing braces at the opening line's indent.
    # A brace in a serialized string or its continuation is inside quotes.
    line = lines[start]
    closing = line[:len(line) - len(line.lstrip())] + '}'
    for i in range(start + 1, len(lines)):
        if lines[i].rstrip() == closing:
            return i
    raise ValueError('unterminated block')


def properties(lines, allowed):
    values = {}
    i = 0
    while i < len(lines):
        field, _, value = lines[i].strip().partition(' ')
        if field not in allowed or field in values:
            raise ValueError('custom or duplicate field ' + field)
        if value == '{':
            stop = block_end(lines, i)
            value = ' '.join(line.strip() for line in lines[i + 1:stop])
            i = stop
        elif value.endswith('\\'):
            while value.endswith('\\'):
                i += 1
                if i >= len(lines):
                    raise ValueError('unterminated string')
                value += '\n' + lines[i].strip()
        values[field] = value
        i += 1
    return values


def target_names(value, *, skip_blank=False):
    names = value.split()
    if not 1 <= len(names) <= 128 or not all(
            re.fullmatch(r'"[\w.-]+"', name) or (skip_blank and name == '""') for name in names):
        raise ValueError('unsupported entity name list')
    return [name for name in names if name != '""']


def page(lines, origin, name='', layouts=STANDARD_LAYOUTS):
    content = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == 'components {':
            stop = block_end(lines, i)
            descriptor = '\n'.join(line.strip() for line in lines[i:stop + 1])
            if DESCRIPTOR.fullmatch(descriptor):
                i = stop + 1
                continue
        if re.fullmatch(r' +editor EntityEditorProps "\{[\da-fA-F]{16}\}" \{', lines[i]):
            stop = block_end(lines, i)
            if stop != i + 1:
                raise ValueError('custom briefing editor properties')
            i = stop + 1
            continue
        content.append(lines[i])
        i += 1
    values = properties(content, ALLOWED)
    if 'm_sDescriptionLayout' in values:
        layout = values['m_sDescriptionLayout']
        if not re.fullmatch(QUOTED, layout) or layout[1:-1] not in layouts:
            raise ValueError('custom description layout')
    for key in ['m_bShowForAnyFaction', 'm_bEmptyFactionVisibility']:
        if values.get(key, '0') not in ('0', '1'):
            raise ValueError('unknown visibility value')
    any_side = values.get('m_bShowForAnyFaction') == '1'
    empty = values.get('m_bEmptyFactionVisibility') == '1'
    factions = values.get('m_aVisibleForFactions', '')
    keys = [key for key in re.findall(r'"([^"]*)"', factions) if key]
    if factions and (not re.fullmatch(r'(?:\s*"[^"]*"\s*)+', factions) or
                     len(keys) > 16 or len(set(keys)) != len(keys)):
        raise ValueError('unsupported faction key list')
    if '""' in factions:
        factions = ' '.join(f'"{key}"' for key in keys)
    audience = BriefingAudience.DEFAULT
    if any_side:
        if not empty:
            audience = BriefingAudience.ASSIGNED
    elif empty:
        audience = BriefingAudience.LISTED_AND_UNASSIGNED
    elif not keys:
        audience = BriefingAudience.HIDDEN
    if not {'m_sTitle', 'm_sTextData'} <= values.keys():
        raise ValueError('inherited title or content')
    title = values['m_sTitle']
    body = values['m_sTextData']
    if not all(re.fullmatch(QUOTED, part) for value in (title, body) for part in value.split('\\\n')):
        raise ValueError('unsupported serialized briefing string')
    if len(title.encode()) - 2 > 128 or len(body.encode()) - 2 > 16384:
        raise ValueError('briefing exceeds GC Lobby string limit')
    return Page(int(values.get('m_iOrder', 0)), title, body, '' if any_side else factions, origin, audience, name)


def description_placement(lines, path, start, result, covered, depth=0, *, layouts=STANDARD_LAYOUTS):
    indent = len(lines[start]) - len(lines[start].lstrip(' '))
    match = PLACEMENT.fullmatch(lines[start][indent:])
    if not match or BASE not in match['head']:
        raise ValueError('inline or custom description prefab')
    if depth > 16:
        raise ValueError('description hierarchy exceeds 16 levels')
    stop = block_end(lines, start)
    covered.add(start)
    if not match['group']:
        name = re.match(r'\s*(?:"([\w.-]+)"|([\w.-]+))\s*(?=:|$)', match['head'])
        description_instance(lines, path, start, stop, (name[1] or name[2]) if name else '', result, covered, depth, layouts)
        return stop
    i = start + 1
    while i < stop:
        member = re.fullmatch(r' ' * (indent + 1) + r'(?:(?:"([\w.-]+)"|([^{}"]+?))\s+)?\{\s*', lines[i])
        if not member:
            raise ValueError('unexpected grouped instance')
        end = block_end(lines, i)
        label = member[1] or member[2] or ''
        name = label if re.fullmatch(r'[\w.-]+', label) else ''
        description_instance(lines, path, i, end, name, result, covered, depth, layouts)
        i = end + 1
    return stop


def description_instance(lines, path, start, stop, name, result, covered, depth, layouts):
    indent = len(lines[start]) - len(lines[start].lstrip(' ')) + 1
    children = next((i for i in range(start + 1, stop) if lines[i] == ' ' * indent + '{'), None)
    result.append(page(lines[start + 1:children if children is not None else stop], f'{path}:{start + 1}', name, layouts))
    if children is None:
        return
    if block_end(lines, children) != stop - 1 or children == stop - 2:
        raise ValueError('unsupported description child list')
    i = children + 1
    while i < stop - 1:
        if (lines[i][:indent + 1] != ' ' * (indent + 1)
                or not PLACEMENT.fullmatch(lines[i][indent + 1:])):
            raise ValueError('non-description child under briefing at line ' + str(i + 1))
        end = description_placement(lines, path, i, result, covered, depth + 1, layouts=layouts)
        i = end + 1


def read_pages(text, path, layouts=STANDARD_LAYOUTS):
    lines = text.split('\n')
    result, gaps, covered = [], [], set()
    for start, line in enumerate(lines):
        if not PLACEMENT.fullmatch(line):
            continue
        try:
            description_placement(lines, path, start, result, covered, layouts=layouts)
        except ValueError as exc:
            gaps.append(f'{path}:{start + 1} {exc}')
    if any(i not in covered and line != line.lstrip() and PLACEMENT.fullmatch(line.lstrip())
           for i, line in enumerate(lines)):
        gaps.append(path + ' nested description')
    return result, gaps


def indexed_pages(pages):
    pages = sorted(pages, key=lambda p: p.order)
    if len(pages) > 32:
        raise ValueError('more than 32 briefing entries')
    orders = [p.order for p in pages]
    compact = bool(orders) and (len(set(orders)) != len(orders) or min(orders) < 0 or max(orders) > 32)
    return [(index if compact else p.order, p) for index, p in enumerate(pages)], compact


def render(world, pages, event=()):
    pages, compact = indexed_pages(pages)
    lines = [f'GC_LobbyGameMode : "{MODE}" {{', f' ID "{guid(world + "/entity")}"',
             ' components {', '  GC_LobbyManager "{6A137A39106A4973}" {', *event]
    if pages:
        lines.append('   m_aBriefings {')
    for order, p in pages:
        lines.extend([f'    GC_Briefing "{{{guid(p.source)}}}" {{', f'     m_iOrder {order}',
                      '     m_sTitle ' + p.title.replace('\n', '\n     '),
                      '     m_sContent ' + p.body.replace('\n', '\n     ')])
        if p.factions:
            lines.extend(['     m_aFactionKeys {', '      ' + p.factions, '     }'])
        if p.audience != BriefingAudience.DEFAULT:
            lines.append(f'     m_eAudience {int(p.audience)}')
        lines.append('    }')
    if pages:
        lines.append('   }')
    return '\n'.join(lines + ['  }', ' }', '}', '']), compact
