import base64
import json
from unittest.mock import AsyncMock

import pytest

from test_collaborative_workspaces import app, agent_action, send, declared_result, finish
from amplifier_web.service import AppError


async def read(app, action='coordination.recap', cursor=None, origin='agent'):
    source = app.state['sessions'][0]
    return (await app.dispatch(action, {'sessionId': source['id'], **({'cursor': cursor} if cursor else {})},
        origin=origin, caller_session_id=source['id']))['result']


async def test_independent_private_cursors_advance_only_returned_changes(app):
    for index in range(12):
        await send(app, 'notice-'+str(index), mode='notify')
    first = await read(app)
    assert len(first['changes']) == 8 and first['remainingChanges'] == 5
    # Reading alone changes no server cursor. An independent client sees all.
    other = await read(app)
    assert other['nextCursor'] == first['nextCursor']
    second = await read(app, cursor=first['nextCursor'])
    assert len(second['changes']) == 5 and second['remainingChanges'] == 0
    assert not set(row['id'] for row in first['changes']) & {row['id'] for row in second['changes']}
    final = await read(app, cursor=second['nextCursor'])
    assert final['changes'] == [] and final['cursorStatus'] == 'continued'
    assert (await read(app, cursor=first['nextCursor']))['changes'] == second['changes']
    cursor = base64.urlsafe_b64decode(final['nextCursor']).decode()
    assert 'Private unsent draft' not in cursor and 'notice-' not in cursor
    assert not app.runtime.inputs


@pytest.mark.parametrize('cursor', ['not base64', 'e30=', 'W10='])
async def test_invalid_or_obsolete_cursor_resets_explicitly(app, cursor):
    result = await read(app, cursor=cursor)
    assert result['cursorStatus'] == 'reset' and result['changes']


async def test_foreign_scope_cursor_is_comparison_data_not_authority(app):
    initial = await read(app)
    source, peer = app.state['sessions']
    source['workspace'] += '/moved'
    assert (await read(app, cursor=initial['nextCursor']))['cursorStatus'] == 'reset'
    with pytest.raises(AppError, match='calling root'):
        await app.dispatch('coordination.recap', {'sessionId': peer['id'], 'cursor': initial['nextCursor']},
            origin='agent', caller_session_id=source['id'])


async def test_reply_qualification_change_is_visible_without_copying_report_text(app):
    await declared_result(app)
    first = await read(app)
    assert any('not yet verified' in row['status'] for row in first['changes'])
    await finish(app, app.state['sessions'][1], 'result-request')
    next_view = await read(app, cursor=first['nextCursor'])
    assert any(row['status'] == 'Result available' for row in next_view['changes'])
    assert 'Checked candidate claim' not in json.dumps(next_view)


async def test_diagnostics_use_existing_metadata_and_redact_content_identifiers_paths(app):
    source = app.state['sessions'][0]
    await send(app, 'PRIVATE-REQUEST-ID', mode='notify', text='PRIVATE-REQUEST-CONTENT')
    source['execution'] = {'nodes': [{'kind': 'llm', 'id': 'PRIVATE-CALL-ID', 'turnId': 'PRIVATE-INPUT',
        'startedAt': 10, 'endedAt': 12, 'phase': 'completed', 'input': 'PRIVATE-PROMPT'}]}
    source['runtimeEvents'] = [{'type': 'runtime.bridge', 'bridgeId': '/private/bridge/path',
        'phase': 'timeout', 'elapsedSeconds': 1.5, 'at': 20, 'error': 'PRIVATE-ERROR'}]
    app.performance_diagnostics.record_lag(.5)
    result = await read(app, 'coordination.diagnostics')
    encoded = json.dumps(result)
    assert 'PRIVATE-' not in encoded and '/private' not in encoded
    assert source['workspace'] not in encoded and source['id'] not in encoded
    assert result['provider']['samples'][0]['durationMs'] == 2000
    assert result['bridge']['samples'][0]['elapsedMs'] == 1500
    assert result['bridge']['samples'][0]['phase'] == 'timeout'
    assert result['eventLoopDelay']['samples'][0]['delayMs'] == 500
    assert result['clientRendering']['status'] == 'not_collected'


