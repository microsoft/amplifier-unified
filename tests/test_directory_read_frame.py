"""One synchronous frame prepares live facts once, never across user mutations."""
import asyncio
from contextvars import copy_context
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest

from amplifier_web import history_directory
from test_history_directory_navigation import directory_fixture
from test_shell_modules import service
from test_shell_query_owner import prepare, settle
from test_cold_display import app, add_history
from test_directory_revision_memo import native_fixture


def observe(monkeypatch):
    calls = []
    original = history_directory._scalars
    def counted(row, *args, **kwargs):
        calls.append(row['id'])
        return original(row, *args, **kwargs)
    monkeypatch.setattr(history_directory, '_scalars', counted)
    return calls


def test_nested_queries_prepare_one_overlay_and_match_unscope_oracle(tmp_path, monkeypatch):
    state, directory = directory_fixture(tmp_path, 12)
    def query():
        return (directory.observation_revision, directory.root_count(state),
                directory.select("session_kind='root'", limit=4, state=state))
    expected = query()
    calls = observe(monkeypatch)
    def frame():
        first = query()
        assert directory.read_frame(query) == first
        return first
    assert directory.read_frame(frame) == expected
    assert calls == [row['id'] for row in state['sessions']]


def test_same_revision_in_place_changes_are_seen_in_each_new_frame(tmp_path):
    state, directory = directory_fixture(tmp_path, 12)
    state['revision'] = 7
    def facts():
        return deepcopy(directory.observation_revision), directory.select('id=?', ('s4',), limit=1, state=state)
    first = directory.read_frame(facts)
    state['sessions'][4]['title'] = 'Changed title without publication'
    state['sessions'][4]['approvals'] = [{'id':'approval', 'title':'Allow tool', 'status':'pending'}]
    second = directory.read_frame(facts)
    assert state['revision'] == 7
    assert second[0] != first[0]
    assert second[1][0]['title'] == 'Changed title without publication'
    assert second[1][0]['approvals'][0]['status'] == 'pending'
    state['sessions'][4]['approvals'][0]['status'] = 'approved'
    third = directory.read_frame(facts)
    assert third[0] != second[0]
    assert third[1][0]['approvals'][0]['status'] == 'approved'


def test_changed_owner_inputs_and_durable_order_rebuild_inside_frame(tmp_path, monkeypatch):
    state, directory = directory_fixture(tmp_path, 3)
    calls = observe(monkeypatch)
    alternate = {**state, 'sessions': deepcopy(state['sessions'])}
    alternate['sessions'][1]['title'] = 'Other owner'
    def row(source):
        return directory.select('id=?', ('s1',), limit=1, state=source)[0]
    def frame():
        assert row(state)['title'] != 'Other owner'
        assert row(alternate)['title'] == 'Other owner'
        assert row(state)['title'] != 'Other owner'
        directory.db.execute("UPDATE history_local_rows SET ordinal='-1' WHERE id='s1'")
        assert row(state)['title'] != 'Other owner'
        assert directory.select('1', limit=1, state=state)[0]['id'] == 's1'
        state['sessions'].append({**state['sessions'][1], 'id':'new-owner'})
        assert directory.select('id=?', ('new-owner',), limit=1, state=state)[0]['id'] == 'new-owner'
    directory.read_frame(frame)
    assert len(calls) == 3 * 4 + 4


def test_exception_and_copied_thread_context_never_retain_frame(tmp_path):
    state, directory = directory_fixture(tmp_path, 3)
    def failing():
        directory.effective(state)
        raise RuntimeError('projection failed')
    with pytest.raises(RuntimeError, match='projection failed'):
        directory.read_frame(failing)
    assert directory._read_frame.get() is None

    with ThreadPoolExecutor(max_workers=1) as pool:
        def nested():
            current = directory._read_frame.get()
            ctx = copy_context()
            other = pool.submit(ctx.run, lambda: directory.read_frame(lambda: directory._read_frame.get())).result()
            assert other is not current and other['thread'] != current['thread']
            assert directory._read_frame.get() is current
        directory.read_frame(nested)
    assert directory._read_frame.get() is None

