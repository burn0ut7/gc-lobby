"""Port the Ruha race using its reviewed course and the shared race rules."""
import math
from pathlib import PurePosixPath
import re
from briefings import guid
from markers import convert_layer
from race_day import BIKE, race_mode

WORLD = 'worlds/Perfk/RaceDayRuha/RaceDayRuha.ent'
HEADER = 'Missions/Perfk/RaceDayRuha.conf'
FOLDER = str(PurePosixPath(WORLD).with_suffix('')) + '_Layers/'
REVIEWED = {
    'default.layer': '606a69e1fd1c9ff3bb63dbfcccd8224315a5a42f',
    'Helicopterarea.layer': 'f24a440032823ede113f737331d7114d5a24ae7c',
    'test.layer': 'fd9d75ab2436520625fe97328435a9472c7249cf',
    'Players.layer': '2247c984b100bc156573d19e87c4434bf9d37e55',
    'Motorbikes.layer': '54aa10a04a16d5a3fb952e3ab81532db0dddd8bf',
}

CLEANUP = """    GC_Event "{94D8334100000001}" {
     m_sName "Clear finish line roadblock"
     m_aActions {
      GC_DeleteEntitiesAction "{94D8334100000002}" {
       m_Radius GC_RadiusDeletion "{94D8334100000003}" {
        m_sCenter "GC_RaceRoadblockCenter"
        m_fRadius 10
        m_aWhitelist {
         "{64C9C1D1C2C1B38D}Prefabs/Props/Infrastructure/Roadblocks/RoadBlock_01.et"
        }
       }
      }
     }
    }
"""

AO = """GC_AOLimitEntity GC_RaceCourse : "{8F470B543A20D944}Prefabs/Logic/Mission/AOLimit.et" {
 components {
  GC_BoundaryWallComponent "{6A4D831C2E5079C1}" {
   m_bEnabled 0
  }
  GC_MapShapeComponent "{8F470B543A20D964}" {
   m_bEnabled 1
   m_bFillEnabled 0
   m_LineColor 0 0.176 1 1
   m_fLineWidth 3
  }
 }
 m_eBehavior SOFT_KILL
 m_fGraceSeconds 20
 m_eVehicleMode EXCEPT_MATCHING
 m_aVehiclePrefabs {
  "{A317D959A4248173}Prefabs/Vehicles/Helicopters/AH6M/AH6M_GAU19.et"
 }
"""

TELEPORT = """GenericEntity GC_RaceTransfer : "{4A7B7EF936B349A7}Prefabs/Logic/Mission/TeleportInteraction.et" {
 components {
  GC_TeleportInteractionComponent "{B3632A2C9CA24FDF}" {
   m_sActionName "Get to the choppa!"
   m_sDestinationName "IP_A"
  }
  ActionsManagerComponent "{F097C296BF6748C5}" {
   ActionContexts {
    UserActionContext "{33B86D1741F2473F}" {
     Position PointInfo "{21B6A8BC15BF41DF}" {
      Offset 0 1.5 0
     }
     Radius 1.5
     Height 0
    }
   }
   additionalActions {
    GC_MissionInteractionAction "{404C2C5909834FF5}" {
     Duration 3
     VisibilityRange 1.5
    }
   }
  }
 }
 coords 7682.552 79.364 6514.182
}
"""


