"""Stage Gwotline Bling's reviewed GC Lobby game mode for world assembly."""
from briefings import block_end, guid
from events import peak_notifications

WORLD = 'worlds/Fruz/Gwotline Bling/Gwotline Bling.ent'
DEFAULT = WORLD[:-4] + '_Layers/default.layer'
GAPS = [
    'Port the two starting rosters, including the mixed BLUFOR layer, and verify issued equipment',
    'Resolve AO prefab defaults and preserve public faction color and map-only settings',
    'Convert the markers in the ammunition layer and verify both map audiences',
    'Transfer the custom faction callsigns and verify both starting areas',
    'Compare the 3,900-second Time Limit event, which has no serialized condition',
    'Verify vehicles, staging restrictions, weather and both casualty notifications in play',
]


def stage_mode(source, preset):
    if source.files[DEFAULT] != 'dc2a5d83afbd07c77c77af7937676563db110420':
        raise ValueError('Gwotline Bling source mode changed')
    lines = source.read(DEFAULT).splitlines()
    rules = peak_notifications(lines, WORLD)
    expected = [('GC_MUJ', '0.9', '5'), ('RHS_USAF', '0.9', '5')]
    if [(r['faction'], r['ratio'], r['delaySeconds']) for r in rules] != expected:
        raise ValueError('Gwotline Bling casualty notification layout changed')
    start = next(i for i, line in enumerate(lines) if line.startswith('  SCR_TimeAndWeatherHandlerComponent '))
    stop = block_end(lines, start)
    weather = '\n'.join(lines[start:stop + 1]).replace('5EE3229927D4D2F5', '6B71E9A8B1A8C501')
    events = ''.join(r['events'] for r in rules)
    mode = f'''GC_LobbyGameMode GC_GwotlineMode : "{{{guid(WORLD + '/resource')}}}{preset}" {{
 components {{
{weather}
  GC_LobbyFreezeComponent "{{E81E75BC287148D0}}" {{
   m_iAuthoredCountdownSeconds 300
  }}
  GC_PeakFactionPopulationComponent "{{{guid(WORLD + '/population')}}}" {{
  }}
  GC_LobbyManager "{{6A137A39106A4973}}" {{
   m_aMissionEvents + {{
{events}   }}
  }}
 }}
 coords 2812.845 10 3152.494
}}
'''
    audit = [{k: v for k, v in record.items() if k != 'events'} for record in rules]
    return mode, audit
