import asyncio
import json
from types import SimpleNamespace

import pytest

from amplifier_web.performance_diagnostics import PerformanceDiagnostics, TimedLock, measured


def test_shell_query_retains_only_fixed_failure_and_disposition_facts(monkeypatch):
    monkeypatch.setattr('amplifier_web.performance_diagnostics.time.monotonic', lambda: 12)
    diagnostics = PerformanceDiagnostics()
    for reason in ('timeout', 'source_changed', 'worker_stopped', 'private query'):
        diagnostics.record_shell_query('chats', 'recent', 1, 2,
            {'reason': reason}, accepted=False, completed_at=11, disposition='retry')
    rows = diagnostics.snapshot()['shellQueries']
    assert [row.get('reason') for row in rows] == ['timeout', 'source_changed', 'worker_stopped', None]
    assert all(row['ownerWaitMs'] == 1000 and row['disposition'] == 'retry' for row in rows)
    diagnostics.record_shell_query('chats', 'recent', 1, 2, {}, accepted=False,
                                   disposition='private owner data')
    assert 'disposition' not in diagnostics.snapshot()['shellQueries'][-1]


def test_shell_query_diagnostics_are_bounded_content_free_and_detached():
    diagnostics = PerformanceDiagnostics()
    for index in range(70):
        diagnostics.record_shell_query('chats', 'private query text', 1, 1.125,
            {'stats': {'vmSteps': index, 'decodedRows': 40, 'elapsedMs': 32.5,
                       'threadCpuMs': float('nan'), 'secret': 'not retained'},
             'value': {'title': 'private chat'}}, accepted=index % 2 == 0)
    rows = diagnostics.snapshot()['shellQueries']
    assert len(rows) == 60 and rows[0]['vmSteps'] == 10
    assert rows[-1]['queueWaitMs'] == 125 and rows[-1]['section'] is None
    assert set(rows[-1]) == {'at', 'operation', 'section', 'queueWaitMs', 'readMs',
                             'accepted', 'vmSteps', 'decodedRows', 'elapsedMs'}
    rows[-1]['accepted'] = True
    assert diagnostics.snapshot()['shellQueries'][-1]['accepted'] is False
    diagnostics.record_shell_query('private operation', 'search', 1, 2, {}, accepted=True)
    assert len(diagnostics.snapshot()['shellQueries']) == 60
from amplifier_web.server import create_app
from test_service import Runtime


def test_shell_query_failure_keeps_errno_without_private_fields_or_aliases():
    diagnostics = PerformanceDiagnostics()
    facts = {'stage': 'spool_setup', 'exceptionType': 'OSError', 'errno': 28,
             'path': '/private/spool', 'message': 'private user data', 'residentBytes': True}
    diagnostics.record_shell_query('chats', 'recent', 1, 2, {'failure': facts}, accepted=False)
    facts['errno'] = 999
    snapshot = diagnostics.snapshot()
    failure = snapshot['shellQueries'][0]['failure']
    assert failure['stage'] == 'spool_setup' and failure['errno'] == 28
    assert failure['residentBytes'] is None and 'private' not in json.dumps(snapshot)
    failure['errno'] = 777
    assert diagnostics.snapshot()['shellQueries'][0]['failure']['errno'] == 28
    diagnostics.record_shell_query('chats', 'recent', 1, 2,
                                   {'failure': {'stage': 'private path'}}, accepted=False)
    assert 'failure' not in diagnostics.snapshot()['shellQueries'][-1]


