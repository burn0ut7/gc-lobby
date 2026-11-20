"""Stage the Hnevice terrain, lobby mode, briefings, groups and supported AO."""
import re

from boundaries import convert_instance
from briefings import block_end, guid
from events import EVENT, LITERAL, MESSAGE, message
from launchers import launcher
from markers import LEGACY, instance

WORLD = 'worlds/Corax/Hnevice/Hnevice.ent'
HEADER = 'Missions/Corax/Hnevice/Hnevice.conf'
FOLDER = WORLD[:-4] + '_Layers/'
REVIEWED = {
    'default.layer': 'd1109fdf8ae985bcc38cf6c3799f14c94a86f7d8',
    'blu.layer': '3747dd7b0649279ff1164566b2ee754ac65454e3',
    'briefing.layer': '180e8cad25296a3a5470ab73ed701c326c5c166c',
}
GAPS = [
    'Verify the two 80% casualty notifications, including spawn/delete sampling and recovery during the delay',
    'Compare the crash site and both platoon icons before slotting and as each assigned side',
    'Compare the map-only polygon before slotting, as US and USSR, and as other factions',
    'Verify the 120-second staging countdown in match, on reconnect and on early match end',
    'Compare legacy freeze-end chat and elapsed mission-time presentation',
    'Verify faction loadouts, eight groups, 68 slots, 11 briefing entries and weather in play',
    'Verify JIP, respawn defaults, and after-action flow against the legacy session',
]

MARKER_PRESETS = {
    # Reviewed TilW objective and platoon prefabs. Each placed marker overrides
    # the inherited transform, tint, and label. The platoon icons also override the imageset.
    'BA3177FCACC39F3A': {'m_sQuadName': '"objective-marker"', 'm_fWorldSize': '50'},
    '63BCE30CF8B65BCE': {'m_sQuadName': '"infantry"', 'm_fWorldSize': '95'},
    '676B84E0291E12D6': {'m_sQuadName': '"infantry"', 'm_fWorldSize': '95'},
}


def mission_icons(source):
    output = []
    for name in ('default.layer', 'briefing.layer'):
        lines = source.read(FOLDER + name).splitlines()
        for pos, line in enumerate(lines):
            match = LEGACY.fullmatch(line)
            if not match:
                continue
            if match['group'] or match['guid'] not in MARKER_PRESETS:
                raise ValueError('Hnevice marker preset changed')
            inherited = {
                'm_sImageSetGlow': '""', 'm_bVisibleForEmptyFaction': '1', 'm_bShowForAnyFaction': '1',
                **MARKER_PRESETS[match['guid']],
            }
            label = ' ' + match['name'] if match['name'] else ''
            output.append(instance(lines[pos + 1:block_end(lines, pos)], label, inherited))
    if len(output) != 3:
        raise ValueError('expected three Hnevice icons')
    return '\n'.join(output) + '\n'


def casualty_rules(lines):
    flags = dict(re.findall(
        r'^  TILW_FactionPlayersKilledFlag "\{[\dA-F]+\}" \{\n'
        r'   m_flagName "(\w+)"\n   m_factionKey "([\w]+)"\n   m_casualtyRatio 0\.8\n  }$',
        '\n'.join(lines), re.M))
    if flags != {'san': 'San_Serriffe', 'don': 'GC_DON'}:
        raise ValueError('Hnevice casualty flag layout changed')
    rules = []
    for start, line in enumerate(lines):
        event = EVENT.fullmatch(line)
        if not event:
            continue
        stop = block_end(lines, start)
        condition = next(i for i in range(start + 1, stop) if LITERAL.fullmatch(lines[i]))
        flag = re.fullmatch(r'    m_flagName "(\w+)"', lines[condition + 1])[1]
        index = next(i for i in range(start + 1, stop) if MESSAGE.fullmatch(lines[i]))
        key = WORLD + '/casualty/' + event[1]
        delay, action = message(lines[index + 1:block_end(lines, index)], key + '/message')
        if delay != 5:
            raise ValueError('Hnevice casualty notification delay changed')
        name = f'{flags[flag]} reached casualty threshold'
        rules.append(f'''    GC_Event "{{{guid(key + '/trigger')}}}" {{
     m_sName "{name}"
     m_aConditions {{
      GC_CustomScriptCondition "{{{guid(key + '/condition')}}}" {{
       m_Script GC_PeakFactionPopulationCheck "{{{guid(key + '/check')}}}" {{
        m_sFaction "{flags[flag]}"
        m_fRatio 0.8
       }}
      }}
     }}
     m_aActions {{
      GC_MessageAction "{{{guid(key + '/log')}}}" {{
       m_sMessage "{name}"
       m_iDelivery 2
      }}
     }}
    }}
    GC_Event "{{{guid(key + '/notify')}}}" {{
     m_sName "{name} notification"
     m_aConditions {{
      GC_MissionEventCondition "{{{guid(key + '/latch')}}}" {{
       m_sEventName "{name}"
      }}
     }}
     m_Advanced GC_EventOptions "{{{guid(key + '/delay')}}}" {{
      m_fDelay {delay}
     }}
     m_aActions {{
{chr(10).join(action)}
     }}
    }}
''')
    if len(rules) != 2:
        raise ValueError('expected two Hnevice casualty notifications')
    return ''.join(rules)


