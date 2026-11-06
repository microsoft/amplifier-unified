"""Normal exported diagnostics are bounded evidence, not tool/file success."""
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from amplifier_web.cold_display import raw
from amplifier_web.resource_files import put
from amplifier_web.server import create_app
from amplifier_web.work_diagnostics import closed, failure
from amplifier_web.work_windows import _ThreadedWindowProjector
from test_work_windows import fixture

CANARY = 'SECRET-path-key-preview-message-body'
FIELDS = {'version', 'sessionId', 'attempt', 'current', 'status', 'stage', 'reason',
          'exceptionCategory', 'errno', 'projectionStatus', 'projectionCurrent'}


def check_shape(value):
    assert set(value) == FIELDS
    assert value['version'] == 1
    assert CANARY not in json.dumps(value)
    assert len(json.dumps(value)) < 800
    assert value['attempt'] is None or len(value['attempt']) == 32


@pytest.mark.parametrize('error,category,number', [
    (PermissionError(13, CANARY, CANARY), 'permission', 13),
    (FileNotFoundError(2, CANARY, CANARY), 'missing', 2),
    (TimeoutError(CANARY), 'timeout', None),
    (OSError(5, CANARY, CANARY), 'io', 5),
    (ValueError(CANARY), 'invalid', None),
    (ImportError(CANARY), 'import', None),
    (RuntimeError(CANARY), 'unknown', None)])
def test_closed_failure_never_serializes_exception(error, category, number):
    error.__class__.__str__  # No repr/str invocation is required for classification.
    assert failure(error) == {'exceptionCategory': category, 'errno': number}
    assert closed({'status': CANARY, 'stage': [], 'reason': {},
                   'exceptionCategory': CANARY, 'errno': True, 'path': CANARY}) == {
        'status': 'unknown', 'stage': 'unknown', 'reason': 'unknown',
        'exceptionCategory': 'unknown', 'errno': None}


@pytest.mark.asyncio
@pytest.mark.parametrize('threaded', [True, False])
async def test_real_threaded_and_process_error_transport(tmp_path, monkeypatch, threaded):
    windows, session, _, _ = fixture(tmp_path, monkeypatch, count=1)
    # An invalid identity is a real input-validation failure inside WindowModel.
    session['nativeIdentity'] = '../' + CANARY
    windows.projector = _ThreadedWindowProjector(tmp_path) if threaded else None
    try:
        await windows.request('s')
        value = windows.diagnostic(session)
        check_shape(value)
        assert (value['status'], value['stage'], value['exceptionCategory']) == (
            'retrying', 'projector', 'invalid')
    finally:
        await windows.close()


def test_process_decoder_only_retains_closed_diagnostic(tmp_path):
    from amplifier_web.history_projection_process import ProcessProjector
    owner = ProcessProjector(tmp_path)
    path = tmp_path / 'output'
    data = json.dumps({'status': 'retry', 'reason': 'worker_failed', 'diagnostic': {
        'exceptionCategory': CANARY, 'errno': -1, 'message': CANARY}}).encode()
    path.write_bytes(data)
    result = owner._decode(path, {'bytes': len(data), 'digest': hashlib.sha256(data).hexdigest()})
    assert result['diagnostic'] == {'exceptionCategory': 'unknown', 'errno': None}
    assert CANARY not in json.dumps(result)


