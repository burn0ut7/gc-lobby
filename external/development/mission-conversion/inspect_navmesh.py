"""Inspect a saved Reforger navigation archive without changing its files."""

import argparse
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from struct import iter_unpack, unpack_from


def verify_tiles(tiles, records, capacity):
    """Check native v3 container boundaries and each populated tile's grid identity."""
    digest = sha256()
    empty = 0
    populated = 0
    total_bytes = 0
    for container in range((capacity + 99) // 100):
        file = tiles / f'navData_{container}.ntile'
        data = file.read_bytes()
        count = min(capacity - container * 100, 100)
        table_size = count * 24
        payload = 28 + table_size
        if (len(data) < payload or data[:4] != b'FORM' or data[8:16] != b'NAVDTIHE'
                or unpack_from('>I', data, 4)[0] != len(data) - 8
                or unpack_from('>I', data, 16)[0] != table_size
                or data[payload - 8:payload - 4] != b'DATA'
                or unpack_from('>I', data, payload - 4)[0] != len(data) - payload):
            raise ValueError(f'{file} has an invalid version 3 tile container')

        previous_offset = -16
        for index in range(count):
            entry = 20 + index * 24
            size, = unpack_from('<I', data, entry)
            offset, = unpack_from('<I', data, entry + 16)
            cell = records.get((container, index), (0, 0))
            if not size:
                if cell != (0, 0):
                    raise ValueError(f'{file} has no payload for cell {cell}')
                empty += 1
                continue
            start = payload + offset
            if (size < 16 or offset < previous_offset + 16 or start + 16 > len(data)
                    or (previous_offset == -16 and offset != 0)
                    or data[start:start + 4] != b'VAND'
                    or unpack_from('<3I', data, start + 4) != (11, *cell)):
                raise ValueError(f'{file} has an invalid payload for cell {cell} at index {index}')
            previous_offset = offset
            populated += 1
        if previous_offset == -16 and payload != len(data):
            raise ValueError(f'{file} has unindexed tile data')
        digest.update(sha256(data).digest())
        total_bytes += len(data)
    return {
        'populatedTiles': populated,
        'emptyTiles': empty,
        'containerBytes': total_bytes,
        'orderedContainerSha256': digest.hexdigest(),
    }


def inspect(path, validate_tiles=False):
    data = path.read_bytes()
    chunk = data.find(b'NAVMTFHE')
    if len(data) < 48 or data[:2] != b'EV' or chunk < 0 or chunk + 12 > len(data):
        raise ValueError(f'{path} has no supported navigation index')
    version, origin_x, _, origin_z, width, depth, capacity = unpack_from('<I5fI', data, 4)
    if (version not in (2, 3) or not all(isfinite(value) for value in (origin_x, origin_z, width, depth))
            or min(width, depth) <= 0 or not capacity):
        raise ValueError(f'{path} has unsupported navigation settings')
    size, = unpack_from('>I', data, chunk + 8)
    start = chunk + 12
    if size != capacity * 16 or start + size > len(data):
        raise ValueError(f'{path} has an invalid navigation index')

    container_count = (capacity + 99) // 100
    cells = set()
    records = {}
    for x, z, container, index in iter_unpack('<4I', data[start:start + size]):
        if not (x or z):
            # Unused entries can carry assigned container and index fields.
            # The index alone cannot distinguish them from the origin cell.
            continue
        if ((x, z) in cells or (container, index) in records
                or container >= container_count or index >= 100 or container * 100 + index >= capacity):
            raise ValueError(f'{path} has a duplicate or invalid tile index')
        cells.add((x, z))
        records[container, index] = x, z

    tiles = path.parent / '.NavData' / path.stem
    missing = [index for index in range(container_count) if not (tiles / f'navData_{index}.ntile').is_file()]
    report = {
        'file': str(path.resolve()),
        'sha256': sha256(data).hexdigest(),
        'format': version,
        'origin': [origin_x, origin_z],
        'tileSize': [width, depth],
        'indexCapacity': capacity,
        'indexedCells': len(cells),
        'excludesOriginCell': True,
        'expectedContainers': container_count,
        'missingContainers': missing,
    }
    if cells:
        x, z = zip(*cells)
        report['tileBounds'] = [min(x), min(z), max(x), max(z)]
        report['worldBounds'] = [
            origin_x + min(x) * width, origin_z + min(z) * depth,
            origin_x + (max(x) + 1) * width, origin_z + (max(z) + 1) * depth,
        ]
    if validate_tiles:
        if version != 3:
            raise ValueError(f'{path} requires a version 3 index for tile validation')
        report['verifiedTiles'] = verify_tiles(tiles, records, capacity)
    return report, cells


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--compare', type=Path, help='another archive on the same tile grid')
    parser.add_argument('--verify-tiles', action='store_true',
                        help='verify version 3 tile payloads in the comparison archive, or the main archive if used alone')
    args = parser.parse_args()
    try:
        report, cells = inspect(args.archive, args.verify_tiles and not args.compare)
        if args.compare:
            comparison, compared_cells = inspect(args.compare, args.verify_tiles)
            if any(report[key] != comparison[key] for key in ('origin', 'tileSize')):
                raise ValueError('comparison requires the same origin and tile dimensions')
            report['comparison'] = {
                **comparison,
                'indexedCellsMissingFromComparison': len(cells - compared_cells),
                'additionalIndexedCellsInComparison': len(compared_cells - cells),
            }
        print(json.dumps(report, indent=2))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
