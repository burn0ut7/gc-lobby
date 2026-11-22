"""Convert complete layers of playable groups backed by both revisions of GC Units."""
from functools import lru_cache, partial
from hashlib import sha256
import json
from pathlib import Path
import re

from briefings import QUOTED, block_end
from catalog import REF
from scenery import NUMBER, StockPrefabs, placements

ROOT = re.compile(r'^(?P<group>\$grp )?SCR_AIGroup(?: [\w.\-]+)? : "\{(?P<guid>[A-Fa-f0-9]{16})\}[^"]+\.et" \{$')
CALL = re.compile(r'^\s*PS_GroupCallsignAssigner "\{([\da-f]{16})\}" \{$', re.I)
STANDARD_CALLSIGN = '63616C6C7369676E'
INDEX = {'m_iCompanyCallsign': 'm_iGC_CallsignCompany', 'm_iPlatoonCallsign': 'm_iGC_CallsignPlatoon',
         'm_iSquadCallsign': 'm_iGC_CallsignSquad'}
SLOTS = re.compile(r'^ m_aUnitPrefabSlots \{\n(.*?)^ \}', re.M | re.S)
OBSOLETE_RESPAWN_POSITION = re.compile(rf'm_vRespawnPosition {NUMBER} {NUMBER} {NUMBER}')
DYNAMIC_LOD_DISTANCE = 'DynamicsimLastlodDistance'
NATIVE_GROUP_FIELD = re.compile(r'm_faction "[\w.-]+"|m_iGroupRadioFrequency \d+|'
                                r'm_b(?:DeleteWhenEmpty|DeleteIfNoPlayer) [01]|m_iMaxMembers \d{1,4}|'
                                rf'(?:DangerEventRange|{DYNAMIC_LOD_DISTANCE}) \d+(?:\.\d+)?')
STATIC_LINK_FIELDS = {'m_aStaticWaypoints {', 'm_aStaticVehicles {'}
COLOR_TEAM_ARRAY = 'm_aUnitPrefabColorTeams {'
COLOR_TEAM_VALUES = {'"None"', '"Red"', '"Blue"', '"Yellow"', '"Green"'}
FORMATION = re.compile(r'AIFormationComponent "\{[\da-fA-F]{16}\}" \{')
MOVEMENT = 'SCR_AIGroupMovementComponent "{6126A0E02097C7FF}" {'
MOVEMENT_FIELD = re.compile(r'Enabled [01]|"(?:Min|Max) Speed" \d+(?:\.\d+)?|DefaultFormation "[A-Z_]+"')
EDITOR_PREVIEW = re.compile(r'SCR_PreviewEntityComponent "\{[\da-fA-F]{16}\}" \{')
PREVIEW_ENTRY = re.compile(r'SCR_BasePreviewEntry "\{[\da-fA-F]{16}\}" \{')
AI_CONFIG = 'SCR_AIConfigComponent "{512CC1759AE69D6D}" {'
AI_CONFIG_FIELD = re.compile(r'(?:Enabled|m_Enable(?:Movement|DangerEvents|Perception|Attack|TakeCover|Looking|'
                            r'Communication)) [01]|m_Skill (?:0(?:\.\d+)?|1(?:\.0+)?)')
GROUP_DEFEND_TREE = 'BehaviorTree "{87F8618AA19A90F5}AI/BehaviorTrees/Chimera/Group/ActivityDefend.bt"'
STOCK_WEAPON_TREES = {
    'm_sDefaultWeaponBehaviorTree "{2603840B02776A59}AI/BehaviorTrees/Chimera/Soldier/Fire_Suppressive.bt"',
    'm_sDefaultWeaponBehaviorTree "{C7C91D4EEF274827}AI/BehaviorTrees/Chimera/Soldier/Defend.bt"',
}
WEAPON_HANDLING_CONFIG = (
    'm_DefaultWeaponTypeHandlingConfig SCR_AIWeaponTypeHandlingConfig "{69377312FF06216C}" {'
)
EDITABLE_GROUP = 'SCR_EditableGroupComponent "{50D291F6C83FA532}" {'
EDITABLE_UI = 'm_UIInfo SCR_EditableGroupUIInfo "{5298E609432D192D}" {'
EMPTY_VARIANT = re.compile(r'm_VariantData SCR_EditableEntityVariantData "\{[\da-fA-F]{16}\}" \{')
EDITABLE_UI_FIELD = re.compile(r'(?:Name|Description) "[^"\n]*"|(?:m_sFaction|IconSetName) "[\w.-]+"|m_Image ' + REF.pattern)
GROUP_IDENTITY = 'SCR_GroupIdentityComponent "{56366C5C50AD3CF2}" {'
MILITARY_SYMBOL = re.compile(r'm_MilitarySymbol SCR_MilitarySymbol "\{[\dA-Fa-f]{16}\}" \{')
MILITARY_IDENTITY = re.compile(r'm_Identity (BLUFOR|OPFOR|INDFOR|CIVILIAN)')
WRAPPERS = json.loads(Path(__file__).with_name('stock_wrappers.json').read_text(encoding='utf-8'))
STOCK_AI_GROUPS = json.loads(Path(__file__).with_name('stock_ai_groups.json').read_text(encoding='utf-8'))
RHS_AI_GROUPS = json.loads(Path(__file__).with_name('rhs_ai_groups.json').read_text(encoding='utf-8'))
STOCK_FORCED_ROSTERS = {
    '1F468430E5AB477E': ('E5E4A45D13175E67', '33155540F5CAA602', 'EC8C02BC296EEEA0',
                         'B7E8542C3CD9E4B2', '218217624DA44CCD', 'C81429173BB025F2'),
    'AC473DE5F4B24E82': ('33155540F5CAA602', '33155540F5CAA602'),
    '666A2B0A6B1967AE': ('6C1FFF78CD4E4ADA', '4D20F57C29B6EC1D', '5EFF3963153BE048',
                         'A16C1DE30B6957D9', '029FF8F84148012E', '1523C468AAF90312'),
    'B721D5A8C1B556CE': ('4D20F57C29B6EC1D', '4D20F57C29B6EC1D'),
}
STOCK_FORCED_CALLSIGNS = {
    '68B49475034AE9C4', '68B49475050A0FED', '68B4947507BD96A1', '68B494750A66A9AD',
    '68B494753290ECAA', '68B494751EAA3C6F', '68B494751D89F7E5', '68B49475189DF08B',
}
# These overrides were resolved by spawning the source component layouts
# and looking up the assigner the same way the legacy playable manager does.
VERIFIED_PLACED_CALLSIGNS = {
    '651367042C5F047E', '657D06A2A8998A83', '68D0CC3F0387DDD0',
    '68D0CC3F2FF116CA', '68D0CC3F63B3DF75',
}
BASE_CHARACTER = ('37578B1666981FCE', 'Prefabs/Characters/Core/Character_Base.et')
UK_CHARACTER_BASE = ('542CBB6908CA71D8', 'Prefabs/Characters/Factions/BLUFOR/UK_Army/Character_UK_Base.et')
UK_PROVIDER = 'BritishForces_5AE50EC5B8D6F4AE'
UK_REGULAR_DIRECTORY = 'Prefabs/Characters/Factions/BLUFOR/UK_Army/1989/Regulars/'
UK_1983_AI_ROLES = {
    'FFB42452E9AD5FFC': 'SectionCommander', 'A6D0DBFAC6349E22': 'Rifleman',
    'F5B9FF92DBFB520C': 'Rifleman_LAT', '2C69231254D1AEDB': 'Section2IC',
    'D005E20AE7CB8431': 'GPMG_No1', '327FBCA6B02BC5A8': 'AMG', 'B3BBFBAC7CBF582E': 'L4LMG_No1',
}
UK_FORCED_ROLES = {
    '59DC74EF0ECF7E49': ('Medic', 'Medic'),
    'D0F124C1610D3232': ('LSW', 'LSW'),
    'B10659FFD99279DC': ('Rifleman_LAT', 'Rifleman_LAT_LAW80'),
    '4AF2023E868540E7': ('Rifleman', 'Rifleman'),
    'F9E2E97FA21AA133': ('Section2IC', 'Section2IC'),
}
RHS_SOBR_BASE = ('B9CA56F3E1FC8641',
                 'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/VV/RG/Blk/Character_RHS_RF_SOBR_BaseLoadout.et')
