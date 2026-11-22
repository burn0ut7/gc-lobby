"""Stage independently scheduled actions for supported legacy framework events."""
from collections import Counter, defaultdict
from decimal import Decimal
from functools import partial
from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath
import re

from briefings import block_end, guid, properties
from catalog import SOURCE_TEXT
from event_actions import (FLAG_NAME, GROUP_SPAWN, SPAWN, VEHICLE_SPAWN, compile_action, prefab_spawn, recognized,
                           render_spawn, vehicle_spawn)
from events import EVENT, LITERAL, MESSAGE, message, peak_flags

FRAMEWORK = re.compile(r'^TILW_MissionFrameworkEntity(?: [\w.-]+)? : "\{8F846D0FD5D6EA51\}[^"]+" \{$')
INTERSECTING_DEFAULT = 'worlds/Corax/IntercectingForces/IntersectingForces_Layers/default.layer'
INTERSECTING_DEFAULT_BLOB = '72085fa3221eaf3c6200c21b1add35ead489c8e5'
ROUTE_FRAMEWORK = 'worlds/Til/RouteTremaux/RouteTremaux_Layers/default.layer'
ROUTE_SCORING_LAYERS = {
    ROUTE_FRAMEWORK: 'fa2adef3fe12ad9a1841b94d678c3abf456dcd9c',
    ROUTE_FRAMEWORK.replace('default.layer', 'node_triggers.layer'): 'defdd5e4bb24597a6b0aaa1b19220447f1bfce20',
    ROUTE_FRAMEWORK.replace('default.layer', 'obstacles_removable.layer'): '06f0036f9a45d0989f462285e56011e0a6fdaee5',
}
ENDING = re.compile(r'^    TILW_EndGameInstruction "\{([\da-fA-F]{16})\}" \{$')
WAYPOINTS = re.compile(r'^    TILW_AssignWaypointsInstruction "\{([\da-fA-F]{16})\}" \{$')
EVENT_CONTROL = re.compile(r'\bTILW_(RunEvents|ReactivateEvents|DeactivateEvents)Instruction\b')
CONTROL_INSTRUCTION = re.compile(r'^    ' + EVENT_CONTROL.pattern + r' "\{([\da-fA-F]{16})\}" \{$')
SCRIPT_INSTRUCTION = re.compile(r'^    TILW_RunScriptInstruction "\{([\dA-F]{16})\}" \{$')
SUPPLY_OUTCOME_LAYER = 'worlds/HotMic/SupplySecureAtMontignac/SupplySecureAtMontignac_Layers/default.layer'
SUPPLY_OUTCOME_BLOB = '01e9a0424825e574bf1439cbc9c1189fe85e226d'
SUPPLY_COUNT_FLAG = 'GC_MigrationSupplyCountRequested'
SUPPLY_COUNT_FIELDS = {
    'm_sFirstDepot': 'BlueforResourceEnity', 'm_sSecondDepot': 'RedforResourceEnity',
    'm_sFirstFlag': 'Blue Won', 'm_sSecondFlag': 'Red Won', 'm_sErrorFlag': 'Script1 Error',
}
SUPPLY_COUNT_FLAGS = [value for field, value in SUPPLY_COUNT_FIELDS.items() if field.endswith('Flag')]
SUPPLY_SCRIPT_CALLS = {'662C63247E8AB627': '2710', '662C632D1A71B31A': '180'}
INTERDICTION_OUTCOME_LAYER = ('worlds/HotMic/VehicleInterdictionCOOPKunar/'
                              'VehicleInterdictionCOOPKunar_Layers/default.layer')
INTERDICTION_OUTCOME_BLOB = '5c04f94b03ea6bc296d20bdef76f82b903e962e0'
INTERDICTION_SCRIPT_CALLS = {
    '66431A0320CA479F': (['     m_iScriptNumber 3'], '5',
                         ['        m_eOperation 0', '        m_sDepot "Bluefor_Supply_Depot"'], ()),
    '663A008164B5B57E': (['     m_executionDelay 0'], '0',
                         ['        m_eOperation 1', '        m_iSupplies 250',
                          '        m_sResultFlag "Failed Cargo Van Supplies Flag"',
                          '        m_aChoiceFlags {', '         "PickConvoyAFlag" "PickConvoyBFlag" "PickConvoyCFlag"',
                          '        }', '        m_aCargoNames {', '         "CargoA" "CargoB" "CargoC"', '        }'],
                         ('Failed Cargo Van Supplies Flag',)),
    '663E442FB72511EF': (['     m_executionDelay 0', '     m_iScriptNumber 2', '     m_bAllowRerun 1'], '0',
                         ['        m_eOperation 2', '        m_iSupplies 250',
                          '        m_sResultFlag "Check Supplies Success Flag"'],
                         ('Check Supplies Success Flag',)),
}
FOOD_MARKET_OUTCOME_LAYER = ('worlds/HotMic/StopTheFOODMarketAtKunar/'
                             'StopTheFOODMarketAtKunar_Layers/default.layer')
FOOD_MARKET_OUTCOME_BLOB = 'a70410c2f38bc78d3108758ec9e7baa2226d21d1'
DEPOT_SUPPLY_REVISIONS = {
    INTERDICTION_OUTCOME_LAYER: (INTERDICTION_OUTCOME_BLOB, INTERDICTION_SCRIPT_CALLS),
    FOOD_MARKET_OUTCOME_LAYER: (FOOD_MARKET_OUTCOME_BLOB, {
        '66863C8ED09B03D3': (['     m_executionDelay 0', '     m_iScriptNumber 3'], '0',
                             ['        m_eOperation 0', '        m_sDepot "Bluefor_Supply_Depot"'], ()),
        '663E442FB72511EF': (['     m_executionDelay 0', '     m_iScriptNumber 2', '     m_bAllowRerun 1'], '0',
                             ['        m_eOperation 3', '        m_iSupplies 500',
                              '        m_sResultFlag "Phase 1 Done Flag"', '        m_iNextSupplies 1000',
                              '        m_sNextFlag "Phase 2 Done Flag"'],
                             ('Phase 1 Done Flag', 'Phase 2 Done Flag')),
    }),
}
RUHA_PHASE_LAYER = ('worlds/HotMic/DelayingActionRuhaPlatoon/'
                    'DelayingActionRuhaPlatoon_Layers/default.layer')
