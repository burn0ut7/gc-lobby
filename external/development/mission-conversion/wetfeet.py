"""Convert WetFeet's reviewed capture and mutually deactivating victory events."""
from collections import defaultdict
import re

from boundaries import MAP_SHAPE, floats, validate_polygon
from briefings import block_end, guid, properties, QUOTED
from events import EVENT, LITERAL, MESSAGE, PEAK_FLAG, message
from catalog import REF
from groups import ROOT, group_layer, inheritance, only_path, scalar
from launchers import META_NAME, launcher, replace_reference
from markers import convert_layer as convert_markers
from scenery import StockPrefabs

WORLD = 'worlds/TheLocalPub/WetFeet/WetFeet.ent'
HEADER = 'Missions/TheLocalPub/WetFeet.conf'
LAYERS = WORLD.removesuffix('.ent') + '_Layers/'
DEFAULT = LAYERS + 'default.layer'
PRIOR_DEFAULT = '4d3e09baeec88adae8e2ebfc8c57a1ba0a17c6cf'
TRIGGER = LAYERS + 'SYSTEMS/MISC.layer'
PUBLIC_MARKERS = LAYERS + 'SYSTEMS/AllMarkers.layer'
MODE = LAYERS + 'default_GC_Mode.layer'
WIRE_GUID = '72F492BF47C08FA1'
DELETER = LAYERS + 'SYSTEMS/Deleter.layer'
SCENERY = {
    'OPFOR/Vehicles.layer': 'da454c3f1b4a6fd09fd5924303ba8ed0d250d5fe',
    'PROPS/BluSpawn.layer': 'd3bceb10dfe6f90410ab2ca2f6e83d359806f31e',
    'PROPS/Checkpoints.layer': '04059f0810bc585e84d75676f105723bd6512749',
    'PROPS/Hamlet.layer': '58b2772ed9711a6475d0e8533013814a45fcf798',
}
RENAMED = {
    '0CA0547EB03759E1': 'Prefabs/Vegetation/Bush/b_rubus_idaeus_1s.et',
    '124518D13290A687': 'Prefabs/Vegetation/Bush/b_salix_cinerea_0.et',
}
PROVIDERS = {
    'ArmaTerrainCore_60B524F7A5AE96A8': {'3872E0C19D19A045', 'FA8AAF819FBC2DFD', 'F39A28AB89986935'},
    'ACETrenchesDev_65AD7CE59E8DB349': {'19D504D46A48529A', '96876CEE4BD63503'},
    'BritishForces_5AE50EC5B8D6F4AE': {'E0CFA3FE78FC5190'},
    'RealisticCombatDrones_65AD60E204191D37': {'FDA92D590B9C633B', 'EF1E4AA6DCD2D982', '55C5BEE65087F7CD'},
    'Motorbike_61B84088181A8FA4': {'E6E5E1E5DC66DD8C'},
}
ROSTERS = {
    'BLUFOR': ('a4f6ddf862d56f56c453a8f51ea3d55742ba42d8', 31),
    'OPFOR': ('0a2607f45c6bc0da1a1f6323264bd6cf742ee42a', 25),
}
REVIEWED = {
    DEFAULT: 'eff30d4f5d74c183e1d0ed2ff93f1255a5ebb19c',
    TRIGGER: '2a15ac2fc36da9709bb5c73ed1331838588ebdf4',
}
DEACTIVATE = re.compile(r'^    TILW_DeactivateEventsInstruction "\{([\da-fA-F]{16})\}" \{$')
IDENTIFIER = r'"[\w.-]+"'


