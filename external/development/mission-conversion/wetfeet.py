"""Convert WetFeet's reviewed capture and mutually deactivating victory events."""
from collections import defaultdict
import re

from briefings import block_end, guid, properties, QUOTED
from events import EVENT, LITERAL, MESSAGE, PEAK_FLAG, message

WORLD = 'worlds/TheLocalPub/WetFeet/WetFeet.ent'
LAYERS = WORLD.removesuffix('.ent') + '_Layers/'
DEFAULT = LAYERS + 'default.layer'
TRIGGER = LAYERS + 'SYSTEMS/MISC.layer'
MODE = LAYERS + 'default_GC_Mode.layer'
REVIEWED = {
    DEFAULT: 'eff30d4f5d74c183e1d0ed2ff93f1255a5ebb19c',
    TRIGGER: '2a15ac2fc36da9709bb5c73ed1331838588ebdf4',
}
DEACTIVATE = re.compile(r'^    TILW_DeactivateEventsInstruction "\{([\da-fA-F]{16})\}" \{$')
IDENTIFIER = r'"[\w.-]+"'


def capture(source):
    lines = source.read(TRIGGER).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('TILW_FactionTriggerEntity CAPZone '))
    fields = properties(lines[start + 1:block_end(lines, start)], {
        'coords', 'scale', 'm_queryRadius', 'm_captureTime', 'm_sendStatusMessages', 'm_statusMessages',
        'm_locationName', 'm_flagName', 'm_playersOnly', 'm_ownerFactionKey',
    })
    status_fields = ('Captured', 'Capturing', 'CaptureInterrupted', 'Lost', 'Losing', 'LossInterrupted')
    status = re.findall(QUOTED, fields['m_statusMessages'])
    if len(status) != len(status_fields) or not re.fullmatch(IDENTIFIER, fields['m_flagName']):
        raise ValueError('WetFeet capture definition changed')
    key = WORLD + '/capture'
    messages = '\n'.join(f'         m_s{name} {text}' for name, text in zip(status_fields, status))
    condition = f'''      GC_AreaCondition "{{{guid(key)}}}" {{
       m_sArea "CAPZone"
       m_Targets GC_CharacterAreaTargets "{{{guid(key + '/players')}}}" {{
        m_aFactions {{
         {fields['m_ownerFactionKey']}
        }}
       }}
       m_Requirement GC_LocalFactionShare "{{{guid(key + '/share')}}}" {{
        m_fRequiredShare 1
       }}
       m_Capture GC_AreaCaptureOptions "{{{guid(key + '/timer')}}}" {{
        m_fSeconds {fields['m_captureTime']}
        m_iInterruption 3
        m_Messages GC_CaptureMessages "{{{guid(key + '/status')}}}" {{
         m_sLocation {fields['m_locationName']}
{messages}
        }}
       }}
      }}'''
    area = f'''GC_RadiusAreaEntity CAPZone : "{{2C4447C53A1E450B}}Prefabs/Logic/Mission/RadiusArea.et" {{
 coords {fields['coords']}
 m_fRadius {fields['m_queryRadius']}
}}'''
    return fields['m_flagName'], condition, area, {
        'name': 'CAPZone', 'radiusMeters': fields['m_queryRadius'], 'seconds': fields['m_captureTime'],
        'ownerFaction': fields['m_ownerFactionKey'][1:-1], 'requiredLocalShare': 1,
    }


