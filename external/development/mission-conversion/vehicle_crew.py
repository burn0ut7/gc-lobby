"""Convert an authored AI crew for a placed, registered stock vehicle."""
from pathlib import PurePosixPath
import re

from briefings import block_end, properties
from catalog import REF
from groups import legacy_member
from local_loadouts import unchanged_units_parent
from polygon_capture import members

VEHICLE = re.compile(
    r'(?P<group>\$grp )?(?:Vehicle|Tank|Turret)(?: (?P<name>[\w.-]+))? : '
    r'"\{(?P<guid>[\da-fA-F]{16})\}(?P<path>Prefabs/Vehicles/[^"]+\.et)" \{')
COMPONENT = re.compile(r'  TILW_VehicleCrewComponent (?P<id>"\{[\da-fA-F]{16}\}") \{')
SERIALIZED_ID = r'"\{[\da-fA-F]{16}\}"'
SEAT_TYPES = ('Pilot', 'Turret', 'Cargo')


def contents(lines, field):
    if (not lines or not re.fullmatch(field + r' \{', lines[0].strip())
            or block_end(lines, 0) != len(lines) - 1):
        raise ValueError('crew authoring needs one ' + field.split()[0])
    return lines[1:-1]


def character_reference(guid, path, source_units, dev_units, stock, owned):
    if guid in owned or not path.startswith('Prefabs/Characters/'):
        raise ValueError('unverified crew character ' + guid)
    if guid in source_units.resources or guid in dev_units.resources:
        if (not unchanged_units_parent(source_units, dev_units, guid)
                or legacy_member(source_units, guid, False)):
            raise ValueError('crew character ancestry needs review ' + guid)
        canonical, = dev_units.resources[guid]
        if PurePosixPath(path).name != PurePosixPath(canonical).name:
            raise ValueError('crew character path needs review ' + guid)
        return f'"{{{guid}}}{canonical}"'
    if not stock.contains(guid, path):
        raise ValueError('unregistered crew character ' + guid)
    return f'"{{{guid}}}{path}"'


def render_component(component, lines, resolve_character):
    station = lines
    for field in (r'm_crewConfig TILW_CrewConfig ' + SERIALIZED_ID, 'm_crewGroups',
                  'TILW_CrewGroup ' + SERIALIZED_ID, 'm_crewStations', 'TILW_CrewStation ' + SERIALIZED_ID):
        station = contents(station, field)
    values = properties(station, {'m_characters', *('m_allow' + seat for seat in SEAT_TYPES)})
    flags = {seat: values.get('m_allow' + seat, '1') for seat in SEAT_TYPES}
    authored = values.get('m_characters', '')
    if (not authored or REF.sub('', authored).strip() or all(flag == '0' for flag in flags.values())
            or any(flag not in ('0', '1') for flag in flags.values())):
        raise ValueError('crew migration requires explicit characters and qualifying seats')
    roles = [resolve_character(guid.upper(), path) for guid, path in REF.findall(authored)]
    return [f'  GC_VehicleCrewComponent {component["id"]} {{',
            *(f'   m_bAllow{seat} 0' for seat in SEAT_TYPES if flags[seat] == '0'),
            '   m_aCharacters {', *('    ' + role for role in roles), '   }', '  }']


def convert(block, source_units, dev_units, stock, owned):
    if not any('TILW_VehicleCrewComponent' in line for line in block):
        return block
    root = VEHICLE.fullmatch(block[0])
    if not root:
        raise ValueError('crew needs a registered stock vehicle')
    guid, path = root['guid'].upper(), root['path']
    if (guid in owned or guid in source_units.resources or guid in dev_units.resources
            or not stock.contains(guid, path)):
        raise ValueError('unverified crew vehicle ' + guid)

    def instance(lines):
        if not any('TILW_VehicleCrewComponent' in line for line in lines):
            return lines
        roots = [index for index, line in enumerate(lines) if line == ' components {']
        crew = [(index, COMPONENT.fullmatch(line)) for index, line in enumerate(lines)
                if 'TILW_VehicleCrewComponent' in line]
        if len(roots) != 1 or len(crew) != 1:
            raise ValueError('placement needs exactly one standard crew component')
        start = roots[0]
        index, component = crew[0]
        if not component or not start < index < block_end(lines, start):
            raise ValueError('crew component is outside the vehicle components')
        end = block_end(lines, index)
        replacement = render_component(
            component, lines[index + 1:end],
            lambda guid, path: character_reference(guid, path, source_units, dev_units, stock, owned))
        return [*lines[:index], *replacement, *lines[end + 1:]]

    if not root['group']:
        return [block[0], *instance(block[1:-1]), block[-1]]
    rendered = [block[0]]
    for name, lines in members(block, True, None):
        rendered.append(f' {name} {{' if name else ' {')
        rendered.extend(' ' + line for line in instance(lines))
        rendered.append(' }')
    return [*rendered, block[-1]]
