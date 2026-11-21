"""Apply common migration rules to legacy missions and report unsupported authoring."""
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache, partial
from hashlib import file_digest, sha256
from pathlib import PurePosixPath
import re

from arc_capture import (CAPTURE_EDITOR_FIELDS, CAPTURE_TRANSFORM, LONGMOOR_CURRENT_PARENT,
                         LONGMOOR_LEGACY_PARENT, capture_instance, capture_messages, capture_status_texts, faction_keys)
from boundaries import ROOT as AO_SHAPE, BARE_SHAPE, PASSIVE_COVER, STANDALONE_SHAPE, convert_boundaries
from briefings import QUOTED, block_end, guid, properties, target_names
from catalog import REF, SOURCE_TEXT
from event_actions import NUMBER, SPAWN, SPAWN_FIELDS, SPAWN_NAME, render_crew_config, render_spawn, spawn_details
from vehicle_crew import VERIFIED_DEFAULT_CREW, crew_config_contents
from events import EVENT, LEGACY_FLAG, peak_flags
from extraction import DEFAULT_STATUS as EXTRACTION_STATUS, condition as extraction_condition
from groups import COLOR_TEAM_ARRAY, DYNAMIC_LOD_DISTANCE, inheritance, reviewed_native_ancestry
from interactions import BLACKBOX, BLACKBOX_FLAG, blackbox_placement, blackbox_prefab
from interactions import convert_block as convert_interaction_block, convert_object
from launchers import META_NAME, launcher, replace_reference
from markers import DEST as MAP_MARKER_PREFAB, convert_hover_camera, convert_layer
from outcomes import FRAMEWORK, SCRIPT_FLAG_APIS, leaves, render as render_outcome, render_capture_trackers
import polygon_capture
from respawns import authored_respawn_fields, read_respawns
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
LEGACY_JIP_MODES = {'Deny': 'DISABLED', 'Auto': 'AUTO', 'Manual': 'MANUAL',
                    '0': 'DISABLED', '1': 'AUTO', '2': 'MANUAL'}
NATIVE = re.compile(r'^(?:\$grp )?(?:Vehicle|Tank|RHS_Tank|Turret|SCR_AIWorld|GameEntity|GenericEntity|GenericTerrainEntity|GenericWorldFogEntity|StaticModelEntity|Tree|'
                    r'DecalEntity|SCR_DestructibleEntity|SCR_DestructibleTreeV2|SCR_\w*(?:Waypoint|BuildingEntity|EnvironmentalEntity|RestrictionZoneEntity)|'
                    r'AIWaypointCycle|PolylineShapeEntity|SCR_MapEntity|ACE_TacticalLadderEntity|LightEntity|Projectile|CommentEntity)\b')
WEATHER = re.compile(r'  SCR_TimeAndWeatherHandlerComponent "\{5EE3229927D4D2F5\}" \{')
INIT_WEATHER = '  SCR_InitWeatherComponent "{61B68227C35BFE9A}" {'
INIT_WEATHER_FIELD = re.compile(
    r'   (?:m_sInitialStartingWeatherId "[\w .-]*"|m_iSetWeatherConstant [01]|'
    r'm_initialWeatherDuration(?:Min|Max) \d+(?:\.\d+)?)')
PERCEPTION = re.compile(r'^PerceptionManager(?: [\w.-]+)? : "\{028DAEAD63E056BE\}[^"]+" \{$')
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
DAMAGE_COMPONENT = re.compile(rf'  TILW_Flag_EntityDamage {SERIALIZED_ID} \{{')
DAMAGE_TARGETS = {
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
    'FE65E8C60C751352': 'Prefabs/Characters/Factions/INDFOR/FIA/Character_FIA_PL.et',
}
DAMAGE_FLAG_NAME = re.compile(r'"[\w .-]+"')
SPAWN_DAMAGE_TARGETS = {
    ('ADA7E056B17AC663',
     'PrefabsEditable/Auto/Props/Military/AmmoBoxes/EquipmentBoxStack/E_EquipmentBoxStack_USSR_01_V2_covered.et'),
}
GMFX_AMMO_TARGETS = (
    ('8B9AFAD3A1548FCE', 'Prefabs/Destructible/AmmoStorage/GMFX_AmmoStorage.et'),
    ('4C931B452C796A2A', 'Prefabs/Destructible/AmmoStorage/GMFX_AmmoStorage_USSR.et'),
)
GMFX_PROVIDER = 'GameMasterFX_5994AD5A9F33BE57'
GMFX_DIGEST = '09a2e7a14300b916b1eaea43446d39d1d3334209e692e50d29406233962f7c80'
WORKSHOP_DAMAGE_TARGETS = {
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
}
WORKSHOP_SPAWN_PREFABS = {
    'BritishForces_5AE50EC5B8D6F4AE': (
        None, (('775DFB7F4529A122', 'Prefabs/Weapons/Rifles/L42A1/Rifle_L42A1.et'),),
    ),
    'RHS-StatusQuo_595F2BF2F44836FB': (
        None,
        (('1A486F1C4D22A32A', 'Prefabs/Vehicles/Tracked/T14/VehParts/Seats/T14_seat.et'),
         ('EDD3E14E481BDA62', 'Prefabs/Vehicles/Airplanes/FA18A/FA18A_GBU_CAS.et'),
         ('EAD777F4CDA11F16', 'Prefabs/Vehicles/Airplanes/F22/F22_Flyby.et'),
         ('FDD01BF3CEAB37E3', 'Prefabs/Vehicles/Airplanes/SU57/SU57_Flyby.et')),
    ),
    GMFX_PROVIDER: (
        GMFX_DIGEST,
        (('DDDDBEC77B49A995', 'Prefabs/Systems/Explosions/Wrapper_Bomb_Huge.et'),
         ('30263B4F1FB2A0F2', 'Prefabs/Systems/Artillery/ArtilleryWrapper_WTF.et')),
    ),
}
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
    ),
}
REVIEWED_HIT_ZONES = {
    ('5CFD8CB5C3E55A4E', 'Prefabs/Vehicles/Tracked/BFV/M2A2_MERDC.et', '"Engine_01"'),
}
# The published CIV group chain and base group have no damage manager.
DAMAGELESS_GROUPS = {
    '0C8BEF088C9D62CA': ('Prefabs/Groups/CIV/Generic/P/Group_CIV_Generic_P.et',
                         '7cb828f03ecaa39cd7f01693a4c0b653baa2e631216be0fa5095cf4efb76ad63'),
}
PLACED_SPAWNER = re.compile(
    r'(?P<group>\$grp )?TILW_PrefabSpawnerEntity(?: (?P<name>[\w.-]+))? : '
    r'"\{7F10372CF1CA1175\}Prefabs/Utils/TILW_PrefabSpawner.et" \{')
