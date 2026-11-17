"""Two independently approved clients share display receipts, never authority."""
import secrets
import uuid

import pytest

from amplifier_web.device_pairing import challenge
from amplifier_web.server import create_app
from amplifier_web.client_presentation import descriptor, observe_execution, plain
from test_service import Runtime
from test_client_observations import replace_catalog


@pytest.fixture
async def presentation_host(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    client = await aiohttp_client(app)
    await app['service'].history.close()
    headers = []
    for name in ('first-device', 'second-device'):
        manager = app['device_pairing'] if 'device_pairing' in app else None
        # Exercise the actual pairing store used by the middleware without a GUI.
        from amplifier_web.device_pairing import Pairings
        manager = Pairings(app['terminal_setup'].devices)
        verifier = secrets.token_urlsafe(32)
        request = manager.start(name, challenge(verifier)); manager.decide(request['id'], True)
        issued = manager.poll(request['id'], verifier)
        native = {'Authorization': 'Bearer ' + issued['token'], 'X-Amplifier-Client': name,
                  'X-Amplifier-State-Transport': 'delta-v1'}
        response = await client.post('/api/clients/attach', headers=native,
            json={'clientId': name, 'kind': 'native', 'protocolVersion': 1, 'includeState': False})
        assert response.status == 200, await response.text()
        discovery = await response.json()
        assert discovery['capabilities']['observations']['dismissals'] == {'version': 1, 'action': 'presentation.dismiss'}
        assert 'state' not in discovery
        headers.append((native, issued['deviceID']))
    rows = [dict(id='chat', title='Example', status='working', sessionKind='root',
                 messages=[], workers=[], approvals=[], completion={'id': 'answer-one', 'at': 1},
                 activityPresentation={'identity': ['action-one'], 'summary': 'Reading a document'})]
    replace_catalog(app['service'], rows)
    yield app, client, headers, rows


async def collection(client, headers, path='/api/attention'):
    response = await client.get(path, headers=headers)
    assert response.status == 200, await response.text()
    value = await response.json()
    assert value['hostIdentity'] and value['hostInstanceId']
    return value


async def hide(client, headers, descriptor, command=None):
    return await client.post('/api/actions', headers=headers, json={
        'id': command or str(uuid.uuid4()), 'action': 'presentation.dismiss',
        'args': {'items': [{k: descriptor[k] for k in ('id', 'fingerprint')}]}})


async def test_shared_dismissal_is_not_read_or_resolution(presentation_host):
    app, client, devices, rows = presentation_host
    first, second = devices[0][0], devices[1][0]
    before = await collection(client, first)
    notice = before['items'][0]
    count = await collection(client, second, '/api/activity')
    command = str(uuid.uuid4())
    response = await hide(client, first, notice['dismissal'], command)
    assert response.status == 200, await response.text()
    assert (await response.json())['accepted']
    after = await collection(client, second)
    assert after['items'][0]['dismissal']['dismissed'] is True
    assert after['items'][0]['read'] is False
    assert after['items'][0]['fingerprint'] == notice['fingerprint']
    assert after['revision'] != before['revision']
    counts_after = await collection(client, second, '/api/activity')
    assert counts_after['attentionCount'] == count['attentionCount']
    assert counts_after['revision'] == count['revision']
    assert not app['service'].state.get('attentionRead')
    assert (await hide(client, first, notice['dismissal'], command)).status == 200
    # Receipt is durable in the ordinary service database, not either client's state.
    import sqlite3
    with sqlite3.connect(app['service'].data_dir / 'app.sqlite3') as db:
        assert db.execute('SELECT fingerprint FROM client_presentation_receipts WHERE id=?', (notice['dismissal']['id'],)).fetchone()[0] == notice['fingerprint']
    rows[0]['completion'] = {'id': 'answer-two', 'at': 2}
    app['service']._save(session_ids={'chat'})
    changed = await collection(client, second)
    assert changed['items'][0]['dismissal']['dismissed'] is False
    assert (await hide(client, first, notice['dismissal'])).status == 409


async def test_work_change_and_revocation(presentation_host):
    app, client, devices, rows = presentation_host
    first, second = devices[0][0], devices[1][0]
    path = '/api/sessions?view=observation&activity=active-or-pinned'
    before = await collection(client, first, path)
    state = before['items'][0]['dismissal']
    assert (await hide(client, first, state)).status == 200
    assert (await collection(client, second, path))['items'][0]['dismissal']['dismissed']
    rows[0]['activityPresentation']['summary'] = 'Still reading'
    app['service']._save(session_ids={'chat'})
    assert (await collection(client, second, path))['items'][0]['dismissal']['dismissed']
    rows[0]['activityPresentation']['identity'] = ['action-two']
    app['service']._save(session_ids={'chat'})
    assert not (await collection(client, second, path))['items'][0]['dismissal']['dismissed']
    assert (await hide(client, first, state)).status == 409
    app['terminal_setup'].devices.revoke(devices[0][1])
    assert (await hide(client, first, state)).status == 401
    assert (await client.get('/api/sessions', headers=second)).status == 403
    assert (await client.post('/api/actions', headers=second, json={'action': 'attention.read', 'args': {'ids': []}})).status == 403
    assert (await client.post('/api/actions', headers=second, json={'action': 'view.update', 'args': {'patch': {}}})).status == 403


async def test_leased_page_retains_original_receipt_view(presentation_host):
    app, client, devices, rows = presentation_host
    rows[0]['manualUnread'] = 'manual'
    app['service']._save(session_ids={'chat'})
    first, second = devices[0][0], devices[1][0]
    before = await collection(client, first, '/api/attention?limit=1')
    assert before['nextCursor']
    all_items = (await collection(client, second))['items']
    last = all_items[-1]['dismissal']
    assert (await hide(client, second, last)).status == 200
    continued = await client.get('/api/attention', headers=first, params={'limit': 1, 'cursor': before['nextCursor']})
    value = await continued.json()
    assert value['revision'] == before['revision']
    assert not value['items'][0]['dismissal']['dismissed']
    assert (await collection(client, first))['items'][-1]['dismissal']['dismissed']


def test_activity_identity_ignores_progress_and_projected_text_avoids_raw_payload():
    node = {'id': 'tool-1', 'turnId': 'turn-1', 'kind': 'tool', 'phase': 'running',
            'label': '**App**', 'inputSummary': 'Open settings', 'input': 'PRIVATE INPUT', 'output': 'PRIVATE OUTPUT'}
    session = {'id': 'chat', 'status': 'working', 'execution': {'nodes': [node]}}
    observe_execution(session, node)
    before = descriptor('activity', session)
    assert session['activityPresentation']['summary'] == 'App · Open settings'
    assert 'PRIVATE' not in str(session['activityPresentation'])
    node.update(updatedAt=99, summary='Progress', revision=3)
    observe_execution(session, node)
    assert descriptor('activity', session) == before
    node['id'] = 'tool-2'; observe_execution(session, node)
    assert descriptor('activity', session)['fingerprint'] != before['fingerprint']
    assert plain('**Done** [details](https://example.test)') == 'Done details'


async def test_batch_is_atomic_and_old_command_never_hides_new_state(presentation_host):
    app, client, devices, rows = presentation_host
    headers = devices[0][0]
    notice = (await collection(client, headers))['items'][0]['dismissal']
    work = (await collection(client, headers, '/api/sessions?view=observation'))['items'][0]['dismissal']
    response = await client.post('/api/actions', headers=headers, json={
        'id': str(uuid.uuid4()), 'action': 'presentation.dismiss', 'args': {'items': [
            {k: notice[k] for k in ('id', 'fingerprint')},
            {'id': work['id'], 'fingerprint': 'stale'}]}})
    assert response.status == 409
    assert not (await collection(client, headers))['items'][0]['dismissal']['dismissed']
    command = str(uuid.uuid4())
    assert (await hide(client, headers, notice, command)).status == 200
    rows[0]['completion'] = {'id': 'next-answer', 'at': 2}
    app['service']._save(session_ids={'chat'})
    assert (await hide(client, headers, notice, command)).status == 200
    assert not (await collection(client, headers))['items'][0]['dismissal']['dismissed']
    # Query tricks cannot broaden the restricted observation-only permission.
    for path in ('/api/sessions?view=observation&view=full', '/api/sessions?view=full', '/api/sessions/chat'):
        assert (await client.get(path, headers=headers)).status in (403, 404)


async def test_receipt_survives_host_restart(presentation_host, aiohttp_client):
    app, client, devices, rows = presentation_host
    first, second = devices[0][0], devices[1][0]
    service = app['service']
    service._save(session_ids={'chat'})
    before = await collection(client, first)
    assert (await hide(client, first, before['items'][0]['dismissal'])).status == 200
    directory, workspace = service.data_dir, service.default_workspace
    await client.close()
    restarted = await create_app(directory, workspace=workspace, runtime=Runtime(), voice=False,
                                 background_updates=False, preload_providers=False)
    peer = await aiohttp_client(restarted)
    await restarted['service'].history.close()
    attached = await peer.post('/api/clients/attach', headers=second, json={
        'clientId': second['X-Amplifier-Client'], 'kind': 'native', 'protocolVersion': 1, 'includeState': False})
    assert attached.status == 200, await attached.text()
    after = await collection(peer, second)
    assert after['hostIdentity'] == before['hostIdentity']
    assert after['hostInstanceId'] != before['hostInstanceId']
    assert after['items'][0]['dismissal']['dismissed']
    assert not after['items'][0]['read']


def test_execution_projection_uses_current_turn_and_survives_directory_freeze():
    from amplifier_web.history_directory import summary
    tool = {'id': 't1', 'turnId': 'turn1', 'kind': 'tool', 'phase': 'running', 'label': 'App', 'actionPath': 'window.focus'}
    model = {'id': 'm1', 'turnId': 'turn1', 'kind': 'llm', 'phase': 'running', 'label': 'Model'}
    session = {'id': 'chat', 'status': 'working', 'execution': {'currentTurnId': 'turn1', 'nodes': [tool, model]},
               'collaborationGeneration': {'id': 'g1', 'inputIds': ['input1']}}
    observe_execution(session, model)
    assert session['activityPresentation']['label'] == 'App'
    before = descriptor('activity', session)
    frozen = summary(session); frozen['_navigationInput'] = ['g1', 'input1']
    assert descriptor('activity', frozen) == before
    old = {'id': 'old', 'turnId': 'turn0', 'kind': 'tool', 'phase': 'running', 'label': 'Stale'}
    observe_execution(session, old)
    assert descriptor('activity', session) == before
    tool['phase'] = 'completed'; observe_execution(session, tool)
    assert session['activityPresentation']['label'] == 'Thinking…'
    assert descriptor('activity', session) != before
    session['collaborationGeneration']['inputIds'].append('input2')
    assert descriptor('activity', session) != descriptor('activity', frozen)
