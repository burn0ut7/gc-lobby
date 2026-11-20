"""Stage independently scheduled actions for supported legacy framework events."""
from collections import defaultdict
from decimal import Decimal
import re

from briefings import block_end, guid, properties
from event_actions import compile_action, recognized
from events import EVENT, LITERAL, MESSAGE, message, peak_flags

FRAMEWORK = re.compile(r'^TILW_MissionFrameworkEntity(?: [\w.-]+)? : "\{8F846D0FD5D6EA51\}[^"]+" \{$')
ENDING = re.compile(r'^    TILW_EndGameInstruction "\{([\da-fA-F]{16})\}" \{$')
WAYPOINTS = re.compile(r'^    TILW_AssignWaypointsInstruction "\{([\da-fA-F]{16})\}" \{$')
KEY = r'"[\w.-]+"'
EVENT_LABEL = r'"[^"\\\x00-\x1f]+"'
RESULT = {'EDITOR_FACTION_VICTORY': 1, 'EDITOR_FACTION_DEFEAT': 2, 'EDITOR_FACTION_DRAW': 3}
TERM = re.compile(r'\s*(?:m_condition )?TILW_(Literal|Conjunction|Disjunction)Term "\{[\da-fA-F]{16}\}" \{')
AI_FLAG = re.compile(r'^  TILW_FactionAIKilledFlag "\{[\da-fA-F]{16}\}" \{$')


def end_action(lines, key):
    data = properties(lines, {'m_factionKey', 'm_gameOverType', 'm_executionDelay'})
    faction = data.get('m_factionKey', '')
    kind = data.get('m_gameOverType', 'EDITOR_FACTION_VICTORY')
    delay = data.get('m_executionDelay', '5')
    if (not re.fullmatch(KEY, faction) or kind not in RESULT
            or not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400):
        raise ValueError('unsupported faction outcome or delay')
    return Decimal(delay), [
        f'      GC_EndMissionAction "{{{guid(key)}}}" {{', f'       m_iResult {RESULT[kind]}',
        '       m_bAnnounceOnly 1', '       m_aWinningFactions {', f'        {faction}', '       }', '      }',
    ], {'faction': faction[1:-1], 'result': kind, 'delaySeconds': delay}


def waypoint_action(lines, key):
    data = properties(lines, {'m_groupName', 'm_waypointNames', 'm_clearExisting', 'm_executionDelay'})
    group = data.get('m_groupName', '')
    names = data.get('m_waypointNames', '').split()
    replace = data.get('m_clearExisting', '0')
    delay = data.get('m_executionDelay', '5')
    if (not re.fullmatch(KEY, group) or not all(re.fullmatch(KEY, name) for name in names)
            or len(names) > 128 or replace not in ('0', '1') or (not names and replace == '0')
            or not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400):
        raise ValueError('unsupported waypoint assignment')
    body = [f'      GC_AssignWaypointsAction "{{{guid(key)}}}" {{', f'       m_sGroupName {group}']
    if replace == '1':
        body.append('       m_bReplaceExisting 1')
    if names:
        body.extend(['       m_aWaypoints {', *(f'        {name}' for name in names), '       }'])
    details = {'group': group[1:-1], 'waypoints': [name[1:-1] for name in names],
               'replaceExisting': replace == '1', 'delaySeconds': delay}
    return Decimal(delay), [*body, '      }'], details


def read_term(lines, flags, captures, ai, depth=0):
    match = TERM.fullmatch(lines[0])
    if not match or depth > 8:
        raise ValueError('unsupported outcome term')
    if match[1] == 'Literal':
        values = properties(lines[1:-1], {'m_flagName', 'm_invertTerm'})
        flag = values.get('m_flagName', '""')
        invert = values.get('m_invertTerm', '0')
        if flag == '""' and invert == '1' and depth == 0:
            return {'trigger': 'Match start'}
        if invert == '0' and flag in flags:
            faction, ratio = flags[flag]
            return {'trigger': 'peak player loss', 'flag': flag[1:-1], 'faction': faction, 'ratio': ratio}
        if invert == '0' and flag in ai:
            faction, ratio = ai[flag]
            return {'trigger': 'AI loss', 'flag': flag[1:-1], 'faction': faction, 'ratio': ratio}
        if invert == '0' and flag in captures:
            return {'trigger': 'area capture', 'flag': flag[1:-1]}
        raise ValueError('unresolved or unsupported outcome flag ' + flag)
    if len(lines) < 4 or lines[1].strip() != 'm_operands {' or block_end(lines, 1) != len(lines) - 2:
        raise ValueError('unsupported compound outcome term fields')
    index, operands = 2, []
    while index < len(lines) - 2:
        end = block_end(lines, index)
        operands.append(read_term(lines[index:end + 1], flags, captures, ai, depth + 1))
        index = end + 1
    if not 1 <= len(operands) <= 64:
        raise ValueError('unsupported number of outcome operands')
    return {'trigger': 'all' if match[1] == 'Conjunction' else 'any', 'operands': operands}


