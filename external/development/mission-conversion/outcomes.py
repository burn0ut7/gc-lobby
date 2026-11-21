"""Stage independently scheduled actions for supported legacy framework events."""
from collections import Counter, defaultdict
from decimal import Decimal
from functools import partial
from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath
import re

from briefings import block_end, guid, properties
from catalog import SOURCE_TEXT
from event_actions import GROUP_SPAWN, SPAWN, VEHICLE_SPAWN, compile_action, prefab_spawn, recognized, render_spawn, vehicle_spawn
from events import EVENT, LITERAL, MESSAGE, message, peak_flags

FRAMEWORK = re.compile(r'^TILW_MissionFrameworkEntity(?: [\w.-]+)? : "\{8F846D0FD5D6EA51\}[^"]+" \{$')
ENDING = re.compile(r'^    TILW_EndGameInstruction "\{([\da-fA-F]{16})\}" \{$')
WAYPOINTS = re.compile(r'^    TILW_AssignWaypointsInstruction "\{([\da-fA-F]{16})\}" \{$')
DEACTIVATE = re.compile(r'^    TILW_DeactivateEventsInstruction "\{([\da-fA-F]{16})\}" \{$')
EVENT_CONTROL = re.compile(r'\bTILW_(RunEvents|ReactivateEvents|DeactivateEvents)Instruction\b')
KEY = r'"[\w.-]+"'
EVENT_LABEL = r'"[^"\\\x00-\x1f]+"'
RESULT = {'EDITOR_FACTION_VICTORY': 1, 'EDITOR_FACTION_DEFEAT': 2, 'EDITOR_FACTION_DRAW': 3}
# The legacy suppressed ending uses only the title, which is the same for
# the timed and editor variants in BaseGameOverScreensConfig.
ANNOUNCEMENT_RESULTS = {'FACTION_DEFEAT_TIME': 2}
TERM = re.compile(
    r'\s*(?:m_(?:condition|definition) )?TILW_(Literal|Conjunction|Disjunction|Minjunction|Maxjunction|Matchjunction)'
    r'Term "\{[\da-fA-F]{16}\}" \{')
COUNT_FIELDS = {'Minjunction': 'm_minTrue', 'Maxjunction': 'm_maxTrue', 'Matchjunction': 'm_matchTrue'}
AI_FLAG = re.compile(r'^  TILW_FactionAIKilledFlag "\{[\da-fA-F]{16}\}" \{$')
FLAG_ACTION = re.compile(r'^    TILW_(Set|Clear)FlagInstruction "\{[\da-fA-F]{16}\}" \{$')
FLAG_FIELD = re.compile(r'^\s+m_flagName (' + KEY + r')\s*$')
META_FLAG = re.compile(r'^  TILW_MetaFlag "\{[\da-fA-F]{16}\}" \{$')
META_FLAG_FIELD = re.compile(r'^\s+m_flagName (' + EVENT_LABEL + r')\s*$')
SCRIPT_FLAG_APIS = ('AdjustMissionFlag', 'IsMissionFlag', 'GetFlagSetCopy', 'm_flagSet')
# The reviewed source produces US_KIA, while this notice consumes USMC_KIA.
DORMANT_NOTICES = {
    ('a75e404c331abce5185f315f47f533d9b8eccf00',
     'worlds/Jub/MissionWithAssets/MissionWithAssets_Layers/default.layer',
     '65DAD44B20B959A8'): '"USMC_KIA"',
}
REVIEWED_INACTIVE_FLAGS = {
    'worlds/HitTheMan/VDV Hello From The Sky/VDVHelloFromTheSky_Layers/default.layer':
        ('ec2d8e9742da07dccacdb9f37beebfb23449e213', '"Industrial"', 2),
    'worlds/RoseOutTheAshes/FIALeMouleRaid/LeMouleRaid_layers/fw.layer':
        ('bff731fee3984b1ab9eb50b692dc2cd841feeb17', '"Sawmill_Held"', 1),
    'worlds/Til/HueyHill/HueyHill_Layers/default.layer':
        ('d1ddf95704da3f6c1fd51954c546639f7dc5917e', '"TruckDestroyed"', 1),
    'worlds/Til/RoutePierre/RoutePierre_Layers/default.layer':
        ('7fecb24882a1c6a5b22b7ab2e78d8c6b0685f040', '"23"', 2),
    'worlds/Wade/NightSeizure/NightSeizure_Layers/default.layer':
        ('1db6432dc54bc23c48b8c94d1d310657fc473ee2', '"Intel"', 2),
}


def inactive_flags(source, world, path, scripts):
    reference = REVIEWED_INACTIVE_FLAGS.get(path)
    if not reference or scripts is None or any(api in scripts for api in SCRIPT_FLAG_APIS):
        return ()
    blob, flag, uses = reference
    if source.files[path] != blob or flag in scripts:
        return ()
    if sum(source.read(p).count(flag) for p in source.closure(world)
           if PurePosixPath(p).suffix.lower() in SOURCE_TEXT) != uses:
        return ()
    return (flag,)


