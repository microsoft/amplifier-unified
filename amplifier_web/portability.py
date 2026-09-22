"""Shared UI/agent staged task transfer; destination execution is always explicit."""
import asyncio
import copy
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time
import uuid

from amplifier_portability import TransferNode
from amplifier_portability.protocol import confirm_durable, durable_directory
from amplifier_portability.capsule import capture_workspace, read_capsule, restore_workspace, write_capsule, _local_path
from amplifier_worktrees.git import digest
from . import portability_data as data
from .host_identity import local_host_identity


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    receipt = {**sid, 'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1}}
    return {
        'portability.inspect': ('Inspect this host identity, paired destinations and durable transfer receipts. No task or provider starts.', schema(sid, [])),
        'portability.export': ('Stage this task for a paired host after reviewed content approval. Fence and save its writer; export canonical history, output lineage and explicit configuration intent with bounded Git changes. No secrets/settings or work replay. Pending/unknown transfers remain fenced.', schema({**sid, 'destination': string(64), 'sourceRevision': string(64), 'expectedExecutionRevision': {'type': 'integer', 'minimum': 0}, 'mode': {'enum': ['clean', 'carry_dirty']}, 'reviewedContent': {'const': True}})),
        'portability.stage': ('Verify a signed task package from a paired host, stage a fresh checkout against an already provisioned exact repository commit, and probe destination-owned provider/account/runtime. Returns a signed readiness file; cannot start the task.', schema({'path': string(4000), 'repository': string(4000)})),
        'portability.release': ('Commit irreversible source ownership release against the destination signed readiness receipt. Preserve source history in a private archive. Source stays fenced if acknowledgement is lost; no automatic rollback.', schema({**receipt, 'path': string(4000)})),
        'portability.activate': ('Activate the staged task only with its exact signed source release. Retain task/native/output identities; never resume input, schedules or uncertain effects automatically.', schema({**receipt, 'path': string(4000)})),
        'portability.cancel': ('Deliberately cancel an unreleased source transfer and retain its evidence. Impossible once release has begun. This restores source admission only; no input or uncertain effect is replayed.', schema({**receipt, 'evidence': {**string(4000), 'minLength': 1}})),
        'portability.discard': ('Retain a signed source cancellation for an unactivated destination stage. Leaves files and native fence intact; cannot execute the staged task or undo a released owner.', schema({**receipt, 'path': string(4000)})),
        'portability.evidence': ('Read retained transfer evidence as bounded JSON text without executing, replaying or reclassifying unknown work.', schema({**sid, 'id': string(100), 'section': {'enum': ['operations', 'operationRequests', 'liveJobs', 'workers', 'approvals', 'questions', 'schedules', 'scheduleRuns']}, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 4000}}, ['sessionId', 'id', 'section'])),
    }