SPAWNER_TRANSFORM = {'coords', 'angles', 'angleX', 'angleY', 'angleZ'}
STOCK_TERRAIN_PARENTS = {
    'worlds/Arland/Arland.ent': 'A9806AF617972E97',
    'worlds/Eden/Eden.ent': '853E92315D1D9EFE',
}
REVIEWED_CAPACITY = {
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
    'Missions/Gabe/TVT97PowerStruggle/TVT97PowerStruggle.conf': (
        'ab58aad1aec16739e3327402f7f9f7eca62a165a',
        (('worlds/Gabe/PowerStruggle/TVT97PowerStruggle_Layers/Players.layer',
          '23f5f5f4c38796dfad894f9c6404be7c2fc55eb9'),),
        97, 95,
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
        'ecde2a6f7b09a65b77a6bbf372c3ed282762daed', '6219244BFF9757BC', 'QRFSpawn',
    ),
    'worlds/Cunnah/Arty_Arty_Arty/Arty_Arty_Arty.ent': (
        'worlds/Cunnah/Arty_Arty_Arty/Arty_Arty_Arty_Layers/default.layer',
        '475f775d2eac67dd94f1161e277b32a2a8094a55', '650557F5B24BB14C', 'motar_target_8',
    ),
}
REVIEWED_MISSING_ORDER_GROUPS = {
    'worlds/HitTheMan/Bradley Leads The Way/BradleyLeadsTheWay.ent': (
        'worlds/HitTheMan/Bradley Leads The Way/BradleyLeadsTheWay_Layers/default.layer',
        '6794efd40331f9e3f20c6c14b3955b4468279d08', {'NVA_AI_7', 'NVA_AI_10', 'T72_NVA2'},
    ),
}
REVIEWED_MISSING_WAYPOINTS = {
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
REVIEWED_MISSING_SPAWN_WAYPOINTS = {
    'worlds/HitTheMan/VDV Hello From The Sky/VDVHelloFromTheSky_Layers/AI_Defenders/AI_Defenders_South.layer':
        ('93dda27768ebdc5519b0ee2ef9ef57304cb44b1e', 'Def_WPMain_US_10'),
}
REVIEWED_MISSING_STATIC_VEHICLES = {
    'worlds/Cunnah/A_Hop_Skip_And_A_Jump/a_hop_skip_and_a_jump_Layers/OPFOR.layer':
        ('9a5b8ff212d1ba8c484e8e17dfed060b9baff796', {'T72A_1'}),
    'worlds/TheLocalPub/SD01_ConvoyChaos/SD01_ConvoyChaos_Layers/Opfor/Players.layer':
        ('9f0599d33d2c7b9964f1e5bd6c7fa526151c29e9', {
            'Ural4320_ammo1', 'Ural4320_ammo2', 'Ural4320_command1',
            'Ural4320_repair1', 'Ural4320_repair2', 'Ural4320_tanker1',
        }),
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
    rf'^(?P<first>(?P<indent> +){CALLSIGN_TRIPLET})(?P=indent){CALLSIGN_TRIPLET}', re.M)
AI_WORLD_ALIASES = {
    '33A5B976E41B0A4C': ('Prefabs/AI/SCR_AIWorld_BadOrb.et', 'Prefabs/AI/SCR_AIWorld_BadOrb_GC.et'),
    '70CCCF16487C927F': ('GitHub/gc-reforger-core/Prefabs/AI/SCR_AIWorld_Eden.et',
                         'Prefabs/AI/SCR_AIWorld_Eden.et'),
}
STOCK_RESOURCE_ALIASES = {
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
    ('F849217AB3BA88BE', 'Prefabs/Items/Equipment/Maps/PaperMap_01_folded_USSR.et'):
        'Prefabs/Items/Equipment/Maps/Map_Paper_01/PaperMap_01_folded_USSR.et',
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
WORKSHOP_RESOURCE_ALIASES = {
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
    ('89B49CDEBBEC973A', 'Prefabs/Weapons/Magazines/Magazine_127x99_M107A1_10rnd_M2.et'): (
        'TacticalFlava-Weapons_DEADC0DE00000001', '89B49CDEBBEC973A',
        'Prefabs/Weapons/Magazines/M107A1/Magazine_127x99_M107A1_10rnd_M2.et'),
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
    ('12C35C0A24EDA6D0', 'Prefabs/Vehicles/Tracked/BFV/M2A3_BUSK_MERDC.et'): (
        'WCS_M2A2_63120AE07E6C0966', '12C35C0A24EDA6D0',
        'Prefabs/Vehicles/Tracked/M2A2/M2A3/M2A3_BUSK_MERDC.et'),
    ('B3F2B47340817ED6', 'Prefabs/Items/GC_Facepaint_01_US.et'): (
        'ACEFacepaintDev_68EAA4B96976D557', 'B3F2B47340817ED6', 'Prefabs/Items/ACE_Facepaint_01_US.et'),
    ('9FF4AA98784BDF8B', 'Prefabs/Vehicles/Helicopters/AH6M/OPFOR/AH6M_OPFOR_base.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '9FF4AA98784BDF8B',
        'Prefabs/Vehicles/Helicopters/AH6M/OPFOR/AH6M_OPFOR_Base.et'),
    ('72018C87FBDC9F8C', 'Prefabs/Vehicles/Tracked/BFV/M2A2_Desert.et'): (
        'WCS_M2A2_63120AE07E6C0966', '72018C87FBDC9F8C',
        'Prefabs/Vehicles/Tracked/M2A2/M2A2_Desert.et'),
    ('72C04A6EABC12143', 'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '72C04A6EABC12143',
        'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134_X2.et'),
    ('823CA4130CE90077', 'Prefabs/Vehicles/Helicopters/AH6M/AH6M_RKT.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '823CA4130CE90077',
        'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134_M260_X2.et'),
    ('1DC01AAACFFA8B63', 'Prefabs/Vehicles/Tracked/BMP1/BMP1_base.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '1DC01AAACFFA8B63',
        'Prefabs/Vehicles/Tracked/BMP1/BMP1_Base.et'),
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
WIRECUTTER_LAYER_BLOBS = {'7673b673c619b44b0f80cfb54d029b5cdd208350', 'f5c094b3c8f19c596b5f44c2b903f6942e0c0874'}
WIRECUTTER_PREFAB = ('42232FFE6E7AC415', 'Prefabs/Items/Core/Wirecutters/wirecutters.et')
LEGACY_WIRECUTTER = re.compile(
    r'\$grp GenericEntity : "\{8554ED3F088BADA6\}Prefabs/Items/Core/Wirecutters/wirecutters(?:old)?\.et" \{')
UNITS_PREFAB_ALIASES = {
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
    ('B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/Radio_R187P1_OLD_Single_Separatist.et'): (
        'B343AB76B0725657', 'Prefabs/Items/Equipment/Radios/RU/Radio_R187P1_OLD.et',
        '5575080709db948fbfb8f0a84fe746aa32e3f72aec97ccbc41ff02e5ea8bb741'),
}
REVIEWED_NATIVE_ASSETS = {
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
    'worlds/anteante101/Prefabs/group/Group_GC_ION_Uniformed_CAT_WL_AA101.et':
        'ae267b03015662a0badcfa9210ce86ca18d1c3bf2e06efe19fe8b0b009fe4813',
    'worlds/arc/CommandosReforged/Prefabs/BLU/Barret_m107a1_IR.et':
        '38e719f739bb51dc4924dd16f6ddc92a3a476aaf0c91b6c03bffc8ac1a738ab8',
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
STARTUP_FIELDS = {
    'm_fRadius': 'm_fRadius',
    'm_excludePrefabsInList': 'm_bExcludePrefabs',
    'm_bVObjectsOnly': 'm_bVisualObjectsOnly',
    'm_bByParentOnly': 'm_bRootsOnly',
    'm_bPreviewShape': 'm_bPreviewShape',
}


def startup_cleanup(block):
    root = STARTUP_CLEANUP.fullmatch(block[0])
    if not root:
        raise ValueError('unsupported startup cleanup placement')
    output = []
    for name, lines in polygon_capture.members(block, root['group'], root['name']):
        values = properties(lines, STARTUP_FIELDS.keys() | CAPTURE_TRANSFORM | {'m_prefabFilterList'})
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
        output.extend([f'{entity} {{', *(f' {key} {v}' for key, v in transform.items()),
                       *(f' {native} {values[key]}' for key, native in STARTUP_FIELDS.items() if key in values)])
        if entries:
            output.extend([' m_aPrefabs {', *(f'  {entry}' for entry in entries), ' }'])
        output.append('}')
    if not output:
        raise ValueError('empty startup cleanup group')
    return '\n'.join([*output, ''])


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


CORE_INITIAL_DAMAGE = re.compile(
    r'components \{\nPK_EntityModifierComponent "\{[\da-fA-F]{16}\}" \{\n'
    r'm_bDamageManagerEnabled 1\nm_aHitzonesData \{\n'
    r'PK_HitzoneData "\{[\da-fA-F]{16}\}" \{\nm_fHealthPercentage 0\n\}\n\}\n\}\n\}'
)


def placed_core_character(block, resolve_character):
    """Keep a verified Core character and its original default-zone startup modifier."""
    head = re.fullmatch(
        r'(?P<group>\$grp )?SCR_ChimeraCharacter(?: (?P<name>[\w.-]+))? : '
        + REF.pattern + r' \{', block[0])
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
    reference = resolve_character(prefab[1].upper(), prefab[2])
    header = block[0].replace(prefab[0], reference, 1)
    return native_placement([header, *block[1:]], reviewed_character=True)


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
            '  components {',
            '   InventoryItemComponent "{5222EB4D0C73006B}" {',
            '    Attributes SCR_ItemAttributeCollection "{5222EB4D0A2B466B}" {',
            '     ItemPhysAttributes ItemPhysicalAttributes "{62F35B4168194640}" : PhysicalAttributes {',
            '      Weight 0.01',
            '     }',
            '     m_SlotType SLOT_LOADOUT_STORAGE',
            '    }',
            '   }',
            '  }',
            *(' ' + field for field in fields),
            ' }',
        ])
    return '\n'.join([*rebuilt, '}', ''])


def native_placement(block, *, reviewed_character=False):
    if not NATIVE.match(block[0]) and not (
            reviewed_character and re.match(r'^(?:\$grp )?SCR_ChimeraCharacter(?: [\w.-]+)? : ', block[0])):
        return None
    lines = block
    if any('TILW_NavmeshRegenComponent' in line or 'PK_EntityProtectorComponent' in line for line in block):
        lines = block.copy()
        stack = []
        for index, line in enumerate(block):
            if (NAVMESH_COMPONENT.fullmatch(line) and stack and stack[-1] == 'components {'
                    and block[index + 1] == ' ' * (len(line) - len(line.lstrip())) + '}'):
                lines[index] = line.replace('TILW_NavmeshRegenComponent', 'GC_NavmeshRebuildComponent', 1)
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
    text = '\n'.join(lines) + '\n'
    return None if LEGACY.search(text) else text


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
                native, details = convert_interaction_block(block, location, stock, local_resources)
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


def damage_flag_rules(source, world, stock, local_resources, source_units, dev_units, rhs=None, *,
                      workshop_targets=()):
    placements, audit, candidates = {}, {}, defaultdict(list)
    observed = {}
    layers = source.layers[world.lower()]
    for path in layers:
        text = source.read(path)
        if 'TILW_Flag_EntityDamage' not in text:
            continue
        for line, block in blocks(text):
            location = f'{path}:{line}'
            if block[0].startswith('$grp '):
                if not any('TILW_Flag_EntityDamage' in part for part in block):
                    continue
                try:
                    observed[(path, line)] = grouped_damage_placement(
                        block, stock, local_resources, source_units, dev_units, rhs,
                        workshop_targets=workshop_targets)
                except ValueError as exc:
                    audit[location] = {'gap': str(exc)}
                continue
            components = [i for i, part in enumerate(block) if DAMAGE_COMPONENT.fullmatch(part)]
            if not components:
                continue
            try:
                ref = REF.search(block[0])
                if len(components) != 1 or not ref or not stock or ref[1].upper() in local_resources:
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
                verified_default = (
                    zone == '""' and (stock_target or (resource, ref[2]) in workshop_targets)
                    and is_character == ref[2].startswith('Prefabs/Characters/')
                    and resource not in source_units.resources and resource not in dev_units.resources)
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
                native = native_placement(converted)
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


def grouped_damage_placement(block, stock, owned, source_units, dev_units, rhs, *, workshop_targets=()):
    ref = REF.search(block[0])
    if (not ref or not re.match(r'^\$grp (Vehicle|Tank|RHS_Tank|GameEntity|GenericEntity|SCR_ChimeraCharacter) ', block[0])
            or ref[1].upper() in owned):
        raise ValueError('unverified grouped damage objective prefab')
    resource, path = ref[1].upper(), ref[2]
    is_character = block[0].startswith('$grp SCR_ChimeraCharacter ')
    registered = ((stock and DAMAGE_TARGETS.get(resource) == path and stock.contains(resource, path))
                  or (resource, path) in workshop_targets)
    verified_target = registered and resource not in source_units.resources and resource not in dev_units.resources
    reviewed_units = UNITS_DAMAGE_TARGETS.get((resource, path))
    if not verified_target and reviewed_units and stock:
        blob, parent = reviewed_units
        verified_target = (parent[0] not in owned and stock.contains(*parent)
                           and all(units.resources.get(resource) == [path]
                                   and units.files.get(path) == blob
                                   and parent[0] not in units.resources
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
    native = native_placement([*lines, block[-1]], reviewed_character=is_character)
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
    for path in layers:
        text = source.read(path)
        if 'm_conditionFlag ' not in text or 'TILW_PrefabSpawnerEntity ' not in text:
            continue
        for _, block in blocks(text):
            match = PLACED_SPAWNER.fullmatch(block[0])
            if not match:
                continue
            try:
                members = list(polygon_capture.members(block, match['group'], match['name']))
            except ValueError:
                continue
            for _, fields in members:
                for line in fields:
                    if flag := re.fullmatch(r' m_conditionFlag "([\w.-]+)"', line):
                        flags[flag[1]] += 1
    return flags


def unused_flag_sampling(source, world, path, line, framework):
    start = framework.index(' m_randomFlags {')
    end = block_end(framework, start)
    flags = []
    index = start + 1
    while index < end:
        if not re.fullmatch(r'  TILW_FlagSampling "\{[\da-fA-F]{16}\}" \{', framework[index]):
            return []
        stop = block_end(framework, index)
        data = properties(framework[index + 1:stop], {'m_flagNames'})
        names = data.get('m_flagNames', '')
        name = r'"([\w.-]+(?: +[\w.-]+)*)"'
        if not re.fullmatch(rf'(?:{name}\s*)+', names):
            return []
        flags.extend(re.findall(name, names))
        index = stop + 1
    content = source.read(path).splitlines()
    # Exclude the entire sampling block only. Declarations elsewhere in the same layer still count as uses.
    rest = '\n'.join(content[:line + start - 1] + content[line + end:])
    other = '\n'.join(source.read(p) for p in sorted(source.closure(world) - {path})
                      if PurePosixPath(p).suffix.lower() in SOURCE_TEXT)
    scripts = script_text(source)
    if not flags or any(f'"{flag}"' in rest or f'"{flag}"' in other or flag in scripts for flag in flags):
        return []
    if 'GetFlagSetCopy' in scripts or 'm_flagSet' in scripts:
        return []
    return sorted(set(flags))


PLACED_ENTITY = re.compile(r'(?P<group>\$grp )?(?P<kind>\w+)(?: (?P<name>[\w.-]+))?(?: : "[^"]+")? \{')


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
        head = PLACED_ENTITY.fullmatch(block[0])
        if not head:
            return
        if head['name']:
            named.setdefault(head['name'], []).append((head['kind'], block))
        if head['group']:
            for index, line in enumerate(block[1:-1], 1):
                member = re.fullmatch(r' (?:([\w.-]+) )?\{', line)
                if member:
                    placement = block[index:block_end(block, index) + 1]
                    if member[1]:
                        named.setdefault(member[1], []).append((head['kind'], placement))
                    visit_children([line[1:] for line in placement])
        else:
            visit_children(block)

    for text in layers:
        for _, block in blocks(text):
            visit(block)
    return named


def convert_vehicle_groups(block, convert_group_block):
    """Convert placed group children in the vehicle's original coordinate space."""
    root = PLACED_ENTITY.fullmatch(block[0])
    if (not root or root['kind'] not in ('Vehicle', 'Tank', 'RHS_Tank')
            or not any(re.match(r'^ +(?:\$grp )?SCR_AIGroup ', line) for line in block[1:])):
        return block, 0, 0
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


def waypoint_gaps(named, assignments, spawned_names, scripts, crew_spawns=None):
    gaps = set()
    no_ops = set()
    for action in assignments:
        group = action['group']
        placements = named.get(group, [])
        if (action['waypoints'] == [group] and len(placements) == 1
                and re.fullmatch(WAYPOINT_CLASS, placements[0][0])
                and group not in spawned_names and group not in scripts):
            # Both runtimes require an AIGroup. A waypoint cannot assign orders to itself.
            no_ops.add(group)
            continue
        crew = (crew_spawns or {}).get(group)
        if len(placements) == 1 and placements[0][0] == 'SCR_AIGroup':
            existing_points = '\n'.join(placements[0][1])
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


def static_link_gaps(layers, named):
    fields = {'m_aStaticWaypoints': WAYPOINT_CLASS, 'm_aStaticVehicles': '(?:Vehicle|Tank)'}
    gaps = []
    for layer, text in layers.items():
        for field, targets in re.findall(r'(?m)^ +(?P<field>m_aStatic(?:Waypoints|Vehicles)) \{\n((?: +"[^\n]*\n)*)', text):
            for target in set(re.findall(r'"([\w.-]+)"', targets)):
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


def passive_asset(source, path):
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == '.edds':
        return source.read_raw(path)
    if suffix == '.imageset':
        return source.read(path)
    if suffix == '.emat':
        body = source.read(path)
        if re.match(r'MatPBR(?:Basic|Decal) \{\n', body) and not LEGACY.search(body):
            return body
    if suffix == '.et':
        body = source.read(path)
        if path == BLACKBOX[1]:
            return blackbox_prefab(body)
        if STORAGE_BOX.fullmatch(body.rstrip('\n')):
            return body
        if (sha256(body.strip().encode()).hexdigest() == REVIEWED_NATIVE_ASSETS.get(path)
                and not LEGACY.search(body)):
            return body
        lines = body.splitlines()
        material = MATERIAL_OVERRIDE.fullmatch(body.rstrip('\n'))
        if material and all(FIELD.fullmatch(line.strip()) for line in material['transform'].splitlines()):
            return body
        if (len(lines) > 8 and lines[0] == LIGHT and lines[2:7] == LIGHT_COMPONENT and lines[-1] == '}'
                and all(line.startswith(' ') and line.strip().partition(' ')[0] in LIGHT_FIELDS
                        and '{' not in line and '}' not in line for line in [lines[1], *lines[7:-1]])):
            return body
    raise ValueError('requires a resource conversion rule')


def verified_local_ai_group(source, dev, source_units, dev_units, stock, resource, path):
    if (not stock or path not in REVIEWED_NATIVE_ASSETS or source.resources.get(resource) != [path]
            or dev.resources.get(resource) not in (None, [path])
            or resource in source_units.resources or resource in dev_units.resources):
        return False
    body = source.read(path)
    if (not body.startswith('SCR_AIGroup : ') or LEGACY.search(body)
            or sha256(body.strip().encode()).hexdigest() != REVIEWED_NATIVE_ASSETS[path]
            or (path in dev.files and body != dev.read(path))):
        return False
    refs = set(REF.findall(body))
    refs.add(('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et'))
    return all(stock.contains(guid, asset)
               and all(guid not in snapshot.resources for snapshot in (source, dev, source_units, dev_units))
               for guid, asset in refs)


def reviewed_group_aliases(source, dev, source_units, dev_units):
    aliases = {}
    for path, digest in REVIEWED_NATIVE_ASSETS.items():
        if path not in source.files or path + '.meta' not in source.files:
            continue
        body = source.read(path)
        if not re.fullmatch(r'SCR_AIGroup : "\{[\dA-Fa-f]{16}\}[^"\n]+" \{\n ID "[\dA-Fa-f]{16}"\n\}\s*', body):
            continue
        if sha256(body.strip().encode()).hexdigest() != digest:
            continue
        identity = META_NAME.search(source.read(path + '.meta'))
        parent, parent_path = REF.search(body).groups()
        if (not identity or source.resources.get(identity[2].upper()) != [path]
                or any(identity[2].upper() in item.resources for item in (source_units, dev_units))
                or dev.resources.get(identity[2].upper()) not in (None, [path])
                or (path in dev.files and dev.read(path) != body)
                or source_units.resources.get(parent) != [parent_path]
                or dev_units.resources.get(parent) != [parent_path]
                or not reviewed_native_ancestry(inheritance(source_units, parent),
                                                inheritance(dev_units, parent), dev_units)):
            continue
        aliases[identity[2].upper()] = (path, parent)
    return aliases


def damage_state_flag_placement(block, *, allow_multiple_states=False):
    lines, flags = damage_state_flag_placements(block, allow_multiple_states=allow_multiple_states)
    if len(flags) != 1:
        raise ValueError('expected one authored damage producer')
    return lines, flags[0]


def damage_state_flag_placements(block, *, allow_multiple_states):
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
        if (not re.fullmatch(r'"[\w.-]+"', flag) or state not in allowed
                or data.get('m_targetZone', '""') != '""' or continuous not in ('0', '1')
                or (continuous == '1' and not allow_multiple_states)
                or data.get('m_initialHealth', '1') != '1'):
            raise ValueError('unsupported destruction state or flag')
        lines.extend(block[cursor:start])
        lines.extend([block[start].replace('TILW_Flag_EntityDamage', 'GC_DestroyedEntityFlagComponent'),
                      '   m_sFlag ' + flag])
        if state not in ('2', 'DESTROYED'):
            lines.append('   m_eTargetState ' + state)
        if continuous == '1':
            lines.append('   m_bTrackChanges 1')
        flags.append(flag[1:-1])
        cursor = stop
    return [*lines, *block[cursor:]], flags


def spawned_damage_objective(block, stock, local_resources, workshop_targets):
    parent = REF.search(block[0])
    if not parent or not block[0].startswith('GenericEntity : '):
        raise ValueError('unsupported spawned damage owner')
    ref = (parent[1].upper(), parent[2])
    registered_stock = ref in SPAWN_DAMAGE_TARGETS and stock and stock.contains(*ref)
    registered_workshop = ref in GMFX_AMMO_TARGETS and ref in workshop_targets
    if ref[0] in local_resources or not (registered_stock or registered_workshop):
        raise ValueError('unverified spawned damage prefab')
    lines, flag = damage_state_flag_placement(block)
    return '\n'.join(lines) + '\n', flag


def spawned_objectives(source, dev, world, stock, local_resources, workshop_targets=()):
    """Identify mission-owned interaction prefabs referenced by a standard spawn."""
    reviewed = {}
    placements = Counter()
    for layer in source.layers[world.lower()]:
        lines = source.read(layer).splitlines()
        for index, line in enumerate(lines):
            if not SPAWN.fullmatch(line):
                continue
            try:
                values = properties(lines[index + 1:block_end(lines, index)], SPAWN_FIELDS)
                prefab = REF.fullmatch(values.get('m_prefab', ''))
                if not prefab:
                    continue
                resource, path = prefab[1].upper(), prefab[2]
                placements[resource] += 1
                if (resource in reviewed or source.resources.get(resource) != [path]
                        or dev.resources.get(resource) not in (None, [path]) or not path.lower().endswith('.et')):
                    continue
                text = source.read(path)
                if 'TILW_BaseInteraction' not in text and 'TILW_Flag_EntityDamage' not in text:
                    continue
                (_, block), = blocks(text)
                if 'TILW_Flag_EntityDamage' in text:
                    native, flag = spawned_damage_objective(block, stock, local_resources, workshop_targets)
                    kind = {'destroyed': True}
                else:
                    native, objectives = convert_interaction_block(block, path, stock, local_resources)
                    if len(objectives) != 1:
                        continue
                    flag = objectives[0]['flag']
                    kind = {'singleUse': objectives[0]['singleUse']}
                if LEGACY.search(native) or (path in dev.files and native.strip() != dev.read(path).strip()):
                    continue
            except ValueError:
                continue
            reviewed[resource] = {'path': path, 'body': native, 'flag': flag, **kind}
    damaged = {resource: asset for resource, asset in reviewed.items() if asset.get('destroyed')}
    if damaged:
        corpus = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                           if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
        scripts = script_text(source)
        for resource, asset in damaged.items():
            flag = asset['flag']
            uses = placements[resource]
            if (corpus.count(f'"{flag}"') != literal_flag_reads(corpus, flag) + 1 or flag in scripts
                    or any(api in scripts for api in SCRIPT_FLAG_APIS)
                    or corpus.upper().count('{' + resource + '}') != uses):
                del reviewed[resource]
            else:
                asset['spawnCount'] = uses
    return reviewed


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
    reviewed = {}
    for layer in source.layers[world.lower()]:
        for line, group in blocks(originals[layer]):
            if (not group[0].startswith('SCR_AIGroup ') or ' m_bSetPlayable 1' not in group
                    or ' m_aUnitPrefabSlots {' not in group):
                continue
            faction = [match[1] for field in group if (match := re.fullmatch(r' m_faction "([\w.-]+)"', field))]
            if len(faction) != 1 or faction[0] in replacement_factions:
                continue
            start = group.index(' m_aUnitPrefabSlots {')
            slots = group[start + 1:block_end(group, start)]
            for slot in slots:
                ref = REF.fullmatch(slot.strip())
                if not ref:
                    continue
                resource, path = ref[1].upper(), ref[2]
                if (source.resources.get(resource) != [path] or dev.resources.get(resource) not in (None, [path])
                        or resource in source_units.resources or resource in dev_units.resources
                        or not path.startswith(world.rsplit('/', 1)[0] + '/')
                        or corpus.upper().count('{' + resource + '}') != 1):
                    continue
                entity = source.read(path).splitlines()
                parent = REF.search(entity[0]) if entity else None
                if (not parent or not entity[0].startswith('SCR_ChimeraCharacter : ')
                        or parent[1] in conflicting_ids or DAMAGE_TARGETS.get(parent[1]) != parent[2]
                        or not parent[2].startswith('Prefabs/Characters/') or not stock.contains(*parent.groups())):
                    continue
                try:
                    fields, death = damage_state_flag_placement(entity)
                    root = fields.index(' components {')
                    end = block_end(fields, root)
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


def ai_objectives(source, dev, world, stock, local_resources, source_units, dev_units):
    """Convert named objective characters instantiated by the reviewed civilian AI group."""
    reviewed = {}
    paths = sorted(p for p in source.closure(world) if PurePosixPath(p).suffix.lower() in SOURCE_TEXT)
    originals = {p: source.read(p) for p in paths}
    corpus = '\n'.join(originals.values())
    condition_corpus = re.sub(r'(?m)^   m_name "[\w.-]+"$', '', corpus)
    scripts = script_text(source)
    base = ('E024A74F8A4BC644', 'Prefabs/Characters/Factions/CIV/Businessman/Character_CIV_Businessman_1.et')
    civ = ('DF688F0BC937BFC7', 'Prefabs/Groups/CIV/Group_CIV_Base.et')
    if (base[0] in local_resources or not stock.contains(*base) or civ[0] in local_resources
            or any(snapshot.resources.get(civ[0]) != [civ[1]] for snapshot in (source_units, dev_units))
            or source_units.read(civ[1]) != dev_units.read(civ[1])
            or not stock.contains('000CD338713F2B5A', 'Prefabs/AI/Groups/Group_Base.et')):
        return reviewed
    for layer in source.layers[world.lower()]:
        if 'SCR_AIGroup ' not in originals[layer]:
            continue
        for line, group in blocks(originals[layer]):
            if not re.fullmatch(r'SCR_AIGroup [\w.-]+ : "' + re.escape(f'{{{civ[0]}}}{civ[1]}') + r'" \{', group[0]):
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
                if (len(actions) != 1 or 'requiredFlag' in actions[0] or LEGACY.search(body)
                        or (path in dev.files and dev.read(path).strip() != body.strip())):
                    continue
                action, = actions
                flags = (death, '"' + action['flag'] + '"')
                if len(set(flags)) != 2 or any(
                    condition_corpus.count(flag) != len(re.findall(r'(?m)^ +(?:m_condition )?TILW_LiteralTerm [^\n]+\n'
                                                         r'(?: +m_invertTerm [01]\n)? +m_flagName '
                                                         + re.escape(flag) + '$', corpus)) + 1
                    or flag in scripts for flag in flags):
                    continue
                if name in scripts or re.search(r'(?m)^\s*\w+ ' + re.escape(name[1:-1]) + r' (?:[:{])', corpus):
                    continue
            except (KeyError, ValueError):
                continue
            reviewed[resource] = {'path': path, 'body': body, 'flag': action['flag'], 'deathFlag': death[1:-1],
                                  'entity': name[1:-1], 'singleUse': action['singleUse'],
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


def spawn_resource(source, dev, source_units, dev_units, stock, guid, path, objectives, workshop_resources=()):
    if not path.lower().endswith('.et') or path.lower().startswith('prefabs/groups/'):
        return None
    owned = source.resources.get(guid)
    if owned:
        if owned != [path] or dev.resources.get(guid) not in (None, [path]):
            return None
        if guid in objectives and objectives[guid]['path'] == path:
            return path
        try:
            body = passive_asset(source, path)
        except ValueError:
            return None
        return path if path not in dev.files or body == dev.read(path) else None
    if guid in dev.resources:
        return None
    before = source_units.resources.get(guid)
    after = dev_units.resources.get(guid)
    if before or after:
        if before != [path] or after != [path]:
            return None
        body = source_units.read(path)
        return path if body == dev_units.read(path) and not LEGACY.search(body) else None
    return path if (guid, path) in workshop_resources or stock.contains(guid, path) else None


def placed_spawn(block, location, resolve_resource, captures, flags, used_flags, resolve_crew, *,
                 resolve_crew_config=None, shadowed_names=(), missing_waypoint=None):
    root = PLACED_SPAWNER.fullmatch(block[0])
    if not root:
        raise ValueError('unverified placed spawner prefab')
    placements, rules, audit = [], [], []
    for index, (name, lines) in enumerate(polygon_capture.members(block, root['group'], root['name'])):
        children = []
        if ' {' in lines:
            child_start = lines.index(' {')
            if block_end(lines, child_start) != len(lines) - 1:
                raise ValueError('custom placement after spawner children')
            children = lines[child_start:]
            for _, child in blocks('\n'.join(line[2:] for line in children[1:-1])):
                head = PLACED_ENTITY.fullmatch(child[0])
                if (not head or head['group'] or not head['name'] or not REF.search(child[0])
                        or not re.fullmatch(WAYPOINT_CLASS, head['kind']) or LEGACY.search('\n'.join(child))):
                    raise ValueError('spawner child needs a named native waypoint')
            lines = lines[:child_start]
        config = None
        config_fields = [i for i, line in enumerate(lines) if line.startswith(' m_crewConfig ')]
        if config_fields:
            if len(config_fields) != 1:
                raise ValueError('vehicle spawn needs one m_crewConfig')
            start, = config_fields
            end = block_end(lines, start)
            config = crew_config_contents(lines[start:end + 1], resolve_crew_config)
            lines = lines[:start] + lines[end + 1:]
        data = properties(lines, SPAWNER_TRANSFORM | {
            'm_prefab', 'm_conditionFlag', 'm_spawnTimings', 'm_setEntityNames', 'm_waypointNames', 'm_pregameSpawn',
        })
        transform = [f'{key} {value}' for key, value in data.items() if key in SPAWNER_TRANSFORM]
        if ('coords' not in data or any(not FIELD.fullmatch(line) for line in transform)
                or data.get('m_pregameSpawn', '0') != '0'):
            raise ValueError('spawner needs a placed Match-only transform')
        timings = data.get('m_spawnTimings', '').split() or ['0']
        if not 1 <= len(timings) <= 128 or any(
                not re.fullmatch(NUMBER, delay) or Decimal(delay) > 86400 for delay in timings):
            raise ValueError('invalid placed spawner schedule')
        spawned_name = data.get('m_setEntityNames') or '""'
        if not re.fullmatch(SPAWN_NAME, spawned_name) or (spawned_name != '""' and len(timings) != 1):
            raise ValueError('multiple spawns have success-dependent entity naming')
        waypoints = target_names(data['m_waypointNames'], skip_blank=True) if data.get('m_waypointNames') else []
        ref = REF.fullmatch(data.get('m_prefab', ''))
        group = bool(ref and ref[2].startswith('Prefabs/Groups/'))
        absent = f'"{missing_waypoint}"' if missing_waypoint else None
        if group and absent in waypoints:
            waypoints = [point for point in waypoints if point != absent]
        else:
            absent = None
        if config is not None and (not ref or not ref[2].startswith('Prefabs/Vehicles/')):
            raise ValueError('crewed spawner needs a verified vehicle')
        if waypoints and not group and config is None:
            raise ValueError('spawner waypoints need a verified AI group')
        key = f'{location}/spawner/{index}'
        anchor = name or 'GC_Spawner_' + guid(key)
        details = spawn_details({
            'm_prefab': data.get('m_prefab', ''), 'm_locationName': f'"{anchor}"',
            'm_setEntityName': spawned_name, 'm_executionDelay': '0',
        }, lambda resource, path: resolve_resource(resource, path, group=group))
        flag = data.get('m_conditionFlag', '""')
        term = {'trigger': 'Match start'}
        if flag != '""':
            check = captures.get(flag, ())
            if any(line.strip() == 'm_bStopAfterFirstChange 1' for line in check):
                term = {'trigger': 'area capture', 'flag': flag[1:-1]}
                if flag[1:-1] not in used_flags and any('m_Messages ' in line for line in check):
                    raise ValueError('capture-gated spawner needs a shared status-message owner')
            elif flag[1:-1] in flags and re.fullmatch(r'"[\w.-]+"', flag):
                term = {'trigger': 'interaction flag', 'flag': flag[1:-1]}
            else:
                raise ValueError('spawner flag lacks a verified producer: ' + flag)
        group_waypoints = waypoints if group else []
        fields = (['        m_aWaypoints {', *('         ' + point for point in group_waypoints), '        }']
                  if group_waypoints else [])
        actions = defaultdict(list)
        crew_groups = []
        for sequence, delay in enumerate(timings):
            spawn_key = f'{key}/{sequence}'
            kind = 'GC_AIGroupSpawn' if group else 'GC_PrefabSpawn'
            if config is not None:
                fields, crew_groups = render_crew_config(config, spawn_key, resolve_crew,
                                                         shadowed_names=shadowed_names,
                                                         allow_default_crew=(ref[1].upper(), ref[2]) in VERIFIED_DEFAULT_CREW)
                kind = 'GC_CrewedVehicleSpawn'
            seconds, action = render_spawn({**details, 'delay': delay}, spawn_key, kind, fields)
            actions[seconds].extend(action)
        label = 'GC_MigratedSpawn_' + guid(key)
        rendered = render_outcome(key, label, term, actions, captures, {flag}, {})
        if term['trigger'] == 'interaction flag':
            rendered[3:3] = [f'      GC_GameModeStateCondition "{{{guid(key + "/match")}}}" {{',
                             '       m_eGameModeState GAME', '      }']
        rules.extend(rendered)
        placements.extend([f'GenericEntity {anchor} {{', *(' ' + line for line in transform), *children, '}'])
        audit.append({'spawnAnchor': anchor, 'spawnName': details['area'], 'prefab': details['prefab'],
                      'spawnDelaysSeconds': timings, 'groupWaypoints': [point[1:-1] for point in group_waypoints], **term})
        if absent:
            audit[-1]['missingGroupWaypoint'] = absent[1:-1]
        if config is not None:
            audit[-1]['crewWaypoints'] = [point for group in crew_groups for point in group['waypoints']]
            audit[-1]['crewGroups'] = crew_groups
            if waypoints:
                # The legacy spawner applies this field only to spawned AI group entities.
                audit[-1]['ignoredVehicleGroupWaypoints'] = [point[1:-1] for point in waypoints]
    if not audit:
        raise ValueError('empty placed spawner group')
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
                anchors.update(name for name, _ in polygon_capture.members(block, root['group'], root['name']) if name)
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


def world_spawners(source, world, resolve_spawn, captures, flags, used_flags, blockers, resolve_crew, resolve_crew_config):
    placements, schedules, audit = {}, [], []
    layers = {path: text for path in source.layers[world.lower()]
              if 'TILW_PrefabSpawnerEntity' in (text := source.read(path))}
    shadowed = shadowed_crew_names(source, world, layers) if any('m_entityName ' in text for text in layers.values()) else ()
    for layer, text in layers.items():
        try:
            reviewed = REVIEWED_MISSING_SPAWN_WAYPOINTS.get(layer)
            missing_waypoint = None
            if reviewed and source.files[layer] == reviewed[0] and reviewed[1] not in script_text(source):
                name = reviewed[1]
                token = re.compile(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])')
                if sum(len(token.findall(source.read(p))) for p in source.closure(world)
                       if PurePosixPath(p).suffix.lower() in SOURCE_TEXT) == 1:
                    missing_waypoint = name
            for line, block in blocks(text):
                if not re.match(r'^(?:\$grp )?TILW_PrefabSpawnerEntity\b', block[0]):
                    continue
                try:
                    native, scheduled, info = placed_spawn(
                        block, f'{layer}:{line}', resolve_spawn, captures, flags, used_flags, resolve_crew,
                        resolve_crew_config=resolve_crew_config, shadowed_names=shadowed, missing_waypoint=missing_waypoint)
                except ValueError as exc:
                    blockers.append({'rule': 'TILW_PrefabSpawnerEntity', 'file': layer, 'line': line,
                                     'detail': str(exc)})
                    placements[(layer, line)] = ''
                    continue
                placements[(layer, line)] = native
                schedules.append(scheduled)
                audit.extend({'file': layer, 'line': line, 'startupChoice': None, **entry} for entry in info)
        except ValueError as exc:
            blockers.append({'rule': 'placed spawner layer', 'file': layer, 'detail': str(exc)})
    return placements, ''.join(schedules), audit


def resource_aliases(workshop, units, stock):
    aliases = {(guid, old): (guid, new) for (guid, old), new in STOCK_RESOURCE_ALIASES.items()
               if stock and stock.contains(guid, new)}
    databases = {}
    if workshop:
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
        if updated != original:
            repairs[path] = updated
            original_hashes[path] = sha256(original.encode()).hexdigest()
    return repairs, original_hashes


def migrate_assets(source, dev, draft, asset_aliases, local_loadouts, spawned_assets, faction_assets):
    files = list(draft)
    blockers = set()
    for owner in files:
        if PurePosixPath(owner).suffix.lower() not in SOURCE_TEXT:
            continue

        def relocate(ref):
            resource, path = ref[1].upper(), ref[2]
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
            if not paths or path in draft or path in dev.files:
                return ref[0]
            try:
                if len(paths) != 1:
                    raise ValueError('ambiguous source resource')
                canonical = paths[0]
                meta = canonical + '.meta'
                identity = META_NAME.search(source.read(meta))
                moved_material = (identity and canonical.endswith('.emat')
                                  and PurePosixPath(path).name == PurePosixPath(canonical).name
                                  == PurePosixPath(identity[3]).name)
                if (not identity or identity[2].upper() != resource
                        or (path != canonical and identity[3] not in (path, canonical) and not moved_material)):
                    raise ValueError('resource path disagrees with metadata')
                if any(other != canonical for other in dev.resources.get(resource, [])):
                    raise ValueError('Dev resource identity collision')
                if canonical not in draft:
                    approved = local_loadouts.get(resource)
                    objective = spawned_assets.get(resource)
                    if objective:
                        draft[canonical] = objective['body']
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
PRESENCE_TRIGGER = re.compile(r'(?P<group>\$grp )?TILW_(?P<kind>Presence)TriggerEntity(?: (?P<name>[\w.-]+))?'
                              r' : "\{826E6F2246328952\}Prefabs/Logic/Triggers/TILW_PresenceTrigger\.et" \{')
PRESENCE_MESSAGES = (
    '"Established presence at %2."', '"Establishing presence at %2."', '"No longer establishing presence at %2."',
    '"Abandoned presence at %2."', '"Abandoning presence at %2."', '"No longer abandoning presence at %2."',
)


def attached_trigger_map(fields, dev):
    start = fields.index(' {')
    if block_end(fields, start) != len(fields) - 1:
        raise ValueError('custom placement after trigger children')
    children = '\n'.join(line[2:] for line in fields[start + 1:-1])
    parent = fields[:start]
    output, amounts = [], Counter()
    for _, block in blocks(children):
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
        else:
            raise ValueError('unsupported trigger child: ' + block[0].removeprefix('$grp ').split()[0])
        output.extend('  ' + line for line in rendered.splitlines())
    if not output:
        raise ValueError('empty trigger child list')
    return parent, output, amounts


def area_rules(source, world, dev=None):
    conditions, placements, audit = {}, {}, {}
    references = None
    scripts = None
    for layer in source.layers[world.lower()]:
        text = source.read(layer)
        if not any(f'TILW_{kind}TriggerEntity' in text for kind in ('Extraction', 'Faction', 'Presence')):
            continue
        for line, block in blocks(text):
            match = TRIGGER.fullmatch(block[0]) or PRESENCE_TRIGGER.fullmatch(block[0])
            shape = polygon_capture.SHAPE.fullmatch(block[0])
            if AO_SHAPE.fullmatch(block[0]) or STANDALONE_SHAPE.fullmatch(block[0]):
                if any(TRIGGER.fullmatch(field.strip()) or PRESENCE_TRIGGER.fullmatch(field.strip())
                       for field in block[1:-1]):
                    try:
                        checks, converted, records = nested_map_areas(source, world, block, conditions)
                    except (ArithmeticError, KeyError, ValueError) as exc:
                        audit[f'{layer}:{line}'] = {'gap': str(exc)}
                    else:
                        conditions.update(checks)
                        placements[(layer, line)] = checks, converted
                        audit[f'{layer}:{line}'] = records
                continue
            if not match and not shape:
                continue
            if shape and not any(TRIGGER.fullmatch(field.strip()) for field in block):
                continue
            root = shape or match
            result, areas, capture_details = {}, [], {}
            flags = set()
            try:
                for name, fields in polygon_capture.members(block, root['group'], root['name']):
                    trigger = match
                    attached = []
                    if shape:
                        if not any(TRIGGER.fullmatch(field.strip()) for field in fields):
                            parent, visual, amounts = attached_trigger_map(fields, dev)
                            if not amounts['attachedMapShapes'] or any(LEGACY.search(field) for field in parent):
                                raise ValueError('capture group sibling needs a native polygon and map shape')
                            head = 'PolylineShapeEntity' + (f' {name}' if name else '') + ' {'
                            areas.append('\n'.join([head, *parent, ' {', *visual, ' }', '}', '']))
                            continue
                        placement, (trigger, fields), vertices = polygon_capture.read(fields, TRIGGER)
                    elif ' {' in fields:
                        fields, attached, amounts = attached_trigger_map(fields, dev)
                    flag_field = next(value.strip() for value in fields if value.strip().startswith('m_flagName '))
                    flag = flag_field.removeprefix('m_flagName ')
                    if not LEGACY_FLAG.fullmatch(flag) or len(flag.encode()) > 130:
                        raise ValueError('invalid area flag ' + flag)
                    if flag in conditions or flag in flags:
                        raise ValueError('duplicate area flag ' + flag)
                    flags.add(flag)
                    token = flag[1:-1]
                    if len(token.encode()) > 100 or not re.fullmatch(r'[\w.-]+', token):
                        token = guid(world + '/area/' + flag)
                    area_name = 'GC_MigrationArea_' + token
                    if shape or trigger['kind'] in ('Faction', 'Presence'):
                        area_name = name or area_name
                    if trigger['kind'] == 'Presence':
                        if scripts is None:
                            scripts = script_text(source)
                        if (name and name in scripts) or flag[1:-1] in scripts:
                            raise ValueError('presence trigger may be controlled by mission scripts')
                        rule, area, details = presence_rule(world, area_name, flag, fields)
                        capture_details[flag[1:-1]] = {'area': area_name, **details}
                    elif trigger['kind'] == 'Extraction' and trigger['guid'] == '192ABD6B9232E064':
                        if scripts is None:
                            scripts = script_text(source)
                        if (name and f'"{name}"' in scripts) or flag in scripts:
                            raise ValueError('extraction trigger may be controlled by mission scripts')
                        rule, area, details = extraction_rule(world, area_name, flag, fields, polygon=bool(shape))
                        capture_details[flag[1:-1]] = {'area': area_name, **details}
                    elif trigger['kind'] == 'Faction' and trigger['guid'] == '8C576D46F6579229':
                        built, area, details = capture_instance(world, area_name, fields, polygon=bool(shape))
                        capture_details[flag[1:-1]] = {'area': area_name, **details}
                        rule = built[flag]
                    else:
                        raise ValueError('inconsistent trigger identity')
                    if (trigger['kind'] == 'Faction' and not name and not shape and not attached
                            and not any('m_Messages ' in item for item in rule)):
                        if references is None:
                            references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                                                   if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
                            scripts = script_text(source)
                        if references.count(flag) == 1 and flag[1:-1] not in scripts:
                            capture_details[flag[1:-1]]['unused'] = True
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
                    elif attached:
                        area = '\n'.join([*area.splitlines()[:-1], ' {', *attached, ' }', '}', ''])
                        capture_details.setdefault(flag[1:-1], {'area': area_name}).update(amounts)
                    result[flag] = rule
                    areas.append(area)
            except StopIteration:
                audit[f'{layer}:{line}'] = {'gap': 'area trigger has no explicit flag'}
                continue
            except (ArithmeticError, KeyError, ValueError) as exc:
                audit[f'{layer}:{line}'] = {'gap': str(exc)}
                continue
            conditions.update(result)
            placements[(layer, line)] = (result, ''.join(areas))
            if capture_details:
                audit[f'{layer}:{line}'] = capture_details
    return conditions, placements, audit


def nested_map_areas(source, world, block, known_conditions):
    checks, records = {}, {}
    scripts = script_text(source)

    def render_trigger(area_name, child):
        root = TRIGGER.fullmatch(child[0]) or PRESENCE_TRIGGER.fullmatch(child[0])
        if not root or (root['kind'] != 'Presence'
                        and (root['kind'], root['guid']) != ('Extraction', '192ABD6B9232E064')):
            raise ValueError('unsupported child on named map extraction polygon')
        anchors = []
        for name, fields in polygon_capture.members(child, root['group'], root['name']):
            flag = next((value.strip().removeprefix('m_flagName ') for value in fields
                         if value.strip().startswith('m_flagName ')), '')
            if (not LEGACY_FLAG.fullmatch(flag) or len(flag.encode()) > 130
                    or flag in known_conditions or flag in checks):
                raise ValueError('invalid or duplicate nested extraction flag ' + flag)
            if (name and (name == area_name or f'"{name}"' in scripts)) or flag in scripts:
                raise ValueError('nested extraction trigger may be controlled by mission scripts')
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
        raise ValueError('named map polygon has no converted extraction triggers')
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


def mode_children(lines, disable_photo=False):
    output = [' {']
    seen = set()
    index = 1
    while index < len(lines) - 1:
        match = re.fullmatch(r'  (\w+)(?: "[\da-fA-F]{16}")? \{', lines[index])
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
            output.extend(['  SCR_FactionManager {', '   ID "6ADC9BB66394BE46"', *coords])
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
            if kind == 'SCR_EditorSettingsEntity' and disable_photo:
                output.append(f'   m_EditorManagerPrefab "{NO_PHOTO_EDITOR}"')
            output.append('  }')
        else:
            raise ValueError('custom legacy mode child: ' + kind)
        index = end + 1
    if not seen:
        raise ValueError('empty legacy mode children')
    if disable_photo and 'SCR_EditorSettingsEntity' not in seen:
        raise ValueError('disabled ArmaVision needs a verified editor settings placement')
    return '\n'.join([*output, ' }', ''])


def render_mode(world, root, preset, additional_rules, zones, ai_faction=None, objective_flags=(), damage_flags=None,
                *, dynamic_respawn_factions=()):
    if not MODE.fullmatch(root[0]):
        raise ValueError('custom legacy game mode')
    weather, environment, freeze = '', '', 0
    child_placements = []
    disable_photo = None
    transform = {}
    immediate_release = False
    construction = {}
    jip = {}
    mode_components_seen = False
    reconnect = {}
    respawn_table_seen = False
    respawn_fields = ''
    safe_start_seen = False
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
                if WEATHER.fullmatch(header):
                    handler, environment = convert_weather(world, component_lines)
                    weather += handler
                elif header == INIT_WEATHER and all(INIT_WEATHER_FIELD.fullmatch(field)
                                                    for field in component_lines[1:-1]):
                    weather += '\n'.join(component_lines) + '\n'
                else:
                    raise ValueError('custom legacy game mode components')
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
        elif line.strip().partition(' ')[0] in CONSTRUCTION_FIELDS:
            field, _, value = line.strip().partition(' ')
            if field in construction:
                raise ValueError('duplicate construction progress field ' + field)
            construction[field] = value
        elif line == ' {' and not child_placements:
            end = block_end(root, index)
            child_placements = root[index:end + 1]
            index = end
        else:
            raise ValueError('game mode field or child requires a conversion rule: ' + line.strip())
        index += 1
    if disable_photo and not child_placements:
        raise ValueError('disabled ArmaVision needs a verified editor settings placement')
    children = mode_children(child_placements, disable_photo) if child_placements else ''
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
    settings = ''
    if construction:
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
    if additional_rules:
        settings += f'''  GC_PeakFactionPopulationComponent "{{{guid(world + '/population')}}}" {{
  }}
'''
    if ai_faction:
        settings += f'''  GC_LivingAndDeadAIComponent "{{{guid(world + '/ai population')}}}" {{
   m_sFaction "{ai_faction}"
  }}
'''
    if zones:
        key = world + '/release staging zones'
        settings += f'''  SCR_PlayersRestrictionZoneManagerComponent "{{{guid(key + '/manager')}}}" {{
  }}
'''
    if freeze:
        authored = ('m_fAuthoredCountdownSeconds' if isinstance(freeze, Decimal) else 'm_iAuthoredCountdownSeconds')
        settings += f'''  GC_LobbyFreezeComponent "{{E81E75BC287148D0}}" {{
   {authored} {freeze:f}
  }}
'''
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
    if additional_rules or objective_flags or respawn_fields or reconnect_fields:
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
        settings += f'''
  GC_LobbyManager "{{6A137A39106A4973}}" {{
{flags}{events}{respawn_fields}{reconnect_fields}  }}
'''
    return f'''GC_LobbyGameMode GC_MigratedMission : "{{{resource}}}{preset}" {{
 components {{
{weather}{environment}{settings} }}
{placement}
{jip_fields}{children}}}
'''


def world_draft(source, dev, world, row, staged, area_placements, interactions, convert_group_block, asset_aliases,
                local_loadouts, spawned_assets, convert_crew, resolve_spawn, resolve_crew, resolve_crew_config, delayed=None,
                faction_assets=None):
    draft, blockers = {}, []
    reviewed_replacements = {}
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
    rules = outcome.get('events', {})
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
    reused_layers = set()
    group_blocks = []
    area_names = []
    objective_flags, interaction_placements = interactions
    damage_flags = {record['flag']: record['entity'] for record in row.get('damageFlagCandidates', {}).values()
                    if 'flag' in record and record['flag'] in objective_flags}
    roster_assets = {resource: asset for resource, asset in spawned_assets.items() if 'deathFlag' in asset}
    damage_flags.update({asset['deathFlag']: asset['entity'] for asset in roster_assets.values()
                         if not asset.get('playable')})
    teleport_targets = {target for record in row.get('interactionCandidate', {}).values() if isinstance(record, dict)
                        for target in record.get('teleportDestinations', [])}
    event_flags = {action['setsFlag'] for event in rules.values() for action in event.get('convertedActions', [])
                   if 'setsFlag' in action}
    supported_interactions = [record for record in row.get('interactionCandidate', {}).values()
                              if isinstance(record, dict)]
    required_teleport_flags = {flag for record in supported_interactions for flag in record.get('teleportRequiredFlags', [])}
    required_interaction_flags = {flag for record in supported_interactions
                                  for flag in record.get('interactionRequiredFlags', [])}
    interaction_writers = {action['flag'] for record in supported_interactions for action in record.get('objectives', [])}
    if missing_flags := (required_teleport_flags | required_interaction_flags) - (event_flags | interaction_writers):
        blockers.append({'rule': 'mission interaction', 'file': world,
                         'detail': {'missingConvertedMatchFlagProducers': sorted(missing_flags)}})
    event_flags.update(flag for sample in outcome.get('startupSamples', []) for flag in sample['flags'])
    cleared_event_flags = {action['clearsFlag'] for event in rules.values() for action in event.get('convertedActions', [])
                           if 'clearsFlag' in action}
    required_event_flags = {term['flag'] for term in terms if term['trigger'] == 'event flag'} | cleared_event_flags
    unresolved_event_flags = required_event_flags - event_flags
    if unresolved_event_flags:
        blockers.append({'rule': 'framework events', 'file': world,
                         'detail': {'missingConvertedFlagProducers': sorted(unresolved_event_flags)}})
    spawners, spawner_audit = {}, []
    if row['serializedTypes'].get('TILW_PrefabSpawnerEntity'):
        captures, placed_areas, _ = area_rules(source, world, dev)
        area_placements = {**placed_areas, **area_placements}
        spawners, schedules, spawner_audit = world_spawners(source, world, resolve_spawn, captures,
                                                           set(objective_flags) | event_flags, used_flags, blockers,
                                                           resolve_crew, resolve_crew_config)
        rule_text += schedules
        used_flags.update(entry['flag'] for entry in spawner_audit if entry['trigger'] == 'area capture')
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
                elif FRAMEWORK.fullmatch(head) and framework_supported and (represented or outcome.get('startupSamples')):
                    root_fields = [field.strip() for field in block[1:-1]
                                   if field.startswith(' ') and not field.startswith('  ') and field.strip() != '}']
                    roots = [field.split()[0] for field in root_fields]
                    # The legacy framework uses named placements and flag data, not its own transform.
                    unsupported = set(roots) - {'m_missionEvents', 'm_casualtyFlags', 'm_suppressGameEnd'}
                    if outcome.get('derivedFlags'):
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
                            if unused_flags:
                                unsupported.clear()
                    if unsupported:
                        blockers.append({'rule': 'framework settings', 'file': layer, 'line': line, 'detail': roots})
                elif kind == 'SCR_AIGroup':
                    try:
                        members = {identity: asset for identity, asset in roster_assets.items()
                                   if asset['placement'] == f'{layer}:{line}'}
                        converted, count, slots = convert_group_block(
                            interaction_placements.get((layer, line), body),
                            {identity: asset['path'] for identity, asset in members.items() if not asset.get('playable')},
                            {identity: asset['path'] for identity, asset in members.items() if asset.get('playable')})
                        result.append(converted)
                        group_blocks.append({'file': layer, 'line': line, 'groups': count, 'slots': slots})
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                elif kind == 'PK_EntityDeleter':
                    try:
                        result.append(startup_cleanup(block))
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                elif (layer, line) in area_placements:
                    checks, areas = area_placements[(layer, line)]
                    status_checks = {flag: check for flag, check in checks.items() if flag[1:-1] not in used_flags}
                    if status_checks:
                        if flag_references is None:
                            flag_references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                                                       if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
                        for flag, check in status_checks.items():
                            # An event's display name is separate from the flag read by its condition.
                            name_fields = len(re.findall(r'^ +m_name ' + re.escape(flag) + r'\s*$', flag_references, re.M))
                            if (not any('m_Messages ' in item for item in check)
                                    or flag_references.count(flag) != 1 + name_fields
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
                    result.append(convert_layer(body, dev, draw_order=True)[0])
                elif kind == 'PS_ManuaMarkerRender':
                    result.append(convert_hover_camera(block, f'{layer}:{line}'))
                elif ('TILW_AOLimit.et' in head or 'TILW_MapShape.et' in head or PASSIVE_COVER.fullmatch(head)
                        or (BARE_SHAPE.fullmatch(head) and 'TILW_MapShapeComponent' in body)):
                    converted, audit = convert_boundaries(body, allow_public_faction_color=True, extended_shapes=True,
                                                          allow_bare=True)
                    if not converted or any('gap' in item for item in audit.values()):
                        blockers.append({'rule': 'boundary or map shape', 'file': layer, 'line': line, 'detail': audit})
                    else:
                        boundaries.setdefault(layer, []).append(converted)
                        converted_cover |= 'm_bAutoFrameMap 1' in converted
                        converted_passive_cover |= bool(PASSIVE_COVER.fullmatch(head))
                        converted_bare_map |= bool(BARE_SHAPE.fullmatch(head))
                elif PERCEPTION.fullmatch(head) and all(
                    field.strip().partition(' ')[0] in SPAWNER_TRANSFORM and FIELD.fullmatch(field.strip())
                    for field in block[1:-1]
                ):
                    continue
                elif kind == 'SCR_EditorRestrictionZoneEntity' and not LEGACY.search(body):
                    names, converted = restriction_zones(block, len(zones) + 1)
                    zones.extend(names)
                    result.append(converted)
                elif kind == 'ParticleEffectEntity' and static_particle_placement(block):
                    result.append(body)
                elif LEGACY_WIRECUTTER.fullmatch(head):
                    result.append(restore_placed_wirecutters(block, asset_aliases, source.files[layer]))
                elif kind == 'SplineShapeEntity' and static_spline_placement(block):
                    result.append(body)
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
                elif NATIVE.match(head):
                    try:
                        vehicle, groups, slots = convert_vehicle_groups(convert_crew(block), convert_group_block)
                        converted = native_placement(restore_rhib_mounts(vehicle, asset_aliases, source.files[layer]))
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

    if status_areas:
        names = {event.get('eventName', event['name']) for event in rules.values()}
        trackers = {flag[1:-1]: 'GC_MigrationAreaStatus_' + guid(world + '/status/' + flag)
                    for flag in status_areas}
        if names.intersection(trackers.values()):
            blockers.append({'rule': 'framework events', 'file': world,
                             'detail': 'generated area status tracker name collides with a mission event'})
        else:
            rule_text += '\n'.join(render_capture_trackers(world, trackers, status_areas, set())) + '\n'
    if mode is None:
        blockers.append({'rule': 'mode placement', 'file': world})
    else:
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
            rendered = render_mode(world, mode, preset or '', rule_text, zones, ai_faction,
                                   set(objective_flags) | event_flags, damage_flags,
                                   dynamic_respawn_factions=edited_factions)
            if preset:
                parts[layer][index] = rendered
        except ValueError as exc:
            blockers.append({'rule': 'mode settings', 'file': mode_index[0], 'detail': str(exc)})
    for path, blocks_in_layer in parts.items():
        if ''.join(blocks_in_layer).strip():
            draft[path] = ''.join(blocks_in_layer)
    assignments = [item for event in rules.values() for item in event.get('waypointAssignments', [])]
    marker_edits = [item for event in rules.values() for item in event.get('convertedActions', []) if 'markers' in item]
    briefing_edits = Counter(order for edit in marker_edits for order in edit.get('briefingOrders', []))
    for order, count in sorted(briefing_edits.items()):
        if count > 1:
            blockers.append({'rule': 'briefing visibility actions', 'file': world,
                             'detail': f'briefing {order} has multiple reveal actions'})
    prefab_spawns = [{**item, 'startupChoice': event['flag'] if event['trigger'] == 'startup choice' else None}
                     for event in rules.values() for item in event.get('convertedActions', []) if 'spawnAnchor' in item]
    prefab_spawns.extend(spawner_audit)
    crew_names = defaultdict(set)
    crew_name_counts = Counter()
    for spawn in prefab_spawns:
        for group in spawn.get('crewGroups', ()):
            if 'groupName' in group:
                crew_names[group['groupName']].add(group.get('shadowedByAnchor', False))
                crew_name_counts[group['groupName']] += 1
    spawn_guids = {REF.fullmatch(item['prefab'])[1].upper() for item in prefab_spawns}
    for resource, asset in sorted(spawned_assets.items()):
        if resource not in roster_assets and asset['flag'] in used_flags and resource not in spawn_guids:
            blockers.append({'rule': 'spawned objective', 'file': asset['path'],
                             'detail': 'objective flag has no converted prefab spawn'})
        if asset.get('destroyed'):
            producers = [item for item in prefab_spawns if REF.fullmatch(item['prefab'])[1].upper() == resource]
            choices = {item['startupChoice'] for item in producers}
            if len(producers) != asset['spawnCount'] or (len(producers) > 1 and (
                    None in choices or len(choices) != len(producers))):
                blockers.append({'rule': 'spawned objective', 'file': asset['path'],
                                 'detail': 'damage objective needs one spawn or mutually exclusive startup choices'})
    extraction_spawns = [item for event in rules.values() for item in event.get('extractionSpawns', [])]
    if delayed:
        flag_uses = Counter(term.get('flag') for term in terms if term['trigger'] == 'area capture')
        if ({item['area'] for item in extraction_spawns} != {item['area'] for item in delayed.spawns.values()}
                or any(flag_uses[flag[1:-1]] != 1 for flag in delayed.conditions)):
            blockers.append({'rule': 'delayed extraction', 'file': world,
                             'detail': 'extraction needs all spawns and one positive consumer per flag'})
    missing_waypoints = []
    missing_spawn_anchors = set()
    self_waypoints = []
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
    if not blockers and (assignments or marker_edits or prefab_spawns or extraction_spawns or area_names or teleport_targets
                         or damage_flags or roster_assets or crew_waypoints
                         or any('m_aStatic' in body for body in draft.values())):
        named = named_placements(draft.values())
        for resource, asset in roster_assets.items():
            reference = f'"{{{resource}}}{asset["path"]}"'
            occurrences = sum(body.count(reference) for body in draft.values())
            if occurrences != 1 or asset['entity'] in named:
                blockers.append({'rule': 'roster objective spawn', 'file': asset['path'],
                                 'detail': 'expected one member and one unique runtime name'})
            else:
                named[asset['entity']] = [('SCR_ChimeraCharacter', reference)]
        for target in sorted(damage_flags.values()):
            if len(named.get(target, [])) != 1:
                blockers.append({'rule': 'damage flag target', 'file': world,
                                 'detail': 'expected one placed entity named ' + target})
        for target in sorted(teleport_targets):
            if len(named.get(target, [])) != 1:
                blockers.append({'rule': 'teleport destination', 'file': world,
                                 'detail': 'expected one placed entity named ' + target})
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
        for name, policies in sorted(crew_names.items()):
            matches = named.get(name, ())
            if policies == {True} and len(matches) == 1 and matches[0][0] == 'GenericEntity' and name not in spawned_names:
                continue
            if matches or name in spawned_names or True in policies:
                blockers.append({'rule': 'named crew group', 'file': world,
                                 'detail': 'crew group name conflicts with another entity: ' + name})
        reviewed_missing_anchor = REVIEWED_MISSING_SPAWN_ANCHORS.get(world)
        for spawn in prefab_spawns:
            anchor, name = spawn['spawnAnchor'], spawn['spawnName']
            missing_route = spawn.get('missingGroupWaypoint')
            if missing_route and (missing_route in named or missing_route in spawned_names
                                  or missing_route in crew_names):
                blockers.append({'rule': 'AI group spawn', 'file': world,
                                 'detail': 'reviewed missing waypoint now resolves: ' + missing_route})
            if (reviewed_missing_anchor and not named.get(anchor) and not name
                    and anchor not in spawned_names and anchor not in crew_names and anchor not in script_text(source)
                    and (source.files.get(reviewed_missing_anchor[0]), spawn.get('instructionId'), anchor)
                    == reviewed_missing_anchor[1:]):
                # The published spawn instruction returns when its anchor is absent.
                missing_spawn_anchors.add(anchor)
                continue
            choices = spawned_names[name]
            mutually_exclusive = None not in choices and len(set(choices)) == len(choices)
            if (len(named.get(anchor, [])) != 1
                    or (name and ((len(choices) > 1 and not mutually_exclusive) or name in named))):
                blockers.append({'rule': 'prefab spawn', 'file': world,
                                 'detail': 'missing anchor or conflicting spawn name for ' + anchor})
            for waypoint in spawn.get('crewWaypoints', spawn.get('groupWaypoints', ())):
                targets = named.get(waypoint, [])
                if len(targets) != 1 or not re.fullmatch(WAYPOINT_CLASS, targets[0][0]):
                    rule = 'AI group spawn' if 'groupWaypoints' in spawn else 'crewed vehicle spawn'
                    blockers.append({'rule': rule, 'file': world,
                                     'detail': 'expected one placed AI waypoint named ' + waypoint})
        layer, blob, absent_groups = REVIEWED_MISSING_ORDER_GROUPS.get(world, (None, None, ()))
        absent_groups = {group for group in absent_groups
                         if source.files.get(layer) == blob and group not in named and group not in spawned_names
                         and group not in crew_name_counts and group not in script_text(source)}
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
            active_orders = [action for action in orders if action['group'] not in absent_groups]
            gaps, no_ops = waypoint_gaps(named, active_orders, spawned_names, script_text(source), earlier_crews)
            self_waypoints.extend(no_ops)
            for gap in gaps:
                blockers.append({'rule': 'waypoint assignments', 'file': world, 'detail': gap})
        for waypoint in sorted(crew_waypoints):
            targets = named.get(waypoint, [])
            if len(targets) != 1 or not re.fullmatch(WAYPOINT_CLASS, targets[0][0]):
                blockers.append({'rule': 'placed crew waypoint', 'file': world,
                                 'detail': 'expected one placed AI waypoint named ' + waypoint})
        for layer, field, target in static_link_gaps(draft, named):
            reviews = REVIEWED_MISSING_WAYPOINTS if field == 'm_aStaticWaypoints' else REVIEWED_MISSING_STATIC_VEHICLES
            reviewed_blob, reviewed_targets = reviews.get(layer, (None, ()))
            if (target in reviewed_targets and source.files.get(layer) == reviewed_blob
                    and target not in named and target not in spawned_names and f'"{target}"' not in script_text(source)):
                missing = missing_waypoints if field == 'm_aStaticWaypoints' else missing_static_vehicles
                missing.append(target)
                continue
            blockers.append({'rule': 'static group links', 'file': layer,
                             'detail': f'{field} requires a unique placed target named {target}'})
        for gap in marker_edit_gaps(named, marker_edits):
            blockers.append({'rule': 'marker visibility actions', 'file': world, 'detail': gap})
    for layer, converted in boundaries.items():
        sibling = layer.removesuffix('.layer') + '_GC_Boundaries.layer'
        if sibling not in dev.files:
            draft[sibling] = ''.join(converted)
            continue
        try:
            remaining = ['\n'.join(block) for _, block in blocks(''.join(converted))]
            for _, existing in blocks(dev.read(sibling)):
                remaining.remove('\n'.join(existing))
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
    draft[world] = body
    for header in row['headers']:
        try:
            _, _, files = launcher(source, dev, header)
            draft.update(files)
        except ValueError as exc:
            blockers.append({'rule': 'launcher', 'file': header, 'detail': str(exc)})
    if delayed:
        draft.update(delayed.assets)
    blockers.extend(migrate_assets(source, dev, draft, asset_aliases, local_loadouts, spawned_assets,
                                   faction_assets or {}))
    for path, text in draft.items():
        if path not in dev.files:
            continue
        data = dev.read_raw(path) if isinstance(text, bytes) else dev.read_raw(path).decode('utf-8-sig').replace('\r\n', '\n')
        if data.strip() != text.strip():
            if (path == preset and isinstance(data, str)
                    and sha256(data.encode()).hexdigest() == row.get('reviewedSettingsPreset', {}).get(path)):
                reviewed_replacements[path] = sha256(data.encode()).hexdigest()
            elif (path.endswith('.layer') and isinstance(data, str) and DUPLICATE_CALLSIGNS.search(data)
                    and DUPLICATE_CALLSIGNS.sub(r'\g<first>', data).strip() == text.strip()):
                reviewed_replacements[path] = sha256(data.encode()).hexdigest()
            else:
                blockers.append({'rule': 'existing Dev file differs', 'file': path})
    complete_rosters = (entry.get('unitPrefabEntries', 0) for entry in row.get('groupLayers', {}).values())
    slots = sum(complete_rosters) + sum(entry['slots'] for entry in group_blocks)
    advertised = {}
    for header in row['headers']:
        count = re.search(r'^\s*m_iPlayerCount (\d+)\s*$', source.read(header), re.M)
        if count:
            advertised[header] = int(count[1])
    remaining = []
    for target in sorted({spawn['missingGroupWaypoint'] for spawn in prefab_spawns if 'missingGroupWaypoint' in spawn}):
        remaining.append(f'The original reinforcement waypoint {target} has no authored target.')
    for target in sorted(missing_spawn_anchors):
        remaining.append(f'The original spawn at {target} has no authored anchor and does not run.')
    for target in sorted(set(missing_waypoints)):
        remaining.append(f'The original static waypoint link to {target} has no authored target.')
    for target in sorted(set(self_waypoints)):
        remaining.append(f'The original assignment refers to the waypoint {target} as its group and leaves orders unchanged.')
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
    if any('m_fAuthoredCountdownSeconds ' in text for text in draft.values() if isinstance(text, str)):
        remaining.append('Compare the authored fractional staging release and displayed countdown.')
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
    if ai_faction:
        remaining.append('AI casualty tracking uses native registration and confirmed death events. '
                         'Compare possession, faction changes and delayed recounts separately.')
    if delayed:
        remaining.append('Compare delayed extraction polling and status messages in multiplayer.')
    if event_flags:
        remaining.append('Compare timing of simultaneous flag changes and their dependent event chains.')
    if any(action.get('deactivatesEvents') for event in rules.values() for action in event.get('convertedActions', ())):
        remaining.append('Compare event deactivation before a trigger and after it queues delayed actions.')
    if any(event.get('waypointAssignments') and event.get('trigger') not in ('Match start', 'dormant')
           for event in rules.values()):
        remaining.append('Compare waypoint assignment timing and order after the event condition becomes true.')
    if crew_names:
        remaining.append('Compare named crew groups across vehicle spawns, initial routes and rollback.')
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
    if crew_waypoints:
        remaining.append('Compare placed crew waypoint order, delay and separate idle turret grouping.')
    if any(isinstance(body, str) and '{823CA4130CE90077}' in body for body in draft.values()):
        remaining.append('Compare the AH-6 gun and rocket loadout against the legacy scenario.')
    if any(isinstance(body, str) and '{C8C37964483452EA}' in body for body in draft.values()):
        remaining.append('Compare the registered Army Cobra rocket loadout against the legacy scenario.')
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
    if converted_cover:
        remaining.append('Native cover framing follows map opens. Compare framing and zoom against the legacy cover.')
    if converted_passive_cover:
        remaining.append('Compare faction-only cover fill and confirm it leaves the map view unchanged.')
    if any(isinstance(body, str) and 'm_bPreviewOnHover 1' in body for body in draft.values()):
        remaining.append('Compare hover camera framing, faction visibility and cleanup on map close.')
    if any(isinstance(text, str) and '{3B62608C6AB716B9}' in text and 'm_fSize 128000\n' in text
           for text in draft.values()):
        remaining.append('Compare the oversized cover marker at the authored scale across map zoom levels.')
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
    if mode and any(line.startswith((' m_iReconnectTime ', ' m_iReconnectTimeAfterBriefing ')) for line in mode):
        remaining.append('Compare reconnect reservations before and after entering Match.')
    if mode and any(line.strip() in ('m_bOverrideGeocoords 1', 'm_bOverrideTimeZoneInfo 1') for line in mode):
        remaining.append('Compare geographic coordinates, local time and sunlight at mission start.')
    if any(path.endswith('.layer') and isinstance(body, str) and 'GC_AOLimitEntity' in body
           and ('\n  GC_AOLimitEntity ' in body or '\n  PolylineShapeEntity ' in body)
           for path, body in draft.items()):
        remaining.append('Compare nested boundary positions, faction outlines and enforcement.')
    if any(spawn['startupChoice'] for spawn in prefab_spawns):
        remaining.append('Compare pregame selection, spawn timing and faction-specific briefing and map updates.')
    if outcome.get('startupSamples'):
        remaining.append('Compare startup flag draws and verify selected events activate only in Match.')
    if any(not spawn['startupChoice'] for spawn in prefab_spawns):
        remaining.append('Compare spawn placement and timing in Match, including concurrent actions.')
    if any(spawn.get('crewDefaults') for spawn in prefab_spawns):
        remaining.append('Compare prefab default occupants, seat filters and crew orders on event-spawned vehicles.')
    return ({} if blockers else draft), {
        'status': 'blocked by conversion rules' if blockers else 'generated for file review',
        'blockers': blockers, 'files': sorted(draft), 'mixedLayerGroups': group_blocks,
        'verifiedPlayableSlots': slots, 'advertisedSlots': advertised,
        **({'reviewedCapacity': capacity_notes} if capacity_notes else {}),
        **({'reviewedReplacements': reviewed_replacements} if reviewed_replacements else {}),
        **({'unusedRandomFlags': unused_flags} if unused_flags else {}),
        **({'placedSpawners': spawner_audit} if spawner_audit else {}),
        **({'remainingBehavior': remaining} if remaining else {}),
    }


def plan(source, dev, report, staged, area_placements, interactions, convert_group_block, asset_aliases,
         local_loadouts, spawned_assets, convert_crew, resolve_spawn, resolve_crew, resolve_crew_config, delayed_extractions,
         faction_assets=None):
    results, emitted, totals, families = {}, {}, Counter(), Counter()
    for world, row in report['worlds'].items():
        draft, result = world_draft(source, dev, world, row, staged, area_placements.get(world, {}),
                                    interactions.get(world, ((), {})), convert_group_block, asset_aliases,
                                    local_loadouts, spawned_assets.get(world, {}), convert_crew,
                                    partial(resolve_spawn, spawned_assets.get(world, {})), resolve_crew,
                                    resolve_crew_config, delayed_extractions.get(world), faction_assets)
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
