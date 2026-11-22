"""Install complete batch drafts into Missions Dev after validating their manifest."""
import argparse
from hashlib import sha256
import json
from os import replace
from pathlib import Path, PurePosixPath
import subprocess
from tempfile import NamedTemporaryFile

from catalog import REF, SOURCE_TEXT, Snapshot
from dependencies import database_project, dependency_resources, installed_version
from launchers import META_NAME, authored_header_text, required_addon_versions
from scenery import StockPrefabs
from terrain_parents import has_reviewed_longmoor, is_longmoor_subscene


def installation_pins(path, body):
    if path.lower().startswith('missions/') and path.lower().endswith('.conf'):
        return required_addon_versions(authored_header_text(body.decode('utf-8-sig')))
    return ()


def unresolved_resources(files, target, checkouts, databases):
    scopes = {installation_pins(path, body) for path, body in files.items()
              if path.lower().startswith('missions/') and path.lower().endswith('.conf')} or {()}
    return set().union(*(unresolved_resources_in_scope(files, target, checkouts, databases, pins)
                         for pins in scopes))


def unresolved_resources_in_scope(files, target, checkouts, databases, pins):
    dependencies, providers, unavailable = dependency_resources(
        target, checkouts, databases, additional_roots=tuple(dict(pins)))
    if pins:
        mounted = {project[0]: db for db in providers if (project := database_project(db))}
        for addon, version in pins:
            if addon not in mounted or installed_version(mounted[addon], addon) != version:
                raise ValueError(f'installation requires addon {addon}={version} in the installation graph')
        if unavailable:
            _, _, baseline = dependency_resources(target, checkouts, databases)
            if newly_missing := set(unavailable) - set(baseline):
                raise ValueError('pinned installation has unavailable dependencies: ' + ', '.join(sorted(newly_missing)))
    available = {(guid, path) for resources in (target.resources, dependencies)
                 for guid, paths in resources.items() for path in paths}
    refs = set()
    longmoor = None
    for path, body in files.items():
        if path.endswith('.meta'):
            meta = META_NAME.search(body.decode('utf-8-sig'))
            if meta and meta[3] == path[:-5] and path[:-5] in files:
                available.add((meta[2].upper(), meta[3]))
        elif PurePosixPath(path).suffix.lower() in SOURCE_TEXT:
            text = body.decode('utf-8-sig')
            if path.lower().endswith('.conf'):
                text = authored_header_text(text)
            if is_longmoor_subscene(path, text.replace('\r\n', '\n')):
                if longmoor is None:
                    longmoor = has_reviewed_longmoor(providers)
                if longmoor:
                    continue
            refs.update((guid.upper(), ref) for guid, ref in REF.findall(text))
    return unregistered_references(refs - available, providers)


def unregistered_references(refs, databases):
    for path in databases:
        if not refs:
            break
        try:
            database = StockPrefabs(path)
        except ValueError:
            continue
        refs = {ref for ref in refs if not database.contains(*ref)}
    return refs


def resource_databases(base, addons):
    if not base.is_file():
        raise ValueError('base resource database does not exist')
    if addons and not addons.is_dir():
        raise ValueError('Workshop addon directory does not exist')
    core = base.parent.parent / 'core' / 'resourceDatabase.rdb'
    databases = [base]
    if core.is_file() and core.resolve() != base.resolve():
        databases.append(core)
    if addons:
        databases.extend(sorted(addons.glob('*/resourceDatabase.rdb')))
    return databases


