"""Behavioral contracts for derived catalog and browser worker retention."""
from copy import deepcopy
import json
import pytest

from amplifier_web.browser_demand import project
from amplifier_web.browser_detail import project as conversation, page
from amplifier_web.provider_catalog import ProviderCatalog


def catalog_state():
    return {
        'selectedSessionId': 'chat', 'view': {}, 'configurationRevision': 1,
        'sessions': [{'id': 'chat', 'runtimeReport': {
            'effective_selection': {'instance': 'one', 'model': 'chosen'},
            'provider_choices': [{'id': 'one', 'sharedCatalogKey': 'current'}]}}],
        'runtimeControl': {'chat': {'configuration.catalog': {
            'configurationRevision': 1, 'providers': [{'id': 'one', 'sharedCatalogKey': 'current'}]}}},
        'modelCatalogs': {
            'current': {'phase': 'ready', 'models': [{'id': 'chosen'}], 'metadata': {
                'info': {'display_name': 'Provider'}, 'configSchema': {'fields': [
                    {'id': 'reasoning_effort', 'default': 'high'}, {'id': 'other', 'description': 'x' * 5000}]}}},
            'old-account': {'models': ['unused'] * 1000}},
        'setup': {'providers': [], 'providerCatalogs': {'old': {'models': ['unused'] * 1000}},
                  'metadata': {'old': {'description': 'x' * 5000}}}}


def test_closed_composer_does_not_receive_options_or_other_accounts():
    state = catalog_state()
    before = deepcopy(state)
    result = project(dict(state))
    assert state == before
    assert set(result['modelCatalogs']) == {'current'}
    assert 'models' not in result['modelCatalogs']['current']
    assert result['modelCatalogs']['current']['modelsDeferred'] is True
    assert result['modelCatalogs']['current']['metadata']['configSchema']['fields'] == [
        {'id': 'reasoning_effort', 'default': 'high'}]
    assert 'providerCatalogs' not in result['setup']
    opened = project({**state, 'view': {'composerModel': {'open': True, 'sessionId': 'chat'}}})
    assert opened['modelCatalogs']['current']['models'] == [{'id': 'chosen'}]
    assert 'old-account' not in opened['modelCatalogs']
    assert len(json.dumps(result)) < len(json.dumps(state)) / 5


async def test_private_cache_bounds_direct_put_restart_and_age(tmp_path):
    now = [1000.]
    path = tmp_path / 'catalog.json'
    cache = ProviderCatalog(path, clock=lambda: now[0], max_entries=2, max_bytes=1000, max_age=60)
    for i in range(3):
        now[0] += 1
        cache.put(('models', str(i)), {'models': [str(i)]})
    assert len(cache.entries) == len(cache.saved) == 2
    assert cache.peek(('models', '0')) is None
    restarted = ProviderCatalog(path, clock=lambda: now[0], max_entries=2, max_bytes=1000, max_age=60)
    assert restarted.peek(('models', '2')) == {'models': ['2']}
    now[0] += 61
    assert restarted.peek(('models', '2')) is None
    # Oversized results still return to their requester; they aren't retained.
    async def large(): return {'models': ['x' * 2000]}
    assert len((await cache.get('large', large))['models'][0]) == 2000
    assert cache.peek('large') is None
    await cache.close()


async def test_private_cache_has_one_budget_for_restores_and_failures(tmp_path):
    from amplifier_web.provider_catalog import fingerprint
    now=[1000.]
    path=tmp_path/'cache.json'
    first=ProviderCatalog(path,max_entries=3,max_bytes=600,clock=lambda:now[0])
    for i in range(3):
        now[0]+=1
        first.put(str(i),{'models':[str(i)]})
    cache=ProviderCatalog(path,max_entries=3,max_bytes=600,clock=lambda:now[0])
    async def fail():raise ValueError('Offline')
    for i in range(3):
        now[0]+=1
        with pytest.raises(ValueError):
            await cache.get('error-'+str(i),fail)
        for j in range(3):
            cache.peek(str(j))
            identities=set(cache.saved)|{fingerprint(k) for k in cache.entries}
            assert len(identities)<=3
            assert sum(cache.sizes.values())<=600
    assert path.stat().st_size<=600
    await cache.close()


