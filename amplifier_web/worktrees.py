"""App policy over managed Git worktrees and explicit execution handoff."""
import asyncio
import copy
import json
from pathlib import Path
import time
import uuid

from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import atomic, digest
from .host_identity import local_host_identity, require_local_host


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    identity = {**sid, 'id': string(200)}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    return {
        'worktree.inspect': ('Inspect this task’s canonical repository, execution folder, current Git revision and changes without starting work.', schema(sid)),
        'worktree.list': ('List checkouts associated with this task and durable handoff receipts.', schema(sid)),
        'worktree.status': ('Inspect a checkout, its original source manifest, conflicts and Git ownership.', schema(identity)),
        'worktree.create': ('Create a managed checkout from an exact inspected source revision. Default clean committed HEAD; carry_dirty must be explicitly requested, copies staged/unstaged/untracked evidence without changing the source. No execution starts.', schema({**sid, 'sourceRevision': string(100), 'mode': {'enum': ['clean', 'carry_dirty']}, 'ref': string(500), 'branch': string(500)}, ['sessionId', 'sourceRevision'])),
        'worktree.attach': ('Associate an existing Git worktree from the same repository; does not copy history or start work. Attached folders cannot be deleted through managed cleanup.', schema({**sid, 'path': string(4000)})),
        'worktree.remove': ('Remove an app-created checkout only when it has no changed/untracked/ignored files and no task is executing there. Preserves branch, source, history and manifests.', schema({**identity, **revision})),
        'worktree.handoff': ('Move this task’s execution folder to an associated checkout or back to its canonical home (id null). Return a durable pending receipt, then cooperatively save and release the old runtime. No input or uncertain effect is replayed. History home, saved settings, task, drafts, voice and artifact identities remain unchanged.', schema({**sid, 'id': {'type': ['string', 'null']}, 'expectedExecutionRevision': {'type': 'integer', 'minimum': 0}}, ['sessionId', 'id', 'expectedExecutionRevision'])),
        'worktree.reconcile': ('After inspecting an unknown handoff, deliberately choose its source or target. Requires current receipt revision and user evidence, confirms writer release again, and never replays model input. Agents must cite the actual user finding.', schema({**identity, **revision, 'destination': {'enum': ['source', 'target']}, 'evidence': {**string(4000), 'minLength': 1}, 'sourceMessageId': string(200)}, ['sessionId', 'id', 'expectedRevision', 'destination', 'evidence'])),
    }