def replace_reviewed_file(path, before, after):
    if path.read_bytes() != before:
        raise OSError('file changed during installation: ' + str(path))
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(after)
        replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--stage', required=True, type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--mission', action='append', help='exact legacy launcher path, repeat to select a batch')
    selection.add_argument('--plan', type=Path, help='install the suggested batch from a file audit plan')
    parser.add_argument('--all-ready', action='store_true',
                        help='install all file-cleared drafts in the plan in one pass')
    parser.add_argument('--dependency', action='append', type=Path, default=[], help='addon checkout at HEAD for resource checks')
    parser.add_argument('--base-database', type=Path, help='verify draft resource references before installing')
    parser.add_argument('--workshop-addons', type=Path, help='include installed provider databases in resource checks')
    parser.add_argument('--replace-reviewed', action='store_true',
                        help='install reviewed updates to existing, unmodified Dev layers or generated settings')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.all_ready and not args.plan:
        parser.error('--all-ready requires --plan')

    def git(*command):
        return subprocess.check_output(['git', '-C', str(args.missions), *command]).decode().strip()

    try:
        inventory_bytes = args.inventory.read_bytes()
        inventory = json.loads(inventory_bytes)
        batch = inventory['batchMigration']
        if git('branch', '--show-current') != 'dev' or git('rev-parse', 'HEAD') != inventory['target']:
            raise ValueError('use the Dev checkout at the inventory target revision')
        root = Path(git('rev-parse', '--show-toplevel')).resolve()
        stage = args.stage.resolve()
        if stage.is_relative_to(root):
            raise ValueError('use an external staging directory')
        candidates = {world: row for world, row in batch['worlds'].items()
                      if row['status'] == 'generated for file review'}
        missions = args.mission
        if args.plan:
            plan = json.loads(args.plan.read_text(encoding='utf-8'))
            if (plan['source'], plan['target']) != (inventory['source'], inventory['target']):
                raise ValueError('plan and inventory describe different revisions')
            if plan.get('inventorySha256') != sha256(inventory_bytes).hexdigest():
                raise ValueError('plan and inventory come from different file audits')
            ready = plan['suggestedBatch']
            if args.all_ready:
                ready = [*ready, *plan['additionalDrafts']]
            missions = [header for row in ready for header in row['launchers']]
            if not missions:
                raise ValueError('the plan has no file-cleared drafts')
            if not args.base_database:
                raise ValueError('--plan requires --base-database for the installation resource audit')
        if missions:
            worlds = set()
            for header in missions:
                record = inventory['headers'].get(header, {})
                destination = record.get('destinationWorld', '').split('}', 1)[-1]
                if destination not in candidates:
                    raise ValueError('no complete staged draft for ' + header)
                worlds.add(destination)
            candidates = {world: candidates[world] for world in worlds}
        if not candidates:
            raise ValueError('the inventory has no complete drafts')

        repairs = {path: digest for row in candidates.values()
                   for path, digest in row.get('reviewedReplacements', {}).items()}
        settings_paths = {str(PurePosixPath(world).with_suffix('')) + '_GC_Settings.et' for world in candidates}
        writes, replacements, files = {}, {}, {}
        for relative in sorted({path for entry in candidates.values() for path in entry['files']}):
            origin, target = (stage / relative).resolve(), (root / relative).resolve()
            if not origin.is_relative_to(stage) or not target.is_relative_to(root):
                raise ValueError('draft path escapes its directory: ' + relative)
            body = origin.read_bytes()
            if sha256(body).hexdigest() != batch['sha256'][relative]:
                raise ValueError('draft changed since generation: ' + relative)
            files[relative] = body
            if target.exists():
                original = target.read_bytes()
                if original == body:
                    continue
                # A Git checkout may use CRLF for authored text.
                normalized = original.replace(b'\r\n', b'\n')
                if target.suffix.lower() != '.edds' and normalized == body:
                    continue
                if ((target.suffix != '.layer' and relative not in settings_paths)
                        or sha256(normalized.removeprefix(b'\xef\xbb\xbf')).hexdigest()
                        != repairs.get(relative)
                        or git('--literal-pathspecs', 'status', '--porcelain', '--', relative)):
                    raise ValueError('would overwrite different contents: ' + relative)
                if not args.replace_reviewed:
                    raise ValueError('review the existing file update and use --replace-reviewed: ' + relative)
                replacements[target] = (original, body)
                continue
            if relative in repairs:
                raise ValueError('reviewed Dev file is missing: ' + relative)
            writes[target] = body
        if args.base_database:
            databases = resource_databases(args.base_database, args.workshop_addons)
            target = Snapshot(root, inventory['target'])
            checkouts = [Snapshot(path, 'HEAD') for path in args.dependency]
            # Validate pinned missions against their own dependency graph.
            cohorts = [{path: files[path] for path in entry['files']} for entry in candidates.values()
                       ] if any(installation_pins(path, body) for path, body in files.items()) else [files]
            missing = set().union(*(unresolved_resources(cohort, target, checkouts, databases) for cohort in cohorts))
            if missing:
                raise ValueError('unresolved references: ' + ', '.join(f'{{{guid}}}{path}' for guid, path in sorted(missing)))
        if not args.dry_run:
            created, repaired = [], []
            try:
                for path, body in writes.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('xb') as stream:
                        created.append(path)
                        stream.write(body)
                for path, (original, body) in replacements.items():
                    replace_reviewed_file(path, original, body)
                    repaired.append(path)
            except OSError:
                for path in reversed(repaired):
                    original, body = replacements[path]
                    replace_reviewed_file(path, body, original)
                for path in created:
                    if path.is_file() and writes[path].startswith(path.read_bytes()):
                        path.unlink()
                raise
        action = 'Checked' if args.dry_run else 'Installed'
        repaired_count = f' and {len(replacements)} reviewed file updates' if replacements else ''
        print(f'{action} {len(candidates)} mission drafts with {len(writes)} new files{repaired_count}')
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
