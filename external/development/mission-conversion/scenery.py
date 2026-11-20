"""Identify intact, scenery and waypoint layers backed by installed stock prefabs."""
import re
from struct import pack, unpack_from

# Enfusion RDBC v6 and v7 serialize resource paths with their NUL-inclusive length,
# followed by file flags and the resource GUID in little-endian order.
class StockPrefabs:
    PATH_FLAGS = b'\x06\0\0\0\0\0'

    def __init__(self, database):
        self.data = database.read_bytes()
        if self.data[:4] != b'FORM' or self.data[8:16] not in (b'RDBC\x06\0\0\0', b'RDBC\x07\0\0\0'):
            raise ValueError('expected a Reforger v6 or v7 resource database')
        self.known = {}

    def contains(self, guid, path):
        key = (guid, path)
        if key not in self.known:
            name = path.encode() + b'\0'
            record = pack('<I', len(name)) + name + self.PATH_FLAGS + pack('<Q', int(guid, 16))
            self.known[key] = record in self.data
        return self.known[key]

    def registered_paths(self, guid):
        """Find other path registrations for an exact GUID in this database."""
        paths = set()
        end_marker = b'\0' + self.PATH_FLAGS + pack('<Q', int(guid, 16))
        end = self.data.find(end_marker, 16)
        while end >= 0:
            # The length is a little-endian uint32 with a zero high byte.
            start = self.data.rfind(b'\0', 16, end) + 1
            if start >= 20 and unpack_from('<I', self.data, start - 4)[0] == end - start + 1:
                try:
                    paths.add(self.data[start:end].decode('utf-8'))
                except UnicodeDecodeError:
                    pass
            end = self.data.find(end_marker, end + len(end_marker))
        return sorted(paths)


SCENERY_CLASS = r'(?:GenericEntity|StaticModelEntity|Tree|DecalEntity|SCR_DestructibleBuildingEntity|SCR_DestructibleEntity|SCR_IndestructibleEnvironmentalEntity)'
NAME = r'[\w.\-]+'
WAYPOINT_CLASS = r'(?:SCR_AIWaypoint|SCR_DefendWaypoint|SCR_SearchAndDestroyWaypoint|SCR_TimedWaypoint|SCR_BoardingWaypoint|SCR_EntityWaypoint|SCR_SuppressWaypoint|SCR_BoardingTimedWaypoint|SCR_BoardingEntityWaypoint|AIWaypointCycle)'
ROOT = re.compile(rf'(?P<group>\$grp )?(?P<kind>{SCENERY_CLASS}|{WAYPOINT_CLASS})(?: {NAME})? : "\{{(?P<guid>[\da-fA-F]{{16}})\}}(?P<path>[^"]+\.et)" \{{')
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
FIELD = re.compile(rf'(?:(?:coords|angles) {NUMBER} {NUMBER} {NUMBER}|(?:angleX|angleY|angleZ|scale) {NUMBER})')
SCENERY = ('prefabs/structures/', 'prefabs/props/', 'prefabs/rocks/', 'prefabs/vegetation/',
           'prefabs/compositions/', 'prefabs/buildings/', 'prefabs/decals/')


def placements(text, stock, owned):
    """Accept a whole layer only if all placements use recognized stock fields."""
    count = 0
    stack = []
    kinds = set()
    for line in text.splitlines():
        token = line.strip()
        if not token:
            continue
        if not stack:
            root = ROOT.fullmatch(token)
            if not root:
                return None
            waypoint = bool(re.fullmatch(WAYPOINT_CLASS, root['kind']))
            kinds.add('waypoints' if waypoint else 'scenery')
            prefixes = ('prefabs/ai/waypoints/',) if waypoint else SCENERY
            if not root['path'].lower().startswith(prefixes):
                return None
            if root['guid'].upper() in owned or not stock.contains(root['guid'], root['path']):
                return None
            group = bool(root['group'])
            stack.append('group' if group else 'entity')
            count += int(not group)
        elif token == '}':
            stack.pop()
        elif stack[-1] == 'group' and re.fullmatch(rf'(?:{NAME} )?\{{', token):
            stack.append('entity')
            count += 1
        elif waypoint and stack[-1] == 'entity' and token == 'CycleWaypoints {':
            stack.append('cycle')
        elif stack[-1] == 'cycle' and re.fullmatch(r'(?:"[\w.\- ]+"\s*)+', token):
            continue
        elif waypoint and stack[-1] == 'entity' and re.fullmatch(rf'CompletionRadius {NUMBER}', token):
            continue
        elif stack[-1] != 'entity' or not FIELD.fullmatch(token):
            return None
    return (sorted(kinds), count) if count and not stack else None
