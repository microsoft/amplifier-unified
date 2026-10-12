"""Content-first storage accounting. Scans are bounded and never follow links."""
from pathlib import Path
import hashlib
import os
import stat

from .managed_deletion import _safe
from .session_files import amplifier_home, sessions_dir, validate_id

CATEGORIES = (
    ('workspaces', 'Workspace files', 'Project folders and their contents'),
    ('sessions', 'Session data', 'Chats, attachments, saved outputs and shared history'),
    ('runtime', 'App & runtime', 'Installed application, components and runtime environments'),
    ('backups', 'Backups', 'Private backups and recovery copies'),
    ('cache', 'Caches & logs', 'Downloaded release metadata, rebuildable caches and logs'),
    ('other', 'Settings & other data', 'Configuration, credentials and application records'),
)


def catalog(home, workspaces, sessions=(), installation=None):
    home = Path(home).absolute()
    roots = {}

    def add(path, category, label, **extra):
        path = Path(path).absolute()
        # Ownership rules are explicit, not inferred from a display name.
        roots[path] = {'id': hashlib.sha256(str(path).encode()).hexdigest()[:24],
                       'category': category, 'label': label, 'absolute': path, **extra}

    for row in workspaces:
        if row.get('path'):
            add(row['path'], 'workspaces', row.get('name') or Path(row['path']).name,
                workspaceId=row['id'], archived=bool(row.get('archived')))
    add(home, 'other', 'Other application data')
    # Exclude CLI configuration from any workspace that contains the CLI home.
    add(amplifier_home(), None, 'CLI configuration')
    add(amplifier_home() / 'projects', 'sessions', 'Shared CLI and Unified history', historyRoot=True)
    for name, category, label in (
        ('workspaces', 'workspaces', 'Other app-managed workspace files'),
        ('chats', 'sessions', 'Other managed chat files'),
        ('sessions', 'sessions', 'Conversation records'),
        ('artifacts', 'sessions', 'Saved outputs'),
        ('attachments', 'sessions', 'Attachments'),
        ('diagnostics', 'sessions', 'Recorded session events'),
        ('app.sqlite3', 'sessions', 'Conversation database'),
        ('app.sqlite3-wal', 'sessions', 'Recent database changes'),
        ('app.sqlite3-shm', 'sessions', 'Database coordination'),
        ('recall.sqlite3', 'sessions', 'Conversation recall index'),
        ('runtime', 'runtime', 'Worker environments'),
        ('foundation', 'runtime', 'Included components'),
        ('source-store', 'runtime', 'Component sources'),
        ('updates', 'runtime', 'Application updates'),
        ('backups', 'backups', 'Private backups'),
        ('reset-backups', 'backups', 'Reset recovery copies'),
        ('cache', 'cache', 'Application cache'),
        ('logs', 'cache', 'Application logs'),
        ('foundation/cache', 'cache', 'Component cache'),
        ('updates/releases', 'cache', 'Release metadata'),
    ):
        add(home / name, category, label)
    from .storage_backup_cleanup import candidates
    try:
        backups = candidates(home)
    except (OSError, ValueError):
        backups = []
    for index, path in enumerate(backups):
        from datetime import datetime
        stamp = datetime.fromtimestamp((path / 'private-state.tar.gz').stat().st_mtime).strftime('%b %d, %Y %H:%M')
        add(path, 'backups', 'Private backup · ' + stamp,
            backupId=path.name, protected=index == 0)
    if installation:
        add(installation, 'runtime', 'Installed application', protected=True)
    # Named managed chats can be reviewed through the existing deletion contract.
    # The category total also includes shared history, which stays read-only.
    for row in sessions:
        if row.get('location', {}).get('kind') != 'managed':
            continue
        try:
            sid = validate_id(row['id'])
            native = validate_id(row.get('runtimeSessionId') or sid)
            workspace = Path(row['workspace']).absolute()
            if workspace != home / 'chats' / sid / 'files':
                continue
        except (ValueError, KeyError, TypeError):
            continue
        for path in (workspace.parent, workspace, home / 'sessions' / native, sessions_dir(workspace) / native):
            add(path, 'sessions', row.get('title') or 'Untitled chat', sessionId=sid, locationLabel=('Chat files' if path == workspace else 'Chat metadata' if path == workspace.parent else 'Conversation records' if path.is_relative_to(home / 'sessions') else 'Native conversation history'))
    return roots


def measure(path, boundaries, seen, budget):
    """Iterative walk; roots below this root are counted by their own owner."""
    total = files = visited = 0
    incomplete = False
    reasons = set()
    pending = [path]
    try:
        _safe(path)
    except ValueError:
        return {'bytes': None, 'files': 0, 'incomplete': True, 'measurementNote': 'Symbolic link excluded'}
    while pending:
        item = pending.pop()
        if item != path and item in boundaries:
            continue
        if budget[0] <= 0:
            incomplete = True
            reasons.add('Scan limit reached; open a folder for a closer measurement')
            break
        budget[0] -= 1
        try:
            info = item.lstat()
            visited += 1
            if stat.S_ISREG(info.st_mode):
                key = (info.st_dev, info.st_ino)
                if key not in seen:
                    seen.add(key)
                    total += info.st_size
                    files += 1
            elif stat.S_ISDIR(info.st_mode):
                # Bound enumeration as well as traversal (one huge folder must
                # not allocate millions of paths before the scan limit applies).
                with os.scandir(item) as entries:
                    for entry in entries:
                        if len(pending) >= budget[0]:
                            incomplete = True
                            reasons.add('Scan limit reached; open a folder for a closer measurement')
                            break
                        pending.append(Path(entry.path))
            else:
                incomplete = True
                reasons.add('Links or special files excluded')
        except FileNotFoundError:
            if item != path:
                incomplete = True
        except OSError:
            incomplete = True
            reasons.add('Some files could not be read')
    return {'bytes': None if not visited and incomplete else total, 'files': files, 'incomplete': incomplete, **({'measurementNote': '; '.join(sorted(reasons))} if reasons else {})}


