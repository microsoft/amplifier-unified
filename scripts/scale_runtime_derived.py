#!/usr/bin/env python3
"""Add only exact copied app presentation-cache COW mounts to inactive sibling."""
import json
import os
from pathlib import Path
import pwd
import sqlite3
import subprocess

from scale_runtime_prepare import BASE, READING, UNIT, NATIVE


def main():
    if subprocess.check_output(['systemctl', 'show', UNIT, '-p', 'MainPID', '--value'], text=True).strip() != '0':
        raise RuntimeError('Stop owned sibling before changing namespace mounts')
    from amplifier_web.session_files import project_slug, validate_id
    account = pwd.getpwnam('unified-dev')
    marker = json.loads((BASE / 'prepared.json').read_text())
    if marker.get('derivedOverlays'):
        raise RuntimeError('Derived cache mounts already prepared')
    native = READING / 'seed/native'
    rows = []
    with sqlite3.connect(f'file:{BASE}/writable/app/app.sqlite3?mode=ro', uri=True) as db:
        for (raw,) in db.execute("SELECT value FROM state_records WHERE kind='session'"):
            row = json.loads(raw)
            if isinstance(row, dict) and row.get('$view'):
                rows.append(row)
    planned = {}
    for row in rows:
        identity = row.get('runtimeSessionId') or row['id']
        validate_id(identity)
        relative = Path('projects') / project_slug(row['workspace']) / 'sessions' / identity / 'unified'
        lower = native / relative
        if lower.resolve() != lower:
            raise RuntimeError('Presentation-cache lower contains symlinks')
        if any(part.is_symlink() for part in (lower, *lower.parents)):
            raise RuntimeError('Presentation-cache symlink boundary')
        planned[str(relative)] = lower
    overlays = []
    scaffolds = {}
    def owned_folder(path):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(path, account.pw_uid, account.pw_gid)
        path.chmod(0o700)
        return path
    # Some SQLite-authoritative views have no legacy view.json directory yet.
    # Build only those empty derived-cache mountpoints in a private project
    # upper, then bind the whole merged project READ-ONLY into the app. This
    # does not add directories or files to the captured native lower.
    for relative, lower in planned.items():
        if lower.is_dir(): continue
        project = Path(relative).parts[1]
        if project not in scaffolds:
            area = owned_folder(BASE / 'writable/project-scaffolds' / str(len(scaffolds)))
            original = native / 'projects' / project
            if not original.exists(): original = owned_folder(area / 'empty-lower')
            for name in ('upper', 'work', 'merged'): owned_folder(area / name)
            subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o',
                f'lowerdir={original},upperdir={area}/upper,workdir={area}/work', str(area / 'merged')], check=True)
            destination = NATIVE + '/projects/' + project
            target = BASE / 'rootfs' / destination.lstrip('/')
            target.mkdir(parents=True, exist_ok=True, mode=0o755); target.chmod(0o755)
            prior = next((row for row in marker['mounts'] if row['target'] == destination), None)
            replacement = {'source': str(area / 'merged'), 'target': destination, 'readOnly': True}
            if prior: marker['mounts'].remove(prior)
            marker['mounts'].append(replacement)
            scaffolds[project] = {'lowerReadOnly': str(original), 'upper': str(area / 'upper'),
                'work': str(area / 'work'), 'merged': str(area / 'merged'), 'target': destination,
                'purpose': 'Empty app presentation-cache mountpoints only; namespace project remains read-only'}
        merged = Path(scaffolds[project]['merged'])
        owned_folder(merged / Path(*Path(relative).parts[2:]))
    # Validate every source before creating any new mount.
    for index, (relative, lower) in enumerate(sorted(planned.items())):
        owned = owned_folder(BASE / 'writable/derived' / str(index))
        if not lower.exists(): lower = owned_folder(owned / 'empty-lower')
        for name in ('upper', 'work', 'merged'):
            path = owned / name
            path.mkdir(parents=True, mode=0o700)
            os.chown(path, account.pw_uid, account.pw_gid)
        owned.chmod(0o700); os.chown(owned, account.pw_uid, account.pw_gid)
        subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o',
            f'lowerdir={lower},upperdir={owned}/upper,workdir={owned}/work', str(owned / 'merged')], check=True)
        destination = NATIVE + '/' + relative
        overlays.append({'lowerReadOnly': str(lower), 'upper': str(owned / 'upper'),
                         'work': str(owned / 'work'), 'merged': str(owned / 'merged'), 'target': destination})
        marker['mounts'].append({'source': str(owned / 'merged'), 'target': destination, 'readOnly': False})
    marker['derivedOverlays'] = overlays
    marker['projectScaffolds'] = list(scaffolds.values())
    for path in (BASE / 'prepared.json', BASE / 'rootfs/replica-owner.json'):
        path.write_text(json.dumps(marker, indent=2) + '\n')
    (BASE / 'rootfs/replica-owner.json').chmod(0o644)
    unit = Path('/etc/systemd/system') / UNIT
    text = '\n'.join(line for line in unit.read_text().splitlines()
                     if not line.startswith(('BindPaths=', 'BindReadOnlyPaths='))) + '\n'
    def quote(value): return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
    text += ''.join(('BindReadOnlyPaths=' if row['readOnly'] else 'BindPaths=')
                    + quote(row['source']) + ':' + quote(row['target']) + '\n' for row in marker['mounts'])
    unit.write_text(text)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print(json.dumps({'derivedPresentationOverlays': len(overlays), 'started': False,
                      'canonicalTranscriptsAndEventsRemainReadOnly': True}))


if __name__ == '__main__':
    main()
