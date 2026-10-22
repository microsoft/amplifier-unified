"""Cold storage must not retain reconstructed execution payloads a hot save omits."""
import copy
import json

import pytest

from amplifier_web.cold_display import ColdRecord, compact_execution_references, load
from amplifier_web.resource_files import put, root
from amplifier_web.service import AppService
from amplifier_web.session_projection import stored_execution
from test_accounting_canonical import observed_tree, populated_logs, assert_usage


@pytest.mark.parametrize('reference', [
    {'pendingWork': False}, {'pendingObservation': False, 'executionProjection': 1},
])
def test_recovery_does_not_load_settled_cold_history(app, monkeypatch, reference):
    from amplifier_web.execution import interrupt_unfinished
    row = ColdRecord({'id': 'settled', 'status': 'idle', '_coldFields': {'execution': reference}}, app.db)
    monkeypatch.setattr('amplifier_web.cold_display.load', lambda *args: pytest.fail('settled history was loaded'))
    assert interrupt_unfinished(row) is False
    assert not dict.__contains__(row, 'execution')


def test_recovery_loads_and_settles_unfinished_cold_history(app):
    from amplifier_web.cold_display import execution_reference
    from amplifier_web.execution import interrupt_unfinished
    reference = execution_reference(app.db, {'turns': [{'id': 'lost-turn', 'phase': 'running'}], 'nodes': []})
    row = ColdRecord({'id': 'lost', 'status': 'idle', '_coldFields': {'execution': reference}}, app.db)
    assert reference['pendingWork'] is True
    assert interrupt_unfinished(row) is True
    assert row['execution']['turns'][0]['phase'] == 'interrupted'
    assert row['failure']['category'] == 'execution_interrupted'


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    yield service
    if not service.closed:
        await service.close()


def tree(identity):
    result = observed_tree(identity)
    result['nodes'].append({'id': 'legacy', 'kind': 'tool', 'output': 'app-only result'})
    result['nodes'].append({'id': 'display', 'kind': 'tool', 'nativeHistory': True,
                            'output': 'reconstructible ' * 10000})
    result['turns'] = [{'id': 'native-turn', 'canonicalHistory': True},
                       {'id': 'app-turn', 'inputId': 'original'}]
    result['currentTurnId'] = 'app-turn'
    return result


async def populate(app):
    await app.dispatch('session.create', {})
    row = app._session()
    row['execution'] = tree(row['id'])
    row['messages'] = [{'id': 'message', 'createdAt': 1, 'role': 'user', 'text': 'retain conversation', 'inputId': 'original'}]
    row['status'] = 'stopped'
    app._state['selectedSessionId'] = None
    app._save()
    return row


async def test_retirement_uses_compact_form_and_preserves_accounting(app, monkeypatch):
    row = await populate(app)
    before = copy.deepcopy(row['execution'])
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    reference = dict.get(row, '_coldFields')['execution']
    assert reference['executionProjection'] == 1
    assert not dict.__contains__(row, 'execution')
    saved = load(app.db, reference)
    assert len(json.dumps(saved)) < len(json.dumps(before)) / 20
    assert saved['nodes'] == [{'id': 'legacy', 'kind': 'tool', 'output': 'app-only result'}]
    assert saved['turns'] == [before['turns'][1]]
    assert saved['currentTurnId'] == before['currentTurnId']
    assert_usage({'id': row['id'], 'execution': saved})


async def test_restart_migrates_old_blob_and_rebuilds_display_without_changing_logs(app):
    row = await populate(app)
    identity = row['id']
    paths = populated_logs(row)
    old = put(app.db, tree(identity))
    dict.pop(row, 'execution')
    row['_coldFields'] = {'execution': old}
    app._save()
    await app.close()
    original_logs = {path: path.read_bytes() for path in paths}
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        assert all(v['id'] != identity for v in restored._state['sessions'])
        current = restored._session(identity, hydrate=False)
        ref = dict.get(current, '_coldFields')['execution']
        assert ref['executionProjection'] == 1 and ref['$resource'] != old['$resource']
        assert not dict.__contains__(current, 'execution')
        assert_usage(current)
        await restored.event_log_view.refresh(identity)
        assert_usage(current)
        assert any(n.get('requestDetail') for n in current['execution']['nodes'])
        assert all(path.read_bytes() == text for path, text in original_logs.items())
        assert current['messages'][0]['text'] == 'retain conversation'
        from amplifier_web.storage_migration import _maintenance_async
        maintenance = getattr(restored, '_storage_maintenance_task', None)
        if maintenance is not None:
            await maintenance
        await _maintenance_async(restored)
        assert not (root(restored.db) / (old['$resource'] + '.json')).exists()
    finally:
        await restored.close()


