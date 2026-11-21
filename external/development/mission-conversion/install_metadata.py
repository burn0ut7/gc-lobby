"""Verify and install a batch of native snapshots into a clean Missions checkout."""
import argparse
import json
from pathlib import Path
import subprocess

from catalog import Snapshot
from install_batch import replace_reviewed_file
from launchers import prepare_metadata_headers


def require_clean(dev):
    revision = subprocess.check_output(['git', '-C', str(dev.repo), 'rev-parse', 'HEAD']).decode().strip()
    status = subprocess.check_output([
        'git', '-C', str(dev.repo), 'status', '--porcelain', '--untracked-files=no',
    ])
    if revision != dev.commit or status:
        raise ValueError('use a clean Missions checkout at the audited revision')


def batch_changes(dev, plan, capture, headers):
    root = dev.repo.resolve()
    if capture.resolve().is_relative_to(root):
        raise ValueError('use an external capture directory')
    require_clean(dev)
    output, _, _ = prepare_metadata_headers(dev, plan, headers, capture)
    changes = {}
    for relative, after in output.items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f'invalid checkout location for {relative}')
        before = path.read_bytes()
        original = dev.read_raw(relative)
        if before.replace(b'\r\n', b'\n') != original.replace(b'\r\n', b'\n'):
            raise ValueError(f'{relative} differs from the audited commit')
        if b'\r\n' in before and b'\r\n' not in after:
            after = after.replace(b'\n', b'\r\n')
        changes[path] = before, after
    return changes


def apply_changes(dev, changes):
    require_clean(dev)
    installed = []
    try:
        for path, (before, after) in changes.items():
            replace_reviewed_file(path, before, after)
            installed.append(path)
    except OSError as exc:
        failures = []
        for path in reversed(installed):
            before, after = changes[path]
            try:
                replace_reviewed_file(path, after, before)
            except OSError:
                failures.append(path.relative_to(dev.repo.resolve()).as_posix())
        if failures:
            raise OSError(f'{exc}. Concurrent changes prevented rollback of {", ".join(failures)}') from exc
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='write the verified files to the checkout')
    parser.add_argument('headers', nargs='+', help='exact paths of the captured launcher headers')
    args = parser.parse_args()
    try:
        dev = Snapshot(args.missions, 'HEAD')
        plan = json.loads(args.audit.read_text(encoding='utf-8'))
        changes = batch_changes(dev, plan, args.capture, args.headers)
        if args.apply:
            apply_changes(dev, changes)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(f'{len(changes)} snapshots {"installed" if args.apply else "verified for installation"}')


if __name__ == '__main__':
    main()
