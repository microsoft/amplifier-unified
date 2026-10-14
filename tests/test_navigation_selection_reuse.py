"""Selection receipts can change without rebuilding identical navigation rows."""
from copy import deepcopy

import pytest

from amplifier_web.shell_resources import scope_for
from test_shell_modules import service


def state_for(service, selected, workspace='one', **view):
    return {**service.state, 'selectedSessionId': selected, 'selectedWorkspaceId': workspace,
            'view': {**service.state['view'], 'navChatScope': 'all', **view}}


@pytest.mark.parametrize('section', ['shortcuts', 'pinned'])
async def test_ordinary_selection_reuses_rows_with_detached_current_scope(service, monkeypatch, section):
    service.state['pinnedSessionIds'] = ['alpha', 'beta'] if section == 'pinned' else []
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces', 'pinnedSessionIds'})
    first = service.projections.chats(state_for(service, 'alpha'), section=section)
    frozen = deepcopy(first)
    following_state = state_for(service, 'beta', 'two')
    expected = service.directory.chats(following_state, section=section)
    def forbidden(*args, **kwargs):
        raise AssertionError('A normal selection must reuse the canonical page')
    monkeypatch.setattr(service.directory, 'chats', forbidden)
    following = service.projections.chats(following_state, section=section)
    assert following == expected
    assert following['items'] is first['items']
    assert following['scope']['selectedSessionId'] == 'beta'
    assert first == frozen


async def test_selected_agent_created_root_is_exact_filter_dependency(service):
    for row in service.state['sessions']:
        row['collaboration'] = {'creatorSessionId': 'creator', 'requestId': row['id']}
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    for selected, expected in [('alpha', ['alpha']), ('beta', ['beta']), (None, [])]:
        scoped = state_for(service, selected)
        page = service.projections.chats(scoped, section='shortcuts')
        assert [row['id'] for row in page['items']] == expected
        assert page == service.directory.chats(scoped, section='shortcuts')


async def test_mixed_pins_reuse_selection_but_keep_workspace_scope(service, monkeypatch):
    service.state.update(pinnedSessionIds=['alpha', 'beta'], pinnedWorkspaceIds=['one', 'two'])
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces', 'pinnedSessionIds', 'pinnedWorkspaceIds'})
    first = service.projections.pinned_navigation(state_for(service, 'alpha'))
    assert service.projections.pinned_navigation(state_for(service, 'beta', 'two')) is first
    for workspace, selected in [('one', 'beta'), ('two', 'alpha')]:
        scoped = state_for(service, selected, workspace, navChatScope='workspace')
        page = service.projections.pinned_navigation(scoped)
        assert page == service.projections._pinned_navigation(scoped)
        assert {item['row']['workspaceId'] for item in page['items']} == {workspace}


@pytest.mark.parametrize('mode,section', [('all', None), ('all', 'recent'), ('workspace', None), ('workspace', 'search')])
async def test_saved_and_inferred_pages_keep_selection_semantics(service, tmp_path, mode, section):
    from amplifier_web.native_catalog import NativeCatalog
    from test_native_directory import seed
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    _, workspace = seed(catalog, tmp_path / 'workspace', 125)
    service.state['workspaces'].append(workspace)
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.directory.bind(catalog)
    initial = state_for(service, 'saved-00001', workspace['id'], navChatScope=mode)
    page = service.projections.chats(initial, section=section)
    saved = {**page['scope'], 'index': 1}
    initial['view']['navChatPage'] = saved
    assert service.projections.chats(initial, section=section)['index'] == 1
    changed = {**initial, 'selectedSessionId': 'saved-00124'}
    actual = service.projections.chats(changed, section=section)
    assert actual == service.directory.chats(changed, section=section)
    assert actual['scope']['selectedSessionId'] == 'saved-00124'
    if mode == 'all' or section == 'recent':
        assert actual['index'] == 0


