"""Assembly recipes for Arc missions with faction captures."""
import re
from decimal import Decimal

from boundaries import convert_boundaries
from briefings import block_end, guid, properties, QUOTED
from catalog import REF
from extraction import condition as extraction_condition
from industrial import outcome_mode
from groups import group_layer
from launchers import launcher
from markers import convert_layer
from scenery import FIELD, NUMBER, StockPrefabs

RECIPES = {
    'FightingRetreat': {
        'world': 'c69a1a6413d4e2ef2c218b62f35eba72947189b9',
        'default.layer': '66aa9379c0287ab6c4abb1976e5583bfda789440',
        'briefing.layer': '034d31b09569d1cfeadff565e8d1e098ce674f74',
        'vehicles.layer': '34f072225b08fa532c8a47ecfaa9bf945e3bf159',
    },
    'ForeignExchangeStudentsII': {
        'world': '0a102de61ea639b9ec5e070426ab77e100c693dc',
        'default.layer': 'a0d1ba960261082dae380644b627871bf48132f7',
        'briefing.layer': 'a0de00d1969315279a27bb3a9dc58f6265f2cd9b',
        'props.layer': 'd24610e87f29aecc31352991cf6f08610e05ebf9',
    },
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
    '00AED68EFBF251BA': 'RHS-StatusQuo_595F2BF2F44836FB',
    '447CFE8D73F95C3E': 'RHS-StatusQuo_595F2BF2F44836FB',
    '62F304402A707F68': 'ACETacticalLadderDev_65AD7CB89F219E38',
    '7BC76F92B882889D': 'TacticalFlava-TIGR_DEADC0DE00000003',
    '8A08A0B2463F5137': 'ArmaTerrainCore_60B524F7A5AE96A8',
    'B0534A120633D08D': 'RHS-StatusQuo_595F2BF2F44836FB',
    'FA6820A9A52CD05B': 'RHS-StatusQuo_595F2BF2F44836FB',
}
STOCK_US_ROLES = {
    '094747C4F8BE9F9F': ('E398E44759DA1A43', 'TL'),
    '501777EB151D644B': ('4FBA24F7BB43E17D', 'Sergeant'),
    '547BA14003A67C04': ('C9E4FEAF5AAC8D8C', 'Medic'),
    '64AD55FF89C97DF9': ('26A9756790131354', 'Rifleman'),
    '7E44CF4FB58F8FF7': ('5B1996C05B1E51A4', 'AR'),
    'A47EB499090D69B9': ('84029128FA6F6BB9', 'GL'),
    'C262DC827577226C': ('27BF1FF235DD6036', 'LAT'),
    'CA2EB3A73FABC625': ('6058AB54781A0C52', 'AMG'),
    'D164278FD7D9C81A': ('0B3167BB0FB68110', 'PL'),
    'DBC177C261C93B9D': ('E45F1E163F5CA080', 'SL'),
    'EB2904CE98753A6E': ('1623EA3AEFACA0E4', 'MG'),
}
LONGMOOR_LEGACY_PARENT = '{2E5C56A432F72F88}Worlds/Longmoor Extended/Longmoor Extended.ent'
LONGMOOR_CURRENT_PARENT = '{E90C30D273FF302E}Worlds/LongmoorTrainingArea/LongmoorTrainingArea.ent'
FOREIGN_ROSTERS = {
    '8e4ccdaf6f0a8ff7e84c15be167e432a379da4b6': 9,
    'd959b465b819c949cf98e3647b6bd05c93758378': 9,
}
FOREIGN_ROLE_LAYERS = {('ForeignExchangeStudents', 'players.layer'), ('ForeignExchangeStudentsII', 'USplayers.layer')}
COVERMAP = 'worlds/TheLocalPub/TopMaloHouse/Covermap/'


def covermap_assets(source):
    reviewed = {
        'UCAMF_CoverMap-198_atlas.edds': 'e8dde44ba7bc8a2d94cdcfb6fa0bbf666c98e5b2',
        'UCAMF_CoverMap-198_atlas.edds.meta': '3e1cfcd1ac817167ddc26739ba81806ae723f542',
        'UCAMF_CoverMap-198_atlas.tga': '067882a9153592f7239c967d8ca23c13ff2b5298',
        'UCAMF_CoverMap.imageset': '5769ea8cdba356004e6e45e5c2edf4f1f7f5a71b',
        'UCAMF_CoverMap.imageset.meta': '43ed061c121d01e87b79b54ae5a824bf0c1ed905',
    }
    files = {}
    for name, oid in reviewed.items():
        path = 'Covermap/' + name
        if source.files.get(path) != oid:
            raise ValueError('legacy covermap asset changed: ' + path)
        contents = source.read_raw(path)
        if name.endswith(('.imageset', '.meta')):
            # Match the marker's authored path and the atlas metadata to their placement.
            contents = contents.replace(
                b'UI/Textures/Icons/UCAMF_CoverMap.imageset', (COVERMAP + 'UCAMF_CoverMap.imageset').encode(),
            )
            contents = contents.replace(b'}Covermap/', ('}' + COVERMAP).encode())
        files[COVERMAP + name] = contents
    return files


