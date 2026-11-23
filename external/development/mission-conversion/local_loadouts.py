"""Migrate character prefabs owned by a mission without changing their playability."""
from functools import lru_cache
from hashlib import sha256
from pathlib import PurePosixPath
import re

from briefings import QUOTED, block_end, properties
from catalog import REF, SOURCE_TEXT
from groups import (inheritance, inherits_legacy_playable_base, legacy_member, reviewed_native_ancestry,
                    stock_character, stock_wrapper_ancestry_matches)
from launchers import META_NAME

CHARACTER = re.compile(r'SCR_ChimeraCharacter : "\{[\da-fA-F]{16}\}[^"\n]+\.et" \{')
PLAYABLE = '  PS_PlayableComponent "{5EFCC1946F475F15}" {'
EDITABLE = '  SCR_EditableCharacterComponent "{520EA1D2F659C85C}" {\n'
EDITABLE_INFO = '   m_UIInfo SCR_EditableEntityUIInfo "{5298E609432D192D}" {\n'
GREEN_SNOOD = ('DBFF84DD336E1065',
               'Prefabs/Characters/HeadGear/Masks/Masks_Keela_Snood/Masks_Keela_Snood_GRN.et')
LEGACY = re.compile(r'\b(?:PS|TILW)_\w+')
REVIEWED_MISSION_CHARACTER_BASES = {
    '720A8AEF1DB6795F': ('worlds/arc/CommandosReforged/Prefabs/Commandos_Character_Base.et',
                        '085c5603d72debcad9a21122c90fba889a34cb80'),
}
MOVED_CHARACTER_PARENTS = {
    ('60E1492FD0E1529B', 'Missions/Huska/Prefabs/Character_RHS_USAF_USCM_HeliPilot_P.et'):
        'Missions/Huska/Prefabs/Character_RHS_USAF_USMC_HeliPilot_P.et',
    ('B64CD72F4D93A99E', 'worlds/James/newbritnvatvt/Character_UK_1989_Paras_LRDriver_P.et'):
        'worlds/James/JavoryPrelude/Character_UK_1989_Paras_LRDriver_P.et',
    ('3245F15CEA803F94',
     'Prefabs/Characters/Factions/BLUFOR/US_Army/1986/Infantry (Woodland)/Character_US_1986_BDU_Mechanic_P.et'):
        'worlds/Til/CompanyTeamCounterAttack/Prefabs/Character_US_1986_BDU_Mechanic_P.et',
}
REVIEWED_RHS_PILOT_PARENT = (
    '228d83d6c2834be63c3405774cdd1cd6c03defff',
    '42A502E3BB727CEC',
    'Prefabs/Characters/Factions/BLUFOR/RHS_USAF/RHS_USAF_USMC_MEF/Character_RHS_USAF_USMC_HeliPilot.et',
)
COMMANDOS_VSR_RIFLEMAN = (
    '3CEC77A6686C1BA3',
    'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/VSR/Character_RHS_RF_MSV_VSR_Rifleman.et',
)
COMMANDOS_RHS_ROOTS = {
    'worlds/arc/CommandosReforged/Prefabs/OP/Character_GC_Cdos_Rifleman_P.et':
        '03536ee311fafd1a2bb6cda2b597ec6102fa11fa',
    'worlds/arc/CommandosReforged/Prefabs/OP/Character_GC_Cdos_Pilot_P.et':
        '53b48a21a7c1f756601c794ae9d52e253e546321',
}
COMMANDOS_OFFICER_PARENT = (
    'CC3E84140343D82E',
    'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/VSR/Character_RHS_RF_MSV_VSR_Officer.et',
)
COMMANDOS_UNUSED_CASUALTY_FLAGS = {
    'worlds/arc/CommandosReforged/Prefabs/OP/Character_GC_Cdos_BASECOMMANDER_P.et':
        ('64821cb589dbcedcc8d76674b128b9e12cd01363', '6476D65DBEC04167', 'OBJ_5_KIA'),
    'worlds/arc/CommandosReforged/Prefabs/OP/Character_GC_Cdos_PolOfficer_P.et':
        ('e47725253bad03b4c2a01ef87f5f367182b53edd', '65EA3858B7156DBC', 'OBJ_5_KIA'),
    'worlds/arc/CommandosReforged/Prefabs/OP/Character_GC_Cdos_DOG.et':
        ('9976a68da1f8bb086976fe647bbdbe261c32ec22', '65DF2580DCFE6FAA', 'OBJ_6_KIA'),
    'worlds/arc/CommandosReforged/Prefabs/OP/Character_GC_Cdos_SCIENTIST.et':
        ('71f34d0b85023660c5010d530903cd7025145481', '6476D65CD08FA080', 'OBJ_6_KIA'),
}
OIL_RIG_LOADOUTS = 'worlds/TheLocalPub/OilRigRangos/Prefabs/Characters/Factions/'
OIL_RIG_RIFLEMAN_PARENT = '4AF2023E868540E7'
LOCAL_PROBLEMS_GARRISON_ROLES = {
    'a24fb95e895f54d644372590e2eb8b9430b344e5': '7E590EDD929739E4',
    '635bae4af183ca6d6c9f867207e865be2d787219': '5A1F1BD88FC6C6E9',
    'f369b0a8eb8eb997cf1588ef52c928bee085940b': 'F0F0A8C16C9901B8',
}
REVIEWED_HISTORICAL_PARENTS = {
    # The convoy officer inherited the archived playable company commander.
    'A50E1A5B20D1D731': 'aac51a27d54bbef7063beb56044880adbb759c66',
}
MOVED_AI_PARENTS = {
    ('4C362B06571604F9', 'Prefabs/Characters/Factions/OPFOR/GC_SEPARATIST/Character_GC_SEPARATIST_SL.et'):
        'Prefabs/Characters/Factions/OPFOR/GC_SEPARATIST/2020/Character_SEP_SL.et',
}
REVIEWED_FORCED_CHARACTERS = {
    # Public Enemies' two mobster groups force playability for their inherited roles.
    '8F51ED5F532BB1E1': 'a66dc5b812d4a498af08bd67c1ab7845a2731aaaa2f8b9b43d3e2ae055eef910',
    '4144099566E3DE33': 'b2fea681d00d188ad5b8731772b78350e83760cba0cf3019d066aa828140d35a',
    # The Local Problems company HQ forces playability on its one HVT role.
    'F32A660889248606': '1ac0586d142dab2e131b6d14d2de4269761ddb0d835144008c3711f506418041',
    # Gunfighter inherits force-playability from the USMC platoon HQ.
    '6249D34810999F63': '8b74873ea5a2cbb6a1673fbdcbc3b0f65d1606c3f98c02b4dfe4843e9dff574b',
    # Trifecta explicitly enables the PATRIOT HQ group and its mission-local roles.
    '970BD38C55D884C8': '0520df5340a21e9045ef5e6eedd96c684b9851bac4c6b14e6bdb9e91a5d07c19',
    'C72F5743A033EAA7': 'bcade9706bd67072f699e95887ce9b723da79e8475b14c0e2039437962483a98',
    '4B023F7E16C8D588': '5abea1a4e38eff232f79c3dbedd15ebdd687fc124faf6c51e7afde13742552cf',
    '358B5AD651512010': '8d7000b8012fc5f82394ff6789631d2bb8ff77adca7880985c3b5812cb0a7491',
    'F9F034704985BAB1': 'd068ac70cebca23f61b2e006f47a0b2b768bb5e0869c5f048fb341f2947085c0',
    'CC3CFA6028544D1E': '1b72cdd8d99322a9f8d960854ef2cc0649448d518421d848876ce8b4dfcb10b5',
    # The Local Files explicitly enables both groups and names these roles.
    'DE94F9CA70A9C23B': '77b727fd2450b84a217b6c3ae6afa47585858caa6726ea77ec316edb745c1c3b',
    '53C0A621CB97E80D': 'ceb640e2ff498307d8cb1ea6ca1dc51a5a678c7610f61f93be3b221712a0f356',
    # Hephaestus Forge explicitly makes these six pilot groups playable.
    '0BC9FB2D45681163': '5572741758b8e7afafcfa397690e07ba0647b6969a55ecb360183edb351f2c34',
    # Kunar's player squads use these inventory-only wrappers on unchanged USMC roles.
    '115E7D87A3F770B7': 'f200810784a6eb7d465c8ef9c2a14dcc9b33f5df2dfe81bf09da9f37bd390df6',
    '573DF3DA110269E9': '1bb50ede66c68ffe2a5cdccceb78eb6698917e86c60e3219b64b45209da66de9',
    '5C2A7F4A52C461DA': 'be401f148d578b4e487cbd1585919d6e1a65e2f79238d1acaa490a7cdad4fb96',
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
            if len(editable) != 1 or lines[index] != EDITABLE:
                raise ValueError('existing character presentation needs review')
            editable_end = block_end(lines, index)
            info = [i for i in range(index + 1, editable_end) if lines[i].startswith('   m_UIInfo ')]
            if len(info) > 1 or (info and lines[info[0]] != EDITABLE_INFO):
                raise ValueError('existing character presentation needs review')
            if info:
                info_start, = info
                info_end = block_end(lines, info_start)
                names = [i for i in range(info_start + 1, info_end) if lines[i].startswith('    Name ')]
                if len(names) > 1 or (names and not re.fullmatch(QUOTED, lines[names[0]].removeprefix('    Name ').strip())):
                    raise ValueError('existing character presentation needs review')
                if names:
                    lines[names[0]] = name
                else:
                    lines.insert(info_start + 1, name)
                    if info_start < component:
                        component += 1
                        stop += 1
            else:
                lines[editable_end:editable_end] = [EDITABLE_INFO, name, '   }\n']
                if editable_end < component:
                    component += 3
                    stop += 3
    del lines[component:stop + 1]
    return ''.join(lines), True


@lru_cache(maxsize=4096)
def unchanged_units_parent(source_units, dev_units, resource, *, force_playable=False):
    try:
        before = inheritance(source_units, resource)
        after = inheritance(dev_units, resource)
        native_before = []
        for index, text in enumerate(before):
            parent = REF.search(text.partition('\n')[0])
            inherited = force_playable or bool(parent and legacy_member(source_units, parent[1], False))
            native = migrate_playability(text, inherited_playable=inherited)[0]
            if (parent and (paths := source_units.resources.get(parent[1]))
                    and paths == dev_units.resources.get(parent[1])
                    and len(paths) == 1 and paths[0] != parent[2]):
                current, = paths
                registration = META_NAME.search(dev_units.read(current + '.meta'))
                if (registration and (registration[2], registration[3]) == (parent[1], current)
                        and PurePosixPath(current).name == PurePosixPath(parent[2]).name):
                    updated = native.replace(parent[0], f'"{{{parent[1]}}}{current}"', 1)
                    if index < len(after) and updated.partition('\n')[0] == after[index].partition('\n')[0]:
                        native = updated
            native_before.append(native)
    except ValueError:
        return False
    if not before or not after or any(not CHARACTER.fullmatch(text.partition('\n')[0]) for text in (*before, *after)):
        return False
    return reviewed_native_ancestry(tuple(native_before), after, dev_units)


def commandos_roster_body(source, source_units, path, text):
    spec = COMMANDOS_UNUSED_CASUALTY_FLAGS.get(path)
    if not spec or source.files[path] != spec[0]:
        return None
    root = 'worlds/arc/CommandosReforged/'
    layers = root + 'CommandosReforged_Layers/'
    headquarters = 'Prefabs/Groups/OPFOR/Russia/2020/AFRF_MotorRifles_2010s/P/AFRF_MotRifles_2020_PHQ_P.et'
    if (source.files.get(layers + 'players_ru.layer') != 'c570e980592b6fae8842b3da616af89dc8c1947b'
            or source_units.resources.get('312B31A3D81EF88A') != [headquarters]
            or ' m_bSetPlayable 1' not in source_units.read(headquarters).splitlines()):
        return None
    world = root + 'CommandosReforged.ent'
    flag = spec[2]
    mentions = sum(source.read(item).count(f'"{flag}"') for item in source.closure(world)
                   if PurePosixPath(item).suffix.lower() in SOURCE_TEXT)
    if mentions != 2 or any(flag.encode() in source.read_raw(item)
                            for item in source.files if item.lower().endswith('.c')):
        return None
    # The Commandos handlers own objective completion. These flags have no readers.
    observer = f'  TILW_Flag_EntityDamage "{{{spec[1]}}}" {{\n   m_flagName "{flag}"\n  }}\n'
    return text.replace(observer, '', 1) if text.count(observer) == 1 else None


def collect(source, dev, source_units, dev_units, stock, *, normalize_references=None, unavailable=(),
            historical_parents=None, published_playable_parents=(), rhs=None, external_roles=None, uk=None):
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
        commandos_role = commandos_roster_body(source, source_units, path, text)
        if commandos_role is not None:
            text = commandos_role
        expected = REVIEWED_FORCED_CHARACTERS.get(guid)
        verified_forced = expected and sha256(text.strip().encode()).hexdigest() == expected
        first = text.partition('\n')[0]
        if not CHARACTER.fullmatch(first):
            return None
        parent = REF.search(first)
        resource, parent_path = parent[1].upper(), parent[2]
        parent_asset = evaluate(resource, seen | {guid}) if resource in source.resources else None
        historical = None
        reviewed_historical_parent = source.files[path] == REVIEWED_HISTORICAL_PARENTS.get(guid)
        if resource not in owned and (reviewed_historical_parent or (
                verified_forced and resource not in source_units.resources)):
            historical = (historical_parents or {}).get((resource, parent_path))
        if reviewed_historical_parent and not historical:
            return None
        published_parent = stock_character(resource, parent_path, stock)
        inherits_published_playability = (published_parent and resource not in owned
                                          and resource not in source_units.resources
                                          and resource not in dev_units.resources)
        verified_playable_parent = (resource, parent_path) in published_playable_parents
        inherited_playable = (bool(parent_asset) or verified_playable_parent or legacy_member(source_units, resource, False)
                              or inherits_published_playability)
        try:
            body, removed = migrate_playability(
                text, inherited_playable=inherited_playable or verified_forced or reviewed_historical_parent)
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
            forced_ion_role = verified_forced and guid == '53C0A621CB97E80D'
            if (not stock_wrapper
                    and not unchanged_units_parent(source_units, dev_units, resource, force_playable=forced_ion_role)):
                return None
            unit_parent = REF.search(inheritance(source_units, resource)[0].partition('\n')[0])
            inherits_published_role = bool(published_parent and unit_parent
                                           and tuple(published_parent) == unit_parent.groups())
            if (not removed and not stock_wrapper and not verified_playable_parent
                    and not legacy_member(source_units, resource, False)
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
                registered_rhs_pilot = (rhs and (source.files[path], resource, parent_path) == REVIEWED_RHS_PILOT_PARENT
                                        and rhs.contains(resource, parent_path))
                registered_rhs_garrison = (rhs and LOCAL_PROBLEMS_GARRISON_ROLES.get(source.files[path]) == resource
                                           and rhs.contains(resource, parent_path))
                registered_commandos_role = (
                    rhs
                    and ((source.files[path] == COMMANDOS_RHS_ROOTS.get(path)
                          and (resource, parent_path) == COMMANDOS_VSR_RIFLEMAN)
                         or (commandos_role is not None and (resource, parent_path) == COMMANDOS_OFFICER_PARENT))
                    and rhs.contains(resource, parent_path))
                registered_oil_rig_role = (resource == OIL_RIG_RIFLEMAN_PARENT and path.startswith(OIL_RIG_LOADOUTS)
                                           and (external_roles or {}).get(resource) == (parent_path, parent_path))
                registered_bebble_role = (
                    uk and path.startswith('worlds/TheLocalPub/BebbleIslandRaid/Prefabs/Characters/Factions/BLUFOR/')
                    and parent_path.startswith('Prefabs/Characters/Factions/BLUFOR/UK_Army/1983/SpecialForces/')
                    and uk.contains(resource, parent_path))
                mission_base = REVIEWED_MISSION_CHARACTER_BASES.get(guid) == (path, source.files[path])
                if not (removed or verified_forced or mission_base or commandos_role is not None) or not (
                        registered_rhs_pilot or registered_rhs_garrison or registered_commandos_role
                        or registered_oil_rig_role or registered_bebble_role
                        or stock.contains(resource, parent_path)):
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
