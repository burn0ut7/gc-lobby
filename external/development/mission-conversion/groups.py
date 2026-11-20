"""Convert complete layers of playable groups backed by both revisions of GC Units."""
from functools import lru_cache, partial
import json
from pathlib import Path
import re

from briefings import block_end
from catalog import REF
from scenery import StockPrefabs

ROOT = re.compile(r'^(?P<group>\$grp )?SCR_AIGroup(?: [\w.\-]+)? : "\{(?P<guid>[A-Fa-f0-9]{16})\}[^"]+\.et" \{$')
CALL = re.compile(r'^\s*PS_GroupCallsignAssigner "\{([\da-f]{16})\}" \{$', re.I)
STANDARD_CALLSIGN = '63616C6C7369676E'
INDEX = {'m_iCompanyCallsign': 'm_iGC_CallsignCompany', 'm_iPlatoonCallsign': 'm_iGC_CallsignPlatoon',
         'm_iSquadCallsign': 'm_iGC_CallsignSquad'}
SLOTS = re.compile(r'^ m_aUnitPrefabSlots \{\n(.*?)^ \}', re.M | re.S)
NATIVE_GROUP_FIELD = re.compile(r'm_faction "[\w.-]+"|m_iGroupRadioFrequency \d+')
STATIC_LINK_FIELDS = {'m_aStaticWaypoints {', 'm_aStaticVehicles {'}
FORMATION = re.compile(r'AIFormationComponent "\{[\da-fA-F]{16}\}" \{')
WRAPPERS = json.loads(Path(__file__).with_name('stock_wrappers.json').read_text(encoding='utf-8'))
BASE_CHARACTER = ('37578B1666981FCE', 'Prefabs/Characters/Core/Character_Base.et')
# These external parents were verified against the published Core/RHS prefabs.
# Both inherit Character_Base, which the legacy GC Lobby package overlays.
LEGACY_PLAYABLE_PARENTS = {
    BASE_CHARACTER,
    ('5346CF7E39A65A6B', 'Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Base.et'),
    ('B889387E9A33D667',
     'Prefabs/Characters/Factions/BLUFOR/RHS_USAF/RHS_USAF_Army/Character_RHS_USA_BaseLoadout.et'),
    ('EDCFF009262F881C', 'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/Character_RHS_AFRF_Base.et'),
    ('7A9EE19AB67B298B', 'Prefabs/Characters/Factions/INDFOR/FIA/Character_FIA_base.et'),
    ('836E7E39AAC5888B', 'Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_Base.et'),
    ('FDAEA6C9F2673B02', 'Prefabs/Characters/Factions/BLUFOR/RHS_USAF/Character_RHS_USAF_Base.et'),
}
RHS_AI_ROLES = {
    '1701B425406974A0': 'SL', '234A37EC8608DFAC': 'Rifleman', '727CB9BA48D9407A': 'SR_GL',
    'A5548567111B86FD': 'AR', 'AFF07941936347BB': 'AAT', 'F11CCA20FCC74AA3': 'AT',
}


def rhs_ai_characters(workshop):
    database = workshop / 'RHS-StatusQuo_595F2BF2F44836FB/resourceDatabase.rdb'
    if not database.is_file():
        return {}, None
    registry = StockPrefabs(database)
    root = 'Prefabs/Characters/Factions/OPFOR/RHS_AFRF/MSV/VKPO_Demiseason/'
    characters = {guid: root + f'RandomSoldiers/Character_RHS_RF_MSV_VKPO_DS_{role}_Random.et'
                  for guid, role in RHS_AI_ROLES.items()}
    characters['0848BAB1B94FCBBA'] = root + 'Character_RHS_RF_MSV_VKPO_DS_Crew.et'
    return {guid: path for guid, path in characters.items() if registry.contains(guid, path)}, registry


def only_path(snapshot, guid):
    paths = snapshot.resources.get(guid.upper(), [])
    if len(paths) != 1:
        raise ValueError('unresolved or duplicate Units prefab ' + guid)
    return paths[0]


@lru_cache(maxsize=2048)
def prefab(snapshot, guid):
    path = only_path(snapshot, guid)
    text = snapshot.read(path)
    if not text.startswith('SCR_AIGroup'):
        raise ValueError('expected SCR_AIGroup ' + path)
    return path, text