def capture(source, world):
    layer = world.removesuffix('.ent') + '_Layers/default.layer'
    if source.files.get(layer) != RECIPES[WORLDS[world]]['default.layer']:
        raise ValueError('Arc capture source changed: ' + layer)
    lines = source.read(layer).splitlines()
    if WORLDS[world] == 'FightingRetreat':
        trigger = 'TILW_ExtractionTriggerEntity : "{192ABD6B9232E064}Prefabs/Logic/Triggers/TILW_ExtractionTrigger.et" {'
        index = lines.index(trigger)
        data = properties(lines[index + 1:block_end(lines, index)], {
            'coords', 'm_queryRadius', 'm_flagName', 'm_stopAfterFirstChange', 'm_factionKey', 'm_ratioThreshold',
        })
        if data['m_stopAfterFirstChange'] != '1':
            raise ValueError('Fighting Retreat extraction must latch')
        flag = data['m_flagName']
        condition = extraction_condition(world, flag[1:-1], 'GC_ArcCapture', data['m_factionKey'], data['m_ratioThreshold'])
        area = f'''GC_RadiusAreaEntity GC_ArcCapture : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
 coords {data['coords']}
 m_fRadius {data['m_queryRadius']}
}}
'''
        audit = {'faction': data['m_factionKey'][1:-1], 'seconds': 0, 'radiusMeters': int(data['m_queryRadius']),
                 'requiredShare': float(data['m_ratioThreshold']), 'population': 'living players across the mission',
                 'stopsAfterFirstChange': True}
        return {flag: ['      ' + line for line in condition.splitlines()]}, area, audit
    trigger = 'TILW_FactionTriggerEntity : "{8C576D46F6579229}Prefabs/Logic/Triggers/TILW_FactionTrigger.et" {'
    if WORLDS[world] != 'ForeignExchangeStudentsII':
        index = lines.index(trigger)
        return capture_instance(world, 'GC_ArcCapture', lines[index + 1:block_end(lines, index)])

    start = lines.index('$grp ' + trigger.replace('Logic/Triggers/', 'Logic/'))
    end = block_end(lines, start)
    conditions, areas, audit = {}, [], {}
    index = start + 1
    while index < end:
        if lines[index] != ' {':
            raise ValueError('unexpected grouped Arc capture declaration')
        stop = block_end(lines, index)
        parsed, area, details = capture_instance(world, f'GC_ArcCapture{len(areas) + 1}', lines[index + 1:stop])
        conditions.update(parsed)
        audit.update({name[1:-1]: details for name in parsed})
        areas.append(area)
        index = stop + 1
    if len(areas) != 2 or set(audit) != {'US_CAP', 'UK_CAP'}:
        raise ValueError('Foreign Exchange Students II capture flags changed')
    return conditions, ''.join(areas), audit


CAPTURE_TRANSFORM = {'coords', 'angles', 'angleX', 'angleY', 'angleZ', 'scale'}
CAPTURE_EDITOR_FIELDS = {'m_drawShapeSurface', 'm_drawShapeOutline', 'm_setShapeColor'}
CAPTURE_TEXT = {
    'm_sCaptured': '"The %1 has taken control of %2."',
    'm_sCapturing': '"The %1 is taking control of %2."',
    'm_sCaptureInterrupted': '"The %1 is no longer taking control of %2."',
    'm_sLost': '"The %1 has lost control of %2."',
    'm_sLosing': '"The %1 is losing control of %2."',
    'm_sLossInterrupted': '"The %1 is no longer losing control of %2."',
}


def faction_keys(value):
    keys = value.split()
    if len(keys) > 256 or len(set(keys)) != len(keys) or any(not re.fullmatch(r'"[\w.-]+"', key) for key in keys):
        raise ValueError('invalid capture faction list')
    return keys


