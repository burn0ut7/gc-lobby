"""Convert authored teleports using the existing GC Lobby interaction prefab."""
import re

from briefings import QUOTED, block_end, guid, properties
from catalog import REF
from interactions import KEY, NATIVE_FIELDS
from polygon_capture import members

TELEPORT_PREFAB = ('5B462DF843A26368', 'Prefabs/Utils/Interactions/TILW_TeleportInteraction.et')
NATIVE_PREFAB = '{4A7B7EF936B349A7}Prefabs/Logic/Mission/TeleportInteraction.et'
PREFAB_FIELDS = {
    '62EF76E0D47ED96A': 'F097C296BF6748C5', '62EF76E0D47ED970': '33B86D1741F2473F',
    '62EF76E0D47ED972': '21B6A8BC15BF41DF', '62EF76E608B6CB36': '404C2C5909834FF5',
}
STOCK_FLAGPOLE = ('0DBE058F13F0C7E8', 'PrefabsEditable/Auto/Structures/Military/Flags/E_FlagPole_02_V1_US.et')
ROOT = re.compile(r'(?P<group>\$grp )?GenericEntity(?: (?P<name>[\w.-]+))?(?: : "[^"]+")? \{')
TELEPORT = re.compile(r'    TILW_TeleportInteraction "\{([\da-fA-F]{16})\}" \{')
MANAGER = re.compile(r'  ActionsManagerComponent "\{[\da-fA-F]{16}\}" \{')
LEGACY_FIELDS = {'m_actionName', 'm_factionKeys', 'm_conditionFlag', 'm_locationName'}


def is_teleport(line):
    return 'TILW_TeleportInteraction' in line


def convert_instance(lines, component_id, inherited):
    action_at = [i for i, line in enumerate(lines) if is_teleport(line)]
    if not action_at:
        return lines, None
    if lines.count(' components {') != 1:
        raise ValueError('teleport needs root components')
    start = lines.index(' components {')
    end = block_end(lines, start)
    if any(l.startswith(('  GC_MissionInteractionComponent ', '  GC_TeleportInteractionComponent '))
           for l in lines[start + 1:end]):
        raise ValueError('existing native interaction component needs review')
    replications = [i for i in range(start + 1, end) if lines[i].startswith('  RplComponent ')]
    if (len(replications) > 1 or (not replications and not inherited)
            or any(l.strip() == 'Enabled 0' for i in replications for l in lines[i + 1:block_end(lines, i)])):
        raise ValueError('teleport needs enabled native replication')
    managers = [i for i in range(start + 1, end) if MANAGER.fullmatch(lines[i])]
    if len(managers) != 1:
        raise ValueError('teleport needs one root action manager')
    manager = managers[0]
    manager_end = block_end(lines, manager)
    actions = [i for i in range(manager + 1, manager_end) if lines[i] == '   additionalActions {']
    if len(actions) != 1:
        raise ValueError('teleport needs one additional action list')
    action_end = block_end(lines, actions[0])
    replacements, settings = {}, None
    for index in action_at:
        match = TELEPORT.fullmatch(lines[index])
        if not match or not actions[0] < index < action_end:
            raise ValueError('teleport action must belong to the root action manager')
        last = block_end(lines, index)
        native, legacy = [], []
        cursor = index + 1
        while cursor < last:
            field = lines[cursor].strip().split(' ', 1)[0]
            stop = block_end(lines, cursor) if lines[cursor].endswith(' {') else cursor
            if field in NATIVE_FIELDS:
                native.extend(lines[cursor:stop + 1])
            elif field in LEGACY_FIELDS:
                legacy.extend(lines[cursor:stop + 1])
            else:
                raise ValueError('unrecognized teleport setting ' + field)
            cursor = stop + 1
        if not native and not legacy:
            # A serialized action without a context or destination can't transfer a player.
            replacements[index] = last, []
            continue
        data = properties(legacy, LEGACY_FIELDS)
        target = data.get('m_locationName', '')
        label = data.get('m_actionName', '"Teleport: Location"')
        factions = data.get('m_factionKeys', '').split()
        if (not re.fullmatch(KEY, target) or not re.fullmatch(QUOTED, label) or label == '""'
                or not all(re.fullmatch(KEY, value) for value in factions)
                or data.get('m_conditionFlag', '""') != '""'):
            raise ValueError('teleport destination, audience or required flag needs review')
        if settings:
            raise ValueError('multiple active teleports need a separate conversion')
        settings = (target, label, factions)
        replacements[index] = last, [lines[index].replace('TILW_TeleportInteraction', 'GC_MissionInteractionAction', 1),
                                      *native, lines[last]]
    output = []
    index = 0
    while index < len(lines):
        if index in replacements:
            stop, replacement = replacements[index]
            output.extend(replacement)
            index = stop + 1
        else:
            output.append(lines[index])
            index += 1
    if not settings:
        return output, None
    target, label, factions = settings
    component = [f'  GC_TeleportInteractionComponent "{{{component_id}}}" {{',
                 '   m_sActionName ' + label, '   m_sDestinationName ' + target]
    if factions:
        component.extend(['   m_aFactions {', *('    ' + faction for faction in factions), '   }'])
    output[start + 1:start + 1] = [*component, '  }']
    return output, target[1:-1]


def convert(block, key, stock, local_resources):
    root = ROOT.fullmatch(block[0])
    if not root:
        raise ValueError('teleport is not on a generic object')
    ref = REF.search(block[0])
    parent = (ref[1].upper(), ref[2]) if ref else None
    standalone = parent == TELEPORT_PREFAB
    if standalone and parent[0] in local_resources:
        raise ValueError('mission overrides the legacy teleport prefab')
    inherited = (standalone or (parent == STOCK_FLAGPOLE and parent[0] not in local_resources
                                and stock.contains(*parent)))
    output = [REF.sub(lambda _: f'"{NATIVE_PREFAB}"', block[0], count=1) if standalone else block[0]]
    destinations = []
    for index, (name, fields) in enumerate(members(block, root['group'], root['name'])):
        component_id = 'B3632A2C9CA24FDF' if standalone else guid(f'{key}/teleport/{index}')
        if standalone:
            if any(re.match(r'  \w+Component ', l) and not MANAGER.fullmatch(l) for l in fields):
                raise ValueError('custom standalone teleport component')
            managers = [l for l in fields if MANAGER.fullmatch(l)]
            if managers and managers != ['  ActionsManagerComponent "{62EF76E0D47ED96A}" {']:
                raise ValueError('custom standalone teleport action manager')
            # The prefab inherits its action and context when the instance omits them.
            if any(TELEPORT.fullmatch(l) and '62EF76E608B6CB36' not in l for l in fields):
                raise ValueError('standalone teleport adds a custom action')
            fields = [re.sub(r'\{([A-F0-9]{16})\}', lambda m: '{' + PREFAB_FIELDS.get(m[1], m[1]) + '}', line)
                      for line in fields]
            fields = [line.replace('"Interact_A"', '"MissionInteraction"')
                      if line.strip() in ('ContextName "Interact_A"', '"Interact_A"') else line for line in fields]
        converted, destination = convert_instance(fields, component_id, inherited)
        if destination:
            destinations.append(destination)
        if root['group']:
            output.append(f' {name} {{' if name else ' {')
            output.extend(' ' + line for line in converted)
            output.append(' }')
        else:
            output.extend(converted)
    return '\n'.join([*output, block[-1]]) + '\n', destinations
