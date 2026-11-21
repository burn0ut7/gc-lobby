"""Apply common migration rules to legacy missions and report unsupported authoring."""
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache, partial
from hashlib import sha256
from pathlib import PurePosixPath
import re

from arc_capture import CAPTURE_EDITOR_FIELDS, capture_instance
from boundaries import convert_boundaries
from briefings import block_end, guid, properties
from catalog import REF, SOURCE_TEXT
from event_actions import (NUMBER, SPAWN, SPAWN_FIELDS, render_crew_config, render_spawn, spawn_details,
                           split_crew_block, target_names)
from events import peak_flags
from extraction import condition as extraction_condition
from groups import inheritance
from interactions import convert_block as convert_interaction_block
from launchers import META_NAME, launcher, replace_reference
from markers import convert_layer
from outcomes import FRAMEWORK, leaves, render as render_outcome
import polygon_capture
from respawns import read_respawns, uses_default_respawn_budget
import teleports
from scenery import FIELD, StockPrefabs, WAYPOINT_CLASS
from weather import convert_weather

LEGACY = re.compile(r'\b(?:TILW|PS)_\w+')
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
                    r'AIWaypointCycle|PolylineShapeEntity|SCR_MapEntity|ACE_TacticalLadderEntity|LightEntity)\b')
