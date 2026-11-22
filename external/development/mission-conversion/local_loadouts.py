"""Migrate character prefabs owned by a mission without changing their playability."""
from functools import lru_cache
from hashlib import sha256
from pathlib import PurePosixPath
import re

from briefings import QUOTED, block_end, properties
from catalog import REF
from groups import (inheritance, inherits_legacy_playable_base, legacy_member, reviewed_native_ancestry,
                    stock_character, stock_wrapper_ancestry_matches)

CHARACTER = re.compile(r'SCR_ChimeraCharacter : "\{[\da-fA-F]{16}\}[^"\n]+\.et" \{')
PLAYABLE = '  PS_PlayableComponent "{5EFCC1946F475F15}" {'
EDITABLE = '  SCR_EditableCharacterComponent "{520EA1D2F659C85C}" {\n'
EDITABLE_INFO = '   m_UIInfo SCR_EditableEntityUIInfo "{5298E609432D192D}" {\n'
GREEN_SNOOD = ('DBFF84DD336E1065',
               'Prefabs/Characters/HeadGear/Masks/Masks_Keela_Snood/Masks_Keela_Snood_GRN.et')
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
REVIEWED_FORCED_CHARACTERS = {
    'A2B3DB1BABEF1F4E': '6cafd343421e6c5329b204b1d796d44a8b7aad751eadf1c97ee40ce7f9d883e0',
    'ACC80CE64A29F89D': '56089d71da9a346773bd28fc2c1c377afc080e88751a7d3937560a1a20ed7cc3',
    'EBB5B37321AA1E3D': '3ee530493b6429a192628cd8ce842d59ba24c0fb091d349b2863096f1ba7e09e',
    '0D39FB782A915648': 'e975d4a56eb21b6a6fc09efb5f6b7e23e1f404679fa0c57552f66e756225b5cd',
    '6AA3A930760964BD': 'c5f18445f33dca9310e720073b48b4a04f0ff9a52abb363a2cc1451c9466e4ba',
    '899712E13A601488': '4c4f34ec51a307bfee86f7153626906af9a3cfe21064e540408a82bb2f9fa1ff',
    'DADA3692BC2D3EE1': 'e2f4d1d7c1b1ec926064f5832a5d9fde9e7899438fb6df189137655a1f821892',
    '3C6B7D7C2498DCE2': '51f6edaa998b7bca611a2a9542c5d467359de7ecda6e6d4517afda3f40ac2e8f',
    '83B790D1347823FF': '3353228d0031557ccbd8691da331a5242d4949606a0db5852cb1795bd8892203',
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
            if len(editable) != 1 or lines[index:index + 2] != [EDITABLE, EDITABLE_INFO]:
                raise ValueError('existing character presentation needs review')
            info_end = block_end(lines, index + 1)
            names = [i for i in range(index + 2, info_end) if lines[i].startswith('    Name ')]
            if (len(names) > 1 or (names and not re.fullmatch(QUOTED, lines[names[0]].removeprefix('    Name ').strip()))
                    or any(line.startswith('   m_UIInfo ') for line in lines[info_end + 1:block_end(lines, index)])):
                raise ValueError('existing character presentation needs review')
            if names:
                lines[names[0]] = name
            else:
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
        native_before = []
        for text in before:
            parent = REF.search(text.partition('\n')[0])
            inherited = bool(parent and legacy_member(source_units, parent[1], False))
            native_before.append(migrate_playability(text, inherited_playable=inherited)[0])
    except ValueError:
        return False
    if not before or not after or any(not CHARACTER.fullmatch(text.partition('\n')[0]) for text in (*before, *after)):
        return False
    return reviewed_native_ancestry(tuple(native_before), after, dev_units)


def collect(source, dev, source_units, dev_units, stock, *, normalize_references=None, unavailable=(),
            historical_parents=None):
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
        expected = REVIEWED_FORCED_CHARACTERS.get(guid)
        verified_forced = expected and sha256(text.strip().encode()).hexdigest() == expected
        first = text.partition('\n')[0]
        if not CHARACTER.fullmatch(first):
            return None
        parent = REF.search(first)
        resource, parent_path = parent[1].upper(), parent[2]
        parent_asset = evaluate(resource, seen | {guid}) if resource in source.resources else None
        historical = None
        if verified_forced and resource not in owned and resource not in source_units.resources:
            historical = (historical_parents or {}).get((resource, parent_path))
        published_parent = stock_character(resource, parent_path, stock)
        inherits_published_playability = (published_parent and resource not in owned
                                          and resource not in source_units.resources
                                          and resource not in dev_units.resources)
        inherited_playable = (bool(parent_asset) or legacy_member(source_units, resource, False)
                              or inherits_published_playability)
        try:
            body, removed = migrate_playability(text, inherited_playable=inherited_playable or verified_forced)
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
        elif resource in dev.resources or not parent_path.removeprefix('GitHub/gc-reforger-units/').lower().startswith(
                'prefabs/characters/'):
            return None
        elif historical and historical[0] not in owned and dev_units.resources.get(historical[0]) == [historical[1]]:
            body = first.replace(parent[0], f'"{{{historical[0]}}}{historical[1]}"', 1) + body[len(first):]
        elif resource in source_units.resources or resource in dev_units.resources:
            stock_wrapper = stock_wrapper_ancestry_matches(source_units, dev_units, resource, stock, owned)
            if not stock_wrapper and not unchanged_units_parent(source_units, dev_units, resource):
                return None
            unit_parent = REF.search(inheritance(source_units, resource)[0].partition('\n')[0])
            inherits_published_role = bool(published_parent and unit_parent
                                           and tuple(published_parent) == unit_parent.groups())
            if (not removed and not stock_wrapper and not legacy_member(source_units, resource, False)
                    and not inherits_published_role
                    and not (verified_forced and inherits_legacy_playable_base(source_units, resource,
                                                                              stock, dev_units))):
                return None
            canonical, = dev_units.resources[resource]
            if not canonical.lower().startswith('prefabs/characters/'):
                return None
            if (parent_path.startswith('GitHub/gc-reforger-units/')
                    and PurePosixPath(parent_path).name != PurePosixPath(canonical).name):
                return None
            header = first.replace(parent[0], f'"{{{resource}}}{canonical}"', 1)
            body = header + body[len(first):]
        else:
            if parent_path.startswith('GitHub/gc-reforger-units/'):
                return None
            if published_parent:
                native_id, native_path = published_parent
                if native_id in source.resources or native_id in dev.resources:
                    return None
                if native_id in source_units.resources or native_id in dev_units.resources:
                    # Removing a playability-only wrapper keeps the same Units override.
                    registration = f'"{{{native_id}}}{native_path}"'
                    if not unchanged_units_parent(source_units, dev_units, native_id):
                        return None
                    for units in (source_units, dev_units):
                        path_in_units, = units.resources[native_id]
                        if not re.search(r'^ Name ' + re.escape(registration) + r'$', units.read(path_in_units + '.meta'), re.M):
                            return None
                body = first.replace(parent[0], f'"{{{native_id}}}{native_path}"', 1) + body[len(first):]
            else:
                if not (removed or verified_forced) or not stock.contains(resource, parent_path):
                    return None
        if (guid == '730C15295DBF4543' and source.files[path] == '90e7c82588520852e48f88e85f35bcbeb29b27ef'
                and GREEN_SNOOD in unavailable
                and sha256('\n'.join(inheritance(source_units, resource)).encode()).hexdigest()
                == 'e469241950b28218b8c61040c46c4bc802fb67998daabe497a0d3a2e8024a058'):
            # The published role cannot load the snood and its reviewed parent has no face item.
            item, item_path = GREEN_SNOOD
            body = body.replace(f'     Prefab "{{{item}}}{item_path}"\n', '     Prefab ""\n', 1)
        compared = normalize_references(body) if normalize_references and path in dev.files else body
        if (dev.resources.get(guid) not in (None, [path])
                or (path in dev.files and compared.strip() != dev.read(path).strip())):
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
