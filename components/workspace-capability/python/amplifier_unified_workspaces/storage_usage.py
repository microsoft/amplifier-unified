"""Bounded, on-demand storage accounting. No file bodies or symlink traversal.

Roots come from the launcher/native owner and authorized catalog, never from a
browser path. Directory descriptors anchor every recursive read against rename
and symlink substitution. Counts are a live observation, not a backup inventory.
"""
import os
from pathlib import Path
import stat

LIMIT = 200_000
ROW_LIMIT = 2000
ROOT_LIMIT = 1000


def open_directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute storage root is required')
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _scan(parent, name, seen, budget, depth=0):
    usage = {'bytes': 0, 'files': 0, 'incomplete': False}
    if budget[0] <= 0 or depth > 128:
        return {**usage, 'incomplete': True}
    budget[0] -= 1
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            return {**usage, 'linked': True, 'incomplete': True}
        if stat.S_ISREG(info.st_mode):
            key = (info.st_dev, info.st_ino)
            if key not in seen:
                seen.add(key)
                usage.update(bytes=info.st_size, files=1)
        elif stat.S_ISDIR(info.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                    return {**usage, 'incomplete': True}
                with os.scandir(fd) as entries:
                    for entry in entries:
                        if budget[0] <= 0:
                            usage['incomplete'] = True
                            break
                        child = _scan(fd, entry.name, seen, budget, depth+1)
                        usage['bytes'] += child['bytes']
                        usage['files'] += child['files']
                        usage['incomplete'] |= child['incomplete']
                after = os.fstat(fd)
                usage['incomplete'] |= (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)
            finally:
                os.close(fd)
        else:
            usage['incomplete'] = True
    except OSError:
        usage['incomplete'] = True
    return usage


def _usage(path, seen, budget):
    try:
        path = Path(path)
        fd = open_directory(path.parent)
        try:
            return _scan(fd, path.name, seen, budget)
        finally:
            os.close(fd)
    except OSError:
        return {'bytes': 0, 'files': 0, 'incomplete': True}


def inspect(roots, args, *, omissions=()):
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ValueError('Storage inspection is unavailable on this host')
    if len(roots) > ROOT_LIMIT:
        raise ValueError('Too many storage roots; choose a specific workspace')
    if not args.get('scope'):
        if args:
            raise ValueError('Choose a storage category before drilling into it')
        rows = []
        seen = set()
        budget = [LIMIT]
        # Parents first: included children never inflate overview totals.
        for root in sorted(roots, key=lambda r: len(Path(r['path']).parts)):
            path = Path(root['path'])
            included = next((r['label'] for r in rows if path.is_relative_to(Path(r['path']))), None)
            rows.append({**root, **_usage(path, set() if included else seen, budget),
                         **({'includedIn': included} if included else {})})
        return {'version': 1, 'rows': rows,
                'bytes': sum(r['bytes'] for r in rows if not r.get('includedIn')),
                'incomplete': bool(omissions) or any(r['incomplete'] for r in rows),
                'omissions': list(omissions), 'scanBudget': LIMIT,
                'description': 'File sizes counted once. Links and unreadable entries are excluded. These live measurements are not filesystem free space.'}
    matches = [r for r in roots if r['scope'] == args['scope'] and
               (r.get('workspaceId') == args.get('workspaceId') if args['scope'] == 'workspace'
                else r['id'] == args.get('rootId', args['scope']))]
    if len(matches) != 1:
        raise ValueError('Choose an available storage category or workspace')
    root = matches[0]
    relative = Path(args.get('path') or '.')
    if relative.is_absolute() or '..' in relative.parts or '\x00' in str(relative):
        raise ValueError('Choose a folder within this storage category')
    path = Path(root['path']) / relative
    try:
        fd = open_directory(path)
    except OSError:
        raise ValueError('This folder is unavailable or contains a symbolic link') from None
    seen = set()
    budget = [LIMIT]
    rows = []
    truncated = False
    try:
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                if len(rows) >= ROW_LIMIT or budget[0] <= 0:
                    truncated = True
                    break
                usage = _scan(fd, entry.name, seen, budget)
                try:
                    directory = stat.S_ISDIR(os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode)
                except OSError:
                    directory = False
                    usage['incomplete'] = True
                rows.append({'id': entry.name, 'label': entry.name, 'path': str(relative/entry.name),
                             'directory': directory, **usage})
        after = os.fstat(fd)
        truncated |= (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)
    finally:
        os.close(fd)
    return {'version': 1, 'rows': sorted(rows, key=lambda r: (-r['bytes'], r['label'].casefold())),
            'path': str(path), 'parent': str(relative.parent) if relative != Path('.') else None,
            'bytes': sum(r['bytes'] for r in rows), 'scanBudget': LIMIT,
            'incomplete': truncated or any(r['incomplete'] for r in rows),
            'workspaceId': root.get('workspaceId'), 'rootId': root['id'], 'scope': root['scope']}


async def read(owner, args, client):
    roots = []
    omissions = []
    if owner.storage_roots_enabled:
        value = await owner.catalog('storageRoots', {})
        if not isinstance(value, dict) or not isinstance(value.get('roots'), list) or len(value['roots']) > 16:
            raise ValueError('Storage owner returned invalid roots')
        for row in value['roots']:
            if set(row) != {'id', 'scope', 'label', 'path'} or row['scope'] not in {'app', 'history'} or not Path(row['path']).is_absolute():
                raise ValueError('Storage owner returned an invalid root')
            roots.append(row)
        omissions.extend(value.get('omissions', []))
    else:
        omissions.append('Application and shared history storage are unavailable from this host')
    if args.get('scope') == 'workspace':
        rows = [await owner.selected(args.get('workspaceId'))]
    else:
        await owner.synchronize()
        rows = []
        cursor = None
        while len(rows) <= ROOT_LIMIT:
            page = await owner.catalog('listWorkspaces', {'connectionId': owner.scope(client), 'limit': 50,
                'allowedWorkspaceRoots': owner.roots, 'includeHidden': True, 'includeUnavailable': True,
                **({'archive': 'all'} if owner.workspace_archive else {}), **({'cursor': cursor} if cursor else {})})
            rows.extend(page['items'])
            next_cursor = page.get('nextCursor')
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise ValueError('Workspace storage paging did not advance')
            cursor = next_cursor
        if len(rows) > ROOT_LIMIT-len(roots):
            rows = rows[:ROOT_LIMIT-len(roots)]
            omissions.append('Additional workspace roots were omitted; choose a specific workspace')
    for row in rows:
        owner.authorize(row['path'], existing=False)
        roots.append({'id': row['id'], 'scope': 'workspace', 'workspaceId': row['id'],
                      'path': row['path'], 'label': row.get('name') or Path(row['path']).name,
                      'archived': bool(row.get('archived'))})
    import asyncio
    return await asyncio.to_thread(inspect, roots, args, omissions=omissions)
