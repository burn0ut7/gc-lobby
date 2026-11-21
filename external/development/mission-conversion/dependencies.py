"""Resolve resource providers reachable from a committed addon's manifest."""
from collections import defaultdict
import re


BASE_GAME = '58D0FB3206B6F859'
GUID = re.compile(r'^ GUID "([0-9A-Fa-f]{16})"\s*$', re.M)
DEPENDENCIES = re.compile(r'^[ \t]+Dependencies[ \t]*\{([^{}]*)\}', re.M)
REF = re.compile(r'"([0-9A-Fa-f]{16})"')


def read_project(content, origin):
    text = content.decode('utf-8-sig')
    guid = GUID.search(text)
    if not guid:
        raise ValueError(f'cannot read addon GUID from {origin}')
    declarations = DEPENDENCIES.search(text)
    if not declarations:
        if re.search(r'^[ \t]+Dependencies\b', text, re.M):
            raise ValueError(f'cannot read dependencies from {origin}')
        return guid[1].upper(), ()
    if REF.sub('', declarations[1]).strip():
        raise ValueError(f'cannot read dependency GUIDs from {origin}')
    return guid[1].upper(), tuple(value.upper() for value in REF.findall(declarations[1]))


def checkout_project(snapshot):
    projects = [path for path in snapshot.files if path.endswith('.gproj') and '/' not in path]
    if len(projects) != 1:
        raise ValueError(f'expected one root .gproj in {snapshot.repo} at {snapshot.commit}')
    return read_project(snapshot.read_raw(projects[0]), snapshot.repo / projects[0])


def dependency_catalogue(checkouts, databases):
    local = {}
    for snapshot in checkouts:
        guid, children = checkout_project(snapshot)
        if guid in local and local[guid][0].commit != snapshot.commit:
            raise ValueError(f'multiple checkout revisions for dependency {guid}')
        local[guid] = snapshot, children
    installed = {}
    for database in databases[1:]:
        projects = sorted(database.parent.glob('*.gproj'))
        if len(projects) != 1:
            continue
        project = projects[0]
        guid, children = read_project(project.read_bytes(), project)
        if guid in installed:
            raise ValueError(f'multiple installed copies of dependency {guid}')
        installed[guid] = database, children
    return local, installed


def dependency_resources(target, checkouts, databases):
    """Use each supplied checkout in preference to the installed copy of the same addon."""
    root, requires = checkout_project(target)
    local, installed = dependency_catalogue(checkouts, databases)

    selected = [databases[0]]
    resources = defaultdict(list)
    unavailable = set()
    seen = {root, BASE_GAME}
    base_requires = ()
    base_projects = sorted(databases[0].parent.glob('*.gproj'))
    if len(base_projects) == 1:
        project = base_projects[0]
        guid, children = read_project(project.read_bytes(), project)
        if guid == BASE_GAME:
            base_requires = children
    pending = list(reversed((*base_requires, *requires)))
    while pending:
        guid = pending.pop()
        if guid in seen:
            continue
        seen.add(guid)
        if guid in local:
            snapshot, children = local[guid]
            for resource, paths in snapshot.resources.items():
                resources[resource].extend(path for path in paths if path not in resources[resource])
        elif guid in installed:
            database, children = installed[guid]
            selected.append(database)
        else:
            unavailable.add(guid)
            continue
        pending.extend(reversed(children))
    return resources, selected, sorted(unavailable)
