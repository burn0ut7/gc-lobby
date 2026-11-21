"""Convert static legacy flag-producing actions on replicated world objects."""
from hashlib import sha256
import re

from briefings import QUOTED, block_end, guid, properties
from catalog import REF
from markers import LEGACY as LEGACY_MARKER, instance as render_marker
from scenery import FIELD

OBJECT = re.compile(r'GenericEntity(?: ([\w.-]+))?(?: : "[^"]+")? \{')
MEMBER = re.compile(r' (?:([\w.-]+) )?\{')
ACTION = re.compile(r'\s*TILW_BaseInteraction "\{([\da-fA-F]{16})\}" \{')
KEY = r'"[\w.-]+"'
NATIVE_FIELDS = {'ParentContextList', 'UIInfo', 'ActionTitle', 'VisibilityRange', 'Duration'}
LEGACY_FIELDS = {'m_actionName', 'm_factionKeys', 'm_flagName', 'm_singleUse', 'm_conditionFlag', 'm_deleteParent'}
REPLICATED_PREFABS = {
    ('EF070B8345288C8B', 'Prefabs/Props/Military/MilitaryCrates/CrateStack_01/Dst/CrateStack_01_Dst_2_base.et'),
    ('E1AED4C5182AA514', 'Prefabs/Props/Military/MilitaryCrates/CrateStack_01/Dst/CrateStack_01_Dst_2_decal_01.et'),
}
LEGACY_PREFAB = ('584EB34600E9698D', 'Prefabs/Utils/Interactions/TILW_SetFlagInteraction.et')
NATIVE_PREFAB = '{B48D431A323D4564}Prefabs/Logic/Mission/Interaction.et'
BLACKBOX = ('B66EE06D3F5749D6', 'worlds/FloridaMan/Prefabs/Setpeice/Wreck_AH64_Blackbox.et')
BLACKBOX_MARKER_ID = '655B660447B37DF0'
BLACKBOX_FLAG = 'BlackboxRemoved'
LEGACY_ACTION = '61F4C18E863E752C'
PREFAB_FIELDS = {
    '61F4C18E835074FF': 'ADA66F0D5EA84AC2',
    '61F4C18ECB0A8B85': '2E0FE192CFC14805',
    '61F4C18F31FA02AA': '6A5E45532F2649F1',
    LEGACY_ACTION: '4F65BF8AFE944D18',
    '61F4C18EBF064AFE': '4D41DD504E094996',
    '6242E7BB27068E93': 'E7A35F87849446C0',
}


def blackbox_prefab(text):
    if sha256(text.strip().encode()).hexdigest() != (
            'cda3d6acbd54ff720b4942f878293198f2832d2cfb43c4f5de61e959273b8baf'):
        raise ValueError('blackbox composition differs from the reviewed prefab')
    lines = text.splitlines()
    action = next(i for i, line in enumerate(lines) if 'Prefabs/Utils/Interactions/TILW_BaseInteraction.et' in line)
    end = block_end(lines, action)
    replacement = lines[action].replace(
        '{584EB34600E9698D}Prefabs/Utils/Interactions/TILW_BaseInteraction.et', NATIVE_PREFAB)
    native, details = convert_object(
        [replacement, *lines[action + 1:end + 1]], '', BLACKBOX[1], inherited_replication=True,
        standalone=True, allow_parent_removal=True)
    if len(details) != 2 or any(item['flag'] != BLACKBOX_FLAG for item in details):
        raise ValueError('blackbox actions differ from the reviewed objective')
    lines[action:end + 1] = native
    marker = next(i for i, line in enumerate(lines) if LEGACY_MARKER.fullmatch(line.strip()))
    end = block_end(lines, marker)
    identity = lines[marker + 1].strip()
    if identity != f'ID "{BLACKBOX_MARKER_ID}"':
        raise ValueError('blackbox marker identity changed')
    data = [line.strip() for line in lines[marker + 2:end]]
    header, *body = render_marker(data, '', draw_order=True).splitlines()
    lines[marker:end + 1] = ['  ' + line for line in (header, ' ' + identity, *body)]
    return '\n'.join(lines) + '\n'


def blackbox_placement(block):
    """Keep the inherited marker override attached to the migrated prefab child."""
    lines = block.copy()
    for index, line in enumerate(block):
        if line.strip() != 'PS_ManualMarker {':
            continue
        end = block_end(block, index)
        identity, *transform = [field.strip() for field in block[index + 1:end]]
        if (identity != f'ID "{BLACKBOX_MARKER_ID}"'
                or any(not FIELD.fullmatch(field) or field.partition(' ')[0] not in
                       {'coords', 'angleX', 'angleY', 'angleZ', 'angles'} for field in transform)):
            raise ValueError('unsupported inherited blackbox marker override')
        lines[index] = line.replace('PS_ManualMarker', 'GenericEntity', 1)
    return '\n'.join(lines) + '\n'