def victory_rules(lines, conditions):
    events = []
    for i, line in enumerate(lines):
        match = EVENT.fullmatch(line)
        if not match:
            continue
        fields = lines[i + 1:block_end(lines, i)]
        name = fields[0].removeprefix('   m_name ')
        if not re.fullmatch(IDENTIFIER, name) or fields[1] != '   m_instructions {':
            raise ValueError('WetFeet event has an unexpected name or instruction list')
        end = block_end(fields, 1)
        if end + 4 != len(fields) or not LITERAL.fullmatch(fields[end + 1]):
            raise ValueError('WetFeet event needs one literal condition')
        flag = fields[end + 2].removeprefix('    m_flagName ')
        key = WORLD + '/victory/' + match[1]
        notices = defaultdict(list)
        targets = []
        instruction = 2
        while instruction < end:
            last = block_end(fields, instruction)
            args = fields[instruction + 1:last]
            action = MESSAGE.fullmatch(fields[instruction])
            control = DEACTIVATE.fullmatch(fields[instruction])
            if action:
                delay, body = message(args, key + '/' + action[1], multiline=True)
                notices[delay].extend(body)
            elif control:
                controls = properties(args, {'m_eventNames'})
                targets = re.findall(IDENTIFIER, controls['m_eventNames'])
                for target in targets:
                    notices[5].extend([
                        f'      GC_EventControlAction "{{{guid(key + "/disable/" + target)}}}" {{',
                        f'       m_sEventName {target}', '       m_iOperation 1', '      }',
                    ])
            else:
                raise ValueError('WetFeet event has an unsupported instruction')
            instruction = last + 1
        events.append((key, name, flag, notices, targets))

    names = {name for _, name, _, _, _ in events}
    if len(names) != len(events) or len(events) != 3:
        raise ValueError('WetFeet victory event count or names changed')
    rules, audit = [], {}
    for key, name, flag, notices, targets in events:
        if flag not in conditions or len(targets) != 2 or set(targets) != names - {name}:
            raise ValueError('WetFeet victory condition or deactivation targets changed')
        rules.extend([
            f'    GC_Event "{{{guid(key)}}}" {{', f'     m_sName {name}',
            '     m_aConditions {', conditions[flag], '     }',
            '     m_aActions {',
            f'      GC_MessageAction "{{{guid(key + "/log")}}}" {{',
            f'       m_sMessage {name}', '       m_iDelivery 2', '      }',
            '     }', '    }',
        ])
        # Legacy deactivation prevents future triggers. Each already queued
        # instruction still executes, even if another outcome then deactivates it.
        for delay, actions in notices.items():
            delivery_key = key + '/delivery/' + str(delay)
            rules.extend([
                f'    GC_Event "{{{guid(delivery_key)}}}" {{',
                f'     m_sName "{name[1:-1]} announcement ({delay}s)"', '     m_aConditions {',
                f'      GC_MissionEventCondition "{{{guid(delivery_key + "/result")}}}" {{',
                f'       m_sEventName {name}', '      }', '     }',
                f'     m_Advanced GC_EventOptions "{{{guid(delivery_key + "/delay")}}}" {{',
                f'      m_fDelay {delay}', '     }', '     m_aActions {', *actions, '     }', '    }',
            ])
        audit[name[1:-1]] = {'sourceEvent': key.rsplit('/', 1)[1], 'flag': flag[1:-1],
                             'deactivates': [target[1:-1] for target in targets],
                             'instructionDelaysSeconds': [str(delay) for delay in notices]}
    return '\n'.join(rules), audit


def stage_mode(source, preset):
    for path, oid in REVIEWED.items():
        if source.files.get(path) != oid:
            raise ValueError('WetFeet source changed: ' + path)

    flag, requirement, area, capture_audit = capture(source)
    conditions = {flag: requirement}
    casualty_audit = {}
    lines = source.read(DEFAULT).splitlines()
    for i, line in enumerate(lines):
        if not PEAK_FLAG.fullmatch(line):
            continue
        data = properties(lines[i + 1:block_end(lines, i)], {'m_flagName', 'm_factionKey', 'm_casualtyRatio'})
        flag = data['m_flagName']
        key = WORLD + '/casualties/' + flag
        conditions[flag] = f'''      GC_CustomScriptCondition "{{{guid(key)}}}" {{
       m_Script GC_PeakFactionPopulationCheck "{{{guid(key + '/check')}}}" {{
        m_sFaction {data['m_factionKey']}
        m_fRatio {data['m_casualtyRatio']}
       }}
      }}'''
        casualty_audit[flag[1:-1]] = {'faction': data['m_factionKey'][1:-1], 'ratio': data['m_casualtyRatio']}
    rules, audit = victory_rules(lines, conditions)
    start = next(i for i, line in enumerate(lines) if line.startswith('PS_GameModeCoop '))
    weather = '\n'.join(lines[start + 2:block_end(lines, start + 1)]).replace('5EE3229927D4D2F5', '6B71E9A8B1A8C501')
    transform = lines[block_end(lines, start) - 1]
    mode = f'''GC_LobbyGameMode GC_WetFeetMode : "{{{guid(WORLD + '/resource')}}}{preset}" {{
 components {{
{weather}
  GC_PeakFactionPopulationComponent "{{{guid(WORLD + '/population')}}}" {{
  }}
  GC_LobbyManager "{{6A137A39106A4973}}" {{
   m_aMissionEvents + {{
{rules}
   }}
  }}
 }}
{transform}
}}
{area}
'''
    return mode, {'capture': capture_audit, 'casualties': casualty_audit, 'outcomes': audit}
