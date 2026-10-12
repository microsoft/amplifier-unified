"""Bounded, on-demand storage accounting. No file bodies or symlink traversal.

Roots come from the launcher/native owner and authorized catalog, never from a
browser path. Directory descriptors anchor every recursive read against rename
and symlink substitution. Counts are a live observation, not a backup inventory.
"""
import os
from pathlib import Path
import stat
import time
import contextvars
from contextlib import contextmanager

_observation = contextvars.ContextVar("storage_observation", default=None)

def _expired():
    value = _observation.get()
    return bool(value and (time.monotonic() >= value[0] or value[1] is not None and value[1].is_set()))

@contextmanager
def _slice(seconds):
    value = _observation.get()
    token = _observation.set((min(value[0], time.monotonic()+seconds), *value[1:])) if value else None
    try:
        yield
    finally:
        if token is not None: _observation.reset(token)


def _publish(value):
    observation = _observation.get()
    if observation and observation[2]: observation[2](value)

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


def _scan(parent, name, seen, budget, depth=0, *, path=None, boundaries=frozenset()):
    from .storage_walk import Walk, fingerprint
    observation = _observation.get()
    cache, walks = (observation[3], observation[5]) if observation else ({}, {})
    key = (str(path), tuple(sorted(boundaries)))
    walk = walks.get(key)
    if walk and walk.done:
        try:
            changed = fingerprint(os.stat(name, dir_fd=parent, follow_symlinks=False)) != walk.fingerprint
        except OSError:
            changed = False
        if changed:
            del walks[key]
            walk = None
    if walk is None:
        # Only a bounded number of open frontiers survive a slice. Later roots
        # remain pending until an earlier walk finishes, rather than exhausting
        # host descriptors on a large collection of registered workspaces.
        active = [item for item in walks.values() if not item.done]
        if len(active) >= 8:
            # Navigation may inherit unfinished walks from an earlier view.
            # Advance one frontier so its slot can eventually be reused; merely
            # waiting for an unvisited view would leave this folder stuck.
            active[0].advance(budget, _expired)
            if not active[0].done:
                return {'bytes': 0, 'files': 0, 'incomplete': True, 'pending': True}
        walk = walks[key] = Walk(parent, name, path, boundaries, seen, cache)
    return walk.advance(budget, _expired)


def _usage(path, seen, budget, *, boundaries=frozenset(), optional=False):
    try:
        path = Path(path)
        fd = open_directory(path.parent)
        try:
            if optional:
                try: os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError: return {'bytes': 0, 'files': 0, 'incomplete': False}
            return _scan(fd, path.name, seen, budget, path=path, boundaries=boundaries)
        finally:
            os.close(fd)
    except FileNotFoundError:
        return {'bytes': 0, 'files': 0, 'incomplete': not optional}
    except OSError:
        return {'bytes': 0, 'files': 0, 'incomplete': True}


def inspect(roots, args, *, omissions=(), stopped=None, progress=None, seconds=2, cache=None, seen=None, walks=None, listings=None, measurements=None):
    owned = walks is None
    walks = {} if walks is None else walks
    listings = {} if listings is None else listings
    token = _observation.set((time.monotonic()+seconds, stopped, progress, cache if cache is not None else {}, seen if seen is not None else {}, walks, listings, measurements if measurements is not None else {}))
    try:
        return _inspect(roots, args, omissions=omissions)
    finally:
        _observation.reset(token)
        if owned:
            for walk in walks.values(): walk.close()
            for listing in listings.values():
                if listing.get('entries') is not None: listing['entries'].close()