def test_migration_is_once_only_and_does_not_hydrate_or_rewrite_shared_blob(app, monkeypatch):
    old = put(app.db, tree('root'))
    rows = [ColdRecord({'id': sid, '_coldFields': {'execution': dict(old)}}, app.db)
            for sid in ['a', 'b']]
    state = {'sessions': rows}
    compact_execution_references(state, app.db)
    refs = [dict.get(v, '_coldFields')['execution'] for v in rows]
    assert refs[0] == refs[1]
    assert all(not dict.__contains__(v, 'execution') for v in rows)
    assert (root(app.db) / (old['$resource'] + '.json')).exists(), 'GC owns removal after manifest commit'
    monkeypatch.setattr('amplifier_web.cold_display.load', lambda *args: pytest.fail('already migrated'))
    compact_execution_references(state, app.db)


@pytest.mark.parametrize('fault', ['missing', 'corrupt', 'write'])
def test_migration_failure_retains_old_reference(app, monkeypatch, fault):
    old = put(app.db, tree('root'))
    app.db.commit()
    row = ColdRecord({'id': 'root', '_coldFields': {'execution': dict(old)}}, app.db)
    path = root(app.db) / (old['$resource'] + '.json')
    if fault == 'missing':
        path.unlink()
    elif fault == 'corrupt':
        path.write_text('{}')
    else:
        def fail(*args):
            raise OSError('disk full')
        monkeypatch.setattr('amplifier_web.cold_display.put', fail)
    with pytest.raises((OSError, ValueError)):
        compact_execution_references({'sessions': [row]}, app.db)
    assert dict.get(row, '_coldFields')['execution'] == old
    assert not dict.__contains__(row, 'execution')


def test_pending_and_uncertain_receipts_are_never_dropped():
    original = tree('root')
    original['nodes'][1].update(phase='outcome_unknown', endedAt=None)
    original['nodes'][2].update(phase='running', endedAt=None)
    compact = stored_execution(original)
    receipts = {v['id']: v for v in compact['retiredUsageNodes']}
    assert receipts['root-call']['phase'] == 'outcome_unknown'
    assert receipts['child-call']['phase'] == 'running'
    assert receipts['root-call']['producerId'] == 'producer'
    assert stored_execution(compact) == compact


def test_restart_settles_pending_cold_receipts_but_keeps_settled_trees_cold(app):
    from amplifier_web.capacity import restore_observation
    value = tree('root')
    value['nodes'][1].update(phase='running', endedAt=None)
    old = put(app.db, value)
    row = ColdRecord({'id': 'root', '_coldFields': {'execution': old}}, app.db)
    compact_execution_references({'sessions': [row]}, app.db)
    assert dict.get(row, '_coldFields')['execution']['pendingObservation'] is True
    restore_observation(row)
    assert next(v for v in row['execution']['retiredUsageNodes']
                if v['id'] == 'root-call')['phase'] == 'outcome_unknown'