class Worktrees:
    def __init__(self, service):
        self.app = service
        self.git = GitWorktrees(service.data_dir / 'managed-worktrees', execution_host=local_host_identity())
        self.receipt_dir = service.data_dir / 'worktree-handoffs'
        self.receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.jobs = set()
        self.mutations = asyncio.Lock()
        for path in self.receipt_dir.glob('*.json'):
            record = json.loads(path.read_text())
            session = next((s for s in service.state['sessions'] if s['id'] == record['sessionId']), {})
            inconsistent = record['phase'] == 'applied' and (session.get('executionRevision', 0) <= record['executionRevision'] or (session.get('executionRevision', 0) == record['executionRevision'] + 1 and session.get('workingDirectory') != record['target']))
            if record['phase'] == 'pending' or inconsistent:
                record.update(phase='unknown', revision=record['revision']+1, detail='The app restarted during handoff. No input was replayed; inspect and reconcile explicitly.')
                self.save(record)
        self.sync()
        self.bind_runtime()

    def execution_state(self, sid):
        session = self.app._session(sid)
        host = session.get('executionHost')
        host_matches = not host or (host.get('scope') == 'local' and host.get('id') == local_host_identity()['id'])
        return {'hostMatches': host_matches, 'executionHost': copy.deepcopy(host), 'directory': session.get('workingDirectory') or session['workspace'],
                'revision': session.get('executionRevision', 0),
                'fenced': any(row['phase'] in {'pending', 'unknown'} for row in session.get('worktreeHandoffs', []))}

    def bind_runtime(self):
        binding = getattr(self.app.runtime, 'bind_execution_state', None)
        if callable(binding): binding(self.execution_state)

    def start(self):
        # Production creates its runtime after AppService hydration. Install
        # the reader before accepting inputs, including restored unknown moves.
        self.bind_runtime()
        operations = getattr(self.app, 'operations', None)
        if operations and hasattr(operations, 'register_source'):
            operations.register_source('worktree', self.operation_records, self.operation_record)

    def operation_records(self, sid):
        return [self.operation_shape(row) for row in self.receipts(sid)]

    def operation_record(self, sid, identity):
        try: return self.operation_shape(self.read(sid, identity.removeprefix('worktree:')))
        except ValueError: return None

    @staticmethod
    def operation_shape(row):
        return {'id': 'worktree:' + row['id'], 'sessionId': row['sessionId'], 'source': 'worktree', 'kind': 'checkout-handoff', 'state': {'pending': 'running', 'applied': 'completed', 'reconciled': 'completed', 'unknown': 'outcome_unknown'}[row['phase']], 'revision': row['revision'], 'createdAt': row['createdAt'], 'updatedAt': row.get('updatedAt', row['createdAt']), 'controlAvailable': False, 'evidence': copy.deepcopy(row)}

    def receipts(self, sid):
        records = [json.loads(path.read_text()) for path in sorted(self.receipt_dir.glob('*.json'))]
        return [record for record in records if record['sessionId'] == sid]
    def save(self, record): atomic(self.receipt_dir / (record['id'] + '.json'), record)
    def read(self, sid, identity):
        try: uuid.UUID(identity)
        except (ValueError, TypeError): raise ValueError('Invalid handoff identity') from None
        path = self.receipt_dir / (identity + '.json')
        if not path.exists(): raise ValueError('Unknown handoff')
        value = json.loads(path.read_text())
        if value['sessionId'] != sid: raise ValueError('This handoff belongs to another task')
        return value

    def record(self, sid, identity):
        record = self.git.get(identity)
        if record.get('sessionId') != sid: raise ValueError('This checkout belongs to another task')
        return record

    @staticmethod
    def check_host(record):
        host = record.get('executionHost')
        if host:
            if host.get('scope') != 'local':
                raise ValueError('Cross-host execution is unavailable.')
            require_local_host(host.get('id'))

    def sync(self):
        records = self.git.records()
        handoffs = [json.loads(path.read_text()) for path in sorted(self.receipt_dir.glob('*.json'))]
        for session in self.app.state['sessions']:
            receipts = [record for record in handoffs if record['sessionId'] == session['id']]
            session['worktrees'] = [record for record in records if record.get('sessionId') == session['id']]
            session['worktreeHandoffs'] = receipts
            if any(row['phase'] in {'pending', 'unknown'} for row in receipts): session['configurationBusy'] = True

    def changed(self):
        self.sync(); self.app._publish()
        operations = getattr(self.app, 'operations', None)
        if operations and hasattr(operations, 'notify'): operations.notify()

    async def dispatch(self, action, args, origin, command_id):
        if action in {'worktree.inspect', 'worktree.list', 'worktree.status'}:
            return await self._dispatch(action, args, origin, command_id)
        async with self.mutations:
            try:
                return await self._dispatch(action, args, origin, command_id)
            except BaseException:
                async with self.app.lock: self.changed()
                raise

    async def _dispatch(self, action, args, origin, command_id):
        sid = args['sessionId']; session = self.app._session(sid)
        if action in {'worktree.inspect', 'worktree.list'}:
            result = {'currentHost': local_host_identity(), 'executionHost': session.get('executionHost') or local_host_identity(), 'historyHome': session['workspace'], 'executionDirectory': session.get('workingDirectory') or session['workspace'], 'executionRevision': session.get('executionRevision', 0), 'settingsScope': session['workspace'], 'items': [r for r in self.git.records() if r.get('sessionId') == sid], 'handoffs': self.receipts(sid)}
            if action == 'worktree.inspect': result['repository'] = await asyncio.to_thread(self.git.inspect, result['executionDirectory'])
            async with self.app.lock:
                session['worktreeInspection'] = result
                self.changed()
            return result
        if action == 'worktree.status':
            self.record(sid, args['id'])
            result = await asyncio.to_thread(self.git.status, args['id'])
            async with self.app.lock:
                session['worktreeStatus'] = result; self.changed()
            return result
        self.check_host(session)
        identity = command_id or str(uuid.uuid4())
        if action in {'worktree.handoff', 'worktree.reconcile'}:
            return await self.handoff(action, args, origin, identity)
        if action == 'worktree.create':
            result = await asyncio.to_thread(self.git.create, session.get('workingDirectory') or session['workspace'], command_id=identity, expected_revision=args['sourceRevision'], mode=args.get('mode', 'clean'), ref=args.get('ref', 'HEAD'), branch=args.get('branch'), session_id=sid)
        elif action == 'worktree.attach':
            result = await asyncio.to_thread(self.git.attach, args['path'], source=session['workspace'], command_id=identity, session_id=sid)
        elif action == 'worktree.remove':
            record = self.record(sid, args['id'])
            self.check_host(record)
            if any((s.get('workingDirectory') or s['workspace']) == record['path'] for s in self.app.state['sessions']): raise ValueError('A task still uses this checkout; hand it back first')
            if any(r['phase'] in {'pending', 'unknown'} and record['path'] in {r['source'], r['target']} for s in self.app.state['sessions'] for r in self.receipts(s['id'])): raise ValueError('An unresolved handoff still refers to this checkout')
            result = await asyncio.to_thread(self.git.remove, args['id'], args['expectedRevision'], identity)
        else: raise ValueError('Unknown worktree action')
        async with self.app.lock: self.changed()
        return result

    async def handoff(self, action, args, origin, command_id):
        sid = args['sessionId']; session = self.app._session(sid)
        signature = digest([action, args, origin])
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'worktree-handoff:' + command_id))
        path = self.receipt_dir / (identity + '.json')
        async with self.app.lock:
            if path.exists():
                record = self.read(sid, identity)
                if record['signature'] != signature: raise ValueError('This handoff command already has different contents')
                return {**record, 'duplicate': True}
            if any(row['phase'] == 'pending' for row in self.receipts(sid)):
                raise ValueError('A handoff is already pending; inspect its receipt before another request')
            previous = None
            if action == 'worktree.reconcile':
                previous = self.read(sid, args['id'])
                self.check_host(previous)
                if previous['revision'] != args['expectedRevision'] or previous['phase'] != 'unknown': raise ValueError('Inspect the current unknown handoff before reconciliation')
                if origin != 'ui':
                    source = next((m for m in session['messages'] if m['id'] == args.get('sourceMessageId') and m.get('role') == 'user' and m.get('inputOrigin') in {'ui', 'user', 'voice'} and not m.get('questionId') and not m.get('scheduledRunId')), None)
                    if not source or source.get('createdAt', 0) < previous['createdAt']: raise ValueError('Cite the user’s actual finding after this handoff')
                target = previous[args['destination']]
            else:
                if session.get('configurationBusy'): raise ValueError('Another configuration change or handoff is unresolved')
                if session.get('executionRevision', 0) != args['expectedExecutionRevision']: raise ValueError('The execution folder revision changed; inspect it again')
                if args.get('id'): self.check_host(self.record(sid, args['id']))
                target = self.record(sid, args['id'])['path'] if args.get('id') else session['workspace']
                if args.get('id') and self.record(sid, args['id'])['status'] != 'ready': raise ValueError('The checkout is not ready; inspect its conflicts first')
            record = {'id': identity, 'sessionId': sid, 'signature': signature, 'revision': 1, 'phase': 'pending', 'executionHost': local_host_identity(), 'source': session.get('workingDirectory') or session['workspace'], 'target': target, 'historyHome': session['workspace'], 'executionRevision': session.get('executionRevision', 0), 'createdAt': time.time(), 'inputsReplayed': False, 'origin': origin, 'detail': 'Saving and releasing the old runtime before changing its execution folder.', 'preserved': {'messageIds': [m['id'] for m in session['messages']], 'taskId': session.get('task', {}).get('id') if session.get('task') else None}, 'reconciles': previous['id'] if previous else None, 'evidence': args.get('evidence')}
            self.save(record)
            session['configurationBusy'] = True
            self.changed()
            job = asyncio.create_task(self.finish(record, copy.deepcopy(session)))
            self.jobs.add(job); job.add_done_callback(self.jobs.discard)
            return record

    async def finish(self, record, session):
        try:
            # The pending receipt is durable independently of bridge delivery.
            # Quiescence never waits on a model-held command lock.
            await asyncio.sleep(0)
            self.check_host(record)
            checked = await asyncio.to_thread(self.git.inspect, record['target'])
            home = await asyncio.to_thread(self.git.inspect, record['historyHome'])
            if checked['repository'] != home['repository']: raise ValueError('The target is no longer in the original repository')
            if not self.app.runtime or not hasattr(self.app.runtime, 'quiesce_for_handoff'): raise ValueError('This runtime cannot confirm safe handoff')
            evidence = await self.app.runtime.quiesce_for_handoff(session, record['id'])
            self.check_host(evidence)
            if not evidence.get('quiesced'): raise RuntimeError('Runtime release was not confirmed')
            async with self.app.lock:
                current = self.app._session(record['sessionId'])
                if current.get('executionRevision', 0) != record['executionRevision']: raise ValueError('The execution folder changed during handoff')
                current.update(workingDirectory=record['target'], executionHost=copy.deepcopy(record['executionHost']), executionRevision=record['executionRevision']+1, configurationBusy=False, status='idle')
                current['ownership'] = {'status': 'available'}
                current.pop('lockOwner', None)
                record.update(phase='applied', revision=2, updatedAt=time.time(), release=evidence, detail='Execution folder changed. Saved history remains in its original home; no input was sent.')
                self.save(record)
                if record.get('reconciles'):
                    previous = self.read(record['sessionId'], record['reconciles'])
                    previous.update(phase='reconciled', revision=previous['revision']+1, reconciliation=record['id'])
                    self.save(previous)
                self.changed()
        except BaseException as exc:
            async with self.app.lock:
                record.update(phase='unknown', revision=record['revision']+1, detail='Handoff did not reach a confirmed boundary. Inspect and reconcile; no input was replayed. ' + str(exc)[:1000])
                self.save(record); self.changed()
            if isinstance(exc, asyncio.CancelledError): raise

    async def close(self):
        for job in list(self.jobs): job.cancel()
        if self.jobs: await asyncio.gather(*self.jobs, return_exceptions=True)