RHS_SOBR_FORCED_ROLES = {
    '7284A0C1AC626E1F': 'SL',
    'E4E52E85601454F5': 'Chad1',
    'EABACB837362304D': 'Breacher',
    '88B11A65DCD0DBDB': 'Chad2',
    'FABC8CBA1DFF203D': 'Breacher2',
    'D5640BB562716235': 'Marksman',
}


def verified_uk_forced_roles(workshop, owned):
    if not workshop:
        return {}
    registry = workshop / UK_PROVIDER / 'resourceDatabase.rdb'
    if not registry.is_file():
        return {}
    database = StockPrefabs(registry)
    if not database.contains(*UK_CHARACTER_BASE):
        return {}
    prefix = UK_REGULAR_DIRECTORY + 'Character_UK_1989_Regulars_'
    result = {}
    for guid, (old, current) in UK_FORCED_ROLES.items():
        path = prefix + current + '.et'
        if guid not in owned and database.contains(guid, path):
            result[guid] = (prefix + old + '.et', path)
    return result


def uk_ai_characters(workshop):
    registry = workshop / UK_PROVIDER / 'resourceDatabase.rdb'
    if not registry.is_file():
        return {}
    database = StockPrefabs(registry)
    if not database.contains(*UK_CHARACTER_BASE):
        return {}
    prefix = 'Prefabs/Characters/Factions/BLUFOR/UK_Army/1983/Regulars/Character_UK_1983_Regulars_'
    if not database.contains('4A29AE1848437792', prefix + 'BaseLoadout.et'):
        return {}
    roles = {guid: prefix + role + '.et' for guid, role in UK_1983_AI_ROLES.items()}
    return {guid: path for guid, path in roles.items() if database.contains(guid, path)}


def verified_rhs_forced_roles(registry, owned, source_units, dev_units):
    if not registry or RHS_SOBR_BASE[0] in owned or not registry.contains(*RHS_SOBR_BASE):
        return {}
    identity = RHS_SOBR_BASE[0]
    before, after = inheritance(source_units, identity), inheritance(dev_units, identity)
    if not reviewed_native_ancestry(before, after, dev_units):
        return {}
    prefix = RHS_SOBR_BASE[1].removesuffix('BaseLoadout.et')
    roles = {guid: f'{prefix}{role}.et' for guid, role in RHS_SOBR_FORCED_ROLES.items()}
    return {guid: (path, path) for guid, path in roles.items()
            if (guid not in owned and guid not in source_units.resources and guid not in dev_units.resources
                and registry.contains(guid, path))}


# These exact Units Dev overlays came from the published legacy Core package.
EXTERNAL_PARENT_BRIDGES = {
    '5346CF7E39A65A6B': ('Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Base.et',
                         'd2c7fb0178245be7ceaa32f52746a76d624a3d9e5573b68192764e9564fe73db'),
    '7A9EE19AB67B298B': ('Prefabs/Characters/Factions/INDFOR/FIA/Character_FIA_base.et',
                         '6bba2563a432f879d466e9ebed1b312e3f1f5def486175ee71e378f72f3b1c05'),
    '836E7E39AAC5888B': ('Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_Base.et',
                         '27bc46c4bd94e55a55363aad3b9f3c927781a70b75301cea113d368efed80b4d'),
    'EDCFF009262F881C': ('Prefabs/Characters/Factions/OPFOR/RHS_AFRF/Character_RHS_AFRF_Base.et',
                         '6bba2563a432f879d466e9ebed1b312e3f1f5def486175ee71e378f72f3b1c05'),
    'FDAEA6C9F2673B02': ('Prefabs/Characters/Factions/BLUFOR/RHS_USAF/Character_RHS_USAF_Base.et',
                         '3b6f688b2cd351b9f87fa26d10372fc861a9aa0d2bdeb37039f22f4befd900f0'),
}
# These external parents were verified against the published Core/RHS prefabs.
# Both inherit Character_Base, which the legacy GC Lobby package overlays.
LEGACY_PLAYABLE_PARENTS = {
    BASE_CHARACTER,
    ('5346CF7E39A65A6B', 'Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Base.et'),
    ('B889387E9A33D667',
     'Prefabs/Characters/Factions/BLUFOR/RHS_USAF/RHS_USAF_Army/Character_RHS_USA_BaseLoadout.et'),
    ('EDCFF009262F881C', 'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/Character_RHS_AFRF_Base.et'),
    ('7A9EE19AB67B298B', 'Prefabs/Characters/Factions/INDFOR/FIA/Character_FIA_base.et'),
    ('836E7E39AAC5888B', 'Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_Base.et'),
    ('FDAEA6C9F2673B02', 'Prefabs/Characters/Factions/BLUFOR/RHS_USAF/Character_RHS_USAF_Base.et'),
}
# The published legacy resolver uses the registered path when this parent GUID is absent.
LEGACY_CHARACTER_PARENT_FALLBACKS = {
    'F80F8EC885274E3A': ('07299990D00B0823', '44F6F191906D9D1C'),
}
RHS_AI_ROLES = {
    '1701B425406974A0': 'SL', '234A37EC8608DFAC': 'Rifleman', '727CB9BA48D9407A': 'SR_GL',
    'A5548567111B86FD': 'AR', 'AFF07941936347BB': 'AAT', 'F11CCA20FCC74AA3': 'AT',
    '4FDBB4B22FD4640B': 'Spotter_GL',
}


