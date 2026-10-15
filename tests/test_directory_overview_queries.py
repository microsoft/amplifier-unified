"""Exact agent overview pointers without full-directory parent/body projection."""
import json
import uuid

from amplifier_web import directory_state
from amplifier_web.agent_state import read_state
from amplifier_web.history_directory import COLUMNS, HistoryDirectory
from amplifier_web.native_catalog import NativeCatalog, SCALAR_COLUMNS, row_scalars
from amplifier_web.session_files import amplifier_home, project_slug
from test_automatic_history import app_factory


def put(catalog, workspace, rows):
    project = project_slug(workspace)
    summary = {'id': uuid.uuid5(uuid.NAMESPACE_URL, str(workspace)).hex,
               'nativeProject': project, 'path': str(workspace), 'name': workspace.name,
               'available': True}
    columns = ','.join(('namespace', 'project', 'identity', 'value', *SCALAR_COLUMNS))
    def values():
        for value in rows:
            identity = value['nativeIdentity']
            row = {'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{project}/{identity}').hex,
                   'nativeProject': project, 'workspaceId': summary['id'], 'workspace': str(workspace),
                   'title': identity, 'createdAt': 1, 'updatedAt': 1,
                   'sessionKind': 'worker', **value}
            yield (catalog.namespace, project, identity, json.dumps(row), *row_scalars(row, summary))
    with catalog.connect() as db:
        db.execute('INSERT OR REPLACE INTO native_projects VALUES(?,?,?)',
                   (catalog.namespace, project, json.dumps({'workspace': summary})))
        db.executemany('INSERT OR REPLACE INTO native_rows (' + columns + ') VALUES('
                      + ','.join('?' for _ in range(4 + len(SCALAR_COLUMNS))) + ')', values())
    return project, summary


def reference(directory, selected):
    sql, bound = directory.effective()
    sql += ', ranked AS (SELECT *,ROW_NUMBER() OVER(ORDER BY ordinal,id)-1 AS position FROM directory_rows)'
    def page(where, params=(), limit=50):
        rows = directory.db.execute(sql + ' SELECT ' + ','.join(COLUMNS)
            + ',position FROM ranked WHERE ' + where + ' ORDER BY position LIMIT ?', (*bound, *params, limit))
        return [(row[-1], directory.decode(row[:-1])) for row in rows]
    owner = page('id=?', (selected,), 1)
    roots = page("session_kind='root'")
    children = page("session_kind!='root' AND parent_id IS ?", (selected,), 20) if selected is not None else []
    total = directory_state._count(directory, "session_kind!='root' AND parent_id IS ?", (selected,)) if selected is not None else 0
    return (owner[0] if owner else None), roots, children, total


async def test_overview_exact_parent_and_global_positions_match_reference(app_factory, tmp_path):
    app = app_factory()
    catalog = NativeCatalog(app.data_dir / 'native.sqlite3', amplifier_home())
    project, workspace = put(catalog, tmp_path / 'a', [
        {'nativeIdentity': 'root', 'sessionKind': 'root'},
        {'nativeIdentity': 'parent', 'sessionKind': 'root'},
        {'nativeIdentity': 'child', 'parentId': 'parent'},
        {'nativeIdentity': 'masked', 'parentId': 'parent'},
        {'nativeIdentity': 'managed', 'parentId': 'parent'},
        {'nativeIdentity': 'self', 'parentId': 'self'},
        {'nativeIdentity': 'orphan', 'parentId': 'unregistered'},
        *({'nativeIdentity': f'other-{i}', 'sessionKind': 'root'} for i in range(55))])
    put(catalog, tmp_path / 'b', [{'nativeIdentity': 'parent', 'sessionKind': 'root'},
                                 {'nativeIdentity': 'child', 'parentId': 'parent'}])
    parent = app._new_session({'title': 'Local parent'})
    parent.update(id='local-parent', nativeIdentity='parent', nativeProject=project, runtimeSessionId='parent',
                  workspace=str(tmp_path / 'a'), workspaceId=workspace['id'])
    masked = app._new_session({'title': 'Web child has another parent'})
    masked.update(id='local-mask', nativeIdentity='masked', nativeProject=project, parentId='elsewhere', sessionKind='worker')
    managed = app._new_session({'title': 'Managed stale parent'})
    managed.update(id='local-managed', nativeIdentity='managed', nativeProject=project, parentId='old-parent',
                   sessionKind='worker', historyManaged=True)
    app._state['sessions'].extend([parent, masked, managed])
    app._state['workspaces'].append(workspace)
    app._save(); app.directory.bind(catalog)
    for selected in ('local-parent', 'parent', 'self', 'unregistered', 'missing', None):
        with_snapshot = app.directory.read_snapshot(app._state)
        try:
            assert directory_state._overview_rows(with_snapshot, selected) == reference(with_snapshot, selected)
            sql, bound = with_snapshot.effective()
            assert directory_state._count(with_snapshot) == with_snapshot.db.execute(
                sql + ' SELECT count(*) FROM directory_rows', bound).fetchone()[0]
        finally:
            with_snapshot.close()
    # The metadata point fetch must preserve native suppression and hidden rows.
    with catalog.connect() as db:
        hidden = db.execute('SELECT hidden_id FROM native_rows WHERE namespace=? AND project=? AND identity=?',
                            (catalog.namespace, project, 'child')).fetchone()[0]
    for hide_workspace in (False, True):
        app._state['hiddenNativeSessions'] = [hidden]
        app._state['hiddenNativeWorkspaces'] = [workspace['id']] if hide_workspace else []
        snapshot = app.directory.read_snapshot(app._state)
        try:
            actual = directory_state._overview_rows(snapshot, 'local-parent')
            assert actual == reference(snapshot, 'local-parent')
            assert 'child' not in [row['nativeIdentity'] for _, row in actual[2]]
        finally:
            snapshot.close()
    assert not app.runtime.started and not app.runtime.sent


