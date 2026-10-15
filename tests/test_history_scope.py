"""Authenticated browser partition signal; the navigation trail stays off-host."""
import json

from amplifier_web.auth import SESSION_COOKIE, _signed, new_session
from amplifier_web.server import create_app
from test_service import Runtime


async def test_browser_scope_matches_attach_snapshot_and_stream_without_persisting(authenticated_client, tmp_path):
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    client.session.headers.pop('Authorization')
    credential = new_session(app['session_secret'])
    headers = {'Cookie': f'{SESSION_COOKIE}={credential}', 'X-Amplifier-Client': 'history-browser'}
    response = await client.post('/api/clients/attach', json={'clientId': 'history-browser'}, headers=headers)
    assert response.status == 200
    attached = await response.json()
    scope = attached['historyScope']
    assert len(scope) == 64 and credential not in scope
    assert response.headers['X-Amplifier-History-Scope'] == scope
    assert attached['state']['client']['historyScope'] == scope
    response = await client.get('/api/state', headers=headers)
    assert (await response.json())['client']['historyScope'] == scope
    async with client.ws_connect('/api/events/ws?clientId=history-browser&transport=delta-v1', headers=headers) as socket:
        initial = await socket.receive_json(timeout=3)
        assert initial['event'] == 'state'
        assert initial['data']['client']['historyScope'] == scope
    # A page reload has a fresh client identity but the same sign-in scope.
    response = await client.post('/api/clients/attach', json={'clientId': 'reloaded-browser', 'resumeClientId': 'history-browser'}, headers=headers)
    assert (await response.json())['historyScope'] == scope
    assert 'historyScope' not in json.dumps(app['service'].clients.records)
    assert 'destinationHistory' not in json.dumps(app['service'].clients.records)
    # The same browser under a new verified login must not restore the old trail.
    other = new_session(app['session_secret'])
    response = await client.post('/api/clients/attach', json={'clientId': 'other-login'}, headers={'Cookie': f'{SESSION_COOKIE}={other}'})
    assert (await response.json())['historyScope'] != scope
    expired = _signed(app['session_secret'], {'kind': 'session', 'issued': 1, 'nonce': 'expired'})
    response = await client.get('/api/state', headers={'Cookie': f'{SESSION_COOKIE}={expired}'})
    assert response.status == 401
    assert 'X-Amplifier-History-Scope' not in response.headers


async def test_control_authenticated_browser_gets_noncredential_partition(authenticated_client, tmp_path):
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    response = await client.post('/api/clients/attach', json={'clientId': 'control-browser'})
    result = await response.json()
    assert result['historyScope'] == result['state']['client']['historyScope']
    assert result['historyScope'] != app['control_token']
    # An opaque scope cannot be used as the bearer credential.
    client.session.headers['Authorization'] = 'Bearer ' + result['historyScope']
    assert (await client.get('/api/state')).status == 401