WEATHER = re.compile(r'  SCR_TimeAndWeatherHandlerComponent "\{5EE3229927D4D2F5\}" \{')
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
    ('1F3291C2A7E2BA00', 'Prefabs/Props/Wrecks/BRDM2_wreck_static.et'):
        'Prefabs/Props/Wrecks/BRDM2_wreck/BRDM2_wreck_static.et',
    ('23C0BE9D80929DBD', 'Prefabs/Vegetation/Bush/b_salix_cinerea_1w_collision.et'):
        'Prefabs/Vegetation/Bush/b_salix_cinerea_1w.et',
    ('2591F1D52FD5DC65', 'PrefabLibrary/Walls/Net/NetFence_04_8m.et'):
        'PrefabLibrary/DefaultLibrary/Walls/Net/NetFence_04_8m.et',
    ('25B265F3E7CBDFC8', 'Prefabs/Props/Wrecks/BMP1_wreck.et'):
        'Prefabs/Props/Wrecks/BMP1_wreck/BMP1_wreck.et',
    ('75FEF1CA582431F3', 'Prefabs/Props/Wrecks/BTR70_wreck.et'):
        'Prefabs/Props/Wrecks/BTR70_Wreck/BTR70_wreck.et',
    ('AE300E34EA8FF40C', 'Prefabs/Props/Wrecks/Ural4320_wreck_static.et'):
        'Prefabs/Props/Wrecks/Ural4320_wreck/Ural4320_wreck_static.et',
    ('AF4BFBE29028A9FD', 'Prefabs/Vegetation/Bush/b_corylus_avellana_1l_collision.et'):
        'Prefabs/Vegetation/Bush/b_corylus_avellana_1l.et',
    ('CAFF4B51059A0F81', 'PrefabLibrary/Walls/Concrete/ConcreteWall_01_3m.et'):
        'PrefabLibrary/DefaultLibrary/Walls/Concrete/ConcreteWall_01_3m.et',
    ('C98A6C1C2372354E', 'Prefabs/Vegetation/Bush/b_corylus_avellana_1_collision.et'):
        'Prefabs/Vegetation/Bush/b_corylus_avellana_1.et',
}
WORKSHOP_RESOURCE_ALIASES = {
    ('36EA87CEC1BE77E0', 'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK_Olive.et'): (
        'WCS_JLTV_5C721177A220B42F', '36EA87CEC1BE77E0',
        'Prefabs/Vehicles/Wheeled/JLTV/OGPK/M2HB/JLTV_OGPK_M2HB_Olive.et'),
    ('3868E03D9A4AF186', 'Prefabs/Vehicles/Tracked/BMP1/BMP1P_base.et'): (
        'WCS_BMP-1_5E524E4FEECCA92B', '3868E03D9A4AF186',
        'Prefabs/Vehicles/Tracked/BMP1/BMP1P_Base.et'),
    ('5CFD8CB5C3E55A4E', 'Prefabs/Vehicles/Tracked/BFV/M2A2_MERDC.et'): (
        'WCS_M2A2_63120AE07E6C0966', '5CFD8CB5C3E55A4E',
        'Prefabs/Vehicles/Tracked/M2A2/M2A2_MERDC.et'),
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
}
UNITS_PREFAB_ALIASES = {
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


def native_placement(block):
    if not NATIVE.match(block[0]):
        return None
    lines = block
    if any('TILW_NavmeshRegenComponent' in line for line in block):
        lines = block.copy()
        stack = []
        for index, line in enumerate(block):
            if (NAVMESH_COMPONENT.fullmatch(line) and stack and stack[-1] == 'components {'
                    and block[index + 1] == ' ' * (len(line) - len(line.lstrip())) + '}'):
                lines[index] = line.replace('TILW_NavmeshRegenComponent', 'GC_NavmeshRebuildComponent', 1)
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
                if not is_group and (DAMAGE_TARGETS.get(ref[1].upper()) != ref[2] or not stock.contains(*ref.groups())):
                    raise ValueError('unverified damage objective prefab or component placement')
                start = components[0]
                stop = block_end(block, start)
                parent = block.index(' components {')
                if not parent < start < stop < block_end(block, parent):
                    raise ValueError('damage flag is outside root components')
                data = properties(block[start + 1:stop],
                                  {'m_flagName', 'm_targetState', 'm_targetZone', 'm_continuous', 'm_initialHealth'})
                flag = data.get('m_flagName', '')
                if (not re.fullmatch(r'"[\w.-]+"', flag) or len(flag.encode()) > 130
                        or data.get('m_targetState', '2') not in ('2', 'DESTROYED')
                        or data.get('m_targetZone', '""') != '""' or data.get('m_continuous', '0') != '0'
                        or not re.fullmatch(r'1(?:\.0+)?', data.get('m_initialHealth', '1'))):
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
                head = re.fullmatch(r'(GenericEntity)(?: ([\w.-]+))?( : .+)', block[0])
                if not head:
                    raise ValueError('damage objective needs an individual placed entity')
                name = head[2] or 'GC_Damage_' + guid(location)
                if len(name.encode()) > 128:
                    raise ValueError('damage objective name exceeds the native limit')
                converted = [head[1] + ' ' + name + head[3], *block[1:start], *block[stop + 1:]]
                native = native_placement(converted)
                if not native:
                    raise ValueError('damage objective has another legacy component')
                candidates[flag[1:-1]].append((path, line, name, native))
            except ValueError as exc:
                audit[location] = {'gap': str(exc)}
    flags = {}
    if not candidates:
        return flags, placements, audit
    references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                           if path.lower().endswith(('.layer', '.ent', '.et')))
    for flag, entries in candidates.items():
        # Every use must be this producer or the immediate field of a literal condition.
        count = len(re.findall(r'(?m)^ +(?:m_condition )?TILW_LiteralTerm [^\n]+\n +m_flagName "'
                               + re.escape(flag) + r'"$', references))
        if len(entries) != 1 or references.count(f'"{flag}"') != count + 1 or flag in script_text(source):
            for path, line, _, _ in entries:
                audit[f'{path}:{line}'] = {'gap': 'damage flag has another producer or unsupported use'}
            continue
        path, line, name, native = entries[0]
        placements[(path, line)] = native
        flags[flag] = name
        audit[f'{path}:{line}'] = {'flag': flag, 'entity': name, 'condition': 'default damage area destroyed'}
    return flags, placements, audit


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