def capture_instance(world, area_name, lines, *, polygon=False):
    data = properties(lines, {
        'm_queryRadius', 'm_captureTime', 'm_sendStatusMessages', 'm_locationName',
        'm_flagName', 'm_stopAfterFirstChange', 'm_playersOnly', 'm_ownerFactionKey', 'm_ratioThreshold',
        'm_comparisonMode', 'm_ignoredFactions', 'm_displayTo', 'm_statusMessages',
        'm_skipFirstQuery', 'm_eventsOnlyDuringGame',
    } | CAPTURE_TRANSFORM | CAPTURE_EDITOR_FIELDS)
    players_only = data.get('m_playersOnly', '0')
    stops = data.get('m_stopAfterFirstChange', '0')
    status = data.get('m_sendStatusMessages', '0')
    skip = data.get('m_skipFirstQuery', '0')
    comparison = data.get('m_comparisonMode', 'EQUAL_OR_MORE')
    if (any(value not in ('0', '1') for value in (status, players_only, stops, skip))
            or comparison not in ('EQUAL_OR_MORE', 'EQUAL_OR_LESS', '1', '0')
            or data.get('m_eventsOnlyDuringGame', '1') != '1'):
        raise ValueError('unsupported capture options')
    at_most = comparison in ('EQUAL_OR_LESS', '0')
    location = data.get('m_locationName', '"the location"')
    owner = data['m_ownerFactionKey']
    ratio = data.get('m_ratioThreshold', '1')
    radius = data.get('m_queryRadius', '25')
    hold = data.get('m_captureTime', '0')
    transform = {key: value for key, value in data.items() if key in CAPTURE_TRANSFORM}
    if ((not polygon and 'coords' not in transform)
            or any(not FIELD.fullmatch(f'{key} {value}') for key, value in transform.items())
            or not re.fullmatch(r'"[\w.-]+"', owner)
            or any(not re.fullmatch(NUMBER, value) for value in (ratio, radius, hold))
            or not (0 <= Decimal(ratio) <= 1 and 0 <= Decimal(hold) <= 86400)
            or (not polygon and not Decimal('0.1') <= Decimal(radius) <= 100000)
            or not re.fullmatch(QUOTED, location) or len(location.encode()) - 2 > 80
            or '<' in location or '>' in location):
        raise ValueError('invalid capture location, threshold or timing')
    ignored = faction_keys(data.get('m_ignoredFactions', ''))
    recipients = faction_keys(data.get('m_displayTo', ''))
    if owner in ignored:
        raise ValueError('capturing faction cannot also be ignored')
    key = world + '/capture/' + data['m_flagName']
    who = '' if players_only == '1' else '        m_iWho 2\n'
    stop = '        m_bStopAfterFirstChange 1\n' if stops == '1' else ''
    discovery = '        m_bSkipDiscovery 1\n' if skip == '1' else ''
    share = '        m_bAtMost 1\n' if at_most else ''
    if ignored:
        share += '        m_aIgnoredFactions {\n         ' + ' '.join(ignored) + '\n        }\n'
    messages = ''
    if status == '1':
        texts = list(CAPTURE_TEXT.values())
        if 'm_statusMessages' in data:
            value = data['m_statusMessages']
            texts = re.findall(QUOTED, value)
            if (len(texts) != 6 or not re.fullmatch(rf'(?:{QUOTED}\s*)+', value)
                    or any(len(text.encode()) - 2 > 256 or '<' in text or '>' in text for text in texts)):
                raise ValueError('unsupported custom capture status messages')
        fields = ''.join(f'         {field} {text}\n' for field, text in zip(CAPTURE_TEXT, texts))
        audience = '         m_aFactions {\n          ' + ' '.join(recipients) + '\n         }\n' if recipients else ''
        messages = f'''        m_Messages GC_CaptureMessages "{{{guid(key + '/messages')}}}" {{
         m_sLocation {location}
{audience}{fields}        }}
'''
    condition = f'''      GC_AreaCondition "{{{guid(key)}}}" {{
       m_sArea "{area_name}"
       m_Targets GC_CharacterAreaTargets "{{{guid(key + '/players')}}}" {{
{who}        m_aFactions {{
         {owner}
        }}
       }}
       m_Requirement GC_LocalFactionShare "{{{guid(key + '/share')}}}" {{
        m_fRequiredShare {ratio}
{share}       }}
       m_Capture GC_AreaCaptureOptions "{{{guid(key + '/timer')}}}" {{
        m_fSeconds {hold}
        m_bUseElapsedTime 1
        m_iInterruption 3
{stop}{discovery}{messages}       }}
      }}'''
    area = ''
    geometry = {}
    if not polygon:
        position = ''.join(f' {field} {value}\n' for field, value in transform.items())
        area = f'''GC_RadiusAreaEntity {area_name} : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
{position} m_fRadius {radius}
}}
'''
        geometry['radiusMeters'] = float(radius) if '.' in radius or 'e' in radius.lower() else int(radius)
    audit = {
        'faction': owner[1:-1],
        'location': location[1:-1],
        'seconds': int(Decimal(hold)) if Decimal(hold) == Decimal(hold).to_integral_value() else float(hold),
        **geometry,
        'requiredLocalShare': float(ratio) if ratio != '1' else 1,
        'timing': 'elapsed mission seconds',
        'population': 'players' if players_only == '1' else 'players and AI',
        'stopsAfterFirstChange': stops == '1',
    }
    if at_most:
        audit['comparison'] = 'at most'
    if ignored:
        audit['ignoredFactions'] = [key[1:-1] for key in ignored]
    if recipients and status == '1':
        audit['statusRecipients'] = [key[1:-1] for key in recipients]
    if skip == '1':
        audit['skipDiscovery'] = True
    return {data['m_flagName']: condition.splitlines()}, area, audit