@pytest.mark.asyncio
async def test_older_completion_cannot_replace_latest_attempt(tmp_path, monkeypatch):
    windows, session, _, _ = fixture(tmp_path, monkeypatch, count=1)
    entered, release = asyncio.Event(), asyncio.Event()
    real = windows.projector.project
    calls = 0
    async def held(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            raise OSError(5, CANARY)
        return await real(*args, **kwargs)
    windows.projector.project = held
    old = asyncio.create_task(windows.read('s', _initial=True))
    try:
        await entered.wait()
        first = windows.diagnostic(session)['attempt']
        await windows.read('s', _initial=True)
        current = windows.diagnostic(session)
        assert current['status'] == 'ready' and current['attempt'] != first
        release.set()
        with pytest.raises(OSError):
            await old
        assert windows.diagnostic(session) == current
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        await windows.close()


@pytest.mark.asyncio
async def test_old_retry_and_repeated_cached_failure_keep_current_attempt(tmp_path, monkeypatch):
    windows, session, _, _ = fixture(tmp_path, monkeypatch, count=1)
    entered, release = asyncio.Event(), asyncio.Event()
    real = windows.projector.project
    calls = 0
    async def held(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            return {'status': 'retry', 'reason': 'source_changed'}
        return await real(*args, **kwargs)
    windows.projector.project = held
    old = windows.request('s')
    try:
        await entered.wait()
        await windows.read('s', _initial=True)
        current = windows.diagnostic(session)
        state = windows.status['s']
        release.set()
        await old
        assert windows.status['s'] is state and windows.diagnostic(session) == current
        assert 's' not in windows.retry_at
        def broken(*args):
            raise PermissionError(13, CANARY)
        monkeypatch.setattr(windows, '_matches', broken)
        prior = current['attempt']
        for _ in range(2):
            await asyncio.sleep(0)
            windows.retry_at['s'] = 0
            await windows.request('s')
            value = windows.diagnostic(session)
            assert value['attempt'] != prior and value['stage'] == 'source'
            assert value['projectionStatus'] == value['status'] == 'retrying'
            prior = value['attempt']
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        await windows.close()


@pytest.mark.asyncio
async def test_projection_attempt_lifecycle_and_stale_owner(tmp_path, monkeypatch):
    windows, session, _, service = fixture(tmp_path, monkeypatch, count=1)
    assert windows.diagnostic(session)['status'] == 'unknown'
    async def broken(*args, **kwargs):
        raise PermissionError(13, CANARY, CANARY)
    real = windows.projector.project
    windows.projector.project = broken
    try:
        await windows.request('s')
        bad = windows.diagnostic(session)
        check_shape(bad)
        assert bad['current'] and bad['status'] == 'retrying'
        assert (bad['stage'], bad['exceptionCategory'], bad['errno']) == ('projector', 'permission', 13)
        assert bad['projectionStatus'] == 'retrying'
        windows.projector.project = real
        windows.retry_at['s'] = 0
        gate = asyncio.Event()
        async def held(*args, **kwargs):
            await gate.wait()
            return await real(*args, **kwargs)
        windows.projector.project = held
        task = windows.request('s')
        await asyncio.sleep(0)
        loading = windows.diagnostic(session)
        assert loading['status'] == 'loading' and loading['attempt'] != bad['attempt']
        assert loading['reason'] == 'none' and loading['exceptionCategory'] == 'none'
        gate.set()
        await task
        ready = windows.diagnostic(session)
        assert ready['status'] == 'ready' and ready['attempt'] == loading['attempt']
        assert ready['stage'] == 'complete' and ready['reason'] == 'none'
        windows.invalidate({'s'})
        assert windows.diagnostic(session)['current'] is False
        await windows.read('s', _initial=True)
        service._session = lambda *args, **kwargs: dict(session)
        assert windows.diagnostic(service._session())['status'] == 'unknown'
    finally:
        await windows.close()


@pytest.mark.asyncio
async def test_stale_and_unknown_worker_results_are_closed(tmp_path, monkeypatch):
    windows, session, _, _ = fixture(tmp_path, monkeypatch)
    async def changed(*args, **kwargs):
        return {'status': 'retry', 'reason': 'source_changed',
                'diagnostic': {'path': CANARY, 'message': CANARY, 'exceptionCategory': CANARY}}
    windows.projector.project = changed
    try:
        await windows.request('s')
        value = windows.diagnostic(session)
        check_shape(value)
        assert value['reason'] == 'source_changed' and value['stage'] == 'projector'
        windows.retry_at['s'] = 0
        async def unavailable(*args, **kwargs):
            return {'status': 'unavailable', 'reason': CANARY, 'diagnostic': {}}
        windows.projector.project = unavailable
        await windows.request('s')
        value = windows.diagnostic(session)
        check_shape(value)
        assert value['status'] == 'unavailable' and value['reason'] == 'unknown'
        session['nativeIdentity'] = 'changed-' + CANARY
        stale = windows.diagnostic(session)
        check_shape(stale)
        assert stale['status'] == 'unknown' and stale['attempt'] is None
        assert stale['reason'] == 'owner_changed'
    finally:
        await windows.close()


@pytest.mark.asyncio
async def test_real_threaded_source_changed_and_bounded_retention(tmp_path, monkeypatch):
    from amplifier_web.event_window import StaleWindow
    import amplifier_web.event_window as events
    windows, session, _, _ = fixture(tmp_path, monkeypatch)
    windows.projector = _ThreadedWindowProjector(tmp_path)
    def changed(*args, **kwargs):
        raise StaleWindow(CANARY)
    monkeypatch.setattr(events, 'read_window', changed)
    try:
        await windows.request('s')
        value = windows.diagnostic(session)
        check_shape(value)
        assert value['status'] == 'retrying' and value['reason'] == 'source_changed'
        assert value['exceptionCategory'] == 'unknown'
        # Retained histories and their payload size do not grow this cache.
        for number in range(100):
            windows.diagnostics.begin(str(number), session, {}, number)
        assert len(windows.diagnostics.records) == 8
        assert windows.diagnostic(session)['status'] == 'unknown'
    finally:
        await windows.close()
        assert not windows.diagnostics.records


@pytest.mark.asyncio
async def test_specific_source_retry_keeps_original_deadline(tmp_path, monkeypatch):
    import amplifier_web.work_windows as module
    from types import SimpleNamespace
    windows, session, _, _ = fixture(tmp_path, monkeypatch, count=1)
    monkeypatch.setattr(windows, '_matches', lambda *args: False)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: 100))
    try:
        await windows.request('s')
        assert windows.status['s']['reason'] == 'source_changed'
        assert windows.retry_at['s'] == 101
        value = windows.diagnostic(session)
        assert value['reason'] == 'source_changed' and value['stage'] == 'source'
    finally:
        await windows.close()


