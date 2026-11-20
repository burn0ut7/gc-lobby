"""Read a native polyline whose only child is a legacy capture trigger."""
import re

from boundaries import floats, validate_polygon
from briefings import block_end, properties

SHAPE = re.compile(r'(?P<group>\$grp )?PolylineShapeEntity(?: (?P<name>[\w.-]+))? \{')
MEMBER = re.compile(r' (?:([\w.-]+) )?\{')
POINT = re.compile(r'ShapePoint "\{[\da-fA-F]{16}\}" \{')


def members(block, grouped, name):
    if not grouped:
        return [(name, block[1:-1])]
    result = []
    start = 1
    while start < len(block) - 1:
        member = MEMBER.fullmatch(block[start])
        if not member:
            raise ValueError('unsupported grouped area placement')
        end = block_end(block, start)
        # Remove the group member's extra indent for the common parser.
        result.append((member[1], [line[1:] for line in block[start + 1:end]]))
        start = end + 1
    return result


def read(lines, trigger_pattern):
    origin, vertices, child = None, None, None
    seen = set()
    yaw = 0
    placement = []
    index = 0
    while index < len(lines):
        line = lines[index]
        field, _, value = line.strip().partition(' ')
        if field in seen:
            raise ValueError('duplicate polygon capture field ' + field)
        seen.add(field)
        end = index
        if field == 'coords':
            origin = floats(value, 3)
        elif field in ('angles', 'angleY'):
            if {'angleY', 'angles'} <= seen:
                raise ValueError('duplicate polygon orientation')
            if field == 'angleY':
                yaw, = floats(value, 1)
            else:
                pitch, yaw, roll = floats(value, 3)
                if pitch or roll:
                    raise ValueError('tilted capture polygon needs world-space validation')
        elif field == 'Points' and value == '{':
            vertices = []
            end = block_end(lines, index)
            point = index + 1
            while point < end:
                if not POINT.fullmatch(lines[point].strip()):
                    raise ValueError('unsupported polygon point')
                last = block_end(lines, point)
                position = properties(lines[point + 1:last], {'Position'}).get('Position', '0 0 0')
                vertices.append(floats(position, 3))
                point = last + 1
        elif field == 'LineColor':
            floats(value, 4)
        elif field == 'IsClosed' and value == '1':
            pass
        elif line == ' {':
            end = block_end(lines, index)
            head = trigger_pattern.fullmatch(lines[index + 1].strip())
            if (not head or head['group'] or head['name'] or block_end(lines, index + 1) != end - 1):
                raise ValueError('polygon requires one unnamed capture trigger child')
            child = (head, [item[1:] for item in lines[index + 2:end - 1]])
            index = end + 1
            continue
        else:
            raise ValueError('unsupported polygon capture field ' + field)
        placement.extend(lines[index:end + 1])
        index = end + 1
    if origin is None or not vertices or 'IsClosed' not in seen or not child:
        raise ValueError('capture requires a closed polygon, explicit origin and one trigger child')
    validate_polygon(vertices, origin, yaw)
    return placement, child, len(vertices)