def _inspect(roots, args, *, omissions=()):
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ValueError('Storage inspection is unavailable on this host')
    if len(roots) > ROOT_LIMIT:
        raise ValueError('Too many storage roots; choose a specific workspace')
    content = any('category' in root for root in roots)
    if content and (not args.get('scope') or args['scope'] == 'category'):
        return content_inspect(roots, args, omissions)
    if not args.get('scope'):
        if args:
            raise ValueError('Choose a storage category before drilling into it')
        rows = []
        seen = _observation.get()[4] if _observation.get() else {}
        budget = [LIMIT]
        # Parents first: included children never inflate overview totals.
        for root in sorted(roots, key=lambda r: len(Path(r['path']).parts)):
            path = Path(root['path'])
            included = next((r['label'] for r in rows if path.is_relative_to(Path(r['path']))), None)
            rows.append({**root, **_usage(path, {} if included else seen, budget),
                         **({'includedIn': included} if included else {})})
        return {'version': 1, 'rows': rows,
                'bytes': sum(r['bytes'] for r in rows if not r.get('includedIn')),
                'incomplete': bool(omissions) or any(r['incomplete'] for r in rows),
                'measuring': any(r.get('pending') for r in rows),
                'omissions': list(omissions), 'scanBudget': LIMIT,
                'description': 'File sizes counted once. Links and unreadable entries are excluded. These live measurements are not filesystem free space.'}
    matches = [r for r in roots if r['scope'] == args['scope'] and
               (r.get('workspaceId') == args.get('workspaceId') if args['scope'] == 'workspace'
                else r['id'] == args.get('rootId', args['scope']))]
    if len(matches) != 1:
        raise ValueError('Choose an available storage category or workspace')
    root = matches[0]
    if content and root.get('category') is None:
        raise ValueError('This storage boundary is excluded from application accounting')
    if content and args.get('categoryId') is not None and args['categoryId'] != root.get('category'):
        raise ValueError('Storage category differs from the selected root')
    relative = Path(args.get('path') or '.')
    if relative.is_absolute() or '..' in relative.parts or '\x00' in str(relative):
        raise ValueError('Choose a folder within this storage category')
    path = Path(root['path']) / relative
    if content and any(r['path'] != root['path'] and path.is_relative_to(Path(r['path'])) and Path(r['path']).is_relative_to(Path(root['path'])) for r in roots):
        raise ValueError('Choose this folder through its own storage category')
    try:
        fd = open_directory(path)
    except OSError:
        raise ValueError('This folder is unavailable or contains a symbolic link') from None
    seen = _observation.get()[4]
    listings = _observation.get()[6]
    identity = (str(path), root['id'])
    listing = listings.get(identity)
    try:
        before = os.fstat(fd)
        stamp = (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
        if listing and listing['stamp'] != stamp:
            if listing.get('entries') is not None: listing['entries'].close()
            listing = None
        if listing is None:
            if len(listings) >= 32:
                old = listings.pop(next(iter(listings)))
                if old.get('entries') is not None: old['entries'].close()
            listing = listings[identity] = {'stamp': stamp, 'entries': os.scandir(fd), 'rows': [], 'truncated': False}
        rows = listing['rows']
        budget = [LIMIT]
        boundaries = {r['path'] for r in roots if r['path'] != root['path']} if content else set()
        def snapshot():
            pending = listing['entries'] is not None or any(r.get('pending') for r in rows)
            return {'version': 1, 'rows': sorted(rows, key=lambda r: (-r['bytes'], r['label'].casefold())),
                    'label': root['label'] if relative == Path('.') else path.name,
                    'path': str(path), 'parent': str(relative.parent) if relative != Path('.') else None,
                    'bytes': sum(r['bytes'] for r in rows), 'scanBudget': LIMIT, 'measuring': pending,
                    'incomplete': pending or listing['truncated'] or any(r['incomplete'] for r in rows),
                    'workspaceId': root.get('workspaceId'), 'rootId': root['id'], 'scope': root['scope'],
                    **({'categoryId': root['category'], 'view': 'folder'} if content else {})}
        def measure(row):
            with _slice(.15):
                usage = _scan(fd, row['id'], seen, budget, path=path/row['id'], boundaries=boundaries)
            # Replace prior transient pending/error fields with the new observation.
            for key in ('pending', 'measurementNote', 'linked', 'separateCategory'): row.pop(key, None)
            row.update(usage)
        _publish(snapshot())
        published = time.monotonic()
        resuming = listing['entries'] is not None or any(r.get('pending') for r in rows)
        for row in rows:
            if resuming and not row.get('pending'): continue
            if _expired(): break
            measure(row)
        while listing['entries'] is not None and not _expired():
            if len(rows) >= ROW_LIMIT:
                listing['truncated'] = True
                listing['entries'].close(); listing['entries'] = None
                break
            try: entry = next(listing['entries'])
            except StopIteration:
                listing['entries'].close(); listing['entries'] = None
                break
            except OSError:
                listing['truncated'] = True
                listing['entries'].close(); listing['entries'] = None
                break
            try: directory = stat.S_ISDIR(os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode)
            except OSError: directory = False
            row = {'id': entry.name, 'label': entry.name, 'path': str(relative/entry.name), 'directory': directory}
            measure(row)
            if root['scope']=='history' and root['id']=='history' and directory and not row.get('linked') and not entry.name.startswith('.'):
                if relative == Path('.'): row['historySelection'] = {'project': entry.name}
                elif len(relative.parts)==2 and relative.parts[1]=='sessions': row['historySelection'] = {'project': relative.parts[0], 'nativeSessionId': entry.name}
            rows.append(row)
            if time.monotonic() - published >= .05:
                _publish(snapshot()); published = time.monotonic()
        after = os.fstat(fd)
        listing['truncated'] |= stamp != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
        return snapshot()
    finally:
        os.close(fd)


CATEGORIES = {
    'workspaces': ('Workspace files', 'Files in your registered and managed workspaces.'),
    'sessions': ('Session data', 'Chats, attachments, outputs, and shared conversation history.'),
    'runtime': ('App & runtime', 'The installed application, components, and runtime environments.'),
    'backups': ('Backups', 'Private backups and retained recovery copies.'),
    'cache': ('Caches & logs', 'Cached data and diagnostic logs.'),
    'other': ('Settings & other data', 'Application settings and remaining saved state.'),
}


def content_inspect(roots, args, omissions):
    category=args.get('categoryId')
    if args and (args.get('scope')!='category' or category not in CATEGORIES or set(args)!={'scope','categoryId'}):
        raise ValueError('Choose a returned storage category')
    rows=[]; seen=_observation.get()[4] if _observation.get() else {}; budget=[LIMIT]; boundaries={r['path'] for r in roots}
    selected_roots=[root for root in roots if root.get('category') is not None and (not category or root['category']==category)]
    measurements = _observation.get()[7]
    for root in selected_roots:
        prior = measurements.get(root['id'])
        rows.append({**root, **(prior if prior is not None else {'bytes': 0, 'files': 0, 'pending': True, 'incomplete': True}), 'directory': True})
    def snapshot():
        pending = any(r.get('pending') for r in rows)
        if category:
            result_rows = sorted(rows, key=lambda r: (-r['bytes'], r['label'].casefold()))
        else:
            result_rows = []
            for key,(label,description) in CATEGORIES.items():
                members = [r for r in rows if r['category']==key]
                result_rows.append({'id': key, 'scope': 'category', 'categoryId': key, 'label': label, 'description': description,
                                    'directory': True, 'bytes': sum(r['bytes'] for r in members), 'files': sum(r['files'] for r in members),
                                    'pending': any(r.get('pending') for r in members), 'incomplete': any(r['incomplete'] for r in members)})
        return {'version': 1, 'view': 'category' if category else 'categories',
                **({'categoryId': category, 'scope': 'category', 'label': CATEGORIES[category][0]} if category else {}),
                'rows': result_rows, 'bytes': sum(r['bytes'] for r in result_rows), 'measuring': pending,
                'incomplete': bool(omissions) or any(r['incomplete'] for r in result_rows),
                'omissions': list(omissions), 'scanBudget': LIMIT}
    _publish(snapshot())
    for index,root in enumerate(selected_roots):
        prior = measurements.get(root['id'])
        if prior is not None and not prior.get('pending'): continue
        if _expired(): break
        path=Path(root['path'])
        allowance=max(1,budget[0]//(len(selected_roots)-index)) if budget[0] else 0
        share=[allowance]
        with _slice(.15):
            usage=_usage(path,seen,share,boundaries=boundaries-{str(path)},optional=root.get('optional',False))
        budget[0]-=allowance-share[0]
        measurements[root['id']] = dict(usage)
        try: directory=stat.S_ISDIR(path.lstat().st_mode)
        except OSError: directory=False
        rows[index] = {**root, **usage, 'directory': directory}
        _publish(snapshot())
    return snapshot()



async def read(owner, args, client):
    from .storage_scans import StorageScans, CONTROLS
    if any(k in args and type(args[k]) is not bool for k in ('progressive','cancel')):
        raise ValueError('Storage measurement flags must be booleans')
    for key in ('scanId','fromScanId'):
        if key in args and (not isinstance(args[key],str) or not 1 <= len(args[key]) <= 100):
            raise ValueError('Invalid storage measurement identity')
    scans=getattr(owner,'storage_scans',None)
    if args.get('scanId'):
        if scans is None:raise ValueError('Storage measurement expired. Refresh storage to measure again.')
        return await scans.read(args,owner.scope(client))
    if args.get('cancel'):raise ValueError('Choose an existing storage measurement to cancel.')
    roots = []
    content = False
    omissions = []
    if owner.storage_roots_enabled:
        value = await owner.catalog('storageRoots', {})
        if not isinstance(value, dict) or not isinstance(value.get('roots'), list) or len(value['roots']) > 16:
            raise ValueError('Storage owner returned invalid roots')
        for row in value['roots']:
            if set(row) != {'id', 'scope', 'label', 'path'} or row['scope'] not in {'app', 'history'} or not Path(row['path']).is_absolute():
                raise ValueError('Storage owner returned an invalid root')
            roots.append(row)
        if value.get('contentVersion') == 1:
            declared=value.get('contentRoots')
            if not isinstance(declared,list) or len(declared)>128:
                raise ValueError('Bounded owner-declared content roots required')
            for row in declared:
                if not isinstance(row,dict) or set(row)-{'id','scope','label','path','category','optional'} or not {'id','scope','label','path','category'}<=set(row) or row['scope'] not in {'app','history'} or row['category'] not in {*CATEGORIES,None} or not isinstance(row['path'],str) or not Path(row['path']).is_absolute() or '..' in Path(row['path']).parts or type(row.get('optional',False)) is not bool:
                    raise ValueError('Invalid owner-declared content boundary')
            roots=declared
            content=True
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
                      'archived': bool(row.get('archived')), **({'category':'workspaces'} if content else {})})
    if content:
        # A registered workspace label replaces the generic container at the
        # same exact path; nested roots remain disjoint boundaries.
        roots=list({row['path']:row for row in roots}.values())
        if len({r['id'] for r in roots})!=len(roots):
            raise ValueError('Storage root identities must be unique')
    import asyncio
    if args.get('progressive'):
        if owner.intake.fence:raise ValueError('Storage measurements are paused while maintenance finishes.')
        if scans is None:scans=owner.storage_scans=StorageScans(owner)
        return await scans.read(args,owner.scope(client),roots,omissions)
    return await asyncio.to_thread(inspect, roots, {k:v for k,v in args.items() if k not in CONTROLS}, omissions=omissions)
