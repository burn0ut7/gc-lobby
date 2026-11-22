"""Convert authored AI crews on registered vehicles and composition turrets."""
from decimal import Decimal
from hashlib import sha256
from pathlib import PurePosixPath
import re

from briefings import block_end, guid, properties, target_names
from catalog import REF
from groups import inheritance, legacy_member, unchanged_disabled_playability
from local_loadouts import unchanged_units_parent
from polygon_capture import members
from scenery import StockPrefabs

ZAGORIA_BMP2 = ('1EF865DC4E3F7E0B', 'IFV/BMP2/BMP2_Olive_test.et')
CORE_BTR70 = ('1C5FE7B7FF49BB8D', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_Base.et')
CORE_BTR70_DIGEST = 'ae982b43b504904f7b97947e7fc45c83f584e46ce5f7fa8d7c08c05e386eee74'
SEPARATIST_RIFLEMAN_RENAME = (
    '917322E3B14A77B7',
    'Prefabs/Characters/Factions/OPFOR/GC_SEPARATIST/Character_GC_SEPARATIST_Rifleman.et',
    'Prefabs/Characters/Factions/OPFOR/GC_SEPARATIST/2020/Character_SEP_Rifleman.et',
)
VEHICLE = re.compile(
    r'(?P<group>\$grp )?(?:Vehicle|Tank|Turret)(?: (?P<name>[\w.-]+))? : '
    r'"\{(?P<guid>[\da-fA-F]{16})\}(?P<path>Prefabs/(?:Vehicles|Weapons/Tripods)/[^"]+\.et|'
    + re.escape(ZAGORIA_BMP2[1]) + r')" \{')
COMPOSITION = re.compile(r'(?P<group>\$grp )?(?P<kind>GenericEntity|StaticModelEntity)(?: (?P<name>[\w.-]+))? : "[^"]+" \{')
PLACED_CHILD = re.compile(r'(GenericEntity|GameEntity|Turret)(?: [\w.-]+)?'
                          r'(?: : ' + REF.pattern + r')? \{')
COMPONENT = re.compile(r'  TILW_VehicleCrewComponent (?P<id>"\{[\da-fA-F]{16}\}") \{')
SERIALIZED_ID = r'"\{[\da-fA-F]{16}\}"'
CREW_CONFIG_FIELD = 'm_crewConfig TILW_CrewConfig ' + SERIALIZED_ID
CREW_CONFIG_REFERENCE = re.compile(CREW_CONFIG_FIELD + r' : ' + REF.pattern + r' \{')
SEAT_TYPES = ('Pilot', 'Turret', 'Cargo')
WORKSHOP_VEHICLES = {
    '130214331292D6B5': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Weapons/Tripods/Tripod_SPG9.et'),
    ZAGORIA_BMP2[0]: ('Zagoria89BMP2_6730D59067916E3D', ZAGORIA_BMP2[1]),
    '0A17C0D659FA2473': ('TacticalFlava-Turrets_DEADC0DE00000007',
                         'Prefabs/Weapons/Tripods/6G10/Tripod_6G10_AGS17.et'),
    '19A9036206E23B23': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/AGS/GAZ_TIGR_v2_AGS17_Tan.et'),
    '0144F571F1998FC4': ('WCS_BMP-1_5E524E4FEECCA92B', 'Prefabs/Vehicles/Tracked/BMP1/BMP1_FIA.et'),
    '1A407760EEAE4156': ('ZU-23_5C9AD9EF76F6B5EA', 'Prefabs/Vehicles/Static/BaconZU23/BaconZU23_Turret_Deployed.et'),
    '9AEDF323A812F9ED': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2_AFRF.et'),
    '18378FFFC82F30BF': ('M113APC_5E5C154FEE1094BB', 'Prefabs/Vehicles/Tracked/M113/M113_APC.et'),
    '2B9DB09AC8BEA673': ('WCS_T-72_5E0AB16BEB16D6A4', 'Prefabs/Vehicles/Tracked/T72A/T72A_FIA.et'),
    '29CBE6F602148A03': ('RHS-StatusQuo_595F2BF2F44836FB',
                         'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_ammo_tan.et'),
    '31F0866273C46D76': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/K17/APC_K17_Berezok.et'),
    '3312ADBF53A16DD8': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/LAV25/LAV25_RHS.et'),
    '3868E03D9A4AF186': ('WCS_BMP-1_5E524E4FEECCA92B', 'Prefabs/Vehicles/Tracked/BMP1/BMP1P_Base.et'),
    '3F0C718D8FA6E1AE': ('WCS_Armaments_629B2BA37EFFD577',
                         'Prefabs/Vehicles/Helicopters/Mi8MT/Mi8MT_Armed_GUV8700_X2.et'),
    '447CFE8D73F95C3E': ('RHS-StatusQuo_595F2BF2F44836FB', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_AFRF.et'),
    '5CFD8CB5C3E55A4E': ('WCS_M2A2_63120AE07E6C0966', 'Prefabs/Vehicles/Tracked/M2A2/M2A2_MERDC.et'),
    '72018C87FBDC9F8C': ('WCS_M2A2_63120AE07E6C0966', 'Prefabs/Vehicles/Tracked/M2A2/M2A2_Desert.et'),
    '6B7CD408BDFB94F5': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_Green.et'),
    '7BC76F92B882889D': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM.et'),
    '7B73B9445F145E6E': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/KORD/GAZ_TIGR_v2_KORD_Tan.et'),
    '7ACFD8FA081996F6': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_1P29.et'),
    '8E7F7A0368CE8805': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/PKP/GAZ_TIGR_v2_PKM_Green_1P29.et'),
    '95A220DF8D6E2543': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/ArbaletDM/GAZ_TIGR_v2_Arbalet_KORD_Green.et'),
    '953523CFE2DB9769': ('M113APC_5E5C154FEE1094BB', 'Prefabs/Vehicles/Tracked/M113/M113_APC_MERDC.et'),
    'AC12E5669B411D57': ('TacticalFlava-TIGR_DEADC0DE00000003',
                         'Prefabs/Vehicles/Wheeled/TIGR/KORD/GAZ_TIGR_v2_KORD.et'),
    'AF1C02364D45CC5B': ('WCS_T-72_5E0AB16BEB16D6A4', 'Prefabs/Vehicles/Tracked/T72A/T72A_Desert.et'),
    'B8C827A45C1347A4': ('RHS-StatusQuo_595F2BF2F44836FB',
                         'Prefabs/Vehicles/Wheeled/M998/M1025_armed_M2HB_USAF_D.et'),
    'E9BEA9042617CAAD': ('M113APC_5E5C154FEE1094BB', 'Prefabs/Vehicles/Tracked/M113/M113A2_APC_MERDC.et'),
    'FD109F3702AAB392': ('ZU-23_5C9AD9EF76F6B5EA', 'Prefabs/Vehicles/Wheeled/Ural4320/Ural4320_ZU23.et'),
}
for provider, directory, variants in (
    ('BritishForces_5AE50EC5B8D6F4AE', 'Prefabs/Vehicles/Wheeled/LR_3Series', {
        '46C8F837F47D036E': '3Series_LWB_Recce_M2HB_Variant_3',
        '57FE9BCA625CA0E3': '3Series_LWB_Recce_M2HB',
        '9B8FC88897DB630F': '3Series_LWB_Recce_Variant_2',
        'A354B4CEF8989405': '3Series_LWB_Recce_M2HB_Variant_2',
    }),
    ('WCS_BMP-1_5E524E4FEECCA92B', 'Prefabs/Vehicles/Tracked/BMP1', {
        '68A65EFA9B616798': 'BMP1_Desert',
        '828E8711567C3242': 'BMP1P_FIA_Desert',
        'C334AD811A3BFFFC': 'BMP1P_Desert',
    }),
    ('WCS_Stryker_5B02128D896F7DE8', 'Prefabs/Vehicles/Wheeled/Stryker', {
        '094A1ECBEE1D9AD1': 'M1126/CROWS/Stryker_CROWS_Cage_MERDC',
        '44E2468321E19451': 'M1296/Stryker_Dragoon_Cage',
        '5F17F054025E8B30': 'M1126/CROWS/Stryker_CROWS_Cage',
        'A76DCC7AD6494682': 'M1128/Stryker_MGS_MERDC',
    }),
    ('WCS_T-72_5E0AB16BEB16D6A4', 'Prefabs/Vehicles/Tracked/T72B', {
        '0154275FD6AE2370': 'T72B_Desert',
    }),
    ('WCS_JLTV_5C721177A220B42F', 'Prefabs/Vehicles/Wheeled/JLTV', {
        '0F200E9F3D3AD905': 'OGPK/M2HB/JLTV_OGPK_M2HB_Woodland',
        '2E6481BC1EE9A767': 'CROWS/M2HB/JLTV_CROWS_M2HB_Woodland',
        '8A9444452D28F5B3': 'CROWS/M240/JLTV_CROWS_M240_Woodland',
        'BAF198F845DF6BBD': 'OGPK/M134/JLTV_OGPK_M134_Woodland',
        'D17CCBF7FE9AAFEC': 'CROWS_II/M230LF/JLTV_CROWS_II_M230LF_Woodland',
    }),
    ('TacticalFlava-HMMWV_DEADC0DE00000005', 'Prefabs/Vehicles/Wheeled/M998', {
        '03708F7E58BB7A53': 'M1151_armed_M2HB',
    }),
    ('TacticalFlava-TIGR_DEADC0DE00000003', 'Prefabs/Vehicles/Wheeled/TIGR', {
        '7A21F47E09038E58': 'ArbaletDM/GAZ_TIGR_v2_Arbalet_KORD',
        'CD4737303AA14C6D': 'KORD/GAZ_TIGR_v2_KORD_SPP',
        'ED23D3FD79C7DECF': 'GAZ_TIGR_v2_Base',
    }),
):
    WORKSHOP_VEHICLES.update({guid: (provider, f'{directory}/{name}.et') for guid, name in variants.items()})

PKM = ('723870DBB19D30B0', 'Prefabs/Weapons/Tripods/Tripod_6T5_PKM.et')
NSV = ('29F0CC704A582154', 'Prefabs/Weapons/Tripods/6T7/Tripod_6T7_NSV.et')
NSV_SPP = ('3EA26CFD7A202636', 'Prefabs/Weapons/Tripods/6T7/Tripod_6T7_NSV_SPP.et')
SINGLE_SEAT_TURRETS = {
    PKM, NSV, NSV_SPP,
    ('130214331292D6B5', 'Prefabs/Weapons/Tripods/Tripod_SPG9.et'),
    ('0A17C0D659FA2473', 'Prefabs/Weapons/Tripods/6G10/Tripod_6G10_AGS17.et'),
    ('73530808E4B455BA', 'Prefabs/Weapons/Tripods/Tripod_M3_M2HB.et'),
    ('887DB60486A18575', 'Prefabs/Weapons/Tripods/Tripod_M122_MG_M60.et'),
}
COMPOSITION_TURRETS = {
    ('25861AD07CEE719A', 'Prefabs/Compositions/Misc/SubCompositions/Sandbag_MG_USSR_01_PKM.et'):
        {('58FEB999CF41D2D6',): PKM},
    ('25861AD07CEE719B', 'Prefabs/Compositions/Misc/SubCompositions/Fortifications/Sandbag_MG_USSR_01_PKM.et'):
        {('5CC6B4BEF18D0296',): PKM},
    ('7A40BF128BB47EFD', 'Prefabs/Compositions/Slotted/SlotFlatSmall/MachineGunNest_S_USSR_01.et'):
        {('5CCCBEDC9EB8023A', '5CC6B4BEF18D0296'): PKM},
    ('114DE81321786CD9', 'Prefabs/Compositions/Slotted/SlotFlatSmall/MachineGunNest_S_USSR_01_PKM.et'):
        {('5CCCBEDC9EB8023A', '5CC6B4BEF18D0296'): PKM},
    ('9751583B234C4202', 'Prefabs/Compositions/Misc/SubCompositions/Sandbag_MG_USSR_02_PKM.et'):
        {('5C5AC612652E574F', '59A297CB53D15C0E'): PKM},
    ('6AE7F9D91B93CCAF', 'Prefabs/Compositions/Misc/FreeRoamBuilding/Emplacement_MG_USSR_01_NSV.et'):
        {('64F282420DD8DFE9',): NSV},
    ('EDB539C08A95005B', 'Prefabs/Compositions/Misc/FreeRoamBuilding/Emplacement_MG_USSR_01_PKM.et'):
        {('64F2824E966DA442',): PKM},
    ('B2173CED8D50CB40', 'PrefabsEditable/Auto/Compositions/Misc/FreeRoamBuilding/E_Emplacement_MG_USSR_01_PKM.et'):
        {('64F2824E966DA442',): PKM},
    ('4E288C9E06A3A2B8', 'Prefabs/Compositions/Misc/SubCompositions/Fortifications/Sandbag_MG_USSR_01_NSV_SPP.et'):
        {('605ECD8C01B355D2',): NSV_SPP},
    ('F671303E925C437A', 'PrefabsEditable/Auto/Compositions/Misc/SubCompositions/Fortifications/E_Sandbag_MG_USSR_01_NSV_SPP.et'):
        {('605ECD8C01B355D2',): NSV_SPP},
}
INLINE_TURRET_HOSTS = {
    ('7AF4B627D5C90235', 'Prefabs/Props/Military/Sandbags/Sandbag_01_round_high_burlap.et'),
    ('CFFEB23FBEA00322', 'Prefabs/Props/Military/Sandbags/Sandbag_01_short_burlap.et'),
    ('8E1DF47DD56E69E6', 'Prefabs/Compositions/Slotted/SlotFlatSmall/SandbagPosition_S_USSR_01.et'),
}
INLINE_TURRET_CHAINS = {('8E1DF47DD56E69E6', '5CD2AC4DBFADB874')}
# The Units override only alters three scenery model flags, leaving the turret inherited.
COMPOSITION_OVERRIDES = {'7A40BF128BB47EFD': '46f28ca4f00646d4223fc72a3e03c28331c99c1e1fae3a42dda45e268b67a45a'}
# The smoke-only ancestor moved from published Core to Units Dev.
BTR70_CREW_OVERLAY = '447CFE8D73F95C3E'
BTR70_CREW_LEAF = '81a8ccf59bed709f4eb87c25c260e529acd6cbb74dab97d09566adf828395c78'
BTR70_SMOKE_ANCESTOR = 'ae982b43b504904f7b97947e7fc45c83f584e46ce5f7fa8d7c08c05e386eee74'
USSR_BTR70_LEAF = '6ff98c749e6e58fb337ad80c11bdb2cabcf5beebe84afdf0fc8a1e6e917bdd94'
CDF_BTR70_LEAF = 'da0a998e4f08c0ec1940ba0ee8aee889a9a8c5cbf727df7f7cb9df90c98a1389'
CRX_HMMWV_LEAF = '99815cf48312662ac44de7615b5a3c027287de6b28c1c96bf36f5d8b9a9d4d1c'
BTR_DUGOUT = '7CF4F44DAD409258'
BTR_DUGOUT_LEAF = '6d19ae2e9bcfc0d707ed9d86d8724b8b49f4c132c33dd6cdaab50730e48e7155'
STOCK_VEHICLE_HOSTS = {
    ('39F7EE885940DC8F', 'Prefabs/Structures/Military/Fortifications/DirtCover_01/DirtCover_01_long_v1.et'),
}
VERIFIED_DEFAULT_CREW = {
    ('B47110AA1A806556', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_FIA.et'),
    ('C00B26E6C8808EF8', 'Prefabs/Vehicles/Wheeled/M998/CRX_M1025_armed_M2HB_gunshield_DESERT.et'),
    ('E9BEA9042617CAAD', 'Prefabs/Vehicles/Tracked/M113/M113A2_APC_MERDC.et'),
    ('123595894023C474', 'Prefabs/Vehicles/Wheeled/LAV25/LAV25_RHS_D.et'),
}
NVA_BTR70_LEAF = 'ac360b5e120fa0f5ba97deee2e1ee68ef6d415fd9e856710c5104efdd8ea551f'
NVA_T72A_LEAF = '30fdb038e061492f404c19340d7b0e1da08746c49abd8f57faf46fb7264d38a2'
NVA_BRDM2_LEAF = 'bbe74f54370ac1a41343d200b834335650508e87632449c8781d5bae490371e4'
UNITS_CREW_PREFABS = {
    'C00B26E6C8808EF8': (
        'Prefabs/Vehicles/Wheeled/M998/CRX_M1025_armed_M2HB_gunshield_DESERT.et',
        ('DD774A8FD0989A78', 'Prefabs/Vehicles/Wheeled/M998/M1025_armed_M2HB_MERDC.et'),
        (CRX_HMMWV_LEAF,), (CRX_HMMWV_LEAF,),
    ),
    'FCEC036D7F903A66': (
        'Prefabs/Vehicles/Wheeled/BTR70/CDF/BTR70_CDF.et',
        ('B47110AA1A806556', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70_FIA.et'),
        (CDF_BTR70_LEAF,), (CDF_BTR70_LEAF,),
    ),
    BTR_DUGOUT: (
        'Prefabs/Structures/Military/Fortifications/Trenches/VehicleDugout_BTR.et',
        ('E04C8E7133043968', 'Prefabs/Structures/Core/Fortifications_Base.et'),
        (BTR_DUGOUT_LEAF,), (BTR_DUGOUT_LEAF,),
    ),
    'AAB3119124C9167A': (
        'Prefabs/Vehicles/Wheeled/BTR70/USSR/BTR70_USSR.et',
        ('C012BB3488BEA0C2', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70.et'),
        (USSR_BTR70_LEAF,), (USSR_BTR70_LEAF,),
    ),
    '1C1038CF07EA4B00': (
        'Prefabs/Vehicles/Wheeled/BTR70/NVA/BTR70_NVA.et',
        ('C012BB3488BEA0C2', 'Prefabs/Vehicles/Wheeled/BTR70/BTR70.et'),
        (NVA_BTR70_LEAF,), (NVA_BTR70_LEAF,),
    ),
    '83FC98FFD5D48825': (
        'Prefabs/Vehicles/Tracked/T-72A/T72A_NVA.et',
        ('8A883ABED22B6E27', 'Prefabs/Vehicles/Core/Tracked_Base.et'),
        (NVA_T72A_LEAF, '44d169d2260f456352926fd245aa50f787083ee2a43a23eb76ebed0b61e0e38d'),
        (NVA_T72A_LEAF, '2ec90305fa151423829093e490029d2a994c655b6b70b47ef1291b2b1207674d'),
    ),
    'BC07885176C7FF8A': (
        'Prefabs/Vehicles/Tracked/T-72A/T72A_base.et',
        ('8A883ABED22B6E27', 'Prefabs/Vehicles/Core/Tracked_Base.et'),
        ('44d169d2260f456352926fd245aa50f787083ee2a43a23eb76ebed0b61e0e38d',),
        ('2ec90305fa151423829093e490029d2a994c655b6b70b47ef1291b2b1207674d',),
    ),
    'EC4811AC7C17DC7E': (
        'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2_NVA.et',
        ('254289B9C09904AB', 'Prefabs/Vehicles/Wheeled/BRDM2/BRDM2.et'),
        (NVA_BRDM2_LEAF,), (NVA_BRDM2_LEAF,),
    ),
}
WORKSHOP_VEHICLE_PARENTS = {
    '8A883ABED22B6E27': ('WCS_SpaceCore_5E389BB9F58B79A6', 'Prefabs/Vehicles/Core/Tracked_Base.et'),
}
FLORA_CREW_CONFIG = '47ABFE35CED7D5AA'
FLORA_CREW_GROUP = 'TILW_CrewGroup "{64F2F8212EF64955}"'
FLORA_HEAD_STATION = 'TILW_CrewStation "{64F2F8217DD33B67}"'
FLORA_MAIN_STATION = 'TILW_CrewStation "{64FAF9F5899BF37B}"'
FLORA_RIFLEMAN = ('"{7CCAB195F5A1D9CA}Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/Flora/'
                   'Character_RHS_RF_MSV_Flora_Rifleman.et"')
REVIEWED_CREW_CONFIGS = {
    FLORA_CREW_CONFIG: ('Configs/Crews/AFRF/BTR_AFRF_Crew_Flora_AI.conf',
                        'd443c4b6878ece839d55219f14daad6c2d6e604175f4841357a3f386686e590f'),
    'A48A101CB478559D': ('Configs/Crews/GC_CDF/BTR70/BTR70_CDF_Crew_AI.conf',
                         'd569b28ad795ab451116b807145d40e52110ba840edc6c7745d44bc7196ed49f'),
    '012709863EB73BED': ('Configs/Crews/GC_NVA/Crew_NVA_AI.conf',
                         '90846172c477653d708d41e39d6a2be4d94607803653f0fdd8c16ef18746c712'),
    '5657389604450AAC': ('Configs/Crews/US/Crew_US_AI.conf',
                         '0e8d90c8331a50c54f78ceafc20774f8c5169b601b1c6b01ff3074a9cd47d066'),
    'E56AA08ED9F1D4B5': ('Configs/Crews/GC_CDF/Rifleman_CDF.conf',
                         'c954cc5a6819f9f240ae42226a560d165dd60e573a828c1ae4a6f1a1f74d0610'),
}
NVA_CREW_ROLE_PATHS = {
    '72BF67D118BC5930': ('Driver', 'CrewDriver'),
    '9594C031304B509D': ('Gunner', 'CrewGunner'),
    '945EE8F65FF4E94C': ('Commander', 'CrewCO'),
}


def workshop_vehicles(workshop, resources=WORKSHOP_VEHICLES):
    result = {}
    if not workshop:
        return result
    databases = {}
    for resource, (provider, path) in resources.items():
        if provider not in databases:
            registry = workshop / provider / 'resourceDatabase.rdb'
            databases[provider] = StockPrefabs(registry) if registry.is_file() else None
        if (database := databases[provider]) and database.contains(resource, path):
            result[resource] = path
    return result


def contents(lines, field):
    if (not lines or not re.fullmatch(field + r' \{', lines[0].strip())
            or block_end(lines, 0) != len(lines) - 1):
        raise ValueError('crew authoring needs one ' + field.split()[0])
    return lines[1:-1]


def character_reference(guid, path, source_units, dev_units, stock, owned, external_ai=None, *,
                        allow_legacy_playable=False):
    if guid in owned or not path.startswith('Prefabs/Characters/'):
        raise ValueError('unverified crew character ' + guid)
    if guid in source_units.resources or guid in dev_units.resources:
        if (not (unchanged_units_parent(source_units, dev_units, guid)
                 or unchanged_disabled_playability(source_units, dev_units, guid))
                or (not allow_legacy_playable and legacy_member(source_units, guid, False))):
            raise ValueError('crew character ancestry needs review ' + guid)
        canonical, = dev_units.resources[guid]
        if (PurePosixPath(path).name != PurePosixPath(canonical).name
                and (guid, path, canonical) != SEPARATIST_RIFLEMAN_RENAME):
            titles = NVA_CREW_ROLE_PATHS.get(guid, ())
            prefix = 'Prefabs/Characters/Factions/OPFOR/GC_NVA/Character_GC_NVA_'
            if (path, canonical) != tuple(prefix + title + '.et' for title in titles):
                raise ValueError('crew character path needs review ' + guid)
        return f'"{{{guid}}}{canonical}"'
    if not stock.contains(guid, path) and (not external_ai or external_ai.get(guid) != path):
        raise ValueError('unregistered crew character ' + guid)
    return f'"{{{guid}}}{path}"'


CREW_ROLE = re.compile(REF.pattern + r'|""')


def crew_roles(authored, flags, resolve_character, allow_empty=False, allow_all_blank=False):
    if (not authored or CREW_ROLE.sub('', authored).strip() or all(flag == '0' for flag in flags.values())
            or any(flag not in ('0', '1') for flag in flags.values())):
        raise ValueError('crew migration requires explicit characters and qualifying seats')
    roles = list(CREW_ROLE.finditer(authored))
    if len(roles) > 128 or (not allow_all_blank and not any(role[1] for role in roles)):
        raise ValueError('unsupported crew size or empty roster')
    if not allow_empty and any(not role[1] for role in roles):
        raise ValueError('crew station contains empty seats')
    return [resolve_character(role[1].upper(), role[2]) if role[1] else '""' for role in roles]


def crew_station(lines, resolve_character, allow_empty=False, placed=False, allow_default_crew=False):
    station = contents(lines, 'TILW_CrewStation ' + SERIALIZED_ID)
    values = properties(station, {'m_characters', *('m_allow' + seat for seat in SEAT_TYPES)})
    flags = {seat: values.get('m_allow' + seat, '1') for seat in SEAT_TYPES}
    authored = values.get('m_characters', '')
    if not authored and allow_default_crew and '1' in flags.values() and set(flags.values()) <= {'0', '1'}:
        return flags, []
    if authored.startswith('""') and not placed:
        raise ValueError('legacy station suppresses crew when the first seat is blank')
    return flags, crew_roles(authored, flags, resolve_character, allow_empty, allow_all_blank=placed)


def crew_route_fields(data, default_delay):
    waypoints = target_names(data['m_waypointNames']) if data.get('m_waypointNames') else []
    if not waypoints:
        return []
    delay = data.get('m_waypointDelay', default_delay)
    if not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400:
        raise ValueError('unsupported placed crew waypoint delay')
    return ['   m_fCrewWaypointDelay ' + delay, '   m_aCrewWaypoints {',
            *('    ' + name for name in waypoints), '   }']


def crew_config_contents(lines, resolve_config=None):
    reference = CREW_CONFIG_REFERENCE.fullmatch(lines[0].strip())
    if not reference:
        return contents(lines, CREW_CONFIG_FIELD)
    if not resolve_config:
        raise ValueError('referenced crew config needs a verified base')
    base = resolve_config(reference[1].upper(), reference[2])
    if reference[1].upper() == FLORA_CREW_CONFIG and lines[1:-1]:
        return merge_flora_vehicle_crew(base, lines[1:-1])
    return merge_crew_routes(base, lines[1:-1]) if lines[1:-1] else base


def merge_flora_vehicle_crew(base, override):
    """Flatten the reviewed BTR crew variant authored for the TIGR placements."""
    groups = contents(override, 'm_crewGroups')
    group = contents(groups, FLORA_CREW_GROUP)
    stations, route = split_crew_block(group, 'm_crewStations')
    expected = [
        FLORA_HEAD_STATION + ' {', 'm_allowPilot 0', 'm_allowTurret 0',
        'm_characters {', '""', '""', '}', '}',
        FLORA_MAIN_STATION + ' {', 'm_allowPilot 1', 'm_allowTurret 1',
        'm_characters +{', FLORA_RIFLEMAN, '}', '}',
    ]
    if [line.strip() for line in stations] != expected:
        raise ValueError('unverified Flora crew station override')
    properties(route, {'m_waypointNames', 'm_waypointDelay'})

    start = next((i for i, row in enumerate(base) if row.strip() == FLORA_HEAD_STATION + ' {'), None)
    if start is None:
        raise ValueError('Flora crew head station not found')
    end = block_end(base, start)
    other = end + 1
    if base[other].strip() != FLORA_MAIN_STATION + ' {':
        raise ValueError('Flora crew station order differs')
    tail = block_end(base, other)
    original = base[other + 1:tail]
    names, fields = split_crew_block(original, 'm_characters')
    if properties(fields, {'m_allowPilot', 'm_allowTurret'}) != {'m_allowPilot': '0', 'm_allowTurret': '0'}:
        raise ValueError('Flora crew base seat filters differ')
    # The first station inherits cargo disabled and explicitly disables pilot and turret.
    # Its blank entries cannot claim a seat. The second station appends one role.
    merged = [base[other], '     m_characters {', *names, '      ' + FLORA_RIFLEMAN, '     }', base[tail]]
    return merge_crew_routes([*base[:start], *merged, *base[tail + 1:]],
                             [override[0], groups[0], *route, groups[-1], override[-1]])


def merge_crew_routes(base, override):
    """Add waypoint fields to the matching inherited crew groups."""
    authored = {}
    fields = {'m_waypointNames', 'm_waypointDelay'}
    groups = contents(override, 'm_crewGroups')
    cursor = 0
    while cursor < len(groups):
        stop = block_end(groups, cursor)
        header = groups[cursor].strip()
        if header in authored:
            raise ValueError('duplicate referenced crew route')
        route = contents(groups[cursor:stop + 1], 'TILW_CrewGroup ' + SERIALIZED_ID)
        properties(route, fields)
        authored[header] = route
        cursor = stop + 1
    start = next((index for index, line in enumerate(base) if line.strip() == 'm_crewGroups {'), None)
    if start is None:
        raise ValueError('referenced crew has no groups')
    end = block_end(base, start)
    result = base[:start + 1]
    cursor = start + 1
    while cursor < end:
        stop = block_end(base, cursor)
        group = base[cursor:stop]
        route = authored.pop(group[0].strip(), ())
        if route and any(line.strip().partition(' ')[0] in fields for line in group[1:]):
            raise ValueError('referenced crew already authors a route')
        result.extend([*group, *route, base[stop]])
        cursor = stop + 1
    if authored:
        raise ValueError('referenced crew route group not found')
    return [*result, *base[end:]]


def split_crew_block(lines, field):
    starts = [i for i, line in enumerate(lines) if re.fullmatch(field + r' \{', line.strip())]
    if len(starts) != 1:
        raise ValueError('vehicle spawn needs one ' + field.split()[0])
    start = starts[0]
    end = block_end(lines, start)
    return lines[start + 1:end], lines[:start] + lines[end + 1:]


def render_crew_config(config, key, resolve_character, *, shadowed_names=(), placed=False,
                       allow_default_crew=False, placed_names=(), missing_waypoints=()):
    groups, fields = split_crew_block(config, 'm_crewGroups')
    settings = properties(fields, {'m_noTurretDismount', 'm_useIdleGroups'})
    seated = settings.get('m_noTurretDismount', '0')
    idle = settings.get('m_useIdleGroups', '1')
    if seated not in ('0', '1') or idle not in ('0', '1'):
        raise ValueError('invalid crew options')
    output = ['        m_aCrewGroups {']
    crew = []
    index = 0
    count = 0
    while index < len(groups):
        end = block_end(groups, index)
        group = contents(groups[index:end + 1], 'TILW_CrewGroup ' + SERIALIZED_ID)
        stations, fields = split_crew_block(group, 'm_crewStations')
        options = properties(fields, {'m_waypointNames', 'm_waypointDelay', 'm_entityName'})
        authored_route = options.get('m_waypointNames')
        route = target_names(authored_route, skip_blank=True) if authored_route else []
        missing = [point[1:-1] for point in route if point[1:-1] in missing_waypoints]
        if missing:
            route = [point for point in route if point[1:-1] not in missing_waypoints]
        if authored_route and not route and idle == '1':
            raise ValueError('blank crew route may change idle gunner grouping')
        name = options.get('m_entityName', '""')
        if not re.fullmatch(r'"[\w.-]{0,128}"', name):
            raise ValueError('unsupported crew group name')
        if placed and name != '""' and name[1:-1] not in placed_names:
            raise ValueError('placed crew group name requires spatial ownership review')
        shadowed = name[1:-1] in shadowed_names
        delay = options.get('m_waypointDelay', '0')
        if not re.fullmatch(r"\d+(?:\.\d+)?", delay) or Decimal(delay) > 86400:
            raise ValueError('invalid crew waypoint delay')
        group_key = f'{key}/crew/{count}'
        output.append(f'         GC_SpawnCrewGroup "{{{guid(group_key)}}}" {{')
        if name != '""':
            output.append(f'          m_sGroupName {name}')
        if shadowed:
            output.append('          m_bNameShadowedByAnchor 1')
        output.append('          m_aStations {')
        start = 0
        characters = []
        while start < len(stations):
            stop = block_end(stations, start)
            flags, roles = crew_station(stations[start:stop + 1], resolve_character, allow_empty=True, placed=placed,
                                        allow_default_crew=allow_default_crew)
            station_id = guid(f'{group_key}/station/{len(characters)}')
            output.extend([f'           GC_SpawnCrewStation "{{{station_id}}}" {{',
                           *(f'            m_b{native} 0' for legacy, native in
                             zip(SEAT_TYPES, ('Driver', 'Gunners', 'Passengers')) if flags[legacy] == '0'),
                           '            m_aCharacters {', *('             ' + role for role in roles),
                           '            }', '           }'])
            start = stop + 1
            characters.append(roles)
        if not 1 <= len(characters) <= 16:
            raise ValueError('unsupported number of crew stations')
        output.append('          }')
        if seated == '1':
            output.append('          m_bKeepGunnersSeated 1')
        if route:
            if idle == '1':
                output.append('          m_bSeparateOrderedGunners 1')
            output.extend(['          m_fWaypointDelay ' + delay, '          m_aWaypoints {',
                           *('           ' + point for point in route), '          }'])
        crew.append({'stations': characters, 'waypoints': [point[1:-1] for point in route],
                     'waypointDelaySeconds': delay if route else '0'})
        if missing:
            crew[-1]['missingWaypoints'] = missing
        if name != '""':
            crew[-1]['groupName'] = name[1:-1]
        if shadowed:
            crew[-1]['shadowedByAnchor'] = True
        output.append('         }')
        index = end + 1
        count += 1
    if not 1 <= count <= 16:
        raise ValueError('unsupported number of crew groups')
    return [*output, '        }'], crew


def render_component(component, lines, resolve_character, single_turret, resolve_config=None, *,
                     allow_default_crew=False, placed_names=()):
    configs = [i for i, line in enumerate(lines) if line.strip().startswith('m_crewConfig ')]
    if not configs:
        data = properties(lines, {'m_customCrew', 'm_noTurretDismount', 'm_idleGroup', 'm_pregameSpawn',
                                  'm_waypointNames', 'm_waypointDelay',
                                  *('m_spawn' + seat for seat in SEAT_TYPES)})
        flags = {seat: data.get('m_spawn' + seat, '0') for seat in SEAT_TYPES}
        if all(flag == '0' for flag in flags.values()):
            flags = dict.fromkeys(SEAT_TYPES, '1')
        seated = data.get('m_noTurretDismount', '0')
        idle = data.get('m_idleGroup', '0')
        # A standalone legacy component deserializes an omitted list as an allocated empty array.
        default_crew = not data.get('m_customCrew')
        roles = [] if default_crew else crew_roles(data['m_customCrew'], flags, resolve_character)
        route = crew_route_fields(data, '5')
        if seated not in ('0', '1') or idle not in ('0', '1') or data.get('m_pregameSpawn', '0') != '0':
            raise ValueError('unsupported placed crew timing or gunner settings')
        separate = idle == '1' and flags['Turret'] == '1'
        options = []
        if default_crew:
            if any(flag not in ('0', '1') for flag in flags.values()):
                raise ValueError('unsupported default crew seat filter')
            options.append('   m_bUseDefaultCrew 1')
        if separate and not (single_turret and len(roles) == 1 and not route):
            options.append('   m_bShareIdleGunnerGroup 1')
        return crew_fields(component, flags, seated, roles, [*options, *route])
    if len(configs) != 1:
        raise ValueError('ambiguous placed crew configuration')
    index, = configs
    close = block_end(lines, index)
    deprecated = {'m_noTurretDismount', *('m_spawn' + seat for seat in SEAT_TYPES)}
    outer = properties(lines[:index] + lines[close + 1:], deprecated | {'m_pregameSpawn'})
    if (outer.get('m_pregameSpawn', '0') != '0'
            or any(outer[field] not in ('0', '1') for field in deprecated.intersection(outer))):
        raise ValueError('unsupported placed crew timing or deprecated seat fields')
    # The configured legacy path reads seat filters and dismount policy only from its config.
    config = crew_config_contents(lines[index:close + 1], resolve_config)
    if placed_names:
        return render_structured_placement(component, config, resolve_character, placed_names=placed_names)
    groups = [i for i, line in enumerate(config) if line.strip() == 'm_crewGroups {']
    if len(groups) != 1:
        raise ValueError('crew authoring needs one m_crewGroups')
    start = groups[0]
    end = block_end(config, start)
    options = properties(config[:start] + config[end + 1:], {'m_noTurretDismount', 'm_useIdleGroups'})
    seated = options.get('m_noTurretDismount', '0')
    idle = options.get('m_useIdleGroups', '1')
    if seated not in ('0', '1') or idle not in ('0', '1'):
        raise ValueError('invalid placed crew options')
    group_records = config[start + 1:end]
    if group_records and block_end(group_records, 0) < len(group_records) - 1:
        return render_structured_placement(component, config, resolve_character)
    group = contents(group_records, 'TILW_CrewGroup ' + SERIALIZED_ID)
    stations = [i for i, line in enumerate(group) if line.strip() == 'm_crewStations {']
    if len(stations) != 1:
        raise ValueError('crew authoring needs one m_crewStations')
    start, = stations
    end = block_end(group, start)
    group_options = properties(group[:start] + group[end + 1:], {'m_waypointNames', 'm_waypointDelay'})
    route = crew_route_fields(group_options, '0')
    station_records = group[start + 1:end]
    if station_records and block_end(station_records, 0) < len(station_records) - 1:
        return render_structured_placement(component, config, resolve_character)
    flags, roles = crew_station(station_records, resolve_character, allow_empty=True,
                                allow_default_crew=allow_default_crew)
    if route:
        if idle == '1':
            route.insert(0, '   m_bSeparateOrderedGunners 1')
        route.insert(0, '   m_bAssignInitialCrewOrders 1')
    if not roles:
        route.insert(0, '   m_bUseDefaultCrew 1')
    return crew_fields(component, flags, seated, roles, route)


def render_structured_placement(component, config, resolve_character, *, placed_names=()):
    fields, _ = render_crew_config(config, component['id'], resolve_character, placed=True, placed_names=placed_names)
    return [f'  GC_VehicleCrewComponent {component["id"]} {{',
            *(line[5:] for line in fields), '  }']


def crew_fields(component, flags, seated, roles, options=()):
    characters = ['   m_aCharacters {', *('    ' + role for role in roles), '   }'] if roles else []
    return [f'  GC_VehicleCrewComponent {component["id"]} {{',
            *(f'   m_bAllow{seat} 0' for seat in SEAT_TYPES if flags[seat] == '0'),
            *(['   m_bKeepGunnersSeated 1'] if seated == '1' else []),
            *options, *characters, '  }']


def verified_btr70_overlay(guid, path, source_units, dev_units):
    if guid != BTR70_CREW_OVERLAY or source_units.resources.get(guid) != [path] or dev_units.resources.get(guid) != [path]:
        return False
    before = [sha256(body.strip().encode()).hexdigest() for body in inheritance(source_units, guid)]
    after = [sha256(body.strip().encode()).hexdigest() for body in inheritance(dev_units, guid)]
    return before == [BTR70_CREW_LEAF] and after == [BTR70_CREW_LEAF, BTR70_SMOKE_ANCESTOR]


def verified_core_btr70_transfer(guid, path, source_units, dev_units, stock):
    """Check the exact published GC Core stock override that moved into Units Dev."""
    return ((guid, path) == CORE_BTR70 and stock.contains(guid, path)
            and not source_units.resources.get(guid) and dev_units.resources.get(guid) == [path]
            and sha256(dev_units.read(path).strip().encode()).hexdigest() == CORE_BTR70_DIGEST)


def verified_units_crew_prefab(guid, path, source_units, dev_units, stock, owned, external_parents):
    spec = UNITS_CREW_PREFABS.get(guid)
    if (not spec or path != spec[0]
            or source_units.resources.get(guid) != [path] or dev_units.resources.get(guid) != [path]):
        return False
    _, parent, source_hashes, dev_hashes = spec
    if not (stock.contains(*parent) or external_parents.get(parent[0]) == parent[1]) or any(
            parent[0] in resources for resources in (owned, source_units.resources, dev_units.resources)):
        return False
    before = tuple(sha256(body.strip().encode()).hexdigest() for body in inheritance(source_units, guid))
    after = tuple(sha256(body.strip().encode()).hexdigest() for body in inheritance(dev_units, guid))
    return before == source_hashes and after == dev_hashes


def verified_crew_config(source_units, guid, path, owned):
    spec = REVIEWED_CREW_CONFIGS.get(guid)
    if not spec or guid in owned or path != spec[0] or source_units.resources.get(guid) != [path]:
        raise ValueError('unverified referenced crew config ' + guid)
    body = source_units.read(path).strip()
    if sha256(body.encode()).hexdigest() != spec[1]:
        raise ValueError('referenced crew config differs from the published body ' + guid)
    if guid == '012709863EB73BED':
        prefix = 'Prefabs/Characters/Factions/OPFOR/GC_NVA/Character_GC_NVA_'
        for role, (legacy, canonical) in NVA_CREW_ROLE_PATHS.items():
            registered = prefix + canonical + '.et'
            if source_units.resources.get(role) != [registered]:
                raise ValueError('unverified published crew role ' + role)
            body = body.replace(f'"{{{role}}}{prefix}{legacy}.et"', f'"{{{role}}}{registered}"')
    return contents(body.splitlines(), 'TILW_CrewConfig')


def convert(block, source_units, dev_units, stock, owned, external_vehicles=None, external_ai=None, *,
            external_parents=None, placed_names=(), resolve_character=None):
    if not any('TILW_VehicleCrewComponent' in line for line in block):
        return block
    vehicle = VEHICLE.fullmatch(block[0])
    composition = COMPOSITION.fullmatch(block[0])
    ref = REF.search(block[0])
    if not ref or not (vehicle or composition):
        raise ValueError('crew needs a registered vehicle or composition')
    guid, path = ref[1].upper(), ref[2]
    inherited_turrets = COMPOSITION_TURRETS.get((guid, path), {}) if composition else {}
    vehicle_host = (composition and composition['kind'] == 'StaticModelEntity'
                    and (guid == BTR_DUGOUT or (guid, path) in STOCK_VEHICLE_HOSTS))
    if composition and not (vehicle_host or (composition['kind'] == 'GenericEntity'
                                              and (inherited_turrets or (guid, path) in INLINE_TURRET_HOSTS))):
        raise ValueError('crew needs a reviewed composition turret identity')
    external_vehicle = external_vehicles and external_vehicles.get(guid) == path
    units_owned = guid in source_units.resources or guid in dev_units.resources
    inherited_override = (inherited_turrets and units_owned and guid in COMPOSITION_OVERRIDES
                          and source_units.resources.get(guid) == dev_units.resources.get(guid) == [path]
                          and source_units.read(path) == dev_units.read(path)
                          and sha256(dev_units.read(path).strip().encode()).hexdigest() == COMPOSITION_OVERRIDES[guid])
    vehicle_overlay = vehicle and external_vehicle and verified_btr70_overlay(guid, path, source_units, dev_units)
    units_vehicle = (vehicle or vehicle_host) and units_owned and verified_units_crew_prefab(
        guid, path, source_units, dev_units, stock, owned, external_parents or {})
    core_transfer = vehicle and verified_core_btr70_transfer(guid, path, source_units, dev_units, stock)
    if (guid in owned or (units_owned and not (inherited_override or vehicle_overlay or units_vehicle or core_transfer))
            or not (stock.contains(guid, path) or external_vehicle or units_vehicle)):
        raise ValueError('unverified crew vehicle ' + guid)
    if resolve_character is None:
        def resolve_character(resource, path):
            return character_reference(resource, path, source_units, dev_units, stock, owned, external_ai,
                                       allow_legacy_playable=True)

    def replace_crew(lines, single_turret, *, allow_default_crew=False):
        if not any('TILW_VehicleCrewComponent' in line for line in lines):
            return lines
        roots = [index for index, line in enumerate(lines) if line == ' components {']
        crew = [(index, COMPONENT.fullmatch(line)) for index, line in enumerate(lines)
                if 'TILW_VehicleCrewComponent' in line]
        if len(roots) != 1 or len(crew) != 1:
            raise ValueError('placement needs exactly one standard crew component')
        start = roots[0]
        index, component = crew[0]
        if not component or not start < index < block_end(lines, start):
            raise ValueError('crew component is outside the vehicle components')
        end = block_end(lines, index)
        replacement = render_component(
            component, lines[index + 1:end], resolve_character,
            single_turret, lambda guid, path: verified_crew_config(source_units, guid, path, owned),
            allow_default_crew=allow_default_crew, placed_names=placed_names)
        return [*lines[:index], *replacement, *lines[end + 1:]]

    def convert_children(lines, parents=()):
        start = next((i for i, line in enumerate(lines) if line == ' {'), None)
        if start is None or block_end(lines, start) != len(lines) - 1:
            raise ValueError('unsupported crew composition child list')
        rendered = lines[:start + 1]
        cursor = start + 1
        while cursor < len(lines) - 1:
            end = block_end(lines, cursor)
            child = lines[cursor:end + 1]
            if any('TILW_VehicleCrewComponent' in line for line in child):
                if vehicle_host and not parents and VEHICLE.fullmatch(child[0].strip()):
                    native = convert([line[2:] for line in child], source_units, dev_units, stock, owned,
                                     external_vehicles, external_ai, external_parents=external_parents,
                                     resolve_character=resolve_character)
                    rendered.extend('  ' + line for line in native)
                    cursor = end + 1
                    continue
                header = PLACED_CHILD.fullmatch(child[0].strip())
                ids = [match[1].upper() for item in child[1:-1]
                       if (match := re.fullmatch(r'   ID "([\dA-Fa-f]{16})"', item))]
                if not header or len(ids) > 1:
                    raise ValueError('crew composition child lacks a verified identity')
                child_prefab = (header[2].upper(), header[3]) if header[2] else None
                if bool(ids) == bool(child_prefab):
                    raise ValueError('crew composition child needs one prefab or inherited ID')
                key = (*parents, *ids)
                body = [line[2:] for line in child[1:-1]]
                if child_prefab and (child_prefab[0] in owned or child_prefab[0] in source_units.resources
                                     or child_prefab[0] in dev_units.resources or not stock.contains(*child_prefab)):
                    raise ValueError('unverified explicit crew composition child ' + child_prefab[0])
                if header[1] == 'GenericEntity':
                    if child_prefab and child_prefab not in INLINE_TURRET_HOSTS:
                        raise ValueError('unverified parent of an inline turret')
                    converted = convert_children(body, key)
                else:
                    turret = inherited_turrets.get(key) if ids else None
                    if (child_prefab in SINGLE_SEAT_TURRETS and header[1] == 'Turret'
                            and (not parents or (guid, *parents) in INLINE_TURRET_CHAINS)):
                        turret = child_prefab
                    if (not turret or turret[0] in owned or turret[0] in source_units.resources
                            or turret[0] in dev_units.resources or not stock.contains(*turret)):
                        raise ValueError('unverified inherited turret ' + '/'.join(key))
                    converted = replace_crew(body, turret in SINGLE_SEAT_TURRETS)
                child = [child[0], *('  ' + line for line in converted), child[-1]]
            rendered.extend(child)
            cursor = end + 1
        return [*rendered, lines[-1]]

    def instance(lines):
        if not any('TILW_VehicleCrewComponent' in line for line in lines):
            return lines
        if composition:
            return convert_children(lines)
        return replace_crew(lines, (guid, path) in SINGLE_SEAT_TURRETS,
                            allow_default_crew=(guid, path) in VERIFIED_DEFAULT_CREW)

    root = vehicle or composition
    if not root['group']:
        return [block[0], *instance(block[1:-1]), block[-1]]
    rendered = [block[0]]
    for name, lines in members(block, True, None):
        rendered.append(f' {name} {{' if name else ' {')
        rendered.extend(' ' + line for line in instance(lines))
        rendered.append(' }')
    return [*rendered, block[-1]]
