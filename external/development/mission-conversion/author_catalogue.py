"""Translate archived GC author classifications into the native catalogue fields."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from briefings import block_end
from catalog import Snapshot


LEGACY = re.compile(r' m_GC_Metadata GC_MissionMetadata ("\{[0-9A-Fa-f]{16}\}") \{')
EXISTING = re.compile(r'^ m_GC_Catalog\b', re.M)
FORCE = re.compile(r'  m_(Friendly|Opposing)Force GC_ForceDefinition ("\{[0-9A-Fa-f]{16}\}") \{')
GROUP = re.compile(r'  m_MissionGroup GC_MissionGroupDefinition "\{[0-9A-Fa-f]{16}\}" \{')
QUOTED = r'"(?:[^"\\]|\\.)*"'
TYPES = {'OTHER', 'LIGHT', 'MOTORIZED', 'MECHANIZED', 'ARMORED', 'AIRMOBILE', 'AIRBORNE', 'COMBINED'}
ASSETS = {
    'UNDEFINED': '',
    'TANKS': 'Armored',
    'ATK_HELIS': 'Airborne',
    'MORTARS': 'Artillery',
    'ARTILLERY': 'Artillery',
    'DRONES': 'Drones',
    'BOATS': 'Naval',
}


def translated_id(identifier):
    value = sha256(('GC native catalogue:' + identifier).encode()).hexdigest()[:16].upper()
    return f'"{{{value}}}"'


def fields(lines, patterns):
    values = {}
    for line in lines:
        key, _, value = line.strip().partition(' ')
        if key in values or key not in patterns or not re.fullmatch(patterns[key], value):
            raise ValueError('unexpected or repeated author field ' + key)
        values[key] = value
    return values


def catalogue(lines):
    result = []
    forces = []
    seen = set()
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        key = line.strip().partition(' ')[0]
        if key in seen:
            raise ValueError('repeated author field ' + key)
        seen.add(key)
        if era := re.fullmatch(r'  m_eEra ERA_(OTHER|1970s|1980s|1990s|2000s|2010s|2020s|2030s)', line):
            era_label = 'Other' if era[1] == 'OTHER' else era[1]
            result.append(f'  m_sEra "{era_label}"')
        elif force := FORCE.fullmatch(line):
            end = block_end(lines, index)
            attrs = fields(lines[index + 1:end], {
                'm_sFactionKey': QUOTED, 'm_eType': '|'.join(sorted(TYPES)),
            })
            forces.append(f'   GC_CatalogForce {translated_id(force[2])} {{')
            if 'm_sFactionKey' in attrs:
                forces.append('    m_sFactionKey ' + attrs['m_sFactionKey'])
            if 'm_eType' in attrs:
                forces.append(f'    m_sForceType "{attrs["m_eType"]}"')
            forces.extend([f'    m_sSide "{force[1].lower()}"', '   }'])
            index = end
        elif GROUP.fullmatch(line):
            end = block_end(lines, index)
            attrs = fields(lines[index + 1:end], {
                'm_sGroupID': QUOTED, 'm_fGroupSimilarity': r'(?:0(?:\.\d+)?|1(?:\.0+)?)',
            })
            if 'm_sGroupID' in attrs:
                result.append('  m_sGroupId ' + attrs['m_sGroupID'])
                attrs.setdefault('m_fGroupSimilarity', '1')
            if 'm_fGroupSimilarity' in attrs:
                result.append('  m_fGroupSimilarity ' + attrs['m_fGroupSimilarity'])
            index = end
        elif line == '  m_eSpecialAssets {':
            end = block_end(lines, index)
            tags = ' '.join(lines[index + 1:end]).split()
            if set(tags) - ASSETS.keys():
                raise ValueError('unmapped legacy asset tags')
            assets = sorted({ASSETS[tag] for tag in tags if ASSETS[tag]})
            if assets:
                result.extend(['  m_aPlayerAssets {', '   ' + ' '.join(f'"{asset}"' for asset in assets), '  }'])
            index = end
        else:
            raise ValueError('unsupported legacy author field ' + key)
        index += 1
    if forces:
        result.extend(['  m_aFactions {', *forces, '  }'])
    if not result:
        raise ValueError('legacy catalogue has no mapped fields')
    return result


def translate(body):
    """Insert current metadata while retaining the original author block byte for byte."""
    text = body.decode('utf-8')
    if EXISTING.search(text):
        return body
    lines = text.splitlines(keepends=True)
    matches = [ix for ix, line in enumerate(lines) if LEGACY.fullmatch(line.rstrip('\r\n'))]
    if len(matches) != 1:
        raise ValueError('expected exactly one original GC author block')
    start, = matches
    source = [line.rstrip('\r\n') for line in lines]
    end = block_end(source, start)
    identifier = translated_id(LEGACY.fullmatch(source[start])[1])
    destination = [f' m_GC_Catalog GC_CatalogMissionMetadata {identifier} {{',
                   *catalogue(source[start:end + 1]), ' }']
    newline = '\r\n' if lines[start].endswith('\r\n') else '\n'
    lines[start:start] = [line + newline for line in destination]
    return ''.join(lines).encode('utf-8')


def prepare(dev, audit, destination):
    if audit['target'] != dev.commit:
        raise ValueError('rerun the file audit for the current Dev commit')
    if destination.resolve().is_relative_to(dev.repo.resolve()):
        raise ValueError('use a staging directory outside the Missions checkout')
    destination.mkdir(parents=True, exist_ok=False)
    staged = []
    held = {}
    for header, checks in sorted(audit['committedLauncherMetadata'].items()):
        original = dev.read_raw(header)
        if b'\n m_GC_Metadata ' not in original or checks['catalogueMetadata']:
            continue
        if not checks['gcMissionHeader']:
            held[header] = 'custom native header type'
            continue
        try:
            updated = translate(original)
        except ValueError as exc:
            held[header] = str(exc)
            continue
        if updated == original:
            continue
        output = destination / header
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(updated)
        staged.append(header)
    return {'target': dev.commit, 'staged': staged, 'held': held}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    print(json.dumps(prepare(Snapshot(args.missions, 'HEAD'), audit, args.stage), indent=2))


if __name__ == '__main__':
    main()
