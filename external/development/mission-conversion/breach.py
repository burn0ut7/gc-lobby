"""Restore the Breach and Clear mission-owned loadout variants."""
from catalog import REF
from briefings import block_end, guid
from events import latched_script_event, peak_event
from groups import ROOT, group_layer, inheritance, legacy_member, only_path, scalar
from launchers import META_NAME, launcher, replace_reference
from markers import convert_layer as convert_markers

WORLD = 'worlds/Corax/Breach and clear/Breach_and_Clear.ent'
FOLDER = 'worlds/Corax/Breach and clear/No ETool/'
PLAYERS = WORLD.removesuffix('.ent') + '_Layers/Players.layer'
LAYERS = WORLD.removesuffix('.ent') + '_Layers/'
HEADER = 'Missions/Corax/Breachandclear/BreachAndClear.conf'
PRIOR_DEFAULT = '41152cb3ef5fac5ec5b0efacb6b2060c6c30c9d9'
SMOKE = 'worlds/Corax/Breach and clear/Building_Explosion_Smoke.ptc'
WORLD_INPUTS = {
    WORLD: 'c69a1a6413d4e2ef2c218b62f35eba72947189b9',
    LAYERS + 'default.layer': '4736ea7184c9246bc9fce73965a78f85de5d21f8',
    LAYERS + 'Fixtures.layer': 'f59d01e6ec469a80eb5fecbaf53902ff139a2d0c',
    LAYERS + 'Briefing.layer': 'f595a5b544194e97e193effc1ed67ecb768443ba',
    SMOKE: '54c8899b8d624bf2c11bf338e17af975a9cb160b',
}
GAPS = [
    'Compare an occupied RHS_AFRF side losing its players and verify remote announcement delivery',
    'Compare dynamic RHS_USAF AI-loss behavior against the legacy session and verify remote delivery',
    'Compare restriction-zone boundary behavior and the legacy warning HUD for both sides',
    'Verify inherited vehicles, weather, scenery, faction marker audiences and issued equipment',
    'Compare Senior Rifleman formation offsets and linked player and AI orders',
]
REVIEWED = {
    'AAT': 'f4c7d29d87dec64268bce907242dbf17cfaf753e',
    'AR': '95b7bafb7bdcd47b276392ef8cefffec6874fc11',
    'AT': 'accc034c744990ca6698ecbf8c51911fbf605383',
    'Rifleman': '944a8b650dcfd34b7de4e4b0f39c746b870b4b25',
    'SL': '557c737a9027cd65a862c8770e3d449f8e06083a',
    'SR': '11434cd66ff13405a48ea86aa167bd5ad4273f8e',
}


def stage_loadouts(source, dev, source_units, dev_units, stock):
    files, audit = {}, {}
    for role, oid in REVIEWED.items():
        path = FOLDER + f'Character_RHS_RF_MSV_Flora_{role}_NoETool_P.et'
        if source.files.get(path) != oid:
            raise ValueError('Breach and Clear loadout changed: ' + path)
        meta = source.read(path + '.meta')
        guid = META_NAME.search(meta)[2].upper()
        if (source.resources.get(guid) != [path] or guid in dev_units.resources
                or any(other != path for other in dev.resources.get(guid, []))):
            raise ValueError('Breach and Clear loadout identity collision: ' + guid)
        text = source.read(path)
        parent = REF.search(text.splitlines()[0])[1]
        if not inheritance(source_units, parent) or not inheritance(dev_units, parent):
            raise ValueError('Breach and Clear loadout parent needs review: ' + path)
        def relocate(match):
            ref_guid, ref_path = match.groups()
            if ref_guid.upper() in dev_units.resources:
                ref_path = only_path(dev_units, ref_guid)
            elif not stock.contains(ref_guid, ref_path):
                raise ValueError('Breach and Clear loadout dependency needs review: ' + ref_path)
            return '"{' + ref_guid + '}' + ref_path + '"'
        files[path] = REF.sub(relocate, text)
        files[path + '.meta'] = replace_reference(meta, META_NAME, guid, path)
        audit[path] = {
            'guid': guid, 'unitsParent': only_path(dev_units, parent),
            'legacyPlayableWithGroupOverride': legacy_member(source_units, parent, True),
        }
    return files, audit


