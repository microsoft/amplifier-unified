"""Known detail commits write only their records; unknown scope checkpoints all."""
import copy
import json
import sqlite3

import pytest

from amplifier_web import browser_state
from amplifier_web.service import AppService
from test_automatic_history import app_factory


def chats(app_factory):
    app = app_factory()
    for queue in list(app.queues):
        app.unsubscribe(queue)
    rows = []
    for number in range(2):
        row = app._new_session({'title': str(number)})
        row['messages'] = [{'id': 'm'+str(number), 'role': 'user', 'text': 'Exact '+str(number)}]
        app._state['sessions'].append(row)
        app._state.setdefault('runtimeControl', {})[row['id']] = {'configuration.providers': {'unchanged': 'x'*20000}}
        rows.append(row)
        client = str(number)
        app.clients.attach(client)
        with app.clients.bind(client):
            app.clients.records[client]['selectedSessionId'] = row['id']
            app.clients.draft(row['id'], 'private-'+client)
    app._publish()
    for number in range(2):
        with app.clients.bind(str(number)):
            app.browser_state()
    return app, rows


async def test_scoped_detail_does_not_encode_or_write_unrelated_global(app_factory, monkeypatch):
    app, rows = chats(app_factory)
    before = app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0]
    dumps = []
    original = json.dumps
    def observe(value, *args, **kwargs):
        if isinstance(value, dict) and ('runtimeControl' in value or value is app._state['runtimeControl'][rows[1]['id']]):
            dumps.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, 'dumps', observe)
    rows[0]['streaming'] = 'only A'
    app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    assert dumps == []
    assert app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0] == before
    from amplifier_web.state_records import load
    durable = load(app.db)
    assert durable['revision'] == app.state['revision']
    assert durable['runtimeControl'][rows[1]['id']] == app._state['runtimeControl'][rows[1]['id']]


async def test_unaffected_client_revision_advances_without_rebuild_or_old_frame_mutation(app_factory, monkeypatch):
    app, rows = chats(app_factory)
    old = app._client_snapshots['1']
    old_revision = old['revision']
    builds = []
    original = browser_state.snapshot
    monkeypatch.setattr(browser_state, 'snapshot', lambda *a, **kw: (builds.append(kw.get('client_id')), original(*a, **kw))[1])
    rows[0]['streaming'] = 'A-only'
    app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    with app.clients.bind('1'):
        current = app.browser_state()
    assert builds.count('1') == 0
    assert current['revision'] == app.state['revision'] > old_revision
    assert old['revision'] == old_revision
    assert current['sessions'] is old['sessions']
    assert current['view']['draft'] == 'private-1'
    # Explicit full reads always see the current mutable record.
    with app.clients.bind('0'):
        assert app.browser_state()['sessions'][0]['streaming'] == 'A-only'


