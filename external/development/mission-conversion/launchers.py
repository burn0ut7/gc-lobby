"""Prepare launcher resources in an external staging directory for world assembly."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from briefings import block_end
from catalog import GC_HEADER, GC_HEADER_NAMES, Snapshot
from dependencies import BASE_GAME


def field(pattern):
    return re.compile(r'^(\s*' + pattern + r'\s+")\{([0-9A-Fa-f]{16})\}([^"\r\n]+)(")', re.M)


WORLD_REF = field('World')
META_NAME = field('Name')
SHARED_ID = '321510A1C6838890'
BUSTERS = 'worlds/Corax/SerriffBusters/SerrifBusters.ent'
TOPDOWN = 'worlds/Delfer/Topdown/Topdown.ent'
PREFIX = rb'\A(?:\xef\xbb\xbf)?[ \t\r\n]*'
LEGACY_HEADER_TYPE = re.compile(PREFIX + rb'(SCR_MissionHeader(?:ScenarioFramework)?)(?=\s*\{)')
GC_HEADER_TYPE = re.compile(PREFIX + GC_HEADER_NAMES.encode() + rb'(?=\s*\{)')
NATIVE_TYPES = {
    b'SCR_MissionHeader': b'GC_MissionHeader',
    b'SCR_MissionHeaderScenarioFramework': b'GC_ScenarioFrameworkMissionHeader',
}
GENERATED = re.compile(r' m_GC_GeneratedMetadata GC_GeneratedMissionMetadata(?: "[^"]+")? \{')
PLAYER_SLOT = re.compile(r'   GC_MissionMetadataSlot(?: "[^"]+")? \{')
ADVERTISED_SLOTS = re.compile(r'^ m_iPlayerCount (\d+)[ \t]*$', re.M)
ADDON_VERSIONS_FIELD = re.compile(r'^ m_aRequiredAddonVersions\b', re.M)
ADDON_VERSION = re.compile(r'"([\da-fA-F]{16})=([0-9]+(?:\.[0-9]+){1,3})"')
REVIEWED_VERSION_PINS = {
    'Missions/Blue/TVT39_Direction_Found.conf': (
        'ca24752458e9292e2d69668ccd46ed318c2abcc5',
        (('worlds/Blue/DirectionFound/DirectionFound_Layers/BLUFOR/Static.layer',
          'bdc881fd99428dc0f38128c05853ebbe3a854692', '5E5C154FEE1094BB=1.0.17'),),
    ),
    'Missions/arc/HotBoxes.conf': (
        '93a505ed61230b9baba1b0e8489006363a650147',
        (('worlds/arc/HotBoxes/HotBoxes_Layers/vehicles.layer',
          '65033744336c167e0865849fd0e1fe4f37df2020', '5E5C154FEE1094BB=1.0.17'),),
    ),
    'Missions/TheLocalPub/OilRigRangos.conf': (
        '3cbbf3fea039dc0f84d984fe4123d5cf63b807c7',
        (('worlds/TheLocalPub/OilRigRangos/Prefabs/Characters/Factions/BLUFOR/UK_Army/2024/VBSS/'
          'Character_UK_2024_RM_Pilot_P.et', '565a11585ed8fa29b4f16ffbfc43bffb98b24e6c', '5E8CA06EE59E7F13=1.2.65'),),
    ),
    'Missions/Jub/OperationArrowheadMulladostPass.conf': (
        'cb25c94860ef584a0110dc41ccfeb81c576bf4dd',
        (('worlds/Jub/OperationArrowheadMulladostPass/OperationArrowheadMulladostPass_Layers/Vehicles.layer',
          '80be4ccc0bdcbbd12f984e3e02e1bb1013eeeede', '6303360DA719E832=8.4.0'),),
    ),
    'Missions/Jub/OperationJaws.conf': (
        '7045bc4a9cb6949bf88d3d2815c9bafec58b95e3',
        (('worlds/Jub/OperationJaws/OperationJaws_Layers/vehicles.layer',
          '54aba909966dd13e2d2916230061aa398cd3f4d0', '5D1880C4AD410C14=2.1.25'),),
    ),
    'Missions/arc/UrbanTroubles.conf': (
        '44c5ff26490d165afbcaab0a572682cb99679a57',
        (('worlds/arc/UrbanTroubles/UrbanTroubles_Layers/props.layer',
          'a20ad2c82ffd839ee41b6f2e3aa53fa5e2f96be8', '59EAA899751805DF=1.3.0'),),
    ),
    'Missions/Huska/WindyGoat.conf': (
        'd7598feec3f1abf731fe149ea80be2fde129c041',
        (('Missions/Huska/Prefabs/Gabe_P.et', '76c5a6c7b1d5255aee20fabc0221fb85df347e64',
          '61BD6595183FCEBD=1.0.56'),),
    ),
    'Missions/Til/MetsalaAirfield.conf': (
        '24e9b6684a21e9e9d41e187999f55e9522f7199f',
        (('worlds/Til/MetsalaAirfield/MetsalaAirfield_Layers/ru.layer',
          'd2b64a9a0d8d2081238c2aff1ee520b4b42a6b61', '628933A0D3A0D700=8.4.0'),
         ('worlds/Til/MetsalaAirfield/MetsalaAirfield_Layers/props.layer',
          'bf64e6ba099e6921f5e62ae93c884943b669fac1', '653CB36244ADBE0F=0.3.2')),
    ),
}


def authored_header_text(body):
    if not GC_HEADER.match(body):
        return body
    lines = body.splitlines()
    for start, line in enumerate(lines):
        if GENERATED.fullmatch(line):
            stop = block_end(lines, start)
            # The snapshot also records diagnostic origins, which are not engine resource paths.
            lines[start:stop + 1] = [''] * (stop - start + 1)
    return '\n'.join(lines)


def snapshot_player_slots(body):
    """Return the saved slot count, with None for headers without a native snapshot."""
    lines = body.splitlines()
    for start, line in enumerate(lines):
        if GENERATED.fullmatch(line):
            end = block_end(lines, start)
            return sum(bool(PLAYER_SLOT.fullmatch(entry)) for entry in lines[start + 1:end])
    return None


def advertised_player_slots(body):
    match = ADVERTISED_SLOTS.search(body)
    return int(match[1]) if match else None


def required_addon_versions(body):
    declarations = list(ADDON_VERSIONS_FIELD.finditer(body))
    if not declarations:
        return ()
    block = re.match(r'\s*\{([^{}]*)\}', body[declarations[0].end():])
    if len(declarations) != 1 or not block or ADDON_VERSION.sub('', block[1]).strip():
        raise ValueError('invalid required addon version block')
    pins = {}
    for resource, version in ADDON_VERSION.findall(block[1]):
        guid = resource.upper()
        if guid in (BASE_GAME, '5614BBCCBB55ED1C') or guid in pins:
            raise ValueError(f'duplicate or stock addon pin {guid}')
        pins[guid] = version
    return tuple(sorted(pins.items()))


def replace_reference(text, pattern, guid, path):
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f'expected one resource reference for {path}')
    match = matches[0]
    return text[:match.start(2)] + guid + '}' + path + text[match.end(3):]


def registered_identity(snapshot, guid, path):
    if snapshot.resources.get(guid.upper()) != [path] or path + '.meta' not in snapshot.files:
        return False
    identity = META_NAME.search(snapshot.read(path + '.meta'))
    return bool(identity and identity[2].upper() == guid.upper() and identity[3] == path)


def native_header(body):
    match = LEGACY_HEADER_TYPE.match(body)
    if not match:
        return body
    return body[:match.start(1)] + NATIVE_TYPES[match[1]] + body[match.end(1):]


def launcher(source, dev, header):
    text = source.read(header)
    match = WORLD_REF.search(text)
    if not match:
        raise ValueError(f'{header} has no world reference')
    old_id, declared = match[2].upper(), match[3]
    paths = source.resources.get(old_id, [])
    world_id = old_id
    if len(paths) == 1 and paths[0].lower().endswith('.ent'):
        world = paths[0]
    elif old_id == SHARED_ID and set(paths) == {BUSTERS, TOPDOWN}:
        world = next((p for p in paths if p.lower() == declared.lower()), None)
        if not world:
            raise ValueError(f'{header} has an ambiguous world path')
        if world == TOPDOWN:
            world_id = hashlib.sha256(('gc-missions-dev/world/' + world).encode()).hexdigest()[:16].upper()
            if world_id in source.resources:
                raise ValueError(f'{world_id} already belongs to another source resource')
    else:
        raise ValueError(f'{header} has unresolved or ambiguous world {old_id}')
    if header + '.meta' in source.files:
        header_meta = source.read(header + '.meta')
    elif header == 'Missions/Corax/LooseEnds/CO57_Loose_Ends.conf':
        # Its header was restored without the metadata removed at 5489ae66.
        header_meta = subprocess.check_output([
            'git', '-C', str(source.repo), 'cat-file', 'blob',
            '699312a17b1d4c4058a6c6810984329088ff5d81',
        ]).decode('utf-8-sig').replace('\r\n', '\n')
    else:
        raise ValueError(f'{header} is missing its resource metadata')
    header_id = META_NAME.search(header_meta)[2].upper()
    if any(other != header for other in source.resources.get(header_id, [])):
        raise ValueError(f'{header_id} already belongs to another source header or resource')
    for guid, path in [(header_id, header), (world_id, world)]:
        if any(other.casefold() != path.casefold() for other in dev.resources.get(guid, [])):
            raise ValueError(f'{guid} already belongs to another Dev resource')
        if path in dev.files and path not in dev.resources.get(guid, []):
            raise ValueError(f'{path} already has a different Dev identity')
    body = native_header(replace_reference(text, WORLD_REF, world_id, world).encode()).decode()
    if header in REVIEWED_VERSION_PINS:
        header_blob, reviewed_assets = REVIEWED_VERSION_PINS[header]
        if source.files[header] == header_blob and all(source.files.get(path) == blob
                                                      for path, blob, _ in reviewed_assets):
            # Keep the package version verified for these authored resources.
            pins = ''.join(f'  "{pin}"\n' for _, _, pin in reviewed_assets)
            body = body.rstrip().removesuffix('}') + f' m_aRequiredAddonVersions {{\n{pins} }}\n}}\n'
    files = {
        header: body,
        header + '.meta': replace_reference(header_meta, META_NAME, header_id, header),
        world + '.meta': replace_reference(source.read(world + '.meta'), META_NAME, world_id, world),
    }
    return world, world_id, files


def merge_metadata_header(dev, header, world, capture, body):
    """Retain the authoritative header and copy only the native export's snapshot."""
    world_files = dev.closure(world) | {header + '.meta'}
    world_files.update(path + '.meta' for path in tuple(world_files) if path + '.meta' in dev.files)
    for path in sorted(world_files):
        captured = (capture / path).read_bytes()
        if path.lower().endswith(('.nmn', '.ntile')):
            # Verify native bytes directly against the tree without spawning Git for every tile.
            original = dev.files[path]
            digest = hashlib.new('sha1' if len(original) == 40 else 'sha256')
            digest.update(f'blob {len(captured)}\0'.encode('ascii'))
            digest.update(captured)
            matches = digest.hexdigest() == original
        else:
            original = dev.read_raw(path)
            matches = original.replace(b'\r\n', b'\n') == captured.replace(b'\r\n', b'\n')
        if not matches:
            raise ValueError(f'{path} differs from the captured Dev world')
    exported = (capture / header).read_text(encoding='utf-8-sig')
    matches = list(WORLD_REF.finditer(exported))
    before = WORLD_REF.search(dev.read(header))
    if len(matches) != 1 or matches[0].groups()[1:3] != before.groups()[1:3]:
        raise ValueError(f'{header} export identifies a different world')

    def metadata_span(lines):
        matches = [i for i, line in enumerate(lines) if GENERATED.fullmatch(line.rstrip('\r\n'))]
        if len(matches) > 1:
            raise ValueError(f'{header} has duplicate generated snapshots')
        if matches:
            start, = matches
            return start, block_end(lines, start) + 1
        return None

    export_lines = exported.splitlines()
    span = metadata_span(export_lines)
    if not span:
        raise ValueError(f'{header} has no native snapshot to merge')
    start, stop = span
    metadata = export_lines[start:stop]
    if ('  m_iSchemaVersion 2' not in metadata
            or f'  m_sWorld "{{{before[2]}}}{world}"' not in metadata
            or '  m_sResourceCoverage "authored_container_graph_partial"' not in metadata):
        raise ValueError(f'{header} snapshot has an unexpected schema, world or coverage')
    lines = body.decode('utf-8').splitlines(keepends=True)
    newline = '\r\n' if b'\r\n' in body else '\n'
    destination = metadata_span(lines)
    if destination:
        start, stop = destination
    else:
        start = stop = max((i for i, line in enumerate(lines) if line.rstrip('\r\n') == '}'), default=-1)
        if start < 1 or any(line.strip() for line in lines[start + 1:]):
            raise ValueError(f'{header} does not have a canonical root close')
    lines[start:stop] = [line + newline for line in metadata]
    return ''.join(lines).encode()


