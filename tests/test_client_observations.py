from copy import deepcopy
import pytest
from amplifier_web.server import create_app
from amplifier_web.client_observations import Observations, SCAN_BUDGET, MAX_TRAVERSALS
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


def replace_catalog(service, rows):
    # Dropping residency is not deletion. This synthetic fixture explicitly
    # replaces durable membership, then retains a small working set for large
    # libraries just as the host now does.
    directory = service.directory
    identities = [row[0] for row in service.db.execute('SELECT id FROM history_local_rows')]
    directory.sync([], service.state, deleted=identities)
    for row in rows:
        row.setdefault('workspace', service.default_workspace)
    service.state['sessions'] = rows
    if len(rows) > 1000:
        directory.sync(rows, service.state)
        service.state['sessions'] = rows[:6]
    service.db.commit()
    service.projections.invalidate()


def catalog(service, count=230):
    rows = [dict(id=f's{i}', title=f'Chat {i}', status='idle', sessionKind='root', workspace=service.default_workspace,
        messages=[], workers=[], approvals=[{'id': f'a{i}', 'title': 'PRIVATE TOOL', 'status': 'pending', 'createdAt': i}],
        completion={'id': f'c{i}', 'at': i}) for i in range(count)]
    service.state['pinnedSessionIds'] = [f's{i}' for i in range(count)]
    replace_catalog(service, rows)


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


async def test_cursors_preserve_snapshots_and_reject_changed_query_and_malformed_parameters(observation_host):
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
    assert response.status == 200
    assert (await response.json())['revision'] == page['revision']
    fresh = await (await client.get('/api/sessions', params=params)).json()
    assert fresh['revision'] != page['revision'] and fresh['items'][0]['status'] == 'working'
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
    assert observations.counts_cache is None and observations.facts is None and not observations.dirty
    response = await client.get('/api/activity')
    etag = response.headers['ETag']
    cached = observations.counts_cache
    assert (await client.get('/api/activity', headers={'If-None-Match': etag})).status == 304
    assert observations.counts_cache is cached
    for endpoint in ['/api/attention', '/api/sessions?view=observation']:
        response = await client.get(endpoint)
        assert (await client.get(endpoint, headers={'If-None-Match': response.headers['ETag']})).status == 304
    service.projections.invalidate()
    assert (await client.get('/api/activity', headers={'If-None-Match': etag})).status == 304
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
        observations.refresh()  # One cold public-facts index, shared across queries.
        calls = 0
        for limit in range(1, MAX_TRAVERSALS + 1):
            page = observations.page('attention', {'limit': str(limit)})
            assert len(page['items']) == limit
        assert calls <= (module.DirectoryFacts.BLOCK + 6) * 5  # One block plus six live metadata owners.
        assert len(observations.snapshots) == 1
        assert len(observations.leases) == MAX_TRAVERSALS
        with pytest.raises(AppError):
            observations.page('attention', {'limit': str(MAX_TRAVERSALS + 1)})
        # These intentionally abandoned queries expire before the next scenario.
        observations.leases.clear()
        assert observations.counts_cache is None
        calls = 0
        # Resuming a dense single-session collection must seek, not rebuild its prefix.
        replace_catalog(service, [dict(id='large', title='Large', approvals=[{'id': str(i)} for i in range(10000)])])
        service.projections.invalidate()
        first = observations.page('attention', {'limit': '200'})
        calls = 0
        second = observations.page('attention', {'limit': '200', 'cursor': first['nextCursor']})
        assert calls == 0 and second['items'][0]['id'] == 'approval:200'
        # Indexed sparse filters seek directly to matching dormant metadata.
        catalog(service, 3000)
        last = service.directory.resolve('s2999')
        last['status'] = 'working'
        service.directory.sync([last], service.state)
        service.db.commit()
        page = observations.page('sessions', {'activity': 'execution'})
        assert [row['id'] for row in page['items']] == ['s2999']
        found = list(page['items'])
        while page['nextCursor']:
            page = observations.page('sessions', {'activity': 'execution', 'cursor': page['nextCursor']})
            found.extend(page['items'])
        assert [r['id'] for r in found] == ['s2999']
    finally:
        service.state['sessions'] = original_sessions
        service.state['pinnedSessionIds'] = []
        service.projections.invalidate()
        await service.close()


