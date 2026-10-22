import asyncio
from copy import deepcopy
import json
import pytest

from amplifier_web.state_transport import delta
from amplifier_web.browser_detail import project
from amplifier_web.server import create_app
from test_service import Runtime


@pytest.mark.parametrize('compact', [False, True])
async def test_send_receipt_observes_consumption_after_admission(authenticated_client, tmp_path, compact):
    import base64

    class ObservedRuntime(Runtime):
        async def send(self, session, text, input_id, emit):
            self.admission = service.browser_state()
            return await super().send(session, text, input_id, emit)

    runtime = ObservedRuntime()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=runtime, voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    client = await authenticated_client(app)
    await client.post('/api/clients/attach', json={'clientId': 'sender'})
    headers = {'X-Amplifier-Client': 'sender'}
    if compact:
        headers['X-Amplifier-State-Transport'] = 'delta-v1'

    async def command(identity, action, args):
        response = await client.post('/api/actions', headers=headers,
                                     json={'id': identity, 'action': action, 'args': args})
        assert response.status == 200, await response.text()
        return await response.json()

    await command('create', 'session.create', {})
    await command('file', 'attachment.add', {'name': 'sent.txt',
                  'base64': base64.b64encode(b'Exact uploaded bytes').decode()})
    with service.clients.bind('sender'):
        sid = service.state['selectedSessionId']
        attachment = deepcopy(service.clients.attachments(service._session(sid))[0])
        queue = service.subscribe()
    try:
        args = {'sessionId': sid, 'text': 'First', 'attachmentIds': [attachment['id']]}
        receipt = await command('first', 'conversation.send', args)
        assert receipt['delivery'] == 'accepted'
        frames = []
        while not queue.empty():
            frame = queue.get_nowait()
            if 'shellClientId' not in frame:
                frames.append(frame)
        admission = runtime.admission
        assert admission['revision'] == receipt['revision']
        admitted = next(row for row in admission['sessions'] if row['id'] == sid)
        assert admitted['draftAttachments'] == [attachment]
        assert next(row for row in admitted['messages'] if row.get('inputId') == 'first')['delivery']['status'] == 'sending'
        if compact:
            assert 'state' not in receipt
            assert receipt['stateRevision'] > receipt['revision']
            observed = next(frame for frame in frames if frame['revision'] == receipt['stateRevision'])
        else:
            assert 'stateRevision' not in receipt
            observed = receipt['state']
        settled = next(row for row in observed['sessions'] if row['id'] == sid)
        assert settled['draftAttachments'] == []
        sent = next(row for row in settled['messages'] if row.get('inputId') == 'first')
        assert sent['delivery']['status'] == 'accepted' and sent['attachments'] == [attachment]
        duplicate = await command('first', 'conversation.send', args)
        assert duplicate['duplicate'] and duplicate['revision'] == receipt['revision']
        assert len(runtime.sent) == 1
        second = await command('second', 'conversation.send', {'sessionId': sid, 'text': 'Adjacent cycle'})
        assert second['delivery'] == 'accepted' and len(runtime.sent) == 2
        rejected = await client.post('/api/actions', headers=headers, json={'id': 'stale',
            'action': 'conversation.send', 'args': {'sessionId': sid, 'text': 'Stale',
                                                   'attachmentIds': [attachment['id']]}})
        assert rejected.status == 400
        assert (await rejected.json())['error'] == 'An attachment is no longer in this draft. Refresh and retry.'
        assert len(runtime.sent) == 2
    finally:
        service.unsubscribe(queue)