def named_placements(layers):
    named = {}
    for text in layers:
        for _, block in blocks(text):
            head = re.fullmatch(r'(?P<group>\$grp )?(?P<kind>\w+)(?: (?P<name>[\w.-]+))?(?: : "[^"]+")? \{',
                                block[0])
            if not head:
                continue
            if head['name']:
                named.setdefault(head['name'], []).append((head['kind'], block))
            if not head['group']:
                continue
            for index, line in enumerate(block[1:-1], 1):
                member = re.fullmatch(r' ([\w.-]+) \{', line)
                if member:
                    placement = block[index:block_end(block, index) + 1]
                    named.setdefault(member[1], []).append((head['kind'], placement))
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
        lines = body.splitlines()
        material = MATERIAL_OVERRIDE.fullmatch(body.rstrip('\n'))
        if material and all(FIELD.fullmatch(line.strip()) for line in material['transform'].splitlines()):
            return body
        if (len(lines) > 8 and lines[0] == LIGHT and lines[2:7] == LIGHT_COMPONENT and lines[-1] == '}'
                and all(line.startswith(' ') and line.strip().partition(' ')[0] in LIGHT_FIELDS
                        and '{' not in line and '}' not in line for line in [lines[1], *lines[7:-1]])):
            return body
    raise ValueError('requires a resource conversion rule')


def spawned_objectives(source, dev, world, stock, local_resources):
    """Identify mission-owned interaction prefabs referenced by a standard spawn."""
    reviewed = {}
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
                if (resource in reviewed or source.resources.get(resource) != [path]
                        or dev.resources.get(resource) not in (None, [path]) or not path.lower().endswith('.et')):
                    continue
                text = source.read(path)
                if 'TILW_BaseInteraction' not in text:
                    continue
                (_, block), = blocks(text)
                native, objectives = convert_interaction_block(block, path, stock, local_resources)
                if (len(objectives) != 1 or LEGACY.search(native)
                        or (path in dev.files and native.strip() != dev.read(path).strip())):
                    continue
            except ValueError:
                continue
            reviewed[resource] = {'path': path, 'body': native, 'flag': objectives[0]['flag'],
                                  'singleUse': objectives[0]['singleUse']}
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


def area_rules(source, world):
    conditions, placements, audit = {}, {}, {}
    for layer in source.layers[world.lower()]:
        text = source.read(layer)
        if 'TILW_ExtractionTriggerEntity' not in text and 'TILW_FactionTriggerEntity' not in text:
            continue
        for line, block in blocks(text):
            match = TRIGGER.fullmatch(block[0])
            shape = polygon_capture.SHAPE.fullmatch(block[0])
            if not match and not shape:
                continue
            root = shape or match
            result, areas, capture_details = {}, [], {}
            try:
                for name, fields in polygon_capture.members(block, root['group'], root['name']):
                    trigger = match
                    if shape:
                        placement, (trigger, fields), vertices = polygon_capture.read(fields, TRIGGER)
                    flag_field = next(value.strip() for value in fields if value.strip().startswith('m_flagName '))
                    flag = flag_field.removeprefix('m_flagName ')
                    if not re.fullmatch(r'"\w+"', flag) or flag in conditions or flag in result:
                        raise ValueError('invalid or duplicate area flag')
                    area_name = 'GC_MigrationArea_' + flag[1:-1]
                    if shape or trigger['kind'] == 'Faction':
                        area_name = name or area_name
                    if trigger['kind'] == 'Extraction' and trigger['guid'] == '192ABD6B9232E064':
                        rule, area = extraction_rule(world, area_name, flag, fields, polygon=bool(shape))
                    elif trigger['kind'] == 'Faction' and trigger['guid'] == '8C576D46F6579229':
                        built, area, details = capture_instance(world, area_name, fields, polygon=bool(shape))
                        capture_details[flag[1:-1]] = {'area': area_name, **details}
                        rule = built[flag]
                    else:
                        raise ValueError('inconsistent trigger identity')
                    if shape:
                        area = '\n'.join([f'PolylineShapeEntity {area_name} {{', *placement, '}', ''])
                        capture_details.setdefault(flag[1:-1], {'area': area_name})
                        capture_details[flag[1:-1]]['polygonVertices'] = vertices
                    result[flag] = rule
                    areas.append(area)
            except (StopIteration, ArithmeticError, KeyError, ValueError):
                continue
            conditions.update(result)
            placements[(layer, line)] = ({flag[1:-1] for flag in result}, ''.join(areas))
            if capture_details:
                audit[f'{layer}:{line}'] = capture_details
    return conditions, placements, audit


