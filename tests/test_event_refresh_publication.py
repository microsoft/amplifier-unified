"""Canonical event updates retain durability without republishing the catalog."""
import copy
import json
import sqlite3

import pytest

from amplifier_web.event_log_view import EventLogView, event_path
from amplifier_web import state_records
from test_automatic_history import app_factory
from test_event_log_view import append
from test_scoped_state_records import chats


def model_event(row, number):
    row['runtimeSessionId'] = 'native'
    path = event_path(row, 'native')
    append(path, 'llm:response', {
        'id': f'model-{number}', 'kind': 'llm', 'sessionId': 'native',
        'model': 'fixture', 'startedAt': 10 + number, 'endedAt': 11 + number,
        'phase': 'completed', 'usage': {'inputTokens': 4, 'outputTokens': 2, 'totalTokens': 6},
    }, 11 + number)
    return path


@pytest.mark.parametrize('use_native_alias', [False, True])
async def test_event_refresh_preserves_catalog_epoch_and_updates_browser_usage(app_factory, monkeypatch, use_native_alias):
    app, rows = chats(app_factory)
    row = rows[0]
    row['messages'][0]['createdAt'] = 1
    model_event(row, 1)
    app._publish()
    view = EventLogView(app)
    epoch = app._history_local_revision
    unrelated = copy.deepcopy(state_records.load(app.db)['runtimeControl'][rows[1]['id']])
    encoded = []
    original = json.dumps
    def observe(value, *args, **kwargs):
        if value is app._state['workspaces'] or value is app._state['runtimeControl'][rows[1]['id']]:
            encoded.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, 'dumps', observe)
    # Subscribe both browser clients so the change exercises snapshot delivery.
    queues = []
    for i in range(2):
        with app.clients.bind(str(i)):
            queues.append(app.subscribe())
    for queue in queues:
        while not queue.empty():
            queue.get_nowait()
    await view.refresh('native' if use_native_alias else row['id'])
    assert app._history_local_revision == epoch
    assert encoded == []
    assert all(not queue.empty() for queue in queues)
    with app.clients.bind('0'):
        current = next(item for item in app.browser_state()['sessions'] if item['id'] == row['id'])
    assert current['execution']['turns'][0]['aggregateUsage']['totalTokens'] == 6
    assert state_records.load(app.db)['runtimeControl'][rows[1]['id']] == unrelated
    # A later event updates the retained browser projection and usage rollup.
    model_event(row, 2)
    await view.refresh('native' if use_native_alias else row['id'])
    with app.clients.bind('0'):
        current = next(item for item in app.browser_state()['sessions'] if item['id'] == row['id'])
    assert current['execution']['turns'][0]['aggregateUsage']['totalTokens'] == 12
    assert app._history_local_revision == epoch
    # Reload from committed records plus the authoritative event log.
    await app.publishing.close()
    restored = app_factory(home=app.data_dir)
    recovered = restored._session(row['id'])
    await EventLogView(restored).refresh(row['id'])
    assert recovered['execution']['turns'][0]['aggregateUsage']['totalTokens'] == 12
    assert recovered['messages'][0]['text'] == 'Exact 0'
    assert restored.clients.records['1']['drafts'][rows[1]['id']] == 'private-1'


async def test_event_refresh_failure_retains_retry_scope_and_pending_other_writer(app_factory, monkeypatch):
    app, rows = chats(app_factory)
    model_event(rows[0], 1)
    app._publish()
    before = state_records.load(app.db)
    rows[1]['messages'][0]['text'] = 'Pending other chat'
    app._publish_progress(session_ids={rows[1]['id']}, record_only=True)
    original = state_records.save
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError('synthetic transaction failure')
    monkeypatch.setattr(state_records, 'save', fail)
    with pytest.raises(sqlite3.OperationalError):
        await EventLogView(app).refresh(rows[0]['id'])
    assert state_records.load(app.db) == before
    assert app._progress_session_ids == {row['id'] for row in rows}
    monkeypatch.setattr(state_records, 'save', original)
    await app._flush_pending_progress()
    assert not app._progress_dirty
    await app.publishing.close()
    restored = app_factory(home=app.data_dir)
    assert restored._session(rows[1]['id'])['messages'][0]['text'] == 'Pending other chat'
    await EventLogView(restored).refresh(rows[0]['id'])
    assert restored._session(rows[0]['id'])['execution']['nodes'][0]['usage']['totalTokens'] == 6