def assemble(source, dev, stock, generated, mode):
    if source.files.get(WORLD) != '57b3ed345d3cc642ebeb20bdc5eec4731792f8c9':
        raise ValueError('WetFeet terrain reference changed')
    world, _, files = launcher(source, dev, HEADER)
    if world != WORLD or source.files.get(HEADER) != '2edd8a4af1aad5d207c04d38b78835d06ffc7793':
        raise ValueError('WetFeet launcher changed')
    files[HEADER] = files[HEADER].replace('(25-57)', '(25-56)').replace('m_iPlayerCount 57', 'm_iPlayerCount 56')
    files[WORLD] = source.read(WORLD)
    default = source.read(DEFAULT).split('PS_GameModeCoop ', 1)[0]
    for resource, path in REF.findall(default):
        if path != 'Prefabs/AI/SCR_AIWorld_Ruha.et' and not stock.contains(resource, path):
            raise ValueError('WetFeet default-layer resource changed: ' + path)
    default += '''SCR_MapEntity MapEntity1 : "{731564B66F91B107}Prefabs/World/Game/MapEntity.et" {
 coords 395.79 66.44 5017.333
}
'''
    files[DEFAULT] = default + mode

    blufor = LAYERS + 'BLUFOR/Markers.layer'
    opfor = LAYERS + 'OPFOR/Markers.layer'
    public = PUBLIC_MARKERS
    for path, oid in [
        (blufor, 'dea404489e111d35c551bf8b56acd0cc0f05f191'),
        (opfor, '7f224976de1b1256b2f1326237d27374b0e74cfe'),
        (public, 'efa65e20c1b86353bc263ce96aa107acfc1c97e7'),
    ]:
        if source.files.get(path) != oid:
            raise ValueError('WetFeet source map markers changed: ' + path)
    lines = source.read(blufor).splitlines()
    shape_end = block_end(lines, 0)
    component_end = block_end(lines, 1)
    points = [floats(line.strip().removeprefix('Position '), 3) for line in lines[component_end:shape_end]
              if line.strip().startswith('Position ')]
    validate_polygon(points, floats(lines[component_end + 1].strip().removeprefix('coords '), 3))
    shape = f'''PolylineShapeEntity CaptureArea : "{MAP_SHAPE}" {{
 components {{
  GC_MapShapeComponent "{{8F470B543A20D962}}" {{
   m_LineColor 0.503 0 0 0.5
   m_fLineWidth 3
   m_bFillEnabled 1
   m_FillColor 0.503 0 0 0.3
   m_aFactionKeys {{
    "UK"
   }}
   m_bVisibleWithoutFaction 0
  }}
 }}
'''
    version = '\n'.join(lines[component_end + 1:shape_end + 1])
    icons, count = convert_markers('\n'.join(lines[shape_end + 1:]))
    files[blufor] = shape + version + '\n' + icons
    files[opfor], opfor_count = convert_markers(source.read(opfor))
    lines = source.read(public).splitlines()
    files[public], public_count = convert_markers('\n'.join(lines[block_end(lines, 0) + 1:]))
    if public.removesuffix('.layer') + '_GC_Boundaries.layer' not in generated:
        raise ValueError('WetFeet AO boundaries did not stage')
    if (count, opfor_count, public_count) != (1, 7, 1):
        raise ValueError('WetFeet icon count changed')
    return files, {'icons': count + opfor_count + public_count, 'captureOverlayPoints': len(points)}


