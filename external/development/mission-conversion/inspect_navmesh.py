"""Inspect a saved Reforger navigation archive without changing its files."""

import argparse
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from struct import iter_unpack, unpack_from


def inspect(path):
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
    for x, z, container, index in iter_unpack('<4I', data[start:start + size]):
        if not (x or z):
            # Unused entries can carry assigned container and index fields.
            # The index alone cannot distinguish them from the origin cell.
            continue
        if (x, z) in cells or container >= container_count or index >= 100:
            raise ValueError(f'{path} has a duplicate or invalid tile index')
        cells.add((x, z))

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
    return report, cells


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--compare', type=Path, help='another archive on the same tile grid')
    args = parser.parse_args()
    try:
        report, cells = inspect(args.archive)
        if args.compare:
            comparison, compared_cells = inspect(args.compare)
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
