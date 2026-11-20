"""Stage Gwotline Bling's reviewed GC Lobby game mode for world assembly."""
from boundaries import convert_boundaries
from briefings import block_end, guid
from catalog import REF
from events import peak_notifications
from groups import ROOT, group_layer
from launchers import launcher
from markers import DEST as MAP_MARKER, convert_layer as convert_markers

WORLD = 'worlds/Fruz/Gwotline Bling/Gwotline Bling.ent'
HEADER = 'Missions/Fruz/Gwotline_Bling.conf'
DEFAULT = WORLD[:-4] + '_Layers/default.layer'
PRIOR_DEFAULT = 'f234b8de5f4bbf1614e31c77222471de5c60f0fc'
AO = DEFAULT.removesuffix('default.layer') + 'AO.layer'
GROUPS = {
    'Blufor1/Blufor.layer': '15814895695ea27140f4d286b38cbe859be90430',
    'Opfor1/OPFOR.layer': '5659970f638434e7de5bb62625cd0f2429064b57',
}
CALLSIGNS = frozenset({'6508C3582F634926', '68B37CEF862EBFE6', '6508C35869053D73'})
GAPS = [
    'Preserve the mixed player and AI machine-gun group',
    'Verify starting slot counts, callsigns, and issued equipment on both sides',
    'Compare the public AO outline and verify that the authored GC_MUC key has no members',
    'Compare both public start markers and the lantern and floodlight behavior in play',
    'Compare native allocation of the custom RHS_USAF callsigns and both starting areas',
    'Compare the 3,900-second Time Limit event, which has no serialized condition',
    'Verify vehicles, staging restrictions, weather and both casualty notifications in play',
]


def stage_boundaries(source):
    if source.files[AO] != '04b3bf78f1ba1b040bacea3649aef58a514014d2':
        raise ValueError('Gwotline Bling source AO layer changed')
    body, audit = convert_boundaries(source.read(AO), allow_public_faction_color=True)
    if set(audit) != {'2', '5', '16'} or audit['16'].get('vertices') != 5:
        raise ValueError('Gwotline Bling AO geometry changed')
    for line, points in [('2', 0), ('5', 2)]:
        if audit[line].get('gap') != 'native polygon requires 3 to 128 points':
            raise ValueError('expected inert legacy AO placement at line ' + line)
        audit[line] = {'vertices': points, 'inert': 'Legacy AO initialization returns before drawing or enforcement'}
    return body, audit


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
    faction_start = lines.index('    SCR_Faction "{5CC8DE37E1FF0F7A}" {')
    faction = '\n'.join(lines[faction_start:block_end(lines, faction_start) + 1])
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
 {{
  SCR_FactionManager {{
   ID "6ADC9BB66394BE46"
   Factions + {{
{faction}
   }}
  }}
 }}
}}
'''
    audit = [{k: v for k, v in record.items() if k != 'events'} for record in rules]
    return mode, audit


def stage_rosters(source, source_units, dev_units, stock, locally_owned):
    for relative, oid in GROUPS.items():
        path = DEFAULT.removesuffix('default.layer') + relative
        if source.files[path] != oid:
            raise ValueError('Gwotline group source layer changed: ' + path)
        lines = source.read(path).splitlines()
        staged, exceptions = [], {}
        groups = slots = i = 0
        while i < len(lines):
            if not lines[i].strip():
                i += 1
                continue
            end = block_end(lines, i)
            if ROOT.fullmatch(lines[i]):
                try:
                    placement = lines[i:end + 1]
                    if relative == 'Blufor1/Blufor.layer' and i == 0:
                        # The HQ has a trailing empty slot, which legacy did not spawn.
                        empty = placement.index('  ""')
                        if placement[empty + 1] != ' }':
                            raise ValueError('Gwotline Ranger HQ empty slot is no longer trailing')
                        placement = placement[:empty] + placement[empty + 1:]
                    body, count, members = group_layer('\n'.join(placement), source_units, dev_units,
                                                       stock, locally_owned, CALLSIGNS)
                    staged.append(body)
                    groups += count
                    slots += members
                except ValueError as exc:
                    exceptions[str(i + 1)] = str(exc)
            else:
                exceptions[str(i + 1)] = 'retained source placement requires separate review'
            i = end + 1
        if not staged:
            raise ValueError('Gwotline roster generated no groups: ' + path)
        yield path.removesuffix('.layer') + '_GC_Roster.layer', ''.join(staged), {
            'sourceLayer': path, 'groups': groups, 'unitPrefabEntries': slots, 'remainingPlacements': exceptions,
        }


def stage_scene(source, stock, locally_owned):
    folder = DEFAULT.removesuffix('default.layer')
    ammo = folder + 'Opfor1/Ammo_crates.layer'
    marker = folder + 'markers.layer'
    blufor = folder + 'Blufor1/Blufor.layer'
    reviewed = {
        ammo: '99628ca964e4f9fd6d075ce96a3a7b0d592c4ba4',
        marker: '268461edb5fc571ba410a9bf84c3f29b11cb9ad9',
        blufor: GROUPS['Blufor1/Blufor.layer'],
    }
    for path, oid in reviewed.items():
        if source.files[path] != oid:
            raise ValueError('Gwotline scenery source layer changed: ' + path)
    props, icon = source.read(ammo).split('PS_ManualMarker ', 1)
    floodlights = source.read(blufor).split('$grp GenericEntity ', 1)[1]
    lights = '$grp GenericEntity ' + floodlights
    ammo_icon, ammo_count = convert_markers('PS_ManualMarker ' + icon)
    start_icon, start_count = convert_markers(source.read(marker))
    if ammo_count != 1 or start_count != 1:
        raise ValueError('expected one start marker for each side')
    for guid, path in REF.findall(props + lights + ammo_icon + start_icon):
        if '{' + guid + '}' + path == MAP_MARKER:
            continue
        if guid.upper() in locally_owned or not stock.contains(guid, path):
            raise ValueError('Gwotline scenery resource needs review: ' + path)
    return {
        ammo: (props + ammo_icon, props.count('\n  coords ') + props.count('\n coords '), 1),
        marker: (start_icon, 0, 1),
        folder + 'Blufor1/Blufor_GC_Scenery.layer': (lights, lights.count('\n  coords '), 0),
    }


def assemble(source, dev, mode):
    if source.files[WORLD] != 'e17645f838b8fcf6a185bafb93421b378505630d':
        raise ValueError('Gwotline Bling terrain parent changed')
    world, _, files = launcher(source, dev, HEADER)
    if world != WORLD:
        raise ValueError('Gwotline Bling launcher resolves to a different world')
    lines = source.read(DEFAULT).splitlines()
    if not lines[0].startswith('SCR_AIWorld '):
        raise ValueError('missing Gwotline Bling AI world placement')
    files[WORLD] = source.read(WORLD)
    ai_world = '\n'.join(lines[:block_end(lines, 0) + 1])
    # Resolve the obsolete legacy identity against the installed Kunar resource registry.
    ai_world = ai_world.replace('{77C3151F7A6E588C}Prefabs/SCR_AIWorld_Kunar.et',
                                '{E2A016456BBC697D}Prefabs/SCR_AIWorld_Kunar.et')
    files[DEFAULT] = ai_world + '\n' + mode
    return files
