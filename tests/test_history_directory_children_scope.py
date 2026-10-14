"""Child history queries preserve parent authority without scanning other chats."""
import json
import time
import uuid

import pytest

from amplifier_web.browser_state import summary
from amplifier_web.chat_navigation import _matches
from amplifier_web.history_directory import initialize
from amplifier_web.native_catalog import NativeCatalog
from test_history_directory_exact_ids import native_fixture, change_native
from test_native_directory import seed


def original_children(directory, state, parent, query='', index=0):
    """The prior unscoped effective-row predicate, independent of pruning."""
    aliases = list({parent.get(key) for key in ('id', 'nativeIdentity', 'runtimeSessionId')} - {None})
    predicate = "session_kind!='root' AND id!=? AND (parent_id=? OR (native_parent_id IN (SELECT value FROM json_each(?)) AND CASE WHEN native_project IS NOT NULL AND ? IS NOT NULL THEN native_project=? ELSE workspace=? END))"
    params = [parent.get('id'), parent.get('id'), json.dumps(aliases), parent.get('nativeProject'),
              parent.get('nativeProject'), parent.get('workspace')]
    rows = directory.select(predicate, params, limit=10000, order='recent DESC,ordinal,id', state=state)
    matched = [row for row in rows if _matches((row.get('title'), row.get('description'), row['id'],
                                               row.get('nativeIdentity') or row.get('runtimeSessionId') or row['id']), query)]
    total = len(matched)
    pages = max(1, (total+49)//50)
    index = max(0, min(pages-1, index)) if type(index) is int else 0
    start, end = index*50, min(total, (index+1)*50)
    return {'items': [summary(row) for row in matched[start:end]], 'total': total,
            'unfilteredTotal': len(rows), 'index': index, 'pages': pages, 'start': start,
            'end': end, 'scope': {'sessionId': parent.get('id'), 'filter': query}}


@pytest.mark.parametrize('count', [270, 27000])
def test_child_query_sql_work_depends_on_one_parent_not_retained_catalog(tmp_path, count):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, count, 187)
    for index, row in enumerate(state['sessions']):
        row.update(nativeIdentity=f'saved-{index:05d}', nativeProject=project,
                   historyManaged=True, titleSource='native')
    child_ids = [f'saved-{count-offset:05d}' for offset in range(1, 7)]
    changed = [{**catalog.get(project, identity), 'parentId': 'saved-00000', 'sessionKind': 'worker'}
               for identity in child_ids]
    change_native(catalog, project, workspace, changed)
    for index in (1, 2):
        state['sessions'][index].update(nativeIdentity=child_ids[index-1], parentId='old-parent')
    state['sessions'][3].update(historyManaged=False, sessionKind='worker', parentId='s0')
    directory.sync(state['sessions'], state)
    directory.db.commit()
    state['sessions'] = state['sessions'][:6]
    # A current live child may not have a saved parent link yet.
    state['sessions'][4].update(historyManaged=False, sessionKind='worker', parentId='s0')
    parent = directory.resolve('s0')
    expected = original_children(directory, state, parent)
    assert {row['id'] for row in expected['items']} == {'s1', 's2', 's3', 's4', *child_ids[2:]}
    with catalog.connect() as db:
        db.execute('UPDATE native_rows SET value=? WHERE identity NOT IN (SELECT value FROM json_each(?))',
                   ('not JSON', json.dumps(child_ids)))
    selected = [row['id'] for row in expected['items']]
    directory.db.execute('UPDATE history_local_rows SET summary=? WHERE id NOT IN (SELECT value FROM json_each(?))',
                         ('not JSON', json.dumps(selected)))
    directory.db.commit()
    decoded = []
    original = directory.decode
    directory.decode = lambda row: (decoded.append(row['id'] if isinstance(row, dict) else row[0]), original(row))[1]
    instructions = 0
    def progress():
        nonlocal instructions
        instructions += 1000
        return instructions > 150000
    changes = directory.db.total_changes
    started = time.monotonic()
    directory.db.set_progress_handler(progress, 1000)
    try:
        actual = directory.children(state, parent)
        empty = directory.children(state, {'id': 'unknown-parent', 'workspace': '/unknown'})
    finally:
        directory.db.set_progress_handler(None, 0)
    assert actual == expected and empty['total'] == 0
    assert len(decoded) == 8 and set(decoded) == set(selected)
    assert len(state['sessions']) == 6
    assert directory.db.total_changes == changes and not directory.db.in_transaction
    print(json.dumps({'nativeRows': count, 'localOverrides': 187, 'children': 8,
                      'vm': instructions, 'ms': (time.monotonic()-started)*1000}))


