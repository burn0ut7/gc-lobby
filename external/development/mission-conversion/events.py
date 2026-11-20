"""Convert one-shot, unconditional game-phase messages with their own instruction delays."""
from collections import defaultdict
from decimal import Decimal
import re

from briefings import block_end, guid, properties, QUOTED

EVENT = re.compile(r'^  TILW_MissionEvent "\{([\da-fA-F]{16})\}" \{$')
MESSAGE = re.compile(r'^    TILW_SendMessageInstruction "\{([\da-fA-F]{16})\}" \{$')
LITERAL = re.compile(r'^   m_condition TILW_LiteralTerm "\{[\da-fA-F]{16}\}" \{$')
FIELDS = {'m_messageTitle', 'm_messageBody', 'm_factionKeys', 'm_adminOnly', 'm_executionDelay'}


def message(lines, key):
    data = properties(lines, FIELDS)
    title = data.get('m_messageTitle', '""')
    body = data.get('m_messageBody', '""')
    for text, limit in ((title, 80), (body, 512)):
        if not re.fullmatch(QUOTED, text) or len(text.encode()) - 2 > limit or '<' in text or '>' in text:
            raise ValueError('message formatting or length needs review')
    if not body[1:-1].strip():
        raise ValueError('empty notification')
    factions = data.get('m_factionKeys', '')
    admin = data.get('m_adminOnly', '0')
    if admin not in ('0', '1') or (admin == '1' and factions):
        raise ValueError('combined faction and admin audience')
    if factions and (not re.fullmatch(r'(?:\s*"[\w.-]+"\s*)+', factions) or factions.count('"') > 512):
        raise ValueError('unsupported faction list')
    seconds = data.get('m_executionDelay', '5')
    if not re.fullmatch(r'\d+(\.\d+)?', seconds) or Decimal(seconds) > 86400:
        raise ValueError('instruction delay outside native range')
    action = [f'      GC_MessageAction "{{{guid(key)}}}" {{', '       m_sTitle ' + title,
              '       m_sMessage ' + body]
    if admin == '1':
        action.append('       m_iDelivery 1')
    if factions:
        action.extend(['       m_aFactions {', '        ' + factions, '       }'])
    return Decimal(seconds), action + ['      }']


def schedule(lines, start, end, key, ordinal):
    actions = defaultdict(list)
    label = 'Match notification'
    condition = False
    seen = set()
    i = start + 1
    while i < end:
        field = lines[i].strip().split(' ', 1)[0]
        if field in seen:
            raise ValueError('duplicate event field ' + field)
        seen.add(field)
        if LITERAL.fullmatch(lines[i]):
            stop = block_end(lines, i)
            values = properties(lines[i+1:stop], {'m_invertTerm', 'm_flagName'})
            if values.get('m_invertTerm') != '1' or values.get('m_flagName', '""') != '""':
                raise ValueError('depends on a mission flag')
            condition = True
            i = stop
        elif lines[i] == '   m_instructions {':
            stop = block_end(lines, i)
            j = i + 1
            while j < stop:
                match = MESSAGE.fullmatch(lines[j])
                if not match:
                    raise ValueError('instruction needs conversion: ' + lines[j].strip().split(' ')[0])
                last = block_end(lines, j)
                delay, action = message(lines[j+1:last], key + '/' + match[1])
                actions[delay].extend(action)
                j = last + 1
            i = stop
        elif re.fullmatch('   m_name ' + QUOTED, lines[i]):
            label = lines[i].strip()[len('m_name "'):-1] or label
        elif lines[i] != '   m_pregameEvent 0':
            raise ValueError('event field needs conversion: ' + field)
        i += 1
    if not condition or not actions:
        raise ValueError('missing unconditional trigger or actions')
    result = []
    for index, (delay, bodies) in enumerate(actions.items()):
        event_id = guid(key + '/' + str(index))
        result.extend([f'    GC_Event "{{{event_id}}}" {{', f'     m_sName "{label} (event {ordinal}, {delay:f}s)"'])
        if delay:
            result.extend([f'     m_Advanced GC_EventOptions "{{{guid(event_id)}}}" {{',
                           f'      m_fDelay {delay:f}', '     }'])
        result.extend(['     m_aActions {', *bodies, '     }', '    }'])
    return result, list(map(str, actions))


