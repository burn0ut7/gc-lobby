"""Assembly recipes for Arc missions with a standalone faction capture."""
import re
from decimal import Decimal

from boundaries import convert_boundaries
from briefings import block_end, guid, properties, QUOTED
from catalog import REF
from industrial import outcome_mode
from groups import group_layer
from launchers import launcher
from markers import convert_layer
from scenery import StockPrefabs

RECIPES = {
    'ForeignExchangeStudents': {
        'world': '0a102de61ea639b9ec5e070426ab77e100c693dc',
        'default.layer': 'bfe7703eb4110838c80e3ff38bcb31d097098e4e',
        'briefing.layer': 'b511399bb04ed8125df0c80ab185ead235d4da5e',
        'vehicles.layer': '76c7f394a20f03ca02c51470bfcd92d71aa685d9',
    },
    'EFPStragglers': {
        'world': '57b3ed345d3cc642ebeb20bdc5eec4731792f8c9',
        'default.layer': '537fec8355d2c74f192e1aa7c04fd179df5c1c1c',
        'briefing.layer': '6c1c18039d31c03ba6bb7ac0473927b7f69bd415',
        'vehicles.layer': '7b7061505cd162c98408ff93605549ab43da3736',
    },
    'SoumontSawmill': {
        'world': '4c0008aff8edc1a7621767d0bdd996585bd07bf3',
        'default.layer': 'c0b0ce31159c5cb6252b8d02afdf242502852367',
        'briefing.layer': 'd5852ab3914efb3e2f1e8b6be3afc890bc85c06b',
        'vehicles.layer': 'd21cad2103183917fa5c0db95569a997bf2fa20b',
    },
    'WateryGrave': {
        'world': 'c69a1a6413d4e2ef2c218b62f35eba72947189b9',
        'default.layer': 'b11df9718ca7386670689a1c7e77f770d6a802c2',
        'Briefing.layer': '8cbbc5883aa53f98d2aefab42107ebe81871ac93',
        'Vehicles.layer': 'ca3b79c9ed517db9df5c44b2b3407a6a9fa2a2ed',
    },
}
WORLDS = {f'worlds/arc/{name}/{name}.ent': name for name in RECIPES}
PROVIDERS = {
    '447CFE8D73F95C3E': 'RHS-StatusQuo_595F2BF2F44836FB',
    '62F304402A707F68': 'ACETacticalLadderDev_65AD7CB89F219E38',
    '7BC76F92B882889D': 'TacticalFlava-TIGR_DEADC0DE00000003',
    'FA6820A9A52CD05B': 'RHS-StatusQuo_595F2BF2F44836FB',
}
STOCK_US_ROLES = {
    '094747C4F8BE9F9F': ('E398E44759DA1A43', 'TL'),
    '547BA14003A67C04': ('C9E4FEAF5AAC8D8C', 'Medic'),
    '64AD55FF89C97DF9': ('26A9756790131354', 'Rifleman'),
    '7E44CF4FB58F8FF7': ('5B1996C05B1E51A4', 'AR'),
    'A47EB499090D69B9': ('84029128FA6F6BB9', 'GL'),
    'C262DC827577226C': ('27BF1FF235DD6036', 'LAT'),
    'CA2EB3A73FABC625': ('6058AB54781A0C52', 'AMG'),
    'DBC177C261C93B9D': ('E45F1E163F5CA080', 'SL'),
    'EB2904CE98753A6E': ('1623EA3AEFACA0E4', 'MG'),
}
LONGMOOR_LEGACY_PARENT = '{2E5C56A432F72F88}Worlds/Longmoor Extended/Longmoor Extended.ent'
LONGMOOR_CURRENT_PARENT = '{E90C30D273FF302E}Worlds/LongmoorTrainingArea/LongmoorTrainingArea.ent'