def test_parent_aliases_reparenting_filters_and_pages_match_original_scope(tmp_path):
    state, directory, catalog, first, workspace = native_fixture(tmp_path, 130, 3)
    second, other = seed(catalog, tmp_path / 'other', 4)
    for project, folder, end in ((first, workspace, 126), (second, other, 4)):
        changed = [{**catalog.get(project, f'saved-{index:05d}'), 'parentId': 'saved-00000',
                    'sessionKind': 'worker', 'title': f'Child {index:03d}'} for index in range(1, end)]
        change_native(catalog, project, folder, changed)
    parent, child, explicit = state['sessions']
    parent.update(nativeIdentity='saved-00000', nativeProject=first, historyManaged=True)
    child.update(nativeIdentity='saved-00001', nativeProject=first, historyManaged=True,
                 parentId='wrong-saved-parent', nativeParentId='wrong-saved-parent')
    # Explicit app parent relationships retain authority across project scope.
    explicit.update(sessionKind='worker', parentId=parent['id'], nativeProject=second,
                    workspace=other['path'], historyManaged=False)
    directory.sync(state['sessions'], state)
    directory.db.commit()
    state['sessions'] = []
    parent = directory.resolve(parent['id'])
    for query, page in (('', 0), ('', 2), ('Child 01?', 0), ('absent', 0), ('', 999)):
        assert directory.children(state, parent, query, page) == original_children(directory, state, parent, query, page)
    page = directory.children(state, parent)
    assert page['total'] == 126 and any(row['id'] == explicit['id'] for row in page['items']) is False
    all_children = directory.children(state, parent, index=2)
    assert any(row['id'] == child['id'] and row['parentId'] == parent['id'] for row in all_children['items'])
    # A lagging caller must still see children whose current canonical parent
    # is its app ID; native alias candidates alone cannot establish this.
    stale = {**parent, 'nativeIdentity': 'old-native-id', 'runtimeSessionId': 'old-native-id'}
    assert directory.children(state, stale) == original_children(directory, state, stale)
    # Project-less references use the existing workspace fallback, not another
    # project's identical raw parent ID.
    fallback = {'id': 'not-a-saved-id', 'nativeIdentity': 'saved-00000', 'workspace': workspace['path']}
    assert directory.children(state, fallback) == original_children(directory, state, fallback)
    hidden = uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-native:{first}/saved-00002').hex
    for overlay in ({'hiddenNativeSessions': [hidden]}, {'hiddenNativeWorkspaces': [workspace['id']]}):
        scoped = {**state, **overlay}
        assert directory.children(scoped, parent) == original_children(directory, scoped, parent)
    directory.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
        ('delete-child', 'token', 'confirmed', json.dumps({'id': 'delete-child', 'project': 'unrelated', 'ids': [child['id']]})))
    directory.db.commit()
    assert directory.children(state, parent, index=2) == original_children(directory, state, parent, index=2)
    assert state['sessions'] == []


def test_child_scope_keeps_snapshot_and_current_live_parent_ownership(tmp_path):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, 4, 2)
    parent = state['sessions'][0]
    parent.update(nativeIdentity='saved-00000', nativeProject=project, historyManaged=True)
    directory.sync([parent], state)
    directory.db.commit()
    child = {**catalog.get(project, 'saved-00002'), 'parentId': 'saved-00000', 'sessionKind': 'worker'}
    change_native(catalog, project, workspace, [child])
    before = directory.children(state, parent)
    assert before['total'] == 1
    reader = directory.read_snapshot(state)
    try:
        # Move the live owner's native identity without saving it. The current
        # parent mapper also retains its saved fallback; pruning must preserve
        # that existing behavior rather than silently change parent authority.
        parent.update(nativeIdentity='saved-00001', runtimeSessionId='saved-00001')
        assert directory.children(state, parent) == original_children(directory, state, parent)
        moved = {**child, 'parentId': 'saved-00001'}
        change_native(catalog, project, workspace, [moved])
        current = directory.children(state, parent)
        assert current == original_children(directory, state, parent) and current['total'] == 1
        assert reader.children(reader._frozen_state, {**parent, 'nativeIdentity': 'saved-00000',
            'runtimeSessionId': 'n0'}) == before
    finally:
        reader.close()


def test_existing_directory_layout_adds_parent_indexes_without_body_migration(tmp_path):
    state, directory, catalog, _, _ = native_fixture(tmp_path, 4, 2)
    directory.db.execute('DROP INDEX history_local_parent')
    directory.db.execute('DROP INDEX history_local_native_parent')
    directory.db.commit()
    with catalog.connect() as db:
        db.execute('DROP INDEX native_rows_parent_workspace')
        db.execute('UPDATE native_rows SET value=?', ('not JSON',))
    NativeCatalog(catalog.path, catalog.home)
    initialize(directory.db)
    assert {'history_local_parent', 'history_local_native_parent'} <= {
        row[1] for row in directory.db.execute("PRAGMA index_list('history_local_rows')")}
    with catalog.connect() as db:
        assert 'native_rows_parent_workspace' in {row[1] for row in db.execute("PRAGMA index_list('native_rows')")}


def test_exact_and_parent_queries_do_not_scan_27000_local_owners(tmp_path):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, 4, 27000)
    parent = state['sessions'][0]
    parent.update(nativeIdentity='saved-00000', nativeProject=project, historyManaged=True)
    native_child = {**catalog.get(project, 'saved-00001'), 'parentId': 'saved-00000', 'sessionKind': 'worker'}
    change_native(catalog, project, workspace, [native_child])
    state['sessions'][1].update(nativeIdentity='saved-00001', nativeProject=project, historyManaged=True)
    for child in state['sessions'][2:7]:
        child.update(parentId='s0', sessionKind='worker')
    directory.sync(state['sessions'][:7], state)
    directory.db.commit()
    state['sessions'] = state['sessions'][:6]
    parent = directory.resolve('s0')
    wanted = {f's{index}' for index in range(1, 7)}
    directory.db.execute('UPDATE history_local_rows SET summary=? WHERE id NOT IN (SELECT value FROM json_each(?))',
                         ('not JSON', json.dumps(sorted(wanted))))
    directory.db.commit()
    for name, read in (('summaries', lambda: directory.summary_ids(wanted, state=state)),
                       ('children', lambda: directory.children(state, parent)['items'])):
        instructions = 0
        def progress():
            nonlocal instructions
            instructions += 1000
            return instructions > 100000
        directory.db.set_progress_handler(progress, 1000)
        started = time.monotonic()
        try:
            rows = read()
        finally:
            directory.db.set_progress_handler(None, 0)
        assert {row['id'] for row in rows} == wanted and len(rows) == 6
        assert len(state['sessions']) == 6
        print(json.dumps({'query': name, 'localOwners': 27000, 'requested': 6, 'vm': instructions,
                          'ms': (time.monotonic()-started)*1000}))
