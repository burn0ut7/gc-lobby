"""Prepare launcher resources in an external staging directory for world assembly."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from briefings import block_end
from catalog import GC_HEADER_NAMES, Snapshot


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


def replace_reference(text, pattern, guid, path):
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f'expected one resource reference for {path}')
    match = matches[0]
    return text[:match.start(2)] + guid + '}' + path + text[match.end(3):]


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
    body = replace_reference(text, WORLD_REF, world_id, world)
    files = {
        header: native_header(body.encode()).decode(),
        header + '.meta': replace_reference(header_meta, META_NAME, header_id, header),
        world + '.meta': replace_reference(source.read(world + '.meta'), META_NAME, world_id, world),
    }
    return world, world_id, files


def merge_metadata_header(dev, header, world, capture, body):
    """Retain the authoritative header and copy only the native export's snapshot."""
    world_files = dev.closure(world) | {header + '.meta'}
    world_files.update(path + '.meta' for path in tuple(world_files) if path + '.meta' in dev.files)
    for path in sorted(world_files):
        original = dev.read_raw(path)
        captured = (capture / path).read_bytes()
        if original.replace(b'\r\n', b'\n') != captured.replace(b'\r\n', b'\n'):
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
        if (not identity or identity[3] != header or dev.resources.get(identity[2].upper()) != [header]
                or not world or dev.resources.get(world[2].upper()) != [world[3]]):
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