def rhs_ai_characters(workshop):
    database = workshop / 'RHS-StatusQuo_595F2BF2F44836FB/resourceDatabase.rdb'
    if not database.is_file():
        return {}, None
    registry = StockPrefabs(database)
    root = 'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/VKPO_Demiseason/'
    characters = {guid: root + f'RandomSoldiers/Character_RHS_RF_MSV_VKPO_DS_{role}_Random.et'
                  for guid, role in RHS_AI_ROLES.items()}
    characters['0848BAB1B94FCBBA'] = root + 'Character_RHS_RF_MSV_VKPO_DS_Crew.et'
    flora = 'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/Flora/'
    characters['2B97973CD921E6AC'] = flora + 'Character_RHS_AFRF_MSV_Flora_Crew.et'
    for guid, role in {'B3C496CCFA72D25E': 'SL', 'DE4C67D3C8F6704D': 'AR', '06E40E12B17F6E11': 'AT',
                       '4375F8D9CF5FCB47': 'AAT', '9E5162BC890ADFCF': 'SR',
                       'D39919F23F411967': 'GL', '7CCAB195F5A1D9CA': 'Rifleman'}.items():
        characters[guid] = flora + f'Character_RHS_RF_MSV_Flora_{role}.et'
    characters['A0CAC00219D7EF55'] = ('Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/VKPO_Summer/'
                                      'Character_RHS_RF_MSV_VKPO_S_Rifleman.et')
    for guid, role in {'47E5851C66B52637': 'SR_GL', 'B4474D20DB777AB6': 'AR',
                       '5C75226D45102461': 'Rifleman'}.items():
        characters[guid] = ('Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/VKPO_Summer/RandomSoldiers/'
                            f'Character_RHS_RF_MSV_VKPO_S_{role}_Random.et')
    characters.update({guid: path for group in RHS_AI_GROUPS.values() for guid, path in group['slots']})
    for guid, role in {'9D7859DE5E1DA3AB': 'AR', '8DE84439090A3D21': 'SR', '918062BE34F7C4D7': 'CC',
                       'E0E062BD23185D62': 'AMG', 'E1EAB80D1FE2C5FE': 'Sergeant'}.items():
        characters[guid] = root + f'Character_RHS_RF_MSV_VKPO_DS_{role}.et'
    return {guid: path for guid, path in characters.items() if registry.contains(guid, path)}, registry


def verified_stock_ai_group(guid, path, stock, locally_owned, source_units, dev_units, rhs=None, *,
                            inherited_slots=True):
    rhs_spec = RHS_AI_GROUPS.get(guid)
    spec = rhs_spec or STOCK_AI_GROUPS.get(guid)
    provider = rhs if rhs_spec else stock
    if not spec or not provider or path != spec['path'] or not provider.contains(guid, path):
        return None
    if inherited_slots and not spec['slots']:
        return None
    def registered(reference, *, slot=False):
        resource, registered_path = reference
        if resource in locally_owned or not (
                provider.contains(resource, registered_path) or (stock and stock.contains(resource, registered_path))):
            return False
        if resource not in source_units.resources and resource not in dev_units.resources:
            return True
        # A same-ID Units overlay is safe for an inherited stock slot only when unchanged.
        return (slot and not rhs_spec and
                unchanged_deprecated_stock_override(source_units, dev_units, resource, registered_path))

    if not all(registered(ref) for ref in [(guid, path), *spec['inheritedGroups']]):
        return None
    unverified_slots = {slot[0] for slot in spec['slots'] if not registered(slot, slot=True)}
    if not unverified_slots:
        return spec
    if inherited_slots:
        return None
    return {**spec, 'unverifiedSlots': unverified_slots}


def only_path(snapshot, guid):
    paths = snapshot.resources.get(guid.upper(), [])
    if len(paths) != 1:
        raise ValueError('unresolved or duplicate Units prefab ' + guid)
    return paths[0]


@lru_cache(maxsize=2048)
def prefab(snapshot, guid):
    path = only_path(snapshot, guid)
    text = snapshot.read(path)
    if not text.startswith('SCR_AIGroup'):
        raise ValueError('expected SCR_AIGroup ' + path)
    return path, text


def callsign(lines):
    result = {}
    for line in lines:
        field, _, value = line.strip().partition(' ')
        if field not in {*INDEX, 'Enabled'} or not re.fullmatch(r'\d+', value):
            raise ValueError('custom callsign component field ' + field)
        result[field] = int(value)
    return result


@lru_cache(maxsize=16384)
def inheritance(snapshot, guid):
    chain = []
    seen = set()
    while guid.upper() in snapshot.resources:
        if guid in seen:
            raise ValueError('cycle in Units prefab ancestry')
        seen.add(guid)
        body = snapshot.read(only_path(snapshot, guid))
        chain.append(body)
        parent = REF.search(body.splitlines()[0])
        if not parent:
            break
        guid = parent[1]
    return tuple(chain)


def scalar(chain, field):
    for body in chain:
        match = re.search(r'^ ' + field + r' (\d+)$', body, re.M)
        if match:
            return int(match[1])
    return None


@lru_cache(maxsize=8192)
def legacy_member(snapshot, guid, force):
    for body in inheritance(snapshot, guid):
        member = re.search(r'^  PS_PlayableComponent "[^"]+" \{\n(.*?)^  \}', body, re.M | re.S)
        if member:
            if force:
                return True
            active = re.search(r'^   m_bIsPlayable ([01])$', member[1], re.M)
            if active:
                return active[1] == '1'
    return False


def inherits_legacy_playable_base(snapshot, guid, stock, dev):
    chain = inheritance(snapshot, guid)
    if not chain or not stock or not stock.contains(*BASE_CHARACTER):
        return False
    fallback = LEGACY_CHARACTER_PARENT_FALLBACKS.get(guid.upper())
    if fallback and len(chain) == 1:
        stale, canonical = fallback
        ref = REF.search(chain[0].partition('\n')[0])
        if (not ref or ref[1].upper() != stale or stale in snapshot.resources
                or snapshot.resources.get(canonical) != [ref[2]]):
            return False
        normalized = chain[0].replace(ref[0], f'"{{{canonical}}}{ref[2]}"', 1)
        chain = (normalized, *inheritance(snapshot, canonical))
        if not reviewed_native_ancestry(chain, inheritance(dev, guid), dev):
            return False
    if any(not body.startswith('SCR_ChimeraCharacter ') or 'PS_PlayableComponent' in body for body in chain):
        return False
    parent = REF.search(chain[-1].splitlines()[0])
    if not parent:
        return False
    base = (parent[1].upper(), parent[2])
    if base == UK_CHARACTER_BASE:
        return reviewed_native_ancestry(chain, inheritance(dev, guid), dev)
    return base in LEGACY_PLAYABLE_PARENTS


def stock_character(guid, path, stock):
    member = WRAPPERS['characters'].get(guid.upper())
    if stock and member and member['path'] == path and stock.contains(*member['parent']):
        return member['parent']
    return None


def reviewed_native_ancestry(before, after, dev):
    if not before or any(re.search(r'\b(?:PS|TILW)_\w+', body) for body in (*before, *after)):
        return False
    if before == after:
        return True
    if before != after[:-1]:
        return False
    ref = REF.search(before[-1].partition('\n')[0])
    bridge = EXTERNAL_PARENT_BRIDGES.get(ref[1].upper()) if ref else None
    return (bool(bridge) and ref[2] == bridge[0] and dev.resources.get(ref[1].upper()) == [bridge[0]]
            and sha256(after[-1].strip().encode()).hexdigest() == bridge[1])


def stock_wrapper_ancestry_matches(source, dev, guid, stock, owned):
    try:
        before, after = inheritance(source, guid), inheritance(dev, guid)
    except ValueError:
        return False
    if not before:
        return False
    if any(not body.startswith('SCR_ChimeraCharacter ') for body in (*before, *after)):
        return False
    ref = REF.search(before[-1].partition('\n')[0])
    if not ref or ref[1].upper() in owned:
        return False
    parent = stock_character(ref[1], ref[2], stock)
    if not parent or parent[0] in owned:
        return False
    native = before[-1].replace(ref[0], f'"{{{parent[0]}}}{parent[1]}"', 1)
    try:
        underlying = inheritance(source, parent[0])
    except ValueError:
        return False
    return reviewed_native_ancestry((*before[:-1], native, *underlying), after, dev)


