"""Bundled shell ownership shares the ordinary loss-safe browser state lane."""
from copy import deepcopy
import json
import sqlite3

from amplifier_web.server import create_app
from amplifier_web.shell_resources import scope_for
from amplifier_web.state_transport import delta
from test_service import Runtime
from test_shell_modules import settled_resources


async def setup(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
    service = app['service']
    # Keep this synthetic catalog stable: discovery is an independent, valid
    # navigation invalidation, outside the unrelated-observation assertions.
    await service.history.close()
    service.clients.attach('resource-browser')
    service.clients.attach('other-browser')
    with service.clients.bind('resource-browser'):
        scope = scope_for(service.state)
    return client, service, scope


async def bootstrap(client, scope, resources=('recentNavigation',), demand=1):
    return await client.get('/api/shell', params={
        'bundled': '1', 'clientId': 'resource-browser', 'demandId': str(demand),
        'navigationResources': json.dumps(resources), 'scope': json.dumps(scope)})


async def test_bootstrap_has_no_unused_snapshots_and_view_updates_stream_with_receipts(
        authenticated_client, tmp_path):
    client, service, scope = await setup(authenticated_client, tmp_path)
    response = await bootstrap(client, scope)
    assert response.status == 200
    body = await response.json()
    assert body['hostInstanceId'] == service.instance_id
    assert 'snapshots' not in body and 'registry' not in body
    assert set(body['shellResources']['instances']) == {'workspaces', 'chats'}
    assert len(body['instanceGenerations']) > 2
    await settled_resources(service, 'resource-browser')
    with service.clients.bind('resource-browser'):
        queue = service.subscribe()
        before = service.browser_state()
        saved = deepcopy(before)
    assert 'shellDataKey' not in before and 'shellChangeToken' not in before
    receipt_response = await client.post('/api/actions', headers={'X-Amplifier-Client': 'resource-browser'}, json={
        'action': 'shell.view.update', 'args': {'clientId': 'resource-browser',
        'instanceId': 'chats', 'generation': 0, 'patch': {'navFilter': 'only-this-module'}}})
    assert receipt_response.status == 200
    receipt = (await receipt_response.json())['result']['shellView']
    assert receipt['viewRevision'] == 1 and receipt['view']['navFilter'] == 'only-this-module'
    frame = queue.get_nowait()
    assert 'shellClientId' not in frame  # No signal that asks the browser to GET everything.
    context = frame['shellResources']['instances']['chats']['resources']['context']['value']
    assert context['viewRevision'] == receipt['viewRevision']
    assert context['view']['navFilter'] == receipt['view']['navFilter']
    assert before == saved  # Queued transport baselines are immutable.
    changes = delta(before, frame)['changes']
    assert any(item['path'][0] == 'shellResources' for item in changes)
    assert not any('recentNavigation' in item['path'] for item in changes)
    service.unsubscribe(queue)


async def test_scope_and_demand_fences_reject_stale_or_cross_client_reads_before_building(
        authenticated_client, tmp_path, monkeypatch):
    client, service, scope = await setup(authenticated_client, tmp_path)
    assert (await bootstrap(client, scope, demand=2)).status == 200
    await settled_resources(service, 'resource-browser')
    calls = []
    original = service.shell.navigation
    monkeypatch.setattr(service.shell, 'navigation', lambda *a, **kw: (calls.append(kw), original(*a, **kw))[1])
    payload = {'clientId': 'resource-browser', 'demandId': 2,
               'resources': ['recentNavigation'], 'scope': scope, 'reason': 'demand'}
    headers = {'X-Amplifier-Client': 'resource-browser'}
    assert (await client.post('/api/shell/resources', headers=headers, json=payload)).status == 200
    assert calls == []
    for patch in ({'demandId': 1}, {'resources': []}, {'scope': [None, None, 'workspaces', None, None]}):
        assert (await client.post('/api/shell/resources', headers=headers, json={**payload, **patch})).status == 409
    assert (await client.post('/api/shell/resources', headers=headers,
                             json={**payload, 'clientId': 'other-browser'})).status == 400
    assert calls == []
    changed = await client.post('/api/shell/resources', headers=headers,
                                json={**payload, 'demandId': 3, 'resources': []})
    assert changed.status == 200
    result = await changed.json()
    assert all(set(row['resources']) == {'context'} for row in result['instances'].values())
    assert all(not call['navigation_resources'] for call in calls)


async def test_unrelated_committed_observations_never_rebuild_navigation(
        authenticated_client, tmp_path, monkeypatch):
    client, service, scope = await setup(authenticated_client, tmp_path)
    assert (await bootstrap(client, scope)).status == 200
    await settled_resources(service, 'resource-browser')
    with service.clients.bind('resource-browser'):
        before = service.browser_state()['shellResources']
    def forbidden(*args, **kwargs):
        raise AssertionError('Unrelated observations must not build shell navigation')
    monkeypatch.setattr(service.shell, 'navigation', forbidden)
    for number in range(100):
        service._state['events'] = [{'id': str(number), 'action': 'fixture.telemetry'}]
        service._publish_changes(globals={'events'})
        with service.clients.bind('resource-browser'):
            observed = service.browser_state()['shellResources']
        assert observed == before


def test_canonical_write_reports_only_globals_that_actually_changed():
    from amplifier_web.state_records import initialize, save
    db = sqlite3.connect(':memory:')
    initialize(db)
    state = {'revision': 1, 'workspaces': [], 'events': []}
    assert save(db, state, {}, (), {'workspaces', 'events'}) == {'workspaces', 'events'}
    assert save(db, state, {}, (), {'workspaces', 'events'}) == set()
    # In-place mutation must invalidate; unrelated telemetry must not invalidate
    # an unchanged registry even when an unknown/full save writes both owners.
    state['workspaces'].append({'id': 'workspace'})
    assert save(db, state, {}, (), {'workspaces', 'events'}) == {'workspaces'}
    state['events'].append({'action': 'fixture.telemetry'})
    assert save(db, state, {}, (), {'workspaces', 'events'}) == {'events'}
    state.pop('workspaces')
    assert save(db, state, {}, (), {'workspaces', 'events'}) == {'workspaces'}
    db.close()


async def test_unregistered_and_restarted_web_frames_advertise_exact_host_bootstrap(
        authenticated_client, tmp_path, monkeypatch):
    import pytest
    client, service, scope = await setup(authenticated_client, tmp_path)
    headers = {'X-Amplifier-Client': 'resource-browser'}
    with service.clients.bind('resource-browser'):
        service.clients.draft(None, 'Keep this private restart draft')
    service._save_changes()
    monkeypatch.setattr(service.projections, 'shell_key',
                        lambda *_: pytest.fail('A bundled web frame must not build the legacy full shell key'))
    initial_response = await client.get('/api/state', headers=headers)
    assert initial_response.status == 200
    initial = await initial_response.json()
    assert initial['shellBootstrapKey'] and 'shellResources' not in initial
    assert 'shellDataKey' not in initial and 'shellChangeToken' not in initial
    before_response = await bootstrap(client, scope)
    assert before_response.status == 200
    before = await before_response.json()
    assert before['shellBootstrapKey'] == initial['shellBootstrapKey']
    assert before['shellResources']['bootstrapKey'] == initial['shellBootstrapKey']
    old_host = before['hostInstanceId']
    await client.close()

    client, restored, current_scope = await setup(authenticated_client, tmp_path)
    assert restored.clients.records['resource-browser']['drafts'][''] == 'Keep this private restart draft'
    monkeypatch.setattr(restored.projections, 'shell_key',
                        lambda *_: pytest.fail('Reconnect must advertise the new bootstrap without legacy projection'))
    response = await client.get('/api/state', headers=headers)
    assert response.status == 200
    fresh = await response.json()
    assert fresh['shellBootstrapKey'] != initial['shellBootstrapKey']
    assert fresh['client']['hostInstanceId'] != old_host
    assert 'shellResources' not in fresh
    assert 'shellDataKey' not in fresh and 'shellChangeToken' not in fresh
    current_response = await bootstrap(client, current_scope)
    assert current_response.status == 200
    current = await current_response.json()
    envelope = current['shellResources']
    assert current['shellBootstrapKey'] == fresh['shellBootstrapKey'] == envelope['bootstrapKey']
    assert current['hostInstanceId'] == fresh['client']['hostInstanceId'] == envelope['hostInstanceId']
    assert envelope['clientId'] == 'resource-browser' and envelope['demandId'] == 1
    assert envelope['scope'] == current_scope
    assert 'context' in envelope['instances']['chats']['resources']


async def test_explicit_retry_admits_current_new_demand_after_cooldown(authenticated_client, tmp_path):
    import time
    client, service, scope = await setup(authenticated_client, tmp_path)
    class Failed:
        calls = 0
        def __init__(self, *args, **kwargs):
            pass
        async def project(self, *args, **kwargs):
            Failed.calls += 1
            return {'status': 'retry', 'reason': 'worker_failed'}
        async def close(self):
            pass
    owner = service.shell.resources.queries
    owner.factory = Failed
    assert (await bootstrap(client, scope)).status == 200
    if owner.runner is not None:
        await owner.runner
    payload = {'clientId': 'resource-browser', 'demandId': 1, 'resources': ['recentNavigation'],
               'scope': scope, 'reason': 'retry'}
    headers = {'X-Amplifier-Client': 'resource-browser'}
    assert (await client.post('/api/shell/resources', headers=headers, json=payload)).status == 429
    before = Failed.calls
    for failure in owner.failures.values():
        failure['until'] = time.monotonic() - 1
    payload['demandId'] = 2
    response = await client.post('/api/shell/resources', headers=headers, json=payload)
    assert response.status == 200
    if owner.runner is not None:
        await owner.runner
    assert Failed.calls > before
    assert (await response.json())['demandId'] == 2
