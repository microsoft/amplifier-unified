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


def test_reviewed_failure_does_not_hide_current_obligations_or_active_work():
    failed = {'status': 'error', 'error': 'Reviewed historical failure'}
    assert activity(failed, error_reviewed=True) == {'kind': 'idle', 'label': 'Idle'}
    assert activity(failed, True, error_reviewed=True)['kind'] == 'unread'
    assert activity({**failed, 'status': 'working'}, error_reviewed=True)['kind'] == 'working'
    assert activity({**failed, 'approvals': [{'status': 'pending'}]}, error_reviewed=True)['label'] == 'Approval requested'
    assert activity({**failed, 'questions': [{'status': 'pending', 'required': True}]}, error_reviewed=True)['label'] == 'Answer requested'
    assert activity({**failed, 'questions': [{'status': 'answered', 'delivery': {'status': 'unknown'}}]}, error_reviewed=True)['label'] == 'Check answer delivery'


def test_chat_filter_and_workspace_rollup_use_the_same_reviewed_error_receipt():
    from amplifier_web.attention import snapshot as attention_snapshot
    state = state_fixture()
    state['sessions'] = [{**chat('failed', status='error', error='Historical failure', errorAt=1),
                          'workspace': '/projects/one/shared'}]
    state['attention'] = attention_snapshot(state)
    item = state['attention']['items'][0]
    assert snapshot(state)['activityCounts']['attention'] == 1
    assert workspaces(state)['rows'][0]['activityCounts']['attention'] == 1
    state['attentionRead'] = {item['id']: item['fingerprint']}
    state['attention'] = attention_snapshot(state)
    before = deepcopy(state)
    assert snapshot(state)['items'][0]['activity']['kind'] == 'idle'
    assert workspaces(state)['rows'][0]['activityCounts']['attention'] == 0
    state['view']['navStatusFilter'] = 'attention'
    assert snapshot(state)['items'] == []
    state['view'].pop('navStatusFilter')
    assert state == before
    # A distinct occurrence of the same message has a different fingerprint.
    state['sessions'][0]['errorAt'] = 2
    state['attention'] = attention_snapshot(state)
    assert snapshot(state)['items'][0]['activity']['kind'] == 'attention'
    assert workspaces(state)['rows'][0]['activityCounts']['attention'] == 1


def test_unique_path_suffixes_include_ancestors_only_when_needed():
    labels = path_labels(['/work/team/app', '/personal/team/app', '/work/other', '/app', 'C:\\dev\\other'])
    assert labels['/work/team/app'] == 'work/team/app'
    assert labels['/personal/team/app'] == 'personal/team/app'
    assert labels['/app'] == '/app'
    assert labels['/work/other'] != labels['C:\\dev\\other']
    assert len(set(labels.values())) == len(labels)


def test_path_labels_refresh_on_registry_changes_and_cannot_be_mutated_by_callers():
    paths = ['/work/team/app']
    labels = path_labels(paths)
    assert labels == {'/work/team/app': 'app'}
    labels['/work/team/app'] = 'caller edit'
    assert path_labels(iter(paths)) == {'/work/team/app': 'app'}
    paths.append('/personal/team/app')
    assert path_labels(paths) == {
        '/work/team/app': 'work/team/app',
        '/personal/team/app': 'personal/team/app',
    }
    paths.pop()
    assert path_labels(paths) == {'/work/team/app': 'app'}


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