async def test_large_counts_progress_survives_unrelated_invalidation(observation_host, monkeypatch):
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
    app['observations'].refresh()
    calls = 0
    response = await client.get('/api/activity')
    assert response.status == 202
    pending = await response.json()
    assert 'executionCount' not in pending and pending['nextCursor']
    assert calls <= module.COUNT_BUDGET
    service.projections.invalidate()
    assert (await client.get('/api/activity', params={'cursor': pending['nextCursor']})).status == 202
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


async def test_busy_host_keeps_semantic_revisions_and_finishes_large_reads(observation_host, monkeypatch):
    app, client = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 10000)
    session_params = {'view': 'observation', 'limit': '200'}
    notice_params = {'limit': '200'}
    first = await client.get('/api/sessions', params=session_params)
    session_etag = first.headers['ETag']
    page = await first.json()
    notice_first = await client.get('/api/attention', params=notice_params)
    notice_etag = notice_first.headers['ETag']
    notices = await notice_first.json()
    activity = await (await client.get('/api/activity')).json()
    revisions = (page['revision'], notices['revision'], activity['revision'])
    sessions, notice_ids = [r['id'] for r in page['items']], [r['id'] for r in notices['items']]
    calls = 0
    original = observations.row_facts
    def observed(*args):
        nonlocal calls
        calls += 1
        return original(*args)
    monkeypatch.setattr(observations, 'row_facts', observed)
    for step in range(110):
        # Real command/save path, not a simulated generation increment. Continue
        # through several full count batches while browser drafts are changing.
        if step < 8:
            with service.clients.bind('generic-api'):
                await service.dispatch('view.update', {'patch': {'draft': f'Draft {step}'}}, include_state=False)
            assert (await client.get('/api/sessions', params=session_params,
                headers={'If-None-Match': session_etag})).status == 304
            assert (await client.get('/api/attention', params=notice_params,
                headers={'If-None-Match': notice_etag})).status == 304
        # A proven transcript-only publication costs no catalog reconciliation.
        calls = 0
        service._state['sessions'][0]['messages'] = [{'role': 'assistant', 'content': f'Token {step}'}]
        service.projections.invalidate(state=service._state, session_ids={'s0'}, detail_only=True)
        if page['nextCursor']:
            page = await (await client.get('/api/sessions', params={**session_params, 'cursor': page['nextCursor']})).json()
            assert page['revision'] == revisions[0]
            sessions.extend(r['id'] for r in page['items'])
        if notices['nextCursor']:
            notices = await (await client.get('/api/attention', params={**notice_params, 'cursor': notices['nextCursor']})).json()
            assert notices['revision'] == revisions[1]
            notice_ids.extend(r['id'] for r in notices['items'])
        if activity['nextCursor']:
            activity = await (await client.get('/api/activity', params={'cursor': activity['nextCursor']})).json()
            assert activity['revision'] == revisions[2]
        assert calls <= 4096  # Three bounded page/count blocks, never all 10k.
        if not any((page['nextCursor'], notices['nextCursor'], activity['nextCursor'])):
            break
    else:
        pytest.fail('Busy host did not finish traversal')
    assert len(sessions) == len(set(sessions)) == 10000
    assert len(notice_ids) == len(set(notice_ids)) == 20000
    assert activity['attentionCount'] == 20000 and activity['requiresActionCount'] == 10000
    response = await client.get('/api/activity')
    with service.clients.bind('generic-api'):
        await service.dispatch('view.update', {'patch': {'draft': 'Final draft'}}, include_state=False)
    assert (await client.get('/api/activity', headers={'If-None-Match': response.headers['ETag']})).status == 304


