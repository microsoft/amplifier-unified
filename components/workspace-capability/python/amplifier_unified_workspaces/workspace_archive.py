"""Reversible navigation state, independent of chat archives and folder removal."""
import json
import time
from . import workspace_navigation as navigation

WRITES = {'workspace.archive', 'workspace.restore'}


async def mutate(owner, operation, args, command):
    from .owner import WorkspaceError, text
    text(command, 'command ID', 200)
    payload = json.dumps(args, sort_keys=True)
    async with owner.lock:
        with owner.db() as db:
            prior = db.execute('SELECT * FROM commands WHERE id=?', (command,)).fetchone()
            if prior:
                if prior['operation'] != operation or prior['payload'] != payload:
                    raise WorkspaceError('Command identity was reused with different arguments')
                if prior['status'] == 'completed':
                    result = json.loads(prior['result'])
                elif prior['status'] == 'rejected':
                    raise WorkspaceError(json.loads(prior['result'])['reason'], receipt=owner.public_receipt(command))
                else:
                    raise WorkspaceError('Workspace command is unresolved; inspect its receipt instead of replaying',
                                         executed=None, receipt=owner.public_receipt(command), code='unknown_outcome')
            else:
                id = text(args.get('id'), 'workspace ID', 128)
                row = db.execute('SELECT * FROM registrations WHERE id=? AND hidden=0', (id,)).fetchone()
                if not row:
                    raise WorkspaceError('Register this workspace before archiving or restoring it')
                owner.authorize(row['path'], existing=False)
                if type(args.get('expectedRevision')) is not int or args['expectedRevision'] != row['revision']:
                    raise WorkspaceError('Workspace revision changed; refresh before editing')
                archived = operation == 'workspace.archive'
                db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
                revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                db.execute('UPDATE registrations SET revision=? WHERE id=?', (revision, id))
                db.execute('INSERT OR REPLACE INTO workspace_archives VALUES(?,?)', (id, int(archived)))
                # Pins survive and regain their position on restore. Their visible
                # ordering changed, so an earlier reorder/paging request is stale.
                pins = navigation.saved(db)
                navigation.write(db, pins, pins['ids'], force=True)
                result = {'workspace': {**dict(row), 'hidden': False, 'registered': True,
                                       'revision': revision, 'archived': archived},
                          'outcome': 'archived' if archived else 'restored',
                          'filesDeleted': False, 'historyPreserved': True, 'workStopped': False}
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',
                           (command, operation, payload, 'completed', json.dumps(result), time.time()))
        await owner.synchronize()
        return {**result, 'receipt': owner.public_receipt(command), 'replayed': False}