def read_event(fields, key, flags, captures, ai, compound):
    actions = defaultdict(list)
    notices = []
    assignments = []
    converted_actions = []
    messages = 0
    seen = set()
    term = None
    i = 0
    while i < len(fields):
        line = fields[i]
        field = line.strip().partition(' ')[0]
        if field in seen:
            raise ValueError('duplicate event field: ' + field)
        seen.add(field)
        if LITERAL.fullmatch(line) or (compound and field == 'm_condition' and TERM.fullmatch(line)):
            end = block_end(fields, i)
            term = read_term(fields[i:end + 1], flags, captures, ai)
            i = end
        elif line == '   m_instructions {':
            end = block_end(fields, i)
            j = i + 1
            while j < end:
                last = block_end(fields, j)
                notify_match = MESSAGE.fullmatch(fields[j])
                end_match = ENDING.fullmatch(fields[j])
                waypoint_match = WAYPOINTS.fullmatch(fields[j]) if compound else None
                action_match = recognized(fields[j]) if compound else None
                if notify_match:
                    delay, body = message(fields[j + 1:last], key + '/' + notify_match[1], multiline=True)
                    messages += 1
                elif end_match:
                    delay, body, notice = end_action(fields[j + 1:last], key + '/' + end_match[1])
                    notices.append(notice)
                elif waypoint_match:
                    delay, body, assignment = waypoint_action(fields[j + 1:last], key + '/' + waypoint_match[1])
                    assignments.append(assignment)
                elif action_match:
                    delay, body, details = compile_action(action_match, fields[j + 1:last], key)
                    converted_actions.append(details)
                else:
                    raise ValueError('unsupported outcome instruction ' + fields[j].strip().split(' ')[0])
                actions[delay].extend(body)
                j = last + 1
            i = end
        elif not re.fullmatch(r'   m_name ' + (EVENT_LABEL if compound else KEY), line) and line != '   m_pregameEvent 0':
            raise ValueError('unsupported outcome event field ' + field)
        i += 1
    if not term or not (notices or assignments or converted_actions or (compound and messages)):
        raise ValueError('missing condition or supported framework instruction')
    if assignments and term['trigger'] != 'Match start':
        raise ValueError('waypoint assignments after Match start require a separate rule')
    return term, actions, notices, assignments, messages, converted_actions


def leaves(term):
    if 'operands' in term:
        for item in term['operands']:
            yield from leaves(item)
    else:
        yield term


def render_check(term, key, captures, capture_messages):
    trigger = term['trigger']
    if trigger == 'area capture':
        flag = '"' + term['flag'] + '"'
        lines = captures[flag]
        # Each occurrence needs its own subtree IDs. A legacy trigger emits status only once.
        if capture_messages is not None:
            for index, line in enumerate(lines):
                if line.lstrip().startswith('m_Messages '):
                    if flag in capture_messages:
                        lines = lines[:index] + lines[block_end(lines, index) + 1:]
                    capture_messages.add(flag)
                    break
        return [re.sub(r'"\{([0-9A-F]{16})\}"', lambda match: '"{' + guid(key + '/' + match[1]) + '}"', line)
                for line in lines]
    if trigger in ('all', 'any'):
        result = [f'      GC_ConditionGroup "{{{guid(key + "/group")}}}" {{',
                  f'       m_iMatch {int(trigger == "any")}', '       m_aConditions {']
        for index, child in enumerate(term['operands']):
            result.extend('  ' + line for line in render_check(child, f'{key}/{index}', captures, capture_messages))
        return result + ['       }', '      }']
    check = 'GC_LivingAndDeadAICheck' if trigger == 'AI loss' else 'GC_PeakFactionPopulationCheck'
    result = [f'      GC_CustomScriptCondition "{{{guid(key + "/condition")}}}" {{',
              f'       m_Script {check} "{{{guid(key + "/check")}}}" {{']
    if trigger != 'AI loss':
        result.append(f'        m_sFaction "{term["faction"]}"')
    return result + [f'        m_fRatio {term["ratio"]}', '       }', '      }']