def stage_scene(source, dev, stock, workshop):
    files, used_providers = {}, {}
    databases = {}
    for provider, guids in PROVIDERS.items():
        database = StockPrefabs(workshop / provider / 'resourceDatabase.rdb')
        databases.update((guid, (provider, database)) for guid in guids)
    wire, = source.resources[WIRE_GUID]
    if (source.files[wire] != '4e153e7e9444f2f138f15ec4f72e6d10d80b508a'
            or source.files[wire + '.meta'] != '9500757264673c7c9ab81e72173c64b6d398de92'
            or any(other != wire for other in dev.resources.get(WIRE_GUID, []))):
        raise ValueError('WetFeet reusable wire resource changed or conflicts with Dev')

    def resolve(match):
        resource, path = match.groups()
        path = RENAMED.get(resource, path)
        if resource in databases:
            provider, database = databases[resource]
            if not database.contains(resource, path):
                raise ValueError(f'WetFeet dependency moved in {provider}: {path}')
            used_providers.setdefault(provider, set()).add(resource)
        elif resource == WIRE_GUID:
            path = wire
        elif not stock.contains(resource, path):
            raise ValueError('WetFeet dependency could not be verified: ' + path)
        return f'"{{{resource}}}{path}"'

    files[wire] = REF.sub(resolve, source.read(wire))
    files[wire + '.meta'] = replace_reference(source.read(wire + '.meta'), META_NAME, WIRE_GUID, wire)
    for layer, oid in SCENERY.items():
        path = LAYERS + layer
        if source.files.get(path) != oid:
            raise ValueError('WetFeet scenery source changed: ' + path)
        files[path] = REF.sub(resolve, source.read(path))
    vehicles = LAYERS + 'OPFOR/Vehicles.layer'
    files[vehicles] = files[vehicles].replace(' m_sAttachmentGroupName "BRDMCrew"\n', '', 1)
    for side, (oid, _) in ROSTERS.items():
        path = LAYERS + side + '/Players.layer'
        if source.files.get(path) != oid:
            raise ValueError('WetFeet equipment source changed: ' + path)
        layer = source.read(path).splitlines()
        start = next(i for i, line in enumerate(layer) if re.match(r'^GenericEntity (?:Blufor|Opfor)EquipmentBox ', line))
        end = block_end(layer, start)
        files[LAYERS + side + '/Equipment.layer'] = REF.sub(resolve, '\n'.join(layer[start:end + 1]) + '\n')

    if source.files.get(DELETER) != '8f54e18796ca779770d0ae3d324d19ec76b561d7':
        raise ValueError('WetFeet legacy tree cleanup changed')
    raw = source.read(DELETER)
    trees = re.findall(r'  coords ([^\n]+)\n  m_fRadius (\d+)\n  m_prefabFilterList \{\n   ("[^"]+")\n  \}', raw)
    if len(trees) != 2:
        raise ValueError('expected two WetFeet tree cleanup placements')
    centers, actions, cleanup = [], [], []
    for index, (coords, radius, prefab) in enumerate(trees, 1):
        prefab = REF.sub(resolve, prefab)
        name = f'GC_WetFeetTree{index}'
        key = WORLD + '/tree/' + str(index)
        centers.append(f'GenericEntity {name} {{\n coords {coords}\n}}\n')
        actions.append(f'''      GC_DeleteEntitiesAction "{{{guid(key)}}}" {{
       m_Radius GC_RadiusDeletion "{{{guid(key + '/radius')}}}" {{
        m_sCenter "{name}"
        m_fRadius {radius}
        m_aWhitelist {{
         {prefab}
        }}
       }}
      }}''')
        cleanup.append({'name': name, 'coords': coords, 'radiusMeters': int(radius), 'prefab': prefab[1:-1]})
    files[DELETER] = ''.join(centers)
    bodies = '\n'.join(actions)
    rule = f'''    GC_Event "{{{guid(WORLD + '/tree-cleanup')}}}" {{
     m_sName "Clear WetFeet fortification trees"
     m_aActions {{
{bodies}
     }}
    }}'''
    return files, rule, {
        'layers': [path for path in files if path.endswith('.layer')],
        'providers': {provider: sorted(guids) for provider, guids in sorted(used_providers.items())},
        'updatedStockPaths': RENAMED,
        'legacyVehicleGroupPreview': {'vehicle': 'BRDM2_FIA1', 'group': 'BRDMCrew'},
        'cleanup': cleanup,
    }


def stage_rosters(source, source_units, dev_units, stock, locally_owned):
    files, audit = {}, {}
    for side, (oid, expected_slots) in ROSTERS.items():
        path = LAYERS + side + '/Players.layer'
        if source.files.get(path) != oid:
            raise ValueError('WetFeet roster source changed: ' + path)
        lines = source.read(path).splitlines()
        i = 0
        placements, equipment = [], []
        reviewed = {}
        while i < len(lines):
            end = block_end(lines, i)
            group = ROOT.fullmatch(lines[i])
            block = lines[i:end + 1]
            if not group:
                if not re.match(r'^GenericEntity (?:Blufor|Opfor)EquipmentBox ', lines[i]):
                    raise ValueError('WetFeet roster contains an unexpected entity')
                equipment.append('\n'.join(block))
                i = end + 1
                continue
            # The placed faction label is redundant with the same editable
            # component on the preserved native Separatist group parent.
            clean = []
            j = 0
            while j < len(block):
                if block[j].strip() == 'SCR_EditableGroupComponent "{50D291F6C83FA532}" {':
                    last = block_end(block, j)
                    label = [line.strip() for line in block[j:last + 1]]
                    parent = [line.strip() for line in inheritance(dev_units, group['guid'])[0].splitlines()]
                    offset = parent.index(label[0])
                    if parent[offset:offset + len(label)] != label:
                        raise ValueError('WetFeet faction label differs from the Dev group')
                    j = last + 1
                    continue
                clean.append(block[j])
                j += 1
            overrides = 'm_bSetPlayable 1' in (item.strip() for item in clean)
            if overrides or scalar(inheritance(source_units, group['guid']), 'm_bSetPlayable') == 1:
                for member, member_path in REF.findall('\n'.join(clean[1:])):
                    if not member_path.startswith('Prefabs/Characters/'):
                        continue
                    chain = inheritance(source_units, member)
                    terminal = REF.search(chain[-1].splitlines()[0]) if chain else None
                    expected_base = '542CBB6908CA71D8' if side == 'BLUFOR' else '37578B1666981FCE'
                    # British Forces' installed parent leads to Character_Base,
                    # where the legacy GC Lobby adds PS_PlayableComponent.
                    # The forced groups enable it on their spawned members.
                    if not terminal or terminal[1].upper() != expected_base:
                        raise ValueError('WetFeet character ancestry changed: ' + member)
                    reviewed[member] = only_path(dev_units, member)
            placements.extend(clean)
            i = end + 1
        result, groups, slots = group_layer('\n'.join(placements), source_units, dev_units, stock, locally_owned,
                                           reviewed_callsigns={'68E715598B5F3FB5', '68E7155981A0C53D'},
                                           reviewed_members=reviewed)
        if (groups, slots, len(equipment)) != (4, expected_slots, 1):
            raise ValueError('WetFeet roster or equipment box count changed')
        dest = path.removesuffix('.layer') + '_GC_Roster.layer'
        files[dest] = result
        audit[side] = {'path': dest, 'groups': groups, 'slots': slots,
                       'equipmentBoxReferences': [guid for guid, _ in REF.findall(equipment[0])]}
    return files, audit