@pytest.mark.parametrize('later_events', [0, 140])
@pytest.mark.parametrize('retired', [False, True])
async def test_registered_web_reopen_keeps_observed_turn_and_complete_tool_fields(
        app, authenticated_client, monkeypatch, later_events, retired):
    """Real host lifecycle/save/reopen, with synthetic canonical capture only."""
    import asyncio
    import time
    from amplifier_web.automatic_history import AutomaticHistory
    from amplifier_web.execution import ensure_turn, finish
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.server import create_app
    from amplifier_web.session_files import append_event
    from test_service import Runtime

    await app.dispatch('session.create', {})
    session = app._session()
    sid = session['id']
    native = session.get('runtimeSessionId') or sid
    input_id = 'retained-input'
    user = app._message(session, 'user', 'Inspect both synthetic files.', 'chat', inputId=input_id)
    ensure_turn(session, input_id)
    emitted = []
    observer = ExecutionEvents(native, emitted.append)
    observer.lifecycle({'type': 'input.delivered', 'input_id': input_id, 'source': 'user'})
    expected, anchors = {}, {}
    for index in range(2):
        progress = app._message(session, 'assistant', f'Inspecting file {index}.', 'chat',
                                inputId=input_id, timestampKnown=True)
        arguments = {'file_path': f'fixture-{index}.txt', 'note': f'input-{index}:' + '界𝄞' * 10000}
        output = f'output-{index}:' + '雪𝄞' * 10000 + f':END-{index}'
        call = f'retained-call-{index}'
        for event in ('tool:pre', 'tool:post'):
            data = {'session_id': native, 'tool_call_id': call, 'tool_name': 'read_file',
                    'tool_input': arguments, 'timestamp': time.time()}
            if event == 'tool:post':
                data['result'] = output
            canonical = observer.hook(native, event, data)
            assert canonical is not None
            update = emitted[-1]['event']
            assert update['turnId'] == input_id
            await app.on_runtime_event('execution.event', update)
            append_event(session['workspace'], native, event, canonical)
        expected[update['id']] = {'input': arguments, 'output': output}
        anchors[update['id']] = progress['id']
    final = app._message(session, 'assistant', 'Both files inspected.', 'chat', inputId=input_id)
    finish(session, 'completed')
    session['status'] = 'idle'
    # Later unrelated canonical records exercise explicit older-page demand;
    # they do not manufacture additional resident tools or turn associations.
    for index in range(later_events):
        append_event(session['workspace'], native, 'tool:post', {
            'session_id': native, 'tool_call_id': f'later-{index}', 'tool_name': 'other',
            'result': f'later-{index}', 'timestamp': time.time()})
    await app._flush_pending_progress()
    if retired:
        app._state['selectedSessionId'] = None
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        monkeypatch.setattr(app.cold_display, 'MIN_BYTES', 1)
        app.cold_display.retire(force=True)
        assert not dict.__contains__(session, 'execution')
        assert dict.get(session, '_coldFields')['execution']['executionProjection'] == 1
    app._save()
    await app.close()

    # Join the one actual initial catalog refresh, never request another refresh
    # or suppress a legitimate publication to make the first HTTP read succeed.
    completed = asyncio.Event()
    original_refresh = AutomaticHistory.refresh
    async def observe_refresh(history, *args, **kwargs):
        try:
            return await original_refresh(history, *args, **kwargs)
        finally:
            if history.service.data_dir == app.data_dir:
                completed.set()
    monkeypatch.setattr(AutomaticHistory, 'refresh', observe_refresh)
    runtime = Runtime()
    application = await create_app(app.data_dir, workspace=app.default_workspace, runtime=runtime,
                                   voice=False, background_updates=False, preload_providers=False)
    restored = application['service']
    client = None
    try:
        await asyncio.wait_for(completed.wait(), 10)
        assert not restored.state['sharedHistory'].get('error')
        assert not restored.state['sharedHistory'].get('issues')
        restored.clients.attach('retained-work-browser')
        client = await authenticated_client(application)
        client.session.headers['X-Amplifier-Client'] = 'retained-work-browser'

        async def get(params):
            response = await client.get('/api/conversation/detail', params=params)
            assert response.status == 200, await response.text()
            return await response.json()

        await get({'sessionId': sid, 'part': 'groups'})
        with restored.clients.bind('retained-work-browser'):
            current = restored._session(sid, hydrate=False)
            projected = restored.event_log_view.windows.project(current)
        if retired:
            assert not dict.__contains__(current, 'execution'), 'An ordinary read hydrated the saved graph'
        segments = [row for row in projected['execution']['segments'] if row.get('turnId') == input_id]
        assert [row['anchorMessageId'] for row in segments] == list(anchors.values())
        assert all(row['anchorMessageId'] not in {user['id'], final['id']} for row in segments)
        found = []
        for segment in segments:
            nodes = await get({'sessionId': sid, 'part': 'nodes', 'group': segment['id']})
            assert len(nodes['items']) == 1
            node = nodes['items'][0]
            found.append(node['id'])
            assert node['turnId'] == input_id and node['anchorMessageId'] == anchors[node['id']]
            query = node['toolDetail']
            for _ in range(8):
                detail = await get(query)
                if detail['item'].get('inputDetail') and detail['item'].get('outputDetail'):
                    break
                assert detail.get('before'), detail
                query = {**node['toolDetail'], 'lookupKey': detail['lookupKey'], 'before': detail['before']}
            else:
                pytest.fail('Explicit bounded lookup never reached the exact observation')
            references = {field: detail['item'][field + 'Detail'] for field in ('input', 'output')}
            for field, reference in references.items():
                chunks, offset = [], 0
                for _ in range(64):
                    page = await get({**reference, 'offset': offset, 'limit': 4096})
                    chunks.append(page['value'])
                    if page.get('nextOffset') is None:
                        break
                    assert page['nextOffset'] > offset
                    offset = page['nextOffset']
                else:
                    pytest.fail('Field cursor failed to terminate within its synthetic bound')
                value = ''.join(chunks)
                assert len(chunks) > 4
                assert (json.loads(value) if field == 'input' else value) == expected[node['id']][field]
            response = await client.post('/api/conversation/work-sync', json={
                'sessionId': sid, 'group': segment['id'],
                'known': {node['id']: node['detailVersion']}})
            assert response.status == 200, await response.text()
            synced = await response.json()
            assert synced['order'] == [node['id']]
            assert (await get({**references['output'], 'offset': 0, 'limit': 4096}))['value'] == expected[node['id']]['output'][:4096]
        assert found == list(expected)
        assert runtime.started == [] and runtime.sent == []
        if retired:
            assert not dict.__contains__(current, 'execution')
    finally:
        if client is not None:
            await client.close()
        else:
            await restored.close()


