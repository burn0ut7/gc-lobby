"""Stage a topology-only child of the authored Zagoria BMP-2 for review."""

import argparse
from hashlib import file_digest, sha256
from pathlib import Path
import re

from briefings import block_end
from launchers import ADDON_VERSIONS_FIELD, required_addon_versions
from review import ZAGORIA_DRIVETRAIN_FAILURE
from roulette import resource_metadata
from scenery import StockPrefabs
from vehicle_crew import ZAGORIA_BMP2


ORIGINAL_PREFAB_SHA256 = 'd8144fd06f25440cce5a5fb398d62a68cb9d10ad39d46a9b48ea7f4c8542f8e3'
WRAPPER = ('0BF3D7C254ABFCBC', 'Prefabs/Vehicles/GC_ZagoriaBMP2_Olive.et')
WRAPPER_SHA256 = '974694f0c13f495f6548dde5b87237c486427dfde3323ebb6cbb49722ba62395'
ADDON_ID, VERSION = '6730D59067916E3D', '1.0.26'
PLACEMENTS = {
    'worlds/Delfer/Topdown/Topdown.ent': 1,
    'worlds/Jub/CombinedAntiArmorTeam/CombinedAntiArmorTeam.ent': 7,
    'worlds/Jub/LightArmoredRecon/LightArmoredRecon.ent': 11,
    'worlds/Jub/OperationArrowheadMulladostPass/OperationArrowheadMulladostPass.ent': 5,
    'worlds/Jub/OperationArrowheadTwoBloodyMiles/OperationArrowheadTwoBloodyMiles.ent': 3,
}
SIMULATION = '  VehicleWheeledSimulation "{731B26FCA2F19855}" {'
RETIRED_SIMULATION = '   Simulation Wheeled "{4D8B26DEA5F25978}" {'
REPLACEMENT_SIMULATION = '   Simulation Wheeled "{17A8A285A915DF73}" {'

ORIGINAL_REFERENCE = f'{{{ZAGORIA_BMP2[0]}}}{ZAGORIA_BMP2[1]}'
CHILD_REFERENCE = f'{{{WRAPPER[0]}}}{WRAPPER[1]}'
SPAWN_REFERENCE = re.compile(
    r'^ *(?:m_sPrefab |(?:\$grp )?Vehicle\b[^"\n]*: )"' + re.escape(ORIGINAL_REFERENCE) + r'"', re.M)


def staged_assets(stage):
    identity, path = WRAPPER
    prefab = (stage / path).read_bytes()
    metadata = (stage / (path + '.meta')).read_bytes()
    if (sha256(prefab).hexdigest() != WRAPPER_SHA256
            or metadata != resource_metadata(path, identity, 'EntityTemplateResourceClass')):
        raise ValueError('expected the reviewed Zagoria topology child and metadata')
    return {path: prefab, path + '.meta': metadata}


def pin_parent(body):
    pins = dict(required_addon_versions(body))
    if ADDON_ID in pins:
        if pins[ADDON_ID] != VERSION:
            raise ValueError('launcher has a different Zagoria parent version')
        return body
    value = f'  "{ADDON_ID}={VERSION}"\n'
    if match := ADDON_VERSIONS_FIELD.search(body):
        opening = body.index('{', match.end()) + 1
        return body[:opening] + '\n' + value + body[opening:].lstrip('\n')
    return body.rstrip().removesuffix('}') + f' m_aRequiredAddonVersions {{\n{value} }}\n}}\n'


def repair_draft(world, draft, assets):
    count = 0
    for path, body in draft.items():
        if not path.endswith('.layer'):
            continue
        repaired, matches = SPAWN_REFERENCE.subn(
            lambda match: match[0].replace(ORIGINAL_REFERENCE, CHILD_REFERENCE), body)
        if ORIGINAL_REFERENCE in repaired:
            raise ValueError(f'unreviewed Zagoria reference in {path}')
        if matches:
            draft[path] = repaired
            count += matches
    if count != PLACEMENTS[world]:
        raise ValueError(f'expected {PLACEMENTS[world]} reviewed Zagoria references in {world}, found {count}')
    for path, body in draft.items():
        if path.startswith('Missions/') and path.endswith('.conf'):
            draft[path] = pin_parent(body)
    draft.update(assets)
    return count


def corrected_prefab(source):
    if sha256(source.encode()).hexdigest() != ORIGINAL_PREFAB_SHA256:
        raise ValueError('expected the reviewed Zagoria 1.0.26 Olive prefab')
    lines = source.splitlines()
    start = lines.index(SIMULATION)
    simulation = lines[start:block_end(lines, start) + 1]
    simulation[simulation.index(RETIRED_SIMULATION)] = REPLACEMENT_SIMULATION

    # The new Simulation instance replaces the inherited differential list.
    # DIFFC was a locked one-output relay with the default 1:1 ratio.
    relay = simulation.index('     Differential DIFFC {')
    del simulation[relay:block_end(simulation, relay) + 1]
    simulation[simulation.index('      Output1 "DIFFC"')] = '      Output1 "DIFFA3"'
    identity, path = ZAGORIA_BMP2
    return '\n'.join([f'Vehicle : "{{{identity}}}{path}" {{', ' components {',
                      *simulation, ' }', '}', ''])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-prefab', type=Path, required=True,
                        help='exact text extracted from Zagoria 1.0.26 data.pak')
    parser.add_argument('--archive', type=Path, required=True,
                        help='Zagoria 1.0.26 workshop addon directory')
    parser.add_argument('--stage', type=Path, required=True,
                        help='external review stage outside the Missions addon')
    args = parser.parse_args()

    try:
        with (args.archive / 'data.pak').open('rb') as package:
            fingerprint = file_digest(package, 'sha256').hexdigest()
        if ZAGORIA_DRIVETRAIN_FAILURE.get(fingerprint) != '1.0.26':
            raise ValueError('expected the reviewed Zagoria 1.0.26 archive')
        registry = StockPrefabs(args.archive / 'resourceDatabase.rdb')
        if not registry.contains(*ZAGORIA_BMP2):
            raise ValueError('archive lacks the authored Olive prefab registration')

        identity, path = WRAPPER
        prefab = corrected_prefab(args.native_prefab.read_text(encoding='utf-8-sig'))
        outputs = {args.stage / path: prefab.encode(),
                   args.stage / (path + '.meta'): resource_metadata(path, identity, 'EntityTemplateResourceClass')}
        for output, content in outputs.items():
            if output.exists() and output.read_bytes() != content:
                raise ValueError(f'staged output already differs: {output}')
        for output, content in outputs.items():
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