def compare_ai_parents(source_units, dev_units, guid, *, replaces_playable_roster=False):
    """Compare native AI group ancestry after removing lobby presentation metadata."""
    def normalize(body):
        lines = body.splitlines()
        kept = []
        index = 0
        while index < len(lines):
            line = lines[index]
            component = CALL.fullmatch(line)
            if component and component[1].upper() == STANDARD_CALLSIGN:
                end = block_end(lines, index)
                callsign(lines[index + 1:end])
                index = end + 1
                continue
            if not re.match(r'^ (?:m_sCustomNameSet|m_sGC_Description|m_iGC_Callsign\w+|m_bPlayable|m_bSetPlayable) ', line):
                kept.append(REF.sub(lambda match: '{' + match[1].upper() + '}', line))
            index += 1
        normalized = '\n'.join(kept).replace(' components {\n }\n', '')
        if re.search(r'\b(?:TILW|PS)_\w+', normalized):
            raise ValueError('AI group parent has custom legacy behavior')
        return normalized

    old = inheritance(source_units, guid)
    new = inheritance(dev_units, guid)
    if not old or tuple(map(normalize, old)) != tuple(map(normalize, new)):
        raise ValueError('inherited AI group behavior differs')
    if not replaces_playable_roster and (
            scalar(old, 'm_bSetPlayable') not in (None, 0) or scalar(new, 'm_bPlayable') not in (None, 0)):
        raise ValueError('inherited AI group playability differs')
    return old


def native_group_field_end(lines, index):
    field = lines[index].strip()
    if NATIVE_GROUP_FIELD.fullmatch(field):
        return index
    if field in STATIC_LINK_FIELDS:
        end = block_end(lines, index)
        # An empty name is a serialized placeholder. All nonempty names remain in order.
        if any(not re.fullmatch(r'\s*(?:"[\w.-]*"\s*)+', entry) for entry in lines[index + 1:end]):
            raise ValueError('unsupported static group link')
        return end
    if field == COLOR_TEAM_ARRAY:
        end = block_end(lines, index)
        if any(value not in COLOR_TEAM_VALUES for line in lines[index + 1:end] for value in line.split()):
            raise ValueError('unsupported group color team')
        return end
    return None


def native_group_symbol(lines, start, end):
    return (end == start + 2 and MILITARY_SYMBOL.fullmatch(lines[start].strip())
            and (MILITARY_IDENTITY.fullmatch(lines[start + 1].strip())
                 or lines[start + 1].strip() == 'm_Dimension AIR'))


def editor_group_preview(lines, start, end):
    """Retain native preview entries explicitly disabled at runtime."""
    if end < start + 4 or [line.strip() for line in lines[start + 1:start + 3]] != [
            'm_bRuntime 0', 'm_aEntries {']:
        return False
    if block_end(lines, start + 2) != end - 1:
        return False
    index = start + 3
    while index < end - 1:
        if not PREVIEW_ENTRY.fullmatch(lines[index].strip()):
            return False
        stop = block_end(lines, index)
        if stop >= end - 1 or any(line.strip().partition(' ')[0] not in {
                'm_iParentID', 'm_Mesh', 'm_Flags', 'm_Shape', 'm_iPivotID', 'm_vScale',
                'm_vPosition', 'm_vAngles',
        } for line in lines[index + 1:stop]):
            return False
        index = stop + 1
    return True


def native_group_component(lines, start, end):
    header = lines[start].strip()
    if FORMATION.fullmatch(header):
        return True
    if header == 'Persistence "{654CB71D928E041B}" {':
        return end == start + 2 and lines[start + 1].strip() == 'Enabled 0'
    if header == 'SCR_CallsignBaseComponent "{65455E36B9829D54}" {':
        return end == start + 1
    if header == MOVEMENT:
        return all(MOVEMENT_FIELD.fullmatch(line.strip()) for line in lines[start + 1:end])
    if header == 'AIChimeraBehaviorTreeComponent "{13BAA835864B8992}" {':
        return end == start + 2 and lines[start + 1].strip() == GROUP_DEFEND_TREE
    if header == AI_CONFIG:
        fields = [line.strip() for line in lines[start + 1:end]]
        if WEAPON_HANDLING_CONFIG in fields:
            index = fields.index(WEAPON_HANDLING_CONFIG)
            if fields[index:index + 2] != [WEAPON_HANDLING_CONFIG, '}']:
                return False
            del fields[index:index + 2]
        return all(line in STOCK_WEAPON_TREES or AI_CONFIG_FIELD.fullmatch(line) for line in fields)
    if header == GROUP_IDENTITY:
        return native_group_symbol(lines, start + 1, end - 1)
    if EDITOR_PREVIEW.fullmatch(header):
        return editor_group_preview(lines, start, end)
    if header != EDITABLE_GROUP or end < start + 3:
        return False
    if end == start + 3 and EMPTY_VARIANT.fullmatch(lines[start + 1].strip()):
        return lines[start + 2].strip() == '}'
    if lines[start + 1].strip() != EDITABLE_UI:
        return False
    if block_end(lines, start + 1) != end - 1:
        return False
    index = start + 2
    found_labels = found_auto_labels = found_symbol = False
    while index < end - 1:
        token = lines[index].strip()
        if token == 'm_aAuthoredLabels {' and not found_labels:
            stop = block_end(lines, index)
            labels = ' '.join(line.strip() for line in lines[index + 1:stop]).split()
            if stop >= end - 1 or any(not re.fullmatch(r'ENTITYTYPE_GROUP|FACTION_[A-Z0-9_]+', label)
                                      for label in labels):
                return False
            found_labels = True
            index = stop + 1
        elif (token == 'm_aAutoLabels {' and not found_auto_labels
              and lines[index + 1].strip() == '}'):
            found_auto_labels = True
            index += 2
        elif EDITABLE_UI_FIELD.fullmatch(token):
            index += 1
        elif MILITARY_SYMBOL.fullmatch(token) and not found_symbol:
            stop = block_end(lines, index)
            if stop >= end - 1 or not native_group_symbol(lines, index, stop):
                return False
            found_symbol = True
            index = stop + 1
        else:
            return False
    return True


def unchanged_disabled_playability(source_units, dev_units, guid):
    return unchanged_explicit_playability(source_units, dev_units, guid, False)


@lru_cache(maxsize=4096)
def unchanged_explicit_playability(source_units, dev_units, guid, playable):
    marker = re.compile(r'^  PS_PlayableComponent "\{5EFCC1946F475F15\}" \{\n   m_bIsPlayable ([01])\n  \}\n', re.M)
    before = inheritance(source_units, guid)
    authored = [value for body in before for value in marker.findall(body)]
    if not authored or authored[0] != str(int(playable)) or legacy_member(source_units, guid, False) != playable:
        return False
    normalized = tuple(marker.sub('', body).replace(' components {\n }\n', '') for body in before)
    after = tuple(body.replace(' components {\n }\n', '') for body in inheritance(dev_units, guid))
    # Compare ancestors after removing the exact legacy marker with no role metadata.
    return reviewed_native_ancestry(normalized, after, dev_units)


