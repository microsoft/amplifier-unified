"""Durable membership and resource ownership do not depend on residency."""
import copy
import json
import sqlite3

import pytest

from amplifier_web.resource_files import collect, put
from amplifier_web.state_records import load, load_owner
from amplifier_web.state_storage import resource
from amplifier_web.service import AppError
from test_automatic_history import app_factory


async def test_exact_local_lookup_and_aliases_do_not_scan_27000_dormant_rows(app_factory):
    from amplifier_web.history_directory import COLUMNS

    app = app_factory()
    active = app._new_session({'title': 'One admitted owner'})
    app._state['sessions'].append(active)
    app._state['selectedSessionId'] = active['id']
    app._save()
    template = dict(zip(COLUMNS, app.db.execute('SELECT ' + ','.join(COLUMNS) +
                                               ' FROM history_local_rows WHERE id=?', (active['id'],)).fetchone()))

    def stored_rows():
        for number in range(27000):
            identity = f'local-{number:05d}'
            values = {**template, 'id': identity, 'native_id': 'runtime-' + identity,
                      'alias_id': 'alias-' + identity, 'title': 'Dormant ' + str(number),
                      'ordinal': f'{number + 1:020d}',
                      'summary': json.dumps({'id': identity, 'title': 'Dormant ' + str(number)})}
            yield tuple(values[key] for key in COLUMNS)

    app.db.executemany('INSERT INTO history_local_rows (' + ','.join(COLUMNS) + ') VALUES (' +
                       ','.join('?' for _ in COLUMNS) + ')', stored_rows())
    app.db.commit()
    before = {row['id'] for row in app._state['sessions']}
    for alias in ('local-26000', 'runtime-local-26000', 'alias-local-26000'):
        instructions = 0

        def progress():
            nonlocal instructions
            instructions += 100
            return 0

        app.db.set_progress_handler(progress, 100)
        try:
            result = app.directory.resolve(alias)
        finally:
            app.db.set_progress_handler(None, 0)
        assert result['id'] == 'local-26000'
        assert instructions < 20000
        assert {row['id'] for row in app._state['sessions']} == before
    assert not app.runtime.started and not app.runtime.sent


def saved_chats(app_factory, count=8):
    app = app_factory()
    rows = []
    for number in range(count):
        row = app._new_session({'title': f'Local title {number}'})
        row['messages'] = [{'id': f'message-{number}', 'role': 'assistant',
                            'text': f'Exact saved body {number} ' + 'x' * 20000}]
        rows.append(row)
    app._state['sessions'].extend(rows)
    app._state['selectedSessionId'] = rows[0]['id']
    app._save()
    return app, rows


def durable_tables(app):
    return {name: app.db.execute('SELECT * FROM ' + name + ' ORDER BY 1,2').fetchall()
            for name in ('state', 'state_records', 'history_local_rows', 'history_directory_layout')}


async def test_dormant_record_and_runtime_resource_survive_global_scoped_saves_restart_and_gc(app_factory):
    app, rows = saved_chats(app_factory)
    active_id, dormant_id = rows[0]['id'], rows[-1]['id']
    payload = {'result': ['Retained exact runtime evidence'] * 3000}
    reference = put(app.db, payload)
    app._state.setdefault('runtimeControl', {})[dormant_id] = {'tool.result': {'body': reference}}
    app._save()
    dormant_record = copy.deepcopy(load_owner(app.db, dormant_id))
    await app.close()

    restored = app_factory(home=app.data_dir)
    assert {row['id'] for row in restored._state['sessions']} == {active_id}
    assert dormant_id not in restored._state['runtimeControl']
    assert restored.directory.resolve(dormant_id)['title'] == 'Local title 7'
    assert dormant_id not in {row['id'] for row in restored._state['sessions']}
    restored._state['theme']['name'] = 'A global preference'
    restored._save()
    restored._session(active_id)['streaming'] = 'Only the active chat changed'
    restored._publish(session_ids={active_id}, detail_only=True, record_only=True)
    assert load_owner(restored.db, dormant_id) == dormant_record
    assert reference['$resource'] not in collect(restored.db, restored._state)
    assert resource(restored.db, reference['$resource']) == payload
    restored.db.commit()
    await restored.close()

    again = app_factory(home=app.data_dir)
    assert dormant_id not in {row['id'] for row in again._state['sessions']}
    assert len(load(again.db)['sessions']) == len(rows)
    admitted = again._session(dormant_id)
    assert admitted['title'] == 'Local title 7'
    assert admitted['messages'][0]['text'].startswith('Exact saved body 7 ')
    assert again._state['runtimeControl'][dormant_id] == dormant_record[1]
    assert not again.runtime.started and not again.runtime.sent


async def test_directory_and_durable_pointers_roll_back_together_and_retry_keeps_dormant_owner(app_factory, monkeypatch):
    app, rows = saved_chats(app_factory, 3)
    active_id, dormant_id = rows[0]['id'], rows[-1]['id']
    await app.close()
    restored = app_factory(home=app.data_dir)
    before = durable_tables(restored)
    old_references = copy.deepcopy(restored._session_projection_refs)
    original = restored.directory.finish_migration
    def reject_commit():
        original()
        raise sqlite3.OperationalError('after projecting the new directory row')
    monkeypatch.setattr(restored.directory, 'finish_migration', reject_commit)
    restored._session(active_id)['title'] = 'A pending rename'
    with pytest.raises(sqlite3.OperationalError):
        restored._publish(session_ids={active_id}, detail_only=True, record_only=True)
    assert durable_tables(restored) == before
    assert restored._session_projection_refs == old_references
    assert dormant_id not in {row['id'] for row in restored._state['sessions']}
    monkeypatch.setattr(restored.directory, 'finish_migration', original)
    restored._publish(session_ids={active_id}, detail_only=True, record_only=True)
    assert restored.directory.resolve(active_id)['title'] == 'A pending rename'
    assert load_owner(restored.db, dormant_id)[0] is not None
    assert len(load(restored.db)['sessions']) == 3


