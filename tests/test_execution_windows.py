"""Indexed Work windows retain exact open admissions without loading all rows."""
import sqlite3

import pytest

from amplifier_web import execution_records as records
from amplifier_web.cold_display import ColdRecord, MARKER
from amplifier_web.execution_working_set import PendingExecution, current, exact, working
from test_resident_work_pages import live_fixture


def indexed(tmp_path, windows, source):
    db = sqlite3.connect(tmp_path / 'indexed.sqlite3')
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    reference = records.import_tree(db, (source['id'],), source.pop('execution'))
    db.commit()
    session = ColdRecord({**source, MARKER: {'execution': reference}}, db)
    windows.service.db = db
    windows.service._session = lambda identity, hydrate=False: session
    return db, session


def save(db, session):
    owner = working(session)
    pending = PendingExecution(owner, owner.stage())
    db.commit()
    pending.committed(session)


async def test_deleted_current_turn_is_not_reintroduced_by_window_metadata(tmp_path, monkeypatch):
    from amplifier_web.execution_windows import selection
    windows, source, _ = live_fixture(tmp_path, monkeypatch, count=5)
    db, session = indexed(tmp_path, windows, source)
    try:
        turn = exact(session, 'turns', {'id': 't'})
        current(session)['turns'].remove(turn)
        assert selection(session).tree()['turns'] == []
        assert windows.live_pages._source(session, 0, 5)['execution']['turns'] == []
        assert not dict.__contains__(session, 'execution')
    finally:
        await windows.close()
        db.close()


async def test_ordered_work_pages_and_open_fields_survive_completion_and_append(tmp_path, monkeypatch):
    windows, source, _ = live_fixture(tmp_path, monkeypatch)
    db, session = indexed(tmp_path, windows, source)
    try:
        monkeypatch.setattr(records, 'materialize', lambda *args, **kwargs: pytest.fail('Full history read'))
        first = windows.live_pages.read(session, 't@user')
        older = windows.live_pages.read(session, 't@user', before=first['before'])
        assert [row['id'] for row in older['items']] == [str(i) for i in range(26)]
        rows = older['items'] + first['items']
        known = {row['id']: row['detailVersion'] for row in rows}
        retained = rows[-1]['outputDetail']
        assert windows.live_pages.read_text(session, retained)['value'] == 'body ' * 1000
        exact(session, 'nodes', {'id': '0'})['label'] = 'Late exact correction'
        current(session)['nodes'].append({'id': '126', 'turnId': 't', 'kind': 'tool', 'label': 'new',
                                          'phase': 'completed', 'startedAt': 1, 'endedAt': 2, 'output': 'new result'})
        save(db, session)
        windows.invalidate({'s'})
        result = windows.live_pages.sync(session, 't@user', known)
        assert result['order'] == [str(i) for i in range(127)]
        assert {'0', '126'} <= {row['id'] for row in result['items']}
        assert windows.live_pages.read_text(session, retained)['value'] == 'body ' * 1000
        assert not dict.__contains__(session, 'execution')
    finally:
        await windows.close()
        db.close()


async def test_deleted_then_reinserted_id_cannot_reuse_open_step_admission(tmp_path, monkeypatch):
    windows, source, _ = live_fixture(tmp_path, monkeypatch, count=5)
    db, session = indexed(tmp_path, windows, source)
    try:
        page = windows.live_pages.read(session, 't@user')
        field = page['items'][1]['outputDetail']
        row = exact(session, 'nodes', {'id': '0'})
        current(session)['nodes'].remove(row)
        save(db, session)
        # Unrelated deletion can move rank, but not this exact admitted ordinal.
        assert windows.live_pages.read_text(session, field)['value'] == 'body ' * 1000
        current(session)['nodes'].append(row)
        save(db, session)
        with pytest.raises(ValueError, match='changed'):
            windows.live_pages.sync(session, 't@user', {item['id']: item['detailVersion'] for item in page['items']})
    finally:
        await windows.close()
        db.close()


async def test_native_field_leases_survive_index_root_changes_and_recent_window_turnover(tmp_path, monkeypatch):
    from test_open_work_fields import opened, append
    from amplifier_web.session_projection import stored_execution
    windows, source, path, _ = await opened(tmp_path, monkeypatch)
    # Same canonical projection that ordinary persistence owns; body fields
    # remain in their native event owner and exact field lease.
    source['execution']['nodes'][-1]['producerId'] = 'producer'
    source['execution'] = stored_execution(source['execution'])
    db, session = indexed(tmp_path, windows, source)
    try:
        page = windows.live_pages.read(session, 't@user')
        first = {row['id']: row for row in page['items']}
        refs = [first['tool:s:call']['inputDetail'], first['tool:s:call']['outputDetail']]
        expected = [await windows.read_text('s', reference) for reference in refs]
        row = exact(session, 'nodes', {'id': 'tool:s:call'})
        row.update(phase='completed', endedAt=10, revision=2)
        save(db, session)
        append(path, 140)
        await windows.read('s')
        windows.live_pages.sync(session, 't@user', {key: row['detailVersion'] for key, row in first.items()})
        assert [await windows.read_text('s', reference) for reference in refs] == expected
        assert not dict.__contains__(session, 'execution')
    finally:
        await windows.close()
        db.close()