def worker_chat():
    return {'id': 'chat', 'messages': [{'id': 'm-' + str(i), 'role': 'user', 'text': 'message'} for i in range(80)],
            'execution': {'nodes': [{'id': 'tool', 'kind': 'tool', 'turnId': 'turn', 'phase': 'completed',
                                     'anchorMessageId': 'm-79'}],
                          'turns': [{'id': 'turn', 'phase': 'completed'}]},
            'workers': [{'id': 'w-' + str(i), 'kind': 'session', 'callId': 'c-' + str(i),
                         'name': 'worker', 'status': 'completed', 'report': 'report ' * 1000}
                        for i in range(1000)]}


def test_worker_window_keeps_terminal_overrides_and_older_recovery_identity():
    state = worker_chat()
    state['workers'] += [
        {'id': 'late', 'kind': 'session', 'callId': 'live-call', 'status': 'working'},
        {'id': 'done', 'kind': 'job', 'callId': 'live-call', 'status': 'completed', 'event': 'job.returned'}]
    state['messages'][0]['observation'] = {'source': 'local-job-recovery', 'call_id': 'c-0'}
    before = deepcopy(state)
    result = conversation(state)
    assert state == before
    assert len(result['workers']) < 10
    assert {'late', 'done'} <= {r['id'] for r in result['workers']}
    assert result['workerSummary']['total'] == 1002
    assert result['workerSummary']['finished'] == 1001
    earlier = page(state, 'messages', before='m-20')
    assert earlier['items'][0]['recoveryWorker']['id'] == 'w-0'
    workers = page(state, 'workers')
    assert workers['total'] == 1002 and len(workers['items']) == 100
    assert len(state['workers']) == 1002


def test_legacy_timeline_retains_complete_compact_workers():
    state = worker_chat()
    state['execution'] = {'nodes': [], 'turns': []}
    result = conversation(state)
    assert len(result['workers']) == len(state['workers'])
    assert result['workers'][0]['reportDetail']['part'] == 'workers'


def test_filter_precedes_report_hashing_and_keeps_unknown_groups(monkeypatch):
    from amplifier_web import browser_detail
    state = worker_chat()
    state['workers'] += [{'id': status, 'status': status} for status in
                        ['idle', 'ready', 'pending', 'preparing', 'retrying', 'stopping', 'new-state']]
    def unexpected(text):
        raise AssertionError('Omitted historical worker reports must not be hashed')
    monkeypatch.setattr(browser_detail, 'digest', unexpected)
    result = conversation(state)
    assert {r['id'] for r in result['workers']} == {'idle', 'ready', 'pending', 'preparing', 'retrying', 'stopping', 'new-state'}
    assert result['workerSummary']['recentFinished'][-1]['id'] == 'new-state'