def instruction_flags(source, world, framework_path, lines, scripts, flag_reads):
    """Find flags whose complete authored use is covered by conversion rules."""
    flags = set()
    known_uses = defaultdict(int)
    for start, line in enumerate(lines):
        action = FLAG_ACTION.fullmatch(line)
        is_literal = line.startswith('   ') and TERM.fullmatch(line)
        if not action and (not is_literal or is_literal[1] != 'Literal'):
            continue
        end = block_end(lines, start)
        body = lines[start + 1:end]
        names = [match[1] for item in body if (match := FLAG_FIELD.fullmatch(item))]
        if len(names) != 1:
            continue
        if action and action[1] == 'Set':
            flags.add(names[0])
        if not action and any(item.strip() not in ('m_invertTerm 0', 'm_invertTerm 1')
                              for item in body if not FLAG_FIELD.fullmatch(item)):
            continue
        known_uses[names[0]] += 1
    if not flags or any(api in scripts for api in SCRIPT_FLAG_APIS):
        return ()
    corpus = '\n'.join('\n'.join(line for line in source.read(path).splitlines()
                                if not (path == framework_path and line.startswith('   m_name ')))
                       for path in sorted(source.closure(world))
                       if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    return tuple(sorted(flag[1:-1] for flag in flags
                        if flag not in scripts
                        and corpus.count(flag) == known_uses[flag] + flag_reads.get(flag[1:-1], 0)))


def derived_flags(source, world, framework_path, body, scripts):
    """Read pure derived flags whose authored references are all literal terms."""
    starts = [i for i, line in enumerate(body) if line == ' m_metaFlags {']
    if not starts:
        return {}
    if len(starts) != 1 or scripts is None or any(api in scripts for api in SCRIPT_FLAG_APIS):
        raise ValueError('meta flag ownership requires review')
    start = starts[0]
    end = block_end(body, start)
    definitions = {}
    cursor = start + 1
    while cursor < end:
        if not META_FLAG.fullmatch(body[cursor]):
            raise ValueError('unsupported meta flag placement')
        stop = block_end(body, cursor)
        fields = body[cursor + 1:stop]
        terms = [i for i, line in enumerate(fields)
                 if line.startswith('   m_definition ') and TERM.fullmatch(line)]
        if len(terms) != 1:
            raise ValueError('meta flag requires one supported definition')
        term = terms[0]
        close = block_end(fields, term)
        flag = properties(fields[:term] + fields[close + 1:], {'m_flagName'}).get('m_flagName', '')
        if not re.fullmatch(EVENT_LABEL, flag) or flag in definitions or flag in scripts:
            raise ValueError('invalid or script-managed meta flag ' + flag)
        definitions[flag] = fields[term:close + 1]
        cursor = stop + 1

    literal_uses = defaultdict(int)
    for index, line in enumerate(body):
        match = TERM.fullmatch(line)
        if not match or match[1] != 'Literal':
            continue
        stop = block_end(body, index)
        fields = body[index + 1:stop]
        for item in fields:
            if name := META_FLAG_FIELD.fullmatch(item):
                literal_uses[name[1]] += 1
    content = '\n'.join('\n'.join(line for line in source.read(path).splitlines()
                                  if not (path == framework_path and line.startswith('   m_name ')))
                        for path in sorted(source.closure(world))
                        if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    for flag in definitions:
        if content.count(flag) != literal_uses[flag] + 1:
            raise ValueError('meta flag has another producer or unsupported use: ' + flag)
    dependencies = {flag: {match[1] for line in lines if (match := META_FLAG_FIELD.fullmatch(line))
                           and match[1] in definitions}
                    for flag, lines in definitions.items()}
    try:
        tuple(TopologicalSorter(dependencies).static_order())
    except CycleError as exc:
        raise ValueError('cyclic meta flag definitions') from exc
    return definitions


def end_action(lines, key, *, extended_announcements=False):
    data = properties(lines, {'m_factionKey', 'm_gameOverType', 'm_executionDelay'})
    faction = data.get('m_factionKey', '')
    kind = data.get('m_gameOverType', 'EDITOR_FACTION_VICTORY')
    delay = data.get('m_executionDelay', '5')
    if not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400:
        raise ValueError('unsupported faction outcome or delay')
    if extended_announcements and kind == 'EDITOR_FACTION_NEUTRAL' and faction in ('', '""'):
        return Decimal(delay), [
            f'      GC_MessageAction "{{{guid(key)}}}" {{',
            '       m_iDelivery 2', '       m_sMessage "Neutral result reached."', '      }',
        ], {'faction': '', 'result': kind, 'delaySeconds': delay, 'delivery': 'server log'}
    if extended_announcements and faction in ('', '""') and kind in (
            'EDITOR_FACTION_VICTORY', 'EDITOR_FACTION_DRAW', 'ENDREASON_TIMELIMIT'):
        # The suppressed legacy instruction resolves the faction name before
        # displaying a hint. These instructions supply no faction key.
        return Decimal(delay), [], {
            'faction': '', 'result': kind, 'delaySeconds': delay, 'delivery': 'missing faction',
        }
    result = RESULT.get(kind)
    if result is None and extended_announcements:
        result = ANNOUNCEMENT_RESULTS.get(kind)
    if not re.fullmatch(KEY, faction) or result is None:
        raise ValueError('unsupported faction outcome or delay')
    return Decimal(delay), [
        f'      GC_EndMissionAction "{{{guid(key)}}}" {{', f'       m_iResult {result}',
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


def waypoint_trigger(term):
    if term.get('inverted'):
        return False
    if term['trigger'] in ('all', 'any'):
        return all(waypoint_trigger(operand) for operand in term['operands'])
    return term['trigger'] in ('Match start', 'dormant', 'area capture', 'interaction flag', 'event flag')


def deactivate_events(lines, key, named_events):
    data = properties(lines, {'m_eventNames', 'm_executionDelay'})
    names = data.get('m_eventNames', '')
    delay = data.get('m_executionDelay', '5')
    if (not re.fullmatch(rf'{EVENT_LABEL}(?:\s+{EVENT_LABEL})*', names)
            or not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400):
        raise ValueError('unsupported event deactivation targets or delay')
    targets = list(dict.fromkeys(re.findall(EVENT_LABEL, names)))
    if any(name[1:-1] != name[1:-1].strip() for name in targets) or len(targets) > 128:
        raise ValueError('unsupported event deactivation target names')
    actions, active, case_mismatches = [], [], []
    folded = Counter(name.casefold() for name in named_events)
    for index, target in enumerate(targets):
        name = target[1:-1]
        if name not in named_events and folded[name.casefold()] == 1:
            # A differently capitalized literal does not match the authored
            # name in the legacy instruction's string list.
            case_mismatches.append(name)
            continue
        active.append(name)
        actions.extend([f'      GC_EventControlAction "{{{guid(f"{key}/{index}")}}}" {{',
                        f'       m_sEventName {target}', '       m_iOperation 1', '      }'])
    return Decimal(delay), actions, {'instruction': 'TILW_DeactivateEventsInstruction',
                                    'deactivatesEvents': active, 'delaySeconds': delay,
                                    **({'caseMismatchedTargets': case_mismatches} if case_mismatches else {})}


def read_term(lines, flags, captures, ai, objective_flags, event_flags, depth=0, *,
              allow_dormant=False, derived=None, negate=False, inactive=()):
    match = TERM.fullmatch(lines[0])
    if not match or depth > 8:
        raise ValueError('unsupported outcome term')
    if match[1] == 'Literal':
        values = properties(lines[1:-1], {'m_flagName', 'm_invertTerm'})
        flag = values.get('m_flagName', '""')
        invert = values.get('m_invertTerm', '0')
        if negate and invert in ('0', '1'):
            invert = str(int(invert == '0'))
        if derived and flag in derived and invert in ('0', '1'):
            resolved = read_term(derived[flag], flags, captures, ai, objective_flags, event_flags, depth + 1,
                                 derived=derived, negate=invert == '1', inactive=inactive)
            return {**resolved, 'requiresMatch': True, 'derivedFlag': flag[1:-1]}
        if flag == '""' and invert == '1' and depth == 0:
            return {'trigger': 'Match start'}
        if flag == '""' and invert == '0' and allow_dormant and depth == 0:
            # Legacy AdjustMissionFlag rejects the empty string.
            return {'trigger': 'dormant'}
        if invert in ('0', '1') and flag in flags:
            faction, ratio = flags[flag]
            return {'trigger': 'peak player loss', 'flag': flag[1:-1], 'faction': faction, 'ratio': ratio,
                    **({'inverted': True} if invert == '1' else {})}
        if invert == '0' and flag in ai:
            faction, ratio = ai[flag]
            return {'trigger': 'AI loss', 'flag': flag[1:-1], 'faction': faction, 'ratio': ratio}
        if flag in captures:
            if invert == '0':
                return {'trigger': 'area capture', 'flag': flag[1:-1]}
            if invert == '1' and any(line.strip() == 'm_bStopAfterFirstChange 1' for line in captures[flag]):
                return {'trigger': 'area capture', 'flag': flag[1:-1], 'inverted': True}
            if invert == '1' and depth and any(line.strip() == 'm_fSeconds 0' for line in captures[flag]):
                return {'trigger': 'area capture', 'flag': flag[1:-1], 'inverted': True,
                        'liveCapture': True, 'requiresMatch': True}
        if invert in ('0', '1') and flag[1:-1] in objective_flags:
            return {'trigger': 'interaction flag', 'flag': flag[1:-1], **({'inverted': True} if invert == '1' else {})}
        if invert in ('0', '1') and flag[1:-1] in event_flags:
            return {'trigger': 'event flag', 'flag': flag[1:-1], **({'inverted': True} if invert == '1' else {})}
        if invert == '0' and flag in inactive:
            return {'trigger': 'inactive flag' if depth else 'dormant', 'flag': flag[1:-1],
                    'reason': 'no legacy flag producer', 'requiresMatch': True}
        raise ValueError('unresolved or unsupported outcome flag ' + flag)
    kind = match[1]
    threshold = COUNT_FIELDS.get(kind)
    indent = len(lines[0]) - len(lines[0].lstrip()) + 1
    start = next((i for i, line in enumerate(lines) if line == ' ' * indent + 'm_operands {'), None)
    if start is None:
        raise ValueError('unsupported compound outcome term fields')
    stop = block_end(lines, start)
    fields = properties([*lines[1:start], *lines[stop + 1:-1]], {'m_invertTerm'} | ({threshold} if threshold else set()))
    inverted = fields.get('m_invertTerm', '0')
    if inverted not in ('0', '1'):
        raise ValueError('invalid compound outcome inversion')
    if negate:
        inverted = str(int(inverted == '0'))
    index, operands = start + 1, []
    while index < stop:
        end = block_end(lines, index)
        if end >= stop:
            raise ValueError('invalid compound outcome operand list')
        operands.append(read_term(lines[index:end + 1], flags, captures, ai, objective_flags, event_flags,
                                  depth + 1, derived=derived, inactive=inactive))
        index = end + 1
    if not 1 <= len(operands) <= 64:
        raise ValueError('unsupported number of outcome operands')
    term = {'operands': operands}
    if inverted == '1' or threshold or any(operand.get('requiresMatch') for operand in operands):
        term['requiresMatch'] = True
    if not threshold:
        return {'trigger': 'all' if kind == 'Conjunction' else 'any', **term,
                **({'inverted': True} if inverted == '1' else {})}
    value = fields.get(threshold, '1')
    if not re.fullmatch(r'\d+', value) or int(value) > len(operands):
        raise ValueError('outcome count outside the operand range')
    count = int(value)
    # Native counts use strict comparisons. Any nonempty minimum-of-zero term always passes.
    if kind == 'Minjunction' and count == 0:
        return {'trigger': 'count', 'comparison': int(inverted == '0'),
                'count': len(operands) + int(inverted == '0'), **term}
    if kind == 'Minjunction':
        comparison, count = (1, count) if inverted == '1' else (0, count - 1)
    elif kind == 'Maxjunction':
        comparison, count = (0, count) if inverted == '1' else (1, count + 1)
    else:
        comparison = 3 if inverted == '1' else 2
    return {'trigger': 'count', 'comparison': comparison, 'count': count, **term}


def read_event(fields, key, flags, captures, ai, compound, objective_flags, event_flags, extraction_spawns,
               resolve_spawn, resolve_crew, startup_flags, hidden_briefings, dormant_flag=None, derived=None,
               log_only=False, named_events=(), inactive=(), binary_startup=None):
    actions = defaultdict(list)
    notices = []
    assignments = []
    empty_waypoints = []
    converted_actions = []
    delayed_areas = []
    messages = 0
    seen = set()
    term = None
    pregame = False
    i = 0
    while i < len(fields):
        line = fields[i]
        field = line.strip().partition(' ')[0]
        if field in seen:
            raise ValueError('duplicate event field: ' + field)
        seen.add(field)
        if LITERAL.fullmatch(line) or (compound and field == 'm_condition' and TERM.fullmatch(line)):
            end = block_end(fields, i)
            literal = LITERAL.fullmatch(line)
            literal_flag = fields[i + 1].strip().removeprefix('m_flagName ') if literal and end == i + 2 else ''
            binary = (properties(fields[i + 1:end], {'m_flagName', 'm_invertTerm'})
                      if binary_startup and literal else {})
            if literal_flag in startup_flags:
                term = {'trigger': 'startup choice', 'flag': literal_flag[1:-1]}
            elif binary and binary.get('m_flagName') == binary_startup[0] and binary.get('m_invertTerm', '0') in ('0', '1'):
                choice = binary_startup[int(binary.get('m_invertTerm', '0'))]
                term = {'trigger': 'startup choice', 'flag': choice[1:-1]}
            elif literal_flag == dormant_flag:
                term = {'trigger': 'dormant', 'flag': literal_flag[1:-1], 'reason': 'no legacy flag producer'}
            else:
                term = read_term(fields[i:end + 1], flags, captures, ai, objective_flags, event_flags,
                                 allow_dormant=compound, derived=derived, inactive=inactive)
            i = end
        elif line == '   m_instructions {':
            end = block_end(fields, i)
            j = i + 1
            while j < end:
                last = block_end(fields, j)
                notify_match = MESSAGE.fullmatch(fields[j])
                end_match = ENDING.fullmatch(fields[j])
                waypoint_match = WAYPOINTS.fullmatch(fields[j]) if compound else None
                deactivate_match = DEACTIVATE.fullmatch(fields[j]) if compound else None
                action_match = recognized(fields[j]) if compound else None
                spawn_match = SPAWN.fullmatch(fields[j]) if extraction_spawns or resolve_spawn else None
                group_match = GROUP_SPAWN.fullmatch(fields[j]) if resolve_spawn else None
                vehicle_match = VEHICLE_SPAWN.fullmatch(fields[j]) if resolve_spawn and resolve_crew else None
                if notify_match:
                    delay, body = message(fields[j + 1:last], key + '/' + notify_match[1], multiline=True)
                    messages += 1
                elif end_match:
                    delay, body, notice = end_action(fields[j + 1:last], key + '/' + end_match[1],
                                                     extended_announcements=compound)
                    notices.append(notice)
                elif waypoint_match:
                    if last == j + 1:
                        # A serialized placeholder has no group to look up.
                        empty_waypoints.append(waypoint_match[1])
                        j = last + 1
                        continue
                    delay, body, assignment = waypoint_action(fields[j + 1:last], key + '/' + waypoint_match[1])
                    assignments.append(assignment)
                elif deactivate_match:
                    delay, body, details = deactivate_events(fields[j + 1:last], key + '/' + deactivate_match[1],
                                                             named_events)
                    converted_actions.append(details)
                elif action_match:
                    delay, body, details = compile_action(action_match, fields[j + 1:last], key, hidden_briefings)
                    flag = details.get('setsFlag') or details.get('clearsFlag')
                    if flag and flag not in event_flags:
                        raise ValueError('flag has another producer or unsupported use: ' + flag)
                    converted_actions.append(details)
                elif spawn_match and spawn_match[1] in extraction_spawns:
                    details = extraction_spawns[spawn_match[1]]
                    delay, body = render_spawn(details, key + '/' + spawn_match[1])
                    delayed_areas.append(details)
                elif (spawn_match or group_match) and resolve_spawn:
                    identity = (spawn_match or group_match)[1]
                    resolver = partial(resolve_spawn, group=True) if group_match else resolve_spawn
                    delay, body, details = prefab_spawn(fields[j + 1:last], key + '/' + identity,
                                                        resolver, group=bool(group_match))
                    converted_actions.append({'instructionId': identity, **details})
                elif vehicle_match:
                    delay, body, details = vehicle_spawn(fields[j + 1:last], key + '/' + vehicle_match[1],
                                                         resolve_spawn, resolve_crew)
                    converted_actions.append({'instructionId': vehicle_match[1], **details})
                else:
                    raise ValueError('unsupported outcome instruction ' + fields[j].strip().split(' ')[0])
                if body:
                    actions[delay].extend(body)
                j = last + 1
            i = end
        elif line == '   m_pregameEvent 1':
            pregame = True
        elif not re.fullmatch(r'   m_name ' + (EVENT_LABEL if compound else KEY), line) and line != '   m_pregameEvent 0':
            raise ValueError('unsupported outcome event field ' + field)
        i += 1
    if not term or not (notices or assignments or converted_actions or delayed_areas or (compound and messages)
                        or log_only or empty_waypoints):
        raise ValueError('missing condition or supported framework instruction')
    if pregame != (term['trigger'] == 'startup choice'):
        raise ValueError('pregame event requires a reviewed startup selection')
    startup_actions = {'TILW_SpawnPrefabInstruction', 'TILW_EditMapItemInstruction'}
    if binary_startup:
        startup_actions.add('TILW_DeleteEntitiesInstruction')
    if pregame and (notices or assignments or delayed_areas or messages
                    or any(action['instruction'] not in startup_actions
                           for action in converted_actions)):
        raise ValueError('unsupported pregame action')
    if assignments and not waypoint_trigger(term):
        raise ValueError('waypoint assignments need positive capture or verified mission flags, or Match start')
    audit = {'announcements': notices, 'instructionDelaysSeconds': [f'{delay:f}' for delay in actions]}
    if log_only:
        audit['logOnly'] = True
    if assignments:
        audit['waypointAssignments'] = assignments
    if empty_waypoints:
        audit['emptyWaypointInstructions'] = empty_waypoints
    if converted_actions:
        audit['convertedActions'] = converted_actions
    if delayed_areas:
        audit['extractionSpawns'] = delayed_areas
    if messages and not (notices or assignments or converted_actions or delayed_areas):
        audit['notificationCount'] = messages
    return term, actions, audit


def leaves(term):
    if 'operands' in term:
        for item in term['operands']:
            yield from leaves(item)
    else:
        yield term


def render_check(term, key, captures, capture_messages, tracked_areas):
    trigger = term['trigger']
    if trigger == 'inactive flag':
        return [f'      GC_GameModeStateCondition "{{{guid(key + "/inactive flag")}}}" {{',
                '       m_eGameModeState GAME', '       m_bRequireOtherState 1', '      }']
    if trigger in ('interaction flag', 'event flag'):
        check = [f'      GC_MissionFlagCondition "{{{guid(key + "/objective")}}}" {{',
                 f'       m_sFlagName "{term["flag"]}"']
        if term.get('inverted'):
            check.append('       m_bRequireFalse 1')
        return [*check, '      }']
    if trigger == 'area capture':
        tracked = tracked_areas.get(term['flag'])
        if tracked:
            lines = [f'      GC_MissionEventCondition "{{{guid(key + "/capture latch")}}}" {{',
                     f'       m_sEventName "{tracked}"']
            if term.get('inverted'):
                lines.append('       m_bRequireFalse 1')
            return [*lines, '      }']
        flag = '"' + term['flag'] + '"'
        lines = captures[flag]
        # Each occurrence needs its own subtree IDs. A legacy trigger emits status only once.
        if capture_messages is not None:
            for index, line in enumerate(lines):
                if line.lstrip().startswith('m_Messages '):
                    if flag in capture_messages or term.get('liveCapture'):
                        lines = lines[:index] + lines[block_end(lines, index) + 1:]
                    if not term.get('liveCapture'):
                        capture_messages.add(flag)
                    break
        check = [re.sub(r'"\{([0-9A-F]{16})\}"', lambda match: '"{' + guid(key + '/' + match[1]) + '}"', line)
                 for line in lines]
        if term.get('liveCapture'):
            return [f'      GC_ConditionGroup "{{{guid(key + "/invert capture")}}}" {{',
                    '       m_iMatch 2', '       m_aConditions {', *('  ' + line for line in check),
                    '       }', '      }']
        return check
    if trigger in ('all', 'any', 'count'):
        if trigger == 'count':
            result = [f'      GC_ConditionCount "{{{guid(key + "/group")}}}" {{',
                      f'       m_iComparison {term["comparison"]}', f'       m_iRequiredCount {term["count"]}']
        else:
            match = int(trigger == 'any')
            if term.get('inverted'):
                match = 3 - match
            result = [f'      GC_ConditionGroup "{{{guid(key + "/group")}}}" {{', f'       m_iMatch {match}']
        result.append('       m_aConditions {')
        for index, child in enumerate(term['operands']):
            result.extend('  ' + line for line in render_check(child, f'{key}/{index}', captures, capture_messages,
                                                               tracked_areas))
        return result + ['       }', '      }']
    check = 'GC_LivingAndDeadAICheck' if trigger == 'AI loss' else 'GC_PeakFactionPopulationCheck'
    result = [f'      GC_CustomScriptCondition "{{{guid(key + "/condition")}}}" {{',
              f'       m_Script {check} "{{{guid(key + "/check")}}}" {{']
    if trigger != 'AI loss':
        result.append(f'        m_sFaction "{term["faction"]}"')
    if term.get('inverted'):
        result.append('        m_bInvert 1')
    return result + [f'        m_fRatio {term["ratio"]}', '       }', '      }']


def render(key, name, term, actions, captures, capture_messages, tracked_areas, display_name=None):
    dormant = term['trigger'] == 'dormant'
    condition = term['trigger'] not in ('Match start', 'dormant')
    startup = term['trigger'] == 'startup choice'
    needs_match = term.get('requiresMatch') or any(
        item['trigger'] in ('event flag', 'interaction flag') and item.get('inverted') for item in leaves(term))

    def match_condition(identity):
        if not needs_match:
            return []
        # A false flag is already true as a negated condition during Lobby.
        return [f'      GC_GameModeStateCondition "{{{guid(identity + "/match phase")}}}" {{',
                '       m_eGameModeState GAME', '      }']

    latch = 'GC_MigrationChoice_' + term['flag'] if startup else name
    result = []
    if condition and not startup:
        if term['trigger'] == 'area capture' and capture_messages is None and term['flag'] not in tracked_areas:
            check = captures['"' + term['flag'] + '"']
        else:
            check = render_check(term, key, captures, capture_messages, tracked_areas)
        result.extend([
            f'    GC_Event "{{{guid(key + "/trigger")}}}" {{', f'     m_sName "{name}"',
            '     m_aConditions {', *match_condition(key), *check, '     }',
            '     m_aActions {', f'      GC_MessageAction "{{{guid(key + "/log")}}}" {{',
            f'       m_sMessage "{display_name or name}"', '       m_iDelivery 2', '      }', '     }', '    }',
        ])
    if not actions and not condition and not dormant:
        actions = {Decimal(0): [
            f'      GC_MessageAction "{{{guid(key + "/log")}}}" {{',
            f'       m_sMessage "{display_name or name}"', '       m_iDelivery 2', '      }',
        ]}
    for delay, body in actions.items():
        delivery = key + '/' + str(delay)
        result.extend([f'    GC_Event "{{{guid(delivery)}}}" {{', f'     m_sName "{name} ({delay:f}s)"'])
        if condition:
            result.extend(['     m_aConditions {', *match_condition(delivery),
                           f'      GC_MissionEventCondition "{{{guid(delivery + "/latch")}}}" {{',
                           f'       m_sEventName "{latch}"', '      }', '     }'])
        if delay or dormant:
            result.append(f'     m_Advanced GC_EventOptions "{{{guid(delivery + "/delay")}}}" {{')
            if delay:
                result.append(f'      m_fDelay {delay:f}')
            if dormant:
                result.append('      m_bDisabled 1')
            result.append('     }')
        result.extend(['     m_aActions {', *body, '     }', '    }'])
    return result


def render_capture_trackers(world, tracked_areas, captures, capture_messages):
    lines = []
    for flag, name in sorted(tracked_areas.items()):
        key = world + '/capture tracker/' + flag
        check = render_check({'trigger': 'area capture', 'flag': flag}, key, captures, capture_messages, {})
        lines.extend([f'    GC_Event "{{{guid(key)}}}" {{', f'     m_sName "{name}"',
                      '     m_aConditions {', *check, '     }', '     m_aActions {',
                      f'      GC_MessageAction "{{{guid(key + "/log")}}}" {{',
                      f'       m_sMessage "{name}"', '       m_iDelivery 2',
                      '      }', '     }', '    }'])
    return lines


def startup_flags(source, world, owner, body, scripts):
    """Allow one uniform draw whose flags are each consumed by one pregame event."""
    roots = [i for i, line in enumerate(body) if line == ' m_randomFlags {']
    if len(roots) != 1:
        return ()
    try:
        index = roots[0] + 1
        stop = block_end(body, index)
        if (not re.fullmatch(r'  TILW_FlagSampling "\{[0-9A-Fa-f]{16}\}" \{', body[index])
                or stop + 1 != block_end(body, roots[0])):
            return ()
        data = properties(body[index + 1:stop], {'m_flagNames', 'm_k', 'm_withReplacement'})
        flags = data.get('m_flagNames', '').split()
        if (data.get('m_k', '1') != '1' or data.get('m_withReplacement', '0') != '0'
                or not 2 <= len(flags) <= 32 or len(set(flags)) != len(flags)
                or any(not re.fullmatch(KEY, flag) for flag in flags)):
            return ()
    except (IndexError, ValueError):
        return ()
    # An event title may match the flag it reads. Event-control instructions are checked before this pass.
    labels = {i + 1 for i, line in enumerate(body[:-1])
              if EVENT.fullmatch(line) and re.fullmatch(r'   m_name ' + EVENT_LABEL, body[i + 1])}
    framework = '\n'.join(line for i, line in enumerate(body) if i not in labels)
    owner_text = source.read(owner)
    outside = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                        if path != owner and PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    if 'GetFlagSetCopy' in scripts or 'm_flagSet' in scripts:
        return ()
    if any(framework.count(flag) != 2 or owner_text.count(flag) != 2 + sum(body[i].endswith(flag) for i in labels)
           or flag in outside or flag[1:-1] in scripts for flag in flags):
        return ()
    return tuple(flags)


def startup_samples(source, world, owner, body, scripts, spawner_reads):
    """Read isolated draws whose flag consumers run during Match."""
    roots = [i for i, line in enumerate(body) if line == ' m_randomFlags {']
    if len(roots) != 1 or any(api in scripts for api in SCRIPT_FLAG_APIS):
        return []
    try:
        index = roots[0] + 1
        end = block_end(body, roots[0])
        samples = []
        sampled = set()
        while index < end:
            if not re.fullmatch(r'  TILW_FlagSampling "\{[0-9A-Fa-f]{16}\}" \{', body[index]):
                return []
            stop = block_end(body, index)
            data = properties(body[index + 1:stop], {'m_flagNames', 'm_k', 'm_withReplacement'})
            flags = data.get('m_flagNames', '').split()
            draws = data.get('m_k', '1')
            replacement = data.get('m_withReplacement', '0')
            if (not 1 <= len(flags) <= 32 or len(set(flags)) != len(flags) or sampled.intersection(flags)
                    or any(not re.fullmatch(KEY, flag) for flag in flags)
                    or not re.fullmatch(r'[1-9]\d?', draws) or int(draws) > 32
                    or replacement not in ('0', '1') or (replacement == '0' and int(draws) > len(flags))):
                return []
            samples.append({'flags': [flag[1:-1] for flag in flags],
                            'drawCount': int(draws), 'withReplacement': replacement == '1'})
            sampled.update(flags)
            index = stop + 1
        if not 1 <= len(sampled) <= 64:
            return []
        reads = defaultdict(int)
        labels = defaultdict(int)
        for start, line in enumerate(body):
            if not EVENT.fullmatch(line):
                continue
            fields = body[start + 1:block_end(body, start)]
            match_only = '   m_pregameEvent 1' not in fields
            for position, field in enumerate(fields):
                label = re.fullmatch(r'   m_name (' + KEY + ')', field)
                if label:
                    labels[label[1]] += 1
                if match_only and (term := TERM.fullmatch(field)) and term[1] == 'Literal':
                    close = block_end(fields, position)
                    values = properties(fields[position + 1:close], {'m_flagName', 'm_invertTerm'})
                    if values.get('m_invertTerm', '0') in ('0', '1'):
                        reads[values.get('m_flagName')] += 1
    except (IndexError, ValueError):
        return []
    authored = source.read(owner)
    outside = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                        if path != owner and PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    for flag in sampled:
        spawner_count = spawner_reads.get(flag[1:-1], 0)
        if (not (reads[flag] or spawner_count) or flag[1:-1] in scripts
                or authored.count(flag) + outside.count(flag) != 1 + reads[flag] + labels[flag] + spawner_count):
            return []
    return samples


def startup_binary_flag(source, world, owner, body, scripts):
    """Recognize a fair draw used by one affirmative and one negative pregame branch."""
    roots = [i for i, line in enumerate(body) if line == ' m_randomFlags {']
    if len(roots) != 1 or any(api in scripts for api in SCRIPT_FLAG_APIS):
        return None
    start = roots[0] + 1
    if not re.fullmatch(r'  TILW_RandomFlag "\{[\da-fA-F]{16}\}" \{', body[start]):
        return None
    try:
        stop = block_end(body, start)
        if stop + 1 != block_end(body, roots[0]):
            return None
        data = properties(body[start + 1:stop], {'m_flagName', 'm_chance'})
        flag = data.get('m_flagName', '')
        if not re.fullmatch(KEY, flag) or not re.fullmatch(r'0\.50*', data.get('m_chance', '0.5')):
            return None
        branches = Counter()
        for index, line in enumerate(body):
            if not EVENT.fullmatch(line):
                continue
            fields = body[index + 1:block_end(body, index)]
            if '   m_pregameEvent 1' not in fields:
                continue
            for position, field in enumerate(fields):
                if LITERAL.fullmatch(field):
                    values = properties(fields[position + 1:block_end(fields, position)],
                                        {'m_flagName', 'm_invertTerm'})
                    if values.get('m_flagName') == flag:
                        branches[values.get('m_invertTerm', '0')] += 1
        if branches != {'0': 1, '1': 1}:
            return None
    except ValueError:
        return None
    negated = f'"GC_MigrationNot_{flag[1:-1]}"'
    if (source.read(owner).count(flag) != 3 or flag in scripts or negated in scripts
            or any(negated in source.read(path) or (path != owner and flag in source.read(path))
                   for path in source.closure(world) if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)):
        return None
    return flag, negated


def startup_conditions(key):
    lines = ['     m_aConditions {', f'      GC_ConditionGroup "{{{guid(key + "/phases")}}}" {{',
             '       m_iMatch 1', '       m_aConditions {']
    for phase in ('0', '1787356801', '1787356802'):
        lines.extend([f'        GC_GameModeStateCondition "{{{guid(key + "/" + phase)}}}" {{',
                      '         m_eGameModeState ' + phase, '        }'])
    return [*lines, '       }', '      }', '     }']


def render_startup_selection(world, flags):
    key = world + '/startup selection'
    lines = [f'    GC_RandomEventGroup "{{{guid(key)}}}" {{',
             '     m_sName "GC_MigrationStartupSelection"', '     m_iPickCount 1',
             *startup_conditions(key), '     m_aEvents {']
    for flag in flags:
        name = 'GC_MigrationChoice_' + flag[1:-1]
        lines.extend([f'      GC_Event "{{{guid(key + "/" + name)}}}" {{', f'       m_sName "{name}"',
                      '       m_aActions {', f'        GC_MessageAction "{{{guid(key + "/" + name + "/log")}}}" {{',
                      f'         m_sMessage "{name}"', '         m_iDelivery 2', '        }', '       }', '      }'])
    return [*lines, '     }', '    }']


def render_startup_samples(world, samples):
    key = world + '/startup sampling'
    lines = [f'    GC_Event "{{{guid(key)}}}" {{', '     m_sName "GC_MigrationStartupSampling"',
             *startup_conditions(key), '     m_aActions {']
    for index, sample in enumerate(samples):
        lines.extend([f'      GC_FlagSamplingAction "{{{guid(f"{key}/{index}")}}}" {{',
                      f'       m_iDrawCount {sample["drawCount"]}'])
        if sample['withReplacement']:
            lines.append('       m_bWithReplacement 1')
        lines.extend(['       m_aFlags {', *(f'        "{flag}"' for flag in sample['flags']),
                      '       }', '      }'])
    return [*lines, '     }', '    }']


def review_prefab_spawns(fields, key, resolve_spawn, resolve_crew):
    review = []
    for index, field in enumerate(fields):
        instruction = SPAWN.fullmatch(field)
        group = GROUP_SPAWN.fullmatch(field)
        vehicle = VEHICLE_SPAWN.fullmatch(field) if resolve_crew else None
        if not instruction and not group and not vehicle:
            continue
        identity = (instruction or group or vehicle)[1]
        try:
            end = block_end(fields, index)
            if vehicle:
                _, _, details = vehicle_spawn(fields[index + 1:end], key + '/' + identity, resolve_spawn, resolve_crew)
            else:
                resolver = partial(resolve_spawn, group=True) if group else resolve_spawn
                _, _, details = prefab_spawn(fields[index + 1:end], key + '/' + identity,
                                              resolver, group=bool(group))
        except ValueError as exc:
            details = {'instruction': field.split()[0], 'gap': str(exc)}
        review.append({'instructionId': identity, **details})
    return review


def compile_outcomes(source, world, captures=None, *, compound=False, notification_audit=None, objective_flags=(),
                     extraction_spawns=None, scripts=None, resolve_spawn=None, resolve_crew=None, hidden_briefings=None,
                     derived_audit=None, match_only_flags=(), startup_samples_audit=None,
                     interaction_reads=None, spawner_reads=None):
    captures = captures or {}
    notification_audit = notification_audit or {}
    extraction_spawns = extraction_spawns or {}
    layers = source.layers[world.lower()]
    owners = [(path, source.read(path)) for path in layers if 'TILW_MissionFrameworkEntity' in source.read(path)]
    if len(owners) != 1:
        return [], {}, 'requires one reviewed framework owner'
    path, text = owners[0]
    local = [p for p in source.closure(world) if p.lower().endswith(('.et', '.layer'))]
    controls = {p: matches for p in local if (matches := EVENT_CONTROL.findall(source.read(p)))}
    if controls and (not compound or set(controls) != {path}
                     or set(controls[path]) != {'DeactivateEvents'}):
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
    if set(objective_flags) & {key[1:-1] for key in (*flags, *ai, *captures)}:
        return [], {}, 'ambiguous interaction flag producer'
    spawner_reads = spawner_reads or {}
    flag_reads = defaultdict(int, interaction_reads or {})
    for flag, count in spawner_reads.items():
        flag_reads[flag] += count
    event_flags = instruction_flags(source, world, path, body, scripts, flag_reads) if scripts is not None else ()
    derived, derived_gap = {}, None
    if compound:
        try:
            derived = derived_flags(source, world, path, body, scripts)
        except ValueError as exc:
            derived_gap = str(exc)
    if derived and derived_audit is not None:
        derived_audit.extend(flag[1:-1] for flag in derived)
    samples, startup, binary_startup = [], (), None
    if compound and resolve_spawn:
        samples = startup_samples(source, world, path, body, scripts or '', spawner_reads)
        if not samples:
            startup = startup_flags(source, world, path, body, scripts or '')
            if not startup:
                binary_startup = startup_binary_flag(source, world, path, body, scripts or '')
                startup = binary_startup or ()
    sampled_flags = {flag for sample in samples for flag in sample['flags']}
    event_flags = {*event_flags, *sampled_flags}
    inactive = inactive_flags(source, world, path, scripts) if compound else ()
    match_only_flags = {*match_only_flags, *sampled_flags}
    parsed, audit = [], {}
    names = set()
    source_names = Counter(match[1][1:-1] for item in body
                           if (match := re.fullmatch(r'   m_name (' + EVENT_LABEL + ')', item)))
    capture_messages = set() if compound else None
    for i, line in enumerate(body):
        event = EVENT.fullmatch(line)
        if not event:
            continue
        fields = body[i + 1:block_end(body, i)]
        loc = f'{path}:{i + base + 2}'
        label = next((item.strip().removeprefix('m_name ') for item in fields if item.startswith('   m_name ')), '""')
        if 'notificationDelaysSeconds' in notification_audit.get(loc, {}):
            continue
        if compound and len(fields) == 1 and re.fullmatch(r'   m_name "-{3,}"', fields[0]):
            audit[loc] = {'name': fields[0].strip().removeprefix('m_name ')[1:-1], 'trigger': 'editor divider'}
            continue
        spawns = {child.split('/')[1]: detail for child, detail in extraction_spawns.items()
                  if child.startswith(event[1] + '/')}
        log_only = (compound and not any(item.startswith('   m_instructions ') for item in fields)
                    and any(TERM.fullmatch(item) for item in fields))
        if not spawns and not log_only and not any(
                ENDING.fullmatch(item) or (compound and (
                    WAYPOINTS.fullmatch(item) or MESSAGE.fullmatch(item) or recognized(item) or DEACTIVATE.fullmatch(item)
                    or (resolve_spawn and (SPAWN.fullmatch(item) or GROUP_SPAWN.fullmatch(item)))
                    or (resolve_crew and VEHICLE_SPAWN.fullmatch(item))))
                for item in fields):
            continue
        name = label
        if not re.fullmatch(EVENT_LABEL if compound else KEY, name):
            name = '"Outcome ' + event[1] + '"'
        name = name[1:-1]
        try:
            term, actions, details = read_event(
                fields, world + '/' + event[1], flags, captures, ai, compound, objective_flags, event_flags, spawns,
                resolve_spawn, resolve_crew, startup, hidden_briefings,
                DORMANT_NOTICES.get((source.commit, path, event[1])) if compound else None, derived,
                log_only=log_only, named_events=source_names, inactive=inactive, binary_startup=binary_startup)
            if any(leaf.get('flag') in match_only_flags for leaf in leaves(term)):
                term['requiresMatch'] = True
            suffixes = {f' ({delay:f}s)' for delay in actions}
            if not suffixes and term['trigger'] == 'Match start':
                suffixes.add(' (0s)')
            if term['trigger'] not in ('Match start', 'startup choice', 'dormant'):
                suffixes.add('')
            native_name = name
            emitted_names = {name + suffix for suffix in suffixes}
            if compound and emitted_names & names:
                native_name = f'{name} [{event[1]}]'
                emitted_names = {native_name + suffix for suffix in suffixes}
            if emitted_names & names:
                raise ValueError('duplicate generated event name')
            parsed.append((event[1], native_name, name, term, actions))
            audit[loc] = {**term, 'name': name, **details}
            if native_name != name:
                audit[loc]['eventName'] = native_name
            names.update(emitted_names)
        except ValueError as exc:
            audit[loc] = {'name': name, 'gap': str(exc)}
            if resolve_spawn:
                spawns_for_review = review_prefab_spawns(fields, world + '/' + event[1], resolve_spawn, resolve_crew)
                if spawns_for_review:
                    audit[loc]['prefabSpawnCandidates'] = spawns_for_review
    if controls:
        # Disable the condition latch only. The legacy instruction leaves any
        # already scheduled deliveries in the call queue.
        trigger_names = {name for _, name, original, term, _ in parsed
                         if name == original and term['trigger'] not in ('Match start', 'startup choice', 'dormant')}
        mapped = 0
        bad_targets = False
        for event in audit.values():
            deactivations = [action for action in event.get('convertedActions', ())
                             if 'deactivatesEvents' in action]
            mapped += len(deactivations)
            targets = {target for action in deactivations for target in action['deactivatesEvents']}
            missing = sorted(target for target in targets
                             if source_names[target] != 1 or target not in trigger_names)
            if missing:
                bad_targets = True
                event['gap'] = 'event deactivation targets require unique converted triggers: ' + ', '.join(missing)
        if mapped != len(controls[path]) or bad_targets:
            return [], audit, 'event deactivation target mapping is incomplete'
    used_choices = [f'"{term["flag"]}"' for _, _, _, term, _ in parsed if term['trigger'] == 'startup choice']
    if used_choices and (len(used_choices) != len(startup) or set(used_choices) != set(startup)):
        return [], audit, 'startup draw does not have one converted event per option'
    startup_names = {'GC_MigrationChoice_' + flag[1:-1] for flag in startup}
    if used_choices and (startup_names | {'GC_MigrationStartupSelection'}) & names:
        return [], audit, 'generated startup selection name collides with a mission event'
    if samples:
        used_samples = {item.get('flag') for _, _, _, term, _ in parsed for item in leaves(term)}
        used_samples.update(spawner_reads)
        if not sampled_flags <= used_samples:
            return [], audit, 'startup sampled flag still has an unconverted consumer'
        if 'GC_MigrationStartupSampling' in names:
            return [], audit, 'generated startup sampling name collides with a mission event'
        if startup_samples_audit is not None:
            startup_samples_audit.extend(samples)
    capture_terms = [item for _, _, _, term, _ in parsed for item in leaves(term)
                     if item['trigger'] == 'area capture']
    live_negations = {item['flag'] for item in capture_terms if item.get('liveCapture')}
    if not live_negations <= {item['flag'] for item in capture_terms if not item.get('inverted')}:
        return [], audit, 'a live capture negation needs a positive capture consumer'
    tracked = {item['flag'] for item in capture_terms if item.get('inverted') and not item.get('liveCapture')}
    tracked_areas = {
        flag: 'GC_MigrationCapture_' + (flag if len(flag.encode()) <= 100 and re.fullmatch(r'[\w.-]+', flag)
                                       else guid(world + '/capture tracker name/' + flag))
        for flag in sorted(tracked)
    }
    if set(tracked_areas.values()) & names:
        return [], audit, 'generated capture tracker name collides with a mission event'
    compiled = render_startup_selection(world, startup) if used_choices else []
    if samples:
        compiled.extend(render_startup_samples(world, samples))
    compiled.extend(render_capture_trackers(world, tracked_areas, captures, capture_messages))
    for identity, name, display_name, term, actions in parsed:
        compiled.extend(render(world + '/' + identity, name, term, actions, captures, capture_messages,
                               tracked_areas, display_name))
    return compiled, audit, derived_gap