async def test_independent_dependencies_and_coherent_snapshots_during_real_changes(observation_host):
    app, client = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 30)
    params = {'limit': '1'}
    sessions = observations.page('sessions', params)
    notices = observations.page('attention', params)
    activity = observations.activity()
    row = service._state['sessions'][0]
    row['status'] = 'working'
    service.projections.invalidate(state=service._state, session_ids={'s0'})
    assert observations.page('sessions', params)['revision'] != sessions['revision']
    assert observations.page('attention', params)['revision'] == notices['revision']
    assert observations.activity()['executionCount'] == 1
    session_revision = observations.page('sessions', params)['revision']
    completion = notices['items'][0]
    service._state['attentionRead'] = {completion['id']: completion['fingerprint']}
    service.projections.invalidate(state=service._state, session_ids=set())
    assert observations.page('sessions', params)['revision'] == session_revision
    assert observations.page('attention', params)['revision'] != notices['revision']
    assert observations.activity()['attentionCount'] == 59
    notice_revision = observations.page('attention', params)['revision']
    activity_revision = observations.activity()['revision']
    service._state['pinnedSessionIds'] = []
    service.projections.pins_changed(service._state)
    assert not observations.page('sessions', params)['items'][0]['pinned']
    assert observations.page('attention', params)['revision'] == notice_revision
    assert observations.activity()['revision'] == activity_revision
    # Every continuation still refers to its immutable original catalog, even
    # when a row is deleted and another row changes between every request.
    ids = [sessions['items'][0]['id']]
    while sessions['nextCursor']:
        service._state['sessions'][-1]['status'] = 'running' if len(ids) % 2 else 'idle'
        if len(ids) == 2:
            service._state['sessions'].pop(0)
        service.projections.invalidate()
        sessions = observations.page('sessions', {**params, 'cursor': sessions['nextCursor']})
        ids.extend(r['id'] for r in sessions['items'])
    assert ids == [f's{i}' for i in range(30)]
    assert observations.page('sessions', params)['items'][0]['id'] == 's1'


async def test_snapshot_capacity_preserves_existing_readers_and_expiry_is_explicit(observation_host, monkeypatch):
    import amplifier_web.client_observations as module
    app, client = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 20)
    now = 1000
    monkeypatch.setattr(module.time, 'monotonic', lambda: now)
    cursors = []
    for i in range(module.MAX_SNAPSHOTS):
        service._state['sessions'][0]['title'] = f'Chat {i}'
        service.projections.invalidate()
        cursors.append(observations.page('sessions', {'limit': '1'})['nextCursor'])
    service._state['sessions'][0]['title'] = 'One more'
    service.projections.invalidate()
    with pytest.raises(AppError) as error:
        observations.page('sessions', {'limit': '1'})
    assert error.value.code == 'observation_busy'
    for cursor in cursors:
        assert observations.page('sessions', {'limit': '1', 'cursor': cursor})['items'][0]['id'] == 's1'
    now += module.SNAPSHOT_SECONDS
    with pytest.raises(AppError) as error:
        observations.page('sessions', {'limit': '1', 'cursor': cursors[0]})
    assert error.value.code == 'snapshot_changed'
    assert observations.page('sessions', {'limit': '1'})['items'][0]['title'] == 'One more'


