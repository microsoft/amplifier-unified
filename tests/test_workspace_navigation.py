import copy
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.workspace_navigation import snapshot, view_patch


def workspace(identity, path, **extra):
    return {'id': identity, 'path': path, 'name': path.rsplit('/', 1)[-1] if path else identity,
            'available': True, **extra}


def chat(identity, workspace_id, **extra):
    return {'id': identity, 'workspaceId': workspace_id, 'sessionKind': 'root', **extra}


def state(workspaces, sessions, selected=None):
    return {'workspaces': workspaces, 'sessions': sessions, 'selectedWorkspaceId': selected, 'view': {}}


def browse(value, **patch):
    value['view'].update(view_patch(value, patch))
    return snapshot(value)


def test_folder_index_shows_only_paths_to_roots_and_separates_selection_from_browsing():
    value = state([
        workspace('app', '/home/me/dev/app'),
        workspace('nested', '/home/me/dev/app/tools/nested'),
        workspace('other', '/home/me/play/other'),
    ], [chat('a', 'app'), chat('n', 'nested'), chat('o', 'other')], 'app')
    initial = snapshot(value)
    assert initial['rootPath'] == '/home/me'
    assert initial['path'] == '/home/me/dev'
    assert initial['parentPath'] == '/home/me'
    assert initial['rows'] == [{'path': '/home/me/dev/app', 'name': 'app', 'workspaceId': 'app',
                                'chatCount': 1, 'descendantWorkspaceCount': 1, 'canBrowse': True, 'unread': 0}]
    inside = browse(value, navWorkspacePath='/home/me/dev/app')
    assert value['selectedWorkspaceId'] == 'app'
    assert inside['rows'][0]['workspaceId'] is None
    assert inside['rows'][0]['path'] == '/home/me/dev/app/tools'
    assert inside['rows'][0]['canBrowse']
    assert inside['rows'][0]['descendantWorkspaceCount'] == 1
    assert inside['rows'][0]['chatCount'] == 0
    leaf = browse(value, navWorkspacePath='/home/me/dev/app/tools')['rows'][0]
    assert leaf['workspaceId'] == 'nested'
    assert not leaf['canBrowse']
    with pytest.raises(ValueError, match='no deeper workspaces'):
        browse(value, navWorkspacePath=leaf['path'])
    assert [row['path'] for row in inside['breadcrumbs']] == ['/home/me', '/home/me/dev', '/home/me/dev/app']


def test_unavailable_unresolved_empty_and_worker_only_workspaces_are_pruned_without_mutation():
    value = state([
        workspace('valid', '/dev/app'), workspace('prefix', '/dev/application'),
        workspace('gone', '/deleted/gone', available=False),
        workspace('unknown', None), workspace('empty', '/empty'),
        workspace('worker-only', '/workers'), workspace('unchecked', '/unchecked', available=None),
    ], [chat('valid-chat', 'valid'), chat('prefix-chat', 'prefix'), chat('gone-chat', 'gone'),
        chat('unresolved-chat', 'unknown'), chat('worker', 'worker-only', sessionKind='worker'),
        chat('unchecked-chat', 'unchecked')], 'valid')
    original = copy.deepcopy(value)
    result = snapshot(value)
    assert result['totalWorkspaces'] == 2
    assert [row['path'] for row in result['rows']] == ['/dev/app', '/dev/application']
    assert all(not row['canBrowse'] for row in result['rows'])
    assert value == original


def test_legacy_paths_forks_and_unread_counts_are_scoped_to_top_level_chats_once():
    value = state([
        workspace('outer', '/work/app'), workspace('inner', '/work/app/child'),
        workspace('boundary', '/work/application'),
    ], [chat('first', 'outer'), {'id': 'fork', 'workspace': '/work/app', 'parentId': 'first'},
        chat('second', 'inner'), chat('second', 'inner'), chat('worker', 'inner', sessionKind='worker'),
        chat('boundary-chat', 'boundary')], 'outer')
    value['attention'] = {'sessions': {'first': 1, 'fork': 1, 'second': 1, 'worker': 1, 'boundary-chat': 1}}
    result = snapshot(value)
    outer, boundary = result['rows']
    assert outer['chatCount'] == 2
    assert outer['descendantWorkspaceCount'] == 1
    assert outer['unread'] == 3
    assert boundary['unread'] == 1 and boundary['descendantWorkspaceCount'] == 0
    nested = browse(value, navWorkspacePath=outer['path'])['rows'][0]
    assert nested['chatCount'] == 1 and nested['unread'] == 1