def capture(source):
    lines = source.read(TRIGGER).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('TILW_FactionTriggerEntity CAPZone '))
    fields = properties(lines[start + 1:block_end(lines, start)], {
        'coords', 'scale', 'm_queryRadius', 'm_captureTime', 'm_sendStatusMessages', 'm_statusMessages',
        'm_locationName', 'm_flagName', 'm_playersOnly', 'm_ownerFactionKey',
    })
    status_fields = ('Captured', 'Capturing', 'CaptureInterrupted', 'Lost', 'Losing', 'LossInterrupted')
    status = re.findall(QUOTED, fields['m_statusMessages'])
    if len(status) != len(status_fields) or not re.fullmatch(IDENTIFIER, fields['m_flagName']):
        raise ValueError('WetFeet capture definition changed')
    key = WORLD + '/capture'
    messages = '\n'.join(f'         m_s{name} {text}' for name, text in zip(status_fields, status))
    condition = f'''      GC_AreaCondition "{{{guid(key)}}}" {{
       m_sArea "CAPZone"
       m_Targets GC_CharacterAreaTargets "{{{guid(key + '/players')}}}" {{
        m_aFactions {{
         {fields['m_ownerFactionKey']}
        }}
       }}
       m_Requirement GC_LocalFactionShare "{{{guid(key + '/share')}}}" {{
        m_fRequiredShare 1
       }}
       m_Capture GC_AreaCaptureOptions "{{{guid(key + '/timer')}}}" {{
        m_fSeconds {fields['m_captureTime']}
        m_bUseElapsedTime 1
        m_iInterruption 3
        m_Messages GC_CaptureMessages "{{{guid(key + '/status')}}}" {{
         m_sLocation {fields['m_locationName']}
{messages}
        }}
       }}
      }}'''
    area = f'''GC_RadiusAreaEntity CAPZone : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
 coords {fields['coords']}
 m_fRadius {fields['m_queryRadius']}
}}'''
    return fields['m_flagName'], condition, area, {
        'name': 'CAPZone', 'radiusMeters': fields['m_queryRadius'], 'seconds': fields['m_captureTime'],
        'ownerFaction': fields['m_ownerFactionKey'][1:-1], 'requiredLocalShare': 1,
        'timing': 'elapsed mission seconds',
    }


