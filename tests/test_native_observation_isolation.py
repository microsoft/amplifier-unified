from amplifier_web.server import create_app
from test_service import Runtime


async def test_native_observation_is_on_demand_and_preserves_browser_state(
        authenticated_client, tmp_path, monkeypatch):
    from copy import deepcopy
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Browser chat'})
    sid = service.state['selectedSessionId']
    for identity, kind in [('browser', 'web'), ('tempo', 'native')]:
        response = await client.post('/api/clients/attach',
            headers={'X-Amplifier-State-Transport': 'delta-v1'},
            json={'clientId': identity, 'kind': kind, 'protocolVersion': 1})
        assert response.status == 200
    with service.clients.bind('browser'):
        await service.dispatch('session.select', {'id': sid})
        await service.dispatch('view.update', {'patch': {'draft': 'Keep this draft'}})
        service.browser_state()
    assert app['observations'].facts is None, 'Ordinary browser use must not build native summaries'
    before = deepcopy(service.clients.records['browser'])
    client.session.headers['X-Amplifier-Client'] = 'tempo'
    first = await client.get('/api/attention')
    assert first.status == 200
    cached_counts = app['observations'].counts_cache
    assert (await client.get('/api/attention')).status == 200
    assert app['observations'].counts_cache is cached_counts
    assert service.clients.records['browser'] == before
    assert not service.runtime.sent


async def test_native_observation_is_authenticated_bounded_and_does_not_load_history(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    client = await aiohttp_client(app)
    assert (await client.get('/api/attention', allow_redirects=False)).status in (401, 302)
    client.session.headers['Authorization'] = 'Bearer ' + app['control_token']
    assert (await client.get('/api/attention')).status == 400
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
    value = await (await client.get('/api/attention')).json()
    counts = await (await client.get('/api/activity')).json()
    sessions = await (await client.get('/api/sessions?view=observation&activity=active-or-pinned')).json()
    assert counts['executionCount'] == 0 and counts['attentionCount'] == 2
    assert sessions['items'][0]['id'] == sid and sessions['items'][0]['pinned']
    assert 'SECRET' not in str(value)
    assert 'messages' not in str(value)
    assert service.clients.records['mac-test']['selectedSessionId'] == before
    assert value['hostIdentity']
    # Same durable completion is identical on a repeated observation.
    again = await (await client.get('/api/attention')).json()
    assert again['items'] == value['items']
    item = next(i for i in value['items'] if i['kind'] == 'completion')
    await service.dispatch('attention.read', {'ids': [item['id']],
                            'fingerprints': {item['id']: item['fingerprint']}})
    reviewed = await (await client.get('/api/attention')).json()
    assert len(reviewed['items']) == 1
    denied = await client.get('/api/attention', headers={'Origin': 'https://evil.invalid'})
    assert denied.status == 403


async def test_adapter_route_is_actually_absent(authenticated_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    assert all(resource.canonical != '/api/companion' for resource in app.router.resources())
    response = await client.get('/api/companion')
    assert response.status == 404
