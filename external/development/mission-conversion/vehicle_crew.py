"""Convert authored AI crews on registered vehicles and composition turrets."""
from decimal import Decimal
from hashlib import sha256
from pathlib import PurePosixPath
import re

from briefings import block_end, properties, target_names
from catalog import REF
from groups import inheritance, legacy_member
from local_loadouts import unchanged_units_parent
from polygon_capture import members
from scenery import StockPrefabs

VEHICLE = re.compile(
    r'(?P<group>\$grp )?(?:Vehicle|Tank|Turret)(?: (?P<name>[\w.-]+))? : '
    r'"\{(?P<guid>[\da-fA-F]{16})\}(?P<path>Prefabs/(?:Vehicles|Weapons/Tripods)/[^"]+\.et)" \{')
COMPOSITION = re.compile(r'(?P<group>\$grp )?GenericEntity(?: (?P<name>[\w.-]+))? : "[^"]+" \{')
PLACED_CHILD = re.compile(r'(GenericEntity|Turret)(?: [\w.-]+)? \{')
COMPONENT = re.compile(r'  TILW_VehicleCrewComponent (?P<id>"\{[\da-fA-F]{16}\}") \{')
SERIALIZED_ID = r'"\{[\da-fA-F]{16}\}"'
SEAT_TYPES = ('Pilot', 'Turret', 'Cargo')
WORKSHOP_VEHICLES = {
    '9AEDF323A812F9ED': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2_AFRF.et'),
    '18378FFFC82F30BF': ('M113APC_5E5C154FEE1094BB', 'Prefabs/Vehicles/Tracked/M113/M113_APC.et'),
    '31F0866273C46D76': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/K17/APC_K17_Berezok.et'),
    '447CFE8D73F95C3E': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_AFRF.et'),
    '6B7CD408BDFB94F5': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_Green.et'),
    '7BC76F92B882889D': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM.et'),
    'AC12E5669B411D57': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/KORD/GAZ_TIGR_v2_KORD.et'),
}
PKM = ('723870DBB19D30B0', 'Prefabs/Weapons/Tripods/Tripod_6T5_PKM.et')
SINGLE_SEAT_TURRETS = {
    PKM,
    ('73530808E4B455BA', 'Prefabs/Weapons/Tripods/Tripod_M3_M2HB.et'),
    ('887DB60486A18575', 'Prefabs/Weapons/Tripods/Tripod_M122_MG_M60.et'),
}
COMPOSITION_TURRETS = {
    ('25861AD07CEE719A', 'Prefabs/Compositions/Misc/SubCompositions/Sandbag_MG_USSR_01_PKM.et'):
        {('58FEB999CF41D2D6',): PKM},
    ('25861AD07CEE719B', 'Prefabs/Compositions/Misc/SubCompositions/Fortifications/Sandbag_MG_USSR_01_PKM.et'):
        {('5CC6B4BEF18D0296',): PKM},
    ('7A40BF128BB47EFD', 'Prefabs/Compositions/Slotted/SlotFlatSmall/MachineGunNest_S_USSR_01.et'):
        {('5CCCBEDC9EB8023A', '5CC6B4BEF18D0296'): PKM},
    ('114DE81321786CD9', 'Prefabs/Compositions/Slotted/SlotFlatSmall/MachineGunNest_S_USSR_01_PKM.et'):
        {('5CCCBEDC9EB8023A', '5CC6B4BEF18D0296'): PKM},
}
# The Units override only alters three scenery model flags, leaving the turret inherited.
COMPOSITION_OVERRIDES = {'7A40BF128BB47EFD': '46f28ca4f00646d4223fc72a3e03c28331c99c1e1fae3a42dda45e268b67a45a'}
# The smoke-only ancestor moved from published Core to Units Dev.
BTR70_CREW_OVERLAY = '447CFE8D73F95C3E'
BTR70_CREW_LEAF = '81a8ccf59bed709f4eb87c25c260e529acd6cbb74dab97d09566adf828395c78'
BTR70_SMOKE_ANCESTOR = 'ae982b43b504904f7b97947e7fc45c83f584e46ce5f7fa8d7c08c05e386eee74'


