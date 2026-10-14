"""Shell pages preserve canonical results without duplicate or unrelated scans."""
from copy import deepcopy
import json

import pytest

from amplifier_web import chat_navigation
from amplifier_web.history_directory_query import chats, archived_count, pinned_navigation
from test_history_directory_navigation import directory_fixture
from test_history_directory_exact_ids import native_fixture


@pytest.mark.parametrize('status', ['all', 'working', 'unread', 'future-kind'])
def test_chat_totals_reuse_all_activity_groups(tmp_path, status):
    state, directory = directory_fixture(tmp_path)
    state['sessions'] = []
    # Unknown activity groups must still count in the complete page, even though
    # the existing public counters deliberately expose just four known kinds.
    directory.db.execute("UPDATE history_local_rows SET base_activity_kind='future-kind' WHERE id='s1'")
    directory.db.commit()
    state['view']['navStatusFilter'] = status
    traces = []
    directory.db.set_trace_callback(traces.append)
    try:
        result = chats(directory, state)
    finally:
        directory.db.set_trace_callback(None)
    assert not any(sql.endswith(' SELECT COUNT(*) FROM filtered') for sql in traces)
    assert len([sql for sql in traces if sql.endswith('GROUP BY activity_kind')]) == 1
    # Evaluate the old count independently against the same canonical query.
    from amplifier_web.history_directory_query import _chat_query
    sql, params, *_ = _chat_query(directory, state, None)
    assert result['total'] == directory.db.execute(sql+' SELECT COUNT(*) FROM filtered', params).fetchone()[0]
    if status == 'future-kind': assert result['total'] == 1


@pytest.mark.parametrize('section', [None, 'search', 'pinned'])
def test_saved_or_pinned_page_does_not_rank_selected_history(tmp_path, section):
    complete, directory = directory_fixture(tmp_path)
    complete['view']['navChatScope'] = 'workspace'
    initial = chat_navigation.snapshot(complete, section=section)
    complete['view']['navChatPage'] = {**initial['scope'], 'index': 1}
    complete['view']['navPinnedPage'] = 1
    expected = chat_navigation.snapshot(complete, section=section)
    state = {**complete, 'sessions': []}
    traces = []
    directory.db.set_trace_callback(traces.append)
    try:
        assert chats(directory, state, section=section) == expected
    finally:
        directory.db.set_trace_callback(None)
    assert not any('ranked AS' in sql for sql in traces)


@pytest.mark.parametrize('count', [270, 27000])
def test_sparse_pins_and_archives_only_visit_requested_public_owners(tmp_path, count):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, count, 6)
    state['workspaces'].append(workspace)
    state['sessions'] = []
    state['pinnedSessionIds'] = ['saved-00001', 'saved-00002', 's1']
    state['pinnedItemIds'] = ['session:'+value for value in state['pinnedSessionIds']]
    state['pinnedWorkspaceIds'] = []
    archived = {'saved-00003': True, 's0': True, 'unknown': True}
    # s0 is a delegate; unknown archived IDs remain counted as before.
    state['conversationOrganization'] = {'archived': archived}
    before = deepcopy(state)
    work = 0
    def budget():
        nonlocal work
        work += 1000
        return work > 150000
    directory.db.set_progress_handler(budget, 1000)
    try:
        page = chats(directory, state, section='pinned')
        mixed = pinned_navigation(directory, state, [])
        assert archived_count(directory, state) == 2
    finally:
        directory.db.set_progress_handler(None, 0)
    assert [row['id'] for row in page['items']] == state['pinnedSessionIds']
    assert [row['row']['id'] for row in mixed['items']] == state['pinnedSessionIds']
    assert state == before
    print(json.dumps({'catalog': count, 'pinArchiveVm': work}))


def test_large_pin_and_archive_sets_preserve_global_page_and_count(tmp_path):
    complete, directory = directory_fixture(tmp_path, 400)
    complete['pinnedSessionIds'] = [f's{i}' for i in range(350)]
    complete['view']['navPinnedPage'] = 2
    complete['conversationOrganization']['archived'] = {f's{i}': True for i in range(300)}
    complete['view']['navArchive'] = 'all'
    expected = chat_navigation.snapshot(complete, section='pinned')
    state = {**complete, 'sessions': []}
    assert chats(directory, state, section='pinned') == expected
    expected_count = 300 - len([row for row in complete['sessions'][:300] if row['sessionKind'] != 'root'])
    assert archived_count(directory, state) == expected_count
