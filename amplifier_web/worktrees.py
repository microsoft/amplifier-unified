"""App policy over managed Git worktrees and explicit execution handoff."""
import asyncio
import copy
import json
from pathlib import Path
import time
import uuid

from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import atomic, common, digest
from .host_identity import local_host_identity, require_local_host


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    identity = {**sid, 'id': string(200)}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    repository = {'repositoryPath': {**string(4000), 'minLength': 1}}
    return {
        'worktree.inspect': ('Inspect the current execution repository or an explicit repositoryPath inside this task\'s workspace/execution folders. Non-Git workspaces can select a child repository. Relative paths use the execution folder. Keep the returned absolute repository root for later creation/attachment.', schema({**sid, **repository}, ['sessionId'])),
        'worktree.list': ('List checkouts associated with this task and durable handoff receipts.', schema(sid)),
        'worktree.status': ('Inspect a checkout, its original source manifest, conflicts and Git ownership.', schema(identity)),
        'worktree.create': ('Create a managed checkout from an exact inspected source revision. repositoryPath selects the source; carry the inspected absolute root for multi-repository work. Default clean committed HEAD; carry_dirty explicitly copies staged/unstaged/untracked evidence. Does not move execution or configure dependencies.', schema({**sid, **repository, 'sourceRevision': string(100), 'mode': {'enum': ['clean', 'carry_dirty']}, 'ref': string(500), 'branch': string(500)}, ['sessionId', 'sourceRevision'])),
        'worktree.attach': ('Associate an existing Git worktree from the selected repositoryPath (canonical home by default); does not copy history or start work. Attached folders cannot be deleted through managed cleanup.', schema({**sid, **repository, 'path': string(4000)}, ['sessionId', 'path'])),
        'worktree.archive': ('Retire an unused app-created checkout while retaining every file and its Git registration. Moves the whole checkout to private archive storage; does not reclaim disk space or create an immutable snapshot. Active or ambiguous ownership is refused.', schema({**identity, **revision})),
        'worktree.restore': ('Restore an archived checkout without sending input or moving conversation execution. Keeps dirty, ignored and unknown files. Refuses a destination collision.', schema({**identity, **revision})),
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
        self._creation_paths = {}
        self._moving_paths = ()
        self._uncertain_paths = ()
        for path in self.receipt_dir.glob('*.json'):
            record = json.loads(path.read_text())
            session = next((s for s in service.state['sessions'] if s['id'] == record['sessionId']), {})
            inconsistent = record['phase'] == 'applied' and (session.get('executionRevision', 0) <= record['executionRevision'] or (session.get('executionRevision', 0) == record['executionRevision'] + 1 and session.get('workingDirectory') != record['target']))
            if record['phase'] == 'pending' and record.get('step') == 'preflight':
                record.update(phase='rejected', revision=record['revision']+1,
                    detail='The app restarted before runtime release began. The source execution folder is unchanged.')
                session['configurationBusy'] = False
                self.save(record)
            elif record['phase'] == 'pending' or inconsistent:
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
                'fenced': any(row['phase'] in {'pending', 'unknown'} for row in session.get('worktreeHandoffs', [])) or
                    bool(getattr(self.app, 'portability', None) and self.app.portability.fenced(sid))}

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
        return {'id': 'worktree:' + row['id'], 'sessionId': row['sessionId'], 'source': 'worktree', 'kind': 'checkout-handoff', 'state': {'pending': 'running', 'applied': 'completed', 'reconciled': 'completed', 'rejected': 'failed', 'unknown': 'outcome_unknown'}[row['phase']], 'revision': row['revision'], 'createdAt': row['createdAt'], 'updatedAt': row.get('updatedAt', row['createdAt']), 'controlAvailable': False, 'evidence': copy.deepcopy(row)}

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

    def require_available_path(self, path):
        """Fence admission without scanning dormant histories or Git records."""
        roots = (*self._moving_paths, *self._uncertain_paths)
        if roots:
            resolved = Path(path).expanduser().resolve()
            if any(resolved.is_relative_to(root) for root in roots):
                raise ValueError('This checkout is being moved or its move is unresolved; inspect it before starting a chat')

    def reserve_creation(self, *paths):
        for path in paths:
            self.require_available_path(path)
        token = object()
        self._creation_paths[token] = tuple(Path(path).expanduser().resolve() for path in paths)
        return token

    def release_creation(self, token):
        self._creation_paths.pop(token, None)

    @staticmethod
    def _occupied(paths, roots):
        return any(Path(path).expanduser().resolve().is_relative_to(root)
                   for path in set(paths) for root in roots)

    @staticmethod
    async def finish_thread(function, *args, **kwargs):
        """Keep path reservations until a filesystem effect actually finishes."""
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                # Cancelling the waiter cannot stop its filesystem thread.
                cancelled = exc
        if cancelled is not None:
            task.exception()
            raise cancelled
        return task.result()

    def _refresh_uncertain_paths(self, records):
        self._uncertain_paths = tuple(Path(path).resolve() for record in records
            for operation in record.get('archiveOperations', {}).values()
            if operation['status'] in {'pending', 'unknown'}
            for path in (operation['source'], operation['target']))

    async def retire(self, action, record, args, identity):
        roots = [Path(record['path']).resolve()]
        if action != 'worktree.remove':
            roots.append((self.git.directory / ('checkouts' if action == 'worktree.restore' else 'archives') / record['id']).resolve())
        async with self.app.lock:
            if any(path.is_relative_to(root) for paths in self._creation_paths.values() for path in paths for root in roots):
                raise ValueError('A chat is being created in this checkout; wait for it to finish')
            # Claim before yielding. Session creation claims its path before
            # directory preparation and checks this fence again at admission.
            self._moving_paths = tuple(roots)
            sessions = self.app.state['sessions']
            occupied = [path for s in sessions for path in (s.get('workingDirectory'), s.get('workspace')) if path]
            handoffs = [path for s in sessions for r in s.get('worktreeHandoffs', [])
                        if r['phase'] in {'pending', 'unknown'} for path in (r['source'], r['target'])]
        try:
            # Resolve aliases off the event loop; this examines only placement
            # metadata, never the sessions' messages or event logs.
            if await asyncio.to_thread(self._occupied, occupied, roots):
                raise ValueError('A task still uses this checkout; hand it back first')
            if await asyncio.to_thread(self._occupied, handoffs, roots):
                raise ValueError('An unresolved handoff still refers to this checkout')
            if action == 'worktree.remove':
                return await self.finish_thread(self.git.remove, args['id'], args['expectedRevision'], identity)
            return await self.finish_thread(self.git.relocate, args['id'], args['expectedRevision'], identity,
                                            restore=action == 'worktree.restore')
        finally:
            # Retain a durable uncertainty fence across request cancellation,
            # failures and restart; inspection can reconcile it without replay.
            self._refresh_uncertain_paths(self.git.records())
            self._moving_paths = ()

    def repository_path(self, session, value=None, *, attaching=False):
        execution = Path(session.get('workingDirectory') or session['workspace']).resolve()
        if value is None:
            return str(Path(session['workspace']).resolve() if attaching else execution)
        candidate = (execution / value).resolve()
        allowed = [execution, Path(session['workspace']).resolve(), *[
            Path(row['path']).resolve() for row in self.git.records()
            if row.get('sessionId') == session['id'] and row.get('status') == 'ready']]
        if not any(candidate.is_relative_to(root) for root in allowed):
            raise ValueError('Choose a repository inside this task\'s workspace, execution folder or associated checkout.')
        root, _ = common(candidate)
        if not any(root.is_relative_to(boundary) for boundary in allowed):
            raise ValueError('The Git root lies outside the selected task folders.')
        return str(root)

    @staticmethod
    def check_host(record):
        host = record.get('executionHost')
        if host:
            if host.get('scope') != 'local':
                raise ValueError('Cross-host execution is unavailable.')
            require_local_host(host.get('id'))

    def sync(self):
        records = self.git.records()
        self._refresh_uncertain_paths(records)
        handoffs = [json.loads(path.read_text()) for path in sorted(self.receipt_dir.glob('*.json'))]
        by_session, by_handoff, unresolved = {}, {}, set()
        for record in records:
            by_session.setdefault(record.get('sessionId'), []).append(record)
        for receipt in handoffs:
            sid = receipt['sessionId']
            by_handoff.setdefault(sid, []).append(receipt)
            if receipt['phase'] in {'pending', 'unknown'}:
                unresolved.add(sid)
        groups = (('worktrees', by_session), ('worktreeHandoffs', by_handoff))
        index = self.app.projections.sessions(self.app.state)
        owners = set(by_session) | set(by_handoff) | getattr(self, '_projected_ids', set())
        # First reconciliation clears stale saved projections too.
        if not hasattr(self, '_projected_ids'):
            owners.update(row['id'] for row in self.app.state['sessions'] if row.get('worktrees') or row.get('worktreeHandoffs'))
        self._projected_ids = set(by_session) | set(by_handoff)
        for sid in owners:
            session = index.by_id.get(sid)
            if session is None: continue
            for field, grouped in groups:
                if sid in grouped:
                    session[field] = list(grouped[sid])
                elif not isinstance(session.get(field), list) or session[field]:
                    session[field] = []
            # Only assert this fence: another configuration owner may still
            # be busy after a handoff resolves. Never clear its state here.
            if sid in unresolved:
                session['configurationBusy'] = True
        return owners

    def changed(self, session_ids=()):
        owners = self.sync() | set(session_ids)
        self.app._publish_changes(sessions=owners)
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
            if action == 'worktree.inspect':
                source = await asyncio.to_thread(self.repository_path, session, args.get('repositoryPath'))
                result['repository'] = await asyncio.to_thread(self.git.inspect, source)
                result['repositorySelection'] = args.get('repositoryPath', '')
            async with self.app.lock:
                session['worktreeInspection'] = result
                self.changed({sid})
            return result
        if action == 'worktree.status':
            self.record(sid, args['id'])
            result = await asyncio.to_thread(self.git.status, args['id'])
            async with self.app.lock:
                session['worktreeStatus'] = result; self.changed({sid})
            return result
        self.check_host(session)
        identity = command_id or str(uuid.uuid4())
        if action in {'worktree.handoff', 'worktree.reconcile'}:
            return await self.handoff(action, args, origin, identity)
        if action == 'worktree.create':
            source = await asyncio.to_thread(self.repository_path, session, args.get('repositoryPath'))
            result = await asyncio.to_thread(self.git.create, source, command_id=identity, expected_revision=args['sourceRevision'], mode=args.get('mode', 'clean'), ref=args.get('ref', 'HEAD'), branch=args.get('branch'), session_id=sid)
        elif action == 'worktree.attach':
            source = await asyncio.to_thread(self.repository_path, session, args.get('repositoryPath'), attaching=True)
            result = await asyncio.to_thread(self.git.attach, args['path'], source=source, command_id=identity, session_id=sid)
        elif action in {'worktree.remove', 'worktree.archive', 'worktree.restore'}:
            record = self.record(sid, args['id'])
            self.check_host(record)
            result = await self.retire(action, record, args, identity)
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
            record['step'] = 'preflight'
            self.save(record)
            session['configurationBusy'] = True
            self.changed()
            job = asyncio.create_task(self.finish(record, copy.deepcopy(session)))
            self.jobs.add(job); job.add_done_callback(self.jobs.discard)
            return record

    async def finish(self, record, session):
        try:
            record['step'] = 'preflight'
            self.save(record)
            # The pending receipt is durable independently of bridge delivery.
            # Quiescence never waits on a model-held command lock.
            await asyncio.sleep(0)
            self.check_host(record)
            checked = await asyncio.to_thread(self.git.inspect, record['target'])
            home = await asyncio.to_thread(self.git.inspect, record['historyHome'])
            if checked['repository'] != home['repository']: raise ValueError('The target is no longer in the original repository')
            if not self.app.runtime or not hasattr(self.app.runtime, 'quiesce_for_handoff'): raise ValueError('This runtime cannot confirm safe handoff')
            # Persist before crossing the effect boundary. A lost release
            # response is uncertain; a failed read-only preflight is not.
            record['step'] = 'releasing'
            self.save(record)
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
                if record.get('step') == 'preflight':
                    record.update(phase='rejected', revision=record['revision']+1,
                        detail='Handoff was rejected before runtime release. The source execution folder is unchanged. ' + str(exc)[:1000])
                    self.app._session(record['sessionId'])['configurationBusy'] = False
                else:
                    record.update(phase='unknown', revision=record['revision']+1, detail='Handoff did not reach a confirmed boundary. Inspect and reconcile; no input was replayed. ' + str(exc)[:1000])
                self.save(record); self.changed()
            if isinstance(exc, asyncio.CancelledError): raise

    async def close(self):
        for job in list(self.jobs): job.cancel()
        if self.jobs: await asyncio.gather(*self.jobs, return_exceptions=True)