RUHA_PHASE_BLOB = '785f74d7a29dcd03ecb23edef48a652878668841'
NAGALAM_LAYERS = 'worlds/Perfk/NagalamTrafficJam/NagalamTrafficJam_Layers/'
NAGALAM_AMBUSH_LAYERS = NAGALAM_LAYERS + 'AI_Phase_Two_Ambush/ambush_options/'
NAGALAM_AMBUSH_BLOBS = (
    '1e3558a86542b9a2725d268fd88f824bc82e57a7',
    '4e5f944d7302d76a6a76edf11f381a60ff5073c7',
    '307d256c30b7425b8fc29eba9b042c1bbc46bbf5',
    'dad9c652f5fbfdb81530f3f2e1e5348fb1f6aeb5',
    '4d34eab4f8c95988cd0068ceac6886429f2347f1',
)
RUHA_UNIT_NAMES = tuple(f'Unit{index}' for index in range(1, 11))
RUHA_SCRIPT_CALLS = {
    '666D3A6A1E573D65': (['     m_executionDelay 75', '     m_bAllowRerun 1'], '75', [], ()),
    '666E050B8BEDB2C6': (['     m_executionDelay 10', '     m_bAllowRerun 1'], '10', [], ()),
    '666E050B9BD2219D': (['     m_executionDelay 225', '     m_bAllowRerun 1'], '225', [], ()),
    **dict.fromkeys(('666D3A6A67B0A257', '666E050BF68F4A49'),
                    (['     m_executionDelay 6', '     m_iScriptNumber 2', '     m_bAllowRerun 1'], '6',
                     ['        m_bDeleteCaptured 1'], ())),
}
NATIVE_ACTION = re.compile(r'      GC_\w+ "\{[\da-fA-F]{16}\}" \{')
KEY = r'"[\w.-]+"'
EVENT_LABEL = r'"[^"\\\x00-\x1f]+"'
MATCH_PHASE_VALUE = '1'
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
FLAG_FIELD = re.compile(r'^\s+m_flagName (' + FLAG_NAME + r')\s*$')
META_FLAG = re.compile(r'^  TILW_MetaFlag "\{[\da-fA-F]{16}\}" \{$')
META_FLAG_FIELD = re.compile(r'^\s+m_flagName (' + EVENT_LABEL + r')\s*$')
SCRIPT_FLAG_APIS = ('AdjustMissionFlag', 'IsMissionFlag', 'GetFlagSetCopy', 'm_flagSet')
# The reviewed source produces US_KIA, while this notice consumes USMC_KIA.
DORMANT_NOTICES = {
    ('a75e404c331abce5185f315f47f533d9b8eccf00',
     'worlds/Jub/MissionWithAssets/MissionWithAssets_Layers/default.layer',
     '65DAD44B20B959A8'): '"USMC_KIA"',
}
REVIEWED_MISSING_TERMS = {
    'worlds/Blue/Ruckusberg/RuckusBerg_Layers/default.layer':
        ('35dd08d6c80cb26a71e53f4dc2d239367d025e4b', {'694F5D48174A461F', '694F5D481A4CCE4F'}),
    'worlds/Perfk/NagalamTrafficJam/NagalamTrafficJam_Layers/default.layer':
        ('e0048e9e3cf31e8776a48dd15235d34b3673246f', {'6598AE7DACE18BBE'}),
    'worlds/StatusRed/OSITheAirfieldIncident/OSITheAirfieldIncident_Layers/Logic.layer':
        ('5b0005e4ca2a8ca6a11207abff3c72192d0efaa9', {'66FB09BDE3CF819F'}),
    'worlds/RedTheKnown/DownhillStryke/DownhillStryke_Layers/MisssionTriggers.layer':
        ('516573a5947dd2aabfafcb80d7b364850f933e64', {'6A9F56058342FF13'}),
}
# The legacy hint contains no display content. The outcome and its delay are separate.
REVIEWED_MISSING_EVENT_WAYPOINTS = {
    'worlds/HitTheMan/Battle For Zarichne/BattleForZarichne_Layers/default.layer':
        ('878a776b06914e05e7cf35b6d1ecae08354d4175', 'LAVSnD'),
    'worlds/TheLocalPub/LamentinLosers/LamentinLosers_Layers/default.layer':
        ('0ad3b0e257f59917e585357ac9b026f6240feba9', 'BLUWP4_4'),
}
REVIEWED_EMPTY_HINTS = {
    'worlds/Til/PerelleBayPeril/PerelleBayPeril_Layers/default.layer':
        ('74df0f746d04e31fdd1227c261978d0b01b070ce', '62672C5D0EC091B7'),
}
REVIEWED_INACTIVE_FLAGS = {
    FOOD_MARKET_OUTCOME_LAYER: (FOOD_MARKET_OUTCOME_BLOB, '"Check Supplies Success Flag"', 1),
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
    'worlds/Perfk/AbadDayinKunarUS/AbadDayinKunarUS_Layers/default.layer':
        ('4168c2d93063db3291d03b9e5b81f37f55477e4d', '"Objective"', 1),
    'worlds/Perfk/AbadDayinKunarUSSR/AbadDayinKunarUSSR_Layers/default.layer':
        ('d26f053367424f03d59a3f205719fcbb8ee90e7b', '"Objective"', 1),
}


def inactive_flags(source, world, path, body, scripts):
    """Prove that literal-only flags have no authored writer."""
    if scripts is None or any(api in scripts for api in SCRIPT_FLAG_APIS):
        return set()
    references = '\n'.join(source.read(p) for p in sorted(source.closure(world))
                           if PurePosixPath(p).suffix.lower() in SOURCE_TEXT)
    reads = Counter()
    for start, line in enumerate(body):
        term = TERM.fullmatch(line)
        if not term or term[1] != 'Literal':
            continue
        fields = body[start + 1:block_end(body, start)]
        names = [match[1] for field in fields if (match := FLAG_FIELD.fullmatch(field))]
        if len(names) == 1 and all(field.strip() in ('m_invertTerm 0', 'm_invertTerm 1')
                                   or FLAG_FIELD.fullmatch(field) for field in fields):
            reads[names[0]] += 1
    # Arbitrary script instructions may write flags without a serialized name.
    inactive = {flag for flag, count in reads.items() if flag not in scripts and references.count(flag) == count
                and 'TILW_RunScriptInstruction' not in references}
    reference = REVIEWED_INACTIVE_FLAGS.get(path)
    if not reference:
        return inactive
    blob, flag, uses = reference
    if flag == '"Objective"':
        # The unrelated Roulette fields use this string as an editor category.
        scripts = re.sub(r'(?m)^[ \t]*\[Attribute\([^\r\n]*, category: "Objective"\)\][ \t]*\r?$', '', scripts)
    if source.files[path] == blob and flag not in scripts and references.count(flag) == uses:
        inactive.add(flag)
    return inactive


def instruction_flags(source, world, framework_path, lines, scripts, flag_reads):
    """Find flags whose complete authored use is covered by conversion rules."""
    flags = set()
    known_uses = defaultdict(int)
    for start, line in enumerate(lines):
        if CONTROL_INSTRUCTION.fullmatch(line):
            end = block_end(lines, start)
            names = next((index for index in range(start + 1, end) if lines[index] == '     m_eventNames {'), None)
            if names is not None:
                for name in re.findall(EVENT_LABEL, ' '.join(lines[names + 1:block_end(lines, names)])):
                    known_uses[name] += 1
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