def stage_roster(source, source_units, dev_units, stock, locally_owned, loadouts):
    if source.files.get(PLAYERS) != '1f7fdf9cbdcc2aad19c50f646c517d00e6c78511':
        raise ValueError('Breach and Clear roster changed')
    playable = {item['guid']: path for path, item in loadouts.items() if item['legacyPlayableWithGroupOverride']}
    ai_path, = [path for path, item in loadouts.items() if not item['legacyPlayableWithGroupOverride']]
    ai_guid = loadouts[ai_path]['guid']
    lines = source.read(PLAYERS).splitlines()
    placements, support = [], []
    for i, line in enumerate(lines):
        group = ROOT.fullmatch(line)
        if not group:
            continue
        stop = block_end(lines, i)
        placement = lines[i:stop + 1]
        if any(ai_guid in item for item in placement):
            source_chain = inheritance(source_units, group['guid'])
            if scalar(source_chain, 'm_bSetPlayable') != 1:
                raise ValueError('Breach and Clear group playability override changed')
            ai_ref = next(match[0] for item in placement for match in REF.finditer(item) if match[1] == ai_guid)
            placement = [item.replace(ai_ref, '').rstrip() for item in placement]
            # Reuse the RHS base already inherited by the playable Units Dev parent.
            parent = REF.search(inheritance(dev_units, group['guid'])[0].splitlines()[0])
            if not parent or parent[1] != 'AE252B0A692DA762':
                raise ValueError('Breach and Clear RHS group ancestry changed')
            transform = [item for item in placement if item.startswith((' coords ', ' angleY '))]
            name = f'GC_BreachSeniorRifleman{len(support) + 1}'
            support.append('\n'.join([
                f'SCR_AIGroup {name} : {parent[0]} {{',
                ' components {', '  AIFormationComponent "{507E9DC12F541FE6}" {',
                '   Enabled 0', '  }', ' }', *transform, ' m_bPlayable 0',
                ' m_aUnitPrefabSlots {', f'  "{{{ai_guid}}}{ai_path}"', ' }', '}', '',
            ]))
        placements.extend(placement)
    body, groups, slots = group_layer('\n'.join(placements), source_units, dev_units, stock, locally_owned,
                                      reviewed_members=playable)
    if (groups, slots, len(support)) != (7, 48, 2):
        raise ValueError('Breach and Clear slot or support roster changed')
    return body + ''.join(support), {'playableGroups': groups, 'unitPrefabEntries': slots, 'supportAI': len(support)}