def test_bounded_detached_timings_on_errors(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('amplifier_web.performance_diagnostics.time.monotonic', lambda: clock[0])
    diagnostics = PerformanceDiagnostics()
    for i in range(100):
        with pytest.raises(ValueError):
            with diagnostics.measure('operation', synchronous=True, session_id='chat'):
                clock[0] += 0.2
                raise ValueError('private contents are not captured')
        diagnostics.record_lag(0.25)
        diagnostics.increment('cache.hit')
    snapshot = diagnostics.snapshot()
    assert snapshot['operations']['operation']['count'] == 100
    assert len(snapshot['slowOperations']) == len(snapshot['eventLoopDelays']) == 60
    assert snapshot['counters']['cache.hit'] == 100
    assert 'private contents' not in json.dumps(snapshot)
    snapshot['slowOperations'][0]['operation'] = 'tampered'
    assert diagnostics.snapshot()['slowOperations'][0]['operation'] == 'operation'
    for i in range(100):
        with diagnostics.measure(str(i)):
            pass
        diagnostics.increment(str(i))
    assert len(diagnostics.operations) == len(diagnostics.counters) == 64


@pytest.mark.parametrize('size', [1, 10000])
def test_serialization_shape_never_resolves_or_copies_bodies(monkeypatch, size):
    from amplifier_web import cold_display

    class Unreadable(dict):
        def get(self, *args):
            pytest.fail('Custom mapping read')
        __getitem__ = __iter__ = items = values = get

    class CustomList(list):
        def __len__(self):
            pytest.fail('Custom length')
        __iter__ = __len__

    class Body:
        def __deepcopy__(self, memo):
            pytest.fail('Body copy')
        def __repr__(self):
            pytest.fail('Body inspection')

    monkeypatch.setattr(cold_display, 'load', lambda *args: pytest.fail('Cold hydration'))
    body = [Body()] * size
    resident = Unreadable(messages=body, execution={'nodes': body, 'retiredUsageNodes': []}, workers=body)
    cold = cold_display.ColdRecord({'_coldFields': {
        'messages': {'$resource': 'private-reference'}, 'execution': {'$resource': 'private-reference'}}}, None)
    owners = Unreadable(resident=resident, cold=cold,
        custom={'messages': CustomList(), 'execution': Unreadable(), 'workers': ()}, missing={})
    clock = [0.0]
    monkeypatch.setattr('amplifier_web.performance_diagnostics.time.monotonic', lambda: clock[0])
    diagnostics = PerformanceDiagnostics()
    with diagnostics.measure('state.serialize', synchronous=True,
                             serialization=(False, owners, {'resident', 'cold', 'custom', 'missing'})):
        body.clear()  # The observation is entry shape, not a borrowed graph.
        clock[0] = .2
    row = diagnostics.snapshot()['slowOperations'][0]
    assert row['scope'] == 'explicit' and row['owners'] == 4
    assert (row['messages'], row['messageOwners'], row['messageColdOwners'], row['messageUnavailableOwners']) == (size, 1, 1, 2)
    assert (row['nodes'], row['nodeOwners'], row['nodeColdOwners'], row['nodeUnavailableOwners']) == (size, 1, 1, 2)
    assert (row['retiredReceipts'], row['receiptOwners'], row['receiptColdOwners'], row['receiptUnavailableOwners']) == (0, 1, 1, 2)
    assert (row['workers'], row['workerOwners'], row['workerColdOwners'], row['workerUnavailableOwners']) == (size, 1, 0, 3)
    assert 'private' not in json.dumps(row)
    row['nodes'] = -1
    assert diagnostics.snapshot()['slowOperations'][0]['nodes'] == size


@pytest.mark.parametrize('failure', [ValueError, asyncio.CancelledError])
def test_serialization_shape_keeps_existing_failure_and_ring_bounds(monkeypatch, failure):
    clock = [0.0]
    monkeypatch.setattr('amplifier_web.performance_diagnostics.time.monotonic', lambda: clock[0])
    diagnostics = PerformanceDiagnostics()
    for _ in range(65):
        error = failure('private failure')
        with pytest.raises(failure) as caught:
            with diagnostics.measure('state.serialize', synchronous=True, serialization=(True, {}, set())):
                clock[0] += .2
                raise error
        assert caught.value is error
    snapshot = diagnostics.snapshot()
    assert snapshot['operations']['state.serialize']['count'] == 65
    assert len(snapshot['slowOperations']) == 60
    assert all(row['scope'] == 'full' and row['owners'] == 0 and row['messages'] == 0
               and row['messageUnavailableOwners'] == 0 and 'threadCpuMs' in row
               for row in snapshot['slowOperations'])
    assert 'private' not in json.dumps(snapshot)


def test_projection_failure_evidence_is_bounded_numeric_and_detached():
    diagnostics = PerformanceDiagnostics()
    for number in range(40):
        diagnostics.record_projection_failure('memory_limit', {
            'stage': 'source_rows', 'residentBytes': number,
            'peakResidentBytes': 100, 'virtualBytes': 200,
            'peakVirtualBytes': 300, 'addressSpaceLimitBytes': 400,
            'path': '/private/session/log', 'message': 'private contents'})
    snapshot = diagnostics.snapshot()
    rows = snapshot['historyProjectionFailures']
    assert len(rows) == 16
    assert rows[0]['residentBytes'] == 24 and rows[-1]['residentBytes'] == 39
    assert rows[-1]['reason'] == 'memory_limit' and rows[-1]['stage'] == 'source_rows'
    assert 'private' not in json.dumps(snapshot)
    rows[-1]['residentBytes'] = 999
    assert diagnostics.snapshot()['historyProjectionFailures'][-1]['residentBytes'] == 39
    diagnostics.record_projection_failure('private-reason', {'stage': 'source_rows'})
    assert len(diagnostics.snapshot()['historyProjectionFailures']) == 16


def test_projection_failure_is_correlated_to_host_requested_chat_not_worker_identity():
    diagnostics = PerformanceDiagnostics()
    failure = {'stage': 'source_retain', 'sessionId': 'untrusted-worker-identity'}
    diagnostics.record_projection_failure('memory_limit', failure, session_id='first-chat')
    diagnostics.record_projection_failure('worker_failed', {'stage': 'setup_import'}, session_id='second-chat')
    diagnostics.record_projection_failure('memory_limit', failure)
    rows = diagnostics.snapshot()['historyProjectionFailures']
    assert [row.get('sessionId') for row in rows] == ['first-chat', 'second-chat', None]
    assert failure['sessionId'] == 'untrusted-worker-identity'
    diagnostics.record_projection_failure('memory_limit', failure, session_id='x' * 500)
    assert len(diagnostics.snapshot()['historyProjectionFailures'][-1]['sessionId']) == 200


async def test_wait_cancellation_and_monitor_cleanup():
    diagnostics = PerformanceDiagnostics()
    service = SimpleNamespace(performance_diagnostics=diagnostics)
    lock = TimedLock(service, 'lock.wait')
    async with lock:
        waiter = asyncio.create_task(lock.acquire())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    async with lock:
        pass
    assert diagnostics.snapshot()['operations']['lock.wait']['count'] == 3
    diagnostics.start()
    task = diagnostics.task
    diagnostics.start()
    assert diagnostics.task is task
    await diagnostics.close()
    assert task.done() and diagnostics.task is None


async def test_endpoint_is_authenticated_and_does_not_project_or_publish(tmp_path, authenticated_client, monkeypatch):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    service = app['service']
    for owner in (service.diagnostics, service.history, service.event_log_view,
                  service.schedules, service.observations):
        await owner.close()
    client = await authenticated_client(app)
    client.session.headers.pop('Authorization')
    assert (await client.get('/api/diagnostics/performance')).status in {401, 403}
    client.session.headers['Authorization'] = 'Bearer ' + app['control_token']
    def unexpected(*args, **kwargs):
        pytest.fail('Diagnostics must not build or persist application state')
    with monkeypatch.context() as patch:
        patch.setattr(service, '_save', unexpected)
        patch.setattr(service, '_publish', unexpected)
        patch.setattr(service, 'browser_state', unexpected)
        response = await client.get('/api/diagnostics/performance?private=not-recorded')
        assert response.status == 200
        body = await response.json()
        assert body['hostInstanceId'] == service.instance_id
        assert body['schemaVersion'] == 1
        assert 'private' not in json.dumps(body)
    response = await client.get('/build.json')
    await response.read()
    assert response.status == 200
    assert 'app;dur=' in response.headers['Server-Timing']
    assert any(row['route'] == '/build.json' for row in service.request_timings)