@pytest.mark.parametrize('compact', [False, True])
async def test_noop_layout_receipt_does_not_wait_for_other_client_revision(authenticated_client, tmp_path, compact):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    # Startup diagnostics publishes shared state after an asynchronous read.
    # Complete that initial snapshot before observing only client-local changes;
    # its normal background reader remains active throughout the assertions.
    await service.diagnostics.publish()
    client = await authenticated_client(app)
    for identity in ('layout-one', 'layout-two'):
        await client.post('/api/clients/attach', json={'clientId': identity})
    transport = {'X-Amplifier-State-Transport': 'delta-v1'} if compact else {}

    async def layout(identity, width, **extra):
        return await client.post('/api/actions', headers={**transport, 'X-Amplifier-Client': identity},
                                 json={'action': 'view.update', 'args': {'patch': {'navWidth': width}}, **extra})

    await layout('layout-one', 301)
    with service.clients.bind('layout-one'):
        baseline = service.browser_state()
        queue = service.subscribe()
    try:
        await layout('layout-two', 302)
        assert service._state['revision'] > baseline['revision'] and queue.empty()
        revision = service._state['revision']
        receipt = await (await layout('layout-one', 301, id='unchanged-layout')).json()
        assert receipt['accepted'] and receipt['revision'] == revision
        assert 'stateRevision' not in receipt and '_stateIndependent' not in receipt
        assert service._state['revision'] == revision and queue.empty()
        if not compact:
            assert receipt['state']['view']['navWidth'] == 301
        saved = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?',
                                             ('unchanged-layout',)).fetchone()[0])
        assert saved['_stateIndependent'] is True

        # A later unrelated change cannot turn an idempotent no-op into a wait.
        await layout('layout-two', 303)
        duplicate = await (await layout('layout-one', 301, id='unchanged-layout')).json()
        assert duplicate['duplicate'] and duplicate['revision'] == revision
        assert 'stateRevision' not in duplicate and '_stateIndependent' not in duplicate
        assert queue.empty()
        # Stale CAS and reused IDs still reject before the no-op shortcut.
        assert (await layout('layout-one', 301, expectedRevision=baseline['revision'])).status == 409
        assert (await layout('layout-one', 302, id='unchanged-layout')).status == 409
        # Actual changes retain their state acknowledgement; caller-supplied
        # markers cannot override the server's validated decision.
        changed = await (await layout('layout-one', 304, _stateIndependent=True)).json()
        assert queue.get_nowait()['view']['navWidth'] == 304
        if compact:
            assert changed['stateRevision'] == changed['revision']
        else:
            assert changed['state']['view']['navWidth'] == 304
        assert '_stateIndependent' not in changed
    finally:
        service.unsubscribe(queue)


@pytest.mark.parametrize('compact', [False, True])
async def test_plan_receipt_has_no_unrelated_state_ack(authenticated_client, tmp_path, compact):
    from amplifier_web.event_log_view import event_path
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    made = await service.dispatch('session.create', {'title': 'Exact plan'})
    session = service._session(made['sessionId'])
    identity = session.get('runtimeSessionId') or session['id']
    path = event_path(session, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'event': 'tool:post', 'timestamp': 1, 'data': {
        'session_id': identity, 'tool_name': 'todo', 'tool_call_id': 'exact-plan',
        'result': {'success': True, 'output': {'todos': [
            {'content': 'Retain the actual plan', 'status': 'completed'}]}}}}) + '\n')
    original = path.read_bytes()
    client = await authenticated_client(app)
    headers = {'X-Amplifier-State-Transport': 'delta-v1'} if compact else {}
    # No publication can acknowledge this unrelated private revision. A pure
    # read must return its own result instead of assigning it to that domain.
    service._state['revision'] += 100
    result = await (await client.post('/api/actions', headers=headers, json={
        'action': 'session.plans', 'args': {'ids': [session['id']], 'includeItems': True}})).json()
    assert result['accepted'] and 'stateRevision' not in result
    assert '_stateIndependent' not in result
    plan = result['result']['items'][0]['plan']
    assert (plan['done'], plan['total']) == (1, 1)
    assert plan['items'] == [{'content': 'Retain the actual plan', 'status': 'completed'}]
    assert path.read_bytes() == original and service._session()['id'] == session['id']
    # Mutations still retain their authoritative state acknowledgement, even
    # when the caller tries to supply the internal read-only marker.
    mutation = await (await client.post('/api/actions', headers=headers, json={
        'action': 'view.update', 'args': {'patch': {'navWidth': 301}},
        '_stateIndependent': True})).json()
    if compact:
        assert mutation['stateRevision'] == mutation['revision']
    else:
        assert mutation['state']['view']['navWidth'] == 301
    assert '_stateIndependent' not in mutation
    # The shell does not declare this capability; no wider delegation grant
    # is introduced merely to optimize the ordinary workspace's direct read.
    await client.post('/api/clients/attach', json={'clientId': 'plan-reader'})
    denied = await client.post('/api/actions', headers={**headers, 'X-Amplifier-Client': 'plan-reader'}, json={
        'action': 'shell.command', 'args': {'clientId': 'plan-reader', 'instanceId': 'chats',
            'action': 'session.plans', 'args': {'ids': [session['id']]}}})
    assert denied.status == 403