async def test_overview_27000_workers_limits_query_work_and_metadata(app_factory, tmp_path, monkeypatch):
    app = app_factory()
    catalog = NativeCatalog(app.data_dir / 'native.sqlite3', amplifier_home())
    count = 27000
    def rows():
        for number in range(12):
            yield {'nativeIdentity': f'root-{number:02d}', 'sessionKind': 'root'}
        for number in range(count):
            yield {'nativeIdentity': f'child-{number:05d}', 'parentId': f'root-{number % 12:02d}'}
    project, workspace = put(catalog, tmp_path / 'workers', rows())
    for number in range(12):
        row = app._new_session({'title': f'Active root {number}'})
        row.update(id=f'app-root-{number:02d}', nativeIdentity=f'root-{number:02d}', nativeProject=project,
                   runtimeSessionId=f'root-{number:02d}', historyManaged=True, historyLoaded=False,
                   workspace=str(tmp_path / 'workers'), workspaceId=workspace['id'], status='working')
        app._state['sessions'].append(row)
    app._state['selectedSessionId'] = 'app-root-00'
    app._state['workspaces'].append(workspace)
    app._save(); app.directory.bind(catalog)
    context = app.state_context()
    decoded = []
    original = HistoryDirectory.decode
    monkeypatch.setattr(HistoryDirectory, 'decode', lambda self, row: (decoded.append(1), original(self, row))[1])
    steps = []
    snapshot = HistoryDirectory.read_snapshot
    def reader(self, state):
        value = snapshot(self, state)
        value.db.set_progress_handler(lambda: (steps.append(1), 0)[1], 1000)
        return value
    monkeypatch.setattr(HistoryDirectory, 'read_snapshot', reader)
    result = read_state(context, {}, session_id='app-root-00', directory=app.directory)
    assert result['subagentChats']['total'] == (count + 11) // 12
    assert len(result['subagentChats']['items']) == min(20, (count + 11) // 12)
    assert len(result['conversations']) == 12
    assert len(decoded) <= 45
    assert [row['id'] for row in result['subagentChats']['items']] == [f'child-{n*12:05d}' for n in range(20)]
    assert [row['$statePath'] for row in result['subagentChats']['items']] == [f'/sessions/{n*12}' for n in range(20)]
    assert [row['$statePath'] for row in result['conversations']] == [f'/sessions/{count+n}' for n in range(12)]
    assert result['session']['$statePath'] == '/sessions/27000'
    assert not app.runtime.started and not app.runtime.sent
    # Global ordinal assignment still scans scalar membership. Guard against
    # the former 32M-step parent/wide-row projections, without a timing limit.
    assert len(steps) * 1000 < 6_000_000
    diagnostic = app.performance_diagnostics.snapshot()['operations']['directory.overview']
    assert diagnostic['count'] == 1