def callsign(lines):
    result = {}
    for line in lines:
        field, _, value = line.strip().partition(' ')
        if field not in {*INDEX, 'Enabled'} or not re.fullmatch(r'\d+', value):
            raise ValueError('custom callsign component field ' + field)
        result[field] = int(value)
    return result


@lru_cache(maxsize=16384)
def inheritance(snapshot, guid):
    chain = []
    seen = set()
    while guid.upper() in snapshot.resources:
        if guid in seen:
            raise ValueError('cycle in Units prefab ancestry')
        seen.add(guid)
        body = snapshot.read(only_path(snapshot, guid))
        chain.append(body)
        parent = REF.search(body.splitlines()[0])
        if not parent:
            break
        guid = parent[1]
    return tuple(chain)


def scalar(chain, field):
    for body in chain:
        match = re.search(r'^ ' + field + r' (\d+)$', body, re.M)
        if match:
            return int(match[1])
    return None


@lru_cache(maxsize=8192)
def legacy_member(snapshot, guid, force):
    for body in inheritance(snapshot, guid):
        member = re.search(r'^  PS_PlayableComponent "[^"]+" \{\n(.*?)^  \}', body, re.M | re.S)
        if member:
            if force:
                return True
            active = re.search(r'^   m_bIsPlayable ([01])$', member[1], re.M)
            if active:
                return active[1] == '1'
    return False


def inherits_legacy_playable_base(snapshot, guid, stock):
    chain = inheritance(snapshot, guid)
    if not chain or not stock or not stock.contains(*BASE_CHARACTER):
        return False
    if any(not body.startswith('SCR_ChimeraCharacter ') or 'PS_PlayableComponent' in body for body in chain):
        return False
    parent = REF.search(chain[-1].splitlines()[0])
    return parent is not None and (parent[1].upper(), parent[2]) in LEGACY_PLAYABLE_PARENTS


def stock_character(guid, path, stock):
    member = WRAPPERS['characters'].get(guid.upper())
    if stock and member and member['path'] == path and stock.contains(*member['parent']):
        return member['parent']
    return None


def compare_ai_parents(source_units, dev_units, guid):
    """Compare native AI group ancestry after removing lobby presentation metadata."""
    def normalize(body):
        lines = body.splitlines()
        kept = []
        index = 0
        while index < len(lines):
            line = lines[index]
            component = CALL.fullmatch(line)
            if component and component[1].upper() == STANDARD_CALLSIGN:
                end = block_end(lines, index)
                callsign(lines[index + 1:end])
                index = end + 1
                continue
            if not re.match(r'^ (?:m_sCustomNameSet|m_sGC_Description|m_iGC_Callsign\w+|m_bPlayable|m_bSetPlayable) ', line):
                kept.append(REF.sub(lambda match: '{' + match[1].upper() + '}', line))
            index += 1
        normalized = '\n'.join(kept).replace(' components {\n }\n', '')
        if re.search(r'\b(?:TILW|PS)_\w+', normalized):
            raise ValueError('AI group parent has custom legacy behavior')
        return normalized

    old = inheritance(source_units, guid)
    new = inheritance(dev_units, guid)
    if not old or tuple(map(normalize, old)) != tuple(map(normalize, new)):
        raise ValueError('inherited AI group behavior differs')
    if scalar(old, 'm_bSetPlayable') not in (None, 0) or scalar(new, 'm_bPlayable') not in (None, 0):
        raise ValueError('inherited AI group playability differs')
    return old


def native_group_field_end(lines, index):
    field = lines[index].strip()
    if NATIVE_GROUP_FIELD.fullmatch(field):
        return index
    if field in STATIC_LINK_FIELDS:
        end = block_end(lines, index)
        # An empty vehicle name is a serialized placeholder. All nonempty names remain in order.
        name = r'[\w.-]*' if field == 'm_aStaticVehicles {' else r'[\w.-]+'
        if any(not re.fullmatch(rf'\s*(?:"{name}"\s*)+', entry) for entry in lines[index + 1:end]):
            raise ValueError('unsupported static group link')
        return end
    return None


