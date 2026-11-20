"""Generate the portfolio comparison and reusable mission-specific briefing presets."""
import argparse
from collections import defaultdict
import json
import re
from pathlib import Path, PurePosixPath

from briefings import guid, read_pages, render
from catalog import Snapshot, WORLD, compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--source', default='origin/master')
    parser.add_argument('--dev', default='origin/dev')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--stage', type=Path, help='empty or identical output folder outside either addon')
    args = parser.parse_args()
    source = Snapshot(args.missions, args.source)
    dev = Snapshot(args.missions, args.dev)
    lobby = Path(__file__).resolve().parents[3]
    template = (lobby / 'Prefabs/MP/Modes/GC_LobbyGameMode.et.meta').read_text(encoding='utf-8')
    report = {'source': source.commit, 'target': dev.commit, 'headers': {}, 'worlds': {}}
    out = {}
    headers_by_world = defaultdict(list)
    target_headers = set(dev.headers())
    for header in sorted(source.headers()):
        match = WORLD.search(source.read(header))
        worlds = source.resources.get(match[1].upper(), []) if match else []
        declared = re.search(r'^\s*World\s+"\{\w+\}([^"]+)"', source.read(header), re.M)
        row = {'worlds': worlds, 'declaredPath': declared[1] if declared else None,
               'devHeaderAtSamePath': header in target_headers}
        report['headers'][header] = row
        for world in worlds:
            if len(worlds) > 1 and declared and world.lower() != declared[1].lower():
                continue
            headers_by_world[world].append(header)

    for world in sorted(p for p in source.files if p.lower().endswith('.ent')):
        row = compare(source, dev, world)
        row['headers'] = headers_by_world[world]
        report['worlds'][world] = row
        pages, gaps = [], []
        for layer in source.layers[world.lower()]:
            text = source.read(layer)
            # 9a681ab9 added the expanded notes alongside its parent's version.
            # Apply the author's newer text only to this exact committed blob.
            if source.files[layer] == '07a115a929850eee77382a911e9511d5b280d448':
                text = re.sub(r'^<<<<<<< Updated upstream\n.*?^=======\n(.*?)^>>>>>>> Stashed changes\n',
                              r'\1', text, count=1, flags=re.M | re.S)
                row['briefingResolution'] = 'Expanded notes introduced in 9a681ab9'
            found, issues = read_pages(text, layer)
            pages.extend(found)
            gaps.extend(issues)
        row['directBriefingsRead'] = len(pages)
        # Nested and script-created pages need their owning resources converted.
        for path in sorted(source.closure(world)):
            if not path.lower().endswith(('.et', '.layer')) or path in source.layers[world.lower()]:
                continue
            if 'PS_MissionDescription' in source.read(path):
                gaps.append(path + ' contains an inherited or prefab-owned briefing')
        if pages and not gaps:
            try:
                body, compact = render(world, pages)
                # Keep scenario settings in the owning mission directory under a unique name.
                dest = str(PurePosixPath(world).with_suffix('')) + '_GC_Settings.et'
                resource = guid(world + '/resource')
                if source.resources.get(resource) or any(p != dest for p in dev.resources.get(resource, [])):
                    raise ValueError('generated resource GUID collision')
                old_name = next(line.strip() for line in template.splitlines() if line.strip().startswith('Name '))
                meta = template.replace(old_name, f'Name "{{{resource}}}{dest}"')
                for path, contents in [(dest, body), (dest + '.meta', meta)]:
                    if path in dev.files and dev.read(path).strip() != contents.strip():
                        raise ValueError('Dev already has differing ' + path)
                out[dest] = body
                out[dest + '.meta'] = meta
                row['briefingModePreset'] = dest
                row['renumberedInSourceOrder'] = compact
            except ValueError as exc:
                gaps.append(str(exc))
        row['briefingGaps'] = gaps
    report['devOnlyHeaders'] = sorted(target_headers - set(report['headers']))
    if args.stage:
        stage = args.stage.resolve()
        if any(stage.is_relative_to(root.resolve()) for root in (args.missions, lobby)):
            parser.error('stage outside both addon trees')
        # Check the whole generation before writing. Never overlay stale outputs.
        if stage.exists():
            existing = {p.relative_to(stage).as_posix(): p for p in stage.rglob('*') if p.is_file()}
            for path, file in existing.items():
                if path not in out or file.read_bytes() != out[path].encode():
                    parser.error(f'staging directory contains differing file {path}')
        for path, contents in out.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents.encode())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes((json.dumps(report, indent=2, ensure_ascii=False) + '\n').encode())
    print(f"{len(report['headers'])} headers, {len(report['worlds'])} worlds, {len(out)//2} briefing presets")


if __name__ == '__main__':
    main()