async def test_large_count_finishes_original_totals_during_sustained_changes(observation_host, monkeypatch):
    app, client = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 10000)
    counts = observations.activity()
    original_revision = counts['revision']
    original = observations.row_facts
    calls = 0
    def observed(*args):
        nonlocal calls
        calls += 1
        return original(*args)
    monkeypatch.setattr(observations, 'row_facts', observed)
    for step in range(30):
        if not counts['nextCursor']:
            break
        calls = 0
        row = service._state['sessions'][step % 6]
        row.update(status='working', completion={'id': f'changed-{step}', 'at': step + 1})
        service.projections.invalidate(state=service._state, session_ids={row['id']})
        counts = observations.activity(cursor=counts['nextCursor'])
        assert calls <= 4096  # Scoped changes plus the bounded count page, never all 10k.
        assert counts['revision'] == original_revision
    else:
        pytest.fail('Real changes starved count continuation')
    assert counts['executionCount'] == 0
    assert counts['attentionCount'] == 20000 and counts['requiresActionCount'] == 10000
    current = observations.activity()
    while current['nextCursor']:
        current = observations.activity(cursor=current['nextCursor'])
    assert current['revision'] != original_revision and current['executionCount'] > 0


async def test_completed_http_traversals_and_large_counts_do_not_exhaust_slots(observation_host):
    import amplifier_web.client_observations as module
    app, client = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 230)
    for generation in range(40):
        service._state['sessions'][0]['status'] = 'working' if generation % 2 else 'idle'
        service.projections.invalidate(state=service._state, session_ids={'s0'})
        rows = await traverse(client, '/api/sessions', {'view': 'observation', 'limit': '200'})
        assert len(rows) == 230
        assert not observations.leases
        assert len(observations.snapshots) <= 1
    catalog(service, 10000)
    for generation in range(24):
        service._state['sessions'][0]['status'] = 'working' if generation % 2 else 'idle'
        service.projections.invalidate(state=service._state, session_ids={'s0'})
        params = {}
        for _ in range(20):
            response = await client.get('/api/activity', params=params)
            assert response.status in (200, 202), await response.text()
            value = await response.json()
            if value['nextCursor'] is None:
                break
            params = {'cursor': value['nextCursor']}
        else:
            pytest.fail('Large count did not finish')
        assert value['executionCount'] == generation % 2
        assert value['attentionCount'] == 20000
        assert params.get('cursor')
        retry = await client.get('/api/activity', params=params)
        assert retry.status == 200 and await retry.json() == value
        assert not observations.leases
        assert len(observations.snapshots) <= 2
        assert len(observations.completed) <= 2 * module.MAX_COMPLETED


async def test_concurrent_readers_survive_completion_and_terminal_retry_reclaims_catalog(observation_host, monkeypatch):
    import amplifier_web.client_observations as module
    app, client = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 230)
    now = 1000
    monkeypatch.setattr(module.time, 'monotonic', lambda: now)
    params = {'limit': '200'}
    for identity in ('reader-a', 'reader-b'):
        response = await client.post('/api/clients/attach', json={
            'clientId': identity, 'kind': 'api', 'protocolVersion': 1, 'includeState': False})
        assert response.status == 200
    with service.clients.bind('reader-a'):
        first_a = observations.page('sessions', params)
        assert observations.page('sessions', params) == first_a  # First-page retry/ETag is stable.
    with service.clients.bind('reader-b'):
        first_b = observations.page('sessions', params)
    assert first_a['nextCursor'] != first_b['nextCursor']
    terminal_a = observations.page('sessions', {**params, 'cursor': first_a['nextCursor']})
    assert not terminal_a['nextCursor'] and len(observations.leases) == 1
    assert observations.page('sessions', {**params, 'cursor': first_a['nextCursor']}) == terminal_a
    original_revision = first_a['revision']
    # Keep B unfinished through substantially more generations than snapshot slots.
    for generation in range(25):
        service._state['sessions'][0]['title'] = f'Generation {generation}'
        service.projections.invalidate(state=service._state, session_ids={'s0'})
        first = observations.page('sessions', params)
        observations.page('sessions', {**params, 'cursor': first['nextCursor']})
        assert len(observations.leases) == 1 and len(observations.snapshots) <= 2
    terminal_b = observations.page('sessions', {**params, 'cursor': first_b['nextCursor']})
    assert terminal_b['revision'] == original_revision
    assert [row['id'] for row in terminal_b['items']] == [f's{i}' for i in range(200, 230)]
    assert original_revision not in observations.snapshots  # No reader pins the old catalog now.
    assert observations.page('sessions', {**params, 'cursor': first_a['nextCursor']}) == terminal_a
    now += module.RETRY_SECONDS
    with pytest.raises(AppError) as error:
        observations.page('sessions', {**params, 'cursor': first_a['nextCursor']})
    assert error.value.code == 'snapshot_changed'
    assert not observations.completed