def workshop_vehicles(workshop):
    result = {}
    if not workshop:
        return result
    for resource, (provider, path) in WORKSHOP_VEHICLES.items():
        registry = workshop / provider / 'resourceDatabase.rdb'
        if registry.is_file() and StockPrefabs(registry).contains(resource, path):
            result[resource] = path
    return result


def contents(lines, field):
    if (not lines or not re.fullmatch(field + r' \{', lines[0].strip())
            or block_end(lines, 0) != len(lines) - 1):
        raise ValueError('crew authoring needs one ' + field.split()[0])
    return lines[1:-1]


def character_reference(guid, path, source_units, dev_units, stock, owned, external_ai=None):
    if guid in owned or not path.startswith('Prefabs/Characters/'):
        raise ValueError('unverified crew character ' + guid)
    if guid in source_units.resources or guid in dev_units.resources:
        if (not unchanged_units_parent(source_units, dev_units, guid)
                or legacy_member(source_units, guid, False)):
            raise ValueError('crew character ancestry needs review ' + guid)
        canonical, = dev_units.resources[guid]
        if PurePosixPath(path).name != PurePosixPath(canonical).name:
            raise ValueError('crew character path needs review ' + guid)
        return f'"{{{guid}}}{canonical}"'
    if not stock.contains(guid, path) and (not external_ai or external_ai.get(guid) != path):
        raise ValueError('unregistered crew character ' + guid)
    return f'"{{{guid}}}{path}"'


CREW_ROLE = re.compile(REF.pattern + r'|""')


def crew_roles(authored, flags, resolve_character, allow_empty=False):
    if (not authored or CREW_ROLE.sub('', authored).strip() or all(flag == '0' for flag in flags.values())
            or any(flag not in ('0', '1') for flag in flags.values())):
        raise ValueError('crew migration requires explicit characters and qualifying seats')
    roles = list(CREW_ROLE.finditer(authored))
    if len(roles) > 128 or not any(role[1] for role in roles):
        raise ValueError('unsupported crew size or empty roster')
    if not allow_empty and any(not role[1] for role in roles):
        raise ValueError('crew station contains empty seats')
    return [resolve_character(role[1].upper(), role[2]) if role[1] else '""' for role in roles]


def crew_station(lines, resolve_character, allow_empty=False):
    station = contents(lines, 'TILW_CrewStation ' + SERIALIZED_ID)
    values = properties(station, {'m_characters', *('m_allow' + seat for seat in SEAT_TYPES)})
    flags = {seat: values.get('m_allow' + seat, '1') for seat in SEAT_TYPES}
    authored = values.get('m_characters', '')
    if authored.startswith('""'):
        raise ValueError('legacy station suppresses crew when the first seat is blank')
    return flags, crew_roles(authored, flags, resolve_character, allow_empty)