def prepare_metadata_headers(dev, plan, headers, capture=None):
    """Stage only the root type of file-cleared launchers for the native metadata exporter."""
    if plan.get('target') != dev.commit:
        raise ValueError('the metadata plan was generated for a different Dev commit')
    eligible = plan.get('committedLauncherMetadata')
    if not eligible:
        raise ValueError('the metadata plan contains no file-cleared launcher checks')
    selected = set(headers) if headers else {
        header for header in eligible if not capture or (capture / header).is_file()
    }
    if not selected:
        raise ValueError('no audited launcher headers were found in the capture directory')
    if selected - eligible.keys():
        raise ValueError('the plan did not clear these launchers: ' + ', '.join(sorted(selected - eligible.keys())))
    prepared, unsupported = {}, {}
    for header in sorted(selected):
        if header not in dev.files or not header.endswith('.conf'):
            raise ValueError(f'{header} is missing from Dev')
        body = dev.read_raw(header)
        world = WORLD_REF.search(dev.read(header))
        identity = META_NAME.search(dev.read(header + '.meta')) if header + '.meta' in dev.files else None
        if (not identity or not registered_identity(dev, identity[2], header)
                or not world or not registered_identity(dev, world[2], world[3])):
            raise ValueError(f'{header} has a missing or ambiguous launcher or world registration')
        if GC_HEADER_TYPE.match(body):
            if capture:
                updated = merge_metadata_header(dev, header, world[3], capture, body)
                if updated != body:
                    prepared[header] = updated
            continue
        if capture:
            raise ValueError(f'{header} needs a native GC header before merging its snapshot')
        updated = native_header(body)
        if updated == body:
            unsupported[header] = dev.read(header).lstrip().split(maxsplit=1)[0]
            continue
        prepared[header] = updated
    return prepared, len(selected), unsupported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--source', default='origin/master')
    parser.add_argument('--dev', default='origin/dev')
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--prepare-metadata', type=Path, metavar='PLAN',
                        help='stage GC header types for the file-cleared Dev launchers in this plan')
    parser.add_argument('--native-exports', type=Path, metavar='CAPTURE',
                        help='merge native metadata from an unchanged captured copy of the Dev mission files')
    parser.add_argument('headers', nargs='*', help='launcher paths, defaults to all eligible launchers')
    args = parser.parse_args()
    if args.native_exports and not args.prepare_metadata:
        parser.error('--native-exports requires --prepare-metadata')
    stage = args.stage.resolve()
    if any(stage.is_relative_to(root.resolve()) for root in [args.missions, Path(__file__).resolve().parents[3]]):
        parser.error('use an external staging directory')
    dev = Snapshot(args.missions, args.dev)
    if args.prepare_metadata:
        try:
            output, count, unsupported = prepare_metadata_headers(
                dev, json.loads(args.prepare_metadata.read_text(encoding='utf-8')), args.headers, args.native_exports)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    else:
        source = Snapshot(args.missions, args.source)
        headers = set(source.headers())
        selected = set(args.headers) if args.headers else headers
        if selected - headers:
            parser.error('unknown launcher: ' + ', '.join(sorted(selected - headers)))
        output = {}
        for header in sorted(selected):
            _, _, files = launcher(source, dev, header)
            for path, contents in files.items():
                body = contents.encode()
                if path in output and output[path] != body:
                    parser.error(f'conflicting output for {path}')
                output[path] = body
        count = len(selected)
        unsupported = {}
    for path in stage.rglob('*'):
        if path.is_file() and output.get(path.relative_to(stage).as_posix()) != path.read_bytes():
            parser.error(f'staging directory contains differing file {path}')
    for path, contents in output.items():
        target = stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
    print(f'{count} launchers, {len(output)} files')
    for header, root in unsupported.items():
        print(f'{header} requires review for header type {root}')


if __name__ == '__main__':
    main()