async def test_shell_selection_reuses_canonical_pages_and_current_identity(service, monkeypatch):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    client = service.clients.attach('selection-client')
    client.update(selectedSessionId='alpha', selectedWorkspaceId='one')
    with service.clients.bind('selection-client'):
        owner = service.shell.resources
        wanted = ['recentNavigation', 'pinnedNavigation', 'workspaceShortcuts']
        owner.register('selection-client', 1, wanted, scope_for(service.state))
        first = owner.snapshot('selection-client')
        frozen = deepcopy(first)
        def forbidden(*args, **kwargs):
            raise AssertionError('Selection must not rerun global navigation queries')
        monkeypatch.setattr(service.directory, 'chats', forbidden)
        monkeypatch.setattr(service.directory, 'workspace_counts', forbidden)
        monkeypatch.setattr(service.projections, '_pinned_navigation', forbidden)
        client.update(selectedSessionId='beta', selectedWorkspaceId='two')
        owner.register('selection-client', 2, wanted, scope_for(service.state))
        following = owner.snapshot('selection-client')
        assert following['scope'][:2] == ['beta', 'two']
        for identity, item in following['instances'].items():
            before = first['instances'][identity]['resources']
            after = item['resources']
            assert after['recentNavigation']['value']['recentNavigation']['scope']['selectedSessionId'] == 'beta'
            assert after['recentNavigation']['value']['recentNavigation']['items'] == before['recentNavigation']['value']['recentNavigation']['items']
            assert after['pinnedNavigation'] == before['pinnedNavigation']
            assert after['workspaceShortcuts'] == before['workspaceShortcuts']
        assert first == frozen


async def test_managed_selection_and_pinned_instance_keep_canonical_scope(service):
    service.state['sessions'][1].update(location={'kind': 'managed'}, workspaceId=None)
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    owner = service.clients.attach('scoped-client')
    owner.update(selectedSessionId='alpha', selectedWorkspaceId='one')
    with service.clients.bind('scoped-client'):
        client = service.shell.client('scoped-client')
        client['baselineView']['navChatScope'] = 'workspace'
        instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'}
        first = service.shell.scoped_state(client, instance)
        assert service.projections.chats(first)['scope']['mode'] == 'workspace'
        owner.update(selectedSessionId='beta', selectedWorkspaceId=None)
        changed = service.shell.scoped_state(client, instance)
        page = service.projections.chats(changed)
        assert page['scope']['mode'] == 'all'
        assert page == service.directory.chats(changed)
        pinned = {**instance, 'scope': {'mode': 'pinned', 'workspaceId': 'one'}}
        result = service.shell.navigation(client, pinned, identity='scoped-client',
                                          navigation_resources={'recentNavigation'})
        assert result['recentNavigation']['scope']['workspaceId'] == 'one'
        assert [row['id'] for row in result['recentNavigation']['items']] == ['alpha']


@pytest.mark.parametrize('count', [270, 2700, 27000])
async def test_scale_100_selection_switches_reuse_pages_without_decoding(service, tmp_path, monkeypatch, count):
    import json
    from amplifier_web.native_catalog import NativeCatalog
    from test_native_directory import seed
    from test_history_directory_query_budget import measure_sql
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    _, workspace = seed(catalog, tmp_path / 'workspace', count)
    service.state['workspaces'].append(workspace)
    service.state['pinnedSessionIds'] = ['alpha']
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces', 'pinnedSessionIds'})
    service.directory.bind(catalog)
    def pages(scoped):
        return (service.projections.chats(scoped, section='shortcuts'),
                service.projections.chats(scoped, section='pinned'),
                service.projections.pinned_navigation(scoped))
    first = pages(state_for(service, 'alpha'))
    def forbidden(*args, **kwargs):
        raise AssertionError('Selection-only reuse must not decode or query historical pages')
    monkeypatch.setattr(service.directory, 'decode', forbidden)
    monkeypatch.setattr(service.directory, 'chats', forbidden)
    monkeypatch.setattr(service.projections, '_pinned_navigation', forbidden)
    samples = []
    for number in range(100):
        selected, wid = ('beta', 'two') if number % 2 == 0 else ('alpha', 'one')
        following, vm, ms = measure_sql(service, lambda: pages(state_for(service, selected, wid)))
        assert following[0]['items'] is first[0]['items']
        assert following[1]['items'] is first[1]['items']
        assert following[2] is first[2]
        assert all(page['scope']['selectedSessionId'] == selected for page in following[:2])
        assert vm < 20000
        samples.append({'vm': vm, 'ms': ms})
    times = sorted(row['ms'] for row in samples)
    print(json.dumps({'selectionDormantRoots': count, 'observations': 100, 'pageQueries': 0,
        'decodedRows': 0, 'maxVm': max(row['vm'] for row in samples), 'p50Ms': times[49],
        'p95Ms': times[94], 'p99Ms': times[98], 'maxMs': times[-1], 'samples': samples}))