def capture(source, world):
    layer = world.removesuffix('.ent') + '_Layers/default.layer'
    if source.files.get(layer) != RECIPES[WORLDS[world]]['default.layer']:
        raise ValueError('Arc capture source changed: ' + layer)
    lines = source.read(layer).splitlines()
    index = lines.index('TILW_FactionTriggerEntity : "{8C576D46F6579229}Prefabs/Logic/Triggers/TILW_FactionTrigger.et" {')
    data = properties(lines[index + 1:block_end(lines, index)], {
        'coords', 'm_queryRadius', 'm_captureTime', 'm_sendStatusMessages', 'm_locationName',
        'm_flagName', 'm_stopAfterFirstChange', 'm_playersOnly', 'm_ownerFactionKey', 'm_ratioThreshold',
    })
    players_only = data.get('m_playersOnly', '0')
    stops = data.get('m_stopAfterFirstChange', '0')
    if data.get('m_sendStatusMessages') != '1' or players_only not in ('0', '1') or stops not in ('0', '1'):
        raise ValueError('Arc capture options changed')
    location = data.get('m_locationName', '"the location"')
    ratio = data.get('m_ratioThreshold', '1')
    if not re.fullmatch(QUOTED, location) or not Decimal(0) <= Decimal(ratio) <= Decimal(1):
        raise ValueError('Arc capture location name changed')
    key = world + '/capture/' + data['m_flagName']
    who = '' if players_only == '1' else '        m_iWho 2\n'
    stop = '        m_bStopAfterFirstChange 1\n' if stops == '1' else ''
    condition = f'''      GC_AreaCondition "{{{guid(key)}}}" {{
       m_sArea "GC_ArcCapture"
       m_Targets GC_CharacterAreaTargets "{{{guid(key + '/players')}}}" {{
{who}        m_aFactions {{
         {data['m_ownerFactionKey']}
        }}
       }}
       m_Requirement GC_LocalFactionShare "{{{guid(key + '/share')}}}" {{
        m_fRequiredShare {ratio}
       }}
       m_Capture GC_AreaCaptureOptions "{{{guid(key + '/timer')}}}" {{
        m_fSeconds {data['m_captureTime']}
        m_bUseElapsedTime 1
        m_iInterruption 3
{stop}        m_Messages GC_CaptureMessages "{{{guid(key + '/messages')}}}" {{
         m_sLocation {location}
         m_sCaptured "The %1 has taken control of %2."
         m_sCapturing "The %1 is taking control of %2."
         m_sCaptureInterrupted "The %1 is no longer taking control of %2."
         m_sLost "The %1 has lost control of %2."
         m_sLosing "The %1 is losing control of %2."
         m_sLossInterrupted "The %1 is no longer losing control of %2."
        }}
       }}
      }}'''
    area = f'''GC_RadiusAreaEntity GC_ArcCapture : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
 coords {data['coords']}
 m_fRadius {data['m_queryRadius']}
}}
'''
    audit = {
        'faction': data['m_ownerFactionKey'][1:-1],
        'location': location[1:-1],
        'seconds': int(data['m_captureTime']),
        'radiusMeters': int(data['m_queryRadius']),
        'requiredLocalShare': float(ratio) if ratio != '1' else 1,
        'timing': 'elapsed mission seconds',
        'population': 'players' if players_only == '1' else 'players and AI',
        'stopsAfterFirstChange': stops == '1',
    }
    return {data['m_flagName']: condition.splitlines()}, area, audit


def foreign_roster(source, source_units, dev_units, stock, locally_owned, path):
    if source.files.get(path) != '8e4ccdaf6f0a8ff7e84c15be167e432a379da4b6':
        raise ValueError('Foreign Exchange Students roster source changed')
    root = 'Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_'
    stock_members = {}

    def relocate(match):
        resource, reference = match.groups()
        if resource not in STOCK_US_ROLES:
            return match[0]
        parent, role = STOCK_US_ROLES[resource]
        stock_path = root + role + '.et'
        if reference != root + role + '_P.et' or not stock.contains(parent, stock_path):
            raise ValueError('Foreign Exchange Students stock role changed: ' + reference)
        stock_members[parent] = stock_path
        return f'"{{{parent}}}{stock_path}"'

    text = REF.sub(relocate, source.read(path))
    if len(stock_members) != len(STOCK_US_ROLES):
        raise ValueError('Foreign Exchange Students role conversions changed')
    return group_layer(text, source_units, dev_units, stock, locally_owned, reviewed_members=stock_members)


