from copy import deepcopy

import pytest

from amplifier_web.chat_navigation import snapshot, view_patch
from amplifier_web.navigation_summary import activity, path_labels
from amplifier_web.workspace_navigation import snapshot as workspaces
from test_chat_navigation import state_fixture, chat


def test_actionable_states_are_distinct_from_unread_and_background_lifecycle():
    assert activity({'status': 'working', 'approvals': [{'status': 'pending'}]})['kind'] == 'attention'
    assert activity({'status': 'working', 'error': 'old failure'})['kind'] == 'working'
    assert activity({'status': 'error'}, True)['kind'] == 'attention'
    assert activity({'approvals': [{'status': 'allowed'}]}, True)['kind'] == 'unread'
    assert activity({'status': 'idle'}) == {'kind': 'idle', 'label': 'Idle'}


def test_unique_path_suffixes_include_ancestors_only_when_needed():
    labels = path_labels(['/work/team/app', '/personal/team/app', '/work/other', '/app', 'C:\\dev\\other'])
    assert labels['/work/team/app'] == 'work/team/app'
    assert labels['/personal/team/app'] == 'personal/team/app'
    assert labels['/app'] == '/app'
    assert labels['/work/other'] != labels['C:\\dev\\other']
    assert len(set(labels.values())) == len(labels)


def test_activity_filters_cover_the_full_library_before_pagination_and_keep_metadata_bounded():
    state = state_fixture()
    state['view'] = {'navChatScope': 'all', 'navStatusFilter': 'attention'}
    state['sessions'] = [chat(f'idle-{i}') for i in range(205)] + [
        chat('approve', approvals=[{'status': 'pending', 'arguments': {'secret': 'never publish'}}]),
        chat('failed', status='error', error='Sensitive provider exception'),
        chat('busy', status='working'), chat('new'),
    ]
    state['attention'] = {'sessions': {'new': 1}}
    before = deepcopy(state)
    page = snapshot(state)
    assert {row['id'] for row in page['items']} == {'approve', 'failed'}
    assert page['activityCounts'] == {'idle': 205, 'attention': 2, 'working': 1, 'unread': 1}
    assert 'never publish' not in str(page) and 'Sensitive provider' not in str(page)
    state['view']['navStatusFilter'] = 'working'
    assert [row['id'] for row in snapshot(state)['items']] == ['busy']
    state['view']['navStatusFilter'] = 'attention'
    assert state == before


def test_recent_workspaces_are_bounded_and_sorted_by_conversation_activity():
    state = state_fixture()
    state['sessions'] = [chat('old', recentActivityAt=10), chat('new', 'two', recentActivityAt=50)]
    state['view']['navWorkspaceMode'] = 'recent'
    rows = workspaces(state)['rows']
    assert rows[0]['workspaceId'] == 'two'
    assert rows[0]['recentActivityAt'] == 50
    assert len(rows) == 2
    state['sessions'][0]['status'] = 'working'
    assert workspaces(state)['rows'][1]['activityCounts']['working'] == 1


@pytest.mark.parametrize('patch', [{'navWorkspaceList': 'yes'}, {'navStatusFilter': 'bogus'}])
def test_invalid_navigation_choices_are_rejected(patch):
    with pytest.raises(ValueError):
        view_patch(patch)
