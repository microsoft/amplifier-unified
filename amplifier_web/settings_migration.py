"""One-time, additive cutover from Unified's former private configuration.

Existing shared settings win. Imported workspace snapshots are deliberately not
replayed: they were copies of files that are now read directly. Originals and
private backups are retained; this module is never called by a settings read.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shlex

from filelock import FileLock

from .shared_settings import atomic_write, read_yaml, settings_paths, update_settings
from .session_files import amplifier_home


def migrate_settings(home, workspaces):
    home, shared = Path(home), amplifier_home()
    key = hashlib.sha256(str(shared).encode()).hexdigest()[:16]
    directory = home / 'config'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = directory / ('shared-settings-' + key + '.json')
    backup = home / 'backups' / ('shared-settings-' + key)

    def preserve(path):
        if path.is_file():
            name = hashlib.sha256(str(path).encode()).hexdigest()[:16] + '-' + path.name
            target = backup / name
            if not target.exists():
                atomic_write(target, path.read_text(), private=True)

    def promote(source, target):
        if not source.is_file():
            return
        legacy = read_yaml(source)
        if target.exists():
            existing = read_yaml(target).get('bundle', {}).get('added', {})
            if not any(name not in existing for name in legacy.get('bundle', {}).get('added', {})):
                return
        preserve(source)
        def migrate(current):
            preserve(target)
            if not target.exists():
                current.update(copy.deepcopy({k: v for k, v in legacy.items() if k not in {'_migration', '_workspace', 'provider_order'}}))
                order = {identity: i + 1 for i, identity in enumerate(legacy.get('provider_order', []))}
                disabled = set(current.get('configurator', {}).get('disabled', {}).get('providers', []))
                for row in current.get('config', {}).get('providers', []):
                    identity = row.get('id') or row.get('instance_id') or row.get('module', '').removeprefix('provider-')
                    if identity in order:
                        row.setdefault('config', {})['priority'] = order[identity]
                    if row.pop('enabled', True) is False or current.get('overrides', {}).get(identity, {}).get('enabled') is False:
                        disabled.add(identity)
                    current.get('overrides', {}).get(identity, {}).pop('enabled', None)
                if disabled:
                    current.setdefault('configurator', {}).setdefault('disabled', {})['providers'] = sorted(disabled)
            else:
                # Retain access to Unified-only named bundles without changing
                # shared model choices, behavior composition or active bundle.
                for name, uri in legacy.get('bundle', {}).get('added', {}).items():
                    current.setdefault('bundle', {}).setdefault('added', {}).setdefault(name, uri)
        update_settings(target, migrate)

    with FileLock(str(directory / '.shared-settings-migration.lock'), timeout=10):
        state = json.loads(marker.read_text()) if marker.exists() else {'global': False, 'workspaces': []}
        if not state['global']:
            promote(home / 'config/settings.yaml', shared / 'settings.yaml')
            source = home / 'config/keys.env'
            if source.is_file():
                target = shared / 'keys.env'
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with FileLock(str(target) + '.lock', timeout=10):
                    preserve(source); preserve(target)
                    lines = target.read_text().splitlines() if target.exists() else []
                    def name(line):
                        return line.strip().removeprefix('export ').split('=', 1)[0].strip()
                    names = {name(line) for line in lines if '=' in line and not line.lstrip().startswith('#')}
                    for line in source.read_text().splitlines():
                        if '=' in line and not line.lstrip().startswith('#') and name(line) not in names:
                            shlex.split(line, comments=True)  # Reject malformed quoting before writing.
                            lines.append(line); names.add(name(line))
                    atomic_write(target, '\n'.join(lines) + '\n', private=True)
            for directory_source in (home / 'config/routing', home / 'foundation/routing'):
                for source in sorted(directory_source.glob('*.yaml')):
                    target = shared / 'routing' / source.name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with FileLock(str(target) + '.lock', timeout=10):
                        if not target.exists():
                            preserve(source)
                            atomic_write(target, source.read_text())
            state['global'] = True
        for workspace in sorted({str(Path(w).expanduser().resolve()) for w in workspaces}):
            if workspace in state['workspaces'] or not Path(workspace).is_dir():
                continue
            paths = settings_paths(workspace)
            for scope in ('project', 'local'):
                if scope not in paths:
                    continue
                target = paths[scope]
                promote(Path(workspace) / '.amplifier-unified' / target.name, target)
            state['workspaces'].append(workspace)
        atomic_write(marker, json.dumps(state, indent=2), private=True)