def test_frame_reuse_never_freezes_external_native_query_fences(tmp_path):
    state, directory, catalog, project, _ = native_fixture(tmp_path)
    def frame():
        before = directory.root_count(state)
        revision = directory.observation_revision
        with catalog.connect() as db:
            db.execute('DELETE FROM native_rows WHERE namespace=? AND project=? AND identity=?',
                       (catalog.namespace, project, 'saved-00000'))
            db.execute('UPDATE native_revisions SET observation_membership=observation_membership+1,'
                       'root_navigation=root_navigation+1 WHERE namespace=?', (catalog.namespace,))
        assert directory.root_count(state) == before - 1
        assert directory.observation_revision != revision
    directory.read_frame(frame)



async def test_awaitable_return_does_not_hold_frame_over_event_loop_yield(tmp_path):
    state, directory = directory_fixture(tmp_path, 3)
    async def delayed():
        assert directory._read_frame.get() is None
        await asyncio.sleep(0)
        state['sessions'][1]['title'] = 'After yield'
        return directory.select('id=?', ('s1',), limit=1, state=state)[0]['title']
    assert await directory.read_frame(delayed) == 'After yield'
    assert directory._read_frame.get() is None


async def test_real_browser_snapshot_prepares_each_admitted_row_once(service, monkeypatch):
    prepare(service)
    await settle(service)
    service._session('alpha', hydrate=False)['status'] = 'working'
    service._save_changes(sessions={'alpha'})
    service._client_snapshots.clear()
    calls = observe(monkeypatch)
    with service.clients.bind('browser'):
        result = service.browser_state()
    assert result['selectedSessionId'] == 'alpha'
    assert result['shellResources']['instances']
    assert calls == [row['id'] for row in service._state['sessions']]


async def test_explicit_session_projection_retains_unscoped_hydration(service, monkeypatch):
    seen = []
    def project(session_id):
        assert service.directory._read_frame.get() is None
        seen.append(session_id)
        return {'owner': session_id}
    monkeypatch.setattr(service, '_browser_state', project)
    assert service.browser_state('alpha') == {'owner': 'alpha'}
    assert seen == ['alpha']


async def test_real_cold_explicit_projection_matches_unscoped_owner(app, monkeypatch):
    row = add_history(app, 'cold-explicit')
    original_messages = deepcopy(row['messages'])
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    assert app.cold_display.is_cold(row)
    hydrated = []
    original = app.cold_display.hydrate
    def hydrate(owner):
        assert app.directory._read_frame.get() is None
        hydrated.append(owner['id'])
        return original(owner)
    monkeypatch.setattr(app.cold_display, 'hydrate', hydrate)
    actual = app.browser_state('cold-explicit')
    assert hydrated and set(hydrated) == {'cold-explicit'}
    assert row['messages'] == original_messages
    expected = app._browser_state('cold-explicit')
    for key in ('sessions', 'attention', 'selectedSessionId'):
        assert actual[key] == expected[key]


async def test_spawned_query_task_cannot_reuse_parent_frame_after_return(tmp_path, monkeypatch):
    state, directory = directory_fixture(tmp_path, 3)
    calls = observe(monkeypatch)
    async def child():
        inherited = directory._read_frame.get()
        assert inherited is not None and not inherited['active'] and inherited['owner'] is None
        actual = directory.select('id=?', ('s1',), limit=1, state=state)[0]
        assert actual['title'] == 'Changed after parent returned'
        return directory.read_frame(lambda: directory.select('id=?', ('s1',), limit=1, state=state)[0])
    def parent():
        directory.effective(state)
        return asyncio.create_task(child())
    task = directory.read_frame(parent)
    state['sessions'][1]['title'] = 'Changed after parent returned'
    assert (await task)['title'] == 'Changed after parent returned'
    assert len(calls) == 9
    assert directory._read_frame.get() is None


def test_caught_rebuild_failure_cannot_reuse_previous_owner_receipt(tmp_path, monkeypatch):
    state, directory = directory_fixture(tmp_path, 3)
    alternate = {**state, 'sessions': deepcopy(state['sessions'])}
    alternate['sessions'][1]['title'] = 'Bad alternate'
    original = history_directory._scalars
    calls = []
    def fail_alternate(row, *args, **kwargs):
        calls.append(row['id'])
        if row.get('title') == 'Bad alternate':
            raise ValueError('invalid alternate row')
        return original(row, *args, **kwargs)
    monkeypatch.setattr(history_directory, '_scalars', fail_alternate)
    def frame():
        directory.effective(state)
        with pytest.raises(ValueError, match='invalid alternate'):
            directory.effective(alternate)
        directory.effective(state)
    directory.read_frame(frame)
    assert calls == ['s0', 's1', 's2', 's0', 's1', 's0', 's1', 's2']