def foreign_roster(source, source_units, dev_units, stock, locally_owned, path):
    expected = FOREIGN_ROSTERS.get(source.files.get(path))
    if expected is None:
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
    if len(stock_members) != expected:
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
    slots = {'ForeignExchangeStudents': 46, 'ForeignExchangeStudentsII': 57, 'FightingRetreat': 72}.get(name, 60)
    if sum(info.get('unitPrefabEntries', 0) for info in rosters.values()) != slots or any(path not in staged for path in rosters):
        raise ValueError(f'{name} needs the complete {slots}-slot roster')
    files.update((path, staged[path]) for path in rosters)
    legacy_vehicle_groups = {}
    for part in RECIPES[name]:
        path = folder + part
        if part.lower() in ('vehicles.layer', 'props.layer'):
            files[path] = source.read(path)
            for resource, ref_path in set(REF.findall(files[path])):
                database = stock
                if resource in PROVIDERS:
                    if not workshop:
                        raise ValueError(name + ' needs the installed Workshop asset databases')
                    database = StockPrefabs(workshop / PROVIDERS[resource] / 'resourceDatabase.rdb')
                if not database.contains(resource, ref_path):
                    raise ValueError(name + ' asset dependency changed: ' + ref_path)
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
            files[path], icons = convert_layer(text[text.index('$grp PS_ManualMarker '):], dev)

    default = folder + 'default.layer'
    boundary_path = folder + 'default_GC_Boundaries.layer'
    text = source.read(default)
    files[boundary_path], audit = convert_boundaries(text, allow_public_faction_color=True)
    boundaries = {'EFPStragglers': 1, 'ForeignExchangeStudents': 0,
                  'ForeignExchangeStudentsII': 1, 'FightingRetreat': 1}.get(name, 2)
    if len(audit) != boundaries or any('gap' in entry for entry in audit.values()):
        raise ValueError(name + ' AO polygons need review')
    if not boundaries:
        del files[boundary_path]
    children = ''
    terrain = None
    environment = ''
    if name == 'FightingRetreat':
        terrain = StockPrefabs(workshop / 'Zarichne2025_65EA8C9C25882838' / 'resourceDatabase.rdb')
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
    faction_ids = {'WateryGrave': ('5978B9CE6585BBE8',),
                   'FightingRetreat': ('5978B9CE6585BBE8', '5CC8DE37E1FF0F7A')}
    if name == 'WateryGrave':
        files[header] = files[header].replace('m_iPlayerCount 45', 'm_iPlayerCount 60')
    longmoor = name in ('ForeignExchangeStudents', 'ForeignExchangeStudentsII')
    if longmoor:
        old_title, new_title = ('(24-45)', '(24-46)') if slots == 46 else ('(30-56)', '(30-57)')
        files[header] = files[header].replace(old_title, new_title).replace(f'm_iPlayerCount {slots - 1}', f'm_iPlayerCount {slots}')
        files[world] = files[world].replace(LONGMOOR_LEGACY_PARENT, LONGMOOR_CURRENT_PARENT)
        lines = text.splitlines()
        # The current terrain includes its own AI world, map and weather manager.
        # The GC Lobby prefab supplies mission perception.
        mode_start = next(i for i, line in enumerate(lines) if line.startswith('PS_GameModeCoop '))
        text = '\n'.join(lines[mode_start:]) + '\n'
        faction_ids[name] = ('56DEAC40D2DBC8B1', '61500924662B6062')
    if name in faction_ids:
        lines = text.splitlines()
        factions = []
        for identifier in faction_ids[name]:
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
                        include_map=not longmoor)
    files[default] = body.rstrip() + '\n' + area
    return files, audit, icons, legacy_vehicle_groups
