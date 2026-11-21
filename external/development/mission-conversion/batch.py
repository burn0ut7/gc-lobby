"""Apply common migration rules to legacy missions and report unsupported authoring."""
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache, partial
from hashlib import sha256
from pathlib import PurePosixPath
import re

from arc_capture import (CAPTURE_EDITOR_FIELDS, CAPTURE_TRANSFORM, LONGMOOR_CURRENT_PARENT,
                         LONGMOOR_LEGACY_PARENT, capture_instance, capture_messages, faction_keys)
from boundaries import STANDALONE_SHAPE, convert_boundaries
from briefings import QUOTED, block_end, guid, properties
from catalog import REF, SOURCE_TEXT
from event_actions import (NUMBER, SPAWN, SPAWN_FIELDS, render_crew_config, render_spawn, spawn_details,
                           split_crew_block, target_names)
from events import LEGACY_FLAG, peak_flags
from extraction import condition as extraction_condition
from groups import inheritance
from interactions import convert_block as convert_interaction_block, convert_object
from launchers import META_NAME, launcher, replace_reference
from markers import convert_layer
from outcomes import FRAMEWORK, leaves, render as render_outcome, render_capture_trackers
import polygon_capture
from respawns import authored_respawn_fields, read_respawns
import teleports
from scenery import FIELD, StockPrefabs, WAYPOINT_CLASS
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
NATIVE = re.compile(r'^(?:\$grp )?(?:Vehicle|Tank|Turret|SCR_AIWorld|GameEntity|GenericEntity|GenericTerrainEntity|GenericWorldFogEntity|StaticModelEntity|Tree|'
                    r'DecalEntity|SCR_DestructibleEntity|SCR_\w*(?:Waypoint|BuildingEntity|EnvironmentalEntity|RestrictionZoneEntity)|'
                    r'AIWaypointCycle|PolylineShapeEntity|SCR_MapEntity|ACE_TacticalLadderEntity|LightEntity|Projectile)\b')
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
NAVMESH_COMPONENT = re.compile(rf' +TILW_NavmeshRegenComponent {SERIALIZED_ID} \{{')
DAMAGE_COMPONENT = re.compile(rf'  TILW_Flag_EntityDamage {SERIALIZED_ID} \{{')
DAMAGE_TARGETS = {
    '78CCABFAE4DC21EF': 'Prefabs/Props/Military/MilitaryCrates/CrateStack_01/Dst/CrateStack_01_Dst_2_dark.et',
    # The installed truck variants inherit SCR_WheeledDamageManagerComponent.
    '1BABF6B33DA0AEB6': 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_command.et',
    '4C81D7ED8F8C0D87': 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_tanker.et',
    '36BDCC88B17B3BFA': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_command.et',
    '38186F12F27A61C6': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_command_MERDC.et',
    '26013123314183EA': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_tanker_MERDC.et',
    'C8656AECF5DF41D9': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_repair_MERDC.et',
    'EC657C9B3C73E192': 'Prefabs/Vehicles/Wheeled/M923A1/M923A1_ammo_MERDC.et',
    # Both Huey chains inherit the damage manager on UH1H_base.et.
    '70BAEEFC2D3FEE64': 'Prefabs/Vehicles/Helicopters/UH1H/UH1H.et',
    'CB4D4CF7E887B2D0': 'Prefabs/Vehicles/Helicopters/UH1H/UH1H_armed_gunship_HEDP.et',
}
SPAWN_DAMAGE_TARGETS = {
    ('ADA7E056B17AC663',
     'PrefabsEditable/Auto/Props/Military/AmmoBoxes/EquipmentBoxStack/E_EquipmentBoxStack_USSR_01_V2_covered.et'),
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
REVIEWED_CAPACITY = {
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
}
STOCK_RESOURCE_ALIASES = {
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
    ('C98A6C1C2372354E', 'Prefabs/Vegetation/Bush/b_corylus_avellana_1_collision.et'):
        'Prefabs/Vegetation/Bush/b_corylus_avellana_1.et',
    ('DCAF0585D17B917A', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_tanker_CIV_ranomized.et'):
        'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_tanker_CIV_randomized.et',
}
WORKSHOP_RESOURCE_ALIASES = {
    ('72C04A6EABC12143', 'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134.et'): (
        'WCS_AH-6M_6273146ADFE8241D', '72C04A6EABC12143',
        'Prefabs/Vehicles/Helicopters/AH6M/AH6M_M134_X2.et'),
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
UNITS_PREFAB_ALIASES = {
    ('4F064E9878DCF301', 'Prefabs/Vehicles/Wheeled/M998/M1151_armed_BTR_TAN.et'): (
        '4F064E9878DCF301', 'Prefabs/Vehicles/Wheeled/M998/M1151/BMP_Turret/M1151_armed_BMP_TAN.et',
        'f83f4593e93f45e86b3e65f59995c436877a70399bb0d6310934efbfbe06f1fb'),
    ('E69BD3AC16F5AA77', 'Prefabs/Vehicles/Wheeled/M998/M1151_armed_BMP_TAN.et'): (
        'E69BD3AC16F5AA77', 'Prefabs/Vehicles/Wheeled/M998/M1151/BTR_Turret/M1151_armed_BTR_TAN.et',
        'c2ed980a0600ae70cdf3c94909e59b5eb5164c75e78f7474f2efdea330aea0f4'),
    ('AAB3119124C9167A', 'Prefabs/Vehicles/Wheeled/BTR70/1980 Mot Rifle/BTR70_MSV80.et'): (
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
    if block[0] == '$grp ParticleEffectEntity {':
        members = []
        cursor = 1
        while cursor < len(block) - 1:
            if not re.fullmatch(r' (?:[\w.-]+ )?\{', block[cursor]):
                return False
            end = block_end(block, cursor)
            members.append(block[cursor + 1:end])
            cursor = end + 1
    elif re.fullmatch(r'ParticleEffectEntity(?: [\w.-]+)? \{', block[0]):
        members = [block[1:-1]]
    else:
        return False
    for fields in members:
        effect = [line.strip() for line in fields if not FIELD.fullmatch(line.strip())]
        if len(effect) != 1 or not re.fullmatch(r'EffectPath "\{[\dA-Fa-f]{16}\}[^"\r\n]+\.ptc"', effect[0]):
            return False
    return bool(members)


def native_placement(block):
    if not NATIVE.match(block[0]):
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
        if 'TILW_BaseInteraction' not in text and 'TILW_TeleportInteraction' not in text:
            continue
        for line, block in blocks(text):
            has_teleport = any(teleports.is_teleport(item) for item in block)
            if not has_teleport and not any('TILW_BaseInteraction' in item for item in block):
                continue
            location = f'{path}:{line}'
            try:
                if has_teleport:
                    native, destinations = teleports.convert(block, location, stock, local_resources)
                    if LEGACY.search(native):
                        raise ValueError('unconverted teleport component or side effect')
                    placements[(path, line)] = native
                    audit[location] = {'teleportDestinations': destinations}
                    continue
                native, details = convert_interaction_block(block, location, stock, local_resources)
                if not details or LEGACY.search(native):
                    raise ValueError('unconverted interaction component or side effect')
                objectives = [record['flag'] for record in details]
                entities = [record['entity'] for record in details]
                if (len(set(objectives)) != len(objectives) or set(objectives) & flags
                        or len(set(entities)) != len(entities) or set(entities) & names):
                    raise ValueError('duplicate interaction entity or flag')
                flags.update(objectives)
                names.update(entities)
                placements[(path, line)] = native
                audit[location] = {'objectives': details}
            except ValueError as exc:
                audit[location] = {'gap': str(exc)}
    return flags, placements, audit


@lru_cache(maxsize=4)
def script_text(source):
    return '\n'.join(source.read_raw(path).decode('utf-8-sig')
                     for path in source.files if path.lower().endswith('.c'))


def damage_flag_rules(source, world, stock, local_resources, source_units):
    placements, audit, candidates = {}, {}, defaultdict(list)
    layers = source.layers[world.lower()]
    for path in layers:
        text = source.read(path)
        if 'TILW_Flag_EntityDamage' not in text:
            continue
        for line, block in blocks(text):
            components = [i for i, part in enumerate(block) if DAMAGE_COMPONENT.fullmatch(part)]
            if not components:
                continue
            location = f'{path}:{line}'
            try:
                ref = REF.search(block[0])
                if len(components) != 1 or not ref or not stock or ref[1].upper() in local_resources:
                    raise ValueError('unverified damage objective prefab or component placement')
                is_group = block[0].startswith('SCR_AIGroup ')
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
                stock_default = (zone == '""' and DAMAGE_TARGETS.get(resource) == ref[2]
                                 and stock.contains(*ref.groups()))
                verified_zone = (resource, ref[2], zone) in REVIEWED_HIT_ZONES
                if not (is_group or stock_default or verified_zone):
                    raise ValueError('unverified damage objective prefab or component placement')
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
                if (not re.fullmatch(r'"[\w.-]+"', flag) or len(flag.encode()) > 130
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
                head = re.fullmatch(r'(GenericEntity|Tank|Vehicle)(?: ([\w.-]+))?( : .+)', block[0])
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
    if not candidates:
        return flags, placements, audit
    references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                           if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    for flag, entries in candidates.items():
        count = literal_flag_reads(references, flag)
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
    return flags, placements, audit


def literal_flag_reads(text, flag):
    # Literal inversion may be serialized before or after the name.
    return len(re.findall(
        rf'(?m)^( +)(?:m_(?:condition|definition) )?TILW_LiteralTerm {SERIALIZED_ID} \{{\n'
        r'(?:\1 m_invertTerm [01]\n)?\1 m_flagName "'
        + re.escape(flag) + r'"\n(?:\1 m_invertTerm [01]\n)?\1\}$', text))


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
        if not re.fullmatch(r'(?:"[\w.-]+"\s*)+', names):
            return []
        flags.extend(re.findall(r'"([\w.-]+)"', names))
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


def waypoint_gaps(named, assignments):
    gaps = set()
    for action in assignments:
        group = action['group']
        placements = named.get(group, [])
        if len(placements) != 1 or placements[0][0] != 'SCR_AIGroup':
            gaps.add('expected one placed AI group named ' + group)
            continue
        group_fields = '\n'.join(placements[0][1])
        for name in action['waypoints']:
            waypoints = named.get(name, [])
            if len(waypoints) != 1 or not re.fullmatch(WAYPOINT_CLASS, waypoints[0][0]):
                gaps.add('expected one placed AI waypoint named ' + name)
            elif action['replaceExisting'] and f'"{name}"' in group_fields:
                gaps.add(f'replacement waypoint {name} may already belong to {group}')
    return sorted(gaps)


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


def spawned_damage_objective(block, stock, local_resources):
    parent = REF.search(block[0])
    if not parent or not block[0].startswith('GenericEntity : '):
        raise ValueError('unsupported spawned damage owner')
    ref = (parent[1].upper(), parent[2])
    if ref not in SPAWN_DAMAGE_TARGETS or ref[0] in local_resources or not stock or not stock.contains(*ref):
        raise ValueError('unverified spawned damage prefab')
    starts = [i for i, line in enumerate(block) if DAMAGE_COMPONENT.fullmatch(line)]
    if len(starts) != 1 or block.count(' components {') != 1:
        raise ValueError('expected one authored damage producer')
    start, = starts
    stop = block_end(block, start)
    root = block.index(' components {')
    if not root < start < stop < block_end(block, root):
        raise ValueError('spawned damage producer is outside root components')
    data = properties(block[start + 1:stop], {'m_flagName', 'm_targetState', 'm_targetZone',
                                               'm_continuous', 'm_initialHealth'})
    flag = data.get('m_flagName', '')
    if (not re.fullmatch(r'"[\w.-]+"', flag) or data.get('m_targetState', '2') not in ('2', 'DESTROYED')
            or data.get('m_targetZone', '""') != '""' or data.get('m_continuous', '0') != '0'
            or data.get('m_initialHealth', '1') != '1'):
        raise ValueError('unsupported spawned damage state or flag')
    lines = [*block[:start], block[start].replace('TILW_Flag_EntityDamage', 'GC_DestroyedEntityFlagComponent'),
             '   m_sFlag ' + flag, *block[stop:]]
    return '\n'.join(lines) + '\n', flag[1:-1]


def spawned_objectives(source, dev, world, stock, local_resources):
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
                    native, flag = spawned_damage_objective(block, stock, local_resources)
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
                    or any(api in scripts for api in ('AdjustMissionFlag', 'IsMissionFlag', 'GetFlagSetCopy', 'm_flagSet'))
                    or corpus.upper().count('{' + resource + '}') != uses):
                del reviewed[resource]
            else:
                asset['spawnCount'] = uses
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
                native, action = convert_object(after, name[1:-1], path, inherited_replication=True)
                body = '\n'.join(native) + '\n'
                if not action or LEGACY.search(body) or (path in dev.files and dev.read(path).strip() != body.strip()):
                    continue
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


def spawn_resource(source, dev, source_units, dev_units, stock, guid, path, objectives):
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
    return path if stock.contains(guid, path) else None


def placed_spawn(block, location, resolve_resource, captures, flags, used_flags, resolve_crew):
    root = PLACED_SPAWNER.fullmatch(block[0])
    if not root:
        raise ValueError('unverified placed spawner prefab')
    placements, rules, audit = [], [], []
    for index, (name, lines) in enumerate(polygon_capture.members(block, root['group'], root['name'])):
        config = None
        if any(line.strip().startswith('m_crewConfig ') for line in lines):
            config, lines = split_crew_block(lines, 'm_crewConfig TILW_CrewConfig ' + SERIALIZED_ID)
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
        names = target_names(data['m_setEntityNames']) if data.get('m_setEntityNames') else []
        if names and (len(names) != 1 or len(timings) != 1):
            raise ValueError('multiple spawns have success-dependent entity naming')
        waypoints = target_names(data['m_waypointNames']) if data.get('m_waypointNames') else []
        ref = REF.fullmatch(data.get('m_prefab', ''))
        group = bool(ref and ref[2].startswith('Prefabs/Groups/'))
        if config is not None and (not ref or not ref[2].startswith('Prefabs/Vehicles/')):
            raise ValueError('crewed spawner needs a verified vehicle')
        if waypoints and not group:
            raise ValueError('spawner waypoints need a verified AI group')
        key = f'{location}/spawner/{index}'
        anchor = name or 'GC_Spawner_' + guid(key)
        details = spawn_details({
            'm_prefab': data.get('m_prefab', ''), 'm_locationName': f'"{anchor}"',
            'm_setEntityName': names[0] if len(names) == 1 else '""', 'm_executionDelay': '0',
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
        fields = ['        m_aWaypoints {', *('         ' + point for point in waypoints), '        }'] if waypoints else []
        actions = defaultdict(list)
        crew_groups = []
        for sequence, delay in enumerate(timings):
            spawn_key = f'{key}/{sequence}'
            kind = 'GC_AIGroupSpawn' if group else 'GC_PrefabSpawn'
            if config is not None:
                fields, crew_groups = render_crew_config(config, spawn_key, resolve_crew)
                kind = 'GC_CrewedVehicleSpawn'
            seconds, action = render_spawn({**details, 'delay': delay}, spawn_key, kind, fields)
            actions[seconds].extend(action)
        label = 'GC_MigratedSpawn_' + guid(key)
        rendered = render_outcome(key, label, term, actions, captures, {flag}, {})
        if term['trigger'] == 'interaction flag':
            rendered[3:3] = [f'      GC_GameModeStateCondition "{{{guid(key + "/match")}}}" {{',
                             '       m_eGameModeState GAME', '      }']
        rules.extend(rendered)
        placements.extend([f'GenericEntity {anchor} {{', *(' ' + line for line in transform), '}'])
        audit.append({'spawnAnchor': anchor, 'spawnName': details['area'], 'prefab': details['prefab'],
                      'spawnDelaysSeconds': timings, 'groupWaypoints': [point[1:-1] for point in waypoints], **term})
        if config is not None:
            audit[-1]['crewWaypoints'] = [point for group in crew_groups for point in group['waypoints']]
    if not audit:
        raise ValueError('empty placed spawner group')
    return '\n'.join(placements) + '\n', '\n'.join(rules) + '\n', audit


def world_spawners(source, world, resolve_spawn, captures, flags, used_flags, blockers, resolve_crew):
    placements, schedules, audit = {}, [], []
    for layer in source.layers[world.lower()]:
        text = source.read(layer)
        if 'TILW_PrefabSpawnerEntity' not in text:
            continue
        try:
            for line, block in blocks(text):
                if not re.match(r'^(?:\$grp )?TILW_PrefabSpawnerEntity\b', block[0]):
                    continue
                try:
                    native, scheduled, info = placed_spawn(
                        block, f'{layer}:{line}', resolve_spawn, captures, flags, used_flags, resolve_crew)
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


def migrate_assets(source, dev, draft, asset_aliases, local_loadouts, spawned_assets):
    files = list(draft)
    blockers = set()
    for owner in files:
        if PurePosixPath(owner).suffix.lower() not in SOURCE_TEXT:
            continue

        def relocate(ref):
            resource, path = ref[1].upper(), ref[2]
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
            if not match and not shape:
                continue
            root = shape or match
            result, areas, capture_details = {}, [], {}
            flags = set()
            try:
                for name, fields in polygon_capture.members(block, root['group'], root['name']):
                    trigger = match
                    attached = []
                    if shape:
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
                        area = '\n'.join([f'PolylineShapeEntity {area_name} {{', *placement, '}', ''])
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


def presence_rule(world, area_name, flag, fields):
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
    if (len(set(names)) != len(names) or 'coords' not in transform
            or any(not FIELD.fullmatch(f'{key} {value}') for key, value in transform.items())
            or any(value not in ('0', '1') for value in (destroyed, skip, stop, status))
            or comparison not in ('0', '1', 'EQUAL_OR_LESS', 'EQUAL_OR_MORE')
            or data.get('m_eventsOnlyDuringGame', '1') != '1'
            or not all(re.fullmatch(NUMBER, n) for n in (radius, hold, ratio))
            or not (Decimal('0.1') <= Decimal(radius) <= 100000 and 0 <= Decimal(hold) <= 86400
                    and 0 <= Decimal(ratio) <= 1)
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
    area = f'''GC_RadiusAreaEntity {area_name} : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
{position} m_fRadius {radius}
}}
'''
    return rule.splitlines(), area, {
        'targets': [name[1:-1] for name in names], 'radiusMeters': float(radius), 'ratio': float(ratio),
        'comparison': 'at most' if at_most else 'at least', 'countsDestroyed': destroyed == '1',
        'quotient': 'whole number',
        'seconds': float(hold), 'skipDiscovery': skip == '1', 'stopsAfterFirstChange': stop == '1',
    }


def extraction_rule(world, area_name, flag, fields, *, polygon=False):
    data = properties(fields, {'m_queryRadius', 'm_captureTime', 'm_flagName', 'm_stopAfterFirstChange',
                               'm_factionKey', 'm_ratioThreshold', 'm_playersOnly', 'm_comparisonMode',
                               'm_skipFirstQuery', 'm_eventsOnlyDuringGame', 'm_locationName',
                               'm_sendStatusMessages'} | CAPTURE_TRANSFORM | CAPTURE_EDITOR_FIELDS)
    players = data.get('m_playersOnly', '1')
    stops = data.get('m_stopAfterFirstChange', '0')
    discovery = data.get('m_skipFirstQuery', '0')
    mode = data.get('m_comparisonMode', 'EQUAL_OR_MORE')
    if (any(value not in ('0', '1') for value in (players, stops, discovery))
            or mode not in ('0', '1', 'EQUAL_OR_LESS', 'EQUAL_OR_MORE')
            or not re.fullmatch(QUOTED, data.get('m_locationName', '""'))
            or data.get('m_sendStatusMessages', '0') != '0'
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
    check = extraction_condition(world, flag[1:-1], area_name, faction, ratio, hold,
                                 skip_discovery=discovery == '1', latch=stops == '1', players_only=players == '1',
                                 at_most=at_most, whole_number_quotient=True)
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
            if len(rest) < 3 or rest[0] != '   Factions {' or block_end(rest, 0) != len(rest) - 1:
                raise ValueError('custom legacy faction overrides')
            if LEGACY.search('\n'.join(rest)):
                raise ValueError('custom legacy faction overrides')
            output.extend(['  SCR_FactionManager {', '   ID "6ADC9BB66394BE46"', *coords,
                           '   Factions + {', *rest[1:], '  }'])
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
            freeze, fraction = divmod(int(line.split()[-1]), 1000)
            if fraction and freeze:
                raise ValueError('nonintegral staging seconds')
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
        settings += f'''  GC_LobbyFreezeComponent "{{E81E75BC287148D0}}" {{
   m_iAuthoredCountdownSeconds {freeze}
  }}
'''
    if zones:
        names = '\n'.join(f'        "{name}"' for name in zones)
        timer = f'''     m_aConditions {{
      GC_TimerCondition "{{{guid(key + '/timer')}}}" {{
       m_fSeconds {freeze}
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
                local_loadouts, spawned_assets, convert_crew, resolve_spawn, resolve_crew, delayed=None):
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
    reused_layers = set()
    group_blocks = []
    area_names = []
    objective_flags, interaction_placements = interactions
    damage_flags = {record['flag']: record['entity'] for record in row.get('damageFlagCandidates', {}).values()
                    if 'flag' in record and record['flag'] in objective_flags}
    ai_assets = {resource: asset for resource, asset in spawned_assets.items() if 'deathFlag' in asset}
    damage_flags.update({asset['deathFlag']: asset['entity'] for asset in ai_assets.values()})
    teleport_targets = {target for record in row.get('interactionCandidate', {}).values() if isinstance(record, dict)
                        for target in record.get('teleportDestinations', [])}
    event_flags = {action['setsFlag'] for event in rules.values() for action in event.get('convertedActions', [])
                   if 'setsFlag' in action}
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
                                                           resolve_crew)
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
                elif FRAMEWORK.fullmatch(head) and framework_supported and represented:
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
                        if any(term['trigger'] == 'startup choice' for term in terms):
                            unsupported.clear()
                        else:
                            unused_flags = unused_flag_sampling(source, world, layer, line, block)
                            if unused_flags:
                                unsupported.clear()
                    if unsupported:
                        blockers.append({'rule': 'framework settings', 'file': layer, 'line': line, 'detail': roots})
                elif kind == 'SCR_AIGroup':
                    try:
                        ai_members = {identity: asset['path'] for identity, asset in ai_assets.items()
                                      if asset['placement'] == f'{layer}:{line}'}
                        converted, count, slots = convert_group_block(
                            interaction_placements.get((layer, line), body), ai_members)
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
                    result.append(areas)
                elif (gap := row.get('captureCandidate', {}).get(f'{layer}:{line}', {}).get('gap')):
                    blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': gap})
                elif (layer, line) in interaction_placements:
                    result.append(interaction_placements[(layer, line)])
                elif (layer, line) in spawners:
                    result.append(spawners[(layer, line)])
                elif kind == 'PS_ManualMarker':
                    result.append(convert_layer(body, dev, draw_order=True)[0])
                elif 'TILW_AOLimit.et' in head or 'TILW_MapShape.et' in head:
                    converted, audit = convert_boundaries(body, allow_public_faction_color=True, extended_shapes=True)
                    if not converted or any('gap' in item for item in audit.values()):
                        blockers.append({'rule': 'boundary or map shape', 'file': layer, 'line': line, 'detail': audit})
                    else:
                        boundaries.setdefault(layer, []).append(converted)
                        converted_cover |= 'm_bAutoFrameMap 1' in converted
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
                elif NATIVE.match(head):
                    try:
                        converted = native_placement(convert_crew(block))
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                    else:
                        if converted is None:
                            blockers.append({'rule': kind, 'file': layer, 'line': line})
                        else:
                            result.append(converted)
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
    spawn_guids = {REF.fullmatch(item['prefab'])[1].upper() for item in prefab_spawns}
    for resource, asset in sorted(spawned_assets.items()):
        if resource not in ai_assets and asset['flag'] in used_flags and resource not in spawn_guids:
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
    if not blockers and (assignments or marker_edits or prefab_spawns or extraction_spawns or area_names or teleport_targets
                         or damage_flags
                         or any('m_aStatic' in body for body in draft.values())):
        named = named_placements(draft.values())
        for resource, asset in ai_assets.items():
            reference = f'"{{{resource}}}{asset["path"]}"'
            occurrences = sum(body.count(reference) for body in draft.values())
            if occurrences != 1 or asset['entity'] in named:
                blockers.append({'rule': 'AI objective spawn', 'file': asset['path'],
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
        for spawn in prefab_spawns:
            anchor, name = spawn['spawnAnchor'], spawn['spawnName']
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
        for gap in waypoint_gaps(named, assignments):
            blockers.append({'rule': 'waypoint assignments', 'file': world, 'detail': gap})
        for layer, field, target in static_link_gaps(draft, named):
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
    blockers.extend(migrate_assets(source, dev, draft, asset_aliases, local_loadouts, spawned_assets))
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
    if damage_flags:
        remaining.append('Compare damage notification timing in Match and confirm no objective damage during staging.')
    if any(asset.get('destroyed') for asset in spawned_assets.values()):
        remaining.append('Compare destruction of a spawned objective, including pre-Match damage and the Match outcome.')
    if any('initialHealth' in record for record in row.get('damageFlagCandidates', {}).values()):
        remaining.append('Compare initial hit zone health and subsequent repairs.')
    if teleport_targets:
        remaining.append('Compare authored teleport choices, reach, faction visibility and destinations in Match.')
    if any(path.endswith('.layer') and 'GC_VehicleCrewComponent' in body
           for path, body in draft.items() if isinstance(body, str)):
        remaining.append('Compare placed crew arrival at Match start, compartment order and gunner dismount policy.')
    if any(item['trigger'] == 'peak player loss' and item.get('inverted')
           for rule in rules.values() if 'trigger' in rule for item in leaves(rule)):
        remaining.append('Compare victory notices below and at the authored casualty threshold after the first sample.')
    if any(rule.get('requiresMatch') for rule in rules.values()):
        remaining.append('Compare compound outcome activation around the authored threshold during Match.')
    if outcome.get('derivedFlags'):
        remaining.append('Compare derived outcomes as source flags change, including inverted consumers in Match.')
    if ticket_edits:
        remaining.append('Compare event-driven ticket changes and any spectator respawn requests.')
    if any(path.endswith('.layer') and isinstance(body, str)
           and re.search(r'^ {2,}(?:\$grp )?SCR_AIGroup\b', body, re.M)
           for path, body in draft.items()):
        remaining.append('Compare nested group placement, child roster registration and slot order.')
    if converted_cover:
        remaining.append('Native cover framing follows map opens. Compare framing and zoom against the legacy cover.')
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
    if any(not spawn['startupChoice'] for spawn in prefab_spawns):
        remaining.append('Compare spawn placement and timing in Match, including concurrent actions.')
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
         local_loadouts, spawned_assets, convert_crew, resolve_spawn, resolve_crew, delayed_extractions):
    results, emitted, totals, families = {}, {}, Counter(), Counter()
    for world, row in report['worlds'].items():
        draft, result = world_draft(source, dev, world, row, staged, area_placements.get(world, {}),
                                    interactions.get(world, ((), {})), convert_group_block, asset_aliases,
                                    local_loadouts, spawned_assets.get(world, {}), convert_crew,
                                    partial(resolve_spawn, spawned_assets.get(world, {})), resolve_crew,
                                    delayed_extractions.get(world))
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
