"""Rebuild Bug Out's extraction, reinforcement and flare events from reviewed layers."""
import re

from arc_capture import LONGMOOR_CURRENT_PARENT, LONGMOOR_LEGACY_PARENT
from briefings import block_end, guid, properties
from catalog import REF
from extraction import condition as extraction_condition
from launchers import launcher
from markers import convert_layer
from scenery import StockPrefabs

WORLD = 'worlds/TheLocalPub/BugOut/BugOut.ent'
HEADER = 'Missions/TheLocalPub/BugOut.conf'
LAYERS = WORLD.removesuffix('.ent') + '_Layers/'
REVIEWED = {
    'default.layer': 'e885702e38c55db1d1736b2ac827a5668361553a',
    'BLUFOR/BLUFOR_MARKERS.layer': '52d74eacd78e5a6de29d5bf5e86f4c8e7629b6b7',
    'BLUFOR/BLUFOR_PLAYERS.layer': '0766528b12530ac5cb983ca4599d4d90f8cfcd04',
    'BLUFOR/BLUFOR_VEHICLES.layer': 'd1db7e18f644c82102df71a063d962b5e433ad60',
    'OPFOR/AI.layer': '96c35cdd5721b9322541918e67469990b2d0af6a',
    'OPFOR/WP.layer': '0e4e383c0df821efb0ef1b7c37d4387d904a655f',
    'PROPS/Loose.layer': '54397725b2135e15e79a14b91aaf3a077df80b85',
    'PROPS/Town.layer': 'bece0297343b19e3516f59b1e0d9b02dd2055478',
    'SYSTEMS/Effects.layer': '844d98052d33d066ddd1abbcc49607ed1e0aa3a9',
    'SYSTEMS/Triggers.layer': '4acf6a4a6a74b91aaf3a0890f94cc6e59cdde7ab',
}
UNCHANGED = ('BLUFOR/BLUFOR_VEHICLES.layer', 'OPFOR/WP.layer', 'PROPS/Loose.layer',
             'PROPS/Town.layer', 'SYSTEMS/Effects.layer')


def container(kind, key, fields):
    return f'{kind} "{{{guid(WORLD + "/" + key)}}}" {{\n' + indent(fields) + '}\n'


def indent(text):
    return ''.join(' ' + line + '\n' for line in text.splitlines())


def array(name, body):
    return f'{name} {{\n' + indent(body) + '}\n'


def instances(text, kind, fields):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith('$grp ' + kind + ' :'):
            continue
        stop = block_end(lines, index)
        index += 1
        while index < stop:
            match = re.fullmatch(r' ([\w_]+) \{', lines[index])
            if not match:
                raise ValueError('unexpected Bug Out placement: ' + lines[index])
            end = block_end(lines, index)
            yield match[1], properties(lines[index + 1:end], fields)
            index = end + 1


def event(name, actions, condition='', delay='0'):
    fields = f'm_sName "{name}"\n'
    if condition:
        fields += array('m_aConditions', condition)
    if float(delay):
        fields += 'm_Advanced ' + container('GC_EventOptions', name + '/delay', f'm_fDelay {delay}\n')
    fields += array('m_aActions', actions)
    return container('GC_Event', name, fields)


def source_event(name, trigger):
    return container('GC_MissionEventCondition', name + '/check', f'm_sEventName {trigger}\n')


def spawn(name, prefab, location, entity_name='', waypoints=''):
    fields = f'm_sPrefab {prefab}\nm_sLocation "{location}"\n'
    if entity_name:
        fields += f'm_sEntityName {entity_name}\n'
    kind = 'GC_PrefabSpawn'
    if waypoints:
        kind = 'GC_AIGroupSpawn'
        fields += array('m_aWaypoints', waypoints)
    return container('GC_SpawnAction', name + '/action', 'm_Spawn ' + container(kind, name + '/prefab', fields))


def delete(name, entity):
    return container('GC_DeleteEntitiesAction', name + '/delete', array('m_aEntities', entity))


