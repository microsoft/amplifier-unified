"""List tracked review inputs without reading their contents or importing the app."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys


KINDS = (
    'source', 'contracts', 'guidance', 'tooling', 'configuration', 'tests',
    'documentation', 'generated', 'evidence', 'assets', 'locks', 'unclassified',
)
OPT_IN = {'generated', 'evidence', 'assets', 'locks'}
ATTRIBUTE = 'source-context'


def git(root, *args, input=None):
    return subprocess.check_output(
        ['git', '-C', str(root), *args], input=input, stderr=subprocess.PIPE,
    )


def inventory(root):
    """Index membership and effective Git attributes; working-tree sizes only."""
    paths = sorted(set(git(root, 'ls-files', '--cached', '-z').split(b'\0')) - {b''})
    if not paths:
        return []
    fields = git(root, 'check-attr', '-z', '--stdin', ATTRIBUTE,
                 input=b'\0'.join(paths) + b'\0').split(b'\0')[:-1]
    attributes = {path: os.fsdecode(value) for path, _, value
                  in zip(fields[::3], fields[1::3], fields[2::3])}
    rows = []
    for raw in paths:
        path = os.fsdecode(raw)
        value = attributes.get(raw, 'unspecified')
        kind = value if value in KINDS else 'unclassified'
        try:
            info = (root / path).lstat()  # Never follow links to read other roots.
            status = ('file' if stat.S_ISREG(info.st_mode) else
                      'symlink' if stat.S_ISLNK(info.st_mode) else 'other')
            size = info.st_size if status == 'file' else None
        except FileNotFoundError:
            status, size = 'missing', None
        rows.append({'path': path, 'kind': kind, 'bytes': size, 'status': status})
    return rows


def prefix(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        raise argparse.ArgumentTypeError('Use a repository-relative path without ..')
    return path.as_posix().rstrip('/')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Git checkout (defaults to this script\'s repository)')
    parser.add_argument('--format', choices=('summary', 'paths', 'json'), default='summary')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--kind', choices=KINDS, action='append', help='Repeat to select kinds')
    selection.add_argument('--all', action='store_true', help='Include opt-in material')
    parser.add_argument('--under', type=prefix, action='append', default=[],
                        help='Restrict to a file or directory; repeat for alternatives')
    parser.add_argument('--null', action='store_true', help='NUL-terminate --format paths')
    parser.add_argument('--check', action='store_true',
                        help='Fail if any tracked path is unclassified, before selection')
    args = parser.parse_args(argv)
    if args.null and args.format != 'paths':
        parser.error('--null requires --format paths')
    try:
        root = Path(os.fsdecode(git(args.root, 'rev-parse', '--show-toplevel')).rstrip('\n'))
        rows = inventory(root)
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f'Cannot inspect Git checkout: {error}\n')

    unknown = [row['path'] for row in rows if row['kind'] == 'unclassified']
    if unknown:
        print(f'{len(unknown)} unclassified tracked paths (kept in default selection):', file=sys.stderr)
        for path in unknown[:10]:
            print('  ' + json.dumps(path), file=sys.stderr)
        if len(unknown) > 10:
            print('  Select --kind unclassified to list all.', file=sys.stderr)
    kinds = (set(args.kind) if args.kind else
             set(KINDS) if args.all or args.format == 'summary' else set(KINDS) - OPT_IN)
    selected = [row for row in rows if row['kind'] in kinds and
                (not args.under or any(p == '.' or row['path'] == p or
                 row['path'].startswith(p + '/') for p in args.under))]
    if args.format == 'json':
        print(json.dumps(selected, indent=2))
    elif args.format == 'paths':
        separator = b'\0' if args.null else b'\n'
        for row in selected:
            sys.stdout.buffer.write(os.fsencode(row['path']) + separator)
    else:
        counts, sizes = Counter(), Counter()
        for row in selected:
            counts[row['kind']] += 1
            sizes[row['kind']] += row['bytes'] or 0
        print('Tracked paths; working-tree regular-file bytes, not tokens or dependency closure.')
        print(f'{"Kind":16} {"Files":>6} {"Bytes":>12}  Path/JSON default')
        for kind in KINDS:
            if counts[kind]:
                print(f'{kind:16} {counts[kind]:6} {sizes[kind]:12}  '
                      + ('opt in' if kind in OPT_IN else 'included'))
        print(f'{"Total":16} {len(selected):6} {sum(sizes.values()):12}')
        absent = sum(row['status'] != 'file' for row in selected)
        if absent:
            print(f'{absent} nonregular or missing paths; see --format json for status.')
    return int(args.check and bool(unknown))


if __name__ == '__main__':
    raise SystemExit(main())
