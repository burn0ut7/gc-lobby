"""Convert authored teleports using the existing GC Lobby interaction prefab."""
import re

from briefings import QUOTED, block_end, guid, properties
from catalog import REF
from interactions import KEY, NATIVE_FIELDS
from polygon_capture import members

TELEPORT_PREFAB = ('5B462DF843A26368', 'Prefabs/Utils/Interactions/TILW_TeleportInteraction.et')
NATIVE_PREFAB = '{4A7B7EF936B349A7}Prefabs/Logic/Mission/TeleportInteraction.et'
PREFAB_ACTION = '404C2C5909834FF5'
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


def convert_instance(lines, key, inherited, standalone):
    action_at = [i for i, line in enumerate(lines) if is_teleport(line)]
    if not action_at:
        return lines, []
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
    replacements, routes = {}, []
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
            if field in NATIVE_FIELDS or re.fullmatch(r'"Sort Priority" -?\d+', lines[cursor].strip()):
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
        required = data.get('m_conditionFlag', '""')
        if (not re.fullmatch(KEY, target) or not re.fullmatch(QUOTED, label) or label == '""'
                or not all(re.fullmatch(KEY, value) for value in factions)
                or (required != '""' and not re.fullmatch(KEY, required))):
            raise ValueError('teleport destination, audience or required flag needs review')
        routes.append((index, last, match[1], target, label, factions, required, native))
    keyed = len(routes) > 1
    if len({route[2] for route in routes}) != len(routes):
        raise ValueError('duplicate teleport action identity')
    if standalone and routes and not any(route[2] == PREFAB_ACTION for route in routes):
        raise ValueError('standalone teleport does not configure its inherited action')
    components, interactions = [], []
    for index, last, identity, target, label, factions, required, native in routes:
        component_id = 'B3632A2C9CA24FDF' if standalone and identity == PREFAB_ACTION else guid(
            key + '/' + identity if keyed else key)
        component = [f'  GC_TeleportInteractionComponent "{{{component_id}}}" {{',
                     '   m_sActionName ' + label, '   m_sDestinationName ' + target]
        if factions:
            component.extend(['   m_aFactions {', *('    ' + faction for faction in factions), '   }'])
        if required != '""':
            component.append('   m_sRequiredFlag ' + required)
        if keyed:
            component.append(f'   m_sInteractionKey "{identity}"')
            native.append(f'     m_sInteractionKey "{identity}"')
        components.extend([*component, '  }'])
        interactions.append({'destination': target[1:-1], 'requiredFlag': required[1:-1]})
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
    output[start + 1:start + 1] = components
    return output, interactions


def convert_children(lines, key, stock, local_resources):
    if ' {' not in lines:
        return lines, [], []
    start = lines.index(' {')
    end = block_end(lines, start)
    if end != len(lines) - 1:
        raise ValueError('teleport children must follow the parent fields')
    children, interactions = [lines[start]], []
    cursor = start + 1
    while cursor < end:
        if not lines[cursor].startswith('  ') or not lines[cursor].endswith(' {'):
            raise ValueError('unsupported teleport child placement')
        stop = block_end(lines, cursor)
        if stop >= end:
            raise ValueError('teleport child crosses its parent')
        child = lines[cursor:stop + 1]
        if any(is_teleport(line) for line in child):
            converted, routes = convert([line[2:] for line in child], f'{key}/child/{cursor}', stock, local_resources)
            child = ['  ' + line for line in converted.splitlines()]
            interactions.extend(routes)
        children.extend(child)
        cursor = stop + 1
    return lines[:start], [*children, lines[end]], interactions


def convert(block, key, stock, local_resources):
    root = ROOT.fullmatch(block[0])
    if not root:
        raise ValueError('teleport is not on a generic object')
    ref = REF.search(block[0])
    parent = (ref[1].upper(), ref[2]) if ref else None
    standalone = parent == TELEPORT_PREFAB
    if standalone and parent[0] in local_resources:
        raise ValueError('mission overrides the legacy teleport prefab')
    inherited = (standalone or (stock and parent == STOCK_FLAGPOLE and parent[0] not in local_resources
                                and stock.contains(*parent)))
    output = [REF.sub(lambda _: f'"{NATIVE_PREFAB}"', block[0], count=1) if standalone else block[0]]
    interactions = []
    for index, (name, fields) in enumerate(members(block, root['group'], root['name'])):
        placement = f'{key}/teleport/{index}'
        fields, children, nested = convert_children(fields, placement, stock, local_resources)
        if standalone:
            if any(re.match(r'  \w+Component ', l) and not MANAGER.fullmatch(l) for l in fields):
                raise ValueError('custom standalone teleport component')
            managers = [l for l in fields if MANAGER.fullmatch(l)]
            if managers and managers != ['  ActionsManagerComponent "{62EF76E0D47ED96A}" {']:
                raise ValueError('custom standalone teleport action manager')
            fields = [re.sub(r'\{([A-F0-9]{16})\}', lambda m: '{' + PREFAB_FIELDS.get(m[1], m[1]) + '}', line)
                      for line in fields]
            fields = [line.replace('"Interact_A"', '"MissionInteraction"')
                      if line.strip() in ('ContextName "Interact_A"', '"Interact_A"') else line for line in fields]
        converted, routes = convert_instance(fields, placement, inherited, standalone)
        converted.extend(children)
        interactions.extend([*routes, *nested])
        if root['group']:
            output.append(f' {name} {{' if name else ' {')
            output.extend(' ' + line for line in converted)
            output.append(' }')
        else:
            output.extend(converted)
    return '\n'.join([*output, block[-1]]) + '\n', interactions