def assemble(source, dev, stock, workshop, world, row, staged, rules, area):
    name = WORLDS[world]
    folder = world.removesuffix('.ent') + '_Layers/'
    for relative, oid in RECIPES[name].items():
        path = world if relative == 'world' else folder + relative
        if source.files.get(path) != oid:
            raise ValueError(name + ' source changed: ' + path)

    header = 'Missions/arc/' + name + '.conf'
    result, _, files = launcher(source, dev, header)
    if result != world:
        raise ValueError(name + ' launcher resolves to a different world')
    files[world] = source.read(world)
    rosters = row.get('groupLayers', {})
    slots = 46 if name == 'ForeignExchangeStudents' else 60
    if sum(info.get('unitPrefabEntries', 0) for info in rosters.values()) != slots or any(path not in staged for path in rosters):
        raise ValueError(f'{name} needs the complete {slots}-slot roster')
    files.update((path, staged[path]) for path in rosters)
    legacy_vehicle_groups = {}
    for part in RECIPES[name]:
        path = folder + part
        if part.lower() == 'vehicles.layer':
            files[path] = source.read(path)
            for resource, ref_path in set(REF.findall(files[path])):
                database = stock
                if resource in PROVIDERS:
                    if not workshop:
                        raise ValueError(name + ' needs the installed Workshop vehicle databases')
                    database = StockPrefabs(workshop / PROVIDERS[resource] / 'resourceDatabase.rdb')
                if not database.contains(resource, ref_path):
                    raise ValueError(name + ' vehicle dependency changed: ' + ref_path)
            if name in ('WateryGrave', 'EFPStragglers'):
                vehicle_name = ''
                kept = []
                for line in files[path].splitlines(keepends=True):
                    placement = re.match(r'^(?:Vehicle ([\w.-]+) :| (BTR70_AFRF\d+) \{)', line)
                    if placement:
                        vehicle_name = placement[1] or placement[2]
                    group = re.fullmatch(r'\s*m_sAttachmentGroupName "([\w.-]+)"\s*', line)
                    if group:
                        if not vehicle_name or vehicle_name in legacy_vehicle_groups:
                            raise ValueError(name + ' has an ambiguous vehicle attachment')
                        legacy_vehicle_groups[vehicle_name] = group[1]
                    else:
                        kept.append(line)
                if len(legacy_vehicle_groups) != (7 if name == 'WateryGrave' else 1):
                    raise ValueError(name + ' vehicle attachments changed')
                files[path] = ''.join(kept)
        elif part.lower() == 'briefing.layer':
            text = source.read(path)
            files[path], icons = convert_layer(text[text.index('$grp PS_ManualMarker '):])

    default = folder + 'default.layer'
    boundary_path = folder + 'default_GC_Boundaries.layer'
    text = source.read(default)
    files[boundary_path], audit = convert_boundaries(text, allow_public_faction_color=True)
    boundaries = {'EFPStragglers': 1, 'ForeignExchangeStudents': 0}.get(name, 2)
    if len(audit) != boundaries or any('gap' in entry for entry in audit.values()):
        raise ValueError(name + ' AO polygons need review')
    if not boundaries:
        del files[boundary_path]
    children = ''
    terrain = None
    environment = ''
    if name == 'EFPStragglers':
        terrain = StockPrefabs(workshop / 'Ruha_653CB36244ADBE0F' / 'resourceDatabase.rdb')
        text = text.replace('m_bUseSpecifiedDate 1', 'm_bDateOverride 1\n   m_iDay 1')
        text = text.replace('m_iSetMonth ', 'm_iMonth ').replace('m_iSetYear ', 'm_iYear ')
        values = {}
        for old, new in {'FogDensity': 'FogAmount', 'FogHeight': 'FogHeightDensity',
                         'Rain': 'RainIntensity', 'Wind': 'WindSpeed'}.items():
            field = re.search(r'^   m_f' + old + r' ([\d.]+)\n', text, re.M)
            values[new] = field[1]
            text = text.replace(field[0], '', 1)
        text = text.replace('   m_bOverrideEnvironment 1\n', '', 1)
        fields = '\n'.join(f'   m_f{key} {value}' for key, value in values.items())
        environment = f'''  GC_MissionEnvironmentComponent "{{{guid(world + '/environment')}}}" {{
{fields}
  }}
'''
    if name == 'WateryGrave':
        lines = text.splitlines()
        start = lines.index('    SCR_Faction "{5978B9CE6585BBE8}" {')
        faction = '\n'.join(lines[start:block_end(lines, start) + 1])
        children = f''' {{
  SCR_FactionManager {{
   ID "6ADC9BB66394BE46"
   Factions + {{
{faction}
   }}
  }}
 }}
'''
        files[header] = files[header].replace('m_iPlayerCount 45', 'm_iPlayerCount 60')
    if name == 'ForeignExchangeStudents':
        files[header] = files[header].replace('(24-45)', '(24-46)').replace('m_iPlayerCount 45', 'm_iPlayerCount 46')
        files[world] = files[world].replace(LONGMOOR_LEGACY_PARENT, LONGMOOR_CURRENT_PARENT)
        lines = text.splitlines()
        # The current terrain includes its own AI world, map and weather manager.
        # The GC Lobby prefab supplies mission perception.
        mode_start = next(i for i, line in enumerate(lines) if line.startswith('PS_GameModeCoop '))
        text = '\n'.join(lines[mode_start:]) + '\n'
        factions = []
        for identifier in ('56DEAC40D2DBC8B1', '61500924662B6062'):
            start = lines.index(f'    SCR_Faction "{{{identifier}}}" {{')
            factions.extend(lines[start:block_end(lines, start) + 1])
        faction_blocks = '\n'.join(factions)
        children = f''' {{
  SCR_FactionManager {{
   ID "6ADC9BB66394BE46"
   Factions + {{
{faction_blocks}
   }}
  }}
 }}
'''
    body = outcome_mode(world, 'GC_' + name + 'Mode', row['briefingModePreset'], text, stock, rules,
                        'GC_ArcStartingZone', children, terrain, environment,
                        include_map=name != 'ForeignExchangeStudents')
    files[default] = body.rstrip() + '\n' + area
    return files, audit, icons, legacy_vehicle_groups