def convert_ai_instance(lines, guid, source_units, dev_units, locally_owned, external_ai):
    old_chain = compare_ai_parents(source_units, dev_units, guid)
    checked = set()

    def member(match):
        resource = match[1].upper()
        if (resource not in locally_owned and resource not in source_units.resources and resource not in dev_units.resources
                and external_ai.get(resource) == match[2]):
            return match[0]
        if resource not in checked:
            path = only_path(source_units, resource)
            character = source_units.read(path)
            if resource in locally_owned or not character.startswith('SCR_ChimeraCharacter') or legacy_member(source_units, resource, True):
                raise ValueError('AI slots require character playability review ' + resource)
            checked.add(resource)
        return '"{' + resource + '}' + only_path(dev_units, resource) + '"'

    for parent in old_chain:
        slots = SLOTS.search(parent)
        if slots:
            REF.sub(member, slots[1])

    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        token = line.strip()
        if re.fullmatch(r'm_aUnitPrefabSlots (?:\+ ?)?\{', token):
            end = block_end(lines, index)
            if any(REF.sub('', entry).strip() for entry in lines[index + 1:end]):
                raise ValueError('unsupported AI slot list')
            result.extend([line, *(REF.sub(member, entry) for entry in lines[index + 1:end]), lines[end]])
            index = end
        elif (end := native_group_field_end(lines, index)) is not None:
            result.extend(lines[index:end + 1])
            index = end
        elif token == 'components {':
            end = block_end(lines, index)
            if (end < index + 3 or not FORMATION.fullmatch(lines[index + 1].strip())
                    or block_end(lines, index + 1) != end - 1):
                raise ValueError('AI group components need a conversion rule')
            result.extend(lines[index:end + 1])
            index = end
        elif token.startswith(('coords ', 'angles ', 'angleX ', 'angleY ', 'angleZ ', 'scale ', 'm_bSnapToTerrain ')):
            result.append(line)
        elif token != 'm_bSetPlayable 0':
            raise ValueError('AI group field needs review: ' + token.split()[0])
        index += 1
    return result, 0


def inherited_playable_roster(old, new, stock, owned_resources):
    old_slots, new_slots = SLOTS.search(old), SLOTS.search(new)
    if not old_slots or not new_slots:
        raise ValueError('inherited slot roster requires comparison')
    legacy = REF.findall(old_slots[1])
    current = REF.findall(new_slots[1])
    if len(legacy) != len(current):
        raise ValueError('inherited slot roster differs')
    wrappers = set()
    for (guid, path), (dev_guid, dev_path) in zip(legacy, current):
        if guid.upper() == dev_guid.upper():
            continue
        parent = None
        if not any(guid.upper() in resources for resources in owned_resources):
            parent = stock_character(guid, path, stock)
        if not parent or tuple(parent) != (dev_guid.upper(), dev_path):
            raise ValueError('inherited slot roster differs')
        wrappers.add(guid.upper())
    if wrappers and (REF.sub('', old_slots[1]).strip() or REF.sub('', new_slots[1]).strip()):
        raise ValueError('inherited wrapper roster contains unsupported entries')
    return [guid for guid, _ in legacy], wrappers


