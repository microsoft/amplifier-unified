"""Large-library work is shared; client observations and drafts stay private."""
import pytest

from amplifier_web import chat_navigation, workspace_navigation
from amplifier_web.service import AppError
from test_automatic_history import app_factory
from test_browser_state import catalog


def fixture(app_factory, count=1000):
    app = app_factory()
    rows = catalog(app, count=count, workers=2, workspaces=4)
    for queue in list(app.queues):
        app.unsubscribe(queue)
    for index in range(4):
        record = app.clients.attach(f'client-{index}')
        record.update(selectedSessionId=rows[index]['id'], selectedWorkspaceId=rows[index]['workspaceId'])
        record['drafts'][rows[index]['id']] = f'Private {index}'
    return app, rows


async def test_publication_shares_indexes_without_copying_full_catalog(app_factory, monkeypatch):
    app, rows = fixture(app_factory)
    app._projections = None  # Measure a cold publication independently of fixture setup.
    builds, projected, queues = [], [], []
    original_index, original_registry, original_project = workspace_navigation._index, chat_navigation.registry, app.clients.project
    monkeypatch.setattr(workspace_navigation, '_index', lambda state: (builds.append('workspace'), original_index(state))[1])
    monkeypatch.setattr(chat_navigation, 'registry', lambda state: (builds.append('chat'), original_registry(state))[1])
    monkeypatch.setattr(app.clients, 'project', lambda state: (projected.append(len(state['sessions'])), original_project(state))[1])
    monkeypatch.setattr(app, 'state_context', lambda: pytest.fail('Browser publication projected the full catalog'))
    for index in range(4):
        with app.clients.bind(f'client-{index}'):
            queues.append(app.subscribe())
    app._publish()
    assert builds.count('workspace') == builds.count('chat') == 1
    assert len(projected) == 4 and max(projected) < 250
    for index, queue in enumerate(queues):
        state = queue.get_nowait()
        assert state['selectedSessionId'] == rows[index]['id']
        assert next(row for row in state['sessions'] if row['id'] == rows[index]['id'])['draft'] == f'Private {index}'
        with app.clients.bind(f'client-{index}'):
            app.shell.inspect(f'client-{index}', snapshots=True)
    assert builds.count('workspace') == builds.count('chat') == 1
    rows[-3]['error'] = 'New off-page error'
    app._save()  # Saving without a revision change must still invalidate indexes.
    with app.clients.bind('client-0'):
        assert rows[-3]['id'] in app.browser_state()['attention']['sessions']
    assert builds.count('workspace') == builds.count('chat') == 2


