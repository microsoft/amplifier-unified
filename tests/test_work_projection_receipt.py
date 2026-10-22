"""Completion evidence belongs to an accepted host read, not rendered headers."""
import asyncio
import copy
import gc
import json
import weakref
from types import SimpleNamespace

import pytest

from amplifier_web.event_log_view import EventLogView
from amplifier_web.history_read_model import SelectedHistoryReadModel
from test_event_log_view import append, source


class Owner(dict):
    pass


@pytest.fixture
def observed(source, tmp_path):
    raw, path = source
    owner = Owner(raw)
    current = [owner]
    publications, notifications = [], []
    service = SimpleNamespace(instance_id='host-one', _session=lambda _: current[0],
                              lock=asyncio.Lock(), closed=False)
    view = EventLogView(service)
    def publish(**kwargs):
        view.invalidate(kwargs.get('session_ids'))
        publications.append(copy.deepcopy(current[0].get('workProjection')))
    service._publish = publish
    service.clients = SimpleNamespace(records={'browser': {'kind': 'web', 'selectedSessionId': 'app'}})
    service.shell = SimpleNamespace(resources=SimpleNamespace(clients={'browser': {}}),
        notify=lambda _: notifications.append(copy.deepcopy(current[0].get('workProjection'))))
    service.notifications = notifications
    model = SelectedHistoryReadModel(tmp_path / 'models')
    class Projector:
        async def project(self, inputs, *, key):
            return await asyncio.to_thread(model.project, inputs)
    view.projector = Projector()
    append(path, 'tool:pre', {'tool_call_id': 'one', 'tool_name': 'bash'})
    yield view, current, path, publications
    model.close()


async def test_receipt_is_only_published_after_accepted_projection_and_quiet_reads_do_not_churn(observed, monkeypatch):
    view, current, _, publications = observed
    owner = current[0]
    original = view.projector.project
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(view.projector, 'project', held)
    task = asyncio.create_task(view.refresh('app', passive=True))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert owner['workProjection'] == {'status': 'loading', 'current': False}
        release.set()
        await asyncio.wait_for(task, 3)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    receipt = owner['workProjection']['receipt']
    assert owner['workProjection'] == {'status': 'ready', 'current': True, 'receipt': receipt}
    assert receipt == {'hostInstanceId': 'host-one', 'sessionId': 'app', 'generation': 1,
        'sourceCount': len(view.read_revisions['app']),
        'nodeCount': len(owner['execution']['nodes']), 'turnCount': len(owner['execution']['turns'])}
    assert len(publications) == 1 and publications[-1] == owner['workProjection']
    assert view.service.notifications == [{'status': 'loading', 'current': False}]
    monkeypatch.setattr(view, 'projection_input', lambda _: pytest.fail('quiet read rebuilt inputs'))
    monkeypatch.setattr(view.projector, 'project', lambda *a, **k: pytest.fail('quiet read projected'))
    for _ in range(20):
        await view.refresh('app', passive=True)
    assert len(publications) == 1 and len(view.service.notifications) == 1
    assert owner['workProjection']['receipt'] is receipt


async def test_invalidation_retires_current_before_publication_and_revalidation_restores_it(observed):
    view, current, _, publications = observed
    await view.refresh('app', passive=True)
    owner = current[0]
    accepted = copy.deepcopy(owner['workProjection'])
    view.invalidate({'other'})
    assert owner['workProjection'] == accepted
    view.service._publish(session_ids={'app'})
    assert publications[-1] == {'status': 'loading', 'current': False, 'receipt': accepted['receipt']}
    assert accepted['current'] is True, 'Invalidation must not mutate an issued snapshot'
    await view.refresh('app', passive=True)
    assert owner['workProjection']['current'] is True
    assert owner['workProjection']['receipt']['generation'] > accepted['receipt']['generation']


async def test_explicit_reference_refresh_does_not_masquerade_as_process_receipt(observed):
    view, current, path, _ = observed
    await view.refresh('app', passive=True)
    prior = current[0]['workProjection']['receipt']
    view.invalidate({'app'})
    append(path, 'tool:post', {'tool_call_id': 'one', 'result': 'finished'}, 12)
    await view.refresh('app')
    assert current[0]['workProjection']['current'] is False
    await view.refresh('app', passive=True)
    assert current[0]['workProjection']['current'] is True
    assert current[0]['workProjection']['receipt']['generation'] > prior['generation']


@pytest.mark.parametrize('failure,status', [('timeout', 'retrying'), ('input_limit', 'unavailable')])
async def test_changed_source_pending_and_failure_retain_only_noncurrent_receipt(observed, monkeypatch, failure, status):
    view, current, path, publications = observed
    await view.refresh('app', passive=True)
    receipt = current[0]['workProjection']['receipt']
    append(path, 'tool:post', {'tool_call_id': 'one', 'result': 'finished'}, 12)
    async def fail(*args, **kwargs):
        assert current[0]['workProjection'] == {'status': 'loading', 'current': False, 'receipt': receipt}
        assert view.service.notifications[-1] == current[0]['workProjection']
        return {'status': 'retry', 'reason': failure}
    monkeypatch.setattr(view.projector, 'project', fail)
    await view.refresh('app', passive=True)
    assert current[0]['workProjection'] == {'status': status, 'current': False,
        'reason': failure, 'receipt': receipt}
    assert len(publications) == 1, 'Status-only delivery must not persist unchanged execution'
    assert view.service.notifications[-1] == current[0]['workProjection']


