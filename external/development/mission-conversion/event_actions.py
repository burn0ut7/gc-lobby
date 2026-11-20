"""Translate legacy actions with an existing GC Lobby equivalent."""
from decimal import Decimal
import re

from briefings import guid, properties
from catalog import REF

INSTRUCTION = re.compile(r'^    (TILW_\w+Instruction) "\{([\da-fA-F]{16})\}" \{$')
NUMBER = r'\d+(?:\.\d+)?'
CLOCK_FIELDS = ('m_iHours', 'm_iMinutes', 'm_iSeconds')
FACTION_VISIBILITY = re.compile(r'TILW_FactionVisibility "\{[\da-fA-F]{16}\}" \{([^{}]*)\}')
SPAWN = re.compile(r'    TILW_SpawnPrefabInstruction "\{([\da-fA-F]{16})\}" \{')


def target_names(value):
    names = value.split()
    if not 1 <= len(names) <= 128 or not all(re.fullmatch(r'"[\w.-]+"', name) for name in names):
        raise ValueError('unsupported entity name list')
    return names


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


def set_flag(data, _key):
    flag = data.get('m_flagName', '')
    if not re.fullmatch(r'"[\w.-]+"', flag):
        raise ValueError('invalid mission flag name')
    return ['       m_sFlagName ' + flag, '       m_iOperation 1', '       m_iAmount 1'], {
        'setsFlag': flag[1:-1],
    }


def render_spawn(details, key):
    body = [
        f'      GC_SpawnAction "{{{guid(key)}}}" {{',
        f'       m_Spawn GC_PrefabSpawn "{{{guid(key + "/spawn")}}}" {{',
        f'        m_sLocation "{details["anchor"]}"',
        f'        m_sPrefab {details["prefab"]}',
    ]
    if details['area']:
        body.append(f'        m_sEntityName "{details["area"]}"')
    return Decimal(details['delay']), [*body, '       }', '      }']


def prefab_spawn(lines, key, resolve_resource):
    data = properties(lines, {'m_executionDelay', 'm_prefab', 'm_locationName', 'm_setEntityName'})
    ref = REF.fullmatch(data.get('m_prefab', ''))
    name = data.get('m_setEntityName', '""')
    anchor = data.get('m_locationName', '')
    delay = data.get('m_executionDelay', '5')
    if (not ref or not re.fullmatch(r'"[\w.-]+"', anchor)
            or not re.fullmatch(r'"[\w.-]*"', name) or not re.fullmatch(NUMBER, delay) or Decimal(delay) > 86400):
        raise ValueError('unsupported prefab spawn placement or delay')
    path = resolve_resource(ref[1].upper(), ref[2])
    if not path:
        raise ValueError('spawn prefab identity or behavior needs review: ' + ref[2])
    details = {'anchor': anchor[1:-1], 'area': name[1:-1],
               'prefab': f'"{{{ref[1]}}}{path}"', 'delay': delay}
    seconds, body = render_spawn(details, key)
    return seconds, body, {'instruction': 'TILW_SpawnPrefabInstruction', 'delaySeconds': delay,
                          'spawnAnchor': details['anchor'], 'spawnName': details['area'], 'prefab': details['prefab']}


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
    'TILW_EditMapItemInstruction': (
        'GC_MapMarkerVisibilityAction', edit_map_items, {'m_itemNames', 'm_factionVisibility', 'm_visibleForEmpty'},
    ),
    'TILW_SetFlagInstruction': ('GC_FlagAction', set_flag, {'m_flagName'}),
}


def recognized(line):
    match = INSTRUCTION.fullmatch(line)
    return match if match and match[1] in ACTIONS else None


def compile_action(match, lines, event_key):
    legacy, instruction = match.groups()
    native, convert, allowed = ACTIONS[legacy]
    data = properties(lines, allowed | {'m_executionDelay'})
    delay = data.get('m_executionDelay', '5')
    if not re.fullmatch(NUMBER, delay) or Decimal(delay) > 86400:
        raise ValueError('instruction delay outside native range')
    key = event_key + '/' + instruction
    fields, details = convert(data, key)
    identity = guid(key)
    return Decimal(delay), [f'      {native} "{{{identity}}}" {{', *fields, '      }'], {
        'instruction': legacy, 'instructionId': instruction, 'delaySeconds': delay, **details,
    }
