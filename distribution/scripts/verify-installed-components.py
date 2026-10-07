#!/usr/bin/env python3
"""Keep the packed dependencies and served client identical to reviewed archives."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import shutil
import tarfile
import json


def files(root):
    if root.is_symlink():
        raise ValueError('Component root cannot be a symbolic link: ' + str(root))
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Component files cannot be symbolic links: ' + str(path))
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def verify(root, *, stage_web=False):
    spec = importlib.util.spec_from_file_location('artifacts', Path(__file__).with_name('verify-artifacts.py'))
    artifacts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(artifacts)
    count = artifacts.verify(root)
    receipt = json.loads((root / 'components.json').read_text())
    for name, row in receipt['components'].items():
        expected = {}
        with tarfile.open(root / row['artifact']) as archive:
            for member in archive:
                parts = member.name.split('/')
                if (not member.isfile() or parts[0] != 'package' or
                        any(part in ('', '.', '..') for part in parts) or '\\' in member.name):
                    raise ValueError('Unsupported component archive member: ' + member.name)
                expected['/'.join(parts[1:])] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
        installed = files(root / 'node_modules' / name)
        # npm may install transitive dependencies inside a component; those are
        # independent packages, not files supplied by this component archive.
        installed = {path: digest for path, digest in installed.items()
                     if path in expected or not path.startswith('node_modules/')}
        if installed != expected:
            raise ValueError('Installed component differs from its recorded archive: ' + name)
    client = root / 'node_modules/@amplifier/unified-client-web/dist'
    web = root / 'web'
    if stage_web and not web.exists():
        shutil.copytree(client, web)
    if files(web) != files(client) or not (web / 'index.html').is_file():
        raise ValueError('Served web differs from the installed client; preserve/remove the old generated web directory and rebuild')
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-web', action='store_true')
    args = parser.parse_args()
    print(f'{verify(Path(__file__).resolve().parent.parent, stage_web=args.stage_web)} installed components and served web match their exact archives')
