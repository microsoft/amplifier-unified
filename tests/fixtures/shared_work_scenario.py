"""Fixed synthetic Work data for the production-host browser fixture.

No general write endpoint, provider call, fabricated verification receipt, or
private history import. This module is enabled only by --shared-work.
"""
import asyncio
import contextvars
import hashlib
import json
import time

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
    if cold:
        # Trap hydration caused by a Work read, without pretending that the
        # ordinary app's chat display is itself forbidden to hydrate history.
        for target, name in [(service.history, 'ensure_loaded'), (service.cold_display, 'hydrate'),
                             (service.management, 'ensure_runtime')]:
            if target is None:
                continue
            original = getattr(target, name)
            async def guarded(*args, _original=original, _name=name, **kwargs):
                if active_read.get():
                    cold_calls.append(_name)
                    raise AssertionError('Cold Work read hydrated or started a worker: ' + _name)
                return await _original(*args, **kwargs)
            setattr(target, name, guarded)

    async def inspect(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        return web.json_response({**manifest, 'coldReopen': cold, 'coldReadCalls': cold_calls,
            'forbiddenRuntimeCalls': list(calls), 'runtimeCallbackCounts': dict(callback_counts),
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
    source('Synthetic ordinary conversation not yet reviewed for capture.')
    service._save()
    coverage = (await service.dispatch('workspace.work.coverage', {
        'workspaceId': workspace['id'], 'limit': 20}))['result']
    manifest = {
        'workspace': workspace, 'ownerSessionId': owner['id'], 'target': target,
        'acceptedIds': [row['id'] for row in rows], 'proposal': proposal, 'closed': closed,
        'originalText': original, 'originalSha256': hashlib.sha256(original.encode()).hexdigest(),
        'correctionText': correction, 'synthetic': True,
        'reportedCleanup': resource_row, 'coverage': coverage,
    }

    return manifest
