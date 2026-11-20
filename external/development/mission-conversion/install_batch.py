"""Install complete batch drafts into Missions Dev after validating their manifest."""
import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import subprocess

from catalog import REF, SOURCE_TEXT, Snapshot
from launchers import META_NAME
from scenery import StockPrefabs


def unresolved_resources(files, addons, databases):
    available, refs = set(), set()
    for addon in addons:
        for guid, paths in Snapshot(addon, 'HEAD').resources.items():
            available.update((guid, path) for path in paths)
    for path, body in files.items():
        if path.endswith('.meta'):
            meta = META_NAME.search(body.decode('utf-8-sig'))
            if meta and meta[3] == path[:-5] and path[:-5] in files:
                available.add((meta[2].upper(), meta[3]))
        elif PurePosixPath(path).suffix.lower() in SOURCE_TEXT | {'.imageset'}:
            refs.update((guid.upper(), ref) for guid, ref in REF.findall(body.decode('utf-8-sig')))
    missing = refs - available
    for path in databases:
        if not missing:
            break
        try:
            database = StockPrefabs(path)
        except ValueError:
            continue
        missing = {ref for ref in missing if not database.contains(*ref)}
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--mission', action='append', help='exact legacy launcher path, repeat to select a batch')
    parser.add_argument('--dependency', action='append', type=Path, default=[], help='addon checkout at HEAD for resource checks')
    parser.add_argument('--base-database', type=Path, help='verify draft resource references before installing')
    parser.add_argument('--workshop-addons', type=Path, help='include installed provider databases in resource checks')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    def git(*command):
        return subprocess.check_output(['git', '-C', str(args.missions), *command]).decode().strip()

    try:
        inventory = json.loads(args.inventory.read_text(encoding='utf-8'))
        batch = inventory['batchMigration']
        if git('branch', '--show-current') != 'dev' or git('rev-parse', 'HEAD') != inventory['target']:
            raise ValueError('use the Dev checkout at the inventory target revision')
        root = Path(git('rev-parse', '--show-toplevel')).resolve()
        stage = args.stage.resolve()
        if stage.is_relative_to(root):
            raise ValueError('use an external staging directory')
        candidates = {world: row for world, row in batch['worlds'].items()
                      if row['status'] == 'generated for file review'}
        if args.mission:
            worlds = set()
            for header in args.mission:
                record = inventory['headers'].get(header, {})
                destination = record.get('destinationWorld', '').split('}', 1)[-1]
                if destination not in candidates:
                    raise ValueError('no complete staged draft for ' + header)
                worlds.add(destination)
            candidates = {world: candidates[world] for world in worlds}
        if not candidates:
            raise ValueError('the inventory has no complete drafts')

        writes, files = {}, {}
        for relative in sorted({path for entry in candidates.values() for path in entry['files']}):
            origin, target = (stage / relative).resolve(), (root / relative).resolve()
            if not origin.is_relative_to(stage) or not target.is_relative_to(root):
                raise ValueError('draft path escapes its directory: ' + relative)
            body = origin.read_bytes()
            if sha256(body).hexdigest() != batch['sha256'][relative]:
                raise ValueError('draft changed since generation: ' + relative)
            files[relative] = body
            if target.exists():
                if target.read_bytes() == body:
                    continue
                # A Git checkout may use CRLF for authored text.
                if target.suffix.lower() != '.edds' and target.read_bytes().replace(b'\r\n', b'\n') == body:
                    continue
                raise ValueError('would overwrite different contents: ' + relative)
            writes[target] = body
        if args.base_database:
            if not args.base_database.is_file():
                raise ValueError('base resource database does not exist')
            databases = [args.base_database]
            if args.workshop_addons:
                if not args.workshop_addons.is_dir():
                    raise ValueError('Workshop addon directory does not exist')
                databases.extend(sorted(args.workshop_addons.glob('*/resourceDatabase.rdb')))
            missing = unresolved_resources(files, [root, *args.dependency], databases)
            if missing:
                raise ValueError('unresolved references: ' + ', '.join(f'{{{guid}}}{path}' for guid, path in sorted(missing)))
        if not args.dry_run:
            created = []
            try:
                for path, body in writes.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('xb') as stream:
                        created.append(path)
                        stream.write(body)
            except OSError:
                for path in created:
                    if path.is_file() and writes[path].startswith(path.read_bytes()):
                        path.unlink()
                raise
        action = 'Checked' if args.dry_run else 'Installed'
        print(f'{action} {len(candidates)} mission drafts with {len(writes)} new files')
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
