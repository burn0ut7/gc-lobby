"""Stage the Hnevice terrain, lobby mode, briefings, groups and supported AO."""
from briefings import block_end, guid
from launchers import launcher

WORLD = 'worlds/Corax/Hnevice/Hnevice.ent'
HEADER = 'Missions/Corax/Hnevice/Hnevice.conf'
FOLDER = WORLD[:-4] + '_Layers/'
REVIEWED = {
    'default.layer': 'd1109fdf8ae985bcc38cf6c3799f14c94a86f7d8',
    'blu.layer': '3747dd7b0649279ff1164566b2ee754ac65454e3',
    'briefing.layer': '180e8cad25296a3a5470ab73ed701c326c5c166c',
}
GAPS = [
    'Convert the two 80% casualty notifications with legacy peak-population semantics',
    'Convert the crash site and the two side-filtered platoon icons',
    'Convert the disabled AO instance as a map-only polygon with US and USSR visibility',
    'Match the legacy 120-second staging behavior and countdown',
    'Verify faction loadouts, eight groups, 68 slots, 11 briefing entries and weather in play',
    'Verify JIP, respawn defaults, and after-action flow against the legacy session',
]


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
    weather_start = next(i for i, line in enumerate(lines) if line.startswith('  SCR_TimeAndWeatherHandlerComponent '))
    weather_end = block_end(lines, weather_start)
    weather = '\n'.join(lines[weather_start:weather_end + 1]).replace('5EE3229927D4D2F5', '6B71E9A8B1A8C501')
    ai_world = '\n'.join(lines[:next(i for i, line in enumerate(lines) if line.startswith('PerceptionManager '))])
    # Core Dev has no GC_Bystrany wrapper. Its legacy layer added only the terrain map.
    files[WORLD] = 'SubScene {\n Parent "{7A888BF1C248E2E6}Worlds/Bystrany/Bystrany.ent"\n}\n'
    files[FOLDER + 'map.layer'] = '''SCR_MapEntity MapEntity1 : "{731564B66F91B107}Prefabs/World/Game/MapEntity.et" {
 "Map Geometry Data" "{0C931F860C09AC4C}Worlds/Bystrany/Bystrany.topo"
 "Satellite background image" "{10EB7E9261F936BC}UI/Textures/Map/Worlds/Bystrany/Bystrany_Rasterized_Enhanced.edds"
}
'''
    files[FOLDER + 'default.layer'] = (
        ai_world + '\n'
        + f'GC_LobbyGameMode GC_HneviceMode : "{{{guid(WORLD + "/resource")}}}{settings}" {{\n'
        + ' components {\n' + weather + '\n }\n coords 2161.418 14.603 -3370.065\n}\n'
    )
    return files
