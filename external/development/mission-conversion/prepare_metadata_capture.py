"""Stage file-cleared missions for a scoped native metadata capture."""
import argparse
from functools import cache
import json
from pathlib import Path, PurePosixPath
import re

from catalog import SOURCE_TEXT, Snapshot
from dependencies import BASE_GAME, DEPENDENCIES, checkout_project, database_project, dependency_catalogue
from install_batch import resource_databases, unregistered_references
from launchers import WORLD_REF
from review import closure, inspect
from scenery import StockPrefabs
from terrain_parents import LONGMOOR_PARENT, has_reviewed_longmoor, is_longmoor_subscene


AUTHORED_CATALOGUE = re.compile(r'^[ \t]*m_aEntityCatalogs\b', re.M)


def capture_dependencies(project, local, installed, omit):
    manifests = {guid: dependencies for guid, (_, dependencies) in (installed | local).items()}
    manifests.setdefault(BASE_GAME, ())

    def reachable(roots):
        seen = set()
        pending = list(roots)
        while pending:
            guid = pending.pop()
            if guid not in seen:
                seen.add(guid)
                pending.extend(manifests.get(guid, ()))
        return seen

    full = reachable(project)
    if omit - full:
        raise ValueError('excluded addon IDs are outside the Missions Dev graph: ' + ', '.join(sorted(omit - full)))
    retained = tuple(guid for guid in project if not reachable([guid]) & omit)
    scoped = reachable(retained)
    if missing := scoped - manifests.keys():
        raise ValueError('missing manifests for retained addons: ' + ', '.join(sorted(missing)))
    return retained, scoped, full - scoped


def prepare(dev, lobby, databases, audit, destination, dependencies=(), omit=frozenset()):
    if audit['target'] != dev.commit:
        raise ValueError('rerun the mission audit against the current Dev commit')
    lobby_id, lobby_dependencies = checkout_project(lobby)
    _, dev_dependencies = checkout_project(dev)
    base_scope = (BASE_GAME, lobby_id)
    if not set(base_scope) <= set(dev_dependencies) or set(lobby_dependencies) != {BASE_GAME}:
        raise ValueError('expected the Missions Dev and Lobby Dev dependency manifests')

    local, installed = dependency_catalogue((lobby, *dependencies), databases)
    base_project = database_project(databases[0])
    if base_project and base_project[0] == BASE_GAME:
        installed[BASE_GAME] = databases[0], base_project[1]
    selected, scope, excluded_addons = capture_dependencies(
        dev_dependencies if omit else base_scope, local, installed, omit
    )
    if not set(base_scope) <= scope:
        raise ValueError('capture requires Base and Lobby Dev')
    providers = [databases[0], *(db for guid, (db, _) in installed.items()
                                  if guid in scope and guid not in local and guid != BASE_GAME)]
    available = set()
    for repo in (dev, *(repo for guid, (repo, _) in local.items() if guid in scope)):
        available.update((guid, paths[0]) for guid, paths in repo.resources.items() if len(paths) == 1)
    excluded_databases = [StockPrefabs(db) for guid, (db, _) in installed.items()
                          if guid in excluded_addons and guid not in local]
    excluded_checkouts = [repo for guid, (repo, _) in local.items() if guid in excluded_addons]

    @cache
    def removed_provider(guid):
        return any(guid in repo.resources for repo in excluded_checkouts) or any(
            db.registered_paths(guid) for db in excluded_databases
        )

    files = set()
    candidates = {}
    excluded = {}
    authored_catalogues = {}
    for header, checks in audit['committedLauncherMetadata'].items():
        if not checks['gcMissionHeader'] or checks['generatedSnapshot']:
            continue
        world = WORLD_REF.search(dev.read(header))[3]
        paths = closure(dev, world, header)
        catalogue_paths = sorted(
            path for path in paths if PurePosixPath(path).suffix.lower() in SOURCE_TEXT
            and AUTHORED_CATALOGUE.search(dev.read(path))
        )
        if catalogue_paths:
            authored_catalogues[header] = catalogue_paths
            continue
        candidates[header] = world, paths, inspect(dev, paths)[2]
    missing = unregistered_references({
        ref for _, _, refs in candidates.values() for ref in refs if ref not in available
    }, providers)
    longmoor = LONGMOOR_PARENT in missing and has_reviewed_longmoor(providers)
    eligible = {}
    for header, (world, paths, refs) in candidates.items():
        unresolved = sorted(
            ref for ref, owners in refs.items()
            if removed_provider(ref[0]) or (ref in missing and not (
                longmoor and ref == LONGMOOR_PARENT and owners == {world}
                and is_longmoor_subscene(world, dev.read(world))
            ))
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
    declarations = ' '.join(f'"{guid}"' for guid in selected)
    replacement = f' Dependencies {{\n  {declarations}\n }}'
    (destination / project).write_bytes(DEPENDENCIES.sub(replacement, manifest, count=1).encode('utf-8'))
    return {
        'missionRevision': dev.commit,
        'dependencyRevisions': {guid: repo.commit for guid, (repo, _) in local.items() if guid in scope},
        'project': str(destination / project),
        'scope': 'saved mission references resolved against the staged project and its retained dependency graph',
        'directDependencies': selected,
        'excludedDependencies': sorted(excluded_addons),
        'eligible': eligible,
        'referencesOutsideScope': excluded,
        'authoredCataloguesForReview': authored_catalogues,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', type=Path, required=True)
    parser.add_argument('--lobby', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--base-database', type=Path, required=True)
    parser.add_argument('--workshop', type=Path)
    parser.add_argument('--dependency-checkout', type=Path, action='append', default=[])
    parser.add_argument('--exclude-addon', action='append', default=[], help='omit addon GUID and all dependents')
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    report = prepare(
        Snapshot(args.missions, 'HEAD'), Snapshot(args.lobby, 'HEAD'),
        resource_databases(args.base_database, args.workshop), audit, args.stage,
        [Snapshot(repo, 'HEAD') for repo in args.dependency_checkout],
        {guid.upper() for guid in args.exclude_addon},
    )
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
