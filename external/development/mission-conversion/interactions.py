"""Convert static legacy flag-producing actions on replicated world objects."""
import re

from briefings import QUOTED, block_end, guid, properties
from catalog import REF

OBJECT = re.compile(r'GenericEntity(?: ([\w.-]+))?(?: : "[^"]+")? \{')
MEMBER = re.compile(r' ([\w.-]+) \{')
ACTION = re.compile(r'\s*TILW_BaseInteraction "\{[\da-fA-F]{16}\}" \{')
KEY = r'"[\w.-]+"'
NATIVE_FIELDS = {'ParentContextList', 'UIInfo', 'ActionTitle', 'VisibilityRange', 'Duration'}
LEGACY_FIELDS = {'m_actionName', 'm_factionKeys', 'm_flagName', 'm_singleUse', 'm_conditionFlag', 'm_deleteParent'}
REPLICATED_PREFABS = {
    ('EF070B8345288C8B', 'Prefabs/Props/Military/MilitaryCrates/CrateStack_01/Dst/CrateStack_01_Dst_2_base.et'),
}


def convert_object(lines, name, key, inherited_replication=False):
    actions = [i for i, line in enumerate(lines) if ACTION.fullmatch(line)]
    if not actions:
        return lines, None
    child_indent = len(lines[0]) - len(lines[0].lstrip()) + 1
    indent = ' ' * child_indent
    if len(actions) != 1 or indent + 'components {' not in lines:
        raise ValueError('requires one interaction on a replicated object')
    components = lines.index(indent + 'components {')
    component_end = block_end(lines, components)
    replication = [index for index in range(components + 1, component_end)
                   if lines[index].startswith(indent + ' RplComponent ')]
    if (len(replication) > 1
            or (replication and any(line.strip() == 'Enabled 0'
                                    for line in lines[replication[0] + 1:block_end(lines, replication[0])]))
            or (not replication and not inherited_replication)):
        raise ValueError('interaction object needs a root replication component')
    start = actions[0]
    end = block_end(lines, start)
    manager = next((i for i in range(components + 1, start)
                    if lines[i].startswith(indent + ' ActionsManagerComponent ') and block_end(lines, i) > end), None)
    if (manager is None or not lines[start].startswith(indent + '   TILW_BaseInteraction ')
            or not any(lines[i] == indent + '  additionalActions {' and block_end(lines, i) > end
                       for i in range(manager + 1, start))):
        raise ValueError('interaction must belong to the root action manager')
    action = lines[start:end + 1]
    native = [action[0].replace('TILW_BaseInteraction', 'GC_MissionInteractionAction', 1)]
    legacy = []
    i = 1
    while i < len(action) - 1:
        field = action[i].strip().split(' ', 1)[0]
        stop = block_end(action, i) if action[i].rstrip().endswith(' {') else i
        if field in NATIVE_FIELDS:
            native.extend(action[i:stop + 1])
        elif field in LEGACY_FIELDS:
            legacy.extend(action[i:stop + 1])
        else:
            raise ValueError('unsupported interaction field ' + field)
        i = stop + 1
    data = properties(legacy, LEGACY_FIELDS)
    title = data.get('m_actionName', '"Interact: Entity"')
    flag = data.get('m_flagName', '')
    factions = data.get('m_factionKeys', '').split()
    once = data.get('m_singleUse', '1')
    if (not re.fullmatch(QUOTED, title) or title == '""' or not re.fullmatch(KEY, flag)
            or not all(re.fullmatch(KEY, faction) for faction in factions) or once not in ('0', '1')
            or data.get('m_conditionFlag', '""') != '""' or data.get('m_deleteParent', '0') != '0'):
        raise ValueError('interaction availability or side effects need review')
    fields = [' m_sActionName ' + title, ' m_sCallFlag ' + flag]
    if once == '1':
        fields.append(' m_bSingleUse 1')
    if factions:
        fields.extend([' m_aFactions {', *('  ' + faction for faction in factions), ' }'])
    component = [f'GC_MissionInteractionComponent "{{{guid(key + "/" + name)}}}" {{', *fields, '}']
    placement = [*lines[:components + 1], *(indent + ' ' + line for line in component),
                 *lines[components + 1:start], *native, action[-1], *lines[end + 1:]]
    return placement, {'entity': name, 'flag': flag[1:-1], 'singleUse': once == '1'}


def convert_block(lines, key, stock=None, local_resources=()):
    group = lines[0].startswith('$grp GenericEntity')
    root = OBJECT.fullmatch(lines[0])
    if not root and not group:
        raise ValueError('interaction is not on a generic object')
    if root:
        prefab = REF.search(lines[0])
        resource = (prefab[1].upper(), prefab[2]) if prefab else None
        inherited = (stock is not None and resource in REPLICATED_PREFABS
                     and resource[0] not in local_resources and stock.contains(*resource))
        placement, details = convert_object(lines, root[1] or key, key, inherited)
        return '\n'.join(placement) + '\n', [details] if details else []
    output, details = [lines[0]], []
    index = 1
    while index < len(lines) - 1:
        name = MEMBER.fullmatch(lines[index])
        if not name:
            raise ValueError('unnamed interaction group member')
        end = block_end(lines, index)
        placement, item = convert_object(lines[index:end + 1], name[1], key)
        output.extend(placement)
        if item:
            details.append(item)
        index = end + 1
    return '\n'.join([*output, lines[-1]]) + '\n', details