async def test_clients_share_navigation_despite_layout_and_private_draft_differences(app_factory, monkeypatch):
    from amplifier_web import browser_state
    app, rows = fixture(app_factory)
    app._projections = None  # Measure a cold publication independently of fixture setup.
    builds, queues = [], []
    for module, name, label in ((workspace_navigation, 'snapshot', 'workspace'),
                                (chat_navigation, 'snapshot', 'chat'),
                                (browser_state, 'navigation', 'browser')):
        original = getattr(module, name)
        def observed(*args, _original=original, _label=label, **kwargs):
            builds.append(_label)
            return _original(*args, **kwargs)
        monkeypatch.setattr(module, name, observed)
    for index in range(4):
        selected = rows[index // 2]
        record = app.clients.records[f'client-{index}']
        record.update(selectedSessionId=selected['id'], selectedWorkspaceId=selected['workspaceId'])
        record['drafts'][selected['id']] = f'Only client {index}'
        record['view'].update(navExpanded=index % 2 == 0, navPinned=index % 2 == 1,
                              navWidth=250 + index * 40, panel='settings' if index % 2 else None)
        with app.clients.bind(f'client-{index}'):
            queues.append(app.subscribe())
    app._publish()
    assert builds.count('workspace') == builds.count('browser') == 2
    assert builds.count('chat') == 4  # Two sidebar pages and two header pages.
    for index, queue in enumerate(queues):
        state = queue.get_nowait()
        selected = rows[index // 2]
        assert state['selectedSessionId'] == selected['id']
        assert state['view']['navWidth'] == 250 + index * 40
        assert state['view']['draft'] == f'Only client {index}'
        assert next(row for row in state['sessions'] if row['id'] == selected['id'])['draft'] == f'Only client {index}'
        with app.clients.bind(f'client-{index}'):
            live = app.state_context()
            for key in ('chatNavigation', 'headerChatNavigation', 'subagentNavigation', 'workspaceExplorer'):
                assert state[key] == live[key]


async def test_projection_queries_share_only_unaffected_navigation(app_factory):
    from amplifier_web.state_projections import StateProjections
    app, rows = fixture(app_factory)
    projections = StateProjections()
    state = dict(app.state)
    first = projections.browser(state)
    # Selecting another chat in the same workspace cannot change the explorer.
    selected = {**state, 'selectedSessionId': rows[4]['id']}
    selection = projections.browser(selected)
    assert selection['workspaceExplorer'] is first['workspaceExplorer']
    assert selection['chatNavigation']['scope']['selectedSessionId'] == rows[4]['id']
    assert selection['subagentNavigation']['scope']['sessionId'] == rows[4]['id']
    # Each query family retains its own controls, without evicting the others.
    workspace = {**state, 'view': {**state['view'], 'navWorkspaceMode': 'recent'}}
    explorer = projections.browser(workspace)
    assert explorer['chatNavigation'] is first['chatNavigation']
    assert explorer['subagentNavigation'] is first['subagentNavigation']
    assert explorer['workspaceExplorer']['mode'] == 'recent'
    chats = {**state, 'view': {**state['view'], 'navFilter': 'Conversation 999'}}
    filtered = projections.browser(chats)
    assert filtered['workspaceExplorer'] is first['workspaceExplorer']
    assert [row['id'] for row in filtered['chatNavigation']['items']] == [rows[999]['id']]
    workers = {**state, 'view': {**state['view'], 'subagentHistory': {'sessionId': rows[4]['id']}}}
    history = projections.browser(workers)
    assert history['chatNavigation'] is first['chatNavigation']
    assert history['workspaceExplorer'] is first['workspaceExplorer']
    assert history['subagentNavigation']['scope']['sessionId'] == rows[4]['id']


async def test_cached_client_filter_page_and_selection_scopes_match_uncached_reads(app_factory):
    from amplifier_web import browser_state
    from amplifier_web.state_projections import StateProjections
    app, rows = fixture(app_factory)
    rows[8]['completion'] = {'id': 'ready', 'at': 10}
    app.state['conversationOrganization'] = {'archived': {rows[12]['id']: {}},
        'collections': [{'id': 'selected', 'name': 'Selected', 'sessionIds': [rows[4]['id'], rows[8]['id']]}]}
    projections = StateProjections()
    for index in range(4):
        with app.clients.bind(f'client-{index}'):
            selected = app.state['selectedSessionId']
            workspace = app.state['selectedWorkspaceId']
            patches = [{}, {'navExpanded': False, 'navWidth': 360, 'draft': 'Private'},
                {'navChatScope': 'workspace'}, {'navFilter': 'Conversation 99'},
                {'navStatusFilter': 'unread'}, {'navStatusFilter': 'working'},
                {'navArchive': 'archived'}, {'navCollection': 'selected'},
                {'navChatPage': {'mode': 'all', 'workspaceId': None, 'filter': '', 'selectedSessionId': selected, 'index': 2}},
                {'navWorkspaceMode': 'recent'},
                {'navWorkspaceBrowseFor': workspace, 'navWorkspaceFilter': 'project-1', 'navWorkspacePage': 2},
                {'navWorkspaceBrowseFor': workspace, 'navWorkspacePath': rows[index]['workspace']},
                {'subagentHistory': {'sessionId': rows[0]['id'], 'filter': '1001', 'index': 1}}]
            for patch in patches:
                state = {**app.state, 'view': {**app.state['view'], **patch}}
                scoped = {**state, 'attention': projections.attention(state)}
                expected = {**browser_state.navigation(scoped), 'attention': scoped['attention'],
                            'workspaceExplorer': workspace_navigation.snapshot(scoped)}
                assert projections.browser(state) == expected


async def test_device_observations_keep_agent_visibility_without_saves_or_cache_churn(app_factory, monkeypatch):
    app, _ = fixture(app_factory, count=20)
    with app.clients.bind('client-0'):
        before = app.browser_state()
        revision = app.state['revision']
        with monkeypatch.context() as patch:
            patch.setattr(app, '_save', lambda: pytest.fail('An observation saved the app'))
            for text in ('First screen', 'Current screen'):
                await app.update_device({'clientId': 'client-0', 'visibleText': text, 'controls': [{'label': text}]})
            assert app.get_state()['devices']['client-0']['visibleText'] == 'Current screen'
            assert app.browser_state() is before and app.state['revision'] == revision
            with pytest.raises(AppError, match='another client'):
                await app.update_device({'clientId': 'client-1'})


async def test_session_streams_share_duplicate_projection_and_keep_private_drafts(app_factory, monkeypatch):
    app, rows = fixture(app_factory, count=20)
    sid = rows[0]['id']
    rows[0]['messages'] = [{'id': str(i), 'role': 'assistant', 'text': str(i)} for i in range(520)]
    queues = []
    for identity in ('client-0', 'client-0', 'client-1'):
        with app.clients.bind(identity):
            queues.append(app.subscribe(session_id=sid))
    app.clients.records['client-1']['drafts'][sid] = 'Other private draft'
    monkeypatch.setattr(app, 'browser_state', lambda *a, **kw: pytest.fail('Session stream built browser navigation'))
    app._publish()
    first, duplicate, other = [queue.get_nowait() for queue in queues]
    assert first is duplicate and first is not other
    assert len(first['sessions']) == 1 and len(first['sessions'][0]['messages']) == 520
    assert first['sessions'][0]['subagentCount'] == 2
    assert first['sessions'][0]['draft'] == 'Private 0'
    assert other['sessions'][0]['draft'] == 'Other private draft'
    app.state['sessions'] = [row for row in app.state['sessions'] if row['id'] != sid]
    app._publish()
    assert all(queue.get_nowait()['sessions'] == [] for queue in queues)


async def test_workspace_preferences_do_not_evict_other_clients_or_stale_on_file_change(app_factory, monkeypatch):
    app, rows = fixture(app_factory, count=20)
    from pathlib import Path
    paths = []
    for index in range(2):
        path = Path(rows[index]['workspace']) / '.amplifier/settings.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(f'voice:\n  preferred_model: voice-{index}\n')
        paths.append(path)
    from amplifier_web import shared_settings
    original, reads = shared_settings.read_settings, []
    def settings(*args, **kwargs):
        reads.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(shared_settings, 'read_settings', settings)
    def read(identity):
        with app.clients.bind(identity):
            return app.browser_state()
    first, second = read('client-0'), read('client-1')
    assert first['settings']['preferredVoice'] == 'voice-0'
    assert second['settings']['preferredVoice'] == 'voice-1'
    assert read('client-0') is first and read('client-1') is second
    assert len(reads) == 2
    paths[0].write_text('voice:\n  preferred_model: updated-voice\n')
    assert read('client-0')['settings']['preferredVoice'] == 'updated-voice'
    assert read('client-1') is second
    assert len(reads) == 3


async def test_shell_key_tracks_offpage_navigation_not_unrelated_progress(app_factory):
    app, rows = fixture(app_factory)
    with app.clients.bind('client-0'):
        original = app.browser_state()['shellDataKey']
        rows[0]['streaming'] = 'A delta with unchanged navigation facts'
        app.state['diagnostics']['local']['sampleCount'] = 42
        app._publish()
        assert app.browser_state()['shellDataKey'] == original
        rows[900]['title'] = 'Renamed off page'
        app._publish()
        changed = app.browser_state()['shellDataKey']
        assert changed != original
        rows[900]['approvals'] = [{'id': 'permission', 'status': 'pending'}]
        app._save()
        assert app.browser_state()['shellDataKey'] != changed


async def test_cached_browser_and_uncached_agent_navigation_share_attention_filters(app_factory):
    app, rows = fixture(app_factory)
    rows[900]['completion'] = {'id': 'ready', 'at': 10}
    with app.clients.bind('client-0'):
        app.state['view']['navStatusFilter'] = 'unread'
        app._save()
        browser = app.browser_state()['chatNavigation']
        agent = app.get_state()['chatNavigation']
        assert [row['id'] for row in browser['items']] == [rows[900]['id']]
        assert browser == agent


async def test_ordinary_snapshots_repair_a_dropped_shell_invalidation(app_factory):
    app, _ = fixture(app_factory, count=20)
    with app.clients.bind('client-0'):
        queue = app.subscribe()
        first = app.browser_state()
        await app.dispatch('shell.view.update', {'clientId': 'client-0', 'instanceId': 'chats', 'patch': {'navFilter': 'changed'}})
        immediate = app.browser_state()
        assert immediate['shellChangeToken'] != first['shellChangeToken']
        assert immediate['shellDataKey'] == first['shellDataKey']
        for _ in range(4):
            app._publish()
        frames = [queue.get_nowait() for _ in range(4)]
        assert all('shellClientId' not in frame for frame in frames)
        assert all(frame['shellChangeToken'] == immediate['shellChangeToken'] for frame in frames)


def test_shared_workspace_index_preserves_duplicate_registration_selection():
    from amplifier_web.state_projections import StateProjections
    state = {'sessions': [{'id': 'a', 'workspaceId': 'one'}, {'id': 'b', 'workspaceId': 'two'}],
             'workspaces': [{'id': identity, 'path': '/work/shared', 'name': identity, 'available': True} for identity in ('one', 'two')],
             'selectedWorkspaceId': 'one', 'view': {}}
    projections = StateProjections()
    first = projections.workspaces(state)
    state['selectedWorkspaceId'] = 'two'
    second = projections.workspaces(state)
    assert first['selected']['workspaceId'] == first['rows'][0]['workspaceId'] == 'one'
    assert second['selected']['workspaceId'] == second['rows'][0]['workspaceId'] == 'two'
    assert first == workspace_navigation.snapshot({**state, 'selectedWorkspaceId': 'one'})
    assert second == workspace_navigation.snapshot(state)


async def test_shared_session_index_matches_parent_identity_rules(app_factory):
    from amplifier_web.browser_state import SessionIndex, direct_child
    app, rows = fixture(app_factory, count=20)
    parent, other = rows[:2]
    parent.update(nativeIdentity="same-native-id", nativeProject="project-a")
    other.update(nativeIdentity="same-native-id", nativeProject="project-b")
    rows.extend([
        {"id": "native-a", "nativeParentId": "same-native-id", "nativeProject": "project-a", "workspace": parent["workspace"]},
        {"id": "native-b", "nativeParentId": "same-native-id", "nativeProject": "project-b", "workspace": other["workspace"]},
        {"id": "explicit", "workspace": parent["workspace"], "parentId": parent["id"], "nativeParentId": "same-native-id", "nativeProject": "project-b"},
        {"id": "workspace-fallback", "nativeParentId": "same-native-id", "workspace": parent["workspace"]},
    ])
    app.state['sessions'] = rows
    index = SessionIndex(app.state)
    for owner in rows:
        assert index.children(owner) == [row for row in rows if direct_child(row, owner)]
    assert index.children(None) == []


async def test_shared_session_index_refreshes_messages_and_busy_offpage_rows(app_factory):
    app, rows = fixture(app_factory)
    target = rows[-3]
    for sequence in range(105):
        target.setdefault('messages', []).append({'id': str(sequence), 'role': 'assistant', 'via': 'text',
                                                'createdAt': sequence, 'text': 'Private notification ' + str(sequence)})
    target['status'] = 'working'
    app.state['notificationSettings'] = {'preview': False}
    app._save()
    with app.clients.bind('client-0'):
        first = app.browser_state()
        assert len(first['notificationMessages']) == 100
        assert first['notificationMessages'][0]['id'] == '5'
        assert all('text' not in item for item in first['notificationMessages'])
        assert target['id'] in {item['id'] for item in first['sessions']}
    target['messages'].append({'id': 'latest', 'role': 'assistant', 'via': 'text', 'createdAt': 1000, 'text': 'Latest'})
    target['status'] = 'idle'
    app.state['notificationSettings']['preview'] = True
    app._save()
    with app.clients.bind('client-0'):
        latest = app.browser_state()
        assert latest['notificationMessages'][-1]['text'] == 'Latest'
        assert target['id'] not in app.projections.sessions(app.state).active


async def test_progress_reuses_navigation_but_refreshes_offpage_and_replaced_rows(app_factory, monkeypatch):
    app, rows = fixture(app_factory)
    builds = []
    original = chat_navigation.catalog
    def counted(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(chat_navigation, 'catalog', counted)
    with app.clients.bind('client-0'):
        first = app.browser_state()
        builds.clear()
        for index in range(3):
            rows[0]['streaming'] = f'Progress {index}'
            app.state['diagnostics']['local']['sampleCount'] = index
            app._publish()
            current = app.browser_state()
            assert current['chatNavigation'] == first['chatNavigation']
            assert current['revision'] > first['revision']
        assert builds == []
        # A same-valued replacement must not make later invalidation depend on
        # old source-object identity. The new off-page row still changes the UI.
        replacement = dict(rows[900])
        app.state['sessions'][900] = replacement
        app._save()
        app.browser_state()
        assert builds == []
        replacement.update(title='Changed off page', status='working')
        app._save()  # No revision change: still must invalidate all queries.
        app.state['view']['navFilter'] = 'Changed off page'
        changed = app.browser_state()
        assert [row['title'] for row in changed['chatNavigation']['items']] == ['Changed off page']
        assert changed['chatNavigation']['activityCounts']['working'] == 1
        assert builds


async def test_published_snapshot_reuse_is_detached_and_client_private(app_factory):
    from copy import deepcopy
    app, rows = fixture(app_factory, count=20)
    app.state['fixtureCatalog'] = {'items': [{'id': 'shared', 'nested': {'values': [1, 2]}}]}
    queues = []
    for index in range(2):
        with app.clients.bind(f'client-{index}'):
            queues.append(app.subscribe())
    app._publish()
    first, other = [queue.get_nowait() for queue in queues]
    original = deepcopy(first)
    assert first['fixtureCatalog'] is not other['fixtureCatalog']
    rows[0]['streaming'] = 'New progress'
    app._publish()
    second, _ = [queue.get_nowait() for queue in queues]
    assert second['fixtureCatalog'] is first['fixtureCatalog']
    assert second['sessions'] is not first['sessions']
    assert second['revision'] > first['revision']
    assert first == original
    # Nested in-place edits must detach the entire changed section, including
    # when a save does not bump revision and when two clients show this data.
    app.state['fixtureCatalog']['items'][0]['nested']['values'].append(3)
    app.clients.records['client-0']['drafts'][rows[0]['id']] = 'Updated private draft'
    app._save()
    with app.clients.bind('client-0'):
        current = app.browser_state()
    with app.clients.bind('client-1'):
        other_current = app.browser_state()
    assert current['fixtureCatalog']['items'][0]['nested']['values'] == [1, 2, 3]
    assert current['fixtureCatalog'] is not first['fixtureCatalog']
    assert first == original
    assert current['fixtureCatalog'] is not other_current['fixtureCatalog']
    assert next(row for row in current['sessions'] if row['id'] == rows[0]['id'])['draft'] == 'Updated private draft'
    assert all(row.get('draft') != 'Updated private draft' for row in other_current['sessions'])


def test_snapshot_copy_cache_keeps_only_recent_client_baselines():
    from amplifier_web.browser_state import SnapshotCopies
    cache = SnapshotCopies(limit=2)
    source = {'catalog': {'nested': [1]}}
    first = cache.detach(source, 'first')
    second = cache.detach(source, 'second')
    assert first['catalog'] is not second['catalog']
    assert cache.detach(source, 'first')['catalog'] is first['catalog']
    cache.detach(source, 'third')
    assert list(cache.frames) == ['first', 'third']
    assert cache.detach(source, 'second')['catalog'] is not second['catalog']
    source['catalog']['nested'].append(2)
    assert first['catalog']['nested'] == second['catalog']['nested'] == [1]