@pytest.mark.parametrize('action', ['coordination.recap', 'coordination.diagnostics'])
async def test_bridge_and_direct_reads_do_not_hydrate_history_or_expose_private_client_state(app, monkeypatch, action):
    source = app.state['sessions'][0]
    original = app._session
    def no_hydrate(sid=None, *, hydrate=True):
        assert hydrate is False, 'Observation must not hydrate the selected or any peer history'
        return original(sid, hydrate=False)
    monkeypatch.setattr(app, '_session', no_hydrate)
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('history loaded')))
    result = await app.app_bridge('dispatch', {'action': action, 'args': {'sessionId': source['id']},
        '_runtimeSessionId': source['id']}, source['id'])
    assert result['accepted'] and 'Private unsent draft' not in json.dumps(result)
    assert not app.runtime.inputs


async def test_recap_fences_workspace_move_during_resource_owner_read(app, monkeypatch):
    import amplifier_web.workspace_resources as resources
    source = app.state['sessions'][0]
    async def moving(*args, **kwargs):
        source['workspace'] += '/other'
        return {'result': {'items': []}}
    monkeypatch.setattr(resources, 'dispatch_reservation', moving)
    with pytest.raises(AppError, match='moved'):
        await read(app)


async def test_large_dormant_catalog_is_not_traversed_by_receipt_projection(app, monkeypatch):
    from amplifier_web.collaboration_observations import receipts
    # SQLite indexes bound each selected actor page; unrelated command bodies
    # cannot force a JSON decode or per-conversation inspection.
    source = app.state['sessions'][0]
    values = [(str(i), 'fixture', json.dumps({'commandAction': 'coordination.send',
        'senderSessionId': 'dormant-'+str(i), 'target': {'sessionId': 'elsewhere'}, 'private': 'secret'})) for i in range(4000)]
    app.db.executemany('INSERT INTO commands VALUES(?,?,?)', values)
    queries = []
    app.db.set_trace_callback(queries.append)
    rows, truncated = receipts(app, source['id'])
    app.db.set_trace_callback(None)
    assert rows == [] and not truncated and len(queries) == 4
    plan = app.db.execute("EXPLAIN QUERY PLAN SELECT rowid,receipt FROM commands WHERE json_extract(receipt,'$.commandAction')='coordination.send' AND json_extract(receipt,'$.senderSessionId')=? ORDER BY rowid DESC LIMIT 33", (source['id'],)).fetchall()
    assert any('USING INDEX coordination_sender' in row[-1] for row in plan)


async def test_cursor_survives_service_reopen_without_replaying_work(app):
    from amplifier_web.service import AppService
    source = app.state['sessions'][0]
    before = await read(app)
    app._publish()
    await app.close()
    reopened = AppService(app.data_dir, app.runtime, workspace=source['workspace'])
    try:
        response = (await reopened.dispatch('coordination.recap', {'sessionId': source['id'],
            'cursor': before['nextCursor']}, origin='agent', caller_session_id=source['id']))['result']
        assert response['cursorStatus'] == 'continued'
        assert not reopened.runtime.inputs
    finally:
        await reopened.close()


def test_item_cap_and_cursor_page_gap_are_explicit():
    from amplifier_web.collaboration_observations import recap, digest, MAX_ITEMS
    source = {'id': 'owner', 'workspace': 'workspace'}
    items = [{'id': digest(i), 'version': digest([i, 1])} for i in range(MAX_ITEMS+1)]
    first = recap(source, items, None, False)
    assert first['truncated']
    moved_page = recap(source, items[8:], first['nextCursor'], False)
    assert moved_page['pageGap'] and moved_page['cursorStatus'] == 'continued'
