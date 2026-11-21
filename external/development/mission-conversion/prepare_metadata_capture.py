"""Stage file-cleared Base/Lobby missions for an isolated native metadata capture."""
import argparse
import json
from pathlib import Path

from catalog import Snapshot
from dependencies import BASE_GAME, DEPENDENCIES, checkout_project
from launchers import WORLD_REF
from review import closure, inspect
from scenery import StockPrefabs


def prepare(dev, lobby, stock, audit, destination):
    if audit['target'] != dev.commit:
        raise ValueError('rerun the mission audit against the current Dev commit')
    lobby_id, lobby_dependencies = checkout_project(lobby)
    _, dev_dependencies = checkout_project(dev)
    allowed = {BASE_GAME, lobby_id}
    if not allowed <= set(dev_dependencies) or set(lobby_dependencies) != {BASE_GAME}:
        raise ValueError('expected the Missions Dev and Lobby Dev dependency manifests')

    files = set()
    eligible = {}
    excluded = {}
    for header, checks in audit['committedLauncherMetadata'].items():
        if not checks['gcMissionHeader'] or checks['generatedSnapshot']:
            continue
        world = WORLD_REF.search(dev.read(header))[3]
        paths = closure(dev, world, header)
        unresolved = sorted(
            (guid, path) for guid, path in inspect(dev, paths)[2]
            if not stock.contains(guid, path)
            and dev.resources.get(guid) != [path]
            and lobby.resources.get(guid) != [path]
        )
        if unresolved:
            excluded[header] = unresolved
            continue
        files.update(paths)
        eligible[header] = world

    destination = destination.resolve()
    if any(destination.is_relative_to(repo.repo.resolve()) for repo in (dev, lobby)):
        raise ValueError('stage the capture project outside both addon checkouts')
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(files):
        output = destination / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(dev.read_raw(path))
    project = next(path for path in dev.files if path.endswith('.gproj') and '/' not in path)
    manifest = dev.read_raw(project).decode('utf-8')
    replacement = f' Dependencies {{\n  "{BASE_GAME}" "{lobby_id}"\n }}'
    (destination / project).write_bytes(DEPENDENCIES.sub(replacement, manifest, count=1).encode('utf-8'))
    return {
        'missionRevision': dev.commit,
        'lobbyRevision': lobby.commit,
        'project': str(destination / project),
        'scope': 'saved mission references resolved against Base, Lobby Dev, and Missions Dev',
        'eligible': eligible,
        'referencesOutsideScope': excluded,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', type=Path, required=True)
    parser.add_argument('--lobby', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--base-database', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    report = prepare(
        Snapshot(args.missions, 'HEAD'), Snapshot(args.lobby, 'HEAD'),
        StockPrefabs(args.base_database), audit, args.stage,
    )
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
