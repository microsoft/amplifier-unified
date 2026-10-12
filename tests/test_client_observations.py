from copy import deepcopy
import pytest
from amplifier_web.server import create_app
from amplifier_web.client_observations import Observations, SCAN_BUDGET
from amplifier_web.service import AppService, AppError
from test_service import Runtime


@pytest.fixture
async def observation_host(authenticated_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    result = await client.post('/api/clients/attach', json={
        'clientId': 'generic-api', 'kind': 'api', 'protocolVersion': 1, 'includeState': False})
    assert result.status == 200
    attached = await result.json()
    assert 'state' not in attached and attached['hostIdentity'] and attached['hostInstanceId']
    assert attached['capabilities']['observations']['version'] == 1
    client.session.headers['X-Amplifier-Client'] = 'generic-api'
    await app['service'].history.close()  # Keep the synthetic catalog stable during traversal.
    original = app['service'].state['sessions']
    yield app, client
    app['service'].state['sessions'] = original
    app['service'].state['pinnedSessionIds'] = []
    app['service'].projections.invalidate()


def catalog(service, count=230):
    service.state['sessions'] = [dict(id=f's{i}', title=f'Chat {i}', status='idle', sessionKind='root',
        messages=[], workers=[], approvals=[{'id': f'a{i}', 'title': 'PRIVATE TOOL', 'status': 'pending', 'createdAt': i}],
        completion={'id': f'c{i}', 'at': i}) for i in range(count)]
    service.state['pinnedSessionIds'] = [f's{i}' for i in range(count)]
    service.projections.invalidate()


async def traverse(client, endpoint, params):
    items, revision = [], None
    for _ in range(100):
        response = await client.get(endpoint, params=params)
        assert response.status == 200, await response.text()
        value = await response.json()
        if revision is not None:
            assert value['revision'] == revision
        revision = value['revision']
        items.extend(value['items'])
        if value['nextCursor'] is None:
            return items
        params = {**params, 'cursor': value['nextCursor']}
    raise AssertionError('Traversal did not finish')


async def test_full_traversal_filters_and_no_hydration(observation_host, monkeypatch):
    app, client = observation_host
    service = app['service']
    catalog(service)
    async def forbidden(*a, **kw):
        raise AssertionError('Observation hydrated history')
    monkeypatch.setattr(service.history, 'ensure_loaded', forbidden)
    monkeypatch.setattr(service, 'browser_state', lambda: pytest.fail('Observation built browser state'))
    sessions = await traverse(client, '/api/sessions', {'view': 'observation', 'limit': '50', 'pinned': 'true'})
    notices = await traverse(client, '/api/attention', {'limit': '200'})
    assert len(sessions) == len({r['id'] for r in sessions}) == 230
    assert len(notices) == len({r['id'] for r in notices}) == 460
    assert sum(r['kind'] == 'approval' for r in notices) == 230
    assert 'PRIVATE TOOL' not in str(notices)
    assert notices[1]['target'] == {'type': 'session', 'sessionId': 's0', 'approvalId': 'a0'}
    assert notices[1]['requiresAction'] and notices[1]['timestamp'] == 0
    assert not notices[0]['requiresAction']
    summary = await (await client.get('/api/activity')).json()
    assert summary['attentionCount'] == 460 and summary['requiresActionCount'] == 230


async def test_205_approvals_in_one_chat_are_all_retrievable(observation_host):
    app, client = observation_host
    catalog(app['service'], 1)
    row = app['service'].state['sessions'][0]
    row.pop('completion')
    row['approvals'] = [{'id': str(i)} for i in range(205)]
    rows = await traverse(client, '/api/attention', {'limit': '200'})
    assert len(rows) == len({r['id'] for r in rows}) == 205
    legacy = await (await client.get('/api/companion')).json()
    assert legacy['attentionCount'] == 205 and legacy['itemsTruncated']
    rest = await (await client.get('/api/companion', params={'itemsCursor': legacy['itemsNextCursor']})).json()
    assert len(rest['items']) == 5 and not rest['itemsTruncated']
    assert (await client.get('/api/companion?offset=200')).status == 400


async def test_lifecycle_and_delegate_semantics_shared_with_navigation(observation_host):
    from amplifier_web.navigation_summary import activity
    app, client = observation_host
    service = app['service']
    catalog(service, 7)
    rows = service.state['sessions']
    for row in rows:
        row.update(approvals=[], completion=None)
    rows[0]['historyLoading'] = True
    rows[1]['configurationBusy'] = True
    rows[2]['status'] = 'starting'
    rows[3]['status'] = 'working'
    rows[4].update(status='working', sessionKind='worker')
    rows[5].update(status='working', sessionKind='internal')
    rows[6].update(preparation={'status': 'preparing'})
    root = await (await client.get('/api/activity')).json()
    assert (root['executionCount'], root['preparingCount'], root['loadingCount']) == (1, 3, 1)
    assert (await (await client.get('/api/activity?scope=all')).json())['executionCount'] == 2
    delegate = await traverse(client, '/api/sessions', {'scope': 'delegates', 'activity': 'execution'})
    assert [r['id'] for r in delegate] == ['s4']
    assert activity(rows[0])['kind'] == 'idle'
    assert activity(rows[1])['label'] == 'Preparing'
    legacy = await (await client.get('/api/companion')).json()
    assert legacy['workingCount'] == 1


async def test_cursors_reject_changed_revision_query_and_malformed_parameters(observation_host):
    app, client = observation_host
    catalog(app['service'], 3)
    params = {'view': 'observation', 'limit': '1'}
    page = await (await client.get('/api/sessions', params=params)).json()
    cursor = page['nextCursor']
    for extra in [{'limit': '2'}, {'scope': 'all'}, {'cursor': cursor + 'x'}]:
        assert (await client.get('/api/sessions', params={**params, 'cursor': cursor, **extra})).status == 400
    assert (await client.get('/api/attention', params={'cursor': cursor})).status == 400
    app['service'].state['sessions'][0]['status'] = 'working'
    app['service'].projections.invalidate()  # Even saves without a global revision invalidate cursors.
    response = await client.get('/api/sessions', params={**params, 'cursor': cursor})
    assert response.status == 409
    assert (await response.json())['code'] == 'snapshot_changed'
    for path in ['/api/attention?offset=200', '/api/activity?offset=0', '/api/sessions?view=observation&offset=1',
                 '/api/attention?limit=no', '/api/attention?limit=201', '/api/attention?limit=1&limit=2',
                 '/api/sessions?bogus=1', '/api/sessions?offset=no']:
        assert (await client.get(path)).status == 400, path


async def test_etags_auth_and_no_new_work_for_web_clients(observation_host, monkeypatch):
    app, client = observation_host
    service = app['service']
    catalog(service, 1)
    observations = app['observations']
    assert observations.counts_cache is None
    with service.clients.bind('generic-api'):
        service.browser_state()
    assert observations.counts_cache is None
    response = await client.get('/api/activity')
    etag = response.headers['ETag']
    cached = observations.counts_cache
    assert (await client.get('/api/activity', headers={'If-None-Match': etag})).status == 304
    assert observations.counts_cache is cached
    for endpoint in ['/api/attention', '/api/sessions?view=observation']:
        response = await client.get(endpoint)
        assert (await client.get(endpoint, headers={'If-None-Match': response.headers['ETag']})).status == 304
    service.projections.invalidate()
    assert (await client.get('/api/activity', headers={'If-None-Match': etag})).status == 200
    for endpoint in ['/api/activity', '/api/attention', '/api/sessions?view=observation']:
        assert (await client.get(endpoint, headers={'Origin': 'https://evil.invalid'})).status == 403
        assert (await client.get(endpoint, headers={'Authorization': 'Bearer invalid'})).status == 401
        assert (await client.get(endpoint, headers={'X-Amplifier-Client': ''})).status == 400


async def test_read_fingerprint_race_and_acknowledgement_never_approves(observation_host):
    app, client = observation_host
    service = app['service']
    catalog(service, 1)
    row = service.state['sessions'][0]
    page = await (await client.get('/api/attention')).json()
    completion, approval = page['items']
    row['completion'] = {'id': 'new-completion', 'at': 2}
    service.projections.invalidate()
    await service.dispatch('attention.read', {'ids': [completion['id'], approval['id']],
        'fingerprints': {r['id']: r['fingerprint'] for r in [completion, approval]}})
    current = await (await client.get('/api/attention')).json()
    assert [r['id'] for r in current['items']] == [completion['id']]
    assert current['items'][0]['fingerprint'] != completion['fingerprint']
    assert row['approvals'][0]['status'] == 'pending'
    assert not service.runtime.sent
    assert len(await traverse(client, '/api/attention', {'read': 'true'})) == 1


async def test_pages_have_bounded_construction_and_no_query_cache_growth(tmp_path, monkeypatch):
    import amplifier_web.client_observations as module
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    original_sessions = service.state['sessions']
    try:
        catalog(service, 10000)
        observations = Observations(service)
        calls = 0
        original = module.session_item_at
        def observed(*args):
            nonlocal calls
            calls += 1
            return original(*args)
        monkeypatch.setattr(module, 'session_item_at', observed)
        for limit in range(1, 201):
            page = observations.page('attention', {'limit': str(limit)})
            assert len(page['items']) == limit
        assert calls <= 200 * SCAN_BUDGET
        assert observations.counts_cache is None and observations.pins_cache is None
        calls = 0
        # Resuming a dense single-session collection must seek, not rebuild its prefix.
        service.state['sessions'] = [dict(id='large', title='Large', approvals=[{'id': str(i)} for i in range(10000)])]
        service.projections.invalidate()
        first = observations.page('attention', {'limit': '200'})
        calls = 0
        second = observations.page('attention', {'limit': '200', 'cursor': first['nextCursor']})
        assert calls == 200 and second['items'][0]['id'] == 'approval:200'
        # Sparse filters can return empty progress pages, still bounded and traversable.
        catalog(service, 3000)
        service.state['sessions'][-1]['status'] = 'working'
        page = observations.page('sessions', {'activity': 'execution'})
        assert not page['items'] and page['nextCursor']
        found = []
        while page['nextCursor']:
            page = observations.page('sessions', {'activity': 'execution', 'cursor': page['nextCursor']})
            found.extend(page['items'])
        assert [r['id'] for r in found] == ['s2999']
    finally:
        service.state['sessions'] = original_sessions
        service.state['pinnedSessionIds'] = []
        service.projections.invalidate()
        await service.close()


async def test_large_counts_progress_is_bounded_and_restart_is_explicit(observation_host, monkeypatch):
    import amplifier_web.client_observations as module
    app, client = observation_host
    service = app['service']
    catalog(service, 10000)
    calls = 0
    original = module.session_item_at
    def observed(*args):
        nonlocal calls
        calls += 1
        return original(*args)
    monkeypatch.setattr(module, 'session_item_at', observed)
    response = await client.get('/api/activity')
    assert response.status == 202
    pending = await response.json()
    assert 'executionCount' not in pending and pending['nextCursor']
    assert calls <= module.COUNT_BUDGET
    service.projections.invalidate()
    assert (await client.get('/api/activity', params={'cursor': pending['nextCursor']})).status == 409
    params = {}
    for _ in range(30):
        calls = 0
        response = await client.get('/api/activity', params=params)
        value = await response.json()
        assert calls <= module.COUNT_BUDGET
        if response.status == 200:
            assert value['attentionCount'] == 20000 and value['requiresActionCount'] == 10000
            break
        assert response.status == 202 and value['status'] == 'collecting'
        params = {'cursor': value['nextCursor']}
    else:
        pytest.fail('Stable large catalog did not finish')
    calls = 0
    assert (await client.get('/api/activity')).status == 200 and calls == 0


async def test_generic_commands_keep_explicit_target_after_navigation_and_safe_retries(observation_host):
    app, client = observation_host
    service = app['service']
    with service.clients.bind('generic-api'):
        await service.dispatch('session.create', {'title': 'Target'})
        target = service.state['selectedSessionId']
        await service.dispatch('session.create', {'title': 'Selected elsewhere'})
        selected = service.state['selectedSessionId']
        await service.dispatch('view.update', {'patch': {'draft': 'Keep my draft'}})
    endpoint = f'/api/sessions/{target}/commands'
    payload = {'id': 'generic-reply', 'action': 'conversation.send',
               'args': {'text': 'Reply to target', 'preserveDraft': True}}
    assert (await client.post(endpoint, json=payload)).status == 200
    duplicate = await client.post(endpoint, json=payload)
    assert (await duplicate.json())['duplicate']
    assert [entry[0] for entry in service.runtime.sent] == [target]
    record = service.clients.records['generic-api']
    assert record['selectedSessionId'] == selected and record['view']['draft'] == 'Keep my draft'
    assert (await client.post(endpoint, json={**payload, 'args': {'text': 'Changed retry'}})).status == 409
    assert len(service.runtime.sent) == 1
    row = service._session(target)
    row['approvals'] = [{'id': 'approval-target', 'title': 'Run tool', 'status': 'pending'}]
    service.projections.invalidate()
    approvals = []
    async def approve(*args):
        approvals.append(args)
    service.runtime.approval = approve
    assert (await client.post(endpoint, json={'id': 'approve-once', 'action': 'approval.respond',
        'args': {'id': 'approval-target', 'decision': 'allow'}})).status == 200
    assert approvals == [(target, 'approval-target', 'allow')]
    assert (await client.post(endpoint, json={'id': 'stop-target', 'action': 'conversation.stop', 'args': {}})).status == 200
    assert service.runtime.stopped == [target]