def native_store(session):
    from amplifier_foundation.session import SharedSessionStore
    if not hasattr(SharedSessionStore, 'confirm_transfer_commit'):
        raise ValueError('Install Foundation with the durable transfer fence on every participating runtime first')
    return SharedSessionStore(session['workspace'], session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'])


def retained_native_fence(session):
    """A retained marker denies writes even when runtime admission is unavailable."""
    from .automatic_history import directory
    from .session_files import project_slug
    projects = {session['nativeProject']} if session.get('nativeProject') else set()
    if session.get('workspace'):
        projects.add(project_slug(session['workspace']))
    identities = {session.get('nativeIdentity'), session.get('runtimeSessionId')} - {None, ''}
    if not identities:
        identities.add(session['id'])
    # Both anchors matter when a history row and its runtime alias differ.
    for project in projects:
        for identity in identities:
            marker = directory({**session, 'nativeProject': project, 'nativeIdentity': identity}) / 'transfer-fence.json'
            try:
                marker.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ValueError('Cannot inspect this task\'s transfer fence.') from exc
            # Contents, permissions or a dangling marker link cannot grant
            # admission. Foundation validates evidence during actual transfer.
            return True
    return False


class Portability:
    def __init__(self, app):
        self.app = app
        self.node = TransferNode(app.data_dir / 'portability', local_host_identity()['label'])
        self.node.recover()
        self.jobs = set()
        self.mutations = asyncio.Lock()

    def fenced(self, sid):
        if self.node.fenced(sid):
            return True
        session = next((row for row in self.app.state['sessions'] if row['id'] == sid), None)
        if session:
            workspace = session.get('workspace')
            if session.get('nativeProject') or not workspace or not Path(workspace).expanduser().is_dir():
                # Indexed history remains addressable without an existing
                # workspace, including legacy worker IDs a runtime cannot use.
                # Marker presence only denies writes; never infer permission
                # from unreadable or malformed transfer evidence.
                if retained_native_fence(session):
                    return True
                if not workspace or not Path(workspace).expanduser().is_dir():
                    return False
            from amplifier_foundation.session import SharedSessionStore
            if hasattr(SharedSessionStore, 'transfer_fence'):
                try:
                    store = SharedSessionStore(workspace, session.get('runtimeSessionId') or session.get('nativeIdentity') or sid)
                except ValueError:
                    if session.get('nativeProject'):
                        # Legacy history IDs and disappeared workspaces cannot
                        # construct a runtime store; known markers were checked.
                        return False
                    raise
                return store.transfer_fence() is not None
        return False

    def write_context(self, sid):
        """Capture without yielding; compare under the app lock before writing."""
        if self.fenced(sid):
            raise ValueError('This task is fenced for transfer; it cannot change or start work here.')
        rows = self.node.records(sid)
        return max(rows, key=lambda row: (row['generation'], row['createdAt']))['id'] if rows else None

    def require_voice_closed(self, sid):
        voice = self.app.state.get('voice', {})
        manager = getattr(self.app, 'voice_service', None)
        call = getattr(manager, 'call', None)
        live_call = (call and call.session_id == sid and
            (not call.closed or (getattr(call, 'closing', False) and not getattr(call, 'close_result', None))))
        if live_call or (voice.get('sessionId') == sid and voice.get('status') not in {None, 'disconnected', 'idle', 'ended', 'error', 'failed'}):
            raise ValueError('End the active voice call before moving the task')

    def sync(self):
        self.app.state['portability'] = {'host': self.node.identity, 'receipts': self.node.records()}
        for session in self.app.state['sessions']:
            rows = self.node.records(session['id'])
            if rows:
                session['portability'] = {'host': self.node.identity, 'receipts': rows, 'fenced': self.fenced(session['id'])}
                if self.fenced(session['id']):
                    session['configurationBusy'] = True

    def changed(self):
        self.sync()
        self.app._publish()

    def review_schedules(self, sid):
        for schedule in self.app.schedules.store.list(sid):
            self.app.schedules.store.review(sid, schedule['id'],
                'Task transfer requires explicit schedule review before any future execution.', time.time())

    def read(self, sid, identity):
        row = self.node.get(identity)
        if row['sessionId'] != sid:
            raise ValueError('This transfer belongs to another task')
        return row

    @asynccontextmanager
    async def publishing_guard(self, sid):
        publishing = getattr(self.app, 'publishing', None)
        if publishing is None:
            yield
            return
        # Match publishing/backup/deletion lock order: publishing, then app.
        async with publishing.lock:
            if publishing.owns_records(sid):
                raise ValueError('This task owns retained publishing state on its current host. Transfer of publishing controls is not supported; keep managing publishing from the source task.')
            yield

    async def dispatch(self, action, args, origin, command_id):
        if action == 'portability.evidence':
            row = self.read(args['sessionId'], args['id'])
            path = row.get('package') or row['destinationState']['package']
            body = read_capsule(Path(path))['body']
            from amplifier_portability.protocol import encoded
            if digest(encoded(body)) != row['capsuleHash']:
                raise ValueError('The transfer evidence differs from its signed receipt')
            contents = json.dumps(body['payload']['observations'][args['section']], ensure_ascii=False)
            offset, limit = args.get('offset', 0), args.get('limit', 4000)
            return {'text': contents[offset:offset+limit], 'nextOffset': offset+limit if offset+limit < len(contents) else None,
                    'historicalOnly': True, 'inputsReplayed': False}
        if action == 'portability.inspect':
            sid = args.get('sessionId')
            if sid: self.app._session(sid)
            result = {'host': self.node.identity, 'peers': self.node.peers(), 'receipts': self.node.records(sid)}
            async with self.app.lock:
                if sid: self.app._session(sid)['portabilityInspection'] = result
                self.changed()
            return result
        async with self.mutations:
            if action == 'portability.export':
                async with self.publishing_guard(args['sessionId']):
                    return await self.export(args, command_id or str(uuid.uuid4()))
            if action == 'portability.stage':
                return await self.stage(args)
            if action == 'portability.release':
                async with self.publishing_guard(args['sessionId']):
                    return await self.release(args)
            if action == 'portability.activate':
                return await self.activate(args)
            if action == 'portability.cancel':
                return await self.cancel(args)
            if action == 'portability.discard':
                row = self.read(args['sessionId'], args['id'])
                result = self.node.discard(row['id'], read_capsule(Path(args['path'])), args['expectedRevision'])
                async with self.app.lock: self.changed()
                return result
            raise ValueError('Unknown portability action')

    async def export(self, args, command_id):
        session = self.app._session(args['sessionId'])
        native_store(session)  # Capability preflight before changing admission.
        if session.get('executionRevision', 0) != args['expectedExecutionRevision']:
            raise ValueError('The execution association changed; inspect it again')
        async with self.app.lock:
            self.require_voice_closed(session['id'])
        if session.get('configurationBusy') and not self.node.records(session['id']):
            raise ValueError('Another task configuration change is unresolved')
        # Reject known nonportable contents before stopping a writer. A second
        # exact capture after release remains authoritative for a running task.
        if not self.node.records(session['id']) or not self.fenced(session['id']):
            await self.app.history.ensure_loaded(session['id'])
            await asyncio.to_thread(capture_workspace, session.get('workingDirectory') or session['workspace'], args['sourceRevision'], args['mode'])
            async with self.app.lock:
                data.capture(self.app, session)
        async with self.app.lock:
            # Voice connect claims state.voice under this same lock. Recheck
            # after the awaited history/Git preflight, then fence atomically.
            self.require_voice_closed(session['id'])
            row = self.node.begin(session['id'], args['destination'], command_id, args)
            if row.get('duplicate'):
                return row
            session['configurationBusy'] = True
            self.review_schedules(session['id'])
            self.changed()
        job = asyncio.create_task(self.finish_export(row, copy.deepcopy(session), args))
        self.jobs.add(job); job.add_done_callback(self.jobs.discard)
        return row

    async def finish_export(self, row, session, args):
        try:
            await asyncio.sleep(0)
            if not self.app.runtime:
                raise ValueError('A runtime writer release is required')
            evidence = await self.app.runtime.quiesce_for_handoff(session, row['id'], transfer_destination=row['destination'])
            if not evidence.get('quiesced') or not evidence.get('transferFenced'):
                raise ValueError('The native writer did not confirm a durable transfer fence')
            # Quiescence stops producers, but cancelled bridge callers can leave
            # shielded journal writes queued. Include their evidence before the
            # snapshot; timeout/failure keeps this transfer unknown and fenced.
            pending = tuple(self.app.operations.pending)
            if pending:
                await asyncio.wait_for(asyncio.gather(*(asyncio.shield(task) for task in pending)), 30)
            await self.app.history.ensure_loaded(session['id'])
            workspace = await asyncio.to_thread(capture_workspace, session.get('workingDirectory') or session['workspace'], args['sourceRevision'], args['mode'])
            async with self.app.lock:
                current = self.app._session(session['id'])
                payload = data.capture(self.app, current)
                payload['workspace'] = workspace
                result = self.node.prepared(row['id'], payload)
                result['releaseEvidence'] = {key: evidence.get(key) for key in ('nativeSessionId', 'quiesced', 'transferFenced', 'inputsReplayed')}
                self.node.save(result)
                self.changed()
        except BaseException:
            async with self.app.lock:
                self.node.unknown(row['id']); self.changed()
            raise_if_cancelled()

    async def destination_checks(self, payload, workspace):
        # The probe runs from the destination repository's settings scope. No
        # transferred source overrides, settings or credentials are mounted.
        from .setup import SetupManager
        manager = SetupManager(self.app.data_dir)
        intent = payload['intent']; selection = intent['selection']
        provider = selection.get('instance') or selection.get('provider')
        config = manager.config(workspace)
        if config.active_bundle != intent['bundle']:
            raise ValueError('Destination bundle differs from saved configuration intent; configure it locally first')
        row = next((r for r in config.providers if (r.get('id') or r.get('instance_id') or r['module'].removeprefix('provider-')) == provider), None)
        if row is None:
            raise ValueError('Configure the saved provider instance on the destination first')
        from .setup import environment_credential
        from .runtime import RuntimeManager
        raw = copy.deepcopy(row.get('config', {}))
        credential = environment_credential(row['module'], raw)
        if not raw.get(credential['field']) and credential['available']:
            raw[credential['field']] = '${' + credential['envVar'] + '}'
        request = {'module': row['module'], 'config': raw, 'model': selection['model'],
                   'source': config.module_sources.get(row['module']) or row.get('source'), 'registryHome': str(config.registry_home)}
        source = request['source']
        if source and not source.startswith('git+https://'):
            candidate = Path(source.removeprefix('file://')).expanduser()
            if not candidate.is_absolute() or candidate.resolve().is_relative_to(Path(workspace).resolve()):
                raise ValueError('The destination provider must use installed code or an independent destination-owned source')
        probe_directory = self.node.directory / 'probe'
        probe_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = RuntimeManager()._command()[:-1] + [str(Path(__file__).with_name('portability_probe.py'))]
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=probe_directory,
            env={**os.environ, 'AMPLIFIER_WEB_HOME': str(self.app.data_dir)}, start_new_session=True)
        try:
            output, _ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), 90)
            result = json.loads(output)
            if process.returncode or result.get('error') or not result.get('accountVerified') or not result.get('runtimeTransferFence'):
                raise ValueError('Destination provider access or native runtime verification failed; inspect local configuration')
        finally:
            if process.returncode is None:
                import signal
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                await process.wait()
        return {'runtimeVerified': True, 'accountVerified': True, 'nativeFenceVerified': True,
                'credentialsOrigin': 'destination', 'method': result['method'],
                'accountVerificationScope': result['accountVerificationScope'],
                'provider': provider, 'model': selection['model'], 'intentHash': digest(intent), 'checkedAt': time.time()}

    async def stage(self, args):
        envelope = read_capsule(Path(args['path']))
        body = self.node.verify(envelope, kind='capsule')
        payload = body['payload']; data.validate(payload)
        if body['sessionId'] != payload['session']['id']:
            raise ValueError('Task identity does not match its package')
        # A retry is a receipt read, even though native staging already exists.
        if self.node.path(body['id']).exists():
            return self.node.receive(envelope, args)
        existing = next((s for s in self.app.state['sessions'] if s['id'] == body['sessionId']), None)
        if existing and not self.node.fenced(body['sessionId']):
            raise ValueError('This destination already has a writable task with that identity')
        from .session_files import amplifier_home
        identity = payload['nativeIdentity']
        if any(path for name in ('transcript.jsonl', 'transcript.jsonl.backup')
               for path in (amplifier_home() / 'projects').glob('*/sessions/' + identity + '/' + name)):
            raise ValueError('This native task already has canonical history on the destination')
        row = self.node.receive(envelope, args)
        if row.get('duplicate'):
            return row
        try:
            target = self.node.directory / 'checkouts' / row['id']
            restored = await asyncio.to_thread(restore_workspace, payload['workspace'], args['repository'], target)
            session = {**copy.deepcopy(payload['session']), 'workspace': str(target), 'workingDirectory': str(target),
                       'runtimeSessionId': identity, 'nativeIdentity': identity}
            store = native_store(session)
            # Staging must also fence standalone native consumers before any
            # transcript becomes discoverable on this host.
            held = store.acquire(app='task-transfer-stage')
            try:
                held.fence_transfer(row['id'], self.node.identity['id'], role='destination')
            finally:
                held.release()
            # History stays solely in the private signed package until source
            # release. Native history catalogs must not discover a staged copy.
            package = self.node.directory / 'packages' / (row['id'] + '.json')
            write_capsule(package, envelope)
            checks = await self.destination_checks(payload, target)
            result = self.node.ready(row['id'], {'workspace': str(target), 'nativeIdentity': identity, 'package': str(package), 'checkout': restored}, checks)
            ready_path = self.node.directory / 'exchange' / (row['id'] + '.ready.json')
            write_capsule(ready_path, result['readyReceipt'])
            result['receiptPath'] = str(ready_path)
            self.node.save(result)
            async with self.app.lock:
                self.changed()
            return result
        except BaseException:
            self.node.unknown(row['id'])
            raise

    async def release(self, args):
        row = self.read(args['sessionId'], args['id'])
        ready = read_capsule(Path(args['path']))
        body = self.node.verify(ready, kind='ready', signer=row['destination'])
        self.node.match(row, body)
        if row['phase'] != 'released':
            session = self.app._session(row['sessionId'])
            package = read_capsule(Path(row['package']))
            from amplifier_portability.protocol import encoded
            if digest(encoded(package['body'])) != row['capsuleHash']:
                raise ValueError('The staged source package differs from its receipt')
            payload = package['body']['payload']
            if row['phase'] == 'prepared':
                async with self.app.lock:
                    # UI edits and local file changes after staging must not
                    # disappear at the irreversible ownership boundary.
                    current = data.capture(self.app, session)
                    if digest(current) != digest({key: value for key, value in payload.items() if key != 'workspace'}):
                        raise ValueError('Task history or outputs changed after export; keep both hosts fenced and inspect')
                    capture_workspace(session.get('workingDirectory') or session['workspace'], payload['workspace']['sourceRevision'], payload['workspace']['mode'])
            archive = self.node.directory / 'archives' / row['id']
            row = self.node.releasing(row['id'], ready, args['expectedRevision'], archive)
            # Source is irreversibly fenced in Foundation before its signed
            # release leaves this host. Originals leave native discovery but
            # remain private, byte-identical archival evidence.
            store = native_store(session)
            fence = store.transfer_fence()
            if not fence or fence['transfer_id'] != row['id'] or fence['role'] != 'source':
                raise ValueError('Source native fence no longer matches this release')
            if fence['phase'] != 'committed':
                held = store.acquire_transfer(row['id'], app='task-transfer-release')
                try:
                    held.commit_transfer()
                finally:
                    held.release()
            else:
                store.confirm_transfer_commit(row['id'], app='task-transfer-release')
            source = data.native_directory(self.app, session)
            durable_directory(archive)
            try:
                for name, value in payload['native'].items():
                    path = _local_path(source / name)
                    destination = _local_path(archive / name)
                    expected = data.decode_file(value)
                    if destination.exists():
                        if destination.read_bytes() != expected or path.exists():
                            raise ValueError('Source archive conflicts with the reviewed transfer')
                        confirm_durable(destination)
                        continue
                    if path.read_bytes() != expected:
                        raise ValueError('Source native history changed before archival')
                    durable_directory(destination.parent)
                    os.replace(path, destination)
                    for parent in (path.parent, destination.parent):
                        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                        try: os.fsync(fd)
                        finally: os.close(fd)
            except BaseException:
                self.node.unknown(row['id'])
                raise
        else:
            native_store(self.app._session(row['sessionId'])).confirm_transfer_commit(row['id'], app='task-transfer-release')
        result = self.node.release(row['id'], ready, row['revision'])
        path = self.node.directory / 'exchange' / (row['id'] + '.release.json')
        write_capsule(path, result['releaseCertificate'])
        result['receiptPath'] = str(path)
        self.node.save(result)
        async with self.app.lock:
            session = self.app._session(row['sessionId'])
            session.update(status='idle', deferRuntimeUntilInteraction=True, configurationBusy=True)
            self.changed()
        return result

    async def activate(self, args):
        row = self.read(args['sessionId'], args['id'])
        certificate = read_capsule(Path(args['path']))
        authorization = self.node.verify(certificate, kind='release', signer=row['source'])
        self.node.match(row, authorization)
        if authorization['readyHash'] != digest(row['readyReceipt']):
            raise ValueError('Source release does not acknowledge this exact destination readiness')
        if row['phase'] == 'unknown' and row.get('previousPhase') == 'activating':
            # Activation and its unknown outcome each advance the ready
            # revision once. Only the exact saved attempt can be acknowledged;
            # an uncertain provider request must never be submitted again.
            if row.get('releaseCertificate') != certificate or args['expectedRevision'] != row['revision'] - 2:
                raise ValueError('This activation attempt has different contents; inspect its uncertain receipt')
            return {**row, 'duplicate': True}
        # Recheck destination credentials/runtime immediately before ownership
        # admission. Import readiness is not a permanent account guarantee.
        destination = row['destinationState']
        envelope = read_capsule(Path(destination['package']))
        body = self.node.verify(envelope, kind='capsule', signer=row['source'])
        from amplifier_portability.protocol import encoded
        if digest(encoded(body)) != row['capsuleHash']:
            raise ValueError('The staged task package changed after readiness')
        payload = body['payload']; data.validate(payload)
        if row['phase'] == 'active':
            result = self.node.activating(row['id'], certificate, args['expectedRevision'])
            session = self.app._session(row['sessionId'])
            self.finish_native_activation(row, session)
            async with self.app.lock:
                session['configurationBusy'] = False; self.changed()
            return result
        if row['phase'] != 'ready' or row['revision'] != args['expectedRevision']:
            raise ValueError('Inspect the current ready transfer before activation')
        capture_workspace(destination['workspace'], payload['workspace']['sourceRevision'], payload['workspace']['mode'])
        async with self.app.lock:
            data.preflight_install(self.app, payload)
        # Record the attempt before a provider probe can have external effects.
        # A crash here recovers as unknown, never as another ready admission.
        row = self.node.activating(row['id'], certificate, args['expectedRevision'])
        try:
            await self.destination_checks(payload, Path(destination['workspace']))
            capture_workspace(destination['workspace'], payload['workspace']['sourceRevision'], payload['workspace']['mode'])
            async with self.app.lock:
                data.preflight_install(self.app, payload)
            from .session_files import project_slug
            previous = next((s for s in self.app.state['sessions'] if s['id'] == row['sessionId']), {})
            session = {**copy.deepcopy(payload['session']), 'workspace': destination['workspace'],
                       'workingDirectory': destination['workspace'], 'runtimeSessionId': payload['nativeIdentity'],
                       'nativeIdentity': payload['nativeIdentity'], 'nativeProject': project_slug(destination['workspace']),
                       'executionRevision': max(payload['session'].get('executionRevision', 0), previous.get('executionRevision', 0)) + 1,
                       'executionHost': local_host_identity(),
                       'historyManaged': False, 'historyLoaded': True, 'shared': True, 'status': 'idle',
                       'workers': [], 'approvals': [], 'configurationBusy': True, 'deferRuntimeUntilInteraction': True,
                       'ownership': {'status': 'available'}, 'portabilityOrigin': payload['origin'],
                       'portabilityEvidence': {'transferId': row['id'], 'historicalOnly': True,
                           'counts': {key: len(records) for key, records in payload['observations'].items()}}}
            native = data.native_directory(self.app, session)
            for name, value in payload['native'].items():
                path = native / name
                if path.exists() or path.is_symlink():
                    raise ValueError('Native destination history changed after staging; originals were preserved')
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                raw = data.decode_file(value)
                if name in {'metadata.json', 'metadata.json.backup'}:
                    metadata = json.loads(raw)
                    metadata.update(working_dir=destination['workspace'], portabilityOrigin=payload['origin'])
                    raw = json.dumps(metadata, ensure_ascii=False).encode()
                write_private_bytes(path, raw)
            local = _local_path(self.app.data_dir / 'sessions' / payload['nativeIdentity'])
            # A returning task must not silently remount stale local session
            # overrides. Retain them locally; only reviewed portable controls
            # are restored over the destination's own global/project settings.
            if local.exists():
                previous_local = _local_path(self.node.directory / 'archives' / row['id'] / 'previous-local-state')
                durable_directory(previous_local.parent)
                if previous_local.exists():
                    raise ValueError('A prior local-state archive already exists; inspect activation before continuing')
                os.replace(local, previous_local)
                durable_directory(local.parent)
                durable_directory(previous_local.parent)
            durable_directory(local)
            controls = copy.deepcopy(payload['intent']['controls'])
            controls['selection'] = copy.deepcopy(payload['intent']['selection'])
            write_private_bytes(local / 'control-state.json', json.dumps(controls).encode())
            if payload.get('contextCheckpoint'):
                write_private_bytes(local / 'context-checkpoint.json', data.decode_file(payload['contextCheckpoint']))
            async with self.app.lock:
                self.review_schedules(session['id'])
                data.install_outputs(self.app, payload)
                data.install_receipts(self.app, payload)
                self.app.state['sessions'] = [s for s in self.app.state['sessions'] if s['id'] != session['id']] + [session]
                self.app.state['canvasArtifacts'] = [r for r in self.app.state.get('canvasArtifacts', []) if r.get('sessionId') != session['id']] + payload['canvas']
                self.changed()
            # Commit destination authority while the native fence still blocks
            # all execution. An explicit retry may finish only this final clear.
            result = self.node.active(row['id'])
            self.finish_native_activation(row, session)
            async with self.app.lock:
                session['configurationBusy'] = False
                self.changed()
            return result
        except BaseException:
            self.node.unknown(row['id'])
            raise

    def finish_native_activation(self, row, session):
        store = native_store(session)
        fence = store.transfer_fence()
        if fence is None:
            return  # Exact committed activation retry; never re-install history.
        if fence['role'] != 'destination' or fence['transfer_id'] != row['id']:
            raise ValueError('Destination native fence differs from the admitted transfer')
        held = store.acquire_transfer(row['id'], app='task-transfer-activate')
        try:
            held.clear_transfer()
        finally:
            held.release()

    async def cancel(self, args):
        row = self.read(args['sessionId'], args['id'])
        session = self.app._session(row['sessionId'])
        store = native_store(session)
        fence = store.transfer_fence()
        if fence and (fence['transfer_id'] != row['id'] or fence['role'] != 'source' or fence['phase'] != 'staged'):
            raise ValueError('The native source fence cannot be cancelled')
        result = self.node.cancel(row['id'], args['expectedRevision'], args['evidence'])
        if fence:
            held = store.acquire_transfer(row['id'], app='task-transfer-cancel')
            try: held.clear_transfer()
            finally: held.release()
        path = self.node.directory / 'exchange' / (row['id'] + '.cancel.json')
        write_capsule(path, result['cancelCertificate'])
        result['receiptPath'] = str(path); self.node.save(result)
        async with self.app.lock:
            session.update(configurationBusy=False, status='idle', deferRuntimeUntilInteraction=True)
            self.changed()
        return result

    async def close(self):
        for job in list(self.jobs):
            job.cancel()
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)


def raise_if_cancelled():
    import sys
    if isinstance(sys.exception(), asyncio.CancelledError):
        raise sys.exception()


def write_private_bytes(path, raw):
    from .host.storage import SessionStore
    path = _local_path(path)
    durable_directory(path.parent)
    SessionStore._atomic(path, raw.decode('utf-8'))
    confirm_durable(path)
