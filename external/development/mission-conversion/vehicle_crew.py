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
    '0144F571F1998FC4': ('WCS_BMP-1_5E524E4FEECCA92B', 'Prefabs/Vehicles/Tracked/BMP1/BMP1_FIA.et'),
    '1A407760EEAE4156': ('ZU-23_5C9AD9EF76F6B5EA', 'Prefabs/Vehicles/Static/BaconZU23/BaconZU23_Turret_Deployed.et'),
    '9AEDF323A812F9ED': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2_AFRF.et'),
    '18378FFFC82F30BF': ('M113APC_5E5C154FEE1094BB', 'Prefabs/Vehicles/Tracked/M113/M113_APC.et'),
    '2B9DB09AC8BEA673': ('WCS_T-72_5E0AB16BEB16D6A4', 'Prefabs/Vehicles/Tracked/T72A/T72A_FIA.et'),
    '31F0866273C46D76': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/K17/APC_K17_Berezok.et'),
    '3312ADBF53A16DD8': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/LAV25/LAV25_RHS.et'),
    '447CFE8D73F95C3E': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_AFRF.et'),
    '5CFD8CB5C3E55A4E': ('WCS_M2A2_63120AE07E6C0966', 'Prefabs/Vehicles/Tracked/M2A2/M2A2_MERDC.et'),
    '6B7CD408BDFB94F5': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_Green.et'),
    '7BC76F92B882889D': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM.et'),
    '7ACFD8FA081996F6': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_1P29.et'),
    '8E7F7A0368CE8805': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_Green_1P29.et'),
    '95A220DF8D6E2543': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/ArbaletDM/GAZ_TIGR_v2_Arbalet_KORD_Green.et'),
    '953523CFE2DB9769': ('M113APC_5E5C154FEE1094BB', 'Prefabs/Vehicles/Tracked/M113/M113_APC_MERDC.et'),
    'AC12E5669B411D57': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/KORD/GAZ_TIGR_v2_KORD.et'),
    'AF1C02364D45CC5B': ('WCS_T-72_5E0AB16BEB16D6A4', 'Prefabs/Vehicles/Tracked/T72A/T72A_Desert.et'),
    'FD109F3702AAB392': ('ZU-23_5C9AD9EF76F6B5EA', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_ZU23.et'),
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
NVA_BTR70_LEAF = 'ac360b5e120fa0f5ba97deee2e1ee68ef6d415fd9e856710c5104efdd8ea551f'
NVA_T72A_LEAF = '30fdb038e061492f404c19340d7b0e1da08746c49abd8f57faf46fb7264d38a2'
NVA_BRDM2_LEAF = 'bbe74f54370ac1a41343d200b834335650508e87632449c8781d5bae490371e4'
UNITS_VEHICLES = {
    '1C1038CF07EA4B00': (
        'Prefabs/Vehicles/Wheeled/BTR70/NVA/BTR70_NVA.et',
        ('C012BB3488BEA0C2', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70.et'),
        (NVA_BTR70_LEAF,), (NVA_BTR70_LEAF,),
    ),
    '83FC98FFD5D48825': (
        'Prefabs/Vehicles/Tracked/T-72A/T72A_NVA.et',
        ('8A883ABED22B6E27', 'Prefabs/Vehicles/Core/Tracked_Base.et'),
        (NVA_T72A_LEAF, '44d169d2260f456352926fd245aa50f787083ee2a43a23eb76ebed0b61e0e38d'),
        (NVA_T72A_LEAF, '2ec90305fa151423829093e490029d2a994c655b6b70b47ef1291b2b1207674d'),
    ),
    'EC4811AC7C17DC7E': (
        'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2_NVA.et',
        ('254289B9C09904AB', 'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2.et'),
        (NVA_BRDM2_LEAF,), (NVA_BRDM2_LEAF,),
    ),
}
WORKSHOP_VEHICLE_PARENTS = {
    '8A883ABED22B6E27': ('WCS_SpaceCore_5E389BB9F58B79A6', 'Prefabs/Vehicles/Core/Tracked_Base.et'),
}
REVIEWED_CREW_CONFIGS = {
    '012709863EB73BED': ('Configs/Crews/GC_NVA/Crew_NVA_AI.conf',
                         '90846172c477653d708d41e39d6a2be4d94607803653f0fdd8c16ef18746c712'),
    '5657389604450AAC': ('Configs/Crews/US/Crew_US_AI.conf',
                         '0e8d90c8331a50c54f78ceafc20774f8c5169b601b1c6b01ff3074a9cd47d066'),
    'E56AA08ED9F1D4B5': ('Configs/Crews/GC_CDF/Rifleman_CDF.conf',
                         'c954cc5a6819f9f240ae42226a560d165dd60e573a828c1ae4a6f1a1f74d0610'),
}
NVA_CREW_ROLE_PATHS = {
    '72BF67D118BC5930': ('Driver', 'CrewDriver'),
    '9594C031304B509D': ('Gunner', 'CrewGunner'),
    '945EE8F65FF4E94C': ('Commander', 'CrewCO'),
}


def workshop_vehicles(workshop, resources=WORKSHOP_VEHICLES):
    result = {}
    if not workshop:
        return result
    databases = {}
    for resource, (provider, path) in resources.items():
        if provider not in databases:
            registry = workshop / provider / 'resourceDatabase.rdb'
            databases[provider] = StockPrefabs(registry) if registry.is_file() else None
        if (database := databases[provider]) and database.contains(resource, path):
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


def crew_route_fields(data, default_delay):
    waypoints = target_names(data['m_waypointNames']) if data.get('m_waypointNames') else []
    if not waypoints:
        return []
    delay = data.get('m_waypointDelay', default_delay)
    if not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400:
        raise ValueError('unsupported placed crew waypoint delay')
    return ['   m_fCrewWaypointDelay ' + delay, '   m_aCrewWaypoints {',
            *('    ' + name for name in waypoints), '   }']


def crew_config_contents(lines, resolve_config=None):
    field = r'm_crewConfig TILW_CrewConfig ' + SERIALIZED_ID
    reference = re.fullmatch(field + r' : ' + REF.pattern + r' {', lines[0].strip())
    if not reference:
        return contents(lines, field)
    if not resolve_config or len(lines) != 2 or lines[1].strip() != '}':
        raise ValueError('referenced crew config has placement overrides')
    return resolve_config(reference[1].upper(), reference[2])


def render_component(component, lines, resolve_character, single_turret, resolve_config=None):
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
        # A standalone legacy component deserializes an omitted list as an allocated empty array.
        default_crew = not data.get('m_customCrew')
        roles = [] if default_crew else crew_roles(data['m_customCrew'], flags, resolve_character)
        route = crew_route_fields(data, '5')
        if seated not in ('0', '1') or idle not in ('0', '1') or data.get('m_pregameSpawn', '0') != '0':
            raise ValueError('unsupported placed crew timing or gunner settings')
        separate = idle == '1' and flags['Turret'] == '1'
        options = []
        if default_crew:
            if any(flag not in ('0', '1') for flag in flags.values()):
                raise ValueError('unsupported default crew seat filter')
            options.append('   m_bUseDefaultCrew 1')
        if separate and not (single_turret and len(roles) == 1 and not route):
            options.append('   m_bShareIdleGunnerGroup 1')
        return crew_fields(component, flags, seated, roles, [*options, *route])
    if len(configs) != 1:
        raise ValueError('ambiguous placed crew configuration')
    index, = configs
    close = block_end(lines, index)
    outer = properties(lines[:index] + lines[close + 1:], {'m_noTurretDismount', 'm_pregameSpawn'})
    if outer.get('m_pregameSpawn', '0') != '0' or outer.get('m_noTurretDismount', '0') not in ('0', '1'):
        raise ValueError('unsupported placed crew timing or deprecated dismount field')
    # The legacy configured-crew path never reads the component's deprecated dismount field.
    config = crew_config_contents(lines[index:close + 1], resolve_config)
    groups = [i for i, line in enumerate(config) if line.strip() == 'm_crewGroups {']
    if len(groups) != 1:
        raise ValueError('crew authoring needs one m_crewGroups')
    start = groups[0]
    end = block_end(config, start)
    options = properties(config[:start] + config[end + 1:], {'m_noTurretDismount', 'm_useIdleGroups'})
    seated = options.get('m_noTurretDismount', '0')
    idle = options.get('m_useIdleGroups', '1')
    if seated not in ('0', '1') or idle not in ('0', '1'):
        raise ValueError('invalid placed crew options')
    group = contents(config[start + 1:end], 'TILW_CrewGroup ' + SERIALIZED_ID)
    stations = [i for i, line in enumerate(group) if line.strip() == 'm_crewStations {']
    if len(stations) != 1:
        raise ValueError('crew authoring needs one m_crewStations')
    start, = stations
    end = block_end(group, start)
    group_options = properties(group[:start] + group[end + 1:], {'m_waypointNames', 'm_waypointDelay'})
    route = crew_route_fields(group_options, '0')
    flags, roles = crew_station(group[start + 1:end], resolve_character, allow_empty=True)
    if route:
        if idle == '1':
            route.insert(0, '   m_bSeparateOrderedGunners 1')
        route.insert(0, '   m_bAssignInitialCrewOrders 1')
    return crew_fields(component, flags, seated, roles, route)


def crew_fields(component, flags, seated, roles, options=()):
    characters = ['   m_aCharacters {', *('    ' + role for role in roles), '   }'] if roles else []
    return [f'  GC_VehicleCrewComponent {component["id"]} {{',
            *(f'   m_bAllow{seat} 0' for seat in SEAT_TYPES if flags[seat] == '0'),
            *(['   m_bKeepGunnersSeated 1'] if seated == '1' else []),
            *options, *characters, '  }']


def verified_btr70_overlay(guid, path, source_units, dev_units):
    if guid != BTR70_CREW_OVERLAY or source_units.resources.get(guid) != [path] or dev_units.resources.get(guid) != [path]:
        return False
    before = [sha256(body.strip().encode()).hexdigest() for body in inheritance(source_units, guid)]
    after = [sha256(body.strip().encode()).hexdigest() for body in inheritance(dev_units, guid)]
    return before == [BTR70_CREW_LEAF] and after == [BTR70_CREW_LEAF, BTR70_SMOKE_ANCESTOR]


def verified_units_vehicle(guid, path, source_units, dev_units, stock, owned, external_parents):
    spec = UNITS_VEHICLES.get(guid)
    if (not spec or path != spec[0]
            or source_units.resources.get(guid) != [path] or dev_units.resources.get(guid) != [path]):
        return False
    _, parent, source_hashes, dev_hashes = spec
    if not (stock.contains(*parent) or external_parents.get(parent[0]) == parent[1]) or any(
            parent[0] in resources for resources in (owned, source_units.resources, dev_units.resources)):
        return False
    before = tuple(sha256(body.strip().encode()).hexdigest() for body in inheritance(source_units, guid))
    after = tuple(sha256(body.strip().encode()).hexdigest() for body in inheritance(dev_units, guid))
    return before == source_hashes and after == dev_hashes


def verified_crew_config(source_units, guid, path, owned):
    spec = REVIEWED_CREW_CONFIGS.get(guid)
    if not spec or guid in owned or path != spec[0] or source_units.resources.get(guid) != [path]:
        raise ValueError('unverified referenced crew config ' + guid)
    body = source_units.read(path).strip()
    if sha256(body.encode()).hexdigest() != spec[1]:
        raise ValueError('referenced crew config differs from the published body ' + guid)
    if guid == '012709863EB73BED':
        prefix = 'Prefabs/Characters/Factions/OPFOR/GC_NVA/Character_GC_NVA_'
        for role, (legacy, canonical) in NVA_CREW_ROLE_PATHS.items():
            registered = prefix + canonical + '.et'
            if source_units.resources.get(role) != [registered]:
                raise ValueError('unverified published crew role ' + role)
            body = body.replace(f'"{{{role}}}{prefix}{legacy}.et"', f'"{{{role}}}{registered}"')
    return contents(body.splitlines(), 'TILW_CrewConfig')


def convert(block, source_units, dev_units, stock, owned, external_vehicles=None, external_ai=None, *,
            external_parents=None):
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
    units_vehicle = vehicle and units_owned and verified_units_vehicle(
        guid, path, source_units, dev_units, stock, owned, external_parents or {})
    if (guid in owned or (units_owned and not (inherited_override or vehicle_overlay or units_vehicle))
            or not (stock.contains(guid, path) or external_vehicle or units_vehicle)):
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
            single_turret, lambda guid, path: verified_crew_config(source_units, guid, path, owned))
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