def convert_object(lines, name, key, inherited_replication=False, *, standalone=False, allow_parent_removal=False):
    actions = {i: match[1] for i, line in enumerate(lines) if (match := ACTION.fullmatch(line))}
    if not actions and not standalone:
        return lines, []
    child_indent = len(lines[0]) - len(lines[0].lstrip()) + 1
    indent = ' ' * child_indent
    if (not actions or len(set(actions.values())) != len(actions) or indent + 'components {' not in lines
            or (standalone and LEGACY_ACTION not in actions.values())):
        raise ValueError('requires distinct root interactions and an authored inherited action')
    components = lines.index(indent + 'components {')
    component_end = block_end(lines, components)
    if any(line.startswith((indent + ' GC_MissionInteractionComponent ',
                            indent + ' GC_TeleportInteractionComponent '))
           for line in lines[components + 1:component_end]):
        raise ValueError('existing native interaction component needs review')
    replication = [index for index in range(components + 1, component_end)
                   if lines[index].startswith(indent + ' RplComponent ')]
    if (len(replication) > 1
            or (replication and any(line.strip() == 'Enabled 0'
                                    for line in lines[replication[0] + 1:block_end(lines, replication[0])]))
            or (not replication and not inherited_replication)):
        raise ValueError('interaction object needs a root replication component')
    managers = [i for i in range(components + 1, component_end)
                if lines[i].startswith(indent + ' ActionsManagerComponent ')]
    if len(managers) != 1:
        raise ValueError('requires one root interaction action manager')
    manager, = managers
    if standalone and not lines[manager].endswith('"{61F4C18E835074FF}" {'):
        raise ValueError('custom standalone interaction action manager')
    lists = [i for i in range(manager + 1, block_end(lines, manager))
             if lines[i] == indent + '  additionalActions {']
    if len(lists) != 1:
        raise ValueError('requires one additional action list')
    action_list, = lists
    list_end = block_end(lines, action_list)
    components_out, details, replacements = [], [], {}
    for start, identity in actions.items():
        end = block_end(lines, start)
        if not (action_list < start < end < list_end
                and lines[start].startswith(indent + '   TILW_BaseInteraction ')):
            raise ValueError('interaction must belong to the root action manager')
        inherited_action = standalone and identity == LEGACY_ACTION
        match_key = identity if len(actions) > 1 else ''
        component_id = ('DCEC291493734CD4' if inherited_action
                        else guid(key + '/' + name + ('/' + match_key if match_key else '')))
        native, fields, detail = convert_action(lines[start:end + 1], name, match_key,
                                                allow_parent_removal=allow_parent_removal)
        replacements[start] = (end, native)
        component = [f'GC_MissionInteractionComponent "{{{component_id}}}" {{', *fields, '}']
        components_out.extend(indent + ' ' + line for line in component)
        details.append(detail)
    placement = [*lines[:components + 1], *components_out]
    index = components + 1
    while index < len(lines):
        if index in replacements:
            stop, replacement = replacements[index]
            placement.extend(replacement)
            index = stop + 1
        else:
            placement.append(lines[index])
            index += 1
    if standalone:
        placement = [re.sub(r'\{([\dA-F]{16})\}', lambda match: '{' + PREFAB_FIELDS.get(match[1], match[1]) + '}', line)
                     for line in placement]
        placement = [line.replace('"Interact_A"', '"MissionInteraction"')
                     if line.strip() in ('ContextName "Interact_A"', 'ActionTitle "Interact_A"', '"Interact_A"')
                     else line for line in placement]
    return placement, details


def convert_action(action, name, match_key, *, allow_parent_removal=False):
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
    required = data.get('m_conditionFlag', '""')
    remove_parent = data.get('m_deleteParent', '0')
    if (not re.fullmatch(QUOTED, title) or title == '""' or not re.fullmatch(KEY, flag)
            or not all(re.fullmatch(KEY, faction) for faction in factions) or once not in ('0', '1')
            or (required != '""' and not re.fullmatch(KEY, required))
            or remove_parent not in ('0', '1') or (remove_parent == '1' and not allow_parent_removal)):
        raise ValueError('interaction availability or side effects need review')
    fields = [' m_sActionName ' + title, ' m_sCallFlag ' + flag]
    if remove_parent == '1':
        fields.append(' m_bDeleteParentOnUse 1')
    if once == '1':
        fields.append(' m_bSingleUse 1')
    if factions:
        fields.extend([' m_aFactions {', *('  ' + faction for faction in factions), ' }'])
    if required != '""':
        fields.append(' m_sRequiredFlag ' + required)
    if match_key:
        fields.append(f' m_sInteractionKey "{match_key}"')
        native.append(' ' * (len(action[0]) - len(action[0].lstrip()) + 1) + f'm_sInteractionKey "{match_key}"')
    return [*native, action[-1]], fields, {'entity': name, 'flag': flag[1:-1], 'singleUse': once == '1',
                                          **({'requiredFlag': required[1:-1]} if required != '""' else {})}


def convert_block(lines, key, stock=None, local_resources=()):
    group = lines[0].startswith('$grp GenericEntity')
    root = OBJECT.fullmatch(lines[0])
    if not root and not group:
        raise ValueError('interaction is not on a generic object')
    prefab = REF.search(lines[0])
    resource = (prefab[1].upper(), prefab[2]) if prefab else None
    standalone = resource == LEGACY_PREFAB
    if standalone and resource[0] in local_resources:
        raise ValueError('mission overrides the legacy interaction prefab')
    inherited = standalone or (stock is not None and resource in REPLICATED_PREFABS
                               and resource[0] not in local_resources and stock.contains(*resource))
    head = REF.sub(lambda _: f'"{NATIVE_PREFAB}"', lines[0], count=1) if standalone else lines[0]
    if root:
        placement, details = convert_object([head, *lines[1:]], root[1] or key, key, inherited, standalone=standalone)
        return '\n'.join(placement) + '\n', details
    output, details, names = [head], [], set()
    index = 1
    while index < len(lines) - 1:
        name = MEMBER.fullmatch(lines[index])
        if not name:
            raise ValueError('unnamed interaction group member')
        end = block_end(lines, index)
        placement, items = convert_object(lines[index:end + 1], name[1] or f'{key}/{index}', key, inherited,
                                          standalone=standalone)
        if items and name[1]:
            if name[1] in names:
                raise ValueError('duplicate interaction group member name')
            names.add(name[1])
        output.extend(placement)
        details.extend(items)
        index = end + 1
    return '\n'.join([*output, lines[-1]]) + '\n', details
