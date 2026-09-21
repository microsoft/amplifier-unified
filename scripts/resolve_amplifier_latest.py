"""Resolve a qualification checkout explicitly; retain old locks as evidence.

This does not install, launch, run acceptance, or change a host's configuration.
Use --mode latest in a new development/qualification checkout; use --mode replay
with the recorded lock when reproducing prior evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from urllib.parse import parse_qs, urlsplit, urlunsplit


def amplifier_names(manifest, lock):
    names = {row['name'] for row in lock.get('package', []) if row['name'].startswith('amplifier-')}
    project = manifest.get('project', {})
    values = list(project.get('dependencies', []))
    for group in project.get('optional-dependencies', {}).values():
        values += group
    for group in manifest.get('dependency-groups', {}).values():
        values += [value for value in group if isinstance(value, str)]
    values += list(manifest.get('tool', {}).get('uv', {}).get('sources', {}))
    for value in values:
        name = re.match(r'[A-Za-z0-9_.-]+', value)
        if name:
            normalized = re.sub(r'[-_.]+', '-', name[0]).lower()
            if normalized.startswith('amplifier-'):
                names.add(normalized)
    return names


def resolution(lock):
    records = []
    for row in lock.get('package', []):
        if not row['name'].startswith('amplifier-'):
            continue
        source = row.get('source', {})
        record = {'name': row['name'], 'version': row['version'], 'kind': next(iter(source), 'unknown')}
        if source.get('git'):
            parsed = urlsplit(source['git'])
            query = parse_qs(parsed.query)
            record.update(repository=urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, '', '')),
                          revision=parsed.fragment, requested=query.get('branch') or query.get('rev') or query.get('tag') or [],
                          subdirectory=query.get('subdirectory', []))
        elif source.get('registry'):
            record['registry'] = source['registry']
        else:
            # Local overrides remain in the private lock, not public evidence.
            record['localOverride'] = True
        records.append(record)
    return sorted(records, key=lambda row: row['name'])


def qualify(project, evidence, mode, *, run=subprocess.run):
    project, evidence = Path(project).resolve(), Path(evidence).resolve()
    manifest_path = project / 'pyproject.toml'
    manifest = tomllib.loads(manifest_path.read_text())
    lock_path = project / 'uv.lock'
    original = lock_path.read_bytes() if lock_path.exists() else None
    if mode == 'replay' and original is None:
        raise ValueError('Replay requires an existing recorded uv.lock.')
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv before resolving qualification dependencies.')
    evidence.mkdir(parents=True, mode=0o700, exist_ok=False)
    if original is not None:
        (evidence / 'before.lock').write_bytes(original)
    report = {'mode': mode, 'startedAt': datetime.now(timezone.utc).isoformat(),
              'manifestSha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              'beforeSha256': hashlib.sha256(original).hexdigest() if original is not None else None,
              'before': resolution(tomllib.loads(original.decode())) if original else [], 'passes': []}
    try:
        refreshed = set()
        for attempt in range(8):
            locked = tomllib.loads(lock_path.read_text()) if lock_path.exists() else {}
            names = amplifier_names(manifest, locked)
            if mode == 'latest' and attempt and names <= refreshed:
                break
            flags = ['--locked'] if mode == 'replay' else [arg for name in sorted(names) for arg in ('--upgrade-package', name, '--refresh-package', name)]
            run([uv, 'lock', '--project', str(project), *flags], check=True, capture_output=True)
            report['passes'].append({'refreshedPackages': sorted(names) if mode == 'latest' else []})
            refreshed |= names
            if mode == 'replay':
                break
        else:
            raise RuntimeError('Amplifier dependency graph did not stabilize within eight resolution passes.')
        final = lock_path.read_bytes()
        if mode == 'replay' and final != original:
            raise RuntimeError('Replay changed the recorded lock.')
        (evidence / 'resolved.lock').write_bytes(final)
        report.update(ok=True, resolvedSha256=hashlib.sha256(final).hexdigest(),
                      resolved=resolution(tomllib.loads(final.decode())))
    except BaseException as error:
        if lock_path.exists():
            (evidence / 'failed.lock').write_bytes(lock_path.read_bytes())
        if original is None:
            lock_path.unlink(missing_ok=True)
        else:
            lock_path.write_bytes(original)
        report.update(ok=False, errorType=type(error).__name__, originalRestored=True)
        raise
    finally:
        (evidence / 'resolution.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True, help='New private evidence directory; never overwritten')
    parser.add_argument('--mode', choices=('latest', 'replay'), required=True)
    args = parser.parse_args()
    report = qualify(args.project, args.evidence, args.mode)
    print(json.dumps({'ok': report['ok'], 'mode': args.mode, 'resolvedSha256': report['resolvedSha256']}))


if __name__ == '__main__':
    main()
