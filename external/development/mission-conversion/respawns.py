"""Read original faction budgets and build match-start ticket actions for GC Lobby."""
import json
import re
from briefings import block_end, guid, properties

FIELDS = {'m_sFactionKey', 'm_iCount', 'm_iTime', 'm_bWaveMode', 'm_sPosition'}
ENTRY = re.compile(r'^  PS_FactionRespawnCount "\{([0-9A-Fa-f]{16})\}" \{$')


def read_respawns(text, path):
    lines = text.splitlines()
    records, gaps = [], []
    for i, line in enumerate(lines):
        if not re.match(r'^\s*m_aFactionRespawnCount\b', line):
            continue
        origin = f'{path}:{i + 1}'
        try:
            parent = next((line for line in reversed(lines[:i]) if line and not line[0].isspace()), '')
            if line != ' m_aFactionRespawnCount {' or not parent.startswith('PS_GameModeCoop '):
                raise ValueError('custom mode or inherited array operator')
            stop = block_end(lines, i)
            j = i + 1
            while j < stop:
                match = ENTRY.fullmatch(lines[j])
                if not match:
                    raise ValueError(f'unsupported entry at line {j + 1}')
                end = block_end(lines, j)
                fields = properties(lines[j+1:end], FIELDS)
                key = json.loads(fields.get('m_sFactionKey', '""'))
                position = json.loads(fields.get('m_sPosition', '""'))
                count = int(fields.get('m_iCount', 0))
                interval = int(fields.get('m_iTime', 0))
                wave = fields.get('m_bWaveMode', '0')
                if not re.fullmatch(r'[A-Za-z_0-9-]+', key):
                    gaps.append(f'{path}:{j + 1} unresolved faction')
                if wave not in ('0', '1') or interval < 0:
                    raise ValueError('invalid source scheduling settings')
                records.append(dict(source=f'{path}:{j + 1}', faction=key, tickets=count,
                                    intervalMs=interval, wave=wave == '1', destination=position))
                j = end + 1
        except ValueError as exc:
            gaps.append(origin + ' ' + str(exc))
    return records, gaps


def ticket_event(world, records, gaps):
    if not records:
        return []
    seen = set()
    for row in records:
        key = row['faction']
        if key in seen:
            gaps.append('duplicate faction ' + key)
        seen.add(key)
        if not 0 <= row['tickets'] <= 1000000:
            gaps.append(f'{key} ticket value outside native action range')
    if gaps:
        return []
    funded = [row for row in records if row['tickets'] > 0]
    if not funded:
        return []
    lines = ['   m_aMissionEvents + {', f'    GC_Event "{{{guid(world + "/tickets")}}}" {{',
             '     m_sName "Initialize faction respawn tickets"', '     m_aActions {']
    for row in funded:
        lines.extend([f'      GC_RespawnTicketsAction "{{{guid(world + "/tickets/" + row["faction"])}}}" {{',
                      f'       m_sFaction "{row["faction"]}"', '       m_iOperation 0',
                      f'       m_iAmount {row["tickets"]}', '      }'])
    return lines + ['     }', '    }', '   }']


def authored_respawn_fields(world, records, gaps):
    if gaps or len({row['faction'] for row in records}) != len(records):
        raise ValueError('invalid or duplicate faction respawn records')
    funded = [row for row in records if row['tickets']]
    if not funded:
        return ''
    lines = ['   m_aAuthoredRespawns {']
    for row in funded:
        faction = row['faction']
        count = row['tickets']
        interval = row['intervalMs']
        anchor = row['destination']
        if (not 0 < count <= 1000000 or not 0 <= interval <= 2147483647 or (row['wave'] and not interval)
                or (anchor and not re.fullmatch(r'[\w.-]+', anchor))):
            raise ValueError('unsupported faction respawn settings for ' + faction)
        key = guid(world + '/scheduled respawn/' + faction)
        lines.extend([f'    GC_AuthoredFactionRespawn "{{{key}}}" {{', f'     m_sFaction "{faction}"',
                      f'     m_iTickets {count}', f'     m_iIntervalMs {interval}'])
        if row['wave']:
            lines.append('     m_bWave 1')
        if anchor:
            lines.append(f'     m_sAnchor "{anchor}"')
        lines.append('    }')
    return '\n'.join([*lines, '   }', ''])