def render_component(component, lines, resolve_character, single_turret):
    configs = [i for i, line in enumerate(lines) if line.strip().startswith('m_crewConfig ')]
    if not configs:
        data = properties(lines, {'m_customCrew', 'm_noTurretDismount', 'm_idleGroup', 'm_pregameSpawn',
                                  'm_waypointNames', 'm_waypointDelay',
                                  *('m_spawn' + seat for seat in SEAT_TYPES)})
        flags = {seat: data.get('m_spawn' + seat, '0') for seat in SEAT_TYPES}
        if all(flag == '0' for flag in flags.values()):
            flags = dict.fromkeys(SEAT_TYPES, '1')
        seated = data.get('m_noTurretDismount', '0')
        idle = data.get('m_idleGroup', '0')
        if 'm_customCrew' not in data:
            raise ValueError('legacy placed crew omits its character list')
        roles = crew_roles(data.get('m_customCrew', ''), flags, resolve_character)
        waypoints = target_names(data['m_waypointNames']) if data.get('m_waypointNames') else []
        delay = data.get('m_waypointDelay', '5')
        if (seated not in ('0', '1') or idle not in ('0', '1') or data.get('m_pregameSpawn', '0') != '0'
                or (waypoints and (not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400))):
            raise ValueError('unsupported placed crew timing or gunner settings')
        separate = idle == '1' and flags['Turret'] == '1'
        options = []
        if separate and not (single_turret and len(roles) == 1 and not waypoints):
            options.append('   m_bShareIdleGunnerGroup 1')
        if waypoints:
            options.extend(['   m_fCrewWaypointDelay ' + delay, '   m_aCrewWaypoints {',
                            *('    ' + name for name in waypoints), '   }'])
        return crew_fields(component, flags, seated, roles, options)
    if len(configs) != 1:
        raise ValueError('ambiguous placed crew configuration')
    index, = configs
    close = block_end(lines, index)
    outer = properties(lines[:index] + lines[close + 1:], {'m_noTurretDismount', 'm_pregameSpawn'})
    if outer.get('m_pregameSpawn', '0') != '0' or outer.get('m_noTurretDismount', '0') not in ('0', '1'):
        raise ValueError('unsupported placed crew timing or deprecated dismount field')
    # The legacy configured-crew path never reads the component's deprecated dismount field.
    config = contents(lines[index:close + 1], r'm_crewConfig TILW_CrewConfig ' + SERIALIZED_ID)
    groups = [i for i, line in enumerate(config) if line.strip() == 'm_crewGroups {']
    if len(groups) != 1:
        raise ValueError('crew authoring needs one m_crewGroups')
    start = groups[0]
    end = block_end(config, start)
    options = properties(config[:start] + config[end + 1:], {'m_noTurretDismount', 'm_useIdleGroups'})
    seated = options.get('m_noTurretDismount', '0')
    if seated not in ('0', '1') or options.get('m_useIdleGroups', '1') not in ('0', '1'):
        raise ValueError('invalid placed crew options')
    station = config[start + 1:end]
    for field in ('TILW_CrewGroup ' + SERIALIZED_ID, 'm_crewStations'):
        station = contents(station, field)
    flags, roles = crew_station(station, resolve_character)
    return crew_fields(component, flags, seated, roles)


def crew_fields(component, flags, seated, roles, options=()):
    return [f'  GC_VehicleCrewComponent {component["id"]} {{',
            *(f'   m_bAllow{seat} 0' for seat in SEAT_TYPES if flags[seat] == '0'),
            *(['   m_bKeepGunnersSeated 1'] if seated == '1' else []),
            *options, '   m_aCharacters {', *('    ' + role for role in roles), '   }', '  }']


def verified_btr70_overlay(guid, path, source_units, dev_units):
    if guid != BTR70_CREW_OVERLAY or source_units.resources.get(guid) != [path] or dev_units.resources.get(guid) != [path]:
        return False
    before = [sha256(body.strip().encode()).hexdigest() for body in inheritance(source_units, guid)]
    after = [sha256(body.strip().encode()).hexdigest() for body in inheritance(dev_units, guid)]
    return before == [BTR70_CREW_LEAF] and after == [BTR70_CREW_LEAF, BTR70_SMOKE_ANCESTOR]