@pytest.mark.parametrize('mutation', ['published-input', 'unpublished-input', 'stale-source', 'superseded-owner'])
async def test_rejected_projection_never_promotes_retained_receipt(observed, monkeypatch, mutation):
    view, current, path, _ = observed
    await view.refresh('app', passive=True)
    receipt = current[0]['workProjection']['receipt']
    append(path, 'tool:post', {'tool_call_id': 'one', 'result': 'finished'}, 12)
    original = view.projector.project
    async def rejected(*args, **kwargs):
        result = await original(*args, **kwargs)
        if mutation.endswith('input'):
            current[0]['messages'].append({'id': 'later', 'role': 'user', 'createdAt': 15})
            if mutation == 'published-input':
                view.invalidate({'app'})
        elif mutation == 'stale-source':
            append(path, 'tool:pre', {'tool_call_id': 'late', 'tool_name': 'read'}, 15)
        else:
            current[0] = Owner(copy.deepcopy(current[0]))
        return result
    monkeypatch.setattr(view.projector, 'project', rejected)
    await view.refresh('app', passive=True)
    assert current[0]['workProjection'] == {'status': 'loading', 'current': False, 'receipt': receipt}
    assert view._work_generation == receipt['generation']


def test_counts_do_not_walk_bodies_and_weak_receipts_retire_at_existing_bound(tmp_path):
    class NoWalk(list):
        def __iter__(self):
            pytest.fail('receipt traversed a body')
    view = EventLogView(SimpleNamespace(instance_id='host', data_dir=tmp_path))
    owners = []
    for number in range(65):
        owner = Owner(id=str(number), execution={'nodes': NoWalk([None] * 40000), 'turns': NoWalk([None] * 50)})
        view._accept_work(owner, NoWalk([None] * 268))
        view._remember_clean(owner)
        owners.append(owner)
    assert owners[0]['workProjection']['current'] is False
    assert len(view._clean_inputs) == 64
    assert owners[-1]['workProjection']['receipt']['nodeCount'] == 40000
    assert len(json.dumps(owners[-1]['workProjection'])) < 1024
    reference = weakref.ref(owners[1])
    owners[1] = None
    gc.collect()
    assert reference() is None


async def test_old_host_receipt_is_not_retained_or_saved(observed, tmp_path):
    from amplifier_web.session_projection import persist, view_path
    view, current, _, _ = observed
    await view.refresh('app', passive=True)
    owner = current[0]
    persist(tmp_path / 'persisted', {'sessions': [owner]}, {})
    assert 'workProjection' not in json.loads(view_path(tmp_path / 'persisted', owner).read_text())
    fresh = EventLogView(SimpleNamespace(instance_id='host-two'))
    fresh._set_work_status(owner, 'loading')
    assert owner['workProjection'] == {'status': 'loading', 'current': False}


async def test_projected_and_weak_receipt_lru_divergence_retires_both_claims(observed):
    view, _, _, _ = observed
    owners = [Owner(id=f'old-{number}', execution={'nodes': [], 'turns': []}) for number in range(64)]
    for owner in owners:
        view._accept_work(owner, ())
        view.projected[owner['id']] = ('old-digest', ())
        view._remember_clean(owner)
    # Quiet source reuse updates clean-input recency independently of projected.
    view._remember_clean(owners[0])
    await view.refresh('app', passive=True)
    assert owners[0]['id'] not in view.projected
    assert owners[1]['id'] in view.projected
    assert all(owner['workProjection']['current'] is False for owner in owners[:2])


