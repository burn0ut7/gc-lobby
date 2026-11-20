"""Generate the portfolio comparison and reusable mission-specific briefing presets."""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
import re
from pathlib import Path, PurePosixPath

from boundaries import convert_boundaries
from briefings import guid, read_pages, render
from catalog import Snapshot, WORLD, compare
from events import world_events
from groups import group_layer
import gwotline
import hnevice
from launchers import launcher
from markers import convert_layer as convert_markers
from respawns import read_respawns, ticket_event
from scenery import StockPrefabs, placements
from race_ruha import ruha_files, WORLD as RUHA_WORLD
from race_day import PRIOR_KUNAR_LAYERS, race_files, WORLD as RACE_WORLD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--source', default='origin/master')
    parser.add_argument('--dev', default='origin/dev')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--stage', type=Path, help='empty or identical output folder outside either addon')
    parser.add_argument('--include-race-day', action='store_true', help='also generate the reviewed Kunar world recipe')
    parser.add_argument('--stage-marker-candidates', action='store_true', help='stage icon layers for visual and asset review')
    parser.add_argument('--base-database', type=Path, help='verify and stage unchanged stock scenery and waypoints using game data/resourceDatabase.rdb')
    parser.add_argument('--units', type=Path, help='compare playable group placements against both Units revisions')
    parser.add_argument('--units-source', default='origin/master')
    parser.add_argument('--units-dev', default='origin/dev')
    parser.add_argument('--include-ruha', action='store_true', help='generate the reviewed Ruha race world')
    parser.add_argument('--include-hnevice-candidate', action='store_true', help='stage the incomplete Hnevice playtest world externally')
    parser.add_argument('--include-gwotline-mode', action='store_true', help='stage the reviewed Gwotline Bling mode placement for assembly')
    parser.add_argument('--include-gwotline-candidate', action='store_true', help='assemble the incomplete Gwotline Bling playtest world externally')
    args = parser.parse_args()
    if args.include_gwotline_candidate:
        if not args.stage or not args.units or not args.base_database:
            parser.error('the Gwotline playtest candidate requires --stage, --units and --base-database')
        args.include_gwotline_mode = True
    source = Snapshot(args.missions, args.source)
    dev = Snapshot(args.missions, args.dev)
    stock = StockPrefabs(args.base_database) if args.base_database else None
    locally_owned = set(source.resources) | set(dev.resources)
    source_paths = {p.casefold() for p in source.files}
    target_paths = {p.casefold(): p for p in dev.files}
    lobby = Path(__file__).resolve().parents[3]
    template = (lobby / 'Prefabs/MP/Modes/GC_LobbyGameMode.et.meta').read_text(encoding='utf-8')
    report = {'source': source.commit, 'target': dev.commit, 'headers': {}, 'worlds': {}}
    if stock:
        report['baseResourceDatabaseSHA256'] = sha256(stock.data).hexdigest()
    if args.units:
        source_units = Snapshot(args.units, args.units_source)
        dev_units = Snapshot(args.units, args.units_dev)
        report['sourceUnits'] = source_units.commit
        report['targetUnits'] = dev_units.commit
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
        try:
            world, world_id, _ = launcher(source, dev, header)
            row['destinationWorld'] = '{' + world_id + '}' + world
            headers_by_world[world].append(header)
        except ValueError as exc:
            row['launcherGap'] = str(exc)

    for world in sorted(p for p in source.files if p.lower().endswith('.ent')):
        row = compare(source, dev, world)
        row['headers'] = headers_by_world[world]
        report['worlds'][world] = row
        pages, gaps = [], []
        respawns, ticket_gaps = [], []
        row['markerLayers'] = {}
        for layer in source.layers[world.lower()]:
            text = source.read(layer)
            if world not in (RACE_WORLD, RUHA_WORLD):
                if args.include_gwotline_mode and layer == gwotline.AO:
                    ao_body, instances = gwotline.stage_boundaries(source)
                else:
                    ao_body, instances = convert_boundaries(text)
                if instances:
                    audit = {'instances': instances}
                    row.setdefault('boundaryLayers', {})[layer] = audit
                    if ao_body:
                        dest = str(PurePosixPath(layer).with_suffix('')) + '_GC_Boundaries.layer'
                        if dest.casefold() in source_paths:
                            raise ValueError('boundary output collides with source: ' + dest)
                        reviewed_candidate = (
                            args.include_hnevice_candidate and world == hnevice.WORLD
                            and layer == hnevice.FOLDER + 'default.layer'
                        )
                        if layer in dev.files and not reviewed_candidate:
                            audit['gap'] = 'Dev already contains the original layer path; review before adding boundary siblings'
                        elif dest.casefold() in target_paths and (target_paths[dest.casefold()] != dest or dev.read(dest).strip() != ao_body.strip()):
                            audit['gap'] = 'Dev has differing boundary output'
                        else:
                            out[dest] = ao_body
                            audit['placementLayer'] = dest
            records, issues = read_respawns(text, layer)
            respawns.extend(records)
            ticket_gaps.extend(issues)
            if args.units:
                try:
                    groups = group_layer(text, source_units, dev_units, stock, locally_owned)
                    if groups:
                        body, count, slots = groups
                        if layer in dev.files and dev.read(layer).strip() != body.strip():
                            raise ValueError('Dev has different group placements')
                        row.setdefault('groupLayers', {})[layer] = {'playableGroups': count, 'unitPrefabEntries': slots}
                        out[layer] = body
                except ValueError as exc:
                    row.setdefault('groupLayers', {})[layer] = {'gap': str(exc)}
            if stock:
                placement = placements(text, stock, locally_owned)
                if placement:
                    kinds, count = placement
                    entry = {'types': kinds, 'placements': count}
                    if layer in dev.files and dev.files[layer] != source.files[layer]:
                        entry['gap'] = 'Dev layer differs from source'
                    else:
                        out[layer] = source.blobs[source.files[layer]].decode('utf-8')
                    row.setdefault('stockPlacementLayers', {})[layer] = entry
            try:
                markers = convert_markers(text)
                if markers:
                    body, count = markers
                    if layer in dev.files and dev.read(layer).strip() != body.strip():
                        raise ValueError('Dev has different layer content')
                    row['markerLayers'][layer] = {'candidateIcons': count}
                    if args.stage_marker_candidates:
                        out[layer] = body
            except ValueError as exc:
                row['markerLayers'][layer] = {'gap': str(exc)}
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
        event = ticket_event(world, respawns, ticket_gaps)
        if respawns or ticket_gaps:
            follow_up = []
            if any(r['tickets'] != 0 for r in respawns):
                follow_up.append('Compare eligible dead slots, ticket consumption on failed replacement and disconnect handling')
            if any(r['tickets'] > 0 and not r['wave'] for r in respawns):
                follow_up.append('Restore individual death-to-respawn scheduling using the recorded interval in milliseconds')
            if any(r['tickets'] > 0 and r['wave'] for r in respawns):
                follow_up.append('Restore wave scheduling aligned to legacy world time')
            if any(r['tickets'] != 0 and r['destination'] for r in respawns):
                follow_up.append('Resolve the named destination at death and preserve original-slot fallback')
            if any(r['tickets'] == -1 for r in respawns):
                follow_up.append('Port the ordered per-character replacement prefab list and exhaustion behavior')
            row['respawn'] = {'factions': respawns, 'ticketGaps': ticket_gaps, 'remainingBehavior': follow_up}
        briefing = pages if not gaps else []
        messages, review = ([], {}) if world in (RACE_WORLD, RUHA_WORLD) else world_events(source, world)
        if review:
            row['frameworkEvents'] = review
        mode_events = event
        if messages:
            mode_events = ['   m_aMissionEvents + {', *event[1:-1], *messages, '   }']
        if briefing or mode_events:
            try:
                body, compact = render(world, briefing, mode_events)
                # Keep scenario settings in the owning mission directory under a unique name.
                dest = str(PurePosixPath(world).with_suffix('')) + '_GC_Settings.et'
                resource = guid(world + '/resource')
                if source.resources.get(resource) or any(p != dest for p in dev.resources.get(resource, [])):
                    raise ValueError('generated resource GUID collision')
                old_name = next(line.strip() for line in template.splitlines() if line.strip().startswith('Name '))
                meta = template.replace(old_name, f'Name "{{{resource}}}{dest}"')
                for path, contents in [(dest, body), (dest + '.meta', meta)]:
                    if path in dev.files and dev.read(path).strip() != contents.strip():
                        prior_versions = [render(world, briefing, event)[0], render(world, briefing)[0]]
                        if path != dest or dev.read(path).strip() not in (v.strip() for v in prior_versions):
                            raise ValueError('Dev already has differing ' + path)
                out[dest] = body
                out[dest + '.meta'] = meta
                if briefing:
                    row['briefingModePreset'] = dest
                    row['renumberedInSourceOrder'] = compact
                if messages:
                    row['notificationModePreset'] = dest
                if event:
                    row['respawn']['ticketModePreset'] = dest
            except ValueError as exc:
                gaps.append(str(exc))
        row['briefingGaps'] = gaps
    report['devOnlyHeaders'] = sorted(target_headers - set(report['headers']))
    for enabled, world, recipe in [(args.include_race_day, RACE_WORLD, race_files), (args.include_ruha, RUHA_WORLD, ruha_files)]:
        if not enabled:
            continue
        if 'briefingModePreset' not in report['worlds'][world]:
            parser.error(f'{world} needs a verified generated briefing preset')
        generated = recipe(source)
        for path, data in generated.items():
            if path in dev.files and dev.read(path).strip() != data.strip():
                if world != RACE_WORLD or dev.files[path] != PRIOR_KUNAR_LAYERS.get(path):
                    parser.error(f'Dev already has differing {path}')
            out[path] = data
        report['worlds'][world]['raceConversion'] = {
            'generatedFiles': len(generated),
            'presentOnDev': all(path in dev.files and dev.read(path).strip() == data.strip()
                                for path, data in generated.items()),
            'remainingBehavior': [
                'Verify a driven finish with the race motorbike',
                'Compare legacy staging countdown, JIP and remote clients',
            ],
        }
        if world == RACE_WORLD:
            report['worlds'][world]['raceConversion']['remainingBehavior'].append(
                'Check both course gates and adjacent objects before and after match-start cleanup')
        if world == RUHA_WORLD:
            report['worlds'][world]['raceConversion']['remainingBehavior'].extend([
                'Compare transfer action reach, map rendering and roadblock cleanup in play',
                'Check boundary at the five-centimetre crossover adjustment',
            ])
    if args.include_hnevice_candidate:
        if not args.stage or not args.units:
            parser.error('the Hnevice playtest candidate requires --stage and --units')
        row = report['worlds'][hnevice.WORLD]
        candidate = hnevice.assemble(source, dev, row, out)
        for path, data in candidate.items():
            if path in dev.files and dev.read(path).strip() != data.strip():
                parser.error(f'Dev already has differing {path}')
            out[path] = data
        row['playtestCandidate'] = {
            'presentOnDev': all(path in dev.files for path in candidate),
            'remainingBehavior': hnevice.GAPS,
        }
    if args.include_gwotline_mode:
        if not args.stage:
            parser.error('the Gwotline Bling mode placement requires --stage')
        row = report['worlds'][gwotline.WORLD]
        mode, audit = gwotline.stage_mode(source, row['briefingModePreset'])
        path = gwotline.DEFAULT.removesuffix('.layer') + '_GC_Mode.layer'
        if path in dev.files and dev.read(path).strip() != mode.strip():
            parser.error(f'Dev already has differing {path}')
        out[path] = mode
        row['modeCandidate'] = {'placementLayer': path, 'casualtyNotifications': audit,
                                'remainingBehavior': gwotline.GAPS}
        if stock:
            row['sceneCandidates'] = {}
            for path, (body, placement_count, icons) in gwotline.stage_scene(source, stock, locally_owned).items():
                if path in dev.files and dev.read(path).strip() != body.strip():
                    parser.error(f'Dev already has differing {path}')
                out[path] = body
                row['sceneCandidates'][path] = {'stockPlacements': placement_count, 'icons': icons}
        if args.units:
            row['rosterCandidates'] = {}
            for path, body, roster in gwotline.stage_rosters(source, source_units, dev_units, stock, locally_owned):
                if path in dev.files and dev.read(path).strip() != body.strip():
                    parser.error(f'Dev already has differing {path}')
                out[path] = body
                row['rosterCandidates'][path] = roster
        if args.include_gwotline_candidate:
            candidate = gwotline.assemble(source, dev, mode)
            for candidate_path, body in candidate.items():
                if candidate_path in dev.files and dev.read(candidate_path).strip() != body.strip():
                    parser.error(f'Dev already has differing {candidate_path}')
                out[candidate_path] = body
            del out[row['modeCandidate']['placementLayer']]
            row['modeCandidate']['placementLayer'] = gwotline.DEFAULT
            row['playtestCandidate'] = {
                'presentOnDev': all(candidate_path in dev.files for candidate_path in candidate),
                'remainingBehavior': gwotline.GAPS,
            }
    if args.stage:
        stage = args.stage.resolve()
        if any(stage.is_relative_to(root.resolve()) for root in ([args.missions, lobby] + ([args.units] if args.units else []))):
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
    presets = sum('briefingModePreset' in row for row in report['worlds'].values())
    print(f"{len(report['headers'])} headers, {len(report['worlds'])} worlds, {presets} briefing presets")


if __name__ == '__main__':
    main()
