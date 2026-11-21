"""Migrate explicitly or ancestrally playable character prefabs owned by a mission."""
from functools import lru_cache
from pathlib import PurePosixPath
import re

from briefings import QUOTED, block_end, properties
from catalog import REF
from groups import inheritance, legacy_member, reviewed_native_ancestry, stock_character, stock_wrapper_ancestry_matches

CHARACTER = re.compile(r'SCR_ChimeraCharacter : "\{[\da-fA-F]{16}\}[^"\n]+\.et" \{')
PLAYABLE = '  PS_PlayableComponent "{5EFCC1946F475F15}" {'
LEGACY = re.compile(r'\b(?:PS|TILW)_\w+')
def migrate_playability(text, *, inherited_playable=False):
    """Remove the reviewed legacy slot marker and retain its authored role label."""
    lines = text.splitlines(keepends=True)
    if PLAYABLE + '\n' not in lines:
        return text, False
    if lines.count(PLAYABLE + '\n') != 1 or lines.count(' components {\n') != 1:
        raise ValueError('ambiguous legacy playable component')
    component = lines.index(PLAYABLE + '\n')
    start = lines.index(' components {\n')
    end = block_end(lines, start)
    if not start < component < end:
        raise ValueError('legacy playability is outside the character components')
    stop = block_end(lines, component)
    data = properties([line.rstrip('\n') for line in lines[component + 1:stop]],
                      {'m_bIsPlayable', 'm_sName', 'm_name'})
    if (data.get('m_bIsPlayable', '1' if inherited_playable else '0') != '1'
            or any(not re.fullmatch(QUOTED, data[field]) for field in ('m_sName', 'm_name') if field in data)):
        raise ValueError('unsupported legacy playability values')
    authored_name = data.get('m_sName', '""')
    # The published legacy component reads m_sName. It has no m_name field.
    if authored_name != '""':
        if any(line.startswith('  SCR_EditableCharacterComponent ') for line in lines[start + 1:end]):
            raise ValueError('existing character presentation needs review')
        lines[end:end] = ['  SCR_EditableCharacterComponent "{520EA1D2F659C85C}" {\n',
                          '   m_UIInfo SCR_EditableEntityUIInfo "{5298E609432D192D}" {\n',
                          f'    Name {authored_name}\n', '   }\n', '  }\n']
    del lines[component:stop + 1]
    return ''.join(lines), True


@lru_cache(maxsize=4096)
def unchanged_units_parent(source_units, dev_units, resource):
    try:
        before = inheritance(source_units, resource)
        after = inheritance(dev_units, resource)
        native_before = tuple(migrate_playability(text)[0] for text in before)
    except ValueError:
        return False
    if not before or not after or any(not CHARACTER.fullmatch(text.partition('\n')[0]) for text in (*before, *after)):
        return False
    return reviewed_native_ancestry(native_before, after, dev_units)


def collect(source, dev, source_units, dev_units, stock):
    """Return approved asset bodies by unique source GUID."""
    approved = {}
    owned = source.resources.keys() | dev.resources.keys()

    def evaluate(guid, seen):
        if guid in approved:
            return approved[guid]
        paths = source.resources.get(guid, ())
        if len(paths) != 1 or guid in seen or guid in source_units.resources or guid in dev_units.resources:
            return None
        path = paths[0]
        if not path.lower().startswith(('worlds/', 'missions/')) or PurePosixPath(path).suffix.lower() != '.et':
            return None
        text = source.read(path)
        first = text.partition('\n')[0]
        if not CHARACTER.fullmatch(first):
            return None
        try:
            body, removed = migrate_playability(text)
        except ValueError:
            return None
        if LEGACY.search(body):
            return None
        parent = REF.search(first)
        resource, parent_path = parent[1].upper(), parent[2]
        if resource in source.resources:
            parent_asset = evaluate(resource, seen | {guid})
            if not parent_asset or parent_path != parent_asset[0]:
                return None
        elif resource in dev.resources or not parent_path.lower().startswith('prefabs/characters/'):
            return None
        elif resource in source_units.resources or resource in dev_units.resources:
            stock_wrapper = stock_wrapper_ancestry_matches(source_units, dev_units, resource, stock, owned)
            if not stock_wrapper and not unchanged_units_parent(source_units, dev_units, resource):
                return None
            if not removed and not stock_wrapper and not legacy_member(source_units, resource, False):
                return None
            canonical, = dev_units.resources[resource]
            if not canonical.lower().startswith('prefabs/characters/'):
                return None
            header = first.replace(parent[0], f'"{{{resource}}}{canonical}"', 1)
            body = header + body[len(first):]
        else:
            native = stock_character(resource, parent_path, stock)
            if native:
                if (native[0] in source.resources or native[0] in dev.resources
                        or native[0] in source_units.resources or native[0] in dev_units.resources):
                    return None
                body = first.replace(parent[0], f'"{{{native[0]}}}{native[1]}"', 1) + body[len(first):]
            elif not removed or not stock.contains(resource, parent_path):
                return None
        if (dev.resources.get(guid) not in (None, [path])
                or (path in dev.files and body.strip() != dev.read(path).strip())):
            return None
        approved[guid] = (path, body)
        return approved[guid]

    for guid in source.resources:
        evaluate(guid, frozenset())
    return approved