def apply(previous, patch):
    result = deepcopy(previous)
    for change in patch['changes']:
        parent = result
        for key in change['path'][:-1]:
            parent = parent[key]
        key = change['path'][-1]
        if change.get('remove'):
            del parent[key]
        else:
            parent[key] = deepcopy(change['value'])
    return result


def test_small_changes_do_not_retransmit_large_assets_or_history():
    state = {'revision': 10, 'theme': {'css': 'x' * 140_000},
             'setup': {'catalog': ['model' * 10_000] * 20},
             'sessions': [{'id': str(i), 'messages': [{'id': 'm', 'text': 'words' * 1000}], 'status': 'idle'} for i in range(100)],
             'view': {'canvasControlsExpanded': False}}
    after = deepcopy(state)
    after['revision'] += 1
    after['view']['canvasControlsExpanded'] = True
    after['sessions'][17]['status'] = 'working'
    patch = delta(state, after)
    assert len(json.dumps(patch)) < 400
    assert apply(state, patch) == after
    after['sessions'].pop(3)
    del after['view']['canvasControlsExpanded']
    after['view']['new'] = None
    assert apply(state, delta(state, after)) == after


def test_accounting_ledger_stays_authoritative_but_out_of_browser_projection():
    session = {'id': 's', 'messages': [], 'execution': {'nodes': [], 'turns': [],
        'retiredUsageNodes': [{'id': str(i), 'detail': 'x' * 1000} for i in range(1000)],
        'aggregateUsage': {'inputTokens': 12345, 'cost': 1.25, 'calls': 1000}}}
    browser = project(session)
    assert 'retiredUsageNodes' not in browser['execution']
    assert browser['execution']['aggregateUsage'] == session['execution']['aggregateUsage']
    assert len(session['execution']['retiredUsageNodes']) == 1000
    assert len(json.dumps(browser)) < 1500


async def read_event(response):
    lines = []
    while True:
        line = await asyncio.wait_for(response.content.readline(), 3)
        assert line
        if line == b'\n':
            break
        lines.append(line.decode().strip())
    return next(line[7:] for line in lines if line.startswith('event: ')), json.loads(next(line[6:] for line in lines if line.startswith('data: ')))


async def test_real_http_receipt_delta_reconnect_and_legacy_client(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    client = await authenticated_client(app)
    await client.post('/api/clients/attach', json={'clientId': 'one'})
    client.session.headers['X-Amplifier-Client'] = 'one'
    stream = await client.get('/api/events?transport=delta-v1')
    kind, baseline = await read_event(stream)
    assert kind == 'state'
    client.session.headers['X-Amplifier-State-Transport'] = 'delta-v1'
    response = await client.post('/api/actions', json={'id': 'width', 'action': 'view.update', 'args': {'patch': {'navWidth': 301}}})
    body = await response.json()
    assert response.status == 200
    assert 'state' not in body and len(json.dumps(body)) < 400
    assert body['hostInstanceId'] == service.instance_id
    while True:
        kind, patch = await read_event(stream)
        if kind == 'shell':
            continue
        assert kind == 'state-delta'
        baseline = apply(baseline, patch)
        if baseline['revision'] >= body['stateRevision']:
            break
    assert baseline['view']['navWidth'] == 301
    assert len(json.dumps(patch)) < 1500
    duplicate = await (await client.post('/api/actions', json={'id': 'width', 'action': 'view.update', 'args': {'patch': {'navWidth': 301}}})).json()
    assert duplicate['duplicate'] and duplicate['revision'] == body['revision']
    stream.close()
    fresh = await client.get('/api/events?transport=delta-v1')
    kind, current = await read_event(fresh)
    assert kind == 'state' and current['view']['navWidth'] == 301
    fresh.close()
    del client.session.headers['X-Amplifier-State-Transport']
    old = await (await client.post('/api/actions', json={'action': 'view.update', 'args': {'patch': {'navWidth': 302}}})).json()
    assert old['state']['view']['navWidth'] == 302
