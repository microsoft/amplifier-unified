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
    usage = {'bytes': 0, 'files': 0, 'incomplete': False}
    reasons = set()
    identity = None
    cache = {}
    if path is not None and str(path) in boundaries:
        return {**usage, 'separateCategory': True}
    if budget[0] <= 0 or depth > 128 or _expired():
        return {**usage, 'incomplete': True, 'measurementNote': 'Scan limit or time budget reached; open a folder for a closer measurement'}
    budget[0] -= 1
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            return {**usage, 'linked': True, 'incomplete': True, 'measurementNote': 'Symbolic link excluded'}
        observation = _observation.get()
        cache = observation[3] if observation else {}
        identity = str(path) if path is not None else None
        fingerprint = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
        saved = cache.get(identity) if identity is not None else None
        if saved and saved[0] == fingerprint:
            return dict(saved[1])
        if stat.S_ISREG(info.st_mode):
            key = (info.st_dev, info.st_ino)
            if key not in seen and len(seen) >= LIMIT:
                usage['incomplete'] = True
                reasons.add('Scan limit reached; refresh storage for a new measurement')
            elif key not in seen or seen[key] == identity:
                seen[key] = identity
                usage.update(bytes=info.st_size, files=1)
        elif stat.S_ISDIR(info.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                    return {**usage, 'incomplete': True, 'measurementNote': 'Folder changed during measurement'}
                with os.scandir(fd) as entries:
                    for entry in entries:
                        if budget[0] <= 0 or _expired():
                            usage['incomplete'] = True
                            reasons.add('Scan limit or time budget reached; open a folder for a closer measurement')
                            break
                        child = _scan(fd, entry.name, seen, budget, depth+1, path=path/entry.name if path is not None else None, boundaries=boundaries)
                        usage['bytes'] += child['bytes']
                        usage['files'] += child['files']
                        usage['incomplete'] |= child['incomplete']
                        if child.get('measurementNote'): reasons.update(child['measurementNote'].split(' · '))
                after = os.fstat(fd)
                if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                    usage['incomplete'] = True
                    reasons.add('Folder changed during measurement')
            finally:
                os.close(fd)
        else:
            usage['incomplete'] = True
            reasons.add('Special files excluded')
    except OSError:
        usage['incomplete'] = True
        reasons.add('Some files could not be read')
    if reasons: usage['measurementNote'] = ' · '.join(sorted(reasons))
    # Reuse only complete observations from this client's short-lived scan lineage.
    # These counts never authorize deletion or bypass opening the selected folder.
    if not usage['incomplete'] and identity is not None and len(cache) < 20_000:
        cache[identity] = (fingerprint, dict(usage))
    return usage


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


def inspect(roots, args, *, omissions=(), stopped=None, progress=None, seconds=2, cache=None, seen=None):
    token = _observation.set((time.monotonic()+seconds, stopped, progress, cache if cache is not None else {}, seen if seen is not None else {}))
    try:
        return _inspect(roots, args, omissions=omissions)
    finally:
        _observation.reset(token)


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
    seen = _observation.get()[4] if _observation.get() else {}
    budget = [LIMIT]
    rows = []
    truncated = False
    def snapshot(pending=False):
        return {'version': 1, 'rows': sorted(rows, key=lambda r: (-r['bytes'], r['label'].casefold())),
                'label': root['label'] if relative == Path('.') else path.name,
                'path': str(path), 'parent': str(relative.parent) if relative != Path('.') else None,
                'bytes': sum(r['bytes'] for r in rows), 'scanBudget': LIMIT,
                'incomplete': pending or truncated or any(r['incomplete'] for r in rows),
                'workspaceId': root.get('workspaceId'), 'rootId': root['id'], 'scope': root['scope'],
                **({'categoryId': root['category'], 'view': 'folder'} if content else {})}
    _publish(snapshot(True))
    published = time.monotonic()
    try:
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                if len(rows) >= ROW_LIMIT or _expired():
                    truncated = True
                    break
                boundaries={r['path'] for r in roots if r['path'] != root['path']} if content else set()
                with _slice(.15):
                    usage = _scan(fd, entry.name, seen, budget, path=path/entry.name, boundaries=boundaries)
                try:
                    directory = stat.S_ISDIR(os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode)
                except OSError:
                    directory = False
                    usage['incomplete'] = True
                rows.append({'id': entry.name, 'label': entry.name, 'path': str(relative/entry.name),
                             'directory': directory, **usage,
                             **({'historySelection': {'project': entry.name}} if root['scope']=='history' and root['id']=='history' and relative==Path('.') and directory and not usage.get('linked') and not entry.name.startswith('.') else {}),
                             **({'historySelection': {'project': relative.parts[0], 'nativeSessionId': entry.name}} if root['scope']=='history' and root['id']=='history' and len(relative.parts)==2 and relative.parts[1]=='sessions' and directory and not usage.get('linked') and not entry.name.startswith('.') else {})})
                if time.monotonic() - published >= .05:
                    _publish(snapshot(True)); published = time.monotonic()
        after = os.fstat(fd)
        truncated |= (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)
    finally:
        os.close(fd)
    return snapshot()


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
    completed=0
    def snapshot(pending=False):
        waiting_roots=selected_roots[completed:] if pending else []
        if category:
            return {'version':1,'view':'category','categoryId':category,'scope':'category','label':CATEGORIES[category][0],'rows':[*rows,*[{**r,'directory':True,'bytes':None,'pending':True,'incomplete':True} for r in waiting_roots]],'bytes':sum(r['bytes'] for r in rows),'incomplete':bool(omissions) or pending or any(r['incomplete'] for r in rows),'omissions':list(omissions)}
        groups=[]
        for key,(label,description) in CATEGORIES.items():
            members=[r for r in rows if r['category']==key]
            waiting=any(r['category']==key for r in waiting_roots)
            groups.append({'id':key,'scope':'category','categoryId':key,'label':label,'description':description,'directory':True,'bytes':sum(r['bytes'] for r in members) if members or not waiting else None,'pending':waiting,'incomplete':waiting or any(r['incomplete'] for r in members)})
        return {'version':1,'view':'categories','rows':groups,'bytes':sum(r['bytes'] or 0 for r in groups),'incomplete':bool(omissions) or pending or any(r['incomplete'] for r in groups),'omissions':list(omissions)}
    _publish(snapshot(True))
    for index,root in enumerate(selected_roots):
        path=Path(root['path'])
        # Reserve a fair portion for the remaining roots. One large installation
        # cannot consume every category's scan budget, and total work stays bounded.
        allowance=budget[0]//(len(selected_roots)-index)
        share=[allowance]
        with _slice(.15):
            usage=_usage(path,seen,share,boundaries=boundaries-{str(path)},optional=root.get('optional',False))
        completed=index+1
        budget[0]-=allowance-share[0]
        try: directory=stat.S_ISDIR(path.lstat().st_mode)
        except FileNotFoundError:
            if root.get('optional'):
                _publish(snapshot(index+1<len(selected_roots)))
                continue
            directory=False
        except OSError: directory=False
        rows.append({**root,**usage,'directory':directory})
        _publish(snapshot(index+1<len(selected_roots)))
    if category:
        selected=[row for row in rows if row['category']==category]
        return {'version':1,'view':'category','categoryId':category,'scope':'category','label':CATEGORIES[category][0],
                'rows':sorted(selected,key=lambda r:(-r['bytes'],r['label'].casefold())),
                'bytes':sum(r['bytes'] for r in selected),'incomplete':bool(omissions) or any(r['incomplete'] for r in selected),
                'omissions':list(omissions),'scanBudget':LIMIT}
    categories=[]
    for key,(label,description) in CATEGORIES.items():
        selected=[row for row in rows if row['category']==key]
        categories.append({'id':key,'scope':'category','categoryId':key,'label':label,'description':description,
                           'bytes':sum(r['bytes'] for r in selected),'files':sum(r['files'] for r in selected),
                           'incomplete':any(r['incomplete'] for r in selected),'directory':True})
    return {'version':1,'view':'categories','rows':categories,'bytes':sum(r['bytes'] for r in categories),
            'incomplete':bool(omissions) or any(r['incomplete'] for r in categories),'omissions':list(omissions),'scanBudget':LIMIT}



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