async def test_mixed_scope_checkpoint_and_restart_keep_all_records_and_private_drafts(app_factory):
    app, rows = chats(app_factory)
    rows[0]['streaming'] = 'first'
    app._publish_progress(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    rows[1]['messages'][0]['text'] = 'same length edit'
    app._publish(session_ids={rows[1]['id']}, detail_only=True, record_only=True)
    from amplifier_web.state_records import load
    assert load(app.db)['revision'] == app.state['revision']
    assert app.db.execute('SELECT count(*) FROM state_records').fetchone()[0] > 0
    # Unknown global writer folds the latest records into the compatible base.
    app._state['theme']['name'] = 'Changed global'
    app._publish()
    assert app.db.execute('SELECT count(*) FROM state_records').fetchone()[0] == 0
    durable = json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert durable['theme']['name'] == 'Changed global'
    await app.publishing.close()  # Release the single-owner fixture lease only.
    restored = app_factory(home=app.data_dir)
    assert restored._session(rows[1]['id'])['messages'][0]['text'] == 'same length edit'
    assert restored.clients.records['0']['drafts'][rows[0]['id']] == 'private-0'
    assert restored.clients.records['1']['drafts'][rows[1]['id']] == 'private-1'


async def test_scope_records_survive_unclean_reader_restore_without_checkpoint(app_factory):
    app, rows = chats(app_factory)
    app._state['runtimeControl'][rows[0]['id']]['task.get'] = {'task': {'revision': 4, 'status': 'active'}}
    rows[0]['messages'][0]['text'] = 'An exact changed body'
    app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    # Do not close/checkpoint the first instance before the new startup reader.
    await app.publishing.close()
    restored = app_factory(home=app.data_dir)
    assert restored._session(rows[0]['id'])['messages'][0]['text'] == 'An exact changed body'
    assert restored._state['runtimeControl'][rows[0]['id']]['task.get']['task']['revision'] == 4
    assert restored._session(rows[1]['id'])['messages'][0]['text'] == 'Exact 1'


async def test_sparse_sql_failure_rolls_back_and_retry_keeps_dirty_union_and_client_draft(app_factory, monkeypatch):
    from amplifier_web import state_records
    app, rows = chats(app_factory)
    before = state_records.load(app.db)
    original = state_records.save
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError('after staging record')
    monkeypatch.setattr(state_records, 'save', fail)
    with app.clients.bind('1'):
        app.clients.draft(rows[1]['id'], 'new private B')
    rows[0]['streaming'] = 'retry A'
    with pytest.raises(sqlite3.OperationalError):
        app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    assert state_records.load(app.db) == before
    assert app._progress_dirty and app.clients.dirty
    monkeypatch.setattr(state_records, 'save', original)
    rows[1]['streaming'] = 'retry B'
    app._publish(session_ids={rows[1]['id']}, detail_only=True, record_only=True)
    await app.publishing.close()
    restored = app_factory(home=app.data_dir)
    assert restored.clients.records['1']['drafts'][rows[1]['id']] == 'new private B'
    assert app._progress_dirty is False


async def test_detail_snapshot_fallback_observes_changed_summary_and_full_save(app_factory):
    app, rows = chats(app_factory)
    rows[0]['historyLoaded'] = True
    app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    with app.clients.bind('1'):
        current = app.browser_state()
    summary = next(row for row in current['sessions'] if row['id'] == rows[0]['id'])
    assert summary['historyLoaded'] is True
    rows[1]['messages'][0]['text'] = 'unscoped same-id replacement'
    app._save()
    with app.clients.bind('1'):
        assert next(row for row in app.browser_state()['sessions'] if row['id'] == rows[1]['id'])['messages'][0]['text'] == 'unscoped same-id replacement'


async def test_unrelated_private_draft_change_is_not_hidden_by_scoped_frame_reuse(app_factory):
    app, rows = chats(app_factory)
    with app.clients.bind('1'):
        app.clients.draft(rows[1]['id'], 'Fresh private draft')
    rows[0]['streaming'] = 'A again'
    app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    with app.clients.bind('1'):
        assert app.browser_state()['view']['draft'] == 'Fresh private draft'


async def test_unknown_pending_writer_never_becomes_sparse_after_later_delta(app_factory):
    from amplifier_web.state_records import load
    app, rows = chats(app_factory)
    app._state['theme']['name'] = 'Unscoped edit'
    app._publish_progress()
    rows[0]['streaming'] = 'later delta'
    app._publish_progress(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    await app._flush_pending_progress()
    assert load(app.db)['theme']['name'] == 'Unscoped edit'
    assert app.db.execute('SELECT count(*) FROM state_records').fetchone()[0] == 0


async def test_maintenance_failure_after_commit_does_not_rewind_durable_revision(app_factory, monkeypatch):
    from amplifier_web.state_records import load
    app, rows = chats(app_factory)
    before = app.state['revision']
    monkeypatch.setattr('amplifier_web.storage_migration.maintenance',
                        lambda app: (_ for _ in ()).throw(sqlite3.OperationalError('GC busy')))
    rows[0]['streaming'] = 'committed'
    app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    assert app.state['revision'] == load(app.db)['revision'] == before+1
    assert app._progress_dirty is False


async def test_committed_sparse_cold_manifest_owns_blob_through_other_session_save_and_gc(app_factory):
    from amplifier_web.cold_display import MARKER
    from amplifier_web.resource_files import collect, put
    from amplifier_web.state_records import load
    app, rows = chats(app_factory)
    reference = put(app.db, [{'id': 'cold', 'role': 'assistant', 'text': 'x'*20000}])
    rows[0][MARKER] = {'messages': reference}
    app._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
    assert load(app.db)['sessions'][0][MARKER]['messages'] == reference
    # Handing out the mutable body consumes the live marker, not the committed
    # root. This other-session commit must not collect the still-owned payload.
    del rows[0]['messages']
    rows[0][MARKER] = {'messages': reference}
    assert rows[0]['messages'][0]['id'] == 'cold'
    rows[1]['streaming'] = 'B'
    app._publish(session_ids={rows[1]['id']}, detail_only=True, record_only=True)
    assert reference['$resource'] not in collect(app.db, app._state)
