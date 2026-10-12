from amplifier_web.server import create_app
from test_service import Runtime


async def test_companion_is_authenticated_bounded_and_does_not_load_history(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    client = await aiohttp_client(app)
    assert (await client.get('/api/companion', allow_redirects=False)).status in (401, 302)
    client.session.headers['Authorization'] = 'Bearer ' + app['control_token']
    assert (await client.get('/api/companion')).status == 400
    response = await client.post('/api/clients/attach',
        headers={'X-Amplifier-State-Transport': 'delta-v1'},
        json={'clientId': 'mac-test', 'kind': 'native', 'protocolVersion': 1})
    assert response.status == 200
    assert 'state' not in await response.json()
    client.session.headers['X-Amplifier-Client'] = 'mac-test'
    service = app['service']
    await service.dispatch('session.create', {'title': 'Private work'})
    sid = service.state['selectedSessionId']
    row = service._session(sid)
    row.update(status='idle', completion={'id': 'turn-1', 'at': 1},
               error='SECRET ERROR DETAIL', errorAt=1)
    service.state['pinnedSessionIds'] = [sid]
    service._save_changes(sessions={sid}, globals={'pinnedSessionIds'})
    async def forbidden(*args, **kwargs):
        raise AssertionError('Observation must not hydrate or warm history')
    service.history.ensure_loaded = forbidden
    before = service.clients.records['mac-test']['selectedSessionId']
    value = await (await client.get('/api/companion')).json()
    assert value['workingCount'] == 0
    assert value['attentionCount'] == 2
    assert value['sessions'][0]['id'] == sid
    assert value['sessions'][0]['pinned']
    assert 'SECRET' not in str(value)
    assert 'messages' not in str(value)
    assert service.clients.records['mac-test']['selectedSessionId'] == before
    assert value['hostIdentity']
    # Same durable completion is identical on a repeated observation.
    again = await (await client.get('/api/companion')).json()
    assert again['items'] == value['items']
    item = next(i for i in value['items'] if i['kind'] == 'completion')
    await service.dispatch('attention.read', {'ids': [item['id']],
                            'fingerprints': {item['id']: item['fingerprint']}})
    reviewed = await (await client.get('/api/companion')).json()
    assert len(reviewed['items']) == 1
    denied = await client.get('/api/companion', headers={'Origin': 'https://evil.invalid'})
    assert denied.status == 403


async def test_companion_limits_and_omits_internal_chats(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.companion import snapshot
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    original = service.state['sessions']
    try:
        base = {'status': 'working', 'messages': [], 'workers': [], 'approvals': []}
        service.state['sessions'] = [dict(base, id=str(i), title='Chat',
            completion={'id': str(i), 'at': i}) for i in range(230)]
        service.state['sessions'].append(dict(base, id='internal', sessionKind='internal'))
        service.projections.invalidate()
        value = snapshot(service)
        assert value['workingCount'] == 230
        assert len(value['sessions']) == 50 and value['sessionsTruncated']
        assert len(value['items']) == 200 and value['itemsTruncated']
        assert 'internal' not in str(value)
    finally:
        service.state['sessions'] = original
        service.projections.invalidate()
        await service.close()
