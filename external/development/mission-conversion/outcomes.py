"""Stage independently scheduled notices for legacy outcome events."""
from collections import defaultdict
from decimal import Decimal
import re

from briefings import block_end, guid, properties
from events import EVENT, LITERAL, MESSAGE, message, peak_flags

FRAMEWORK = re.compile(r'^TILW_MissionFrameworkEntity(?: [\w.-]+)? : "\{8F846D0FD5D6EA51\}[^"]+" \{$')
ENDING = re.compile(r'^    TILW_EndGameInstruction "\{([\da-fA-F]{16})\}" \{$')
KEY = r'"[\w.-]+"'
RESULT = {'EDITOR_FACTION_VICTORY': 1, 'EDITOR_FACTION_DEFEAT': 2, 'EDITOR_FACTION_DRAW': 3}


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


def read_event(fields, key, flags):
    actions = defaultdict(list)
    notices = []
    seen = set()
    term = None
    i = 0
    while i < len(fields):
        line = fields[i]
        field = line.strip().partition(' ')[0]
        if field in seen:
            raise ValueError('duplicate event field: ' + field)
        seen.add(field)
        if LITERAL.fullmatch(line):
            end = block_end(fields, i)
            values = properties(fields[i + 1:end], {'m_flagName', 'm_invertTerm'})
            flag = values.get('m_flagName', '""')
            invert = values.get('m_invertTerm', '0')
            if flag == '""' and invert == '1':
                term = {'trigger': 'Match start'}
            elif invert == '0' and flag in flags:
                faction, ratio = flags[flag]
                term = {'trigger': 'peak player loss', 'flag': flag[1:-1], 'faction': faction, 'ratio': ratio}
            else:
                raise ValueError('unresolved or unsupported outcome flag ' + flag)
            i = end
        elif line == '   m_instructions {':
            end = block_end(fields, i)
            j = i + 1
            while j < end:
                last = block_end(fields, j)
                notify_match = MESSAGE.fullmatch(fields[j])
                end_match = ENDING.fullmatch(fields[j])
                if notify_match:
                    delay, body = message(fields[j + 1:last], key + '/' + notify_match[1], multiline=True)
                elif end_match:
                    delay, body, notice = end_action(fields[j + 1:last], key + '/' + end_match[1])
                    notices.append(notice)
                else:
                    raise ValueError('unsupported outcome instruction ' + fields[j].strip().split(' ')[0])
                actions[delay].extend(body)
                j = last + 1
            i = end
        elif not re.fullmatch(r'   m_name ' + KEY, line) and line != '   m_pregameEvent 0':
            raise ValueError('unsupported outcome event field ' + field)
        i += 1
    if not term or not notices:
        raise ValueError('missing literal condition or end instruction')
    return term, actions, notices


def render(key, name, term, actions):
    condition = term['trigger'] == 'peak player loss'
    result = []
    if condition:
        result.extend([
            f'    GC_Event "{{{guid(key + "/trigger")}}}" {{', f'     m_sName "{name}"',
            '     m_aConditions {', f'      GC_CustomScriptCondition "{{{guid(key + "/condition")}}}" {{',
            f'       m_Script GC_PeakFactionPopulationCheck "{{{guid(key + "/check")}}}" {{',
            f'        m_sFaction "{term["faction"]}"', f'        m_fRatio {term["ratio"]}', '       }', '      }', '     }',
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


def compile_outcomes(source, world):
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
    compiled, audit = [], {}
    names = set()
    for i, line in enumerate(body):
        event = EVENT.fullmatch(line)
        if not event:
            continue
        fields = body[i + 1:block_end(body, i)]
        if not any(ENDING.fullmatch(item) for item in fields):
            continue
        name = next((item.strip().removeprefix('m_name ') for item in fields if item.startswith('   m_name ')), '""')
        if not re.fullmatch(KEY, name):
            name = '"Outcome ' + event[1] + '"'
        name = name[1:-1]
        loc = f'{path}:{i + base + 2}'
        try:
            if name in names:
                raise ValueError('duplicate outcome name')
            term, actions, notices = read_event(fields, world + '/' + event[1], flags)
            compiled.extend(render(world + '/' + event[1], name, term, actions))
            audit[loc] = {**term, 'name': name, 'announcements': notices,
                          'instructionDelaysSeconds': [f'{delay:f}' for delay in actions]}
            names.add(name)
        except ValueError as exc:
            audit[loc] = {'name': name, 'gap': str(exc)}
    return compiled, audit, None
