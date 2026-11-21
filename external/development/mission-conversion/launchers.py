"""Prepare launcher resources in an external staging directory for world assembly."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from catalog import Snapshot


def field(pattern):
    return re.compile(r'^(\s*' + pattern + r'\s+")\{([0-9A-Fa-f]{16})\}([^"\r\n]+)(")', re.M)


WORLD_REF = field('World')
META_NAME = field('Name')
SHARED_ID = '321510A1C6838890'
BUSTERS = 'worlds/Corax/SerriffBusters/SerrifBusters.ent'
TOPDOWN = 'worlds/Delfer/Topdown/Topdown.ent'
LEGACY_HEADER_TYPE = re.compile(rb'\A((?:\xef\xbb\xbf)?[ \t\r\n]*)SCR_MissionHeader(?=\s*\{)')
GC_HEADER_TYPE = re.compile(rb'\A(?:\xef\xbb\xbf)?[ \t\r\n]*GC_MissionHeader(?=\s*\{)')


def replace_reference(text, pattern, guid, path):
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f'expected one resource reference for {path}')
    match = matches[0]
    return text[:match.start(2)] + guid + '}' + path + text[match.end(3):]


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
    files = {
        header: replace_reference(text, WORLD_REF, world_id, world),
        header + '.meta': replace_reference(header_meta, META_NAME, header_id, header),
        world + '.meta': replace_reference(source.read(world + '.meta'), META_NAME, world_id, world),
    }
    return world, world_id, files


def prepare_metadata_headers(dev, plan, headers):
    """Stage only the root type of file-cleared launchers for the native metadata exporter."""
    if plan.get('target') != dev.commit:
        raise ValueError('the metadata plan was generated for a different Dev commit')
    eligible = plan.get('committedLauncherMetadata')
    if not eligible:
        raise ValueError('the metadata plan contains no file-cleared launcher checks')
    selected = set(headers) if headers else set(eligible)
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
            continue
        updated, changed = LEGACY_HEADER_TYPE.subn(rb'\g<1>GC_MissionHeader', body, count=1)
        if not changed:
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
    parser.add_argument('headers', nargs='*', help='launcher paths, defaults to all eligible launchers')
    args = parser.parse_args()
    stage = args.stage.resolve()
    if any(stage.is_relative_to(root.resolve()) for root in [args.missions, Path(__file__).resolve().parents[3]]):
        parser.error('use an external staging directory')
    dev = Snapshot(args.missions, args.dev)
    if args.prepare_metadata:
        try:
            output, count, unsupported = prepare_metadata_headers(
                dev, json.loads(args.prepare_metadata.read_text(encoding='utf-8')), args.headers)
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
