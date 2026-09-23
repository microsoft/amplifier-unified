"""Only immutable folder geometry may survive a workspace projection rebuild."""
from pathlib import PureWindowsPath

from amplifier_web import workspace_navigation as navigation
from test_workspace_navigation import chat, state, workspace


def clear_geometry():
    navigation._common_root.cache_clear()
    navigation._ancestor_paths.cache_clear()
    navigation._registry_labels.cache_clear()


def test_geometry_reuse_keeps_activity_attention_aliases_and_selection_fresh():
    clear_geometry()
    value = state([workspace('one', '/work/one/app'), workspace('two', '/work/two/app')],
                  [chat('a', 'one', recentActivityAt=10), chat('b', 'two', recentActivityAt=20)], 'one')
    value['view']['navWorkspaceMode'] = 'recent'
    before = navigation.snapshot(value)
    misses = [fn.cache_info().misses for fn in (navigation._common_root, navigation._registry_labels)]
    assert [row['workspaceId'] for row in before['rows']] == ['two', 'one']
    value['sessions'][0].update(status='working', recentActivityAt=30)
    value['attention'] = {'sessions': {'a': 1}}
    value['workspaces'][0]['name'] = 'Updated alias'
    value['selectedWorkspaceId'] = 'two'
    # Mutating an old caller-owned snapshot must not poison cached geometry.
    before['rows'][0]['activityCounts']['working'] = 99
    after = navigation.snapshot(value)
    assert [row['workspaceId'] for row in after['rows']] == ['one', 'two']
    assert after['rows'][0]['customName'] == 'Updated alias'
    assert after['rows'][0]['activityCounts']['working'] == 1
    assert after['rows'][0]['unread'] == 1
    assert after['rows'][1]['activityCounts']['working'] == 0
    assert after['selected']['workspaceId'] == 'two'
    assert [fn.cache_info().misses for fn in (navigation._common_root, navigation._registry_labels)] == misses


def test_registry_changes_recompute_unique_labels_and_folder_roots():
    clear_geometry()
    value = state([workspace('one', '/work/one/app'), workspace('two', '/work/two/app')],
                  [chat('a', 'one'), chat('b', 'two')])
    value['view']['navWorkspaceMode'] = 'recent'
    first = navigation.snapshot(value)
    assert {row['pathLabel'] for row in first['rows']} == {'one/app', 'two/app'}
    value['workspaces'][1]['available'] = False
    single = navigation.snapshot(value)
    assert single['rootPath'] == '/work/one'
    assert single['rows'][0]['pathLabel'] == 'app'
    value['workspaces'].append(workspace('three', '/elsewhere/three/app'))
    value['sessions'].append(chat('c', 'three'))
    added = navigation.snapshot(value)
    assert added['rootPath'] == '/'
    assert {row['pathLabel'] for row in added['rows']} == {'one/app', 'three/app'}
    value['workspaces'][1]['available'] = True
    restored = navigation.snapshot(value)
    assert restored['totalWorkspaces'] == 3
    assert {row['pathLabel'] for row in restored['rows']} == {'one/app', 'two/app', 'three/app'}


def test_windows_geometry_cache_preserves_current_spelling_despite_path_equality():
    clear_geometry()
    upper = PureWindowsPath('C:/Work/Team/App')
    lower = PureWindowsPath('c:/work/team/app')
    assert upper == lower  # Path equality is unsuitable as the cache identity.
    assert str(navigation._root([upper])) == 'C:\\Work\\Team'
    assert str(navigation._root([lower])) == 'c:\\work\\team'
    assert [str(path) for path in navigation._ancestors(upper, PureWindowsPath('C:/'))] == [
        'C:\\Work\\Team', 'C:\\Work', 'C:\\']
    assert [str(path) for path in navigation._ancestors(lower, PureWindowsPath('c:/'))] == [
        'c:\\work\\team', 'c:\\work', 'c:\\']

