"""One durable host admission hold; native runtimes own continuation contents.

The host records only bounded identities and proofs. It neither saves Python
stacks nor reconstructs input. An uncertain resume claim is never replayed.
All mutating methods run under the service lock, except the explicitly async
entry points which acquire that lock themselves.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import time
import uuid

MAX_ROOTS = 512
MAX_CHILDREN = 256
DETAIL = 'Host maintenance is paused. Resume maintenance explicitly before starting new work.'
STATUSES = {'waiting', 'suspended', 'unsupported', 'uncertain', 'stopped', 'quiescent', 'resumed'}
ACTIONS = {'maintenance.status', 'maintenance.pause', 'maintenance.resume',
           'maintenance.stopPreview', 'maintenance.stopNow'}
NEW_WORK = {
    'session.create', 'session.warm', 'session.recover', 'session.takeover',
    'session.fork', 'conversation.send', 'conversation.retry', 'worker.spawn',
    'worker.message', 'worker.steer', 'call.start', 'question.answer',
    'coordination.create', 'coordination.send',
}


def definitions(schema, string):
    epoch = {'epoch': {**string(64), 'minLength': 1}}
    return {
        'maintenance.status': ('Inspect bounded host maintenance receipts without starting work.',
            schema({'offset': {'type': 'integer', 'minimum': 0},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, [])),
        'maintenance.pause': ('Commit a durable admission hold, then request native safe-boundary suspension. Unsupported or uncertain work remains visible.', schema()),
        'maintenance.resume': ('Explicitly resume only this maintenance epoch’s unchanged proven continuations once, and reopen host admission. Never sends a prompt or retries unknown work.', schema(epoch)),
        'maintenance.stopPreview': ('Inspect the exact host-owned execution target for emergency Stop now; does not stop work.', schema()),
        'maintenance.stopNow': ('Separately confirmed emergency Stop of exactly this host’s owned execution. Keeps the app and inspection available; does not undo remote effects.',
            schema({'confirm': {'const': True}, 'hostInstanceId': string(64),
                    'targetDigest': string(64)})),
    }


def held(app):
    owner = getattr(app, 'maintenance', None)
    return bool(owner.value['held'] if owner else app.state.get('hostMaintenance', {}).get('held'))


def require_open(app):
    if held(app):
        from .service import AppError
        raise AppError(DETAIL, 409, code='maintenance_paused')


def restore_projection(app, session):
    """Restore a saved pause display without granting new runtime authority."""
    marker = session.get('maintenancePause')
    if not marker:
        return False
    from .execution import pause_for_maintenance, leave_maintenance_pause
    try:
        host_row = app.db.execute('SELECT value FROM host_maintenance WHERE id=1').fetchone()
        root_row = app.db.execute('SELECT value FROM maintenance_roots WHERE session_id=?',
                                 (session['id'],)).fetchone()
        host = json.loads(host_row[0]) if host_row else {}
        root = json.loads(root_row[0]) if root_row else {}
        proof = _proof(root.get('proof'))
        if (host.get('held') and host.get('phase') == 'paused'
                and root.get('phase') == 'suspended'
                and proof.get('epoch') == marker.get('epoch') == host.get('epoch')
                and proof.get('checkpointId') == marker.get('checkpointId')):
            pause_for_maintenance(session, proof)
            return True
    except (ValueError, TypeError, KeyError):
        pass
    # A replaced/uncertain owner is not a proved pause. Let ordinary restart
    # recovery classify the old execution; this does not dispatch any input.
    leave_maintenance_pause(session, marker.get('epoch'), resumed=False)
    return False


def starts_work(action, args):
    return (action in NEW_WORK
            or action == 'session.naming' and args.get('regenerate')
            or action == 'session.naming.backfill'
            or action == 'approval.respond' and args.get('decision') not in {'deny', 'reject'}
            or action == 'runtime.control' and args.get('operation') not in {
                'status', 'task.get', 'capacity.read', 'history.read', 'stop'}
            or action.startswith('smartTools.') and action not in {
                'smartTools.context', 'smartTools.result'})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _text(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError('Maintenance receipt requires bounded exact identities.')
    return value


def _origin(value):
    if not isinstance(value, dict):
        raise ValueError('A generation origin requires exact input attribution.')
    inputs = value.get('inputIds')
    if not isinstance(inputs, list) or not inputs or len(inputs) > 100:
        raise ValueError('A generation origin requires bounded original inputs.')
    inputs = [_text(item) for item in inputs]
    if len(set(inputs)) != len(inputs):
        raise ValueError('A generation origin repeats an input identity.')
    return {'generationId': _text(value.get('generationId')), 'inputIds': inputs}


def _proof(value, *, child=False):
    if not isinstance(value, dict):
        raise ValueError('Invalid maintenance receipt.')
    status = value.get('status', 'waiting')
    if status not in STATUSES:
        raise ValueError('Unknown maintenance receipt status.')
    result = {'sessionId': _text(value.get('sessionId')), 'status': status}
    for key in ('runtimeId', 'epoch', 'checkpointId', 'generationId'):
        if value.get(key) is not None:
            result[key] = _text(value[key])
    inputs = value.get('inputIds', [])
    if not isinstance(inputs, list) or len(inputs) > 100:
        raise ValueError('Maintenance receipt has invalid input identities.')
    result['inputIds'] = [_text(item) for item in inputs]
    if len(set(result['inputIds'])) != len(inputs):
        raise ValueError('Maintenance receipt repeats an input identity.')
    pending = value.get('pendingInputIds', [])
    if not isinstance(pending, list) or len(pending) > 100:
        raise ValueError('Pending input identities exceed their bound.')
    result['pendingInputIds'] = [_text(item) for item in pending]
    if len(set(result['pendingInputIds'])) != len(pending):
        raise ValueError('Maintenance receipt repeats a pending input.')
    if not set(pending) <= set(result['inputIds']):
        raise ValueError('Pending inputs must belong to this exact root input union.')
    if 'ownedGenerations' in value:
        origins = value['ownedGenerations']
        if not isinstance(origins, list) or len(origins) > 5:
            raise ValueError('Maintenance origins exceed the root and four-job bound.')
        result['ownedGenerations'] = sorted((_origin(item) for item in origins), key=lambda row: row['generationId'])
        if len({row['generationId'] for row in result['ownedGenerations']}) != len(origins):
            raise ValueError('Maintenance receipt repeats an owned generation.')
        if set(result['inputIds']) != (set(pending) | {
                item for row in result['ownedGenerations'] for item in row['inputIds']}):
            raise ValueError('Owned generations must account for the exact input union.')
    if value.get('rootGenerationTerminal') is not None:
        result['rootGenerationTerminal'] = _origin(value['rootGenerationTerminal'])
        if result['rootGenerationTerminal'] not in result.get('ownedGenerations', []):
            raise ValueError('Terminal evidence must retain its original owned generation.')
    if 'jobOrigins' in value:
        jobs = value['jobOrigins']
        if not isinstance(jobs, list) or len(jobs) > 4:
            raise ValueError('Maintenance job origins exceed their bound.')
        result['jobOrigins'] = []
        for job in jobs:
            origin = _origin(job)
            if origin not in result.get('ownedGenerations', []):
                raise ValueError('A job must retain an original owned generation.')
            result['jobOrigins'].append({**origin, 'jobId': _text(job.get('jobId')),
                                         'status': _text(job.get('status')),
                                         **({'callId': _text(job['callId'])} if job.get('callId') else {})})
        if len({row['jobId'] for row in result['jobOrigins']}) != len(jobs):
            raise ValueError('Maintenance receipt repeats a job identity.')
        result['jobOrigins'].sort(key=lambda row: row['jobId'])
    pending_results = value.get('pendingResults', [])
    if not isinstance(pending_results, list) or len(pending_results) > 4:
        raise ValueError('Maintenance result origins exceed their job bound.')
    result['pendingResults'] = []
    jobs = {job['jobId']: job for job in result.get('jobOrigins', [])}
    for pending_result in pending_results:
        item = {key: _text(pending_result.get(key)) for key in ('inputId', 'jobId', 'callId')}
        job = jobs.get(item['jobId'])
        if (not job or job.get('callId') != item['callId']
                or job['status'] not in {'returned', 'failed', 'cancelled'}
                or item['inputId'] in result['inputIds']):
            raise ValueError('A pending result must retain its exact completed job owner.')
        result['pendingResults'].append(item)
    result['pendingResults'].sort(key=lambda row: row['inputId'])
    if (len({row['inputId'] for row in result['pendingResults']}) != len(pending_results)
            or len({row['jobId'] for row in result['pendingResults']}) != len(pending_results)):
        raise ValueError('Maintenance repeats a pending result owner.')
    if child:
        for key in ('parentSessionId', 'callId'):
            if value.get(key) is not None:
                result[key] = _text(value[key])
    else:
        children = value.get('children', [])
        if not isinstance(children, list) or len(children) > MAX_CHILDREN:
            raise ValueError('Maintenance receipt exceeds the child bound.')
        result['children'] = [_proof(item, child=True) for item in children]
        if len({row['sessionId'] for row in result['children']}) != len(children):
            raise ValueError('Maintenance receipt repeats a child identity.')
        result['children'].sort(key=lambda row: row['sessionId'])
    if value.get('reason'):
        result['reason'] = str(value['reason'])[:500]
    return result


def _identity(value):
    return {**{key: value.get(key) for key in
               ('sessionId', 'runtimeId', 'generationId', 'inputIds', 'ownedGenerations', 'pendingInputIds')},
            'jobOrigins': [{key: row.get(key) for key in ('jobId', 'callId', 'generationId', 'inputIds')}
                           for row in value.get('jobOrigins', [])]}


def _pause_identity_matches(previous, proof):
    old, current = _identity(previous), _identity(proof)
    if any(row not in proof.get('pendingResults', []) for row in previous.get('pendingResults', [])):
        return False
    if old == current:
        return True
    # An admitted root can finish while one of its already-owned background
    # jobs suspends. Accept only that terminal transition with explicit immutable
    # origin evidence; absence of an active generation alone proves nothing.
    terminal = proof.get('rootGenerationTerminal')
    return (old.get('generationId') is not None and current.get('generationId') is None
            and {**current, 'generationId': old['generationId']} == old
            and isinstance(terminal, dict) and terminal['generationId'] == old['generationId']
            and terminal in (old.get('ownedGenerations') or [])
            and (any(row['status'] in {'paused', 'suspended'} for row in proof.get('jobOrigins', []))
                 or bool(proof.get('pendingResults'))
                 or proof['status'] == 'quiescent' and not proof.get('pendingInputIds')
                 and all(row['status'] in {'returned', 'failed', 'cancelled'} for row in proof.get('jobOrigins', []))
                 and not proof.get('children')))


def _continuation(value):
    return {**_identity(value), 'checkpointId': value.get('checkpointId'),
            'pendingResults': value.get('pendingResults', []),
            'rootGenerationTerminal': value.get('rootGenerationTerminal'),
            'children': [{**_identity(row), **{key: row.get(key) for key in
                          ('checkpointId', 'parentSessionId', 'callId')}}
                         for row in value.get('children', [])]}


def _settled_children(proof):
    for child in proof.get('children', []):
        if child['status'] not in {'suspended', 'quiescent', 'stopped'}:
            return False
        if child['status'] == 'suspended' and not all(child.get(key) for key in
                ('runtimeId', 'checkpointId', 'parentSessionId', 'callId')):
            return False
    return True


class Maintenance:
    def __init__(self, app):
        self.app = app
        app.db.execute('CREATE TABLE IF NOT EXISTS host_maintenance (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
        app.db.execute('CREATE TABLE IF NOT EXISTS maintenance_roots (session_id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        row = app.db.execute('SELECT value FROM host_maintenance WHERE id=1').fetchone()
        self.value = json.loads(row[0]) if row else {
            'epoch': None, 'held': False, 'phase': 'open', 'revision': 0,
            'inventoryComplete': True,
        }
        # Durable claims survive process loss. Neither startup nor a passive
        # read reissues Pause, Resume or Stop.
        if row and self.value['phase'] in {'pausing', 'resuming', 'stopping'}:
            self.value.update(held=True, phase='uncertain', revision=self.value['revision'] + 1,
                              reason='The host restarted during maintenance; no operation was replayed.')
            for root in self._rows():
                if root['phase'] in {'waiting', 'resume-requested', 'resuming', 'stopping'}:
                    root.update(phase='uncertain', reason='The prior maintenance outcome is unconfirmed.')
                    self._put(root)
            app.db.execute('INSERT OR REPLACE INTO host_maintenance VALUES (1,?)',
                           (json.dumps(self.value),))
        app.db.commit()
        self._project()
        self.bind_runtime()

    def bind_runtime(self):
        for name, callback in {
            'set_maintenance_observer': self.observe,
            'set_maintenance_authorizer': self.authorize_resume,
            'set_maintenance_resume_guard': self.validate_resume,
            'set_maintenance_admission': self.admit,
            'set_maintenance_loader': self.load_resume,
            'set_maintenance_releaser': self.release_resume,
        }.items():
            setter = getattr(self.app.runtime, name, None)
            if callable(setter):
                setter(callback)

    def _rows(self):
        return [json.loads(row[0]) for row in self.app.db.execute(
            'SELECT value FROM maintenance_roots ORDER BY session_id LIMIT ?', (MAX_ROOTS,))]

    def _row(self, sid):
        row = self.app.db.execute('SELECT value FROM maintenance_roots WHERE session_id=?', (sid,)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, row):
        self.app.db.execute('INSERT OR REPLACE INTO maintenance_roots VALUES (?,?)',
                            (row['sessionId'], json.dumps(row)))

    def _hold(self, **changes):
        self.value.update(changes, held=True)
        # Capture can fail before _commit is reached. Close all event-loop
        # admission readers immediately; no runtime request is issued yet.
        self.app.state['hostMaintenance'] = {
            **self.app.state.get('hostMaintenance', {}), **copy.deepcopy(self.value),
            'readyForRestart': False,
        }

    def _project(self):
        from .maintenance_auxiliary import snapshot as auxiliary_snapshot
        auxiliary = auxiliary_snapshot(self.app)
        rows = self._rows()
        counts = {}
        for row in rows:
            status = row.get('phase', row['proof']['status'])
            counts[status] = counts.get(status, 0) + 1
        # Only this bounded summary joins the ordinary shell state. Exact
        # receipts are explicitly paged through maintenance.status.
        self.app.state['hostMaintenance'] = {
            **copy.deepcopy(self.value), 'counts': counts, 'total': len(rows),
            'auxiliary': auxiliary,
            'canPause': bool(not self.value['held'] and self.value['phase'] != 'resuming'),
            'readyForRestart': bool(auxiliary['settled'] and self.value['held'] and self.value['inventoryComplete']
                and self.value['phase'] in {'pausing', 'paused', 'stopping'}
                and all(row.get('phase') in {'suspended', 'stopped', 'quiescent'} for row in rows)),
            'canResume': bool(auxiliary['settled'] and self.value['held'] and self.value['inventoryComplete']
                and self.value['phase'] == 'paused'
                and all(row.get('phase') in {'suspended', 'blocked', 'stopped', 'quiescent'}
                        and row['proof']['status'] in {'suspended', 'stopped', 'quiescent'}
                        and _settled_children(row['proof']) for row in rows)),
        }

    def _commit(self):
        self.value['revision'] += 1
        self.value['updatedAt'] = time.time()
        # Never expose reopened admission before persistence, or let a failed
        # pause write leave shell-based admission consumers falsely open.
        current = self.app.state.get('hostMaintenance', {})
        self.app.state['hostMaintenance'] = {
            **current, 'held': bool(current.get('held') or self.value['held']),
            'readyForRestart': False,
        }
        try:
            self.app.db.execute('INSERT OR REPLACE INTO host_maintenance VALUES (1,?)', (json.dumps(self.value),))
            self.app.db.commit()
        except Exception:
            self.value.update(held=True, phase='uncertain',
                              reason='Maintenance persistence failed; admission remains held.')
            self.app.state['hostMaintenance'].update(self.value, readyForRestart=False)
            raise
        self._project()
        self.app._publish_changes(globals={'hostMaintenance'})

    def snapshot(self, offset=0, limit=100):
        self._project()
        rows = self._rows()
        return {**copy.deepcopy(self.app.state['hostMaintenance']), 'offset': offset,
                'roots': [{key: copy.deepcopy(row[key]) for key in
                           ('sessionId', 'proof', 'phase', 'reason', 'resumeCommandId')
                           if key in row} for row in rows[offset:offset + limit]],
                'hasMore': offset + limit < len(rows)}

    def _inventory(self):
        getter = getattr(self.app.runtime, 'maintenance_snapshot', None)
        if not callable(getter):
            return [], self.app.runtime is None
        raw = getter()
        if inspect.isawaitable(raw):
            if inspect.iscoroutine(raw):
                raw.close()
            raise ValueError('Maintenance inventory must be a synchronous owned-runtime snapshot.')
        if isinstance(raw, dict):
            raw = raw.get('roots')
        if not isinstance(raw, list) or len(raw) > MAX_ROOTS:
            raise ValueError('Maintenance inventory is unavailable or exceeds the root bound.')
        rows = [_proof(item) for item in raw]
        if len({row['sessionId'] for row in rows}) != len(rows):
            raise ValueError('Maintenance inventory repeats a root.')
        return sorted(rows, key=lambda row: row['sessionId']), True

    def _binding(self, sid):
        session = self.app._session(sid)
        task = self._task_record(session)
        from .task_binding import continuation_epoch
        assignment = self.app.assignments.current(session)
        return {
            'runtimeId': session.get('runtimeSessionId') or sid,
            'interruptionRevision': session.get('interruptionRevision', 0),
            'executionRevision': session.get('executionRevision', 0),
            'workspace': session.get('workspace'),
            'workingDirectory': session.get('workingDirectory'),
            'historyEdit': (session.get('historyEdit') or {}).get('operationId'),
            'workerStops': {row['id']: row.get('interruptionRevision', 0)
                           for row in session.get('workers', [])
                           if row.get('interruptionRevision')},
            'configurationRevision': self.app.state.get('configurationRevision', 0),
            'taskId': task.get('id'), 'taskEpoch': continuation_epoch(task),
            'assignmentId': assignment.get('id') if assignment else None,
        }

    def _task_record(self, session):
        identity = session.get('runtimeSessionId') or session['id']
        path = self.app.data_dir / 'sessions' / identity / 'control-state.json'
        try:
            if path.stat().st_size > 2_000_000:
                raise ValueError('Persisted task authority exceeds its bound.')
        except FileNotFoundError:
            return {}
        value = json.loads(path.read_text())
        task = value.get('task') or {}
        if not isinstance(task, dict):
            raise ValueError('Persisted task authority is invalid.')
        return task

    def _capture(self, epoch, inventory, *, replace=True):
        from .service import AppError
        if replace:
            self.app.db.execute('DELETE FROM maintenance_roots')
        for proof in inventory:
            if not replace and self._row(proof['sessionId']):
                continue
            if self.app.db.execute('SELECT count(*) FROM maintenance_roots').fetchone()[0] >= MAX_ROOTS:
                self.value.update(inventoryComplete=False, reason='Owned root inventory exceeded its bound; readiness is unproven.')
                break
            proof = {**proof, 'epoch': epoch}
            row = {'sessionId': proof['sessionId'], 'proof': proof, 'phase': 'waiting'}
            try:
                row['binding'] = self._binding(proof['sessionId'])
                if proof.get('runtimeId') != row['binding']['runtimeId']:
                    raise ValueError('Runtime identity does not match the owned host root.')
                if self.app._session(proof['sessionId']).get('status') in {
                        'stopped', 'error', 'read-only'}:
                    row.update(phase='blocked', reason='This root was already stopped or blocked before maintenance.')
            except (ValueError, KeyError, OSError, AppError):
                row.update(phase='uncertain', reason='The host cannot bind this owned runtime to a current conversation.')
            self._put(row)

    def _policy_reason(self, row):
        from .service import AppError
        sid = row['sessionId']
        try:
            if not row.get('binding') or self._binding(sid) != row['binding']:
                return 'Stop, history, placement, task, configuration or assignment ownership changed.'
            session = self.app._session(sid)
            if session.get('configurationBusy') or session.get('fileAccessPending'):
                return 'Conversation configuration or access is changing.'
            if session.get('ownership', {}).get('status') in {
                    'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
                return 'Conversation execution belongs to another owner.'
            if session.get('failure', {}).get('category') in {'content_filter', 'computer_capture_stop'}:
                return 'A safety stop requires its own explicit recovery.'
            task = self._task_record(session)
            if task.get('status') in {'paused', 'blocked', 'completed'}:
                return 'The saved task is paused, blocked or completed.'
            from .capacity import saved_policy, evaluate, usage_snapshot
            result = evaluate(saved_policy(self.app, sid), usage_snapshot(session))
            if not result['allowed']:
                return '; '.join(result['reasons'])
            result = self.app.assignments.admission(session, {})
            if not result['allowed']:
                return '; '.join(result['reasons'])
        except (ValueError, KeyError, OSError, AppError):
            return 'Current conversation authority or budget could not be verified.'
        return None

    def _valid_resume(self, sid, epoch, receipt, phases):
        row = self._row(sid)
        if (not row or epoch != self.value['epoch'] or row.get('phase') not in phases
                or row['proof'].get('epoch') != epoch):
            return None
        try:
            proof = _proof(receipt)
        except (ValueError, TypeError):
            return None
        if (proof.get('epoch') != epoch or _identity(proof) != _identity(row['proof'])
                or not proof.get('checkpointId')
                or _continuation(proof) != _continuation(row['proof'])
                or self._policy_reason(row)):
            return None
        return row

    def admit(self, session_id=None, operation=None):
        # Manager operations sometimes already run under app.lock. This
        # synchronous event-loop guard also works under their final worker lock
        # without reentering the service lock or yielding after the check.
        require_open(self.app)
        return True

    async def authorize_resume(self, session_id, epoch, receipt):
        async with self.app.lock:
            row = self._valid_resume(session_id, epoch, receipt, {'resume-requested'})
            if (row is None or self.value['phase'] != 'resuming'
                    or self.value.get('releasedEpoch') != epoch or self.value['held']):
                return False
            row.update(phase='resuming', claimedAt=time.time())
            self._put(row)
            self._commit()
            return True

    async def release_resume(self, epoch, receipts):
        """One durable decision after every selected root remounted still held."""
        async with self.app.lock:
            if (epoch != self.value['epoch'] or self.value['phase'] != 'resuming'
                    or not self.value['held'] or self.value.get('releasedEpoch') == epoch):
                return False
            from .maintenance_auxiliary import snapshot as auxiliary_snapshot
            if not auxiliary_snapshot(self.app)['settled']:
                return False
            expected = {row['sessionId'] for row in self._rows() if row['phase'] == 'resume-requested'}
            if (not isinstance(receipts, list) or len(receipts) != len(expected)
                    or {row.get('sessionId') for row in receipts} != expected):
                return False
            if any(self._valid_resume(row['sessionId'], epoch, row, {'resume-requested'}) is None
                   for row in receipts):
                return False
            self.value.update(held=False, releasedEpoch=epoch, releasedAt=time.time())
            self._commit()
            return True

    async def validate_resume(self, session_id, epoch, receipt):
        async with self.app.lock:
            return self._valid_resume(session_id, epoch, receipt, {'resuming', 'resumed'}) is not None

    async def load_resume(self, session_id):
        """Canonical source for paused-only remount; does not authorize effects."""
        async with self.app.lock:
            row = self._row(session_id)
            if (not row or row['phase'] != 'resume-requested'
                    or self.value['phase'] != 'resuming'
                    or row['proof'].get('epoch') != self.value['epoch']
                    or self._policy_reason(row)):
                raise ValueError('This maintenance continuation cannot be remounted.')
            return copy.deepcopy(self.app._session(session_id)), self.app.on_runtime_event

    async def observe(self, receipt):
        proof = _proof(receipt)
        async with self.app.lock:
            row = self._row(proof['sessionId'])
            if (row is None or proof.get('epoch') != self.value['epoch']
                    or not (_pause_identity_matches(row['proof'], proof) if row['phase'] in {'waiting', 'unsupported'}
                            else _identity(proof) == _identity(row['proof']))):
                return False
            status = proof['status']
            if row['phase'] == 'resume-requested' and status == 'suspended':
                # Paused remount observes the same durable checkpoint before
                # claiming its continuation. Acknowledge that observation
                # without erasing the explicit, not-yet-consumed resume intent.
                return _continuation(proof) == _continuation(row['proof'])
            if row['phase'] in {'resuming', 'resumed'}:
                if status not in {'resumed', 'uncertain', 'stopped'}:
                    return False
                if _continuation(proof) != _continuation(row['proof']):
                    return False
            elif row['phase'] in {'stopping', 'stopped', 'uncertain', 'blocked'}:
                if status != 'stopped':
                    return False
            elif status == 'resumed':
                return False
            elif row['phase'] == 'suspended' and _continuation(proof) != _continuation(row['proof']):
                return False
            if status == 'suspended' and (
                    not proof.get('checkpointId')
                    or not proof.get('inputIds') or not _settled_children(proof)
                    or self._policy_reason(row)):
                status = 'blocked'
                row['reason'] = 'The continuation is unproven or current authority changed.'
            if status == 'quiescent' and (not _settled_children(proof)
                    or any(child['status'] == 'suspended' for child in proof.get('children', []))):
                status = 'uncertain'
                row['reason'] = 'A child has not proved a settled maintenance boundary.'
            row.update(proof=proof, phase=status)
            self._put(row)
            if status in {'suspended', 'resumed', 'stopped'}:
                session = self.app._session(proof['sessionId'])
                from .execution import pause_for_maintenance, leave_maintenance_pause, stop_maintenance_pause
                if status == 'suspended':
                    pause_for_maintenance(session, proof)
                elif status == 'stopped':
                    stop_maintenance_pause(session, proof['epoch'])
                else:
                    leave_maintenance_pause(session, proof['epoch'], resumed=True)
                    collaboration = getattr(self.app, 'collaboration', None)
                    if collaboration is not None:
                        collaboration.resume_maintenance_generation(session, proof)
                save = getattr(self.app, '_save', None)
                if callable(save):
                    save(session_ids={session['id']}, record_only=True)
            self._commit()  # awaited observer cannot report success before persistence
            return True

    def invalidate(self, session_id, reason):
        """Called within an existing durable Stop/edit transaction, never resumes."""
        row = self._row(session_id)
        if row and row['phase'] not in {'stopped', 'blocked'}:
            row.update(phase='blocked', reason=reason)
            self._put(row)
            self._project()

    def _stop_target(self):
        inventory, complete = self._inventory()
        if not complete:
            raise ValueError('This runtime cannot identify owned execution for emergency Stop.')
        return {'hostInstanceId': self.app.instance_id, 'targetDigest': _digest({
            'instance': self.app.instance_id, 'epoch': self.value['epoch'],
            'revision': self.value['revision'], 'roots': [_identity(row) for row in inventory],
        })}, inventory

    async def dispatch(self, action, args, origin, command_id, admission_check=None):
        from .service import AppError, command_fingerprint
        if origin not in {'ui', 'user'}:
            raise AppError('Host maintenance controls require an explicit human action.', 403)
        async with self.app.lock:
            if action == 'maintenance.status':
                return {'accepted': True, 'result': self.snapshot(args.get('offset', 0), args.get('limit', 100))}
            if action == 'maintenance.stopPreview':
                try:
                    target, inventory = self._stop_target()
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                return {'accepted': True, 'result': {**target, 'ownedRoots': len(inventory),
                    'detail': 'Stop owned execution now. Remote accepted effects cannot be undone.'}}
            if not command_id:
                raise AppError('Maintenance changes require a stable command identity.', 400)
            digest = command_fingerprint(action, args, origin, self.app.clients.current.get())
            previous = self.app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise AppError('This command identity already names another request.', 409)
                return {**json.loads(previous[1]), 'duplicate': True}
            if admission_check is not None:
                admission_check()
            if action == 'maintenance.pause':
                if self.value['held']:
                    operation, ids = None, []
                else:
                    epoch = str(uuid.uuid4())
                    try:
                        inventory, complete = self._inventory()
                    except (ValueError, TypeError):
                        inventory, complete = [], False
                    self._hold(epoch=epoch, phase='pausing',
                               inventoryComplete=complete, requestedAt=time.time())
                    self._capture(epoch, inventory)
                    operation, ids = 'pause', []
            elif action == 'maintenance.resume':
                if not self.value['held'] or args['epoch'] != self.value['epoch']:
                    raise AppError('The maintenance epoch changed. Inspect it before resuming.', 409)
                if self.value['phase'] == 'resuming':
                    raise AppError('The recorded resume is still pending; inspect it without replaying.', 409)
                self._project()
                if not self.app.state['hostMaintenance']['canResume']:
                    raise AppError('Work has not reached confirmed pause boundaries. Wait or explicitly stop owned execution before resuming.', 409)
                ids = []
                for row in self._rows():
                    if row['phase'] != 'suspended':
                        continue
                    reason = self._policy_reason(row)
                    if reason:
                        row.update(phase='blocked', reason=reason)
                    else:
                        ids.append(row['sessionId'])
                        row.update(phase='resume-requested', resumeCommandId=command_id)
                    self._put(row)
                self.value['phase'] = 'resuming'
                operation = 'resume'
            elif action == 'maintenance.stopNow':
                try:
                    target, inventory = self._stop_target()
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if args.get('confirm') is not True or any(args.get(key) != value for key, value in target.items()):
                    raise AppError('The host or owned execution changed. Review and confirm Stop again.', 409)
                if not self.value['held']:
                    epoch = str(uuid.uuid4())
                    self._hold(epoch=epoch, inventoryComplete=True)
                    self._capture(epoch, inventory)
                else:
                    self._capture(self.value['epoch'], inventory, replace=False)
                self.value['phase'] = 'stopping'
                for row in self._rows():
                    row['phase'] = 'stopping'
                    self._put(row)
                operation, ids = 'stop', []
            else:
                raise AppError('Unknown host maintenance action.', 404)
            receipt = {'accepted': True, 'commandAction': action, 'requestId': command_id,
                       'result': {'epoch': self.value['epoch'], 'phase': self.value['phase'],
                                  'sessionIds': ids, 'inputsReplayed': False}}
            self.app.db.execute('INSERT INTO commands VALUES (?,?,?)', (command_id, digest, json.dumps(receipt)))
            self._commit()
            if operation is not None:
                self.app._task(self._run(operation, self.value['epoch'], ids))
            return receipt

    async def _run(self, operation, epoch, ids):
        phase = {'pause': 'pausing', 'resume': 'resuming', 'stop': 'stopping'}[operation]
        try:
            callback = getattr(self.app.runtime, 'maintenance_' + operation, None)
            empty_runtime = self.app.runtime is None and not self._rows()
            if not callable(callback) and not empty_runtime:
                raise ValueError('This runtime does not support native maintenance ' + operation + '.')
            # Pause may be superseded by emergency Stop before its task runs.
            async with self.app.lock:
                if self.value['epoch'] != epoch or self.value['phase'] != phase:
                    return
            result = None if empty_runtime else (
                await callback(epoch, ids) if operation == 'resume' else await callback(epoch))
            if result is not None:
                rows = result.get('roots', []) if isinstance(result, dict) else result
                if not isinstance(rows, list) or len(rows) > MAX_ROOTS:
                    raise ValueError('Runtime maintenance returned an invalid bounded snapshot.')
                for row in rows:
                    await self.observe(row)
            async with self.app.lock:
                if self.value['epoch'] != epoch or self.value['phase'] != phase:
                    return
                if operation == 'resume':
                    # Claimed-but-unconfirmed roots retain uncertainty; never
                    # reset them to suspended just because a response was lost.
                    for row in self._rows():
                        if row['phase'] in {'resume-requested', 'resuming'}:
                            row.update(phase='uncertain', reason='Resume was not positively confirmed; it will not be replayed.')
                            self._put(row)
                    self.value.update(held=False, phase='open', lastResumedAt=time.time())
                elif operation in {'pause', 'stop'}:
                    self.value['phase'] = 'paused'
                self._commit()
        except BaseException as exc:
            async with self.app.lock:
                if self.value['epoch'] == epoch and self.value['phase'] == phase:
                    # A persisted release decision is not rolled back after
                    # some roots may already execute. Unconfirmed roots remain
                    # uncertain and are never replayed. A new explicit Pause
                    # captures current work, including any newly admitted work.
                    self.value.update(held=self.value.get('releasedEpoch') != epoch,
                                      phase='uncertain', reason=str(exc)[:500])
                    for row in self._rows():
                        if row['phase'] in {'waiting', 'resume-requested', 'resuming', 'stopping'}:
                            row.update(phase='uncertain', reason='Runtime maintenance outcome could not be confirmed.')
                            self._put(row)
                    self._commit()
            import asyncio
            if isinstance(exc, asyncio.CancelledError):
                raise