DEPRECATED_STOCK_AI_OVERRIDES = {
    '3726077BE60962FF', 'E398E44759DA1A43', 'E45F1E163F5CA080', '6058AB54781A0C52',
}


@lru_cache(maxsize=4096)
def unchanged_deprecated_stock_override(source_units, dev_units, guid, path):
    if guid not in DEPRECATED_STOCK_AI_OVERRIDES:
        return False
    directory, name = path.rsplit('/', 1)
    deprecated = f'{directory}/Deprecated/{name}'
    if source_units.resources.get(guid) != [deprecated] or dev_units.resources.get(guid) != [deprecated]:
        return False
    return (source_units.files[deprecated + '.meta'] == dev_units.files[deprecated + '.meta']
            and reviewed_native_ancestry(inheritance(source_units, guid), inheritance(dev_units, guid), dev_units))


def convert_ai_instance(lines, guid, source_units, dev_units, locally_owned, external_ai, stock_group=None,
                        reviewed_ai_members=None, *, replaces_playable_roster=False):
    old_chain = () if stock_group else compare_ai_parents(
        source_units, dev_units, guid, replaces_playable_roster=replaces_playable_roster)
    inherited_stock_slots = {tuple(slot) for slot in stock_group['slots']} if stock_group else set()
    checked = set()

    def member(match):
        resource = match[1].upper()
        if reviewed_ai_members and reviewed_ai_members.get(resource) == match[2]:
            return match[0]
        if resource not in locally_owned:
            external = external_ai.get(resource) == match[2]
            if (resource not in source_units.resources and resource not in dev_units.resources
                    and (external or (resource, match[2]) in inherited_stock_slots)):
                return match[0]
            if external and resource in DEPRECATED_STOCK_AI_OVERRIDES:
                if unchanged_deprecated_stock_override(source_units, dev_units, resource, match[2]):
                    return match[0]
                raise ValueError('stock AI character override needs review ' + resource)
        if resource not in checked:
            path = only_path(source_units, resource)
            character = source_units.read(path)
            former_slot = legacy_member(source_units, resource, True)
            if (resource in locally_owned or not character.startswith('SCR_ChimeraCharacter')
                    or (former_slot and not unchanged_disabled_playability(source_units, dev_units, resource))):
                raise ValueError('AI slots require character playability review ' + resource)
            checked.add(resource)
        return '"{' + resource + '}' + only_path(dev_units, resource) + '"'

    if not replaces_playable_roster:
        for parent in old_chain:
            slots = SLOTS.search(parent)
            if slots:
                REF.sub(member, slots[1])

    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        token = line.strip()
        if re.fullmatch(r'm_aUnitPrefabSlots (?:\+ ?)?\{', token):
            end = block_end(lines, index)
            if any(entry.strip() != '""' and REF.sub('', entry).strip() for entry in lines[index + 1:end]):
                raise ValueError('unsupported AI slot list')
            result.extend([line, *(REF.sub(member, entry) for entry in lines[index + 1:end]), lines[end]])
            index = end
        elif (end := native_group_field_end(lines, index)) is not None:
            result.extend(lines[index:end + 1])
            index = end
        elif token == 'components {':
            end = block_end(lines, index)
            start = index + 1
            components = []
            while start < end:
                stop = block_end(lines, start)
                assigner = CALL.fullmatch(lines[start])
                if assigner and assigner[1].upper() == STANDARD_CALLSIGN:
                    # This component is consulted only when a legacy player slot is registered.
                    callsign(lines[start + 1:stop])
                elif native_group_component(lines, start, stop):
                    components.extend(lines[start:stop + 1])
                else:
                    raise ValueError('AI group component needs review: ' + lines[start].strip().split()[0])
                start = stop + 1
            if components:
                result.extend([lines[index], *components, lines[end]])
            index = end
        elif token.startswith(('coords ', 'angles ', 'angleX ', 'angleY ', 'angleZ ', 'scale ', 'm_bSnapToTerrain ')):
            result.append(line)
        elif re.fullmatch(r'm_sCustomNameSet ' + QUOTED, token):
            result.append(line.replace('m_sCustomNameSet ', 'm_sGC_ManualCallsign ', 1))
        elif token == 'm_bPlayable 0' and (stock_group or replaces_playable_roster):
            result.append(line)
        elif token != 'm_bSetPlayable 0' and not OBSOLETE_RESPAWN_POSITION.fullmatch(token):
            raise ValueError('AI group field needs review: ' + token.split()[0])
        index += 1
    return result, 0


def inherited_playable_roster(old, new, stock, owned_resources):
    old_slots, new_slots = SLOTS.search(old), SLOTS.search(new)
    if not old_slots or not new_slots:
        raise ValueError('inherited slot roster requires comparison')
    legacy = REF.findall(old_slots[1])
    current = REF.findall(new_slots[1])
    if len(legacy) != len(current):
        raise ValueError('inherited slot roster differs')
    wrappers = set()
    for (guid, path), (dev_guid, dev_path) in zip(legacy, current):
        if guid.upper() == dev_guid.upper():
            continue
        parent = None
        if not any(guid.upper() in resources for resources in owned_resources):
            parent = stock_character(guid, path, stock)
        if not parent or tuple(parent) != (dev_guid.upper(), dev_path):
            raise ValueError('inherited slot roster differs')
        wrappers.add(guid.upper())
    if wrappers and (REF.sub('', old_slots[1]).strip() or REF.sub('', new_slots[1]).strip()):
        raise ValueError('inherited wrapper roster contains unsupported entries')
    return [guid for guid, _ in legacy], wrappers