def convert(block, source_units, dev_units, stock, owned, external_vehicles=None, external_ai=None):
    if not any('TILW_VehicleCrewComponent' in line for line in block):
        return block
    vehicle = VEHICLE.fullmatch(block[0])
    composition = COMPOSITION.fullmatch(block[0])
    ref = REF.search(block[0])
    if not ref or not (vehicle or composition):
        raise ValueError('crew needs a registered vehicle or composition')
    guid, path = ref[1].upper(), ref[2]
    inherited_turrets = COMPOSITION_TURRETS.get((guid, path)) if composition else None
    if composition and not inherited_turrets:
        raise ValueError('crew needs a reviewed composition turret identity')
    external_vehicle = external_vehicles and external_vehicles.get(guid) == path
    units_owned = guid in source_units.resources or guid in dev_units.resources
    inherited_override = (inherited_turrets and units_owned and guid in COMPOSITION_OVERRIDES
                          and source_units.resources.get(guid) == dev_units.resources.get(guid) == [path]
                          and source_units.read(path) == dev_units.read(path)
                          and sha256(dev_units.read(path).strip().encode()).hexdigest() == COMPOSITION_OVERRIDES[guid])
    vehicle_overlay = vehicle and external_vehicle and verified_btr70_overlay(guid, path, source_units, dev_units)
    if (guid in owned or (units_owned and not (inherited_override or vehicle_overlay))
            or not (stock.contains(guid, path) or external_vehicle)):
        raise ValueError('unverified crew vehicle ' + guid)

    def replace_crew(lines, single_turret):
        if not any('TILW_VehicleCrewComponent' in line for line in lines):
            return lines
        roots = [index for index, line in enumerate(lines) if line == ' components {']
        crew = [(index, COMPONENT.fullmatch(line)) for index, line in enumerate(lines)
                if 'TILW_VehicleCrewComponent' in line]
        if len(roots) != 1 or len(crew) != 1:
            raise ValueError('placement needs exactly one standard crew component')
        start = roots[0]
        index, component = crew[0]
        if not component or not start < index < block_end(lines, start):
            raise ValueError('crew component is outside the vehicle components')
        end = block_end(lines, index)
        replacement = render_component(
            component, lines[index + 1:end],
            lambda guid, path: character_reference(guid, path, source_units, dev_units, stock, owned, external_ai),
            single_turret)
        return [*lines[:index], *replacement, *lines[end + 1:]]

    def convert_inherited(lines, parents=()):
        start = next((i for i, line in enumerate(lines) if line == ' {'), None)
        if start is None or block_end(lines, start) != len(lines) - 1:
            raise ValueError('unsupported crew composition child list')
        rendered = lines[:start + 1]
        cursor = start + 1
        while cursor < len(lines) - 1:
            end = block_end(lines, cursor)
            child = lines[cursor:end + 1]
            if any('TILW_VehicleCrewComponent' in line for line in child):
                header = PLACED_CHILD.fullmatch(child[0].strip())
                ids = [match[1].upper() for item in child[1:-1]
                       if (match := re.fullmatch(r'   ID "([\dA-Fa-f]{16})"', item))]
                if not header or len(ids) != 1:
                    raise ValueError('crew composition child lacks a verified identity')
                key = (*parents, ids[0])
                body = [line[2:] for line in child[1:-1]]
                if header[1] == 'GenericEntity':
                    converted = convert_inherited(body, key)
                else:
                    turret = inherited_turrets.get(key)
                    if (not turret or turret[0] in owned or turret[0] in source_units.resources
                            or turret[0] in dev_units.resources or not stock.contains(*turret)):
                        raise ValueError('unverified inherited turret ' + '/'.join(key))
                    converted = replace_crew(body, turret in SINGLE_SEAT_TURRETS)
                child = [child[0], *('  ' + line for line in converted), child[-1]]
            rendered.extend(child)
            cursor = end + 1
        return [*rendered, lines[-1]]

    def instance(lines):
        if not any('TILW_VehicleCrewComponent' in line for line in lines):
            return lines
        if inherited_turrets:
            return convert_inherited(lines)
        return replace_crew(lines, (guid, path) in SINGLE_SEAT_TURRETS)

    root = vehicle or composition
    if not root['group']:
        return [block[0], *instance(block[1:-1]), block[-1]]
    rendered = [block[0]]
    for name, lines in members(block, True, None):
        rendered.append(f' {name} {{' if name else ' {')
        rendered.extend(' ' + line for line in instance(lines))
        rendered.append(' }')
    return [*rendered, block[-1]]