def convert_instance(lines, guid, old, new, source_units, dev_units, stock, locally_owned,
                     reviewed_callsigns=frozenset(), reviewed_members=None, external_ai=None):
    reviewed_members = reviewed_members or {}
    explicit_force = any(l.strip() == 'm_bSetPlayable 1' for l in lines)
    native_playable = any(l.strip() == 'm_bPlayable 1' for l in lines) or scalar(inheritance(dev_units, guid), 'm_bPlayable') == 1
    if not native_playable and not explicit_force:
        return convert_ai_instance(lines, guid, source_units, dev_units, locally_owned, external_ai or {})
    replace_slots = any(l.strip() == 'm_aUnitPrefabSlots {' for l in lines)
    roster = []
    converted_wrappers = set()
    if not replace_slots:
        roster, converted_wrappers = inherited_playable_roster(
            old, new, stock, (locally_owned, source_units.resources, dev_units.resources))
    override = next((l.strip()[-1] for l in lines if l.strip().startswith('m_bSetPlayable ')), None)
    force = override == '1' if override is not None else scalar(inheritance(source_units, guid), 'm_bSetPlayable') == 1
    out = []
    metadata = []
    depth = len(lines[0]) - len(lines[0].lstrip()) if lines else 1
    indent = ' ' * depth
    i = 0
    while i < len(lines):
        line = lines[i]
        token = line.strip()
        if token == 'components {':
            end = block_end(lines, i)
            j = i + 1
            components = []
            assigned = None
            while j < end:
                last = block_end(lines, j)
                match = CALL.fullmatch(lines[j])
                if match and (match[1].upper() == STANDARD_CALLSIGN or match[1].upper() in reviewed_callsigns):
                    placed = callsign(lines[j+1:last])
                    if assigned is not None:
                        # A unification pass reused the same ID for the retained numeric
                        # override and the old disabled default. Enabled has no bearing on
                        # the numeric reads made by the legacy playable manager.
                        if not reviewed_callsigns and not (
                                assigned[0] == match[1].upper() and set(assigned[1]) & INDEX.keys()
                                and placed == {'Enabled': 0}):
                            raise ValueError('ambiguous duplicate callsign component')
                        j = last + 1
                        continue
                    # PS_PlayableManager reads the component even when Enabled is zero.
                    # Unspecified attributes inherit through the Units prefab chain, then
                    # use the three zero defaults of PS_GroupCallsignAssigner.
                    effective = dict.fromkeys(INDEX, 0)
                    for body in reversed(inheritance(source_units, guid)):
                        source_lines = body.splitlines()
                        for pos, item in enumerate(source_lines):
                            inherited_call = CALL.fullmatch(item)
                            if inherited_call and inherited_call[1].upper() == match[1].upper():
                                effective.update(callsign(source_lines[pos+1:block_end(source_lines, pos)]))
                    effective.update(placed)
                    metadata.extend(indent + f'{dest} "{effective[k]}"' for k, dest in INDEX.items())
                    assigned = (match[1].upper(), placed)
                elif FORMATION.fullmatch(lines[j].strip()):
                    components.extend(lines[j:last+1])
                else:
                    raise ValueError('group component needs review')
                j = last + 1
            if components:
                out.extend([line, *components, lines[end]])
            i = end
        elif re.fullmatch(r'm_aUnitPrefabSlots (?:\+ ?)?\{', token):
            end = block_end(lines, i)
            def slot(match):
                guid, path = match.groups()
                if guid.upper() in reviewed_members:
                    path = reviewed_members[guid.upper()]
                elif guid.upper() in locally_owned:
                    raise ValueError('mission-owned slot ' + guid)
                elif guid.upper() in source_units.resources or guid.upper() in dev_units.resources:
                    only_path(source_units, guid)
                    path = only_path(dev_units, guid)
                else:
                    parent = stock_character(guid, path, stock) if replace_slots else None
                    if parent:
                        converted_wrappers.add(guid.upper())
                        return f'"{{{parent[0]}}}{parent[1]}"'
                    if not stock or not stock.contains(guid, path):
                        raise ValueError('unresolved character prefab ' + guid)
                if guid.upper() not in reviewed_members and not path.lower().startswith('prefabs/characters/'):
                    raise ValueError('unexpected slot resource ' + path)
                return '"{' + guid + '}' + path + '"'
            if '+' not in token:
                roster.clear()
            roster.extend(g for item in lines[i+1:end] for g, _ in REF.findall(item))
            out.append(line)
            for item in lines[i+1:end]:
                if REF.sub('', item).replace('""', '').strip():
                    raise ValueError('unsupported slot list')
                out.append(REF.sub(slot, item))
            out.append(lines[end])
            i = end
        elif (end := native_group_field_end(lines, i)) is not None:
            out.extend(lines[i:end + 1])
            i = end
        elif token.startswith('m_sCustomNameSet '):
            metadata.append(line.replace('m_sCustomNameSet ', 'm_sGC_Description ', 1))
        elif token in ('m_bSetPlayable 0', 'm_bSetPlayable 1'):
            pass
        elif token.startswith(('coords ', 'angles ', 'angleX ', 'angleY ', 'angleZ ', 'scale ', 'm_bSnapToTerrain ')) or token == 'm_bPlayable 1':
            out.append(line)
        else:
            raise ValueError('group field needs review: ' + token.split(' ')[0])
        i += 1
    if not roster:
        raise ValueError('empty group roster')
    for member in roster:
        if member.upper() in reviewed_members or member.upper() in converted_wrappers:
            continue
        if not (legacy_member(source_units, member, force)
                or (force and inherits_legacy_playable_base(source_units, member, stock))):
            raise ValueError('legacy slot playability requires review ' + member)
        only_path(dev_units, member)
    if not native_playable:
        metadata.append(indent + 'm_bPlayable 1')
    return out + metadata, len(roster)