def convert_instance(lines, guid, old, new, source_units, dev_units, stock, locally_owned,
                     reviewed_callsigns=frozenset(), reviewed_members=None, external_ai=None, restored_members=None,
                     reviewed_ai_members=None, forced_external=None):
    reviewed_members = reviewed_members or {}
    restored_members = restored_members or {}
    lines = list(lines)
    authored = [i for i, line in enumerate(lines) if re.fullmatch(r'\s*m_aUnitPrefabSlots (?:\+ ?)?\{', line)]
    for start in authored:
        end = block_end(lines, start)
        for index in range(start + 1, end):
            # Some legacy slot arrays contain bare resource names without spaces.
            bare = re.fullmatch(r'( +)(\{[\da-fA-F]{16}\}[^\s"]+\.et)\s*', lines[index])
            if bare:
                lines[index] = f'{bare[1]}"{bare[2]}"'
    explicit_force = any(l.strip() == 'm_bSetPlayable 1' for l in lines)
    native_override = next((l.strip()[-1] for l in lines if l.strip() in ('m_bPlayable 0', 'm_bPlayable 1')), None)
    native_playable = (native_override == '1' if native_override is not None
                       else (scalar((new,), 'm_bPlayable') == 1
                             or scalar(inheritance(dev_units, guid), 'm_bPlayable') == 1))
    slots_start = next((i for i, line in enumerate(lines) if line.strip() == 'm_aUnitPrefabSlots {'), None)
    replace_slots = slots_start is not None
    switches = [line.strip() for line in lines if line.strip().startswith('m_bSetPlayable ')]
    override = next((l.strip()[-1] for l in lines if l.strip().startswith('m_bSetPlayable ')), None)
    force = (override == '1' if override is not None else
             (scalar((old,), 'm_bSetPlayable') == 1 or scalar(inheritance(source_units, guid), 'm_bSetPlayable') == 1))
    if native_playable and native_override is None and not force and len(authored) == 1 and replace_slots:
        entries = '\n'.join(lines[slots_start + 1:block_end(lines, slots_start)])
        slots = [(resource.upper(), path) for resource, path in REF.findall(entries)]
        known = external_ai or {}
        reviewed_external = switches == ['m_bSetPlayable 0'] and all(
            known.get(resource) == path and all(resource not in assets for assets in
                                                (locally_owned, source_units.resources, dev_units.resources))
            for resource, path in slots
        )
        # Some native groups inherit a playable default from the Units migration.
        # The legacy roster only exposed reviewed non-playable roles when explicitly forced.
        reviewed_unforced = not switches and all(
            resource in source_units.resources and path in (forced_external or {}).get(resource, ())
            and not legacy_member(source_units, resource, False) for resource, path in slots
        )
        if slots and not REF.sub('', entries).strip() and (reviewed_external or reviewed_unforced):
            indent = lines[slots_start].partition('m_aUnitPrefabSlots')[0]
            return convert_ai_instance([*lines, indent + 'm_bPlayable 0'], guid, source_units, dev_units,
                                       locally_owned, known, replaces_playable_roster=True)
    restored_playable = False

    def playable_member(resource, path):
        if legacy_member(source_units, resource, False):
            return True
        if all(resource not in owned for owned in (locally_owned, source_units.resources, dev_units.resources)):
            return stock_character(resource, path, stock) is not None
        return stock_wrapper_ancestry_matches(source_units, dev_units, resource, stock, locally_owned)

    if not native_playable and not explicit_force and replace_slots and restored_members:
        end = block_end(lines, slots_start)
        slots = [(resource.upper(), path) for resource, path in REF.findall('\n'.join(lines[slots_start + 1:end]))]
        if any(slot in restored_members for slot in slots):
            if not all(slot in restored_members or playable_member(*slot) for slot in slots):
                raise ValueError('mixed playability alongside restored legacy roles')
            restored_playable = True
    character_playable = False
    if not (native_playable or explicit_force or restored_playable):
        # The old lobby registered playable characters independently of the group's native flag.
        if len(authored) <= 1:
            base = SLOTS.search(old)
            members = REF.findall(base[1]) if base and not replace_slots else []
            if authored:
                start = authored[0]
                members.extend(REF.findall('\n'.join(lines[start + 1:block_end(lines, start)])))
            playable = [playable_member(resource.upper(), path) for resource, path in members]
            if any(playable) and not all(playable):
                raise ValueError('mixed character playability requires review')
            character_playable = bool(playable) and all(playable)
    if not (native_playable or explicit_force or restored_playable or character_playable):
        return convert_ai_instance(lines, guid, source_units, dev_units, locally_owned, external_ai or {},
                                   reviewed_ai_members=reviewed_ai_members)
    roster = []
    converted_wrappers = set()
    if not replace_slots:
        roster, converted_wrappers = inherited_playable_roster(
            old, new, stock, (locally_owned, source_units.resources, dev_units.resources))
    forced_external = (forced_external or {}) if force else {}
    approved_external = set()
    out = []
    metadata = []
    depth = len(lines[0]) - len(lines[0].lstrip()) if lines else 1
    indent = ' ' * depth
    inherited = []
    if forced_external and not replace_slots and any(member.upper() in forced_external for member in roster):
        before, after = SLOTS.search(old), SLOTS.search(new)
        if REF.sub('', before[1]).strip() or REF.sub('', after[1]).strip():
            raise ValueError('unsupported inherited external roster')
        for resource, path in REF.findall(after[1]):
            identity = resource.upper()
            if identity in forced_external:
                paths = forced_external[identity]
                if path not in paths:
                    raise ValueError('unverified external role path ' + path)
                path = paths[1]
                approved_external.add(identity)
            elif identity in dev_units.resources:
                path = only_path(dev_units, identity)
            inherited.append(indent + f' "{{{resource}}}{path}"')
    i = 0
    while i < len(lines):
        line = lines[i]
        token = line.strip()
        if token == 'components {':
            end = block_end(lines, i)
            j = i + 1
            components = []
            assigned = None
            while j < end:
                last = block_end(lines, j)
                match = CALL.fullmatch(lines[j])
                if (match and match[1].upper() == '65136701CA021D8F'
                        and assigned == (STANDARD_CALLSIGN, {'Enabled': 1})):
                    placed = callsign(lines[j+1:last])
                    if placed != {'Enabled': 0, 'm_iCompanyCallsign': 1, 'm_iSquadCallsign': 1}:
                        raise ValueError('changed secondary callsign component')
                elif match and (match[1].upper() == STANDARD_CALLSIGN
                                or match[1].upper() in VERIFIED_PLACED_CALLSIGNS
                                or match[1].upper() in reviewed_callsigns):
                    identity = match[1].upper()
                    placed = callsign(lines[j+1:last])
                    if assigned is not None:
                        # A unification pass reused the same ID for the retained numeric
                        # override and the old disabled default. Enabled has no bearing on
                        # the numeric reads made by the legacy playable manager.
                        if reviewed_callsigns or assigned == (identity, placed) or (
                                assigned[0] == identity and placed == {'Enabled': 0}
                                                  and set(assigned[1]) & INDEX.keys()):
                            j = last + 1
                            continue
                        # Enfusion 1.9 keeps the later same-ID active override as
                        # a whole. Omitted indices still inherit from the prefab.
                        same_active_component = (
                            identity == STANDARD_CALLSIGN == assigned[0]
                            and placed.get('Enabled') == assigned[1].get('Enabled') == 1
                            and set(placed) & INDEX.keys()
                            and set(assigned[1]) & INDEX.keys())
                        if not same_active_component and not (
                                assigned == (identity, {'Enabled': 0}) and set(placed) & INDEX.keys()):
                            raise ValueError('ambiguous duplicate callsign component')
                    assigned = (identity, placed)
                elif native_group_component(lines, j, last):
                    components.extend(lines[j:last+1])
                else:
                    raise ValueError('group component needs review: ' + lines[j].strip().split()[0])
                j = last + 1
            if assigned is not None:
                # Unspecified indices inherit through Units, then use the three zero defaults.
                effective = dict.fromkeys(INDEX, 0)
                for body in reversed(inheritance(source_units, guid)):
                    source_lines = body.splitlines()
                    for pos, item in enumerate(source_lines):
                        inherited_call = CALL.fullmatch(item)
                        if inherited_call and inherited_call[1].upper() == assigned[0]:
                            effective.update(callsign(source_lines[pos+1:block_end(source_lines, pos)]))
                effective.update(assigned[1])
                metadata.extend(indent + f'{dest} "{effective[k]}"' for k, dest in INDEX.items())
            if components:
                out.extend([line, *components, lines[end]])
            i = end
        elif re.fullmatch(r'm_aUnitPrefabSlots (?:\+ ?)?\{', token):
            end = block_end(lines, i)
            def slot(match):
                guid, path = match.groups()
                if (paths := forced_external.get(guid.upper())) and path in paths:
                    approved_external.add(guid.upper())
                    return '"{' + guid + '}' + paths[1] + '"'
                restored = restored_members.get((guid.upper(), path))
                if restored:
                    converted_wrappers.add(guid.upper())
                    return f'"{{{restored[0]}}}{restored[1]}"'
                if guid.upper() in reviewed_members:
                    path = reviewed_members[guid.upper()]
                elif guid.upper() in locally_owned:
                    raise ValueError('mission-owned slot ' + guid)
                elif guid.upper() in source_units.resources or guid.upper() in dev_units.resources:
                    only_path(source_units, guid)
                    path = only_path(dev_units, guid)
                else:
                    parent = stock_character(guid, path, stock)
                    if parent:
                        converted_wrappers.add(guid.upper())
                        return f'"{{{parent[0]}}}{parent[1]}"'
                    if not stock or not stock.contains(guid, path):
                        raise ValueError('unresolved character prefab ' + guid)
                if guid.upper() not in reviewed_members and not path.lower().startswith('prefabs/characters/'):
                    raise ValueError('unexpected slot resource ' + path)
                return '"{' + guid + '}' + path + '"'
            if '+' not in token:
                roster.clear()
            roster.extend(g for item in lines[i+1:end] for g, _ in REF.findall(item))
            if inherited:
                out.extend([indent + 'm_aUnitPrefabSlots {', *inherited])
                inherited.clear()
            else:
                out.append(line)
            for item in lines[i+1:end]:
                if REF.sub('', item).replace('""', '').strip():
                    raise ValueError('unsupported slot list')
                out.append(REF.sub(slot, item).rstrip())
            out.append(lines[end])
            i = end
        elif token == 'm_aUnitPrefabOverrideIcons {':
            # Published legacy Lobby reads each role icon from its character UI info.
            # Its group class has no field or consumer for this serialized array.
            end = block_end(lines, i)
            if any(not re.fullmatch(r'\s*(?:' + QUOTED + r'\s*)+', entry)
                   for entry in lines[i + 1:end]):
                raise ValueError('unsupported legacy role icon override list')
            i = end
        elif (end := native_group_field_end(lines, i)) is not None:
            out.extend(lines[i:end + 1])
            i = end
        elif token.startswith('m_sCustomNameSet '):
            metadata.append(line.replace('m_sCustomNameSet ', 'm_sGC_Description ', 1))
        elif token in ('m_bSetPlayable 0', 'm_bSetPlayable 1'):
            pass
        elif OBSOLETE_RESPAWN_POSITION.fullmatch(token):
            # Workbench rejects this old SCR_AIGroup field in the published legacy missions.
            pass
        elif token == 'm_bPlayable 0' and (explicit_force or restored_playable or character_playable):
            # Legacy character playability was independent of this native group setting.
            pass
        elif token.startswith(('coords ', 'angles ', 'angleX ', 'angleY ', 'angleZ ', 'scale ', 'm_bSnapToTerrain ')) or token == 'm_bPlayable 1':
            out.append(line)
        else:
            raise ValueError('group field needs review: ' + token.split(' ')[0])
        i += 1
    if not roster:
        raise ValueError('empty group roster')
    if inherited:
        out.extend([indent + 'm_aUnitPrefabSlots {', *inherited, indent + '}'])
    for member in roster:
        if member.upper() in reviewed_members or member.upper() in converted_wrappers or member.upper() in approved_external:
            continue
        if not (legacy_member(source_units, member, force)
                or (force and inherits_legacy_playable_base(source_units, member, stock, dev_units))
                or stock_wrapper_ancestry_matches(source_units, dev_units, member, stock, locally_owned)):
            raise ValueError('legacy slot playability requires review ' + member)
        only_path(dev_units, member)
    if not native_playable:
        metadata.append(indent + 'm_bPlayable 1')
    return out + metadata, len(roster)