def course_polygon(layer):
    # The legacy even/odd boundary has two lobes touching at a single crossing.
    # Keep the original edges up to 5 cm from that crossing, joining the lobes
    # inside that disk so the native simple-polygon validator can accept them.
    start = layer.index(' coords 6130.866 71.245 4263.294')
    prefix, rest = layer[start:].split(' Points {\n', 1)
    raw, tail = rest.split('\n }\n', 1)
    positions = re.findall(r'  ShapePoint "\{[A-F0-9]+\}" \{\n   Position ([^\n]+)\n  \}', raw)
    original = re.findall(r'  ShapePoint "[^"]+" \{\n   Position [^\n]+\n  \}', raw)
    if len(positions) != 123 or '\n'.join(original) != raw:
        raise ValueError('unexpected Ruha polygon')
    points = [tuple(map(float, v.split())) for v in positions]
    a, b, c, d = (points[k] for k in (36, 37, 72, 73))
    ux, uz, vx, vz = b[0] - a[0], b[2] - a[2], d[0] - c[0], d[2] - c[2]
    det = ux * vz - uz * vx
    t = ((c[0] - a[0]) * vz - (c[2] - a[2]) * vx) / det
    u = ((c[0] - a[0]) * uz - (c[2] - a[2]) * ux) / det
    if not (0 < t < 1 and 0 < u < 1):
        raise ValueError('Ruha crossing moved')

    def nearby(first, last, fraction, label):
        xyz = ' '.join(f'{v + fraction * (w - v):.5f}' for v, w in zip(first, last))
        return f'  ShapePoint "{{{guid(WORLD + label)}}}" {{\n   Position {xyz}\n  }}'

    dt, du = 0.05 / math.hypot(ux, uz), 0.05 / math.hypot(vx, vz)
    ordered = [*original[:37], nearby(a, b, t - dt, 'A'), nearby(c, d, u - du, 'C'),
               *reversed(original[37:73]), nearby(a, b, t + dt, 'B'), nearby(c, d, u + du, 'D'),
               *original[73:]]
    return prefix + ' Points {\n' + '\n'.join(ordered) + '\n }\n' + tail


def ruha_files(source):
    for name, oid in REVIEWED.items():
        if source.files.get(FOLDER + name) != oid:
            raise ValueError('Ruha source changed; review ' + name)
    paths = [HEADER, HEADER + '.meta', WORLD, WORLD + '.meta', *source.layers[WORLD.lower()]]
    files = {p: source.read(p) for p in paths}
    core = files[FOLDER + 'default.layer']
    fences = re.findall(r'^\s*"(fence_\d+)"$', core, re.M)
    placed = re.findall(r'^ (fence_\d+) \{$', files[FOLDER + 'Fences.layer'], re.M)
    if len(fences) != 44 or set(fences) != set(placed) or len(placed) != 44:
        raise ValueError('Ruha fence list differs from the placed starting gate')
    names = '\n'.join('         "' + n + '"' for n in [*fences, 'GC_RaceStartingZone'])
    ai = core[:core.index('PerceptionManager')]
    props = core[core.index('$grp GenericEntity'):core.index('PS_MissionDescription')]
    zone = core[core.index('SCR_EditorRestrictionZoneEntity'):].replace(
        'SCR_EditorRestrictionZoneEntity :', 'SCR_EditorRestrictionZoneEntity GC_RaceStartingZone :', 1)
    mode = race_mode(WORLD, names, '5513.636 58.224 4518.457', '7689.284 74.265 6514.434',
                     '   m_iStartingHours 7\n   m_iStartingMinutes 30', CLEANUP)
    files[FOLDER + 'default.layer'] = ai + mode + props + zone
    key = FOLDER + 'Players.layer'
    files[key] = files[key].replace('{BF38869730F83577}Prefabs/Characters/Factions/CIV/GenericCivilians/Character_CIV_Randomized_P.et',
                                    '{22E43956740A6794}Prefabs/Characters/Factions/CIV/GenericCivilians/Character_CIV_Randomized.et')
    key = FOLDER + 'Motorbikes.layer'
    files[key] = files[key].replace('}worlds/Perfk/' + BIKE, '}' + BIKE)
    key = FOLDER + 'Helicopterarea.layer'
    helicopter = files[key]
    placements, raw_markers = helicopter.split('$grp PS_ManualMarker', 1)
    markers, count = convert_layer('$grp PS_ManualMarker' + raw_markers)
    if count != 2:
        raise ValueError('expected the two Ruha markers')
    flagpole, vehicles = placements.split('$grp SCR_DestructibleBuildingEntity', 1)
    flagpole = flagpole.split('\n', 1)[0] + '\n coords 7682.552 79.364 6514.182\n}\n'
    files[key] = flagpole + TELEPORT + '$grp SCR_DestructibleBuildingEntity' + vehicles + markers
    key = FOLDER + 'test.layer'
    original = files[key]
    visuals = original[:original.index('PK_EntityDeleter')]
    polygon = course_polygon(original)
    files[key] = visuals + 'GenericEntity GC_RaceRoadblockCenter {\n coords 7719.174 74.849 6504.438\n}\n' + AO + polygon
    return files
