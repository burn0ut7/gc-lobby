"""Translate legacy actions with an existing GC Lobby equivalent."""
from decimal import Decimal
from functools import partial
import re

from briefings import block_end, guid, properties, target_names
from catalog import REF
from vehicle_crew import SEAT_TYPES, SERIALIZED_ID, crew_roles, render_crew_config, split_crew_block

INSTRUCTION = re.compile(r'^    (TILW_\w+Instruction) "\{([\da-fA-F]{16})\}" \{$')
NUMBER = r'\d+(?:\.\d+)?'
FLAG_NAME = r'"[\w.-]+(?: +[\w.-]+)*"'
CLOCK_FIELDS = ('m_iHours', 'm_iMinutes', 'm_iSeconds')
FACTION_VISIBILITY = re.compile(r'TILW_FactionVisibility "\{[\da-fA-F]{16}\}" \{([^{}]*)\}')
SPAWN = re.compile(r'    TILW_SpawnPrefabInstruction "\{([\da-fA-F]{16})\}" \{')
GROUP_SPAWN = re.compile(r'    TILW_SpawnGroupInstruction "\{([\da-fA-F]{16})\}" \{')
VEHICLE_SPAWN = re.compile(r'    TILW_SpawnVehicleInstruction "\{([\da-fA-F]{16})\}" \{')
SPAWN_FIELDS = {'m_executionDelay', 'm_prefab', 'm_locationName', 'm_setEntityName'}
SPAWN_NAME = r'"(?:[\w.-]+(?: +[\w.-]+)*)?"'
DEFAULT_CREW_PILOTS = {
    ('C012BB3488BEA0C2', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70.et'),
    ('B47110AA1A806556', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_FIA.et'),
}


def entity_fields(names):
    return ['       m_aEntities {', *(f'        {name}' for name in names), '       }']


def delete_entities(data, _key):
    names = target_names(data.get('m_entityNames', ''))
    if '"GC_MigratedMission"' in names:
        raise ValueError('cannot delete the destination game mode')
    return entity_fields(names), {'entities': [name[1:-1] for name in names]}


def service_vehicles(data, _key):
    names = target_names(data.get('m_aVehicleNames', ''))
    fuel, supplies, repair = (data.get(field, '0') for field in ('m_bAdjustFuel', 'm_bAdjustSupplies', 'm_bRepair'))
    if any(value not in ('0', '1') for value in (fuel, supplies, repair)):
        raise ValueError('invalid vehicle servicing options')
    if fuel == '1':
        # The legacy code requires a fuel manager on the root before visiting attached tanks.
        raise ValueError('vehicle fuel requires a verified root fuel manager')
    if supplies == repair == '0':
        raise ValueError('vehicle service has no enabled operation')
    amount = data.get('m_iAmountSupplies', '0')
    if supplies == '1' and (not re.fullmatch(r'\d+', amount) or int(amount) > 1000000):
        raise ValueError('invalid vehicle supply amount')
    lines = entity_fields(names)
    details = {'entities': [name[1:-1] for name in names], 'repair': repair == '1'}
    if supplies == '1':
        lines.extend(['       m_bChangeSupplies 1', f'       m_iSuppliesAmount {amount}'])
        details['supplies'] = int(amount)
    if repair == '1':
        lines.append('       m_bRepair 1')
    return lines, details


def change_time(data, _key):
    clock = [data.get(field, default) for field, default in zip(CLOCK_FIELDS, ('8', '0', '0'))]
    if any(not re.fullmatch(r'\d+', value) or int(value) > maximum for value, maximum in zip(clock, (23, 59, 59))):
        raise ValueError('invalid 24-hour clock value')
    fields = [f'       {field} {value}' for field, value in zip(CLOCK_FIELDS, clock)]
    return ['       m_bChangeWeather 0', '       m_bChangeTime 1', *fields], {'clock': list(map(int, clock))}


def change_weather(data, _key):
    preset = data.get('m_sWeatherPresetName', '')
    natural = data.get('m_bAllowRandomChanges', '0')
    if not re.fullmatch(r'"[\w .-]+"', preset) or not preset[1:-1].strip() or natural not in ('0', '1'):
        raise ValueError('unsupported weather preset or progression')
    return [f'       m_sWeatherPreset {preset}', f'       m_bAllowNaturalChanges {natural}'], {
        'weather': preset[1:-1], 'allowNaturalChanges': natural == '1',
    }


def edit_respawn_tickets(data, _key):
    faction = data.get('m_factionKey', '')
    operation = {'SET': 0, 'ADD': 1, '0': 0, '1': 1}.get(data.get('m_operation', 'SET'))
    amount = data.get('m_amount', '0')
    retry = data.get('m_allowSpecRespawn', '0')
    if (not re.fullmatch(r'"[\w.-]+"', faction) or operation is None
            or not re.fullmatch(r'\d+', amount) or int(amount) > 1000000 or retry not in ('0', '1')):
        raise ValueError('unsupported respawn ticket operation')
    fields = [f'       m_sFaction {faction}', f'       m_iOperation {operation}', f'       m_iAmount {amount}']
    if retry == '1':
        fields.append('       m_bRetryCasualties 1')
    return fields, {'ticketFaction': faction[1:-1], 'ticketAmount': int(amount),
                    'ticketOperation': operation, 'retryCasualties': retry == '1'}


def edit_map_items(data, key):
    names = target_names(data.get('m_itemNames', ''))
    unassigned = data.get('m_visibleForEmpty', '0')
    serialized = data.get('m_factionVisibility', '')
    factions = {}
    if unassigned not in ('0', '1') or FACTION_VISIBILITY.sub('', serialized).strip():
        raise ValueError('unsupported marker audience edit')
    for entry in FACTION_VISIBILITY.finditer(serialized):
        values = re.fullmatch(r'\s*m_factionKey ("[\w.-]+")(?: m_visible ([01]))?\s*', entry[1])
        if not values or values[1] in factions:
            raise ValueError('unsupported or duplicate marker faction edit')
        factions[values[1]] = values[2] == '1'
    if len(factions) > 16:
        raise ValueError('too many marker faction edits')
    fields = ['       m_aMarkers {', *(f'        {name}' for name in names), '       }',
              f'       m_bVisibleWithoutFaction {unassigned}']
    if factions:
        fields.append('       m_aFactions {')
        for faction, visible in factions.items():
            identity = guid(key + '/marker audience/' + faction)
            fields.extend([f'        GC_MarkerFactionVisibility "{{{identity}}}" {{',
                           f'         m_sFaction {faction}', f'         m_bVisible {int(visible)}', '        }'])
        fields.append('       }')
    return fields, {'markers': [name[1:-1] for name in names], 'visibleWithoutFaction': unassigned == '1',
                    'factions': {faction[1:-1]: value for faction, value in factions.items()}}


def change_flag(data, _key, *, clear=False):
    flag = data.get('m_flagName', '')
    if not re.fullmatch(FLAG_NAME, flag):
        raise ValueError('invalid mission flag name')
    fields = ['       m_sFlagName ' + flag]
    fields.extend(['       m_iOperation 4'] if clear else ['       m_iOperation 1', '       m_iAmount 1'])
    return fields, {('clearsFlag' if clear else 'setsFlag'): flag[1:-1]}


def render_spawn(details, key, spawn_type='GC_PrefabSpawn', fields=()):
    body = [
        f'      GC_SpawnAction "{{{guid(key)}}}" {{',
        f'       m_Spawn {spawn_type} "{{{guid(key + "/spawn")}}}" {{',
        f'        m_sLocation "{details["anchor"]}"',
        f'        m_sPrefab {details["prefab"]}',
    ]
    if details['area']:
        body.append(f'        m_sEntityName "{details["area"]}"')
    return Decimal(details['delay']), [*body, *fields, '       }', '      }']


def spawn_details(data, resolve_resource):
    ref = REF.fullmatch(data.get('m_prefab', ''))
    name = data.get('m_setEntityName', '""')
    anchor = data.get('m_locationName', '')
    delay = data.get('m_executionDelay', '5')
    if (not ref or not re.fullmatch(r'"[\w.-]+"', anchor)
            or not re.fullmatch(SPAWN_NAME, name) or len(name.encode()) > 130
            or not re.fullmatch(NUMBER, delay) or Decimal(delay) > 86400):
        raise ValueError('unsupported prefab spawn placement or delay')
    path = resolve_resource(ref[1].upper(), ref[2])
    if not path:
        raise ValueError('spawn prefab identity or behavior needs review: ' + ref[2])
    return {'anchor': anchor[1:-1], 'area': name[1:-1],
            'prefab': f'"{{{ref[1]}}}{path}"', 'delay': delay}


def prefab_spawn(lines, key, resolve_resource, group=False):
    allowed = SPAWN_FIELDS | {'m_waypointNames'} if group else SPAWN_FIELDS
    data = properties(lines, allowed)
    details = spawn_details(data, resolve_resource)
    fields = []
    waypoints = target_names(data['m_waypointNames'], skip_blank=True) if data.get('m_waypointNames') else []
    if waypoints:
        fields = ['        m_aWaypoints {', *('         ' + name for name in waypoints), '        }']
    kind = 'GC_AIGroupSpawn' if group else 'GC_PrefabSpawn'
    instruction = 'TILW_SpawnGroupInstruction' if group else 'TILW_SpawnPrefabInstruction'
    seconds, body = render_spawn(details, key, kind, fields)
    audit = {'instruction': instruction, 'delaySeconds': details['delay'],
                          'spawnAnchor': details['anchor'], 'spawnName': details['area'], 'prefab': details['prefab']}
    if group:
        audit['groupWaypoints'] = [name[1:-1] for name in waypoints]
    return seconds, body, audit


def vehicle_spawn(lines, key, resolve_resource, resolve_character):
    kind = 'GC_CrewedVehicleSpawn'
    if any(line.strip().startswith('m_crewConfig ') for line in lines):
        config, spawn = split_crew_block(lines, 'm_crewConfig TILW_CrewConfig ' + SERIALIZED_ID)
        data = properties(spawn, SPAWN_FIELDS)
        fields, groups = render_crew_config(config, key, resolve_character)
        crew = {'crewGroups': groups, 'crewWaypoints': [point for group in groups for point in group['waypoints']]}
    else:
        data = properties(lines, SPAWN_FIELDS | {
            'm_customCrew', 'm_waypointNames', 'm_waypointDelay', 'm_idleGroup',
            *('m_spawn' + position for position in SEAT_TYPES),
        })
        if all(data.get('m_spawn' + position) == '0' for position in SEAT_TYPES):
            # The deprecated event instruction passes an empty seat filter, so
            # AddCrew never selects an occupant or obtains a group to route.
            kind, fields, crew = 'GC_PrefabSpawn', [], {'crew': [], 'crewDisabled': True}
        else:
            fields, crew = render_legacy_crew(data, key, resolve_character)
    ref = REF.fullmatch(data.get('m_prefab', ''))
    if not ref or not ref[2].startswith('Prefabs/Vehicles/'):
        raise ValueError('crewed spawn requires a vehicle prefab')
    details = spawn_details(data, resolve_resource)
    seconds, body = render_spawn(details, key, kind, fields)
    return seconds, body, {'instruction': 'TILW_SpawnVehicleInstruction', 'delaySeconds': details['delay'],
                          'spawnAnchor': details['anchor'], 'spawnName': details['area'], 'prefab': details['prefab'],
                          **crew}


def render_legacy_crew(data, key, resolve_character):
    flags = {position: data.get('m_spawn' + position, '1') for position in SEAT_TYPES}
    if any(flag not in ('0', '1') for flag in flags.values()) or all(flag == '0' for flag in flags.values()):
        raise ValueError('crew migration requires qualifying seats')
    authored = data.get('m_customCrew', '')
    if not authored:
        ref = REF.fullmatch(data.get('m_prefab', ''))
        if not ref or (ref[1].upper(), ref[2]) not in DEFAULT_CREW_PILOTS or flags['Pilot'] != '1':
            raise ValueError('default crew requires a verified prefab with a selected pilot')
    roles = crew_roles(authored, flags, resolve_character, allow_empty=True) if authored else []
    idle = data.get('m_idleGroup', '0')
    waypoint_delay = data.get('m_waypointDelay', '5')
    waypoints = target_names(data['m_waypointNames'], skip_blank=True) if data.get('m_waypointNames') else []
    if (idle not in ('0', '1')
            or (waypoints and (not re.fullmatch(NUMBER, waypoint_delay) or Decimal(waypoint_delay) > 86400))):
        raise ValueError('unsupported crew order delay or configuration')
    flags_native = zip(SEAT_TYPES, ('Driver', 'Gunners', 'Passengers'))
    fields = ['        m_aCrewGroups {', f'         GC_SpawnCrewGroup "{{{guid(key + "/crew")}}}" {{',
              *(f'          m_b{native} 0' for legacy, native in flags_native if flags[legacy] == '0')]
    if roles:
        fields.extend(['          m_aCharacters {', *('           ' + role for role in roles), '          }'])
    if idle == '1' and flags['Turret'] == '1':
        fields.append('          m_bShareIdleGunnerGroup 1')
    if waypoints:
        if Decimal(waypoint_delay):
            fields.append('          m_fWaypointDelay ' + waypoint_delay)
        fields.extend(['          m_aWaypoints {', *('           ' + name for name in waypoints), '          }'])
    fields.extend(['         }', '        }'])
    return fields, {'crew': roles, 'crewWaypoints': [waypoint[1:-1] for waypoint in waypoints],
                    'crewWaypointDelaySeconds': waypoint_delay if waypoints else '0',
                    **({'crewDefaults': True} if not authored else {})}


ACTIONS = {
    'TILW_DeleteEntitiesInstruction': ('GC_DeleteEntitiesAction', delete_entities, {'m_entityNames'}),
    'TILW_ServiceVehiclesInstruction': (
        'GC_VehicleAction', service_vehicles,
        {'m_aVehicleNames', 'm_bAdjustFuel', 'm_fFuelRatio', 'm_bAdjustSupplies', 'm_iAmountSupplies', 'm_bRepair'},
    ),
    'TILW_ChangeTimeInstruction': ('GC_WeatherAction', change_time, set(CLOCK_FIELDS)),
    'TILW_ChangeWeatherInstruction': (
        'GC_WeatherAction', change_weather, {'m_sWeatherPresetName', 'm_bAllowRandomChanges'},
    ),
    'TILW_EditRespawnTicketsInstruction': (
        'GC_RespawnTicketsAction', edit_respawn_tickets, {'m_factionKey', 'm_operation', 'm_amount', 'm_allowSpecRespawn'},
    ),
    'TILW_EditMapItemInstruction': (
        'GC_MapMarkerVisibilityAction', edit_map_items, {'m_itemNames', 'm_factionVisibility', 'm_visibleForEmpty'},
    ),
    'TILW_SetFlagInstruction': ('GC_FlagAction', change_flag, {'m_flagName'}),
    'TILW_ClearFlagInstruction': ('GC_FlagAction', partial(change_flag, clear=True), {'m_flagName'}),
}


def recognized(line):
    match = INSTRUCTION.fullmatch(line)
    return match if match and match[1] in ACTIONS else None


def compile_action(match, lines, event_key, hidden_briefings=None):
    legacy, instruction = match.groups()
    native, convert, allowed = ACTIONS[legacy]
    data = properties(lines, allowed | {'m_executionDelay'})
    delay = data.get('m_executionDelay', '5')
    if not re.fullmatch(NUMBER, delay) or Decimal(delay) > 86400:
        raise ValueError('instruction delay outside native range')
    key = event_key + '/' + instruction
    fields, details = convert(data, key)
    identity = guid(key)
    briefing_orders = []
    actions = []
    if legacy == 'TILW_EditMapItemInstruction' and hidden_briefings:
        names = details['markers']
        reveal = [name for name in names if name in hidden_briefings]
        if reveal:
            factions = details['factions']
            unassigned = details['visibleWithoutFaction']
            if not all(factions.values()) or not (factions or unassigned):
                raise ValueError('unsupported hidden briefing audience edit')
            names = [name for name in names if name not in hidden_briefings]
            for name in reveal:
                target = key + '/briefing/' + name
                order = hidden_briefings[name]
                briefing_orders.append(order)
                actions.extend([f'      GC_CustomScriptAction "{{{guid(target)}}}" {{',
                                f'       m_Script GC_RevealBriefingScript "{{{guid(target + "/script")}}}" {{',
                                f'        m_iBriefingOrder {order}'])
                if factions:
                    actions.extend(['        m_aFactions {', *(f'         "{faction}"' for faction in factions),
                                    '        }'])
                if unassigned:
                    actions.append('        m_bVisibleWithoutFaction 1')
                actions.extend(['       }', '      }'])
            if names:
                fields, details = convert({**data, 'm_itemNames': ' '.join(f'"{name}"' for name in names)}, key)
            else:
                fields = []
                details = {**details, 'markers': []}
    if fields:
        actions.extend([f'      {native} "{{{identity}}}" {{', *fields, '      }'])
    if briefing_orders:
        details['briefingOrders'] = briefing_orders
    return Decimal(delay), actions, {
        'instruction': legacy, 'instructionId': instruction, 'delaySeconds': delay, **details,
    }
