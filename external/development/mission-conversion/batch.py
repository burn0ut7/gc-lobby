"""Apply common migration rules to legacy missions and report unsupported authoring."""
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache, partial
from hashlib import file_digest, sha256
from itertools import chain
from math import isfinite
from pathlib import PurePosixPath
import re
from subprocess import CalledProcessError, check_output

from arc_capture import (CAPTURE_EDITOR_FIELDS, CAPTURE_TRANSFORM, LONGMOOR_CURRENT_PARENT,
                         LONGMOOR_LEGACY_PARENT, capture_instance, capture_messages, capture_status_texts, faction_keys)
from boundaries import (ROOT as AO_SHAPE, BARE_SHAPE, PASSIVE_COVER, STANDALONE_SHAPE,
                        convert_boundaries, convert_instance as convert_boundary_instance, group_members,
                        convert_child_outline, passive_shape_component)
from briefings import QUOTED, block_end, guid, properties, target_names
from catalog import OBSOLETE_NAVMESH, REF, SOURCE_TEXT, VEHICLE_LINK
from commandos import (CONTROLLER_REF as COMMANDOS_CONTROLLER, MANAGER as COMMANDOS_MANAGER,
                       MODE_LAYER as COMMANDOS_MODE_LAYER, OBJECTIVES_LAYER as COMMANDOS_OBJECTIVES_LAYER,
                       WORLD as COMMANDOS_WORLD, audit_objectives as audit_commandos_objectives,
                       objective_flags as commandos_objective_flags, runtime_files as commandos_runtime_files)
from event_actions import (FLAG_NAME, NUMBER, SPAWN, SPAWN_FIELDS, SPAWN_NAME, VEHICLE_SPAWN,
                           render_crew_config, render_spawn, spawn_details)
from vehicle_crew import (BMP1_PARENT, BMP1_TURRET_PARENT, CREW_CONFIG_FIELD, FLORA_CREW_CONFIG, MISSION_BMP1,
                          MISSION_BMP1_TURRET, VERIFIED_DEFAULT_CREW, ZAGORIA_BMP2, crew_config_contents,
                          split_crew_block, verified_core_btr70_transfer)
from events import EVENT, LEGACY_FLAG, peak_flags
from extraction import DEFAULT_STATUS as EXTRACTION_STATUS, condition as extraction_condition
from groups import (CALL, COLOR_TEAM_ARRAY, DYNAMIC_LOD_DISTANCE, INDEX, SLOTS, STATIC_VEHICLE_NAME,
                    UK_1983_AI_ROLES, UK_GROUP_DIRECTORY, UK_PROVIDER,
                    compare_ai_parents, inheritance,
                    legacy_member, reviewed_ai_member_ancestry, reviewed_native_ancestry,
                    unchanged_explicit_playability, verified_stock_ai_group)
from interactions import BLACKBOX, BLACKBOX_FLAG, blackbox_placement, blackbox_prefab
from interactions import (convert_action as convert_interaction_action,
                          convert_block as convert_interaction_block, convert_object)
from launchers import META_NAME, launcher, replace_reference
from local_loadouts import migrate_playability, unchanged_units_parent
from markers import (ALLOWED as MARKER_FIELDS, DEST as MAP_MARKER_PREFAB, LEGACY as LEGACY_MARKER,
                     BASE as LEGACY_MARKER_BASE, convert_baked_marker_generator, convert_hover_camera,
                     convert_layer, convert_wall_markers, instance as marker_instance)
from outcomes import (ASSAD_FRAMEWORK_BLOB, ASSAD_FRAMEWORK_LAYER, CONCRETE_COUNT, CONCRETE_DONE,
                      CONCRETE_FRAMEWORK, CONCRETE_INTEL, CONCRETE_TOWNS, DEPOT_SUPPLY_REVISIONS,
                      FOOD_MARKET_OUTCOME_LAYER, FRAMEWORK, INTERDICTION_OUTCOME_LAYER,
                      INTERSECTING_DEFAULT, INTERSECTING_DEFAULT_BLOB, MATCH_PHASE_VALUE,
                      NAGALAM_AMBUSH_BLOBS, NAGALAM_AMBUSH_LAYERS, NAGALAM_LAYERS,
                      OIL_RIG_POPULATION_AREA, OIL_RIG_POPULATION_FRAMEWORK, OLD_WOOD_SPAWNERS, OLD_WOOD_STEPOFF,
                      RUHA_PHASE_BLOB, RUHA_PHASE_LAYER, RUHA_UNIT_NAMES, SCRIPT_FLAG_APIS,
                      SEEDER_REPLACEMENT_LAYER, SEEDER_REPLACEMENT_LAYER_BLOB,
                      SUPPLY_OUTCOME_BLOB, SUPPLY_OUTCOME_LAYER,
                      TRIFECTA_FRAMEWORK, TRIFECTA_MARKER, TRIFECTA_ROOT, TRIFECTA_STM,
                      leaves, render as render_outcome, render_capture_trackers, render_controlled_event)
import polygon_capture
from respawns import authored_respawn_fields, read_respawns
from terrain_parents import BYSTRANY_LEGACY_PARENT, BYSTRANY_MAP_ENTITY, BYSTRANY_NATIVE_PARENT
import teleports
from scenery import FIELD, NUMBER as COORDINATE, StockPrefabs, WAYPOINT_CLASS
from weather import convert_weather

LEGACY = re.compile(r'\b(?:(?:TILW|PS)_\w+|PK_Entity(?:Deleter|ProtectorComponent))\b')
MODE = re.compile(r'^PS_GameModeCoop(?: [\w.-]+)? : "'
                  r'(?:\{4CFD54745CD45673\}Prefabs/MP/Modes/PS_GameMode_Lobby_(?:GC|TILWMF)\.et'
                  r'|\{9C2001FE7A2F2281\}Prefabs/MP/Modes/PS_GameMode_Lobby\.et)" \{$')
CONSTRUCTION_FIELDS = {
    'm_fPrepDuration': ('m_fPreparationSeconds', 0, 2147483),
    'm_fPrepMultiplier': ('m_fPreparationMultiplier', 0.5, 10000000),
    'm_fGameMultiplier': ('m_fMatchMultiplier', 0.5, 10000000),
}
DEFAULT_BUILDING_MANAGER = '  SCR_CampaignBuildingManagerComponent "{61B4DDBF56BF1B48}" {'
ASSAD_LAYERS = ASSAD_FRAMEWORK_LAYER.removesuffix('framework.layer')
ARCHIVED_AAS_CREW = {
    'E417F82BEA7137D5': ('Rifleman', 'b9f7231eeac59fd931fcf4edb4b62be2b06c7197',
                         '3003343eed710f717c2fd3c9786072fda13d3cc6'),
    'C18E05B3F6ACAB13': ('AT', 'd2cbbfe146c86a98cb93444380e2ae7ce0258fcb',
                         'daf412d61e6dc8a23318bcbd5b2d16f6adbd9e50'),
}
EMPTY_LEGACY_CUTSCENE = [
    '  PS_CutsceneManager "{60E6C661B81E7619}" {',
    '   m_aCutscenes {',
    '    PS_Cutscene "{66276DEFD7DE434C}" {',
    '    }',
    '   }',
    '  }',
]
EMPTY_GROUP_FLAGS = [
    '  SCR_GroupsManagerComponent "{5706EC99934496F5}" {',
    '   m_aGroupFlags {', '   }', '  }',
]
DISABLED_NATIVE_MODE_COMPONENTS = {
    '  SCR_NametagConfigComponent "{571AA9E54FC9210D}" {',
    '  SCR_SupportStationManagerComponent "{5B0AB3A852FCBF7E}" {',
}
NATIVE_END_SCREEN_SUBTITLES = re.compile(
    r'  SCR_GameOverScreenManagerComponent "\{56DEC646298AD219\}" \{\n'
    r'   m_GameOverScreenConfig SCR_GameOverScreenConfig "\{56DEC6462FFDD82D\}" \{\n'
    r'    m_aGameOverScreens \{\n'
    r'     SCR_FactionVictoryGameOverScreenInfo "\{568EB6DFEF507060\}" \{\n'
    r'      m_OptionalParams SCR_BaseGameOverScreenInfoOptional "\{568EB6DFF87D9C84\}" \{\n'
    rf'       m_sSubtitle {QUOTED}\n      \}}\n     \}}\n'
    r'     SCR_EditorFactionGameOverScreenInfo "\{56EED1ED0A9954E6\}" \{\n'
    r'      m_OptionalParams SCR_BaseGameOverScreenInfoOptional "\{56EED1ED10CDB164\}" \{\n'
    rf'       m_sSubtitle {QUOTED}\n      \}}\n     \}}\n    \}}\n   \}}\n  \}}')
LEGACY_JIP_MODES = {'Deny': 'DISABLED', 'Auto': 'AUTO', 'Manual': 'MANUAL',
                    '0': 'DISABLED', '1': 'AUTO', '2': 'MANUAL'}
NATIVE = re.compile(r'^(?:\$grp )?(?:Vehicle|Tank|RHS_Tank|Turret|SCR_AIWorld|GameEntity|GenericEntity|GenericTerrainEntity|GenericWorldFogEntity|StaticModelEntity|Tree|'
                    r'DecalEntity|SCR_DestructibleEntity|SCR_DestructibleTreeV2|SCR_TreeDebrisSmallEntity|SCR_JunctionPowerPole|'
                    r'SCR_\w*(?:Waypoint|BuildingEntity|EnvironmentalEntity|RestrictionZoneEntity)|'
                    r'AIWaypointCycle|PolylineShapeEntity|SCR_MapEntity|ACE_TacticalLadderEntity|LightEntity|Projectile|CommentEntity)\b')
INACTIVE_DOOR_LAYER = 'worlds/TheLocalPub/RailroadReactions/RailroadReactions_Layers/PROPS/TrainArea.layer'
INTERSECTING_FIXTURES = 'worlds/Corax/IntercectingForces/IntersectingForces_Layers/Fixtures.layer'
INTERSECTING_FIXTURES_BLOB = 'c86b8774a4db1d2c3706d8c296dbb996f4d22596'
INACTIVE_DOOR = [
    'GenericEntity : "{212D8A2CE34F802E}Prefabs/Utils/Other/TILW_DoorOpenerEntity.et" {',
    ' coords 6430.397 59.368 6007.69',
    '}',
]
WEATHER = re.compile(r'  SCR_TimeAndWeatherHandlerComponent "\{5EE3229927D4D2F5\}" \{')
INIT_WEATHER = '  SCR_InitWeatherComponent "{61B68227C35BFE9A}" {'
INIT_WEATHER_FIELD = re.compile(
    r'   (?:m_sInitialStartingWeatherId "[\w .-]*"|m_iSetWeatherConstant [01]|'
    r'm_initialWeatherDuration(?:Min|Max) \d+(?:\.\d+)?)')
PERCEPTION = re.compile(r'^PerceptionManager(?: [\w.-]+| "(?P<serialized>[\da-fA-F]{16})")?'
                        r' : "\{028DAEAD63E056BE\}[^"]+" \{$')
SUMMER_DAYS_DEFAULT = 'worlds/Corax/SummerDays/SummerDays_Layers/default.layer'
SUMMER_DAYS_DEFAULT_BLOB = '45d91535504c59cfdf271f36fecaa9f028eccbb2'
SUMMER_DAYS_TOPO = 'worlds/Corax/SummerDays/SummerDays.topo'
PROJECTILE_SOUNDS_MANAGER = 'ProjectileSoundsManager : "{B4DA8B0024536625}Prefabs/World/Game/ProjectileSoundsManager.et" {'
SUMMER_DAYS_MANAGERS = {
    'SCR_DestructionManager : "{E5B570B5F32A7BAE}Prefabs/Systems/Destruction/DestructionManager.et" {',
    'BasePreloadManager PreloadManager1 : "{104F8505FD1871EF}Prefabs/World/Game/PreloadManager.et" {',
    'RadioBroadcastManager : "{66B93BC296E2F977}Prefabs/Radio/RadioBroadcastManager.et" {',
    'SoundWorld : "{8A8604E158704937}Prefabs/Sounds/SoundWorld/SoundWorld_Eden.et" {',
    'SCR_MPDestructionManager : "{9BB369F2803C6F71}Prefabs/MP/MPDestructionManager.et" {',
}
LIGHT = 'LightEntity : "{C274EE54AB618B04}Prefabs/Structures/Core/Lamp_LightEntity_base.et" {'
LIGHT_COMPONENT = [
    ' components {', '  StreetLampComponent "{55A0954A909C9109}" {', '   Enabled 0', '  }', ' }',
]
LIGHT_FIELDS = {'ID', 'coords', 'Color', 'LV', 'ClipEV', 'Radius', 'NearPlane', 'SpotAngle',
                'SpotAngleAttenuation', 'DistanceAttenuation', 'LensFlareType', 'LensFlareScale'}
SERIALIZED_ID = '"\\{[0-9a-fA-F]{16}\\}"'
SPLINE_RECORD = re.compile(
    r'\{|\}|(?:Points|SplinePoints|RoadEntity|Data|m_PrefabNames|'
    r'SplinePointData SplinePointData|PrefabGeneratorEntity) \{|'
    rf'(?:ShapePoint|(?:Data )?SplinePointData|ForestGeneratorPointData|GeneratorType GeneratorType)'
    rf' {SERIALIZED_ID} \{{|'
    r'(?:\$grp )?(?:RoadGeneratorEntity|ForestGeneratorEntity|WallGeneratorEntity|'
    r'PrefabGeneratorEntity|SCR_DestructibleEntity|StaticModelEntity|Tree)(?: [\w.-]+)?'
    r' : "\{[\dA-Fa-f]{16}\}[^"\r\n]+\.et" \{|'
    r'(?:Type|ID) "[^"\r\n]+"|(?:BeginningPiece|EndPiece) none|'
    r'"\{[\dA-Fa-f]{16}\}[^"\r\n]+\.et"|Flags (?:0x[\dA-Fa-f]+ \d+|\d+)|'
    rf'(?:Position|InTangent|OutTangent) {COORDINATE} {COORDINATE} {COORDINATE}|'
    rf'LineColor {COORDINATE} {COORDINATE} {COORDINATE} {COORDINATE}|'
    rf'(?:IsClosedSpline|m_bAvoidObjects) [01]|(?:m_iSeed|RandomSeed) \d+|Width {NUMBER}')
NAVMESH_COMPONENT = re.compile(rf' +TILW_NavmeshRegenComponent {SERIALIZED_ID} \{{')
BLOODY_GARRISON = '3FB7CCCE82C20BD6'
BLOODY_GARRISON_DIR = 'worlds/Jub/OperationArrowheadTwoBloodyMiles/OperationArrowheadTwoBloodyMiles_Layers/'
BLOODY_GARRISON_PREFAB = BLOODY_GARRISON_DIR + 'Building_01.et'
BLOODY_GARRISON_BLOBS = {
    BLOODY_GARRISON_PREFAB: '329fa8d70d0bccfb6990a272f1d34207caec4715',
    BLOODY_GARRISON_PREFAB + '.meta': '6cee2ac3d425c1bb7410dbfb7d6d506f1f130f11',
    BLOODY_GARRISON_DIR + 'AI/GreenAI.layer': '8db56904dc732aef35e77795b786e0cb5b38bad0',
}
DAMAGE_COMPONENT = re.compile(rf'  TILW_Flag_EntityDamage {SERIALIZED_ID} \{{')
DAMAGE_TARGETS = {
    '82CCE96E35DC6A6E': 'Prefabs/Structures/Military/Antennas/Antenna_BARS_01/Antenna_BARS_01_transmitter.et',
    '78CCABFAE4DC21EF': 'Prefabs/Props/Military/MilitaryCrates/CrateStack_01/Dst/CrateStack_01_Dst_2_dark.et',
    '7D39683D0F8EDB59': 'Prefabs/Props/Military/MilitaryCrates/CrateStack_01/Dst/CrateStack_01_Dst_3_dark.et',
    'D042CEA90B8CB14D': 'Prefabs/Props/Military/MilitaryCrates/CrateStack_01/CrateStack_01_decals_02.et',
    '2C83CD194C70DA9C': 'PrefabsEditable/Auto/Props/Military/SupplyBox/SupplyStack/SupplyStack_Large_01/E_SupplyStack_Large_01_storage.et',
    '5F76887813B86AD4': 'Prefabs/Props/Military/AmmoBoxes/EquipmentBoxStack/USSR/EquipmentBoxStack_USSR_01_V6_covered.et',
    '2FCB46C395C9CBE7': 'Prefabs/Props/Airport/GeneratorAirfield_USSR_01/GeneratorAirfield_USSR_01.et',
    '6597847D2AC253F5': 'Prefabs/Props/Military/Generators/GeneratorMilitary_USSR_01/GeneratorMilitary_USSR_01.et',
    # The installed truck variants inherit SCR_WheeledDamageManagerComponent.
    '1BABF6B33DA0AEB6': 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_command.et',
    '4C81D7ED8F8C0D87': 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_tanker.et',
    '3336BE330C4F355B': 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_ammo.et',
    'A5647958579A4149': 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_repair.et',
    '36BDCC88B17B3BFA': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_command.et',
    '38186F12F27A61C6': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_command_MERDC.et',
    '26013123314183EA': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_tanker_MERDC.et',
    'C8656AECF5DF41D9': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_repair_MERDC.et',
    'EC657C9B3C73E192': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_ammo_MERDC.et',
    'E6AEC014D162D686': 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_FIA_command.et',
    '1C5FE7B7FF49BB8D': 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_Base.et',
    '254289B9C09904AB': 'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2.et',
    '5729759DD092B0F7': 'Prefabs/Vehicles/Wheeled/S1203/S1203_cargo_randomized.et',
    # The Huey and Mi-8 chains inherit native helicopter damage managers.
    '70BAEEFC2D3FEE64': 'Prefabs/Vehicles/Helicopters/UH1H/UH1H.et',
    'CB4D4CF7E887B2D0': 'Prefabs/Vehicles/Helicopters/UH1H/UH1H_armed_gunship_HEDP.et',
    'DDDD9B51F1234DF3': 'Prefabs/Vehicles/Helicopters/UH1H/UH1H_armed.et',
    '21E9A875C0A3C409': 'Prefabs/Vehicles/Helicopters/UH1H/UH1H_armed_gunship_HE.et',
    '3133602B406D131C': 'Prefabs/Vehicles/Helicopters/UH1H/UH1H_armed_gunship_HEDP_sharkNose.et',
    '7BD282AF716ED639': 'Prefabs/Vehicles/Helicopters/Mi8MT/Mi8MT_armed.et',
    # These stock character chains inherit SCR_CharacterDamageManagerComponent.
    '42A502E3BB727CEB': 'Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_HeliPilot.et',
    '5117311FB822FD1F': 'Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Officer.et',
    'B3AB6D12D247DDB5': 'Prefabs/Characters/Factions/CIV/Businessman/Character_CIV_Businessman_Randomized.et',
    'E024A74F8A4BC644': 'Prefabs/Characters/Factions/CIV/Businessman/Character_CIV_Businessman_1.et',
    'A517C72CEF150898': 'Prefabs/Characters/Factions/CIV/Businessman/Character_CIV_Businessman_2.et',
    '626874113CAE4387': 'Prefabs/Characters/Factions/CIV/Businessman/Character_CIV_Businessman_3.et',
    'FE65E8C60C751352': 'Prefabs/Characters/Factions/INDFOR/FIA/Character_FIA_PL.et',
}
DAMAGE_FLAG_NAME = re.compile(r'"[\w .-]+"')
SPAWN_DAMAGE_TARGETS = {
    ('ADA7E056B17AC663',
     'PrefabsEditable/Auto/Props/Military/AmmoBoxes/EquipmentBoxStack/E_EquipmentBoxStack_USSR_01_V2_covered.et'),
}
CAMPAIGN_RADIO_TRUCK = ('CA75B7173C19B734', 'Prefabs/MP/Campaign/Assets/CampaignRadioTruckEast.et')
SPAWN_DAMAGE_VEHICLES = {
    ('0FBF8F010F81A4E5', 'Prefabs/Vehicles/Wheeled/LAV25/LAV25.et'),
    ('48A6D4372444B85A', 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_transport_covered_closed.et'),
    CAMPAIGN_RADIO_TRUCK,
}
SEEDER_REPLACEMENT_PARENTS = {
    ('259EE7B78C51B624', 'Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et'),
    ('43C4AF1EEBD001CE', 'Prefabs/Vehicles/Wheeled/UAZ452/UAZ452_ambulance.et'),
    ('1FBB492E86002BF5', 'Prefabs/Vehicles/Wheeled/UAZ452/UAZ452_transport.et'),
    ('A5647958579A4149', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_repair.et'),
    ('4C81D7ED8F8C0D87', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_tanker.et'),
    ('1BABF6B33DA0AEB6', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_command.et'),
    ('7BD282AF716ED639', 'Prefabs/Vehicles/Helicopters/Mi8MT/Mi8MT_armed.et'),
}
SEEDER_RHS_REPLACEMENT_PARENTS = {
    ('00AED68EFBF251BA', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_transport_covered_closed_camo1.et'),
    ('323B9051ED5C2F7A', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_engineer_camo1.et'),
}
SEEDER_REPLACEMENT_DIR = 'worlds/HitTheMan/Seeder Cold War Domination/Prefabs/Respawn Vehicles/'
SEEDER_TRUCK_BLOBS = {
    'Transports/Ural_Truck_1.et': 'e5b81108b638698b7f314ec836613b8abfbacd79',
    'Transports/Ural_Truck_2.et': 'df71a1b99ccce5d8e5314d3ae1333fb8ef40bbf8',
    'Transports/Ural_Truck_3.et': '4c9eac480b3ff495a928dea0078292e3123ed0f2',
    'Ural4320_engineer_camo1.et': 'c0ab3ac1cebe4196823a7f2e415ffa2922626ee7',
    'Ural4320_engineer_camo2.et': 'f48c0d2f0c5835bcab2b021cdeea8706cbe9b9ca',
    'Ural4320_repair1.et': 'f44e331d7f69dd21eaabfd2c3caed92efa9a3d4e',
    'Ural4320_repair2.et': 'f7dc7f44cf3f299f90fd30c8862e2584c261d85b',
    'Ural4320_tanker1.et': '77c6a1fa70c4ab57b759532828f542bfd8ea2627',
    'Ural4320_tanker2.et': '00fdc7f3552e9cc7a4d440958c90fd3530f34678',
    'Ural4320_command1.et': 'b7355e23d8ae23c181311d387a86c93eea80e4b5',
}
SEEDER_COMMAND_TRUCK = SEEDER_REPLACEMENT_DIR + 'Ural4320_command1.et'
SEEDER_COMMAND_MARKER = SEEDER_REPLACEMENT_DIR + 'CommandTruckMarker.et'
SEEDER_COMMAND_ANCHOR = SEEDER_REPLACEMENT_DIR + 'TPTruck.et'
SEEDER_BUILDABLE_MI8 = SEEDER_REPLACEMENT_DIR.removesuffix('Respawn Vehicles/') + 'Buildable Vehicles/Mi8MT_armed_Domi.et'
SEEDER_BUILDABLE_MI8_BLOB = 'c9a97e9b92959800a1d841f02d9d10f632f8bc66'
SEEDER_MI8_MARKER = SEEDER_REPLACEMENT_DIR + 'Helicopter1Marker.et'
SEEDER_VEHICLE_CHILDREN = {
    SEEDER_COMMAND_MARKER: 'b29d1cbfff1577e9e6a33e67ce5944a40845e9b1',
    SEEDER_COMMAND_ANCHOR: 'f2b93071267b1a2c01982db8a168648af90bd32e',
    SEEDER_MI8_MARKER: 'e074713aeee9a3765db6fb47e491bcaebb404995',
}
SEEDER_CARGO_BLOBS = {
    'HTMEngineeringBox.et': '1b69602de123e1f107b734540d9f1cfc71af01cc',
    'HTMRepairingBox.et': 'a3c6d12d51fd7897f470b56a8320379e29615ee7',
    'HTMTransportBox.et': '81ea34c7b0f95770586d29a4da20d32d914fe7f9',
    'HTMTransportBoxSupply400.et': '22d358e27225f592878199559853520c5e96e549',
    'HTMTransportBoxSupply500.et': '79552770d7d8e7189ba973ed53f440deb73158c8',
}
SEEDER_WORLD = SEEDER_REPLACEMENT_LAYER.removesuffix('_Layers/default.layer') + '.ent'
SEEDER_PLAYER_VEHICLES = SEEDER_REPLACEMENT_LAYER.replace('default.layer', 'OPFOR_Players/OPFOR_Players.layer')
SEEDER_RECON_BLOBS = {
    SEEDER_REPLACEMENT_DIR + 'BRDM2_AFRF1.et': ('108b93125c89559423ee955430f654e51d8fd88d', 'Rec1'),
    SEEDER_REPLACEMENT_DIR + 'BRDM2_AFRF2.et': ('e6313ca4f968ffc619e6cf578ba34ac8b335176f', 'Rec2'),
}
SEEDER_REWARD_DIR = SEEDER_REPLACEMENT_DIR.removesuffix('Respawn Vehicles/') + 'Reward Depots/'
SEEDER_REWARD_BLOBS = {
    'Helipad_Domi_Tier1.et': '40698fa013043b94ac2cb1d71f34248ea3cbff8f',
    'Helipad_Domi_Tier2.et': '4c2111382400132b510959a5a6b0d9b03083879a',
    'SupplyReward.et': '0a2cf5414293304606545da82779e936805543a1',
    'VehicleDepot_Domi_Tier1.et': '15998deb05b7168a97ac9765751a1055ae590042',
    'VehicleDepot_Domi_Tier2.et': '631d2932bb522a813ab74c0012976c2be8fcc238',
    'VehicleDepot_Domi_Tier3.et': '4a283f1e534f50812ff9d3aa16609c8dce618d52',
}
SEEDER_OBJECTIVE_DIR = SEEDER_REPLACEMENT_DIR.removesuffix('Respawn Vehicles/') + 'Objectives/'
SEEDER_OBJECTIVE_BLOBS = {
    'Brambledeep_Objective.et': '847b5282998d19c57d8243b41951638eb10eec48',
    'Kamoryn_Objective.et': 'ff56cad3317a5b3fa718453d711fbee4c78f3d11',
    'NovaOlesov_Objective.et': '65d9819fb10a401a559e70cad5ced1df4def8aac',
    'Pasastjen_Objective.et': 'cdcd69628be2c1f5e6b8ea175efc0c93fb3cc9cc',
    'Side Objectives/SideObjCaches.et': 'd781fd420568c315361db419b468d3a1238eb6a5',
    'Side Objectives/SideObjKillOfficer_1.et': '78b3dc112d8cf0923649d38b71ab5b9235ce3ea8',
    'Side Objectives/SideObjKillOfficer_2.et': 'a5f91c5fd1d6b74c9c787e9d169a11c862bbde64',
}
SEEDER_OBJECTIVE_ASSETS = {
    'AntennaMarker.et': 'e3a1180ebbe2eb10382129a981aa84f086dfc3aa',
    'Rewards/Seperatists_Supplies_Markers.et': '4366672856579a2bcb827b1f3173c2a4829e72af',
    'Rewards/Seperatists_Supplies_Markers_Road.et': '4c326a218c9fe229e8d09fe1f675854598e39eb5',
    'Rewards/Seperatists_Supplies.et': '080b41899fde26d21ff3fc432599ed5bd7c65c34',
    'Rewards/Seperatists_Supplies_Road.et': '14a02de64f76af20484af4961ab89233224dc3c7',
}
SEEDER_OBJECTIVE_ROOT = SEEDER_OBJECTIVE_DIR.removesuffix('Objectives/')
SEEDER_OFFICER = SEEDER_OBJECTIVE_DIR + 'Side Objectives/Character_SEP_1990_PL_DomiOfficer.et'
SEEDER_OFFICER_BLOB = '4a3249c46e91d5b65ae11b93a280e9f101f45e08'
SEEDER_CHARACTER_PATHS = {
    '140B6687EDDBF1FC': 'Crew',
    '26F7A621B2020C0F': 'SL',
}
GMFX_AMMO_TARGETS = (
    ('8B9AFAD3A1548FCE', 'Prefabs/Destructible/AmmoStorage/GMFX_AmmoStorage.et'),
    ('4C931B452C796A2A', 'Prefabs/Destructible/AmmoStorage/GMFX_AmmoStorage_USSR.et'),
)
GMFX_PROVIDER = 'GameMasterFX_5994AD5A9F33BE57'
GMFX_DIGEST = '09a2e7a14300b916b1eaea43446d39d1d3334209e692e50d29406233962f7c80'
COBRA_PROVIDER = 'WCS_AH-1S_64CB39E57377C861'
COBRA_DIGEST = '683b318ce93cabf75ae5b3b2fe0a1ce49f30e76a693e3c959e353a6bc51da3f4'
COBRA_DAMAGE_PARENT = ('56E88FE5DDF6AA12', 'Prefabs/Vehicles/Helicopters/AH1S/AH1S_Base.et')
HIND_PROVIDER = 'WCS_Mi-24V_628933A0D3A0D700'
HIND_DIGEST = 'dd565719ee18758cc875dca490b3d05dd19a002df0a1a4e4af90767d9e91b4f8'
HIND_DAMAGE_PARENT = ('65ED07118C75B32F', 'Prefabs/Vehicles/Helicopters/Mi24/Mi24V_Armed_UPK23_X2.et')
GMFX_AP_MINE = ('E1BBAB7F28FE8B0B', 'Prefabs/Systems/Mines/Mine_AP.et')
DEPLOYED_PARACHUTE = ('9ADD554959C09197', 'Prefabs/DeployedParachutes/Core/DeployedParachuteMK4.et')
WORKSHOP_DAMAGE_TARGETS = {
    COBRA_PROVIDER: (COBRA_DIGEST, (COBRA_DAMAGE_PARENT,)),
    HIND_PROVIDER: (HIND_DIGEST, (HIND_DAMAGE_PARENT,)),
    'WCS_M2A2_63120AE07E6C0966': (
        '7544977a28637283cf8ae8830f4efeaba71688b06efc872c79344c7f0263a121',
        (('25BDE6AB2921AB8A', 'Prefabs/Vehicles/Tracked/M2A2/M2A2_BUSK_OD.et'),),
    ),
    GMFX_PROVIDER: (
        GMFX_DIGEST,
        (*GMFX_AMMO_TARGETS,
         ('6B52C6B5FEC3E369', 'Prefabs/Destructible/GMFX_Destructible_AmmoDump.et'),
         ('FFCDFB1375A99125', 'Prefabs/Destructible/GMFX_Destructible_RPL5_01_generator.et')),
    ),
    'ZU-23_5C9AD9EF76F6B5EA': (
        '5f647d652a2104e0570f662f2d8199ef2aedea5837810b03bac91eada81a86a2',
        (('FD109F3702AAB392', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_ZU23.et'),),
    ),
    'RHS-StatusQuo_595F2BF2F44836FB': (
        '0aa031f2f9103e4663b99698a8712d2bb9a35601a0a8611e0acb2680cc2e46da',
        (('2C95509CA3E61DE1', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_command_camo1.et'),),
    ),
    'SikorskyH60Project_60ED3CC6E7E40221': (
        '01e5390c986af0406bd5134fbcf5ff890f28d0171bfb0bc9b14347f0d8114bca',
        (('47F2921E0DACA300', 'Prefabs/Vehicles/Helicopters/AUS_MH60/AUS_UH60_Supply.et'),
         ('48F288F5BFF10D8B', 'Prefabs/Vehicles/Helicopters/AUS_MH60/AUS_UH60_armed.et')),
    ),
    'WCS_AH-6M_6273146ADFE8241D': (
        'a2784b9f538456cd3fcb1f14d00819db1b868e6cad2d3e18664fe30ea76ccf56',
        (('AF950919C81D91B4', 'Prefabs/Vehicles/Helicopters/AH6M/MH6M.et'),),
    ),
}
WORKSHOP_SPAWN_PREFABS = {
    'WCS_Armaments_629B2BA37EFFD577': (
        '11a4b537297275db86e7b2773df7770b893d5d1f1d5d11ad928d4fbc141a072e',
        (('962C400354E4BD6C', 'Prefabs/Weapons/Launchers/9K38_IGLA/Launcher_9K38_IGLA.et'),),
    ),
    'ParachuteFramework_65930CB4CD0237B2': (
        'ccb22523ba0040cb9d9206a7ab0ae7e5b4411b1704bde39e3e4a7953416e3042',
        (DEPLOYED_PARACHUTE,),
    ),
    'ArmaTerrainCore_60B524F7A5AE96A8': (
        '28574b99f5d6a12a7199a29d94194081b196b8ca37d854895fe296763cba778c',
        (('6650B2B969248F4C', 'Prefabs/Structures/Rock/Afghan_Rock/GraniteCliff_Afghan_03.et'),
         ('B67A4797AED7B3BC', 'Prefabs/Structures/Wreck/ikarus.et')),
    ),
    'Motorbike_61B84088181A8FA4': (
        '685d9beda81c9a52b79650a5ad42341cd7341370e5f4f77db5db6d49f55901ed',
        (('F22B338BFAC1FD68', 'Prefabs/Vehicles/Wheeled/Motorbike/motorbike.et'),),
    ),
    'M113APC_5E5C154FEE1094BB': (
        'cbe2ae6f2454452f819c58d9becf0d2321add8e85f1a5c40172d08917d4a0cb1',
        (('0A41E8DF1F680496', 'Prefabs/Vehicles/Tracked/M113/M113A2_APC.et'),),
    ),
    'BritishForces_5AE50EC5B8D6F4AE': (
        None, (('775DFB7F4529A122', 'Prefabs/Weapons/Rifles/L42A1/Rifle_L42A1.et'),),
    ),
    COBRA_PROVIDER: (
        COBRA_DIGEST,
        (('C8C37964483452EA', 'Prefabs/Vehicles/Helicopters/AH1S/Army/AH1S_Army_M261_X4.et'),),
    ),
    'RHS-StatusQuo_595F2BF2F44836FB': (
        None,
        (('1A486F1C4D22A32A', 'Prefabs/Vehicles/Tracked/T14/VehParts/Seats/T14_seat.et'),
         ('F0C80F54A089F40B', 'Prefabs/Vehicles/Airplanes/FA18A/CAS_Spawner_GBU12.et'),
         ('EDD3E14E481BDA62', 'Prefabs/Vehicles/Airplanes/FA18A/FA18A_GBU_CAS.et'),
         ('17B08DA4B050BBFE', 'Prefabs/Vehicles/Missles/kh55/KH55_CAS.et'),
         ('EAD777F4CDA11F16', 'Prefabs/Vehicles/Airplanes/F22/F22_Flyby.et'),
         ('FDD01BF3CEAB37E3', 'Prefabs/Vehicles/Airplanes/SU57/SU57_Flyby.et'),
         ('123595894023C474', 'Prefabs/Vehicles/Wheeled/LAV25/LAV25_RHS_D.et')),
    ),
    GMFX_PROVIDER: (
        GMFX_DIGEST,
        (('DDDDBEC77B49A995', 'Prefabs/Systems/Explosions/Wrapper_Bomb_Huge.et'),
         ('9016E09E91956B2E', 'Prefabs/Systems/Explosions/Wrapper_Bomb_Medium.et'),
         ('4BE47BA2B7E3877E', 'Prefabs/Systems/Fire/Wrapper_Fire_Large_Damage.et'),
         ('93FC2145F05A1EEE', 'Prefabs/Systems/Sound/sound_airraidsiren.et'),
         ('30263B4F1FB2A0F2', 'Prefabs/Systems/Artillery/ArtilleryWrapper_WTF.et'),
         ('FC359D25E07FB239', 'Prefabs/Systems/Explosions/BombWarhead_TacNuke_Init.et'),
         ('2D854F52D8D6B31E', 'Prefabs/Systems/Smoke/Wrapper_Smoke_Purple.et'),
         ('6BDBEFF5CE6E2F28', 'Prefabs/Systems/Smoke/Wrapper_Smoke_Green.et'), GMFX_AP_MINE),
    ),
}
WORKSHOP_SPAWN_GROUPS = {
    '6B5_652CFAA0BB9EFE74': (
        '601bb470309242c82429bf6956a9eaf8e6b62d84f10c946d36cbbfbe29da6710',
        (('8A265F54C454DD93', 'Prefabs/Groups/OPFOR/Group_USSR_ManeuverGroup_Late.et'),),
    ),
}
BRITISH_1983_GROUPS = {
    '4679AD5C647BBE6A': ('InfantrySection', (
        'FFB42452E9AD5FFC', '8C380664085E4A89', 'E06C3284B49AC5A7', 'F5B9FF92DBFB520C',
        'F5B9FF92DBFB520C', '2C69231254D1AEDB', 'D005E20AE7CB8431', '327FBCA6B02BC5A8',
    )),
    '7EA45A04944D66B8': ('Team_AT', ('8C380664085E4A89', 'E06C3284B49AC5A7')),
    'A54348BD64AE32BB': ('GunGroup', ('2C69231254D1AEDB', 'D005E20AE7CB8431', '327FBCA6B02BC5A8')),
}
UK_1989_RIFLE_SECTION = (
    'FB08033A33E826D5', UK_GROUP_DIRECTORY + '1989/Light Infantry/Group_UK_1989_REG_RifleSection.et')
UK_1989_RIFLE_SECTION_BLOBS = ('57dcd8d9d38e0253cc87f0bbaa2b859d092dbe63',
                               '751db058fa3720d7ec29183d3ba6c2f7a896d29a')
BRITISH_1989_FALLBACK_GROUPS = {
    'A0C411E558DF236F': 'Regulars_GunGroup',
    '756BBB58355968B1': 'Regulars_PlatoonHQ',
    '711B73BBB94DAEF8': 'SpecialForces_Patrol',
}
BRITISH_1989_FALLBACK_ROLES = {
    '7D7C6FBCBA40F45D': 'Regulars/SectionCommander', 'F9E2E97FA21AA133': 'Regulars/Section2IC',
    'D0F124C1610D3232': 'Regulars/LSW', 'B10659FFD99279DC': 'Regulars/Rifleman_LAT_LAW80',
    '4AF2023E868540E7': 'Regulars/Rifleman', 'F487760AB6777BF3': 'Regulars/PlatoonLeader',
    'B9CFB916A26682D6': 'Regulars/Sergeant', '323D2A366A8DAEED': 'Regulars/RTO',
    '59DC74EF0ECF7E49': 'Regulars/Medic', '6FAAF2B4A124BA7F': 'SpecialForces/SL',
    '6471B09A37F58FD4': 'SpecialForces/RTO', '58BD727F9736FC19': 'SpecialForces/Medic',
    'CF564BD6D0DA773A': 'SpecialForces/LMG',
}
METSALA_VEHICLE_LAYER = 'worlds/Til/MetsalaAirfield/MetsalaAirfield_Layers/ru.layer'
METSALA_VEHICLE_BLOB = 'd2b64a9a0d8d2081238c2aff1ee520b4b42a6b61'
METSALA_HIND = '{658B80A790C7EFA7}Prefabs/Vehicles/Helicopters/Mi24/Mi24V_armed_9M114.et'
RHS_DAMAGE_PATHS = {
    '52B13341B4A2F012': 'Prefabs/Vehicles/Tracked/2S1/Tank_2S1_Green.et',
    'D8136D90BE12445F': 'Prefabs/Vehicles/Tracked/2S1/Tank_2S1.et',
}
LOCKED_RHS_DAMAGE = ('BA824F5F67636A44', 'Prefabs/Vehicles/Tracked/2S1/Tank_2S1_locked.et')
RHS_DAMAGE_OVERRIDES = {
    'BA824F5F67636A44': '615db277a484e78ea645f1bca85b5a9fe01df4b3a19a60768051f7a696624fb5',
    'D8136D90BE12445F': '19ac6eea43f0794f1e347e142330c6eaf31ad37df5153ddcd62bca337514cba8',
}
UNITS_DAMAGE_TARGETS = {
    ('AAB3119124C9167A', 'Prefabs/Vehicles/Wheeled/BTR70/USSR/BTR70_USSR.et'): (
        '3724095bc0b5a778965bfe0b7cc5ad98fec11aee',
        ('C012BB3488BEA0C2', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70.et'),
        None,
    ),
    ('C90019A0A487DDD3', 'Prefabs/Vehicles/Helicopters/AUS_MH60/GC_AUS_MH60_armed.et'): (
        '53f4101fcfe91ad91ddaafecf1edea47008072f6',
        ('48F288F5BFF10D8B', 'Prefabs/Vehicles/Helicopters/AUS_MH60/AUS_UH60_armed.et'),
        '6de8976c505309efa5a6a95be9fd09d90e5dde69',
    ),
}
REVIEWED_HIT_ZONES = {
    ('5CFD8CB5C3E55A4E', 'Prefabs/Vehicles/Tracked/BFV/M2A2_MERDC.et', '"Engine_01"'),
}
REVIEWED_DAMAGE_FLAG_ZONES = {
    ('70BAEEFC2D3FEE64', 'Prefabs/Vehicles/Helicopters/UH1H/UH1H.et'): {'"Engine_01"', '"Hull"'},
}
# The published CIV group chain and base group have no damage manager.
DAMAGELESS_GROUPS = {
    '0C8BEF088C9D62CA': ('Prefabs/Groups/CIV/Generic/P/Group_CIV_Generic_P.et',
                         '7cb828f03ecaa39cd7f01693a4c0b653baa2e631216be0fa5095cf4efb76ad63'),
}
PLACED_SPAWNER = re.compile(
    r'(?P<group>\$grp )?TILW_PrefabSpawnerEntity(?: (?P<name>[\w.-]+))? : '
    r'"\{7F10372CF1CA1175\}Prefabs/Utils/TILW_PrefabSpawner.et" \{')
BARE_SPAWNER = re.compile(r'(?P<group>\$grp )?TILW_PrefabSpawnerEntity(?: (?P<name>[\w.-]+))? \{')
REVIEWED_BARE_SPAWNER_LAYERS = {
    ('worlds/Corax/TF141/COOPXX_Loose Ends_V1/COOPXX_LooseEnds_V1_Layers/AI.layer',
     'f5d07d2c876a4ffae1a82146a65e5fabed52b5ea'),
    ('worlds/Cunnah/Local_Problems/Local_Problems_Layers/Bomber.layer',
     '3453aac53509f5ad8d27bfa143ce994614376940'),
    ('worlds/Cunnah/Local_Problems/Local_Problems_Layers/INDFOR_AI.layer',
     'b3bd80efa9adf365db55b1bfa8e45413b9fed6f6'),
    ('worlds/Cunnah/Local_Problems/Local_Problems_Layers/OPFOR_AI.layer',
     '90bf857673479e4a3eb2ebe70a561d4a1a40ca47'),
}
SPAWNER_TRANSFORM = {'coords', 'angles', 'angleX', 'angleY', 'angleZ'}
STOCK_TERRAIN_PARENTS = {
    'worlds/Arland/Arland.ent': 'A9806AF617972E97',
    'worlds/Eden/Eden.ent': '853E92315D1D9EFE',
}
REVIEWED_CAPACITY = {
    'Missions/arc/CommandosReforged.conf': (
        '0869d9b0064e352e3963157fb2431b0ecbbbc805',
        tuple((f'worlds/arc/CommandosReforged/CommandosReforged_Layers/players_{side}.layer', blob)
              for side, blob in (('us', '5d85e00025f1fb2a1635c6c75f1dd9bcd04e9db2'),
                                 ('ru', 'c570e980592b6fae8842b3da616af89dc8c1947b'))),
        60, 62,
    ),
    'Missions/HitTheMan/Beach Holiday to Mineraly/BeachHolidaytoMineraly.conf': (
        '0efea21f96214b6ff48a003b938cf07f950b5102',
        tuple((f'worlds/HitTheMan/Beach Holiday To Mineraly/BeachHolidaytoMineraly_Layers/{side}_Players/{side}_Players.layer',
               blob) for side, blob in (('BLUFOR', '2cdfdee7c12327f6e112cd50461a96f2b8b68bc8'),
                                        ('OPFOR', '6e23f1042beeb98dcf7b5b083709ed360f9be97c'))),
        110, 90,
    ),
    'Missions/TheLocalPub/OilRigRangos.conf': (
        '3cbbf3fea039dc0f84d984fe4123d5cf63b807c7',
        tuple((f'worlds/TheLocalPub/OilRigRangos/OilRigRangos_Layers/{layer}', blob) for layer, blob in (
            ('BLUFOR/Players.layer', '18f1b11ab72cf4545b14dce051bfc371314aba06'),
            ('CIV/Player.layer', '35e1818db34359182dc442584649590ebc77fea1'),
            ('OPFOR/Players.layer', '2cd001153af4e918b92394e7a32176f51a803b0f'),
        )),
        47, 45,
    ),
    'Missions/Jub/OperationArrowheadMulladostPass.conf': (
        'cb25c94860ef584a0110dc41ccfeb81c576bf4dd',
        (('worlds/Jub/OperationArrowheadMulladostPass/OperationArrowheadMulladostPass_Layers/players.layer',
          '813e4a817cf1725869d94733a867f4e58c746da5'),),
        47, 49,
    ),
    'Missions/Til/Countdown.conf': (
        'ea0942570cfe1205ef4560f10a72c23a337f9a27',
        tuple((f'worlds/Til/Countdown/Countdown_Layers/players_{faction}.layer', blob) for faction, blob in (
            ('civ', '5c6f75ed36b83f67431860cea74911b4d73c8399'),
            ('sep', '514a44275b4df2160b20b88ad52c18e9d8663b5d'),
            ('us', '39f94f05d58ca89a237aa71928eb6d2cf24ce1c5'),
        )),
        53, 50,
    ),
    'Missions/HitTheMan/Seeder Cold War Domination/ColdWarDomination.conf': (
        'bc6197c01e5c71e08c4a6ca62b7e7fc090a8202f',
        ((SEEDER_PLAYER_VEHICLES, 'c3da94fe6583b635ef99a100b023a5fe2be32393'),
         (SEEDER_REPLACEMENT_LAYER, SEEDER_REPLACEMENT_LAYER_BLOB)),
        79, 71,
    ),
    'Missions/HitTheMan/Air Assault Onto Skalisty/AirAssaultOntoSkalisty.conf': (
        '72af1b267a4138d2d5dd82595b69a6dc81c5a0d0',
        (('worlds/HitTheMan/Air Assault Onto Skalisty/AirAssaultOnSkalisty_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '1891597489728b5a2bc4f2dd101468445571d0ea'),
         ('worlds/HitTheMan/Air Assault Onto Skalisty/AirAssaultOnSkalisty_Layers/OPFOR_Players/OPFOR_Players.layer',
          'fc23379018f367731dde8cd1214c4b800c72f0e5')),
        79, 60,
    ),
    'Missions/HitTheMan/Battle For Zarichne/BattleForZarichne.conf': (
        'ea65ffeca620e32bfc7abdc15a9fa252f50b2ace',
        (('worlds/HitTheMan/Battle For Zarichne/BattleForZarichne_Layers/NVA_Players/NVA_Players.layer',
          '8e7f98f6ebcf5e792f583614bee4bd0472b86b42'),
         ('worlds/HitTheMan/Battle For Zarichne/BattleForZarichne_Layers/US_Players/US_Players.layer',
          '2216e12d652b1340978cbd5cc4b652b262aec963')),
        110, 80,
    ),
    'Missions/arc/UrbanTroubles.conf': (
        '44c5ff26490d165afbcaab0a572682cb99679a57',
        (('worlds/arc/UrbanTroubles/UrbanTroubles_Layers/playersCIV.layer',
          'a43c1088d778b3e3f5a4a0248fcab32a4963297d'),
         ('worlds/arc/UrbanTroubles/UrbanTroubles_Layers/playersIRA.layer',
          '5942013d9033e51abfe00b290ba22887dc5e4041'),
         ('worlds/arc/UrbanTroubles/UrbanTroubles_Layers/playersUK.layer',
          'da8502c0627c7a7996270c53a0b756dd843960f8')),
        60, 61,
    ),
    'Missions/Til/DaybreakDiplomacy.conf': (
        '83caf41bad5cbdd497a9809a7cff64982cfa0dac',
        (('worlds/Til/DaybreakDiplomacy/DaybreakDiplomacy_Layers/extr.layer',
          '3bbe183980ea461867e95558fe4849bee15bbf15'),
         ('worlds/Til/DaybreakDiplomacy/DaybreakDiplomacy_Layers/us.layer',
          '10a67b56893ab13cf6ff18e5ba5b555f00b216a5')),
        53, 64,
    ),
    'Missions/HitTheMan/Screening The Orb/ScreeningTheOrb.conf': (
        '518b91880a094abbdcbafdc5ea04246fdb386f8e',
        (('worlds/HitTheMan/Screening The Orb/ScreeningTheOrb_Layers/OPFOR/OPF_Players.layer',
          '1dcf3a3855fe0d3c30702eea18cb2e8570f89b40'),),
        79, 69,
    ),
    'Missions/TemplateHeader.conf': (
        'dd0c95da8fe67c84206139b954d9a86006c19735',
        tuple((f'worlds/TemplateMission/TemplateMission_Layers/{faction}/Players.layer',
               'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391')
              for faction in ('BLUFOR', 'CIVFOR', 'INDFOR', 'OPFOR')),
        30, 0,
    ),
    'Missions/TheLocalPub/BarnyardBashers.conf': (
        '045f48cbb044d096c67f9700fe54e32e291314a1',
        (('worlds/TheLocalPub/BarnyardBashers/BarnyardBashers_Layers/BLUFOR/Players.layer',
          'de8d2200b9d045879dfc67ef7aabbeac2cb941d9'),
         ('worlds/TheLocalPub/BarnyardBashers/BarnyardBashers_Layers/OPFOR/Players.layer',
          '116ac725b1f73bb63c234aa961de80de448f0aa4')),
        36, 35,
    ),
    'Missions/Til/OwlsPrey.conf': (
        '4f5817d0d3f1824f202cc6e0722dfffe2b99751b',
        (('worlds/Til/OwlsPrey/OwlsPrey_Layers/players.layer', '927a4841ce88685181a781b1f26c6c1838504977'),),
        38, 37,
    ),
    'Missions/blackskimask/ZarichneFront.conf': (
        'b543004e58932c8a1864fa33d592c074a27f332b',
        (('worlds/blackskimask/ZarichneFront/ZarichneFront_Layers/players_ru.layer', 'ae2d0e8f28285129561a0b7d9d9330f38272a45d'),
         ('worlds/blackskimask/ZarichneFront/ZarichneFront_Layers/players_uk.layer', '2cc29592391afecab4ed0f0ce8990321323f9d23')),
        65, 67,
    ),
    'Missions/HitTheMan/Breaking Into Nova Olesov/BreakingIntoNovaOlesov.conf': (
        '81f0da74b2f471ccca064ebce5f9cc5b5c94117a',
        (('worlds/HitTheMan/Breaking Into Nova Olesov/BreakingIntoNovaOlesov_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '637ed0b5f922e8e69944bc3e3f10c2634c66221b'),
         ('worlds/HitTheMan/Breaking Into Nova Olesov/BreakingIntoNovaOlesov_Layers/OPFOR_Players/OPFOR_Players.layer',
          '9891383de16948b531ccdca1b638565541245653')),
        110, 80,
    ),
    'Missions/HitTheMan/Urdarnik Assault/UdarnikAssault.conf': (
        'c52cd1302f8c06030fe43dae2794d8d3c7fcc580',
        (('worlds/HitTheMan/Urdarnik Assault/UrdarnikAssault_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          'a9354dbedceae3f73dffac5887121475d7bbc5d4'),),
        110, 73,
    ),
    'Missions/Blue/COTVT80_TheBearWentOvertheMountainCH1S6.conf': (
        '177b4cd767a9fb3293ac23be596d2562be42180f',
        (('worlds/Blue/TheBearWentOvertheMountain2/TheBearWentOvertheMountain2_Layers/BLUFOR.layer',
          '13e9265d879ceae9a2b8a09ab00d804c0cdc04ce'),
         ('worlds/Blue/TheBearWentOvertheMountain2/TheBearWentOvertheMountain2_Layers/OPFOR.layer',
          '89c00432d3a54f3d9695d8394699d2f965989043')),
        80, 78,
    ),
    'Missions/Blue/TVT69_RUCKUSBerg.conf': (
        'a87dcad8fed68685ca5f79aec8f6ceeb00140fc1',
        (('worlds/Blue/Ruckusberg/RuckusBerg_Layers/UK/UKPlayers.layer', '4d974d42c141ed842ae49e6e3506a45775900f19'),
         ('worlds/Blue/Ruckusberg/RuckusBerg_Layers/USSR/USSRPlayer.layer', '73b066ac63781cd5c6cd6ab25acd297e5a645989')),
        73, 74,
    ),
    'Missions/HitTheMan/VDV Blockade/VDVBlockadeEvent.conf': (
        'f5c78882ccc913c7ddc1579744ebb99da98d1fb7',
        (('worlds/HitTheMan/VDV Blockade/VDVBlockadeEvent_Layers/Players_Soviets/OPFOR_Players.layer',
          '73d37e3d9a18d950124edbc54bfec66c6d83241c'),
         ('worlds/HitTheMan/VDV Blockade/VDVBlockadeEvent_Layers/Players_UK/UK_Players.layer',
          '1a268e9159d50cdd4c20d950767c807503336ebb')),
        94, 128,
    ),
    'Missions/Delfer/Topdown/Topdown.conf': (
        '8e0fde58ddfacdeba1baaca5f7e71af31efb62aa',
        (('worlds/Delfer/Topdown/Topdown_Layers/blufor_players.layer',
          'c5c17c96318ffd088b48c46907976ded58516f35'),
         ('worlds/Delfer/Topdown/Topdown_Layers/redfor_players.layer',
          'aa0a43c45e58ecee9cf22d43c13220132e1c9aa9')),
        68, 69,
    ),
    'Missions/James/OrbSturm.conf': (
        '0fdc64807190cbe82f0b3f53a9fea06efde34c2d',
        (('worlds/James/OrbSturm/OrbSturm_Layers/nva_players.layer',
          'a31eb2a2de4cb35c2ea41998494dace2bc4ed84e'),),
        79, 76,
    ),
    'Missions/Blue/SimonsAbatis.conf': (
        'fb882b62a84b29bb8933e27e00761cd8936bceb0',
        (('worlds/Blue/SimonsAbatis/SimonsAbatis_Layers/BLUFOR/BLUFORPlayer.layer',
          '61142fcae0f9291cce53a0c846e51e60e1cf0c19'),
         ('worlds/Blue/SimonsAbatis/SimonsAbatis_Layers/OPFOR/OPFORPlayer.layer',
          'dc4e0bb8e2326faba6319e7419f0fb8a27ba7e0b')),
        69, 68,
    ),
    'Missions/HitTheMan/Ruinous Defence/RuinousDefence.conf': (
        '12c18581f4294fceffa122fb22f2706091352f33',
        (('worlds/HitTheMan/Ruinous Defence/RuinousDefence_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '2fe91c9b992fd00f479253a7ddbb9aed4ec1d18d'),
         ('worlds/HitTheMan/Ruinous Defence/RuinousDefence_Layers/OPFOR_Players/OPFOR_Players.layer',
          '5e4b74a93c6dcfef1c90e4b63c46b30f8dd22598')),
        70, 72,
    ),
    'Missions/HitTheMan/Nadezhda Mine Clearence/NadezhdaMineClearence.conf': (
        '1ff8bffb54d8c0c63ecaacc7bc7e54230b289737',
        (('worlds/HitTheMan/Nadezhda Mine Clearence/NadezhdaMineClearence_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          'ac8fa5ccd6149d33560b905093b2b77d3d82a3ee'),
         ('worlds/HitTheMan/Nadezhda Mine Clearence/NadezhdaMineClearence_Layers/OPFOR_Players/OPFOR_Players.layer',
          '6714175b5ca462b777c47bd709b613bddfcf87a4')),
        75, 66,
    ),
    'Missions/HitTheMan/Pushing Through Hnevice/PushingThroughHnevice.conf': (
        'cf4aa8e5055c9760d5df4173a8a9f6335599d29a',
        (('worlds/HitTheMan/Pushing Through Hnevice/PushingThroughHnevice_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '3d9a49b52a9b82e0075acd847d089220cd4ca1eb'),
         ('worlds/HitTheMan/Pushing Through Hnevice/PushingThroughHnevice_Layers/OPFOR_Players/OPFOR_Players.layer',
          'a55a4c44b1a3b82c2f616bea7157063e9f56c61a')),
        110, 81,
    ),
    'Missions/HitTheMan/Devils Crossing/DevilsCrossing.conf': (
        '4567fcab9ab11d0ad160f4c47933ea073cd2bb67',
        (('worlds/HitTheMan/Devils Crossing/DevilsCrossing_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          'da0328ab2dd786247848531d951f38357fb19533'),
         ('worlds/HitTheMan/Devils Crossing/DevilsCrossing_Layers/OPFOR_Players/OPFOR_Players.layer',
          'c176119fe554d308886f33094b3247046c663553')),
        110, 65,
    ),
    'Missions/Huska/MortonBay.conf': (
        '349110f369ae52809278847d26ee41539ca02d83',
        (('worlds/Huska/MortonBay/MortonBay_Layers/Players.layer',
          'a9176e60348f369402dd3e85ea4377946024830c'),),
        70, 69,
    ),
    'Missions/anteante101/Its all uphill from here.conf': (
        '2ba58c7179dcd5863b8978f31413c4b08c1c4a3d',
        (('worlds/anteante101/Its all uphill from here/Its all uphill from here_Layers/ION.layer',
          '4ddb6f0af7125f2a71d30835bc8cde481e437619'),
         ('worlds/anteante101/Its all uphill from here/Its all uphill from here_Layers/opfor.layer',
          'bbe5dfa642cb3e770d9a0455068b699c461bec4e')),
        19, 18,
    ),
    'Missions/RoseOutTheAshes/FIALeMouleRaid.conf': (
        '55381d7ee37738834726dcde83d4e0256269ccf6',
        (('worlds/RoseOutTheAshes/FIALeMouleRaid/LeMouleRaid_layers/players.layer',
          '3a8fe16cfe672cfa4cc1ef3f545f2d6ba9132bdb'),),
        16, 17,
    ),
    'Missions/Woody/COTVTXX_Kassel_Crashers.conf': (
        '4941956ada2db5d66db4dacb029d9037e252e032',
        (('worlds/Woody/COTVTXX_Kassel_Crashers/COTVTXX_Kassel_Crashers_Layers/playergroups.layer',
          'ae5cdb834e5ae627e051767e3de9b813607b350f'),
         ('worlds/Woody/COTVTXX_Kassel_Crashers/COTVTXX_Kassel_Crashers_Layers/aigroups.layer',
          '698ea5a6253ae5282ea9967ce112d4ce7869b75f')),
        69, 67,
    ),
    'Missions/Woody/COTVTXX_Kassel_Crashers_MODERN_REDUX2.conf': (
        '7bb04df27323e14c9da426b0d291bd3a8da79515',
        (('worlds/Woody/COTVTXX_Kassel_Crashers_MODERN_REDUX/'
          'COTVTXX_Kassel_Crashers_MODERN_REDUX_Layers/playergroups.layer',
          'e49a67187d64d0aeb162a90be8f503efcdc5d2ef'),),
        69, 72,
    ),
    'Missions/HitTheMan/VDV Hello From The Sky/VDVHelloFromTheSky.conf': (
        '68347cf26401cd0d1ebd780e568a9b4cc163363d',
        (('worlds/HitTheMan/VDV Hello From The Sky/VDVHelloFromTheSky_Layers/OPFOR_Players/OPFOR_Players.layer',
          '7183aac2e553552ca13179c56cf49e4a1eb65d3e'),),
        79, 64,
    ),
    'Missions/HitTheMan/VDV Leads The Way/VDVLeadsTheWay.conf': (
        '9e42d838f05f0498631cca91c04d2a0344014cc7',
        (('worlds/HitTheMan/VDV Leads The Way/VDVLLeadsTheWay_Layers/OPFOR_Players/OPFOR_Players.layer',
          'fd0e6c248aed7310caad26397b51677a15cffe98'),),
        79, 66,
    ),
    'Missions/Cunnah/Arty_Arty_Arty.conf': (
        'ba9e7fb39aebc6074a15750152a9ec9acefa98de',
        (('worlds/Cunnah/Arty_Arty_Arty/Arty_Arty_Arty_Layers/BLUFOR.layer',
          '8862e65edeb38d4fcf744e6e8c97a54229104026'),
         ('worlds/Cunnah/Arty_Arty_Arty/Arty_Arty_Arty_Layers/OPFOR.layer',
          '1becf9d159310dd07d958b1bd9081801a6e35dda')),
        58, 57,
    ),
    'Missions/Corax/SanSerriffeBusters/Serriff_Busters.conf': (
        'dca7ef8249bd38cf60d1c1d5ce2b1b87ad6b9253',
        (('worlds/Corax/SerriffBusters/SerrifBusters_Layers/BLUFOR.layer',
          'b0aa003e3b00a2c6e52125024468f213ea549efc'),
         ('worlds/Corax/SerriffBusters/SerrifBusters_Layers/OPFOR.layer',
          '7ce290991afcf44ebd1b6c1f3baa7aa619be793a')),
        71, 72,
    ),
    'Missions/HitTheMan/Mountainous Mujahideen/MountainousMujahideen.conf': (
        'ad9bf1a4a0bdce3da88cc090b6ba369bb1f30ca7',
        (('worlds/HitTheMan/Mountainous Mujahideen/MountainousMujahideen_Layers/CDF_Players/CDF_Players.layer',
          'f55b2cf87565c259a8be08a09bacbb43c44d70cc'),
         ('worlds/HitTheMan/Mountainous Mujahideen/MountainousMujahideen_Layers/SEP_Players/SEP_Players.layer',
          '26894091c5f42cace73db42cbba370ed29656b10')),
        79, 67,
    ),
    'Missions/Jub/Another Hill/Another Hill.conf': (
        '75e4e21677f4e64358d3ffee3d523e567cf589d8',
        (('worlds/Jub/Another Hill/Another Hill_Layers/Argies.layer',
          'ba5ec00e2b2810c13de1394c0ae07f3565ae316e'),
         ('worlds/Jub/Another Hill/Another Hill_Layers/UK.layer',
          'f385fe7176b2f6e8e6ceca69d9c48f8c0387347a')),
        124, 125,
    ),
    'Missions/arc/BunkerBustersXL.conf': (
        '97e515f41ba225f60c1f7997fad5d782005e4090',
        (('worlds/arc/BunkerBustersXL/BunkerBustersXL_Layers/players.layer',
          '3d293bac14b7cf19e85f66f49bc00a9caeae22a8'),),
        64, 56,
    ),
    'Missions/HitTheMan/Frontline CDF Construction Site/FrontlineCDFConstructionSite.conf': (
        '22a47682027e05164c8343d14fb3d1be146d06aa',
        (('worlds/HitTheMan/Frontline CDF Construction Site/FrontlineCDFConstructionSite_Layers/CDF_Players/CDF_Players.layer',
          '343e5c23ac781fef81ea50712fcfc2569dffd31f'),
         ('worlds/HitTheMan/Frontline CDF Construction Site/FrontlineCDFConstructionSite_Layers/SEP_Players/SEP_Players.layer',
          '85d59bd8662dc90c8127759c8d5e04b82b3b5c83')),
        79, 76,
    ),
    'Missions/Cunnah/A_Hop_Skip_And_A_Jump.conf': (
        '727183f983e89f5ccbbdc75856d72c7c420fe90d',
        (('worlds/Cunnah/A_Hop_Skip_And_A_Jump/a_hop_skip_and_a_jump_Layers/BLUFOR.layer',
          'e344b83766a3aca6f6ff32292858c0791a772542'),
         ('worlds/Cunnah/A_Hop_Skip_And_A_Jump/a_hop_skip_and_a_jump_Layers/OPFOR.layer',
          '9a5b8ff212d1ba8c484e8e17dfed060b9baff796')),
        67, 66,
    ),
    'Missions/IronFunnelEvent/MSN1.conf': (
        'bc54df59d5f3682b81e89394990e787b2d0e4b53',
        (('worlds/IronFunnelEvent/MSN1/StPierre_Air_Assault_Layers/NATO_playergroups.layer',
          '7473e531083f7dbba88825cc424ecd5541da1aa6'),
         ('worlds/IronFunnelEvent/MSN1/StPierre_Air_Assault_Layers/PACT_playergroups.layer',
          '1bf14443a3d96d4b3a2395fd7a0e643edc308530')),
        113, 108,
    ),
    'Missions/IronFunnelEvent/MSN3.conf': (
        'b1a007aab61b09ae2be6d13a4498247c561a035c',
        (('worlds/IronFunnelEvent/MSN3/SovietLevies_Layers/players_us.layer',
          'b60a65383fd036ed2eefdfcb141bea23068665ee'),
         ('worlds/IronFunnelEvent/MSN3/SovietLevies_Layers/players_ussr.layer',
          '703f3974833ee6ad2dc17909339796467f8cf417')),
        108, 111,
    ),
    'Missions/Burger4606/Hush.conf': (
        'c36a235738f8d82c94a2ca091948be6b340718c8',
        (('worlds/Burger4606/Hush/Hush_Layers/players_UK.layer',
          '256de7aa2cf79a8b39bcd62c908cdb316ecf016f'),
         ('worlds/Burger4606/Hush/Hush_Layers/OPFOR.layer',
          '6518e72ec35ab6c08c0594aec99554815403036d')),
        20, 21,
    ),
    'Missions/HitTheMan/Battalion Breakthrough/BattalionBreakthrough.conf': (
        '45f4dd177e24450169695f8db49b9434a3b036e0',
        (('worlds/HitTheMan/Battalion Breakthrough/BattalionBreakthrough_Layers/Players_Soviets/OPFOR_Players.layer',
          '47bd6ed8057a86fadfb07fd77204a0b50d7569e5'),
         ('worlds/HitTheMan/Battalion Breakthrough/BattalionBreakthrough_Layers/Players_UK/UK_Players.layer',
          'a4b7f1367d499e41776cb1b30b6a4719122c2e51')),
        92, 96,
    ),
    'Missions/HitTheMan/Encircled NVA/EncircledNVA.conf': (
        '8b2852e0ebaf3cca6cf58a6559f7b888e3f203a7',
        (('worlds/HitTheMan/Encircled NVA/EncircledNVA_Layers/NVA_Players/NVA_Players.layer',
          '554325bf5e5aba9c953223e729f3560e84bd4bc7'),
         ('worlds/HitTheMan/Encircled NVA/EncircledNVA_Layers/US_Players/US_Players.layer',
          'd111df69e5a5afa0fa81d64de2d2cd6cda681283')),
        111, 50,
    ),
    'Missions/HitTheMan/Clearing Out Gorshkovo/ClearingOutGorshkovo.conf': (
        '2b7159d447acd626e736710f294eaeddc62f7047',
        (('worlds/HitTheMan/Clearing Out Gorshkovo/ClearingOutGorshkovo_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '84b6ca4a01e3188815f1fc0078582f610f0e7be3'),
         ('worlds/HitTheMan/Clearing Out Gorshkovo/ClearingOutGorshkovo_Layers/OPFOR_Players/OPFOR_Players.layer',
          '05a370a236bdb82e0b5ac867ac973704ac0b4d26')),
        110, 35,
    ),
    'Missions/Reapzr/Deadlock.conf': (
        'ceaac59d93946de86c2f59625d0adda978d92a98',
        (('worlds/Reapzr/Deadlock/Deadlock_Layers/Groups/Attackers.layer',
          '462687e5a7657b378d7d1a807c24a0726f836d73'),
         ('worlds/Reapzr/Deadlock/Deadlock_Layers/Groups/Defenders.layer',
          '45637a514cbd9e37136052c833b33832e9187fd5')),
        60, 66,
    ),
    'Missions/arc/PatrolTroubles.conf': (
        '3e3558ba1284691a71fd64781266612bc881b5df',
        (('worlds/arc/PatrolTroubles/PatrolTroubles_Layers/players.layer',
          '872d093e2b12108445da912dabc0df2df3d2e90d'),),
        40, 41,
    ),
    'Missions/Sasha20k_pro/Assault of thirty palms.conf': (
        '05531d2a0fc40bdc0cd36b570db1bff7ddf6cedc',
        (('worlds/Sasha20k_pro/Assault of Thirty Palms/Assault of Thirty Palms_Layers/Players_us.layer',
          'f4fdb7d55ee5eff19868fa908f0e31eca0660ecc'),
         ('worlds/Sasha20k_pro/Assault of Thirty Palms/Assault of Thirty Palms_Layers/AI_Units.layer',
          'e1ca2d7eb3835b417793ee7fda5bc32d76c45c22'),
         ('worlds/Sasha20k_pro/Assault of Thirty Palms/Assault of Thirty Palms_Layers/Insurgent_AI_Groups.layer',
          '4ca1d9c447c4c2b306176ec1331a89edb3af7460')),
        48, 46,
    ),
    'Missions/Til/FalconsFlight.conf': (
        'c7b8968d8bae440cc329ac49db91f89e6c64307b',
        (('worlds/Til/FalconsFlight/FalconsFlight_Layers/nva.layer',
          'a2b465b61d72f5b68dcc36038252a863e895c311'),
         ('worlds/Til/FalconsFlight/FalconsFlight_Layers/us.layer',
          '20ad0a8ba62948c28c93b0f9b7a89b2b1961ec00')),
        109, 110,
    ),
    'Missions/Til/AndresBeacon.conf': (
        'a9ef521d896898eabd7a513d7e8a6c02ca7150ef',
        (('worlds/Til/AndresBeacon/AndresBeacon_Layers/players.layer',
          'd4972c5249abef9e68891dfb3aac0f8549e7a725'),),
        68, 67,
    ),
    'Missions/Til/JunctionJabXL.conf': (
        'de161be13cf65db02c7a982c60fb4c335f996c5e',
        (('worlds/Til/JunctionJabXL/JunctionJabXL_Layers/players_caf.layer',
          'ee76d8c1c238c9d957f7b4f987361449a2db426f'),
         ('worlds/Til/JunctionJabXL/JunctionJabXL_Layers/players_nva.layer',
          '4b42ceae026d066bc782515fcaafc8530d048d16')),
        100, 99,
    ),
    'Missions/anteante101/Attack of the Howlers.conf': (
        'db07e88229b315c5af74b96a65d0663e84e07107',
        (('worlds/anteante101/Attack of the Howelrs/Attack of the Howlers_Layers/B4_heart_OP4.layer',
          '8717b2304cf8fb8fa8f9e4887ce009f7fc46753e'),
         ('worlds/anteante101/Attack of the Howelrs/Attack of the Howlers_Layers/Howlers.layer',
          'a65027f6ae9a003d3e79934b068a2cdf87d4e836')),
        50, 55,
    ),
    'Missions/HitTheMan/Frontline CDF Assault/FrontlineCDFAssault.conf': (
        '81326ea91d4371e361190f44e19758ad03b59072',
        (('worlds/HitTheMan/Frontline CDF Assault/FrontlineCDFAssault_Layers/NVA_Players/NVA_Players.layer',
          '7b1d1cc3fd884ea62fc95ef25782b8471ba7d751'),
         ('worlds/HitTheMan/Frontline CDF Assault/FrontlineCDFAssault_Layers/US_Players/US_Players.layer',
          '786f9c2fc60bf64dcea4a035d66f9c1846622965')),
        79, 59,
    ),
    'Missions/arc/StationSmashers.conf': (
        '7bcb2beb501cba2cb14a896d7fe8fad1f267e705',
        (('worlds/arc/StationSmashers/StationSmashers_Layers/players_ins.layer',
          'd675a0072d39d427245c28258f9add77831c2c1f'),
         ('worlds/arc/StationSmashers/StationSmashers_Layers/players_us.layer',
          'b7ab827c80f7034784f4f554ff03a44330fca9b0')),
        48, 52,
    ),
    'Missions/IronFunnelEvent/MSN2.conf': (
        '0bd1338b3736f1caa0e5418a9e996696bb14b41e',
        (('worlds/IronFunnelEvent/MSN2/ScoutsOut_Layers/BLUFOR/Players.layer',
          '7dd2280de0c2199652506029973cf7428ff0c085'),
         ('worlds/IronFunnelEvent/MSN2/ScoutsOut_Layers/OPFOR/PlayerOPFOR.layer',
          '33881ef2ecfaa07dcd497d15716e0e99517202a9')),
        108, 107,
    ),
    'Missions/Jub/MissionWithAssets.conf': (
        '00bd356069766c8d7d0c7235edf17f9992941a32',
        (('worlds/Jub/MissionWithAssets/MissionWithAssets_Layers/players_RU.layer',
          'dec5abeb11a238728fbe3d180ea3626ae980e070'),
         ('worlds/Jub/MissionWithAssets/MissionWithAssets_Layers/players_usmc.layer',
          'd8e3e76dc13d012582dc32edb30b974c4e2395a9')),
        66, 78,
    ),
    'Missions/HitTheMan/Green Hill Military Assault/GreenHillAssault.conf': (
        'a24e2eafb399d77eccd92a73a8e9e71c5ec6821e',
        (('worlds/HitTheMan/Green Hill Assault/GreenHillAssault_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '77d19e368560b03bd49a37add2f84ed06cb8d9d7'),
         ('worlds/HitTheMan/Green Hill Assault/GreenHillAssault_Layers/OPFOR_Players/OPFOR_Players.layer',
          '5bbd674fd88eb274ae97901b47a798f320b9571d')),
        75, 63,
    ),
    'Missions/Woody/COXX_Landay_Haul.conf': (
        '90d1f355e72fc950632ced756ec41f62c746bc66',
        (('worlds/Woody/COXX_Landay_Haul/COXX_Landay_Haul_Layers/playergroups.layer',
          'b17ee6b7d81dce91a0065354c2dfeba53d619a08'),),
        60, 62,
    ),
    'Missions/HitTheMan/Hall Boss/HallBoss.conf': (
        '36dfbc13413a59bf068ae40807395fe62314e6f6',
        (('worlds/HitTheMan/Hall Boss/HallBoss_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '8089e9efa4f45f36e03a9ace9149ace17aef4299'),
         ('worlds/HitTheMan/Hall Boss/HallBoss_Layers/OPFOR_Players/OPFOR_Players.layer',
          '9ad00b7c276ce6c9464afc73ab1a8723cea1ccad')),
        75, 47,
    ),
    'Missions/HitTheMan/Hall Boss Mines/HallBossMines.conf': (
        'c6b5cc2df728c4553588b1c40b7a114f607814e8',
        (('worlds/HitTheMan/Hall Boss Mines/HallBossMines_Layers/BLUFOR_Players/BLUFOR_Players.layer',
          '2b7329b7c7b85db3cb3cd407b3d735ba7cd1f745'),
         ('worlds/HitTheMan/Hall Boss Mines/HallBossMines_Layers/OPFOR_Players/OPFOR_Players.layer',
          '9ad00b7c276ce6c9464afc73ab1a8723cea1ccad')),
        75, 47,
    ),
}
REVIEWED_MISSING_SPAWN_ANCHORS = {
    'worlds/Til/TyroneThrust/TyroneThrust.ent': (
        'worlds/Til/TyroneThrust/TyroneThrust_Layers/default.layer',
        'ecde2a6f7b09a65b77a6bbf372c3ed282762daed', {('6219244BFF9757BC', 'QRFSpawn')},
    ),
    'worlds/Cunnah/Arty_Arty_Arty/Arty_Arty_Arty.ent': (
        'worlds/Cunnah/Arty_Arty_Arty/Arty_Arty_Arty_Layers/default.layer',
        '475f775d2eac67dd94f1161e277b32a2a8094a55', {('650557F5B24BB14C', 'motar_target_8')},
    ),
    'worlds/HotMic/DelayingActionRuhaPlatoon/DelayingActionRuhaPlatoon.ent': (
        RUHA_PHASE_LAYER, RUHA_PHASE_BLOB,
        {('660D35BA2C88B6D2', 'Phase2_Smoke1'), ('660D35BBEF2F1785', 'Phase3_Smoke1')},
    ),
    'worlds/Jub/LightArmoredRecon/LightArmoredRecon.ent': (
        'worlds/Jub/LightArmoredRecon/LightArmoredRecon_Layers/default.layer',
        '6c5a7598c2308f558fa7cc47e04fcf3e696bfb3c',
        {('6A3B7A7E7370D2A7', 'SpawnPos1'), ('6A3B7A7D8C6566BB', 'SpawnPos2'),
         ('6A3B7A7D83B720FA', 'SpawnPos3'), ('6A3B7A7CCB7AEA59', 'SpawnPos4')},
    ),
}
REVIEWED_MISSING_ORDER_GROUPS = {
    'worlds/HitTheMan/Bradley Leads The Way/BradleyLeadsTheWay.ent': (
        'worlds/HitTheMan/Bradley Leads The Way/BradleyLeadsTheWay_Layers/default.layer',
        '6794efd40331f9e3f20c6c14b3955b4468279d08', {'NVA_AI_7', 'NVA_AI_10', 'T72_NVA2'},
    ),
}
BREAK_THE_LINE_MISSING_WAYPOINTS = {f'def_wp_Mid_CAF_{i}' for i in (*range(8, 25), 28)}
SKALISTY_NORTH_LAYER = ('worlds/HitTheMan/Air Assault Onto Skalisty/'
                        'AirAssaultOnSkalisty_Layers/AI_Defenders/AI_Defenders_North.layer')
SKALISTY_NORTH_BLOB = '3f995e58fda7c5b32500dad62ff5b7b6c599eacd'
REVIEWED_MISSING_WAYPOINTS = {
    ASSAD_LAYERS + 'AiA.layer': ('5fcab9e05f41d5aaa9dcdc3516d462b57fd1686f', {'AIWP_Defend_12'}),
    SKALISTY_NORTH_LAYER: (SKALISTY_NORTH_BLOB, {'DefInside_WPNorth_US_10'}),
    'worlds/HitTheMan/2nd Break The Line/2ndBreakTheLine_Layers/BLUFOR/BLU_AI_Town_Defene_60xAI.layer':
        ('1eb75b271a2d5a51e0e2982674a6a5f0bfdb4f32', BREAK_THE_LINE_MISSING_WAYPOINTS),
    'worlds/HitTheMan/3rd Break The Line/3rdBreakTheLine_Layers/BLUFOR/BLU_AI_Town_Defene_60xAI.layer':
        ('8573eeee4429b7f50d01367cf51c7faba24b49c3', BREAK_THE_LINE_MISSING_WAYPOINTS),
    'worlds/HitTheMan/Screening The Orb/ScreeningTheOrb_Layers/BLUFOR/BLU_AI_STINGERS_7AI.layer':
        ('59c8e46c27403dddb8a7557eb800c04408f6f513', {f'def_wp_CAF_{i}' for i in range(50, 55)}),
    'worlds/HitTheMan/Screening The Orb/ScreeningTheOrb_Layers/BLUFOR/BLU_AI_TOWN_86AI.layer':
        ('adc697a4bd5df559b857519ee476ca8cc519bb70', {'def_wp_CAF_1'}),
    'worlds/TheLocalPub/LamentinLosers/LamentinLosers_Layers/BLUFOR_AI.layer':
        ('a8a7e4e23395b5de1f902bbb64b65148300a933f', {'BLUWATK10_1'}),
    'worlds/Barwick/The Blood and Smoke/The Blood and Smoke_Layers/Insurgents/Insurgent_AI.layer':
        ('899263002cdc221867c535cfca930b39e55867d9', {'Defend'}),
    'worlds/HitTheMan/VDV Leads The Way/VDVLLeadsTheWay_Layers/AI_Defenders/AI_Encounters_1.layer':
        ('9b284785753b8cac64f6c6a971f19b198f0120f9', {'DefInside_WPWest_US_1'}),
    'worlds/HitTheMan/VDV Hello From The Sky/VDVHelloFromTheSky_Layers/AI_Defenders/AI_Defenders_South.layer':
        ('93dda27768ebdc5519b0ee2ef9ef57304cb44b1e', {f'DefInside_WPWest_US_{i}' for i in range(1, 15)}),
    'worlds/HitTheMan/VDV Hello From The Sky/VDVHelloFromTheSky_Layers/AI_Defenders/AI_Defenders_Spawned.layer':
        ('28aafa42d617068b2b6a530ac89bff70e62b1de7', {'DefInside_WPWest_US_8', 'Spawned_Def_WPMain_'}),
    'worlds/Reapzr/Deadlock/Deadlock_Layers/Groups/AI.layer':
        ('f539728c37d0fa180a89e171dde57baa0dd4c147', {
            'AIWaypoint_Defend_1', 'AIWaypoint_Patrol_1', 'AIWaypoint_Patrol_2',
            'AIWaypoint_Scout_1', 'AIWaypoint_Scout_2',
            'AIWaypoint_SearchandDestroy_1', 'AIWaypoint_SearchandDestroy_2',
        }),
}
KHANDAR_AI_LAYER = 'worlds/James/KhandarClearance/KhandarClearance_Layers/ai.layer'
KHANDAR_AI_BLOB = 'dc18ed6e0e49d9af717bd183b8781dab0bc3af89'
REVIEWED_MISSING_SPAWN_WAYPOINTS = {
    SKALISTY_NORTH_LAYER: (SKALISTY_NORTH_BLOB, {'RecaptureBadOrb': 1}),
    'worlds/HitTheMan/VDV Hello From The Sky/VDVHelloFromTheSky_Layers/AI_Defenders/AI_Defenders_South.layer':
        ('93dda27768ebdc5519b0ee2ef9ef57304cb44b1e', {'Def_WPMain_US_10': 1}),
    KHANDAR_AI_LAYER: (KHANDAR_AI_BLOB, {'defnorth': 1, 'top': 4}),
}
REVIEWED_MISSING_STATIC_VEHICLES = {
    'worlds/chllboy/Hephaestus Forge/Hephaestus Forge_Layers/Blufor.layer': (
        '5f48590ff9f7209224c3ce7e22311c72112cb2c6', (
            '#AH6M-Vehicle_AH6M_Name - #WCS-Weapon_M134_Name/#WCS-Weapon_M260_Name',
            '#AR-Vehicle_UH60_Transport_Name1', '#AR-Vehicle_UH60_Transport_Name',
        ),
    ),
    'worlds/Cunnah/A_Hop_Skip_And_A_Jump/a_hop_skip_and_a_jump_Layers/OPFOR.layer':
        ('9a5b8ff212d1ba8c484e8e17dfed060b9baff796', {'T72A_1'}),
    'worlds/TheLocalPub/SD01_ConvoyChaos/SD01_ConvoyChaos_Layers/Opfor/Players.layer':
        ('9f0599d33d2c7b9964f1e5bd6c7fa526151c29e9', {
            'Ural4320_ammo1', 'Ural4320_ammo2', 'Ural4320_command1',
            'Ural4320_repair1', 'Ural4320_repair2', 'Ural4320_tanker1',
        }),
    'worlds/Perfk/NagalamTrafficJam/NagalamTrafficJam_Layers/Blufor_Convoy/Blufor_Convoy_Vehicles.layer':
        ('a1ea4653b6b39ea66debf7e91d35f5a7fd714314', {'LAV25_GulfWar1'}),
}
MATERIAL_OVERRIDE = re.compile(
    rf'SCR_DestructibleBuildingEntity : "\{{[\da-fA-F]{{16}}\}}Prefabs/Structures/[^"\n]+\.et" \{{\n'
    rf' ID "[\da-fA-F]{{16}}"\n components \{{\n  MeshObject {SERIALIZED_ID} \{{\n   Materials \{{\n'
    rf'(?:    MaterialAssignClass {SERIALIZED_ID} \{{\n     SourceMaterial "[^"\n]+"\n'
    r'     AssignedMaterial "\{[\da-fA-F]{16}\}[^"\n]+\.emat"\n    \}\n)+'
    r'   \}\n  \}\n \}\n(?P<transform>(?: [^\n]+\n)*)\}'
)
STORAGE_BOX = re.compile(
    r'GenericEntity : "\{A0E432F0BBED71B4\}PrefabsEditable/Auto/Props/Military/AmmoBoxes/US/'
    r'E_EquipmentBoxWooden_Equipment_01_US\.et" \{\n'
    r' ID "[\da-fA-F]{16}"\n components \{\n'
    r'  SCR_UniversalInventoryStorageComponent "\{5476A2F100DF4EFF\}" \{\n'
    r'   Enabled 1\n   MultiSlots \{\n'
    rf'(?:    MultiSlotConfiguration {SERIALIZED_ID} \{{\n'
    rf'     SlotTemplate (?:InventoryStorageSlot|EquipmentStorageSlot) [\w.-]+ \{{\n'
    r'      Prefab "\{[\da-fA-F]{16}\}[^"\n]+\.et"\n'
    r'     \}\n     NumSlots [1-9]\d*\n    \}\n)+'
    r'   \}\n  \}\n  ActionsManagerComponent "\{5476B36C403D4CC0\}" \{\n'
    r'   Enabled 1\n  \}\n \}\n\}'
)
CALLSIGN_TRIPLET = (r'm_iGC_CallsignCompany "\d+"\n(?P=indent)m_iGC_CallsignPlatoon "\d+"\n'
                    r'(?P=indent)m_iGC_CallsignSquad "\d+"\n')
DUPLICATE_CALLSIGNS = re.compile(
    rf'^(?P<first>(?P<indent> +){CALLSIGN_TRIPLET})(?P<second>(?P=indent){CALLSIGN_TRIPLET})', re.M)
AI_WORLD_ALIASES = {
    '33A5B976E41B0A4C': ('Prefabs/AI/SCR_AIWorld_BadOrb.et', 'Prefabs/AI/SCR_AIWorld_BadOrb_GC.et'),
    '70CCCF16487C927F': ('GitHub/gc-reforger-core/Prefabs/AI/SCR_AIWorld_Eden.et',
                         'Prefabs/AI/SCR_AIWorld_Eden.et'),
}
# Workbench resolves these stale GUID-qualified references to the current tree
# prefab at the same authored path (logs_2026-11-21_15-24-06).
STOCK_RESOURCE_REKEYS = {
    ('585B412BC51E59F3', 'Prefabs/Vegetation/Bush/b_juniperus_communis_1s.et'): '6C454FA2B0FAFAEE',
    ('6230E64FC38CA6B1', 'Prefabs/Vegetation/Bush/b_juniperus_communis_0.et'): '81EE8FAE7539DAA1',
    ('9025E96A11516E7A', 'Prefabs/Vegetation/Bush/b_juniperus_communis_2w.et'): 'A43BE7E364B5CD67',
}

STOCK_RESOURCE_ALIASES = {
    ('66B93BC296E2F977', 'Prefabs/Radio/RadioBroadcastManager.et'):
        'Prefabs/Systems/Radio/RadioBroadcastManager_Everon.et',
    ('D0BD7A55306FC46B', 'Prefabs/Props/Furniture/Faucet_01.et'):
        'Prefabs/Props/Furniture/Faucet_01/Faucet_01.et',
    ('407A7B6D0444D05B', 'PrefabLibrary/Vegetation/Plants/b_urtica_dioica_0s.et'):
        'PrefabLibrary/DefaultLibrary/Vegetation/Plants/b_urtica_dioica_0s.et',
    ('531BB93086610CA1', 'PrefabLibrary/Vegetation/Trees/t_betula_pendula_0.et'):
        'PrefabLibrary/DefaultLibrary/Vegetation/Trees/t_betula_pendula_0.et',
    ('670F3221F5114B42', 'PrefabLibrary/Vegetation/Debris/BranchTrash_dec_01.et'):
        'PrefabLibrary/DefaultLibrary/Vegetation/Debris/BranchTrash_dec_01.et',
    ('7BEC4D46FABA042E', 'PrefabLibrary/Vegetation/Trees/t_carpinus_betulus_branch_01.et'):
        'PrefabLibrary/DefaultLibrary/Vegetation/Trees/t_carpinus_betulus_branch_01.et',
    ('A35E15EBC6033C07', 'PrefabLibrary/Vegetation/Bushes/b_corylus_avellana_1.et'):
        'PrefabLibrary/DefaultLibrary/Vegetation/Bushes/b_corylus_avellana_1.et',
    ('F62AA8E7B8EA9D26', 'configs/ResourceTypes/PS5/MeshObjectCommon.conf'):
        'Configs/System/ResourceTypes/PS5/MeshObjectCommon.conf',
    ('0B1562B127AF6116', 'Configs/Weapons/AIBallisiticTables/AIBT_545x39_Tracer_7T3.conf'):
        'Configs/Weapons/AIBallisticTables/AIBT_545x39_Tracer_7T3.conf',
    ('18A55B529C1F6655', 'Configs/Weapons/AIBallisiticTables/AIBT_762x54r_Tracer_7T2.conf'):
        'Configs/Weapons/AIBallisticTables/AIBT_762x54r_Tracer_7T2.conf',
    ('71F92160489B0A8E', 'Assets/Weapons/Ammo/Bullets/762x54/Cartridge_762x54_T-46.xob'):
        'Assets/Weapons/Ammo/Bullets/762x54/Cartridge_762x54_T46.xob',
    ('AED5759B653DF6D0', 'Prefabs/Vegetation/Bush/b_rosa_canina_1s_collision.et'):
        'Prefabs/Vegetation/Bush/b_rosa_canina_1s.et',
    ('D6EC31A48403A52A', 'Prefabs/Vegetation/Bush/b_sambucus_nigra_1_collision.et'):
        'Prefabs/Vegetation/Bush/b_sambucus_nigra_1.et',
    ('2D556E41F306336C', 'Configs/Destruction/MultiphaseDestruction/FX_Debris/FX_Debris_Snadbags_Plastic.conf'):
        'Configs/Destruction/MultiphaseDestruction/FX_Debris/FX_Debris_Sandbags_Plastic.conf',
    ('2B24356C351A2538', 'Prefabs/Props/Wrecks/M113_wreck.et'):
        'Prefabs/Props/Wrecks/M113_wreck/M113_wreck.et',
    ('6BDDFE9FA1D4B2F0', 'Prefabs/Props/Wrecks/UAZ469_wreck.et'):
        'Prefabs/Props/Wrecks/UAZ469_wreck/UAZ469_wreck.et',
    ('6813E89F9EF2BDAC', 'PrefabLibrary/Vegetation/Bushes/b_rosa_canina_1s.et'):
        'PrefabLibrary/DefaultLibrary/Vegetation/Bushes/b_rosa_canina_1s.et',
    ('2D556E41F306336C', 'Configs/Destruction/MultiphaseDestruction/FX_Debris/FX_Debris_Snadbags_Plastic.conf'):
        'Configs/Destruction/MultiphaseDestruction/FX_Debris/FX_Debris_Sandbags_Plastic.conf',
    ('0CA0547EB03759E1', 'Prefabs/Vegetation/Bush/b_rubus_idaeus_1s_collision.et'):
        'Prefabs/Vegetation/Bush/b_rubus_idaeus_1s.et',
    ('124518D13290A687', 'Prefabs/Vegetation/Bush/b_salix_cinerea_0_collision.et'):
        'Prefabs/Vegetation/Bush/b_salix_cinerea_0.et',
    ('6113990D163E5249', 'Prefabs/Items/Equipment/BalisticTable/BalisticTable_US.et'):
        'Prefabs/Items/Equipment/BallisticTable/BallisticTable_US.et',
    ('3386EAA823BE462E', 'Prefabs/Props/Wrecks/UAZ469_wreck_static.et'):
        'Prefabs/Props/Wrecks/UAZ469_wreck/UAZ469_wreck_static.et',
    ('B90E5414AB7C325B', 'Prefabs/Props/Wrecks/BTR70_wreck_static.et'):
        'Prefabs/Props/Wrecks/BTR70_Wreck/BTR70_wreck_static.et',
    ('388B774519BD6294', 'Prefabs/Props/Wrecks/T62_wreck.et'):
        'Prefabs/Props/Wrecks/T62_wreck/T62_wreck.et',
    ('922F95F91943F69A', 'Prefabs/Items/Equipment/Maps/PaperMap_01_folded_US.et'):
        'Prefabs/Items/Equipment/Maps/Map_Paper_01/PaperMap_01_folded_US.et',
    ('F849217AB3BA88BE', 'Prefabs/Items/Equipment/Maps/PaperMap_01_folded_USSR.et'):
        'Prefabs/Items/Equipment/Maps/Map_Paper_01/PaperMap_01_folded_USSR.et',
    ('865B9E01D2C0B2D7', 'Prefabs/Items/Equipment/Maps/PaperMap_01_full.et'):
        'Prefabs/Items/Equipment/Maps/Map_Paper_01/PaperMap_01_full.et',
    ('35D7F1189BE00E94', 'Prefabs/Items/Equipment/BalisticTable/BalisticTable.et'):
        'Prefabs/Items/Equipment/BallisticTable/BallisticTable_base.et',
    ('9668B6C528DCECBD', 'Prefabs/Props/Wrecks/Mi8MT_wreck_static.et'):
        'Prefabs/Props/Wrecks/Mi8MT_wreck/Mi8MT_wreck_static.et',
    ('BA785041AC632123', 'Prefabs/Vegetation/Bush/b_salix_cinerea_1_collision.et'):
        'Prefabs/Vegetation/Bush/b_salix_cinerea_1.et',
    ('B3E8911E396DE447', 'Prefabs/Props/Services/FireExtinguisherBox_01_Shovel.et'):
        'Prefabs/Props/Services/Fire/FireExtinguisherBox_01_Shovel.et',
    ('4CC2C69DE1AEF103', 'Prefabs/Props/Core/Destructible_Props_Base.et'):
        'Prefabs/Props/Core/DestructibleMultiPhase_Props_Base.et',
    ('4F8AAD383330D2B9', 'Configs/Weapons/AIBallisiticTables/AIBT_Smoke_M18_Violet.conf'):
        'Configs/Weapons/AIBallisticTables/AIBT_Smoke_M18_Violet.conf',
    ('900E362C5B4A1B69', 'Configs/Weapons/AIBallisiticTables/AIBT_Smoke_RDG2.conf'):
        'Configs/Weapons/AIBallisticTables/AIBT_Smoke_RDG2.conf',
    ('E26B2BD5B751713D', 'Configs/Weapons/AIBallisiticTables/AIBT_Smoke_ANM8HC.conf'):
        'Configs/Weapons/AIBallisticTables/AIBT_Smoke_ANM8HC.conf',
    ('1D0B32AA80873228', 'Prefabs/Props/Wrecks/LAV25_wreck.et'):
        'Prefabs/Props/Wrecks/LAV25_wreck/LAV25_wreck.et',
    ('1F3291C2A7E2BA00', 'Prefabs/Props/Wrecks/BRDM2_wreck_static.et'):
        'Prefabs/Props/Wrecks/BRDM2_wreck/BRDM2_wreck_static.et',
    ('23C0BE9D80929DBD', 'Prefabs/Vegetation/Bush/b_salix_cinerea_1w_collision.et'):
        'Prefabs/Vegetation/Bush/b_salix_cinerea_1w.et',
    ('2591F1D52FD5DC65', 'PrefabLibrary/Walls/Net/NetFence_04_8m.et'):
        'PrefabLibrary/DefaultLibrary/Walls/Net/NetFence_04_8m.et',
    ('25B265F3E7CBDFC8', 'Prefabs/Props/Wrecks/BMP1_wreck.et'):
        'Prefabs/Props/Wrecks/BMP1_wreck/BMP1_wreck.et',
    ('626C85F8D51AC829', 'PrefabLibrary/Props/Military/ObstacleBridge_01_platform.et'):
        'PrefabLibrary/DefaultLibrary/Props/Military/ObstacleBridge_01_platform.et',
    ('75FEF1CA582431F3', 'Prefabs/Props/Wrecks/BTR70_wreck.et'):
        'Prefabs/Props/Wrecks/BTR70_Wreck/BTR70_wreck.et',
    ('AE300E34EA8FF40C', 'Prefabs/Props/Wrecks/Ural4320_wreck_static.et'):
        'Prefabs/Props/Wrecks/Ural4320_wreck/Ural4320_wreck_static.et',
    ('AF4BFBE29028A9FD', 'Prefabs/Vegetation/Bush/b_corylus_avellana_1l_collision.et'):
        'Prefabs/Vegetation/Bush/b_corylus_avellana_1l.et',
    ('C1E318C4198DFA2A', 'PrefabLibrary/Props/Military/Duckboard_01.et'):
        'PrefabLibrary/DefaultLibrary/Props/Military/Duckboard_01.et',
    ('C433B56E102780AA', 'PrefabLibrary/Props/Military/ObstacleRamp_01_start.et'):
        'PrefabLibrary/DefaultLibrary/Props/Military/ObstacleRamp_01_start.et',
    ('CAFF4B51059A0F81', 'PrefabLibrary/Walls/Concrete/ConcreteWall_01_3m.et'):
        'PrefabLibrary/DefaultLibrary/Walls/Concrete/ConcreteWall_01_3m.et',
    ('BFDAD14EBBE6D7FF', 'PrefabLibrary/Walls/Concrete/ConcreteRetainingWall_01_8m.et'):
        'PrefabLibrary/DefaultLibrary/Walls/Concrete/ConcreteRetainingWall_01_8m.et',
    ('C98A6C1C2372354E', 'Prefabs/Vegetation/Bush/b_corylus_avellana_1_collision.et'):
        'Prefabs/Vegetation/Bush/b_corylus_avellana_1.et',
    ('A5DE58FC9FB6BA60', 'Prefabs/Vegetation/Bush/b_corylus_avellana_2_collision.et'):
        'Prefabs/Vegetation/Bush/b_corylus_avellana_2.et',
    ('1A1CE09F7BCA6650', 'Prefabs/Props/Wrecks/S105_wreck_static.et'):
        'Prefabs/Props/Wrecks/S105_wreck/S105_wreck_static.et',
    ('137DCF41459A89BE', 'Prefabs/Vehicles/Helicopters/UH1H/UH1H_gunship.et'):
        'Prefabs/Vehicles/Helicopters/UH1H/UH1H_armed_gunship_empty.et',
    ('DCAF0585D17B917A', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_tanker_CIV_ranomized.et'):
        'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_tanker_CIV_randomized.et',
    ('0C55DFF2B0385FFE', 'Prefabs/Structures/Agriculture/Hayloft/Hayloft_E_01_wood.et'):
        'Prefabs/Structures/Agriculture/Hayloft/Hayloft_E_01/Hayloft_E_01_wood.et',
    ('2D10FC11D64DCE60', 'Prefabs/Structures/Agriculture/Manure/SilageStorage_E_01.et'):
        'Prefabs/Structures/Agriculture/Manure/SilageStorage_E_01/SilageStorage_E_01.et',
    ('4B176F8E58FA9D53', 'Prefabs/Vegetation/Tree/t_carpinus_betulus_2f.et'):
        'Prefabs/Vegetation/Tree/t_carpinus_betulus/t_carpinus_betulus_2f.et',
    ('8F91FCAFE65FCDA6', 'Prefabs/Vegetation/Tree/t_carpinus_betulus_3s.et'):
        'Prefabs/Vegetation/Tree/t_carpinus_betulus/t_carpinus_betulus_3s.et',
    ('208FEE0BF258449B', 'Prefabs/SP01/SP01_UAZ469_USSR.et'):
        'Prefabs/Scenarios/SP01/SP01_UAZ469_USSR.et',
}
MARINE_COBRA = ('0174CF72FDB6B805', 'Prefabs/Vehicles/Helicopters/AH1S/AH1S_Marines.et')
LACOURT_HORNBEAM_LAYER = 'worlds/James/LacourtManor/LacourtManor_Layers/map_objects.layer'
LACOURT_HORNBEAM_BLOB = '093740b8eb4453861952cf72981d43b60c46c195'
LACOURT_HORNBEAMS = {
    ('40DDF1D03B686B5A', 'Prefabs/Vegetation/Tree/Dst/t_carpinus_betulus_2f_dst.et'):
        ('4B176F8E58FA9D53', 'Prefabs/Vegetation/Tree/t_carpinus_betulus_2f.et'),
    ('F8A054C092BE9AAE', 'Prefabs/Vegetation/Tree/Dst/t_carpinus_betulus_3s_dst.et'):
        ('8F91FCAFE65FCDA6', 'Prefabs/Vegetation/Tree/t_carpinus_betulus_3s.et'),
}
MARINE_M299 = ('B8D8076535277E61', 'Prefabs/Vehicles/Helicopters/AH1S/AH1S_Marines_M299.et')
MARINE_M299_CURRENT = (MARINE_M299[0], 'Prefabs/Vehicles/Helicopters/AH1S/Marines/AH1S_Marines_M299_X2.et')

ZARICHNE_NAVMESH_ALIASES = {
    ('4C5DC23B33285CBA', 'NavMesh/Soldiers.nmn'): ('26764981146D437B', 'NavMesh/Zarichne_Soldiers.nmn'),
    ('6957D1DE8462E9A0', 'NavMesh/BTRlike.nmn'): ('7CF5039956B33F65', 'NavMesh/Zarichne_BTR.nmn'),
    ('0C645FF0DA66996D', 'NavMesh/lowres.nmn'): ('C4AE9581E30730A8', 'NavMesh/Zarichne_LowRes.nmn'),
}

WORKSHOP_RESOURCE_ALIASES = {
    ('28FA34E692783ACB', 'Prefabs/Items/GC_Facepaint_01_USSR.et'): (
        'ACEFacepaintDev_68EAA4B96976D557', '28FA34E692783ACB', 'Prefabs/Items/ACE_Facepaint_01_USSR.et'),
    ('3DD336DB69DCD4AC', 'Prefabs/Vehicles/Helicopters/Mi24/Mi24P_armed_R60.et'): (
        'WCS_Mi-24V_628933A0D3A0D700', '3DD336DB69DCD4AC', 'Prefabs/Vehicles/Helicopters/Mi24/Mi24P_Armed_R60_X2.et'),
    ('56E88FE5DDF6AA12', 'Prefabs/Vehicles/Helicopters/AH1S/AH1S_base.et'): (
        COBRA_PROVIDER, '56E88FE5DDF6AA12', 'Prefabs/Vehicles/Helicopters/AH1S/AH1S_Base.et'),
    ('73BC3BBE9B36EF4D', 'Prefabs/Vehicles/Helicopters/AH1S/VehParts/Turret/M197_M299_Turret.et'): (
        COBRA_PROVIDER, '73BC3BBE9B36EF4D', 'Prefabs/Vehicles/Helicopters/AH1S/VehParts/Turret/M197_Turret_M299_X2.et'),
    ('5D3C2A4DF727EB59', 'UI/Textures/EditorPreviews/Vehicles/Helicopters/AH1S/AH1S_Marines_AIM9.edds'): (
        COBRA_PROVIDER, '5D3C2A4DF727EB59', 'UI/Textures/EditorPreviews/Vehicles/Helicopters/AH1S/AH1S_Marines_AIM9_X2.edds'),
    ('808D62E2814BD5B1', 'UI/Textures/EditorPreviews/Vehicles/Helicopters/AH1S/AH1S_Marines_M299.edds'): (
        COBRA_PROVIDER, '808D62E2814BD5B1', 'UI/Textures/EditorPreviews/Vehicles/Helicopters/AH1S/AH1S_Marines_M299_X2.edds'),
    ('C45C3238BFA3D3A9', 'Sounds/Weapons/Launchers/FIM92_Stinger/Weapons_Launchers_FIM92_Stinger_Lock.acp'): (
        'WCS_Armaments_629B2BA37EFFD577', 'C45C3238BFA3D3A9',
        'Sounds/Weapons/Launchers/FIM92_Stinger/WCS_Weapons_Launchers_FIM92_Stinger_Lock.acp'),
    ('2FFB1076C8040CB4', 'Prefabs/Vehicles/Helicopters/Mi24/VehParts/Weapons/Mi24_Turret_base.et'): (
        'WCS_Mi-24V_628933A0D3A0D700', '2FFB1076C8040CB4', 'Prefabs/Vehicles/Helicopters/Mi24/VehParts/Weapons/Mi24V_Turret_Base.et'),
    ('54B4D76AFE0FA31B', 'UI/Textures/EditorPreviews/Vehicles/Helicopters/Mi24V/Mi24P_armed_R60.edds'): (
        'WCS_Mi-24V_628933A0D3A0D700', '54B4D76AFE0FA31B', 'UI/Textures/EditorPreviews/Vehicles/Helicopters/Mi24V/Mi24P_Armed_R60_X2.edds'),
    ('59769013F69B1DE9', 'Prefabs/Vehicles/Wheeled/S105/S105_rally_wcs.et'): (
        'WCS_Armaments_629B2BA37EFFD577', '59769013F69B1DE9',
        'Prefabs/Vehicles/Wheeled/S105/S105_rally_WCS.et'),
    ('3F0C718D8FA6E1AE', 'Prefabs/Vehicles/Helicopters/Mi8MT/Mi8MT_armed_GUV8700.et'): (
        'WCS_Armaments_629B2BA37EFFD577', '3F0C718D8FA6E1AE',
        'Prefabs/Vehicles/Helicopters/Mi8MT/Mi8MT_Armed_GUV8700_X2.et'),
    ('71E416DCB2BE86D7', 'Prefabs/Vehicles/Tracked/BFV/M2A2_BUSK_Desert.et'): (
        'WCS_M2A2_63120AE07E6C0966', '71E416DCB2BE86D7',
        'Prefabs/Vehicles/Tracked/M2A2/M2A2_BUSK_Desert.et'),
    ('051A6522307D9476', 'Prefabs/Compositions/Misc/FreeRoamBuilding/ACE_TrenchHalf_01_long.et'): (
        'ACETrenchesDev_65AD7CE59E8DB349', '051A6522307D9476',
        'Prefabs/Compositions/Misc/FreeRoamBuilding/ACE_TrenchSegment_01_short.et'),
    ('B9E3B9B143A43A79', 'Prefabs/Weapons/Magazines/AKM/Magazine_AK_762x39_AK55_30rnd.et'): (
        'TacticalFlava-Weapons_DEADC0DE00000001', 'B9E3B9B143A43A79',
        'Prefabs/Weapons/Magazines/AKM/Magazine_AK_762x39_Steel_30rnd_Ball.et'),
    ('4A30874E3A255689', 'Prefabs/Weapons/HeavyWeapons/2A46/Cannon_2A466_T72A_HE.et'): (
        'WCS_T-72_5E0AB16BEB16D6A4', '4A30874E3A255689',
        'Prefabs/Weapons/Cannons/Cannon_2A46/Cannon_2A46_T72A_HE.et'),
    ('279EDF225600C324', 'Prefabs/Weapons/HeavyWeapons/2A46/Cannon_2A46_T72A.et'): (
        'WCS_T-72_5E0AB16BEB16D6A4', '279EDF225600C324',
        'Prefabs/Weapons/Cannons/Cannon_2A46/Cannon_2A46_T72A.et'),
    ('2E1792971BFB33E2', 'Assets/Vehicles/Tracked/BMP1/workspace/BMP1_vehicle.asi'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '2E1792971BFB33E2',
        'Assets/Vehicles/Tracked/BMP1/workspaces/BMP1_vehicle.asi'),
    ('309FB429B1BB6ACC', 'Sounds/Vehicles/Tracked/Vehicles_Track_l.acp'): (
        'WCS_SpaceCore_5E389BB9F58B79A6', '309FB429B1BB6ACC',
        'Sounds/Vehicles/_SharedData/Tracks/Vehicles_Tracks_Left.acp'),
    ('DD496B0736385E78', 'Prefabs/Weapons/Magazines/Magazine_M240_Coax/Box_762x51_M240C_450rnd_4AP_1Tracer.et'): (
        'WCS_SpaceCore_5E389BB9F58B79A6', 'DD496B0736385E78',
        'Prefabs/Weapons/Magazines/Magazine_M240_Coax/Box_762x51_M240C_450rnd_4AP_1SLAPT.et'),
    ('30E1AF152CEB078A', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Road.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '30E1AF152CEB078A',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Road.et'),
    ('433A781E4D407EA7', 'Assets/Vehicles/Tracked/BMP1/workspace/BMP1.agr'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '433A781E4D407EA7',
        'Assets/Vehicles/Tracked/BMP1/workspaces/BMP1.agr'),
    ('6B4323202A9E8DB2', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Return.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '6B4323202A9E8DB2',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Return.et'),
    ('8021A4D1ADFCF506', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Turret/BMP1_Turret.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '8021A4D1ADFCF506',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Turret/BMP1_Turret_Base.et'),
    ('8251957B1516F33B', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Idler.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '8251957B1516F33B',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Idler.et'),
    ('8B3ABBF37425FFA3', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/BMP1_shadow.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '8B3ABBF37425FFA3',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/BMP1_Shadow.et'),
    ('91B4746C14E47D27', 'Prefabs/Weapons/Core/Configs/WCS_Armament_VehicleCameraAction.conf'): (
        'WCS_Armaments_629B2BA37EFFD577', '91B4746C14E47D27',
        'Prefabs/Vehicles/Core/Configs/Actions/WCS_Armament_VehicleCameraAction.conf'),
    ('966BEF4C4B522398', 'Prefabs/Vehicles/Tracked/BMP1/Configs/BMP1Suspension_Hard.conf'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '966BEF4C4B522398',
        'Prefabs/Vehicles/Tracked/BMP1/Configs/VehicleSimulationSuspensionFront_BMP1_WCS.conf'),
    ('986BBDD66E50B41F', 'Prefabs/Vehicles/Generic/VehParts/TriplexPlaceholder/Triplex_Placeholder.et'): (
        'WCS_T-72_5E0AB16BEB16D6A4', '986BBDD66E50B41F',
        'Prefabs/Vehicles/Tracked/T72A/VehParts/Triplex/Triplex_Placeholder.et'),
    ('B05996E21B24F44F', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Drive.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'B05996E21B24F44F',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Drive.et'),
    ('C1948C7DC2E45F20', 'Sounds/Vehicles/Tracked/Vehicles_Track_r.acp'): (
        'WCS_SpaceCore_5E389BB9F58B79A6', 'C1948C7DC2E45F20',
        'Sounds/Vehicles/_SharedData/Tracks/Vehicles_Tracks_Right.acp'),
    ('CB6A2EDCF9FF2AA9', 'Prefabs/Vehicles/Tracked/BMP1/Configs/CargoCompartment_BMP1.conf'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'CB6A2EDCF9FF2AA9',
        'Prefabs/Vehicles/Tracked/BMP1/Configs/BMP1_Cargo_Compartment_Left.conf'),
    ('D06C222F2BB4A491', 'Prefabs/Vehicles/Tracked/BMP1/Configs/CargoCompartment_BMP1_Right.conf'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'D06C222F2BB4A491',
        'Prefabs/Vehicles/Tracked/BMP1/Configs/BMP1_Cargo_Compartment_Right.conf'),
    ('D6A5CB323C315D68', 'Prefabs/Vehicles/Tracked/BMP1/Configs/BMP1Wheel.conf'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'D6A5CB323C315D68',
        'Prefabs/Vehicles/Tracked/BMP1/Configs/VehicleSimulationWheelFront_BMP1_WCS.conf'),
    ('DF59EC98B914811D', 'Assets/Vehicles/Tracked/BMP1/BMP1_BASE.xob'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'DF59EC98B914811D',
        'Assets/Vehicles/Tracked/BMP1/BMP1_Body.xob'),
    ('E18C63F9FC4D4D56', 'Assets/Vehicles/Tracked/BMP1/workspace/BMP1_player.asi'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'E18C63F9FC4D4D56',
        'Assets/Vehicles/Tracked/BMP1/workspaces/BMP1_player.asi'),
    ('E83FE945143E9FCC', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/CommanderTurret/BMP1_Commander_Turret.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'E83FE945143E9FCC',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Turret/Commander/BMP1_Commander_Turret.et'),
    ('ED3198AD721F851E', 'Prefabs/Vehicles/Tracked/BMP1/Configs/BMP1Suspension.conf'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'ED3198AD721F851E',
        'Prefabs/Vehicles/Tracked/BMP1/Configs/VehicleSimulationSuspensionMiddle_BMP1_WCS.conf'),
    ('FDF879E8CF62A137', 'Prefabs/Vehicles/Tracked/BMP1/Configs/VehicleSimulationTire_BMP1.conf'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'FDF879E8CF62A137',
        'Prefabs/Vehicles/Tracked/BMP1/Configs/VehicleSimulationTire_BMP1_WCS.conf'),
    ('C0221A073A780540', 'Assets/Vehicles/Tracked/BFV/BFV_Wreck.xob'): (
        'WCS_M2A2_63120AE07E6C0966', 'C0221A073A780540', 'Assets/Vehicles/Tracked/M2A2/M2A2_Wreck.xob'),
    ('C1EAE57BFC75D349', 'Prefabs/Structures/Bulding/HouseK/House_K_8_EP1_full.et'): (
        'ArmaTerrainCore_60B524F7A5AE96A8', 'F4D545DC6824850D',
        'Prefabs/Structures/Bulding/HouseK/House_K_8_EP1_full.et'),
    ('2FA364D8E326CB3C', 'Prefabs/Vehicles/Helicopters/Mi24/Mi24P_armed_9M114.et'): (
        'WCS_Mi-24V_628933A0D3A0D700', '2FA364D8E326CB3C',
        'Prefabs/Vehicles/Helicopters/Mi24/Mi24P_Armed_B8V20A_X4.et'),
    ('C8C37964483452EA', 'Prefabs/Vehicles/Helicopters/AH1S/AH1S_Army.et'): (
        'WCS_AH-1S_64CB39E57377C861', 'C8C37964483452EA',
        'Prefabs/Vehicles/Helicopters/AH1S/Army/AH1S_Army_M261_X4.et'),
    MARINE_COBRA: ('WCS_AH-1S_64CB39E57377C861', MARINE_COBRA[0],
                  'Prefabs/Vehicles/Helicopters/AH1S/Marines/AH1S_Marines_M261_X4.et'),
    ('89B49CDEBBEC973A', 'Prefabs/Weapons/Magazines/Magazine_127x99_M107A1_10rnd_M2.et'): (
        'TacticalFlava-Weapons_DEADC0DE00000001', '89B49CDEBBEC973A',
        'Prefabs/Weapons/Magazines/M107A1/Magazine_127x99_M107A1_10rnd_M2.et'),
    ('AB5AAFA893C60C2D', 'Prefabs/Weapons/Magazines/Magazine_127x99_M107A1_10rnd_M33_M10.et'): (
        'TacticalFlava-Weapons_DEADC0DE00000001', 'AB5AAFA893C60C2D',
        'Prefabs/Weapons/Magazines/M107A1/Magazine_127x99_M107A1_10rnd_M33_M10.et'),
    ('23C815180F127C5F', 'Prefabs/Weapons/Magazines/Magazine_762x51_SCAR_H_20rnd_m80.et'): (
        'TacticalFlava-Weapons_DEADC0DE00000001', '23C815180F127C5F',
        'Prefabs/Weapons/Magazines/FN_SCAR_H/Magazine_762x51_SCAR_H_20rnd_m80.et'),
    ('A58F99A3723E119C', 'Prefabs/Weapons/Magazines/Magazine_762x51_SCAR_H_20rnd_m62.et'): (
        'TacticalFlava-Weapons_DEADC0DE00000001', 'A58F99A3723E119C',
        'Prefabs/Weapons/Magazines/FN_SCAR_H/Magazine_762x51_SCAR_H_20rnd_m62.et'),
    ('9AA441DE6DAB6D94', 'Prefabs/Vehicles/Helicopters/Mi24/Mi24V_armed_S8.et'): (
        'WCS_Mi-24V_628933A0D3A0D700', '9AA441DE6DAB6D94',
        'Prefabs/Vehicles/Helicopters/Mi24/Mi24V_Armed_B8V20A_X4.et'),
    ('5CFD8CB5C3E55A4E', 'Prefabs/Vehicles/Tracked/BFV/M2A2_MERDC.et'): (
        'WCS_M2A2_63120AE07E6C0966', '5CFD8CB5C3E55A4E',
        'Prefabs/Vehicles/Tracked/M2A2/M2A2_MERDC.et'),
    ('AF1C02364D45CC5B', 'Prefabs/Vehicles/Tracked/T-72A/T72A_Desert.et'): (
        'WCS_T-72_5E0AB16BEB16D6A4', 'AF1C02364D45CC5B',
        'Prefabs/Vehicles/Tracked/T72A/T72A_Desert.et'),
    ('7FF511EDC4034D9F', 'Prefabs/Vehicles/Helicopters/Mi24/Mi24V_armed_S5.et'): (
        'WCS_Mi-24V_628933A0D3A0D700', '7FF511EDC4034D9F',
        'Prefabs/Vehicles/Helicopters/Mi24/Mi24V_Armed_UB1657_X4.et'),
    ('38DE44985B0AD338', 'Prefabs/Vehicles/Helicopters/Mi24/Mi24P_base.et'): (
        'WCS_Mi-24V_628933A0D3A0D700', '38DE44985B0AD338',
        'Prefabs/Vehicles/Helicopters/Mi24/Mi24P_Base.et'),
    ('2B9DB09AC8BEA673', 'Prefabs/Vehicles/Tracked/T-72A/T72A_FIA.et'): (
        'WCS_T-72_5E0AB16BEB16D6A4', '2B9DB09AC8BEA673', 'Prefabs/Vehicles/Tracked/T72A/T72A_FIA.et'),
    ('8A7ECB6BDF6ADC0F', 'Assets/Weapons/Launchers/9K38_IGLA/IGLA.xob'): (
        'WCS_Armaments_629B2BA37EFFD577', '8A7ECB6BDF6ADC0F',
        'Assets/Weapons/Launchers/9K38_IGLA/9K38_IGLA.xob'),
    ('1DC01AAACFFA8B63', 'Prefabs/Vehicles/Tracked/BMP1/BMP1_base.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '1DC01AAACFFA8B63', 'Prefabs/Vehicles/Tracked/BMP1/BMP1_Base.et'),
    ('9FD84A316EC55517', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Drive_Desert.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '9FD84A316EC55517',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Drive_Desert.et'),
    ('F40657D66B658694', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Idler_Desert.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'F40657D66B658694',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Idler_Desert.et'),
    ('D4CDA97B64620EAA', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Return_Desert.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'D4CDA97B64620EAA',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Return_Desert.et'),
    ('738645F98C2D1E87', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_IFV_BMP1_Wheel_Road_Desert.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '738645F98C2D1E87',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Wheels/VehPart_BMP1_Wheel_Road_Desert.et'),
    ('47A596669B0676E1', 'Assets/Vehicles/Tracked/BMP1/Data/Desert/bmp_wheels_desert.emat'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '47A596669B0676E1',
        'Assets/Vehicles/Tracked/BMP1/Data/Desert/BMP1_Wheels_Desert.emat'),
    ('CE1E9AA5FEAC34C5', 'Assets/Vehicles/Tracked/BMP1/Data/Desert/bmp1_hull_desert.emat'): (
        'WCS_BMP-1_5E524E4FEECCA92B', 'CE1E9AA5FEAC34C5',
        'Assets/Vehicles/Tracked/BMP1/Data/Desert/BMP1_Hull_Desert.emat'),
    ('23979C5C696D0EB5', 'Prefabs/Vehicles/Tracked/BMP1/VehParts/CommanderTurret/BMP1_Commander_Turret_Desert.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '23979C5C696D0EB5',
        'Prefabs/Vehicles/Tracked/BMP1/VehParts/Turret/Commander/BMP1_Commander_Turret_Desert.et'),
    ('023236B2A390BA5B', 'Prefabs/Weapons/Tripods/BGM71_TOW/Tripod_BGM-71_TOW.et'): (
        'WCS_Armaments_629B2BA37EFFD577', '023236B2A390BA5B',
        'Prefabs/Weapons/Tripods/BGM71_TOW/Tripod_BGM71_TOW.et'),
    ('C45AE68B74B6136D', 'Prefabs/Weapons/Tripods/BGM71_TOW/Pod_BGM-71_TOW.et'): (
        'WCS_Armaments_629B2BA37EFFD577', 'C45AE68B74B6136D',
        'Prefabs/Weapons/Tripods/BGM71_TOW/Pod_BGM71_TOW.et'),
    ('E7B6EE8A99C7F760', 'Prefabs/Weapons/Ammo/Ammo_Missile_BGM-71_BFV.et'): (
        'WCS_M2A2_63120AE07E6C0966', 'E7B6EE8A99C7F760',
        'Prefabs/Weapons/Ammo/Ammo_Missile_BGM-71_M2A2.et'),
    ('19D504D46A48529A', 'Prefabs/Structures/Military/Fortifications/Trenches/ACE_TrenchHalf_01_long.et'): (
        'ACETrenchesDev_65AD7CE59E8DB349', '19D504D46A48529A',
        'Prefabs/Structures/Military/Fortifications/Trenches/ACE_TrenchSegment_01_short.et'),
    ('1DF64746992BC64A', 'PrefabsEditable/Auto/Structures/Military/Fortifications/E_ACE_TrenchHalf_01_long.et'): (
        'ACETrenchesDev_65AD7CE59E8DB349', '1DF64746992BC64A',
        'PrefabsEditable/Auto/Structures/Military/Fortifications/E_ACE_TrenchSegment_01_short.et'),
    ('12C35C0A24EDA6D0', 'Prefabs/Vehicles/Tracked/BFV/M2A3_BUSK_MERDC.et'): (
        'WCS_M2A2_63120AE07E6C0966', '12C35C0A24EDA6D0',
        'Prefabs/Vehicles/Tracked/M2A2/M2A3/M2A3_BUSK_MERDC.et'),
    ('B3F2B47340817ED6', 'Prefabs/Items/GC_Facepaint_01_US.et'): (
        'ACEFacepaintDev_68EAA4B96976D557', 'B3F2B47340817ED6', 'Prefabs/Items/ACE_Facepaint_01_US.et'),
    ('9FF4AA98784BDF8B', 'Prefabs/Vehicles/Helicopters/AH6M/OPFOR/AH6M_OPFOR_base.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '9FF4AA98784BDF8B',
        'Prefabs/Vehicles/Helicopters/AH6M/OPFOR/AH6M_OPFOR_Base.et'),
    ('6CA46BC65DE861A8', 'Prefabs/Vehicles/Helicopters/AH6M/AH6M_Armed_base.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '6CA46BC65DE861A8',
        'Prefabs/Vehicles/Helicopters/AH6M/AH6M_Armed_Base.et'),
    ('72018C87FBDC9F8C', 'Prefabs/Vehicles/Tracked/BFV/M2A2_Desert.et'): (
        'WCS_M2A2_63120AE07E6C0966', '72018C87FBDC9F8C',
        'Prefabs/Vehicles/Tracked/M2A2/M2A2_Desert.et'),
    ('72C04A6EABC12143', 'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '72C04A6EABC12143',
        'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134_X2.et'),
    ('823CA4130CE90077', 'Prefabs/Vehicles/Helicopters/AH6M/AH6M_RKT.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '823CA4130CE90077',
        'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134_M260_X2.et'),
    ('24223D0679CDD612', 'Prefabs/Vehicles/Tracked/BFV/M2A2_OD.et'): (
        'WCS_M2A2_63120AE07E6C0966', '24223D0679CDD612',
        'Prefabs/Vehicles/Tracked/M2A2/M2A2_OD.et'),
    ('5B4927667C3291EF', 'Prefabs/Structures/Wall/CityDoor.et'): (
        'ArmaTerrainCore_60B524F7A5AE96A8', '5B4927667C3291EE',
        'Prefabs/Structures/Wall/CityDoor.et'),
    ('36EA87CEC1BE77E0', 'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK_Olive.et'): (
        'WCS_JLTV_5C721177A220B42F', '36EA87CEC1BE77E0',
        'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK_M2HB_Olive.et'),
    ('3868E03D9A4AF186', 'Prefabs/Vehicles/Tracked/BMP1/BMP1P_base.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '3868E03D9A4AF186',
        'Prefabs/Vehicles/Tracked/BMP1/BMP1P_Base.et'),
    ('5CFD8CB5C3E55A4E', 'Prefabs/Vehicles/Tracked/BFV/M2A2_MERDC.et'): (
        'WCS_M2A2_63120AE07E6C0966', '5CFD8CB5C3E55A4E',
        'Prefabs/Vehicles/Tracked/M2A2/M2A2_MERDC.et'),
    ('65FE6DD2E27E366E', 'Prefabs/Vehicles/Wheeled/JLTV/CROWS_2/Javelin/JLTV_CROWSII_Javelin_Olive.et'): (
        'WCS_JLTV_5C721177A220B42F', '65FE6DD2E27E366E',
        'Prefabs/Vehicles/Wheeled/JLTV/CROWS_II/Javelin/JLTV_CROWS_II_Javelin_Olive.et'),
    ('9B906EE7402FDA06', 'Prefabs/Vehicles/Wheeled/JLTV/CROWS_2/Javelin/JLTV_CROWSII_Javelin.et'): (
        'WCS_JLTV_5C721177A220B42F', '9B906EE7402FDA06',
        'Prefabs/Vehicles/Wheeled/JLTV/CROWS_II/Javelin/JLTV_CROWS_II_Javelin.et'),
    ('A19889DD96914B75', 'Prefabs/Vehicles/Wheeled/JLTV/CROWS_1/M2HB/JLTV_CROWS.et'): (
        'WCS_JLTV_5C721177A220B42F', 'A19889DD96914B75',
        'Prefabs/Vehicles/Wheeled/JLTV/CROWS/M2HB/JLTV_CROWS_M2HB.et'),
    ('E49B4C9AEA70BE3F', 'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK.et'): (
        'WCS_JLTV_5C721177A220B42F', 'E49B4C9AEA70BE3F',
        'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK_M2HB.et'),
    ('0F200E9F3D3AD905', 'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK_Woodland.et'): (
        'WCS_JLTV_5C721177A220B42F', '0F200E9F3D3AD905',
        'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK_M2HB_Woodland.et'),
    ('2E6481BC1EE9A767', 'Prefabs/Vehicles/Wheeled/JLTV/CROWS_1/M2HB/JLTV_CROWS_Woodland.et'): (
        'WCS_JLTV_5C721177A220B42F', '2E6481BC1EE9A767',
        'Prefabs/Vehicles/Wheeled/JLTV/CROWS/M2HB/JLTV_CROWS_M2HB_Woodland.et'),
    ('8A9444452D28F5B3', 'Prefabs/Vehicles/Wheeled/JLTV/CROWS_1/M240/JLTV_CROWS_M240_Woodland.et'): (
        'WCS_JLTV_5C721177A220B42F', '8A9444452D28F5B3',
        'Prefabs/Vehicles/Wheeled/JLTV/CROWS/M240/JLTV_CROWS_M240_Woodland.et'),
    ('D17CCBF7FE9AAFEC', 'Prefabs/Vehicles/Wheeled/JLTV/CROWS_2/JLTV_CROWSII_Woodland.et'): (
        'WCS_JLTV_5C721177A220B42F', 'D17CCBF7FE9AAFEC',
        'Prefabs/Vehicles/Wheeled/JLTV/CROWS_II/M230LF/JLTV_CROWS_II_M230LF_Woodland.et'),
    ('77CA4803A4A2B8E3', 'Prefabs/Drones/FPVBase/FPVRPG.et'): (
        'RealisticCombatDrones_65AD60E204191D37', '77CA4803A4A2B8E3', 'Prefabs/Drones/FPVBase/FPVRpg.et'),
    ('B0DFDF7AAA9C5D39', 'Prefabs/Weapons/Magazines/BC_Shell_12ga_Buckshot.et'): (
        'ChungusShotguns_620E584B1D2C96A4', 'B0DFDF7AAA9C5D39',
        'Prefabs/Weapons/Magazines/Buckshot 00/BC_Shell_12ga_Buckshot_00.et'),
    ('6B7CD408BDFB94F5', 'Prefabs/Vehicles/Wheeled/TIGR/GAZ_TIGR_v2_PKM_Green.et'): (
        'TacticalFlava-TIGR_DEADC0DE00000003', '6B7CD408BDFB94F5',
        'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_Green.et'),
    ('6F4D5B56F4905A62', 'Prefabs/Vehicles/Wheeled/TIGR/GAZ_TIGR_v2_Unarmed_Green.et'): (
        'TacticalFlava-TIGR_DEADC0DE00000003', '6F4D5B56F4905A62',
        'Prefabs/Vehicles/Wheeled/TIGR/Unarmed/GAZ_TIGR_v2_Unarmed_Green.et'),
    ('A317D959A4248173', 'Prefabs/Vehicles/Helicopters/AH6M/AH6M_GAU19.et'): (
        'WCS_AH-6M_6273146ADFE8241D', 'A317D959A4248173',
        'Prefabs/Vehicles/Helicopters/AH6M/AH6M_GAU19_X1.et'),
    ('77C3151F7A6E588C', 'SCR_AIWorld_Kunar.et'): (
        'KunarProvince_5C9691EA7FD7A79F', 'E2A016456BBC697D', 'Prefabs/SCR_AIWorld_Kunar.et'),
    ('77C3151F7A6E588C', 'Prefabs/SCR_AIWorld_Kunar.et'): (
        'KunarProvince_5C9691EA7FD7A79F', 'E2A016456BBC697D', 'Prefabs/SCR_AIWorld_Kunar.et'),
    ('BF5F2D022EA8CB36', 'Worlds/Bystrany/Bystrany_Map_v02.topo'): (
        'Bystrany_6626CB89B6B9C913', '0C931F860C09AC4C', 'Worlds/Bystrany/Bystrany.topo'),
    # The newer ChakChakChosen placements use this registration for the same rampart path and class.
    ('D25D2FE26B8DA6BE', 'Prefabs/Structures/Bulding/Military/fort_rampart_ep1.et'): (
        'ArmaTerrainCore_60B524F7A5AE96A8', '8F92FA56BC6BBB4F',
        'Prefabs/Structures/Bulding/Military/fort_rampart_ep1.et'),
}
RHIB_PATH = 'Prefabs/Vehicles/Naval/RHIB/'
RHIB_GUNBOATS = {
    ('DDE009B6A29873EE', RHIB_PATH + 'RHIB_Gunboat.et'):
        ('RHIB_Gunboat.et', 'C5A173E13289F0FB', 'M2HB'),
    ('38527BF34A02C3C7', RHIB_PATH + 'RHIB_Gunboat_M134.et'):
        ('RHIB_Gunboat_MK19.et', '2636057EEB31D2EA', 'M134'),
}
RHIB_LEGACY_MOUNT_LAYERS = {
    '48ca3751dc84ada292b3f543e3ed2d9efaac2b4c',  # Privateering
    '3a5e2a7ece2807560fb281b1ef0d4e43ed25016c',  # North Coast Raid
    '386dfc4dd48baa575d3b6893f973813518d2c878',  # Stealthing Into Villa
    '338ce4c121323ef57ed329ee547a1e1b2886c354',  # Oil Rig Rangos
}
RHIB_CURRENT_MOUNT_LAYERS = {'10cceaf84b88a05e2c3cad314b005f18334dba38'}  # Taking Trenches
WIRECUTTER_LAYER_BLOBS = {
    '7673b673c619b44b0f80cfb54d029b5cdd208350',
    'f5c094b3c8f19c596b5f44c2b903f6942e0c0874',
    '0f1ca2152e82aab22f40edde9c9922deeea15006',
}
WIRECUTTER_PREFAB = ('42232FFE6E7AC415', 'Prefabs/Items/Core/Wirecutters/wirecutters.et')
GRAVETTE_WIRECUTTER_ROLE = 'worlds/ac337/prefab/1980s SF and Partners/Partner Force/AC_PF_Rifleman_Ladder_P.et'
GRAVETTE_WIRECUTTER = 'worlds/ac337/prefab/Gravette_Wirecutters.et'
LEGACY_WIRECUTTER = re.compile(
    r'\$grp GenericEntity : "\{8554ED3F088BADA6\}Prefabs/Items/Core/Wirecutters/wirecutters(?:old)?\.et" \{')
UNITS_PREFAB_ALIASES = {
    ('E1A0797669362096', 'Prefabs/Groups/OPFOR/USSR/SovietArmy_Airborne_1983/Group_USSR_VDV_PHQ.et'): (
        'E1A0797669362096', 'Prefabs/Groups/OPFOR/Russia/1980s/Airborne 1983/Group_USSR_VDV_PHQ.et',
        '9ee0e54b862f0576caf8a1326ab05436a696fd55735026644ede6440c4ce5540',
    ),
    ('870783CDCE09CCD9', 'Prefabs/Groups/OPFOR/USSR/SovietArmy_Airborne_1983/Group_USSR_VDV_Squad.et'): (
        '870783CDCE09CCD9', 'Prefabs/Groups/OPFOR/Russia/1980s/Airborne 1983/Group_USSR_VDV_Squad.et',
        'e5fc8d79b6190df6bf1fc8c51ad3b701c50edc5cc35be76b21e5a4b3dd8c25f3',
    ),
    ('BE2630E26227D529', 'Prefabs/Vehicles/Wheeled/M998/GC_M1151_armed_M134.et'): (
        'BE2630E26227D529', 'Prefabs/Vehicles/Wheeled/M998/M1151/M134/GC_M1151_armed_M134.et',
        '1c9b03336c59ea87be7bce7a5e7c271bed535b54272e01c65e3cced9a205c1bc',
    ),
    # The previous Power Struggle variant migrated this exact vehicle radio slot in 1b3bd78c.
    ('88B4E9C3B4F3B89D', 'Prefabs/Items/Equipment/Radios/Radio_R148_Multi.et'): (
        'E1A5D4B878AA8980', 'Prefabs/Items/Equipment/Radios/RU/Radio_R148.et',
        '82b8d536d8af5b0d207dc83ecaf8fe0230c022b551dcfbb63a9001afc9df11f8'),
    ('A3E19E754552334B', 'Prefabs/Items/Equipment/Radios/Radio_PRC352.et'): (
        'A3E19E754552334B', 'Prefabs/Items/Equipment/Radios/UK/Radio_PRC351.et',
        '801d79b4ecfb2e2c7e9b78bc660b2cc5ca0cbfdfcbae9874807a90927fa34e77'),
    ('91766A63132EE378', 'Prefabs/Groups/BLUFOR/US/Army_1985_Rifles_Desert/Group_US_Army_1985_Desert_RifleSquad.et'): (
        '91766A63132EE378',
        'Prefabs/Groups/BLUFOR/US/Deprecated/Army_1985_Rifles_Desert/Group_US_Army_1985_Desert_RifleSquad.et',
        '7d3df1aef362ab7d1979cb4e18126135966c143144cd510790f27d86748388b2'),
    ('C00B26E6C8808EF8', 'Prefabs/Vehicles/Wheeled/M998/CRX_M1025_armed_M2HB_gunshield_MERDC.et'): (
        'C00B26E6C8808EF8', 'Prefabs/Vehicles/Wheeled/M998/CRX_M1025_armed_M2HB_gunshield_DESERT.et',
        '99815cf48312662ac44de7615b5a3c027287de6b28c1c96bf36f5d8b9a9d4d1c'),
    ('84EEEF18361F752D', 'Prefabs/Groups/OPFOR/GC_NVA/Group_GC_NVA_RifleSquad.et'): (
        '84EEEF18361F752D', 'Prefabs/Groups/OPFOR/GC_NVA/Group_NVA_RifleSquad.et',
        'f046a4d8717e2e9b23d830f420a53d84e454eea1acad22ab3356d896619ad845'),
    ('7A0813F1B65EA589', 'Prefabs/Groups/BLUFOR/US/Army_1985_Rifles/Group_US_Army_1985_RifleSquad.et.et'): (
        '7A0813F1B65EA589', 'Prefabs/Groups/BLUFOR/US/Deprecated/Army_1985_Rifles/Group_US_Army_1985_RifleSquad.et',
        '8f08612231343578e903ece165c370d0a48311eee482b803df90ab29dede97a7'),
    ('D57725689E9E8EDA', 'Prefabs/Groups/OPFOR/GC_SEPARATIST/GC_SEPARATIST_RifleSquad.et'): (
        'D57725689E9E8EDA', 'Prefabs/Groups/OPFOR/GC_SEPARATIST/2020/Group_SEP_RifleSquad.et',
        '0305282df1d84452f884b72815847edc9c0785314f54727aa7bfa88ab3ec2a3a'),
    ('2CE3925D1803A664', 'Prefabs/Vehicles/Wheeled/M998/M1025_armed_BGM-71_MERDC.et'): (
        '2CE3925D1803A664', 'Prefabs/Vehicles/Wheeled/M998/M1046_armed_BGM-71_MERDC.et',
        '0a545c5d39ebf2ef49440561ee0f1bda70276b58561e2a7bb6e7b44b333eeaf9'),
    ('BC07885176C7FF8A', 'GitHub/gc-reforger-units/Prefabs/Vehicles/Tracked/T-72A/T72A_base.et'): (
        'BC07885176C7FF8A', 'Prefabs/Vehicles/Tracked/T-72A/T72A_base.et',
        '2ec90305fa151423829093e490029d2a994c655b6b70b47ef1291b2b1207674d'),
    ('26BBC4FA9CE02F61', 'Prefabs/Characters/Backpacks/Radio/Kombat_Radio_Pack_MTP.et'): (
        '26BBC4FA9CE02F61', 'Prefabs/Items/Equipment/Radios/UK/Kombat_Radio_Pack_MTP.et',
        '7803e7446801c2bed2905beb134571e78b0b5f6b4b88e34a06c4c3a2a06a0d8a'),
    ('4F064E9878DCF301', 'Prefabs/Vehicles/Wheeled/M998/M1151_armed_BTR_TAN.et'): (
        '4F064E9878DCF301', 'Prefabs/Vehicles/Wheeled/M998/M1151/BMP_Turret/M1151_armed_BMP_TAN.et',
        'f83f4593e93f45e86b3e65f59995c436877a70399bb0d6310934efbfbe06f1fb'),
    ('E69BD3AC16F5AA77', 'Prefabs/Vehicles/Wheeled/M998/M1151_armed_BMP_TAN.et'): (
        'E69BD3AC16F5AA77', 'Prefabs/Vehicles/Wheeled/M998/M1151/BTR_Turret/M1151_armed_BTR_TAN.et',
        'c2ed980a0600ae70cdf3c94909e59b5eb5164c75e78f7474f2efdea330aea0f4'),
    ('AAB3119124C9167A', 'Prefabs/Vehicles/Wheeled/BTR70/1980 Mot Rifle/BTR70_MSV80.et'): (
        'AAB3119124C9167A', 'Prefabs/Vehicles/Wheeled/BTR70/USSR/BTR70_USSR.et',
        '6ff98c749e6e58fb337ad80c11bdb2cabcf5beebe84afdf0fc8a1e6e917bdd94'),
    ('AAB3119124C9167A', 'Prefabs/Vehicles/Wheeled/BTR70/USSR/BTR70_MSV80.et'): (
        'AAB3119124C9167A', 'Prefabs/Vehicles/Wheeled/BTR70/USSR/BTR70_USSR.et',
        '6ff98c749e6e58fb337ad80c11bdb2cabcf5beebe84afdf0fc8a1e6e917bdd94'),
    ('F503F71C08BE3F7D', 'Prefabs/Vehicles/Helicopters/AUS_MH60/AUS_UH60_armed.et'): (
        '48F288F5BFF10D8B', 'Prefabs/Vehicles/Helicopters/AUS_MH60/AUS_UH60_armed.et',
        '67ea7fa93be8a84811d12f1be66ae2bce2b58f0a881dbd4375e0a89cbeeee5cd'),
    ('B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/Radio_R187P1_OLD_Separatist.et'): (
        'B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/RU/Radio_R187P1_OLD.et',
        '5575080709db948fbfb8f0a84fe746aa32e3f72aec97ccbc41ff02e5ea8bb741'),
    ('B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/Radio_R187P1_OLD_Insurgent.et'): (
        'B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/RU/Radio_R187P1_OLD.et',
        '5575080709db948fbfb8f0a84fe746aa32e3f72aec97ccbc41ff02e5ea8bb741'),
    ('B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/Radio_R187P1_OLD_Single_Separatist.et'): (
        'B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/RU/Radio_R187P1_OLD.et',
        '5575080709db948fbfb8f0a84fe746aa32e3f72aec97ccbc41ff02e5ea8bb741'),
}
RASMAN_AA_GROUP = 'worlds/HotMic/Prefabs/Characters/Afghan/GC_INS_Afghan_Squad_WithAA_HotMIc.et'
LOCAL_PROBLEMS_MILITIA = 'worlds/Cunnah/Prefabs/Groups/INDFOR/Militia/'
LOCAL_PROBLEMS_DEFAULT = 'worlds/Cunnah/Local_Problems/Local_Problems_Layers/default.layer'
LOCAL_PROBLEMS_DEFAULT_BLOB = '7cce7d507e5d9cc83c4899fe9050b48adcd32508'
LOCAL_PROBLEMS_RADIO = 'worlds/Cunnah/Prefabs/Items/Equipment/Radios/Radio_R187P1_Garrison_Single.et'
LOCAL_PROBLEMS_ROADBLOCKS = ('worlds/Cunnah/Local_Problems/Local_Problems_Layers/OPFOR_AI.layer',
                             '90bf857673479e4a3eb2ebe70a561d4a1a40ca47')
LOCAL_PROBLEMS_GROUPS = {
    '6B10747A3401270A': LOCAL_PROBLEMS_MILITIA + 'Militia_AT_Team.et',
    '7B626931CD41BC3F': LOCAL_PROBLEMS_MILITIA + 'Militia_MG_Team.et',
    'E86B8D95400DA31C': LOCAL_PROBLEMS_MILITIA + 'Militia_Rifle_Squad.et',
}
REVIEWED_NATIVE_ASSETS = {
    'worlds/TheLocalPub/PublicEnemies/Configs/Factions/GC_Federal.conf':
        '515c0a77a7e644da48fb4a40d4505570a7eadc252abc759baecada29387d6171',
    'worlds/TheLocalPub/PublicEnemies/Configs/Factions/GC_Mobsters.conf':
        '8eee8347f9d62082b7f717f6a98ceb1f953d38e5245791010b29f8487fb5d845',
    LOCAL_PROBLEMS_RADIO: 'b3f2f48fa135288210469d75cbd093e03636ef83e88fad0165dd9baf20f976ce',
    LOCAL_PROBLEMS_MILITIA + 'Militia_AT_Team.et': '05b1fdfb3e365e7ee397b792ee4dc21bec9e6926f057cf3bcec9ea62a7d58557',
    LOCAL_PROBLEMS_MILITIA + 'Militia_MG_Team.et': 'e0d808e8c2bb3dd64afa7389a75974274678127a61d5ccb78bbc9c0f2f28b82f',
    LOCAL_PROBLEMS_MILITIA + 'Militia_Rifle_Squad.et': '02731df540593fd4f1dd11c2ed5fcfbd97d9c81891b44d334084f0869bc3ecb1',
    # Oil Rig Rangos uses the stock warhead parent and the original Anizay fuel effect.
    'worlds/TheLocalPub/OilRigRangos/Prefabs/Weapons/Warheads/Explosions/Explosion_Tnt_Large.et':
        '8601adcda3595b20f8342e7ec8d866785a22826174f72ab9f6c921cb68474d8f',
    'worlds/TheLocalPub/OilRigRangos/Prefabs/Weapons/Warheads/Explosions/Explosion_nodamage.et':
        '480be3cec4967d21c3644962ebafbb2bd18f1192375a5d2a0e1fc5c43c48d88d',
    'worlds/TheLocalPub/OilRigRangos/Prefabs/Weapons/Warheads/Explosions/Explosion_nodamage_fire.et':
        '3d8ec1a3caf47e2cbc7202ad014b683311dddac3cfe245b06d1d6a2a64695927',
    'worlds/TheLocalPub/OilRigRangos/Prefabs/Items/Equipment/Radios/Radio_R187P1_OLD_Single.et':
        'beae3ccce22aa16b1005bfcb5691cdbd97f39a75f3ccc8c6c5067db9ab5b7b39',
    TRIFECTA_ROOT + 'Prefabs/SafehouseV3.et': 'd196c18e73eceb95035902b1fd8daa3e9cdf1766c207d933f13cac4bf33c6fde',
    TRIFECTA_ROOT + 'Prefabs/Solar_Panel.et': '87b5822c315917f2cbff304ad571639ac9d64bca8bef3987cc777a47724eb920',
    TRIFECTA_ROOT + 'Prefabs/SVDM_Infratech.et': '7f39ee1ee6d296b8fbc6f1ecd6182eb29feef5ca93285ef51324795a992ebff3',
    TRIFECTA_ROOT + 'Prefabs/SR_25_REAPir.et': '9eb5e863fa4e40aaf99d1fab0269739cb452f8da4016a3680a164cc427ca6a5b',
    RASMAN_AA_GROUP: 'ee9f8f696dbc3ee06ad5d2218082bf11703a4575b6ab5d75830d953c0ff9006d',
    'worlds/chllboy/Hephaestus Forge/Prefabs/AUS_UH60_armed1_EW.et':
        '24b130cf49808656d5d1ae0aff3e2f6fb1345253aa5925adda880c290bb21787',
    'worlds/chllboy/Hephaestus Forge/Prefabs/AUS_UH60_unarmed_EW.et':
        '668dadddeb114cb6650274f8955645a4ecc3bee2bf60789d2053744c067372a0',
    'worlds/chllboy/Hephaestus Forge/Prefabs/M107IR.et':
        'dd7b812de41c17ef03876ef612b8caa729ffa5c4a458b95eb183c50f9ab6547f',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Cartridge_545x39.et':
        '7e08bfb1b97ae1046f1bbe4097fec969536f3ae72f69fc68bd7ff188ccc1779a',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Cartridge_762x39_PS.et':
        '8617eea03a8dc0a4fbf9152d273c3bac128be02aae48f6ffd4b9ae6bb48847c0',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Cartridge_762x51.et':
        'f784feaf3210e9c981ad69bd30901d96fecacc4cad9f08f9c61b61526a1c3b65',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Cartridge_9x18.et':
        '1b7f7b2f381afe3bf9ff3e7eabd98bbe46d302b91b144757261a00e5bd056093',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Cartridge_9x19.et':
        'b691e18790bb3d01e7be8b658efaada9fe299b0670abed3505ba05f74da731ab',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Firebarrel.et':
        '880d6c8fea8300590e220973531da31bcf3b072ae1fd7d81bb9eabd2cdaa2080',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Pouch.et':
        '9a88b76d0c7753b3d930a325cbabb41f9d8cc81b4acbf1f8550fdb10e4e8ee09',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Tire_fire.et':
        '139d7d110eecbb7a6da18c2f2fc97c6960407dc54c585c4cf12c156e307c21b2',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Tire_fire_large.et':
        '3d77dc4534932792998ac38c4bccf7d45de2436af02a7c3f2a696c1cf9160308',
    'worlds/chllboy/Hephaestus Forge/Prefabs/Props/Tripod_6T5.et':
        'e5ccda04571298143b1e6e84891a7f44ba8177ced11489867dff33a95d1d8814',
    'worlds/Til/RouteTremaux/Prefabs/blockers/abatis_1.et':
        '4c85ef09b8fa042f302061b88607c9a55aa836c6fde8cbe8cabc13b0c7cb0601',
    'worlds/Til/RouteTremaux/Prefabs/blockers/bridge_wrecks.et':
        '464176b25f231dd2d59b9b8b4c28b6b28d0fa97e6c1ca30fad42e349f38a96f9',
    'worlds/Til/RouteTremaux/Prefabs/blockers/creek_wrecks.et':
        '3e8a15c8a3902683c441513625518eeb6c23b7f52f226a8dc2e4ad4e093777e9',
    'worlds/Til/RouteTremaux/Prefabs/blockers/rocks_1.et':
        '15df5d446eda9c0b71b02e2646d7640ffafcb3460a5e919d7e09cf36f8ac1939',
    'worlds/Til/RouteTremaux/Prefabs/blockers/wreck_blockage.et':
        '57cf0e6aefc8c9dbd57289a94eeb7425e2e453e8a1b59357377faf4bde1c8ce0',
    'worlds/FSM/Prefabs/Weapons/Magazines/FSM_Box_762x51_M240_100rnd_2Ball_1Tracer.et':
        '676f8f91d9795922ddb9a37da90842d5e1ad37c5650202ccba29cfc9757efd00',
    'worlds/Jub/OperationJaws/prefabs/JAWs_GC_M1151_armed_M2HB_MDO_OGPK+Cover_tan.et':
        '2367afc084709214966caba3aa9876e6c2b36053d81a4513fb22b8e254730596',
    'worlds/Jub/OperationJaws/prefabs/JAWs_M1151_armed_MK19_cover_Tan.et':
        'd9b16ded48ce5d6dbb8518e747d0273dad7607a1f3c1eda4909243216ba09769',
    'worlds/anteante101/Prefabs/Weapons/MG_M60_empty_AA101.et':
        'b13396f1e6d2b266144ee6f661bbab573e5ce93447e6cbac7db9bee6250db598',
    'worlds/anteante101/Prefabs/Weapons/Rifle_MP5_empty_AA101.et':
        'dca15e409fc2f836c3a5b269edbd14b8db94669d184b3d834eecd6698d328860',
    'worlds/HitTheMan/Air Assault Onto Skalisty/Prefab/AmmoCrateStingers.et':
        'ed732d82b4cdc573ea1177b09b7ab99a5c0239d33386c73b594fabad9abb174f',
    'worlds/HitTheMan/Air Assault Onto Skalisty/Prefab/Mi8MT_armed3HTM.et':
        'd0599de581d4c3ecb31d23a947538fdae8a768f09f2c7da200b9a8f04563886b',
    # Keep the repair station, three default occupants, and Gulf War appearance.
    'worlds/Perfk/Prefabs/Vehicles/Wheeled/M923A1/M923A1_repair_GulfWar_unlimited_supply.et':
        '5f43afde1cd048677e478cef8982457ffb77f47313d296c24383793714f42b80',
    'worlds/Corax/IntercectingForces/EffectModule_Zoned_MortarBarrage_Larger.et':
        '1c43b6578d12fd59e1d7c04ea36ef87054d9ad0604b97938e65e0abd4ad37f44',
    'worlds/James/KhandarClearance/EffectModule_Zoned_BM21Barrage.et':
        '3d5e01e2ce8c84f17f1462bdaf81f1ee5b357c3b695c13ea7ea263043689d78f',
    'worlds/James/KhandarClearance/Ammo_Rocket_9M22_HE_EffectModule.et':
        '02eb4e34d0f8980b8690ca07ce115846d0788d0953c2168e466d33a9392cfb90',
    'worlds/HitTheMan/Battle For Zarichne/Mine_M15AT_EffectModuleEntity_HTM.et':
        '9ddd4b64ea129d6435e224f5732052697e70e5e16b29f3221015e121193bc961',
    'worlds/HitTheMan/Battle For Zarichne/Warhead_Mine_M15AT_HTM.et':
        '907ccedfaf6cca314f647e329ffb44f908d135a980dfd054364a057284c98869',
    'worlds/Til/MetsalaAirfield/Prefabs/Mi8MT_Hardpoints_black_HEDP_2.et':
        '2a457545cecc38d326ff96d123e76fad987f90704c3dc0eeac8c4aaa400c700b',
    'worlds/HitTheMan/2nd Break The Line/HTM_EffectModule_Zoned_MortarBarrage_Creeping.et':
        '730b92d721c0fb153f471e346b1fe2368f347e96b9f4e95e0471eda1e1625ff9',
    'worlds/Perfk/Prefabs/Crates/pk_MedicalCrate.et':
        'f52ff16de37f38ddfe97d2b655a3a9e86fffe38aaaf5572c4ca3585ea32a8484',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_02_Fighters.et':
        '1d9d5c3dbefc0eaa86ad7398bbe90607a75bbd287ee892d010866b1e9ebd95d1',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_03_Fighters.et':
        '8c810ae694d1f6d566c04b7b8f6e00de9750b02135c106f999990dbcac7b26c0',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_07_Fighters.et':
        'c4a1547a97ab19afea92c1613fbbdc9fb9992eb7d99928d4dd8274eb81739fd2',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_11_Fighters.et':
        '672813fbb0f03f92ced2d3978ca4c485dd84d2f9a0c4edecad583c5401a9637b',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_13_Fighters.et':
        '7375d119e66768daf4d1bf9959d3c1593396b714258459b0076e5dfdaf6ee41b',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_04_Fighters.et':
        '3b919002ce6916b5cb8bed6f284d34344dc32013b8f85190e927474e6240279d',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_03_AT_Fighters.et':
        '87058deab8acd463db439ce499d2794409dc568fcad095a2522e1425429adb7b',
    'worlds/Perfk/Prefabs/Groups/PK_MUJ_03_MG_Fighters.et':
        '9a73cdc93d8d1e25d82db2f285b511c509c7268f028e17cbcf982154ad22ea07',
    'worlds/Perfk/PrefabsEditable/EffectsModules/Mine/PK_EffectModule_MineField_Single_USSR.et':
        '0fe010cc26fab16cde911abed109c7e8b04f04dfc62ae0cc2284824997adc679',
    # These supply containers keep their authored capacity, fill, and decay settings.
    'worlds/HitTheMan/Seeder Cold War Domination/Prefabs/Respawn Vehicles/HTMRepairingBox.et':
        'f2b3f512ef0977f2970103d0acb48292362a366871af235f29711b8bc4e08854',
    'worlds/HitTheMan/Seeder Cold War Domination/Prefabs/Respawn Vehicles/HTMTransportBox.et':
        '6bb4a1bde834d64e26cc765f3729861c136b95c97a9574876a0ef42f28aadc03',
    'worlds/HitTheMan/Seeder Cold War Domination/Prefabs/Respawn Vehicles/HTMTransportBoxSupply400.et':
        '00b6fa2144d52bc181f98232665bb97aeebe510c73ef55c65d8ad5ae92c98a0c',
    'worlds/HitTheMan/Seeder Cold War Domination/Prefabs/Respawn Vehicles/HTMTransportBoxSupply500.et':
        '4869b2be5055f2d6d756040f13f2424cda87200c1760cc17af7871f9e493cd3f',
    'worlds/Jub/CobrasBite/Prefabs/JLTV_Base_CobrasBite.et':
        '07961f825852cbda25e268caeeb97adcd89b71b431f82469bce28866e1e6e869',
    'worlds/Jub/OperationArrowheadMulladostPass/Prefabs/Stryker_CROWS_Cage_MK19_HQ.et':
        '96d86ba4b10a812c3e1c315729ea14061ad52132abc690d566b979304769d723',
    'worlds/Jub/OperationArrowheadMulladostPass/Prefabs/Stryker_CROWS_Cage_combatload.et':
        'ebc93ec2130658bcaf95f486c1348663bb93c81095d92ba354769271b0d48c05',
    'worlds/anteante101/Prefabs/Weapons/Rifle_M16A2_carbine_empty_AA101.et':
        '2339ab3175b7db61be53fa243235efac60dff4ed8913b13376d299bdd40cdf25',
    'worlds/anteante101/Prefabs/Weapons/Rifle_M16A2_empty_AA101.et':
        '3e74c9f128709371d06128b3be1866f22c6d05cb2c3b3f4617eea087f07adfca',
    'worlds/ac337/prefab/1980s SF and Partners/SF/Rifle_M16A2_suppressor_M203_A2k.et':
        '30c03bee49f56c89683434638d6a35dcd859a29971d31db5f37ec22da4fe580b',
    'worlds/FSM/Prefabs/Items/Core/Wirecutters/FSM_wirecutters.et':
        '130d030871b4ba325e17d9314d447a2f3d1366a268868e0159affbeb67381a6a',
    'worlds/RedTheKnown/ChakChakChosen/Prefabs/RebUnits/Custom_Shortmag_BC_PPSh41.et':
        'dc68fa28e9136e995f0d03acfb29fe7b74d95d5b136df9f87df9cb1cee610b1e',
    'worlds/HitTheMan/2nd Break The Line/HTM_EffectModule_Zoned_Smoke_MortarBarrage_Creeping.et.et':
        '7b56f96c988c3e00ffa59c446488ab8ac9f50809a7772670a501339953d36a56',
    'worlds/HitTheMan/Prefabs/Vehicles/Heavy Weapon/Cannon_2A466_T72A_HE_HTM.et':
        'ed928b9073da5bc06a95ee0230feca72b2cb404502475cbd88093dfa581fdabd',
    'worlds/HitTheMan/Prefabs/Vehicles/Heavy Weapon/Cannon_2A46_T72A_HTM.et':
        '947b118bc08f1142bc90add6855cb8c8fe38a6d0ed7ca612175f39712c5d32e6',
    'worlds/HitTheMan/Prefabs/Vehicles/BMP1_base_HTM.et':
        '411e80c186456e96276392a2023aa9e5881aae911c51d13fdb471371822cfebb',
    'worlds/HitTheMan/Prefabs/Vehicles/T72A_Green_AI.et':
        '391cbad064ac66bd1b1c9b519d0d5736c7102e8c91312b81875a1aa65832bceb',
    'worlds/HitTheMan/Prefabs/Vehicles/T72_turret_Green_AI.et':
        'abb9f2523a46e2443e0e3c8d1eddd786ebc0f37b40867fbb4c82d22c5d77ee81',
    'worlds/HitTheMan/VDV Blockade/t_picea_abies_3f_high_HTM.et':
        'cb1cdb2ae1479ec1b3eb4a0c67131fc126ed64a84fee0dd7aebc276f34d5ff12',
    'worlds/FSM/Prefabs/Weapons/Explosives/DemoBlock_M112/FSM_DemoBlock_M112.et':
        'b0f3de095593e9567db7ee6d08c54f12a0cc01d59b13f1af973f45ca64b713fb',
    'worlds/FSM/Configs/Weapons/Ammo/FSM_545_Tracer_Array.conf':
        'd3d90125f1f867ba3fc99106125de72b9d6ea527f21d2742326b5aca58ce3c2a',
    'worlds/FSM/Configs/Weapons/Ammo/FSM_762_Ammo_Tracer_Array.conf':
        '1e9470f7ee255e0551d2b7909b894f94319c854507b8e0ddb8aa25b8a5ec7c2b',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_762x54_Tracer_Green.et':
        '0bd76a58fc60cac32411149e72cd91edbb03a6e824967e9be2a5828f42268f04',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_Tracer_Bullet_Blue.et':
        '60f8f32ba70e690cab282e4a0c65c74c4dac9a6cb805638dbf11290c2716daa7',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_Tracer_Bullet_Green.et':
        '632f7914bb05c690047d467de77919edc4a111f820f2478060bf2c7d5e5420c1',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_Tracer_Bullet_Orange.et':
        '68274861b1059c85aca4883cf43d6a4a3903f1e989c563e62958dc243064e89b',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_Tracer_Bullet_Purple.et':
        '8a943a3d8674196c344a3333c44e0bbd76a5f9f646cd19e08ab98935b1c7ca3f',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_Tracer_Bullet_Red.et':
        'a40f10bf7d55f850d0c9c806adf66e91c1fdee08f09f0bd646199fe58b7ee3cf',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_Tracer_Bullet_White.et':
        '5017776f628d8da3b97719e310147e6b5d0ad8133e8adbdc0c4c3d8b395cb5f7',
    'worlds/FSM/Prefabs/Weapons/Ammo/FSM_Tracer_Bullet_Yellow.et':
        '4527850029d27af100c2f84e7480151f7bdc61ca6df5c0aa1765994f200e2aa5',
    'worlds/FSM/Prefabs/Weapons/MachineGuns/PKM/FSM_MG_PKM_mixed.et':
        '5d8ccf3b2775455b40361b22bfbdf7e12b6845ce31beee3dc9a6bcaf08368bd8',
    'worlds/FSM/Prefabs/Weapons/Magazines/FSM_545_30rd_Plastic_Black_Reload_tracer.et':
        '97c08a73b4cbfd8ebde1dab9619db9eff701d802e07d46de66e96943782d53cf',
    'worlds/FSM/Prefabs/Weapons/Magazines/FSM_545_30rd_Plastic_black_Tracer_green.et':
        '648ac0989da8cefc6239946998d4fcbca4bc6063cd47403a6cae01b0c7bf36ab',
    'worlds/FSM/Prefabs/Weapons/Magazines/FSM_Box_762_3rd_1rd_tracer.et':
        '1d90a0e1e5a517138552a71933c109fae0c46cc4c90631237c92b1235f0479ba',
    'worlds/FSM/Prefabs/Weapons/Rifles/ak74_Reload_tracer/FSM_AK74M_GP25_RT.et':
        '75be11888ee610a691a74124adf3b3f02fcc7a475758a2b847904dc2d8d125bd',
    'worlds/FSM/Prefabs/Weapons/Rifles/ak74_Reload_tracer/FSM_AK74M_RT.et':
        'cde01b0a9e57e7eb8d6a4a4e1d15dd8fef73021293e630f0e3c70bc044f1bcb6',
    'worlds/FSM/Prefabs/Weapons/Rifles/ak74_Reload_tracer/FSM_FANCY_AK74M_b10m_b19n_Holo_RT.et':
        'da3036c9c96a2f5b3da4ca781fa2fb9339e8f3ae64e2fee5ec7570547f225249',
    'worlds/FSM/Prefabs/Weapons/Rifles/ak74_Reload_tracer/FSM_FANCY_AK74M_b30u_b31n_RT.et':
        '5fcc4f78b28f6f29ad539f5379342a4081ac0e04d81971e93a371fb389cdd2dd',
    'worlds/FSM/Prefabs/Weapons/Rifles/ak74_Reload_tracer/FSM_Rifle_AK74_RT.et':
        'bb196a9a9eb14ffa7d72047731f654cf0d456590fd88050d42c4d653c9da557f',
    'worlds/TheLocalPub/RailroadReactions/Prefabs/AI/Groups/MSV_Flora_RifleSquad.et':
        '0a5ac894b9d00d95a61ab7bff77bbda73bc0a1adf45e26b8a630587850f3ea09',
    'worlds/HitTheMan/Prefabs/Vehicles/BMP1_Desert_ZU_HTM.et':
        '0ae50a7102b52545fd8413762cc3bb141a06d12a29bae7f8387c354d2621676f',
    'worlds/Aurelas/Spearhead Air Support/Prefabs/HQ_USSR_Simple.et':
        'f2a2d8a7f3d24e45c7d2773920168bf272e9bee1158faab7dcc1b78fa43a0777',
    'worlds/Blue/Bangalorious/Data/M252PIT_NOARSENAL.et':
        '2d378e69fd2a331f702d635b90dafbd0f93b605b9c89af17dd64180863740371',
    'worlds/Blue/Bangalorious/Data/Trenches/ErodedEmbankment_01_dirt_straight_GREEN.et':
        '578197d9e7526e6d4a15a2c60861fdabb443ecb1d5241bb6796080b1b696e6c2',
    'worlds/Blue/Ruckusberg/Prefabs/LR_3Series/3Series_LWB_Transport_EXPANDEDSTORAGE.et':
        '7ed00aa822c1c0bb716eefa5ac3a6181b85de3b2dbf9c094a54126f2c67bfefd',
    'worlds/FSM/Prefabs/Props/Military/Sandbags/FSM_Sandbag_01_end_high_plastic_low_placeable.et':
        'b77d4439f47dd8452582fcd9b8bebf5db826d51117a9d5e4186a35feb9334a62',
    'worlds/FSM/Prefabs/Props/Military/Sandbags/FSM_Sandbag_01_end_high_plastic_placeable.et':
        'a7b2487984061cc3d6a020abc129f2908c0711d3019edc37c75bb22ae84d70b3',
    'worlds/FSM/Prefabs/Props/Military/Sandbags/FSM_Sandbag_Wall_Round.et':
        '37cfb3d12c54adc6d226c8847031a7b925d0a26ab2ce371c51e4a05e8baafe1c',
    'worlds/FSM/Prefabs/Props/Military/Sandbags/FSM_Sandbag_Wall_Tall.et':
        '476dbb7847accdd576f3f4d29b69280aa6dd4f465625f247a12757e8d9baee8c',
    'worlds/FSM/Prefabs/Props/Military/Sandbags/FSM_Sandbag_Wall_Tall_Window.et':
        '2afa48319772152dc3d008ed4969183e004e30aa55db624ef926786382927656',
    'worlds/FSM/Prefabs/Props/Military/Sandbags/FSM_Sandbag_Wall_long.et':
        'bc8b2c0ab3eba61460d4232985c91a9bd06b3d45f8f656bcb36a149cfb76f08b',
    'worlds/FSM/Prefabs/Vegetation/Cuttings/FSM_Placeable_Juniperus_Communis.et':
        'ca6596393d8b0a9b786774139919e3cd44938a02ab9092bdcf78f5fb09d46196',
    'worlds/FSM/Prefabs/Vegetation/Cuttings/FSM_Placeable_Malus_domestica.et':
        '6d38a6cf1ce4048a32c4fc5b4d7a9b6c9638e9502b8877e3cff35bb62e58efaa',
    'worlds/FSM/Prefabs/Vegetation/Cuttings/FSM_Placeable_Pinus_sylvestris_medium_01.et':
        '6edb925d66607b633511de2a204201e3b2f976b4d826a6f637ac73f5e68004b1',
    'worlds/FSM/Prefabs/Vegetation/Cuttings/FSM_Placeable_abies_bush.et':
        '49159627e2835d6551c9a9416269892d4ce95906a33228ec6d9cadde2d78ad47',
    'worlds/FSM/Prefabs/Vegetation/Cuttings/FSM_Placeable_pendula_pendula.et':
        '3ba599adf536e5f4f0e674acd23d078f87416823507ab53d51f11df4f5b253d2',
    'worlds/Gabe/BlocParty/Prefabs/GAZ_TIGR_v2_AGS17_Gabe.et':
        'a0d24dd42e4091b9606d03c586af25963c59c90fefa6d92930848a336b26584c',
    'worlds/Gabe/BlocParty/Prefabs/GAZ_TIGR_v2_KORD_Gabe.et':
        '053dc32088587e8d5fea21baeb5f3df559d7e74a833482639e7f7a13f288e791',
    'worlds/Gabe/BlocParty/Prefabs/Ural4320_transport_Gabe.et':
        '12755e43dc29609547ce1fd643e5cc843c938b5dfab536c2a802f70076778042',
    'worlds/HitTheMan/Prefabs/Lights/LampFluorescent_01_HTM.et':
        '065ba341fe6d6b630bdda1f4edb5bcfff9688602f91716f82e57aa589c90620f',
    'worlds/HotMic/Prefabs/Trucks/truck_engineer_box_wo_commandPost_HotMic.et':
        'd86339702bbe500b500e2c0269628498f7d62e8d26fe417d0c2fd2d4220418ad',
    'worlds/Perfk/Prefabs/Structures/PK_WaterTower_01.et':
        '39b5382023c3757f702c9af4e92feec3dfe496077905c45a3c43b38b89a59ce2',
    'worlds/TheLocalPub/BlockDefenders/Prefabs/Vehicles/Wheeled/M998/M997_maxi_ambulance_UK_BlockDefenders.et':
        '75c6bd60f5725209ac7ddf136c97941623ebccf495769dd82273f0dccf671abd',
    'worlds/TheLocalPub/RailroadReactions/Prefabs/Vegetation/Cuttings/PUB_Placeable_Juniperus_Communis.et':
        '24481fd8fda0d0fd768e2836cf274980d8b9cf2e229609d59aee175260038ac4',
    'worlds/TheLocalPub/RailroadReactions/Prefabs/Vegetation/Cuttings/PUB_Placeable_Malus_domestica.et':
        '35eeff5787c2342b5ffe498c62d1f1697f084021951ad5b9e659b229c3757675',
    'worlds/TheLocalPub/RailroadReactions/Prefabs/Vegetation/Cuttings/PUB_Placeable_Pinus_sylvestris_medium_01.et':
        'fa8bd0abe0edfe579948a9a08fc02327333cd074c3227b95adb3331b92291865',
    'worlds/TheLocalPub/RailroadReactions/Prefabs/Vegetation/Cuttings/PUB_Placeable_abies_bush.et':
        '28dfe2f10480b70980b909ac6bd0708190aa87f102dab63d935d8140f5dbe83b',
    'worlds/TheLocalPub/RailroadReactions/Prefabs/Vegetation/Cuttings/PUB_Placeable_pendula_pendula.et':
        'fed6352bd3dc8ef8229af2bc3575852299696e720ae71f7d104eef528f48f185',
    'worlds/HitTheMan/Battle For Rasman/Mine_TM62M_EffectModuleEntity_HTM.et':
        'd4e0941b7f09ffed2432b3f6ef369ea509d3a7b02e557074668fa32faed411ce',
    'worlds/HitTheMan/Battle For Rasman/Warhead_Mine_TM62M_HTM.et':
        '0adf5e900bc9dfe9907a2a575e47f9035d9cc888f3a636b0fb8bb9f8cd7879cc',
    'worlds/James/OrbSturm/EffectModule_Zoned_MortarBarrage_Larger.et':
        'a6625b775c00556a34ccaea78274e53207614fe97718959028ce226270b7e9f3',
    'worlds/FSM/Prefabs/Props/Military/Sandbags/FSM_Sandbag_Wall_Round_Low.et':
        '32c230dad35e78b1da7b8b93351f4c3e7a4962b0918299be45e16c1a5b4d8cbc',
    'worlds/TheLocalPub/LamentinLosers/Prefabs/Weapons/Explosives/Mine_TM62M/Mine_TM62M_LamentinLosers.et':
        '168f1b77cfe825a1a980903dcacc10461131b8f9b56463e228dae8931f25bc58',
    'worlds/TheLocalPub/LamentinLosers/Prefabs/Wheeled/Ural4320/VehParts/Ural4320_ammo_box_no_arsenal.et':
        'f41c3211fd6951a0e06fc8fe4dc9f358c717470c0d507fd687f69a9adb6046d3',
    'worlds/anteante101/Prefabs/group/Group_GC_ION_Uniformed_CAT_WL_AA101.et':
        'ae267b03015662a0badcfa9210ce86ca18d1c3bf2e06efe19fe8b0b009fe4813',
    'worlds/arc/CommandosReforged/Prefabs/BLU/Barret_m107a1_IR.et':
        '38e719f739bb51dc4924dd16f6ddc92a3a476aaf0c91b6c03bffc8ac1a738ab8',
    'worlds/arc/CommandosReforged/Prefabs/BLU/Helmet_MICH2000_norotos_velcro_green2_commandos.et':
        '0956a322e5d26e91df76f5b8e85cb9dc76a456f107512879ba4682870cab5033',
    'worlds/arc/CommandosReforged/Prefabs/BLU/MG_M240_Light_MDO_CDOS.et':
        '583ad6a6e38cb6863bd6e50ac2a9d2947680bea1b20a6cf81f29a9648cc57f4e',
    'worlds/arc/CommandosReforged/Prefabs/BLU/Rifle_M4A1_BLOCK_0_NT4_Commandos.et':
        '835e0cabe44a21797067c46f759f502f8531d880ba75a97a70ff3bd3235dd56e',
    'worlds/arc/CommandosReforged/Prefabs/BLU/Rifle_M4A1_M203_RAS_TA31RCO_PEQ_CDOS.et':
        '48d78a9377e2de9bf5118875e5c0d10aff2534d6a70747af56f56d2ea9a50935',
    'worlds/arc/CommandosReforged/Prefabs/BLU/Rifle_SCAR_H_STD_SemiBlack_CDOS.et':
        '778c3261098ca5fe0f322dae7db533e34acce57e255a070838d05c9c65e0b54d',
    'worlds/arc/CommandosReforged/Prefabs/BLU/Rifle_SCAR_SSR_CDOS.et':
        'e8c0ec56083f1bc8cd9fd7afb4c2392af31d5c9781e7e880bed03d5567774ac3',
    'worlds/arc/CommandosReforged/Prefabs/Mi8MT_armed1_Comandos.et':
        'eda92f4784b14a363777023e2569de33ffc2f78619955514d0cea57594fc5c44',
    'worlds/arc/CommandosReforged/Prefabs/Mi8MT_Flare_Dispenser.et':
        '7277ee6977ccc500797df702bb6083be82708853cbf9181d12048b0e202dee6b',
    'worlds/HitTheMan/Prefabs/Vehicles/BMP1_ZSU_base_HTM.et':
        '25b2f4c66289a46fd029a1c0fae3e072d35b1c858982b8a873221d98791d5a1f',
    'worlds/HitTheMan/Prefabs/Vehicles/BaconZU23_Turret_NotEditable_HTM.et':
        '31ddf51a7efa4150a60239637af698e26899f6ea95af3cb298167f65fcb04324',
    'worlds/RedTheKnown/BritsAtBeauregard/Prefabs/OpforUnits/ScoutTeam/Custom_Submag_ARY_Rifle_AK74N_PBS4_GP25_Polymer_Plum.et':
        'b6a0584b1f7fb0ec6e6fe29745017fb2e794cb2daedcd6fc0856bb05eed62260',
    'worlds/RedTheKnown/BritsAtBeauregard/Prefabs/OpforUnits/ScoutTeam/Custom_Submag_ARY_Rifle_AK74N_PBS4_Polymer_Plum.et':
        '5b2cad0ac983d4fc4ec2f6de3c59282626e771f2674d2b1642e7ab1d6d8ea1d0',
    'worlds/Til/OwlsPrey/data/OwlsPrey_Mi8_1.et':
        '98926fa48bdd73e42a0eeb98fe0b2de32e140c52387ba6c934ab0a1fd9aaaf4f',
    'worlds/Til/OwlsPrey/data/OwlsPrey_Mi8_2.et':
        'f178993b1d9754a987054609c3ab43b3efa95beacfc7fe3912307491f10e209b',
    'worlds/arc/SunnyWAC/Prefabs/Group_CIV.et':
        '43e9ca2c554b85e7b044960f7eb9ccfeb83b809527c02fe839f48825a70412cb',
    'worlds/HitTheMan/Prefabs/Vehicles/M113A2_APC_CDF_HTM.et':
        'afb884dab973abe538fd9fc9d63092e2838da18ed50ddc77f774404eefbf71a8',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Military/Camps/AmmoboxBLUE.et':
        '1c0cd1aeba2d805cc47a56dae9ae30ed352b4131662c71766b0dde6765dd6e1d',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Magazines/Magzine_1shot_blue.et':
        '4c25a48693ffb6bcbcfb4ef89821fd4dc74f260d048f8f94980ea2bd93349d23',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Grenades/M18/Smoke_M18_Violet_US.et':
        '4915c486c26c3c384c5306930c966c5f45d747d9f295142e48098bf99ae7b827',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Grenades/Smoke_ANM8HC_US.et':
        '8f65ccfdb561457e84aef0ceabb73c1297a9460d07c764c8920626c8a8917d04',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Military/Camps/AmmoboxRED.et':
        'b795011ea21ca849c305f872c076ad492440f9f7cddb258941bb170a8a589c79',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Magazines/Magazine_1shot_red.et':
        '56d37eb8484d72629b0f0e0e0a13db1f59afc3dea5c9c88b3b4b20856076a622',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Grenades/Smoke_RDG2_USSR.et':
        'efff8807508bb0a3fb00cae6895f03e7540f250f97aa4b728a7e95fabaa9f989',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Grenades/M18/Smoke_M18_Red_USSR.et':
        '507aae83ddab9061a017e74cd170e793add20bda88e2cadd4de0cc3813017efe',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Characters/HeadGear/Helmet_PASGT_01/Helmet_PASGT_01_cover_BLUE.et':
        '73b2b79fe2318441eea3c2917fe0e345e9a66cfbf98f80b5e745f177d882a325',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Characters/HeadGear/Helmet_SSh68_01/Helmet_SSh68_01_net_RED.et':
        '1e363cb4d578331dc39baa1be64443487b4f08fa6f303d3d64bb87006409a710',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Characters/Uniforms/VKPO/Undershorts_VKPO_RED.et':
        'e6d627735791bd86888f62920f8c46bf729bccaffbf41b1b054fbc8917647545',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Characters/Uniforms/VKPO/Undershorts_VKPO_blue.et':
        'abf5938b6d91f249913ffcd2a90d6eabc3c773c1de4ff58e8ec8b99fd223ed51',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Items/Equipment/Backpacks/MedBag.et':
        '6ab532670799685f49ef4f28a17f30ce0be79269f16e84eb04138ffc88c01931',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Fabric/Flags/Flag_blue1.et':
        '720a2abf5310286fd418bf2cd3f04d45a8020a936ac4a452d38f2cb6617d5c77',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Fabric/Flags/Flag_blue2.et':
        '1b7c61ff1982f6ffbbb74e3d03a2e7f97e914b0f687a65c0a11c2813492a13c2',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Fabric/Flags/Flag_blue3.et':
        '351e932e8f9c804ab7a59de7791fd6a51206fe507e0b61e043bcd068f29e50ed',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Fabric/Flags/Flag_red1.et':
        '29524f0be37dabfd93d241c21a7111ca7ee96b6ead49954c881f2fc59084f5a1',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Fabric/Flags/Flag_red2.et':
        'c678e467ff01a12d6da037a8b03c00a4487f4d1db45e8ab916efb992d77cecc7',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Props/Fabric/Flags/Flag_red3.et':
        '5e7601e00775bf46c4d229ab3757f9114d7717ad78399cf7afa258b0810b82c9',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/AK74/REDBANNER1.et':
        'c843d898d92b39741f822a368956c412f8e9cfbaf1afb0473ae54da654a69e93',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/AK74/REDBANNER2.et':
        'cff0f4494163f6b53d8d76b7fcf63858ad2dddc47bbfd318935ac27086b8c002',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/AK74/REDBANNER3.et':
        '00d3382b339a4fbcb35c180c0c9850c3dd2ac8fc26e70f821a6d86bc80e20372',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/AK74/Rifle_AK74_RED.et':
        '61a775f19f442fd52bc9bde042bc2607b532b0d4bf1dc3174779815353a82522',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/M16/BLUBANNER1.et':
        '94402fd57c42485e7fb846717a942d1b2291734e639af8ac2fb7928c469878ad',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/M16/BLUBANNER2.et':
        '63567168ec2f8e3e3599345b84f096bce823929a708d9861c6157cb920c6afb8',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/M16/BLUBANNER3.et':
        '006c5af93054adcad1a80415eca7cccd4deaabb6bd82c472fe4b14b110ff8d02',
    'worlds/TheLocalPub/BattleOfTheBulges/Prefabs/Weapons/Rifles/M16/Rifle_M16A2_blue,.et':
        '2ba0dad4f29f8ea1bcbb855938794e79393231bd904134041919cb190cd936a6',
}
ROCKY_CIVILIANS = 'worlds/Burn0ut7/Rocky Road/Prefabs/CiV_Group.et'
LOCAL_FILES_ROOT = 'worlds/Wade/TheLocalFiles/'
LOCAL_FILES_DEFAULT = LOCAL_FILES_ROOT + 'TheLocalFiles_Layers/default.layer'
LOCAL_FILES_DEFAULT_BLOB = 'e2d54ef1a3e35835b00210ccf02942519b34053c'
LOCAL_FILES_SPAWNER_LAYERS = {
    f'{LOCAL_FILES_ROOT}TheLocalFiles_Layers/ComputerSpawners/BananaRandom{index}.layer': blob
    for index, blob in enumerate((
        'edc4794d6bb744cb8b5af199da6a78e246a88f6f', 'ec0fb9336a4dcf432557087b094eeb64e4693097',
        '26e6e9e380dd55771185813d9714bff01ccd3feb', '7f61e48e21053f90db6401178dfa8178620c1c3a',
        'd187bce3647c951415f94ecf12b00c9024469626', '7952d34d82aa185afce2df15438b756586da004b',
    ), 1)
}
LOCAL_FILES_COMPUTER = ('F9516BAAF0D2E496', LOCAL_FILES_ROOT + 'Computer_Secure.et')
LOCAL_FILES_MARKER = ('7CEE9BACABE39C3E', LOCAL_FILES_ROOT + 'ComputerMarker.et')
LOCAL_FILES_MARKER_SPAWNER = LOCAL_FILES_ROOT + 'MarkerPrefab.et'
LOCAL_FILES_SEARCH_FLAG = 'ComputerCaptured'
LOCAL_FILES_INTEL = ('F3739484332D8932', LOCAL_FILES_ROOT + 'Intel.et')
LOCAL_FILES_TRACKER_ASSETS = {
    LOCAL_FILES_DEFAULT: LOCAL_FILES_DEFAULT_BLOB,
    LOCAL_FILES_INTEL[1]: 'b308a287d9238b2093ffecd68a81dd8743141efd',
    LOCAL_FILES_ROOT + 'Objective_Marker_Lunchyyy.et': 'bd85de28a39108cd7adb867bbc7f0a97edb34a20',
    'worlds/arc/CommandosReforged/Prefabs/Objective_Marker.et': '15d8920014534efd3ad16a710e1d3985da7337cd',
    'scripts/game/arc/CommandosReforged/GC_CommandosManager.c':
        '6964da122cd0121fb80e9f3a8c7033c996bcf320',
}


def local_files_computer_assets(source, dev, stock, local_resources):
    assets = {}
    for (identity, path), blob in (
        (LOCAL_FILES_COMPUTER, 'cd006b78e62354ecaffbc8c7c5741455ed0c48cc'),
        (LOCAL_FILES_MARKER, 'ab22168e7496f604072cd40510179df0315467ff'),
    ):
        if (source.files.get(path) != blob or source.resources.get(identity) != [path]
                or dev.resources.get(identity) not in (None, [path])):
            raise ValueError('The Local Files computer or map marker identity changed')
        lines = source.read(path).splitlines()
        root_id = lines.pop(1)
        if identity == LOCAL_FILES_COMPUTER[0]:
            body, actions = convert_interaction_block(lines, path, stock, local_resources)
            if [action['flag'] for action in actions] != [LOCAL_FILES_SEARCH_FLAG]:
                raise ValueError('The Local Files computer search action changed')
        else:
            body, _ = convert_layer('\n'.join(lines) + '\n', draw_order=True, source=source)
        head, fields = body.split('\n', 1)
        native = '\n'.join((head, root_id, fields))
        if LEGACY.search(native) or (path in dev.files and native.strip() != dev.read(path).strip()):
            raise ValueError('The Local Files computer or marker has an unconverted field')
        assets[identity] = {'path': path, 'body': native}
    return assets


def local_files_marker_child(source, child, parent_fields):
    """Resolve one independently scheduled marker while retaining its relative transform."""
    template = REF.search(child[0])
    if (not template or template.groups() != ('24DE47146AB85B95', 'worlds/Wade/BananaHoedown/MarkerPrefab.et')
            or source.files.get(LOCAL_FILES_MARKER_SPAWNER) != '64ee2b886829a804b535c3c55b6434234df435a3'):
        raise ValueError('unverified nested marker spawner')
    values = properties(child[1:-1], {'coords', 'angles', 'm_conditionFlag', 'm_spawnTimings'})
    # Group 1 keeps the condition flag inherited from the marker-spawner prefab.
    flag = values.get('m_conditionFlag', '"BananaRandomFlag1"')
    if (f' m_conditionFlag {flag}' not in parent_fields or values.get('m_spawnTimings') != '300'
            or not {'coords', 'angles'} <= values.keys()):
        raise ValueError('nested marker flag or timeline differs from the reviewed schedule')
    return [
        'TILW_PrefabSpawnerEntity : "{7F10372CF1CA1175}Prefabs/Utils/TILW_PrefabSpawner.et" {',
        *(f' {key} {values[key]}' for key in ('coords', 'angles')),
        f' m_prefab "{{{LOCAL_FILES_MARKER[0]}}}{LOCAL_FILES_MARKER[1]}"',
        f' m_conditionFlag {flag}', ' m_spawnTimings {', '  300', ' }', '}',
    ]


def local_files_intel_marker(source, dev, stock):
    """Port the single Intel tracker without importing the Commandos runtime."""
    identity, path = LOCAL_FILES_INTEL
    if (any(source.files.get(name) != blob for name, blob in LOCAL_FILES_TRACKER_ASSETS.items())
            or source.resources.get(identity) != [path] or dev.resources.get(identity) not in (None, [path])):
        raise ValueError('The Local Files Intel or tracker source changed')
    lines = source.read(path).splitlines()
    base = REF.search(lines[0])
    if not stock or not base or not stock.contains(*base.groups()):
        raise ValueError('The Local Files Intel base is unavailable')
    start = next(index for index, line in enumerate(lines) if line.startswith('  GC_CommandosObjRegister '))
    del lines[start:block_end(lines, start) + 1]
    body = '\n'.join(lines) + '\n'
    if path in dev.files and body.strip() != dev.read(path).strip():
        raise ValueError('Dev contains different Intel prefab content')
    intel = [block for _, block in blocks(source.read(LOCAL_FILES_DEFAULT))
             if block[0] == f'GenericEntity Intel : "{{{identity}}}{path}" {{']
    if len(intel) != 1:
        raise ValueError('The Local Files requires exactly one named Intel placement')
    coordinates = next(line for line in intel[0] if line.startswith(' coords '))
    marker = marker_instance([
        coordinates, ' angleY 0', ' m_MarkerColor 0.5 0 1 1', ' m_sQuadName "objective-marker"',
        ' m_fWorldSize 300', ' m_sDescription "HVT"', ' m_bShowForAnyFaction 1', ' m_iZOrder 100',
    ], ' GC_LocalFilesIntelMarker', draw_order=True)
    marker = marker.replace('   m_sDescription "HVT"\n',
                            '   m_sDescription "HVT"\n   m_sTrackedEntity "Intel"\n   m_iTrackingIntervalMs 600000\n')
    return {identity: {'path': path, 'body': body, 'trackingMarker': marker + '\n'}}


TRIFECTA_EXTRACTS = {
    '24B24161E870CE7B': ('ION_HARBOUR_EXTRACT', '570d6c851266dae8b245a9f36a67056fe59eff2e'),
    '9C8ABD5BDB78B4E8': ('ION_BEACH_EXTRACT', '92b5f87fe479d35e11b5633ec118ddc2da415261'),
    'E0CC1F1AECDAEDB3': ('AFRF_KERMOVAN_EX', 'c168547a028fae66bf7cb5e0b6d6f4ae9899b9da'),
    'DFC65F8E60D54E83': ('AFRF_BOAT_EX', 'a06854088f6690f4d69c52d232c9996ce46f293d'),
    'E214CD324D84F76E': ('FIA_MIL_EX', '3d0f0a48cf3b144043cfc1316ed8800090576e58'),
}
TRIFECTA_PLACED_EXTRACTS = {
    'MIL': ('FIA_MIL_EX', '078244022733ebf34c5b4ff81bf6a4fb86bdb56d'),
    'BEACH': ('ION_LESCREUX_BEACH_EX', 'db7046a9c61ae15ccc3b74949afb446b3639ebfb'),
    'HARBOUR': ('ION_MEAUX_HARBOUR_EX', 'f1a6e95ba4a2bafd600b5270c58c10e5d4ad03b2'),
    'BOAT': ('PATRIOT_BOAT_EX', 'ab90a3b13a2bd74b831fe35361802df5b70a570c'),
    'KERMOVAN': ('PATRIOT_KERMOVAN_EX', 'b7efab6f881a5d6f93390ceec638a75b7a3bee6a'),
}


def trifecta_assets(source, dev):
    if (source.files.get(TRIFECTA_FRAMEWORK[0]) != TRIFECTA_FRAMEWORK[1]
            or source.files.get('scripts/game/arc/CommandosReforged/GC_CommandosManager.c')
            != '6964da122cd0121fb80e9f3a8c7033c996bcf320'):
        raise ValueError('Trifecta tracker or framework changed')
    assets = {}

    def checked_source(identity, path, blob):
        if (source.files.get(path) != blob or source.resources.get(identity) != [path]
                or dev.resources.get(identity) not in (None, [path])):
            raise ValueError('Trifecta asset or registration changed: ' + path)
        return source.read(path)

    def remember(identity, path, body, **details):
        if LEGACY.search(body) or (path in dev.files and body.strip() != dev.read(path).strip()):
            raise ValueError('Trifecta has unconverted or differing asset: ' + path)
        assets[identity] = {'path': path, 'body': body, **details}

    identity, path = TRIFECTA_STM
    lines = checked_source(identity, path, '43370cc6e12eec78a29d018e6eb1633ef17f5929').splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('  GC_CommandosObjRegister '))
    del lines[start:block_end(lines, start) + 1]
    stm, names = convert_mission_names('\n'.join(lines) + '\n')
    if names != 1:
        raise ValueError('Trifecta STM needs one runtime name')
    remember(identity, path, stm, trackerName='STMMARK')

    identity, path = TRIFECTA_MARKER
    text = checked_source(identity, path, 'd8775e8bf2be383bcf9d0ce785aa5ca22a0ff23b')
    marker = seeder_named_marker(text.replace(' m_MarkerColor 0.251 0 0.502 1\n',
                                               ' m_MarkerColor 0.5 0 1 1\n m_sDescription "STM"\n'))
    marker = marker.replace('   m_sDescription "STM"\n',
                            '   m_sDescription "STM"\n   m_sTrackedEntity "STM"\n   m_iTrackingIntervalMs 180000\n')
    remember(identity, path, marker)

    for identity, (name, blob) in TRIFECTA_EXTRACTS.items():
        path = TRIFECTA_ROOT + f'Prefabs/{name}.et'
        text = checked_source(identity, path, blob)
        native, names = convert_mission_names(text)
        block = native.splitlines()
        fields, children = trigger_children(block[1:-1])
        if names != 1 or len(children) != 1:
            raise ValueError('Trifecta extraction needs one named polygon and one trigger')
        _, child = children[0]
        trigger = TRIGGER.fullmatch(child[0])
        if not trigger or trigger['kind'] != 'Extraction' or any('m_flagName ' in line for line in child):
            raise ValueError('Trifecta extraction requires a notice-only trigger')
        trigger_fields = [line for line in child[1:-1] if not re.fullmatch(r' ID "[\dA-F]{16}"', line)]
        check, _, details = extraction_rule(path, name, '""', trigger_fields, polygon=True)
        if not details.get('statusMessages'):
            raise ValueError('Trifecta extraction is missing its status notices')
        observer = render_area_flag_observer(path, name, '""', check, False, status_only=True)
        observer[0] = observer[0].replace('GC_AreaFlagObserver ', 'm_Observer GC_AreaFlagObserver ', 1)
        component = [f'  GC_BoundAreaFlagComponent "{{{guid(path + "/notices")}}}" {{',
                     *(line[1:] for line in observer), '  }']
        stop = block_end(fields, fields.index(' components {'))
        fields[stop:stop] = component
        remember(identity, path, '\n'.join([block[0], *fields, block[-1], '']))
    return assets
BULGES = 'worlds/TheLocalPub/BattleOfTheBulges'
LOST_AND_FOUND_DELTA_GROUP = ('worlds/FloridaMan/Prefabs/Groups/BLUFOR/US/Army_2010_DeltaForce/'
                              'Group_Delta_2010_Assault_m_P.et')
LOST_AND_FOUND_PILOT_GROUP = 'worlds/FloridaMan/Prefabs/Groups/BLUFOR/US/US_Pilot_Group.et'
LOST_AND_FOUND_PILOT_ID = 'C1E798172DA45F29'
REVIEWED_SPAWNED_MARKERS = {
    'worlds/Perfk/Prefabs/Markers/EditableMarker_AmmoCache_VIS_GC_MUJ.et':
        '8246d5e01642372cc3e1e71714c846059d5ee937e6abf54f0d75f33e8380131e',
    'worlds/FloridaMan/Prefabs/Setpeice/opfor_notice_marker.et':
        '219df188327c2f3a4b1ff82b6efa0e81e3775f4f68a44877d51a902e5e86c4c5',
    'worlds/HitTheMan/Stealthing Into Villa/Prefab/IglaMarkerV4.et':
        'e72a1e4b722ee7d53311090cbeb05283315b39edf096f6e3b51eafa17c0d82e9',
}
BULGES_RED_MATERIAL = (
    '196D8860670DE1B9',
    'Assets/Props/Fabric/Flags/Data/Flagcolourred1.emat',
    f'{BULGES}/Assets/Props/Fabric/Flags/Data/Flagcolourred1.emat',
    'Assets/Props/Fabric/Flags/Data/Flagred1.emat',
)
# The indices are recorded from the reviewed prefab or its effective placements.
REVIEWED_GROUP_CALLSIGNS = {
    f'{BULGES}/Prefabs/Groups/BLUFOR/Group_BLUE_RIFLE_P.et': (
        '166c62b096beca9d5cc75efc4ae122f3742b92ae',
        f'{BULGES}/BattleOfTheBulges_Layers/BLUFOR_PLAYERS.layer',
        '53120d90d0b513254d90068d7acb85849da733f5', (0, 0, 3), False),
    f'{BULGES}/Prefabs/Groups/OPFOR/Group_RED_RIFLES_P.et': (
        'ca3a2abb657a82d2bb34058659e7c4991ab8aac6',
        f'{BULGES}/BattleOfTheBulges_Layers/OPFOR_PLAYERS.layer',
        'b5002cd92179dbd5983a04681ca172b8a5f7179e', (0, 0, 1), False),
    LOST_AND_FOUND_DELTA_GROUP: (
        '12bd5100b584b1d07a21445336bcbdb67537dee0',
        'worlds/FloridaMan/worlds/LostAndFound/LostAndFound_Layers/BLUFOR.layer',
        '9063e2ec7ab2a43b8dc4c89d63a918aa5ad9782d', (1, 1, 1), True),
    LOST_AND_FOUND_PILOT_GROUP: (
        '946a8720187a87393c596e12d0ae53c024565039',
        'worlds/FloridaMan/worlds/LostAndFound/LostAndFound_Layers/BLUFOR.layer',
        '9063e2ec7ab2a43b8dc4c89d63a918aa5ad9782d', (4, 5, 2), False),
}
REVIEWED_PLAYABLE_GROUPS = {
    ROCKY_CIVILIANS: '6ab575e5895921c869d63aa3c896bd59f52a4b809317a1c80f3717514a4de117',
    'worlds/FSM/Prefabs/Groups/OPFOR/Seperatists/P/FSM_Playable_Seperatists_HQ.et':
        'ebef299d474f67109ec80c182beb374979d253f886600006d3ff1d0e7c56f358',
    'worlds/FSM/Prefabs/Groups/OPFOR/Seperatists/P/FSM_Playable_Seperatists_RifleSquad.et':
        'f38d729add3e1a2d89d005dcde68e189e52d7b73fe747ff6bcb1d0af8c33f9a2',
}


def blocks(text):
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        if lines[index][0].isspace() or not lines[index].endswith(' {'):
            raise ValueError('unsupported layer syntax at line ' + str(index + 1))
        end = block_end(lines, index)
        yield index + 1, lines[index:end + 1]
        index = end + 1


STARTUP_CLEANUP = re.compile(r'(?P<group>\$grp )?PK_EntityDeleter(?: (?P<name>[\w.-]+))? \{')
STARTUP_PROTECTION = re.compile(rf' +PK_EntityProtectorComponent {SERIALIZED_ID} \{{')
GARBAGE_PROTECTION = re.compile(rf' +TILW_NoGarbageComponent {SERIALIZED_ID} \{{')
STARTUP_FIELDS = {
    'm_fRadius': 'm_fRadius',
    'm_excludePrefabsInList': 'm_bExcludePrefabs',
    'm_bVObjectsOnly': 'm_bVisualObjectsOnly',
    'm_bByParentOnly': 'm_bRootsOnly',
    'm_bPreviewShape': 'm_bPreviewShape',
}


def authored_placement_flags(values):
    flags = values.get('Flags')
    if flags is None:
        return []
    if flags != '0x200000 0':
        raise ValueError('unsupported entity placement flags')
    return ['Flags ' + flags]


def startup_cleanup(block, *, preserve_children=False):
    root = STARTUP_CLEANUP.fullmatch(block[0])
    if not root:
        raise ValueError('unsupported startup cleanup placement')
    output = []
    for name, lines in polygon_capture.members(block, root['group'], root['name']):
        children = []
        if preserve_children and ' {' in lines:
            first = lines.index(' {')
            if block_end(lines, first) != len(lines) - 1 or any(LEGACY.search(line) for line in lines[first:]):
                raise ValueError('unsupported startup cleanup hierarchy')
            children, lines = lines[first:], lines[:first]
        values = properties(lines, STARTUP_FIELDS.keys() | CAPTURE_TRANSFORM | {'m_prefabFilterList', 'Flags'})
        transform = {key: value for key, value in values.items() if key in CAPTURE_TRANSFORM}
        if ('coords' not in transform or any(not FIELD.fullmatch(f'{key} {v}') for key, v in transform.items())
                or ('angles' in transform and any(axis in transform for axis in ('angleX', 'angleY', 'angleZ')))):
            raise ValueError('unsupported cleanup transform')
        radius = values.get('m_fRadius', '20')
        if not re.fullmatch(NUMBER, radius) or not 0 <= Decimal(radius) <= 1000:
            raise ValueError('startup cleanup radius is out of range')
        if any(values.get(field, '0') not in ('0', '1') for field in STARTUP_FIELDS if field != 'm_fRadius'):
            raise ValueError('unsupported startup cleanup option')
        filters = values.get('m_prefabFilterList', '')
        entries = [ref[0] for ref in REF.finditer(filters)]
        if (REF.sub('', filters).replace('""', '').strip()
                or any(not REF.fullmatch(entry)[2].endswith('.et') for entry in entries)):
            raise ValueError('unsupported startup cleanup prefab filter')
        entity = 'GC_StartupCleanupEntity' + (f' {name}' if name else '')
        output.extend([f'{entity} {{', *(' ' + field for field in authored_placement_flags(values)),
                       *(f' {key} {v}' for key, v in transform.items()),
                       *(f' {native} {values[key]}' for key, native in STARTUP_FIELDS.items() if key in values)])
        if entries:
            output.extend([' m_aPrefabs {', *(f'  {entry}' for entry in entries), ' }'])
        output.extend(children)
        output.append('}')
    if not output:
        raise ValueError('empty startup cleanup group')
    return '\n'.join([*output, ''])


COMMANDOS_OBJECTIVES_BLOB = 'c173a7dc7e159bccc8c9764500392838742b7316'
COMMANDOS_BLACK_HAWK = 'worlds/arc/CommandosReforged/Prefabs/Blackhawk_OBJ.et'
COMMANDOS_BLACK_HAWK_PARENT = ('1529FD2C6A8162A1', 'Prefabs/Vehicles/Helicopters/AUS_MH60/AUS_UH60_Pylon_Quad_Hydra.et')
COMMANDOS_BLACK_HAWK_TANKS = ('5622A70CD4036C5A', '661A5AD75FDB52FF')
COMMANDOS_OBJECTIVE_MARKER = 'worlds/arc/CommandosReforged/Prefabs/Objective_Marker.et'
COMMANDOS_DEFAULT_MARKER = 'worlds/arc/CommandosReforged/Prefabs/Marker_Any.et'
COMMANDOS_INTERACTION = 'worlds/arc/CommandosReforged/Prefabs/Objective_Interact.et'
COMMANDOS_INTERACTION_BLOB = '3da7cdb4a2c03e31897fffb8b254c2b497a09bc1'
COMMANDOS_INTERACTION_COMPONENT = guid('Commandos/Objective_Interact/component')


def commandos_interactions(block):
    lines = block.copy()
    starts = [index for index, line in enumerate(lines) if line.lstrip().startswith('GC_CommandosInteraction ')]
    for start in reversed(starts):
        end = block_end(lines, start)
        indent = len(lines[start]) - len(lines[start].lstrip())
        if indent < 4:
            raise ValueError('Commandos interaction needs an entity action manager')
        components_header = ' ' * (indent - 3) + 'components {'
        components = next((index for index in range(start - 1, -1, -1) if lines[index] == components_header), -1)
        if components < 0 or block_end(lines, components) < end:
            raise ValueError('Commandos interaction is outside entity components')
        component_indent = ' ' * (indent - 2)
        component_head = component_indent + f'GC_MissionInteractionComponent "{{{COMMANDOS_INTERACTION_COMPONENT}}}" {{'
        if component_head in lines[components + 1:block_end(lines, components)]:
            raise ValueError('multiple Commandos objective actions on one entity')
        fields = lines[start + 1:end]
        name, = [line.strip().removeprefix('m_objectiveName ') for line in fields
                 if line.strip().startswith('m_objectiveName ')]
        if not re.fullmatch(QUOTED, name) or any(line.strip().startswith('m_defectorObjective ') for line in fields):
            raise ValueError('Commandos interaction needs a public named objective')
        required, completed = commandos_objective_flags(name[1:-1])
        titles = [line.strip().removeprefix('Name ') for line in fields if line.strip().startswith('Name ')]
        if len(titles) > 1:
            raise ValueError('Commandos interaction has ambiguous action text')
        fields = [line for line in fields if not line.strip().startswith('m_objectiveName ')]
        native_fields = ' ' * (indent + 1)
        if not any(line.strip() == 'm_factionKeys {' for line in fields):
            fields.extend([native_fields + 'm_factionKeys {', native_fields + ' "RHS_USAF"', native_fields + '}'])
        fields.extend(native_fields + field for field in (
            'm_actionName ' + (titles[0] if titles else '"Sabotage"'),
            f'm_conditionFlag "{required}"', f'm_flagName "{completed}"', 'm_singleUse 0'))
        legacy = [lines[start].replace('GC_CommandosInteraction', 'TILW_BaseInteraction', 1), *fields, lines[end]]
        action, component, _ = convert_interaction_action(legacy, name[1:-1], '')
        lines[start:end + 1] = action
        lines[components + 1:components + 1] = [
            component_head, *(component_indent + field for field in component), component_indent + '}',
        ]
    return lines


def commandos_objective_cleanup(block):
    if block[0] == 'PK_EntityDeleter Radar_OBJ {':
        # The legacy cleanup removes this child after its objective registers.
        return startup_cleanup(block, preserve_children=True)
    nested = list(nested_placement_blocks(block, STARTUP_CLEANUP))
    if len(nested) != 1:
        raise ValueError('expected the reviewed ammo dump cleanup placement')
    start, stop, depth, cleanup = nested[0]
    replacement = [' ' * depth + line for line in startup_cleanup(cleanup).splitlines()]
    converted = native_placement([*block[:start], *replacement, *block[stop + 1:]])
    if not converted:
        raise ValueError('unconverted commandos objective placement')
    return converted


def static_particle_placement(block):
    """Preserve effect-only native placements and leave their resources to the file audit."""
    head = re.fullmatch(r'(?P<group>\$grp )?ParticleEffectEntity(?: (?P<name>[\w.-]+))?'
                        r'(?P<prefab> : "\{[\dA-Fa-f]{16}\}[^"\r\n]+\.et")? \{', block[0])
    if not head:
        return False
    members = polygon_capture.members(block, head['group'], head['name'])
    for _, fields in members:
        effect = [line.strip() for line in fields if not FIELD.fullmatch(line.strip())]
        if not effect and head['prefab']:
            continue
        if len(effect) != 1 or not re.fullmatch(r'EffectPath "\{[\dA-Fa-f]{16}\}[^"\r\n]+\.ptc"', effect[0]):
            return False
    return bool(members)


def static_spline_placement(block):
    """Keep native world-editor geometry with its baked placements and point data."""
    if not re.fullmatch(r'(?:\$grp )?SplineShapeEntity(?: [\w.-]+)? \{', block[0]):
        return False
    # Only accept the native generator serialization. Custom components and
    # mixed map overlays need a conversion rule of their own.
    return (not any(LEGACY.search(line) for line in block)
            and any(line.strip() == 'Points {' for line in block)
            and all(FIELD.fullmatch(line.strip()) or SPLINE_RECORD.fullmatch(line.strip())
                    for line in block[1:-1]))


TAKISTAN_DIRT_ROAD = ('9495F43BDE130455', 'Prefabs/WEGenerators/Roads/Dirt/RG_Road_Dirt_Takistan.et')
ROAD_GENERATOR = re.compile(r'(?P<group>\$grp )?RoadGeneratorEntity(?: (?P<name>[\w.-]+))? : ' + REF.pattern + r' \{')
ROAD_ID = re.compile(r'   ID "[\da-fA-F]{16}"')


def static_road_placement(block):
    """Keep the Takistan generator and its authored instance overrides."""
    head = ROAD_GENERATOR.fullmatch(block[0])
    if not head or REF.search(block[0]).groups() != TAKISTAN_DIRT_ROAD:
        return False
    members = polygon_capture.members(block, head['group'], head['name'])
    for _, fields in members:
        if ' {' in fields:
            start = fields.index(' {')
            stop = block_end(fields, start)
            if fields[stop - 1:stop + 1] != ['  }', ' }'] or fields[start + 1] != '  RoadEntity {':
                return False
            child = fields[start + 2:stop - 1]
            if not child or not ROAD_ID.fullmatch(child[0]) or not all(
                    FIELD.fullmatch(line.strip()) for line in child[1:]):
                return False
            fields = fields[:start] + fields[stop + 1:]
        if not all(FIELD.fullmatch(line[1:]) for line in fields):
            return False
    return bool(members)


SCENERY_MAP_PARENT = re.compile(
    r'(?:\$grp )?(?P<kind>SplineShapeEntity|(?:StaticModel|PrefabGenerator|RoadGenerator|WallGenerator)Entity)'
    r'(?: [\w.-]+)?(?: : ' + REF.pattern + r')? \{'
)
SPLINE_MAP_COMPONENT = re.compile(r'  TILW_MapShapeComponent "\{([\da-fA-F]{16})\}" \{')


def scenery_map_placement(block, dev):
    """Replace opt-in map content without rebuilding native scenery or generator children."""
    text = '\n'.join(block) + '\n'
    if not LEGACY.search(text):
        return text
    head = block[0]
    if head.startswith(('PS_ManualMarker ', '$grp PS_ManualMarker ')):
        return convert_layer(text, dev, draw_order=True)[0]
    if STANDALONE_SHAPE.fullmatch(head):
        converted, audit = convert_boundaries(text, extended_shapes=True)
        if not converted or any('gap' in item for item in audit.values()):
            raise ValueError(next((item['gap'] for item in audit.values() if 'gap' in item), 'no scenery map shape'))
        return converted
    parent = SCENERY_MAP_PARENT.fullmatch(head)
    if not parent:
        raise ValueError('unsupported map attachment on ' + head.removeprefix('$grp ').split()[0])

    def convert_member(fields):
        result = []
        index = 0
        while index < len(fields):
            line = fields[index]
            if line == ' {':
                end = block_end(fields, index)
                children = '\n'.join(child[2:] for child in fields[index + 1:end])
                result.append(line)
                for _, child in blocks(children):
                    result.extend('  ' + row for row in scenery_map_placement(child, dev).splitlines())
                result.append(fields[end])
                index = end + 1
            elif line == ' components {' and parent['kind'] == 'SplineShapeEntity':
                end = block_end(fields, index)
                components = fields[index + 1:end]
                if any(LEGACY.search(row) for row in components):
                    match = SPLINE_MAP_COMPONENT.fullmatch(components[0])
                    if not match or block_end(components, 0) != len(components) - 1:
                        raise ValueError('spline has custom map components')
                    result.extend(passive_shape_component(components, match[1], spline=True))
                else:
                    result.extend(fields[index:end + 1])
                index = end + 1
            else:
                result.append(line)
                index += 1
        return result

    if head.startswith('$grp '):
        converted = [head]
        for name, fields in polygon_capture.members(block, True, None):
            converted.append(f' {name} {{' if name else ' {')
            converted.extend(' ' + line for line in convert_member(fields))
            converted.append(' }')
        if len(converted) == 1:
            raise ValueError('empty scenery group')
    else:
        converted = [head, *convert_member(block[1:-1])]
    output = '\n'.join([*converted, '}', ''])
    if LEGACY.search(output):
        raise ValueError('scenery has legacy content outside a supported map attachment')
    return output


CORE_INITIAL_DAMAGE = re.compile(
    r'components \{\nPK_EntityModifierComponent "\{[\da-fA-F]{16}\}" \{\n'
    r'm_bDamageManagerEnabled 1\nm_aHitzonesData \{\n'
    r'PK_HitzoneData "\{[\da-fA-F]{16}\}" \{\nm_fHealthPercentage 0\n\}\n\}\n\}\n\}'
)
LEGACY_SENTRY_TREE = 'OverrideAIBehaviorData "{1E81FF0F3EC49323}AI/BehaviorTrees/Chimera/Soldier/TILW_StaticAI.bt"'
UK_AMMO_BOX = '{E0E74C1EF4CD0C60}Prefabs/Props/Military/Arsenal/AmmoBoxes/US/AmmoBoxArsenal_Ammunition_HQ_UK.et'
STOCK_US_AMMO_BOX = '{5FFD91CF507BFFFB}Prefabs/Props/Military/Arsenal/AmmoBoxes/US/AmmoBoxArsenal_Ammunition_US.et'
# These six rows are inherited by the authored placements from British Forces 1.0.67.
UK_AMMO_BOX_ITEMS = {
    '6490E7E5E9391AF4': ('RIFLE', '68CFC7B6DE0CC75F', 'Magazine_762x51_FAL_Commonwealth_20rnd_Ball.et'),
    '6490E7E5E9391AED': ('', 'E882742A33858D5A', 'Magazine_556x45_STANAG_30rnd_RadwayGreen_Ball.et'),
    '6490E7E5E9391AE0': ('PISTOL', '8B853CDD11BA916E', 'Magazine_9x18_PM_8rnd_Ball.et'),
    '6490E7E5E9391AE2': ('', 'A827B610B7CD4158', 'Vz58/Magazine_762x39_Vz58_30rnd_Last_5Tracer.et'),
    '6490E7E5E9391AE4': ('', '0A84AA5A3884176F', 'Magazine_545x39_AK_30rnd_Last_5Tracer.et'),
    '649CE5C7B75FD778': ('RIFLE', '91EA3DF1BA0AB1EF', 'Magazine_9x19_L2A3_34rnd_Ball.et'),
}
PLACED_CHARACTER = re.compile(
    r'(?P<group>\$grp )?SCR_ChimeraCharacter(?: (?P<name>[\w.-]+))? : ' + REF.pattern + r' \{')
SENTRY_CONTROL = re.compile(
    r'^(?P<indent> +)(?P<header>ChimeraAIControlComponent "\{520EA1D2DB109F34\}" \{)\n'
    r'(?P=indent) ' + re.escape(LEGACY_SENTRY_TREE) + r'\n(?P=indent) EnableAI 1\n(?P=indent)\}$', re.M)


def placed_core_character(block, resolve_character):
    """Keep a verified Core character and its original default-zone startup modifier."""
    head = PLACED_CHARACTER.fullmatch(block[0])
    if not head:
        return None
    members = polygon_capture.members(block, head['group'], head['name'])
    for _, fields in members:
        transforms = [field for field in fields if FIELD.fullmatch(field[1:])]
        if (not any(field.startswith(' coords ') for field in transforms)
                or not CORE_INITIAL_DAMAGE.fullmatch(
                    '\n'.join(field.strip() for field in fields if field not in transforms))):
            return None
    if not members:
        return None
    prefab = REF.search(block[0])
    reference = resolve_character(prefab[1].upper(), prefab[2], allow_legacy_playable=False)
    header = block[0].replace(prefab[0], reference, 1)
    return native_placement([header, *block[1:]], reviewed_character=True)


def placed_static_sentries(block, resolve_character):
    head = PLACED_CHARACTER.fullmatch(block[0])
    if not head:
        raise ValueError('unsupported sentry placement')
    members = polygon_capture.members(block, head['group'], head['name'])
    expected = [' components {', '  ChimeraAIControlComponent "{520EA1D2DB109F34}" {',
                '   ' + LEGACY_SENTRY_TREE, '   EnableAI 1', '  }', ' }']
    if not members:
        raise ValueError('sentry placement has no members')
    for _, fields in members:
        if (not any(field.startswith(' coords ') for field in fields)
                or [field for field in fields if not FIELD.fullmatch(field[1:])] != expected):
            raise ValueError('unsupported authored sentry fields')
    prefab = REF.search(block[0])
    reference = resolve_character(prefab[1].upper(), prefab[2], allow_legacy_playable=False)
    component = guid('migration/static sentry')
    replacement = (r'\g<indent>\g<header>\n\g<indent> EnableAI 1\n\g<indent>}\n'
                   + rf'\g<indent>GC_StaticSentryComponent "{{{component}}}" {{\n\g<indent>}}')
    fields, count = SENTRY_CONTROL.subn(replacement, '\n'.join(block[1:]))
    header = block[0].replace(prefab[0], reference, 1)
    result = native_placement([header, *fields.splitlines()], reviewed_character=True)
    if count != len(members) or result is None:
        raise ValueError('sentry placement has another legacy declaration')
    return result


def restore_uk_ammo_boxes(block, path, source_blob):
    """Materialize the inherited stock of Another Hill's authored boxes on their original stock parent."""
    if (path != 'worlds/Jub/Another Hill/Another Hill_Layers/defences.layer'
            or source_blob != 'e6725abe700b8cf61ef7c86a46744cab941ca56f'):
        raise ValueError('UK ammunition box authoring needs review')
    authored_boxes = polygon_capture.members(block, True, None)
    if len(authored_boxes) != 4:
        raise ValueError('reviewed UK ammunition box count changed')
    result = [block[0].replace(UK_AMMO_BOX, STOCK_US_AMMO_BOX, 1)]
    for name, fields in authored_boxes:
        for index, line in enumerate(fields):
            if line == '  SCR_ArsenalComponent "{589F01C9C8D4A475}" {':
                break
        else:
            raise ValueError('UK ammunition box is missing the authored arsenal component')
        arsenal_end = block_end(fields, index)
        arsenal = '\n'.join(fields[index:arsenal_end])
        inherited_items = []
        for entry, (kind, identity, prefab) in UK_AMMO_BOX_ITEMS.items():
            header = f'     SCR_ArsenalItemStandalone "{{{entry}}}" {{'
            stub = header + '\n     }\n'
            if header in arsenal and arsenal.count(stub) != 1:
                raise ValueError('reviewed UK ammunition list changed')
            arsenal = arsenal.replace(stub, '', 1)
            item = [header]
            if kind:
                item.append('      m_eItemType ' + kind)
            item.extend(['      m_eItemMode AMMUNITION', '      m_iSupplyCost 0',
                         f'      m_ItemResourceName "{{{identity}}}Prefabs/Weapons/Magazines/{prefab}"', '     }'])
            inherited_items.extend(item)
        items_header = '    m_aArsenalItems {\n'
        if arsenal.count(items_header) != 1:
            raise ValueError('reviewed UK ammunition list format changed')
        # Native inheritance retains all six rows even where a placement lists only its additions.
        arsenal = arsenal.replace(items_header, items_header + '\n'.join(inherited_items) + '\n', 1)
        inherited = ['   m_bCheckFactionForOverwriteArsenalConfig 1', '   m_eArsenalSaveType SAVING_DISABLED', '  }',
                     '  SCR_ServicePointComponent "{5C7489C2D2BCCFA0}" {', '   m_sDelegatePrefab ""', '  }']
        if not any(field.startswith('  SCR_FactionAffiliationComponent ') for field in fields):
            inherited.extend(['  SCR_FactionAffiliationComponent "{5A50E7650A1AE9EC}" {',
                              '   "faction affiliation" "UK"', '  }'])
        rendered = [*fields[:index], *arsenal.splitlines(), *inherited, *fields[arsenal_end + 1:]]
        result.extend([f' {name} {{' if name else ' {', *(' ' + field for field in rendered), ' }'])
    return '\n'.join([*result, '}', ''])


def restore_rhib_mounts(block, aliases, source_blob):
    ref = REF.search(block[0])
    if not ref:
        return block
    original = ref[1].upper(), ref[2]
    if original not in RHIB_GUNBOATS:
        return block
    if original not in aliases:
        raise ValueError('RHIB mounts need the reviewed Workshop archive')
    if source_blob in RHIB_CURRENT_MOUNT_LAYERS:
        return block
    if source_blob not in RHIB_LEGACY_MOUNT_LAYERS:
        raise ValueError('RHIB authoring revision needs loadout review')

    _, mount, suffix = RHIB_GUNBOATS[original]
    components = [
        '  SlotManagerComponent "{55BCE45E438E4CFF}" {',
        '   Slots {',
        '    RegisteringComponentSlotInfo Turret {',
        f'     Prefab "{{{mount}}}{RHIB_PATH}RHIB_gun_mount_{suffix}.et"',
        '    }',
        '    RegisteringComponentSlotInfo Turret_Rear {',
        f'     Prefab "{{FB8ABBE5AF24ED1E}}{RHIB_PATH}RHIB_gun_mount_Mk19_rear.et"',
        '    }',
        '   }',
        '  }',
    ]
    if suffix == 'M2HB':
        components.extend([
            '  SCR_EditableVehicleComponent "{50DEB7C26B5EB312}" {',
            '   m_UIInfo SCR_EditableEntityUIInfo "{5298E609432D192D}" {',
            '    Name "#SPC-Vehicle_RHIB_Name - #SPC-Weapon_M2HB_Name #SPC-Weapon_MK19_Name"',
            '   }',
            '  }',
        ])
    def apply(fields):
        if any(line.lstrip().startswith(('SlotManagerComponent ', 'SCR_EditableVehicleComponent ')) for line in fields):
            raise ValueError('RHIB has authored mount or vehicle presentation overrides')
        if ' components {' not in fields:
            return [' components {', *components, ' }', *fields]
        index = fields.index(' components {') + 1
        return [*fields[:index], *components, *fields[index:]]

    if not block[0].startswith('$grp '):
        return [block[0], *apply(block[1:-1]), block[-1]]
    rebuilt = [block[0]]
    for name, fields in polygon_capture.members(block, True, None):
        rebuilt.append(f' {name} {{' if name else ' {')
        rebuilt.extend(' ' + field for field in apply(fields))
        rebuilt.append(' }')
    return [*rebuilt, block[-1]]


def update_cobra_armament(block, aliases):
    ref = REF.search(block[0])
    if not ref or (ref[1].upper(), ref[2]) != MARINE_COBRA or MARINE_COBRA not in aliases:
        return block
    # The WCS parent retains these node IDs and the isEnabled field under renamed types.
    renamed = {
        ('WCS_ArmamentWeaponGroup', '6242ECBBA8787739'): 'WCS_Armament_WeaponGroup',
        ('WCS_ArmamentWeaponInfo', '6242ECBBA878773F'): 'WCS_Armament_WeaponInfo',
        ('WCS_ArmamentWeaponInfo', '6242ECBBA8787730'): 'WCS_Armament_WeaponInfo',
    }
    output = []
    for line in block:
        match = re.fullmatch(r' +(WCS_ArmamentWeapon(?:Group|Info)) "\{([\dA-F]{16})\}" \{', line)
        if match:
            kind = renamed.get(match.groups())
            if not kind:
                raise ValueError('Marine Cobra armament override needs review')
            line = line.replace(match[1], kind, 1)
        output.append(line)
    return output


def wirecutter_inventory_fields():
    return [
        ' components {',
        '  InventoryItemComponent "{5222EB4D0C73006B}" {',
        '   Attributes SCR_ItemAttributeCollection "{5222EB4D0A2B466B}" {',
        '    ItemPhysAttributes ItemPhysicalAttributes "{62F35B4168194640}" : PhysicalAttributes {',
        '     Weight 0.01',
        '    }',
        '    m_SlotType SLOT_LOADOUT_STORAGE',
        '   }',
        '  }',
        ' }',
    ]


def restore_placed_wirecutters(block, aliases, source_blob):
    if source_blob not in WIRECUTTER_LAYER_BLOBS or WIRECUTTER_PREFAB not in aliases:
        raise ValueError('wirecutter placement needs the reviewed source and Workshop archive')
    guid, path = WIRECUTTER_PREFAB
    rebuilt = [f'$grp GenericEntity : "{{{guid}}}{path}" {{']
    members = polygon_capture.members(block, True, None)
    for name, fields in members:
        if not fields or not all(FIELD.fullmatch(field.strip()) for field in fields):
            raise ValueError('wirecutter placement has unsupported overrides')
        rebuilt.extend([
            f' {name} {{' if name else ' {',
            *(' ' + field for field in wirecutter_inventory_fields()),
            *(' ' + field for field in fields),
            ' }',
        ])
    return '\n'.join([*rebuilt, '}', ''])


def staging_vehicle_placement(block):
    root = PLACED_ENTITY.fullmatch(block[0])
    if not root or root['kind'] not in ('Vehicle', 'Tank', 'RHS_Tank'):
        raise ValueError('staging vehicle exemption needs a placed vehicle')
    marker = f'  GC_StagingVehicleExemptionComponent "{{{guid("staging vehicle exemption")}}}" {{'
    rendered = [block[0]]
    for name, fields in polygon_capture.members(block, root['group'], root['name']):
        flags = [(index, field) for index, field in enumerate(fields) if 'm_bEnableMoveOnFreeze' in field]
        if flags:
            if len(flags) != 1 or flags[0][1] not in (' m_bEnableMoveOnFreeze 0', ' m_bEnableMoveOnFreeze 1'):
                raise ValueError('staging vehicle exemption requires a single instance flag')
            index, field = flags[0]
            fields = fields[:index] + fields[index + 1:]
            if field.endswith(' 1'):
                if ' components {' in fields:
                    start = fields.index(' components {')
                    fields[start + 1:start + 1] = [marker, '  }']
                else:
                    fields[:0] = [' components {', marker, '  }', ' }']
        if root['group']:
            rendered.extend([f' {name} {{' if name else ' {', *(' ' + line for line in fields), ' }'])
        else:
            rendered.extend(fields)
    return [*rendered, block[-1]]


REVIEWED_VEHICLE_PARTS = {
    '72CDA8BE510C54C3': ('VehiclePartPanel', 'Prefabs/Vehicles/Shared/Gauges_Ru/RHS_FireBox2.et'),
    'B3CB28972BD32713': ('VehiclePartPanel', 'Prefabs/Vehicles/Shared/Gauges_Ru/RHS_FCS.et'),
    'C4C669819F1CBAD8': ('VehiclePartPanel', 'Prefabs/Vehicles/Shared/Gauges_Ru/RHS_TNPO-168A.et'),
    '3B7D5676B67CC90F': ('VehiclePartRotor',
                         'Prefabs/Vehicles/Helicopters/Mi8MT/VehParts/Rotor/VehPart_Mi8_rotor_main.et'),
}


def reviewed_vehicle_part(block):
    """Retain the original prefab and inherit its native behavior."""
    reference = REF.search(block[0])
    if not reference:
        return False
    kind, path = REVIEWED_VEHICLE_PARTS.get(reference[1], (None, None))
    return (block[0] == f'{kind} : "{{{reference[1]}}}{path}" {{'
            and len(block) == 4
            and block[1].startswith(' coords ') and block[2].startswith(' angles ')
            and all(FIELD.fullmatch(row.strip()) for row in block[1:-1]))


def native_placement(block, *, reviewed_character=False):
    if not NATIVE.match(block[0]) and not (
            reviewed_character and re.match(r'^(?:\$grp )?SCR_ChimeraCharacter(?: [\w.-]+)? : ', block[0])):
        return None
    if any('m_bEnableMoveOnFreeze' in line for line in block):
        block = staging_vehicle_placement(block)
    if ' components {' in block and any('TILW_SetNameComponent ' in line for line in block):
        head = PLACED_ENTITY.fullmatch(block[0])
        if head and head['name'] and not head['group']:
            component_start = block.index(' components {')
            component_end = block_end(block, component_start)
            setters = [index for index in range(component_start + 1, component_end)
                       if re.fullmatch(rf'  TILW_SetNameComponent {SERIALIZED_ID} \{{', block[index])]
            if len(setters) == 1:
                index = setters[0]
                if block[index + 1:index + 3] == [f'   m_setName "{head["name"]}"', '  }']:
                    # The authored entity name already performs the only mutation.
                    block = [*block[:index], *block[index + 3:]]
    text = '\n'.join(migrate_native_components(block)) + '\n'
    return None if LEGACY.search(text) else text


def migrate_native_components(block):
    lines = block
    if any(rebuilt in line for line in block for rebuilt in
           ('TILW_NavmeshRegenComponent', 'PK_EntityProtectorComponent', 'TILW_NoGarbageComponent')):
        lines = block.copy()
        stack = []
        for index, line in enumerate(block):
            if (NAVMESH_COMPONENT.fullmatch(line) and stack and stack[-1] == 'components {'
                    and block[index + 1] == ' ' * (len(line) - len(line.lstrip())) + '}'):
                lines[index] = line.replace('TILW_NavmeshRegenComponent', 'GC_NavmeshRebuildComponent', 1)
            if (GARBAGE_PROTECTION.fullmatch(line) and stack and stack[-1] == 'components {'
                    and block[index + 1] == ' ' * (len(line) - len(line.lstrip())) + '}'):
                lines[index] = line.replace('TILW_NoGarbageComponent', 'GC_GarbageProtectionComponent', 1)
            if STARTUP_PROTECTION.fullmatch(line) and stack and stack[-1] == 'components {':
                end = block_end(block, index)
                values = properties(block[index + 1:end], {'m_keepEntityAfterDelete'})
                if values.get('m_keepEntityAfterDelete', '1') not in ('0', '1'):
                    raise ValueError('unsupported cleanup protection settings')
                lines[index] = line.replace('PK_EntityProtectorComponent', 'GC_StartupCleanupProtectionComponent', 1)
                for field in range(index + 1, end):
                    lines[field] = ''
            if line.endswith(' {'):
                stack.append(line.strip())
            elif line.strip() == '}':
                stack.pop()
    return lines


EDITOR_COMMENT = re.compile(r'(?P<group>\$grp )?EditorCommentEntity(?: (?P<name>[\w.-]+))? \{')


def validate_editor_comments(block):
    head = EDITOR_COMMENT.fullmatch(block[0])
    if not head:
        raise ValueError('expected native editor comment')
    for _, fields in polygon_capture.members(block, head['group'], head['name']):
        fields, children = trigger_children(fields) if ' {' in fields else (fields, [])
        values = properties(fields, CAPTURE_TRANSFORM | {'m_Comment', 'm_Size', 'm_FaceCamera', 'm_TextBackground'})
        for field, value in values.items():
            if field in CAPTURE_TRANSFORM and not FIELD.fullmatch(f'{field} {value}'):
                raise ValueError('invalid editor comment transform')
        for _, child in children:
            validate_editor_comments(child)


def garrison_anchors(block, expected):
    # The inherited trigger only writes an unused flag. Keep its ID and local
    # transform so the three authored instance overrides still bind to it.
    lines = block.copy()
    starts = [index for index, line in enumerate(lines)
              if line.strip().startswith('TILW_FactionTriggerEntity ')]
    if len(starts) != expected:
        raise ValueError('reviewed garrison trigger shape changed')
    for start in reversed(starts):
        stop = block_end(lines, start)
        indent = ' ' * (len(lines[start]) - len(lines[start].lstrip()))
        fields = [line for line in lines[start + 1:stop]
                  if line == indent + ' ID "6A69010935EEE9B1"'
                  or (line.startswith(indent + ' ') and FIELD.fullmatch(line[len(indent) + 1:]))]
        if indent + ' ID "6A69010935EEE9B1"' not in fields:
            raise ValueError('reviewed garrison trigger identity changed')
        name = ' Building_1_Trigger' if ' Building_1_Trigger : ' in lines[start] else ''
        lines[start:stop + 1] = [indent + f'GenericEntity{name} {{', *fields, indent + '}']
    result = native_placement(lines)
    if not result:
        raise ValueError('garrison has another legacy declaration')
    return result


def garrison_placement(source, world, layer, block):
    if (layer != BLOODY_GARRISON_DIR + 'AI/GreenAI.layer'
            or any(source.files.get(path) != blob for path, blob in BLOODY_GARRISON_BLOBS.items())
            or source.resources.get(BLOODY_GARRISON) != [BLOODY_GARRISON_PREFAB]):
        raise ValueError('garrison resource or placement changed')
    flag = 'Building_1_Trigger'
    scripts = script_text(source)
    if (any(token in scripts for token in (flag, 'GetFlagSetCopy', 'm_flagSet'))
            or any(flag in source.read(path) for path in source.closure(world) - {BLOODY_GARRISON_PREFAB}
                   if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)):
        raise ValueError('garrison trigger has an authored consumer')
    return garrison_anchors(block, 3)


REVIEWED_REMOVABLE_SIGNS = {
    'worlds/HotMic/AssaultOnRasmanMilitaryBase/AssaultOnRasmanMilitaryBase_Layers/default.layer':
        'e6590e10100359f749151b044bd0e19069241b59',
    'worlds/HotMic/OperationConcreteCrushers/OperationConcreteCrushers_Layers/Objectives.layer':
        '1afcdd5588c87b19e7282e90e76a9ee752eecdf8',
}


def interaction_rules(source, world, stock, local_resources):
    placements, audit = {}, {}
    names, flags = set(), set()
    for path in source.layers[world.lower()]:
        text = source.read(path)
        if 'TILW_BaseInteraction' not in text and 'TILW_TeleportInteraction' not in text and BLACKBOX[0] not in text:
            continue
        for line, block in blocks(text):
            ref = REF.search(block[0])
            if ref and ref.groups() == BLACKBOX:
                location = f'{path}:{line}'
                try:
                    if source.resources.get(BLACKBOX[0]) != [BLACKBOX[1]]:
                        raise ValueError('blackbox prefab identity is ambiguous')
                    blackbox_prefab(source.read(BLACKBOX[1]))
                    native = blackbox_placement(block)
                    if LEGACY.search(native):
                        raise ValueError('blackbox placement has another legacy override')
                    placements[(path, line)] = native
                    flags.add(BLACKBOX_FLAG)
                    audit[location] = {'objectives': [{'entity': location, 'flag': BLACKBOX_FLAG, 'singleUse': True}],
                                       'inheritedBlackbox': True}
                except ValueError as exc:
                    audit[location] = {'gap': str(exc)}
                continue
            has_teleport = any(teleports.is_teleport(item) for item in block)
            if not has_teleport and not any('TILW_BaseInteraction' in item for item in block):
                continue
            location = f'{path}:{line}'
            try:
                if has_teleport:
                    native, routes = teleports.convert(block, location, stock, local_resources)
                    if LEGACY.search(native):
                        raise ValueError('unconverted teleport component or side effect')
                    placements[(path, line)] = native
                    audit[location] = {'teleportDestinations': [route['destination'] for route in routes]}
                    flags_read = [route['requiredFlag'] for route in routes if route['requiredFlag']]
                    if flags_read:
                        audit[location]['teleportRequiredFlags'] = flags_read
                    continue
                native, details = convert_interaction_block(
                    block, location, stock, local_resources,
                    removable_sign=source.files[path] == REVIEWED_REMOVABLE_SIGNS.get(path))
                if not details or LEGACY.search(native):
                    raise ValueError('unconverted interaction component or side effect')
                objectives = [record['flag'] for record in details]
                entities = {record['entity'] for record in details}
                if (len(set(objectives)) != len(objectives) or set(objectives) & flags
                        or entities & names):
                    raise ValueError('duplicate interaction entity or flag')
                flags.update(objectives)
                names.update(entities)
                placements[(path, line)] = native
                audit[location] = {'objectives': details}
                if flags_read := [record['requiredFlag'] for record in details if 'requiredFlag' in record]:
                    audit[location]['interactionRequiredFlags'] = flags_read
            except ValueError as exc:
                audit[location] = {'gap': str(exc)}
    return flags, placements, audit


@lru_cache(maxsize=4)
def script_text(source):
    return '\n'.join(source.read_raw(path).decode('utf-8-sig')
                     for path in source.files if path.lower().endswith('.c'))


FIA_CACHE_COMPOSITION = ('AB1A97B1BAE8C395', 'Prefabs/Compositions/Slotted/SlotFlatSmall/SupplyCache_S_FIA_01.et')
FIA_CACHE_NESTED_DAMAGE = (
    ('9AE2149222FAD9A3', 'Prefabs/Compositions/Misc/SubCompositions/Storage/Slotted/Storage_Supplies_FIA_03.et'),
    ('3FD3F403025BBDF5', 'Prefabs/Props/Military/SupplyBox/SupplyStack/SupplyStack_Large_01/SupplyStack_Large_01_storage.et'),
    ('BD2934A1F1EFE742', 'Prefabs/Props/Military/SupplyBox/SupplyStack/SupplyStack_Large_01/SupplyStack_Large_01_storage_base.et'),
    ('3260189525F39062', 'Prefabs/Props/Military/SupplyBox/SupplyStack/SupplyStack_Large_01/SupplyStack_Large_01_base.et'),
)


def fia_cache_damage_placement(block, stock, source_units, dev_units, local_resources):
    references = (FIA_CACHE_COMPOSITION, *FIA_CACHE_NESTED_DAMAGE)
    if (not stock or not all(stock.contains(*ref) for ref in references)
            or any(identity in local_resources or identity in source_units.resources or identity in dev_units.resources
                   for identity, _ in references)
            or sha256('\n'.join(block).encode()).hexdigest()
            != 'c51174920566523737086ccf2fd6ed1435a4f9f0eeca6c426e563ad2ea320b8b'):
        raise ValueError('reviewed FIA cache composition or damage target changed')
    # The stock storage subcomposition binds this placement ID to its large supply stack.
    start = block.index('     ID "5D45393FFA2FEF0F"') - 1
    stop = block_end(block, start)
    indent = '    '
    leaf = [line.removeprefix(indent) for line in block[start:stop + 1]]
    converted, flag = damage_state_flag_placement(leaf, clear_on_mismatch=True)
    native = native_placement([*block[:start], *(indent + line for line in converted), *block[stop + 1:]])
    if not native:
        raise ValueError('FIA cache composition has another legacy component')
    return native, [flag]


BEBBLE_ROOT = 'worlds/TheLocalPub/BebbleIslandRaid/'
BEBBLE_OBJECTIVES = BEBBLE_ROOT + 'BebbleIslandRaid_Layers/OBJECTIVES.layer'
BEBBLE_OBJECTIVES_BLOB = 'd7ac84420272fa4c521c6ca0e66aaa521cf9bf86'
BEBBLE_HVT = BEBBLE_ROOT + 'Prefabs/Characters/Factions/OPFOR/GC_ARG/1980_Regulars/Character_GC_ARG_1980_HVT.et'
BEBBLE_HVT_BLOB = '409f34968af01738fb4edf42e71f5301bf1b8b9c'
BEBBLE_FUEL = (BEBBLE_ROOT + 'Prefabs/Structures/Industrial/Containers/FuelTanks/FuelTank_01/FuelTank_01_green.et')
BEBBLE_FUEL_BLOB = '3dcda1664a20d2fe6cf3225c7a35efefd70cb386'
BEBBLE_AMMO = BEBBLE_ROOT + 'Prefabs/Props/Military/Camps/AmmoBox.et'
BEBBLE_AMMO_BLOB = '82eb89185ff8846f84933861e4d50ed31834fc87'
BEBBLE_AMMO_BASE = ('4CC2C69DE1AEF103', 'Prefabs/Props/Core/DestructibleMultiPhase_Props_Base.et')
BEBBLE_FUEL_BASE = ('C604699791656656', 'Prefabs/Structures/Industrial/Containers/FuelTanks/FuelTank_01/FuelTank_01_Base.et')
BEBBLE_ZU = ('FD109F3702AAB392', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_ZU23.et')
BEBBLE_AI_ONLY = '  PS_PlayableComponent "{5EFCC1946F475F15}" {\n   m_bIsPlayable 0\n  }\n'
BEBBLE_SPECIAL_FORCES = (BEBBLE_ROOT + 'Prefabs/Characters/Factions/BLUFOR/UK_Army/1983/Special Forces/'
                         'Character_UK_1983_SpecialForces_')
BEBBLE_WIRECUTTER_ROLES = {
    BEBBLE_SPECIAL_FORCES + role + '_P.et': blob for role, blob in (
        ('2IC', '2a32b664b478c861fd0808100c5c8dfb16f8c7f1'),
        ('CO', '14b4a183b6e0cec3fce62e1dff81184b9d378003'),
        ('Gren', '08bdbb8a45374a61230e01f682e681c427043abb'),
        ('PSG', '2dfeeebf391dd210c323319e4926799853f59c2a'),
        ('RecceSL', '3f7cae63a4c3a9d0956546e2e590448bd33305e1'),
        ('RecceTrooper', '34ebdfc985b77986aa3043469a759029505e5720'),
        ('Runner', '2b1293df9cf0a53f42b3093a1acfc02e78ed330b'),
        ('SL', '8cba77ce8d732d6ad8c335e1bc12598837bc12d4'),
        ('XO', '74ad483fecf95c6deaf017a51fa99fbc02318c87'),
    )
}
BEBBLE_WIRECUTTER = BEBBLE_ROOT + 'Prefabs/Items/Equipment/Wirecutters.et'


def bebble_hvt_placement(block):
    # The placement overrides the damage component's prefab flag along with the
    # character's faction, loadout and presentation fields.
    body = '\n'.join(block).replace(BEBBLE_AI_ONLY, '', 1)
    body = body.replace('   ' + LEGACY_SENTRY_TREE + '\n  }',
                        '  }\n  GC_StaticSentryComponent "{' + guid('migration/static sentry') + '}" {\n  }', 1)
    converted, flag = damage_state_flag_placement(body.splitlines(), allow_multiple_states=True)
    result = native_placement(converted, reviewed_character=True)
    if not result or flag != 'HVTDead':
        raise ValueError('unsupported Bebble HVT placement')
    return result, [flag]


def damage_flag_rules(source, world, stock, local_resources, source_units, dev_units, rhs=None, *,
                      workshop_targets=(), convert_crew):
    placements, audit, candidates = {}, {}, defaultdict(list)
    observed = {}
    layers = source.layers[world.lower()]
    for path in layers:
        text = source.read(path)
        aircraft_layer = REVIEWED_AIRCRAFT_LAYERS.get(path)
        if 'TILW_Flag_EntityDamage' not in text and not aircraft_layer:
            continue
        for line, block in blocks(text):
            location = f'{path}:{line}'
            if aircraft_layer and source.files[path] == aircraft_layer[0]:
                ref = REF.search(block[0])
                asset = REVIEWED_AIRCRAFT.get(ref[2]) if ref and ref[2] in aircraft_layer[2] else None
                parent = aircraft_layer[1]
                if (asset and asset[1] and source.resources.get(ref[1]) == [ref[2]]
                        and source.files.get(ref[2]) == asset[0] and parent in workshop_targets
                        and all(parent[0] not in resources for resources in
                                (local_resources, source_units.resources, dev_units.resources))):
                    native = native_placement(block)
                    if native:
                        # The flag observer is migrated on the single referenced prefab.
                        observed[(path, line)] = (native, [asset[1]])
                        continue
            bebble = path == BEBBLE_OBJECTIVES and source.files[path] == BEBBLE_OBJECTIVES_BLOB
            if block[0].startswith('$grp '):
                if not any('TILW_Flag_EntityDamage' in part for part in block):
                    continue
                try:
                    ref = REF.search(block[0])
                    authored_fuel = (bebble and ref and ref.groups() == ('E3295327B3F5929F', BEBBLE_FUEL)
                                     and source.files.get(BEBBLE_FUEL) == BEBBLE_FUEL_BLOB
                                     and stock and stock.contains(*BEBBLE_FUEL_BASE) and BEBBLE_FUEL_BASE[0] not in local_resources
                                     and all(BEBBLE_FUEL_BASE[0] not in units.resources for units in (source_units, dev_units)))
                    authored_zu = (bebble and ref and ref.groups() == BEBBLE_ZU and BEBBLE_ZU in workshop_targets
                                   and all(BEBBLE_ZU[0] not in units.resources for units in (source_units, dev_units)))
                    observed[(path, line)] = grouped_damage_placement(
                        block, stock, local_resources, source_units, dev_units, rhs,
                        workshop_targets=workshop_targets, reviewed_local=bool(authored_fuel),
                        convert_crew=convert_crew if authored_zu else None)
                except ValueError as exc:
                    audit[location] = {'gap': str(exc)}
                continue
            components = [i for i, part in enumerate(block) if DAMAGE_COMPONENT.fullmatch(part)]
            if not components:
                ref = REF.search(block[0])
                if ref and ref.groups() == FIA_CACHE_COMPOSITION and any('TILW_Flag_EntityDamage' in part for part in block):
                    try:
                        observed[(path, line)] = fia_cache_damage_placement(
                            block, stock, source_units, dev_units, local_resources)
                    except ValueError as exc:
                        audit[location] = {'gap': str(exc)}
                continue
            try:
                ref = REF.search(block[0])
                if (bebble and ref and ref.groups() == ('BEC1AB7A6E6C97B0', BEBBLE_HVT)
                        and source.files.get(BEBBLE_HVT) == BEBBLE_HVT_BLOB
                        and unchanged_units_parent(source_units, dev_units, 'BB64738FA06CE9D1')):
                    observed[(path, line)] = bebble_hvt_placement(block)
                    continue
                if not ref or not stock or ref[1].upper() in local_resources:
                    raise ValueError('unverified damage objective prefab or component placement')
                authored = (ref[1].upper(), ref[2])
                if (zones := REVIEWED_DAMAGE_FLAG_ZONES.get(authored)) and any(
                        field.strip().startswith('m_targetZone ') for field in block):
                    if (not stock.contains(*authored)
                            or any(authored[0] in units.resources for units in (source_units, dev_units))):
                        raise ValueError('unverified named damage objective prefab')
                    converted, observed_flags = damage_state_flag_placements(
                        block, allow_multiple_states=True, verified_zones=zones)
                    native = native_placement(converted)
                    if not native:
                        raise ValueError('named damage objective has another legacy component')
                    observed[(path, line)] = (native, observed_flags)
                    continue
                if len(components) != 1:
                    raise ValueError('unverified damage objective prefab or component placement')
                is_group = block[0].startswith('SCR_AIGroup ')
                is_character = block[0].startswith('SCR_ChimeraCharacter ')
                resource = ref[1].upper()
                start = components[0]
                stop = block_end(block, start)
                parent = block.index(' components {')
                if not parent < start < stop < block_end(block, parent):
                    raise ValueError('damage flag is outside root components')
                data = properties(block[start + 1:stop],
                                  {'m_flagName', 'm_targetState', 'm_targetZone', 'm_continuous', 'm_initialHealth'})
                flag = data.get('m_flagName', '""')
                zone = data.get('m_targetZone', '""')
                health = data.get('m_initialHealth', '1')
                if not re.fullmatch(NUMBER, health) or not 0 <= Decimal(health) <= 1:
                    raise ValueError('unsupported initial hit zone health')
                initial_health = Decimal(health) < 1
                stock_target = DAMAGE_TARGETS.get(resource) == ref[2] and stock.contains(*ref.groups())
                core_transfer = (block[0].startswith('Vehicle ')
                                 and verified_core_btr70_transfer(*authored, source_units, dev_units, stock))
                registered_target = ((stock_target or authored in workshop_targets)
                                     and resource not in source_units.resources and resource not in dev_units.resources)
                verified_default = (
                    zone == '""' and (core_transfer or registered_target)
                    and is_character == ref[2].startswith('Prefabs/Characters/'))
                verified_zone = (resource, ref[2], zone) in REVIEWED_HIT_ZONES
                if not (is_group or verified_default or verified_zone):
                    raise ValueError('unverified damage objective prefab or component placement')
                if is_character and (not verified_default or initial_health):
                    raise ValueError('character damage flag needs a verified default hit zone')
                initial = []
                if initial_health and not is_group:
                    initial = [block[start].replace('TILW_Flag_EntityDamage', 'GC_InitialHitZoneHealthComponent', 1),
                               f'   m_fInitialHealth {health}']
                    if zone != '""':
                        initial.append(f'   m_sHitZoneName {zone}')
                    initial.append(block[stop])
                if flag == '""' and initial:
                    converted = native_placement([*block[:start], *initial, *block[stop + 1:]])
                    if not converted:
                        raise ValueError('initial health target has another legacy component')
                    placements[(path, line)] = converted
                    audit[location] = {'initialHealth': health, 'hitZone': zone[1:-1]}
                    continue
                if (verified_default and not is_group and not initial_health
                        and (is_character or data.get('m_targetState', '2') not in ('2', 'DESTROYED')
                             or data.get('m_continuous', '0') != '0')):
                    converted, observed_flag = damage_state_flag_placement(block, allow_multiple_states=True)
                    native = native_placement(converted, reviewed_character=is_character)
                    if not native:
                        raise ValueError('damage objective has another legacy component')
                    observed[(path, line)] = (native, [observed_flag])
                    continue
                if (not DAMAGE_FLAG_NAME.fullmatch(flag) or not flag[1:-1].strip() or len(flag.encode()) > 130
                        or data.get('m_targetState', '2') not in ('2', 'DESTROYED')
                        or zone != '""' or data.get('m_continuous', '0') != '0'
                        or (is_group and initial_health)):
                    raise ValueError('unsupported damage flag condition')
                if is_group:
                    resource = ref[1].upper()
                    chain = inheritance(source_units, resource)
                    base = REF.search(chain[-1].partition('\n')[0]) if chain else None
                    signature = sha256('\n'.join(chain).encode()).hexdigest()
                    casualty_flags = peak_flags([line for layer in layers for line in source.read(layer).splitlines()])
                    if (DAMAGELESS_GROUPS.get(resource) != (ref[2], signature) or not base
                            or base[1].upper() in local_resources or not stock.contains(*base.groups())
                            or flag not in casualty_flags):
                        raise ValueError('unverified damageless group or missing casualty flag')
                    # Legacy binds only the owner, never a member. Keep the authored casualty producer.
                    placements[(path, line)] = '\n'.join([*block[:start], *block[stop + 1:]]) + '\n'
                    audit[location] = {'inertGroupFlag': flag[1:-1], 'condition': 'owner has no damage manager'}
                    continue
                head = re.fullmatch(r'(GameEntity|GenericEntity|Tank|Vehicle)(?: ([\w.-]+))?( : .+)', block[0])
                if not head:
                    raise ValueError('damage objective needs an individual placed entity')
                name = head[2] or 'GC_Damage_' + guid(location)
                if len(name.encode()) > 128:
                    raise ValueError('damage objective name exceeds the native limit')
                converted = [head[1] + ' ' + name + head[3], *block[1:start], *initial, *block[stop + 1:]]
                native = native_placement(convert_crew(converted))
                if not native:
                    raise ValueError('damage objective has another legacy component')
                candidates[flag[1:-1]].append((path, line, name, native, health if initial else None))
            except ValueError as exc:
                audit[location] = {'gap': str(exc)}
    flags = {}
    if not candidates and not observed:
        return flags, placements, audit
    references = '\n'.join(damage_flag_reference_text(source.read(path)) for path in sorted(source.closure(world))
                           if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    spawner_reads = placed_spawner_flag_reads(source, layers)
    for flag, entries in candidates.items():
        count = literal_flag_reads(references, flag) + spawner_reads[flag]
        if len(entries) != 1 or references.count(f'"{flag}"') != count + 1 or flag in script_text(source):
            for path, line, *_ in entries:
                audit[f'{path}:{line}'] = {'gap': 'damage flag has another producer or unsupported use'}
            continue
        path, line, name, native, health = entries[0]
        placements[(path, line)] = native
        flags[flag] = name
        audit[f'{path}:{line}'] = {'flag': flag, 'entity': name, 'condition': 'default damage area destroyed'}
        if health is not None:
            audit[f'{path}:{line}']['initialHealth'] = health
    for (path, line), (native, observed_flags) in observed.items():
        scripts = script_text(source)
        producer_counts = Counter(observed_flags)
        if (any(api in scripts for api in SCRIPT_FLAG_APIS) or any(
                references.count(f'"{flag}"') != literal_flag_reads(references, flag) + spawner_reads[flag] + count
                or f'"{flag}"' in scripts for flag, count in producer_counts.items())):
            audit[f'{path}:{line}'] = {'gap': 'observed damage flag has another producer or unsupported use'}
            continue
        placements[(path, line)] = native
        flags.update(dict.fromkeys(observed_flags))
        audit[f'{path}:{line}'] = {'placedDestructionFlags': observed_flags}
        if zones := re.findall(r'^   m_sHitZoneName "([^"]+)"$', native, re.M):
            audit[f'{path}:{line}']['namedDamageZones'] = zones
        shared = {flag: count for flag, count in producer_counts.items() if count > 1}
        if shared:
            audit[f'{path}:{line}']['sharedDamageFlags'] = shared
        if re.match(r'^(?:\$grp )?SCR_ChimeraCharacter ', native):
            audit[f'{path}:{line}']['placedCharacterDamage'] = True
    return flags, placements, audit


def verified_rhs_damage(resource, path, source_units, dev_units, rhs):
    if not rhs:
        return False
    declared = [(resource, path)]
    if (resource, path) == LOCKED_RHS_DAMAGE:
        parent = 'D8136D90BE12445F'
        declared.append((parent, RHS_DAMAGE_PATHS[parent]))
    elif RHS_DAMAGE_PATHS.get(resource) != path:
        return False
    if not all(rhs.contains(guid, prefab) for guid, prefab in declared if guid != LOCKED_RHS_DAMAGE[0]):
        return False
    for guid, prefab in declared:
        if guid not in source_units.resources and guid not in dev_units.resources and guid != LOCKED_RHS_DAMAGE[0]:
            continue
        if (source_units.resources.get(guid) != [prefab] or dev_units.resources.get(guid) != [prefab]
                or source_units.read(prefab) != dev_units.read(prefab)
                or sha256(dev_units.read(prefab).strip().encode()).hexdigest() != RHS_DAMAGE_OVERRIDES.get(guid)):
            return False
    return True


def grouped_damage_placement(block, stock, owned, source_units, dev_units, rhs, *, workshop_targets=(),
                             reviewed_local=False, convert_crew=None):
    ref = REF.search(block[0])
    if (not ref or not re.match(
            r'^\$grp (Vehicle|Tank|RHS_Tank|GameEntity|GenericEntity|StaticModelEntity|SCR_ChimeraCharacter|SCR_DestructibleBuildingEntity) ', block[0])
            or (ref[1].upper() in owned and not reviewed_local)):
        raise ValueError('unverified grouped damage objective prefab')
    resource, path = ref[1].upper(), ref[2]
    is_character = block[0].startswith('$grp SCR_ChimeraCharacter ')
    registered = ((stock and DAMAGE_TARGETS.get(resource) == path and stock.contains(resource, path))
                  or (resource, path) in workshop_targets)
    verified_target = ((registered or reviewed_local)
                       and resource not in source_units.resources and resource not in dev_units.resources)
    reviewed_units = UNITS_DAMAGE_TARGETS.get((resource, path))
    if not verified_target and reviewed_units:
        blob, parent, parent_override = reviewed_units
        parent_registered = parent in workshop_targets or (stock and stock.contains(*parent))
        parent_id, parent_path = parent
        verified_target = (parent[0] not in owned and parent_registered
                           and all(units.resources.get(resource) == [path]
                                   and units.files.get(path) == blob
                                   and (parent_id not in units.resources if parent_override is None else
                                        units.resources.get(parent_id) == [parent_path]
                                        and units.files.get(parent_path) == parent_override)
                                   for units in (source_units, dev_units)))
    if (is_character != path.startswith('Prefabs/Characters/')
            or not (verified_target or not is_character and verified_rhs_damage(resource, path, source_units, dev_units, rhs))):
        raise ValueError('unverified grouped damage objective prefab')
    members, flags = [], []
    for name, fields in polygon_capture.members(block, True, None):
        member = ['{', *fields, '}']
        member_flags = []
        if any('TILW_Flag_EntityDamage' in line for line in fields):
            member, member_flags = damage_state_flag_placements(member, allow_multiple_states=True)
            flags.extend(member_flags)
        members.append((name, member, member_flags))
    shared = Counter(flags)
    lines = [block[0]]
    for name, member, member_flags in members:
        shared_settings = {f'   m_sFlag "{flag}"' for flag in member_flags if shared[flag] > 1}
        # Each legacy producer could clear the shared flag before its own first match.
        for setting in range(len(member) - 1, -1, -1):
            if member[setting] in shared_settings:
                member.insert(setting + 1, '   m_bClearOnMismatch 1')
        lines.extend([f' {name} {{' if name else ' {', *(' ' + part for part in member[1:-1]), ' }'])
    lines.append(block[-1])
    native = native_placement(convert_crew(lines) if convert_crew else lines, reviewed_character=is_character)
    if not native or not flags:
        raise ValueError('grouped damage placement has another legacy component')
    return native, flags


def damage_flag_reference_text(text):
    # An event's display name can equal its condition flag without reading or writing the flag.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if EVENT.fullmatch(line):
            stop = block_end(lines, index)
            for item in range(index + 1, stop):
                if lines[item].startswith('   m_name '):
                    lines[item] = ''
    return '\n'.join(lines)


def literal_flag_reads(text, flag):
    # Literal inversion may be serialized before or after the name.
    return len(re.findall(
        rf'(?m)^( +)(?:m_(?:condition|definition) )?TILW_LiteralTerm {SERIALIZED_ID} \{{\n'
        r'(?:\1 m_invertTerm [01]\n)?\1 m_flagName "'
        + re.escape(flag) + r'"\n(?:\1 m_invertTerm [01]\n)?\1\}$', text))


def placed_spawner_flag_reads(source, layers):
    """Count direct consumers that the placed spawner rule must also convert."""
    flags = Counter()
    local_files_layers = [path for path in layers if source.files[path] == LOCAL_FILES_SPAWNER_LAYERS.get(path)]
    if local_files_layers and LOCAL_FILES_MARKER_SPAWNER in source.closure(
            LOCAL_FILES_ROOT + 'TheLocalFiles.ent'):
        flags['BananaRandomFlag1'] += 1
    for path in layers:
        text = source.read(path)
        if 'm_conditionFlag ' not in text or 'TILW_PrefabSpawnerEntity ' not in text:
            continue
        for _, _, block in placed_spawner_entries(source, path, text):
            match = placed_spawner_root(block[0], (path, source.files[path]) in REVIEWED_BARE_SPAWNER_LAYERS)
            if not match:
                continue
            try:
                members = polygon_capture.members(block, match['group'], match['name'], allow_quoted=True)
            except ValueError:
                continue
            for _, fields in members:
                direct = fields
                if path in local_files_layers and ' {' in fields:
                    parent, children = trigger_children(fields)
                    direct = [*parent, *(line for _, child in children for line in child[1:-1])]
                for line in direct:
                    if flag := re.fullmatch(r' m_conditionFlag (' + FLAG_NAME + ')', line):
                        flags[flag[1][1:-1]] += 1
    return flags


def placed_spawner_root(header, allow_bare):
    return PLACED_SPAWNER.fullmatch(header) or (allow_bare and BARE_SPAWNER.fullmatch(header))


def trigger_children(fields):
    start = fields.index(' {')
    if block_end(fields, start) != len(fields) - 1:
        raise ValueError('custom placement after trigger children')
    return fields[:start], list(blocks('\n'.join(line[2:] for line in fields[start + 1:-1])))


SCENARIO_AREA_GROUP = '$grp GenericEntity : "{C72F956E4AC6A6E7}Prefabs/Systems/ScenarioFramework/Components/Area.et" {'
REVIEWED_BUILDING_AREAS = {
    '31f8d038b181f0a8a3021ad08cae9713dc7e5df1',
    '27ce0a37a17a119bc53ccf9257df2a09a76679bf',
}
SCREENING_AREA_LAYER = ('worlds/HitTheMan/Screening The Orb/ScreeningTheOrb_Layers/'
                        'BLUFOR/BLU_AI_EXTERIOR_CachedAI.layer')
SCREENING_AREA_BLOB = '2fd93fe71c5cbe9fcde680e6ba61c3ada8d9fac2'
SCREENING_AREA_FLAGS = {'SouthExterior1': 1, 'WestExterior2': 1, 'SouthExterior2': 2}
CONCRETE_CRUSHERS_LAYERS = 'worlds/HotMic/OperationConcreteCrushers/OperationConcreteCrushers_Layers/'
CONCRETE_EXTRACTION_WRITERS = {
    'Lamentin': '2d32007d8fc2d7f4513da1cba6710845a6576459',
    'Montignac': '1fb7cf66c2ebc8163867d70ddf2c2d392497103c',
    'Morton': '1de19a7b3d43a972e3b08fb0d6493971d7c52e70',
    'StPhilipe': '50848ae50530a27b7fbd293ec4eee83f5ea57015',
    'StPierre': '23d608db5c2d1d7be648d476f10b75b18473fb09',
}
DESERT_SENTINEL_LAYERS = 'worlds/anteante101/Operation Desert Sentinel/Operation Desert Sentinel_Layers/'
DESERT_SENTINEL_MARKERS = DESERT_SENTINEL_LAYERS + 'markers.layer'
DESERT_SENTINEL_MARKERS_BLOB = '90a3debf2867d1f7236d725165135fa213f69a3e'
ZARICHNE_DEFAULT = 'worlds/HitTheMan/Battle For Zarichne/BattleForZarichne_Layers/default.layer'
ZARICHNE_DEFAULT_BLOB = '878a776b06914e05e7cf35b6d1ecae08354d4175'
ZARICHNE_PROPS = 'worlds/HitTheMan/Battle For Zarichne/BattleForZarichne_Layers/CORE/Props.layer'
ZARICHNE_PROPS_BLOB = 'bfa408082be4d02e2b168aa8ec0a420d1799cc95'
ZARICHNE_LAV_LAYER = 'worlds/HitTheMan/Battle For Zarichne/BattleForZarichne_Layers/US_Players/US_Players.layer'
ZARICHNE_LAV_BLOB = '2216e12d652b1340978cbd5cc4b652b262aec963'
ZARICHNE_CREW_NAMES = ('LAVCrew1', 'LAVCrew2')
ZARICHNE_MINE = 'worlds/HitTheMan/Battle For Zarichne/Mine_M15AT_EffectModuleEntity_HTM.et'
ZARICHNE_WARHEAD = 'worlds/HitTheMan/Battle For Zarichne/Warhead_Mine_M15AT_HTM.et'
BEACH_HOLIDAY = 'worlds/HitTheMan/Beach Holiday To Mineraly/'
BEACH_HOLIDAY_LAYERS = BEACH_HOLIDAY + 'BeachHolidaytoMineraly_Layers/'
BEACH_HOLIDAY_DEFAULT = BEACH_HOLIDAY_LAYERS + 'default.layer'
BEACH_HOLIDAY_DEFAULT_BLOB = '8fcc23a5628083ffb70b10eb3b8ef322870eca48'
BEACH_HOLIDAY_WAVES = BEACH_HOLIDAY_LAYERS + 'AI_Defenders/AI_Waves.layer'
BEACH_HOLIDAY_WAVES_BLOB = '4829323a30d5063ca8b69d13d663bd4a9559c007'
BEACH_HOLIDAY_APARTMENT = BEACH_HOLIDAY_LAYERS + 'AI_Defenders/AI_Defenders_Apartment.layer'
BEACH_HOLIDAY_APARTMENT_BLOB = '444b585fc7ff80c8809ec566141476b9a244b087'
BEACH_HOLIDAY_BLUE = BEACH_HOLIDAY_LAYERS + 'BLUFOR_Players/BLUFOR_Players.layer'
BEACH_HOLIDAY_BLUE_BLOB = '2cdfdee7c12327f6e112cd50461a96f2b8b68bc8'
VILLA = 'worlds/HitTheMan/Stealthing Into Villa/'
VILLA_HIND = VILLA + 'Prefab/Mi24V_armed_UPK1_HTM.et'
VILLA_OPFOR = VILLA + 'StealthingIntoVilla_Layers/OPFOR_Players/OPFOR_Players.layer'
VILLA_OPFOR_BLOB = '5daa19731dbdcc75bd45833e0f17285b384dbe2a'
VILLA_BLUFOR = VILLA + 'StealthingIntoVilla_Layers/BLUFOR_Players/BLUFOR_Players.layer'
VILLA_BLUFOR_BLOB = '386dfc4dd48baa575d3b6893f973813518d2c878'
VILLA_ROLE_ASSETS_HASH = 'b9f9378648905622eebdb9a86d84f96dba1d4ec9be6b530e6cd82609d9722735'
VILLA_UNPLACED_MARKERS = {'Medic_Marker_2', 'Medic_Marker_3', 'Rifleman_Marker_1'}
VILLA_PROPS = VILLA + 'StealthingIntoVilla_Layers/CORE/Props.layer'
VILLA_DEFAULT = VILLA + 'StealthingIntoVilla_Layers/default.layer'
VILLA_DEFAULT_BLOB = '3cf1bfd80d2ccf4d78ad37e074c6ef6759db894e'
VILLA_PROPS_BLOB = 'ad35832247cc956f9252146c506c55ed92f6ebec'
VILLA_LIGHT_CLEANUP = ('3AA16BA294431D4E', VILLA + 'Prefab/LightsGeneratorCheckpoint.et',
                      'ee206e451196828f2fa9e75bc6dd49e71037d8ef')
VILLA_COMMANDER = ('EF8996D07DEC3E86', VILLA + 'Prefab/Character_USSR_Officer_HTM.et',
                   'ab76c84351e6b20760d645e9b60eaec93ecc0ccc')
VILLA_ESCAPE_TRIGGER = ('D344EE6C304890DD', VILLA + 'Prefab/BluforEscapeTrigger.et',
                        'dcf0f545b012295af1641e6ed3a96017220c8443')
VILLA_ESCAPE_BOUNDARY = ('80437C58E91B0A3E', VILLA + 'Prefab/AO_Draw_Escape_BLU_Prefab.et',
                         'c90046ff90d0690c0e1aa7f5ed64578d9fe6eff1')
IRON_CIPHER = 'worlds/Jub/OperationArrowheadIronCipher/'
IRON_CIPHER_LAYERS = IRON_CIPHER + 'OperationArrowheadIronCipher_Layers/'
IRON_CIPHER_DEFAULT_BLOB = 'ea4e6b40efcf8df9119bff025c64adea1448eaaa'
IRON_CIPHER_INTEL = ('13A9833A6EEB870A', 'worlds/Wade/NightSeizure/Prefabs/Intel.et',
                     '729184282a8fd64e84ae6b16f01da6b0469827e3')
IRON_CIPHER_AO = ('EAD42D9E3F10491B', IRON_CIPHER + 'Prefabs/AO2.et',
                  '4a4b6eea551653c85ba78ce033c117066b4a85a6')
IRON_CIPHER_MARKER = ('7BE5411E75A669E2', IRON_CIPHER + 'Prefabs/EP2.et',
                      'ae4bab70759ef97429f1cf0480eefb9e595a85c3')
IRON_CIPHER_EXTRACT_LAYER = IRON_CIPHER_LAYERS + 'SYSTEMS/Triggers/Extract.layer'
IRON_CIPHER_RADIUS_PREFABS = {
    'CC335FCAB3776616': (IRON_CIPHER + 'Prefabs/Extract.et', 'c5f6d5a0010e8c2c741e7ac9a60d6fc66165aab3'),
    '76B07D8520A22B06': (IRON_CIPHER + 'Prefabs/intelex2.et', '4536378bf14aef31a80e187d971a81b19804d521'),
}
VILLA_CONVOY_LAYER = VILLA_OPFOR.replace('OPFOR_Players.layer', 'FIA_RandomizedTrucks.layer')
VILLA_CONVOY_LAYER_BLOB = '8b538f02ffd668927c84e6d50c593e74e63cb9f8'
VILLA_CONVOY_PREFABS = VILLA + 'Prefab/QRF Relief Convoy/'
VILLA_OLD_CONVOY_PREFABS = VILLA + 'Prefab/2nd Soviet Faction/'
VILLA_CONVOY_FLAGS = ('TruckSpawn2', 'TruckSpawn1', 'TruckSpawn3')
VILLA_CONVOY_GROUPS = {
    'F99AE6CF35749D42': ('ConvoyCommand', '9aa1ea5b2d3e84bc591ca6e7f61a1f15e49a8572', 1),
    '7C78E50CA7AF6C54': ('ConvoyEscort_1', '69404e071bd52c95bc6c18f2ee8aeda76ff003bd', 0),
    '102CD1EC1B6BE37A': ('ConvoyEscort_2', 'b85cac917e3125154e10dbd5e66cf0b5a60a4ae8', 3),
}
VILLA_CONVOY_MARKER = ('603A005CC6689857', 'FuelTruckMarkerV4.et', 'fde2d7d86ce66030062527c54fca5ca98122c147')
REVIEWED_AIRCRAFT = {
    BEACH_HOLIDAY + 'Prefab/AH1S_Marines_AIM9_Limited_HTM.et': ('c02db5fafc059fa494a8a087e89dc3ec345b8f01', 'AH1S_1'),
    BEACH_HOLIDAY + 'Prefab/AH1S_Marines_M299_Limited_HTM2.et': ('5a34a85fea8f47f53f2caa01f2bf8aef1a2751af', 'AH1S_2'),
    BEACH_HOLIDAY + 'Prefab/Mi24P_armed_R60_NOATGM_HTM.et': ('719366cae8992154dbdd7c72f74ac22318904a40', None),
    VILLA_HIND: ('193c535e8cb1e6bc8bb41f308bb1e4b749d4cc20', 'HindDestroyed'),
}
REVIEWED_AIRCRAFT_LAYERS = {
    BEACH_HOLIDAY_BLUE: (BEACH_HOLIDAY_BLUE_BLOB, COBRA_DAMAGE_PARENT,
                        {path for path in REVIEWED_AIRCRAFT if path.startswith(BEACH_HOLIDAY)}),
    VILLA_OPFOR: (VILLA_OPFOR_BLOB, HIND_DAMAGE_PARENT, {VILLA_HIND}),
}
REVIEWED_AREA_FLAG_WRITERS = {
    BEACH_HOLIDAY_DEFAULT: (BEACH_HOLIDAY_DEFAULT_BLOB, {'Factory': 1}),
    BEACH_HOLIDAY_WAVES: (BEACH_HOLIDAY_WAVES_BLOB, {'Waves': 1}),
    OIL_RIG_POPULATION_AREA[0]: (OIL_RIG_POPULATION_AREA[1], {'Hostage<': 1}),
    OLD_WOOD_STEPOFF[0]: (OLD_WOOD_STEPOFF[1], {'UK_STEPOFF': 1}),
    INTERSECTING_DEFAULT: (INTERSECTING_DEFAULT_BLOB, {'reinforce': 2}),
    SCREENING_AREA_LAYER: (SCREENING_AREA_BLOB, SCREENING_AREA_FLAGS),
    **{f'{CONCRETE_CRUSHERS_LAYERS}PhaseTwoRedfor/{location}.layer':
       (blob, {'Phase 1 Delete Units Flag': 1}) for location, blob in CONCRETE_EXTRACTION_WRITERS.items()},
    DESERT_SENTINEL_LAYERS + 'trigger.layer':
        ('d746de66b64f2048cdc41dcffec4e415e9e7d2be', {'extract': 3}),
    'worlds/Huska/WindyGoat/WindyGoat_Layers/Triggers.layer':
        ('c939b15d295709f83d2008d87cc90766388b2c0f', {'HVT_Extracted': 2}),
    'worlds/Perfk/PowerPlay/PowerPlay_Layers/OPFOR/Players/Players.layer':
        ('4789915b7734553a2e1ba6492a4bc5da95d3ac55', {'ReleaseOpfor': 6}),
    ZARICHNE_DEFAULT: (ZARICHNE_DEFAULT_BLOB, {'Compound': 2}),
    **{f'{TRIFECTA_ROOT}Trifecta_Layers/EXTRACTS/{layer}.layer': (blob, {f'STM_IN_{suffix}': 1})
       for suffix, (layer, blob) in TRIFECTA_PLACED_EXTRACTS.items()},
    **{f'{NAGALAM_AMBUSH_LAYERS}Ambush_{index:02}.layer':
       (blob, {f'Ambush_{index:02}_Trigger': 2})
       for index, blob in enumerate(NAGALAM_AMBUSH_BLOBS, 1)},
}
REVIEWED_AREA_FLAG_READERS = {
    BEACH_HOLIDAY_DEFAULT: (BEACH_HOLIDAY_DEFAULT_BLOB, 'event',
                            {'Factory': ('Respawn Condition', 'Factory TP')}),
    OIL_RIG_POPULATION_FRAMEWORK[0]: (OIL_RIG_POPULATION_FRAMEWORK[1], 'event', {'Hostage<': ('CLEARFLAGS', 'UKMinVic')}),
    TRIFECTA_FRAMEWORK[0]: (TRIFECTA_FRAMEWORK[1], 'event with redundant clear', {
        f'STM_IN_{suffix}': (f'STM_{suffix}_EX', f'STM_NOT_IN_{suffix}')
        for suffix in TRIFECTA_PLACED_EXTRACTS
    }),
    CONCRETE_CRUSHERS_LAYERS + 'default.layer':
        ('2f142b3b9ff66a1dff96c07d077256ff3a402cec', 'event', {'Phase 1 Delete Units Flag': 'Phase 1 Delete Units'}),
    DESERT_SENTINEL_LAYERS + 'default.layer':
        ('63b3243cf54d619b68e4f16724fb2cdd9970d7d3', 'event', {'extract': 'Mission Victory'}),
    'worlds/Huska/WindyGoat/WindyGoat_Layers/default.layer':
        ('7cef76758a05e016aefe225413d201f04ecdb443', 'event', {'HVT_Extracted': 'All_HVTs_Extracted'}),
    'worlds/Perfk/PowerPlay/PowerPlay_Layers/default.layer':
        ('327c5cf20c9bbcfdf16a8456e2d0b5aa0812d2eb', 'event', {'ReleaseOpfor': 'ReleaseOpfor'}),
    ZARICHNE_DEFAULT:
        (ZARICHNE_DEFAULT_BLOB, 'event', {'Compound': ('NVA OBJ Victory', 'Compound Message')}),
    NAGALAM_LAYERS + 'default.layer':
        ('e0048e9e3cf31e8776a48dd15235d34b3673246f', 'spawner condition',
         {f'Ambush_{index:02}_Trigger': f'Ambush_{index:02}_CloseTrigger'
          for index in range(1, len(NAGALAM_AMBUSH_BLOBS) + 1)}),
}
SCREENING_AI_WORLD_LAYER = 'worlds/HitTheMan/Screening The Orb/ScreeningTheOrb_Layers/default.layer'
SCREENING_AI_WORLD_SOURCE = '3f8fbb421bf2b903a9034f8d698f85a01b8b27d2'
SCREENING_AI_WORLD_REVISIONS = {'58773b357fef8e5db20e8f3a5fc0b12891a43521',
                                '3eac40a7befe19671fbd9abb4e8e78714a804dd0'}
BAD_ORB_AI_WORLD = ('33A5B976E41B0A4C', 'Prefabs/AI/SCR_AIWorld_BadOrb_GC.et')
BAD_ORB_AI_WORLD_BLOB = 'fa88aab78356bca0f0a6995a880e5ccf9cf738f2'
BARE_AI_WORLD = 'SCR_AIWorld {\n}\n'
OFF_MAP_ARTILLERY_LAYERS = {
    'worlds/HitTheMan/2nd Break The Line/2ndBreakTheLine_Layers/default.layer':
        '67b3e00e605f97eec6ae80538fd3ae551deb7d28',
    'worlds/HitTheMan/3rd Break The Line/3rdBreakTheLine_Layers/default.layer':
        'd7e5c4daa844e11787e8f1be7d7b4df2f94f5933',
    'worlds/Corax/SummerDays/SummerDays_Layers/default.layer':
        '45d91535504c59cfdf271f36fecaa9f028eccbb2',
}
LEGACY_ARTILLERY = 'GCC_Arty : "{6C38A36F9C37A967}Prefabs/Modules/Off_Map_Arty.et" {'
LOBBY_ARTILLERY = ('GC_OffMapArtillery GC_OffMapArtillery : '
                   '"{3AF7F6A04B4F7564}Prefabs/Modules/GC_OffMapArtillery.et" {')
REVIEWED_DORMANT_SPAWNER_FLAGS = {
    'c63178d8012c2dfeb9ccf8d669018d8502bcb10f': (('Spawn At Oil Depots', 1),),
    'fc23379018f367731dde8cd1214c4b800c72f0e5': (('HeliTwo', 1),),
    **dict.fromkeys(REVIEWED_BUILDING_AREAS, (('SpawnTwo', 2), ('US_Trigger_Right_3', 1))),
    '9dfaedf4beacd04c9260e63be23dc6194ce4d9be': (('Counter_Trigger_2', 2),),
    SCREENING_AREA_BLOB: (('WestExterior1', 1), ('MiddleExterior_Trigger_1', 1)),
    KHANDAR_AI_BLOB: (('atk1', 3),),
}
BREAK_THE_LINE_EFFECTS = {
    'A7E0C1FAD9FFAB46': 'HTM_EffectModule_Zoned_MortarBarrage_Creeping.et',
    '189D347F759C07DF': 'HTM_EffectModule_Zoned_Smoke_MortarBarrage_Creeping.et.et',
}
ARCHIVED_VOLHA = ('BD1F07D0C32629AF', 'Prefabs/Structures/Wreck/volha.et')
VOLHA_MESH = ('6AF0EEA50424149A', 'Assets/ME_Wreck/Volha.xob')
VOLHA_STOCK_PARENT = ('84CBCE9163176B3E', 'Prefabs/Props/Wrecks/S105_wreck/S105_wreck.et')


def nested_placement_blocks(block, pattern):
    for index, line in enumerate(block[1:-1], 1):
        body = line.lstrip(' ')
        if body == line or not pattern.fullmatch(body):
            continue
        indent = len(line) - len(body)
        stop = block_end(block, index)
        yield index, stop, indent, [row[indent:] for row in block[index:stop + 1]]


NAGALAM_TOWN_SPAWN_LAYER = NAGALAM_LAYERS + 'AI_Phase_One_Convoy_Attack/AI_Convoy_WPs.layer'
NAGALAM_TOWN_SPAWN_BLOB = '7982a8e55ee1d4f891307e8aaabb76178f1696e8'
NAGALAM_HILLTOP_LAYER = NAGALAM_LAYERS + 'AI_Phase_One_Convoy_Attack/HiltopWave.layer'
NAGALAM_AI_GROUPS = {
    '23DFFD3FDEECE9D7': 'PK_MUJ_04_Fighters.et',
    '247DB5CD3CBA7418': 'PK_MUJ_03_AT_Fighters.et',
    '6FD18265B1F81D25': 'PK_MUJ_03_MG_Fighters.et',
}


def reviewed_spawner_container(source, layer, block):
    return (layer == NAGALAM_TOWN_SPAWN_LAYER and source.files[layer] == NAGALAM_TOWN_SPAWN_BLOB
            and block[0] == '$grp GenericEntity {'
            and any(PLACED_SPAWNER.fullmatch(row.lstrip(' ')) for row in block[1:-1]))


def placed_spawner_entries(source, layer, text):
    """Yield root spawners and spawners under supported area placements."""
    for line, block in blocks(text):
        if re.match(r'^(?:\$grp )?TILW_PrefabSpawnerEntity\b', block[0]):
            yield line, False, block
        elif block[0] == SCENARIO_AREA_GROUP or reviewed_spawner_container(source, layer, block):
            for _, _, _, child in nested_placement_blocks(block, PLACED_SPAWNER):
                yield line, True, child
        elif root := TRIGGER.fullmatch(block[0]):
            try:
                members = list(polygon_capture.members(block, root['group'], root['name']))
                children = [child for _, fields in members if ' {' in fields
                            for _, child in trigger_children(fields)[1]]
            except ValueError:
                continue
            for child in children:
                if (head := PLACED_SPAWNER.fullmatch(child[0])) and not head['group'] and head['name']:
                    yield line, True, child


def unused_flag_sampling(source, world, path, line, framework):
    start = framework.index(' m_randomFlags {')
    end = block_end(framework, start)
    flags = []
    index = start + 1
    name = r'"([\w.-]+(?: +[\w.-]+)*)"'
    while index < end:
        kind = re.fullmatch(r'  TILW_(FlagSampling|RandomFlag) "\{[\da-fA-F]{16}\}" \{', framework[index])
        if not kind:
            return None
        stop = block_end(framework, index)
        # With no flag name this default Bernoulli draw cannot write a mission flag.
        if kind[1] == 'RandomFlag' and stop == index + 1:
            index = stop + 1
            continue
        if kind[1] == 'FlagSampling':
            data = properties(framework[index + 1:stop], {'m_flagNames'})
            names = data.get('m_flagNames', '')
        else:
            data = properties(framework[index + 1:stop], {'m_flagName', 'm_chance'})
            chance = data.get('m_chance', '0.5')
            names = data.get('m_flagName', '')
            if not re.fullmatch(NUMBER, chance) or not 0 <= Decimal(chance) <= 1 or not re.fullmatch(name, names):
                return None
        if not re.fullmatch(rf'(?:{name}\s*)+', names):
            return None
        flags.extend(re.findall(name, names))
        index = stop + 1
    content = source.read(path).splitlines()
    # Exclude the entire sampling block only. Declarations elsewhere in the same layer still count as uses.
    rest = '\n'.join(content[:line + start - 1] + content[line + end:])
    other = '\n'.join(source.read(p) for p in sorted(source.closure(world) - {path})
                      if PurePosixPath(p).suffix.lower() in SOURCE_TEXT)
    scripts = script_text(source)
    if any(f'"{flag}"' in rest or f'"{flag}"' in other or flag in scripts for flag in flags):
        return None
    if 'GetFlagSetCopy' in scripts or 'm_flagSet' in scripts:
        return None
    return sorted(set(flags))


PLACED_ENTITY = re.compile(r'(?P<group>\$grp )?(?P<kind>\w+)(?: (?P<name>[\w.-]+))?(?: : "[^"]+")? \{')
QUOTED_PLACED_ENTITY = re.compile(r'(?P<group>\$grp )?(?P<kind>\w+) "(?P<name>[\w.-]+)"(?: : "[^"]+")? \{')


def named_placements(layers):
    named = {}

    def visit_children(block):
        if ' {' not in block[1:-1]:
            return
        start = block.index(' {', 1)
        end = block_end(block, start)
        children = '\n'.join(line[2:] for line in block[start + 1:end])
        for _, child in blocks(children):
            visit(child)

    def visit(block):
        head = PLACED_ENTITY.fullmatch(block[0]) or QUOTED_PLACED_ENTITY.fullmatch(block[0])
        if not head:
            return
        if head['name']:
            named.setdefault(head['name'], []).append((head['kind'], block))
        if head['group']:
            for index, line in enumerate(block[1:-1], 1):
                member = polygon_capture.MEMBER.fullmatch(line) or polygon_capture.QUOTED_MEMBER.fullmatch(line)
                if member:
                    placement = block[index:block_end(block, index) + 1]
                    if member[1]:
                        named.setdefault(member[1].strip('"'), []).append((head['kind'], placement))
                    visit_children([line[1:] for line in placement])
        else:
            visit_children(block)

    for text in layers:
        for _, block in blocks(text):
            visit(block)
    return named


def unsupported_vehicle_group_fields(block):
    """Locate legacy fields that hide the adjacent child roster in the native loader."""
    root = PLACED_ENTITY.fullmatch(block[0])
    if not root or root['kind'] not in ('Vehicle', 'Tank', 'RHS_Tank'):
        return ()
    indent = '  ' if root['group'] else ' '
    removed = []
    for index in range(2, len(block) - 2):
        if (block[index] != indent + '{'
                or not re.match(r'^(?:\$grp )?SCR_AIGroup ', block[index + 1].removeprefix(indent + ' '))):
            continue
        previous = index - 1
        if block[previous] == indent + 'm_bEnableMoveOnFreeze 0':
            # The legacy field defaults to false. Preserve nondefault settings for review.
            removed.append(previous)
            previous -= 1
        if block[previous].startswith(indent + 'm_sAttachmentGroupName ') and VEHICLE_LINK.fullmatch(block[previous]):
            removed.append(previous)
    return tuple(removed)


def convert_vehicle_groups(block, convert_group_block):
    """Convert placed group children in the vehicle's original coordinate space."""
    root = PLACED_ENTITY.fullmatch(block[0])
    if (not root or root['kind'] not in ('Vehicle', 'Tank', 'RHS_Tank')
            or not any(re.match(r'^ +(?:\$grp )?SCR_AIGroup ', line) for line in block[1:])):
        return block, 0, 0
    # The catalog records the original links for the deferred vehicle preview feature.
    removed = set(unsupported_vehicle_group_fields(block))
    block = [line for index, line in enumerate(block) if index not in removed]
    rendered = [block[0]]
    groups = slots = 0
    for name, fields in polygon_capture.members(block, root['group'], root['name']):
        if ' {' in fields:
            start = fields.index(' {')
            stop = block_end(fields, start)
            if stop != len(fields) - 1:
                raise ValueError('unsupported fields after vehicle children')
            children = []
            for _, child in blocks('\n'.join(line[2:] for line in fields[start + 1:stop])):
                if re.match(r'^(?:\$grp )?SCR_AIGroup ', child[0]):
                    output, group_count, slot_count = convert_group_block('\n'.join(child) + '\n')
                    child = output.splitlines()
                    groups += group_count
                    slots += slot_count
                children.extend('  ' + line for line in child)
            fields = [*fields[:start + 1], *children, fields[stop]]
        if root['group']:
            rendered.extend([f' {name} {{' if name else ' {', *(' ' + line for line in fields), ' }'])
        else:
            rendered.extend(fields)
    return [*rendered, block[-1]], groups, slots


def waypoint_gaps(named, assignments, spawned_names, scripts, crew_spawns=None, placed_crews=()):
    gaps = set()
    no_ops = set()
    for action in assignments:
        group = action['group']
        placements = named.get(group, [])
        missing = action.get('missingWaypoint')
        if missing and (missing in named or missing in spawned_names or missing in placed_crews or missing in scripts):
            gaps.add('reviewed missing waypoint now resolves: ' + missing)
        if (action['waypoints'] == [group] and len(placements) == 1
                and re.fullmatch(WAYPOINT_CLASS, placements[0][0])
                and group not in spawned_names and group not in scripts):
            # Both runtimes require an AIGroup. A waypoint cannot assign orders to itself.
            no_ops.add(group)
            continue
        crew = (crew_spawns or {}).get(group)
        if len(placements) == 1 and placements[0][0] == 'SCR_AIGroup':
            existing_points = '\n'.join(placements[0][1])
        elif not placements and group in placed_crews and group not in spawned_names and group not in scripts:
            existing_points = ''
        elif (not placements and crew and group not in spawned_names and group not in scripts
              and Decimal(crew[0]) < Decimal(action['delaySeconds'])):
            existing_points = ' '.join(f'"{point}"' for point in crew[1])
        else:
            gaps.add('expected one placed AI group named ' + group)
            continue
        for name in action['waypoints']:
            waypoints = named.get(name, [])
            if len(waypoints) != 1 or not re.fullmatch(WAYPOINT_CLASS, waypoints[0][0]):
                gaps.add('expected one placed AI waypoint named ' + name)
            elif action['replaceExisting'] and f'"{name}"' in existing_points:
                gaps.add(f'replacement waypoint {name} may already belong to {group}')
    return sorted(gaps), sorted(no_ops)


def reviewed_spawner_links(source, world):
    """Recognize authored orders that only ever name a legacy spawner entity."""
    if world.endswith('/BreakingIntoNovaOlesov.ent'):
        layer = world.removesuffix('.ent') + '_Layers/OPFOR_Players/OPFOR_AI.layer'
        if source.files.get(layer) == 'b630da96a8fa2f37cdc72a9eb27c797f6c2bb2ff':
            return {f'BMPSpawner_{i}' for i in range(1, 8)} | {f'T72Spawner_{i}' for i in range(1, 4)}
    return set()


def static_link_gaps(layers, named):
    fields = {'m_aStaticWaypoints': WAYPOINT_CLASS, 'm_aStaticVehicles': '(?:Vehicle|Tank)'}
    gaps = []
    for layer, text in layers.items():
        for field, targets in re.findall(r'(?m)^ +(?P<field>m_aStatic(?:Waypoints|Vehicles)) \{\n((?: +"[^\n]*\n)*)', text):
            name = STATIC_VEHICLE_NAME if field == 'm_aStaticVehicles' else r'"([\w.-]+)"'
            for target in set(filter(None, re.findall(name, targets))):
                matches = named.get(target, [])
                if len(matches) != 1 or not re.fullmatch(fields[field], matches[0][0]):
                    gaps.append((layer, field, target))
    return sorted(set(gaps))


def marker_edit_gaps(named, edits):
    gaps = set()
    for edit in edits:
        for name in edit['markers']:
            placements = named.get(name, [])
            if len(placements) != 1 or not any(line.lstrip().startswith('GC_BriefingMarkerComponent ')
                                               for line in placements[0][1]):
                gaps.add('expected one placed briefing marker named ' + name)
                continue
            restricted = any(line.strip() in ('m_aFactionKeys {', 'm_bRestrictToListedFactions 1')
                             for line in placements[0][1])
            if not restricted and not all(edit['factions'].values()):
                gaps.add('legacy public marker cannot hide one assigned faction: ' + name)
    return sorted(gaps)


NATIVE_VEHICLE_OVERRIDES = re.compile(
    rf'(?:Vehicle|Tank) : "\{{[\dA-Fa-f]{{16}}\}}Prefabs/Vehicles/[^"\n]+\.et" \{{\n'
    rf' ID "[\dA-Fa-f]{{16}}"\n'
    rf'(?: components \{{\n'
    rf'(?:  SCR_EditableVehicleComponent {SERIALIZED_ID} \{{\n'
    rf'   m_UIInfo SCR_Editable(?:Vehicle|Entity)UIInfo {SERIALIZED_ID} \{{\n'
    rf'(?:    m_a(?:Authored|Auto)Labels \{{\n     [A-Z0-9_]+(?: [A-Z0-9_]+)*\n    \}}\n'
    rf'|    m_EntityBudgetCost \{{\n     SCR_EntityBudgetValue {SERIALIZED_ID} \{{\n'
    rf'      m_Value \d+\n     \}}\n    \}}\n)'
    rf'   \}}\n  \}}\n)?'
    rf'(?:  SCR_FuelManagerComponent {SERIALIZED_ID} \{{\n   FuelNodes \{{\n'
    rf'    SCR_FuelNode {SERIALIZED_ID} \{{\n     m_fInitialFuelTankState {NUMBER}\n'
    rf'    \}}\n   \}}\n  \}}\n)?'
    rf' \}}\n)?(?: {FIELD.pattern}\n)*\}}'
)
REVIEWED_LOCKED_M113 = {
    'worlds/Blue/Bangalorious/Data/M113_APC_LOCKED.et':
        ('ccdb45f9107caa5d70e3d92402f2abd5d6d522fa5117529caceb569ec782af6a', ''),
    'worlds/Blue/Bangalorious/Data/M2HB_LOCKED.et':
        ('50d848fd6fea260904624ca33b67647a9ee227da83086723ecbc378fbbc071cc', '   m_bIsLocked 1\n'),
}


SKALISTY_FALLBACK_AO = 'worlds/HitTheMan/Air Assault Onto Skalisty/Prefab/AO_Limit_BLU_Main.et'


def skalisty_fallback_ao(text):
    if sha256(text.strip().encode()).hexdigest() != 'fd6e51bd43e320f3320b6f836e59a8b6d1e9d8daf124a729753f5ea250d22d1e':
        raise ValueError('fallback AO changed since review')
    legacy = text.splitlines()
    rendered, vertices = convert_boundary_instance(legacy[2:-1], '', extended_shapes=True, reviewed_crossing_ao=True)
    parts = [block for _, block in blocks(rendered)]
    if vertices != 24 or len(parts) != 2 or not parts[1][0].startswith('GC_AOLimitEntity '):
        raise ValueError('expected a fallback limit with a separate public map fill')
    fill, limit = parts
    fill_component = fill[2:block_end(fill, 1)]
    if not fill_component or '   m_bInvertFill 1' not in fill_component:
        raise ValueError('expected a public inverted fallback fill')
    # Both map components read the same points and follow the same spawned entity lifetime.
    limit[2:2] = fill_component
    limit.insert(1, legacy[1])
    return '\n'.join(limit) + '\n'


OIL_RIG_CREW_VARIANTS = {
    'Character_CIV_ConstructionWorker_P_1.et': 'ef405968479df5f42c164b3d5f5d83f9591d97c2',
    'Character_CIV_ConstructionWorker_P_2.et': 'f9beadad1e10ae16eae5c3788e78a32b07f9a083',
}
OIL_RIG_CREW_PREFABS = 'worlds/TheLocalPub/OilRigRangos/Prefabs/Characters/Factions/CIV/ConstructionWorker/'
OIL_RIG_FACTION = 'worlds/TheLocalPub/OilRigRangos/Configs/Factions/ECOTERROR.conf'
OIL_RIG_DELETED_GUNNER = 'D8F7C335536E5786'


CLEAN_HOUSE = 'worlds/TheLocalPub/CleanHouse/'
CLEAN_HOUSE_PROTECTED_ASSETS = {
    'Structures/Houses/Prefabricated/ApartmentBuilding_5I01/Pub_ApartmentBuilding_USSR_5I01.et':
        '4afa030b6237ac5a460d441b5526d2bcb3e0ef62',
    'Weapons/MachineGuns/PKM/Pub_MG_PKM_mounted_PKDeleter.et': '9135ccc40ca4e13fe2b2350e49600cf3373ae1a7',
    'Weapons/Magazines/Pub_Box_762x54_PK_100rnd_4Ball_1Tracer_PkDeleter.et': '7408ed1fbe8ca58f69bfbbd809d443e15fd1ba38',
}
CLEAN_HOUSE_RANGE_TARGET = ('379D34B0D41DA810', 'Prefabs/Props/Military/Training/TargetInfantry_01.et')

STASHED_CACHE_PREFABS = 'worlds/HelloCam3/Prefabs/'
STASHED_CACHE_PINK_VARIANTS = {
    'Props/Civilian/hc3_RadioSmall_01_Pink.et': '509ab070cba50f36bee3becebd7f65f4a30781b9',
    'Vehicles/Wheeled/S105/hc3_S105_rally_pink.et': '9e3ea0da4b4e487acf2b27864fd44c05830f5a23',
    'Weapons/Magazines/hc3_Magazine_545x39_AK_30rnd_Pink_Ball.et': '3830239668340f1c985d9f0c61eb18beda0b8196',
    'Weapons/Rifles/AK74/Variants/hc3_Rifle_AK74N_Pink_PBS4.et': '71c9d954e96e84602d9c43c0db701c73fa62c856',
    'Weapons/Rifles/AKS74U/Variants/hc3_Rifle_AKS74UN_Pink_PBS4.et': '2a4c9ff7a63d45c87a7bdefb56477cdf340a546c',
}
STASHED_CACHE_MATERIALS = {
    'worlds/HelloCam3/Assets/Props/Civilian/RadioSmall_01/Data/hc3_RadioSmall_01_Pink.emat':
        '2e6571e618bdb5b838068a5fc9788c074d526aa4',
    'worlds/HelloCam3/Assets/Vehicles/Wheeled/S105/Data/hc3_S105Body_mat_pink.emat':
        'c82337040d1f756742ceaca1ef7b32bc022fa0ad',
    'worlds/HelloCam3/Assets/Vehicles/Wheeled/S105/Data/hc3_S105Body_mat_pink_int.emat':
        'a2744e4bac3739073f86c112c67fb4caa5cfd10e',
}
STASHED_CACHE_GRIP_MATERIAL = 'worlds/HelloCam3/Assets/Weapons/Rifles/AK74/Data/hc3_AK74_Grip_Pink.emat'
STASHED_CACHE_GRIP_TEXTURE = STASHED_CACHE_GRIP_MATERIAL.removesuffix('.emat') + '_BCR.edds'
STASHED_CACHE_PLAYER_LAYER = ('worlds/HelloCam3/worlds/COOP_StashedCache/'
                              'COOP_StashedCache_v2_Layers/playergroups.layer')
STASHED_CACHE_MODE_BLOB = 'b43214648b8ca258155563b28c291f0f259de0cf'
STASHED_CACHE_NATIVE_MODE = {
    'RHS_AdditionalGameModeSettingsComponent', 'RHS_RadialMenuItemsComponent',
    'SCR_CampaignBuildingManagerComponent', 'SCR_DataCollectorComponent',
    'SCR_EntityCatalogManagerComponent', 'SCR_GroupsManagerComponent', 'SCR_HintManagerComponent',
    'SCR_IdentityManagerComponent', 'SCR_NametagConfigComponent', 'SCR_NotificationSenderComponent',
    'SCR_PerceivedFactionManagerComponent', 'SCR_RadialMenuGameModeComponent',
    'SCR_RadialMenuItemsComponent', 'SCR_TextsTaskManagerComponent',
}
STASHED_CACHE_INHERITED_MODE = {
    'SCR_GameModeHealthSettings', 'SCR_GameOverScreenManagerComponent',
    'SCR_VotingManagerComponent', 'RplComponent',
}
STASHED_CACHE_LOBBY_MODE = {'SCR_PreGameGameModeStateComponent', 'SCR_ReconnectComponent',
                           'SCR_RespawnSystemComponent'}
STASHED_CACHE_COMMAND = 'Characters/Factions/OPFOR/USSR_Army/Naval_Infantry/'
STASHED_CACHE_COMMAND_ROLES = {
    'FBFAD3A8C271B0FC': ('hc3_Character_USSR_NI_CoyL.et', '1bd3a46ccf2eb4d1b9b224202138534f8918fe2d'),
    'A454CAB60FF72E52': ('hc3_Character_USSR_NI_CSM.et', '326329aa3b514d392f6eb932cb9b471fe48311c2'),
}


def passive_asset(source, path):
    if source.files[path] == STASHED_CACHE_MATERIALS.get(path):
        return source.read(path)
    if (path.startswith(STASHED_CACHE_PREFABS)
            and source.files[path] == STASHED_CACHE_PINK_VARIANTS.get(path.removeprefix(STASHED_CACHE_PREFABS))):
        # Keep the authored native radio interaction, vehicle and inventory variants.
        return source.read(path)
    if (path.startswith(STASHED_CACHE_PREFABS + STASHED_CACHE_COMMAND)
            and (PurePosixPath(path).name, source.files[path]) in STASHED_CACHE_COMMAND_ROLES.values()):
        return source.read(path)
    if (aircraft := REVIEWED_AIRCRAFT.get(path)) and source.files[path] == aircraft[0]:
        body = source.read(path)
        if aircraft[1]:
            lines, flag = damage_state_flag_placement(body.splitlines(), allow_multiple_states=True)
            if flag != aircraft[1]:
                raise ValueError('unexpected aircraft damage flag')
            body = '\n'.join(lines) + '\n'
        return re.sub(r'(?m)^( +)WCS_ArmamentWeapon(Group|Info) ', r'\1WCS_Armament_Weapon\2 ', body)
    if path.startswith(CLEAN_HOUSE + 'Prefabs/') and source.files[path] == CLEAN_HOUSE_PROTECTED_ASSETS.get(
            path.removeprefix(CLEAN_HOUSE + 'Prefabs/')):
        body = '\n'.join(migrate_native_components(source.read(path).splitlines())) + '\n'
        if LEGACY.search(body):
            raise ValueError('unconverted cleanup protection in mission prefab')
        return body
    if source.files.get(BEBBLE_OBJECTIVES) == BEBBLE_OBJECTIVES_BLOB:
        if path == BEBBLE_HVT and source.files[path] == BEBBLE_HVT_BLOB:
            # The only placement supplies the effective HVTDead observer.
            return source.read(path).replace(BEBBLE_AI_ONLY, '', 1).replace(
                '  TILW_Flag_EntityDamage "{65AD042580CF7A6F}" {\n   m_flagName "HVT"\n  }\n', '', 1)
        if path == BEBBLE_FUEL and source.files[path] == BEBBLE_FUEL_BLOB:
            return source.read(path).replace('  TILW_Flag_EntityDamage "{65DA917B80071341}" {\n  }\n', '', 1)
    if (path == OIL_RIG_FACTION and source.files[path] == 'f0d3f76d2cf7ba98a5e5510f7225861eb405d262'
            and OIL_RIG_DELETED_GUNNER not in source.resources):
        # The publisher removed this variant from the addon and roster but left its catalog entry.
        body = source.read(path).replace(' m_bShowInWelcomeScreenIfNonPlayable 0\n', '', 1)
        return re.sub(r'(?m)^    SCR_EntityCatalogEntry "\{6677556ECD1DB618\}" \{\n'
                      rf'     m_sEntityPrefab "\{{{OIL_RIG_DELETED_GUNNER}\}}[^"\n]+"\n    \}}\n', '', body, count=1)
    if (path == SUMMER_DAYS_TOPO and source.files[path] == 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391'
            and source.files.get(path + '.meta') == 'ad93c07b9a7e51f489692ab26ec3860a6cfead54'):
        # The published archive also contains the empty map topology resource.
        return b''
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == '.edds':
        return source.read_raw(path)
    if suffix == '.imageset':
        return source.read(path)
    if path in ROUTE_OBSTACLE_PREFABS:
        return route_obstacle_prefab(source, path)[0]
    if (path.startswith(OIL_RIG_CREW_PREFABS)
            and source.files[path] == OIL_RIG_CREW_VARIANTS.get(path.removeprefix(OIL_RIG_CREW_PREFABS))
            and source.files.get(OIL_RIG_CREW_PREFABS + 'Character_CIV_ConstructionWorker_Randomized_P.et')
                == '04df79ac0e169eb24f806031d379a81ae6e60513'):
        # These variants are reached through the native randomized character. Their
        # legacy component only repeats the display name and leaves playability false.
        body = source.read(path)
        return body.replace('  PS_PlayableComponent "{5EFCC1946F475F15}" {\n   m_sName "Crew member"\n  }\n', '', 1)
    if path in REVIEWED_NATIVE_ASSETS:
        body = source.read(path)
        if sha256(body.strip().encode()).hexdigest() == REVIEWED_NATIVE_ASSETS[path] and not LEGACY.search(body):
            if path == RASMAN_AA_GROUP:
                return body.replace('\n m_sCustomNameSet ', '\n m_sGC_Description ', 1)
            return body
    if suffix == '.nmn' and source.files[path] == OBSOLETE_NAVMESH.get(path):
        tiles = len(source.navmesh_tiles(path))
        detail = f'Workbench rejected the compiled navmesh as obsolete. Found {tiles} original tile files'
        if observed := source.legacy_navmesh_region(path):
            region, bounds = observed
            min_x, max_x, min_y, max_y, count = region
            detail += f' and {count} populated grid cells at X {min_x}–{max_x}, Y {min_y}–{max_y}'
            if bounds:
                from_x, from_z, to_x, to_z = bounds
                detail += f', world X/Z ({from_x:g}, {from_z:g}) to ({to_x:g}, {to_z:g})'
        raise ValueError(detail + '. Regeneration needed.')
    if suffix == '.emat':
        body = source.read(path)
        if re.match(r'MatPBR(?:Basic|Decal) \{\n', body) and not LEGACY.search(body):
            return body
        if (path == 'worlds/Blue/Bangalorious/Data/ErodedEmbankment_01_EMPTY_grass.emat'
                and sha256(body.strip().encode()).hexdigest()
                == '55f8a99c8737ac091d599e6f9dd4ce38aa47a7e58dc7af389298dda89ba22b84'):
            return body
    if suffix == '.et':
        body = source.read(path)
        if path.startswith(SEEDER_OBJECTIVE_ROOT):
            expected = SEEDER_OBJECTIVE_ASSETS.get(path.removeprefix(SEEDER_OBJECTIVE_ROOT))
            if expected and source.files.get(path) == expected:
                if LEGACY_MARKER.fullmatch(body.splitlines()[0]):
                    return seeder_named_marker(body)
                native, _ = convert_mission_names(body)
                if not LEGACY.search(native):
                    return native
        if path in SEEDER_RECON_BLOBS:
            blob, flag = SEEDER_RECON_BLOBS[path]
            if source.files.get(path) != blob or not seeder_recon_flags_unused(source):
                raise ValueError('reviewed recon vehicle damage flag changed')
            return '\n'.join(remove_unused_damage_flag(body.splitlines(), flag)) + '\n'
        if path.startswith(SEEDER_REWARD_DIR):
            expected = SEEDER_REWARD_BLOBS.get(path.removeprefix(SEEDER_REWARD_DIR))
            if expected and source.files.get(path) == expected:
                converted, _ = convert_mission_names(body)
                if not LEGACY.search(converted):
                    return converted
        if path.startswith(SEEDER_REPLACEMENT_DIR):
            blob = SEEDER_CARGO_BLOBS.get(path.removeprefix(SEEDER_REPLACEMENT_DIR))
            if blob and source.files.get(path) == blob and not LEGACY.search(body):
                return body
        if path in SEEDER_VEHICLE_CHILDREN:
            return seeder_vehicle_child(source, path)
        if path == SKALISTY_FALLBACK_AO:
            return skalisty_fallback_ao(body)
        if path in REVIEWED_SPAWNED_MARKERS:
            if sha256(body.strip().encode()).hexdigest() != REVIEWED_SPAWNED_MARKERS[path]:
                raise ValueError('spawned marker needs review')
            return spawned_marker_prefab(source, body)
        if path in REVIEWED_LOCKED_M113:
            digest, native_lock = REVIEWED_LOCKED_M113[path]
            if sha256(body.strip().encode()).hexdigest() != digest:
                raise ValueError('locked M113 prefab changed')
            return body.replace('   m_lockPilot 1\n   m_lockTurret 1\n', native_lock)
        if path == BLOODY_GARRISON_PREFAB:
            if any(source.files.get(file) != blob for file, blob in BLOODY_GARRISON_BLOBS.items()):
                raise ValueError('garrison resource or placement changed')
            return garrison_anchors(body.splitlines(), 1)
        if path in REVIEWED_GROUP_CALLSIGNS:
            return reviewed_callsign_group(source, path)
        if path in REVIEWED_PLAYABLE_GROUPS:
            return playable_group_prefab(body, path)
        if path == BLACKBOX[1]:
            return blackbox_prefab(body)
        if STORAGE_BOX.fullmatch(body.rstrip('\n')):
            return body
        lines = body.splitlines()
        material = MATERIAL_OVERRIDE.fullmatch(body.rstrip('\n'))
        if material and all(FIELD.fullmatch(line.strip()) for line in material['transform'].splitlines()):
            return body
        if NATIVE_VEHICLE_OVERRIDES.fullmatch(body.rstrip('\n')):
            return body
        if (len(lines) > 8 and lines[0] == LIGHT and lines[2:7] == LIGHT_COMPONENT and lines[-1] == '}'
                and all(line.startswith(' ') and line.strip().partition(' ')[0] in LIGHT_FIELDS
                        and '{' not in line and '}' not in line for line in [lines[1], *lines[7:-1]])):
            return body
    raise ValueError('requires a resource conversion rule')


def convert_mission_names(body):
    return re.subn(
        rf'(?m)^( +)TILW_SetNameComponent ({SERIALIZED_ID}) \{{\n\1 m_setName ("[\w.-]+")\n\1\}}$',
        r'\1GC_MissionEntityNameComponent \2 {\n\1 m_sMissionName \3\n\1}', body)


def seeder_named_marker(body):
    lines = body.splitlines()
    stop = block_end(lines, 2)
    naming, count = convert_mission_names('\n'.join(lines[3:stop]))
    if count != 1 or LEGACY.search(naming):
        raise ValueError('unsupported objective marker name')
    native = marker_instance(lines[stop + 1:-1], '', draw_order=True).splitlines()
    stop = block_end(native, 1)
    native[stop:stop] = naming.splitlines()
    native.insert(1, lines[1])
    return '\n'.join(native) + '\n'


SEEDER_FACTION_AREA = re.compile(
    r'TILW_FactionTriggerEntity(?: [\w.-]+)? : "\{8C576D46F6579229\}'
    r'Prefabs/Logic/Triggers/TILW_FactionTrigger.et" \{')


def seeder_objective_area(path, block):
    components = block.index(' components {')
    component_end = block_end(block, components)
    children = block.index(' {', component_end) if ' {' in block[component_end:] else len(block) - 1
    identity = block[1]
    fields = [*block[2:components], *block[component_end + 1:children]]
    names = [line for line in block[components:component_end] if line.startswith('   m_sMissionName ')]
    if len(names) != 1 or ' ID ' not in identity:
        raise ValueError('objective capture area needs a named prefab identity')
    name = names[0].split('"')[1]
    checks, area, details = capture_instance(path, name, fields)
    flag, condition = next(iter(checks.items()))
    observer = render_area_flag_observer(path, name, flag, condition, details['stopsAfterFirstChange'])
    observer[0] = observer[0].replace('GC_AreaFlagObserver ', 'm_Observer GC_AreaFlagObserver ', 1)
    component = [f'  GC_BoundAreaFlagComponent "{{{guid(path + "/bound area/" + name)}}}" {{',
                 *(line[1:] for line in observer), '  }']
    native = area.splitlines()
    native[1:1] = [identity, *block[components:component_end], *component, block[component_end]]
    native[-1:-1] = block[children:-1]
    return native, {'name': name, 'flag': flag[1:-1], 'condition': condition, **details}


def seeder_objective_outline(block):
    components = block.index(' components {')
    component_end = block_end(block, components)
    setter = next(i for i in range(components, component_end)
                  if block[i].startswith('  GC_MissionEntityNameComponent '))
    setter_end = block_end(block, setter)
    fields = [*block[2:setter], *block[setter_end + 1:-1]]
    original_name = AO_SHAPE.fullmatch(block[0])['name']
    name = ' ' + original_name if original_name else ''
    native, count = convert_boundary_instance(fields, name, extended_shapes=True, nested=True)
    if native.count('\n}\n') or not native.startswith('PolylineShapeEntity'):
        raise ValueError('objective overlay needs one passive map shape')
    lines = native.splitlines()
    start = lines.index(' components {')
    stop = block_end(lines, start)
    lines[stop:stop] = block[setter:setter_end + 1]
    lines.insert(1, block[1])
    return lines, count


def seeder_objective_markers(source, block):
    root = LEGACY_MARKER.fullmatch(block[0])
    inherited, default_naming = {}, []
    if root['guid'].upper() != LEGACY_MARKER_BASE:
        path = root['path']
        expected = SEEDER_OBJECTIVE_ASSETS.get(path.removeprefix(SEEDER_OBJECTIVE_ROOT))
        if not expected or source.files.get(path) != expected or source.resources.get(root['guid']) != [path]:
            raise ValueError('unverified objective marker template')
        template, _ = convert_mission_names(source.read(path))
        lines = template.splitlines()
        stop = block_end(lines, 2)
        inherited = properties(lines[stop + 1:-1], MARKER_FIELDS)
        default_naming = lines[3:stop]
    markers = []
    count = 0
    for name, fields in polygon_capture.members(block, root['group'], root['name']):
        if not fields or not re.fullmatch(r' ID "[\da-fA-F]{16}"', fields[0]):
            raise ValueError('objective marker needs an authored identity')
        identity = fields[0]
        fields = fields[1:]
        naming = default_naming
        if ' components {' in fields:
            start = fields.index(' components {')
            stop = block_end(fields, start)
            naming = fields[start + 1:stop]
            fields = [*fields[:start], *fields[stop + 1:]]
        cursor = 0
        while cursor < len(naming):
            if not naming[cursor].startswith(('  GC_MissionEntityNameComponent ',
                                              '  GC_DestroyedEntityFlagComponent ')):
                raise ValueError('unsupported objective marker component override')
            cursor = block_end(naming, cursor) + 1
        native = marker_instance(fields, ' ' + name if name else '', inherited, draw_order=True).splitlines()
        stop = block_end(native, native.index(' components {'))
        native[stop:stop] = naming
        native.insert(1, identity)
        markers.extend(native)
        count += 1
    return markers, count


def seeder_objective_spawners(block, path):
    """Replace legacy spawner members in place, keeping their child waypoint trees."""
    root = placed_spawner_root(block[0], allow_bare=False)
    if not root or not root['group']:
        raise ValueError('unexpected objective spawner container: ' + path)
    result = ['$grp GenericEntity {']
    review = []
    for index, (name, lines) in enumerate(polygon_capture.members(block, True, '')):
        key = f'{path}/spawner/{index}'
        retained, authored, crew = [], [], None
        cursor = 0
        while cursor < len(lines):
            line = lines[cursor]
            end = block_end(lines, cursor) if line.endswith('{') else cursor
            chunk = lines[cursor:end + 1]
            if line.startswith(' m_crewConfig '):
                if crew is not None:
                    raise ValueError('duplicate objective crew configuration: ' + key)
                crew = crew_config_contents(chunk)
            elif line.startswith(' m_'):
                authored.extend(chunk)
            else:
                retained.extend(chunk)
            cursor = end + 1
        data = properties(authored, {'m_prefab', 'm_conditionFlag', 'm_spawnTimings',
                                     'm_setEntityNames', 'm_waypointNames'})
        prefab = REF.fullmatch(data.get('m_prefab', ''))
        flag = data.get('m_conditionFlag')
        delays = data.get('m_spawnTimings', '').split()
        entity_name = data.get('m_setEntityNames', '""')
        waypoints = target_names(data['m_waypointNames'], skip_blank=True) if data.get('m_waypointNames') else []
        if (not prefab or flag not in ('"VehicleSpawner"', '"SpawnReinforcements"')
                or len(delays) > 128
                or any(not re.fullmatch(NUMBER, delay) or not 0 <= Decimal(delay) <= 86400 for delay in delays)
                or not re.fullmatch(SPAWN_NAME, entity_name) or (len(delays) > 1 and entity_name != '""')):
            raise ValueError('unsupported objective spawner: ' + key)
        fields = []
        crew_groups = []
        if crew is not None:
            kind = 'GC_CrewedVehicleSpawn'
            # The draft retains the original references for subsequent active-graph review.
            fields, crew_groups = render_crew_config(
                crew, key, lambda resource, path: f'"{{{resource}}}{path}"', use_available_initial_waypoints=True)
            fields = ['    ' + line[8:] for line in fields]
        elif prefab[2].startswith('Prefabs/Groups/'):
            kind = 'GC_AIGroupSpawn'
            if waypoints:
                fields = ['    m_aWaypoints {', *('     ' + point for point in waypoints), '    }']
        else:
            raise ValueError('unverified objective spawn type: ' + key)
        component = [f'  GC_MissionAnchorSpawnerComponent "{{{guid(key)}}}" {{',
                     '   m_sRequiredFlag ' + flag]
        if delays:
            component.extend(['   m_aDelaySeconds {', '    ' + ' '.join(delays), '   }'])
        component.extend([f'   m_Spawn {kind} "{{{guid(key + "/spawn")}}}" {{',
                          '    m_sPrefab ' + prefab[0]])
        if entity_name != '""':
            component.append('    m_sEntityName ' + entity_name)
        component.extend([*fields, '   }', '  }'])
        if ' components {' in retained:
            start = retained.index(' components {')
            stop = block_end(retained, start)
            retained[stop:stop] = component
        else:
            start = retained.index(' {') if ' {' in retained else len(retained)
            retained[start:start] = [' components {', *component, ' }']
        result.extend([f' {name} {{' if name else ' {', *(' ' + line for line in retained), ' }'])
        review.append({'anchor': name, 'flag': flag[1:-1], 'spawnDelaysSeconds': delays or ['0'],
                       'prefab': prefab[0], 'spawnName': entity_name[1:-1],
                       'waypoints': [] if crew is not None else [point[1:-1] for point in waypoints],
                       'crewGroups': crew_groups})
    return [*result, '}'], review


def seeder_objective_prefabs(source):
    """Prepare the shared authored geometry and observations for the seven rotating objectives."""
    assets = {}
    if source.files.get(SEEDER_REPLACEMENT_LAYER) != SEEDER_REPLACEMENT_LAYER_BLOB:
        return assets
    scripts = script_text(source)
    corpus = '\n'.join(source.read(path) for path in source.closure(SEEDER_WORLD)
                       if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    can_omit_cleanup = (not any(api in scripts for api in SCRIPT_FLAG_APIS)
                       and all(name not in scripts and corpus.count(f'"{name}"') == count
                               for name, count in (('ClearAI', 4), ('ClearAISide', 3), ('Deleter', 7))))
    for relative, expected in SEEDER_OBJECTIVE_BLOBS.items():
        path = SEEDER_OBJECTIVE_DIR + relative
        if source.files.get(path) != expected:
            raise ValueError('reviewed objective composition changed: ' + path)
        body, name_count = convert_mission_names(source.read(path))
        body, empty_controls = re.subn(rf'(?m)^( +)TILW_ControlModeFlag {SERIALIZED_ID} \{{\n\1\}}\n', '', body)
        lines = body.splitlines()
        damage_flags = []
        for index in range(len(lines) - 1, -1, -1):
            line = lines[index]
            indent = len(line) - len(line.lstrip()) - 2
            if indent < 0 or not DAMAGE_COMPONENT.fullmatch(line[indent:]):
                continue
            stop = block_end(lines, index)
            component = [text[indent:] for text in lines[index:stop + 1]]
            native, flag = damage_state_flag_placement(
                ['{', ' components {', *component, ' }', '}'],
                allow_multiple_states=True, clear_on_mismatch=True)
            lines[index:stop + 1] = [' ' * indent + field for field in native[2:-2]]
            damage_flags.append(flag)
        overlays = []
        for start, stop, indent, outline in reversed(list(nested_placement_blocks(lines, AO_SHAPE))):
            native, points = seeder_objective_outline(outline)
            lines[start:stop + 1] = [' ' * indent + field for field in native]
            overlays.append(points)
        markers = 0
        for start, stop, indent, marker in reversed(list(nested_placement_blocks(lines, LEGACY_MARKER))):
            native, count = seeder_objective_markers(source, marker)
            lines[start:stop + 1] = [' ' * indent + field for field in native]
            markers += count
        cleanup_members = []
        objective_spawners = []
        for start, stop, indent, spawners in reversed(list(nested_placement_blocks(lines, PLACED_SPAWNER))):
            if not spawners[0].startswith('$grp '):
                continue
            for index in range(len(spawners) - 2, 0, -1):
                if spawners[index] != ' Deleter {':
                    continue
                end = block_end(spawners, index)
                fields = spawners[index + 1:end]
                prefab = next((REF.search(field) for field in fields if field.startswith('  m_prefab ')), None)
                flag = next((field.split('"')[1] for field in fields if field.startswith('  m_conditionFlag "')), '')
                if (not can_omit_cleanup or not prefab or prefab[1] not in ('3D54A6583D027631', '4FF97BC050F82106')
                        or flag not in ('ClearAI', 'ClearAISide')
                        or '  {' in fields or '  m_pregameSpawn 1' in fields):
                    raise ValueError('unverified objective cleanup spawner')
                cleanup_members.append(next(field.strip() for field in fields if field.startswith('  ID ')))
                del spawners[index:end + 1]
            native, spawner_review = seeder_objective_spawners(spawners, path)
            objective_spawners.extend(spawner_review)
            lines[start:stop + 1] = [' ' * indent + field for field in native]
        areas = []
        for start, stop, indent, trigger in reversed(list(nested_placement_blocks(lines, SEEDER_FACTION_AREA))):
            native, details = seeder_objective_area(path, trigger)
            lines[start:stop + 1] = [' ' * indent + field for field in native]
            areas.append(details)
        native, details = seeder_objective_area(path, lines)
        areas.insert(0, details)
        body = '\n'.join(native) + '\n'
        remaining = Counter(LEGACY.findall(body))
        assets[path] = {'body': body, 'sha256': sha256(body.encode()).hexdigest(),
                        'names': name_count, 'emptyControlObservers': empty_controls,
                        'damageFlags': sorted(damage_flags), 'areas': areas, 'overlayVertices': overlays,
                        'markers': markers,
                        'omittedCleanupMembers': cleanup_members,
                        'spawners': objective_spawners,
                        'spawnerFlagReaders': dict(sorted(Counter(item['flag'] for item in objective_spawners).items())),
                        'remainingLegacy': dict(sorted(remaining.items()))}
    return assets


def seeder_native_assets(source, dev, source_units, dev_units, prefabs):
    if not prefabs or source.files.get(SEEDER_OFFICER) != SEEDER_OFFICER_BLOB:
        raise ValueError('objective prefab dependencies need review')
    aliases = {}
    original = 'Prefabs/Characters/Factions/OPFOR/GC_SEP/1990/'
    current = original.replace('/GC_SEP/', '/GC_SEPARATIST/')
    for resource, role in SEEDER_CHARACTER_PATHS.items():
        filename = f'Character_SEP_1990_{role}.et'
        path = current + filename
        if (source_units.resources.get(resource) != [path] or dev_units.resources.get(resource) != [path]
                or source_units.files[path] != dev_units.files[path]):
            raise ValueError(f'objective crew dependency changed: {filename}')
        aliases[(resource, original + filename)] = (resource, path)
    officer, flag = damage_state_flag_placement(source.read(SEEDER_OFFICER).splitlines(), clear_on_mismatch=True)
    if flag != 'SideObjectiveOfficer':
        raise ValueError('objective officer death flag changed')
    bodies = {path: item['body'] for path, item in prefabs.items()}
    bodies[SEEDER_OFFICER] = '\n'.join(officer) + '\n'
    assets = {}
    for path, body in bodies.items():
        registration = META_NAME.search(source.read(path + '.meta'))
        resource = registration[2].upper() if registration else None
        if source.resources.get(resource) != [path] or dev.resources.get(resource) not in (None, [path]):
            raise ValueError('objective prefab registration needs review: ' + path)
        body = replace_registered_paths(body, source, dev, aliases)
        if LEGACY.search(body) or (path in dev.files and body.strip() != dev.read(path).strip()):
            raise ValueError('unconverted or differing objective prefab: ' + path)
        assets[resource] = {'path': path, 'body': body}
    return assets


CONCRETE_COMMANDER = 'worlds/HotMic/Prefabs/Characters/Objectives/RHS_RF_Commander.et'
CONCRETE_COMMANDER_BLOB = '783f8662c5733dbd27709998c4c75ad5e5de8a48'
CONCRETE_COMMANDER_ID = 'C36FA3DD3507C03E'
CONCRETE_COMMANDER_FLAG = 'RHS RF Commander Killed Flag'


def concrete_commander(source, dev, rhs):
    path = CONCRETE_COMMANDER
    resource = CONCRETE_COMMANDER_ID
    if (source.files.get(path) != CONCRETE_COMMANDER_BLOB or source.resources.get(resource) != [path]
            or dev.resources.get(resource) not in (None, [path])):
        raise ValueError('Concrete Crushers commander identity changed')
    block = source.read(path).splitlines()
    parent = REF.search(block[0])
    if not rhs or not parent or not rhs.contains(*parent.groups()):
        raise ValueError('Concrete Crushers commander parent is unavailable')
    lines, flag = damage_state_flag_placement(block)
    body = '\n'.join(lines) + '\n'
    if (flag != CONCRETE_COMMANDER_FLAG or LEGACY.search(body)
            or (path in dev.files and body.strip() != dev.read(path).strip())):
        raise ValueError('Concrete Crushers commander damage state needs review')
    return {resource: {'path': path, 'body': body}}


@lru_cache(maxsize=4)
def seeder_recon_flags_unused(source):
    if (source.files.get(SEEDER_REPLACEMENT_LAYER) != SEEDER_REPLACEMENT_LAYER_BLOB
            or source.files.get(SEEDER_PLAYER_VEHICLES) != 'c3da94fe6583b635ef99a100b023a5fe2be32393'
            or any(source.files.get(path) != blob for path, (blob, _) in SEEDER_RECON_BLOBS.items())):
        return False
    scripts = script_text(source)
    if any(api in scripts for api in SCRIPT_FLAG_APIS) or any(flag in scripts for flag in ('Rec1', 'Rec2')):
        return False
    corpus = '\n'.join(source.read(path) for path in source.closure(SEEDER_WORLD)
                        if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    return corpus.count('"Rec1"') == 1 and corpus.count('"Rec2"') == 2


def remove_unused_damage_flag(block, flag):
    matches = [i for i, line in enumerate(block) if DAMAGE_COMPONENT.fullmatch(line)
               and block[i + 1:i + 3] == [f'   m_flagName "{flag}"', '  }']]
    if len(matches) != 1:
        raise ValueError('expected one reviewed dormant damage observer')
    index = matches[0]
    return [*block[:index], *block[index + 3:]]


def remove_legacy_callsigns(lines):
    result = []
    index = 0
    while index < len(lines):
        if CALL.fullmatch(lines[index]):
            index = block_end(lines, index) + 1
        else:
            result.append(lines[index])
            index += 1
    return '\n'.join(result) + '\n'


def reviewed_callsign_group(source, path):
    prefab_blob, layer, layer_blob, indices, _ = REVIEWED_GROUP_CALLSIGNS[path]
    if source.files.get(path) != prefab_blob or source.files.get(layer) != layer_blob:
        raise ValueError('reviewed callsign prefab or placement changed')
    body = remove_legacy_callsigns(source.read(path).splitlines())
    body = body.replace('\n m_bSetPlayable 1\n', '\n')
    body = body.replace('\n m_sCustomNameSet ', '\n m_sGC_Description ', 1)
    if path == LOST_AND_FOUND_DELTA_GROUP:
        body = body.replace('}Prefabs/Characters/Factions/BLUFOR/RHS_USAF/Delta Force/2010/P/',
                            '}Prefabs/Characters/Factions/BLUFOR/RHS_USAF/2009/Delta/P/')
    fields = [' m_bPlayable 1', *(f' {field} "{value}"' for field, value in zip(INDEX.values(), indices, strict=True))]
    return '\n'.join([*body.splitlines()[:-1], *fields, '}', ''])


def playable_group_prefab(body, path):
    if sha256(body.strip().encode()).hexdigest() != REVIEWED_PLAYABLE_GROUPS[path] or LEGACY.search(body):
        raise ValueError('playable group prefab needs review')
    if path == ROCKY_CIVILIANS:
        return body.replace('\n}', '\n m_bPlayable 1\n}', 1)
    return body.replace('\n m_bSetPlayable 1\n', '\n m_bPlayable 1\n').replace(
        '\n m_sCustomNameSet ', '\n m_sGC_Description ')


def spawned_marker_prefab(source, body):
    root, identity, *fields = body.splitlines()
    marker, _ = convert_layer('\n'.join([root, *fields]) + '\n', draw_order=True, source=source)
    head, rest = marker.split('\n', 1)
    return '\n'.join([head, identity, rest])


def reviewed_villa_convoy(source, dev, source_units, dev_units, loadouts):
    if source.files.get(VILLA_CONVOY_LAYER) != VILLA_CONVOY_LAYER_BLOB:
        return {}
    parent = 'Prefabs/Groups/OPFOR/Russia/1980s/MotorRifle_MSV80/Group_USSR_MSV80_MotRifleSquad.et'
    original = ('Prefabs/Groups/OPFOR/Russia/1980s/Motorised Rifles (BTR-Early)/P/'
                'Group_USSR_MSV80_MotRifleSquad_P.et')
    group_id = '7544247CE7FBC839'
    if any(snapshot.resources.get(group_id) != [parent] or snapshot.files.get(parent) != blob
           for snapshot, blob in ((source_units, 'c48ef10d43bb4021854289a3ef55d8624b38fae8'),
                                  (dev_units, '927cdcdb7558631932885635c1ee3ea63871c03c'))):
        return {}
    assets = {}
    for resource, (name, blob, squad) in VILLA_CONVOY_GROUPS.items():
        path = VILLA_CONVOY_PREFABS + name + '.et'
        old = VILLA_OLD_CONVOY_PREFABS + name + '.et'
        if (source.files.get(path) != blob or source.resources.get(resource) != [path]
                or dev.resources.get(resource) not in (None, [path])):
            return {}
        body = source.read(path)
        head = REF.search(body.partition('\n')[0])
        slots = SLOTS.search(body)
        if slots:
            if head.groups() != (group_id, original):
                return {}
            members = REF.findall(slots[1])
            if len(members) != 3 or any(
                    guid not in loadouts or not authored.startswith(VILLA_OLD_CONVOY_PREFABS)
                    or loadouts[guid][0] != VILLA_CONVOY_PREFABS + PurePosixPath(authored).name
                    for guid, authored in members):
                return {}
            body = body.replace(head[0], f'"{{{group_id}}}{parent}"', 1)
            body = REF.sub(lambda ref: f'"{{{ref[1]}}}{loadouts[ref[1]][0]}"'
                          if ref.groups() in members else ref[0], body)
            count = len(members)
        else:
            predecessor = assets.get(head[1], {})
            if head[2] != predecessor.get('legacyPath'):
                return {}
            body = body.replace(head[0], f'"{{{head[1]}}}{predecessor["path"]}"', 1)
            count = predecessor['startupSlots']
        body = remove_legacy_callsigns(body.splitlines()).replace(' components {\n }\n', '')
        body = body.replace('\n m_sCustomNameSet ', '\n m_sGC_Description ', 1)
        fields = [' m_bPlayable 1', *(f' {field} "{value}"' for field, value in
                                      zip(INDEX.values(), (0, 0, squad), strict=True))]
        native = '\n'.join([*body.splitlines()[:-1], *fields, '}', ''])
        if path in dev.files and dev.read(path).strip() != native.strip():
            return {}
        assets[resource] = {'path': path, 'legacyPath': old, 'body': native, 'startupSlots': count}
    resource, name, blob = VILLA_CONVOY_MARKER
    path = VILLA_CONVOY_PREFABS + name
    if (source.files.get(path) != blob or source.resources.get(resource) != [path]
            or dev.resources.get(resource) not in (None, [path])):
        return {}
    native = spawned_marker_prefab(source, source.read(path))
    if path in dev.files and dev.read(path).strip() != native.strip():
        return {}
    assets[resource] = {'path': path, 'legacyPath': VILLA_OLD_CONVOY_PREFABS + name, 'body': native}
    return assets


def verified_local_ai_group(source, dev, source_units, dev_units, stock, resource, path, external_ai,
                            local_ai, rhs=None):
    if (not stock or path not in REVIEWED_NATIVE_ASSETS or source.resources.get(resource) != [path]
            or dev.resources.get(resource) not in (None, [path])
            or resource in source_units.resources or resource in dev_units.resources):
        return False
    try:
        body = passive_asset(source, path)
    except ValueError:
        return False
    if (not body.startswith(('SCR_AIGroup : ', 'SCR_AIGroup {')) or (path in dev.files and body != dev.read(path))):
        return False
    parent = REF.search(body.partition('\n')[0])
    roster = SLOTS.search(body)
    members = set(REF.findall(roster[1])) if roster else set()
    owned = source.resources.keys() | dev.resources.keys()
    refs = {*REF.findall(body), ('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et')}
    for guid, asset in refs:
        if guid in owned:
            if (guid, asset) not in members or local_ai.get(guid, (None,))[0] != asset:
                return False
            continue
        if guid not in source_units.resources and guid not in dev_units.resources:
            if not (stock.contains(guid, asset) or external_ai.get(guid) == asset
                    or (members and parent and parent.groups() == (guid, asset)
                        and verified_stock_ai_group(guid, asset, stock, owned, source_units, dev_units,
                                                    rhs, inherited_slots=False))):
                return False
            continue
        if source_units.resources.get(guid) != [asset] or dev_units.resources.get(guid) != [asset]:
            return False
        if parent and parent.groups() == (guid, asset):
            try:
                compare_ai_parents(source_units, dev_units, guid)
            except ValueError:
                return False
        else:
            before = inheritance(source_units, guid)
            after = inheritance(dev_units, guid)
            if (not before[0].startswith('SCR_ChimeraCharacter') or legacy_member(source_units, guid, False)
                    or not (reviewed_native_ancestry(before, after, dev_units)
                            or reviewed_ai_member_ancestry(guid, before, after, dev_units))):
                return False
    return True


def reviewed_rocky_civilian(source, dev, source_units, dev_units, stock, guid, path):
    if (guid, path) != ('A339CCBF0627477D', 'Prefabs/Characters/Factions/CIV/Afghan/P/Civ_Random_P.et'):
        return False
    if source_units.resources.get(guid) != [path] or dev_units.resources.get(guid) != [path]:
        return False
    variants = [REF.fullmatch(line.strip().removeprefix('m_sVariantPrefab '))
                for line in source_units.read(path).splitlines() if line.strip().startswith('m_sVariantPrefab ')]
    stock_parent = ('37578B1666981FCE', 'Prefabs/Characters/Core/Character_Base.et')
    if len(variants) != 3 or not all(variants) or not stock.contains(*stock_parent):
        return False
    for member, member_path in [(guid, path), *(variant.groups() for variant in variants)]:
        if (member in source.resources or member in dev.resources
                or source_units.resources.get(member) != [member_path] or dev_units.resources.get(member) != [member_path]
                or not unchanged_explicit_playability(source_units, dev_units, member, True)):
            return False
        parent = REF.search(inheritance(dev_units, member)[-1].partition('\n')[0])
        if not parent or parent.groups() != stock_parent:
            return False
    return True


def reviewed_group_aliases(source, dev, source_units, dev_units, stock, reviewed_members, rhs=None):
    aliases = {}
    for path, digest in (REVIEWED_NATIVE_ASSETS | REVIEWED_PLAYABLE_GROUPS | REVIEWED_GROUP_CALLSIGNS).items():
        if path not in source.files or path + '.meta' not in source.files:
            continue
        body = source.read(path)
        playable = path in REVIEWED_PLAYABLE_GROUPS or path in REVIEWED_GROUP_CALLSIGNS
        if not playable and not re.fullmatch(r'SCR_AIGroup : "\{[\dA-Fa-f]{16}\}[^"\n]+" \{\n ID "[\dA-Fa-f]{16}"\n\}\s*', body):
            continue
        try:
            if path in REVIEWED_GROUP_CALLSIGNS:
                native = reviewed_callsign_group(source, path)
            elif sha256(body.strip().encode()).hexdigest() == digest:
                native = playable_group_prefab(body, path) if playable else body
            else:
                continue
        except ValueError:
            continue
        identity = META_NAME.search(source.read(path + '.meta'))
        parent, parent_path = REF.search(body.partition('\n')[0]).groups()
        if (not identity or source.resources.get(identity[2].upper()) != [path]
                or any(identity[2].upper() in item.resources for item in (source_units, dev_units))
                or dev.resources.get(identity[2].upper()) not in (None, [path])
                or (path in dev.files and dev.read(path) != native)):
            continue
        if path == LOST_AND_FOUND_DELTA_GROUP:
            owned = source.resources.keys() | dev.resources.keys()
            if not verified_stock_ai_group(parent, parent_path, stock, owned, source_units, dev_units, rhs,
                                           inherited_slots=False):
                continue
            roles = REF.findall(SLOTS.search(native)[1])
            if any(reviewed_members.get(guid) != member and (
                    guid in owned or dev_units.resources.get(guid) != [member]
                    or not legacy_member(source_units, guid, False)
                    or not unchanged_units_parent(source_units, dev_units, guid)) for guid, member in roles):
                continue
        elif path == LOST_AND_FOUND_PILOT_GROUP:
            delta = aliases.get(parent)
            roles = REF.findall(SLOTS.search(native)[1])
            if not delta or delta[0] != parent_path or any(reviewed_members.get(guid) != member
                                                           for guid, member in roles):
                continue
        elif playable:
            slots = SLOTS.search(body)
            if (not stock or not stock.contains(parent, parent_path) or not slots
                    or any(parent in snapshot.resources for snapshot in (source, dev, source_units, dev_units))
                    or any(reviewed_members.get(guid) != member and (
                        path != ROCKY_CIVILIANS or not reviewed_rocky_civilian(
                            source, dev, source_units, dev_units, stock, guid, member))
                           for guid, member in REF.findall(slots[1]))):
                continue
        elif (source_units.resources.get(parent) != [parent_path]
                or dev_units.resources.get(parent) != [parent_path]
                or not reviewed_native_ancestry(inheritance(source_units, parent),
                                                inheritance(dev_units, parent), dev_units)):
            continue
        aliases[identity[2].upper()] = (path, parent, (body, native) if playable else None)
    return aliases


def reviewed_oil_rig_group(source, dev, source_units, dev_units, loadouts):
    resource = '558D7D02AE68982F'
    path = 'worlds/TheLocalPub/OilRigRangos/Prefabs/Groups/OPFOR/ECO forces/Group_ECO_RifleTeam_P.et'
    parent, parent_path = UK_1989_RIFLE_SECTION
    if (source.resources.get(resource) != [path] or source.files.get(path) != '183f4e56be96f6ac6d928aa5cdb86f1c8da0ddef'
            or dev.resources.get(resource) not in (None, [path]) or parent in source.resources or parent in dev.resources
            or any(resource in snapshot.resources or snapshot.resources.get(parent) != [parent_path]
                   for snapshot in (source_units, dev_units))
            or source_units.files.get(parent_path) != UK_1989_RIFLE_SECTION_BLOBS[0]
            or dev_units.files.get(parent_path) not in UK_1989_RIFLE_SECTION_BLOBS):
        return {}, {}
    old = source.read(path)
    if any(loadouts.get(member, (None,))[0] != name for member, name in REF.findall(SLOTS.search(old)[1])):
        return {}, {}
    old_parent = parent_path.replace('/Light Infantry', '')
    native = old.replace(f'{{{parent}}}{old_parent}', f'{{{parent}}}{parent_path}', 1)
    native = native.replace(' m_sCustomNameSet ""\n', ' m_bPlayable 1\n', 1)
    if path in dev.files and dev.read(path) != native:
        return {}, {}
    return {resource: (path, native)}, {resource: (path, parent, (old, native))}


PUBLIC_ENEMIES = 'worlds/TheLocalPub/PublicEnemies/'
PUBLIC_ENEMIES_GROUPS = {
    '0DEBAC606FB62737': ('Blufor/Group_Federal_base.et', 'cd37b0e26504acbe6abaa860226602671346384f', None),
    '2A325FD7A29810CD': ('Opfor/Group_Mobster_Base.et', '09d2fd173ea725d1706e4fc1607231738e6bf02f', None),
    '05C8C3FD535E3087': ('Blufor/Group_Federal_CaptainTeam.et', '31216e35ed31f1988e95f6ee187672d1b3a85942', '0DEBAC606FB62737'),
    '35CB89A86E6851DB': ('Blufor/Group_Federal_StandardTeam.et', '50225d881c06e18ceafc33c518bd1a5f26abdafb', '0DEBAC606FB62737'),
    'FAE41331EAAB0399': ('Blufor/Group_Federal_ChiefTeam.et', 'a9d0487ca5aaf29281b0a2bb2ac1033d9298d3ca', '0DEBAC606FB62737'),
    '1CE33BB24C41F43C': ('Opfor/Group_Mobster_GreyMobsters1.et', 'f2921961b7d3c2f1163af25d591b656cc8e73624', '2A325FD7A29810CD'),
    '9115FB4624E4329C': ('Opfor/Group_Mobster_BrownMobsters1.et', 'd7518064178990b82b66197b6b65cb34440cfc25', '2A325FD7A29810CD'),
}
PUBLIC_ENEMIES_PLAYERS = 'PublicEnemies_Layers/OPFOR/Player.layer'
PUBLIC_ENEMIES_BOSSES = {
    # Grey also appears in the group's default roster, replaced by the placed roster.
    'FB3DF47EABBAE7CF': ('6be396106379f16195e36a855072675e107b203f', '4144099566E3DE33', '1CE33BB24C41F43C', 2),
    'E18FFF2A7564502A': ('5679ca8db278bbdc1924872b8c34354b822989a3', '8F51ED5F532BB1E1', '9115FB4624E4329C', 1),
}


def public_enemies_groups(source, dev, source_units, dev_units, stock, loadouts, bosses):
    directory = PUBLIC_ENEMIES + 'Prefabs/AI/Groups/'
    stock_group = ('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et')
    if (not stock or not stock.contains(*stock_group)
            or any(stock_group[0] in snapshot.resources for snapshot in (source, dev, source_units, dev_units))
            or bosses.keys() != PUBLIC_ENEMIES_BOSSES.keys()):
        return {}, {}
    members = {**{resource: entry[0] for resource, entry in loadouts.items()},
               **{resource: entry['path'] for resource, entry in bosses.items()}}
    assets, aliases = {}, {}
    for resource, (relative, blob, parent) in PUBLIC_ENEMIES_GROUPS.items():
        path = directory + relative
        if (source.resources.get(resource) != [path] or source.files.get(path) != blob
                or dev.resources.get(resource) not in (None, [path])
                or any(resource in snapshot.resources for snapshot in (source_units, dev_units))):
            return {}, {}
        old = source.read(path)
        if parent is None:
            native = old.replace(' m_bSetPlayable 1\n', ' m_bPlayable 1\n', 1)
        else:
            slots = SLOTS.search(old)
            if (not slots or REF.sub('', slots[1]).strip()
                    or any(members.get(slot) != name for slot, name in REF.findall(slots[1]))):
                return {}, {}
            native = REF.sub(lambda _: f'"{{{parent}}}{assets[parent][0]}"', old, count=1)
            # The saved leaf inherits its playable setting from the base.
            aliases[resource] = (path, parent, (old.rstrip()[:-1] + ' m_bSetPlayable 1\n}',
                                               native.rstrip()[:-1] + ' m_bPlayable 1\n}'))
        if LEGACY.search(native) or (path in dev.files and dev.read(path).strip() != native.strip()):
            return {}, {}
        assets[resource] = (path, native)
    return assets, aliases


def reviewed_southwind_groups(source, dev, source_units, dev_units, stock, ai_loadouts):
    """Retain the mission's two native group prefabs and review all forced roles."""
    root = 'worlds/anteante101/Prefabs/group/FIA Rebel/'
    paths = {
        'C2BE7BE6B80386FF': (root + 'Group_FIA_REBEL_AA101.et',
                             '74955e3fea709345952f98196c62bd74781d986c7ef5567d06f99f47fd50e213'),
        '4A6E8E54916F2A29': (root + 'Group_FIA_REBEL_squad_AA101.et',
                             'bae06f30e35fb74d0e4934c8ab48d1844ff49455f039a629848c99cc70df6a6b'),
    }
    layer = 'worlds/anteante101/South wind is blowing/South wind is blowing_Layers/fia.layer'
    if not stock or source.files.get(layer) != '71ce428cc9a24fe9fc3ccfdd777137679608e532':
        return {}, {}, {}
    parent = '3E19B428E9137511'
    parent_path = 'Prefabs/Groups/INDFOR/FIA/FIA_Modern/Group_FIAModern_RifleSquad.et'
    if any(snapshot.resources.get(parent) != [parent_path]
           or snapshot.files.get(parent_path) != '983615501d8011bc2e318bdaeeaa52450a951e02'
           for snapshot in (source_units, dev_units)):
        return {}, {}, {}
    assets = {}
    for resource, (path, digest) in paths.items():
        if (source.resources.get(resource) != [path] or dev.resources.get(resource) not in (None, [path])
                or resource in source_units.resources or resource in dev_units.resources):
            return {}, {}, {}
        body = source.read(path)
        if sha256(body.strip().encode()).hexdigest() != digest or LEGACY.search(body):
            return {}, {}, {}
        assets[resource] = (path, body)

    resource = 'C2BE7BE6B80386FF'
    path, old = assets[resource]
    local_parent = '4A6E8E54916F2A29'
    native = old.replace('worlds/anteante101/Prefabs/group/Group_FIA_REBEL_squad_AA101.et',
                         assets[local_parent][0], 1)
    assets[resource] = (path, native)
    if any(path in dev.files and dev.read(path) != body for path, body in assets.values()):
        return {}, {}, {}

    members = {}
    for identity, name in set(REF.findall(source.read(layer) + old + assets[local_parent][1])):
        if (identity, name) in ((resource, path), (parent, parent_path)) or identity == local_parent:
            continue
        if identity in ('677B515F119222C2', 'FE65E8C60C751352'):
            if (not stock.contains(identity, name)
                    or any(identity in snapshot.resources for snapshot in (source, dev, source_units, dev_units))):
                return {}, {}, {}
        elif ai_loadouts.get(identity, (None,))[0] != name:
            # References outside the groups are consumed by their own reviewed spawner or placement.
            if not name.startswith('worlds/anteante101/Prefabs/characther/FIA_REBEL/'):
                continue
            return {}, {}, {}
        members[identity] = name
    return assets, {resource: (path, parent, (old, native))}, members


def damage_state_flag_placement(block, *, allow_multiple_states=False, clear_on_mismatch=False):
    lines, flags = damage_state_flag_placements(block, allow_multiple_states=allow_multiple_states,
                                                clear_on_mismatch=clear_on_mismatch)
    if len(flags) != 1:
        raise ValueError('expected one authored damage producer')
    return lines, flags[0]


def damage_state_flag_placements(block, *, allow_multiple_states, clear_on_mismatch=False, verified_zones=()):
    starts = [i for i, line in enumerate(block) if DAMAGE_COMPONENT.fullmatch(line)]
    if not starts or block.count(' components {') != 1:
        raise ValueError('expected authored damage producers in root components')
    root = block.index(' components {')
    end = block_end(block, root)
    allowed = {'0', 'UNDAMAGED', '1', 'INTERMEDIARY', '2', 'DESTROYED'} if allow_multiple_states else {'2', 'DESTROYED'}
    lines, flags = [], []
    cursor = 0
    for start in starts:
        stop = block_end(block, start)
        if not root < start < stop < end or start < cursor:
            raise ValueError('damage producer is outside root components')
        data = properties(block[start + 1:stop], {'m_flagName', 'm_targetState', 'm_targetZone',
                                                    'm_continuous', 'm_initialHealth'})
        flag = data.get('m_flagName', '')
        state = data.get('m_targetState', '2')
        continuous = data.get('m_continuous', '0')
        zone = data.get('m_targetZone', '""')
        health = data.get('m_initialHealth', '1')
        if (not re.fullmatch(FLAG_NAME, flag) or len(flag.encode()) > 130 or state not in allowed
                or (zone != '""' and zone not in verified_zones) or continuous not in ('0', '1')
                or (continuous == '1' and not allow_multiple_states)
                or (health != '1' and (not verified_zones or not re.fullmatch(NUMBER, health)
                                       or not 0 <= Decimal(health) <= 1))):
            raise ValueError('unsupported destruction state or flag')
        lines.extend(block[cursor:start])
        lines.extend([block[start].replace('TILW_Flag_EntityDamage', 'GC_DestroyedEntityFlagComponent'),
                      '   m_sFlag ' + flag])
        if state not in ('2', 'DESTROYED'):
            lines.append('   m_eTargetState ' + state)
        if continuous == '1':
            lines.append('   m_bTrackChanges 1')
        elif clear_on_mismatch or verified_zones:
            lines.append('   m_bClearOnMismatch 1')
        if verified_zones:
            if zone != '""':
                lines.append('   m_sHitZoneName ' + zone)
            if health != '1':
                lines.append('   m_fInitialHealth ' + health)
            lines.append('   m_bDeferInitialSample 1')
        flags.append(flag[1:-1])
        cursor = stop
    return [*lines, *block[cursor:]], flags


def spawned_damage_objective(block, stock, local_resources, workshop_targets, *, vehicle=False,
                             replacement=False, reviewed_overrides=False, rhs=None):
    parent = REF.search(block[0])
    owner_type = 'Vehicle' if vehicle else 'GenericEntity'
    if not parent or not block[0].startswith(owner_type + ' : '):
        raise ValueError('unsupported spawned damage owner')
    ref = (parent[1].upper(), parent[2])
    approved = SPAWN_DAMAGE_VEHICLES if vehicle else SPAWN_DAMAGE_TARGETS
    if replacement:
        approved = approved | SEEDER_REPLACEMENT_PARENTS
    registered_stock = ref in approved and (
        stock and stock.contains(*ref))
    registered_workshop = not vehicle and ref in GMFX_AMMO_TARGETS and ref in workshop_targets
    if replacement and ref in SEEDER_RHS_REPLACEMENT_PARENTS:
        registered_workshop = rhs and rhs.contains(*ref)
    if ref[0] in local_resources or not (registered_stock or registered_workshop):
        raise ValueError('unverified spawned damage prefab')
    if vehicle and not reviewed_overrides:
        if (len(block) < 8 or not re.fullmatch(r' ID "[\da-fA-F]{16}"', block[1])
                or block[2] != ' components {' or not DAMAGE_COMPONENT.fullmatch(block[3])):
            raise ValueError('spawned damage vehicle has other authored overrides')
        end = block_end(block, 3)
        tail = block[end + 1:]
        if (len(tail) == 3 and ref == CAMPAIGN_RADIO_TRUCK and tail[1].startswith(' coords ')
                and FIELD.fullmatch(tail[1].strip())):
            tail = [tail[0], tail[-1]]
        if (replacement and len(tail) == 4 and tail[1].startswith(' coords ')
                and tail[2].startswith(' angles ')):
            if all(FIELD.fullmatch(line.strip()) for line in tail[1:3]):
                tail = [tail[0], tail[-1]]
        if tail != [' }', '}']:
            raise ValueError('spawned damage vehicle has other authored overrides')
    # A replacement vehicle observes its initial state and can clear a flag left by the previous instance.
    lines, flag = damage_state_flag_placement(block, allow_multiple_states=vehicle, clear_on_mismatch=vehicle)
    if replacement:
        index = lines.index('   m_bClearOnMismatch 1')
        lines.insert(index + 1, '   m_bDeferInitialSample 1')
    return '\n'.join(lines) + '\n', flag


def seeder_vehicle_child(source, path):
    if source.files.get(path) != SEEDER_VEHICLE_CHILDREN[path]:
        raise ValueError('reviewed replacement vehicle child changed')
    lines = source.read(path).splitlines()
    name = next(index for index, line in enumerate(lines) if 'TILW_SetNameComponent ' in line)
    naming = [lines[name].replace('TILW_SetNameComponent', 'GC_MissionEntityNameComponent'),
              lines[name + 1].replace('m_setName', 'm_sMissionName'), '  }']
    component_end = block_end(lines, 2)
    fields = lines[component_end + 1:-1]
    if path == SEEDER_COMMAND_ANCHOR:
        transform = [line for line in fields if FIELD.fullmatch(line.strip())]
        return '\n'.join(['GenericEntity {', lines[1], ' components {', *naming, ' }', *transform, '}', ''])
    marker = marker_instance(fields, '', draw_order=True).splitlines()
    component_end = block_end(marker, 2)
    marker[component_end:component_end] = ['   m_bTrackTransform 1']
    if path != SEEDER_MI8_MARKER:
        marker[component_end + 2:component_end + 2] = naming
    marker.insert(1, lines[1])
    return '\n'.join(marker) + '\n'


def reviewed_replacement_vehicle(source, resource, path, corpus):
    """Verify one initial placement and the complete destruction-to-replacement loop."""
    buildable = path == SEEDER_BUILDABLE_MI8
    if (not (buildable or path.startswith(SEEDER_REPLACEMENT_DIR))
            or source.files.get(SEEDER_REPLACEMENT_LAYER) != SEEDER_REPLACEMENT_LAYER_BLOB):
        return None
    relative = path.removeprefix(SEEDER_REPLACEMENT_DIR)
    truck_blob = SEEDER_TRUCK_BLOBS.get(relative)
    if buildable:
        if (source.files.get(path) != SEEDER_BUILDABLE_MI8_BLOB
                or source.files.get(SEEDER_MI8_MARKER) != SEEDER_VEHICLE_CHILDREN[SEEDER_MI8_MARKER]):
            return None
    elif truck_blob:
        if source.files.get(path) != truck_blob:
            return None
    elif not re.fullmatch(r'Transports/(?:UAZ|UAZ452_ambulance|UAZ452_transport)[1-3]\.et', relative):
        return None
    name = 'Mi8MT_armed1' if buildable else PurePosixPath(path).stem
    fields = re.findall(r'(?m)^   m_flagName "([\w.-]+)"$', source.read(path))
    if len(fields) != 1:
        return None
    flag = fields[0]
    event_name = 'Respawn ' + flag.removeprefix('Resp')
    anchor = flag if flag.startswith('Resp') else 'Resp' + flag
    resource_ref = f'"{{{resource}}}{path}"'
    owner = f'Vehicle {name} : {resource_ref} {{'
    initial = f'           m_sEntityPrefab {resource_ref}' if buildable else owner
    if corpus.splitlines().count(initial) != 1 or corpus.count(resource_ref) != 2:
        return None
    events = source.read(SEEDER_REPLACEMENT_LAYER).splitlines()
    # The flags UAZ1 through UAZ3 also occur as the entity names in the
    # pinned delete and spawn instructions for the separate UAZ4–UAZ6 loops.
    entity_uses = 0
    if name.startswith('UAZ452_transport'):
        entity_uses = events.count(f'      "{flag}"') + events.count(f'     m_setEntityName "{flag}"')
    if anchor == flag:
        entity_uses += events.count(f'     m_locationName "{anchor}"')
    if buildable:
        entity_uses += source.read(SEEDER_MI8_MARKER).splitlines().count(f'   m_setName "{flag}"')
    if corpus.count(f'"{flag}"') != literal_flag_reads(corpus, flag) + 2 + entity_uses:
        return None
    for start, line in enumerate(events):
        if not EVENT.fullmatch(line):
            continue
        fields = events[start + 1:block_end(events, start)]
        if f'   m_name "{event_name}"' not in fields:
            continue
        if (f'     m_prefab {resource_ref}' in fields and f'     m_setEntityName "{name}"' in fields
                and f'     m_locationName "{anchor}"' in fields and f'      "{name}"' in fields
                and fields.count(f'      "{event_name}"') == 1):
            replacement = {'replacementEntity': name, 'replacementEvent': event_name, 'flag': flag}
            if path == SEEDER_COMMAND_TRUCK:
                if any(source.files.get(child) != SEEDER_VEHICLE_CHILDREN[child]
                       for child in (SEEDER_COMMAND_MARKER, SEEDER_COMMAND_ANCHOR)):
                    return None
                replacement['attachedTeleportDestination'] = 'TPTruck'
            if buildable:
                replacement['initialBuildCatalog'] = True
            return replacement
    return None


def authored_spawn_references(source, world):
    for layer in source.layers[world.lower()]:
        source_layer = source.read(layer)
        lines = source_layer.splitlines()
        if 'TILW_PrefabSpawnerEntity ' in source_layer:
            for _, _, block in placed_spawner_entries(source, layer, source_layer):
                if root := PLACED_SPAWNER.fullmatch(block[0]):
                    try:
                        placements = polygon_capture.members(block, root['group'], root['name'], allow_quoted=True)
                    except ValueError:
                        # A later file check still reports unsupported placements. The
                        # reference count prevents partial coverage for a damage prefab.
                        continue
                    for _, fields in placements:
                        for field in fields:
                            if field.startswith(' m_prefab ') and (ref := REF.fullmatch(field.removeprefix(' m_prefab '))):
                                yield ref[1].upper(), ref[2], False
        for index, line in enumerate(lines):
            vehicle = bool(VEHICLE_SPAWN.fullmatch(line))
            if not vehicle and not SPAWN.fullmatch(line):
                continue
            try:
                fields = lines[index + 1:block_end(lines, index)]
                if vehicle:
                    _, fields = split_crew_block(fields, CREW_CONFIG_FIELD)
                values = properties(fields, SPAWN_FIELDS)
                if prefab := REF.fullmatch(values.get('m_prefab', '')):
                    yield prefab[1].upper(), prefab[2], vehicle
            except ValueError:
                continue


def spawned_boundary_prefab(rendered, count, expected, identity):
    overlay, limit = (block for _, block in blocks(rendered))
    if (count != expected or not overlay[0].startswith('PolylineShapeEntity ')
            or not limit[0].startswith('GC_AOLimitEntity ')):
        raise ValueError('spawned boundary needs its reviewed limit and map overlay')
    # Both drawings share the AO entity, transform and lifetime.
    limit[2:2] = overlay[2:block_end(overlay, 1)]
    limit.insert(1, identity)
    return '\n'.join(limit) + '\n'


def reviewed_iron_cipher_assets(source, dev, world, stock):
    if (world != IRON_CIPHER + 'OperationArrowheadIronCipher.ent'
            or source.files.get(IRON_CIPHER_LAYERS + 'default.layer') != IRON_CIPHER_DEFAULT_BLOB
            or source.files.get(IRON_CIPHER_LAYERS + 'SYSTEMS/Triggers/IntelSpawns.layer')
                != '6ada30e7d895c3f661ba1994b300e50622512b89'):
        return {}
    refs = Counter((resource, path) for resource, path, _ in authored_spawn_references(source, world))
    assets = {}
    for (resource, path, blob), expected in ((IRON_CIPHER_INTEL, 9), (IRON_CIPHER_AO, 1), (IRON_CIPHER_MARKER, 1)):
        if (source.files.get(path) != blob or source.resources.get(resource) != [path]
                or dev.resources.get(resource) not in (None, [path]) or refs[resource, path] != expected):
            return {}
        legacy = source.read(path).splitlines()
        if resource == IRON_CIPHER_INTEL[0]:
            parent = REF.search(legacy[0])
            if not stock or not stock.contains(*parent.groups()):
                return {}
            body = source.read(path)
        elif resource == IRON_CIPHER_AO[0]:
            # The standard TilW AO inherits a disabled cover component and an enabled limit.
            rendered, count = convert_boundary_instance(legacy[2:-1], '', extended_shapes=True)
            body = spawned_boundary_prefab(rendered, count, 12, legacy[1])
        else:
            fields = '\n'.join([legacy[0], *legacy[2:]]) + '\n'
            marker = convert_layer(fields, dev, draw_order=True)[0].splitlines()
            marker.insert(1, legacy[1])
            body = '\n'.join(marker) + '\n'
        if LEGACY.search(body) or (path in dev.files and dev.read(path).strip() != body.strip()):
            return {}
        assets[resource] = {'path': path, 'body': body}
    return assets


def reviewed_villa_escape(source, dev, world):
    if (VILLA_DEFAULT not in source.layers[world.lower()]
            or source.files.get(VILLA_DEFAULT) != VILLA_DEFAULT_BLOB):
        return {}
    refs = Counter((resource, path) for resource, path, _ in authored_spawn_references(source, world))
    for resource, path, blob in (VILLA_ESCAPE_TRIGGER, VILLA_ESCAPE_BOUNDARY):
        if (source.files.get(path) != blob or source.resources.get(resource) != [path]
                or dev.resources.get(resource) not in (None, [path]) or refs[resource, path] != 1):
            return {}
    closure = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                        if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    scripts = script_text(source)
    if (closure.count('"BluforEscaped"') != 3 or literal_flag_reads(closure, 'BluforEscaped') != 2
            or 'BluforEscaped' in scripts or any(api in scripts for api in SCRIPT_FLAG_APIS)):
        return {}
    resource, path, _ = VILLA_ESCAPE_TRIGGER
    legacy = source.read(path).splitlines()
    component_end = block_end(legacy, 2)
    checks, area, details = capture_instance(path, 'EscapeAOTrigger', [' coords 0 0 0', *legacy[component_end+1:-1]])
    flag, condition = next(iter(checks.items()))
    observer = render_area_flag_observer(path, 'EscapeAOTrigger', flag, condition, details['stopsAfterFirstChange'])
    observer[0] = observer[0].replace('GC_AreaFlagObserver ', 'm_Observer GC_AreaFlagObserver ', 1)
    component = [f'  GC_BoundAreaFlagComponent "{{{guid(path + "/bound area")}}}" {{',
                 *(line[1:] for line in observer), '  }']
    native = area.splitlines()
    native[1:1] = [legacy[1], *legacy[2:component_end], *component, legacy[component_end]]
    assets = {resource: {'path': path, 'body': '\n'.join(native) + '\n', 'flag': flag[1:-1],
                         'boundArea': True, 'audit': details}}

    resource, path, _ = VILLA_ESCAPE_BOUNDARY
    legacy = source.read(path).splitlines()
    fields = '\n'.join(legacy[2:-1])
    # The disabled editor-only cover contributes no framing. The native AO supplies replication.
    fields = fields.replace('  TILW_CoverMapComponent "{66EC24A8B99D8E4D}" {\n   Enabled 0\n  }\n', '')
    fields = fields.replace('  RplComponent "{66EC24A8B99D8E81}" {\n   Streamable Disabled\n  }\n', '')
    rendered, count = convert_boundary_instance(fields.splitlines(), '', extended_shapes=True, standalone=True, bare=True)
    assets[resource] = {'path': path, 'body': spawned_boundary_prefab(rendered, count, 32, legacy[1])}
    return assets


def spawned_objectives(source, dev, world, stock, local_resources, workshop_targets=(), *, rhs=None):
    """Identify mission-owned objective prefabs referenced by a standard spawn."""
    reviewed = {}
    placements = Counter()
    corpus = None
    for resource, path, vehicle in authored_spawn_references(source, world):
        placements[resource] += 1
        if (resource in reviewed or source.resources.get(resource) != [path]
                or dev.resources.get(resource) not in (None, [path]) or not path.lower().endswith('.et')):
            continue
        text = source.read(path)
        if 'TILW_BaseInteraction' not in text and 'TILW_Flag_EntityDamage' not in text:
            continue
        try:
            (_, block), = blocks(text)
            if 'TILW_Flag_EntityDamage' in text:
                vehicle = vehicle or block[0].startswith('Vehicle : ')
                replacement = None
                if vehicle and SEEDER_REPLACEMENT_LAYER in source.layers[world.lower()]:
                    if corpus is None:
                        corpus = '\n'.join(source.read(owner) for owner in sorted(source.closure(world))
                                           if PurePosixPath(owner).suffix.lower() in SOURCE_TEXT)
                    replacement = reviewed_replacement_vehicle(source, resource, path, corpus)
                reviewed_overrides = (replacement and (path == SEEDER_BUILDABLE_MI8
                    or path.removeprefix(SEEDER_REPLACEMENT_DIR) in SEEDER_TRUCK_BLOBS))
                if (path == BEBBLE_AMMO and source.files[path] == BEBBLE_AMMO_BLOB and not vehicle
                        and stock and stock.contains(*BEBBLE_AMMO_BASE) and BEBBLE_AMMO_BASE[0] not in local_resources):
                    lines, flag = damage_state_flag_placement(block)
                    native = '\n'.join(lines) + '\n'
                else:
                    native, flag = spawned_damage_objective(block, stock, local_resources, workshop_targets,
                                                            vehicle=vehicle, replacement=bool(replacement), rhs=rhs,
                                                            reviewed_overrides=bool(reviewed_overrides))
                kind = {'destroyed': True, **({'vehicle': True} if vehicle else {})}
                if replacement:
                    if flag != replacement['flag']:
                        continue
                    kind.update(replacement)
                    if 'attachedTeleportDestination' in replacement:
                        native = native.replace(SEEDER_REPLACEMENT_DIR + 'CommandTruckMarkers.et',
                                                SEEDER_COMMAND_MARKER, 1)
                    if replacement.get('initialBuildCatalog'):
                        native = native.replace('worlds/HitTheMan/Seeder Domination/Prefabs/Respawn Vehicles/Helicopter1Markers.et',
                                                SEEDER_MI8_MARKER, 1)
                    attachment = re.search(r'(?m)^ m_sAttachmentGroupName "([^"\n]+)"$', text)
                    if attachment:
                        kind['legacyAttachmentGroup'] = attachment[1]
            else:
                if vehicle:
                    continue
                native, objectives = convert_interaction_block(block, path, stock, local_resources)
                if len(objectives) != 1:
                    continue
                flag = objectives[0]['flag']
                kind = {'singleUse': objectives[0]['singleUse']}
            if LEGACY.search(native) or (not kind.get('destroyed') and path in dev.files
                                         and native.strip() != dev.read(path).strip()):
                continue
        except ValueError:
            continue
        reviewed[resource] = {'path': path, 'body': native, 'flag': flag, **kind}
    damaged = {resource: asset for resource, asset in reviewed.items() if asset.get('destroyed')}
    if damaged:
        if corpus is None:
            corpus = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                               if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
        scripts = script_text(source)
        for resource, asset in damaged.items():
            flag = asset['flag']
            uses = placements[resource]
            replacement = 'replacementEntity' in asset
            if ((not replacement and corpus.count(f'"{flag}"') != literal_flag_reads(corpus, flag) + 1)
                    or flag in scripts
                    or any(api in scripts for api in SCRIPT_FLAG_APIS)
                    or corpus.upper().count('{' + resource + '}') != uses + int(replacement)):
                del reviewed[resource]
            else:
                asset['spawnCount'] = uses
    return reviewed


CIVILIAN_AI_GROUP = ('DF688F0BC937BFC7', 'Prefabs/Groups/CIV/Group_CIV_Base.et')


def verified_civilian_ai_group(local_resources, source_units, dev_units):
    resource, path = CIVILIAN_AI_GROUP
    return (resource not in local_resources
            and all(snapshot.resources.get(resource) == [path] for snapshot in (source_units, dev_units))
            and source_units.read(path) == dev_units.read(path))


def villa_commander(source, dev, world, stock, local_resources, source_units, dev_units):
    resource, path, blob = VILLA_COMMANDER
    officer = ('5117311FB822FD1F', 'Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Officer.et')
    if (world != VILLA + 'StealthingIntoVilla.ent'
            or source.files.get(VILLA_DEFAULT) != VILLA_DEFAULT_BLOB
            or source.files.get(VILLA_OPFOR) != VILLA_OPFOR_BLOB
            or source.files.get(path) != blob or source.resources.get(resource) != [path]
            or dev.resources.get(resource) not in (None, [path])
            or any(identity in snapshot.resources for snapshot in (source_units, dev_units)
                   for identity in (resource, officer[0]))
            or officer[0] in local_resources or not stock or not stock.contains(*officer)):
        return {}
    corpus = '\n'.join(source.read(item) for item in source.closure(world)
                       if PurePosixPath(item).suffix.lower() in SOURCE_TEXT)
    flags = ('CommanderDead', 'CommanderAlive')
    scripts = script_text(source)
    if (corpus.upper().count('{' + resource + '}') != 1
            or any(flag in scripts for flag in flags)
            or any(api in scripts for api in SCRIPT_FLAG_APIS)
            or corpus.count('"CommanderDead"') != 2 + literal_flag_reads(corpus, 'CommanderDead')
            or corpus.count('"CommanderAlive"') != 1 + literal_flag_reads(corpus, 'CommanderAlive')):
        return {}
    fields, written = damage_state_flag_placements(source.read(path).splitlines(), allow_multiple_states=True)
    if tuple(written) != flags:
        raise ValueError('Villa commander damage producers differ from the reviewed prefab')
    start = fields.index('  PS_PlayableComponent "{5EFCC1946F475F15}" {')
    del fields[start:block_end(fields, start) + 1]
    body = '\n'.join(fields) + '\n'
    if LEGACY.search(body) or (path in dev.files and dev.read(path).strip() != body.strip()):
        return {}
    return {resource: {'path': path, 'body': body, 'placement': VILLA_OPFOR + ':72',
                       'rosterFlags': flags, 'playable': True}}


def villa_us_roster(source, dev, world, stock, local_resources, source_units, dev_units):
    if (world != VILLA + 'StealthingIntoVilla.ent' or not stock
            or source.files.get(VILLA_BLUFOR) != VILLA_BLUFOR_BLOB
            or source.files.get(VILLA_DEFAULT) != VILLA_DEFAULT_BLOB):
        return {}, {}
    layer = source.read(VILLA_BLUFOR)
    corpus = '\n'.join(source.read(item) for item in source.closure(world)
                       if PurePosixPath(item).suffix.lower() in SOURCE_TEXT)
    scripts = script_text(source)
    roles, markers = {}, {}
    role_prefix = VILLA + 'Prefab/US/HTM_Campaign_US_Player_SF_'
    marker_prefix = VILLA + 'Prefab/US/EditableMarker_Stealth_HTM_'
    owned = local_resources | source_units.resources.keys() | dev_units.resources.keys()
    if any(api in scripts for api in SCRIPT_FLAG_APIS):
        return {}, {}
    for resource, path in REF.findall(layer):
        if not path.startswith(role_prefix):
            continue
        body = source.read(path)
        fields = body.splitlines()
        parent = REF.search(fields[0]) if fields else None
        if (source.resources.get(resource) != [path] or corpus.upper().count('{' + resource + '}') != 1
                or resource in source_units.resources or resource in dev_units.resources
                or dev.resources.get(resource) not in (None, [path])
                or not parent or not fields[0].startswith('SCR_ChimeraCharacter : ')
                or not parent[2].startswith('Prefabs/Characters/Campaign/Final/BLUFOR/US_army/GreenBerets/Suppressed/')
                or parent[1] in owned or not stock.contains(*parent.groups())):
            return {}, {}
        try:
            fields, flags = damage_state_flag_placements(fields, allow_multiple_states=False)
            flag, = flags
            child_start = fields.index(' {')
            child = fields[child_start + 1:-2]
            ref = REF.search(child[0])
            if (len(child) != 5 or not ref or not ref[2].startswith(marker_prefix)
                    or child[0] != '  PS_ManualMarker : ' + ref[0] + ' {'
                    or not re.fullmatch(r'   ID "[\da-fA-F]{16}"', child[1])
                    or child[2:] != ['   coords 0 0 0', '   angles 0 0 0', '  }']
                    or flag in scripts or not literal_flag_reads(corpus, flag)
                    or corpus.count(f'"{flag}"') != 1 + literal_flag_reads(corpus, flag)):
                return {}, {}
            marker_id, marker_path = ref.groups()
            if (source.resources.get(marker_id) != [marker_path] or marker_id in markers
                    or marker_id in source_units.resources or marker_id in dev_units.resources
                    or dev.resources.get(marker_id) not in (None, [marker_path])
                    or corpus.upper().count('{' + marker_id + '}') != 1):
                return {}, {}
            original = source.read(marker_path).splitlines()
            end = block_end(original, 2)
            naming, = (block for _, block in blocks('\n'.join(line[2:] for line in original[3:end]))
                       if block[0].startswith('TILW_SetNameComponent '))
            name = properties(naming[1:-1], {'m_setName'})['m_setName'][1:-1]
            naming, count = convert_mission_names('\n'.join('  ' + line for line in naming))
            if (count != 1 or original[0] != 'PS_ManualMarker {' or original[2] != ' components {'
                    or name in VILLA_UNPLACED_MARKERS or name in scripts or corpus.count(f'"{name}"') != 3):
                return {}, {}
            native = marker_instance(original[end + 1:-1], '', draw_order=True).splitlines()
            stop = block_end(native, 1)
            native[stop:stop] = naming.splitlines()
            native.insert(block_end(native, 2), '   m_bTrackTransform 1')
            native.insert(1, original[1])
            marker_body = '\n'.join(native) + '\n'
            fields[child_start + 1] = child[0].replace('PS_ManualMarker', 'GenericEntity', 1)
            role, playable = migrate_playability('\n'.join(fields) + '\n')
            if (not playable or LEGACY.search(role + marker_body)
                    or any(file in dev.files and dev.read(file).strip() != converted.strip()
                           for file, converted in ((path, role), (marker_path, marker_body)))):
                return {}, {}
        except (IndexError, KeyError, ValueError):
            return {}, {}
        roles[resource] = {'path': path, 'body': role, 'placement': VILLA_BLUFOR + ':129',
                           'rosterFlags': (flag,), 'playable': True, 'attachedMarker': marker_id}
        markers[marker_id] = {'path': marker_path, 'body': marker_body, 'markerName': name}
    paths = sorted(asset['path'] for asset in chain(roles.values(), markers.values()))
    signature = sha256('\n'.join(f'{path} {source.files[path]}' for path in paths).encode()).hexdigest()
    if (len(roles) != 18 or len({marker['markerName'] for marker in markers.values()}) != 18
            or signature != VILLA_ROLE_ASSETS_HASH):
        return {}, {}
    return roles, markers


def playable_objectives(source, dev, world, stock, local_resources, source_units, dev_units, respawn):
    """Migrate named characters in explicitly playable, single-life rosters."""
    if not stock or respawn.get('ticketGaps'):
        return {}
    paths = sorted(path for path in source.closure(world) if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    originals = {path: source.read(path) for path in paths}
    corpus = '\n'.join(originals.values())
    scripts = script_text(source)
    if 'TILW_EditRespawnTicketsInstruction' in corpus or any(api in scripts for api in SCRIPT_FLAG_APIS):
        return {}
    replacement_factions = {entry['faction'] for entry in respawn.get('factions', ())}
    conflicting_ids = local_resources | source_units.resources.keys() | dev_units.resources.keys()
    reviewed = villa_commander(source, dev, world, stock, local_resources, source_units, dev_units)
    civ_id, civ_path = CIVILIAN_AI_GROUP
    civ_group = (f'{{{civ_id}}}{civ_path}' if verified_civilian_ai_group(local_resources, source_units, dev_units)
                 and ' m_faction "CIV"' in source_units.read(civ_path).splitlines()
                 and stock.contains('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et') else None)
    mobster_layer = PUBLIC_ENEMIES + PUBLIC_ENEMIES_PLAYERS
    mobster_groups = {}
    if (world == PUBLIC_ENEMIES + 'PublicEnemies.ent'
            and source.files.get(mobster_layer) == 'fd7f293373f6398b2c4cf314199a015a79c07f36'
            and all(source.files.get(PUBLIC_ENEMIES + 'Prefabs/AI/Groups/' + path) == blob
                    for path, blob, _ in PUBLIC_ENEMIES_GROUPS.values())):
        mobster_groups = {entry[2]: resource for resource, entry in PUBLIC_ENEMIES_BOSSES.items()}
    for layer in source.layers[world.lower()]:
        forced = ((line, group, None) for line, group in blocks(originals[layer])
                  if group[0].startswith('SCR_AIGroup ') and ' m_bSetPlayable 1' in group)
        civilian = named_ai_rosters(originals[layer], {civ_group}) if civ_group else ()
        mobsters = ((line, group, mobster_groups[ref[1]]) for line, group in blocks(originals[layer])
                    if (ref := REF.search(group[0])) and ref[1] in mobster_groups) if layer == mobster_layer else ()
        for line, group, inherited in chain(forced, civilian, mobsters):
            if group.count(' m_aUnitPrefabSlots {') != 1:
                continue
            faction = [match[1] for field in group if (match := re.fullmatch(r' m_faction "([\w.-]+)"', field))]
            mobster = inherited in PUBLIC_ENEMIES_BOSSES
            if inherited and not faction:
                faction = ['GC_Mobster' if mobster else 'CIV']
            if inherited and (any(field.startswith((' m_bSetPlayable ', ' m_bPlayable ')) for field in group)
                              or faction != ['GC_Mobster' if mobster else 'CIV']):
                continue
            if len(faction) != 1 or faction[0] in replacement_factions:
                continue
            start = group.index(' m_aUnitPrefabSlots {')
            slots = group[start + 1:block_end(group, start)]
            for slot in slots:
                ref = REF.fullmatch(slot.strip())
                if not ref:
                    continue
                resource, path = ref[1].upper(), ref[2]
                expected_references = PUBLIC_ENEMIES_BOSSES[inherited][3] if mobster else 1
                if (source.resources.get(resource) != [path] or dev.resources.get(resource) not in (None, [path])
                        or resource in source_units.resources or resource in dev_units.resources
                        or not path.startswith(world.rsplit('/', 1)[0] + '/')
                        or corpus.upper().count('{' + resource + '}') != expected_references):
                    continue
                entity = source.read(path).splitlines()
                parent = REF.search(entity[0]) if entity else None
                if not parent or not entity[0].startswith('SCR_ChimeraCharacter : '):
                    continue
                if mobster:
                    blob, base, _, _ = PUBLIC_ENEMIES_BOSSES[inherited]
                    if (resource != inherited or source.files.get(path) != blob or parent[1] != base
                            or source.resources.get(base) != [parent[2]]):
                        continue
                elif (parent[1] in conflicting_ids or DAMAGE_TARGETS.get(parent[1]) != parent[2]
                      or not parent[2].startswith('Prefabs/Characters/') or not stock.contains(*parent.groups())):
                    continue
                try:
                    fields, death = damage_state_flag_placement(entity)
                    root = fields.index(' components {')
                    end = block_end(fields, root)
                    marker = ['  PS_PlayableComponent "{5EFCC1946F475F15}" {', '   m_bIsPlayable 1', '  }']
                    playable = next((i for i in range(root + 1, end) if fields[i:i + len(marker)] == marker), None)
                    if inherited and not mobster and playable is None:
                        continue
                    if playable is not None:
                        del fields[playable:playable + len(marker)]
                        end -= len(marker)
                    setters = [i for i in range(root + 1, end) if re.fullmatch(
                        rf'  TILW_SetNameComponent {SERIALIZED_ID} \{{', fields[i])]
                    setter, = setters
                    stop = block_end(fields, setter)
                    name = properties(fields[setter + 1:stop], {'m_setName'})['m_setName']
                    if (not re.fullmatch(r'"[\w.-]{1,128}"', name)
                            or any(field.startswith(('  RplComponent ', '  SCR_CharacterDamageManagerComponent '))
                                   for field in fields[root + 1:end])):
                        continue
                    fields[setter:stop + 1] = [
                        fields[setter].replace('TILW_SetNameComponent', 'GC_MissionEntityNameComponent', 1),
                        f'   m_sMissionName {name}', '  }']
                    body = '\n'.join(fields) + '\n'
                    if (LEGACY.search(body) or name in scripts or death in scripts
                            or corpus.count(f'"{death}"') != 1 + literal_flag_reads(corpus, death)
                            or (path in dev.files and dev.read(path).strip() != body.strip())):
                        continue
                except (KeyError, ValueError):
                    continue
                reviewed[resource] = {'path': path, 'body': body, 'deathFlag': death, 'entity': name[1:-1],
                                      'placement': f'{layer}:{line}', 'playable': True}
    return reviewed


def named_ai_rosters(text, prefabs):
    references = '|'.join(re.escape(prefab) for prefab in sorted(prefabs))
    head = re.compile(r'(?P<group>\$grp )?SCR_AIGroup(?: (?P<name>[\w.-]+))? : "'
                      + '(?P<prefab>' + references + r')" \{')
    for line, group in blocks(text):
        root = head.fullmatch(group[0])
        if not root:
            continue
        try:
            members = polygon_capture.members(group, root['group'], root['name'])
        except ValueError:
            continue
        for name, fields in members:
            if name:
                yield line, fields, root['prefab']


def ai_objectives(source, dev, world, stock, local_resources, source_units, dev_units):
    """Convert named objective characters instantiated by verified AI groups."""
    reviewed = {}
    paths = sorted(p for p in source.closure(world) if PurePosixPath(p).suffix.lower() in SOURCE_TEXT)
    originals = {p: source.read(p) for p in paths}
    corpus = '\n'.join(originals.values())
    condition_corpus = re.sub(r'(?m)^   m_name "[\w.-]+"$', '', corpus)
    scripts = script_text(source)
    base = ('E024A74F8A4BC644', 'Prefabs/Characters/Factions/CIV/Businessman/Character_CIV_Businessman_1.et')
    civ = CIVILIAN_AI_GROUP
    fia = ('EE92725E9B949C3D', 'Prefabs/Groups/INDFOR/Group_FIA_PlatoonHQ.et')
    if (base[0] in local_resources or not stock.contains(*base)
            or not stock.contains('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et')):
        return reviewed
    prefabs = set()
    if verified_civilian_ai_group(local_resources, source_units, dev_units):
        prefabs.add(f'{{{civ[0]}}}{civ[1]}')
    fia_prefab = f'{{{fia[0]}}}{fia[1]}'
    if verified_stock_ai_group(*fia, stock, local_resources, source_units, dev_units, inherited_slots=False):
        prefabs.add(fia_prefab)
    if not prefabs:
        return reviewed
    for layer in source.layers[world.lower()]:
        if 'SCR_AIGroup ' not in originals[layer]:
            continue
        for line, group, prefab in named_ai_rosters(originals[layer], prefabs):
            if prefab == fia_prefab and [field for field in group if field.startswith(' m_faction ')] != [' m_faction "CIV"']:
                continue
            slots = [i for i, field in enumerate(group) if field == ' m_aUnitPrefabSlots {']
            if len(slots) != 1 or any('m_bPlayable' in field or 'm_bSetPlayable' in field for field in group):
                continue
            start = slots[0]
            ref = REF.fullmatch(group[start + 1].strip())
            if not ref or block_end(group, start) != start + 2:
                continue
            resource, path = ref[1].upper(), ref[2]
            if (source.resources.get(resource) != [path] or dev.resources.get(resource) not in (None, [path])
                    or not path.startswith(world.rsplit('/', 1)[0] + '/') or corpus.count(ref[0]) != 1):
                continue
            entity = source.read(path).splitlines()
            if not entity or entity[0] != f'SCR_ChimeraCharacter : "{{{base[0]}}}{base[1]}" {{':
                continue
            try:
                root = entity.index(' components {')
                root_end = block_end(entity, root)
                setters = [i for i, value in enumerate(entity) if re.fullmatch(
                    rf'  TILW_SetNameComponent {SERIALIZED_ID} \{{', value)]
                damage = [i for i, value in enumerate(entity) if DAMAGE_COMPONENT.fullmatch(value)]
                if len(setters) != 1 or len(damage) != 1:
                    continue
                setter, damage_index = setters[0], damage[0]
                setter_close, damage_close = block_end(entity, setter), block_end(entity, damage_index)
                if not (root < setter < setter_close < root_end and root < damage_index < damage_close < root_end):
                    continue
                name = properties(entity[setter + 1:setter_close], {'m_setName'})['m_setName']
                death = properties(entity[damage_index + 1:damage_close], {'m_flagName'})['m_flagName']
                if (not re.fullmatch(r'"[\w.-]{1,128}"', name) or not re.fullmatch(r'"[\w.-]{1,128}"', death)
                        or any(field.startswith(('  RplComponent ', '  SCR_CharacterDamageManagerComponent '))
                               for field in entity[root + 1:root_end])):
                    continue
                after = entity.copy()
                after[setter:setter_close + 1] = [
                    entity[setter].replace('TILW_SetNameComponent', 'GC_MissionEntityNameComponent'),
                    f'   m_sMissionName {name}', '  }']
                del after[damage_index:damage_close + 1]
                native, actions = convert_object(after, name[1:-1], path, inherited_replication=True)
                body = '\n'.join(native) + '\n'
                if (not actions or any('requiredFlag' in action for action in actions) or LEGACY.search(body)
                        or (path in dev.files and dev.read(path).strip() != body.strip())):
                    continue
                flags = [death, *(f'"{action["flag"]}"' for action in actions)]
                if len(set(flags)) != len(flags) or any(
                    condition_corpus.count(flag) != len(re.findall(r'(?m)^ +(?:m_condition )?TILW_LiteralTerm [^\n]+\n'
                                                         r'(?: +m_invertTerm [01]\n)? +m_flagName '
                                                         + re.escape(flag) + '$', corpus)) + 1
                    or flag in scripts for flag in flags):
                    continue
                if name in scripts or re.search(r'(?m)^\s*\w+ ' + re.escape(name[1:-1]) + r' (?:[:{])', corpus):
                    continue
            except (KeyError, ValueError):
                continue
            reviewed[resource] = {'path': path, 'body': body, 'interactions': actions, 'deathFlag': death[1:-1],
                                  'entity': name[1:-1],
                                  'placement': f'{layer}:{line}'}
    return reviewed


def workshop_resources(workshop, packages):
    approved = set()
    for provider, (digest, references) in packages.items():
        directory = workshop / provider
        archive = directory / 'data.pak'
        registry = directory / 'resourceDatabase.rdb'
        if not registry.is_file():
            continue
        if digest:
            if not archive.is_file():
                continue
            with archive.open('rb') as stream:
                if file_digest(stream, 'sha256').hexdigest() != digest:
                    continue
        database = StockPrefabs(registry)
        approved.update(reference for reference in references if database.contains(*reference))
    return approved


def verified_british_fallback_groups(workshop, stock, locally_owned, source_units, dev_units):
    base = ('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et')
    occupied = locally_owned | source_units.resources.keys() | dev_units.resources.keys()
    if not workshop or not stock or base[0] in occupied or not stock.contains(*base):
        return set()
    groups = {(resource, UK_GROUP_DIRECTORY + '1989/Group_UK_1989_' + name + '.et')
              for resource, name in BRITISH_1989_FALLBACK_GROUPS.items()}
    required = groups | {
        ('6521AB6151C0A7C8', UK_GROUP_DIRECTORY + '1989/Group_UK_1989_Regulars_Base.et'),
        ('D1850DAD5D572363', UK_GROUP_DIRECTORY + '1983/Group_UK_1983_SpecialForces_Base.et'),
        ('FA77FB102E05692C', UK_GROUP_DIRECTORY + 'Group_UK_Base.et'),
        ('542CBB6908CA71D8', 'Prefabs/Characters/Factions/BLUFOR/UK_Army/Character_UK_Base.et'),
    }
    unit_overrides = {
        '7D7C6FBCBA40F45D': 'Light Infantry/Character_UK_1989_Regulars_SectionCommander.et',
        'F487760AB6777BF3': 'Light Infantry/Character_UK_1989_Regulars_PlatoonLeader.et',
        'B9CFB916A26682D6': 'Light Infantry/Character_UK_1989_Regulars_Sergeant.et',
        '6FAAF2B4A124BA7F': 'Special Forces/Character_UK_1989_SpecialForces_SL.et',
    }
    role_dir = 'Prefabs/Characters/Factions/BLUFOR/UK_Army/1989/'
    for resource, relative in unit_overrides.items():
        path = role_dir + relative
        if (resource in locally_owned or source_units.resources.get(resource) != [path]
                or dev_units.resources.get(resource) != [path]
                or not unchanged_units_parent(source_units, dev_units, resource)):
            return set()
    for resource, role in BRITISH_1989_FALLBACK_ROLES.items():
        category, name = role.split('/')
        required.add((resource, f'{role_dir}{category}/Character_UK_1989_{category}_{name}.et'))
    if any(resource in occupied and resource not in unit_overrides for resource, _ in required):
        return set()
    if workshop_resources(workshop, {
            UK_PROVIDER: ('fb3135513cb2abc077d891353cb83420c765a77186ecd751615fb683a7851350', required),
    }) != required:
        return set()
    resource, path = UK_1989_RIFLE_SECTION
    if (resource not in locally_owned and source_units.resources.get(resource) == dev_units.resources.get(resource) == [path]
            and (source_units.files[path], dev_units.files[path]) == UK_1989_RIFLE_SECTION_BLOBS):
        groups.add(UK_1989_RIFLE_SECTION)
    return groups


def verified_british_ai_groups(workshop, stock, locally_owned, source_units, dev_units):
    """Verify the archive containing the reviewed 1983 AI groups and their original rosters."""
    base = ('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et')
    occupied = locally_owned | source_units.resources.keys() | dev_units.resources.keys()
    if not workshop or not stock or base[0] in occupied or not stock.contains(*base):
        return {}

    group_prefix = UK_GROUP_DIRECTORY + '1983/Group_UK_1983_Regulars_'
    role_prefix = 'Prefabs/Characters/Factions/BLUFOR/UK_Army/1983/Regulars/Character_UK_1983_Regulars_'
    members = {member for _, roster in BRITISH_1983_GROUPS.values() for member in roster}
    roles = {resource: role_prefix + UK_1983_AI_ROLES[resource] + '.et' for resource in members}
    specs = {}
    for resource, (name, roster) in BRITISH_1983_GROUPS.items():
        path = group_prefix + name + '.et'
        specs[resource, path] = {'path': path, 'slots': [(member, roles[member]) for member in roster]}
    loadout = '4A29AE1848437792'
    package_loadout = role_prefix + 'BaseLoadout.et'
    units_loadout = package_loadout.replace('/Regulars/', '/Light Infantry/')
    if (loadout in locally_owned or source_units.resources.get(loadout) != [units_loadout]
            or dev_units.resources.get(loadout) != [units_loadout]
            or any(snapshot.files.get(units_loadout) != '77a0b17f00bfd014678b658b6d20852d5ac3d4f4'
                   or snapshot.files.get(units_loadout + '.meta') != '17524f6e0b2d8358bacdd69a1172d82c9956ef1b'
                   for snapshot in (source_units, dev_units))):
        return {}
    required = set(specs) | set(roles.items()) | {
        ('B08CB5CAE067C4CB', group_prefix + 'Base.et'),
        ('FA77FB102E05692C', UK_GROUP_DIRECTORY + 'Group_UK_Base.et'),
    }
    if any(resource in occupied for resource, _ in required):
        return {}
    required.add((loadout, package_loadout))
    approved = workshop_resources(workshop, {
        UK_PROVIDER: ('fb3135513cb2abc077d891353cb83420c765a77186ecd751615fb683a7851350', required),
    })
    return specs if approved == required else {}


BMP_DAMPER_AUDIO = ('F7D2EDE33E61A12A', 'Sounds/Vehicles/Wheeled/BTR70/Vehicles_BTR70_Dampers.acp')


def unavailable_optional_resources(databases, snapshots, resources):
    """Check both identifiers before treating a reviewed legacy resource as unavailable."""
    missing = {(guid, path) for guid, path in resources
               if all(guid not in snapshot.resources and path not in snapshot.files for snapshot in snapshots)}
    if not databases:
        return set()
    for registry in databases:
        if not missing:
            break
        try:
            database = StockPrefabs(registry)
        except ValueError:
            return set()
        missing = {(guid, path) for guid, path in missing
                   if not database.registered_paths(guid) and not database.registered_ids(path)}
    return missing


def reviewed_missing_bmp_audio(source, dev, unavailable):
    """Preserve the published prefab's audible behavior when the obsolete bank cannot load."""
    resource = '84A2CD45010EC22E'
    path = 'worlds/HitTheMan/Prefabs/Vehicles/BMP1_base_HTM.et'
    sound, sound_path = BMP_DAMPER_AUDIO
    if (BMP_DAMPER_AUDIO not in unavailable or source.resources.get(resource) != [path]
            or dev.resources.get(resource) not in (None, [path])):
        return {}
    try:
        body = passive_asset(source, path)
    except ValueError:
        return {}
    token = f' "{{{sound}}}{sound_path}"'
    if body.count(token) != 1:
        return {}
    return {resource: (path, body.replace(token, '', 1))}


def spawn_resource(source, dev, source_units, dev_units, stock, guid, path, objectives, workshop_resources=(), *,
                   reviewed_assets=None, aliases=None):
    if (not path.lower().endswith('.et') or path.lower().startswith('prefabs/groups/')
            or path in REVIEWED_PLAYABLE_GROUPS or path in REVIEWED_GROUP_CALLSIGNS):
        return None
    owned = source.resources.get(guid)
    if owned:
        if owned != [path] or dev.resources.get(guid) not in (None, [path]):
            return None
        if guid in objectives and objectives[guid]['path'] == path:
            return path
        try:
            approved = (reviewed_assets or {}).get(guid)
            body = approved[1] if approved and approved[0] == path else passive_asset(source, path)
        except ValueError:
            return None
        if path in dev.files and isinstance(body, str) and aliases:
            body = replace_registered_paths(body, source, dev, aliases)
        return path if path not in dev.files or body == dev.read(path) else None
    if guid in dev.resources:
        return None
    before = source_units.resources.get(guid)
    after = dev_units.resources.get(guid)
    if verified_core_btr70_transfer(guid, path, source_units, dev_units, stock):
        return path
    if before or after:
        if before != [path] or after != [path]:
            return None
        body = source_units.read(path)
        return path if body == dev_units.read(path) and not LEGACY.search(body) else None
    return path if (guid, path) in workshop_resources or stock.contains(guid, path) else None


def reviewed_spawn_type(source, resource, path, types):
    if source.resources.get(resource) != [path] or path not in REVIEWED_NATIVE_ASSETS:
        return False
    body = passive_asset(source, path)
    return body.startswith(types)


def villa_light_cleanup_is_inert(source, location):
    if (location != VILLA_PROPS + ':85' or source.files.get(VILLA_PROPS) != VILLA_PROPS_BLOB
            or source.files.get(VILLA_DEFAULT) != VILLA_DEFAULT_BLOB):
        return False
    resource, path, blob = VILLA_LIGHT_CLEANUP
    if source.files.get(path) != blob or source.resources.get(resource) != [path]:
        return False
    world = VILLA + 'StealthingIntoVilla.ent'
    corpus = '\n'.join(source.read(item) for item in source.closure(world)
                       if PurePosixPath(item).suffix.lower() in SOURCE_TEXT)
    return (corpus.upper().count('{' + resource + '}') == 5
            and len(re.findall(r'\bLightDeleterSpawner_[1-5]\b', corpus)) == 5
            and not re.search(r'LightDeleterSpawner_|3AA16BA294431D4E|LightsGeneratorCheckpoint',
                              script_text(source), re.I))


def placed_spawn(block, location, resolve_resource, captures, flags, used_flags, resolve_crew, *, source,
                 resolve_crew_config=None, shadowed_names=(), missing_waypoints=(), self_waypoints=(),
                 capture_latches=None, single_draw_flags=(), derived_conditions=None, dormant_flags=(), allow_bare=False,
                 local_files_markers=False, grouped_actions=None, shared_schedule=None, startup_sample_flags=(),
                 reviewed_startup_assets=None):
    root = placed_spawner_root(block[0], allow_bare)
    if not root:
        raise ValueError('unverified placed spawner prefab')
    placements, rules, audit = [], [], []
    paired_actions = defaultdict(list) if local_files_markers else None
    transform_fields = SPAWNER_TRANSFORM | {'scale'}
    inert_light_cleanup = villa_light_cleanup_is_inert(source, location)
    members = polygon_capture.members(block, root['group'], root['name'], allow_quoted=True)
    for index, (authored_name, lines) in enumerate(members):
        name = authored_name.strip('"') if authored_name else None
        children = []
        marker_actions = defaultdict(list) if local_files_markers else None
        key = f'{location}/spawner/{index}'
        if ' {' in lines:
            child_start = lines.index(' {')
            if block_end(lines, child_start) != len(lines) - 1:
                raise ValueError('custom placement after spawner children')
            children = lines[child_start:]
            child_blocks = list(blocks('\n'.join(line[2:] for line in children[1:-1])))
            if local_files_markers:
                if len(child_blocks) != 1:
                    raise ValueError('computer spawner needs one authored marker')
                child = local_files_marker_child(source, child_blocks[0][1], lines[:child_start])
                native, _, info = placed_spawn(
                    child, key + '/marker', resolve_resource, captures, flags, used_flags, resolve_crew, source=source,
                    single_draw_flags=single_draw_flags, grouped_actions=marker_actions)
                children = [' {', *('  ' + line for line in native.splitlines()), ' }']
                audit.extend(info)
                child_blocks = []
            for _, child in child_blocks:
                head = PLACED_ENTITY.fullmatch(child[0])
                if (not head or head['group'] or not head['name'] or not REF.search(child[0])
                        or not re.fullmatch(WAYPOINT_CLASS, head['kind']) or LEGACY.search('\n'.join(child))):
                    raise ValueError('spawner child needs a named native waypoint')
            lines = lines[:child_start]
        config = None
        reserve_next_seat = False
        config_fields = [i for i, line in enumerate(lines) if line.startswith(' m_crewConfig ')]
        if config_fields:
            if len(config_fields) != 1:
                raise ValueError('vehicle spawn needs one m_crewConfig')
            start, = config_fields
            end = block_end(lines, start)
            config = crew_config_contents(lines[start:end + 1], resolve_crew_config)
            reference = REF.search(lines[start])
            reserve_next_seat = (allow_bare and bool(reference) and reference[1].upper() == FLORA_CREW_CONFIG)
            lines = lines[:start] + lines[end + 1:]
        data = properties(lines, transform_fields | {
            'm_prefab', 'm_conditionFlag', 'm_spawnTimings', 'm_setEntityNames', 'm_waypointNames', 'm_pregameSpawn',
            'Flags',
        })
        if local_files_markers:
            identity, path = LOCAL_FILES_COMPUTER
            if not children or data.get('m_prefab') != f'"{{{identity}}}worlds/Wade/BananaHoedown/Computer_Secure.et"':
                raise ValueError('unverified computer spawner')
            data['m_prefab'] = f'"{{{identity}}}{path}"'
        transform = [f'{key} {value}' for key, value in data.items() if key in transform_fields]
        if 'coords' not in data or any(not FIELD.fullmatch(line) for line in transform):
            raise ValueError('spawner needs a placed transform')
        transform[:0] = authored_placement_flags(data)
        pregame = data.get('m_pregameSpawn', '0')
        if pregame not in ('0', '1'):
            raise ValueError('invalid placed spawner phase')
        flag = data.get('m_conditionFlag', '""')
        if (inert_light_cleanup and re.fullmatch(r'LightDeleterSpawner_[1-5]', name or '')
                and data.get('m_prefab') == f'"{{{VILLA_LIGHT_CLEANUP[0]}}}{VILLA_LIGHT_CLEANUP[1]}"'
                and pregame == '1' and data.get('m_spawnTimings') == '1'):
            # These helpers are spawned after the only world-postprocess callback.
            audit.append({'spawnAnchor': name, 'spawnName': '', 'prefab': data['m_prefab'],
                          'spawnDelaysSeconds': ['1'], 'trigger': 'inactive cleanup', 'flag': flag[1:-1]})
            continue
        if (flag == '"Sirens"' and 'Sirens' in flags and source.files.get(VILLA_DEFAULT) == VILLA_DEFAULT_BLOB
                and (location == VILLA_DEFAULT + ':4128'
                     or (location == VILLA_PROPS + ':85' and source.files.get(VILLA_PROPS) == VILLA_PROPS_BLOB))):
            # Both writers are Match events with a ten-second delay, after legacy spawner initialization.
            pregame = '0'
        if (pregame == '1' and flag != '""'
                and (not re.fullmatch(FLAG_NAME, flag) or flag[1:-1] not in startup_sample_flags)):
            raise ValueError('pregame spawner needs a verified pregame flag producer')
        if 'scale' in data and (not isfinite(scale := float(data['scale'])) or scale <= 0):
            raise ValueError('spawner scale must be positive and finite')
        timings = data.get('m_spawnTimings', '').split() or ['0']
        startup_delay = 1 if pregame == '1' else 0
        if not 1 <= len(timings) <= 128 or any(
                not re.fullmatch(NUMBER, delay) or Decimal(delay) + startup_delay > 86400 for delay in timings):
            raise ValueError('invalid placed spawner schedule')
        spawned_name = data.get('m_setEntityNames') or '""'
        if not re.fullmatch(SPAWN_NAME, spawned_name) or (spawned_name != '""' and len(timings) != 1):
            raise ValueError('multiple spawns have success-dependent entity naming')
        waypoints = target_names(data['m_waypointNames'], skip_blank=True) if data.get('m_waypointNames') else []
        ref = REF.fullmatch(data.get('m_prefab', ''))
        ignored_effect_waypoints = []
        if ref and (effect := BREAK_THE_LINE_EFFECTS.get(ref[1].upper())):
            destination = f'worlds/HitTheMan/2nd Break The Line/{effect}'
            if (ref[2] in (destination, f'worlds/HitTheMan/3rd Break The Line/{effect}')
                    and reviewed_spawn_type(source, ref[1].upper(), destination, 'GenericEntity : ')):
                data['m_prefab'] = f'"{{{ref[1]}}}{destination}"'
                ref = REF.fullmatch(data['m_prefab'])
                # The legacy spawner passes waypoints only to spawned AI groups.
                ignored_effect_waypoints, waypoints = waypoints, []
        startup_asset = (reviewed_startup_assets or {}).get(ref[1].upper(), {}) if ref else {}
        relocated = startup_asset.get('legacyPath') == ref[2] if ref else False
        startup_slots = startup_asset.get('startupSlots', 0) if relocated else 0
        if startup_slots and (not startup_delay or flag[1:-1] not in single_draw_flags or waypoints or config is not None
                              or spawned_name != f'"{PurePosixPath(startup_asset["path"]).stem}"'):
            raise ValueError('playable group requires an exclusive staging choice and its authored name')
        group = bool(ref and (startup_slots or ref[2].startswith('Prefabs/Groups/') or
                              reviewed_spawn_type(source, ref[1].upper(), ref[2], ('SCR_AIGroup : ', 'SCR_AIGroup {'))))
        absent = next((point for point in waypoints if point[1:-1] in missing_waypoints), None) if group else None
        if absent:
            waypoints = [point for point in waypoints if point != absent]
        self_waypoint = group and name in self_waypoints and waypoints == [f'"{name}"']
        if self_waypoint:
            waypoints = []
        if config is not None and (not ref or (not ref[2].startswith('Prefabs/Vehicles/') and
                                               (ref[1].upper(), ref[2]) != ZAGORIA_BMP2 and
                                               not reviewed_spawn_type(source, ref[1].upper(), ref[2],
                                                                       ('Vehicle : ', 'Tank : ', 'RHS_Tank : ')))):
            vehicle = ref[0] if ref else data.get('m_prefab', '')
            raise ValueError(f'crewed spawner {name or index + 1} needs a verified vehicle: {vehicle}')
        if waypoints and not group and config is None:
            raise ValueError('spawner waypoints need a verified AI group')
        anchor = name or 'GC_Spawner_' + guid(key)
        if flag in dormant_flags:
            placements.extend([f'GenericEntity {authored_name or anchor} {{',
                               *(' ' + line for line in transform), *children, '}'])
            audit.append({'spawnAnchor': anchor, 'spawnName': '', 'prefab': data.get('m_prefab', ''),
                          'spawnDelaysSeconds': timings, 'groupWaypoints': [point[1:-1] for point in waypoints],
                          'trigger': 'inactive flag', 'flag': flag[1:-1],
                          **({'missingGroupWaypoint': absent[1:-1]} if absent else {})})
            continue
        def resolve_spawn_prefab(resource, path):
            if relocated:
                return startup_asset['path']
            return resolve_resource(resource, path, group=group)

        details = spawn_details({
            'm_prefab': data.get('m_prefab', ''), 'm_locationName': f'"{anchor}"',
            'm_setEntityName': spawned_name, 'm_executionDelay': '0',
        }, resolve_spawn_prefab)
        term = {'trigger': 'pregame start' if startup_delay else 'Match start'}
        if flag != '""':
            check = captures.get(flag, ())
            if startup_delay:
                term = {'trigger': 'event flag', 'flag': flag[1:-1]}
            elif capture_latches and flag[1:-1] in capture_latches:
                term = {'trigger': 'area capture', 'flag': flag[1:-1]}
            elif any(line.strip() == 'm_bStopAfterFirstChange 1' for line in check):
                term = {'trigger': 'area capture', 'flag': flag[1:-1]}
                if flag[1:-1] not in used_flags and any('m_Messages ' in line for line in check):
                    raise ValueError('capture-gated spawner needs a shared status-message owner')
            elif derived_conditions and flag[1:-1] in derived_conditions:
                term = derived_conditions[flag[1:-1]]
                if any(item['flag'] not in flags for item in leaves(term)
                       if item['trigger'] in ('event flag', 'interaction flag')):
                    raise ValueError('spawner meta flag lacks converted producers: ' + flag)
            elif flag[1:-1] in flags and re.fullmatch(FLAG_NAME, flag):
                term = {'trigger': 'interaction flag', 'flag': flag[1:-1]}
            else:
                raise ValueError('spawner flag lacks a verified producer: ' + flag)
        group_waypoints = waypoints if group else []
        fields = (['        m_aWaypoints {', *('         ' + point for point in group_waypoints), '        }']
                  if group_waypoints else [])
        actions = defaultdict(list)
        crew_groups = []
        duplicate_roadblock_name = (source.files.get(LOCAL_PROBLEMS_ROADBLOCKS[0]) == LOCAL_PROBLEMS_ROADBLOCKS[1]
                                    and location.startswith(LOCAL_PROBLEMS_ROADBLOCKS[0] + ':')
                                    and anchor in {'Road_Block_Tech', 'Road_Block_Tech2'}
                                    and details['area'] == 'Bomber_Target_Vic')
        for sequence, delay in enumerate(timings):
            spawn_key = f'{key}/{sequence}'
            kind = 'GC_AIGroupSpawn' if group else 'GC_PrefabSpawn'
            if startup_slots:
                kind = 'GC_StagingPlayableGroupSpawn'
            if config is not None:
                fields, crew_groups = render_crew_config(config, spawn_key, resolve_crew,
                                                         shadowed_names=shadowed_names,
                                                         missing_waypoints=missing_waypoints,
                                                         allow_default_crew=(reserve_next_seat or
                                                                             (ref[1].upper(), ref[2]) in VERIFIED_DEFAULT_CREW))
                if reserve_next_seat:
                    fields.insert(0, '        m_bReserveNextCrewSeat 1')
                kind = 'GC_CrewedVehicleSpawn'
            if duplicate_roadblock_name:
                fields = [*fields, '        m_bAllowDuplicateName 1']
            scheduled = str(Decimal(delay) + startup_delay) if startup_delay else delay
            seconds, action = render_spawn({**details, 'delay': scheduled}, spawn_key, kind, fields)
            actions[seconds].extend(action)
        if local_files_markers or grouped_actions is not None:
            if (term['trigger'] != 'interaction flag' or term['flag'] not in single_draw_flags
                    or set(actions) != {Decimal(300)}):
                raise ValueError('paired spawn requires one verified Match draw and the authored schedule')
            target = paired_actions if local_files_markers else grouped_actions
            for seconds, deliveries in actions.items():
                target[seconds].extend(deliveries)
                if marker_actions:
                    target[seconds].extend(marker_actions[seconds])
        elif shared_schedule is not None:
            if (flag != '"TownSpawnActive"' or not group or pregame != '0' or config is not None
                    or spawned_name != '""' or len(timings) != 1 or Decimal(timings[0]) <= 0):
                raise ValueError('shared town schedule requires one delayed Match AI group spawn per placement')
            shared_schedule.append((term, actions))
        else:
            label = 'GC_MigratedSpawn_' + guid(key)
            rendered = render_outcome(key, label, term, actions, captures, {flag}, capture_latches or {})
            if term['trigger'] == 'interaction flag' and not term.get('requiresMatch'):
                rendered[3:3] = [f'      GC_GameModeStateCondition "{{{guid(key + "/match")}}}" {{',
                                 f'       m_eGameModeState {MATCH_PHASE_VALUE}', '      }']
            rules.extend(rendered)
        placements.extend([f'GenericEntity {authored_name or anchor} {{',
                           *(' ' + line for line in transform), *children, '}'])
        audit.append({'spawnAnchor': anchor, 'spawnName': details['area'], 'prefab': details['prefab'],
                      'spawnDelaysSeconds': timings, 'groupWaypoints': [point[1:-1] for point in group_waypoints], **term})
        if startup_delay:
            audit[-1]['startupInitializationSeconds'] = startup_delay
        if startup_slots:
            audit[-1]['startupPlayableSlots'] = startup_slots
        if duplicate_roadblock_name:
            audit[-1]['allowsDuplicateSpawnName'] = True
        if flag[1:-1] in single_draw_flags:
            audit[-1]['startupChoice'] = flag[1:-1]
        elif startup_delay and flag[1:-1] in startup_sample_flags:
            audit[-1]['startupSample'] = flag[1:-1]
        if 'scale' in data:
            audit[-1]['anchorScale'] = data['scale']
        if absent:
            audit[-1]['missingGroupWaypoint'] = absent[1:-1]
        if ignored_effect_waypoints:
            audit[-1]['ignoredEffectWaypoints'] = [point[1:-1] for point in ignored_effect_waypoints]
        if self_waypoint:
            audit[-1]['selfGroupWaypoint'] = name
        if config is not None:
            audit[-1]['crewWaypoints'] = [point for group in crew_groups for point in group['waypoints']]
            audit[-1]['crewGroups'] = crew_groups
            if waypoints:
                # The legacy spawner applies this field only to spawned AI group entities.
                audit[-1]['ignoredVehicleGroupWaypoints'] = [point[1:-1] for point in waypoints]
    if not audit:
        raise ValueError('empty placed spawner group')
    if local_files_markers:
        sampled_flags = {item.get('startupChoice') for item in audit}
        if len(sampled_flags) != 1 or None in sampled_flags or len(audit) != 50:
            raise ValueError('expected 25 computer and marker pairs for one startup choice')
        selected, = sampled_flags
        term = {'trigger': 'interaction flag', 'flag': selected, 'requiresMatch': True}
        rules = render_outcome(location, f'GC_ComputerSpawns_{selected}', term, paired_actions, captures, None, {})
    return '\n'.join(placements) + '\n', '\n'.join(rules) + '\n', audit


def shadowed_crew_names(source, world, layers):
    """Allow a name shared only by one original spawner and its named crew authors."""
    referenced = Counter()
    anchors = Counter()
    try:
        for text in layers.values():
            for _, block in blocks(text):
                if not (root := PLACED_SPAWNER.fullmatch(block[0])):
                    continue
                anchors.update(name.strip('"') for name, _ in polygon_capture.members(
                    block, root['group'], root['name'], allow_quoted=True) if name)
                referenced.update(re.findall(r'(?m)^ +m_entityName "([\w.-]+)"$', '\n'.join(block)))
    except ValueError:
        return set()
    eligible = {name for name in referenced if anchors[name] == 1}
    if not eligible:
        return set()
    corpus = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                       if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    scripts = script_text(source)
    return {name for name in eligible if name not in scripts
            and len(re.findall(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])', corpus)) == referenced[name] + 1}


def world_spawners(source, world, resolve_spawn, captures, flags, used_flags, blockers, resolve_crew, resolve_crew_config,
                   single_draw_flags, derived_conditions, area_placements, *, startup_sample_flags, spawned_assets):
    placements, schedules, audit = {}, [], []
    attached = defaultdict(list)
    layers = {path: text for path in source.layers[world.lower()]
              if 'TILW_PrefabSpawnerEntity' in (text := source.read(path))}
    capture_latches = spawn_capture_latches(source, world, layers, captures, used_flags)
    shadowed = shadowed_crew_names(source, world, layers) if any('m_entityName ' in text for text in layers.values()) else ()
    for layer, text in layers.items():
        try:
            startup_assets = (spawned_assets if layer == VILLA_CONVOY_LAYER
                              and source.files[layer] == VILLA_CONVOY_LAYER_BLOB else None)
            town_schedule = [] if (layer == NAGALAM_TOWN_SPAWN_LAYER
                                   and source.files[layer] == NAGALAM_TOWN_SPAWN_BLOB) else None
            dormant = dormant_spawner_flags(source, world, layer)
            blob, reviewed = REVIEWED_MISSING_SPAWN_WAYPOINTS.get(layer, (None, {}))
            missing_waypoints = set()
            if source.files[layer] == blob:
                scripts = script_text(source)
                references = '\n'.join(source.read(p) for p in source.closure(world)
                                       if PurePosixPath(p).suffix.lower() in SOURCE_TEXT)
                for name, count in reviewed.items():
                    token = re.compile(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])')
                    if not token.search(scripts) and len(token.findall(references)) == count:
                        missing_waypoints.add(name)
            for line, nested, block in placed_spawner_entries(source, layer, text):
                if nested and (layer, line) not in area_placements:
                    continue
                location = f'{layer}:{line}'
                if nested:
                    name = PLACED_SPAWNER.fullmatch(block[0])['name']
                    location += '/' + (name or guid('\n'.join(block)))
                try:
                    shared_schedule = [] if town_schedule is not None else None
                    self_waypoints = set()
                    if source.files[layer] == 'b5b00eae008bf41c803dd4469a54a34b93199767':
                        self_waypoints = {f'AI_Spawn_Sector2_{i}' for i in range(1, 15)}
                    native, scheduled, info = placed_spawn(
                        block, location, resolve_spawn, captures, flags, used_flags, resolve_crew,
                        source=source, resolve_crew_config=resolve_crew_config, shadowed_names=shadowed,
                        missing_waypoints=missing_waypoints, self_waypoints=self_waypoints,
                        capture_latches=capture_latches, single_draw_flags=single_draw_flags,
                        startup_sample_flags=startup_sample_flags,
                        reviewed_startup_assets=startup_assets,
                        derived_conditions=derived_conditions, dormant_flags=dormant,
                        local_files_markers=source.files[layer] == LOCAL_FILES_SPAWNER_LAYERS.get(layer),
                        allow_bare=(layer, source.files[layer]) in REVIEWED_BARE_SPAWNER_LAYERS,
                        shared_schedule=shared_schedule)
                except ValueError as exc:
                    blockers.append({'rule': 'TILW_PrefabSpawnerEntity', 'file': layer, 'line': line,
                                     'detail': str(exc)})
                    if not nested:
                        placements[(layer, line)] = ''
                    continue
                if nested:
                    attached[(layer, line)].append((block, native.splitlines()))
                else:
                    placements[(layer, line)] = native
                if town_schedule is not None:
                    town_schedule.extend(shared_schedule)
                schedules.append(scheduled)
                audit.extend({'file': layer, 'line': line, 'startupChoice': None, **entry} for entry in info)
            if town_schedule is not None:
                if len(town_schedule) != 127 or any(term != town_schedule[0][0] for term, _ in town_schedule):
                    raise ValueError('town schedule needs all 127 original spawns under the same condition')
                # A successful flag check commits every place's independent timer. Keep
                # source registration order for actions with the same due time.
                actions = defaultdict(list)
                for _, deliveries in town_schedule:
                    for delay, body in deliveries.items():
                        actions[delay].extend(body)
                rendered = render_controlled_event(layer, 'GC_TownSpawnActive', town_schedule[0][0], actions,
                                                   captures, set(), capture_latches,
                                                   'TownSpawnActive')
                schedules.append('\n'.join(rendered) + '\n')
        except ValueError as exc:
            blockers.append({'rule': 'placed spawner layer', 'file': layer, 'detail': str(exc)})
    used_captures = {entry['flag'] for entry in audit if entry['trigger'] == 'area capture'}
    latches = {flag: name for flag, name in capture_latches.items() if flag in used_captures}
    if latches:
        schedules.insert(0, '\n'.join(render_capture_trackers(world, latches, captures, set())) + '\n')
    return placements, ''.join(schedules), audit, attached


def dormant_spawner_flags(source, world, layer):
    authored = REVIEWED_DORMANT_SPAWNER_FLAGS.get(source.files[layer])
    if not authored:
        return set()
    references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                           if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    scripts = script_text(source)
    if any(api in scripts for api in SCRIPT_FLAG_APIS):
        return set()
    return {f'"{flag}"' for flag, readers in authored
            if flag not in scripts and references.count(f'"{flag}"') == readers
            and len(re.findall(rf'^ +m_conditionFlag "{flag}"\s*$', references, re.M)) == readers}


def spawn_capture_latches(source, world, layers, captures, used_flags):
    reads = placed_spawner_flag_reads(source, layers)
    candidates = {}
    for quoted, check in captures.items():
        flag = quoted[1:-1]
        fields = [line.strip() for line in check]
        has_stop_override = any(line.startswith('m_bStopAfterFirstChange ') for line in fields)
        has_messages = any(line.startswith('m_Messages ') for line in fields)
        if reads[flag] and not has_stop_override and not has_messages:
            candidates[quoted] = False
        elif reads[flag] and 'm_bStopAfterFirstChange 1' in fields and has_messages and flag not in used_flags:
            candidates[quoted] = True
    if not candidates:
        return {}
    scripts = script_text(source)
    references = '\n'.join(damage_flag_reference_text(source.read(path)) for path in sorted(source.closure(world))
                           if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    latches = {}
    for quoted, owns_messages in candidates.items():
        flag = quoted[1:-1]
        # The spawner schedules once on the first positive observation. Other
        # literal consumers can continue to observe the area's reversible state.
        # A first-change tracker can own the notice when every consumer is a spawner.
        other_reads = 0 if owns_messages else literal_flag_reads(references, flag)
        if (flag not in scripts
                and references.count(quoted) == 1 + reads[flag] + other_reads):
            name = 'GC_MigrationSpawnCapture_' + guid(world + '/spawn capture/' + flag)
            if name not in references and name not in scripts:
                latches[flag] = name
    return latches


def stock_library_aliases(source, stock, occupied):
    """Resolve the stock PrefabLibrary folder move while retaining each GUID."""
    prefix = 'PrefabLibrary/'
    current_prefix = prefix + 'DefaultLibrary/'
    candidates = set()
    for blob in source.blobs.values():
        if b'}PrefabLibrary/' in blob:
            candidates.update((resource.upper(), path) for resource, path in REF.findall(blob.decode('utf-8-sig'))
                              if path.startswith(prefix) and not path.startswith(current_prefix))
    aliases = {}
    for resource, path in sorted(candidates):
        if resource in occupied:
            continue
        canonical = current_prefix + path.removeprefix(prefix)
        if stock.registered_paths(resource) == [canonical] and stock.registered_ids(canonical) == [resource]:
            aliases[resource, path] = (resource, canonical)
    return aliases


WCS_COLLIMATOR_RENAMES = (
    ('1D595DCB97075A58', 'Optic_TrijiconRMR/Collim_RMR.et',
     'Trijicon_RMR/Collim_Trijicon_RMR.et'),
    ('2C1CA987E4304D9E', 'Optic_AcroP2/Collim_AcroP2_Black.et',
     'Aimpoint_ACRO_P2/Collim_Aimpoint_ACRO_P2_BLK.et'),
    ('2C1E9900B1F3FDE5', 'Optic_AimpointCompM5/Collim_AimpointCompM5_BLK.et',
     'Aimpoint_CompM5/Collim_Aimpoint_CompM5_BLK.et'),
    ('3D4B54552E600505', 'Optic_EotechEXPS3/Collim_EXPS3_BLK.et',
     'EOTech_EXPS3/Collim_EOTech_EXPS3_BLK.et'),
    ('3E048957C94835DB', 'Optic_AimpointCompM5/Collim_AimpointCompM5_FDE.et',
     'Aimpoint_CompM5/Collim_Aimpoint_CompM5_FDE.et'),
    ('3EE60991BBA7BC35', 'Optic_TrijiconRMR/Collim_RMR_FDE.et',
     'Trijicon_RMR/Collim_Trijicon_RMR_FDE.et'),
    ('40937549C55B8372', 'Optic_AcroP2/Collim_AcroP2_FDE.et',
     'Aimpoint_ACRO_P2/Collim_Aimpoint_ACRO_P2_FDE.et'),
    ('586EE7AA31738D92', 'Optic_SIGRomeo/Collim_SIGRomeo_BLK.et',
     'SIG_ROMEO4T/Collim_SIG_ROMEO4T_BLK.et'),
    ('648E66F8FA240BA6', 'Optic_AimpointCompM4s/Collim_AimpointCompM4s_BLK.et',
     'Aimpoint_CompM4s/Collim_Aimpoint_CompM4s_BLK.et'),
    ('769476AF829FC398', 'Optic_AimpointCompM4s/Collim_AimpointCompM4s_FDE.et',
     'Aimpoint_CompM4s/Collim_Aimpoint_CompM4s_FDE.et'),
    ('7BB2A49CD128753E', 'Optic_TrijiconMRO/Collim_MRO.et',
     'Trijicon_MRO/Collim_Trijicon_MRO.et'),
    ('9A609B24E6040FC9', 'Optic_TrijiconSRS/Collim_SRS.et',
     'Trijicon_SRS/Collim_Trijicon_SRS.et'),
    ('9BA30971E84691EA', 'Optic_EotechEXPS3/Collim_EXPS3_FDE.et',
     'EOTech_EXPS3/Collim_EOTech_EXPS3_FDE.et'),
    ('EE6C2C52B77E53D1', 'Optic_AimpointPRO/Collim_AIMPOINT-PRO_FDE.et',
     'Aimpoint_PRO/Collim_Aimpoint_PRO_FDE.et'),
    ('FC763C05CFC59BEF', 'Optic_AimpointPRO/Collim_AIMPOINT-PRO_blk.et',
     'Aimpoint_PRO/Collim_Aimpoint_PRO_BLK.et'),
)


def resource_aliases(workshop, units, stock):
    aliases = {(guid, old): (guid, new) for (guid, old), new in STOCK_RESOURCE_ALIASES.items()
               if stock and stock.contains(guid, new)}
    aliases.update({(old, path): (new, path) for (old, path), new in STOCK_RESOURCE_REKEYS.items()
                    if stock and not stock.registered_paths(old) and stock.contains(new, path)})
    databases = {}
    if workshop:
        if COMMANDOS_BLACK_HAWK_PARENT in workshop_resources(workshop, {
            'SikorskyH60Project_60ED3CC6E7E40221': (
                '01e5390c986af0406bd5134fbcf5ff890f28d0171bfb0bc9b14347f0d8114bca',
                (COMMANDOS_BLACK_HAWK_PARENT,),
            ),
        }):
            aliases[COMMANDOS_BLACK_HAWK_PARENT] = COMMANDOS_BLACK_HAWK_PARENT
        collimator_prefix = 'Prefabs/Weapons/Attachments/Optics/Collimators/'
        collimators = {(resource, collimator_prefix + current) for resource, _, current in WCS_COLLIMATOR_RENAMES}
        registered_collimators = workshop_resources(workshop, {
            'WCS_Scopes_62A668F513428630': (
                '2d4b23fe7c8f5ee505c595bb34f84544fdfa377d9903a95b28a15137bd65581b', collimators),
        })
        for resource, old, current in WCS_COLLIMATOR_RENAMES:
            reference = (resource, collimator_prefix + current)
            if reference in registered_collimators:
                aliases[resource, collimator_prefix + old] = reference
        hind_path = 'Prefabs/Vehicles/Helicopters/Mi24/'
        hind = HIND_DAMAGE_PARENT
        hind_refs = (hind, ('A8C732BE755F8BD0', hind_path + 'Mi24V_Base.et'),
                     ('8FCC897602A4BEB1', hind_path + 'VehParts/Weapons/Mi24V_Turret_9M120_X4.et'))
        transport_variants = {
            'Mi24V_supply.et': ('9F4841C20C2F4F12', hind_path + 'Mi24V_Supply.et'),
            'Mi24V_transport.et': ('F889C67BFCAD2CF7', hind_path + 'Mi24V_Transport.et'),
        }
        registered = workshop_resources(workshop, {
            HIND_PROVIDER: (
                HIND_DIGEST,
                (*hind_refs, *transport_variants.values())),
        })
        if set(hind_refs) <= registered:
            aliases[hind[0], hind_path + 'Mi24V_armed_UPK23.et'] = hind
        for original, reference in transport_variants.items():
            if reference in registered:
                aliases[reference[0], hind_path + original] = reference
        if stock.contains(*VOLHA_STOCK_PARENT) and VOLHA_MESH in workshop_resources(workshop, {
            'ArmaTerrainCore_60B524F7A5AE96A8': (
                '28574b99f5d6a12a7199a29d94194081b196b8ca37d854895fe296763cba778c', (VOLHA_MESH,)),
        }):
            aliases[VOLHA_MESH] = VOLHA_MESH
        zarichne = workshop_resources(workshop, {
            'Zarichne2025_65EA8C9C25882838': (
                'd72a1e7c7420d2a91313e0b13c0861902e31782e9a8590eff9d52b8c333cb261',
                [*ZARICHNE_NAVMESH_ALIASES.values(), ('FA50B49D2E7A581D', 'Worlds/Zarichne.ent')]),
        })
        if len(zarichne) == len(ZARICHNE_NAVMESH_ALIASES) + 1:
            aliases.update(ZARICHNE_NAVMESH_ALIASES)
        if MARINE_M299_CURRENT in workshop_resources(workshop, {
            COBRA_PROVIDER: (COBRA_DIGEST, (MARINE_M299_CURRENT,)),
        }):
            aliases[MARINE_M299] = MARINE_M299_CURRENT
        apache_path = 'Prefabs/Vehicles/Helicopters/AH64D/'
        apache = ('397971C2026D588F', apache_path + 'USArmy/AH64D_USArmy_AGM114L_X2.et')
        if apache in workshop_resources(workshop, {
            'WCS_AH-64D_6303360DA719E832': (
                '7f7cbdf17572449ef466802c5e834a7c6fdc64b14f2b96f08d20890800d7fb9b', (apache,)),
        }):
            aliases[apache[0], apache_path + 'AH64D_AGM114L_X2.et'] = apache
        for original, (provider, guid, path) in WORKSHOP_RESOURCE_ALIASES.items():
            if provider not in databases:
                registry = workshop / provider / 'resourceDatabase.rdb'
                databases[provider] = StockPrefabs(registry) if registry.is_file() else None
            database = databases[provider]
            if database and database.contains(guid, path):
                aliases[original] = (guid, path)
        directory = workshop / 'RHIB_5994614FB5AD35C2'
        archive = directory / 'data.pak'
        registry = directory / 'resourceDatabase.rdb'
        if archive.is_file() and registry.is_file():
            with archive.open('rb') as stream:
                digest = file_digest(stream, 'sha256').hexdigest()
            if digest == '4dc8b818337a350f678a96f31cf36447a4372d1830fa1beeb977a929e5dcd215':
                database = StockPrefabs(registry)
                for original, (leaf, mount, suffix) in RHIB_GUNBOATS.items():
                    current = original[0], RHIB_PATH + leaf
                    if (database.contains(*current)
                            and database.contains(mount, RHIB_PATH + f'RHIB_gun_mount_{suffix}.et')
                            and database.contains('FB8ABBE5AF24ED1E', RHIB_PATH + 'RHIB_gun_mount_Mk19_rear.et')):
                        aliases[original] = current
        directory = workshop / 'Wirecutters2_62F364B35E9B51B0'
        archive = directory / 'data.pak'
        registry = directory / 'resourceDatabase.rdb'
        if archive.is_file() and registry.is_file() and StockPrefabs(registry).contains(*WIRECUTTER_PREFAB):
            with archive.open('rb') as stream:
                if file_digest(stream, 'sha256').hexdigest() == (
                        '32d8df1a5e3a84efa2b132b8a5bc01e71efc3dafe2e9d7e1f3972e711f9335c9'):
                    aliases[WIRECUTTER_PREFAB] = WIRECUTTER_PREFAB
    for original, (current, path, digest) in UNITS_PREFAB_ALIASES.items():
        old, _ = original
        if ((old == current or old not in units.resources) and units.resources.get(current) == [path]
                and sha256(units.read(path).strip().encode()).hexdigest() == digest):
            aliases[original] = (current, path)
    return aliases


def registered_alias(resource, path, source, dev, aliases):
    if resource not in source.resources and resource not in dev.resources:
        alias = aliases.get((resource, path))
        if alias:
            return f'"{{{alias[0]}}}{alias[1]}"'
    return None


def reviewed_units_asset_paths(source_units, dev_units):
    aliases = {}
    arg_root = 'Prefabs/Characters/Factions/OPFOR/GC_ARG/1980_Regulars/'
    for identity in ('98167A5BFF14B2F2', '9FE2A9738832231C', 'BB64738FA06CE9D1', 'D557EF5068CAAD87',
                     'DB5AFCA42285E34F', 'ED0F3A045287A1B4', 'F0205A51BCCCE2C8'):
        paths = dev_units.resources.get(identity, ())
        if (len(paths) == 1 and paths[0].startswith(arg_root) and paths == source_units.resources.get(identity)
                and unchanged_units_parent(source_units, dev_units, identity)):
            path, = paths
            aliases[identity, 'worlds/TheLocalPub/TopMaloHouse/' + path] = identity, path
    icon = '5E4DDEE56C6D4D16'
    original = 'worlds/Blue/TheBearWentOvertheMountain2/Assets/Mujahadeen.edds'
    current = 'Images/GC_MUJ_icon.edds'
    if (source_units.resources.get(icon) == dev_units.resources.get(icon) == [current]
            and source_units.files[current] == dev_units.files[current]
            and source_units.files[current + '.meta'] == dev_units.files[current + '.meta']):
        aliases[icon, original] = icon, current
    directory = 'Prefabs/Groups/OPFOR/GC_NVA/'
    for identity, role, old_blob, new_blob in (
        ('ECE5B52C1476B039', 'CHQ', '1e8acff7a8a5d680091b2c39fa914a80853610e9',
         '818cea2295fdf55828b49ae1d0139504675bd25a'),
        ('F357F0DCAC2F8A5B', 'PHQ', '498c65d0482ea97553158a7e24162e6353580f44',
         'a8e9fea460f81f957517ccb8256ab254e1e514e0'),
    ):
        current = directory + f'Group_NVA_{role}.et'
        if (source_units.resources.get(identity) == dev_units.resources.get(identity) == [current]
                and source_units.files[current] == old_blob and dev_units.files[current] == new_blob):
            aliases[identity, directory + f'Group_GC_NVA_{role}.et'] = identity, current
    return aliases


def reviewed_faction_assets(source, dev, source_units, dev_units):
    path = 'worlds/TheLocalPub/BebbleIslandRaid/Configs/Factions/Argentina.conf'
    identity = '0BE3E7B63E7C70AC'
    if (source.files.get(path) != '02c5b555c513edaca3fa97238261294d15bdca5b'
            or source.resources.get(identity) != [path]
            or dev_units.resources.get(identity) != ['Configs/Factions/GC_ARG.conf']):
        return {}
    # The mission profile adds its own catalog and faction settings to INDFOR.
    # Units already uses the original GUID for a different faction profile.
    destination = path.removesuffix('.conf') + '_GC.conf'
    resource = guid(destination)
    if any(resource in item.resources for item in (source, source_units, dev_units)):
        return {}
    body = source.read(path)
    paths = {}
    for dependency, original in REF.findall(body):
        if not original.startswith('worlds/'):
            continue
        current = dev_units.resources.get(dependency, [])
        if len(current) != 1 or PurePosixPath(current[0]).suffix != PurePosixPath(original).suffix:
            return {}
        if original.endswith('.et') and not reviewed_native_ancestry(inheritance(source_units, dependency),
                                                                    inheritance(dev_units, dependency), dev_units):
            return {}
        if original.endswith('.emat'):
            legacy = source.resources.get(dependency, [])
            if len(legacy) != 1:
                return {}
            old_material = REF.sub(r'\1', source.read(legacy[0])).strip()
            new_material = REF.sub(r'\1', dev_units.read(current[0])).strip()
            if old_material != new_material:
                return {}
        paths[(dependency, original)] = current[0]
    body = REF.sub(lambda ref: f'"{{{ref[1]}}}{paths[ref.groups()]}"' if ref.groups() in paths else ref[0], body)
    meta = replace_reference(source.read(path + '.meta'), META_NAME, resource, destination)
    files = {destination: body, destination + '.meta': meta}
    if (dev.resources.get(resource) not in (None, [destination])
            or any(file in dev.files and dev.read(file) != text for file, text in files.items())):
        return {}
    return {(identity, path): (resource, destination, files)}


def replace_registered_paths(text, source, dev, aliases):
    return REF.sub(lambda ref: registered_alias(ref[1].upper(), ref[2], source, dev, aliases) or ref[0], text)


def verified_mission_bmp1(source, dev, aliases, external_parents):
    if any(external_parents.get(identity) != path for identity, path in (BMP1_PARENT, BMP1_TURRET_PARENT)):
        return False
    for (identity, path), blob in ((MISSION_BMP1, '81543b5b2b7666a89471ecd1e4a41c276b95aa10'),
                                   (MISSION_BMP1_TURRET, '27da82e29525946edfa1d13de0c1b64e8976b4d2')):
        if (source.resources.get(identity) != [path] or dev.resources.get(identity) != [path]
                or source.files[path] != blob
                or replace_registered_paths(source.read(path), source, dev, aliases).strip() != dev.read(path).strip()):
            return False
    return True


SYMBOLIC_MATCH_CONDITION = re.compile(
    r'(?m)^(?P<prefix>(?P<indent> +)GC_GameModeStateCondition "\{[\da-fA-F]{16}\}" \{\n'
    r'(?P=indent) m_eGameModeState )GAME$')


def screening_ai_world(dev):
    identity, path = BAD_ORB_AI_WORLD
    if dev.files.get(path) != BAD_ORB_AI_WORLD_BLOB or dev.resources.get(identity) != [path]:
        return None
    return f'SCR_AIWorld : "{{{identity}}}{path}" {{\n coords 0 0 0\n}}\n'


def repair_existing_layers(source, dev, world, aliases):
    """Stage verified changes to existing layers with the original file hash."""
    repairs, original_hashes = {}, {}
    layers = dev.layers[world.lower()]
    markers_from_batch = any('GC_LobbyGameMode GC_MigratedMission :' in dev.read(path) for path in layers)
    for path in layers:
        original = dev.read(path)
        updated = replace_registered_paths(original, source, dev, aliases)
        if (markers_from_batch and path in source.layers[world.lower()]
                and original.startswith('GenericEntity') and 'GC_BriefingMarkerComponent' in original):
            legacy = source.read(path)
            try:
                before = convert_layer(legacy, dev)
                if before and before[0].strip() == original.strip():
                    updated = replace_registered_paths(convert_layer(legacy, dev, draw_order=True)[0],
                                                       source, dev, aliases)
            except ValueError:
                # Layers with unknown or independently edited markers need a file comparison.
                pass
        updated = SYMBOLIC_MATCH_CONDITION.sub(lambda match: match['prefix'] + MATCH_PHASE_VALUE, updated)
        if 'm_sAttachmentGroupName ' in updated or 'm_bEnableMoveOnFreeze 0' in updated:
            try:
                hidden_group_fields = {start - 1 + index for start, block in blocks(updated)
                                       for index in unsupported_vehicle_group_fields(block)}
            except ValueError:
                hidden_group_fields = set()
            if hidden_group_fields:
                updated = ''.join(line for index, line in enumerate(updated.splitlines(keepends=True))
                                  if index not in hidden_group_fields)
        if (path == SCREENING_AI_WORLD_LAYER and source.files.get(path) == SCREENING_AI_WORLD_SOURCE
                and dev.files[path] in SCREENING_AI_WORLD_REVISIONS and updated.startswith('SCR_AIWorld {\n')):
            if native_world := screening_ai_world(dev):
                updated = native_world + updated.partition('\n}\n')[2]
        if updated != original:
            repairs[path] = updated
            original_hashes[path] = sha256(original.encode()).hexdigest()
    return repairs, original_hashes


FSM_ORANGE_TRACER = ('E385D042495DEC73', 'Assets/Weapons/Ammo/Tracers/hc3_tracer_Base_Orange.xob')
FSM_ORANGE_MATERIAL = ('7700A708C3B1AE6E', 'Assets/Weapons/Ammo/Tracers/Data/FSM_Tracer_Emit_Color_Orange.emat')
FSM_ORANGE_TRACER_BLOBS = {
    FSM_ORANGE_TRACER[1]: 'cc0ea55743b2375154c48f15d7ea0ed8633cd9e1',
    FSM_ORANGE_TRACER[1] + '.meta': 'c83e945c2957d880258cdfb31162bcbf8c0f4bb0',
    FSM_ORANGE_TRACER[1].removesuffix('.xob') + '.fbx': '1d67c95616d48ea6d34f1ccb299a1445b17cda4a',
    FSM_ORANGE_TRACER[1].removesuffix('.xob') + '.txo': '66b6e18d9bf8828a28e509148147e49b325beb14',
    FSM_ORANGE_MATERIAL[1]: '465bceae7b76d8445fc5e5bc4517b3906af854e1',
    FSM_ORANGE_MATERIAL[1] + '.meta': '3a8008691c74cc0d2978afb7ae10bcce84bb304e',
}
COMMANDOS_DOG_ROOT = 'worlds/arc/CommandosReforged/'
COMMANDOS_DOG_RESOURCES = {
    '0E59A495FEB3F552': 'Prefabs/Dog.et',
    '7A0FB5C37F8437B2': 'Prefabs/Dog_Knife.et',
    '8A390301136E2634': 'Assets/DogBark.acp',
}
COMMANDOS_DOG_TREE_HASH = '4b0be8c33ab42a87feb1da36b6ce4a0e771e6a9e195cc1909a7fe415ebf9a593'


def fsm_orange_tracer_assets(source, dev, aliases):
    prefix = 'worlds/FSM/'
    for identity, path in (FSM_ORANGE_TRACER, FSM_ORANGE_MATERIAL):
        if (source.resources.get(identity) != [prefix + path]
                or dev.resources.get(identity) not in (None, [path])):
            raise ValueError('Orange tracer mesh or material registration changed')
    files = {}
    for path, blob in FSM_ORANGE_TRACER_BLOBS.items():
        original = prefix + path
        if source.files.get(original) != blob:
            raise ValueError('Orange tracer mesh, import source or material changed')
        # The compiled mesh embeds the original material path. Keep both registrations there.
        body = (replace_registered_paths(source.read(original), source, dev, aliases)
                if path.endswith(('.meta', '.emat')) else source.read_raw(original))
        if path in dev.files:
            existing = dev.read_raw(path) if isinstance(body, bytes) else dev.read(path)
            if existing != body:
                raise ValueError('Orange tracer asset would overwrite different Dev content')
        files[path] = body
    return files


def commandos_dog_assets(source, dev, aliases):
    paths = {COMMANDOS_DOG_ROOT + name for name in (*COMMANDOS_DOG_RESOURCES.values(), 'Assets/DogBark.wav')}
    paths.update(path for path in source.files if path.startswith(COMMANDOS_DOG_ROOT + 'Assets/Dog/')
                 and not path.endswith(('.meta', 'German Shepherd Dog.txo')))
    paths = sorted(paths | {path + '.meta' for path in paths if path + '.meta' in source.files})
    signature = sha256('\n'.join(f'{path} {source.files.get(path)}' for path in paths).encode()).hexdigest()
    if signature != COMMANDOS_DOG_TREE_HASH:
        raise ValueError('Commandos dog resource tree changed')
    files = {}
    for path in paths:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in SOURCE_TEXT | {'.meta', '.acp'}:
            body = source.read_raw(path).decode('utf-8-sig').replace('\r\n', '\n')
            body = replace_registered_paths(body, source, dev, aliases)
            if suffix == '.meta':
                identity = META_NAME.search(body)
                target = path.removesuffix('.meta')
                if (not identity or source.resources.get(identity[2]) != [target]
                        or dev.resources.get(identity[2]) not in (None, [target])):
                    raise ValueError('Commandos dog resource identity collision')
                body = replace_reference(body, META_NAME, identity[2], target)
            if path in dev.files and dev.read_raw(path).decode('utf-8-sig').replace('\r\n', '\n') != body:
                raise ValueError('Commandos dog resource would overwrite different Dev content')
        else:
            body = source.read_raw(path)
            if path in dev.files and dev.read_raw(path) != body:
                raise ValueError('Commandos dog asset would overwrite different Dev content')
        if path not in dev.files:
            files[path] = body
    return files


def commandos_black_hawk(source, aliases):
    if (source.files.get(COMMANDOS_BLACK_HAWK) != '3ea9e9583dfc4118371e36f9e1163eeedfacba3d'
            or aliases.get(COMMANDOS_BLACK_HAWK_PARENT) != COMMANDOS_BLACK_HAWK_PARENT):
        raise ValueError('Commandos Black Hawk needs the reviewed objective and H-60 prefab')
    lines = source.read(COMMANDOS_BLACK_HAWK).splitlines()
    start = lines.index('   FuelNodes {')
    end = block_end(lines, start)
    nodes = [f'    SCR_FuelNode "{{{tank}}}" {{\n     MaxFuel 0\n     m_fInitialFuelTankState 0\n    }}'
             for tank in COMMANDOS_BLACK_HAWK_TANKS]
    # Zero both current tanks. The other three authored IDs no longer exist on the parent.
    return '\n'.join([*lines[:start + 1], *nodes, *lines[end:], ''])


def commandos_marker(source, path):
    if (source.files.get(COMMANDOS_OBJECTIVE_MARKER) != '15d8920014534efd3ad16a710e1d3985da7337cd'
            or source.files.get(COMMANDOS_DEFAULT_MARKER) != '02bc84124f05f7929622066601407bafbdc6d0d0'):
        raise ValueError('Commandos marker templates changed')
    objective = source.read(COMMANDOS_OBJECTIVE_MARKER).splitlines()
    # Native markers publish their snapshots through the Lobby manager.
    style = objective[block_end(objective, 2) + 1:-1]
    inherited = None
    legacy = objective
    if path == COMMANDOS_DEFAULT_MARKER:
        legacy = source.read(path).splitlines()
        # The child inherits the marker style, but supplies its own transform.
        inherited = properties(style, MARKER_FIELDS | {'m_iZOrder'})
        for field in CAPTURE_TRANSFORM:
            inherited.pop(field, None)
        style = legacy[2:-1]
    rendered = marker_instance(style, '', inherited, draw_order=True).splitlines()
    rendered.insert(1, legacy[1])
    return '\n'.join([*rendered, ''])


def archived_volha_assets(source, dev, path, identity):
    if path in source.files or identity in source.resources or dev.resources.get(identity) not in (None, [path]):
        raise ValueError('archived wreck prefab identity collision')
    parent, parent_path = VOLHA_STOCK_PARENT
    mesh, mesh_path = VOLHA_MESH
    # ArmaTerrainCore 1.1.7 used this stock wreck inheritance and mesh override.
    # The installed provider retains the original mesh but replaced the template.
    body = f'''GenericEntity : "{{{parent}}}{parent_path}" {{
 ID "F0DBA538AC2A0552"
 components {{
  MeshObject "{{5872F0EB7FA40561}}" {{
   Object "{{{mesh}}}{mesh_path}"
  }}
 }}
 coords 2863.265 2.499 2303.186
 angleY 157
}}
'''
    metadata = replace_reference(source.read('worlds/Perfk/Prefabs/Groups/PK_MUJ_04_Fighters.et.meta'),
                                 META_NAME, identity, path)
    files = {path: body, path + '.meta': metadata}
    if any(name in dev.files and dev.read(name).strip() != text.strip() for name, text in files.items()):
        raise ValueError('archived wreck prefab would overwrite different Dev content')
    return {name: text for name, text in files.items() if name not in dev.files}


LOOSE_ENDS_COMPUTER_IMAGE = 'Missions/Corax/LooseEnds/Computer.edds'
SUMMER_DAYS_SATELLITE = 'worlds/Corax/SummerDays/SummerDays.edds'
ARCHIVED_MISSION_IMAGES = {
    ('B044199ADB2E7A7C', LOOSE_ENDS_COMPUTER_IMAGE): (
        'worlds/Corax/TF141/COOPXX_Loose Ends_V1/COOPXX_LooseEnds_V1_Layers/Briefing.layer',
        '272c3a708f50d1a6089a3cc710e91497f705b8d7', {
            LOOSE_ENDS_COMPUTER_IMAGE: 'c9e30be31c2668c8a503872d7f0033549ee4171a',
            LOOSE_ENDS_COMPUTER_IMAGE + '.meta': '7cbdd4e4731ff4e01a712bd261d030d9d026c415',
            'Missions/Corax/LooseEnds/Computer.jpg': 'f0baabb2639887f66cae71e949f75b86b3eb2785',
        }),
    ('C8BA8787536DB9A7', SUMMER_DAYS_SATELLITE): (SUMMER_DAYS_DEFAULT, SUMMER_DAYS_DEFAULT_BLOB, {
        SUMMER_DAYS_SATELLITE: '1213fd398f81c55d50bc18d76105e43b94e6f806',
        SUMMER_DAYS_SATELLITE + '.meta': '64027788bca402537ff561970d0c899f88a84114',
    }),
}


def reviewed_archived_mission_crew(source, dev, stock):
    """Restore the original AI-only crew assets removed with Central Anizay AAS."""
    if (not stock or source.files.get(ASSAD_FRAMEWORK_LAYER) != ASSAD_FRAMEWORK_BLOB
            or source.files.get(ASSAD_LAYERS + 'props.layer') != '15d7559e5c9b917375979b7d6f0090e5360771a8'):
        return {}
    restored = {}
    for resource, (role, asset_blob, meta_blob) in ARCHIVED_AAS_CREW.items():
        name = f'Character_GC_INSURGENT_AAS_{role}.et'
        path = 'worlds/arc/AAS/CentralAnizay/Prefabs/' + name
        archived_path = 'Prefabs/Characters/Factions/OPFOR/GC_INSURGENT/' + name
        if resource in source.resources or dev.resources.get(resource, [path]) != [path]:
            raise ValueError('archived AAS crew resource identity collision ' + resource)
        data = []
        for blob in (asset_blob, meta_blob):
            data.append(check_output(['git', '-C', str(source.repo), 'cat-file', 'blob', blob])
                        .decode('utf-8-sig').replace('\r\n', '\n'))
        body, meta = data
        identity = META_NAME.search(meta)
        parent = REF.search(body.partition('\n')[0])
        if (not identity or identity.groups()[1:3] != (resource, archived_path)
                or not body.startswith('SCR_ChimeraCharacter : ') or LEGACY.search(body)
                or not parent or not stock.contains(*parent.groups())):
            raise ValueError('archived AAS crew registration or native parent changed ' + resource)
        meta = replace_reference(meta, META_NAME, resource, path)
        for relative, content in ((path, body), (path + '.meta', meta)):
            if relative in dev.files and dev.read(relative) != content:
                raise ValueError('Dev already has a different archived crew asset ' + relative)
        restored[(resource, path)] = body, meta
    return restored


def reviewed_wirecutter_inventory(source, owner):
    blob = source.files.get(owner)
    if owner == GRAVETTE_WIRECUTTER_ROLE and blob == '0c1bdbf6239721b499105469fed6b5520a4ee099':
        return GRAVETTE_WIRECUTTER, owner + '.meta'
    if blob and BEBBLE_WIRECUTTER_ROLES.get(owner) == blob:
        return BEBBLE_WIRECUTTER, BEBBLE_SPECIAL_FORCES + '2IC_P.et.meta'
    return None


def migrate_assets(source, dev, draft, asset_aliases, local_loadouts, spawned_assets, faction_assets, archived_crew):
    files = list(draft)
    blockers = set()
    for owner in files:
        if PurePosixPath(owner).suffix.lower() not in SOURCE_TEXT:
            continue

        def relocate(ref):
            resource, path = ref[1].upper(), ref[2]
            if (dog_path := COMMANDOS_DOG_RESOURCES.get(resource)) and path == COMMANDOS_DOG_ROOT + dog_path:
                if path not in draft and path not in dev.files:
                    try:
                        assets = commandos_dog_assets(source, dev, asset_aliases)
                        if any(name in draft and draft[name] != body for name, body in assets.items()):
                            raise ValueError('Commandos dog resource differs from an earlier draft')
                        draft.update(assets)
                    except ValueError as exc:
                        blockers.add((owner, path, str(exc)))
                return ref[0]
            if crew := archived_crew.get((resource, path)):
                if path not in draft and path not in dev.files:
                    draft[path], draft[path + '.meta'] = crew
                    files.append(path)
                return ref[0]
            if ((archived := ARCHIVED_MISSION_IMAGES.get((resource, path)))
                    and resource not in source.resources and resource not in dev.resources
                    and path not in draft):
                placement, expected, assets = archived
                if source.files.get(placement) == expected:
                    try:
                        draft.update({name: check_output(['git', '-C', str(source.repo), 'cat-file', 'blob', blob])
                                      for name, blob in assets.items()})
                    except (OSError, CalledProcessError):
                        blockers.add((owner, path, 'original mission image blobs are unavailable'))
                return ref[0]
            if (resource, path) == ARCHIVED_VOLHA and VOLHA_MESH in asset_aliases:
                try:
                    destination = 'worlds/Perfk/Prefabs/Scenery/GC_ArchivedVolhaWreck.et'
                    identity = guid(destination)
                    draft.update(archived_volha_assets(source, dev, destination, identity))
                except ValueError as exc:
                    blockers.add((owner, path, str(exc)))
                    return ref[0]
                return f'"{{{identity}}}{destination}"'
            if ((resource, path) == ('8554ED3F088BADA6', WIRECUTTER_PREFAB[1])
                    and WIRECUTTER_PREFAB in asset_aliases
                    and (wirecutter := reviewed_wirecutter_inventory(source, owner))):
                destination, metadata_source = wirecutter
                identity = guid(destination)
                if identity in source.resources or dev.resources.get(identity) not in (None, [destination]):
                    blockers.add((owner, path, 'wirecutter compatibility prefab identity collision'))
                    return ref[0]
                parent, parent_path = WIRECUTTER_PREFAB
                draft[destination] = '\n'.join([
                    f'GenericEntity : "{{{parent}}}{parent_path}" {{',
                    ' ID "508AB2013EEE1E00"', *wirecutter_inventory_fields(), '}', '',
                ])
                draft[destination + '.meta'] = replace_reference(
                    source.read(metadata_source), META_NAME, identity, destination)
                return f'"{{{identity}}}{destination}"'
            if faction := faction_assets.get((resource, path)):
                identity, destination, assets = faction
                draft.update(assets)
                return f'"{{{identity}}}{destination}"'
            alias = registered_alias(resource, path, source, dev, asset_aliases)
            if alias:
                return alias
            old_ai_path, new_ai_path = AI_WORLD_ALIASES.get(resource, (None, None))
            if path == old_ai_path and dev.resources.get(resource) == [new_ai_path]:
                identity = META_NAME.search(dev.read(new_ai_path + '.meta'))
                if identity and identity[3] == new_ai_path and dev.read(new_ai_path).startswith('SCR_AIWorld : '):
                    return f'"{{{ref[1]}}}{new_ai_path}"'
            paths = source.resources.get(resource, [])
            if (resource, path) == FSM_ORANGE_TRACER:
                try:
                    if path not in draft:
                        draft.update(fsm_orange_tracer_assets(source, dev, asset_aliases))
                except ValueError as exc:
                    blockers.add((owner, path, str(exc)))
                return ref[0]
            if not paths or path in draft or path in dev.files:
                return ref[0]
            try:
                if len(paths) != 1:
                    raise ValueError('ambiguous source resource')
                canonical = paths[0]
                meta = canonical + '.meta'
                identity = META_NAME.search(source.read(meta))
                moved_material = (identity and canonical.endswith('.emat') and (
                    PurePosixPath(path).name == PurePosixPath(canonical).name == PurePosixPath(identity[3]).name
                    or (resource, path, canonical, identity[3]) == BULGES_RED_MATERIAL))
                moved_warhead = (owner == ZARICHNE_MINE
                                 and source.files.get(owner) == '4e4194d349321ca4fe86ef3d4c317a8cbf837476'
                                 and resource == '1D1694497FF79C23' and canonical == ZARICHNE_WARHEAD
                                 and path == 'worlds/HitTheMan/Battle For Rasman/Warhead_Mine_M15AT_HTM.et')
                moved_garrison_radio = (
                    resource == '19311332068509C9' and canonical == LOCAL_PROBLEMS_RADIO
                    and source.files[canonical] == '857b299e53863d2008260e1e12cdc8a75a24bcf3'
                    and source.files[meta] == '39944bfeb464c3499c6f13d08ec8b02a1fb76c40'
                    and path == LOCAL_PROBLEMS_RADIO.replace('_Single.et', '_Multi.et'))
                moved_stashed_texture = (
                    owner == STASHED_CACHE_GRIP_MATERIAL
                    and source.files.get(owner) == 'bfd6e5cf2f16e57f24937bdbfeba8fdd0fab71da'
                    and (resource, canonical) == ('89A1B384262CD56F', STASHED_CACHE_GRIP_TEXTURE)
                    and source.files[canonical] == '8da3bbe7c38650431926ac7e5cbdc65d6c81cee8'
                    and source.files[meta] == 'de7d0d9283fa39f082f08dad82158970f115602c'
                    and path == 'Assets/Weapons/Rifles/AK74/Data/AK74_Grip_Pink_BCR.edds')
                if (not identity or identity[2].upper() != resource
                        or (path != canonical and identity[3] not in (path, canonical)
                            and not (moved_material or moved_warhead or moved_garrison_radio or moved_stashed_texture))):
                    raise ValueError('resource path disagrees with metadata')
                if any(other != canonical for other in dev.resources.get(resource, [])):
                    raise ValueError('Dev resource identity collision')
                if canonical not in draft:
                    approved = local_loadouts.get(resource)
                    objective = spawned_assets.get(resource)
                    if objective:
                        draft[canonical] = objective['body']
                    elif canonical == COMMANDOS_BLACK_HAWK:
                        draft[canonical] = commandos_black_hawk(source, asset_aliases)
                    elif canonical in (COMMANDOS_OBJECTIVE_MARKER, COMMANDOS_DEFAULT_MARKER):
                        draft[canonical] = commandos_marker(source, canonical)
                    elif canonical == COMMANDOS_INTERACTION:
                        if source.files[canonical] != COMMANDOS_INTERACTION_BLOB:
                            raise ValueError('Commandos objective interaction template changed')
                        draft[canonical] = '\n'.join(commandos_interactions(source.read(canonical).splitlines())) + '\n'
                    else:
                        draft[canonical] = approved[1] if approved else passive_asset(source, canonical)
                    draft[meta] = replace_reference(source.read(meta), META_NAME, identity[2], canonical)
                    files.append(canonical)
                return f'"{{{ref[1]}}}{canonical}"'
            except ValueError as exc:
                blockers.add((owner, path, str(exc)))
                return ref[0]

        draft[owner] = REF.sub(relocate, draft[owner])
    return [{'rule': 'mission-owned resource', 'file': owner, 'detail': path, 'reason': reason}
            for owner, path, reason in sorted(blockers)]


TRIGGER = re.compile(r'(?P<group>\$grp )?TILW_(?P<kind>Extraction|Faction)TriggerEntity(?: (?P<name>[\w.-]+))?'
                     r' : "\{(?P<guid>192ABD6B9232E064|8C576D46F6579229)\}[^"\n]+" \{')
TEMPLATE_WORLD = 'worlds/TemplateMission/TemplateMission.ent'
TEMPLATE_SETTINGS_NOTE = ('4. In Workbench, duplicate TemplateMission_GC_Settings.et into your mission folder. '
                          'Reassign the GC_LobbyGameMode in your default layer to the copy before editing '
                          'briefings or mission rules.')
TEMPLATE_TRIGGER_PATHS = {
    'worlds/TemplateMission/TemplateMission_Layers/SYSTEMS/Triggers/Capture.layer': '843785c1340060e2046f5e7161e26d6a1481d065',
    'worlds/TemplateMission/TemplateMission_Layers/SYSTEMS/Triggers/Extract.layer': '2375bbeb375662a178c062dfd27de46b150583d7',
}
PRESENCE_TRIGGER = re.compile(r'(?P<group>\$grp )?TILW_(?P<kind>Presence)TriggerEntity(?: (?P<name>[\w.-]+))?'
                              r' : "\{826E6F2246328952\}Prefabs/Logic/Triggers/TILW_PresenceTrigger\.et" \{')
BARE_PRESENCE_TRIGGER = re.compile(r'(?P<group>\$grp )?TILW_(?P<kind>Presence)TriggerEntity'
                                   r'(?: (?P<name>[\w.-]+))? \{')
ROUTE_OBSTACLE_LAYER = 'worlds/Til/RouteTremaux/RouteTremaux_Layers/obstacles_removable.layer'
ROUTE_OBSTACLE_LAYER_BLOB = '06f0036f9a45d0989f462285e56011e0a6fdaee5'
ROUTE_OBSTACLE_PREFABS = {
    'worlds/Til/RouteTremaux/Prefabs/obstacles/obstacle_mines_1.et': '6bb7f4cca722c085521e12e04aaef87df8e45c94',
    'worlds/Til/RouteTremaux/Prefabs/obstacles/obstacle_wire_1.et': '0f55ff42f932e65f62df958f41b38ba41e5b6db3',
    'worlds/Til/RouteTremaux/Prefabs/obstacles/obstacle_wire_2.et': 'ad24f9762180fd59e186e20e0a7e8d23c081f658',
}
ROUTE_OBSTACLE_GROUP = re.compile(r'\$grp GenericEntity : ' + REF.pattern + r' \{')
PRESENCE_OVERRIDE = re.compile(r'TILW_PresenceTriggerEntity ([\w.-]+) \{')
OBSTACLE_TARGET = re.compile(r'(GenericEntity|StaticModelEntity) ([\w.-]+) \{')
PRESENCE_MESSAGES = (
    '"Established presence at %2."', '"Establishing presence at %2."', '"No longer establishing presence at %2."',
    '"Abandoned presence at %2."', '"Abandoning presence at %2."', '"No longer abandoning presence at %2."',
)


def render_area_flag_observer(world, area, flag, check, stop, *, reset_after_discovery=False, status_only=False):
    key = f'{world}/area observer/{area}'
    if status_only:
        check = check.copy()
    else:
        timer = next(i for i, line in enumerate(check) if line.lstrip().startswith('m_Capture '))
        check = [*check[:timer], check[-1]]
    check[0] = check[0].replace('GC_AreaCondition ', 'm_Area GC_AreaCondition ', 1)
    check = [re.sub(r'"\{([A-F0-9]{16})\}"', lambda match: f'"{{{guid(key + "/" + match[1])}}}"',
                    line[1:]) for line in check]
    return [f'    GC_AreaFlagObserver "{{{guid(key)}}}" {{',
            *([] if status_only else [f'     m_sFlag {flag}']),
            *(['     m_bStopAfterMatchChange 1'] if stop else []),
            *(['     m_bResetAfterDiscovery 1'] if reset_after_discovery else []), *check, '    }']


def reviewed_area_flag_readers(source, world):
    readers = {}
    for layer in source.layers[world.lower()]:
        blob, kind, targets = REVIEWED_AREA_FLAG_READERS.get(layer, (None, None, {}))
        if source.files[layer] == blob:
            readers.update({flag: (kind, target) for flag, target in targets.items()})
    return readers


def reviewed_area_flag_reference_text(source, path):
    text = source.read(path)
    if path == BEACH_HOLIDAY_DEFAULT and source.files[path] == BEACH_HOLIDAY_DEFAULT_BLOB:
        return re.sub(r'(?m)^ +m_sDescription "Factory"$', '', text)
    if path == DESERT_SENTINEL_MARKERS and source.files[path] == DESERT_SENTINEL_MARKERS_BLOB:
        # These three marker descriptions are display text, not flag consumers.
        return re.sub(r'(?m)^ +m_sDescription "extract"$', '', text)
    if path == ZARICHNE_DEFAULT and source.files[path] == ZARICHNE_DEFAULT_BLOB:
        return re.sub(r'(?m)^ +(?:m_locationName|m_sDescription) "Compound"$', '', text)
    if path == KHANDAR_AI_LAYER and source.files[path] == KHANDAR_AI_BLOB:
        return re.sub(r'(?m)^( +m_waypointNames \{\n) +("top")\n', r'\1', text)
    return text


def checked_area_flag_reads(flag, readers, events, derived):
    if flag not in readers:
        return 0
    kind, target = readers[flag]
    if kind in ('event', 'event with redundant clear'):
        uses = [event.get('name') for event in events.values() if 'gap' not in event
                for term in leaves(event) if term.get('flag') == flag]
        expected = (target,) if isinstance(target, str) else target
        if Counter(uses) == Counter(expected):
            clears = sum(action.get('omittedFalseClear') == flag for event in events.values()
                         for action in event.get('convertedActions', ()))
            if kind == 'event' or clears == 1:
                return len(expected) + expected.count(flag) + clears
    elif sum(term.get('flag') == flag for term in leaves(derived.get(target, {}))) == 1:
        return 1
    return 0


def attached_trigger_map(fields, dev, *, convert_radius=None):
    parent, children = trigger_children(fields)
    output, amounts = [], Counter()
    for _, block in children:
        child = '\n'.join(block) + '\n'
        if block[0].startswith(('PS_ManualMarker ', '$grp PS_ManualMarker ')):
            rendered, count = convert_layer(child, dev, draw_order=True)
            amounts['attachedMarkers'] += count
        elif STANDALONE_SHAPE.fullmatch(block[0]):
            if any(re.match(r'^ (?:angles|angle[XYZ]|scale) ', field) for field in parent):
                raise ValueError('attached map shape has a rotated or scaled trigger parent')
            rendered, audit = convert_boundaries(child, extended_shapes=True)
            if not rendered or any('gap' in row for row in audit.values()):
                gap = next((row['gap'] for row in audit.values() if 'gap' in row), 'no converted shape')
                raise ValueError('attached map shape: ' + gap)
            amounts['attachedMapShapes'] += len(audit)
        elif (spawner := PLACED_SPAWNER.fullmatch(block[0])) and not spawner['group'] and spawner['name']:
            # The spawner pass replaces this child after its flags and prefab resolve.
            rendered = child
            amounts['attachedSpawners'] += 1
        elif convert_radius and (trigger := PRESENCE_TRIGGER.fullmatch(block[0]) or TRIGGER.fullmatch(block[0])):
            rendered = convert_radius(trigger, block[1:-1])
            amounts['attachedRadiusTriggers'] += 1
        elif ((head := PLACED_ENTITY.fullmatch(block[0])) and head['name'] and not head['group']
              and re.fullmatch(WAYPOINT_CLASS, head['kind']) and REF.search(block[0])
              and all(FIELD.fullmatch(field.strip()) or re.fullmatch(rf' CompletionRadius {NUMBER}', field)
                      for field in block[1:-1])):
            rendered = child
            amounts['attachedWaypoints'] += 1
        else:
            raise ValueError('unsupported trigger child: ' + block[0].removeprefix('$grp ').split()[0])
        output.extend('  ' + line for line in rendered.splitlines())
    if not output:
        raise ValueError('empty trigger child list')
    return parent, output, amounts


def flag_area_name(world, flag):
    token = flag[1:-1]
    if len(token.encode()) > 100 or not re.fullmatch(r'[\w.-]+', token):
        token = guid(world + '/area/' + flag)
    return 'GC_MigrationArea_' + token


def attached_radius_rule(world, trigger, fields, scripts):
    """Convert a child of a radius trigger in the original local coordinate space."""
    name = trigger['name']
    if trigger['group'] or (name and name in scripts):
        raise ValueError('grouped or scripted child radius trigger')
    flag_fields = [field.removeprefix(' m_flagName ') for field in fields if field.startswith(' m_flagName ')]
    if len(flag_fields) != 1 or not LEGACY_FLAG.fullmatch(flag_fields[0]) or len(flag_fields[0].encode()) > 130:
        raise ValueError('child radius trigger needs one valid flag')
    flag, = flag_fields
    if flag[1:-1] in scripts:
        raise ValueError('child radius trigger may be controlled by mission scripts')
    area_name = name or flag_area_name(world, flag)
    if not any(field.startswith(' coords ') for field in fields):
        fields = [' coords 0 0 0', *fields]
    if trigger['kind'] == 'Presence':
        rule, area, details = presence_rule(world, area_name, flag, fields)
    elif trigger['kind'] == 'Extraction' and trigger['guid'] == '192ABD6B9232E064':
        rule, area, details = extraction_rule(world, area_name, flag, fields)
    else:
        raise ValueError('unsupported child radius trigger identity')
    return flag, rule, area, {'area': area_name, **details}


JAWS_OBJECTIVES = 'worlds/Jub/OperationJaws/OperationJaws_Layers/OBJs.layer'
JAWS_OBJECTIVES_BLOB = '067bf721e4a21f0efd05beb7f753d76a1f8a3a8f'


def omit_empty_capture_outline(fields):
    """Remove a verified child that cannot draw its line without points."""
    children = fields.index(' {')
    start = next((index for index in range(children + 1, block_end(fields, children))
                  if STANDALONE_SHAPE.fullmatch(fields[index].strip())), None)
    if start is None:
        raise ValueError('expected the reviewed empty outline')
    stop = block_end(fields, start)
    if any(line.strip().startswith(('Points ', 'm_drawArea 1')) for line in fields[start + 1:stop]):
        raise ValueError('capture outline has authored geometry')
    return [*fields[:start], *fields[stop + 1:]]


def iron_cipher_radius_placement(source, block):
    reference = REF.search(block[0])
    prefab = IRON_CIPHER_RADIUS_PREFABS.get(reference[1]) if reference else None
    if not prefab:
        return block
    path, blob = prefab
    if (reference[2] != path or source.files.get(path) != blob
            or source.resources.get(reference[1]) != [path]):
        return block
    parent, _, *fields = source.read(path).splitlines()
    overrides = {line.strip().partition(' ')[0] for line in block[1:-1]}
    # These reviewed placements override scalar values. Keep entire inherited
    # blocks for the intel entity names and the authored presence messages.
    inherited = [field for field in fields[:-1] if
                 not (field.startswith(' ') and not field.startswith('  ')
                      and field.strip().partition(' ')[0] in overrides)]
    return [parent, *inherited, *block[1:]]


def area_rules(source, world, dev=None):
    conditions, placements, audit = {}, {}, {}
    references = None
    scripts = None
    for layer in source.layers[world.lower()]:
        text = source.read(layer)
        writer_blob, writer_flags = REVIEWED_AREA_FLAG_WRITERS.get(layer, (None, {}))
        if source.files[layer] != writer_blob:
            writer_flags = {}
        if not any(f'TILW_{kind}TriggerEntity' in text for kind in ('Extraction', 'Faction', 'Presence')):
            continue
        for line, block in blocks(text):
            if (layer == IRON_CIPHER_EXTRACT_LAYER
                    and source.files[layer] == '929203d8bd170d964eaf9ed22997e6fcc2d700ae'):
                block = iron_cipher_radius_placement(source, block)
            if layer == ROUTE_OBSTACLE_LAYER and source.files[layer] == ROUTE_OBSTACLE_LAYER_BLOB:
                try:
                    checks, converted, records = route_obstacle_placements(source, world, block, conditions)
                except (ArithmeticError, KeyError, ValueError) as exc:
                    audit[f'{layer}:{line}'] = {'gap': str(exc)}
                else:
                    conditions.update(checks)
                    placements[(layer, line)] = checks, converted
                    audit[f'{layer}:{line}'] = records
                continue
            match = TRIGGER.fullmatch(block[0]) or PRESENCE_TRIGGER.fullmatch(block[0])
            if not match and layer == LOCAL_FILES_DEFAULT and source.files[layer] == LOCAL_FILES_DEFAULT_BLOB:
                match = BARE_PRESENCE_TRIGGER.fullmatch(block[0])
            shape = polygon_capture.SHAPE.fullmatch(block[0])
            if AO_SHAPE.fullmatch(block[0]) or STANDALONE_SHAPE.fullmatch(block[0]):
                if any(TRIGGER.fullmatch(field.strip()) or PRESENCE_TRIGGER.fullmatch(field.strip())
                       for field in block[1:-1]):
                    try:
                        checks, converted, records = nested_map_areas(
                            source, world, block, conditions, shape_scope=f'{layer}:{line}')
                    except (ArithmeticError, KeyError, ValueError) as exc:
                        audit[f'{layer}:{line}'] = {'gap': str(exc)}
                    else:
                        conditions.update(checks)
                        placements[(layer, line)] = checks, converted
                        audit[f'{layer}:{line}'] = records
                continue
            if not match and not shape:
                if block[0] == SCENARIO_AREA_GROUP and source.files[layer] in REVIEWED_BUILDING_AREAS:
                    try:
                        checks, converted, records = building_area_captures(source, world, block, conditions)
                    except (ArithmeticError, KeyError, ValueError) as exc:
                        audit[f'{layer}:{line}'] = {'gap': str(exc)}
                    else:
                        conditions.update(checks)
                        placements[(layer, line)] = checks, converted
                        audit[f'{layer}:{line}'] = records
                continue
            if shape and not any(TRIGGER.fullmatch(field.strip()) for field in block):
                continue
            root = shape or match
            result, areas, capture_details = {}, [], {}
            flags = set()
            try:
                for index, (name, fields) in enumerate(polygon_capture.members(block, root['group'], root['name'])):
                    trigger = match
                    attached = []
                    child_rules = {}
                    child_areas = set()
                    pilot_spawn = None
                    empty_outline = False
                    if shape:
                        if not any(TRIGGER.fullmatch(field.strip()) for field in fields):
                            parent, visual, amounts = attached_trigger_map(fields, dev)
                            if not amounts['attachedMapShapes'] or any(LEGACY.search(field) for field in parent):
                                raise ValueError('capture group sibling needs a native polygon and map shape')
                            head = 'PolylineShapeEntity' + (f' {name}' if name else '') + ' {'
                            areas.append('\n'.join([head, *parent, ' {', *visual, ' }', '}', '']))
                            continue
                        if any(field.startswith('  TILW_AreaSpawningComponent ') for field in fields):
                            prefab_blob, spawn_layer, layer_blob, _, _ = REVIEWED_GROUP_CALLSIGNS[
                                LOST_AND_FOUND_PILOT_GROUP]
                            if (layer != spawn_layer or source.files.get(layer) != layer_blob
                                    or source.files.get(LOST_AND_FOUND_PILOT_GROUP) != prefab_blob
                                    or source.resources.get(LOST_AND_FOUND_PILOT_ID) != [LOST_AND_FOUND_PILOT_GROUP]):
                                raise ValueError('polygon startup spawn needs a verified group and placement')
                            pilot_spawn = ('658EF768737FE0CE', guid(layer + '/startup-group'),
                                           f'"{{{LOST_AND_FOUND_PILOT_ID}}}{LOST_AND_FOUND_PILOT_GROUP}"')
                        if layer == JAWS_OBJECTIVES and source.files[layer] == JAWS_OBJECTIVES_BLOB and line == 1:
                            fields = omit_empty_capture_outline(fields)
                            empty_outline = True
                        placement, (trigger, fields), vertices = polygon_capture.read(
                            fields, TRIGGER, startup_prefab=pilot_spawn)
                    elif ' {' in fields:
                        def convert_radius(child, child_fields):
                            flag, child_rule, child_area, details = attached_radius_rule(
                                world, child, child_fields, script_text(source))
                            if flag in conditions or flag in flags or flag[1:-1] in writer_flags:
                                raise ValueError('duplicate child radius flag ' + flag)
                            if details['area'] in child_areas:
                                raise ValueError('duplicate child radius area ' + details['area'])
                            flags.add(flag)
                            child_areas.add(details['area'])
                            child_rules[flag] = child_rule
                            capture_details[flag[1:-1]] = details
                            return child_area

                        fields, attached, amounts = attached_trigger_map(fields, dev, convert_radius=convert_radius)
                    flag_field = next((value.strip() for value in fields if value.strip().startswith('m_flagName ')),
                                      'm_flagName ""')
                    flag = flag_field.removeprefix('m_flagName ')
                    status_only = flag == '""'
                    if status_only:
                        if (not name or shape or trigger['kind'] != 'Faction'
                                or not any(field.strip() == 'm_sendStatusMessages 1' for field in fields)):
                            raise ValueError('area trigger has no explicit flag')
                        if references is None:
                            references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                                                   if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
                        if scripts is None:
                            scripts = script_text(source)
                        flag = f'"GC_MigrationStatus_{guid(f"{layer}:{line}/{name}")}"'
                        if flag in references or flag[1:-1] in scripts or f'"{name}"' in scripts:
                            raise ValueError('capture status name conflicts with authored mission content')
                        fields = [field for field in fields if field.strip() != 'm_flagName ""']
                        fields.append(' m_flagName ' + flag)
                    if not LEGACY_FLAG.fullmatch(flag) or len(flag.encode()) > 130:
                        raise ValueError('invalid area flag ' + flag)
                    shared_writer = flag[1:-1] in writer_flags
                    if not shared_writer and (flag in conditions or flag in flags):
                        raise ValueError('duplicate area flag ' + flag)
                    if not shared_writer:
                        flags.add(flag)
                    area_name = flag_area_name(world, flag)
                    if shared_writer and not name:
                        area_name += '_' + guid(f'{layer}:{line}/{index}')
                    if shape or trigger['kind'] in ('Faction', 'Presence'):
                        area_name = name or area_name
                    if area_name in child_areas:
                        raise ValueError('child radius area uses its parent name ' + area_name)
                    if trigger['kind'] == 'Presence':
                        if scripts is None:
                            scripts = script_text(source)
                        if (name and name in scripts) or flag[1:-1] in scripts:
                            raise ValueError('presence trigger may be controlled by mission scripts')
                        rule, area, details = presence_rule(world, area_name, flag, fields)
                    elif trigger['kind'] == 'Extraction' and trigger['guid'] == '192ABD6B9232E064':
                        if scripts is None:
                            scripts = script_text(source)
                        if (name and f'"{name}"' in scripts) or flag in scripts:
                            raise ValueError('extraction trigger may be controlled by mission scripts')
                        rule, area, details = extraction_rule(world, area_name, flag, fields, polygon=bool(shape))
                    elif trigger['kind'] == 'Faction' and trigger['guid'] == '8C576D46F6579229':
                        if shared_writer and layer == BEACH_HOLIDAY_WAVES and flag == '"Waves"':
                            # This option controls result callbacks. The silent
                            # trigger writes its flag in every phase either way.
                            fields = [field for field in fields if field.strip() != 'm_eventsOnlyDuringGame 0']
                        built, area, details = capture_instance(world, area_name, fields, polygon=bool(shape))
                        rule = built[flag]
                    else:
                        raise ValueError('inconsistent trigger identity')
                    if shared_writer:
                        if (shape or attached or details['seconds'] or details.get('skipDiscovery')
                                or any('m_Messages ' in item for item in rule)):
                            raise ValueError('shared area writer must be an immediate silent radius')
                        reset = layer == INTERSECTING_DEFAULT and flag == '"reinforce"'
                        observer = render_area_flag_observer(world, area_name, flag, rule, details['stopsAfterFirstChange'],
                                                             reset_after_discovery=reset)
                        capture_details[area_name] = {'area': area_name, **details, 'writesFlag': flag[1:-1],
                                                      'areaFlagObserver': observer}
                        if reset:
                            capture_details[area_name]['resetAfterDiscovery'] = True
                        areas.append(area)
                        continue
                    capture_details[flag[1:-1]] = {'area': area_name, **details}
                    if empty_outline:
                        capture_details[flag[1:-1]]['omittedEmptyOutline'] = True
                    if status_only:
                        capture_details[flag[1:-1]]['statusOnly'] = True
                    unused_presence = (layer == NAGALAM_HILLTOP_LAYER
                                       and source.files[layer] == '2a7bc142928320aff39efbec9bc0ec2214a3e57d'
                                       and trigger['kind'] == 'Presence'
                                       and flag == '"Group_hilltop_is_not_present"')
                    unused_khandar = (layer == KHANDAR_AI_LAYER and source.files[layer] == KHANDAR_AI_BLOB
                                      and trigger['kind'] == 'Faction' and flag == '"top"')
                    if ((unused_presence or trigger['kind'] == 'Faction' and not name)
                            and not shape and not attached and not any('m_Messages ' in item for item in rule)):
                        if references is None:
                            references = '\n'.join(reviewed_area_flag_reference_text(source, path)
                                                   for path in sorted(source.closure(world))
                                                   if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
                        if scripts is None:
                            scripts = script_text(source)
                        script_name = flag if unused_khandar else flag[1:-1]
                        if references.count(flag) == 1 and script_name not in scripts:
                            capture_details[flag[1:-1]]['unused'] = True
                            if unused_presence or unused_khandar:
                                areas.append(area)
                            continue
                    if shape:
                        child = []
                        if anchor := trigger['name']:
                            if scripts is None:
                                scripts = script_text(source)
                            if anchor == area_name or f'"{anchor}"' in scripts:
                                raise ValueError('named capture child may be referenced by mission scripts')
                            transform = [field for field in fields if FIELD.fullmatch(field.strip())]
                            child = [' {', f'  GenericEntity {anchor} {{',
                                     *('  ' + field for field in transform), '  }', ' }']
                            capture_details[flag[1:-1]]['triggerAnchor'] = anchor
                        area = '\n'.join([f'PolylineShapeEntity {area_name} {{', *placement, *child, '}', ''])
                        capture_details.setdefault(flag[1:-1], {'area': area_name})
                        capture_details[flag[1:-1]]['polygonVertices'] = vertices
                        if any('GC_MapShapeComponent ' in row for row in placement):
                            capture_details[flag[1:-1]]['polygonMapOverlay'] = True
                        if pilot_spawn:
                            capture_details[flag[1:-1]]['startupPlayableSlots'] = 2
                    elif attached:
                        area = '\n'.join([*area.splitlines()[:-1], ' {', *attached, ' }', '}', ''])
                        capture_details.setdefault(flag[1:-1], {'area': area_name}).update(amounts)
                    result[flag] = rule
                    result.update(child_rules)
                    areas.append(area)
            except (ArithmeticError, KeyError, ValueError) as exc:
                audit[f'{layer}:{line}'] = {'gap': str(exc)}
                continue
            conditions.update(result)
            placements[(layer, line)] = (result, ''.join(areas))
            if capture_details:
                audit[f'{layer}:{line}'] = capture_details
    return conditions, placements, audit


def building_area_captures(source, world, block, known_conditions):
    checks, records = {}, {}
    scripts = script_text(source)
    references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                           if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    converted = block.copy()
    for start, stop, indent, child in reversed(list(nested_placement_blocks(block, TRIGGER))):
        trigger = TRIGGER.fullmatch(child[0])
        name = trigger['name']
        if (trigger['kind'] != 'Faction' or trigger['guid'] != '8C576D46F6579229'
                or trigger['group'] or not name or name in scripts):
            raise ValueError('unverified building capture trigger')
        built, area, details = capture_instance(world, name, child[1:-1])
        flag, = built
        if not LEGACY_FLAG.fullmatch(flag) or flag in known_conditions or flag in checks or flag[1:-1] in scripts:
            raise ValueError('invalid, duplicate or scripted building capture flag ' + flag)
        checks.update(built)
        records[flag[1:-1]] = {'area': name, **details}
        if (references.count(flag) == 1 and not any(api in scripts for api in SCRIPT_FLAG_APIS)
                and not any('m_Messages ' in row for row in built[flag])):
            del checks[flag]
            records[flag[1:-1]]['unused'] = True
        converted[start:stop + 1] = [' ' * indent + row for row in area.splitlines()]
    return checks, '\n'.join(converted) + '\n', records


def nested_map_areas(source, world, block, known_conditions, *, shape_scope):
    checks, records = {}, {}
    scripts = script_text(source)

    root = STANDALONE_SHAPE.fullmatch(block[0])
    if root and root['group']:
        block = block.copy()
        names = {}
        for start, end, name in group_members(block, 0, len(block) - 1, root):
            if name:
                continue
            children = [TRIGGER.fullmatch(row[3:]) for row in block[start + 1:end] if row.startswith('   ')]
            captures = [child for child in children if child and child['kind'] == 'Faction'
                        and child['guid'] == '8C576D46F6579229']
            if len(captures) == 1:
                anchor = captures[0]['name'] or f'{shape_scope}:{start}'
                names[start] = f'GC_MigrationArea_{guid(world + "/polygon/" + anchor)}'
        if names:
            references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                                   if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
            if len(set(names.values())) != len(names) or any(
                    name in references or name in scripts for name in names.values()):
                raise ValueError('generated polygon name conflicts with authored mission content')
            for start, name in names.items():
                block[start] = f' {name} {{'

    def render_trigger(area_name, child):
        root = TRIGGER.fullmatch(child[0]) or PRESENCE_TRIGGER.fullmatch(child[0])
        if not root or (root['kind'] != 'Presence'
                        and (root['kind'], root['guid']) not in {
                            ('Extraction', '192ABD6B9232E064'), ('Faction', '8C576D46F6579229')
                        }):
            raise ValueError('unsupported child on named map polygon')
        anchors = []
        for name, fields in polygon_capture.members(child, root['group'], root['name']):
            flag = next((value.strip().removeprefix('m_flagName ') for value in fields
                         if value.strip().startswith('m_flagName ')), '')
            if (not LEGACY_FLAG.fullmatch(flag) or len(flag.encode()) > 130
                    or flag in known_conditions or flag in checks):
                raise ValueError('invalid or duplicate nested area flag ' + flag)
            if (name and (name == area_name or f'"{name}"' in scripts)) or flag in scripts:
                raise ValueError('nested area trigger may be controlled by mission scripts')
            if root['kind'] == 'Faction':
                built, _, record = capture_instance(world, area_name, fields, polygon=True)
                check = built[flag]
            else:
                build = extraction_rule if root['kind'] == 'Extraction' else presence_rule
                check, _, record = build(world, area_name, flag, fields, polygon=True)
            checks[flag] = check
            records[flag[1:-1]] = {'area': area_name, **record}
            if name:
                transform = [field for field in fields if FIELD.fullmatch(field.strip())]
                anchors.extend([f'GenericEntity {name} {{', *transform, '}'])
                records[flag[1:-1]]['triggerAnchor'] = name
        return anchors

    converted, audit = convert_boundaries('\n'.join(block) + '\n', allow_public_faction_color=True,
                                          extended_shapes=True, area_children=render_trigger)
    if gap := next((entry['gap'] for entry in audit.values() if 'gap' in entry), None):
        raise ValueError(gap)
    if not converted or not checks:
        raise ValueError('named map polygon has no converted area triggers')
    return checks, converted, records


def presence_rule(world, area_name, flag, fields, *, polygon=False):
    data = properties(fields, {
        'm_queryRadius', 'm_captureTime', 'm_flagName', 'm_stopAfterFirstChange', 'm_entityNames',
        'm_allowDestroyed', 'm_ratioThreshold', 'm_comparisonMode', 'm_skipFirstQuery',
        'm_eventsOnlyDuringGame', 'm_sendStatusMessages', 'm_locationName', 'm_statusMessages', 'm_displayTo',
    } | CAPTURE_TRANSFORM | CAPTURE_EDITOR_FIELDS)
    names = target_names(data.get('m_entityNames', ''))
    transform = {key: value for key, value in data.items() if key in CAPTURE_TRANSFORM}
    radius, hold, ratio = (data.get('m_queryRadius', '25'), data.get('m_captureTime', '0'),
                           data.get('m_ratioThreshold', '1'))
    comparison = data.get('m_comparisonMode', 'EQUAL_OR_MORE')
    destroyed = data.get('m_allowDestroyed', '0')
    skip = data.get('m_skipFirstQuery', '0')
    stop = data.get('m_stopAfterFirstChange', '0')
    status = data.get('m_sendStatusMessages', '0')
    location = data.get('m_locationName', '"the location"')
    if (len(set(names)) != len(names) or (not polygon and 'coords' not in transform)
            or any(not FIELD.fullmatch(f'{key} {value}') for key, value in transform.items())
            or any(value not in ('0', '1') for value in (destroyed, skip, stop, status))
            or comparison not in ('0', '1', 'EQUAL_OR_LESS', 'EQUAL_OR_MORE')
            or data.get('m_eventsOnlyDuringGame', '1') != '1'
            or not all(re.fullmatch(NUMBER, n) for n in (radius, hold, ratio))
            or (not polygon and not Decimal('0.1') <= Decimal(radius) <= 100000)
            or not (0 <= Decimal(hold) <= 86400 and 0 <= Decimal(ratio) <= 1)
            or not re.fullmatch(QUOTED, location) or len(location.encode()) - 2 > 80
            or '<' in location or '>' in location
            or (status == '1' and '%1' in data.get('m_statusMessages', ''))):
        raise ValueError('unsupported named presence settings')
    key = world + '/presence/' + flag
    at_most = comparison in ('0', 'EQUAL_OR_LESS')
    maximum = '        m_bAtMost 1\n' if at_most else ''
    exclude = '        m_bExcludeDestroyed 1\n' if destroyed == '0' else ''
    discovery = '        m_bSkipDiscovery 1\n' if skip == '1' else ''
    once = '        m_bStopAfterFirstChange 1\n' if stop == '1' else ''
    recipients = faction_keys(data.get('m_displayTo', ''))
    messages = capture_messages(key, data, location, recipients, PRESENCE_MESSAGES)
    rule = f'''      GC_AreaCondition "{{{guid(key)}}}" {{
       m_sArea "{area_name}"
       m_Targets GC_NamedAreaTargets "{{{guid(key + '/entities')}}}" {{
        m_aNames {{
         {' '.join(names)}
        }}
        m_bCountMissingAsOutside 1
{exclude}       }}
       m_Requirement GC_LivingProportion "{{{guid(key + '/proportion')}}}" {{
        m_fRequiredProportion {ratio}
        m_bWholeNumberQuotient 1
{maximum}       }}
       m_Capture GC_AreaCaptureOptions "{{{guid(key + '/timer')}}}" {{
        m_fSeconds {hold}
        m_bUseElapsedTime 1
        m_iInterruption 3
{once}{discovery}{messages}       }}
      }}'''
    position = ''.join(f' {field} {value}\n' for field, value in transform.items())
    area = '' if polygon else f'''GC_RadiusAreaEntity {area_name} : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
{position} m_fRadius {radius}
}}
'''
    return rule.splitlines(), area, {
        'targets': [name[1:-1] for name in names], 'ratio': float(ratio),
        **({} if polygon else {'radiusMeters': float(radius)}),
        'comparison': 'at most' if at_most else 'at least', 'countsDestroyed': destroyed == '1',
        'quotient': 'whole number',
        'seconds': float(hold), 'skipDiscovery': skip == '1', 'stopsAfterFirstChange': stop == '1',
    }


@lru_cache(maxsize=12)
def route_obstacle_prefab(source, path):
    if source.files.get(path) != ROUTE_OBSTACLE_PREFABS[path]:
        raise ValueError('obstacle prefab differs from the reviewed source')
    original = source.read(path).splitlines()
    parent, children = trigger_children(original[1:-1])
    targets = {}
    converted = []
    presence = None
    for _, child in children:
        if PRESENCE_TRIGGER.fullmatch(child[0]):
            if presence:
                raise ValueError('obstacle prefab needs one presence trigger')
            identity = child[1]
            fields = child[2:-1]
            data = properties(fields, CAPTURE_TRANSFORM | CAPTURE_EDITOR_FIELDS | {
                'm_queryRadius', 'm_comparisonMode', 'm_stopAfterFirstChange', 'm_entityNames',
                'm_ratioThreshold',
            })
            # The placement supplies the flag and the full name array.
            start = fields.index(' m_entityNames {')
            end = block_end(fields, start)
            preserved = fields[:start] + fields[end + 1:]
            presence = identity, preserved
            child = ['GC_RadiusAreaEntity {', identity,
                     *(f' {key} {value}' for key, value in data.items() if key in CAPTURE_TRANSFORM),
                     ' m_fRadius ' + data['m_queryRadius'], '}']
        else:
            group = re.fullmatch(r'\$grp (GenericEntity|StaticModelEntity) : ' + REF.pattern + r' \{', child[0])
            if not group:
                raise ValueError('obstacle prefab contains an unreviewed target')
            for _, fields in polygon_capture.members(child, True, None):
                data = properties(fields, CAPTURE_TRANSFORM | {'ID'})
                if data['ID'] in targets:
                    raise ValueError('duplicate obstacle target ID')
                targets[data['ID']] = group[1]
        converted.extend('  ' + field for field in child)
    if not presence or not targets:
        raise ValueError('obstacle prefab needs a trigger and targets')
    return '\n'.join([original[0], *parent, ' {', *converted, ' }', '}', '']), presence, targets


def route_obstacle_placements(source, world, block, known_conditions):
    root = ROUTE_OBSTACLE_GROUP.fullmatch(block[0])
    if not root or root[2] not in ROUTE_OBSTACLE_PREFABS or source.resources.get(root[1].upper()) != [root[2]]:
        raise ValueError('obstacle placement needs a reviewed prefab')
    _, (trigger_id, inherited), template_targets = route_obstacle_prefab(source, root[2])
    checks, records, output = {}, {}, [block[0]]
    for name, fields in polygon_capture.members(block, True, None):
        if not re.fullmatch(r'obst_\d{2}', name or ''):
            raise ValueError('unsupported obstacle name')
        parent, children = trigger_children(fields)
        if not all(FIELD.fullmatch(field.strip()) for field in parent):
            raise ValueError('unsupported obstacle transform')
        targets = {}
        flag = area_name = None
        placed = []
        for _, child in children:
            if target := OBSTACLE_TARGET.fullmatch(child[0]):
                data = properties(child[1:-1], {'ID'})
                identity = data['ID']
                if identity in targets or template_targets.get(identity) != target[1]:
                    raise ValueError('obstacle target does not match its prefab')
                targets[identity] = target[2]
            elif (trigger := PRESENCE_OVERRIDE.fullmatch(child[0])) and not area_name and child[1] == trigger_id:
                area_name = trigger[1]
                data = properties(child[2:-1], {'m_flagName', 'm_entityNames'})
                flag = data['m_flagName']
                names = target_names(data['m_entityNames'])
                area_fields = child[2:-1]
                child = [f'GC_RadiusAreaEntity {area_name} {{', trigger_id, '}']
            else:
                raise ValueError('unsupported obstacle child override')
            placed.extend('   ' + field for field in child)
        if (not area_name or flag != f'"{name}"' or flag in known_conditions or flag in checks
                or set(targets) != template_targets.keys() or sorted(names) != sorted(f'"{target}"' for target in targets.values())
                or len(set(targets.values())) != len(targets)):
            raise ValueError('obstacle trigger targets must match its inherited children')
        position = [] if any(field.startswith(' coords ') for field in inherited) else [' coords 0 0 0']
        check, _, details = presence_rule(world, area_name, flag, [*position, *inherited, *area_fields])
        checks[flag] = check
        records[flag[1:-1]] = {'area': area_name, 'obstaclePrefab': root[2], **details}
        output.extend([f' {name} {{', *(' ' + field for field in parent), '  {', *placed, '  }', ' }'])
    return checks, '\n'.join([*output, '}', '']), records


def extraction_rule(world, area_name, flag, fields, *, polygon=False):
    data = properties(fields, {'m_queryRadius', 'm_captureTime', 'm_flagName', 'm_stopAfterFirstChange',
                               'm_factionKey', 'm_ratioThreshold', 'm_playersOnly', 'm_comparisonMode',
                               'm_skipFirstQuery', 'm_eventsOnlyDuringGame', 'm_locationName',
                               'm_sendStatusMessages', 'm_statusMessages', 'm_displayTo'} |
                      CAPTURE_TRANSFORM | CAPTURE_EDITOR_FIELDS)
    players = data.get('m_playersOnly', '1')
    stops = data.get('m_stopAfterFirstChange', '0')
    discovery = data.get('m_skipFirstQuery', '0')
    mode = data.get('m_comparisonMode', 'EQUAL_OR_MORE')
    enabled = data.get('m_sendStatusMessages', '0')
    location = data.get('m_locationName', '"the location"')
    if (any(value not in ('0', '1') for value in (players, stops, discovery, enabled))
            or mode not in ('0', '1', 'EQUAL_OR_LESS', 'EQUAL_OR_MORE')
            or not re.fullmatch(QUOTED, location)
            or (enabled == '1' and (len(location.encode()) - 2 > 80 or '<' in location or '>' in location))
            or data.get('m_eventsOnlyDuringGame', '1') != '1'):
        raise ValueError('unsupported extraction settings')
    faction = data['m_factionKey']
    transform = {key: value for key, value in data.items() if key in CAPTURE_TRANSFORM}
    radius = data.get('m_queryRadius', '25')
    hold, ratio = data.get('m_captureTime', '0'), data.get('m_ratioThreshold', '1')
    if (not re.fullmatch(r'"[\w.-]+"', faction)
            or not all(re.fullmatch(NUMBER, value) for value in (radius, hold, ratio))
            or not 0 <= Decimal(hold) <= 86400 or not 0 <= Decimal(ratio) <= 1
            or (not polygon and ('coords' not in transform or not Decimal('0.1') <= Decimal(radius) <= 100000))
            or any(not FIELD.fullmatch(f'{key} {value}') for key, value in transform.items())):
        raise ValueError('invalid extraction settings')
    at_most = mode in ('0', 'EQUAL_OR_LESS')
    notices = capture_status_texts(data, EXTRACTION_STATUS) if enabled == '1' else ()
    recipients = faction_keys(data.get('m_displayTo', '')) if notices else ()
    check = extraction_condition(world, flag[1:-1], area_name, faction, ratio, hold,
                                 skip_discovery=discovery == '1', latch=stops == '1', players_only=players == '1',
                                   at_most=at_most, whole_number_quotient=True,
                                   location=location if notices else None, status=notices, recipients=recipients)
    position = ''.join(f' {field} {value}\n' for field, value in transform.items())
    area = '' if polygon else f'''GC_RadiusAreaEntity {area_name} : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
{position} m_fRadius {radius}
}}
'''
    return ['      ' + value for value in check.splitlines()], area, {
        'population': 'players' if players == '1' else 'players and AI',
        'faction': faction[1:-1], 'seconds': float(hold), 'ratio': float(ratio),
        'comparison': 'at most' if at_most else 'at least', 'quotient': 'whole number',
        'stopsAfterFirstChange': stops == '1', 'skipDiscovery': discovery == '1',
        **({'statusMessages': True, 'statusRecipients': [key[1:-1] for key in recipients]} if notices else {}),
        **({} if polygon else {'radiusMeters': float(radius)}),
    }


def restriction_zones(block, base):
    header = block[0].removeprefix('$grp ')
    resource = re.fullmatch(r'SCR_EditorRestrictionZoneEntity(?: ([\w.-]+))? : ("[^"\n]+") \{', header)
    if not resource:
        raise ValueError('unsupported restriction zone prefab')
    if not block[0].startswith('$grp '):
        name = resource[1] or f'GC_MigrationStartingZone{base}'
        output = '\n'.join([f'SCR_EditorRestrictionZoneEntity {name} : {resource[2]} {{', *block[1:]])
        return [name], output + '\n'
    names, output = [], []
    index = 1
    while index < len(block) - 1:
        head = re.fullmatch(r' (?:(\w+) )?\{', block[index])
        if not head:
            raise ValueError('unsupported grouped restriction zone')
        end = block_end(block, index)
        name = head[1] or f'GC_MigrationStartingZone{base + len(names)}'
        names.append(name)
        output.extend([f'SCR_EditorRestrictionZoneEntity {name} : {resource[2]} {{',
                       *(line[1:] for line in block[index + 1:end + 1])])
        index = end + 1
    return names, '\n'.join(output) + '\n'


MODE_SINGLETONS = {
    'RadioManagerEntity': ('5ED9088735FF2DEE', 'A79C4E0A43DEB824', '', []),
    'ScriptedChatEntity': ('5ED9088735FF2DC2', 'A79C4E0A43DEB825', '', []),
    'ItemPreviewManagerEntity': (
        '5ED9088735FF2DD5', '5ED9088735FF2DD5',
        ' : "{9F18C476AB860F3B}Prefabs/World/Game/ItemPreviewManager.et"', [],
    ),
    'SCR_EditorSettingsEntity': ('61B4E967E6C20362', '61B4E967E6C20362', '', ['   m_bIsUnlimitedEditorLegal 1']),
}
NO_PHOTO_EDITOR = '{1197E0F8B1D64714}Prefabs/Editor/GC_EditorManagerNoPhoto.et'
STAGED_ACCESS_EDITOR = '{23C09F78414E6B35}Prefabs/Editor/GC_EditorManagerStagedAccess.et'


def mode_children(lines, disable_photo=False, lock_editor=False):
    output = [' {']
    seen = set()
    editor_prefab = NO_PHOTO_EDITOR if disable_photo else STAGED_ACCESS_EDITOR if lock_editor else None
    index = 1
    while index < len(lines) - 1:
        name = ' FactionManager' if lines[index] == '  SCR_FactionManager FactionManager {' else ''
        match = re.fullmatch(r'  (\w+)(?: "[\da-fA-F]{16}")? \{', lines[index].replace(name + ' {', ' {', 1))
        if not match or match[1] in seen:
            raise ValueError('custom or duplicate legacy mode child: ' + lines[index].strip())
        kind = match[1]
        seen.add(kind)
        end = block_end(lines, index)
        fields = lines[index + 1:end]
        if kind == 'SCR_FactionManager':
            if not fields or fields[0] != '   ID "5ED9088735FF2D8B"':
                raise ValueError('unexpected legacy faction manager ID')
            rest = fields[1:]
            coords = []
            if rest and rest[0].startswith('   coords ') and FIELD.fullmatch(rest[0].strip()):
                coords, rest = rest[:1], rest[1:]
            output.extend([f'  SCR_FactionManager{name} {{', '   ID "6ADC9BB66394BE46"', *coords])
            if rest:
                if (len(rest) < 3 or rest[0] != '   Factions {' or block_end(rest, 0) != len(rest) - 1
                        or LEGACY.search('\n'.join(rest))):
                    raise ValueError('custom legacy faction overrides')
                output.extend(['   Factions + {', *rest[1:]])
            output.append('  }')
        elif kind in MODE_SINGLETONS:
            previous, current, prefab, defaults = MODE_SINGLETONS[kind]
            if (not fields or fields[0] != f'   ID "{previous}"' or len(fields) > 2
                    or any(not field.startswith('   coords ') or not FIELD.fullmatch(field.strip())
                           for field in fields[1:])):
                raise ValueError('custom legacy ' + kind + ' settings')
            output.extend([f'  {kind}{prefab} {{', f'   ID "{current}"', *fields[1:], *defaults])
            if kind == 'SCR_EditorSettingsEntity' and editor_prefab:
                output.append(f'   m_EditorManagerPrefab "{editor_prefab}"')
            output.append('  }')
        else:
            raise ValueError('custom legacy mode child: ' + kind)
        index = end + 1
    if disable_photo and 'SCR_EditorSettingsEntity' not in seen:
        raise ValueError('disabled ArmaVision needs a verified editor settings placement')
    if lock_editor and 'SCR_EditorSettingsEntity' not in seen:
        _, identity, _, defaults = MODE_SINGLETONS['SCR_EditorSettingsEntity']
        output.extend(['  SCR_EditorSettingsEntity {', f'   ID "{identity}"', *defaults,
                       f'   m_EditorManagerPrefab "{editor_prefab}"', '  }'])
    elif not seen:
        raise ValueError('empty legacy mode children')
    return '\n'.join([*output, ' }', ''])


def render_mode(world, root, preset, additional_rules, zones, ai_faction=None, objective_flags=(), damage_flags=None,
                *, dynamic_respawn_factions=(), area_flag_observers=(), cache_supply_depot=False,
                inherited_immediate_release=False, capture_phase_entities=False, source_blob=None,
                derived_flag_containers=(), replaced_tracker=None, population_flags=None, commandos_component=None):
    if not MODE.fullmatch(root[0]):
        raise ValueError('custom legacy game mode')
    weather, environment, freeze = '', '', 0
    native_overrides = []
    child_placements = []
    disable_photo = None
    lock_editor = None
    restrict_staging_combat = None
    briefing_leader_markers = None
    transform = {}
    stashed_cache = source_blob == STASHED_CACHE_MODE_BLOB
    immediate_release = inherited_immediate_release or stashed_cache
    construction = {}
    jip = {}
    mode_components_seen = False
    reconnect = {}
    disabled_resources = ''
    respawn_table_seen = False
    respawn_fields = ''
    safe_start_seen = False
    controller_override = ''
    index = 1
    while index < len(root) - 1:
        line = root[index]
        if line == ' components {':
            end = block_end(root, index)
            if mode_components_seen or end == index + 1:
                raise ValueError('custom legacy game mode components')
            mode_components_seen = True
            component = index + 1
            seen = set()
            while component < end:
                header = root[component]
                stop = block_end(root, component)
                if header in seen or stop >= end:
                    raise ValueError('custom legacy game mode components')
                seen.add(header)
                component_lines = root[component:stop + 1]
                kind = header.strip().partition(' ')[0]
                if WEATHER.fullmatch(header) or (stashed_cache and kind == 'SCR_TimeAndWeatherHandlerComponent'):
                    handler, environment = convert_weather(world, component_lines)
                    weather += handler
                elif header == INIT_WEATHER and all(INIT_WEATHER_FIELD.fullmatch(field)
                                                    for field in component_lines[1:-1]):
                    weather += '\n'.join(component_lines) + '\n'
                elif ((header == DEFAULT_BUILDING_MANAGER and stop == component + 1)
                      or component_lines == EMPTY_GROUP_FLAGS
                      or (header in DISABLED_NATIVE_MODE_COMPONENTS
                          and component_lines[1:-1] == ['   Enabled 0'])
                      or NATIVE_END_SCREEN_SUBTITLES.fullmatch('\n'.join(component_lines))):
                    native_overrides.extend(component_lines)
                elif source_blob == ASSAD_FRAMEWORK_BLOB and component_lines == EMPTY_LEGACY_CUTSCENE:
                    # This mode inherits the disabled cutscene setting and has no cinematic entity.
                    pass
                elif header == replaced_tracker:
                    # The native marker placement owns the reviewed Intel tracking behavior.
                    pass
                elif commandos_component and header == COMMANDOS_MANAGER:
                    native_overrides.extend(commandos_component.splitlines())
                elif stashed_cache and kind in STASHED_CACHE_NATIVE_MODE:
                    native_overrides.extend(component_lines)
                elif stashed_cache and kind in STASHED_CACHE_INHERITED_MODE | STASHED_CACHE_LOBBY_MODE:
                    # The exact archive settings are inherited or owned by native Lobby components.
                    pass
                elif stashed_cache and kind == 'PS_ObjectiveManager':
                    # This authored table supplies a legacy debrief label with no registered objectives.
                    pass
                else:
                    raise ValueError('custom legacy game mode component: ' + kind)
                component = stop + 1
            index = end
        elif line.strip().partition(' ')[0] in SPAWNER_TRANSFORM and FIELD.fullmatch(line.strip()):
            field, _, value = line.strip().partition(' ')
            if field in transform:
                raise ValueError('duplicate game mode transform ' + field)
            transform[field] = value
        elif re.fullmatch(r' m_iFreezeTime \d+', line):
            milliseconds = int(line.split()[-1])
            freeze, fraction = divmod(milliseconds, 1000)
            if fraction:
                freeze = Decimal(milliseconds) / 1000
            immediate_release = not freeze
        elif line == ' m_aFactionRespawnCount {' and not respawn_table_seen:
            end = block_end(root, index)
            records, gaps = read_respawns('\n'.join([root[0], *root[index:end + 1], '}']), world)
            respawn_fields = authored_respawn_fields(world, records, gaps, dynamic_respawn_factions)
            respawn_table_seen = True
            index = end
        elif line.startswith((' m_eJIPState ', ' m_denyJIPTime ')):
            field, value = line.strip().split(' ', 1)
            if field in jip:
                raise ValueError('duplicate legacy JIP setting ' + field)
            jip[field] = value
        elif line.startswith((' m_iReconnectTime ', ' m_iReconnectTimeAfterBriefing ')):
            field, value = line.strip().split(' ', 1)
            if field in reconnect or not re.fullmatch(r'-?\d+', value) or not -1 <= int(value) <= 2147483647:
                raise ValueError('invalid legacy reconnect duration')
            milliseconds = int(value)
            if milliseconds > 0 and milliseconds % 1000:
                raise ValueError('legacy reconnect duration requires whole seconds')
            reconnect[field] = milliseconds // 1000 if milliseconds > 0 else -1
        elif re.fullmatch(r' m_safeStartTime -?\d+', line) and not safe_start_seen:
            # The published Core character controller never calls its safe-start handler.
            if not -2147483648 <= int(line.split()[-1]) <= 2147483647:
                raise ValueError('invalid inactive legacy safe-start time')
            safe_start_seen = True
        elif re.fullmatch(r' m_bDisableArmaVision [01]', line) and disable_photo is None:
            disable_photo = line.endswith(' 1')
        elif re.fullmatch(r' m_bDisableBuildingModeAfterFreezeTime [01]', line) and lock_editor is None:
            lock_editor = line.endswith(' 1')
        elif re.fullmatch(r' m_bFreezeTimeShootingForbiden [01]', line) and restrict_staging_combat is None:
            restrict_staging_combat = line.endswith(' 1')
        elif re.fullmatch(r' m_bMarkersOnlyOnBriefing [01]', line) and briefing_leader_markers is None:
            briefing_leader_markers = line.endswith(' 1')
        elif line == ' m_aDisabledResourceTypes {' and not disabled_resources:
            end = block_end(root, index)
            values = ' '.join(field.strip() for field in root[index + 1:end]).split()
            if len(set(values)) != len(values) or set(values) - {'SUPPLIES', 'ELECTRICITY'}:
                raise ValueError('unsupported disabled resource types')
            disabled_resources = '\n'.join(root[index:end + 1]) + '\n'
            index = end
        elif line.strip().partition(' ')[0] in CONSTRUCTION_FIELDS:
            field, _, value = line.strip().partition(' ')
            if field in construction:
                raise ValueError('duplicate construction progress field ' + field)
            construction[field] = value
        elif commandos_component and line == ' PlayerControllerPrefab ' + COMMANDOS_CONTROLLER and not controller_override:
            controller_override = line + '\n'
        elif line == ' {' and not child_placements:
            end = block_end(root, index)
            child_placements = root[index:end + 1]
            index = end
        else:
            raise ValueError('game mode field or child requires a conversion rule: ' + line.strip())
        index += 1
    if disable_photo and not child_placements:
        raise ValueError('disabled ArmaVision needs a verified editor settings placement')
    if lock_editor and (freeze > 3600 or not (freeze or immediate_release)
                        and '{9C2001FE7A2F2281}Prefabs/MP/Modes/PS_GameMode_Lobby.et' not in root[0]):
        raise ValueError('limited editor lock requires a verified staging duration')
    if restrict_staging_combat:
        if not 1 < freeze <= 3600:
            raise ValueError('combat staging requires a verified duration')
        # The legacy timer subtracts its first second before Match starts.
        freeze -= 1
    children = ''
    if child_placements or lock_editor:
        children = mode_children(child_placements or [' {', ' }'], disable_photo, lock_editor)
    jip_fields = ''
    if jip:
        mode = LEGACY_JIP_MODES.get(jip.get('m_eJIPState', 'Manual'))
        opening = jip.get('m_denyJIPTime', '-1')
        if not mode or not re.fullmatch(r'-?\d+', opening) or not -2147483648 <= int(opening) <= 2147483647:
            raise ValueError('unsupported legacy JIP settings')
        jip_fields = f' m_eJipMode {mode}\n'
        if mode != 'DISABLED':
            # Legacy blocks requests before this value, despite the field's name.
            jip_fields += f' m_iJipOpenSeconds {max(60, int(opening))}\n'
    if (zones and not (freeze or immediate_release)) or len(set(zones)) != len(zones):
        raise ValueError('staging duration requires uniquely named restriction zones')
    if 'angles' in transform and any(axis in transform for axis in ('angleX', 'angleY', 'angleZ')):
        raise ValueError('mixed game mode rotation representations')
    placement = '\n'.join(f' {field} {value}' for field, value in {'coords': '0 0 0', **transform}.items())
    resource = guid(world + '/resource')
    settings = ''.join(line + '\n' for line in native_overrides)
    if construction:
        if DEFAULT_BUILDING_MANAGER in native_overrides:
            raise ValueError('default building manager with legacy construction multipliers needs review')
        fields = []
        for field, (native, minimum, maximum) in CONSTRUCTION_FIELDS.items():
            if field not in construction:
                continue
            value = construction[field]
            if not re.fullmatch(NUMBER, value) or not minimum <= Decimal(value) <= maximum:
                raise ValueError('construction progress setting outside supported range: ' + field)
            fields.append(f'    {native} {value}\n')
        layout = '{96A8B496A076F1C0}scripts/Game/Building/CampaignBuildingCompositionOutline.conf'
        settings += f'''  SCR_CampaignBuildingManagerComponent "{{60EC2158F48C679A}}" {{
   m_sFreeRoamBuildingClientTrigger "{{5E191CEAF4B95816}}Prefabs/MP/FreeRoamBuildingClientTrigger.et"
   m_BudgetType CAMPAIGN
   m_iCompositionRefundPercentage 100
   m_OutlineManager GC_ConstructionOutlineManager "{{60EC2158E10CB829}}" : "{layout}" {{
{''.join(fields)}   }}
   m_sPrefabsToBuildResource "{{D2527D9AA5B4A33E}}Configs/Editor/PlaceableEntities/Compositions/Compositions_FreeRoamBuilding.conf"
  }}
'''
    if additional_rules or population_flags:
        settings += f'  GC_PeakFactionPopulationComponent "{{{guid(world + "/population")}}}" {{\n'
        if population_flags:
            settings += '   m_aObservedFlags {\n'
            for flag, (faction, ratio) in population_flags.items():
                identity = guid(f'{world}/population flag/{flag}')
                settings += (f'    GC_PeakFactionPopulationFlag "{{{identity}}}" {{\n'
                             f'     m_sName "{flag}"\n     m_sFaction "{faction}"\n     m_fRatio {ratio}\n    }}\n')
            settings += '   }\n'
        settings += '  }\n'
    if ai_faction:
        settings += f'''  GC_LivingAndDeadAIComponent "{{{guid(world + '/ai population')}}}" {{
   m_sFaction "{ai_faction}"
  }}
'''
    if zones:
        key = world + '/release staging zones'
        manager_type = ('GC_StagingRestrictionZoneManagerComponent' if restrict_staging_combat
                        else 'SCR_PlayersRestrictionZoneManagerComponent')
        settings += f'''  {manager_type} "{{{guid(key + '/manager')}}}" {{
  }}
'''
    if freeze or lock_editor:
        settings += '  GC_LobbyFreezeComponent "{E81E75BC287148D0}" {\n'
        if freeze:
            authored = ('m_fAuthoredCountdownSeconds' if isinstance(freeze, Decimal) else 'm_iAuthoredCountdownSeconds')
            settings += f'   {authored} {freeze}\n'
        if lock_editor:
            settings += '   m_bLockLimitedEditorAfterStaging 1\n'
        if restrict_staging_combat:
            settings += '   m_bRestrictAuthoredStagingCombat 1\n'
        settings += '  }\n'
    if zones:
        names = '\n'.join(f'        "{name}"' for name in zones)
        timer = f'''     m_aConditions {{
      GC_TimerCondition "{{{guid(key + '/timer')}}}" {{
       m_fSeconds {freeze:f}
      }}
     }}
''' if freeze else ''
        additional_rules += f'''    GC_Event "{{{guid(key)}}}" {{
     m_sName "Release staging zones"
{timer}     m_aActions {{
      GC_DeleteEntitiesAction "{{{guid(key + '/delete')}}}" {{
       m_aEntities {{
{names}
       }}
      }}
     }}
    }}
'''
    reconnect_fields = ''
    if reconnect:
        pregame = reconnect.get('m_iReconnectTime', 120)
        match = reconnect.get('m_iReconnectTimeAfterBriefing', -1)
        reconnect_fields = (f'   m_iSlotReservationTimeoutSeconds {pregame}\n'
                            f'   m_iMatchSlotReservationTimeoutSeconds {match}\n')
    if additional_rules or objective_flags or respawn_fields or reconnect_fields or area_flag_observers:
        flags = ''
        if objective_flags:
            flag_lines = []
            for flag in sorted(objective_flags):
                identity = guid(world + '/interaction flag/' + flag)
                flag_lines.extend([f'    GC_MissionFlag "{{{identity}}}" {{', f'     m_sName "{flag}"'])
                if damage_flags and flag in damage_flags:
                    target = damage_flags[flag]
                    condition = guid(world + '/damage flag condition/' + flag)
                    flag_lines.extend(['     m_aConditions {', f'      GC_EntityDamageCondition "{{{condition}}}" {{',
                                       f'       m_sEntityName "{target}"', '      }', '     }'])
                flag_lines.append('    }')
            flags = '   m_aMissionFlags + {\n' + '\n'.join(flag_lines) + '\n   }\n'
        events = f'   m_aMissionEvents + {{\n{additional_rules}   }}\n' if additional_rules else ''
        observers = ('   m_aAreaFlagObservers {\n' + '\n'.join(area_flag_observers) + '\n   }\n'
                     if area_flag_observers else '')
        derived = ('   m_aDerivedMissionFlags {\n' + '\n'.join(derived_flag_containers) + '\n   }\n'
                   if derived_flag_containers else '')
        settings += f'''
  GC_LobbyManager "{{6A137A39106A4973}}" {{
{flags}{events}{observers}{derived}{respawn_fields}{reconnect_fields}  }}
'''
    if cache_supply_depot:
        settings += f'  GC_MissionSupplyDepotComponent "{{{guid(world + "/supply depot")}}}" {{\n  }}\n'
    if capture_phase_entities:
        names = ' '.join(f'"{name}"' for name in RUHA_UNIT_NAMES)
        settings += (f'  GC_PhaseEntitySet "{{{guid(world + "/phase entities")}}}" {{\n'
                     f'   m_aTemporaryNames {{\n    {names}\n   }}\n'
                     '   m_sCapturedPrefix "PhaseUnit"\n  }\n')
    marker_fields = ' m_bBriefingLeaderMarkers 1\n' if briefing_leader_markers else ''
    return f'''GC_LobbyGameMode GC_MigratedMission : "{{{resource}}}{preset}" {{
 components {{
{weather}{environment}{settings} }}
{placement}
{jip_fields}{marker_fields}{disabled_resources}{controller_override}{children}}}
'''


def world_draft(source, dev, world, row, staged, area_placements, interactions, convert_group_block, asset_aliases,
                local_loadouts, spawned_assets, convert_crew, resolve_spawn, resolve_crew, resolve_crew_config, delayed=None,
                faction_assets=None, archived_crew=None):
    draft, blockers = {}, []
    reviewed_replacements = {}
    commandos_objectives = None
    commandos_runtime = {}
    if row.get('playtestCandidate') or row.get('raceConversion'):
        return {}, {'status': 'explicit recipe'}
    if not row['headers']:
        return {}, {'status': 'no launcher'}
    if world in dev.files:
        repairs, hashes = repair_existing_layers(source, dev, world, asset_aliases)
        if repairs:
            return repairs, {'status': 'generated for file review', 'files': sorted(repairs),
                             'reviewedReplacements': hashes, 'existingLayerRepairs': True, 'blockers': []}
        return {}, {'status': 'existing Dev world', 'review': row['headers']}
    preset = row.get('batchModePreset')
    if not preset or row['briefingGaps']:
        blockers.append({'rule': 'briefing preset', 'file': world, 'detail': row['briefingGaps']})
    if respawn := row.get('respawn'):
        try:
            authored_respawn_fields(world, respawn['factions'], respawn['ticketGaps'])
        except ValueError as exc:
            blockers.append({'rule': 'respawn', 'file': world, 'detail': str(exc)})
    outcome = row.get('outcomeCandidate', {})
    local_files_tracker = spawned_assets.get(LOCAL_FILES_INTEL[0], {}).get('trackingMarker')
    trifecta_tracker = spawned_assets.get(TRIFECTA_STM[0], {}).get('trackerName')
    rules = outcome.get('events', {})
    route_graph = outcome.get('orderedRouteFlags', {})
    route_flags = set(route_graph.get('areas', ()))
    route_observers = {}
    supported_interactions = [record for record in row.get('interactionCandidate', {}).values()
                              if isinstance(record, dict)]
    required_teleport_flags = {flag for record in supported_interactions for flag in record.get('teleportRequiredFlags', [])}
    required_interaction_flags = {flag for record in supported_interactions
                                  for flag in record.get('interactionRequiredFlags', [])}
    interaction_reads = Counter(flag for record in supported_interactions
                                for field in ('interactionRequiredFlags', 'teleportRequiredFlags')
                                for flag in record.get(field, []))
    dormant_interaction_flags = set()
    observed_teleport_flags = set()
    shared_writers = [record for area in row.get('captureCandidate', {}).values() for record in area.values()
                      if isinstance(record, dict) and 'areaFlagObserver' in record]
    shared_flags = {record['writesFlag'] for record in shared_writers}
    population_flags = outcome.get('populationFlags', {})
    if shared_writers:
        closure = '\n'.join(reviewed_area_flag_reference_text(source, path) for path in source.closure(world)
                            if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
        readers = placed_spawner_flag_reads(source, source.layers[world.lower()])
        writers = Counter(record['writesFlag'] for record in shared_writers)
        expected_writers = Counter()
        for layer in source.layers[world.lower()]:
            blob, flags = REVIEWED_AREA_FLAG_WRITERS.get(layer, (None, {}))
            if source.files[layer] == blob:
                expected_writers.update(flags)
        reviewed_readers = reviewed_area_flag_readers(source, world)
        derived = outcome.get('derivedSpawnerConditions', {})
        reviewed_reads = {flag: checked_area_flag_reads(flag, reviewed_readers, rules, derived) for flag in writers}
        timer_writers = set()
        if (source.files.get(OLD_WOOD_STEPOFF[0]) == OLD_WOOD_STEPOFF[1]
                and OLD_WOOD_STEPOFF[0] in source.layers[world.lower()]
                and source.files.get(OLD_WOOD_SPAWNERS[0]) == OLD_WOOD_SPAWNERS[1]
                and readers['UK_STEPOFF'] == 8 and any(
                    event.get('name') == 'STEPOFF_FALLBACK' and event.get('trigger') == 'Match start'
                    and any(action.get('setsFlag') == 'UK_STEPOFF' and action.get('delaySeconds') == '300'
                            for action in event.get('convertedActions', ())) for event in rules.values())):
            timer_writers.add('UK_STEPOFF')
        reset_flags = {record['writesFlag'] for record in shared_writers if record.get('resetAfterDiscovery')}
        if reset_flags != set(outcome.get('discoveryResetFlags', ())):
            blockers.append({'rule': 'shared area flags', 'file': world,
                             'detail': 'discovery reset must match a verified legacy meta definition'})
        scripts = script_text(source)
        if ('Factory' in writers and source.files.get(BEACH_HOLIDAY_APARTMENT) == BEACH_HOLIDAY_APARTMENT_BLOB
                and BEACH_HOLIDAY_APARTMENT in source.layers[world.lower()]
                and interaction_reads['Factory'] == 1):
            observed_teleport_flags.add('Factory')
            # The second apartment door uses the destination identifier as its
            # flag. Every other occurrence is a destination, never a writer.
            destination_free = re.sub(r'(?m)^ +m_locationName "FuelDepotPOS"$', '', closure)
            if (interaction_reads['FuelDepotPOS'] == 1 and destination_free.count('"FuelDepotPOS"') == 1
                    and 'FuelDepotPOS' not in scripts and not any(api in scripts for api in SCRIPT_FLAG_APIS)):
                dormant_interaction_flags.add('FuelDepotPOS')
        unresolved = sorted(flag for flag, count in writers.items()
                            if not (readers[flag] or reviewed_reads[flag]) or f'"{flag}"' in scripts
                            or closure.count(f'"{flag}"') != count + readers[flag] + reviewed_reads[flag]
                            + (flag in reset_flags) + (flag in population_flags) + (flag in timer_writers)
                            + (interaction_reads[flag] if flag in observed_teleport_flags else 0)
                            or (flag in reset_flags and (readers[flag] != 3
                                or source.files.get(INTERSECTING_DEFAULT.replace('default.layer', 'Opfor.layer'))
                                != '42a0e55cfa0d5197e995e1474a814e0a09e5ae5c')))
        if unresolved or writers != expected_writers or any(api in scripts for api in SCRIPT_FLAG_APIS):
            detail = 'expected all reviewed writers and only reviewed event or placed spawner readers'
            if unresolved:
                detail += ' for ' + ', '.join(unresolved)
            blockers.append({'rule': 'shared area flags', 'file': world,
                             'detail': detail})
    ticket_edits = [action for event in rules.values() for action in event.get('convertedActions', [])
                    if 'ticketFaction' in action]
    edited_factions = {action['ticketFaction'] for action in ticket_edits if action['ticketAmount']}
    source_respawns = {entry['faction'] for entry in row.get('respawn', {}).get('factions', [])}
    if edited_factions - source_respawns:
        blockers.append({'rule': 'respawn', 'file': world, 'detail': 'ticket edit lacks a verified respawn policy for '
                         + ', '.join(sorted(edited_factions - source_respawns))})
    notices = {location for location, entry in row.get('frameworkEvents', {}).items()
               if 'notificationDelaysSeconds' in entry}
    represented = notices | {location for location, entry in rules.items() if 'gap' not in entry}
    framework_supported = (len(represented) == row['serializedTypes'].get('TILW_MissionEvent', 0)
                           and not outcome.get('gap'))
    phase_entity_capture = (framework_supported and RUHA_PHASE_LAYER in source.layers[world.lower()]
                            and source.files.get(RUHA_PHASE_LAYER) == RUHA_PHASE_BLOB)
    temporary_names = RUHA_UNIT_NAMES if phase_entity_capture else ()
    rule_text = staged.get(outcome.get('stagedFragment'), '')
    terms = [term for event in rules.values() if 'gap' not in event for term in leaves(event)]
    used_flags = {term.get('flag') for term in terms}
    ai_faction = next((term['faction'] for term in terms if term['trigger'] == 'AI loss'), None)
    mode = None
    mode_index = None
    zones = []
    parts = {}
    boundaries = {}
    converted_cover = False
    converted_passive_cover = False
    converted_bare_map = False
    converted_hornbeams = False
    converted_artillery = False
    converted_scenery_map = False
    inactive_placements = []
    if 'FuelDepotPOS' in dormant_interaction_flags:
        inactive_placements.append('The original apartment door to FuelDepotPOS has no flag producer and stays locked.')
    reused_layers = set()
    group_blocks = []
    area_names = []
    objective_flags, interaction_placements = interactions
    damage_flags = {record['flag']: record['entity'] for record in row.get('damageFlagCandidates', {}).values()
                    if 'flag' in record and record['flag'] in objective_flags}
    roster_assets = {resource: asset for resource, asset in spawned_assets.items()
                     if 'deathFlag' in asset or 'rosterFlags' in asset}
    damage_flags.update({asset['deathFlag']: asset['entity'] for asset in roster_assets.values()
                         if not asset.get('playable')})
    teleport_targets = {target for record in row.get('interactionCandidate', {}).values() if isinstance(record, dict)
                        for target in record.get('teleportDestinations', [])}
    event_flags = {action['setsFlag'] for event in rules.values() for action in event.get('convertedActions', [])
                   if 'setsFlag' in action}
    script_flags = {flag for event in rules.values() for action in event.get('convertedActions', [])
                    for flag in action.get('producesFlags', ())}
    concrete_layer, concrete_blob = CONCRETE_FRAMEWORK
    concrete_crushers = (CONCRETE_COUNT in event_flags and concrete_layer in source.layers[world.lower()]
                         and source.files[concrete_layer] == concrete_blob)
    depot_layer = next((path for path in source.layers[world.lower()]
                        if path in DEPOT_SUPPLY_REVISIONS and source.files[path] == DEPOT_SUPPLY_REVISIONS[path][0]), None)
    cache_supply_depot = bool(script_flags) and framework_supported and depot_layer is not None
    event_flags.update(script_flags)
    interaction_writers = {action['flag'] for record in supported_interactions for action in record.get('objectives', [])}
    startup_samples = outcome.get('startupSamples', [])
    event_flags.update(flag for sample in startup_samples for flag in sample['flags'])
    draw_by_flag = {flag: index for index, sample in enumerate(startup_samples) if sample['drawCount'] == 1
                    for flag in sample['flags']}
    single_draw_flags = set(draw_by_flag)
    startup_sample_flags = {flag for sample in startup_samples for flag in sample['flags']}
    cleared_event_flags = {action['clearsFlag'] for event in rules.values() for action in event.get('convertedActions', [])
                           if 'clearsFlag' in action and not action.get('reviewedExternalFlagClear')}
    required_event_flags = {term['flag'] for term in terms if term['trigger'] == 'event flag'} | cleared_event_flags
    unresolved_event_flags = required_event_flags - event_flags - route_flags - set(route_graph.get('outputs', ()))
    if unresolved_event_flags:
        blockers.append({'rule': 'framework events', 'file': world,
                         'detail': {'missingConvertedFlagProducers': sorted(unresolved_event_flags)}})
    spawners, spawner_audit = {}, []
    if row['serializedTypes'].get('TILW_PrefabSpawnerEntity'):
        captures, placed_areas, _ = area_rules(source, world, dev)
        if NAGALAM_TOWN_SPAWN_LAYER in source.layers[world.lower()]:
            for line, block in blocks(source.read(NAGALAM_TOWN_SPAWN_LAYER)):
                if reviewed_spawner_container(source, NAGALAM_TOWN_SPAWN_LAYER, block):
                    placed_areas[(NAGALAM_TOWN_SPAWN_LAYER, line)] = ({}, '\n'.join(block) + '\n')
        area_placements = {**placed_areas, **area_placements}
        spawners, schedules, spawner_audit, attached = world_spawners(source, world, resolve_spawn, captures,
                                                           set(objective_flags) | event_flags | shared_flags, used_flags, blockers,
                                                           resolve_crew, resolve_crew_config, single_draw_flags,
                                                           outcome.get('derivedSpawnerConditions'), area_placements,
                                                           startup_sample_flags=startup_sample_flags,
                                                           spawned_assets=spawned_assets)
        for location, (checks, areas) in area_placements.items():
            layer, line = location
            area_lines = areas.splitlines()
            native_container = (areas.startswith(SCENARIO_AREA_GROUP + '\n')
                                or reviewed_spawner_container(source, layer, area_lines))
            for original, native in attached.get(location, ()):
                matches = [(start, stop, indent) for start, stop, indent, child
                           in nested_placement_blocks(area_lines, PLACED_SPAWNER) if child == original]
                if len(matches) != 1:
                    blockers.append({'rule': 'placed spawner layer', 'file': layer, 'line': line,
                                     'detail': 'attached spawner does not have a unique converted parent'})
                else:
                    (start, stop, indent), = matches
                    area_lines[start:stop + 1] = [' ' * indent + row for row in native]
            areas = '\n'.join(area_lines) + '\n' if attached.get(location) else areas
            if re.search(r'(?m)^ +(?:\$grp )?TILW_PrefabSpawnerEntity\b', areas):
                blockers.append({'rule': 'placed spawner layer', 'file': layer, 'line': line,
                                 'detail': 'area has an unconverted spawner child'})
            elif native_container:
                converted = native_placement(area_lines)
                if converted is None:
                    blockers.append({'rule': 'placed spawner layer', 'file': layer, 'line': line,
                                     'detail': 'spawner container has an unconverted legacy component'})
                else:
                    areas = converted
            area_placements[location] = checks, areas
        rule_text += schedules
        used_flags.update(entry['flag'] for entry in spawner_audit if entry['trigger'] == 'area capture')
        used_flags.update(item['flag'] for entry in spawner_audit if entry.get('derivedFlag')
                          for item in leaves(entry))
        if not ai_faction:
            ai_faction = next((item['faction'] for entry in spawner_audit if entry.get('derivedFlag')
                               for item in leaves(entry) if item['trigger'] == 'AI loss'), None)
    unused_flags = []
    status_areas = {}
    flag_references = None
    for layer in source.layers[world.lower()]:
        if layer in staged:
            parts[layer] = [staged[layer]]
            continue
        result = []
        parts[layer] = result
        try:
            for line, block in blocks(source.read(layer)):
                if layer == COMMANDOS_OBJECTIVES_LAYER and source.files[layer] == COMMANDOS_OBJECTIVES_BLOB:
                    block = commandos_interactions(block)
                head = block[0]
                body = '\n'.join(block) + '\n'
                kind = head.removeprefix('$grp ').split()[0]
                if kind == 'PS_GameModeCoop':
                    if mode is not None:
                        raise ValueError('multiple legacy game mode placements')
                    mode, mode_index = block, (layer, len(result))
                    result.append('')
                elif kind == 'PS_MissionDescription' and preset and not row['briefingGaps']:
                    continue
                elif (kind == 'SCR_PowerPole' and layer == TRIFECTA_ROOT + 'Trifecta_Layers/OBJECTIVE/SATELLITEPARTS.layer'
                      and source.files[layer] == 'f761f4c53d580b6f44fc58927f6f59aa991713df'
                      and not LEGACY.search(body)):
                    result.append(body)
                elif kind == 'EditorCommentEntity':
                    try:
                        validate_editor_comments(block)
                        if world == TEMPLATE_WORLD:
                            body = body.replace(
                                '"- Framework"', '"- Mission rules and briefings in the copied Lobby settings prefab"')
                            body = body.replace('"- PS_GameMode_Lobby_GC"', '"- GC_LobbyGameMode"')
                            step = next((field for field in block if '"3. Rename the folder to ' in field), '')
                            if step.endswith('\\'):
                                indent = step[:len(step) - len(step.lstrip())]
                                body = body.replace(step + '\n', step + '\n' + indent +
                                                    f'"{TEMPLATE_SETTINGS_NOTE}"\\\n', 1)
                        result.append(body)
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                elif world == TEMPLATE_WORLD and (trigger := TRIGGER.fullmatch(head)) and (
                        TEMPLATE_TRIGGER_PATHS.get(layer) == source.files[layer]
                        and trigger['name'] and len(block) == 3 and FIELD.fullmatch(block[1].strip())):
                    result.append(f'GC_RadiusAreaEntity {trigger["name"]} : '
                                  '"{2C4447C53A1E450B}Prefabs/Logic/Mission/RadiusArea.et" {\n'
                                  f'{block[1]}\n m_fRadius 25\n}}\n')
                elif FRAMEWORK.fullmatch(head) and (
                        (world == TEMPLATE_WORLD and source.files[layer] == 'f2c572e21672fb6eccb7e9c583a0109678944e00'
                         or layer == SUMMER_DAYS_DEFAULT and source.files[layer] == SUMMER_DAYS_DEFAULT_BLOB)
                        and len(block) == 3 and FIELD.fullmatch(block[1].strip())):
                    label = 'template framework' if world == TEMPLATE_WORLD else 'Summer Days framework'
                    inactive_placements.append(f'The original {label} is empty and runs no mission events.')
                elif (layer == VILLA_PROPS and source.files[layer] == VILLA_PROPS_BLOB
                      and source.files.get(VILLA_DEFAULT) == VILLA_DEFAULT_BLOB
                      and head.startswith('SCR_FactionManager "5EA8C932C68D400F" : "{4A188E44289B9A50}')
                      and block[1:] == [' coords 2654.675 220.845 6982.4', '}']):
                    # The default layer's mode child registers first on the authored Cain scene.
                    inactive_placements.append('The placed editor faction manager is inactive in the original layer order.')
                elif (layer == SUMMER_DAYS_DEFAULT and source.files[layer] == SUMMER_DAYS_DEFAULT_BLOB
                      and block[1:] == ['}'] and (head in SUMMER_DAYS_MANAGERS or head == PROJECTILE_SOUNDS_MANAGER)):
                    if head == PROJECTILE_SOUNDS_MANAGER:
                        # ChimeraSystemsConfig enables projectile audio in the shared SndSystem.
                        inactive_placements.append('Compare flybys and sonic cracks with the native projectile sound module.')
                    else:
                        result.append(body)
                elif (layer == INACTIVE_DOOR_LAYER and source.files[layer] == 'b21a4cb26d47b0c4e0cdd9fc04786a90adc2c5cd'
                      and block == INACTIVE_DOOR and 'TILW_DoorOpenerComponent' not in script_text(source)):
                    # The released TilW prefab still names the component removed in c47df35.
                    inactive_placements.append('The original TrainArea door opener has no runtime component in the published legacy addon.')
                elif layer == INTERSECTING_FIXTURES and source.files[layer] == INTERSECTING_FIXTURES_BLOB and line == 1:
                    # The bare turret has no compartment manager. The published crew component returns without boarding.
                    component = block.index('        TILW_VehicleCrewComponent "{6520BB55CAC5BA93}" {')
                    fields = [*block[:component], *block[block_end(block, component) + 1:]]
                    converted = native_placement(fields)
                    if converted is None:
                        blockers.append({'rule': kind, 'file': layer, 'line': line,
                                         'detail': 'inactive turret fixture has an unconverted component'})
                    else:
                        result.append(converted)
                        inactive_placements.append(
                            'The bare turret has no compartment manager and the original crew component never boards it.')
                elif FRAMEWORK.fullmatch(head) and framework_supported and (represented or outcome.get('startupSamples')):
                    fields = block[1:-1]
                    if ((script_flags and layer == SUPPLY_OUTCOME_LAYER and source.files[layer] == SUPPLY_OUTCOME_BLOB)
                            or (cache_supply_depot and layer == depot_layer)
                            or (phase_entity_capture and layer == RUHA_PHASE_LAYER)
                            or (concrete_crushers and layer == concrete_layer)
                            or (layer == LOCAL_FILES_DEFAULT and source.files[layer] == LOCAL_FILES_DEFAULT_BLOB
                                and 'TILW_RunScriptInstruction' not in body)):
                        # The reviewed prefix contains the replaced scripts and optional unused coordinates.
                        fields = fields[fields.index(' m_missionEvents {'):]
                    root_fields = [field.strip() for field in fields
                                   if field.startswith(' ') and not field.startswith('  ') and field.strip() != '}']
                    roots = [field.split()[0] for field in root_fields]
                    # The legacy framework uses named placements and flag data, not its own transform.
                    unsupported = set(roots) - {'m_missionEvents', 'm_casualtyFlags', 'm_suppressGameEnd'}
                    if any(outcome.get(field) for field in
                           ('derivedFlags', 'identityFlags', 'omittedMetaDividers', 'discoveryResetFlags')):
                        unsupported.discard('m_metaFlags')
                    for field in root_fields:
                        name = field.split()[0]
                        if roots.count(name) == 1 and FIELD.fullmatch(field):
                            unsupported.discard(name)
                    if unsupported == {'m_randomFlags'} and roots.count('m_randomFlags') == 1:
                        if outcome.get('startupSamples') or any(term['trigger'] == 'startup choice' for term in terms):
                            unsupported.clear()
                        else:
                            unused_flags = unused_flag_sampling(source, world, layer, line, block)
                            if unused_flags is not None:
                                unsupported.clear()
                                if not unused_flags:
                                    inactive_placements.append('The original random flag block has no named flag to set.')
                    if unsupported:
                        blockers.append({'rule': 'framework settings', 'file': layer, 'line': line, 'detail': roots})
                elif kind == 'SCR_AIGroup':
                    try:
                        members = {identity: asset for identity, asset in roster_assets.items()
                                   if asset['placement'] == f'{layer}:{line}'}
                        group_body = interaction_placements.get((layer, line), body)
                        ref = REF.search(block[0])
                        if ref and ref[2] in REVIEWED_GROUP_CALLSIGNS:
                            _, expected_layer, layer_blob, _, use_placed_callsigns = REVIEWED_GROUP_CALLSIGNS[ref[2]]
                            if layer != expected_layer or source.files.get(layer) != layer_blob:
                                raise ValueError('callsign placement differs from runtime evidence')
                            if not use_placed_callsigns:
                                group_body = remove_legacy_callsigns(group_body.splitlines())
                        if 'PK_EntityProtectorComponent' in group_body:
                            group_body = '\n'.join(migrate_native_components(group_body.splitlines())) + '\n'
                        playable_members = {identity: asset['path'] for identity, asset in members.items()
                                            if asset.get('playable')}
                        if (layer == STASHED_CACHE_PLAYER_LAYER
                                and source.files[layer] == '366271c69a72f3481a5bc1b61210d8283e80e36d'):
                            for identity, (name, blob) in STASHED_CACHE_COMMAND_ROLES.items():
                                path = STASHED_CACHE_COMMAND + name
                                canonical = STASHED_CACHE_PREFABS + path
                                if source.resources.get(identity) == [canonical] and source.files.get(canonical) == blob:
                                    playable_members[identity] = 'Prefabs/' + path
                        converted, count, slots = convert_group_block(
                            group_body,
                            {identity: asset['path'] for identity, asset in members.items() if not asset.get('playable')},
                            playable_members)
                        result.append(converted)
                        group_blocks.append({'file': layer, 'line': line, 'groups': count, 'slots': slots})
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                elif (kind == 'SCR_ChimeraCharacter' and line == 50
                      and layer == 'worlds/arc/CommandosReforged/CommandosReforged_Layers/players_us.layer'
                      and source.files[layer] == '5d85e00025f1fb2a1635c6c75f1dd9bcd04e9db2'):
                    # These placed appearances explicitly disabled role selection.
                    converted, count = re.subn(
                        r'(?m)^  components \{\n   PS_PlayableComponent "\{5EFCC1946F475F15\}" \{\n'
                        r'    m_bIsPlayable 0\n   \}\n  \}\n', '', body)
                    native = native_placement(converted.splitlines(), reviewed_character=True)
                    if count != 5 or not native or 'EC6F888025ED54EF' not in local_loadouts:
                        blockers.append({'rule': kind, 'file': layer, 'line': line,
                                         'detail': 'reviewed static Commandos riflemen need their native prefab'})
                    else:
                        result.append(native)
                elif (layer == COMMANDOS_OBJECTIVES_LAYER and source.files[layer] == COMMANDOS_OBJECTIVES_BLOB
                      and line in (1, 477)):
                    try:
                        result.append(commandos_objective_cleanup(block))
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                elif kind == 'PK_EntityDeleter':
                    try:
                        result.append(startup_cleanup(block))
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                elif (kind == 'SCR_FiringRangeTarget' and layer.startswith(CLEAN_HOUSE)
                      and (ref := REF.search(head)) and ref.groups() == CLEAN_HOUSE_RANGE_TARGET
                      and resolve_spawn(*CLEAN_HOUSE_RANGE_TARGET)):
                    converted = '\n'.join(migrate_native_components(block)) + '\n'
                    if LEGACY.search(converted):
                        blockers.append({'rule': kind, 'file': layer, 'line': line})
                    else:
                        result.append(converted)
                elif (layer, line) in area_placements:
                    checks, areas = area_placements[(layer, line)]
                    status_checks = {}
                    for flag, check in checks.items():
                        name = flag[1:-1]
                        if name not in route_flags:
                            if name not in used_flags:
                                status_checks[flag] = check
                            continue
                        record = row.get('captureCandidate', {}).get(f'{layer}:{line}', {}).get(name, {})
                        if (name in route_observers or not record.get('area') or record.get('seconds') != 0
                                or not record.get('stopsAfterFirstChange') or record.get('skipDiscovery')
                                or any('m_Messages ' in item for item in check)):
                            blockers.append({'rule': 'route scoring', 'file': layer, 'line': line,
                                             'detail': 'route flag needs one immediate area observer: ' + flag})
                            continue
                        route_observers[name] = render_area_flag_observer(world, record['area'], flag, check, True)
                    if status_checks:
                        if flag_references is None:
                            flag_references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                                                       if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
                        for flag, check in status_checks.items():
                            # An event's display name is separate from the flag read by its condition.
                            name_fields = len(re.findall(r'^ +m_name ' + re.escape(flag) + r'\s*$', flag_references, re.M))
                            record = row.get('captureCandidate', {}).get(f'{layer}:{line}', {}).get(flag[1:-1], {})
                            status_only = record.get('statusOnly')
                            action_reads = interaction_reads[flag[1:-1]]
                            if (not any('m_Messages ' in item for item in check)
                                    or (action_reads and (status_only or not record.get('stopsAfterFirstChange')
                                                         or record.get('skipDiscovery')
                                                         or flag[1:-1] in event_flags | interaction_writers))
                                    or flag_references.count(flag) != (0 if status_only else 1 + name_fields + action_reads)
                                    or flag[1:-1] in script_text(source)):
                                blockers.append({'rule': kind, 'file': layer, 'line': line,
                                                 'detail': 'capture flag may have an unconverted consumer ' + flag})
                            else:
                                status_areas[flag] = check
                    area_names.extend(re.findall(r'^(?:GC_RadiusAreaEntity|PolylineShapeEntity) ([\w.-]+) ', areas, re.M))
                    area_names.extend(match[1] for check in checks.values() for field in check
                                      if (match := re.fullmatch(r'\s*m_sArea "([\w.-]+)"', field)))
                    result.append(areas)
                elif (gap := row.get('captureCandidate', {}).get(f'{layer}:{line}', {}).get('gap')):
                    blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': gap})
                elif (layer, line) in interaction_placements:
                    result.append(interaction_placements[(layer, line)])
                elif (layer, line) in spawners:
                    result.append(spawners[(layer, line)])
                elif kind == 'PS_ManualMarker':
                    result.append(convert_layer(body, dev, draw_order=True, source=source,
                                                child_outline=convert_child_outline)[0])
                elif kind == 'PS_ManuaMarkerRender':
                    result.append(convert_hover_camera(block, f'{layer}:{line}'))
                elif head == polygon_capture.STARTUP_AREA:
                    try:
                        result.append(polygon_capture.startup_areas(block, f'{layer}:{line}', resolve_spawn))
                    except ValueError as exc:
                        blockers.append({'rule': 'startup area spawner', 'file': layer, 'line': line, 'detail': str(exc)})
                elif BARE_SHAPE.fullmatch(head) and 'Prefabs/WEGenerators/Markers/PG_MarkerGenerator_' in body:
                    result.append(convert_baked_marker_generator(block, source, dev))
                    converted_scenery_map = True
                elif BARE_SHAPE.fullmatch(head) and 'WallGeneratorEntity {' in body and 'PS_ManualMarker :' in body:
                    result.append(convert_wall_markers(block, source, dev))
                    converted_scenery_map = True
                elif ('TILW_AOLimit.et' in head or 'TILW_MapShape.et' in head or PASSIVE_COVER.fullmatch(head)
                        or (BARE_SHAPE.fullmatch(head) and 'TILW_MapShapeComponent' in body)):
                    converted, audit = convert_boundaries(body, allow_public_faction_color=True, extended_shapes=True,
                                                          allow_bare=True, source_blob=source.files[layer],
                                                          source_line=line)
                    if not converted or any('gap' in item for item in audit.values()):
                        blockers.append({'rule': 'boundary or map shape', 'file': layer, 'line': line, 'detail': audit})
                    else:
                        boundaries.setdefault(layer, []).append(converted)
                        converted_cover |= 'm_bAutoFrameMap 1' in converted
                        converted_passive_cover |= bool(PASSIVE_COVER.fullmatch(head))
                        converted_bare_map |= bool(BARE_SHAPE.fullmatch(head))
                elif (layer == PUBLIC_ENEMIES + 'PublicEnemies_Layers/default.layer'
                      and source.files[layer] == 'ba62e7b847c7e673b05b07e7cfe20511d2124ae8'
                      and PERCEPTION.fullmatch(head)
                      and block[2:-1] == [' SunDirectLightElevCurve {', '  SCR_Faction "{68A73D13C0414D66}" {',
                                          '  }', ' }']):
                    # This empty faction element supplies no native CurvePoint values.
                    continue
                elif ((perception := PERCEPTION.fullmatch(head))
                      and all(field.strip().partition(' ')[0] in SPAWNER_TRANSFORM and FIELD.fullmatch(field.strip())
                              for field in block[1:-1])
                      and (not perception['serialized'] or (perception['serialized'] not in script_text(source)
                           and sum(source.read(path).count(perception['serialized']) for path in source.closure(world)
                                   if PurePosixPath(path).suffix.lower() in SOURCE_TEXT) == 1))):
                    continue
                elif kind == 'SCR_EditorRestrictionZoneEntity' and not LEGACY.search(body):
                    names, converted = restriction_zones(block, len(zones) + 1)
                    zones.extend(names)
                    result.append(converted)
                elif kind == 'ParticleEffectEntity' and static_particle_placement(block):
                    result.append(body)
                elif kind == 'GMFX_MineTriggerEntity':
                    resource = REF.search(head)
                    if (resource and resource.groups() == GMFX_AP_MINE and resolve_spawn(*GMFX_AP_MINE)
                            and all(FIELD.fullmatch(field.strip()) for field in block[1:-1])):
                        result.append(body)
                    else:
                        blockers.append({'rule': kind, 'file': layer, 'line': line,
                                         'detail': 'unverified GMFX placement'})
                elif LEGACY_WIRECUTTER.fullmatch(head):
                    result.append(restore_placed_wirecutters(block, asset_aliases, source.files[layer]))
                elif kind == 'SplineShapeEntity' and static_spline_placement(block):
                    result.append(body)
                elif kind == 'RoadGeneratorEntity' and static_road_placement(block):
                    result.append(body)
                elif kind in ('SplineShapeEntity', 'StaticModelEntity') and LEGACY.search(body) and (
                        'PS_ManualMarker' in body or 'TILW_MapShape' in body):
                    try:
                        converted = scenery_map_placement(block, dev)
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                    else:
                        result.append(converted)
                        converted_scenery_map = True
                elif kind == 'SCR_ChimeraCharacter' and 'PK_EntityModifierComponent' in body:
                    try:
                        converted = placed_core_character(block, resolve_crew)
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                    else:
                        if converted is None:
                            blockers.append({'rule': kind, 'file': layer, 'line': line,
                                             'detail': 'unverified Core startup modifier'})
                        else:
                            result.append(converted)
                elif kind == 'SCR_ChimeraCharacter' and LEGACY_SENTRY_TREE in body:
                    result.append(placed_static_sentries(block, resolve_crew))
                elif head == '$grp GenericEntity : "' + UK_AMMO_BOX + '" {':
                    result.append(restore_uk_ammo_boxes(block, layer, source.files[layer]))
                elif (layer == SCREENING_AI_WORLD_LAYER and source.files[layer] == SCREENING_AI_WORLD_SOURCE
                      and body == BARE_AI_WORLD):
                    if native_world := screening_ai_world(dev):
                        result.append(native_world)
                    else:
                        blockers.append({'rule': kind, 'file': layer, 'line': line,
                                         'detail': 'verified Bad Orb AI world prefab unavailable'})
                elif head == LEGACY_ARTILLERY and OFF_MAP_ARTILLERY_LAYERS.get(layer) == source.files[layer]:
                    converted = [line.replace('  GCC_ArtyData "', '  GC_OffMapRound "', 1)
                                 if line.startswith('  GCC_ArtyData "') else line for line in block[1:]]
                    result.append('\n'.join([LOBBY_ARTILLERY, *converted]) + '\n')
                    converted_artillery = True
                elif (layer == LACOURT_HORNBEAM_LAYER
                      and source.files[layer] == LACOURT_HORNBEAM_BLOB
                      and (tree := REF.search(head)) and tree.groups() in LACOURT_HORNBEAMS):
                    replacement = registered_alias(*LACOURT_HORNBEAMS[tree.groups()], source, dev, asset_aliases)
                    if (replacement and head == f'SCR_DestructibleTreeV2 : {tree[0]} {{'
                            and all(field.strip().partition(' ')[0] in SPAWNER_TRANSFORM and FIELD.fullmatch(field.strip())
                                    for field in block[1:-1])):
                        result.append('\n'.join([f'Tree : {replacement} {{', *block[1:]]) + '\n')
                        converted_hornbeams = True
                    else:
                        blockers.append({'rule': kind, 'file': layer, 'line': line,
                                         'detail': 'unverified hornbeam replacement or placement'})
                elif head.startswith(('VehiclePartPanel ', 'VehiclePartRotor ')) and reviewed_vehicle_part(block):
                    result.append('\n'.join(block) + '\n')
                elif NATIVE.match(head):
                    try:
                        if (layer == SEEDER_PLAYER_VEHICLES and head.startswith('Vehicle BRDM2_AFRF2 : ')
                                and seeder_recon_flags_unused(source)):
                            block = remove_unused_damage_flag(block, 'Rec2')
                            inactive_placements.append('The two BRDMs wrote unused recon damage flags in the original mission.')
                        if f'{{{BLOODY_GARRISON}}}' in head:
                            converted, groups, slots = garrison_placement(source, world, layer, block), 0, 0
                        else:
                            placed_names = ()
                            if (layer == ZARICHNE_LAV_LAYER and source.files[layer] == ZARICHNE_LAV_BLOB and line == 1
                                    and source.files.get(ZARICHNE_DEFAULT) == ZARICHNE_DEFAULT_BLOB):
                                placed_names = ZARICHNE_CREW_NAMES
                            vehicle, groups, slots = convert_vehicle_groups(
                                convert_crew(block, placed_names=placed_names), convert_group_block)
                            if layer == ZARICHNE_PROPS and source.files[layer] == ZARICHNE_PROPS_BLOB:
                                for start, stop, indent, marker in reversed(list(
                                        nested_placement_blocks(vehicle, LEGACY_MARKER))):
                                    rendered, _ = convert_layer('\n'.join(marker) + '\n', dev, draw_order=True)
                                    vehicle[start:stop + 1] = [' ' * indent + line for line in rendered.splitlines()]
                            if (layer == METSALA_VEHICLE_LAYER and source.files[layer] == METSALA_VEHICLE_BLOB
                                    and head == f'$grp Vehicle : "{METSALA_HIND}" {{'):
                                # Version 8.4.0 retains these component, group and station IDs.
                                vehicle = [line.replace(METSALA_HIND, METSALA_HIND.replace('_armed_', '_Armed_'))
                                           .replace('WCS_ArmamentWeaponGroup ', 'WCS_Armament_WeaponGroup ')
                                           .replace('WCS_ArmamentWeaponInfo ', 'WCS_Armament_WeaponInfo ')
                                           for line in vehicle]
                            vehicle = restore_rhib_mounts(vehicle, asset_aliases, source.files[layer])
                            converted = native_placement(update_cobra_armament(vehicle, asset_aliases))
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                    else:
                        if converted is None:
                            blockers.append({'rule': kind, 'file': layer, 'line': line})
                        else:
                            result.append(converted)
                            if groups:
                                group_blocks.append({'file': layer, 'line': line, 'groups': groups, 'slots': slots})
                else:
                    rule = 'framework events' if kind == 'TILW_MissionFrameworkEntity' else kind
                    blockers.append({'rule': rule, 'file': layer, 'line': line})
        except ValueError as exc:
            blockers.append({'rule': 'layer conversion', 'file': layer, 'detail': str(exc)})

    observed_interaction_flags = {flag[1:-1] for flag in status_areas}
    if missing_flags := ((required_teleport_flags | required_interaction_flags)
                         - (event_flags | interaction_writers | observed_interaction_flags
                            | observed_teleport_flags | dormant_interaction_flags)):
        blockers.append({'rule': 'mission interaction', 'file': world,
                         'detail': {'missingConvertedMatchFlagProducers': sorted(missing_flags)}})
    if status_areas:
        names = {event.get('eventName', event['name']) for event in rules.values()}
        trackers = {flag[1:-1]: 'GC_MigrationAreaStatus_' + guid(world + '/status/' + flag)
                    for flag in status_areas}
        if names.intersection(trackers.values()):
            blockers.append({'rule': 'framework events', 'file': world,
                             'detail': 'generated area status tracker name collides with a mission event'})
        else:
            published = observed_interaction_flags & (required_teleport_flags | required_interaction_flags)
            rule_text += '\n'.join(render_capture_trackers(world, trackers, status_areas, set(), published)) + '\n'
            event_flags.update(published)
    if mode is None:
        blockers.append({'rule': 'mode placement', 'file': world})
    else:
        if world == COMMANDOS_WORLD and mode_index[0] == COMMANDOS_MODE_LAYER:
            try:
                commandos_objectives = audit_commandos_objectives(source, mode)
                commandos_runtime = commandos_runtime_files(source)
            except ValueError as exc:
                commandos_objectives = None
                blockers.append({'rule': 'Commandos objective authoring', 'file': COMMANDOS_MODE_LAYER,
                                 'detail': str(exc)})
        if route_flags != route_observers.keys():
            blockers.append({'rule': 'route scoring', 'file': world,
                             'detail': 'not all route scoring area observers could be placed'})
        mode_name = mode[0].split(' : ', 1)[0].removeprefix('PS_GameModeCoop').strip()
        deletion_targets = {name for event in rules.values() for action in event.get('convertedActions', [])
                            if action['instruction'] == 'TILW_DeleteEntitiesInstruction'
                            for name in action['entities']}
        if mode_name in deletion_targets:
            blockers.append({'rule': 'mode settings', 'file': mode_index[0],
                             'detail': 'legacy instruction deletes its own game mode'})
        deleted_briefings = sorted(deletion_targets.intersection(row.get('namedBriefings', [])))
        if deleted_briefings:
            blockers.append({'rule': 'briefing lifecycle', 'file': world,
                             'detail': 'legacy instruction removes briefing pages: ' + ', '.join(deleted_briefings)})
        try:
            layer, index = mode_index
            mode_flags = set(objective_flags) | event_flags
            if commandos_objectives:
                mode_flags.update(objective[key] for objective in commandos_objectives['definitions'].values()
                                  if objective['kind'] == 'interaction'
                                  for key in ('availabilityFlag', 'completionFlag'))
            rendered = render_mode(world, mode, preset or '', rule_text, zones, ai_faction,
                                   mode_flags, damage_flags,
                                   population_flags=population_flags,
                                   commandos_component=(commandos_objectives['nativeManagerComponent']
                                                        if commandos_objectives else None),
                                   dynamic_respawn_factions=edited_factions,
                                   cache_supply_depot=cache_supply_depot,
                                   inherited_immediate_release=(
                                       depot_layer == layer == FOOD_MARKET_OUTCOME_LAYER or bool(commandos_objectives)),
                                   capture_phase_entities=phase_entity_capture,
                                   source_blob=source.files[layer],
                                   replaced_tracker=(
                                       '  GC_CommandosManager "{66FA7E68E581F311}" {'
                                       if local_files_tracker and layer == LOCAL_FILES_DEFAULT else
                                       '  GC_CommandosManager "{691080D344BD9507}" {'
                                       if trifecta_tracker and layer == TRIFECTA_FRAMEWORK[0] else None),
                                   derived_flag_containers=route_graph.get('containers', ()),
                                   area_flag_observers=[line for observer in route_observers.values() for line in observer]
                                   + [line for record in shared_writers for line in record['areaFlagObserver']])
            if preset:
                event_pattern = r'(?m)^\s*GC_(?:Event|RandomEventGroup) "\{[\da-fA-F]{16}\}" \{'
                if len(re.findall(event_pattern, rendered + staged.get(preset, ''))) > 256:
                    raise ValueError('generated mission exceeds the 256-rule engine limit')
                parts[layer][index] = rendered
        except ValueError as exc:
            blockers.append({'rule': 'mode settings', 'file': mode_index[0], 'detail': str(exc)})
    for path, blocks_in_layer in parts.items():
        contents = ''.join(blocks_in_layer)
        if local_files_tracker and path == LOCAL_FILES_DEFAULT:
            contents += local_files_tracker
        if contents.strip() or world == TEMPLATE_WORLD and not source.read(path):
            draft[path] = contents
    if world == TEMPLATE_WORLD and (instructions := 'worlds/TemplateMission/READ ME.txt') in source.files:
        original = source.read_raw(instructions).decode('utf-8').rstrip('\r\n')
        draft[instructions] = f'{original}\n{TEMPLATE_SETTINGS_NOTE}\n'
    assignments = [item for event in rules.values() for item in event.get('waypointAssignments', [])]
    marker_edits = [item for event in rules.values() for item in event.get('convertedActions', []) if 'markers' in item]
    briefing_edits = Counter(order for edit in marker_edits for order in edit.get('briefingOrders', []))
    for order, count in sorted(briefing_edits.items()):
        if count > 1:
            blockers.append({'rule': 'briefing visibility actions', 'file': world,
                             'detail': f'briefing {order} has multiple reveal actions'})
    prefab_spawns = []
    for event in rules.values():
        flag = event.get('flag')
        selected = (event.get('trigger') == 'startup choice' or
                    (event.get('trigger') == 'event flag' and not event.get('inverted') and flag in single_draw_flags))
        prefab_spawns.extend({**item, 'startupChoice': flag if selected else None, 'sourceEvent': event['name']}
                             for item in event.get('convertedActions', []) if 'spawnAnchor' in item)
    prefab_spawns.extend(spawn for spawn in spawner_audit if spawn['trigger'] != 'inactive cleanup')
    crew_names = defaultdict(set)
    crew_name_counts = Counter()
    for spawn in prefab_spawns:
        for group in spawn.get('crewGroups', ()):
            if 'groupName' in group:
                crew_names[group['groupName']].add(group.get('shadowedByAnchor', False))
                crew_name_counts[group['groupName']] += 1
    spawn_guids = {REF.fullmatch(item['prefab'])[1].upper() for item in prefab_spawns}
    spawned_assets = spawned_assets.copy()
    for resource, asset in sorted(spawned_assets.items()):
        if 'flag' in asset and resource not in roster_assets and asset['flag'] in used_flags and resource not in spawn_guids:
            blockers.append({'rule': 'spawned objective', 'file': asset['path'],
                             'detail': 'objective flag has no converted prefab spawn'})
        if asset.get('destroyed'):
            producers = [item for item in prefab_spawns if REF.fullmatch(item['prefab'])[1].upper() == resource]
            choices = {item['startupChoice'] for item in producers}
            complete = (len(producers) == asset['spawnCount']
                        and all(len(item.get('spawnDelaysSeconds', ['0'])) == 1 for item in producers))
            same_draw = len({draw_by_flag.get(choice) for choice in choices}) == 1
            exclusive = len(choices) == len(producers) and None not in choices and same_draw
            shared_draw = (complete and not asset.get('vehicle') and choices <= single_draw_flags
                           and same_draw
                           and len({item['spawnAnchor'] for item in producers}) == len(producers)
                           and all(item.get('spawnDelaysSeconds') == ['0'] for item in producers))
            if not complete or (len(producers) > 1 and not (exclusive or shared_draw)):
                blockers.append({'rule': 'spawned objective', 'file': asset['path'],
                                 'detail': 'damage objective needs one spawn or mutually exclusive startup choices'})
            elif len(producers) > 1 and not exclusive:
                # A chosen location can spawn multiple observers. Each writes the same
                # flag until its own hit zone reaches the target state.
                field = f'   m_sFlag "{asset["flag"]}"\n'
                body = asset['body'].replace(field, field + '   m_bClearOnMismatch 1\n'
                                            '   m_bDeferInitialSample 1\n', 1)
                spawned_assets[resource] = {**asset, 'body': body}
            path = asset['path']
            if path in dev.files:
                body = replace_registered_paths(spawned_assets[resource]['body'], source, dev, asset_aliases)
                if body.strip() != dev.read(path).strip():
                    blockers.append({'rule': 'spawned objective', 'file': path,
                                     'detail': 'existing Dev prefab differs from the verified spawn policy'})
    extraction_spawns = [item for event in rules.values() for item in event.get('extractionSpawns', [])]
    if delayed:
        flag_uses = Counter(term.get('flag') for term in terms if term['trigger'] == 'area capture')
        expected_spawns = [item for group in delayed.spawns.values() for item in group]
        placements = Counter((item['area'], item['anchor']) for item in extraction_spawns)
        if (placements != Counter((item['area'], item['anchor']) for item in expected_spawns)
                or any(flag_uses[flag[1:-1]] != delayed.startup_consumers.get(flag[1:-1], 1)
                       for flag in delayed.conditions)):
            blockers.append({'rule': 'delayed extraction', 'file': world,
                             'detail': 'extraction needs all reviewed spawns and consumers'})
        if delayed.startup_consumers:
            choices = {item['sample'] for item in expected_spawns}
            if not any(set(sample['flags']) == choices and sample['drawCount'] == 1
                       for sample in startup_samples):
                blockers.append({'rule': 'delayed extraction', 'file': world,
                                 'detail': 'extraction spawns need a shared exclusive draw'})
    missing_waypoints = []
    group_waypoint_links = []
    missing_spawn_anchors = set()
    self_waypoints = []
    inert_spawner_orders = set()
    missing_order_groups = set()
    missing_static_vehicles = []
    placed_crew_fields = []
    for path, body in draft.items():
        if path.endswith('.layer'):
            placed_crew_fields.extend(fields for _, fields in re.findall(
                r'(?ms)^( +)GC_VehicleCrewComponent "\{[\da-fA-F]{16}\}" \{\n(.*?)^\1\}', body))
    crew_waypoints = {name for crew in placed_crew_fields
                      for _, fields in re.findall(r'(?ms)^( +)m_a(?:Crew)?Waypoints \{\n(.*?)^\1\}', crew)
                      for name in re.findall(r'"([\w.-]+)"', fields)}
    placed_crew_names = Counter(name for crew in placed_crew_fields
                                for name in re.findall(r'(?m)^ +m_sGroupName "([\w.-]+)"$', crew))
    if not blockers and (assignments or marker_edits or prefab_spawns or extraction_spawns or area_names or teleport_targets
                         or concrete_crushers
                         or damage_flags or roster_assets or crew_waypoints
                         or any('m_aStatic' in body for body in draft.values())):
        named = named_placements(draft.values())
        if concrete_crushers:
            intel_targets = [name for intel in CONCRETE_INTEL for name in (intel, intel + '_Marker')]
            if any(len(named.get(target, ())) != 1 for target in intel_targets):
                blockers.append({'rule': 'framework events', 'file': concrete_layer,
                                 'detail': 'startup intel cleanup needs exactly one pickup and marker per choice'})
            commander_ref = f'"{{{CONCRETE_COMMANDER_ID}}}{CONCRETE_COMMANDER}"'
            commander_spawns = [spawn for spawn in spawner_audit if spawn.get('prefab') == commander_ref]
            expected_towns = Counter(frozenset((town + ' Flag', CONCRETE_DONE)) for town in CONCRETE_TOWNS)
            actual_towns = Counter(frozenset(term['flag'] for term in leaves(spawn) if term['trigger'] == 'event flag')
                                   for spawn in commander_spawns if spawn['trigger'] == 'all' and spawn['requiresMatch'])
            if len(commander_spawns) != len(CONCRETE_TOWNS) or actual_towns != expected_towns:
                blockers.append({'rule': 'spawned objective', 'file': CONCRETE_COMMANDER,
                                 'detail': 'expected one commander spawn condition per exclusive town'})
        attached_roster_markers = set()
        for resource, asset in roster_assets.items():
            reference = f'"{{{resource}}}{asset["path"]}"'
            occurrences = sum(body.count(reference) for body in draft.values())
            entity = asset.get('entity')
            if occurrences != 1 or entity in named:
                blockers.append({'rule': 'roster objective spawn', 'file': asset['path'],
                                 'detail': 'expected one member with no conflicting runtime name'})
            elif entity:
                named[entity] = [('SCR_ChimeraCharacter', reference)]
            if marker_id := asset.get('attachedMarker'):
                marker = spawned_assets[marker_id]
                marker_name = marker['markerName']
                if occurrences != 1 or marker_name in named:
                    blockers.append({'rule': 'roster marker', 'file': marker['path'],
                                     'detail': 'expected one role with a unique attached marker name'})
                else:
                    named[marker_name] = [('GenericEntity', marker['body'].splitlines())]
                    attached_roster_markers.add(marker_name)
        for target in sorted(damage_flags.values()):
            if len(named.get(target, [])) != 1:
                blockers.append({'rule': 'damage flag target', 'file': world,
                                 'detail': 'expected one placed entity named ' + target})
        attached_destinations = defaultdict(list)
        for asset in spawned_assets.values():
            if destination := asset.get('attachedTeleportDestination'):
                attached_destinations[destination].append(asset['replacementEntity'])
        for target in sorted(teleport_targets):
            placements = named.get(target, ())
            attached = attached_destinations.get(target, ())
            if not attached and len(placements) == 1:
                continue
            if not placements and len(attached) == 1 and len(named.get(attached[0], ())) == 1:
                continue
            blockers.append({'rule': 'teleport destination', 'file': world,
                             'detail': 'expected one placed or vehicle-attached entity named ' + target})
        for name in sorted(set(area_names)):
            if len(named.get(name, [])) != 1:
                blockers.append({'rule': 'area name', 'file': world, 'detail': 'ambiguous area named ' + name})
        for spawn in extraction_spawns:
            if len(named.get(spawn['anchor'], [])) != 1 or spawn['area'] in named:
                blockers.append({'rule': 'delayed extraction', 'file': world,
                                 'detail': 'missing or ambiguous placement for ' + spawn['anchor']})
        spawned_names = defaultdict(list)
        for item in prefab_spawns:
            if item['spawnName']:
                spawned_names[item['spawnName']].append(item['startupChoice'])
        rotating_names = set()
        if world == SEEDER_WORLD and source.files.get(SEEDER_REPLACEMENT_LAYER) == SEEDER_REPLACEMENT_LAYER_BLOB:
            side_name = 'Side_Obj_Trigger'
            side_spawns = [item for item in prefab_spawns if item['spawnName'] == side_name]
            side_events = {event['name']: event.get('convertedActions', ()) for event in rules.values()
                           if any(action.get('spawnName') == side_name for action in event.get('convertedActions', ()))}
            expected = {'Side - Kill Officer 1 - Start', 'Side - Kill Officer 2 - Start', 'Side - Destroy Caches 1 - Start'}
            if (len(side_spawns) == 3 and side_events.keys() == expected
                    and all(Decimal(spawn['delaySeconds']) == 5
                            and any(action.get('entities') == [side_name] and Decimal(action['delaySeconds']) == 1
                                    for action in side_events[spawn['sourceEvent']])
                            for spawn in side_spawns)):
                rotating_names.add(side_name)
        for name, policies in sorted(crew_names.items()):
            matches = named.get(name, ())
            if policies == {True} and len(matches) == 1 and matches[0][0] == 'GenericEntity' and name not in spawned_names:
                continue
            if matches or (name in spawned_names and name not in temporary_names) or True in policies:
                blockers.append({'rule': 'named crew group', 'file': world,
                                 'detail': 'crew group name conflicts with another entity: ' + name})
        anchor_source, anchor_blob, missing_anchors = REVIEWED_MISSING_SPAWN_ANCHORS.get(world, (None, None, ()))
        local_problems = (world == 'worlds/Cunnah/Local_Problems/Local_Problems.ent'
                          and source.files.get(LOCAL_PROBLEMS_DEFAULT) == LOCAL_PROBLEMS_DEFAULT_BLOB)
        local_problems_bomber = (local_problems and len(spawned_names.get('Bomber_Vic', ())) == 1
                                 and not named.get('Bomber_Vic') and any(
                                     spawn['spawnName'] == 'Bomber_Vic' and spawn['trigger'] == 'pregame start'
                                     for spawn in spawner_audit))
        local_roadblocks = (local_problems and not named.get('Bomber_Target_Vic')
                           and len(spawned_names.get('Bomber_Target_Vic', ())) == 2
                           and {spawn['spawnAnchor'] for spawn in spawner_audit if spawn.get('allowsDuplicateSpawnName')}
                               == {'Road_Block_Tech', 'Road_Block_Tech2'})
        for spawn in prefab_spawns:
            anchor, name = spawn['spawnAnchor'], spawn['spawnName']
            resource = REF.fullmatch(spawn['prefab'])[1].upper()
            asset = spawned_assets.get(resource, {})
            targets = named.get(name, ())
            replacement = (name and name == asset.get('replacementEntity') and len(spawned_names[name]) == 1
                           and spawn.get('sourceEvent') == asset['replacementEvent']
                           and len(targets) == 1 and targets[0][0] == 'Vehicle'
                           and f' : {spawn["prefab"]} {{' in targets[0][1][0])
            missing_routes = [route for group in spawn.get('crewGroups', ()) for route in group.get('missingWaypoints', ())]
            if missing_route := spawn.get('missingGroupWaypoint'):
                missing_routes.append(missing_route)
            for target in missing_routes:
                if target in named or target in spawned_names or target in crew_names:
                    blockers.append({'rule': 'spawn waypoint', 'file': world,
                                     'detail': 'reviewed missing waypoint now resolves: ' + target})
            if ((spawn.get('instructionId'), anchor) in missing_anchors
                    and source.files.get(anchor_source) == anchor_blob and not named.get(anchor) and not name
                    and anchor not in spawned_names and anchor not in crew_names
                    and anchor not in script_text(source)):
                # The published spawn instruction returns when its anchor is absent.
                missing_spawn_anchors.add(anchor)
                continue
            choices = spawned_names[name]
            mutually_exclusive = (None not in choices and len(set(choices)) == len(choices)
                                  and len({draw_by_flag.get(choice) for choice in choices}) == 1)
            tracked_bomber = (spawn.get('sourceEvent') == 'Explode_Bomb'
                              and ((local_problems_bomber and anchor == 'Bomber_Vic')
                                   or (local_roadblocks and anchor == 'Bomber_Target_Vic')))
            authored_duplicate = local_roadblocks and name == 'Bomber_Target_Vic'
            if ((len(named.get(anchor, [])) != 1 and not tracked_bomber)
                    or (name and ((len(choices) > 1 and not authored_duplicate and not mutually_exclusive and name not in temporary_names
                                   and name not in rotating_names)
                                  or (name in named and not replacement)))):
                blockers.append({'rule': 'prefab spawn', 'file': world,
                                 'detail': 'missing anchor or conflicting spawn name for ' + anchor})
            for waypoint in spawn.get('crewWaypoints', spawn.get('groupWaypoints', ())):
                targets = named.get(waypoint, [])
                if len(targets) != 1 or not re.fullmatch(WAYPOINT_CLASS, targets[0][0]):
                    rule = 'AI group spawn' if 'groupWaypoints' in spawn else 'crewed vehicle spawn'
                    blockers.append({'rule': rule, 'file': world,
                                     'detail': 'expected one placed AI waypoint named ' + waypoint})
            if target := spawn.get('selfGroupWaypoint'):
                entries = named.get(target, [])
                if (len(entries) != 1 or entries[0][0] != 'GenericEntity' or target in spawned_names
                        or target in crew_name_counts or target in script_text(source)):
                    blockers.append({'rule': 'AI group spawn', 'file': world,
                                     'detail': 'self waypoint also has a runtime target: ' + target})
        layer, blob, absent_groups = REVIEWED_MISSING_ORDER_GROUPS.get(world, (None, None, ()))
        absent_groups = {group for group in absent_groups
                         if source.files.get(layer) == blob and group not in named and group not in spawned_names
                         and group not in crew_name_counts and group not in script_text(source)}
        reviewed_anchors = reviewed_spawner_links(source, world) & {spawn['spawnAnchor'] for spawn in spawner_audit}
        inert_anchors = {name for name in reviewed_anchors
                         if len(named.get(name, ())) == 1 and named[name][0][0] == 'GenericEntity'
                         and name not in spawned_names and name not in crew_name_counts and name not in script_text(source)}
        for event in rules.values():
            if not event.get('waypointAssignments'):
                continue
            earlier_crews = {group['groupName']: (spawn['delaySeconds'], group['waypoints'])
                             for spawn in event.get('convertedActions', ())
                             for group in spawn.get('crewGroups', ())
                             if group.get('groupName') and crew_name_counts[group['groupName']] == 1
                             and not group.get('shadowedByAnchor')}
            orders = event['waypointAssignments']
            missing_order_groups.update(action['group'] for action in orders if action['group'] in absent_groups)
            inert_spawner_orders.update(action['group'] for action in orders if action['group'] in inert_anchors)
            active_orders = [action for action in orders if action['group'] not in absent_groups | inert_anchors]
            placed_crews = ()
            if event.get('name') == 'LAVWaypoint' and source.files.get(ZARICHNE_DEFAULT) == ZARICHNE_DEFAULT_BLOB:
                placed_crews = {name for name in ZARICHNE_CREW_NAMES if placed_crew_names[name] == 1
                                and name not in crew_name_counts}
            if local_problems:
                for spawn in spawner_audit:
                    if spawn['trigger'] not in ('pregame start', 'Match start') or spawn['spawnDelaysSeconds'] != ['0']:
                        continue
                    for group in spawn.get('crewGroups', ()):
                        name = group.get('groupName')
                        if (name in {'Bomber', 'Tech_1', 'Tech_2', 'truck_blockade'} and crew_name_counts[name] == 1
                                and not group.get('shadowedByAnchor')):
                            earlier_crews[name] = ('0', group['waypoints'])
            gaps, no_ops = waypoint_gaps(named, active_orders, spawned_names, script_text(source), earlier_crews,
                                        placed_crews=placed_crews)
            self_waypoints.extend(no_ops)
            for gap in gaps:
                blockers.append({'rule': 'waypoint assignments', 'file': world, 'detail': gap})
        for waypoint in sorted(crew_waypoints):
            targets = named.get(waypoint, [])
            if len(targets) != 1 or not re.fullmatch(WAYPOINT_CLASS, targets[0][0]):
                blockers.append({'rule': 'placed crew waypoint', 'file': world,
                                 'detail': 'expected one placed AI waypoint named ' + waypoint})
        source_named = None
        for layer, field, target in static_link_gaps(draft, named):
            reviews = REVIEWED_MISSING_WAYPOINTS if field == 'm_aStaticWaypoints' else REVIEWED_MISSING_STATIC_VEHICLES
            reviewed_blob, reviewed_targets = reviews.get(layer, (None, ()))
            if (target in reviewed_targets and source.files.get(layer) == reviewed_blob
                    and target not in named and target not in spawned_names and f'"{target}"' not in script_text(source)):
                missing = missing_waypoints if field == 'm_aStaticWaypoints' else missing_static_vehicles
                missing.append(target)
                continue
            targets = named.get(target, ())
            if (field == 'm_aStaticWaypoints' and len(targets) == 1 and targets[0][0] == 'SCR_AIGroup'
                    and target not in spawned_names and target not in crew_name_counts
                    and target not in script_text(source)):
                if source_named is None:
                    source_named = named_placements(source.read(path) for path in source.layers[world.lower()])
                original = source_named.get(target, ())
                if len(original) == 1 and original[0][0] == 'SCR_AIGroup':
                    # SCR_AIGroup.AddWaypointsStatic casts the named entity to AIWaypoint.
                    group_waypoint_links.append(target)
                    continue
            blockers.append({'rule': 'static group links', 'file': layer,
                             'detail': f'{field} requires a unique placed target named {target}'})
        marker_targets = named
        if trifecta_tracker:
            marker_spawns = [spawn for spawn in prefab_spawns if spawn['spawnName'] == trifecta_tracker]
            marker_asset = spawned_assets.get(TRIFECTA_MARKER[0], {})
            marker_ref = f'"{{{TRIFECTA_MARKER[0]}}}{TRIFECTA_MARKER[1]}"'
            expected = {f'POS{i}' for i in range(1, 6)}
            if (marker_asset and trifecta_tracker not in named and len(marker_spawns) == 5
                    and {spawn['startupChoice'] for spawn in marker_spawns} == expected
                    and all(spawn['prefab'] == marker_ref for spawn in marker_spawns)
                    and len({draw_by_flag[choice] for choice in expected if choice in draw_by_flag}) == 1
                    and expected <= draw_by_flag.keys()):
                marker_targets = {**named, trifecta_tracker: [('GenericEntity', marker_asset['body'].splitlines())]}
        if (len(attached_roster_markers) == 18
                and source.files.get(VILLA_DEFAULT) == VILLA_DEFAULT_BLOB
                and source.files.get(VILLA_BLUFOR) == VILLA_BLUFOR_BLOB
                and not VILLA_UNPLACED_MARKERS.intersection(marker_targets)):
            # These three reveal targets belong to roles absent from the saved roster.
            marker_edits = [{**edit, 'markers': [name for name in edit['markers']
                                               if name not in VILLA_UNPLACED_MARKERS]}
                            for edit in marker_edits]
        for gap in marker_edit_gaps(marker_targets, marker_edits):
            blockers.append({'rule': 'marker visibility actions', 'file': world, 'detail': gap})
    for layer, converted in boundaries.items():
        sibling = layer.removesuffix('.layer') + '_GC_Boundaries.layer'
        if sibling not in dev.files:
            draft[sibling] = ''.join(converted)
            continue
        try:
            original = dev.read(sibling)
            remaining = ['\n'.join(block) for _, block in blocks(''.join(converted))]
            for _, existing in blocks(original):
                remaining.remove('\n'.join(existing))
            updated = replace_registered_paths(original, source, dev, asset_aliases)
            if updated != original:
                draft[sibling] = updated
                reviewed_replacements[sibling] = sha256(original.encode()).hexdigest()
            else:
                reused_layers.add(sibling)
            if remaining:
                supplemental = layer.removesuffix('.layer') + '_GC_AdditionalBoundaries.layer'
                draft[supplemental] = '\n'.join(remaining) + '\n'
        except ValueError:
            blockers.append({'rule': 'existing Dev boundary differs', 'file': sibling})
    for layer in dev.layers[world.lower()]:
        if layer not in draft and layer not in reused_layers:
            blockers.append({'rule': 'additional Dev layer', 'file': layer})
    if preset:
        for path in (preset, preset + '.meta'):
            draft[path] = staged[path]
    body = source.read(world)
    path_parent = re.fullmatch(r'\s*SubScene \{\s*Parent "([^"\r\n]+)"\s*}\s*', body)
    if path_parent and path_parent[1] in STOCK_TERRAIN_PARENTS:
        parent = path_parent[1]
        body = body.replace(f'"{parent}"', f'"{{{STOCK_TERRAIN_PARENTS[parent]}}}{parent}"', 1)
    if not re.fullmatch(r'\s*SubScene \{\s*Parent "\{[A-Fa-f0-9]{16}\}[^"]+"\s*}\s*', body):
        blockers.append({'rule': 'terrain and subscene', 'file': world})
    migrated_longmoor = body.strip() == f'SubScene {{\n Parent "{LONGMOOR_LEGACY_PARENT}"\n}}'
    if migrated_longmoor:
        body = body.replace(LONGMOOR_LEGACY_PARENT, LONGMOOR_CURRENT_PARENT)
    if route_graph and body.strip() == f'SubScene {{\n Parent "{BYSTRANY_LEGACY_PARENT}"\n}}':
        if not mode_index or any('SCR_MapEntity' in source.read(layer) for layer in source.layers[world.lower()]):
            blockers.append({'rule': 'terrain and subscene', 'file': world,
                             'detail': 'Bystrany wrapper replacement requires a unique terrain map'})
        else:
            body = body.replace(BYSTRANY_LEGACY_PARENT, BYSTRANY_NATIVE_PARENT)
            main_layer = mode_index[0]
            draft[main_layer] = BYSTRANY_MAP_ENTITY + draft[main_layer]
    draft[world] = body
    for header in row['headers']:
        try:
            _, _, files = launcher(source, dev, header)
            draft.update(files)
        except ValueError as exc:
            blockers.append({'rule': 'launcher', 'file': header, 'detail': str(exc)})
    if delayed:
        draft.update(delayed.assets)
    draft.update(commandos_runtime)
    blockers.extend(migrate_assets(source, dev, draft, asset_aliases, local_loadouts, spawned_assets,
                                   faction_assets or {}, archived_crew or {}))
    for path, text in draft.items():
        if path not in dev.files:
            continue
        data = dev.read_raw(path) if isinstance(text, bytes) else dev.read_raw(path).decode('utf-8-sig').replace('\r\n', '\n')
        if data.strip() != text.strip():
            if path in reviewed_replacements and text == replace_registered_paths(data, source, dev, asset_aliases):
                continue
            if (path == preset and isinstance(data, str)
                    and sha256(data.encode()).hexdigest() == row.get('reviewedSettingsPreset', {}).get(path)):
                reviewed_replacements[path] = sha256(data.encode()).hexdigest()
            elif (path.endswith('.layer') and isinstance(data, str) and DUPLICATE_CALLSIGNS.search(data)
                    and any(DUPLICATE_CALLSIGNS.sub(rf'\g<{copy}>', data).strip() == text.strip()
                            for copy in ('first', 'second'))):
                reviewed_replacements[path] = sha256(data.encode()).hexdigest()
            else:
                blockers.append({'rule': 'existing Dev file differs', 'file': path})
    complete_rosters = (entry.get('unitPrefabEntries', 0) for entry in row.get('groupLayers', {}).values())
    captures_audit = [record for trigger in row.get('captureCandidate', {}).values()
                      for record in trigger.values() if isinstance(record, dict)]
    startup_slots = sum(record.get('startupPlayableSlots', 0) for record in captures_audit)
    if VILLA_CONVOY_LAYER in source.layers[world.lower()] and any(
            asset.get('startupSlots') for asset in spawned_assets.values()):
        authored_groups = {name: spawned_assets[resource]['startupSlots']
                           for resource, (name, _, _) in VILLA_CONVOY_GROUPS.items()}
        observed = Counter((item['startupChoice'], item['spawnName'], item['startupPlayableSlots'])
                           for item in spawner_audit if 'startupPlayableSlots' in item)
        expected = Counter((flag, name, count) for flag in VILLA_CONVOY_FLAGS
                           for name, count in authored_groups.items())
        if (observed != expected or not any(tuple(sample['flags']) == VILLA_CONVOY_FLAGS
                                             and sample['drawCount'] == 1 for sample in startup_samples)):
            blockers.append({'rule': 'startup roster', 'file': VILLA_CONVOY_LAYER,
                             'detail': 'expected all three groups for each choice in the same exclusive draw'})
        else:
            startup_slots += sum(authored_groups.values())
    slots = sum(complete_rosters) + sum(entry['slots'] for entry in group_blocks) + startup_slots
    advertised = {}
    for header in row['headers']:
        count = re.search(r'^\s*m_iPlayerCount (\d+)\s*$', source.read(header), re.M)
        if count:
            advertised[header] = int(count[1])
    remaining = inactive_placements
    if any(item['trigger'] == 'inactive cleanup' for item in spawner_audit):
        remaining.append('The five generator light cleanup spawns miss the original world-postprocess callback. '
                         'The generator flags and messages remain active.')
    if any(item.get('startupPlayableSlots') for item in spawner_audit):
        remaining.append('Compare the selected convoy roster, callsigns, vehicle positions and marker audience.')
    if converted_hornbeams:
        remaining.append('The two retired hornbeams use current destructible trees of the same varieties. '
                         'Historical damage thresholds and break physics remain unverified.')
    if converted_artillery:
        remaining.append('Compare the call-in menu, radio authorization, salvo timing and the late-join ammunition display.')
    if phase_entity_capture:
        remaining.append('Compare the three phase snapshots and cleanup when a phase ends before its capture runs.')
    if cache_supply_depot and depot_layer == INTERDICTION_OUTCOME_LAYER:
        remaining.append('Compare the cargo selection, selected van refill, depot initialization and repeated exact-supply check.')
    elif cache_supply_depot:
        remaining.append('Compare the two ordered supply thresholds, including a first reading above both thresholds.')
    elif concrete_crushers:
        remaining.append('Compare the two retained intel pickups, command reveal and commander death in Match. '
                         'The reveal uses the native notification display duration.')
    elif script_flags:
        remaining.append('Compare depot measurement after either authored deadline, including ties, missing depots and one-shot delivery.')
    for target in sorted({spawn['missingGroupWaypoint'] for spawn in prefab_spawns if 'missingGroupWaypoint' in spawn}):
        remaining.append(f'The original reinforcement waypoint {target} has no authored target.')
    for target in sorted({route for spawn in prefab_spawns for group in spawn.get('crewGroups', ())
                          for route in group.get('missingWaypoints', ())}):
        remaining.append(f'The original reinforcement waypoint {target} has no authored target.')
    for target in sorted(missing_spawn_anchors):
        remaining.append(f'The original spawn at {target} has no authored anchor and does not run.')
    for target in sorted(set(missing_waypoints)):
        remaining.append(f'The original static waypoint link to {target} has no authored target.')
    for target in sorted(set(group_waypoint_links)):
        remaining.append(f'The original static waypoint link to {target} names a group and leaves orders unchanged.')
    for target in sorted(set(self_waypoints)):
        remaining.append(f'The original assignment refers to the waypoint {target} as its group and leaves orders unchanged.')
    for target in sorted(inert_spawner_orders):
        remaining.append(f'The original waypoint assignment for {target} names a spawner and does not run.')
    for target in sorted({spawn['selfGroupWaypoint'] for spawn in prefab_spawns if 'selfGroupWaypoint' in spawn}):
        remaining.append(f'The original reinforcement waypoint {target} names its spawner and leaves orders unchanged.')
    for target in sorted(missing_order_groups):
        remaining.append(f'The original waypoint assignment for {target} has no authored group and does not run.')
    for target in sorted(set(missing_static_vehicles)):
        remaining.append(f'The original static vehicle link to {target} has no mission-layer target.')
    if any(record.get('statusMessages') for trigger in row.get('captureCandidate', {}).values()
           for record in trigger.values() if isinstance(record, dict)):
        remaining.append('Compare extraction progress and status recipients during Match.')
    if any(record.get('triggerAnchor') for trigger in row.get('captureCandidate', {}).values()
           for record in trigger.values() if isinstance(record, dict)):
        remaining.append('Compare named capture anchors, polygon occupation and associated map outlines.')
    if any(record.get('attachedRadiusTriggers') for trigger in row.get('captureCandidate', {}).values()
           for record in trigger.values() if isinstance(record, dict)):
        remaining.append('Compare the parent and child radius triggers independently during Match.')
    if any(record.get('polygonMapOverlay') for trigger in row.get('captureCandidate', {}).values()
           for record in trigger.values() if isinstance(record, dict)):
        remaining.append('Compare the capture footprint and its shared map fill, outline and faction visibility.')
    if any('SCR_EditableSystemComponent "{5EDF4803CD048668}"' in body
           for path, body in draft.items() if path.endswith('.layer') and isinstance(body, str)):
        remaining.append('Compare Game Master marker selection and descriptor display. Live marker attribute editing remains deferred.')
    if startup_slots:
        remaining.append('Compare random pilot placement and the two authored slots before role selection.')
    if shared_writers:
        if reviewed_readers:
            remaining.append('Compare ordered area flag changes during staging and Match and the dependent actions.')
        else:
            remaining.append('Compare ordered area flag changes during staging and Match and the triggered AI spawns.')
    if any('m_fAuthoredCountdownSeconds ' in text for text in draft.values() if isinstance(text, str)):
        remaining.append('Compare the authored fractional staging release and displayed countdown.')
    if any('m_bRestrictAuthoredStagingCombat 1' in text for text in draft.values() if isinstance(text, str)):
        remaining.append('Compare remote-player staging input, vehicle exemptions, zone exit and release.')
    if any('m_bBriefingLeaderMarkers 1' in text for text in draft.values() if isinstance(text, str)):
        remaining.append('Compare leader rank and equal-rank precedence, briefing marker creation and Match drag lock.')
    if migrated_longmoor:
        remaining.append('Compare the authored locations against the historical Longmoor terrain.')
    capacity_notes = {}
    for header, actual in advertised.items():
        if header in REVIEWED_CAPACITY:
            header_blob, rosters, limit, slots_in_source = REVIEWED_CAPACITY[header]
            if (source.files[header] == header_blob
                    and all(source.files.get(roster) == blob for roster, blob in rosters)
                    and actual == limit and slots == slots_in_source):
                capacity_notes[header] = f'{limit} advertised players and {slots} original roster entries preserved.'
        elif (world == VILLA + 'StealthingIntoVilla.ent' and actual == 110 and slots == 75
              and source.files[header] == '415338f6ef78d0aa5811d82a54f34dde62d59bef'
              and source.files.get(VILLA_DEFAULT) == VILLA_DEFAULT_BLOB
              and source.files.get(VILLA_BLUFOR) == VILLA_BLUFOR_BLOB
              and source.files.get(VILLA_OPFOR) == VILLA_OPFOR_BLOB
              and source.files.get(VILLA_CONVOY_LAYER) == VILLA_CONVOY_LAYER_BLOB):
            capacity_notes[header] = '110 advertised players, 66 placed slots, and 9 slots in each convoy draw.'
    if ai_faction:
        remaining.append('AI casualty tracking uses native registration and confirmed death events. '
                         'Compare possession, faction changes and delayed recounts separately.')
    if delayed:
        remaining.append('Compare delayed extraction polling and status messages in multiplayer.')
    if any(ending.get('delivery') == 'match end' for event in rules.values() for ending in event.get('announcements', [])):
        remaining.append('Compare the authored time from the outcome trigger to native after-action report.')
    if event_flags:
        remaining.append('Compare timing of simultaneous flag changes and their dependent event chains.')
    if any(action.get('deactivatesEvents') for event in rules.values() for action in event.get('convertedActions', ())):
        remaining.append('Compare event deactivation before a trigger and after it queues delayed actions.')
    if any(action.get('reactivatesEvents') or action.get('runsEvents')
           for event in rules.values() for action in event.get('convertedActions', ())):
        remaining.append('Compare event rearming, direct runs and overlapping timed actions in Match.')
    if any(event.get('waypointAssignments') and event.get('trigger') not in ('Match start', 'dormant')
           for event in rules.values()):
        remaining.append('Compare waypoint assignment timing and order after the event condition becomes true.')
    if crew_names:
        remaining.append('Compare named crew groups across vehicle spawns, initial routes and rollback.')
    if any(asset.get('vehicle') for asset in spawned_assets.values()):
        remaining.append('Compare default convoy crew, rearming and flag reset when a replacement vehicle arrives.')
    if any(path.endswith('.layer') and '{7FF511EDC4034D9F}Prefabs/Vehicles/Helicopters/Mi24/Mi24V_Armed_UB1657_X4.et'
           in body for path, body in draft.items()):
        remaining.append('Compare the registered Mi-24V S5 loadout with the historical mission loadout.')
    if any(path.endswith('.layer') and '{9AA441DE6DAB6D94}Prefabs/Vehicles/Helicopters/Mi24/Mi24V_Armed_B8V20A_X4.et'
           in body for path, body in draft.items()):
        remaining.append('Compare the registered Mi-24V S8 loadout with the historical mission loadout.')
    if damage_flags:
        remaining.append('Compare damage notification timing in Match and confirm no objective damage during staging.')
    if any(asset.get('playable') for asset in roster_assets.values()):
        remaining.append('Compare named playable objectives, their damage flags and extraction with occupied roles.')
    if any(entry.get('inheritedBlackbox') for entry in supported_interactions):
        remaining.append('Compare blackbox marker visibility, faction-specific hold times and removal of the radio assembly.')
    if any(asset.get('destroyed') for asset in spawned_assets.values()):
        remaining.append('Compare destruction of a spawned objective, including pre-Match damage and the Match outcome.')
    if any(record.get('placedDestructionFlags') for record in row.get('damageFlagCandidates', {}).values()):
        remaining.append('Compare grouped objective destruction, including pre-Match damage and the Match outcome.')
    if any(record.get('namedDamageZones') for record in row.get('damageFlagCandidates', {}).values()):
        remaining.append('Compare initial health, named hit-zone repair flags and destruction before and during Match.')
    if any(record.get('placedCharacterDamage') for record in row.get('damageFlagCandidates', {}).values()):
        remaining.append('Compare character damage flags as the default hit zone changes, including death and removal.')
    if any(record.get('sharedDamageFlags') for record in row.get('damageFlagCandidates', {}).values()):
        remaining.append('Compare shared damage flags as separate producers reach or leave their target states.')
    if any(asset in draft for _, _, files in (faction_assets or {}).values() for asset in files):
        remaining.append('Compare faction presentation and flag textures against the legacy mission.')
    if any(action.get('instruction') == 'TILW_DeleteEntitiesInstruction'
           for event in rules.values() if event.get('trigger') == 'startup choice'
           for action in event.get('convertedActions', ())):
        remaining.append('Compare both startup branches and confirm that only the unwanted entity is removed.')
    if any(isinstance(text, str) and 'm_eTargetState ' in text and 'GC_DestroyedEntityFlagComponent ' in text
           for text in draft.values()):
        remaining.append('Compare damage flags at the authored hit zone state during Match.')
    if any('initialHealth' in record for record in row.get('damageFlagCandidates', {}).values()):
        remaining.append('Compare initial hit zone health and subsequent repairs.')
    if any(isinstance(text, str) and 'GC_StaticSentryComponent ' in text for text in draft.values()):
        remaining.append('Compare stationary sentry firing, danger response, and control when a player possesses and releases a sentry.')
    if teleport_targets:
        remaining.append('Compare authored teleport choices, reach, faction visibility and destinations in Match.')
    if required_teleport_flags:
        remaining.append('Compare teleport availability as the required Match flag is set or cleared.')
    if required_interaction_flags:
        remaining.append('Compare flag-gated mission actions, including repeated use and action-specific hold times.')
    if placed_crew_fields:
        remaining.append('Compare placed crew arrival at Match start, compartment order and gunner dismount policy.')
    if any('m_aCrewGroups {' in crew for crew in placed_crew_fields):
        remaining.append('Compare multi-station seat reservation, empty seats and separate AI groups.')
    if any(isinstance(body, str) and 'm_bCheckRootBoundsAtPoint 1' in body for body in draft.values()):
        remaining.append('Compare all startup group placements, point contact rejection and terrain alignment.')
    if crew_waypoints:
        remaining.append('Compare placed crew waypoint order, delay and separate idle turret grouping.')
    if any(isinstance(body, str) and '{823CA4130CE90077}' in body for body in draft.values()):
        remaining.append('Compare the AH-6 gun and rocket loadout against the legacy scenario.')
    if any(isinstance(body, str) and '{C8C37964483452EA}' in body for body in draft.values()):
        remaining.append('Compare the registered Army Cobra rocket loadout against the legacy scenario.')
    if any(isinstance(body, str) and '{0174CF72FDB6B805}' in body for body in draft.values()):
        remaining.append('Compare the Marine Cobra pylons, disabled stations and AO exemption.')
    if any(isinstance(body, str) and f'{{{MARINE_M299[0]}}}' in body for body in draft.values()):
        remaining.append('Compare the Marine Cobra flight handling, sight and cockpit controls in the current WCS release.')
    if any(isinstance(body, str) and '{2FA364D8E326CB3C}' in body for body in draft.values()):
        remaining.append('Compare the Mi-24P control, pylon selection and turret in the current WCS release.')
    if any(isinstance(body, str) and '{F4D545DC6824850D}' in body for body in draft.values()):
        remaining.append('Compare House K door interaction and destruction in the current terrain package.')
    if any(isinstance(body, str) and RHIB_PATH in body for body in draft.values()):
        remaining.append('Compare RHIB boarding, flotation and the authored front and rear mounts.')
    if any(isinstance(body, str) and '{42232FFE6E7AC415}' in body for body in draft.values()):
        remaining.append('Compare wirecutter pickup, storage and fence interaction in Match.')
    if any(isinstance(body, str) and '{D17CCBF7FE9AAFEC}' in body for body in draft.values()):
        remaining.append('Compare the woodland CROWS II turret and crew stations against the legacy scenario.')
    if any(path.endswith('.layer') and isinstance(body, str) and '{BC07885176C7FF8A}' in body
           for path, body in draft.items()):
        remaining.append('Compare T-72A compartments, storage and appearance against the published addon stack.')
    if any(path.endswith('.layer') and isinstance(body, str) and '{726286E1F9570EA1}' in body
           for path, body in draft.items()):
        remaining.append('Compare T-72B crew and cargo compartments, inventory, damage and vehicle movement against the published addon stack.')
    if any('m_bShareIdleGunnerGroup 1' in body for body in draft.values() if isinstance(body, str)):
        remaining.append('Compare boarding, idle turret grouping and delayed waypoints for spawned vehicle crews.')
    if any(item['trigger'] == 'peak player loss' and item.get('inverted')
           for rule in rules.values() if 'trigger' in rule for item in leaves(rule)):
        remaining.append('Compare victory notices below and at the authored casualty threshold after the first sample.')
    if any(rule.get('requiresMatch') for rule in rules.values()):
        remaining.append('Compare compound outcome activation around the authored threshold during Match.')
    for rule in rules.values():
        if rule.get('reason') == 'no authored event condition':
            remaining.append(f'The original event {rule["name"]} has no authored condition and does not run.')
        for notice in rule.get('notificationsWithoutRecipients', []):
            remaining.append(f'The original notification {notice["instructionId"]} in {rule["name"]} '
                             'has no eligible recipient and does not display.')
    if outcome.get('derivedFlags'):
        remaining.append('Compare derived outcomes as source flags change, including inverted consumers in Match.')
    if ticket_edits:
        remaining.append('Compare event-driven ticket changes and any spectator respawn requests.')
    if any(path.endswith('.layer') and isinstance(body, str)
           and re.search(r'^ {2,}(?:\$grp )?SCR_AIGroup\b', body, re.M)
           for path, body in draft.items()):
        remaining.append('Compare nested group placement, child roster registration and slot order.')
    if any(path.endswith('.layer') and isinstance(body, str) and COLOR_TEAM_ARRAY in body
           for path, body in draft.items()):
        remaining.append('Compare authored group color teams and their slot assignments.')
    if any(path.endswith('.layer') and isinstance(body, str) and DYNAMIC_LOD_DISTANCE in body
           for path, body in draft.items()):
        remaining.append('Compare group simulation as players cross the authored LOD distance.')
    if converted_bare_map:
        remaining.append('Compare authored map fill, outline and faction visibility, including unassigned viewers.')
    if converted_scenery_map:
        remaining.append('Compare attached scenery markers and spline outlines across zoom and faction assignment.')
    if converted_cover:
        remaining.append('Native cover framing follows map opens. Compare framing and zoom against the legacy cover.')
    if converted_passive_cover:
        remaining.append('Compare faction-only cover fill and confirm it leaves the map view unchanged.')
    if any(isinstance(body, str) and 'm_bPreviewOnHover 1' in body for body in draft.values()):
        remaining.append('Compare hover camera framing, faction visibility and cleanup on map close.')
    if any(isinstance(text, str) and '{3B62608C6AB716B9}' in text and 'm_fSize 128000\n' in text
           for text in draft.values()):
        remaining.append('Compare the oversized cover marker at the authored scale across map zoom levels.')
    if any(' m_bFillOpenShape 1' in body for bodies in boundaries.values() for body in bodies):
        remaining.append('Compare polygon fill and confirm the authored open outline has no closing segment.')
    if any(' IsClosed 0' in body for bodies in boundaries.values() for body in bodies):
        remaining.append('Compare open briefing map outlines as zoom and faction view change.')
    if any(path.endswith('.layer') and isinstance(body, str)
           and re.search(r'^ {2,}GenericEntity(?: [\w.-]+)? : "' + re.escape(MAP_MARKER_PREFAB), body, re.M)
           for path, body in draft.items()):
        remaining.append('Compare nested marker positions, audience and drawing order.')
    if status_areas:
        remaining.append('Compare capture and loss notices for areas with no outcome rule.')
    if any('GC_NavmeshRebuildComponent' in body for bodies in boundaries.values() for body in bodies):
        remaining.append('Compare navmesh rebuilds for map shape placements.')
    if any(isinstance(body, str) and all(f'{{{identity}}}{path}' in body
                                         for identity, path in ZARICHNE_NAVMESH_ALIASES.values())
           for body in draft.values()):
        remaining.append('Compare AI routing on the authored Zarichne island and verify custom navigation links.')
    if BLOODY_GARRISON_PREFAB in draft:
        remaining.append('Compare inherited garrison waypoints, slot transforms and navmesh rebuilds.')
    if mode and any(line.startswith((' m_iReconnectTime ', ' m_iReconnectTimeAfterBriefing ')) for line in mode):
        remaining.append('Compare reconnect reservations before and after entering Match.')
    if mode and ' m_bDisableBuildingModeAfterFreezeTime 1' in mode:
        remaining.append('Compare limited editor access before and after authored staging, including late joins and Game Master access.')
    if mode and any(line.strip() in ('m_bOverrideGeocoords 1', 'm_bOverrideTimeZoneInfo 1') for line in mode):
        remaining.append('Compare geographic coordinates, local time and sunlight at mission start.')
    if any(path.endswith('.layer') and isinstance(body, str) and 'GC_AOLimitEntity' in body
           and ('\n  GC_AOLimitEntity ' in body or '\n  PolylineShapeEntity ' in body)
           for path, body in draft.items()):
        remaining.append('Compare nested boundary positions, faction outlines and enforcement.')
    if any(instance.get('bridgeMetres') for layer in row.get('boundaryLayers', {}).values()
           for instance in layer.get('instances', {}).values()):
        remaining.append('Compare boundary containment and the map outline at the repaired crossing.')
    if SKALISTY_FALLBACK_AO in draft:
        remaining.append('Compare the fallback boundary transition, containment at all three crossings, and public shading.')
    if any(spawn['startupChoice'] for spawn in prefab_spawns):
        remaining.append('Compare pregame selection, spawn timing and faction-specific briefing and map updates.')
    if any(event.get('startupVehicles') for event in rules.values()):
        remaining.append('Compare pregame vehicle placement, crew seating and delays across the transition to Match.')
    if outcome.get('startupSamples'):
        if any(spawn.get('startupSample') for spawn in prefab_spawns):
            remaining.append('Compare the startup draws and verify each selected spawn in its authored phase.')
        elif any(spawn.get('startupInitializationSeconds') and spawn['startupChoice'] for spawn in prefab_spawns):
            remaining.append('Compare the single startup draw and verify each selected spawn in its authored phase.')
        else:
            remaining.append('Compare startup flag draws and verify selected events activate only in Match.')
    if any(not spawn['startupChoice'] for spawn in prefab_spawns):
        remaining.append('Compare spawn placement and timing in Match, including concurrent actions.')
    if any(spawn.get('crewDefaults') for spawn in prefab_spawns):
        remaining.append('Compare prefab default occupants, seat filters and crew orders on event-spawned vehicles.')
    if any(spawn.get('crewGroups') for spawn in spawner_audit):
        remaining.append('Compare explicit crew seating and group routes on timed vehicle spawns.')
    if mode_index and source.files[mode_index[0]] == STASHED_CACHE_MODE_BLOB:
        remaining.append('Compare the 06:00 Cloudy start, support actions, construction and faction catalogues.')
        remaining.append('The legacy debrief displays a zero-score Major Loss label. GC Lobby displays its native after-action report.')
    if (BEACH_HOLIDAY_DEFAULT in source.layers[world.lower()]
            and source.files[BEACH_HOLIDAY_DEFAULT] == BEACH_HOLIDAY_DEFAULT_BLOB):
        remaining.append('Compare the authored helicopter armament overrides and historical infrared lock delay.')
    if world == SEEDER_WORLD and spawned_assets:
        remaining.append('Compare successive main and side objectives, shared flag resets and scheduled AI crews in Match.')
    if commandos_objectives:
        remaining.extend([
            'Compare Commandos target registration before cleanup, the opening draw, shared interaction completion and damage notices.',
            'Compare Commandos owner-only objective pages and markers across reconnect and with a second client.',
            'Compare the Dog role model, moving-character footprints, bark and voice during possession and after leaving the role.',
        ])
    return ({} if blockers else draft), {
        'status': 'blocked by conversion rules' if blockers else 'generated for file review',
        'blockers': blockers, 'files': sorted(draft), 'mixedLayerGroups': group_blocks,
        'verifiedPlayableSlots': slots, 'advertisedSlots': advertised,
        **({'reviewedCapacity': capacity_notes} if capacity_notes else {}),
        **({'reviewedReplacements': reviewed_replacements} if reviewed_replacements else {}),
        **({'unusedRandomFlags': unused_flags} if unused_flags else {}),
        **({'placedSpawners': spawner_audit} if spawner_audit else {}),
        **({'commandosObjectives': commandos_objectives} if commandos_objectives else {}),
        **({'remainingBehavior': remaining} if remaining else {}),
    }


def plan(source, dev, report, staged, area_placements, interactions, convert_group_block, asset_aliases,
         local_loadouts, spawned_assets, convert_crew, resolve_spawn, resolve_crew, resolve_crew_config, delayed_extractions,
         faction_assets=None, archived_crew=None):
    results, emitted, totals, families = {}, {}, Counter(), Counter()
    for world, row in report['worlds'].items():
        draft, result = world_draft(source, dev, world, row, staged, area_placements.get(world, {}),
                                    interactions.get(world, ((), {})), convert_group_block, asset_aliases,
                                    local_loadouts, spawned_assets.get(world, {}), convert_crew,
                                    partial(resolve_spawn, spawned_assets.get(world, {})), resolve_crew,
                                    resolve_crew_config, delayed_extractions.get(world), faction_assets, archived_crew)
        results[world] = result
        totals[result['status']] += 1
        families.update({blocker['rule'] for blocker in result.get('blockers', [])})
        for path, text in draft.items():
            if path in emitted and emitted[path] != text:
                raise ValueError('batch drafts disagree on ' + path)
        emitted.update(draft)
    digests = {path: sha256(text if isinstance(text, bytes) else text.encode()).hexdigest() for path, text in emitted.items()}
    grouped = dict(sorted(families.items(), key=lambda item: (-item[1], item[0])))
    report['batchMigration'] = {'totals': dict(totals), 'blockedWorldsByRule': grouped,
                                'worlds': results, 'sha256': digests}
    return emitted
