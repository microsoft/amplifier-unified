"""Shared ordered pins. Only registration metadata is read; no sessions are opened."""
import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

MAX_PINS = 10000
MAX_BYTES = 1024 * 1024
PIN_WRITES = {'workspace.pin', 'workspace.pinOrder'}


def saved(db):
    rows = dict(db.execute(
        "SELECT key,substr(value,1,?) FROM meta WHERE key IN ('workspacePins','workspacePinsVersion')",
        (MAX_BYTES + 1,),
    ))
    if not rows:
        return {'revision': 0, 'ids': []}
    try:
        value = json.loads(rows['workspacePins'])
        ids = value['ids']
        if (rows['workspacePinsVersion'] != '1'
                or len(rows['workspacePins'].encode()) > MAX_BYTES
                or type(value['revision']) is not int
                or not 0 <= value['revision'] <= 9007199254740991
                or not isinstance(ids, list) or len(ids) > MAX_PINS
                or not all(isinstance(id, str) and re.fullmatch(r'workspace:[0-9a-f]{64}', id) for id in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Invalid workspace pins')
        return value
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError('Saved workspace pins could not be read; original preferences preserved') from error


def write(db, previous, ids, *, force=False):
    if ids == previous['ids'] and not force:
        return previous['revision']
    if previous['revision'] >= 9007199254740991:
        raise ValueError('Workspace pin revision limit reached; preferences preserved')
    revision = previous['revision'] + 1
    db.execute("INSERT OR REPLACE INTO meta VALUES('workspacePins',?)",
               (json.dumps({'revision': revision, 'ids': ids}, separators=(',', ':')),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('workspacePinsVersion','1')")
    return revision


def summary(owner):
    # The watched topic stays small even with 10000 pins. The separate bounded
    # action supplies the complete ID vector needed for exact reorder.
    with owner.db() as db:
        return {'version': 1, 'revision': saved(db)['revision']}


def remove(db, id):
    previous = saved(db)
    write(db, previous, [item for item in previous['ids'] if item != id])


def visible(owner, db, ids):
    # Authorization and explicit registration visibility precede pagination.
    clauses, params = [], [json.dumps(ids)]
    for root in owner.roots:
        prefix = root.rstrip('/') + '/'
        clauses.append('(r.path=? OR substr(r.path,1,?)=?)')
        params.extend((root, len(prefix), prefix))
    return db.execute(
        'SELECT r.* FROM json_each(?) p JOIN registrations r ON r.id=p.value '
        'WHERE r.hidden=0 AND NOT EXISTS(SELECT 1 FROM workspace_archives a WHERE a.id=r.id AND a.archived=1) AND (' + ' OR '.join(clauses) + ') ORDER BY CAST(p.key AS INTEGER)',
        params,
    )


def page(owner, args, client):
    limit = owner.limit(args)
    offset = args.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= MAX_PINS:
        raise ValueError('Invalid pinned workspace page')
    with owner.db() as db:
        current = saved(db)
        if offset and args.get('expectedRevision') != current['revision']:
            raise ValueError('Workspace pins changed; refresh before paging')
        rows = visible(owner, db, current['ids'])
        ids, items = [], []
        for row in rows:
            ids.append(row['id'])
            if offset < len(ids) <= offset + limit:
                item = dict(row)
                # Only this bounded page is inspected. Missing folders retain their pin.
                try:
                    resolved = owner.authorize(item['path'], existing=False)
                    availability = 'present' if resolved == Path(item['path']) and stat.S_ISDIR(resolved.stat().st_mode) else 'missing'
                except (FileNotFoundError, NotADirectoryError):
                    availability = 'missing'
                except (OSError, ValueError):
                    availability = 'unknown'
                items.append({**item, 'hidden': False, 'registered': True,
                              'available': availability == 'present', 'availability': availability,
                              'source': 'registered', 'checkedAt': datetime.now(timezone.utc).isoformat()})
    return {'version': 1, 'revision': current['revision'], 'ids': ids, 'items': items,
            'offset': offset, 'nextOffset': offset + limit if offset + limit < len(ids) else None,
            'coverage': {'nativeBodiesRead': False, 'agentsStarted': False}}


async def mutate(owner, operation, args, command):
    """The preference and completed command receipt commit in one SQLite transaction."""
    from .owner import WorkspaceError, text
    import time
    text(command, 'command ID', 200)
    payload = json.dumps(args, sort_keys=True)
    if len(payload.encode()) > MAX_BYTES:
        raise WorkspaceError('Workspace pin command exceeds 1MiB')
    async with owner.lock:
        with owner.db() as db:
            original = db.execute('SELECT * FROM commands WHERE id=?', (command,)).fetchone()
            if original:
                if original['operation'] != operation or original['payload'] != payload:
                    raise WorkspaceError('Command identity was reused with different arguments')
                if original['status'] == 'completed':
                    return {**json.loads(original['result']), 'receipt': owner.public_receipt(command), 'replayed': False}
                if original['status'] == 'rejected':
                    raise WorkspaceError(json.loads(original['result'])['reason'], receipt=owner.public_receipt(command))
                raise WorkspaceError('Workspace command is unresolved; inspect its receipt instead of replaying',
                                     executed=None, receipt=owner.public_receipt(command), code='unknown_outcome')
            previous = saved(db)
            expected = args.get('expectedRevision')
            if type(expected) is not int or expected != previous['revision']:
                raise WorkspaceError('Workspace pins changed; refresh before editing')
            ids = list(previous['ids'])
            if operation == 'workspace.pin':
                id = text(args.get('id'), 'workspace ID', 128)
                pinned = args.get('pinned')
                if type(pinned) is not bool:
                    raise WorkspaceError('Choose whether to pin this workspace')
                row = db.execute('SELECT * FROM registrations WHERE id=? AND hidden=0', (id,)).fetchone()
                if not row or db.execute('SELECT 1 FROM workspace_archives WHERE id=? AND archived=1',(id,)).fetchone():
                    raise WorkspaceError('Register or restore this workspace before pinning it')
                owner.authorize(row['path'], existing=False)
                if pinned and id not in ids:
                    if len(ids) >= MAX_PINS:
                        raise WorkspaceError('At most 10000 workspaces can be pinned')
                    ids.append(id)
                elif not pinned:
                    ids = [item for item in ids if item != id]
            else:
                order = args.get('ids')
                current = [row['id'] for row in visible(owner, db, ids)]
                if (not isinstance(order, list) or len(order) > MAX_PINS
                        or any(not isinstance(id, str) for id in order)
                        or len(set(order)) != len(order) or set(order) != set(current)):
                    raise WorkspaceError('Include every current pinned workspace exactly once')
                # Retain inaccessible pins if configured roots narrowed.
                positions = set(current)
                iterator = iter(order)
                ids = [next(iterator) if id in positions else id for id in ids]
            revision = write(db, previous, ids)
            result = {'pinsRevision': revision, 'filesDeleted': False, 'historyPreserved': True}
            db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',
                       (command, operation, payload, 'completed', json.dumps(result), time.time()))
        return {**result, 'receipt': owner.public_receipt(command)}
