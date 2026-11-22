"""Generate the portfolio comparison and reusable mission-specific briefing presets."""
import argparse
from collections import Counter, defaultdict
from functools import partial
from hashlib import sha256
import json
import re
from pathlib import Path, PurePosixPath
from subprocess import CalledProcessError, run

from boundaries import convert_boundaries
import breach
import arc_capture
import batch
import local_loadouts
from install_batch import resource_databases
from restore_units_roles import (verified_aircrew_roles, verified_group_rosters, verified_msv85_forced_roles,
                                 verified_msv85_wrappers, verified_roles, verified_soviet_aircrew_roles)
import vehicle_crew
import bugout
from briefings import BriefingAudience, STANDARD_LAYOUTS, guid, indexed_pages, read_pages, render
from catalog import REF, Snapshot, WORLD, compare
from delayed_extraction import collect as collect_delayed_extraction
from event_actions import ACTIONS
from events import world_events
from groups import (SLOTS, compare_ai_parents, convert_ai_instance, group_layer, inheritance, rhs_ai_characters,
                    uk_ai_characters,
                    unchanged_deprecated_stock_override, unchanged_disabled_playability, verified_rhs_forced_roles,
                    verified_stock_ai_group, verified_uk_forced_roles)
import gwotline
import hnevice
import industrial
import irish
from launchers import launcher
from markers import convert_layer as convert_markers
from markers import relocate_images
from outcomes import compile_outcomes
from respawns import authored_respawn_fields, read_respawns, ticket_event
from scenery import StockPrefabs, placements
from race_ruha import ruha_files, WORLD as RUHA_WORLD
from race_day import PRIOR_KUNAR_LAYERS, race_files, WORLD as RACE_WORLD
import wetfeet


PREPARED_SETTINGS_SOURCE = 'a75e404c331abce5185f315f47f533d9b8eccf00'
PREPARED_SETTINGS_REVISIONS = (
    '5e571911b33f85facef1f21cc62872d1a7e323de',
    '1e49382a6a20df4686844ca19cece47d18428a8f',
)