async def test_collection_capacity_isolated_and_only_terminal_cursor_can_retry(observation_host, monkeypatch):
    import amplifier_web.client_observations as module
    app, client = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 230)
    params = {'limit': '100'}
    first = observations.page('sessions', params)
    second = observations.page('sessions', {**params, 'cursor': first['nextCursor']})
    last = observations.page('sessions', {**params, 'cursor': second['nextCursor']})
    assert observations.page('sessions', {**params, 'cursor': second['nextCursor']}) == last
    with pytest.raises(AppError) as error:
        observations.page('sessions', {**params, 'cursor': first['nextCursor']})
    assert error.value.code == 'snapshot_changed'
    for generation in range(module.MAX_SNAPSHOTS):
        service._state['sessions'][0]['title'] = str(generation)
        service.projections.invalidate()
        observations.page('sessions', {'limit': '1'})
    service._state['sessions'][0]['title'] = 'Capacity'
    service.projections.invalidate()
    with pytest.raises(AppError):
        observations.page('sessions', {'limit': '1'})
    # Exhausting sessions cannot deny either other collection its first read.
    assert observations.page('attention', {'limit': '200'})['items']
    assert observations.activity()['attentionCount'] == 460
    assert len(observations.snapshots) <= 3 * module.MAX_SNAPSHOTS


async def test_terminal_retry_cache_is_bounded_without_retaining_old_snapshots(observation_host):
    import amplifier_web.client_observations as module
    app, _ = observation_host
    service, observations = app['service'], app['observations']
    catalog(service, 3)
    oldest = None
    for generation in range(module.MAX_COMPLETED + 5):
        service._state['sessions'][0]['title'] = str(generation)
        service.projections.invalidate(state=service._state, session_ids={'s0'})
        first = observations.page('sessions', {'limit': '2'})
        oldest = oldest or first['nextCursor']
        terminal = observations.page('sessions', {'limit': '2', 'cursor': first['nextCursor']})
        assert terminal['nextCursor'] is None
        assert not observations.leases and not observations.snapshots
        assert len(observations.completed) <= module.MAX_COMPLETED
    with pytest.raises(AppError) as error:
        observations.page('sessions', {'limit': '2', 'cursor': oldest})
    assert error.value.code == 'snapshot_changed'


async def test_agent_creation_classification_is_shared_with_navigation(observation_host):
    app, client = observation_host
    service = app['service']
    catalog(service, 3)
    rows = service.state['sessions']
    rows[1]['collaboration'] = {'creatorSessionId': 's0', 'requestId': 'peer-request'}
    rows[2]['collaboration'] = {'creatorSessionId': 's0'}  # Incomplete evidence is not enough.
    service.projections.invalidate()
    sessions = await traverse(client, '/api/sessions', {'view': 'observation'})
    assert {row['id']: row['agentCreated'] for row in sessions} == {'s0': False, 's1': True, 's2': False}
    notices = await traverse(client, '/api/attention', {})
    assert all(row['agentCreated'] == (row['target']['sessionId'] == 's1') for row in notices)
    response = await client.get('/api/companion')
    assert response.status == 200
    adapter = await response.json()
    assert next(row for row in adapter['sessions'] if row['id'] == 's1')['agentCreated']
    assert all(row['agentCreated'] == (row['sessionId'] == 's1') for row in adapter['items'])
    rows[1].pop('collaboration')
    service.projections.invalidate()
    changed = await traverse(client, '/api/sessions', {'view': 'observation'})
    assert not any(row['agentCreated'] for row in changed)