def meta_flag_definitions(source, world, path, body, scripts):
    """Read meta definitions, including self-assignments and unused editor dividers."""
    starts = [i for i, line in enumerate(body) if line == ' m_metaFlags {']
    if not starts:
        return {}, set(), {}, []
    if len(starts) != 1 or scripts is None or any(api in scripts for api in SCRIPT_FLAG_APIS):
        raise ValueError('meta flag ownership requires review')
    start = starts[0]
    end = block_end(body, start)
    definitions = {}
    identities = set()
    dividers = Counter()
    discovery_resets = []
    cursor = start + 1
    while cursor < end:
        if not META_FLAG.fullmatch(body[cursor]):
            raise ValueError('unsupported meta flag placement')
        stop = block_end(body, cursor)
        fields = body[cursor + 1:stop]
        if ((path, source.files[path]) == (INTERSECTING_DEFAULT, INTERSECTING_DEFAULT_BLOB)
                and fields == ['   m_flagName "reinforce"',
                               '   m_definition TILW_BaseTerm "{6520BB572CF3E852}" {', '   }']):
            discovery_resets.append('reinforce')
            cursor = stop + 1
            continue
        terms = [i for i, line in enumerate(fields)
                 if line.startswith('   m_definition ') and TERM.fullmatch(line)]
        if len(terms) != 1:
            raise ValueError('meta flag requires one supported definition')
        term = terms[0]
        close = block_end(fields, term)
        flag = properties(fields[:term] + fields[close + 1:], {'m_flagName'}).get('m_flagName', '')
        if re.fullmatch(r'"-{3,}"', flag) and TERM.fullmatch(fields[term])[1] == 'Literal' and close == term + 1:
            dividers[flag] += 1
            cursor = stop + 1
            continue
        if not re.fullmatch(EVENT_LABEL, flag) or flag in definitions or flag in identities or flag in scripts:
            raise ValueError('invalid or script-managed meta flag ' + flag)
        operand = fields[term + 1:close]
        if TERM.fullmatch(fields[term])[1] == 'Literal' and operand in (
                ['    m_flagName ' + flag],
                ['    m_flagName ' + flag, '    m_invertTerm 0']):
            # Writing the flag's current value is idempotent. Keep counting
            # the two serialized occurrences when validating startup ownership.
            identities.add(flag)
        else:
            definitions[flag] = fields[term:close + 1]
        cursor = stop + 1

    if dividers:
        references = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                               if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
        divider_uses = Counter(re.findall(r'-{3,}', references + '\n' + scripts))
        if any(divider_uses[flag[1:-1]] != count for flag, count in dividers.items()):
            raise ValueError('meta flag divider has another authored use')
    return definitions, identities, {flag[1:-1]: count for flag, count in dividers.items()}, discovery_resets


def acyclic_meta_definitions(definitions):
    """Keep definitions whose dependency chains can be expanded without a cycle."""
    graph = TopologicalSorter({
        flag: sorted({match[1] for line in lines if (match := META_FLAG_FIELD.fullmatch(line))
                      and match[1] in definitions})
        for flag, lines in definitions.items()
    })
    gap = None
    try:
        graph.prepare()
    except CycleError as exc:
        gap = 'cyclic meta flag definitions: ' + ' -> '.join(exc.args[1])
    if gap is None:
        return definitions, None
    available = set()
    while ready := graph.get_ready():
        available.update(ready)
        graph.done(*ready)
    return {flag: lines for flag, lines in definitions.items() if flag in available}, gap