def convert_stock_instance(lines, spec, source_units, dev_units, stock, locally_owned):
    roster = list(spec['slots'])
    placement = []
    depth = min((len(line) - len(line.lstrip()) for line in lines if line.strip()), default=1)
    indent = ' ' * depth
    index = 0
    while index < len(lines):
        line = lines[index]
        slot_block = re.fullmatch(re.escape(indent) + r'm_aUnitPrefabSlots (\+ ?)?\{', line)
        if not slot_block:
            placement.append(line)
            index += 1
            continue
        end = block_end(lines, index)
        values = '\n'.join(lines[index + 1:end])
        if REF.sub('', values).strip():
            raise ValueError('unsupported stock wrapper slot list')
        if not slot_block[1]:
            roster.clear()
        roster.extend(REF.findall(values))
        index = end + 1
    verified = {}
    rendered = []
    for guid, path in roster:
        parent = stock_character(guid, path, stock)
        if not parent:
            raise ValueError('unverified stock wrapper character ' + guid)
        resource, asset = parent
        verified[resource.upper()] = asset
        rendered.append(f'{indent} "{{{resource}}}{asset}"')
    placement.extend([indent + 'm_aUnitPrefabSlots {', *rendered, indent + '}', indent + 'm_bSetPlayable 1'])
    return convert_instance(placement, spec['parent'][0], '', '', source_units, dev_units, stock, locally_owned,
                            reviewed_members=verified)


def group_layer(text, source_units, dev_units, stock, locally_owned, reviewed_callsigns=frozenset(),
                reviewed_members=None, external_ai=None):
    if not re.search(r'^(?:\$grp )?SCR_AIGroup\b', text, re.M):
        return None
    lines = text.splitlines()
    result = []
    count = slots = i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        match = ROOT.fullmatch(lines[i])
        if not match:
            raise ValueError('mixed layer or custom group declaration')
        guid = match['guid'].upper()
        if guid in locally_owned:
            raise ValueError('mission-owned group ' + guid)
        spec = WRAPPERS['groups'].get(guid) if guid not in source_units.resources and stock else None
        if spec:
            if REF.search(lines[i])[2].casefold() != spec['path'].casefold() or not stock.contains(*spec['parent']):
                raise ValueError('unverified stock wrapper group ' + guid)
            resource, dest = spec['parent']
            convert = partial(convert_stock_instance, spec=spec, source_units=source_units, dev_units=dev_units,
                              stock=stock, locally_owned=locally_owned)
        else:
            _, old = prefab(source_units, guid)
            dest, new = prefab(dev_units, guid)
            resource = guid
            convert = partial(convert_instance, guid=guid, old=old, new=new, source_units=source_units,
                              dev_units=dev_units, stock=stock, locally_owned=locally_owned,
                              reviewed_callsigns=reviewed_callsigns, reviewed_members=reviewed_members,
                              external_ai=external_ai)
        result.append(REF.sub(lambda _: '"{' + resource + '}' + dest + '"', lines[i], count=1))
        end = block_end(lines, i)
        if match['group']:
            j = i + 1
            while j < end:
                if not re.fullmatch(r' (?:[\w.\-]+ )?\{', lines[j]):
                    raise ValueError('unexpected group instance')
                last = block_end(lines, j)
                body, members = convert(lines[j+1:last])
                result.extend([lines[j], *body, lines[last]])
                slots += members
                count += int(members > 0)
                j = last + 1
        else:
            body, members = convert(lines[i+1:end])
            result.extend(body)
            slots += members
            count += int(members > 0)
        result.append(lines[end])
        i = end + 1
    return '\n'.join(result) + '\n', count, slots