async def test_catalog_pruning_conserves_connected_running_and_incoming(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.conversation_models import publish_catalogs, browse
    from amplifier_web import draft_defaults
    from test_service import Runtime
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    service.management = Management(service)
    queues = []
    try:
        ids = []
        for key in ['viewed-a', 'viewed-b', 'running', 'dormant']:
            await service.dispatch('session.create', {})
            row = service._session()
            ids.append(row['id'])
            row['runtimeReport'] = {'provider_choices': [{'id': key, 'sharedCatalogKey': key}]}
            if key == 'running': row['status'] = 'working'
        for client, sid in zip(['client-a', 'client-b'], ids):
            service.clients.attach(client)
            with service.clients.bind(client):
                service.state['selectedSessionId'] = sid
                queues.append(service.subscribe())
        service._state['selectedSessionId'] = ids[0]
        service._state['setup'] = {'providerCatalogs': {'setup': {'sharedCatalogKey': 'setup'}}}
        service._save_full(reason='Synthetic catalog reference test')
        service._state['modelCatalogs'] = {k: {'models': [k]} for k in
                                         ['viewed-a', 'viewed-b', 'running', 'dormant', 'setup', 'obsolete']}
        assert publish_catalogs(service.management, {'incoming': {'models': ['new']}})
        assert set(service._state['modelCatalogs']) == {'viewed-a', 'viewed-b', 'running', 'setup', 'incoming'}
        assert not publish_catalogs(service.management, {'incoming': {'models': ['new']}})
        # Full-state readers also see derived eviction, never lost worker evidence.
        assert 'obsolete' not in service.get_state()['modelCatalogs']
        assert 'dormant' not in service.get_state()['modelCatalogs']
        dormant = service._session(ids[-1])
        dormant['selection'] = {'instance': 'saved', 'model': 'saved-model'}
        service._state['configurationRevision'] = 1
        async def resolve(*args, **kwargs):
            return {'providers': [{'id': 'current', 'sharedCatalogKey': 'current-account'}],
                    'catalogs': {'current-account': {'phase': 'ready', 'models': ['current-model'], 'metadata': {'info': {}}}}}
        monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
        await browse(service.management, dormant)
        full = service.get_state()
        assert 'dormant' not in full['modelCatalogs']
        assert full['modelCatalogs']['current-account']['models'] == ['current-model']
        assert full['runtimeControl'][dormant['id']]['configuration.catalog']['providers'][0]['id'] == 'current'
        assert dormant['selection'] == {'instance': 'saved', 'model': 'saved-model'}
    finally:
        for queue in queues: service.unsubscribe(queue)
        await service.close()


async def test_unchanged_conversation_catalog_does_not_publish(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.conversation_models import browse
    from amplifier_web import draft_defaults
    from test_service import Runtime
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    service.management = Management(service)
    try:
        await service.dispatch('session.create', {})
        row = service._session()
        async def resolve(*args, **kwargs):
            return {'providers': [{'id': 'one', 'sharedCatalogKey': 'key'}],
                    'effective': {'instance': 'one', 'model': 'model'},
                    'catalogs': {'key': {'phase': 'ready', 'models': ['model'], 'metadata': {'info': {}}, 'supported': True}}}
        monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
        calls = []
        original = service._publish_changes
        def publish(**kwargs):
            calls.append(kwargs)
            original(**kwargs)
        monkeypatch.setattr(service, '_publish_changes', publish)
        await browse(service.management, row)
        await browse(service.management, row)
        assert len(calls) == 1
    finally:
        await service.close()


@pytest.mark.parametrize('reverse', [False, True])
async def test_recovery_identity_initial_older_and_focused_http(authenticated_client, tmp_path, reverse):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path/'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {})
    session = service._session()
    source = worker_chat()
    source['workers'] = [
        {'id': 'job', 'kind': 'job', 'toolCallId': 'call', 'status': 'completed', 'name': 'First'},
        {'id': 'child', 'kind': 'session', 'callId': 'call', 'status': 'completed', 'name': 'Second'}]
    if reverse: source['workers'].reverse()
    for index in (0, 79):
        source['messages'][index]['observation'] = {'source': 'local-job-recovery', 'call_id': 'call'}
    session.update({k:v for k,v in source.items() if k != 'id'})
    session.update(historyManaged=False, historyLoaded=True)
    expected = source['workers'][0]['id']
    before = deepcopy(session['workers'])
    initial = conversation(session)
    assert initial['messages'][-1]['recoveryWorker']['id'] == expected
    older = await client.get(f"/api/conversation/detail?sessionId={session['id']}&part=messages&before=m-20")
    assert older.status == 200
    assert (await older.json())['items'][0]['recoveryWorker']['id'] == expected
    focused = await client.get(f"/api/conversation/navigation?sessionId={session['id']}&messageId=m-0&window=true")
    assert focused.status == 200
    assert (await focused.json())['messages'][0]['recoveryWorker']['id'] == expected
    workers = await client.get(f"/api/conversation/detail?sessionId={session['id']}&part=workers")
    assert workers.status == 200 and len((await workers.json())['items']) == 2
    assert session['workers'] == before


async def test_worker_summary_cached_invalidation_and_full_evidence(tmp_path):
    from amplifier_web.service import AppService
    from test_service import Runtime
    service = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    try:
        await service.dispatch('session.create', {})
        row = service._session()
        row.update({k:v for k,v in worker_chat().items() if k != 'id'})
        service._save_changes(sessions={row['id']})
        first = deepcopy(service.projections.detail(row))
        assert first['workerSummary']['total'] == 1000 and first['workers'] == []
        row['workers'][0]['status'] = 'running'
        service._save_changes(sessions={row['id']})
        current = service.projections.detail(row)
        assert current['workerSummary']['finished'] == 999
        assert current['workers'][0]['id'] == 'w-0'
        assert first['workerSummary']['finished'] == 1000 and first['workers'] == []
        assert len(service.get_state()['sessions'][0]['workers']) == 1000
        row['execution'] = {'nodes': [], 'turns': []}
        service._save_changes(sessions={row['id']})
        legacy = service.projections.detail(row)
        assert 'workerSummary' not in legacy and len(legacy['workers']) == 1000
    finally:
        await service.close()