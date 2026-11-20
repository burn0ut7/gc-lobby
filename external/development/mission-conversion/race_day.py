"""Reviewed world recipe for Race Day - Kunar."""
from pathlib import PurePosixPath
import re
from briefings import guid


WORLD = 'worlds/Perfk/RaceDayKunar/RaceDayKunar.ent'
HEADER = 'Missions/Perfk/RaceDayKunar.conf'
BIKE = 'Prefabs/Vehicles/Wheeled/Motorbike/motorbike_green_low_ejection_chance.et'


def race_files(source):
    folder = str(PurePosixPath(WORLD).with_suffix('')) + '_Layers/'
    core = folder + 'default.layer'
    if source.files[core] != 'e42f5269711b7813bbd790e444327f2965d872a2':
        raise ValueError('Race Day Kunar source rules changed; review the world recipe')
    paths = [HEADER, HEADER + '.meta', WORLD, WORLD + '.meta', *source.layers[WORLD.lower()]]
    files = {p: source.read(p) for p in paths}
    bikes = folder + 'Blufor/Bikes.layer'
    files[bikes] = files[bikes].replace('}worlds/Perfk/' + BIKE, '}' + BIKE)
    players = folder + 'Blufor/Players.layer'
    files[players] = files[players].replace(
        '{BF38869730F83577}Prefabs/Characters/Factions/CIV/GenericCivilians/Character_CIV_Randomized_P.et',
        '{22E43956740A6794}Prefabs/Characters/Factions/CIV/GenericCivilians/Character_CIV_Randomized.et')
    original = files[core]
    fences = re.findall(r'^\s*"(fence_\d+)"$', original, re.M)
    if len(fences) != 44 or len(set(fences)) != 44:
        raise ValueError('start gate has unexpected entity names')
    names = '\n'.join(f'         "{name}"' for name in [*fences, 'GC_RaceStartingZone'])
    # Preserve the terrain AI world. The GC template supplies perception, factions, radio and chat.
    ai = original[:original.index('PerceptionManager')]
    zone = original[original.index('SCR_EditorRestrictionZoneEntity'):]
    zone = zone.replace('SCR_EditorRestrictionZoneEntity :', 'SCR_EditorRestrictionZoneEntity GC_RaceStartingZone :', 1)
    files[core] = ai + race_mode(WORLD, names, '3080.505 10.036 3056.896', '2719.005 10 3160.181', '   m_iStartingHours 9') + zone
    return files


def race_mode(world, names, coords, finish, weather, extra_event=''):
    mode = str(PurePosixPath(world).with_suffix('')) + '_GC_Settings.et'
    body = """GC_LobbyGameMode GC_RaceMode : "@MODE@" {
 components {
  SCR_TimeAndWeatherHandlerComponent "{6B71E9A8B1A8C501}" {
@WEATHER@
  }
  GC_LobbyManager "{6A137A39106A4973}" {
   m_bSlotsStartLocked 0
   m_aMissionEvents {
@EXTRA@    GC_Event "{6664B56611AE97FF}" {
     m_sName "RemoveFence"
     m_aConditions {
      GC_TimerCondition "{D944E3DF2B010001}" {
       m_fSeconds 60
      }
     }
     m_aActions {
      GC_DeleteEntitiesAction "{6664B566E4C323FE}" {
       m_aEntities {
@NAMES@
       }
      }
      GC_MessageAction "{6664B56722D6CC5F}" {
       m_sTitle "RACE RACE RACE"
       m_sMessage "RACE RACE RACE"
      }
     }
    }
    GC_Event "{6664B56D36864AEF}" {
     m_sName "First racer finished"
     m_aConditions {
      GC_AreaCondition "{6664B56D0E1FFB87}" {
       m_sArea "GC_RaceFinish"
       m_Targets GC_CharacterAreaTargets "{D944E3DF2B010002}" {
        m_aFactions {
         "CIV"
        }
       }
       m_Requirement GC_LivingProportion "{D944E3DF2B010003}" {
        m_fRequiredProportion 0.01
       }
      }
     }
     m_aActions {
      GC_MessageAction "{6664B56D0A29E963}" {
       m_sTitle "WINNER"
       m_sMessage "The first player is across the finish line!"
      }
     }
    }
    GC_Event "{D944E3DF2B010004}" {
     m_sName "Finish reminder"
     m_aConditions {
      GC_MissionEventCondition "{D944E3DF2B010005}" {
       m_sEventName "First racer finished"
      }
     }
     m_Advanced GC_EventOptions "{D944E3DF2B010006}" {
      m_fDelay 15
     }
     m_aActions {
      GC_MessageAction "{6664B56DA651136B}" {
       m_sTitle "GM's decision"
       m_sMessage "GM's, please give the other slowpokes a few minutes to try and finish before selecting the next mission."
      }
     }
    }
   }
  }
 }
 coords @MODE_COORDS@
 m_eJipMode DISABLED
}
GC_RadiusAreaEntity GC_RaceFinish : "{2C4447C53A1E450B}Prefabs/Logic/Mission/RadiusArea.et" {
 coords @FINISH@
 m_fRadius 25
}
"""
    return (body.replace('@MODE@', '{' + guid(world + '/resource') + '}' + mode)
            .replace('@NAMES@', names).replace('@MODE_COORDS@', coords).replace('@FINISH@', finish)
            .replace('@WEATHER@', weather).replace('@EXTRA@', extra_event))
