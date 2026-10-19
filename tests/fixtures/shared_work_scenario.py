"""Fixed synthetic Work data for the production-host browser fixture.

No general write endpoint, provider call, fabricated verification receipt, or
private history import. This module is enabled only by --shared-work.
"""
import hashlib

from aiohttp import web


async def install(app, runtime):
    service = app['service']
    workspace = service.state['workspaces'][0]
    created = await service.dispatch('session.create', {
        'workspace': workspace['path'], 'title': 'Synthetic Work owner'},
        command_id='fixture-shared-work-owner')
    assert created['accepted']
    owner = service._session(created['result']['sessionId'])
    calls = []

    async def forbidden_start(*args, **kwargs):
        calls.append('start')
        raise AssertionError('Work inspection must not start a runtime')

    async def forbidden_send(*args, **kwargs):
        calls.append('send')
        raise AssertionError('Work inspection must not send model input')

    runtime.start = forbidden_start
    runtime.send = forbidden_send

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
    rows = [target]
    for index in range(22):
        rows.append(await command('create', f'page-{index}',
            source=source(f'Synthetic accepted request {index + 1}.'),
            intentKey=f'page-{index}', title=f'Paged accepted request {index + 1:02}',
            commitment='accepted', criteria=[{'id': 'check', 'text': 'Not checked yet.'}]))
    proposal = await command('create', 'proposal', source=source('A peer suggestion, not human acceptance.', 'peer'),
        intentKey='proposal', title='Unaccepted peer proposal', commitment='proposed', criteria=[])
    closed = await command('create', 'closed-create', source=source('Cancel this synthetic request explicitly.'),
        intentKey='cancelled', title='Cancelled scope is not shipped', commitment='accepted', criteria=[])
    closed = await command('disposition', 'closed-disposition', id=closed['id'],
        expectedRevision=closed['revision'], disposition='closed', basis='cancelled',
        reason='Synthetic cancellation; no correctness or publication claim.')
    service._save()
    manifest = {
        'workspace': workspace, 'ownerSessionId': owner['id'], 'target': target,
        'acceptedIds': [row['id'] for row in rows], 'proposal': proposal, 'closed': closed,
        'originalText': original, 'originalSha256': hashlib.sha256(original.encode()).hexdigest(),
        'correctionText': correction, 'synthetic': True,
    }

    async def inspect(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        return web.json_response({**manifest, 'forbiddenRuntimeCalls': list(calls)})

    app.router.add_get('/fixture/shared-work', inspect)
