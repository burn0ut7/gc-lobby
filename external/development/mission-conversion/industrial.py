"""Assemble the reviewed Industrial Metal world with compiled outcome rules."""
import re

from boundaries import convert_boundaries
from briefings import block_end, guid
from catalog import REF
from launchers import launcher
from markers import convert_layer
from scenery import StockPrefabs

WORLD = 'worlds/arc/IndustrialMetal/IndustrialMetal.ent'
HEADER = 'Missions/arc/IndustrialMetal.conf'
LAYERS = WORLD.removesuffix('.ent') + '_Layers/'
DEFAULT = LAYERS + 'default.layer'
BOUNDARIES = LAYERS + 'default_GC_Boundaries.layer'
REVIEWED = {
    WORLD: '4c0008aff8edc1a7621767d0bdd996585bd07bf3',
    DEFAULT: 'a2aef4a65f959d5a44f482a92491b5abb94d1ffa',
    LAYERS + 'briefing.layer': '79b7494652465d81ff30597bc08ab9885b2911ff',
    LAYERS + 'vehicles.layer': '1fe0f4994509007ffc0e2243e1bb539fbbb823e0',
}


def assemble(source, dev, stock, workshop, preset, rules, staged):
    for path, oid in REVIEWED.items():
        if source.files.get(path) != oid:
            raise ValueError('Industrial Metal source changed: ' + path)
    world, _, files = launcher(source, dev, HEADER)
    if world != WORLD:
        raise ValueError('Industrial Metal launcher resolves to a different world')
    files[world] = source.read(world)
    for layer in ('players_cdf.layer', 'players_ru.layer'):
        path = LAYERS + layer
        files[path] = staged[path]
    vehicles = LAYERS + 'vehicles.layer'
    files[vehicles] = source.read(vehicles)
    providers = {
        '6B7CD408BDFB94F5': 'TacticalFlava-TIGR_DEADC0DE00000003',
        'A61E5C16E9A2161A': 'RHS-StatusQuo_595F2BF2F44836FB',
    }
    for resource, path in REF.findall(files[vehicles]):
        database = StockPrefabs(workshop / providers[resource] / 'resourceDatabase.rdb')
        if not database.contains(resource, path):
            raise ValueError('Industrial Metal vehicle dependency changed: ' + path)
    briefing = LAYERS + 'briefing.layer'
    text = source.read(briefing)
    files[briefing], count = convert_layer(text[text.index('$grp PS_ManualMarker '):])
    if count != 2:
        raise ValueError('Industrial Metal marker count changed')

    text = source.read(DEFAULT)
    files[BOUNDARIES], review = convert_boundaries(text, allow_public_faction_color=True)
    if len(review) != 2 or any(value.get('vertices') != 4 for value in review.values()):
        raise ValueError('Industrial Metal boundaries need review')
    files[DEFAULT] = outcome_mode(WORLD, 'GC_IndustrialMode', preset, text, stock, rules, 'GC_IndustrialStartingZone')
    return files, review


def outcome_mode(world, name, preset, text, stock, rules, zone_name, children='', terrain=None, environment='',
                 include_map=True):
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('PS_GameModeCoop '))
    prelude = '\n'.join(lines[:start])
    for resource, path in REF.findall(prelude):
        if not stock.contains(resource, path) and not (terrain and terrain.contains(resource, path)):
            raise ValueError('default prefab changed: ' + path)
    weather = '\n'.join(lines[start + 2:block_end(lines, start + 1)]).replace('5EE3229927D4D2F5', '6B71E9A8B1A8C501')
    old_mode = '\n'.join(lines[start:block_end(lines, start)])
    coords = re.search(r'^ coords (.+)$', old_mode, re.M)[1]
    millis = int(re.search(r'^ m_iFreezeTime (\d+)$', old_mode, re.M)[1])
    if millis % 1000:
        raise ValueError('staging time needs whole seconds')
    seconds = millis // 1000
    zones, names = [], []
    for i, line in enumerate(lines):
        grouped = re.fullmatch(r'\$grp (SCR_EditorRestrictionZoneEntity) : ("[^"]+") \{', line)
        if grouped:
            instance = i + 1
            stop = block_end(lines, i)
            while instance < stop:
                if lines[instance] != ' {':
                    raise ValueError('unexpected grouped restriction-zone declaration')
                end = block_end(lines, instance)
                label = zone_name if not names else zone_name + str(len(names) + 1)
                names.append(label)
                fields = '\n'.join(field[1:] for field in lines[instance + 1:end])
                zones.append(f'{grouped[1]} {label} : {grouped[2]} {{\n{fields}\n}}\n')
                instance = end + 1
            continue
        if not line.startswith('SCR_EditorRestrictionZoneEntity :'):
            continue
        label = zone_name if not names else zone_name + str(len(names) + 1)
        names.append(label)
        end = block_end(lines, i)
        zone = '\n'.join(lines[i:end + 1]).replace('SCR_EditorRestrictionZoneEntity :',
                                                    f'SCR_EditorRestrictionZoneEntity {label} :', 1)
        if text.endswith('\n') or end < len(lines) - 1:
            zone += '\n'
        zones.append(zone)
    if not names:
        raise ValueError('missing restriction zone')
    zones = ''.join(zones)
    for resource, path in REF.findall(zones):
        if not stock.contains(resource, path):
            raise ValueError('restriction zone prefab changed: ' + path)
    key = world + '/release'
    zone_names = '\n'.join(f'        "{label}"' for label in names)
    event_name = 'Release Industrial Metal staging zone' if world == WORLD else 'Release staging zone'
    map_entity = f'''
SCR_MapEntity MapEntity1 : "{{731564B66F91B107}}Prefabs/World/Game/MapEntity.et" {{
 coords {coords}
}}
''' if include_map else ''
    return prelude + map_entity + f'''GC_LobbyGameMode {name} : "{{{guid(world + '/resource')}}}{preset}" {{
 components {{
{weather}
{environment}  SCR_PlayersRestrictionZoneManagerComponent "{{{guid(WORLD + '/zone-manager')}}}" {{
  }}
  GC_LobbyFreezeComponent "{{E81E75BC287148D0}}" {{
   m_iAuthoredCountdownSeconds {seconds}
  }}
  GC_PeakFactionPopulationComponent "{{{guid(world + '/population')}}}" {{
  }}
  GC_LobbyManager "{{6A137A39106A4973}}" {{
   m_aMissionEvents + {{
{rules}    GC_Event "{{{guid(key)}}}" {{
     m_sName "{event_name}"
     m_aConditions {{
      GC_TimerCondition "{{{guid(key + '/timer')}}}" {{
       m_fSeconds {seconds}
      }}
     }}
     m_aActions {{
      GC_DeleteEntitiesAction "{{{guid(key + '/delete')}}}" {{
       m_aEntities {{
{zone_names}
       }}
      }}
     }}
    }}
   }}
  }}
 }}
 coords {coords}
{children}}}
''' + zones
