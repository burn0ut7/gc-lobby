"""Prepare launcher resources in an external staging directory for world assembly."""
import argparse
import hashlib
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--source', default='origin/master')
    parser.add_argument('--dev', default='origin/dev')
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('headers', nargs='*', help='source paths, defaults to every legacy launcher')
    args = parser.parse_args()
    stage = args.stage.resolve()
    if any(stage.is_relative_to(root.resolve()) for root in [args.missions, Path(__file__).resolve().parents[3]]):
        parser.error('use an external staging directory')
    source = Snapshot(args.missions, args.source)
    dev = Snapshot(args.missions, args.dev)
    headers = set(source.headers())
    selected = set(args.headers) if args.headers else headers
    if selected - headers:
        parser.error('unknown launcher: ' + ', '.join(sorted(selected - headers)))
    output = {}
    for header in sorted(selected):
        _, _, files = launcher(source, dev, header)
        for path, contents in files.items():
            if path in output and output[path] != contents:
                parser.error(f'conflicting output for {path}')
            output[path] = contents
    for path in stage.rglob('*'):
        if path.is_file() and output.get(path.relative_to(stage).as_posix(), None) != path.read_bytes().decode('utf-8'):
            parser.error(f'staging directory contains differing file {path}')
    for path, contents in output.items():
        target = stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents.encode())
    print(f'{len(selected)} launchers, {len(output)} files')


if __name__ == '__main__':
    main()
