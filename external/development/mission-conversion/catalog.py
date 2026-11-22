"""Compare committed mission resources without touching either checkout."""
from collections import Counter, defaultdict
from functools import cached_property, lru_cache
from pathlib import PurePosixPath
import re
from struct import iter_unpack, unpack_from
import subprocess


REF = re.compile(r'"\{([0-9A-Fa-f]{16})\}([^"\r\n]+)"')
WORLD = re.compile(r'^\s*World\s+"\{([0-9A-Fa-f]{16})\}', re.M)
NAME = re.compile(r'^\s*Name\s+"\{([0-9A-Fa-f]{16})\}', re.M)
# Match typed declarations, excluding quoted descriptions, resource paths and entity names.
TYPES = re.compile(r'^[ \t]*(?:\$grp )?(?:(?:m_\w+) )?((?:TILW|PS|GC)_\w+|\w*GameMode\w*|SCR_AIGroup)\b[^\n]*\{[ \t]*$', re.M)
VEHICLE_LINK = re.compile(r'^\s*m_sAttachmentGroupName\s+"([^"\r\n]+)"\s*$')
SOURCE_TEXT = {'.ent', '.layer', '.et', '.conf', '.layout', '.ptc', '.emat', '.imageset'}
OBSOLETE_NAVMESH = {
    'worlds/Perfk/NavMesh/Ruha_Power_Plant/PK_Ruha_Power_Plant.nmn':
        'cc346c28b8bc3c04e3ee72eb31de4afa8d483321',
}
GC_HEADER_NAMES = r'GC_(?:MissionHeader|ScenarioFrameworkMissionHeader)'
GC_HEADER = re.compile(r'\A\s*' + GC_HEADER_NAMES + r'(?=\s*\{)')
MISSION_HEADER = re.compile(r'\A\s*(?:SCR_MissionHeader\w*|' + GC_HEADER_NAMES + r')\b')


class Snapshot:
    def __init__(self, repo, revision):
        self.repo = repo
        def git(*args, **kwargs):
            return subprocess.check_output(['git', '-C', str(repo), *args], **kwargs)

        self.commit = git('rev-parse', '--verify', revision + '^{commit}').decode().strip()
        self.files = {}
        for row in git('ls-tree', '-rz', self.commit).split(b'\0'):
            if row:
                entry, name = row.split(b'\t', 1)
                self.files[name.decode()] = entry.split()[2].decode()

        # Read by object ID. This bypasses checkout filters and Windows path limits.
        wanted = {oid for path, oid in self.files.items()
                  if PurePosixPath(path).suffix.lower() in SOURCE_TEXT | {'.meta'}}
        objects = git('cat-file', '--batch', input=('\n'.join(sorted(wanted)) + '\n').encode())
        self.blobs = {}
        offset = 0
        while offset < len(objects):
            end = objects.index(b'\n', offset)
            oid, kind, size = objects[offset:end].split()
            start = end + 1
            offset = start + int(size) + 1
            self.blobs[oid.decode()] = objects[start:offset - 1]

        self.resources = defaultdict(list)
        for path in self.files:
            if path.endswith('.meta'):
                match = NAME.search(self.read(path))
                if match and path[:-5] in self.files:
                    self.resources[match[1].upper()].append(path[:-5])

    @lru_cache(maxsize=4096)
    def read(self, path):
        return self.blobs[self.files[path]].decode('utf-8-sig').replace('\r\n', '\n')

    def read_raw(self, path):
        oid = self.files[path]
        if oid in self.blobs:
            return self.blobs[oid]
        return subprocess.check_output(['git', '-C', str(self.repo), 'cat-file', 'blob', oid])

    def navmesh_tiles(self, path):
        resource = PurePosixPath(path)
        prefix = f'{resource.parent}/.NavData/{resource.stem}/'
        return sorted(file for file in self.files if file.startswith(prefix) and file.endswith('.ntile'))

    def legacy_navmesh_region(self, path):
        """Read the tile bounds from a reviewed obsolete navmesh header."""
        if OBSOLETE_NAVMESH.get(path) != self.files[path]:
            return None
        data = self.read_raw(path)
        chunk = data.find(b'NAVMTFHE')
        if chunk < 0 or chunk + 12 > len(data):
            return None
        size, = unpack_from('>I', data, chunk + 8)
        start = chunk + 12
        if size % 16 or start + size > len(data):
            return None
        cells = {(x, y) for x, y, _, _ in iter_unpack('<4I', data[start:start + size]) if x or y}
        if not cells:
            return None
        x, y = zip(*cells)
        return min(x), max(x), min(y), max(y), len(cells)

    @cached_property
    def layers(self):
        result = defaultdict(list)
        for path in self.files:
            if path.lower().endswith('.layer'):
                for parent in PurePosixPath(path).parents:
                    if parent.name.lower().endswith('_layers'):
                        result[str(parent)[:-7].lower() + '.ent'].append(path)
                        break
        return result

    def closure(self, start):
        todo = [start]
        seen = set()
        while todo:
            path = todo.pop()
            if path in seen:
                continue
            seen.add(path)
            if path.lower().endswith('.ent'):
                todo.extend(self.layers[path.lower()])
            elif path.lower().endswith('.nmn'):
                todo.extend(self.navmesh_tiles(path))
            if PurePosixPath(path).suffix.lower() in SOURCE_TEXT:
                for guid, _ in REF.findall(self.read(path)):
                    todo.extend(self.resources.get(guid.upper(), []))
        return seen

    def headers(self):
        for path in self.files:
            if path.lower().endswith('.conf') and MISSION_HEADER.match(self.read(path)):
                yield path


def compare(source, target, start):
    closure = sorted(source.closure(start))
    kinds = Counter()
    delta = Counter()
    settings = defaultdict(list)
    vehicle_links = defaultdict(list)
    for path in closure:
        if PurePosixPath(path).suffix.lower() in SOURCE_TEXT:
            text = source.read(path)
            kinds.update(TYPES.findall(text))
            for no, line in enumerate(text.splitlines(), 1):
                # Keep authored overrides (including zero), units and source location.
                field = re.match(r'^\s*(m_\w*(?:respawn|freezetime|suppressgameend)\w*)\s+(.+)$',
                                 line, re.I)
                if field:
                    settings[path].append([no, field[1], field[2]])
                if re.match(r'^\s*PS_FactionRespawnCount\s', line):
                    settings[path].append([no, 'PS_FactionRespawnCount', 'see faction entry'])
                attachment = VEHICLE_LINK.fullmatch(line)
                if attachment:
                    vehicle_links[path].append([no, attachment[1]])
        if path not in target.files:
            delta['absent'] += 1
        elif source.files[path] == target.files[path]:
            delta['identical'] += 1
        else:
            delta['changed'] += 1
    report = {'sourceResourceCount': len(closure), 'devPaths': dict(sorted(delta.items())),
              'serializedTypes': dict(sorted(kinds.items())), 'configurationOverrides': dict(settings)}
    if vehicle_links:
        report['legacyVehicleGroupLinks'] = dict(vehicle_links)
    return report
