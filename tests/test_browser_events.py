import asyncio
import uuid
import pytest
from aiohttp import WSServerHandshakeError, WSMsgType
from amplifier_web.server import create_app
from test_service import Runtime


async def attached(client):
    identity = str(uuid.uuid4())
    response = await client.post('/api/clients/attach', json={'clientId': identity, 'includeState': False})
    assert 'websocket' in (await response.json())['transports']
    return identity


async def test_socket_snapshot_delta_private_clients_and_release(authenticated_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    client = await authenticated_client(app)
    service = app['service']
    ids = [await attached(client), await attached(client)]
    sockets = [await client.ws_connect('/api/events/ws?transport=delta-v1&clientId='+i) for i in ids]
    frames = [await s.receive_json(timeout=3) for s in sockets]
    assert all(f['event'] == 'state' for f in frames)
    assert [f['data']['client']['id'] for f in frames] == ids
    response = await client.post('/api/actions', headers={'X-Amplifier-Client': ids[0]},
        json={'action': 'view.update', 'args': {'patch': {'draft': 'private draft'}}})
    assert response.status == 200
    # Attachment/history publications can precede this action's delta. Observe
    # the requested change, rather than treating the next frame as its receipt.
    async with asyncio.timeout(3):
        while True:
            frame = await sockets[0].receive_json()
            if 'private draft' in str(frame):
                break
    assert frame['event'] == 'state-delta'
    assert 'private draft' in str(frame)
    # Another client cannot receive the first client's private draft.
    second = await client.get('/api/state', headers={'X-Amplifier-Client': ids[1]})
    assert 'private draft' not in await second.text()
    for s in sockets:
        await s.close()
    await asyncio.sleep(.05)
    assert not service.queues


async def test_socket_admission_and_no_commands(authenticated_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    client = await authenticated_client(app)
    identity = await attached(client)
    for path,headers,status in [('/api/events/ws', {},400),
        ('/api/events/ws?clientId='+identity, {'Origin':'https://untrusted.example'},403),
        ('/api/events/ws?clientId='+identity, {'Authorization':'Bearer invalid'},401)]:
        with pytest.raises(WSServerHandshakeError) as error:
            await client.ws_connect(path,headers=headers)
        assert error.value.status == status
    socket = await client.ws_connect('/api/events/ws?clientId='+identity)
    await socket.receive_json(timeout=3)
    before = len(app['service'].state['sessions'])
    await socket.send_json({'action':'session.create','args':{}})
    # Independent state publications may already be in flight before rejection.
    # Observe the close itself, without treating an earlier snapshot as a reply.
    async with asyncio.timeout(3):
        while True:
            response = await socket.receive()
            if response.type != WSMsgType.TEXT:
                break
            assert response.json()['event'] in {'state', 'state-delta', 'shell'}
    assert response.type == WSMsgType.CLOSE and response.data == 1008
    assert len(app['service'].state['sessions']) == before


@pytest.mark.parametrize('host,scheme,authority', [
    ('localhost:8443', 'http', 'localhost:8443'),
    ('example.test', 'https', 'example.test'),
    ('[::1]:8765', 'http', '[::1]:8765'),
])
def test_csp_names_only_current_socket_authority(host, scheme, authority):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request
    from amplifier_web.server import _set_response_headers
    request = make_mocked_request('GET', '/', headers={'Host': host,
        'X-Forwarded-Host': 'untrusted.example'}).clone(scheme=scheme)
    response = _set_response_headers(web.Response(), request)
    csp = response.headers['Content-Security-Policy']
    assert f"connect-src 'self' ws://{authority} wss://{authority} " in csp
    assert 'untrusted.example' not in csp and 'ws: ' not in csp and 'wss: ' not in csp
    strict = web.Response(headers={'Content-Security-Policy': "default-src 'none'"})
    assert _set_response_headers(strict, request).headers['Content-Security-Policy'] == "default-src 'none'"