def extraction_rule(world, area_name, flag, fields, *, polygon=False):
    data = properties(fields, {'coords', 'm_queryRadius', 'm_captureTime', 'm_flagName',
                               'm_stopAfterFirstChange', 'm_factionKey', 'm_ratioThreshold', 'm_playersOnly'}
                      | CAPTURE_EDITOR_FIELDS)
    if data.get('m_stopAfterFirstChange') != '1' or data.get('m_playersOnly', '1') != '1':
        raise ValueError('unsupported extraction settings')
    faction, coords = data['m_factionKey'], data.get('coords', '')
    radius = data.get('m_queryRadius', '25') if polygon else data['m_queryRadius']
    hold, ratio = data.get('m_captureTime', '0'), data.get('m_ratioThreshold', '1')
    if not (re.fullmatch(r'"[\w.-]+"', faction) and (polygon or 0.1 <= float(radius) <= 100000)
            and (float(hold) == 0 or 0.1 <= float(hold) <= 86400)
            and 0 <= float(ratio) <= 1 and (polygon or re.fullmatch(r'[-+\d.]+ [-+\d.]+ [-+\d.]+', coords))):
        raise ValueError('invalid extraction settings')
    check = extraction_condition(world, flag[1:-1], area_name, faction, ratio, hold if float(hold) else None)
    area = '' if polygon else f'''GC_RadiusAreaEntity {area_name} : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
 coords {coords}
 m_fRadius {radius}
}}
'''
    return ['      ' + value for value in check.splitlines()], area


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


def mode_children(lines):
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
            output.extend([f'  {kind}{prefab} {{', f'   ID "{current}"', *fields[1:], *defaults, '  }'])
        else:
            raise ValueError('custom legacy mode child: ' + kind)
        index = end + 1
    if not seen:
        raise ValueError('empty legacy mode children')
    return '\n'.join([*output, ' }', ''])


