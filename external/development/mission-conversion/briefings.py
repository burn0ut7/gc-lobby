"""Translate placed mission descriptions to GC Lobby mode-prefab overrides."""
from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha256
import re


# The Core overlay for the common description sets only the standard layout.
BASE = '{3136BE42592F3B1B}'
LAYOUT = '{B6DE04F7CF7A95DA}'
MODE = '{A79C4E0A43DEB821}Prefabs/MP/Modes/GC_LobbyGameMode.et'
QUOTED = r'"(?:[^"\\\n]|\\.|"")*"'
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


def page(lines, origin, name=''):
    values = properties(lines, ALLOWED)
    if 'm_sDescriptionLayout' in values and not values['m_sDescriptionLayout'].startswith('"' + LAYOUT):
        raise ValueError('custom description layout')
    for key in ['m_bShowForAnyFaction', 'm_bEmptyFactionVisibility']:
        if values.get(key, '0') not in ('0', '1'):
            raise ValueError('unknown visibility value')
    any_side = values.get('m_bShowForAnyFaction') == '1'
    empty = values.get('m_bEmptyFactionVisibility') == '1'
    factions = values.get('m_aVisibleForFactions', '')
    keys = re.findall(r'"([^"]+)"', factions)
    if factions and (not re.fullmatch(r'(?:\s*"[^"]+"\s*)+', factions) or
                     len(keys) > 16 or len(set(keys)) != len(keys)):
        raise ValueError('unsupported faction key list')
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
    if not re.fullmatch(QUOTED, title) or not all(re.fullmatch(QUOTED, s) for s in body.split('\\\n')):
        raise ValueError('unsupported serialized briefing string')
    if len(title.encode()) - 2 > 128 or len(body.encode()) - 2 > 16384:
        raise ValueError('briefing exceeds GC Lobby string limit')
    return Page(int(values.get('m_iOrder', 0)), title, body, '' if any_side else factions, origin, audience, name)


def read_pages(text, path):
    lines = text.split('\n')
    result, gaps = [], []
    starts = [i for i, line in enumerate(lines) if PLACEMENT.fullmatch(line)]
    for start in starts:
        loc = f'{path}:{start + 1}'
        try:
            match = PLACEMENT.fullmatch(lines[start])
            if BASE not in match['head']:
                raise ValueError('inline or custom description prefab')
            stop = block_end(lines, start)
            if match['group']:
                i = start + 1
                while i < stop:
                    if not re.fullmatch(r'\s+(?:[^{}"]+\s+)?\{\s*', lines[i]):
                        raise ValueError('unexpected grouped instance')
                    end = block_end(lines, i)
                    member = re.fullmatch(r'\s+([\w.-]+)\s+\{\s*', lines[i])
                    result.append(page(lines[i + 1:end], f'{path}:{i + 1}', member[1] if member else ''))
                    i = end + 1
            else:
                name = re.match(r'\s*([\w.-]+)\s*(?=:|$)', match['head'])
                result.append(page(lines[start + 1:stop], loc, name[1] if name else ''))
        except ValueError as exc:
            gaps.append(f'{loc} {exc}')
    # Nested descriptions need parent transforms/lifecycle and possible runtime updates.
    if re.search(r'^\s+(?:\$grp )?PS_MissionDescription\b', text, re.M):
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
                      f'     m_sTitle {p.title}', '     m_sContent ' + p.body.replace('\n', '\n     ')])
        if p.factions:
            lines.extend(['     m_aFactionKeys {', '      ' + p.factions, '     }'])
        if p.audience != BriefingAudience.DEFAULT:
            lines.append(f'     m_eAudience {int(p.audience)}')
        lines.append('    }')
    if pages:
        lines.append('   }')
    return '\n'.join(lines + ['  }', ' }', '}', '']), compact