def prepared_settings_blobs(source):
    """Find exact generated ticket and notification presets from the original preparation."""
    if source.commit != PREPARED_SETTINGS_SOURCE:
        return {}
    blobs = defaultdict(set)
    for revision in PREPARED_SETTINGS_REVISIONS:
        tree = run(['git', '-C', str(source.repo), 'diff-tree', '--no-commit-id', '--no-renames',
                    '--no-abbrev', '--raw', '-r', '-z', revision, '--', 'worlds'],
                   capture_output=True)
        if tree.returncode:
            continue
        entries = iter(tree.stdout.rstrip(b'\0').split(b'\0'))
        for entry, path in zip(entries, entries):
            _, _, _, blob, status = entry.split()
            if status in (b'A', b'M') and path.endswith(b'_GC_Settings.et'):
                blobs[path.decode()].add(blob.decode())
    return blobs


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
    parser.add_argument('--include-breach-loadouts', action='store_true', help='stage Breach and Clear mission-owned loadouts and roster')
    parser.add_argument('--include-breach-candidate', action='store_true', help='assemble the incomplete Breach and Clear playtest world externally')
    parser.add_argument('--include-wetfeet-mode', action='store_true', help='stage the WetFeet capture and outcome logic externally')
    parser.add_argument('--include-wetfeet-rosters', action='store_true', help='stage both reviewed WetFeet rosters externally')
    parser.add_argument('--include-wetfeet-scene', action='store_true', help='stage WetFeet scenery, equipment and cleanup')
    parser.add_argument('--include-wetfeet-candidate', action='store_true', help='assemble the WetFeet playtest world externally')
    parser.add_argument('--workshop-addons', type=Path, help='installed Workshop addon directory for provider resource checks')
    parser.add_argument('--include-outcomes', action='store_true', help='audit and stage supported legacy outcome rule fragments')
    parser.add_argument('--include-industrial-candidate', action='store_true', help='assemble the Industrial Metal playtest world')
    parser.add_argument('--include-irish-candidate', action='store_true', help='assemble the Irish Checkpoint playtest world')
    parser.add_argument('--include-arc-capture-candidates', action='store_true', help='assemble the reviewed Arc capture missions')
    parser.add_argument('--include-bugout-candidate', action='store_true', help='assemble the reviewed Bug Out mission')
    parser.add_argument('--batch', action='store_true', help='stage complete drafts from common conversion rules and report blockers')
    args = parser.parse_args()
    if args.batch:
        if not (args.stage and args.units and args.base_database):
            parser.error('--batch requires --stage, --units and --base-database')
        args.include_outcomes = True
        args.stage_marker_candidates = True
    if args.include_bugout_candidate and not (args.stage and args.units and args.base_database and args.workshop_addons):
        parser.error('Bug Out requires --stage, --units, --base-database and --workshop-addons')
    if args.include_industrial_candidate:
        if not args.stage or not args.units or not args.workshop_addons or not args.base_database:
            parser.error('Industrial Metal requires --stage, --units, --workshop-addons and --base-database')
        args.include_outcomes = True
    if args.include_irish_candidate:
        if not args.stage or not args.units or not args.base_database:
            parser.error('Irish Checkpoint requires --stage, --units and --base-database')
        args.include_outcomes = True
    arc_worlds = arc_capture.WORLDS if args.include_arc_capture_candidates else {}
    if arc_worlds:
        if not args.stage or not args.units or not args.base_database or not args.workshop_addons:
            parser.error('the Arc capture worlds require --stage, --units, --base-database and --workshop-addons')
        args.include_outcomes = True
    if args.include_wetfeet_candidate:
        args.include_wetfeet_scene = args.include_wetfeet_rosters = True
    if args.include_breach_candidate:
        args.include_breach_loadouts = True
    if args.include_gwotline_candidate:
        if not args.stage or not args.units or not args.base_database:
            parser.error('the Gwotline playtest candidate requires --stage, --units and --base-database')
        args.include_gwotline_mode = True
    source = Snapshot(args.missions, args.source)
    dev = Snapshot(args.missions, args.dev)
    prepared_settings = prepared_settings_blobs(source) if args.batch else {}
    briefing_layouts = set(STANDARD_LAYOUTS)
    perfk_layout = 'worlds/Perfk/UI/MissionDescription/DescriptionEditableScrollable_size18.layout'
    if (source.files.get(perfk_layout) == '4808df7fdfd404e8ea0d0df16f7ddd93aac464bd'
            and source.resources.get('E7AB311330BF986C') == [perfk_layout]):
        briefing_layouts.add('{E7AB311330BF986C}' + perfk_layout)
    stock = StockPrefabs(args.base_database) if args.base_database else None
    locally_owned = set(source.resources) | set(dev.resources)
    source_paths = {p.casefold() for p in source.files}
    target_paths = {p.casefold(): p for p in dev.files}
    lobby = Path(__file__).resolve().parents[3]
    template = (lobby / 'Prefabs/MP/Modes/GC_LobbyGameMode.et.meta').read_text(encoding='utf-8')
    report = {'source': source.commit, 'target': dev.commit, 'headers': {}, 'worlds': {}}
    if stock:
        report['baseResourceDatabaseSHA256'] = sha256(stock.data).hexdigest()
    external_ai = {}
    external_vehicles = {}
    rhs_database = None
    uk_database = None
    workshop_spawn_resources = ()
    workshop_spawn_groups = ()
    workshop_damage_resources = ()
    external_vehicle_parents = {}
    if args.batch and args.workshop_addons:
        try:
            external_ai, rhs_database = rhs_ai_characters(args.workshop_addons)
            british_ai, uk_database = uk_ai_characters(args.workshop_addons)
            external_ai.update(british_ai)
            external_vehicles = vehicle_crew.workshop_vehicles(args.workshop_addons)
            external_vehicle_parents = vehicle_crew.workshop_vehicles(
                args.workshop_addons, vehicle_crew.WORKSHOP_VEHICLE_PARENTS)
            workshop_spawn_resources = (batch.workshop_resources(args.workshop_addons, batch.WORKSHOP_SPAWN_PREFABS)
                                        | set(external_vehicles.items()))
            workshop_spawn_groups = batch.workshop_resources(args.workshop_addons, batch.WORKSHOP_SPAWN_GROUPS)
            workshop_damage_resources = batch.workshop_resources(args.workshop_addons, batch.WORKSHOP_DAMAGE_TARGETS)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if rhs_database:
            report['rhsResourceDatabaseSHA256'] = sha256(rhs_database.data).hexdigest()
    stock_ai = {
        '22E43956740A6794': 'Prefabs/Characters/Factions/CIV/GenericCivilians/Character_CIV_Randomized.et',
        '8A97F7055F1A003A': 'Prefabs/Characters/Factions/CIV/GenericCivilians/Character_CIV_CottonShirt_6.et',
        '2CDFBF355F3410DD': 'Prefabs/Characters/Factions/CIV/Dockworker/Character_CIV_Dockworker_2.et',
        'D97EAE64721478F3': 'Prefabs/Characters/Factions/CIV/ConstructionWorker/Character_CIV_ConstructionWorker_Randomized.et',
        'D25BC9815A9F9E8D': 'Prefabs/Characters/Factions/INDFOR/FIA/Character_FIA_AT.et',
    }
    stock_ai.update({
        guid: f'Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_{role}.et'
        for guid, role in {
            '0B3167BB0FB68110': 'PL', '4FBA24F7BB43E17D': 'Sergeant', '3726077BE60962FF': 'RTO',
            '26A9756790131354': 'Rifleman', '5B1996C05B1E51A4': 'AR', '84029128FA6F6BB9': 'GL',
            'E398E44759DA1A43': 'TL', 'E45F1E163F5CA080': 'SL',
            '1623EA3AEFACA0E4': 'MG', '27BF1FF235DD6036': 'LAT', '6058AB54781A0C52': 'AMG',
            'C9E4FEAF5AAC8D8C': 'Medic', 'DE15FB5FAFC3E63F': 'Officer',
        }.items()
    })
    stock_ai.update({
        guid: f'Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_{role}.et'
        for guid, role in {
            '23ADBBC31B6A3DC6': 'AR', '426F7FAAC77A2A6D': 'PL', '1C78331E156A3D65': 'AT',
            '631158F6898738A4': 'AAT', '333DA6244C7DA34C': 'SR', '5436629450D8387A': 'SL',
            'DCB41B3746FDD1BE': 'Rifleman', '8E0FE664CE7D1CA9': 'GL',
            '98EB9CDD85B8C92C': 'Unarmed', '928DC9882A0AC79D': 'Sergeant',
            'AB9726163EC1BD81': 'Medic',
            '612F43A4D5AE765F': 'RTO', 'BA010C4C82F3DE92': 'CC', '96C784C502AC37DA': 'MG',
            '9FFEF10757E742EB': 'Crew',
            '5117311FB822FD1F': 'Officer', '976AC400219898FA': 'Sharpshooter',
            'E9AEEF2D9E41321B': 'AMG', '069118B2D809C9E1': 'Rifleman_Variant_1', 'B31D4E408AEC99A4': 'Ammo',
        }.items()
    })
    stock_ai['145DBC42B19FC4D6'] = (
        'Prefabs/Characters/Factions/OPFOR/USSR_Army/KLMK/Character_USSR_Rifleman_KLMK.et')
    if args.batch and stock:
        external_ai.update({guid: path for guid, path in stock_ai.items() if stock.contains(guid, path)})
    forced_external = {}
    asset_aliases = {}
    reviewed_audio = {}
    unavailable = set()
    if args.units:
        source_units = Snapshot(args.units, args.units_source)
        dev_units = Snapshot(args.units, args.units_dev)
        report['sourceUnits'] = source_units.commit
        report['targetUnits'] = dev_units.commit
        if args.batch:
            try:
                occupied = locally_owned | source_units.resources.keys() | dev_units.resources.keys()
                forced_external = verified_uk_forced_roles(
                    args.workshop_addons, occupied) | verified_rhs_forced_roles(
                        rhs_database, locally_owned, source_units, dev_units) | verified_msv85_forced_roles(
                            source_units, dev_units, locally_owned)
                asset_aliases = batch.resource_aliases(args.workshop_addons, dev_units, stock)
                asset_aliases.update(batch.reviewed_units_asset_paths(source_units, dev_units))
                if args.workshop_addons:
                    unavailable = batch.unavailable_optional_resources(
                        resource_databases(args.base_database, args.workshop_addons),
                        (source, dev, source_units, dev_units), (batch.BMP_DAMPER_AUDIO, local_loadouts.GREEN_SNOOD))
                    reviewed_audio = batch.reviewed_missing_bmp_audio(source, dev, unavailable)
            except (OSError, ValueError) as exc:
                parser.error(str(exc))

    def resolve_spawn(objectives, resource, path, *, group=False, vehicle=False):
        if vehicle:
            asset = objectives.get(resource, {})
            return path if asset.get('vehicle') and asset['path'] == path else None
        if group:
            if name := batch.NAGALAM_AI_GROUPS.get(resource):
                if path in (f'Prefabs/Groups/{name}', f'worlds/Perfk/Prefabs/Groups/{name}'):
                    path = f'worlds/Perfk/Prefabs/Groups/{name}'
            owners = (locally_owned, source_units.resources, dev_units.resources)
            if (resource, path) in workshop_spawn_groups and not any(resource in owner for owner in owners):
                return path
            if verified_stock_ai_group(resource, path, stock, locally_owned, source_units, dev_units, rhs_database):
                return path
            if batch.verified_local_ai_group(source, dev, source_units, dev_units, stock, resource, path, external_ai):
                return path
            if (alias := asset_aliases.get((resource, path))) and alias[0] == resource:
                path = alias[1]
            if (resource in locally_owned or source_units.resources.get(resource) != [path]
                    or dev_units.resources.get(resource) != [path]):
                return None
            try:
                ancestry = compare_ai_parents(source_units, dev_units, resource)
                if not ancestry[0].startswith('SCR_AIGroup '):
                    return None
                convert_ai_instance([], resource, source_units, dev_units, locally_owned, external_ai)
                for parent in inheritance(dev_units, resource):
                    slots = SLOTS.search(parent)
                    if not slots:
                        continue
                    for member, member_path in REF.findall(slots[1]):
                        member = member.upper()
                        if (external_ai.get(member) == member_path and member not in locally_owned
                                and (not any(member in owner for owner in owners)
                                     or unchanged_deprecated_stock_override(
                                         source_units, dev_units, member, member_path))):
                            continue
                        if (dev_units.resources.get(member) != [member_path]
                                or not (local_loadouts.unchanged_units_parent(source_units, dev_units, member)
                                        or unchanged_disabled_playability(source_units, dev_units, member))):
                            return None
                    break
            except ValueError:
                return None
            return path
        if resource not in locally_owned and (alias := asset_aliases.get((resource, path))) and alias[0] == resource:
            path = alias[1]
        if resource not in locally_owned and vehicle_crew.verified_units_crew_prefab(
                resource, path, source_units, dev_units, stock, locally_owned, external_vehicle_parents):
            return path
        return batch.spawn_resource(source, dev, source_units, dev_units, stock, resource, path, objectives,
                                    workshop_spawn_resources, reviewed_assets=reviewed_audio, aliases=asset_aliases)

    def resolve_crew(resource, path, *, allow_legacy_playable=True):
        return vehicle_crew.character_reference(resource, path, source_units, dev_units, stock, locally_owned,
                                                external_ai, allow_legacy_playable=allow_legacy_playable)

    def convert_crew(block, *, placed_names=()):
        ref = REF.search(block[0])
        if (ref and vehicle_crew.VEHICLE.fullmatch(block[0])
                and any('TILW_VehicleCrewComponent' in line for line in block)):
            guid = ref[1].upper()
            alias = batch.registered_alias(guid, ref[2], source, dev, asset_aliases)
            if alias and REF.fullmatch(alias)[1] == guid:
                block = [block[0][:ref.start()] + alias + block[0][ref.end():], *block[1:]]
        return vehicle_crew.convert(block, source_units, dev_units, stock, locally_owned,
                                    external_vehicles, external_ai, external_parents=external_vehicle_parents,
                                    placed_names=placed_names)

    out = {}
    arc_areas = {}
    batch_areas = {}
    batch_interactions = {}
    spawned_assets = {}
    delayed_extractions = {}
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
            row['devWorldAtSamePath'] = world in dev.files
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
                reviewed_tint = ((args.include_industrial_candidate and layer == industrial.DEFAULT)
                                 or (world in arc_worlds and layer == world.removesuffix('.ent') + '_Layers/default.layer'))
                if args.include_gwotline_mode and layer == gwotline.AO:
                    ao_body, instances = gwotline.stage_boundaries(source)
                elif args.include_irish_candidate and layer == irish.DEFAULT:
                    ao_body, instances = irish.boundaries(text)
                else:
                    ao_body, instances = convert_boundaries(text, allow_public_faction_color=reviewed_tint,
                                                            extended_shapes=args.batch,
                                                            source_blob=source.files[layer] if args.batch else None)
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
                        ) or (
                            args.include_breach_candidate and world == breach.WORLD
                            and layer == breach.LAYERS + 'default.layer'
                        ) or (
                            args.include_wetfeet_candidate and world == wetfeet.WORLD
                            and layer == wetfeet.PUBLIC_MARKERS
                        ) or (
                            args.include_industrial_candidate and layer == industrial.DEFAULT
                        ) or (
                            args.include_irish_candidate and layer == irish.DEFAULT
                        ) or (
                            world in arc_worlds and reviewed_tint
                        )
                        if layer in dev.files and not reviewed_candidate:
                            audit['gap'] = 'Dev already contains the original layer path; review before adding boundary siblings'
                        else:
                            existing = target_paths.get(dest.casefold())
                            if existing and (existing != dest or dev.read(dest).strip() != ao_body.strip()):
                                previous, _ = convert_boundaries(text, split_fill=False)
                                old_partial, _ = convert_boundaries(text)
                                reviewed_upgrade = reviewed_tint and dev.read(dest).strip() == old_partial.strip()
                                if existing != dest or (dev.read(dest).strip() != previous.strip() and not reviewed_upgrade):
                                    audit['gap'] = 'Dev has differing boundary output'
                            if 'gap' not in audit:
                                out[dest] = ao_body
                                audit['placementLayer'] = dest
            records, issues = read_respawns(text, layer)
            respawns.extend(records)
            ticket_gaps.extend(issues)
            if args.units:
                try:
                    if (arc_worlds.get(world), PurePosixPath(layer).name) in arc_capture.FOREIGN_ROLE_LAYERS:
                        groups = arc_capture.foreign_roster(source, source_units, dev_units, stock, locally_owned, layer)
                    else:
                        groups = group_layer(text, source_units, dev_units, stock, locally_owned, external_ai=external_ai,
                                             forced_external=forced_external)
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
                markers = convert_markers(text, dev, draw_order=args.batch)
                if markers:
                    body, count = markers
                    if layer in dev.files and relocate_images(dev.read(layer), dev).strip() != body.strip():
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
            found, issues = read_pages(text, layer, briefing_layouts)
            pages.extend(found)
            gaps.extend(issues)
        row['directBriefingsRead'] = len(pages)
        named_briefings = sorted({page.name for page in pages if page.name})
        if named_briefings and args.batch:
            row['namedBriefings'] = named_briefings
        # Nested and script-created pages need their owning resources converted.
        for path in sorted(source.closure(world)):
            if not path.lower().endswith(('.et', '.layer')) or path in source.layers[world.lower()]:
                continue
            if 'PS_MissionDescription' in source.read(path):
                gaps.append(path + ' contains an inherited or prefab-owned briefing')
        authored_respawns = False
        if args.batch and respawns:
            try:
                authored_respawns = bool(authored_respawn_fields(world, respawns, ticket_gaps))
            except ValueError:
                pass
        # The event action has its own lower authoring limit. Native per-faction
        # policies initialize the full ticket value directly.
        event = ticket_event(world, respawns, list(ticket_gaps) if authored_respawns else ticket_gaps)
        if respawns or ticket_gaps:
            follow_up = []
            if authored_respawns:
                follow_up.append('Verify death-triggered ticket spending, scheduled replacement, and spectator handoff in Match')
            elif any(r['tickets'] != 0 for r in respawns):
                follow_up.append('Compare eligible dead slots, ticket consumption on failed replacement and disconnect handling')
            if not authored_respawns and any(r['tickets'] > 0 and not r['wave'] for r in respawns):
                follow_up.append('Restore individual death-to-respawn scheduling using the recorded interval in milliseconds')
            if not authored_respawns and any(r['tickets'] > 0 and r['wave'] for r in respawns):
                follow_up.append('Restore wave scheduling aligned to legacy world time')
            if not authored_respawns and any(r['tickets'] != 0 and r['destination'] for r in respawns):
                follow_up.append('Resolve the named destination at death and preserve original-slot fallback')
            if any(r['tickets'] == -1 for r in respawns):
                follow_up.append('Port the ordered per-character replacement prefab list and exhaustion behavior')
            row['respawn'] = {'factions': respawns, 'ticketGaps': ticket_gaps, 'remainingBehavior': follow_up}
        briefing = pages if not gaps else []
        messages, review = ([], {}) if world in (RACE_WORLD, RUHA_WORLD) else world_events(source, world)
        if review:
            row['frameworkEvents'] = review
        objective_flags = ()
        interaction_placements = {}
        if args.batch and any(row['serializedTypes'].get(kind)
                              for kind in ('TILW_BaseInteraction', 'TILW_TeleportInteraction')):
            try:
                objective_flags, interaction_placements, audit = batch.interaction_rules(source, world, stock, locally_owned)
                batch_interactions[world] = (objective_flags, interaction_placements)
                row['interactionCandidate'] = audit
            except ValueError as exc:
                row['interactionCandidate'] = {'gap': str(exc)}
        if args.batch and row['serializedTypes'].get('TILW_Flag_EntityDamage'):
            try:
                damage_flags, damage, audit = batch.damage_flag_rules(
                    source, world, stock, locally_owned, source_units, dev_units, rhs_database,
                    workshop_targets=workshop_damage_resources, convert_crew=convert_crew)
                if set(damage_flags) & set(objective_flags) or damage.keys() & interaction_placements.keys():
                    raise ValueError('damage flag or target also has an interaction')
                objective_flags = set(objective_flags) | set(damage_flags)
                interaction_placements.update(damage)
                batch_interactions[world] = (objective_flags, interaction_placements)
                row['damageFlagCandidates'] = audit
            except ValueError as exc:
                row['damageFlagCandidates'] = {'review': {'gap': str(exc)}}
        has_outcomes = row['serializedTypes'].get('TILW_EndGameInstruction')
        delayed = None
        objectives = {}
        if args.batch and row['serializedTypes'].get('TILW_SetNameComponent') and row['serializedTypes'].get('TILW_Flag_EntityDamage'):
            try:
                ai_assets = batch.ai_objectives(source, dev, world, stock, locally_owned, source_units, dev_units)
                playable = batch.playable_objectives(source, dev, world, stock, locally_owned, source_units,
                                                     dev_units, row.get('respawn', {}))
                if ai_assets.keys() & playable.keys():
                    raise ValueError('objective character appears in both AI and playable rosters')
                roster_assets = {**ai_assets, **playable}
                ai_flags = [asset['deathFlag'] for asset in roster_assets.values()]
                ai_flags.extend(action['flag'] for asset in ai_assets.values() for action in asset['interactions'])
                if len(set(ai_flags)) != len(ai_flags) or set(ai_flags) & set(objective_flags):
                    raise ValueError('duplicate AI objective flag producers')
                if roster_assets:
                    objectives.update(roster_assets)
                    objective_flags = set(objective_flags) | set(ai_flags)
                    batch_interactions[world] = (objective_flags, interaction_placements)
                    spawned_assets[world] = objectives
                    row['aiObjectives'] = {identity: {k: v for k, v in asset.items() if k != 'body'}
                                           for identity, asset in roster_assets.items()}
            except ValueError as exc:
                row['aiObjectiveGap'] = str(exc)
        if args.batch and any(row['serializedTypes'].get(kind)
                              for kind in ('TILW_SpawnPrefabInstruction', 'TILW_SpawnVehicleInstruction',
                                           'TILW_PrefabSpawnerEntity')):
            spawned = batch.spawned_objectives(source, dev, world, stock, locally_owned, workshop_damage_resources)
            if spawned:
                asset_flags = [item['flag'] for item in spawned.values()]
                if (len(asset_flags) != len(set(asset_flags)) or set(asset_flags) & set(objective_flags)
                        or objectives.keys() & spawned.keys()):
                    row.setdefault('interactionCandidate', {})['spawnedObjectives'] = {
                        'gap': 'duplicate objective flag producers'}
                else:
                    objectives.update(spawned)
                    objective_flags = set(objective_flags) | set(asset_flags)
                    batch_interactions[world] = (objective_flags, interaction_placements)
                    spawned_assets[world] = objectives
                    row.setdefault('interactionCandidate', {})['spawnedObjectives'] = {
                        'prefabs': {guid: {key: value for key, value in item.items() if key != 'body'}
                                    for guid, item in spawned.items()}}
        if args.batch and row['serializedTypes'].get('TILW_SpawnPrefabInstruction'):
            try:
                delayed = collect_delayed_extraction(source, world)
                if delayed.conditions:
                    if any(flag[1:-1] in batch.script_text(source) for flag in delayed.conditions):
                        raise ValueError('mission script refers to a delayed extraction flag')
                    delayed_extractions[world] = delayed
            except ValueError as exc:
                row['delayedExtractionGap'] = str(exc)
                delayed = None
        has_event_actions = args.batch and any(row['serializedTypes'].get(kind) for kind in
                                              ('TILW_AssignWaypointsInstruction', 'TILW_SendMessageInstruction',
                                               'TILW_ReactivateEventsInstruction', 'TILW_DeactivateEventsInstruction',
                                               'TILW_RunEventsInstruction',
                                               'TILW_SpawnPrefabInstruction', 'TILW_SpawnVehicleInstruction', *ACTIONS))
        has_spawner_sampling = (args.batch and row['serializedTypes'].get('TILW_FlagSampling')
                                and row['serializedTypes'].get('TILW_PrefabSpawnerEntity'))
        if args.include_outcomes and (has_outcomes or has_event_actions or has_spawner_sampling
                                      or (delayed and delayed.spawns)):
            try:
                captures = {}
                if world in arc_worlds:
                    captures, area, capture_audit = arc_capture.capture(source, world)
                    row['captureCandidate'] = capture_audit
                    arc_areas[world] = area
                elif args.batch:
                    captures, batch_areas[world], capture_audit = batch.area_rules(source, world, dev)
                    if capture_audit:
                        row['captureCandidate'] = capture_audit
                if delayed and delayed.conditions:
                    if captures.keys() & delayed.conditions.keys():
                        raise ValueError('dynamic extraction flag also has a placed area producer')
                    captures.update(delayed.conditions)
                hidden_briefings = {}
                if args.batch and len(pages) <= 32 and row['serializedTypes'].get('TILW_EditMapItemInstruction'):
                    names = Counter(page.name for page in pages if page.name)
                    indexed, _ = indexed_pages(pages)
                    hidden_briefings = {page.name: order for order, page in indexed
                                        if names[page.name] == 1 and page.audience == BriefingAudience.HIDDEN}
                derived_audit = []
                derived_spawners_audit = {}
                identity_flags_audit = []
                meta_dividers_audit = {}
                startup_samples_audit = []
                match_only_flags = {flag for candidate in row.get('damageFlagCandidates', {}).values()
                                    for flag in candidate.get('placedDestructionFlags', [])}
                if delayed:
                    match_only_flags.update(delayed.startup_consumers)
                area_flag_readers = batch.reviewed_area_flag_readers(source, world) if args.batch else {}
                observed_area_flags = {record['writesFlag'] for area in row.get('captureCandidate', {}).values()
                                       for record in area.values() if isinstance(record, dict)
                                       and record.get('writesFlag') in area_flag_readers}
                match_only_flags.update(observed_area_flags)
                rules, outcome_audit, gap = compile_outcomes(
                    source, world, captures, compound=args.batch, notification_audit=review,
                    objective_flags=set(objective_flags) | observed_area_flags,
                    extraction_spawns=delayed.spawns if delayed else None,
                    scripts=batch.script_text(source) if args.batch else None,
                    resolve_spawn=partial(resolve_spawn, objectives) if args.batch else None,
                    resolve_crew=resolve_crew if args.batch else None, hidden_briefings=hidden_briefings,
                    derived_audit=derived_audit, identity_flags_audit=identity_flags_audit,
                    meta_dividers_audit=meta_dividers_audit,
                    startup_samples_audit=startup_samples_audit,
                    match_only_flags=match_only_flags |
                    {asset['flag'] for asset in objectives.values() if asset.get('destroyed')},
                    interaction_reads=Counter(flag for placement in row.get('interactionCandidate', {}).values()
                                              if isinstance(placement, dict)
                                              for field in ('teleportRequiredFlags', 'interactionRequiredFlags')
                                              for flag in placement.get(field, [])),
                    spawner_reads=batch.placed_spawner_flag_reads(source, source.layers[world.lower()])
                    if args.batch else None, derived_spawners_audit=derived_spawners_audit if args.batch else None)
                outcome = {'events': outcome_audit}
                if derived_audit:
                    outcome['derivedFlags'] = derived_audit
                if derived_spawners_audit:
                    outcome['derivedSpawnerConditions'] = derived_spawners_audit
                if identity_flags_audit:
                    outcome['identityFlags'] = identity_flags_audit
                if meta_dividers_audit:
                    outcome['omittedMetaDividers'] = meta_dividers_audit
                if startup_samples_audit:
                    outcome['startupSamples'] = startup_samples_audit
                if gap:
                    outcome['gap'] = gap
                if rules:
                    fragment = str(PurePosixPath(world).with_suffix('')) + '_GC_OutcomeRules.inc'
                    if fragment.casefold() in source_paths or fragment.casefold() in target_paths:
                        raise ValueError('outcome rules fragment collides with an addon file')
                    out[fragment] = '\n'.join(rules) + '\n'
                    outcome['stagedFragment'] = fragment
                row['outcomeCandidate'] = outcome
            except ValueError as exc:
                row['outcomeCandidate'] = {'gap': str(exc)}
        mode_events = [] if authored_respawns else event
        if messages:
            mode_events = ['   m_aMissionEvents + {', *mode_events[1:-1], *messages, '   }']
        if briefing or mode_events or (args.batch and row['headers'] and world not in dev.files and not gaps):
            try:
                body, compact = render(world, briefing, mode_events)
                # Keep scenario settings in the owning mission directory under a unique name.
                dest = str(PurePosixPath(world).with_suffix('')) + '_GC_Settings.et'
                resource = guid(world + '/resource')
                if source.resources.get(resource) or any(p != dest for p in dev.resources.get(resource, [])):
                    raise ValueError('generated resource GUID collision')
                old_name = next(line.strip() for line in template.splitlines() if line.strip().startswith('Name '))
                meta = template.replace(old_name, f'Name "{{{resource}}}{dest}"')
                prior_preset = None
                for path, contents in [(dest, body), (dest + '.meta', meta)]:
                    if path not in dev.files:
                        continue
                    existing = dev.read(path)
                    if existing.strip() != contents.strip():
                        if path != dest:
                            raise ValueError('Dev already has differing ' + path)
                        if world not in dev.files and dev.files[path] in prepared_settings.get(path, ()):
                            prior_preset = sha256(existing.encode()).hexdigest()
                            continue
                        ticket_versions = [render(world, briefing, event)[0].strip()] if event else []
                        if authored_respawns and messages:
                            with_tickets = ['   m_aMissionEvents + {', *event[1:-1], *messages, '   }']
                            ticket_versions.append(render(world, briefing, with_tickets)[0].strip())
                        previous_briefing = render(world, briefing)[0].strip()
                        if existing.strip() not in [previous_briefing, *ticket_versions]:
                            raise ValueError('Dev already has differing ' + path)
                        if authored_respawns and existing.strip() in ticket_versions:
                            prior_preset = sha256(existing.encode()).hexdigest()
                out[dest] = body
                out[dest + '.meta'] = meta
                if prior_preset:
                    row['reviewedSettingsPreset'] = {dest: prior_preset}
                if args.batch:
                    row['batchModePreset'] = dest
                if briefing:
                    row['briefingModePreset'] = dest
                    row['renumberedInSourceOrder'] = compact
                if messages:
                    row['notificationModePreset'] = dest
                if event and not authored_respawns:
                    row['respawn']['ticketModePreset'] = dest
            except ValueError as exc:
                gaps.append(str(exc))
        row['briefingGaps'] = gaps
    report['devOnlyHeaders'] = sorted(target_headers - set(report['headers']))
    reviewed_race_aliases = {(guid, path): (guid, alternate)
                             for (guid, path), alternate in batch.STOCK_RESOURCE_ALIASES.items()}
    reviewed_race_aliases.update({key: resource[1:] for key, resource in batch.WORKSHOP_RESOURCE_ALIASES.items()})
    for enabled, world, recipe in [(args.include_race_day, RACE_WORLD, race_files), (args.include_ruha, RUHA_WORLD, ruha_files)]:
        if not enabled:
            continue
        if 'briefingModePreset' not in report['worlds'][world]:
            parser.error(f'{world} needs a verified generated briefing preset')
        generated = recipe(source)
        for path, data in generated.items():
            if path in dev.files and dev.read(path).strip() != data.strip():
                # Reuse already-installed reference repairs if the rest of the recipe output matches.
                repaired = batch.replace_registered_paths(data, source, dev, reviewed_race_aliases)
                if path.endswith('.layer') and dev.read(path).strip() == repaired.strip():
                    data = repaired
                elif world != RACE_WORLD or dev.files[path] != PRIOR_KUNAR_LAYERS.get(path):
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
                    if path != gwotline.OPFOR_ROSTER or dev.files[path] != gwotline.PRIOR_OPFOR_ROSTER:
                        parser.error(f'Dev already has differing {path}')
                out[path] = body
                row['rosterCandidates'][path] = roster
        if args.include_gwotline_candidate:
            candidate = gwotline.assemble(source, dev, mode)
            for candidate_path, body in candidate.items():
                if candidate_path in dev.files and dev.read(candidate_path).strip() != body.strip():
                    if candidate_path != gwotline.DEFAULT or dev.files[candidate_path] != gwotline.PRIOR_DEFAULT:
                        parser.error(f'Dev already has differing {candidate_path}')
                out[candidate_path] = body
            del out[row['modeCandidate']['placementLayer']]
            row['modeCandidate']['placementLayer'] = gwotline.DEFAULT
            row['playtestCandidate'] = {
                'presentOnDev': all(candidate_path in dev.files and dev.read(candidate_path).strip() == body.strip()
                                    for candidate_path, body in candidate.items()),
                'remainingBehavior': gwotline.GAPS,
            }
    if args.include_breach_loadouts:
        if not args.stage or not args.units or not stock:
            parser.error('Breach and Clear loadouts require --stage, --units and --base-database')
        files, audit = breach.stage_loadouts(source, dev, source_units, dev_units, stock)
        roster, roster_audit = breach.stage_roster(source, source_units, dev_units, stock, locally_owned, audit)
        files[breach.PLAYERS] = roster
        for path, body in files.items():
            if path in dev.files and dev.read(path).strip() != body.strip():
                if path != breach.PLAYERS or dev.files[path] != breach.PRIOR_ROSTER:
                    parser.error(f'Dev already has differing {path}')
            out[path] = body
        row = report['worlds'][breach.WORLD]
        row['loadoutCandidates'] = audit
        row['rosterCandidate'] = roster_audit
        if args.include_breach_candidate:
            candidate, icons = breach.assemble(source, dev, row['briefingModePreset'])
            for path, body in candidate.items():
                if path in dev.files and dev.read(path).strip() != body.strip():
                    approved = {breach.LAYERS + 'default.layer': breach.PRIOR_DEFAULT, breach.HEADER: breach.PRIOR_HEADER}
                    if dev.files[path] != approved.get(path):
                        parser.error(f'Dev already has differing {path}')
                out[path] = body
            row['playtestCandidate'] = {
                'presentOnDev': all(path in dev.files and dev.read(path).strip() == body.strip()
                                    for path, body in {**files, **candidate}.items()),
                'icons': icons,
                'playerLossAnnouncement': {'sourceEvent': '6508D983AB223731', 'lossFaction': 'RHS_AFRF',
                                           'ratio': 1, 'delaySeconds': 10, 'winner': 'RHS_USAF'},
                'aiLossAnnouncement': {'sourceEvent': '6508D983AB010064', 'lossFaction': 'RHS_USAF',
                                       'ratio': 0.9, 'delaySeconds': 10, 'winner': 'RHS_AFRF'},
                'remainingBehavior': breach.GAPS,
            }
    wetfeet_cleanup = ''
    if args.include_wetfeet_scene:
        if not args.stage or not stock or not args.workshop_addons:
            parser.error('the WetFeet scene requires --stage, --base-database and --workshop-addons')
        args.include_wetfeet_mode = True
        scene, wetfeet_cleanup, scene_audit = wetfeet.stage_scene(source, dev, stock, args.workshop_addons)
        for path, body in scene.items():
            if path in dev.files and dev.read(path).strip() != body.strip():
                parser.error(f'Dev already has differing {path}')
            out[path] = body
        report['worlds'][wetfeet.WORLD]['sceneCandidate'] = scene_audit
    if args.include_wetfeet_mode:
        if not args.stage:
            parser.error('the WetFeet mode placement requires --stage')
        row = report['worlds'][wetfeet.WORLD]
        if 'briefingModePreset' not in row:
            parser.error('the WetFeet mode placement requires the generated briefing preset')
        body, audit = wetfeet.stage_mode(source, row['briefingModePreset'], wetfeet_cleanup)
        if wetfeet.MODE in dev.files and dev.read(wetfeet.MODE).strip() != body.strip():
            parser.error(f'Dev already has differing {wetfeet.MODE}')
        out[wetfeet.MODE] = body
        row['modeCandidate'] = {'placementLayer': wetfeet.MODE, **audit, 'remainingBehavior': [
            'Compare match-start discovery, interrupted capture and concurrent outcome timing with the legacy session',
            'Assemble and verify the starting rosters, markers, scenery, vehicles and tree cleanup',
            'Verify faction-restricted notices and join-in-progress behavior on remote clients',
        ]}
    if args.include_wetfeet_rosters:
        if not args.stage or not args.units:
            parser.error('the WetFeet rosters require --stage and --units')
        files, audit = wetfeet.stage_rosters(source, source_units, dev_units, stock, locally_owned)
        for path, body in files.items():
            if path in dev.files and dev.read(path).strip() != body.strip():
                parser.error(f'Dev already has differing {path}')
            out[path] = body
        report['worlds'][wetfeet.WORLD]['rosterCandidates'] = audit
    if args.include_wetfeet_candidate:
        row = report['worlds'][wetfeet.WORLD]
        files, audit = wetfeet.assemble(source, dev, stock, out, out.pop(wetfeet.MODE))
        for path, body in files.items():
            if path in dev.files and dev.read(path).strip() != body.strip():
                if path != wetfeet.DEFAULT or dev.files[path] != wetfeet.PRIOR_DEFAULT:
                    parser.error(f'Dev already has differing {path}')
            out[path] = body
        row['modeCandidate']['placementLayer'] = wetfeet.DEFAULT
        row['playtestCandidate'] = {
            **audit,
            'presentOnDev': all(
                path in dev.files and dev.read(path).strip() == out[path].strip()
                for path in (set(files) | set(scene) | {row['briefingModePreset'], row['briefingModePreset'] + '.meta'}
                             | {path for path in out if path.startswith(wetfeet.LAYERS)})
            ),
            'remainingBehavior': [
                'Compare live contested capture, multiplayer casualty outcomes and notification audiences',
                'Compare AO, icon visibility and British overlay with each faction and before assignment',
                'Compare equipment inventory and vehicle behavior',
                'Restore the legacy BRDM preview and locked-vehicle removal for BRDMCrew',
                'Compare match-start tree cleanup with the legacy world-initialization deletion',
                'Compare zoom-dependent legacy outline to the native three-pixel overlay',
            ],
        }
    if args.include_industrial_candidate:
        row = report['worlds'][industrial.WORLD]
        outcome = row['outcomeCandidate']
        if len(outcome.get('events', {})) != 3 or any('gap' in event for event in outcome['events'].values()):
            parser.error('Industrial Metal needs all three reviewed source outcome events')
        rules = out[outcome['stagedFragment']]
        candidate, audit = industrial.assemble(source, dev, stock, args.workshop_addons,
                                                row['briefingModePreset'], rules, out)
        for path, body in candidate.items():
            if path in dev.files and dev.read(path).strip() != body.strip():
                if path != industrial.BOUNDARIES or out.get(path) != body:
                    parser.error(f'Dev already has differing {path}')
            out[path] = body
        row['playtestCandidate'] = {
            'presentOnDev': all(path in dev.files and dev.read(path).strip() == body.strip()
                                for path, body in candidate.items()),
            'boundaries': audit, 'icons': 2,
            'remainingBehavior': [
                'Compare live casualty thresholds, timeout notices and delivery to remote clients',
                'Compare both boundary audiences, the staging-zone restriction and release',
                'Check vehicle handling, loadout contents and original callsign labels',
            ],
        }
    if args.include_irish_candidate:
        row = report['worlds'][irish.WORLD]
        outcome = row['outcomeCandidate']
        if len(outcome.get('events', {})) != 3 or any('gap' in event for event in outcome['events'].values()):
            parser.error('Irish Checkpoint needs all three reviewed source outcome events')
        candidate, audit, icons = irish.assemble(source, dev, stock, row['briefingModePreset'],
                                                 out[outcome['stagedFragment']], out)
        for path, body in candidate.items():
            if path in dev.files and relocate_images(dev.read(path), dev).strip() != body.strip():
                parser.error(f'Dev already has differing {path}')
            out[path] = body
        row['playtestCandidate'] = {
            'presentOnDev': all(path in dev.files and dev.read(path).strip() == body.strip()
                                for path, body in candidate.items()),
            'boundaries': audit, 'icons': icons,
            'remainingBehavior': [
                'Compare live casualty thresholds, timeout notices and remote-client delivery',
                'Compare boundary enforcement within five centimetres of the IRA polygon crossing',
                'Compare staging-zone enforcement and release timing',
                'Compare issued equipment, vehicle handling and original callsigns',
            ],
        }
    for world in arc_worlds:
        row = report['worlds'][world]
        outcome = row['outcomeCandidate']
        name = arc_worlds[world]
        expected_events = 5 if name == 'ForeignExchangeStudentsII' else 4
        if len(outcome.get('events', {})) != expected_events or any('gap' in event for event in outcome['events'].values()):
            parser.error(f'{world} needs all {expected_events} reviewed source outcome events')
        candidate, audit, icons, legacy_vehicle_groups = arc_capture.assemble(
            source, dev, stock, args.workshop_addons, world, row, out,
            out[outcome['stagedFragment']], arc_areas[world],
        )
        for path, body in candidate.items():
            if path in dev.files and relocate_images(dev.read(path), dev).strip() != body.strip():
                boundary_path = world.removesuffix('.ent') + '_Layers/default_GC_Boundaries.layer'
                prior_vehicles = legacy_vehicle_groups and path.endswith('/Vehicles.layer') and dev.files[path] == source.files[path]
                prior_timing = (path.endswith('/default.layer')
                                and dev.read(path).strip() == body.replace('        m_bUseElapsedTime 1\n', '').strip())
                if not prior_vehicles and not prior_timing and (path != boundary_path or out.get(path) != body):
                    parser.error(f'Dev already has differing {path}')
            out[path] = body
        row['playtestCandidate'] = {
            'presentOnDev': all(path in dev.files and dev.read(path).strip() == body.strip()
                                for path, body in candidate.items()),
            'boundaries': audit, 'icons': icons,
            'remainingBehavior': [
                'Compare live capture, interrupted and contested capture, and remote notifications',
                'Compare staging-zone enforcement, release and long timeout notices',
                'Compare callsign labels, issued equipment and vehicle handling',
            ],
        }
        if name in ('ForeignExchangeStudents', 'ForeignExchangeStudentsII'):
            row['playtestCandidate']['terrainParent'] = {
                'legacy': arc_capture.LONGMOOR_LEGACY_PARENT,
                'dev': arc_capture.LONGMOOR_CURRENT_PARENT,
                'terrainAddon': '61BD1072371B5470',
            }
            row['playtestCandidate']['remainingBehavior'].append('Compare authored placements against historical Longmoor Extended')
        if name == 'ForeignExchangeStudentsII':
            asset_files = arc_capture.covermap_assets(source)
            for path, contents in asset_files.items():
                if path in dev.files and dev.read_raw(path) != contents:
                    parser.error(f'Dev already has differing {path}')
                out[path] = contents
            row['playtestCandidate']['sharedMapAssets'] = list(asset_files)
            row['playtestCandidate']['presentOnDev'] &= all(path in dev.files for path in asset_files)
        if legacy_vehicle_groups:
            row['playtestCandidate']['legacyVehicleGroups'] = legacy_vehicle_groups
            row['playtestCandidate']['remainingBehavior'].append('Restore legacy vehicle previews and locked-vehicle removal')
    if args.include_bugout_candidate:
        row = report['worlds'][bugout.WORLD]
        try:
            candidate, audit = bugout.assemble(source, dev, dev_units, stock, args.workshop_addons, row, out)
        except ValueError as exc:
            parser.error(str(exc))
        for path, body in candidate.items():
            if path in dev.files and dev.read(path).strip() != body.strip():
                parser.error(f'Dev already has differing {path}')
            out[path] = body
        row['playtestCandidate'] = {
            **audit,
            'presentOnDev': all(path in dev.files for path in candidate),
            'remainingBehavior': [
                'Compare initial deployment and route against historical Longmoor Extended',
                'Compare all pursuit and reinforcement waypoints, flare appearance and occupied-side casualties',
                'Compare remote announcement delivery and issued equipment',
            ],
        }
    if args.batch:
        ai_loadouts = local_loadouts.collect_ai(source, dev, source_units, dev_units, stock)
        native_ai_members = {guid: asset[0] for guid, asset in ai_loadouts.items()}
        try:
            historical_roles = verified_roles(dev_units, stock)
            aircrew_roles = verified_aircrew_roles(source_units, dev_units, stock, locally_owned)
            restored = (historical_roles | verified_msv85_wrappers(source_units, dev_units)
                        | aircrew_roles
                        | verified_soviet_aircrew_roles(source_units, dev_units, stock, locally_owned))
        except (OSError, ValueError, CalledProcessError) as exc:
            parser.error(str(exc))
        restored = {old: new for old, new in restored.items()
                    if old[0] not in locally_owned and new[0] not in locally_owned}
        restored_rosters = verified_group_rosters(source_units, dev_units, stock, locally_owned, restored)
        loadouts = local_loadouts.collect(
            source, dev, source_units, dev_units, stock,
            normalize_references=lambda body: batch.replace_registered_paths(body, source, dev, asset_aliases),
            unavailable=unavailable, historical_parents=historical_roles, rhs=rhs_database,
            published_playable_parents={current for (identity, _), current in aircrew_roles.items()
                                        if identity == current[0]})
        reviewed_members = {guid: asset[0] for guid, asset in loadouts.items()}
        local_groups = batch.reviewed_group_aliases(source, dev, source_units, dev_units, stock, reviewed_members,
                                                    rhs_database)

        def convert_group_block(body, reviewed_ai_members=None, reviewed_playable_members=None):
            return group_layer(body, source_units, dev_units, stock, locally_owned,
                               reviewed_members={**reviewed_members, **(reviewed_playable_members or {})},
                               external_ai=external_ai,
                               allow_stock_ai=True, restored_members=restored,
                               reviewed_ai_members={**native_ai_members, **(reviewed_ai_members or {})},
                               forced_external=forced_external,
                               rhs=rhs_database, uk=uk_database, local_aliases=local_groups,
                               restored_rosters=restored_rosters)

        def resolve_crew_config(resource, path):
            return vehicle_crew.verified_crew_config(source_units, resource, path, locally_owned)

        out = batch.plan(source, dev, report, out, batch_areas, batch_interactions, convert_group_block,
                         asset_aliases, {**loadouts, **ai_loadouts, **reviewed_audio}, spawned_assets,
                         convert_crew, resolve_spawn, resolve_crew,
                         resolve_crew_config, delayed_extractions,
                         batch.reviewed_faction_assets(source, dev, source_units, dev_units))
    if args.stage:
        staged_bytes = {path: contents if isinstance(contents, bytes) else contents.encode()
                        for path, contents in out.items()}
        stage = args.stage.resolve()
        if any(stage.is_relative_to(root.resolve()) for root in ([args.missions, lobby] + ([args.units] if args.units else []))):
            parser.error('stage outside both addon trees')
        # Check the whole generation before writing. Never overlay stale outputs.
        if stage.exists():
            existing = {p.relative_to(stage).as_posix(): p for p in stage.rglob('*') if p.is_file()}
            for path, file in existing.items():
                if path not in staged_bytes or file.read_bytes() != staged_bytes[path]:
                    parser.error(f'staging directory contains differing file {path}')
        for path, contents in staged_bytes.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes((json.dumps(report, indent=2, ensure_ascii=False) + '\n').encode())
    presets = sum('briefingModePreset' in row for row in report['worlds'].values())
    print(f"{len(report['headers'])} headers, {len(report['worlds'])} worlds, {presets} briefing presets")


if __name__ == '__main__':
    main()