def assemble(source, dev, preset):
    for path, oid in WORLD_INPUTS.items():
        if source.files.get(path) != oid:
            raise ValueError('Breach and Clear world source changed: ' + path)
    world, _, files = launcher(source, dev, HEADER)
    if world != WORLD:
        raise ValueError('Breach and Clear launcher resolves to a different world')
    files[world] = source.read(world)
    smoke_guid = 'BF496ADB7584E640'
    if any(path != SMOKE for path in dev.resources.get(smoke_guid, [])):
        raise ValueError('Breach and Clear smoke resource identity collision')
    files[SMOKE] = source.read(SMOKE)
    files[SMOKE + '.meta'] = replace_reference(source.read(SMOKE + '.meta'), META_NAME, smoke_guid, SMOKE)

    lines = source.read(LAYERS + 'default.layer').splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('PS_GameModeCoop '))
    stop = block_end(lines, start)
    arms = next(i for i, line in enumerate(lines) if line.startswith('$grp PolylineShapeEntity '))
    component_end = block_end(lines, start + 1)
    weather = '\n'.join(lines[start + 2:component_end]).replace('5EE3229927D4D2F5', '6B71E9A8B1A8C501')
    # The reviewed fixtures bind ruloss to all RHS_AFRF players and delay the
    # RHS_USAF victory instruction by 10 seconds. Legacy Core suppresses game end.
    key = WORLD + '/casualty/6508D983AB223731'
    announcement = peak_event(key, 'RHS_AFRF', '1', 10, [
        f'      GC_MessageAction "{{{guid(key + "/notice")}}}" {{',
        '       m_sTitle "MISSION END"',
        '       m_sMessage "End conditions have been met:"\\',
        '       "U.S. Armed Forces victory."',
        '      }',
    ])
    key = WORLD + '/casualty/6508D983AB010064'
    ai_check = f'''       m_Script GC_LivingAndDeadAICheck "{{{guid(key + '/check')}}}" {{
        m_fRatio 0.9
       }}'''
    announcement += latched_script_event(key, 'RHS_USAF reached AI casualty threshold', ai_check, 10, [
        f'      GC_MessageAction "{{{guid(key + "/notice")}}}" {{',
        '       m_sTitle "MISSION END"',
        '       m_sMessage "End conditions have been met:"\\',
        '       "Armed Forces of the Russian Federation victory."',
        '      }',
    ])
    mode = f'''GC_LobbyGameMode GC_BreachMode : "{{{guid(WORLD + '/resource')}}}{preset}" {{
 components {{
{weather}
  SCR_PlayersRestrictionZoneManagerComponent "{{{guid(WORLD + '/restriction-manager')}}}" {{
  }}
  GC_LobbyFreezeComponent "{{E81E75BC287148D0}}" {{
   m_iAuthoredCountdownSeconds 60
  }}
  GC_PeakFactionPopulationComponent "{{{guid(WORLD + '/population')}}}" {{
  }}
  GC_LivingAndDeadAIComponent "{{{guid(WORLD + '/ai-population')}}}" {{
   m_sFaction "RHS_USAF"
  }}
  GC_LobbyManager "{{6A137A39106A4973}}" {{
   m_aMissionEvents + {{
    GC_Event "{{{guid(WORLD + '/release')}}}" {{
     m_sName "Release starting zone"
     m_aConditions {{
      GC_TimerCondition "{{{guid(WORLD + '/release/timer')}}}" {{
       m_fSeconds 60
      }}
     }}
     m_aActions {{
      GC_DeleteEntitiesAction "{{{guid(WORLD + '/release/delete')}}}" {{
       m_aEntities {{
        "GC_BreachStartingZone"
       }}
      }}
     }}
    }}
{announcement}   }}
  }}
 }}
 coords 1601.722 20.219 2178.804
}}'''
    files[LAYERS + 'default.layer'] = '\n'.join([
        *lines[:block_end(lines, 0) + 1], mode, *lines[stop + 1:arms],
        *lines[block_end(lines, arms) + 1:], '',
    ])

    briefing = source.read(LAYERS + 'Briefing.layer').splitlines()
    start = next(i for i, line in enumerate(briefing) if line.startswith('$grp PS_ManualMarker '))
    icons, briefing_count = convert_markers('\n'.join(briefing[start:]))
    files[LAYERS + 'Briefing.layer'] = icons
    fixtures = source.read(LAYERS + 'Fixtures.layer').splitlines()
    model = []
    i = 0
    fixture_count = 0
    while i < len(fixtures):
        if not fixtures[i].strip():
            i += 1
            continue
        end = block_end(fixtures, i)
        if fixtures[i].startswith('$grp PS_ManualMarker '):
            body, fixture_count = convert_markers('\n'.join(fixtures[i:end + 1]))
            model.extend(body.splitlines())
        elif not fixtures[i].startswith('TILW_MissionFrameworkEntity '):
            block = fixtures[i:end + 1]
            if block[0].startswith('SCR_EditorRestrictionZoneEntity :'):
                block[0] = block[0].replace(' :', ' GC_BreachStartingZone :', 1)
            model.extend(block)
        i = end + 1
    files[LAYERS + 'Fixtures.layer'] = '\n'.join(model) + '\n'
    return files, briefing_count + fixture_count