def render(key, name, term, actions, captures, capture_messages):
    condition = term['trigger'] != 'Match start'
    result = []
    if condition:
        if term['trigger'] == 'area capture' and capture_messages is None:
            check = captures['"' + term['flag'] + '"']
        else:
            check = render_check(term, key, captures, capture_messages)
        result.extend([
            f'    GC_Event "{{{guid(key + "/trigger")}}}" {{', f'     m_sName "{name}"',
            '     m_aConditions {', *check, '     }',
            '     m_aActions {', f'      GC_MessageAction "{{{guid(key + "/log")}}}" {{',
            f'       m_sMessage "{name}"', '       m_iDelivery 2', '      }', '     }', '    }',
        ])
    for delay, body in actions.items():
        delivery = key + '/' + str(delay)
        result.extend([f'    GC_Event "{{{guid(delivery)}}}" {{', f'     m_sName "{name} ({delay:f}s)"'])
        if condition:
            result.extend(['     m_aConditions {', f'      GC_MissionEventCondition "{{{guid(delivery + "/latch")}}}" {{',
                           f'       m_sEventName "{name}"', '      }', '     }'])
        if delay:
            result.extend([f'     m_Advanced GC_EventOptions "{{{guid(delivery + "/delay")}}}" {{',
                           f'      m_fDelay {delay:f}', '     }'])
        result.extend(['     m_aActions {', *body, '     }', '    }'])
    return result


def compile_outcomes(source, world, captures=None, *, compound=False, notification_audit=None):
    captures = captures or {}
    notification_audit = notification_audit or {}
    layers = source.layers[world.lower()]
    owners = [(path, source.read(path)) for path in layers if 'TILW_MissionFrameworkEntity' in source.read(path)]
    if len(owners) != 1:
        return [], {}, 'requires one reviewed framework owner'
    path, text = owners[0]
    local = [p for p in source.closure(world) if p.lower().endswith(('.et', '.layer'))]
    if any(re.search(r'TILW_(?:RunEvents|ReactivateEvents|DeactivateEvents)Instruction\b', source.read(p)) for p in local):
        return [], {}, 'has legacy event control instructions'
    lines = text.splitlines()
    roots = [i for i, line in enumerate(lines) if FRAMEWORK.fullmatch(line)]
    if len(roots) != 1:
        return [], {}, 'custom framework placement'
    base = roots[0]
    stop = block_end(lines, base)
    body = lines[base + 1:stop]
    if any(line.strip() == 'm_suppressGameEnd 0' for line in body):
        return [], {}, 'instance ends the mission and needs a separate runtime port'
    flags = peak_flags(body)
    ai = peak_flags(body, AI_FLAG) if compound else {}
    if set(ai) & set(flags) or len({faction for faction, _ in ai.values()}) > 1:
        return [], {}, 'ambiguous AI casualty faction tracking'
    compiled, audit = [], {}
    names = set()
    capture_messages = set() if compound else None
    for i, line in enumerate(body):
        event = EVENT.fullmatch(line)
        if not event:
            continue
        fields = body[i + 1:block_end(body, i)]
        loc = f'{path}:{i + base + 2}'
        if 'notificationDelaysSeconds' in notification_audit.get(loc, {}):
            continue
        if not any(ENDING.fullmatch(item) or (compound and (WAYPOINTS.fullmatch(item) or MESSAGE.fullmatch(item)
                                                           or recognized(item)))
                   for item in fields):
            continue
        name = next((item.strip().removeprefix('m_name ') for item in fields if item.startswith('   m_name ')), '""')
        if not re.fullmatch(EVENT_LABEL if compound else KEY, name):
            name = '"Outcome ' + event[1] + '"'
        name = name[1:-1]
        try:
            term, actions, notices, assignments, messages, converted_actions = read_event(
                fields, world + '/' + event[1], flags, captures, ai, compound)
            emitted_names = {f'{name} ({delay:f}s)' for delay in actions}
            if term['trigger'] != 'Match start':
                emitted_names.add(name)
            if emitted_names & names:
                raise ValueError('duplicate generated event name')
            compiled.extend(render(world + '/' + event[1], name, term, actions, captures, capture_messages))
            audit[loc] = {**term, 'name': name, 'announcements': notices,
                          'instructionDelaysSeconds': [f'{delay:f}' for delay in actions]}
            if assignments:
                audit[loc]['waypointAssignments'] = assignments
            if converted_actions:
                audit[loc]['convertedActions'] = converted_actions
            if messages and not (notices or assignments or converted_actions):
                audit[loc]['notificationCount'] = messages
            names.update(emitted_names)
        except ValueError as exc:
            audit[loc] = {'name': name, 'gap': str(exc)}
    return compiled, audit, None
