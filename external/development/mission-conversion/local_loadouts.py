"""Migrate character prefabs owned by a mission without changing their playability."""
from functools import lru_cache
from pathlib import PurePosixPath
import re

from briefings import QUOTED, block_end, properties
from catalog import REF
from groups import inheritance, legacy_member, reviewed_native_ancestry, stock_character, stock_wrapper_ancestry_matches

CHARACTER = re.compile(r'SCR_ChimeraCharacter : "\{[\da-fA-F]{16}\}[^"\n]+\.et" \{')
PLAYABLE = '  PS_PlayableComponent "{5EFCC1946F475F15}" {'
EDITABLE = '  SCR_EditableCharacterComponent "{520EA1D2F659C85C}" {\n'
EDITABLE_INFO = '   m_UIInfo SCR_EditableEntityUIInfo "{5298E609432D192D}" {\n'
LEGACY = re.compile(r'\b(?:PS|TILW)_\w+')
MOVED_CHARACTER_PARENTS = {
    ('B64CD72F4D93A99E', 'worlds/James/newbritnvatvt/Character_UK_1989_Paras_LRDriver_P.et'):
        'worlds/James/JavoryPrelude/Character_UK_1989_Paras_LRDriver_P.et',
    ('3245F15CEA803F94',
     'Prefabs/Characters/Factions/BLUFOR/US_Army/1986/Infantry (Woodland)/Character_US_1986_BDU_Mechanic_P.et'):
        'worlds/Til/CompanyTeamCounterAttack/Prefabs/Character_US_1986_BDU_Mechanic_P.et',
}
MOVED_AI_PARENTS = {
    ('4C362B06571604F9', 'Prefabs/Characters/Factions/OPFOR/GC_SEPARATIST/Character_GC_SEPARATIST_SL.et'):
        'Prefabs/Characters/Factions/OPFOR/GC_SEPARATIST/2020/Character_SEP_SL.et',
}


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
        editable = [index for index in range(start + 1, end)
                    if lines[index].startswith('  SCR_EditableCharacterComponent ')]
        name = f'    Name {authored_name}\n'
        if not editable:
            lines[end:end] = [EDITABLE, EDITABLE_INFO, name, '   }\n', '  }\n']
        else:
            index = editable[0]
            if len(editable) != 1 or lines[index:index + 4] != [EDITABLE, EDITABLE_INFO, '   }\n', '  }\n']:
                raise ValueError('existing character presentation needs review')
            lines.insert(index + 2, name)
            if index < component:
                component += 1
                stop += 1
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
        parent = REF.search(first)
        resource, parent_path = parent[1].upper(), parent[2]
        parent_asset = evaluate(resource, seen | {guid}) if resource in source.resources else None
        inherited_playable = bool(parent_asset) or legacy_member(source_units, resource, False)
        try:
            body, removed = migrate_playability(text, inherited_playable=inherited_playable)
        except ValueError:
            return None
        if LEGACY.search(body):
            return None
        if resource in source.resources:
            if not parent_asset or (parent_path != parent_asset[0]
                                    and MOVED_CHARACTER_PARENTS.get((resource, parent_path)) != parent_asset[0]):
                return None
            if parent_path != parent_asset[0]:
                header = first.replace(parent[0], f'"{{{resource}}}{parent_asset[0]}"', 1)
                body = header + body[len(first):]
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


def collect_ai(source, dev, source_units, dev_units, stock):
    """Retain native mission-owned AI loadouts whose parent chain is unchanged."""
    approved = {}
    for identity, paths in source.resources.items():
        if len(paths) != 1 or identity in source_units.resources or identity in dev_units.resources:
            continue
        path, = paths
        if (not path.lower().startswith(('worlds/', 'missions/')) or PurePosixPath(path).suffix.lower() != '.et'
                or dev.resources.get(identity) not in (None, [path])):
            continue
        text = source.read(path)
        first, separator, fields = text.partition('\n')
        if not CHARACTER.fullmatch(first) or LEGACY.search(text):
            continue
        parent = REF.search(first)
        resource, authored = parent[1].upper(), parent[2]
        current = dev_units.resources.get(resource, [])
        if len(current) != 1 or resource in source.resources or resource in dev.resources:
            continue
        canonical, = current
        old = source_units.resources.get(resource)
        if old != [canonical]:
            continue
        registered = REF.search(source_units.read(canonical + '.meta'))
        if (authored not in (canonical, registered[2] if registered else None)
                and MOVED_AI_PARENTS.get((resource, authored)) != canonical):
            continue
        if (not unchanged_units_parent(source_units, dev_units, resource)
                or legacy_member(source_units, resource, True) or legacy_member(dev_units, resource, True)):
            continue
        inherited = inheritance(dev_units, resource)
        base = REF.search(inherited[-1].partition('\n')[0])
        if (not base or not stock or not stock.contains(*base.groups())
                or any(base[1].upper() in snapshot.resources
                       for snapshot in (source, dev, source_units, dev_units))):
            continue
        body = first.replace(parent[0], f'"{{{resource}}}{canonical}"', 1) + separator + fields
        if path not in dev.files or body.strip() == dev.read(path).strip():
            approved[identity] = (path, body)
    return approved