async def test_cold_retirement_invalidates_receipt_without_loading_bodies(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from test_cold_display import add_history
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        owner = add_history(app, 'retiring')
        owner['execution'] = {'nodes': [{'id': 'saved', 'kind': 'tool', 'output': 'x' * 20000}], 'turns': []}
        app._publish(session_ids={owner['id']}, record_only=True)
        view = app.event_log_view
        view._accept_work(owner, ())
        view._remember_clean(owner)
        receipt = owner['workProjection']['receipt']
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        app.cold_display.retire(force=True)
        assert app.cold_display.is_cold(owner)
        assert 'execution' not in dict.keys(owner)
        assert owner['workProjection'] == {'status': 'loading', 'current': False, 'receipt': receipt}
    finally:
        await app.close()


@pytest.mark.parametrize('transport', ['sse', 'websocket'])
async def test_status_only_same_revision_stream_is_private_immutable_and_does_not_save(
        authenticated_client, tmp_path, monkeypatch, transport):
    from amplifier_web.server import create_app
    from amplifier_web.shell_resources import scope_for
    from test_service import Runtime
    from test_state_transport import apply, read_event
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.diagnostics.close()
    # Stop periodic demand while retaining the actual bounded window owner.
    view = service.event_log_view
    if view.task is not None:
        view.task.cancel()
        await asyncio.gather(view.task, return_exceptions=True)
        view.task = None
    client = await authenticated_client(app)
    rows = [service._new_session({'title': title}) for title in ('selected', 'unrelated')]
    service.state['sessions'].extend(rows)
    service._publish()
    target, other = (service._session(row['id']) for row in rows)
    windows = view.windows
    # Admit a real empty source window before observing status-only changes.
    # Its page geometry stays fixed while acceptance becomes noncurrent/ready.
    await windows.read(target['id'])
    accepted = copy.deepcopy(windows.status[target['id']]['receipt'])
    assert accepted['scope'] == 'recent-events' and accepted['historyComplete'] is False
    assert accepted['hostInstanceId'] == service.instance_id and accepted['sessionId'] == target['id']
    windows.invalidate({target['id']})
    queues, prior, issued = {}, {}, {}
    for identity, selected in (('one', target), ('same-owner', target), ('other-owner', other)):
        service.clients.attach(identity)
        with service.clients.bind(identity):
            service.clients.records[identity]['selectedSessionId'] = selected['id']
            service.clients.draft(selected['id'], identity + ' private draft')
            service.shell.resources.register(identity, 1, [], scope_for(service.state))
            queues[identity] = service.subscribe()
            issued[identity] = service.browser_state()
            prior[identity] = copy.deepcopy(issued[identity])
    stream = None
    try:
        headers = {'X-Amplifier-Client': 'one'}
        if transport == 'sse':
            stream = await client.get('/api/events?transport=delta-v1', headers=headers)
            event, baseline = await read_event(stream)
        else:
            stream = await client.ws_connect('/api/events/ws?transport=delta-v1', headers=headers)
            message = await stream.receive_json(timeout=3)
            event, baseline = message['event'], message['data']
        assert event == 'state'
        original = copy.deepcopy(baseline)
        revision, writes = service.state['revision'], service.db.total_changes
        def no_write(*args, **kwargs):
            pytest.fail('Ephemeral Work receipt attempted durable publication')
        for name in ('_publish', '_save', '_save_changes'):
            monkeypatch.setattr(service, name, no_write)
        sibling_baseline = prior['same-owner']
        for ready in (True, False):
            if ready:
                await windows.read(target['id'], _initial=True)
            else:
                windows.invalidate({target['id']})
            expected = copy.deepcopy(windows.status[target['id']])
            assert expected['current'] is ready and expected['receipt'] == accepted
            # Reconnect may already have queued an unchanged initial-refresh
            # frame. Require the actual nonempty status delta, not that no-op.
            async with asyncio.timeout(3):
                while True:
                    if transport == 'sse':
                        event, patch = await read_event(stream)
                    else:
                        message = await stream.receive_json(timeout=3)
                        event, patch = message['event'], message['data']
                    assert event == 'state-delta'
                    assert patch['baseRevision'] == patch['revision'] == revision
                    if patch['changes']:
                        break
                    assert apply(baseline, patch) == baseline
            assert all('workProjection' in row['path'] for row in patch['changes'])
            baseline = apply(baseline, patch)
            selected = next(row for row in baseline['sessions'] if row['id'] == target['id'])
            assert selected['workProjection'] == expected
            assert selected['draft'] == 'one private draft'
            while True:
                sibling = queues['same-owner'].get_nowait()
                sibling_row = next(row for row in sibling['sessions'] if row['id'] == target['id'])
                if sibling_row['workProjection'] == expected:
                    break
                assert sibling == sibling_baseline, 'Only an unchanged frame may precede the status'
            sibling_baseline = copy.deepcopy(sibling)
            assert sibling_row['workProjection'] == expected
            assert sibling_row['draft'] == 'same-owner private draft'
            assert queues['other-owner'].empty()
            with service.clients.bind('other-owner'):
                assert service.browser_state() == prior['other-owner']
            assert service.state['revision'] == revision and service.db.total_changes == writes
        assert issued == prior, 'Issued baselines must not be mutated in place'
        # Authentication decorates each transport response with its opaque
        # history partition; the service-issued baseline stays undecorated.
        from amplifier_web.auth import history_scope
        expected_original = copy.deepcopy(prior['one'])
        assert 'historyScope' not in expected_original['client']
        expected_original['client']['historyScope'] = history_scope(
            app['session_secret'], app['control_token'], service.data_dir)
        assert original == expected_original
        original_selected = next(row for row in original['sessions'] if row['id'] == target['id'])
        assert original_selected['workProjection'] == {'status': 'loading', 'current': False, 'receipt': accepted}
        assert 'workProjection' not in target, 'Display receipt must not become canonical saved state'
        assert baseline['sessions'][0]['workProjection']['receipt'] == sibling_row['workProjection']['receipt']
    finally:
        monkeypatch.undo()
        if stream is not None:
            if transport == 'sse':
                stream.close()
            else:
                await stream.close()
        for queue in queues.values():
            service.unsubscribe(queue)
