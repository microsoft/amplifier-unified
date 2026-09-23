"""Sidebar sections share history, keep filters independent, and bound pages."""
from copy import deepcopy

import pytest

from amplifier_web.chat_navigation import snapshot, view_patch
from test_chat_navigation import state_fixture, chat, ids
from test_shell_modules import service, command, prepare
from amplifier_web.shell_modules import DEFAULT


@pytest.mark.parametrize('count,size,pages', [(0,0,1),(40,40,1),(50,50,1),(51,40,2),(80,40,2),(81,40,3)])
def test_recent_threshold_excludes_pins_before_paging(count, size, pages):
    state = state_fixture()
    state['view']['navChatScope'] = 'all'
    state['sessions'] = [chat(f'chat-{i}', recent=i) for i in range(count)] + [chat('pin')]
    state['pinnedSessionIds'] = ['pin']
    before = deepcopy(state)
    page = snapshot(state, section='recent')
    assert (page['total'], len(page['items']), page['pages']) == (count, size, pages)
    assert 'pin' not in ids(page) and state == before
    assert ids(snapshot(state, section='pinned')) == ['pin']
    state['view']['navChatPage'] = {**page['scope'], 'index': 999}
    final = snapshot(state, section='recent')
    assert final['index'] == pages - 1 and final['end'] == count
    if count:
        state['view']['navFilter'] = 'chat-0'
        filtered = snapshot(state, section='recent')
        assert filtered['index'] == 0 and ids(filtered) == ['chat-0']


def test_pins_remain_available_beyond_a_recent_page():
    state = state_fixture()
    state['view']['navChatScope'] = 'all'
    state['sessions'] = [chat(str(i)) for i in range(150)]
    state['pinnedSessionIds'] = [str(i) for i in range(110)]
    first = snapshot(state, section='pinned')
    state['view']['navPinnedPage'] = 2
    last = snapshot(state, section='pinned')
    assert first['total'] == 110 and len(first['items']) == 40
    assert ids(last) == [str(i) for i in range(80,110)]
    assert snapshot(state, section='recent')['total'] == 40


def test_section_activity_counts_precede_status_filters_and_exclude_pins():
    state = state_fixture()
    state['view'].update(navChatScope='all', navStatusFilter='attention')
    state['sessions'] = [chat('error', error='Failed', status='error'),
                         chat('busy', status='working'), chat('pin', status='working')]
    state['pinnedSessionIds'] = ['pin']
    page = snapshot(state, section='recent')
    assert ids(page) == ['error']
    assert page['activityCounts']['attention'] == page['activityCounts']['working'] == 1


@pytest.mark.parametrize('patch', [
    {'navSectionsCollapsed': 'pinned'}, {'navSectionsCollapsed': ['other']},
    {'navPinnedPage': True}, {'navPinnedPage': -1},
    {'navRecentView': {'navRecentView': {}}}, {'navRecentView': {'navFilter': 4}},
    {'navRecentView': {'navChatScope': 'all'}},
])
def test_rejects_invalid_section_state(patch):
    with pytest.raises(ValueError):
        view_patch(patch)


async def test_sections_are_agent_visible_client_local_and_do_not_fork_history(service):
    service.state['sessions'] += [{**chat(f'alpha-{i}', recent=i), 'workspace':service.state['workspaces'][0]['path'], 'messages':[]} for i in range(60)]
    service.state['pinnedSessionIds'] = ['alpha', 'beta']
    service._save()
    before = deepcopy(service.state['sessions'])
    await command(service, 'shell.view.update', clientId='one', instanceId='chats', patch={
        'navSectionsCollapsed': ['workspaces'], 'navFilter': 'alpha',
        'navRecentView': {'navFilter': 'alpha-5'}, 'navWorkspaceList': False})
    query = (await command(service, 'shell.query', clientId='one', instanceId='chats'))['result']
    sections = query['sidebarNavigation']
    assert ids(sections['pinned']) == ['alpha', 'beta']
    assert sections['recent']['total'] == 11
    assert sections['workspace']['total'] == 61
    assert query['view']['navSectionsCollapsed'] == ['workspaces']
    other = (await command(service, 'shell.query', clientId='two', instanceId='chats'))['result']
    assert other['sidebarNavigation']['recent']['total'] == 60
    assert not other['view'].get('navSectionsCollapsed')
    assert service.state['sessions'] == before
    assert service.state['view']['draft'] == 'Keep this unsent message'
    # Saved view state, including filters/collapse, survives a new read from disk.
    assert service.shell.get('client', 'one')['views']['chats']['view']['navSectionsCollapsed'] == ['workspaces']


async def test_workspace_pinned_module_never_leaks_global_pins_or_recents(service):
    service.state['pinnedSessionIds'] = ['alpha']
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id':'beta-only','package':'builtin.chats','slot':'navigation',
                                    'scope':{'mode':'pinned','workspaceId':'two'}})
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    page = (await command(service, 'shell.query', clientId='browser-one', instanceId='beta-only'))['result']['sidebarNavigation']
    assert page['pinned']['total'] == 0
    assert ids(page['recent']) == ['beta'] and ids(page['workspace']) == ['beta']


async def test_created_empty_workspace_is_visible_in_the_single_explorer(service):
    plan = (await service.dispatch('workspace.prepare', {'name': 'Fresh folder'}, origin='agent'))['result']
    await service.dispatch('workspace.create', {'planId': plan['planId']}, origin='agent', command_id='empty-sidebar-folder')
    result = (await command(service, 'shell.query', clientId='new-client', instanceId='workspaces'))['result']
    assert result['workspaceExplorer']['mode'] == 'recent'
    row = next(row for row in result['workspaceExplorer']['rows'] if row['path'] == plan['path'])
    assert row['chatCount'] == 0 and row['workspaceId']