def history_selection(root, relative, directory):
    if not root.get('historyRoot') or not directory:
        return {}
    parts = relative.parts
    if len(parts) == 1:
        return {'historySelection': {'project': parts[0]}}
    if len(parts) == 2 and parts[1] == 'sessions':
        return {'historySelection': {'project': parts[0]}}
    if len(parts) == 3 and parts[1] == 'sessions' and not parts[2].startswith('.'):
        return {'historySelection': {'project': parts[0], 'nativeId': parts[2]}}
    return {}


def inspect(home, workspaces, args, sessions=(), installation=None, limit=200000):
    roots = catalog(home, workspaces, sessions, installation)
    boundaries = set(roots)
    category = args.get('category')
    if category and category not in {r[0] for r in CATEGORIES}:
        raise ValueError('Choose a storage category.')
    selected = next((r for r in roots.values() if r['id'] == args.get('rootId')), None)
    if args.get('rootId'):
        if not selected or selected['category'] != category:
            raise ValueError('This storage item is no longer available.')
        relative = Path(args.get('path') or '.')
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Choose a path within this storage category.')
        path = _safe(selected['absolute'] / relative)
        if not path.is_dir():
            raise ValueError('This folder is no longer available.')
        rows = []; seen = set(); truncated = False
        with os.scandir(path) as entries:
            for entry in entries:
                if len(rows) >= 2000:
                    truncated = True
                    break
                child = Path(entry.path)
                owner = roots.get(child)
                if owner and owner['category'] is None:
                    continue
                usage = measure(child, boundaries, seen, [limit])
                rows.append({'id': entry.name, 'label': entry.name,
                             'path': str(child.relative_to(selected['absolute'])),
                             'directory': entry.is_dir(follow_symlinks=False), **usage,
                             **history_selection(selected, child.relative_to(selected['absolute']), entry.is_dir(follow_symlinks=False)),
                             **({'destination': {'category': owner['category'], 'rootId': owner['id']}} if owner else {})})
        return {'rows': sorted(rows, key=lambda r: r['bytes'] or 0, reverse=True),
                'label': selected['label'] if relative == Path('.') else path.name, 'path': str(path),
                **history_selection(selected, relative, True),
                'parent': str(relative.parent) if relative != Path('.') else None,
                'bytes': sum(r['bytes'] or 0 for r in rows),
                'incomplete': truncated or any(r['incomplete'] for r in rows),
                **{k: selected[k] for k in ('workspaceId', 'sessionId', 'backupId', 'protected') if k in selected}}
    measured = []; seen = set()
    for root in roots.values():
        if root['category'] is None or (category and root['category'] != category):
            continue
        path = root['absolute']
        usage = measure(path, boundaries, seen, [limit])
        # Empty known roots do not need a drill-down row. Inaccessible roots do.
        if not usage['bytes'] and not usage['incomplete']:
            continue
        measured.append({k: v for k, v in root.items() if k != 'absolute'} |
                        {'directory': path.is_dir() and not path.is_symlink(), **usage})
    if category:
        rows = [r for r in measured if r['category'] == category]
        # Each chat may own several locations. Present one logical row and let
        # the deletion preview account for all its locations together.
        if args.get('sessionId'):
            rows = [dict({k:v for k,v in r.items() if k != 'sessionId'}, label=r.get('locationLabel', r['label'])) for r in rows if r.get('sessionId') == args['sessionId']]
            return {'rows': sorted(rows, key=lambda r: r['bytes'] or 0, reverse=True),
                    'label': 'Chat storage locations', 'sessionId': args['sessionId'],
                    'bytes': sum(r['bytes'] or 0 for r in rows), 'incomplete': any(r['incomplete'] for r in rows)}
        combined = {}
        for row in rows:
            key = row.get('sessionId') or row['id']
            if key not in combined:
                combined[key] = row.copy()
                if row.get('sessionId'):
                    combined[key]['directory'] = True
                    combined[key]['destination'] = {'category': 'sessions', 'sessionId': row['sessionId']}
            else:
                prior = combined[key]
                prior['bytes'] = (prior['bytes'] or 0) + (row['bytes'] or 0)
                prior['incomplete'] |= row['incomplete']
        rows = sorted(combined.values(), key=lambda r: r['bytes'] or 0, reverse=True)
        return {'rows': rows, 'label': next(c[1] for c in CATEGORIES if c[0] == category),
                'bytes': sum(r['bytes'] or 0 for r in rows), 'incomplete': any(r['incomplete'] for r in rows)}
    rows = []
    for identity, label, description in CATEGORIES:
        members = [r for r in measured if r['category'] == identity]
        rows.append({'id': identity, 'label': label, 'description': description,
                     'bytes': sum(r['bytes'] or 0 for r in members),
                     'incomplete': any(r['incomplete'] for r in members), 'items': len(members)})
    return {'rows': sorted(rows, key=lambda r: r['bytes'], reverse=True),
            'bytes': sum(r['bytes'] for r in rows), 'incomplete': any(r['incomplete'] for r in rows),
            'description': 'File content on this host, counted once across categories. Shared CLI history is included; CLI configuration is excluded. Linked and unreadable files are not counted.'}