def triggers(text):
    fields = {'coords', 'm_queryRadius', 'm_captureTime', 'm_flagName', 'm_stopAfterFirstChange',
              'm_factionKey', 'm_ownerFactionKey', 'm_ratioThreshold', 'm_ignoredFactions'}
    rules, areas, audit = [], [], {}
    for kind in ('TILW_ExtractionTriggerEntity', 'TILW_FactionTriggerEntity'):
        extraction = kind == 'TILW_ExtractionTriggerEntity'
        for entity, data in instances(text, kind, fields):
            if data.get('m_stopAfterFirstChange') != '1':
                raise ValueError('Bug Out trigger must latch: ' + entity)
            name = data['m_flagName'][1:-1]
            faction = data['m_factionKey' if extraction else 'm_ownerFactionKey']
            if extraction:
                condition = extraction_condition(WORLD, name, entity, faction, data['m_ratioThreshold'], data.get('m_captureTime'))
            else:
                requirement = f'm_fRequiredShare {data["m_ratioThreshold"]}\n'
                if data.get('m_ignoredFactions'):
                    requirement += array('m_aIgnoredFactions', data['m_ignoredFactions'])
                targets = array('m_aFactions', faction)
                targets += 'm_iWho 2\n'
                fields = f'm_sArea "{entity}"\n'
                fields += 'm_Targets ' + container('GC_CharacterAreaTargets', name + '/targets', targets)
                fields += 'm_Requirement ' + container('GC_LocalFactionShare', name + '/share', requirement)
                condition = container('GC_AreaCondition', name + '/area', fields)
            action = container('GC_MessageAction', name + '/log', f'm_iDelivery 2\nm_sMessage "{name}"\n')
            rules.append(event(name, action, condition))
            areas.append(f'GC_RadiusAreaEntity {entity} : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{\n'
                         f' coords {data["coords"]}\n m_fRadius {data["m_queryRadius"]}\n}}\n')
            audit[name] = {
                'faction': faction[1:-1], 'radiusMeters': int(data['m_queryRadius']),
                'seconds': float(data.get('m_captureTime', '0')), 'share': float(data['m_ratioThreshold']),
                'population': 'living players across the mission' if extraction else 'local living players and AI',
                'ignoredFactions': re.findall(r'"([^"]+)"', data.get('m_ignoredFactions', '')),
            }
    if set(audit) != {'UK_Extracted', 'UKArrived', 'UK_left', 'UK_left2'}:
        raise ValueError('Bug Out requires all four reviewed triggers')
    return ''.join(rules), ''.join(areas), audit


def reinforcements(text, units):
    fields = {'coords', 'angles', 'm_prefab', 'm_conditionFlag', 'm_setEntityNames', 'm_spawnTimings', 'm_waypointNames'}
    rules, locations, audit = [], [], {}
    for name, data in instances(text, 'TILW_PrefabSpawnerEntity', fields):
        source = REF.fullmatch(data['m_prefab'])
        paths = units.resources.get(source[1], [])
        if len(paths) != 1:
            raise ValueError('Bug Out reinforcement prefab missing: ' + source[2])
        prefab = f'"{{{source[1]}}}{paths[0]}"'
        locations.append(f'GenericEntity {name}_Spawn {{\n coords {data["coords"]}\n angles {data["angles"]}\n}}\n')
        delay = data.get('m_spawnTimings', '0')
        trigger = data.get('m_conditionFlag', '')
        condition = source_event(name, trigger) if trigger else ''
        action = spawn(name, prefab, name + '_Spawn', data.get('m_setEntityNames', ''), data['m_waypointNames'])
        rules.append(event(name, action, condition, delay))
        audit[name] = {'delaySeconds': int(delay), 'requires': trigger.strip('"'),
                       'waypoints': re.findall(r'"([^"]+)"', data['m_waypointNames'])}
    if len(audit) != 8:
        raise ValueError('Bug Out must retain eight authored reinforcement spawners')
    return ''.join(rules), ''.join(locations), audit


def effects_and_outcomes(text, units):
    lines = text.splitlines()
    rules, audit = [], []
    for i, line in enumerate(lines):
        if not line.startswith('  TILW_MissionEvent '):
            continue
        name = lines[i + 1].strip().removeprefix('m_name ').strip('"')
        if name == 'Effects_DisableFlareEvent':
            continue
        stop = block_end(lines, i)
        for j in range(i + 2, stop):
            if not re.match(r'^    TILW_\w+Instruction ', lines[j]):
                continue
            data = properties(lines[j + 1:block_end(lines, j)], {
                'm_executionDelay', 'm_prefab', 'm_locationName', 'm_setEntityName', 'm_entityNames',
                'm_messageTitle', 'm_messageBody',
            })
            if name == 'Effects_Flares':
                delay = data['m_executionDelay']
                child = name + ' (' + delay + 's)'
                if 'm_prefab' in data:
                    resource, path = REF.fullmatch(data['m_prefab']).groups()
                    if units.resources.get(resource) != [path]:
                        raise ValueError('Bug Out flare prefab missing')
                    action = spawn(child, data['m_prefab'], data['m_locationName'][1:-1], data['m_setEntityName'])
                else:
                    action = delete(child, data['m_entityNames'])
                rules.append(event(child, action, delay=delay))
                audit.append(int(delay))
            elif name in ('EC_UKWin', 'EC_UKLose'):
                condition = (source_event(name, '"UK_Extracted"') if name == 'EC_UKWin' else
                             container('GC_CustomScriptCondition', name + '/check', 'm_Script ' +
                                       container('GC_PeakFactionPopulationCheck', name + '/peak', 'm_sFaction "UK"\nm_fRatio 0.8\n')))
                action = container('GC_MessageAction', name + '/message',
                                   'm_sTitle ' + data['m_messageTitle'] + '\nm_sMessage ' + data['m_messageBody'] + '\n')
                rules.append(event(name, action, condition))
            else:
                raise ValueError('unexpected Bug Out source event: ' + name)
    if len(audit) != 10 or len(rules) != 12:
        raise ValueError('Bug Out flare or outcome count changed')
    # Legacy queues every flare instruction together at Match start. Deactivation
    # only affects events that have not occurred; queued instructions still run.
    rules.append(event('Effects_RemoveFlare', delete('Effects_RemoveFlare', '"RedFlare"'), source_event('Effects_RemoveFlare', '"UK_left"')))
    return ''.join(rules), audit