async def test_restart_admits_selected_and_pending_owners_without_replaying_or_hydrating_other_chats(app_factory, monkeypatch):
    app, rows = saved_chats(app_factory, 8)
    rows[1].update(status='working', configurationBusy=True)
    rows[2]['configurationPending'] = {'revision': 4}
    rows[3]['naming'] = {'status': 'working'}
    rows[4]['bundleChange'] = {'phase': 'working'}
    rows[1]['workers'] = [{'id': 'child', 'status': 'stopping'}]
    app._save()
    await app.close()
    from amplifier_web import state_records
    calls = []
    original = state_records.load_owner
    def observed(db, identity):
        calls.append(identity)
        return original(db, identity)
    monkeypatch.setattr(state_records, 'load_owner', observed)
    restored = app_factory(home=app.data_dir)
    expected = {row['id'] for row in rows[:5]}
    assert {row['id'] for row in restored._state['sessions']} == expected
    assert set(calls) == expected
    assert restored._session(rows[1]['id'])['status'] == 'interrupted'
    assert restored._session(rows[1]['id'])['workers'][0]['status'] == 'interrupted'
    assert restored._session(rows[3]['id'])['naming']['status'] == 'error'
    assert restored._session(rows[4]['id'])['bundleChange']['phase'] == 'error'
    assert not restored.runtime.started and not restored.runtime.sent
    assert len(load(restored.db)['sessions']) == 8


async def test_directory_queries_keep_dormant_record_cold_and_do_not_open_writer_transaction(app_factory):
    app, rows = saved_chats(app_factory, 8)
    await app.close()
    restored = app_factory(home=app.data_dir)
    before = durable_tables(restored)
    assert not restored.db.in_transaction
    assert restored.directory.resolve(rows[-1]['id'])['title'] == 'Local title 7'
    assert len(restored.directory.select(limit=3)) == 3
    assert not restored.db.in_transaction
    assert durable_tables(restored) == before
    assert len(restored._state['sessions']) == 1


async def test_explicit_managed_deletion_removes_dormant_owner_without_deleting_unrelated_records(app_factory):
    app = app_factory()
    result = await app.dispatch('session.create', {'location': {'kind': 'managed'}, 'title': 'Delete exactly this chat'})
    target_id = result['sessionId']
    target = app._session(target_id)
    target['messages'] = [{'id': 'keep-until-delete', 'role': 'assistant', 'text': 'Owned payload ' + 'x' * 20000}]
    unrelated = app._new_session({'title': 'Keep this chat'})
    active = app._new_session({'title': 'Currently selected chat'})
    app._state['sessions'].extend((unrelated, active))
    app._state['selectedSessionId'] = active['id']
    app._save()
    unrelated_record = copy.deepcopy(load_owner(app.db, unrelated['id']))
    await app.close()
    restored = app_factory(home=app.data_dir)
    assert target_id not in {row['id'] for row in restored._state['sessions']}
    assert unrelated['id'] not in {row['id'] for row in restored._state['sessions']}
    preview = await restored.dispatch('session.deletePreview', {'id': target_id}, include_state=False)
    result = await restored.dispatch('session.delete', {'id': target_id,
        'confirmationToken': preview['result']['confirmationToken']}, include_state=False)
    assert result['result']['deleted'] is True
    assert load_owner(restored.db, target_id) == (None, None)
    assert restored.directory.resolve(target_id) is None
    assert load_owner(restored.db, unrelated['id']) == unrelated_record
    assert not restored.runtime.started and not restored.runtime.sent
    await restored.close()
    again = app_factory(home=app.data_dir)
    assert {row['id'] for row in load(again.db)['sessions']} == {unrelated['id'], active['id']}
    assert again.directory.resolve(target_id) is None


async def test_admitting_one_native_owner_does_not_resolve_an_ambiguous_dormant_alias(app_factory, tmp_path):
    from amplifier_web.native_catalog import NativeCatalog
    from amplifier_web.session_files import amplifier_home
    from test_native_directory import seed
    app = app_factory()
    catalog = NativeCatalog(app.data_dir / 'native-catalog.sqlite3', amplifier_home())
    seed(catalog, tmp_path / 'native-one', 1)
    seed(catalog, tmp_path / 'native-two', 1)
    app.directory.bind(catalog)
    first, second = catalog.lookup('saved-00000')
    admitted = app._session(first['id'], hydrate=False)
    assert admitted['nativeProject'] == first['nativeProject']
    assert len(app._state['sessions']) == 1
    with pytest.raises(AppError, match='more than one project'):
        app._session('saved-00000', hydrate=False)
    assert app._session(second['id'], hydrate=False)['nativeProject'] == second['nativeProject']
    assert not app.runtime.started and not app.runtime.sent