@pytest.mark.parametrize('change', ['reference', 'source', 'owner', 'epoch'])
@pytest.mark.parametrize('boundary', ['resource', 'cached-stat'])
async def test_saved_work_placement_rechecks_owner_after_resource_read(app, tmp_path, monkeypatch, change, boundary):
    import threading
    import asyncio
    from amplifier_web import resource_files
    from amplifier_web.cold_display import execution_reference
    from amplifier_web.work_windows import WorkWindows
    from test_tool_window_records import _host_placed_tool
    from test_work_windows import FakeProjector

    await app.dispatch('session.create', {})
    session = app._session()
    app._message(session, 'user', 'Inspect the saved fixture.', 'chat', id='user', inputId='original')
    # Empty canonical source is valid; placement is a separately owned view.
    _host_placed_tool(session)
    reference = execution_reference(app.db, session['execution'])
    dict.pop(session, 'execution')
    session['_coldFields'] = {'execution': reference}
    windows = WorkWindows(app)
    windows.projector = FakeProjector(app.data_dir)
    if boundary == 'cached-stat':
        await windows.read(session['id'])
    prior_page = windows.current.get(session['id'])
    entered, release = threading.Event(), threading.Event()
    original = resource_files.read_detached if boundary == 'resource' else windows._matches
    def held(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        if not release.wait(5):
            raise TimeoutError('Synthetic saved placement barrier did not release')
        return result
    if boundary == 'resource':
        monkeypatch.setattr(resource_files, 'read_detached', held)
    else:
        monkeypatch.setattr(windows, '_matches', held)
    read = asyncio.create_task(windows.read(session['id'], _initial=boundary == 'cached-stat'))
    try:
        assert await asyncio.to_thread(entered.wait, 5), 'Saved resource read was not reached'
        if change == 'reference':
            replacement = execution_reference(app.db, {'nodes': [], 'turns': []})
            session['_coldFields']['execution'] = replacement
        elif change == 'source':
            session['runtimeSessionId'] = 'different-native-owner'
        elif change == 'owner':
            from amplifier_web.cold_display import raw
            original_session = app._session
            # dict(ColdRecord) materializes virtual keys. The mutation under
            # test is replacement, not an explicit read of the old owner.
            replacement = ColdRecord(copy.deepcopy(raw(session)), app.db)
            assert not dict.__contains__(replacement, 'execution')
            assert not dict.__contains__(session, 'execution')
            monkeypatch.setattr(app, '_session', lambda sid=None, **kwargs:
                replacement if sid == session['id'] else original_session(sid, **kwargs))
        else:
            windows.invalidate({session['id']})
        release.set()
        with pytest.raises(ValueError, match='conversation changed'):
            await asyncio.wait_for(read, 5)
        if boundary == 'resource':
            assert not windows.current and not windows.pages
        else:
            assert windows.current.get(session['id']) == prior_page, 'No replacement page was accepted'
        assert not dict.__contains__(session, 'execution')
    finally:
        release.set()
        if not read.done():
            read.cancel()
        await asyncio.gather(read, return_exceptions=True)
        await windows.close()