def assemble(source, dev, row, generated):
    if source.files[WORLD] != '577e41bd6ec0f56f41e51342e1d886c59423009e':
        raise ValueError('Hnevice source terrain parent changed')
    expected_layers = {FOLDER + name for name in REVIEWED}
    if set(source.layers[WORLD.lower()]) != expected_layers:
        raise ValueError('Hnevice layer tree changed')
    for name, oid in REVIEWED.items():
        if source.files[FOLDER + name] != oid:
            raise ValueError('Hnevice source layer changed: ' + name)
    settings = row['briefingModePreset']
    groups = row['groupLayers'][FOLDER + 'blu.layer']
    ao = row['boundaryLayers'][FOLDER + 'default.layer']
    if groups.get('playableGroups') != 8 or groups.get('unitPrefabEntries') != 68:
        raise ValueError('Hnevice groups or slot counts changed')
    if sum('vertices' in item for item in ao['instances'].values()) != 1:
        raise ValueError('Hnevice boundary coverage changed')
    for path in (settings, FOLDER + 'blu.layer', ao['placementLayer']):
        if path not in generated:
            raise ValueError('missing Hnevice staged resource: ' + path)
    world, _, files = launcher(source, dev, HEADER)
    if world != WORLD:
        raise ValueError('Hnevice launcher resolves to a different world')
    lines = source.read(FOLDER + 'default.layer').splitlines()
    passive = [int(line) - 1 for line, entry in ao['instances'].items() if 'gap' in entry]
    if len(passive) != 1:
        raise ValueError('expected one Hnevice passive AO placement')
    start = passive[0]
    shape, vertices = convert_instance(lines[start + 1:block_end(lines, start)], ' GC_HneviceMapMask', passive_only=True)
    if vertices != 4:
        raise ValueError('Hnevice passive polygon geometry changed')
    freeze_ms = next(int(line.split()[1]) for line in lines if line.startswith(' m_iFreezeTime '))
    if freeze_ms != 120000:
        raise ValueError('Hnevice staging duration changed')
    weather_start = next(i for i, line in enumerate(lines) if line.startswith('  SCR_TimeAndWeatherHandlerComponent '))
    weather_end = block_end(lines, weather_start)
    weather = '\n'.join(lines[weather_start:weather_end + 1]).replace('5EE3229927D4D2F5', '6B71E9A8B1A8C501')
    ai_world = '\n'.join(lines[:next(i for i, line in enumerate(lines) if line.startswith('PerceptionManager '))])
    # Core Dev has no GC_Bystrany wrapper. Its legacy layer added only the terrain map.
    files[WORLD] = 'SubScene {\n Parent "{7A888BF1C248E2E6}Worlds/Bystrany/Bystrany.ent"\n}\n'
    files[FOLDER + 'icons.layer'] = mission_icons(source)
    files[FOLDER + 'map_mask.layer'] = shape + '\n'
    ao['instances'][str(start + 1)] = {
        'vertices': vertices, 'name': 'GC_HneviceMapMask',
        'lastLine': block_end(lines, start) + 1, 'candidateLayer': FOLDER + 'map_mask.layer',
    }
    # The native marker manager reads the map singleton during mode initialization.
    map_entity = '''SCR_MapEntity MapEntity1 : "{731564B66F91B107}Prefabs/World/Game/MapEntity.et" {
 "Map Geometry Data" "{0C931F860C09AC4C}Worlds/Bystrany/Bystrany.topo"
 "Satellite background image" "{10EB7E9261F936BC}UI/Textures/Map/Worlds/Bystrany/Bystrany_Rasterized_Enhanced.edds"
}
'''
    files[FOLDER + 'default.layer'] = (
        ai_world + '\n' + map_entity
        + f'GC_LobbyGameMode GC_HneviceMode : "{{{guid(WORLD + "/resource")}}}{settings}" {{\n'
        + ' components {\n' + weather + '\n'
        + f'  GC_LobbyFreezeComponent "{{E81E75BC287148D0}}" {{\n   m_iAuthoredCountdownSeconds {freeze_ms // 1000}\n  }}\n'
        + f'  GC_PeakFactionPopulationComponent "{{{guid(WORLD + "/population")}}}" {{\n  }}\n'
        + '  GC_LobbyManager "{6A137A39106A4973}" {\n   m_aMissionEvents + {\n'
        + casualty_rules(lines)
        + '   }\n  }\n }\n coords 2161.418 14.603 -3370.065\n}\n'
    )
    return files