def test_search_uses_full_paths_aliases_and_fnmatch_for_duplicate_folder_names():
    value = state([
        workspace('one', '/work/team-one/app', name='Design kit'),
        workspace('two', '/work/team-two/app'),
        workspace('nested', '/work/team-two/app/demo'),
    ], [chat('a', 'one'), chat('b', 'two'), chat('c', 'nested')], 'one')
    plain = browse(value, navWorkspaceFilter='APP')
    assert len(plain['rows']) == 3
    assert plain['rows'][0]['customName'] == 'Design kit'
    alias = browse(value, navWorkspaceFilter='design')
    assert [row['workspaceId'] for row in alias['rows']] == ['one']
    glob = browse(value, navWorkspaceFilter='*/TEAM-?WO/APP')
    assert [row['workspaceId'] for row in glob['rows']] == ['two']
    assert glob['rows'][0]['canBrowse']
    browse(value, navWorkspacePath='/work/team-two/app')
    assert snapshot(value)['filter'] == ''
    assert snapshot(value)['rows'][0]['workspaceId'] == 'nested'


def test_pages_are_bounded_and_reset_after_browsing_or_filtering():
    value = state([workspace(str(i), f'/projects/project-{i:03}') for i in range(205)],
                  [chat(f'chat-{i}', str(i)) for i in range(205)])
    assert snapshot(value)['totalWorkspaces'] == 205
    assert len(snapshot(value)['rows']) == 100
    second = browse(value, navWorkspacePage=2)
    assert second['page'] == 2 and second['pages'] == 3
    assert second['rows'][0]['name'] == 'project-100'
    assert len(browse(value, navWorkspacePage=999)['rows']) == 5
    assert snapshot(value)['page'] == 3
    filtered = browse(value, navWorkspaceFilter='*-00?')
    assert filtered['page'] == 1 and filtered['totalRows'] == 10
    reset = browse(value, navWorkspacePath='/projects')
    assert reset['page'] == 1 and reset['filter'] == ''


def test_browsing_persists_for_same_selection_but_reveals_new_workspace_on_selection():
    value = state([workspace('one', '/home/dev/one'), workspace('two', '/home/play/two')],
                  [chat('a', 'one'), chat('b', 'two')], 'one')
    browse(value, navWorkspacePath='/home/play', navWorkspaceFilter='two')
    value['view'].update(view_patch(value, {'draft': 'unchanged location'}))
    assert snapshot(value)['path'] == '/home/play' and snapshot(value)['filter'] == 'two'
    value['selectedWorkspaceId'] = 'two'
    result = snapshot(value)
    assert result['path'] == '/home/play' and result['filter'] == ''
    browse(value, navWorkspaceFilter='play')
    assert value['view']['navWorkspaceBrowseFor'] == 'two'
    assert value['view']['navWorkspacePath'] == '/home/play'


def test_stale_location_falls_back_to_nearest_remaining_ancestor():
    value = state([workspace('app', '/home/dev/app'), workspace('nested', '/home/dev/app/nested'),
                   workspace('other', '/home/play/other')],
                  [chat('a', 'app'), chat('n', 'nested'), chat('o', 'other')], 'app')
    browse(value, navWorkspacePath='/home/dev/app')
    value['workspaces'][1]['available'] = False
    result = snapshot(value)
    assert result['path'] == '/home/dev'
    assert result['rows'][0]['workspaceId'] == 'app'
    value['workspaces'][0]['available'] = False
    result = snapshot(value)
    assert result['rootPath'] == result['path'] == '/home/play'
    assert result['rows'][0]['workspaceId'] == 'other'


@pytest.mark.parametrize('patch', [
    {'navWorkspacePath': '/elsewhere'}, {'navWorkspacePath': '../work'}, {'navWorkspacePath': '/work/../work'},
    {'navWorkspacePath': '/work\0'}, {'navWorkspacePath': False}, {'navWorkspacePath': ''},
    {'navWorkspaceFilter': 123}, {'navWorkspaceFilter': 'a' * 501},
    {'navWorkspacePage': True}, {'navWorkspacePage': 0}, {'navWorkspacePage': 1.5}, {'navWorkspacePage': 1000001},
])
def test_browse_controls_reject_invalid_types_paths_and_bounds(patch):
    value = state([workspace('a', '/work/app')], [chat('one', 'a')])
    with pytest.raises(ValueError):
        view_patch(value, patch)


def test_windows_drive_and_path_boundaries_do_not_depend_on_host_platform():
    value = state([workspace('a', r'C:\Users\me\dev\app', name='app'),
                   workspace('nested', r'C:\Users\me\dev\app\child', name='child'),
                   workspace('b', r'D:\app', name='app')],
                  [chat('a', 'a'), chat('n', 'nested'), chat('b', 'b')], 'a')
    result = snapshot(value)
    assert result['rootPath'] == ''
    assert result['path'] == 'C:\\Users\\me\\dev'
    assert result['rows'][0]['workspaceId'] == 'a' and result['rows'][0]['canBrowse']
    root = browse(value, navWorkspacePath='')
    assert [row['path'] for row in root['rows']] == ['C:\\', 'D:\\']
    assert root['parentPath'] is None
    assert root['breadcrumbs'] == [{'path': '', 'name': 'Workspaces'}]
    drive = browse(value, navWorkspacePath='D:\\')
    assert drive['parentPath'] == ''
    assert not drive['rows'][0]['canBrowse']
    with pytest.raises(ValueError):
        view_patch(value, {'navWorkspacePath': 'C:dev'})