def world_events(source, world):
    layers = source.layers[world.lower()]
    owners = [(p, source.read(p)) for p in layers if 'TILW_MissionFrameworkEntity' in source.read(p)]
    if not owners:
        return [], {}
    audit, compiled = {}, []
    local = [p for p in source.closure(world) if p.lower().endswith(('.layer', '.et'))]
    indirect = any(re.search(r'\bTILW_(?:RunEvents|ReactivateEvents|DeactivateEvents)Instruction\b', source.read(p)) for p in local)
    for path, text in owners:
        lines = text.splitlines()
        owner = collection = ''
        ordinal = 0
        for i, line in enumerate(lines):
            if line and not line.startswith(' '):
                owner, collection = line, ''
            elif re.match(r'^ [^ ]', line):
                collection = line
            match = EVENT.fullmatch(line)
            if not match:
                continue
            loc = f'{path}:{i+1}'
            ordinal += 1
            try:
                if collection != ' m_missionEvents {' or not re.match(r'^TILW_MissionFrameworkEntity(?: [\w.-]+)? : "\{8F846D0FD5D6EA51\}', owner):
                    raise ValueError('custom framework or event collection')
                if len(owners) != 1 or indirect:
                    raise ValueError('multiple framework layers or event control instructions')
                events, delays = schedule(lines, i, block_end(lines, i), world + '/' + match[1], ordinal)
                audit[loc] = {'notificationDelaysSeconds': delays}
                compiled.extend(events)
            except ValueError as exc:
                audit[loc] = {'gap': str(exc)}
    return compiled, audit


def peak_event(key, faction, ratio, delay, action):
    action_text = '\n'.join(action)
    name = f'{faction} reached casualty threshold'
    return f'''    GC_Event "{{{guid(key + '/trigger')}}}" {{
     m_sName "{name}"
     m_aConditions {{
      GC_CustomScriptCondition "{{{guid(key + '/condition')}}}" {{
       m_Script GC_PeakFactionPopulationCheck "{{{guid(key + '/check')}}}" {{
        m_sFaction "{faction}"
        m_fRatio {ratio}
       }}
      }}
     }}
     m_aActions {{
      GC_MessageAction "{{{guid(key + '/log')}}}" {{
       m_sMessage "{name}"
       m_iDelivery 2
      }}
     }}
    }}
    GC_Event "{{{guid(key + '/notify')}}}" {{
     m_sName "{name} notification"
     m_aConditions {{
      GC_MissionEventCondition "{{{guid(key + '/latch')}}}" {{
       m_sEventName "{name}"
      }}
     }}
     m_Advanced GC_EventOptions "{{{guid(key + '/delay')}}}" {{
      m_fDelay {delay}
     }}
     m_aActions {{
{action_text}
     }}
    }}
'''


PEAK_FLAG = re.compile(r'^  TILW_FactionPlayersKilledFlag "\{[\da-fA-F]{16}\}" \{$')


def peak_notifications(lines, world):
    flags = {}
    for start, line in enumerate(lines):
        if not PEAK_FLAG.fullmatch(line):
            continue
        stop = block_end(lines, start)
        data = properties(lines[start + 1:stop], {'m_flagName', 'm_factionKey', 'm_casualtyRatio'})
        flag, faction = [data.get(field, '') for field in ('m_flagName', 'm_factionKey')]
        ratio = data.get('m_casualtyRatio', '1')
        if (flag in flags or not re.fullmatch(r'"[\w.-]+"', flag)
                or not re.fullmatch(r'"[\w.-]+"', faction)
                or not re.fullmatch(r'\d+(?:\.\d+)?', ratio) or not 0 <= Decimal(ratio) <= 1):
            raise ValueError('unsupported or duplicate peak population flag')
        flags[flag] = faction[1:-1], ratio

    rules = []
    for start, line in enumerate(lines):
        event = EVENT.fullmatch(line)
        if not event:
            continue
        stop = block_end(lines, start)
        fields = lines[start + 1:stop]
        if fields and re.fullmatch('   m_name ' + QUOTED, fields[0]):
            fields = fields[1:]
        if len(fields) < 7 or fields[0] != '   m_instructions {' or not MESSAGE.fullmatch(fields[1]):
            continue
        last_message = block_end(fields, 1)
        condition = last_message + 2
        if (len(fields) != condition + 3 or fields[last_message + 1] != '   }'
                or not LITERAL.fullmatch(fields[condition]) or fields[-1] != '   }'):
            continue
        flag = fields[condition + 1].removeprefix('    m_flagName ')
        if flag not in flags:
            continue

        faction, ratio = flags[flag]
        key = world + '/casualty/' + event[1]
        delay, action = message(fields[2:last_message], key + '/message')
        rules.append({'sourceLine': start + 1, 'faction': faction, 'ratio': ratio,
                      'delaySeconds': str(delay), 'events': peak_event(key, faction, ratio, delay, action)})
    if len({rule['faction'] for rule in rules}) != len(rules):
        raise ValueError('multiple peak notifications for a faction need separate trigger names')
    return rules