def convert_stock_instance(lines, spec, source_units, dev_units, stock, locally_owned, reviewed_members, *,
                           reviewed_callsigns=frozenset()):
    roster = list(spec['slots'])
    placement = []
    depth = min((len(line) - len(line.lstrip()) for line in lines if line.strip()), default=1)
    indent = ' ' * depth
    index = 0
    while index < len(lines):
        line = lines[index]
        slot_block = re.fullmatch(re.escape(indent) + r'm_aUnitPrefabSlots (\+ ?)?\{', line)
        if not slot_block:
            placement.append(line)
            index += 1
            continue
        end = block_end(lines, index)
        values = '\n'.join(lines[index + 1:end])
        if REF.sub('', values).strip():
            raise ValueError('unsupported stock wrapper slot list')
        if not slot_block[1]:
            roster.clear()
        roster.extend(REF.findall(values))
        index = end + 1
    verified = {}
    rendered = []
    for guid, path in roster:
        canonical = (reviewed_members or {}).get(guid.upper())
        parent = (guid.upper(), canonical) if canonical == path else stock_character(guid, path, stock)
        if not parent:
            raise ValueError('unverified stock wrapper character ' + guid)
        resource, asset = parent
        verified[resource.upper()] = asset
        rendered.append(f'{indent} "{{{resource}}}{asset}"')
    placement.extend([indent + 'm_aUnitPrefabSlots {', *rendered, indent + '}', indent + 'm_bSetPlayable 1'])
    return convert_instance(placement, spec['parent'][0], '', '', source_units, dev_units, stock, locally_owned,
                            reviewed_members=verified, reviewed_callsigns=reviewed_callsigns)


def convert_registered_group(lines, guid, source_units, dev_units, stock, locally_owned, external_ai,
                             stock_group, reviewed_ai_members, forced_external, reviewed_members=None):
    rosters = [index for index, line in enumerate(lines) if line.strip() == 'm_aUnitPrefabSlots {']
    entries = ''
    authored_slots = []
    if len(rosters) == 1:
        start, = rosters
        entries = '\n'.join(lines[start + 1:block_end(lines, start)])
        authored_slots = REF.findall(entries)
    if not stock_group['slots'] and (not authored_slots or REF.sub('', entries).strip()):
        raise ValueError('native group base needs a complete authored roster ' + guid)
    unverified_slots = stock_group.get('unverifiedSlots')
    if unverified_slots and (
            not authored_slots or REF.sub('', entries).strip()
            or any(line.strip().startswith('m_aUnitPrefabSlots +') for line in lines)
            or any(resource.upper() in unverified_slots for resource, _ in authored_slots)):
        raise ValueError('unverified native AI group requires a complete replacement roster ' + guid)
    all_playable = bool(authored_slots) and all(
        resource.upper() in source_units.resources and legacy_member(source_units, resource.upper(), False)
        for resource, _ in authored_slots)
    switches = [line.strip() for line in lines if line.strip().startswith('m_bSetPlayable ')]
    if not rosters and not unverified_slots and switches == ['m_bSetPlayable 1'] and guid in STOCK_FORCED_ROSTERS:
        registered = {resource: path for group, order in STOCK_FORCED_ROSTERS.items()
                      for resource, path in STOCK_AI_GROUPS[group]['slots'] if resource in order}
        available = {resource: path for resource, path in registered.items() if stock.contains(resource, path)
                     and all(resource not in owned for owned in (locally_owned, source_units.resources,
                                                                  dev_units.resources))}
        order = STOCK_FORCED_ROSTERS[guid]
        if any(member not in available for member in order):
            raise ValueError('unverified inherited playable roster ' + guid)
        spec = {'slots': [(member, available[member]) for member in order], 'parent': (guid, stock_group['path'])}
        placement = [line for line in lines if line.strip() != 'm_bSetPlayable 1']
        callsigns = [match[1].upper() for line in placement if (match := CALL.fullmatch(line))]
        reviewed_callsigns = (set(callsigns) & STOCK_FORCED_CALLSIGNS) if len(callsigns) == 1 else frozenset()
        return convert_stock_instance(placement, spec, source_units, dev_units, stock, locally_owned,
                                      {**(reviewed_members or {}), **available}, reviewed_callsigns=reviewed_callsigns)
    if all_playable or 'm_bSetPlayable 1' in switches:
        if len(rosters) != 1:
            raise ValueError('forced playable native group needs a complete authored roster')
        return convert_instance(lines, guid, '', '', source_units, dev_units, stock, locally_owned,
                                forced_external=forced_external)
    return convert_ai_instance(lines, guid, source_units, dev_units, locally_owned, external_ai,
                               stock_group=stock_group, reviewed_ai_members=reviewed_ai_members)