def victory_rules(lines, conditions):
    events = []
    for i, line in enumerate(lines):
        match = EVENT.fullmatch(line)
        if not match:
            continue
        fields = lines[i + 1:block_end(lines, i)]
        name = fields[0].removeprefix('   m_name ')
        if not re.fullmatch(IDENTIFIER, name) or fields[1] != '   m_instructions {':
            raise ValueError('WetFeet event has an unexpected name or instruction list')
        end = block_end(fields, 1)
        if end + 4 != len(fields) or not LITERAL.fullmatch(fields[end + 1]):
            raise ValueError('WetFeet event needs one literal condition')
        flag = fields[end + 2].removeprefix('    m_flagName ')
        key = WORLD + '/victory/' + match[1]
        notices = defaultdict(list)
        targets = []
        instruction = 2
        while instruction < end:
            last = block_end(fields, instruction)
            args = fields[instruction + 1:last]
            action = MESSAGE.fullmatch(fields[instruction])
            control = DEACTIVATE.fullmatch(fields[instruction])
            if action:
                delay, body = message(args, key + '/' + action[1], multiline=True)
                notices[delay].extend(body)
            elif control:
                controls = properties(args, {'m_eventNames'})
                targets = re.findall(IDENTIFIER, controls['m_eventNames'])
                for target in targets:
                    notices[5].extend([
                        f'      GC_EventControlAction "{{{guid(key + "/disable/" + target)}}}" {{',
                        f'       m_sEventName {target}', '       m_iOperation 1', '      }',
                    ])
            else:
                raise ValueError('WetFeet event has an unsupported instruction')
            instruction = last + 1
        events.append((key, name, flag, notices, targets))

    names = {name for _, name, _, _, _ in events}
    if len(names) != len(events) or len(events) != 3:
        raise ValueError('WetFeet victory event count or names changed')
    rules, audit = [], {}
    for key, name, flag, notices, targets in events:
        if flag not in conditions or len(targets) != 2 or set(targets) != names - {name}:
            raise ValueError('WetFeet victory condition or deactivation targets changed')
        rules.extend([
            f'    GC_Event "{{{guid(key)}}}" {{', f'     m_sName {name}',
            '     m_aConditions {', conditions[flag], '     }',
            '     m_aActions {',
            f'      GC_MessageAction "{{{guid(key + "/log")}}}" {{',
            f'       m_sMessage {name}', '       m_iDelivery 2', '      }',
            '     }', '    }',
        ])
        # Legacy deactivation prevents future triggers. Each already queued
        # instruction still executes, even if another outcome then deactivates it.
        for delay, actions in notices.items():
            delivery_key = key + '/delivery/' + str(delay)
            rules.extend([
                f'    GC_Event "{{{guid(delivery_key)}}}" {{',
                f'     m_sName "{name[1:-1]} announcement ({delay}s)"', '     m_aConditions {',
                f'      GC_MissionEventCondition "{{{guid(delivery_key + "/result")}}}" {{',
                f'       m_sEventName {name}', '      }', '     }',
                f'     m_Advanced GC_EventOptions "{{{guid(delivery_key + "/delay")}}}" {{',
                f'      m_fDelay {delay}', '     }', '     m_aActions {', *actions, '     }', '    }',
            ])
        audit[name[1:-1]] = {'sourceEvent': key.rsplit('/', 1)[1], 'flag': flag[1:-1],
                             'deactivates': [target[1:-1] for target in targets],
                             'instructionDelaysSeconds': [str(delay) for delay in notices]}
    return '\n'.join(rules), audit


def stage_mode(source, preset, cleanup=''):
    for path, oid in REVIEWED.items():
        if source.files.get(path) != oid:
            raise ValueError('WetFeet source changed: ' + path)

    flag, requirement, area, capture_audit = capture(source)
    conditions = {flag: requirement}
    casualty_audit = {}
    lines = source.read(DEFAULT).splitlines()
    for i, line in enumerate(lines):
        if not PEAK_FLAG.fullmatch(line):
            continue
        data = properties(lines[i + 1:block_end(lines, i)], {'m_flagName', 'm_factionKey', 'm_casualtyRatio'})
        flag = data['m_flagName']
        key = WORLD + '/casualties/' + flag
        conditions[flag] = f'''      GC_CustomScriptCondition "{{{guid(key)}}}" {{
       m_Script GC_PeakFactionPopulationCheck "{{{guid(key + '/check')}}}" {{
        m_sFaction {data['m_factionKey']}
        m_fRatio {data['m_casualtyRatio']}
       }}
      }}'''
        casualty_audit[flag[1:-1]] = {'faction': data['m_factionKey'][1:-1], 'ratio': data['m_casualtyRatio']}
    rules, audit = victory_rules(lines, conditions)
    if cleanup:
        rules += '\n' + cleanup
    start = next(i for i, line in enumerate(lines) if line.startswith('PS_GameModeCoop '))
    weather = '\n'.join(lines[start + 2:block_end(lines, start + 1)])
    for old, new in {
        '5EE3229927D4D2F5': '6B71E9A8B1A8C501',
        'm_bUseSpecifiedDate': 'm_bDateOverride',
        'm_iSetDay': 'm_iDay',
        'm_iSetMonth': 'm_iMonth',
        'm_iSetYear': 'm_iYear',
    }.items():
        weather = weather.replace(old, new)
    transform = lines[block_end(lines, start) - 1]
    mode = f'''GC_LobbyGameMode GC_WetFeetMode : "{{{guid(WORLD + '/resource')}}}{preset}" {{
 components {{
{weather}
  GC_PeakFactionPopulationComponent "{{{guid(WORLD + '/population')}}}" {{
  }}
  GC_LobbyManager "{{6A137A39106A4973}}" {{
   m_aMissionEvents + {{
{rules}
   }}
  }}
 }}
{transform}
}}
{area}
'''
    return mode, {'capture': capture_audit, 'casualties': casualty_audit, 'outcomes': audit}