def test_filesystem_root_workspaces_remain_selectable_and_have_only_valid_descendants():
    value = state([workspace('root', '/', name='/'), workspace('dev', '/usr/dev')],
                  [chat('root-chat', 'root'), chat('dev-chat', 'dev')], 'root')
    assert snapshot(value)['rows'][0]['workspaceId'] == 'root'
    root = browse(value, navWorkspacePath='')
    assert root['rows'][0]['path'] == '/'
    assert root['rows'][0]['workspaceId'] == 'root'
    assert root['rows'][0]['canBrowse']
    inside = browse(value, navWorkspacePath='/')
    assert inside['parentPath'] == ''
    assert inside['rows'][0]['path'] == '/usr'
    only_root = state([workspace('root', '/', name='/')], [chat('root-chat', 'root')], 'root')
    assert snapshot(only_root)['rows'][0]['workspaceId'] == 'root'
    assert not snapshot(only_root)['rows'][0]['canBrowse']


def test_unc_shares_and_posix_folders_have_distinct_root_branches():
    value = state([workspace('one', r'\\server\one\app', name='app'),
                   workspace('two', r'\\server\two\app', name='app'),
                   workspace('posix', '/dev/app')],
                  [chat('one', 'one'), chat('two', 'two'), chat('posix', 'posix')])
    result = snapshot(value)
    assert result['rootPath'] == result['path'] == ''
    assert {row['path'] for row in result['rows']} == {'/', '\\\\server\\one\\', '\\\\server\\two\\'}
    assert all(row['workspaceId'] is None and row['descendantWorkspaceCount'] == 1 for row in result['rows'])
    share = browse(value, navWorkspacePath='\\\\server\\one\\')
    assert share['rows'][0]['workspaceId'] == 'one'
    assert not share['rows'][0]['canBrowse']


def test_empty_registry_and_selected_empty_workspace_do_not_require_filesystem_access(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('The folder projection must not scan the filesystem')
    monkeypatch.setattr(Path, 'is_dir', forbidden)
    value = state([workspace('empty', '/projects/empty')], [], 'empty')
    result = snapshot(value)
    assert result['rows'] == [] and result['totalWorkspaces'] == 0
    assert result['page'] == result['pages'] == 1
    assert value['selectedWorkspaceId'] == 'empty'


async def test_agent_and_ui_share_durable_explorer_controls_and_selection_scope(tmp_path):
    first = tmp_path / 'dev' / 'first'
    second = tmp_path / 'other' / 'second'
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    app = AppService(tmp_path / 'state', workspace=first)
    await app.dispatch('session.create', {})
    first_chat = app.state['selectedSessionId']
    await app.dispatch('workspace.add', {'path': str(second)})
    await app.dispatch('session.create', {})
    second_chat = app.state['selectedSessionId']
    await app.dispatch('session.select', {'id': first_chat})
    await app.app_bridge('dispatch', {'action': 'view.update', 'args': {'patch': {'navWorkspacePath': str(tmp_path / 'other')}}}, first_chat)
    assert app.state['selectedSessionId'] == first_chat
    overview = await app.app_bridge('get_state', {}, first_chat)
    assert overview['workspaceExplorer']['path'] == str(tmp_path / 'other')
    assert overview['workspaceExplorer']['rows'][0]['path'] == str(second)
    assert 'navWorkspacePath' in overview['_stateAccess']['history']
    actions = await app.app_bridge('list_actions', {'prefix': 'view.'}, first_chat)
    assert 'navWorkspacePage' in actions[0]['description']
    with pytest.raises(AppError):
        await app.dispatch('view.update', {'patch': {'navWorkspaceBrowseFor': 'spoof'}})
    with pytest.raises(AppError):
        await app.dispatch('view.update', {'patch': {'navWorkspacePage': False}})
    with pytest.raises(AppError):
        await app.dispatch('view.update', {'patch': {'navWorkspaceAncestorsOpen': 1}})
    await app.app_bridge('dispatch', {'action': 'view.update', 'args': {'patch': {'navWorkspaceAncestorsOpen': True}}}, first_chat)
    assert app.get_state()['view']['navWorkspaceAncestorsOpen'] is True
    await app.dispatch('view.update', {'patch': {'navPinned': True}})
    assert app.get_state()['workspaceExplorer']['path'] == str(tmp_path / 'other')
    await app.close()
    restored = AppService(tmp_path / 'state', workspace=first)
    assert restored.get_state()['workspaceExplorer']['path'] == str(tmp_path / 'other')
    await restored.dispatch('session.select', {'id': second_chat})
    assert restored.get_state()['workspaceExplorer']['path'] == str(second.parent)
    assert not restored.get_state()['view'].get('navWorkspaceAncestorsOpen')
    await restored.dispatch('session.select', {'id': first_chat})
    assert restored.get_state()['workspaceExplorer']['path'] == str(first.parent)
    await restored.close()