def derived_flags_with_known_readers(source, world, framework_path, body, definitions, spawner_reads):
    """Return definitions with fully accounted readers and their first unresolved flag."""
    if not definitions:
        return definitions, None
    literal_uses = literal_flag_reads(body)
    labels = {
        NAGALAM_AMBUSH_LAYERS + 'Ambush_01.layer':
            (NAGALAM_AMBUSH_BLOBS[0], '  m_locationName "Ambush_01"'),
    }
    content = '\n'.join('\n'.join(line for line in source.read(path).splitlines()
                                  if not (path == framework_path and line.startswith('   m_name '))
                                  and labels.get(path) != (source.files[path], line))
                        for path in sorted(source.closure(world))
                        if PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    unverified = {flag for flag in definitions
                  if content.count(flag) != literal_uses[flag] + spawner_reads.get(flag[1:-1], 0) + 1}
    if not unverified:
        return definitions, None
    dependencies = {
        flag: {match[1] for line in lines if (match := META_FLAG_FIELD.fullmatch(line)) and match[1] in definitions}
        for flag, lines in definitions.items()
    }
    first = next(flag for flag in definitions if flag in unverified)
    for flag in TopologicalSorter(dependencies).static_order():
        if dependencies[flag] & unverified:
            unverified.add(flag)
    return {flag: lines for flag, lines in definitions.items() if flag not in unverified}, first


def literal_flag_reads(body):
    literal_uses = Counter()
    for index, line in enumerate(body):
        match = TERM.fullmatch(line)
        if not match or match[1] != 'Literal':
            continue
        stop = block_end(body, index)
        fields = body[index + 1:stop]
        for item in fields:
            if name := META_FLAG_FIELD.fullmatch(item):
                literal_uses[name[1]] += 1
    return literal_uses


def end_action(lines, key, *, extended_announcements=False, end_match=False):
    data = properties(lines, {'m_factionKey', 'm_gameOverType', 'm_executionDelay'})
    faction = data.get('m_factionKey', '')
    kind = data.get('m_gameOverType', 'EDITOR_FACTION_VICTORY')
    delay = data.get('m_executionDelay', '5')
    if not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400:
        raise ValueError('unsupported faction outcome or delay')
    if not end_match and extended_announcements and kind == 'EDITOR_FACTION_NEUTRAL' and faction in ('', '""'):
        return Decimal(delay), [
            f'      GC_MessageAction "{{{guid(key)}}}" {{',
            '       m_iDelivery 2', '       m_sMessage "Neutral result reached."', '      }',
        ], {'faction': '', 'result': kind, 'delaySeconds': delay, 'delivery': 'server log'}
    if not end_match and extended_announcements and faction in ('', '""') and kind in (
            'EDITOR_FACTION_VICTORY', 'EDITOR_FACTION_DRAW', 'ENDREASON_TIMELIMIT'):
        # The suppressed legacy instruction resolves the faction name before
        # displaying a hint. These instructions supply no faction key.
        return Decimal(delay), [], {
            'faction': '', 'result': kind, 'delaySeconds': delay, 'delivery': 'missing faction',
        }
    result = RESULT.get(kind)
    if result is None and extended_announcements and not end_match:
        result = ANNOUNCEMENT_RESULTS.get(kind)
    if not re.fullmatch(KEY, faction) or result is None:
        raise ValueError('unsupported faction outcome or delay')
    delivery = [] if end_match else ['       m_bAnnounceOnly 1']
    return Decimal(delay), [
        f'      GC_EndMissionAction "{{{guid(key)}}}" {{', f'       m_iResult {result}',
        *delivery, '       m_aWinningFactions {', f'        {faction}', '       }', '      }',
    ], {'faction': faction[1:-1], 'result': kind, 'delaySeconds': delay,
        **({'delivery': 'match end'} if end_match else {})}


def waypoint_action(lines, key, *, missing_waypoint=None):
    data = properties(lines, {'m_groupName', 'm_waypointNames', 'm_clearExisting', 'm_executionDelay'})
    group = data.get('m_groupName', '')
    names = data.get('m_waypointNames', '').split()
    absent = f'"{missing_waypoint}"' if missing_waypoint else None
    omitted = absent in names
    if omitted:
        names = [name for name in names if name != absent]
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
    if omitted:
        details['missingWaypoint'] = missing_waypoint
    return Decimal(delay), [*body, '      }'], details


def waypoint_trigger(term):
    if term.get('inverted'):
        return False
    if term['trigger'] in ('all', 'any'):
        return all(waypoint_trigger(operand) for operand in term['operands'])
    return term['trigger'] in ('Match start', 'dormant', 'area capture', 'interaction flag', 'event flag')


def event_control(kind, lines, key, named_events, *, direct, inactive_targets=()):
    allowed = {'m_eventNames', 'm_executionDelay'}
    if kind == 'RunEvents':
        allowed.add('m_allowRerun')
    data = properties(lines, allowed)
    names = data.get('m_eventNames', '')
    delay = data.get('m_executionDelay', '5')
    if (not re.fullmatch(rf'{EVENT_LABEL}(?:\s+{EVENT_LABEL})*', names)
            or not re.fullmatch(r'\d+(?:\.\d+)?', delay) or Decimal(delay) > 86400):
        raise ValueError('unsupported event control targets or delay')
    targets = list(dict.fromkeys(re.findall(EVENT_LABEL, names)))
    if any(name[1:-1] != name[1:-1].strip() for name in targets) or len(targets) > 128:
        raise ValueError('unsupported event control target names')
    rerun = data.get('m_allowRerun', '0')
    if rerun not in ('0', '1'):
        raise ValueError('invalid event rerun setting')
    operation, audit_field = {
        'DeactivateEvents': (1, 'deactivatesEvents'),
        'ReactivateEvents': (4, 'reactivatesEvents'),
        'RunEvents': (5 + int(rerun), 'runsEvents'),
    }[kind]
    if direct:
        order = {name: index for index, name in enumerate(named_events)}
        targets.sort(key=lambda name: order.get(name[1:-1], len(order)))
    actions, active, case_mismatches, inactive = [], [], [], []
    folded = Counter(name.casefold() for name in named_events)
    for index, target in enumerate(targets):
        name = target[1:-1]
        if name in inactive_targets and name not in named_events:
            inactive.append(name)
            continue
        if name not in named_events and folded[name.casefold()] == 1:
            # A differently capitalized literal does not match the authored
            # name in the legacy instruction's string list.
            case_mismatches.append(name)
            continue
        active.append(name)
        actions.extend([f'      GC_EventControlAction "{{{guid(f"{key}/{index}")}}}" {{',
                        f'       m_sEventName {target}', f'       m_iOperation {operation}', '      }'])
    return Decimal(delay), actions, {'instruction': f'TILW_{kind}Instruction',
                                    audit_field: active, 'delaySeconds': delay,
                                    **({'inactiveTargets': inactive} if inactive else {}),
                                    **({'caseMismatchedTargets': case_mismatches} if case_mismatches else {})}


def read_term(lines, flags, captures, ai, objective_flags, event_flags, depth=0, *,
              allow_dormant=False, derived=None, negate=False, inactive=(), flag_expression=False):
    match = TERM.fullmatch(lines[0])
    # Leave room for native condition wrappers within the runtime's depth limit of 16.
    if not match or depth > 12:
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
        if flag_expression and flag == '""' and invert in ('0', '1'):
            return {'trigger': 'constant', 'value': invert == '1'}
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
            check = captures[flag]
            if invert == '0':
                return {'trigger': 'area capture', 'flag': flag[1:-1]}
            if invert == '1' and any(line.strip() == 'm_bStopAfterFirstChange 1' for line in check):
                return {'trigger': 'area capture', 'flag': flag[1:-1], 'inverted': True}
            immediate = (any(line.strip() == 'm_fSeconds 0' for line in check)
                         and not any(line.strip().startswith(('m_Messages ', 'm_bSkipDiscovery ')) for line in check))
            if invert == '1' and (depth or immediate):
                # Both copies advance in the area tracker on the same samples. Keep
                # status messages on the required positive consumer.
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
        if (depth == 0 and not threshold and inverted == '0' and index == start + 1 and end == stop - 1
                and (literal := TERM.fullmatch(lines[index])) and literal[1] == 'Literal'):
            values = properties(lines[index + 1:end], {'m_flagName', 'm_invertTerm'})
            if values.get('m_flagName', '""') == '""' and values.get('m_invertTerm') == '1':
                # A one-operand wrapper preserves the inverted empty literal's constant true result.
                return {'trigger': 'Match start'}
        operands.append(read_term(lines[index:end + 1], flags, captures, ai, objective_flags, event_flags,
                                  depth + 1, derived=derived, inactive=inactive, flag_expression=flag_expression))
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
               log_only=False, named_events=(), inactive=(), binary_startup=None, missing_term=False, ends_match=False,
               direct_control=False, empty_hint=None, missing_group_waypoint=None, sampled_pregame_flags=(),
               supply_comparison=False, reviewed_script_calls=None, reviewed_script_type=None):
    actions = defaultdict(list)
    notices = []
    assignments = []
    empty_waypoints = []
    converted_actions = []
    delayed_areas = []
    unroutable_notices = []
    omitted_empty_hint = False
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
            elif literal_flag in sampled_pregame_flags and '   m_pregameEvent 1' in fields:
                term = {'trigger': 'event flag', 'flag': literal_flag[1:-1], 'pregameSample': True}
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
                control_match = CONTROL_INSTRUCTION.fullmatch(fields[j]) if compound else None
                action_match = recognized(fields[j]) if compound else None
                script_match = SCRIPT_INSTRUCTION.fullmatch(fields[j]) if supply_comparison or reviewed_script_calls else None
                spawn_match = SPAWN.fullmatch(fields[j]) if extraction_spawns or resolve_spawn else None
                group_match = GROUP_SPAWN.fullmatch(fields[j]) if resolve_spawn else None
                vehicle_match = VEHICLE_SPAWN.fullmatch(fields[j]) if resolve_spawn and resolve_crew else None
                if notify_match:
                    if notify_match[1] == empty_hint and last == j + 1:
                        omitted_empty_hint = True
                        j = last + 1
                        continue
                    delay, body = message(fields[j + 1:last], key + '/' + notify_match[1],
                                          multiline=True, drop_unroutable=compound)
                    messages += 1
                    if not body:
                        unroutable_notices.append({'instructionId': notify_match[1], 'delaySeconds': str(delay)})
                elif end_match:
                    delay, body, notice = end_action(fields[j + 1:last], key + '/' + end_match[1],
                                                     extended_announcements=compound, end_match=ends_match)
                    notices.append(notice)
                elif waypoint_match:
                    if last == j + 1:
                        # A serialized placeholder has no group to look up.
                        empty_waypoints.append(waypoint_match[1])
                        j = last + 1
                        continue
                    delay, body, assignment = waypoint_action(fields[j + 1:last], key + '/' + waypoint_match[1],
                                                              missing_waypoint=missing_group_waypoint)
                    assignments.append(assignment)
                elif control_match:
                    kind, identity = control_match.groups()
                    inactive_targets = ()
                    if reviewed_script_type == 'GC_PhaseEntitySetScript' and identity == '660C40F7AE4DE2A2':
                        inactive_targets = ('Phase 2 Flag',)
                    delay, body, details = event_control(kind, fields[j + 1:last], key + '/' + identity,
                                                         named_events, direct=direct_control,
                                                         inactive_targets=inactive_targets)
                    converted_actions.append(details)
                elif action_match:
                    delay, body, details = compile_action(action_match, fields[j + 1:last], key, hidden_briefings)
                    flag = details.get('setsFlag') or details.get('clearsFlag')
                    if flag and flag not in event_flags:
                        raise ValueError('flag has another producer or unsupported use: ' + flag)
                    converted_actions.append(details)
                elif script_match:
                    identity = script_match[1]
                    action_key = key + '/' + identity
                    if supply_comparison:
                        delay_text = SUPPLY_SCRIPT_CALLS.get(identity)
                        if not delay_text or fields[j + 1:last] != [f'     m_executionDelay {delay_text}']:
                            raise ValueError('unreviewed inline supply comparison call')
                        body = [f'      GC_FlagAction "{{{guid(action_key)}}}" {{',
                                f'       m_sFlagName "{SUPPLY_COUNT_FLAG}"', '       m_iOperation 1',
                                '       m_iAmount 1', '      }']
                        details = {'setsFlag': SUPPLY_COUNT_FLAG, 'producesFlags': SUPPLY_COUNT_FLAGS}
                    else:
                        entry = reviewed_script_calls.get(identity)
                        if not entry or fields[j + 1:last] != entry[0]:
                            raise ValueError('unreviewed inline script call')
                        _, delay_text, settings, flags_written = entry
                        body = [f'      GC_CustomScriptAction "{{{guid(action_key)}}}" {{',
                                f'       m_Script {reviewed_script_type} "{{{guid(action_key + "/script")}}}" {{',
                                *settings, '       }', '      }']
                        details = {'producesFlags': flags_written}
                    delay = Decimal(delay_text)
                    converted_actions.append({'instruction': 'TILW_RunScriptInstruction', 'instructionId': identity,
                                              'delaySeconds': delay_text, **details})
                elif spawn_match and spawn_match[1] in extraction_spawns:
                    for details in extraction_spawns[spawn_match[1]]:
                        identity = key + '/' + spawn_match[1]
                        if 'sample' in details:
                            identity += '/' + details['flag']
                        delay, spawn_body = render_spawn(details, identity)
                        actions[delay].extend(spawn_body)
                        delayed_areas.append(details)
                    body = []
                elif (spawn_match or group_match) and resolve_spawn:
                    identity = (spawn_match or group_match)[1]
                    resolver = partial(resolve_spawn, group=True) if group_match else resolve_spawn
                    delay, body, details = prefab_spawn(
                        fields[j + 1:last], key + '/' + identity, resolver,
                        group=bool(group_match), missing_group_waypoint=missing_group_waypoint)
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
    if not term and missing_term and 'm_condition' not in seen and not pregame:
        term = {'trigger': 'dormant', 'reason': 'no authored event condition'}
    if not term or not (notices or assignments or converted_actions or delayed_areas or (compound and messages)
                        or log_only or empty_waypoints):
        raise ValueError('missing condition or supported framework instruction')
    if compound and pregame and term['trigger'] == 'Match start':
        term = {'trigger': 'pregame start'}
    pregame_start = term['trigger'] == 'pregame start'
    sample_before_match = term.get('pregameSample', False)
    if delayed_areas and any('sample' in area for area in delayed_areas):
        if not pregame or not sample_before_match or any(area.get('sample') != term.get('flag') for area in delayed_areas):
            raise ValueError('extraction spawn needs its verified pregame sample')
        pregame_extraction = True
    else:
        pregame_extraction = False
    if pregame != (term['trigger'] in ('startup choice', 'pregame start') or sample_before_match):
        raise ValueError('pregame event requires a reviewed startup selection')
    startup_actions = ({'TILW_SetFlagInstruction'} if pregame_start
                       else {'TILW_SpawnPrefabInstruction', 'TILW_EditMapItemInstruction'})
    if binary_startup and not pregame_start:
        startup_actions.add('TILW_DeleteEntitiesInstruction')
    if sample_before_match:
        startup_actions.add('TILW_DeleteEntitiesInstruction')
    if pregame and (notices or assignments or (delayed_areas and not pregame_extraction)
                    or messages or (pregame_start and not converted_actions)
                    or (sample_before_match and direct_control)
                    or any(action['instruction'] not in startup_actions
                           for action in converted_actions)):
        raise ValueError('unsupported pregame action')
    if assignments and not waypoint_trigger(term):
        raise ValueError('waypoint assignments need positive capture or verified mission flags, or Match start')
    if direct_control and not pregame and sum(
            bool(NATIVE_ACTION.fullmatch(line)) for body in actions.values() for line in body) > 256:
        raise ValueError('controlled event exceeds the native action limit')
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
    if unroutable_notices:
        audit['notificationsWithoutRecipients'] = unroutable_notices
    if omitted_empty_hint:
        audit['omittedEmptyHint'] = empty_hint
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
                f'       m_eGameModeState {MATCH_PHASE_VALUE}', '       m_bRequireOtherState 1', '      }']
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
                f'       m_eGameModeState {MATCH_PHASE_VALUE}', '      }']

    latch = 'GC_MigrationChoice_' + term['flag'] if startup else name
    result = []
    if condition and not startup:
        if term['trigger'] == 'pregame start':
            conditions = startup_conditions(key)
        else:
            if term['trigger'] == 'area capture' and capture_messages is None and term['flag'] not in tracked_areas:
                check = captures['"' + term['flag'] + '"']
            else:
                check = render_check(term, key, captures, capture_messages, tracked_areas)
            if term.get('pregameSample'):
                conditions = [*startup_conditions(key)[:-1], *check, '     }']
            else:
                conditions = ['     m_aConditions {', *match_condition(key), *check, '     }']
        result.extend([
            f'    GC_Event "{{{guid(key + "/trigger")}}}" {{', f'     m_sName "{name}"', *conditions,
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


def render_controlled_event(key, name, term, actions, captures, capture_messages, tracked_areas, display_name):
    # A run commits the event once and schedules that run's independent actions.
    # Later controls can rearm the event without cancelling any scheduled delivery.
    result = [f'    GC_Event "{{{guid(key + "/trigger")}}}" {{', f'     m_sName "{name}"',
              '     m_aConditions {', f'      GC_GameModeStateCondition "{{{guid(key + "/match phase")}}}" {{',
              f'       m_eGameModeState {MATCH_PHASE_VALUE}', '      }']
    if term['trigger'] == 'dormant':
        result.extend(render_check({'trigger': 'inactive flag'}, key, captures, capture_messages, tracked_areas))
    elif term['trigger'] != 'Match start':
        result.extend(render_check(term, key, captures, capture_messages, tracked_areas))
    result.extend(['     }', '     m_aActions {'])
    for delay, body in actions.items():
        for line in body:
            result.append(line)
            if NATIVE_ACTION.fullmatch(line):
                result.extend(['       m_bScheduleAction 1', f'       m_fActionDelay {delay:f}'])
    if not actions:
        result.extend([f'      GC_MessageAction "{{{guid(key + "/log")}}}" {{',
                       '       m_bScheduleAction 1', '       m_fActionDelay 0', f'       m_sMessage "{display_name}"',
                       '       m_iDelivery 2', '      }'])
    return [*result, '     }', '    }']


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


def startup_flags(source, world, owner, body, scripts, identity_flags=()):
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
    if any(framework.count(flag) != 2 + 2 * (flag in identity_flags)
           or owner_text.count(flag) != 2 + 2 * (flag in identity_flags) + sum(body[i].endswith(flag) for i in labels)
           or flag in outside or flag[1:-1] in scripts for flag in flags):
        return ()
    return tuple(flags)


def startup_samples(source, world, owner, body, scripts, spawner_reads, definitions, identity_flags=()):
    """Read isolated draws used by verified event conditions or placed spawners."""
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
            authored_flags = ' '.join(data.get('m_flagNames', '').splitlines())
            flags = re.findall(FLAG_NAME, authored_flags)
            draws = data.get('m_k', '1')
            replacement = data.get('m_withReplacement', '0')
            if (not 1 <= len(flags) <= 32 or len(set(flags)) != len(flags) or sampled.intersection(flags)
                    or not re.fullmatch(rf'{FLAG_NAME}(?:\s+{FLAG_NAME})*', authored_flags)
                    or not re.fullmatch(r'[1-9]\d?', draws) or int(draws) > 32
                    or replacement not in ('0', '1') or (replacement == '0' and int(draws) > len(flags))):
                return []
            samples.append({'flags': [flag[1:-1] for flag in flags],
                            'drawCount': int(draws), 'withReplacement': replacement == '1'})
            sampled.update(flags)
            index = stop + 1
        if not 1 <= len(sampled) <= 64:
            return []
        reads = Counter()
        pregame = set()
        for definition in definitions.values():
            reads.update(literal_flag_reads(definition))
        labels = defaultdict(int)
        spawn_anchors = Counter()
        for start, line in enumerate(body):
            if not EVENT.fullmatch(line):
                continue
            fields = body[start + 1:block_end(body, start)]
            match_only = '   m_pregameEvent 1' not in fields
            for position, field in enumerate(fields):
                label = re.fullmatch(r'   m_name (' + KEY + ')', field)
                if label:
                    labels[label[1]] += 1
                if SPAWN.fullmatch(field):
                    close = block_end(fields, position)
                    for instruction_field in fields[position + 1:close]:
                        anchor = re.fullmatch(r'     m_locationName (' + FLAG_NAME + ')', instruction_field)
                        if anchor:
                            spawn_anchors[anchor[1]] += 1
                if (term := TERM.fullmatch(field)) and term[1] == 'Literal':
                    close = block_end(fields, position)
                    values = properties(fields[position + 1:close], {'m_flagName', 'm_invertTerm'})
                    flag = values.get('m_flagName')
                    if match_only and values.get('m_invertTerm', '0') in ('0', '1'):
                        reads[flag] += 1
                    elif not match_only and LITERAL.fullmatch(field) and len(values) == 1:
                        reads[flag] += 1
                        pregame.add(flag)
    except (IndexError, ValueError):
        return []
    authored = source.read(owner)
    outside = '\n'.join(source.read(path) for path in sorted(source.closure(world))
                        if path != owner and PurePosixPath(path).suffix.lower() in SOURCE_TEXT)
    unused = set()
    for flag in sampled:
        spawner_count = spawner_reads.get(flag[1:-1], 0)
        expected = 1 + reads[flag] + labels[flag] + spawner_count + spawn_anchors[flag] + 2 * (flag in identity_flags)
        if flag[1:-1] in scripts or authored.count(flag) + outside.count(flag) != expected:
            return []
        if not (reads[flag] or spawner_count):
            unused.add(flag[1:-1])
    if len(unused) == len(sampled) or (unused and 'TILW_RunScriptInstruction' in authored + outside):
        return []
    if pregame and startup_flags(source, world, owner, body, scripts, identity_flags):
        return []
    for sample in samples:
        if scoped := [flag for flag in sample['flags'] if f'"{flag}"' in pregame]:
            sample['pregameFlags'] = scoped
        if unconsumed := [flag for flag in sample['flags'] if flag in unused]:
            sample['unconsumedFlags'] = unconsumed
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


def route_flag_graph(world, definitions, samples, captures):
    """Build the reviewed route graph without expanding its stateful cycles."""
    areas = {f'"_{node}"' for node in 'ABCDEFGHO'} | {f'"obst_{index:02d}"' for index in range(1, 27)}
    fixed_false = {'"block_AC"', '"block_EF"', '"block_GH"'}
    sampled = {f'"{name}"' for sample in samples for name in sample['flags']}
    references = set().union(*(literal_flag_reads(definition) for definition in definitions.values()))
    if not areas <= captures.keys() or references - definitions.keys() != areas | fixed_false | sampled:
        raise ValueError('route flag graph has an unverified upstream flag')
    values = {flag[1:-1] for flag in definitions.keys() | areas | fixed_false | sampled}

    def expression(key, term, prefix):
        identity = guid(key)
        kind = term['trigger']
        header = [f'{prefix}GC_RuleCondition "{{{identity}}}" {{']
        indent = prefix.split('m_Expression')[0] + ' '
        if kind == 'event flag':
            fields = ['m_iCheck 1', f'm_sFlagName "{term["flag"]}"']
        elif kind == 'constant':
            fields = ['m_iCheck 2', f'm_bTestAnswer {int(term["value"])}']
        elif kind in ('all', 'any'):
            fields = [f'm_eGroupNeeds {int(kind == "any")}']
        else:
            raise ValueError('unsupported route flag expression ' + kind)
        if term.get('inverted'):
            fields.append('m_bReverseAnswer 1')
        header.extend(indent + field for field in fields)
        if kind in ('all', 'any'):
            header.append(indent + 'm_aChecks {')
            for index, child in enumerate(term['operands']):
                header.extend(expression(f'{key}/{index}', child, indent + ' '))
            header.append(indent + '}')
        return [*header, indent[:-1] + '}']

    rendered = []
    for flag, definition in definitions.items():
        key = f'{world}/derived flag/{flag}'
        term = read_term(definition, {}, {}, {}, (), values, flag_expression=True)
        rendered.extend([f'    GC_DerivedMissionFlag "{{{guid(key)}}}" {{',
                         f'     m_sName {flag}', *expression(key + '/expression', term, '     m_Expression '), '    }'])
    return {'areas': sorted(flag[1:-1] for flag in areas),
            'outputs': [flag[1:-1] for flag in definitions], 'containers': rendered}


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
                     derived_audit=None, identity_flags_audit=None, meta_dividers_audit=None, discovery_resets_audit=None,
                     match_only_flags=(), startup_samples_audit=None,
                     interaction_reads=None, spawner_reads=None, derived_spawners_audit=None, route_graph_audit=None):
    captures = captures or {}
    notification_audit = notification_audit or {}
    extraction_spawns = extraction_spawns or {}
    layers = source.layers[world.lower()]
    owners = [(path, source.read(path)) for path in layers if 'TILW_MissionFrameworkEntity' in source.read(path)]
    if len(owners) != 1:
        return [], {}, 'requires one reviewed framework owner'
    path, text = owners[0]
    supply_comparison = compound and path == SUPPLY_OUTCOME_LAYER and source.files[path] == SUPPLY_OUTCOME_BLOB
    depot_blob, depot_supply_calls = DEPOT_SUPPLY_REVISIONS.get(path, (None, {}))
    if not compound or source.files[path] != depot_blob:
        depot_supply_calls = {}
    phase_capture = compound and path == RUHA_PHASE_LAYER and source.files[path] == RUHA_PHASE_BLOB
    reviewed_script_calls = RUHA_SCRIPT_CALLS if phase_capture else depot_supply_calls
    reviewed_script_type = 'GC_PhaseEntitySetScript' if phase_capture else 'GC_MissionSupplyScript'
    local = [p for p in source.closure(world) if p.lower().endswith(('.et', '.layer'))]
    controls = {p: matches for p in local if (matches := EVENT_CONTROL.findall(source.read(p)))}
    if controls and (not compound or set(controls) != {path}):
        return [], {}, 'has legacy event control instructions'
    direct_control = bool(controls and set(controls[path]) - {'DeactivateEvents'})
    lines = text.splitlines()
    roots = [i for i, line in enumerate(lines) if FRAMEWORK.fullmatch(line)]
    if len(roots) != 1:
        return [], {}, 'custom framework placement'
    base = roots[0]
    stop = block_end(lines, base)
    body = lines[base + 1:stop]
    suppression = [line for line in body if line.startswith(' m_suppressGameEnd ')]
    if suppression not in ([], [' m_suppressGameEnd 0'], [' m_suppressGameEnd 1']):
        return [], {}, 'unsupported game end suppression settings'
    ends_match = suppression == [' m_suppressGameEnd 0']
    if ends_match and not compound:
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
    definitions, derived, identity_flags, derived_gap = {}, {}, set(), None
    if compound:
        try:
            definitions, identities, dividers, discovery_resets = meta_flag_definitions(source, world, path, body, scripts)
            if meta_dividers_audit is not None:
                meta_dividers_audit.update(dividers)
            if discovery_resets_audit is not None:
                discovery_resets_audit.extend(discovery_resets)
            acyclic, derived_gap = acyclic_meta_definitions(definitions)
            derived, unverified = derived_flags_with_known_readers(
                source, world, path, body, acyclic,
                spawner_reads if derived_spawners_audit is not None else {})
            if unverified:
                derived_gap = derived_gap or 'meta flag has another producer or unsupported use: ' + unverified
            identity_flags = identities
        except ValueError as exc:
            derived_gap = str(exc)
    if derived and derived_audit is not None:
        derived_audit.extend(flag[1:-1] for flag in derived)
    if identity_flags and identity_flags_audit is not None:
        identity_flags_audit.extend(flag[1:-1] for flag in sorted(identity_flags))
    samples, startup, binary_startup = [], (), None
    if compound and resolve_spawn:
        samples = startup_samples(source, world, path, body, scripts or '', spawner_reads, definitions, identity_flags)
        if not samples:
            startup = startup_flags(source, world, path, body, scripts or '', identity_flags)
            if not startup:
                binary_startup = startup_binary_flag(source, world, path, body, scripts or '')
                startup = binary_startup or ()
    sampled_flags = {flag for sample in samples for flag in sample['flags']}
    sampled_pregame_flags = {f'"{flag}"' for sample in samples for flag in sample.get('pregameFlags', ())}
    event_flags = {*event_flags, *sampled_flags}
    event_captures = captures
    spawner_definitions = derived
    if (route_graph_audit is not None and path == ROUTE_FRAMEWORK
            and all(source.files.get(layer) == blob for layer, blob in ROUTE_SCORING_LAYERS.items())
            and all(layer in layers for layer in ROUTE_SCORING_LAYERS) and not controls):
        graph = route_flag_graph(world, definitions, samples, captures)
        route_areas = set(graph['areas'])
        event_captures = {flag: check for flag, check in captures.items() if flag[1:-1] not in route_areas}
        event_flags.update((*graph['outputs'], *route_areas))
        route_graph_audit.update(graph)
        derived = {}
        derived_gap = None
    if supply_comparison:
        event_flags.update(SUPPLY_COUNT_FLAGS)
    if depot_supply_calls:
        event_flags.update(flag for _, _, _, output in depot_supply_calls.values() for flag in output)
    inactive = inactive_flags(source, world, path, body, scripts) if compound else ()
    match_only_flags = {*match_only_flags, *sampled_flags}
    if route_graph_audit:
        match_only_flags.update((*route_graph_audit['areas'], *route_graph_audit['outputs']))
    derived_spawners = {}
    for flag, definition in spawner_definitions.items():
        if not spawner_reads.get(flag[1:-1]):
            continue
        try:
            term = read_term(definition, flags, captures, ai, objective_flags, event_flags, derived=spawner_definitions)
            for item in leaves(term):
                if item['trigger'] in ('event flag', 'interaction flag', 'AI loss'):
                    continue
                if item['trigger'] == 'peak player loss' and not item.get('inverted'):
                    continue
                check = captures.get(f'"{item.get("flag", "")}"', ())
                if (item['trigger'] != 'area capture' or item.get('inverted')
                        or 'm_bStopAfterFirstChange 1' not in {line.strip() for line in check}
                        or any(line.strip().startswith('m_Messages ') for line in check)):
                    raise ValueError('requires a verified first-change area, positive casualty check or mission flag')
        except ValueError as exc:
            derived_gap = derived_gap or f'meta flag {flag} cannot gate a placed spawner: {exc}'
            continue
        derived_spawners[flag[1:-1]] = {**term, 'requiresMatch': True, 'derivedFlag': flag[1:-1]}
    parsed, audit = [], {}
    names = set()
    source_names = Counter(match[1][1:-1] for item in body
                           if (match := re.fullmatch(r'   m_name (' + EVENT_LABEL + ')', item)))
    missing_term_blob, reviewed_missing_terms = REVIEWED_MISSING_TERMS.get(path, (None, ()))
    if not compound or source.files[path] != missing_term_blob or scripts is None or 'EvalExpression' in scripts:
        reviewed_missing_terms = ()
    hint_blob, reviewed_empty_hint = REVIEWED_EMPTY_HINTS.get(path, (None, None))
    if source.files[path] != hint_blob:
        reviewed_empty_hint = None
    route_blob, missing_group_waypoint = REVIEWED_MISSING_EVENT_WAYPOINTS.get(path, (None, None))
    if (not missing_group_waypoint or source.files[path] != route_blob
            or scripts is None or missing_group_waypoint in scripts):
        missing_group_waypoint = None
    capture_messages = set() if compound else None
    for i, line in enumerate(body):
        event = EVENT.fullmatch(line)
        if not event:
            continue
        fields = body[i + 1:block_end(body, i)]
        loc = f'{path}:{i + base + 2}'
        label = next((item.strip().removeprefix('m_name ') for item in fields if item.startswith('   m_name ')), '""')
        if (compound and not fields and event[1] == '66A9271774862F8E'
                and path == 'worlds/Perfk/PowerPlay/PowerPlay_Layers/default.layer'
                and source.files[path] == '327c5cf20c9bbcfdf16a8456e2d0b5aa0812d2eb'):
            audit[loc] = {'name': '', 'trigger': 'dormant', 'reason': 'trailing event has no condition or instructions'}
            continue
        if 'notificationDelaysSeconds' in notification_audit.get(loc, {}):
            continue
        if compound and len(fields) == 1 and re.fullmatch(r'   m_name "(?:-{3,}|={3,} [\w ]+ ={3,})"', fields[0]):
            audit[loc] = {'name': fields[0].strip().removeprefix('m_name ')[1:-1], 'trigger': 'editor divider'}
            continue
        spawns = {child.split('/')[1]: detail for child, detail in extraction_spawns.items()
                  if child.startswith(event[1] + '/')}
        log_only = (compound and not any(item.startswith('   m_instructions ') for item in fields)
                    and any(TERM.fullmatch(item) for item in fields))
        if not spawns and not log_only and not any(
                ENDING.fullmatch(item) or (compound and (
                    WAYPOINTS.fullmatch(item) or MESSAGE.fullmatch(item) or recognized(item) or CONTROL_INSTRUCTION.fullmatch(item)
                    or ((supply_comparison or reviewed_script_calls) and SCRIPT_INSTRUCTION.fullmatch(item))
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
                fields, world + '/' + event[1], flags, event_captures, ai, compound, objective_flags, event_flags, spawns,
                resolve_spawn, resolve_crew, startup, hidden_briefings,
                DORMANT_NOTICES.get((source.commit, path, event[1])) if compound else None, derived,
                log_only=log_only, named_events=source_names, inactive=inactive, binary_startup=binary_startup,
                direct_control=direct_control, ends_match=ends_match,
                empty_hint=reviewed_empty_hint, missing_group_waypoint=missing_group_waypoint,
                sampled_pregame_flags=sampled_pregame_flags, supply_comparison=supply_comparison,
                reviewed_script_calls=reviewed_script_calls, reviewed_script_type=reviewed_script_type,
                missing_term=(event[1] in reviewed_missing_terms and
                              (not controls or text.count(label) == 1 and name not in scripts)))
            if not term.get('pregameSample') and any(leaf.get('flag') in match_only_flags for leaf in leaves(term)):
                term['requiresMatch'] = True
            suffixes = {f' ({delay:f}s)' for delay in actions}
            if not suffixes and term['trigger'] == 'Match start':
                suffixes.add(' (0s)')
            if term['trigger'] not in ('Match start', 'startup choice', 'dormant'):
                suffixes.add('')
            if direct_control and term['trigger'] not in ('startup choice', 'pregame start'):
                suffixes = {''}
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
    pregame_writes = {action['setsFlag'] for entry in audit.values() if entry.get('trigger') == 'pregame start'
                      for action in entry['convertedActions']}
    for term in [item[3] for item in parsed] + [entry for entry in audit.values() if 'gap' not in entry]:
        if term.get('trigger') and any(item.get('flag') in pregame_writes for item in leaves(term)):
            term['requiresMatch'] = True
    if controls:
        # Disable the condition latch only. The legacy instruction leaves any
        # already scheduled deliveries in the call queue.
        excluded = ('startup choice', 'pregame start')
        if not direct_control:
            excluded += ('Match start', 'dormant')
        trigger_names = {name for _, name, original, term, _ in parsed
                         if name == original and term['trigger'] not in excluded}
        control_fields = ('deactivatesEvents', 'reactivatesEvents', 'runsEvents')
        mapped = 0
        bad_targets = False
        for event in audit.values():
            operations = [action for action in event.get('convertedActions', ())
                          if any(field in action for field in control_fields)]
            mapped += len(operations)
            targets = {target for action in operations for field in control_fields for target in action.get(field, ())}
            missing = sorted(target for target in targets
                             if source_names[target] != 1 or target not in trigger_names)
            if missing:
                bad_targets = True
                event['gap'] = 'event control targets require unique converted triggers: ' + ', '.join(missing)
        if mapped != len(controls[path]) or bad_targets:
            operation = 'control' if direct_control else 'deactivation'
            return [], audit, f'event {operation} target mapping is incomplete'
    used_choices = [f'"{term["flag"]}"' for _, _, _, term, _ in parsed if term['trigger'] == 'startup choice']
    if used_choices and (len(used_choices) != len(startup) or set(used_choices) != set(startup)):
        return [], audit, 'startup draw does not have one converted event per option'
    startup_names = {'GC_MigrationChoice_' + flag[1:-1] for flag in startup}
    if used_choices and (startup_names | {'GC_MigrationStartupSelection'}) & names:
        return [], audit, 'generated startup selection name collides with a mission event'
    if samples:
        used_samples = {item.get('flag') for _, _, _, term, _ in parsed for item in leaves(term)}
        used_samples.update(spawner_reads)
        used_samples.update(item.get('flag') for term in derived_spawners.values() for item in leaves(term))
        unconsumed = {flag for sample in samples for flag in sample.get('unconsumedFlags', ())}
        if not sampled_flags <= used_samples | unconsumed:
            return [], audit, 'startup sampled flag still has an unconverted consumer'
        if 'GC_MigrationStartupSampling' in names:
            return [], audit, 'generated startup sampling name collides with a mission event'
        if startup_samples_audit is not None:
            startup_samples_audit.extend(samples)
    capture_terms = [item for _, _, _, term, _ in parsed for item in leaves(term)
                     if item['trigger'] == 'area capture']
    live_negations = {item['flag'] for item in capture_terms if item.get('liveCapture') and any(
        line.strip().startswith('m_Messages ') for line in captures[f'"{item["flag"]}"'])}
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
    if supply_comparison:
        calls = {action['instructionId'] for entry in audit.values() for action in entry.get('convertedActions', ())
                 if action.get('setsFlag') == SUPPLY_COUNT_FLAG}
        if calls != SUPPLY_SCRIPT_CALLS.keys() or SUPPLY_COUNT_FLAG in names:
            return [], audit, 'inline comparison requires both reviewed schedules and a unique shared trigger'
        identity = world + '/shared supply comparison'
        compiled.extend([
            f'    GC_Event "{{{guid(identity)}}}" {{', f'     m_sName "{SUPPLY_COUNT_FLAG}"',
            '     m_aConditions {',
            *render_check({'trigger': 'event flag', 'flag': SUPPLY_COUNT_FLAG},
                          identity, captures, capture_messages, tracked_areas),
            '     }', '     m_aActions {',
            f'      GC_CustomScriptAction "{{{guid(identity + "/action")}}}" {{',
            f'       m_Script GC_CompareSuppliesScript "{{{guid(identity + "/script")}}}" {{',
            *(f'        {field} "{value}"' for field, value in SUPPLY_COUNT_FIELDS.items()),
            '       }', '      }', '     }', '    }',
        ])
    if reviewed_script_calls:
        calls = {action['instructionId'] for entry in audit.values() for action in entry.get('convertedActions', ())
                 if action.get('instruction') == 'TILW_RunScriptInstruction'}
        if calls != reviewed_script_calls.keys():
            return [], audit, 'inline script conversion requires all reviewed calls'
    compiled.extend(render_capture_trackers(world, tracked_areas, captures, capture_messages))
    for identity, name, display_name, term, actions in parsed:
        if direct_control and term['trigger'] not in ('startup choice', 'pregame start'):
            compiled.extend(render_controlled_event(world + '/' + identity, name, term, actions, captures,
                                                    capture_messages, tracked_areas, display_name))
        else:
            compiled.extend(render(world + '/' + identity, name, term, actions, captures, capture_messages,
                                   tracked_areas, display_name))
    if derived_spawners_audit is not None:
        derived_spawners_audit.update(derived_spawners)
    return compiled, audit, derived_gap