def render_mode(world, root, preset, additional_rules, zones, ai_faction=None, objective_flags=(), damage_flags=None):
    if not MODE.fullmatch(root[0]):
        raise ValueError('custom legacy game mode')
    weather, environment, coords, freeze, children = '', '', ' coords 0 0 0', 0, ''
    immediate_release = False
    construction = {}
    jip = {}
    respawn_table_seen = False
    safe_start_seen = False
    index = 1
    while index < len(root) - 1:
        line = root[index]
        if line == ' components {':
            end = block_end(root, index)
            if (end <= index + 2 or not WEATHER.fullmatch(root[index + 1])
                    or block_end(root, index + 1) != end - 1):
                raise ValueError('custom legacy game mode components')
            weather, environment = convert_weather(world, root[index + 1:end])
            index = end
        elif re.fullmatch(r' coords [-+\d.]+ [-+\d.]+ [-+\d.]+', line):
            coords = line
        elif re.fullmatch(r' m_iFreezeTime \d+', line):
            freeze, fraction = divmod(int(line.split()[-1]), 1000)
            if fraction and freeze:
                raise ValueError('nonintegral staging seconds')
            immediate_release = not freeze
        elif line == ' m_aFactionRespawnCount {' and not respawn_table_seen:
            end = block_end(root, index)
            records, gaps = read_respawns('\n'.join([root[0], *root[index:end + 1], '}']), world)
            if not uses_default_respawn_budget({'factions': records, 'ticketGaps': gaps}):
                raise ValueError('legacy faction respawn behavior requires a conversion rule')
            respawn_table_seen = True
            index = end
        elif line.startswith((' m_eJIPState ', ' m_denyJIPTime ')):
            field, value = line.strip().split(' ', 1)
            if field in jip:
                raise ValueError('duplicate legacy JIP setting ' + field)
            jip[field] = value
        elif re.fullmatch(r' m_safeStartTime -?\d+', line) and not safe_start_seen:
            # The published Core character controller never calls its safe-start handler.
            if not -2147483648 <= int(line.split()[-1]) <= 2147483647:
                raise ValueError('invalid inactive legacy safe-start time')
            safe_start_seen = True
        elif line.strip().partition(' ')[0] in CONSTRUCTION_FIELDS:
            field, _, value = line.strip().partition(' ')
            if field in construction:
                raise ValueError('duplicate construction progress field ' + field)
            construction[field] = value
        elif line == ' {' and not children:
            end = block_end(root, index)
            children = mode_children(root[index:end + 1])
            index = end
        else:
            raise ValueError('game mode field or child requires a conversion rule: ' + line.strip())
        index += 1
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
    if additional_rules or objective_flags:
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
{flags}{events}  }}
'''
    return f'''GC_LobbyGameMode GC_MigratedMission : "{{{resource}}}{preset}" {{
 components {{
{weather}{environment}{settings} }}
{coords}
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
    preset = row.get('briefingModePreset')
    if not preset or row['briefingGaps']:
        blockers.append({'rule': 'briefing preset', 'file': world, 'detail': row['briefingGaps']})
    if row.get('respawn') and not uses_default_respawn_budget(row['respawn']):
        blockers.append({'rule': 'respawn', 'file': world})
    outcome = row.get('outcomeCandidate', {})
    rules = outcome.get('events', {})
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
        captures, placed_areas, _ = area_rules(source, world)
        area_placements = {**placed_areas, **area_placements}
        spawners, schedules, spawner_audit = world_spawners(source, world, resolve_spawn, captures,
                                                           set(objective_flags) | event_flags, used_flags, blockers,
                                                           resolve_crew)
        rule_text += schedules
        used_flags.update(entry['flag'] for entry in spawner_audit if entry['trigger'] == 'area capture')
    unused_flags = []
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
                        converted, count, slots = convert_group_block(interaction_placements.get((layer, line), body))
                        result.append(converted)
                        group_blocks.append({'file': layer, 'line': line, 'groups': count, 'slots': slots})
                    except ValueError as exc:
                        blockers.append({'rule': kind, 'file': layer, 'line': line, 'detail': str(exc)})
                elif (layer, line) in area_placements and area_placements[(layer, line)][0] <= used_flags:
                    areas = area_placements[(layer, line)][1]
                    area_names.extend(re.findall(r'^(?:GC_RadiusAreaEntity|PolylineShapeEntity) ([\w.-]+) ', areas, re.M))
                    result.append(areas)
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
                elif PERCEPTION.fullmatch(head) and (len(block) == 2 or
                                                     (len(block) == 3 and block[1].startswith(' coords '))):
                    continue
                elif kind == 'SCR_EditorRestrictionZoneEntity' and not LEGACY.search(body):
                    names, converted = restriction_zones(block, len(zones) + 1)
                    zones.extend(names)
                    result.append(converted)
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
                                   set(objective_flags) | event_flags, damage_flags)
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
        if asset['flag'] in used_flags and resource not in spawn_guids:
            blockers.append({'rule': 'spawned objective', 'file': asset['path'],
                             'detail': 'objective flag has no converted prefab spawn'})
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
            if (path.endswith('.layer') and isinstance(data, str) and DUPLICATE_CALLSIGNS.search(data)
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
    if converted_cover:
        remaining.append('Native cover framing follows map opens. Auxiliary outlines use screen width instead of metres.')
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
