"""One bounded request per process, using the independently installed public library.

No record-directory scans, native runtime imports, or effect retries. Full source
manifests stay in the library's private durable files and are read by named ID.
"""
import json
import sys
import uuid
import sqlite3
from pathlib import Path

from amplifier_worktrees import GitWorktrees

MAX_INPUT = 65536
MAX_OUTPUT = 1024 * 1024


def inspection(value):
    value = dict(value)
    value['statusTruncated'] = len(value.get('status', '')) > 8192
    value['status'] = value.get('status', '')[:8192]
    for key in ('untracked', 'worktrees'):
        rows = value.get(key, [])
        value[key + 'Count'] = len(rows)
        value[key] = rows[:50]
    return value


def record(value, offset=0, limit=50):
    value = dict(value)
    if 'checkout' in value:
        value['checkout'] = inspection(value['checkout'])
    manifest = dict(value.get('manifest', {}))
    rows = manifest.get('untracked', [])
    manifest['untrackedCount'] = len(rows)
    manifest['untracked'] = rows[offset:offset + limit]
    if offset + limit < len(rows):
        manifest['nextOffset'] = offset + limit
    value['manifest'] = manifest
    return value


def run(request):
    owner = GitWorktrees(request['directory'], execution_host=request.get('executionHost'))
    method, args = request['method'], request.get('args', {})
    identity = None
    if method == 'create':
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'managed-worktree:' + args['commandId']))
    elif method == 'attach':
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'attached-worktree:' + args['commandId']))
    try:
        if method == 'inspect':
            return {'result': inspection(owner.inspect(args['path']))}
        if method == 'create':
            value = owner.create(args['source'], command_id=args['commandId'],
                                 expected_revision=args['sourceRevision'], mode=args.get('mode', 'clean'),
                                 ref=args.get('ref', 'HEAD'), branch=args.get('branch'), session_id=args['sessionId'])
        elif method == 'attach':
            value = owner.attach(args['path'], source=args['source'], command_id=args['commandId'], session_id=args['sessionId'])
        elif method == 'remove':
            value = owner.remove(args['id'], args['expectedRevision'], args['commandId'])
        elif method == 'status':
            value = owner.status(args['id'])
        elif method == 'get':
            value = owner.get(args['id'])
        else:
            raise ValueError('Unknown worktree operation')
        offset, limit = args.get('offset', 0), args.get('limit', 50)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('Invalid manifest page')
        return {'result': record(value, offset, limit)}
    except Exception as exc:
        partial = None
        if identity:
            try:
                partial = record(owner.get(identity))
            except ValueError:
                pass
        # A create/attach with no durable library record failed its preflight.
        # Removal may have completed Git before losing its receipt: stay unknown.
        return {'error': str(exc)[:4000], 'executed': False if identity and partial is None else None,
                **({'record': partial} if partial else {})}


def guarded_run(request):
    """The shared lease outlives a dead Node viewer; no uncertain worker is replayed."""
    identity=request.get('workerId')
    if not identity:
        if request.get('method')!='get':raise ValueError('Mutation worker lifetime identity required')
        return run(request)
    uuid.UUID(identity)
    root=Path(request['gateDirectory'])
    if root.resolve()!=Path(request['directory']).resolve().parent:raise ValueError('Worker lifetime scope differs')
    lease=sqlite3.connect(root/'worktree-worker-lock.sqlite',timeout=0)
    ledger=sqlite3.connect(root/'worktree-workers.sqlite',timeout=5)
    try:
        try:
            lease.execute('BEGIN');lease.execute('SELECT count(*) FROM lease').fetchone()
            gate=sqlite3.connect(f"file:{root/'worktree-quiescence.sqlite'}?mode=ro",uri=True)
            try:held=gate.execute('SELECT 1 FROM fence WHERE id=1').fetchone()
            finally:gate.close()
            if held:raise ValueError('Worktree intake closed before Git admission')
        except (sqlite3.OperationalError,ValueError):
            ledger.execute("UPDATE workers SET state='settled' WHERE id=?",(identity,));ledger.commit()
            return {'error':'Worktree intake is closed; no Git effect admitted','executed':False}
        if not ledger.execute("SELECT 1 FROM workers WHERE id=? AND state='pending'",(identity,)).fetchone():raise ValueError('Exact worker lifetime reservation required')
        response=run(request)
        ledger.execute("UPDATE workers SET state='settled' WHERE id=?",(identity,));ledger.commit()
        return response
    finally:
        ledger.close();lease.close()


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('Worktree request exceeds 64 KiB')
        response = guarded_run(json.loads(raw))
    except Exception as exc:
        response = {'error': str(exc)[:4000]}
    encoded = json.dumps(response).encode()
    if len(encoded) > MAX_OUTPUT:
        encoded = json.dumps({'error': 'Worktree representation exceeds 1 MiB; original evidence preserved'}).encode()
    sys.stdout.buffer.write(encoded)


if __name__ == '__main__':
    main()
