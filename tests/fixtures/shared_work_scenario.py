"""Fixed synthetic Work data for the production-host browser fixture.

No general write endpoint, provider call, fabricated verification receipt, or
private history import. This module is enabled only by --shared-work.
"""
import asyncio
import contextvars
import hashlib
import json
import os
import time
from types import SimpleNamespace

from aiohttp import web


async def install(app, runtime, *, manifest_path=None):
    service = app['service']
    calls = []
    callback_counts = {'start': 0, 'send': 0, 'prewarm': 0}

    async def forbidden_start(*args, **kwargs):
        calls.append('start')
        callback_counts['start'] += 1
        raise AssertionError('Work inspection must not start a runtime')

    async def forbidden_send(*args, **kwargs):
        calls.append('send')
        callback_counts['send'] += 1
        raise AssertionError('Work inspection must not send model input')

    async def forbidden_prewarm(*args, **kwargs):
        calls.append('prewarm')
        callback_counts['prewarm'] += 1
        raise AssertionError('Work inspection must not prewarm a runtime')

    # SessionWarmup.schedule silently returns when prewarm is absent. Supply a
    # counted failure trap, not a worker emulator, before any scenario creation.
    runtime.start = forbidden_start
    runtime.send = forbidden_send
    runtime.prewarm = forbidden_prewarm
    cold = bool(manifest_path and manifest_path.exists())
    if cold:
        manifest = json.loads(manifest_path.read_text())
        # No replay of seed commands or source capture on a cold process.
        actual = service.workspace_work.summary(service.workspace_work.load(
            manifest['target']['id'], manifest['workspace']))
        assert actual['revision'] == manifest['target']['revision']
        assert actual['promiseRevision'] == manifest['target']['promiseRevision']
    else:
        manifest = await seed(service)
        if manifest_path:
            manifest_path.write_text(json.dumps(manifest))
    active_read = contextvars.ContextVar('shared_work_fixture_read', default=False)
    cold_calls = []
    task_reads, task_controls = [], []
    if os.environ.get('AMPLIFIER_TEST_COLD_TASK') == '1':
        assert manifest.get('coldTask'), 'Cold task seed must exist before reopening'
        original_dispatch = service.dispatch
        async def observed_task(action, *args, **kwargs):
            if action != 'task.get':
                return await original_dispatch(action, *args, **kwargs)
            token = active_read.set(True)
            try:
                response = await original_dispatch(action, *args, **kwargs)
                value = response.get('result', {})
                task_reads.append({'action': action, 'accepted': response.get('accepted'),
                    'status': value.get('status'), 'revision': (value.get('task') or {}).get('revision'),
                    'broadStateReturned': bool(response.get('state'))})
                return response
            finally:
                active_read.reset(token)
        async def forbidden_control(*args, **kwargs):
            task_controls.append('runtime.control')
            raise AssertionError('Cold saved-task inspection must not control a worker')
        service.dispatch = observed_task
        runtime.control = forbidden_control
    if cold:
        # Trap hydration caused by a Work read, without pretending that the
        # ordinary app's chat display is itself forbidden to hydrate history.
        for target, name in [(service.history, 'ensure_loaded'), (service.cold_display, 'hydrate'),
                             (service.management, 'ensure_runtime')]:
            if target is None:
                continue
            original = getattr(target, name)
            def guarded(*args, _original=original, _name=name, **kwargs):
                if active_read.get():
                    cold_calls.append(_name)
                    raise AssertionError('Cold Work read hydrated or started a worker: ' + _name)
                # Preserve each original call shape: hydrate/ensure_runtime
                # are synchronous, while ensure_loaded returns an awaitable.
                return _original(*args, **kwargs)
            setattr(target, name, guarded)

    async def inspect(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        return web.json_response({**manifest, 'coldReopen': cold, 'coldReadCalls': cold_calls,
            'forbiddenRuntimeCalls': list(calls), 'runtimeCallbackCounts': dict(callback_counts),
            'taskReadObservations': task_reads, 'taskControlCalls': task_controls,
            'pendingWarmups': len(service.warmup.pending)})
    app.router.add_get('/fixture/shared-work', inspect)

    # Observe the production wait without changing its timeout, cancellation or
    # result. stdout records survive the HTTP connection and server shutdown.
    dispatch = service.workspace_work.dispatch
    sequence = 0
    async def observed_dispatch(action, args, origin, command_id, caller):
        nonlocal sequence
        token = active_read.set(True)
        try:
            if action != 'workspace.work.wait':
                return await dispatch(action, args, origin, command_id, caller)
            sequence += 1
            identity = sequence
            def emit(phase, **fields):
                print(json.dumps({'workWait': {'id': identity, 'phase': phase,
                    'at': time.monotonic(), 'waitMs': args.get('waitMs', 30000), **fields}}), flush=True)
            emit('entered')
            try:
                response = await dispatch(action, args, origin, command_id, caller)
            except asyncio.CancelledError:
                emit('cancelled')
                raise
            except Exception as error:
                emit('failed', error=type(error).__name__)
                raise
            emit('returned', changed=response.get('changed'), timedOut=response.get('timedOut'))
            return response
        finally:
            active_read.reset(token)
    service.workspace_work.dispatch = observed_dispatch


async def seed(service):
    workspace = service.state['workspaces'][0]
    created = await service.dispatch('session.create', {
        'workspace': workspace['path'], 'title': 'Synthetic Work owner'},
        command_id='fixture-shared-work-owner')
    assert created['accepted']
    owner = service._session(created['result']['sessionId'])

    def source(text, origin='ui'):
        message = service._message(owner, 'user', text, inputOrigin=origin)
        service._save()
        return {'sessionId': owner['id'], 'messageId': message['id']}

    async def command(verb, identity, **args):
        result = await service.dispatch('workspace.work.' + verb, {
            'sessionId': owner['id'], 'workspaceId': workspace['id'], **args},
            command_id='fixture-shared-work-' + identity)
        assert result['accepted'], result
        return result['result']['item']

    original = 'Retain this exact source.\n' + ('cafe\u0301 \U0001d11e <script>not executable</script>\n' * 230) + 'ORIGINAL-END'
    target = await command('create', 'original', source=source(original),
        intentKey='original', title='Retain the original promise', commitment='accepted',
        interpretation='Keep the original words and each later correction.',
        criteria=[{'id': 'source', 'text': 'All source pages remain readable.'},
                  {'id': 'cold', 'text': 'A cold reopen retains the same promise.'}],
        nextAction='Check the exact candidate independently.')
    target = await command('report', 'participant-return', id=target['id'],
        expectedRevision=target['revision'], promiseRevision=1,
        candidate='synthetic-candidate-one', outcome='passed',
        report='Participant reports success. This is not a retained check receipt.',
        criteria=['source', 'cold'])
    correction = 'Also retain paused work. Do not infer completion from idle. CORRECTION-END'
    target = await command('revise', 'correction', id=target['id'],
        expectedRevision=target['revision'], expectedPromiseRevision=1,
        source=source(correction), interpretation='Keep paused work and every prior source too.')
    # More than one history page; every event is produced by the real domain.
    for index in range(22):
        target = await command('revise', f'next-{index}', id=target['id'],
            expectedRevision=target['revision'], expectedPromiseRevision=2,
            nextAction=f'Inspect retained source page {index + 1}.')
    target = await command('report', 'unknown-current', id=target['id'],
        expectedRevision=target['revision'], promiseRevision=2,
        candidate='synthetic-candidate-one', outcome='unknown',
        report='The current cold-reopen outcome is unknown; the old success is retained.',
        criteria=['cold'])
    # Exact current producer: publication is only an attributed report. It must
    # not change the unknown check outcome, verification or human decision.
    target = await command('publication', 'reported-publication', id=target['id'],
        expectedRevision=target['revision'], promiseRevision=target['promiseRevision'],
        candidate='synthetic-candidate-one', outcome='published',
        destination='Synthetic preview destination',
        references=['https://example.invalid/releases/synthetic-candidate-one'],
        report='Participant reports publication; no host-observed delivery or independent verification.')
    rows = [target]
    for index in range(22):
        rows.append(await command('create', f'page-{index}',
            source=source(f'Synthetic accepted request {index + 1}.'),
            intentKey=f'page-{index}', title=f'Paged accepted request {index + 1:02}',
            commitment='accepted', criteria=[{'id': 'check', 'text': 'Not checked yet.'}]))
    proposal = await command('create', 'proposal', source=source('A peer suggestion, not human acceptance.', 'peer'),
        intentKey='proposal', title='Unaccepted peer proposal', commitment='proposed', criteria=[])
    closed = await command('create', 'closed-create', source=source('Cancel this synthetic request explicitly.'),
        intentKey='cancelled', title='Cancelled scope is not shipped', commitment='accepted',
        criteria=[{'id': 'cancelled-scope', 'text': 'Cancellation retains the original source and reason.'}])
    closed = await command('disposition', 'closed-disposition', id=closed['id'],
        expectedRevision=closed['revision'], disposition='closed', basis='cancelled',
        reason='Synthetic cancellation; no correctness or publication claim.')
    # Keep the target's missing-cleanup oracle. A different existing row carries
    # an owner-reported reaped resource, not independently verified teardown.
    resource_row = await command('resource', 'reported-cleanup', id=rows[1]['id'],
        expectedRevision=rows[1]['revision'], resourceId='synthetic-resource-one',
        kind='test-container', reportedState='reaped',
        evidenceReference='synthetic-cleanup-receipt')
    rows[1] = resource_row
    decisions = {}
    for name in ['fresh', 'later']:
        row = await command('create', name + '-decision-create',
            source=source('Synthetic decision scope: ' + name),
            intentKey=name + '-decision', title='Decision currentness ' + name,
            commitment='accepted', criteria=[{'id': 'decision', 'text': 'Keep the decision evidence distinct.'}])
        row = await command('report', name + '-decision-pass', id=row['id'],
            expectedRevision=row['revision'], promiseRevision=row['promiseRevision'],
            candidate='synthetic-decision-candidate', outcome='passed',
            report='Participant report only, not independent verification.', criteria=['decision'])
        row = await command('decision', name + '-decision-accept', id=row['id'],
            expectedRevision=row['revision'], promiseRevision=row['promiseRevision'],
            candidate='synthetic-decision-candidate', decision='accepted')
        if name == 'later':
            row = await command('report', name + '-decision-unknown', id=row['id'],
                expectedRevision=row['revision'], promiseRevision=row['promiseRevision'],
                candidate='synthetic-decision-candidate', outcome='unknown',
                report='Later counterevidence must remain visible.', criteria=['decision'])
        raw = service.workspace_work.load(row['id'], workspace)
        assert row['humanDecisionCurrent'] is (name == 'fresh')
        assert ('counterevidenceSequence' in raw) is (name == 'later')
        assert row['verification'] == 'not_verified'
        rows.append(row)
        decisions[name] = row
    source('Synthetic ordinary conversation not yet reviewed for capture.')
    service._save()
    coverage = (await service.dispatch('workspace.work.coverage', {
        'workspaceId': workspace['id'], 'limit': 20}))['result']
    manifest = {
        'workspace': workspace, 'ownerSessionId': owner['id'], 'target': target,
        'acceptedIds': [row['id'] for row in rows], 'proposal': proposal, 'closed': closed,
        'originalText': original, 'originalSha256': hashlib.sha256(original.encode()).hexdigest(),
        'correctionText': correction, 'synthetic': True,
        'reportedCleanup': resource_row, 'coverage': coverage, 'decisions': decisions,
    }
    if os.environ.get('AMPLIFIER_TEST_COLD_TASK') == '1':
        manifest['coldTask'] = await seed_cold_task(owner['id'])

    return manifest


async def seed_cold_task(sid):
    """Use the real task controller to persist a fixture, then close it.

    This is data preparation, not a live worker or a substitute task.get
    implementation. The reopened HTTP service must read its canonical file.
    """
    from amplifier_web.runtime_controls import RuntimeControls

    class Coordinator:
        def __init__(self):
            self.session_state = {}
            self.capabilities = {'live.continuation_guard_supported': True}
            self.config = {'session': {'context': {'module': 'context-simple'}}, 'providers': []}
            self.loop = SimpleNamespace(goal_stall_threshold=3)
        def get(self, name):
            return {'orchestrator': self.loop, 'providers': {}}.get(name)
        def get_capability(self, name):
            return self.capabilities.get(name)
        def register_capability(self, name, value):
            self.capabilities[name] = value

    controller = RuntimeControls(SimpleNamespace(session_id=sid, coordinator=Coordinator()),
                                 SimpleNamespace(generation=None, queued_inputs=0))
    objective = 'Read the persisted cold task without starting its worker'
    private_marker = 'UNLOADED-TASK-DETAIL'
    try:
        await controller.perform('task.create', {'commandId': 'fixture-cold-task-create',
            'expectedRevision': 0, 'objective': objective, 'constraints': ['Keep the original evidence'],
            'artifactRefs': [f'{private_marker}-{index}-' + 'x' * 100 for index in range(80)]})
        await controller.perform('task.pause', {'commandId': 'fixture-cold-task-pause', 'expectedRevision': 1})
        task = controller.tasks.record()
        return {'sessionId': sid, 'id': task['id'], 'revision': task['revision'],
                'objective': objective, 'privateMarker': private_marker,
                'producer': 'real RuntimeControls task persistence; no running worker'}
    finally:
        await controller.close()