def assemble(source, dev, units, stock, workshop, row, staged):
    w, _, files = launcher(source, dev, HEADER)
    if w != WORLD or source.files[WORLD] != '0a102de61ea639b9ec5e070426ab77e100c693dc':
        raise ValueError('Bug Out world changed')
    for layer, oid in REVIEWED.items():
        if source.files.get(LAYERS + layer) != oid:
            raise ValueError('Bug Out layer changed: ' + layer)
    files[WORLD] = source.read(WORLD).replace(LONGMOOR_LEGACY_PARENT, LONGMOOR_CURRENT_PARENT)
    british = StockPrefabs(workshop / 'BritishForces_5AE50EC5B8D6F4AE/resourceDatabase.rdb')
    for layer in UNCHANGED:
        body = source.read(LAYERS + layer)
        for resource in REF.findall(body):
            if not stock.contains(*resource) and not british.contains(*resource):
                raise ValueError('Bug Out scene resource missing: ' + resource[1])
        files[LAYERS + layer] = body
    roster = LAYERS + 'BLUFOR/BLUFOR_PLAYERS.layer'
    if row.get('groupLayers', {}).get(roster, {}).get('unitPrefabEntries') != 28:
        raise ValueError('Bug Out requires all 28 British slots')
    files[roster] = staged[roster]
    markers = LAYERS + 'BLUFOR/BLUFOR_MARKERS.layer'
    files[markers], icons = convert_layer(source.read(markers))
    area_rules, files[LAYERS + 'SYSTEMS/Triggers.layer'], capture_audit = triggers(source.read(LAYERS + 'SYSTEMS/Triggers.layer'))
    ai_rules, files[LAYERS + 'OPFOR/AI.layer'], ai_audit = reinforcements(source.read(LAYERS + 'OPFOR/AI.layer'), units)
    text = source.read(LAYERS + 'default.layer')
    effect_rules, flare_audit = effects_and_outcomes(text, units)
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('PS_GameModeCoop '))
    weather = '\n'.join(lines[start + 2:block_end(lines, start + 1)])
    for old, new in (('5EE3229927D4D2F5', '6B71E9A8B1A8C501'), ('m_bUseSpecifiedDate', 'm_bDateOverride'),
                     ('m_iSetDay', 'm_iDay'), ('m_iSetMonth', 'm_iMonth'), ('m_iSetYear', 'm_iYear')):
        weather = weather.replace(old, new)
    coords = re.search(r'^ coords (.+)$', '\n'.join(lines[start:block_end(lines, start)]), re.M)[1]
    mode = f'GC_LobbyGameMode GC_BugOutMode : "{{{guid(WORLD + "/resource")}}}{row["briefingModePreset"]}" {{\n'
    mode += ' components {\n' + weather + '\n'
    mode += '  GC_PeakFactionPopulationComponent "{' + guid(WORLD + '/population') + '}" {\n  }\n'
    mode += '  GC_LobbyManager "{6A137A39106A4973}" {\n   m_aMissionEvents + {\n'
    for rule_line in (area_rules + ai_rules + effect_rules).splitlines():
        mode += '    ' + rule_line + '\n'
    files[LAYERS + 'default.layer'] = mode + f'   }}\n  }}\n }}\n coords {coords}\n}}\n'
    return files, {'icons': icons, 'triggers': capture_audit, 'reinforcements': ai_audit, 'flareDelaysSeconds': flare_audit}
