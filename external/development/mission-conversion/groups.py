"""Convert complete layers of playable groups backed by both revisions of GC Units."""
from functools import lru_cache
import re

from briefings import block_end
from catalog import REF

ROOT = re.compile(r'^(?P<group>\$grp )?SCR_AIGroup(?: [\w.\-]+)? : "\{(?P<guid>[A-Fa-f0-9]{16})\}[^"]+\.et" \{$')
CALL = re.compile(r'^\s*PS_GroupCallsignAssigner "\{([\da-f]{16})\}" \{$', re.I)
STANDARD_CALLSIGN = '63616C6C7369676E'
INDEX = {'m_iCompanyCallsign': 'm_iGC_CallsignCompany', 'm_iPlatoonCallsign': 'm_iGC_CallsignPlatoon',
         'm_iSquadCallsign': 'm_iGC_CallsignSquad'}
SLOTS = re.compile(r'^ m_aUnitPrefabSlots \{\n(.*?)^ \}', re.M | re.S)


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


def convert_instance(lines, guid, old, new, source_units, dev_units, stock, locally_owned,
                     reviewed_callsigns=frozenset(), reviewed_members=None):
    reviewed_members = reviewed_members or {}
    explicit_force = any(l.strip() == 'm_bSetPlayable 1' for l in lines)
    native_playable = any(l.strip() == 'm_bPlayable 1' for l in lines) or scalar(inheritance(dev_units, guid), 'm_bPlayable') == 1
    if not native_playable and not explicit_force:
        raise ValueError('parent playability requires inheritance or member review')
    replace_slots = any(l.strip() == 'm_aUnitPrefabSlots {' for l in lines)
    roster = []
    if not replace_slots:
        old_slots, new_slots = SLOTS.search(old), SLOTS.search(new)
        if not old_slots or not new_slots:
            raise ValueError('inherited slot roster requires comparison')
        roster = [g for g, _ in REF.findall(old_slots[1])]
        if roster != [g for g, _ in REF.findall(new_slots[1])]:
            raise ValueError('inherited slot roster differs')
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
            assigned = False
            while j < end:
                last = block_end(lines, j)
                match = CALL.fullmatch(lines[j])
                if match and (match[1].upper() == STANDARD_CALLSIGN or match[1].upper() in reviewed_callsigns):
                    if assigned and not reviewed_callsigns:
                        raise ValueError('ambiguous duplicate callsign component')
                    placed = callsign(lines[j+1:last])
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
                    if not assigned:
                        metadata.extend(indent + f'{dest} "{effective[k]}"' for k, dest in INDEX.items())
                    assigned = True
                elif re.fullmatch(r'AIFormationComponent "\{[\da-fA-F]{16}\}" \{', lines[j].strip()):
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
                elif not stock or not stock.contains(guid, path):
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
        if member.upper() in reviewed_members:
            continue
        if not legacy_member(source_units, member, force):
            raise ValueError('legacy slot playability requires review ' + member)
        only_path(dev_units, member)
    if not native_playable:
        metadata.append(indent + 'm_bPlayable 1')
    return out + metadata, len(roster)


def group_layer(text, source_units, dev_units, stock, locally_owned, reviewed_callsigns=frozenset(),
                reviewed_members=None):
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
        _, old = prefab(source_units, guid)
        dest, new = prefab(dev_units, guid)
        result.append(REF.sub(lambda _: '"{' + guid + '}' + dest + '"', lines[i], count=1))
        end = block_end(lines, i)
        if match['group']:
            j = i + 1
            while j < end:
                if not re.fullmatch(r' (?:[\w.\-]+ )?\{', lines[j]):
                    raise ValueError('unexpected group instance')
                last = block_end(lines, j)
                body, members = convert_instance(lines[j+1:last], guid, old, new, source_units,
                                                 dev_units, stock, locally_owned, reviewed_callsigns, reviewed_members)
                result.extend([lines[j], *body, lines[last]])
                slots += members
                count += 1
                j = last + 1
        else:
            body, members = convert_instance(lines[i+1:end], guid, old, new, source_units,
                                             dev_units, stock, locally_owned, reviewed_callsigns, reviewed_members)
            result.extend(body)
            slots += members
            count += 1
        result.append(lines[end])
        i = end + 1
    return '\n'.join(result) + '\n', count, slots