def group_layer(text, source_units, dev_units, stock, locally_owned, reviewed_callsigns=frozenset(),
                reviewed_members=None, external_ai=None, allow_stock_ai=False, restored_members=None,
                reviewed_ai_members=None, forced_external=None, *, rhs=None, depth=0, local_aliases=None,
                restored_rosters=None):
    if not re.search(r'^(?:\$grp )?SCR_AIGroup\b', text, re.M):
        return None

    def convert_member(convert, fields, indent):
        prefix = ' ' * indent
        start = next((i for i, line in enumerate(fields) if line == prefix + '{'), None)
        if start is None:
            body, members = convert(fields)
            return body, int(members > 0), members
        stop = block_end(fields, start)
        if depth >= 12 or stop != len(fields) - 1 or stop == start + 1:
            raise ValueError('unsupported group child list')
        child_prefix = prefix + ' '
        if any(line.strip() and not line.startswith(child_prefix) for line in fields[start + 1:stop]):
            raise ValueError('invalid group child indentation')
        nested = '\n'.join(line[len(child_prefix):] for line in fields[start + 1:stop])
        # Native waypoints keep their attachment, local transforms and serialized order.
        native_children = placements(nested, stock, locally_owned) if stock else None
        if native_children and native_children[0] == ['waypoints']:
            body, members = convert(fields[:start])
            return [*body, *fields[start:]], int(members > 0), members
        children = group_layer(nested, source_units, dev_units, stock, locally_owned, reviewed_callsigns,
                               reviewed_members, external_ai, allow_stock_ai, restored_members,
                               reviewed_ai_members, forced_external, rhs=rhs, depth=depth + 1,
                               local_aliases=local_aliases, restored_rosters=restored_rosters)
        if children is None:
            raise ValueError('unsupported child entity on a group')
        body, members = convert(fields[:start])
        child_body, child_groups, child_slots = children
        return ([*body, fields[start], *(child_prefix + line for line in child_body.splitlines()), fields[stop]],
                int(members > 0) + child_groups, members + child_slots)

    lines = text.splitlines()
    result = []
    count = slots = i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        match = ROOT.fullmatch(lines[i])
        if not match:
            raise ValueError('mixed layer or custom group declaration')
        guid = match['guid'].upper()
        local = (local_aliases or {}).get(guid)
        if guid in locally_owned and (not local or REF.search(lines[i])[2] != local[0]):
            raise ValueError('mission-owned group ' + guid)
        spec = WRAPPERS['groups'].get(guid) if guid not in source_units.resources and stock else None
        native_group = allow_stock_ai and (guid in STOCK_AI_GROUPS or guid in RHS_AI_GROUPS)
        stock_group = None
        if native_group and guid not in source_units.resources and guid not in dev_units.resources:
            path = REF.search(lines[i])[2]
            stock_group = verified_stock_ai_group(guid, path, stock, locally_owned, source_units, dev_units,
                                                  rhs, inherited_slots=False)
            if not stock_group:
                raise ValueError('unverified native AI group ancestry ' + guid)
        if spec:
            if REF.search(lines[i])[2].casefold() != spec['path'].casefold() or not stock.contains(*spec['parent']):
                raise ValueError('unverified stock wrapper group ' + guid)
            resource, dest = spec['parent']
            convert = partial(convert_stock_instance, spec=spec, source_units=source_units, dev_units=dev_units,
                              stock=stock, locally_owned=locally_owned, reviewed_members=reviewed_members)
        elif stock_group:
            resource, dest = guid, stock_group['path']
            convert = partial(convert_registered_group, guid=guid, source_units=source_units, dev_units=dev_units,
                              stock=stock, locally_owned=locally_owned, external_ai=external_ai or {},
                              stock_group=stock_group, reviewed_ai_members=reviewed_ai_members,
                              forced_external=forced_external, reviewed_members=reviewed_members)
        else:
            parent = local[1] if local else guid
            if local and local[2]:
                old, new = local[2]
            else:
                _, old = prefab(source_units, parent)
                dest, new = prefab(dev_units, parent)
            if local:
                dest = local[0]
            resource = guid
            convert = partial(convert_instance, guid=parent, old=old, new=new, source_units=source_units,
                              dev_units=dev_units, stock=stock, locally_owned=locally_owned,
                              reviewed_callsigns=reviewed_callsigns, reviewed_members=reviewed_members,
                              external_ai=external_ai, restored_members=restored_members,
                              reviewed_ai_members=reviewed_ai_members, forced_external=forced_external)
            if roster := (restored_rosters or {}).get((guid, REF.search(lines[i])[2])):
                convert = partial(convert_with_restored_roster, convert=convert, roster=roster,
                                  indent='  ' if match['group'] else ' ')
        result.append(REF.sub(lambda _: '"{' + resource + '}' + dest + '"', lines[i], count=1))
        end = block_end(lines, i)
        if match['group']:
            j = i + 1
            while j < end:
                if not re.fullmatch(r' (?:[\w.\-]+ )?\{', lines[j]):
                    raise ValueError('unexpected group instance')
                last = block_end(lines, j)
                body, groups, members = convert_member(convert, lines[j+1:last], 2)
                result.extend([lines[j], *body, lines[last]])
                slots += members
                count += groups
                j = last + 1
        else:
            body, groups, members = convert_member(convert, lines[i+1:end], 1)
            result.extend(body)
            slots += members
            count += groups
        result.append(lines[end])
        i = end + 1
    return '\n'.join(result) + '\n', count, slots


def convert_with_restored_roster(fields, *, convert, roster, indent):
    if any(field.strip() in ('m_bSetPlayable 0', 'm_bPlayable 0') for field in fields):
        return convert(fields)
    start = next((index for index, field in enumerate(fields)
                  if re.fullmatch(r'\s*m_aUnitPrefabSlots (?:\+ ?)?\{', field)), None)
    if start is not None:
        end = block_end(fields, start)
        if '+' not in fields[start] or end != start + 1:
            return convert(fields)
        fields = [*fields[:start], *fields[end + 1:]]
    entries = [f'{indent} "{{{resource}}}{path}"' for resource, path in roster]
    return convert([*fields, indent + 'm_aUnitPrefabSlots {', *entries, indent + '}'])