class Runtime:
    async def close(self): pass
    async def stop(self, *args, **kwargs): pass


@pytest.mark.asyncio
async def test_normal_json_export_cold_failure_while_nodes_http_200(tmp_path, monkeypatch):
    for key, name in [('AMPLIFIER_HOME', 'shared'), ('AMPLIFIER_WEB_HOME', 'app'),
                      ('AMPLIFIER_SESSION_STATE_HOME', 'state'), ('AMPLIFIER_TERMINAL_HOME', 'term')]:
        monkeypatch.setenv(key, str(tmp_path / name))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)
    app = await create_app(tmp_path / 'app', runtime=Runtime(), workspace=tmp_path,
        voice=False, background_updates=False, background_work=False, preload_providers=False)
    service = app['service']
    service.management.warm_providers = AsyncMock()
    await service.dispatch('session.create', {'location': {'kind': 'managed'}})
    session = service._session()
    sid = session['id']
    session.update(nativeProject='synthetic', nativeIdentity='synthetic',
        workspace=r'C:\Users\Fixture\app\chats\synthetic\files', historyLoaded=False)
    tree = {'nodes': [{'id': 'call', 'toolCallId': 'call', 'sessionId': sid,
        'kind': 'tool', 'phase': 'completed', 'label': 'read_file', 'liveObservation': True,
        'turnId': 't', 'startedAt': 1, 'endedAt': 2}],
        'turns': [{'id': 't', 'phase': 'completed', 'anchorMessageId': 'u'}]}
    session['messages'] = [{'id': 'u', 'role': 'user', 'text': 'Synthetic task', 'createdAt': 0}]
    session['execution'] = tree
    windows = service.event_log_view.windows
    await service.history.close()
    # Freeze periodic interest, not the reader: every tested read is explicit.
    view = service.event_log_view
    if view.task is not None:
        view.task.cancel()
        await asyncio.gather(view.task, return_exceptions=True)
        view.task = None
    windows.projector = _ThreadedWindowProjector(service.data_dir)
    service.clients.attach('web')
    service.clients.attach('wrong-web')
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {'Authorization': 'Bearer ' + app['control_token'], 'X-Amplifier-Client': 'web'}
    async def exported():
        receipt = await client.post('/api/actions', json={'action': 'session.export',
            'args': {'id': sid, 'format': 'json'}}, headers=headers)
        assert receipt.status == 200, await receipt.text()
        body = await receipt.json()
        effect = next(e for e in body['effects'] if e['type'] == 'download')
        value = json.loads(effect['content'])['workDiagnostics']
        check_shape(value)
        return value
    try:
        assert (await exported())['status'] == 'unknown'
        # Admit normal resident groups for the same client, then retire execution.
        token = service.clients.current.set('web')
        try:
            group = next(x['id'] for x in windows.project(session)['execution']['segments'] if x['turnId'] == 't')
        finally:
            service.clients.current.reset(token)
        # First bind the normal group request so a later HTTP200 is real.
        query = {'sessionId': sid, 'part': 'nodes', 'group': group}
        response = await client.get('/api/conversation/detail', params=query, headers=headers)
        assert response.status == 200
        ref = put(service.db, tree)
        dict.pop(session, 'execution')
        dict.__setitem__(session, '_coldFields', {'execution': ref})
        import amplifier_web.resource_files as resources
        real = resources.read_detached
        def broken(*args, **kwargs):
            raise PermissionError(13, CANARY, CANARY)
        monkeypatch.setattr(resources, 'read_detached', broken)
        if sid in windows.pending:
            await windows.pending[sid]
            await asyncio.sleep(0)
        windows.retry_at[sid] = 0
        await windows.request(sid)
        Path('/artifacts/cold-binding.json').write_text(json.dumps({
            'diagnostic': windows.diagnostic(session),
            'ownerSame': service._session(sid, hydrate=False) is session,
            'record': windows.diagnostics.records.get(sid),
            'source': {k: session.get(k) for k in ('id', 'workspace', 'nativeIdentity', 'nativeProject')},
            'fence': windows.epochs.get(sid), 'cold': raw(session).get('_coldFields'),
            'status': windows.status.get(sid)}, indent=2))
        assert windows.diagnostic(session)['stage'] == 'cold_resource', {
            'value': windows.diagnostic(session), 'fence': windows.epochs.get(sid),
            'record': windows.diagnostics.records.get(sid),
            'source': {k: session.get(k) for k in ('id', 'workspace', 'nativeIdentity', 'nativeProject')},
            'cold': raw(session).get('_coldFields'), 'status': windows.status.get(sid)}
        value = await exported()
        assert value['stage'] == 'cold_resource' and value['exceptionCategory'] == 'permission', value
        assert value['status'] == value['projectionStatus'] == 'retrying'
        assert 'execution' not in raw(session), 'Diagnostic export hydrated execution'
        monkeypatch.setattr(resources, 'read_detached', real)
        windows.retry_at[sid] = 0
        await windows.request(sid)
        ready = await exported()
        assert ready['status'] == 'ready' and ready['attempt'] != value['attempt']
        assert ready['exceptionCategory'] == 'none' and ready['reason'] == 'none'
        # Resident nodes and saved-event projection are independent owners:
        # reproduce the native observed nodes200/retrying combination without
        # assuming cold placement must remain valid across normal publication.
        session['execution'] = tree
        windows.invalidate({sid})
        real_project = windows.projector.project
        async def failed_project(*args, **kwargs):
            raise PermissionError(13, CANARY, CANARY)
        monkeypatch.setattr(windows.projector, 'project', failed_project)
        windows.retry_at[sid] = 0
        await windows.request(sid)
        retrying = await exported()
        assert retrying['stage'] == 'projector' and retrying['exceptionCategory'] == 'permission'
        # HTTP200 is deliberately not projection success.
        response = await client.get('/api/conversation/detail', params=query, headers=headers)
        assert response.status == 200, await response.text()
        assert (await exported()) == retrying
        nodes = await response.json()
        tool = next(row['toolDetail'] for row in nodes['items'] if row.get('toolDetail'))
        response = await client.get('/api/conversation/detail', params=tool,
            headers={**headers, 'X-Amplifier-Client': 'wrong-web'})
        assert response.status == 400
        monkeypatch.setattr(windows.projector, 'project', real_project)
        session['nativeIdentity'] = 'replacement'
        assert (await exported())['status'] == 'unknown'
        # Fail the exporter's own immutable payload read, not just Work's
        # detached reader; preserve diagnostics and explicit partial coverage.
        directory = resources.root(service.db)
        dict.pop(session, 'execution')
        dict.__setitem__(session, '_coldFields', {'execution': ref})
        (directory / (ref['$resource'] + '.json')).write_text(CANARY)
        receipt = await client.post('/api/actions', json={'action': 'session.export',
            'args': {'id': sid}}, headers=headers)
        assert receipt.status == 200
        body = await receipt.json()
        export = json.loads(next(e['content'] for e in body['effects'] if e['type'] == 'download'))
        assert export['exportAvailability'] == {'status': 'partial', 'unavailableFields': ['execution']}
        assert 'execution' not in export and 'execution' not in raw(session)
        check_shape(export['workDiagnostics'])
        Path('/artifacts/diagnostic-seam.json').write_text(json.dumps({
            'coldResourceFailure': value, 'readyRecovery': ready,
            'projectorFailureWithNodesHTTP200': retrying,
            'wrongClientToolStatus': 400, 'canonicalExecutionHydrated': False,
            'runtimeCalls': 0, 'nativePlatform': False}, indent=2) + '\n')
    finally:
        await client.close()