"""Browsing changes client presentation, never a running chat's execution scope."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError
from test_workspace_experience import app


async def make_work(app, tmp_path):
    paths = [tmp_path / name for name in ('planning', 'engineering')]
    ids = []
    for path in paths:
        path.mkdir()
        await app.dispatch('workspace.add', {'path': str(path)})
        await app.dispatch('session.create', {'workspace': str(path), 'title': path.name})
        ids.append((app.state['selectedWorkspaceId'], app.state['selectedSessionId']))
    return paths, ids


@pytest.mark.parametrize('origin', ['ui', 'agent'])
async def test_workspace_browse_preserves_chat_draft_canvas_and_other_client(app, tmp_path, origin):
    paths, ids = await make_work(app, tmp_path)
    app.clients.attach('reader'); app.clients.attach('other')
    with app.clients.bind('reader'):
        await app.dispatch('session.select', {'id': ids[0][1]})
        await app.dispatch('view.update', {'patch': {'draft': 'Do not retarget this message'}})
        before = deepcopy({key: app.state[key] for key in ('selectedSessionId','selectedWorkspaceId','canvas','sessions')})
        await app.dispatch('view.update', {'patch': {'workSurface': 'workspace', 'workWorkspaceId': ids[1][0]}}, origin=origin)
        query = app.shell.inspect('reader', snapshots=True)['snapshots']['chats']
        assert query['selectedWorkspaceId'] == ids[1][0]
        assert any(row['id'] == ids[1][0] for row in app.browser_state()['workspaces'])
        assert [row['id'] for row in query['sidebarNavigation']['workspace']['items']] == [ids[1][1]]
        assert all(app.state[key] == value for key, value in before.items())
        assert app.state['view']['draft'] == 'Do not retarget this message'
        await app.dispatch('session.select', {'id': ids[0][1]})
        assert app.state['view']['workSurface'] == 'chat'
        assert app.state['view']['draft'] == 'Do not retarget this message'
        await app.dispatch('view.update', {'patch': {'workSurface': 'workspaces'}})
        await app.dispatch('session.draft', {'location': {'kind': 'managed'}})
        assert app.state['view']['workSurface'] == 'chat'
    with app.clients.bind('other'):
        assert app.state['view'].get('workSurface', 'chat') == 'chat'
        assert app.state['selectedSessionId'] == ids[1][1]


@pytest.mark.parametrize('patch', [{'workSurface':'unknown'}, {'workWorkspaceId':False}, {'workWorkspaceId':'missing'}, {'workWorkspaceTab':'invalid'}])
async def test_browse_rejects_bad_targets_without_mutating_scope(app, patch):
    before=deepcopy(app.state['view'])
    with pytest.raises(AppError):
        await app.dispatch('view.update', {'patch':patch})
    assert app.state['view'] == before


async def test_row_draft_targets_other_workspace_without_creating_or_retargeting_work(app, tmp_path):
    paths, ids = await make_work(app, tmp_path)
    app.clients.attach('reader'); app.clients.attach('other')
    # Commit ordinary first-save normalization before the baseline, strictly
    # before every reader action below. Keep the full record, including Canvas.
    app._save()
    persisted_other = app.db.execute("SELECT value FROM client_views WHERE id='other'").fetchone()[0]
    with app.clients.bind('other'):
        other = deepcopy(app.clients.record())
    with app.clients.bind('reader'):
        await app.dispatch('session.draft', {'workspace': str(paths[0])})
        setup = {'workspace': str(paths[0]), 'location': {'kind': 'workspace'}, 'bundle': 'work',
                 'selection': {'instance': 'test-provider', 'model': 'chosen-model', 'effort': 'high'}}
        await app.dispatch('view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Keep unsent text'}})
        await app.dispatch('attachment.add', {'name': 'unsent.txt', 'base64': 'a2VlcA=='})
        await app.dispatch('session.select', {'id': ids[0][1]})
        await app.dispatch('view.update', {'patch': {'draft': 'Keep original text'}})
        await app.dispatch('attachment.add', {'sessionId': ids[0][1], 'name': 'draft.txt', 'base64': 'a2VlcA=='})
        await app.dispatch('canvas.show', {'kind': 'text', 'title': 'Original Canvas', 'content': 'Keep Canvas'})
        canvas_id = app.state['canvas']['id']
        sessions = deepcopy(app.state['sessions'])
        await app.dispatch('view.update', {'patch': {'workSurface': 'workspace', 'workWorkspaceId': ids[0][0]}})
        await app.dispatch('session.draft', {'workspace': str(paths[1]), 'workspaceId': ids[1][0], 'location': {'kind': 'workspace'}})
        assert app.state['selectedSessionId'] is None
        assert app.state['view']['newSessionDraft']['workspace'] == str(paths[1])
        assert app.state['view']['newSessionDraft']['selection'] == setup['selection']
        assert app.state['view']['newSessionDraft']['bundle'] == 'work'
        assert app.state['view']['draft'] == 'Keep unsent text'
        assert app.clients.record()['attachments'][''][0]['name'] == 'unsent.txt'
        assert app.state['view']['workSurface'] == 'chat'
        assert app.state['sessions'] == sessions
        assert app.state['selectedWorkspaceId'] == ids[0][0]
        await app.dispatch('session.select', {'id': ids[0][1]})
        assert app.state['view']['draft'] == 'Keep original text'
        assert app.state['canvas']['id'] == canvas_id
        assert app.state['canvas']['content'] == 'Keep Canvas'
        assert app.clients.record()['attachments'][ids[0][1]][0]['name'] == 'draft.txt'
    with app.clients.bind('other'):
        assert app.clients.record() == other
    assert app.db.execute("SELECT value FROM client_views WHERE id='other'").fetchone()[0] == persisted_other


@pytest.mark.parametrize('failure', ['unknown', 'mismatch', 'unavailable', 'removed-folder'])
async def test_row_draft_rejects_stale_destination_before_changing_view(app, tmp_path, failure):
    paths, ids = await make_work(app, tmp_path)
    args = {'workspace': str(paths[0]), 'workspaceId': ids[0][0]}
    if failure == 'unknown':
        args['workspaceId'] = 'unknown'
    elif failure == 'mismatch':
        args['workspace'] = str(paths[1])
    elif failure == 'unavailable':
        next(row for row in app.state['workspaces'] if row['id'] == ids[0][0])['available'] = False
    else:
        paths[0].rmdir()
    before = deepcopy(app.state['view'])
    selected = app.state['selectedSessionId']
    with pytest.raises(AppError, match='workspace folder is unavailable'):
        await app.dispatch('session.draft', args)
    assert app.state['view'] == before
    assert app.state['selectedSessionId'] == selected


async def test_previews_are_bounded_and_independent_of_full_browser_filters(app, tmp_path):
    paths, ids = await make_work(app, tmp_path)
    exemplar=deepcopy(app.state['sessions'][0])
    app.state['sessions']=[dict(exemplar,id=f'chat-{i}',title=f'Chat {i}',recentActivityAt=i,createdAt=i) for i in range(70)]
    app.state['pinnedSessionIds']=['chat-0']
    app.state['selectedSessionId']=None
    app.clients.attach('reader')
    with app.clients.bind('reader'):
        await app.dispatch('view.update', {'patch': {'workSurface':'chats'}})
        await app.dispatch('shell.view.update', {'clientId':'reader','instanceId':'chats','patch':{'navRecentView':{'navFilter':'Chat 1'}}})
        snapshot=app.shell.inspect('reader',snapshots=True)['snapshots']['chats']
        assert len(snapshot['recentShortcuts']) == 20
        assert snapshot['recentShortcuts'][0]['id'] == 'chat-69'
        assert all(row['id']!='chat-0' for row in snapshot['recentShortcuts'])
        assert len(snapshot['workspaceShortcuts']) <= 6
        assert snapshot['sidebarNavigation']['recent']['total'] == 11
        assert [row['id'] for row in snapshot['sidebarNavigation']['pinned']['items']] == ['chat-0']
        await app.dispatch('shell.view.update', {'clientId':'reader','instanceId':'chats','patch':{'navRecentView':{'navFilter':''}}})
        page=app.shell.inspect('reader',snapshots=True)['snapshots']['chats']['sidebarNavigation']['recent']
        assert page['total']==69 and len(page['items'])==40 and page['pages']==2


async def test_quiet_recent_preferences_survive_two_clients_two_modules_and_reload(app, tmp_path):
    from amplifier_web.shell_modules import ShellModules
    from test_service import Runtime
    app.runtime = Runtime()
    paths, ids = await make_work(app, tmp_path)
    exemplar = deepcopy(app.state['sessions'][0])
    app.state['sessions'] = [dict(exemplar, id=f'quiet-{i}', title=f'Quiet {i}',
                                  recentActivityAt=140-i, navigationActivityAt=140-i) for i in range(140)]
    app.state['sessions'][0]['collaboration'] = {
        'creatorSessionId': 'fixture-creator', 'requestId': 'fixture-request', 'brief': 'Private fixture'}
    app.state['selectedSessionId'] = 'quiet-139'
    app._publish()
    app.clients.attach('quiet-a'); app.clients.attach('quiet-b')
    async def query(client, instance='chats'):
        with app.clients.bind(client):
            return (await app.dispatch('shell.query', {'clientId': client, 'instanceId': instance}))['result']
    with app.clients.bind('quiet-a'):
        await app.dispatch('view.update', {'patch': {'draft': 'Keep text'}})
        await app.dispatch('attachment.add', {'sessionId': 'quiet-139', 'name': 'keep.txt', 'base64': 'a2VlcA=='})
        await app.dispatch('canvas.show', {'kind': 'text', 'title': 'Kept Canvas', 'content': 'Keep Canvas'})
        before = deepcopy(app.clients.record())
        inspected = (await app.dispatch('shell.inspect', {'clientId': 'quiet-a'}))['result']
        composition = deepcopy(inspected['composition'])
        composition['instances'].append({'id': 'quiet-second', 'package': 'builtin.chats', 'slot': 'navigation'})
        change = (await app.dispatch('shell.changes.prepare', {'clientId': 'quiet-a',
            'expectedRevision': inspected['revision'], 'composition': composition}))['result']
        await app.dispatch('shell.changes.apply', {'clientId': 'quiet-a',
            'expectedRevision': inspected['revision'], 'changeId': change['id']})
        for limit in (20, 40, 60, 80, 100, 120, 140):
            await app.dispatch('shell.view.update', {'clientId': 'quiet-a', 'instanceId': 'chats',
                                                   'patch': {'navRecentLimit': limit}})
            page = (await query('quiet-a'))['recentNavigation']
            assert (len(page['items']), page['total'], page['remaining']) == (min(limit, 139), 139, max(0, 139-limit))
            assert page['scope']['limit'] == limit
        await app.dispatch('shell.view.update', {'clientId': 'quiet-a', 'instanceId': 'chats',
                                               'patch': {'navShowAgentCreated': True}})
        current = await query('quiet-a')
        assert current['recentNavigation']['limit'] == 140
        assert current['recentNavigation']['total'] == 140
        assert current['recentShortcuts'][0]['id'] == 'quiet-0'
        assert app.clients.record() == before
        assert app.shell.get('client', 'quiet-a')['views']['chats']['view']['navRecentLimit'] == 100
        # Reads/reconciliation in the same document retain the expanded prefix.
        assert (await query('quiet-a'))['recentNavigation']['limit'] == 140
    assert (await query('quiet-a', 'quiet-second'))['recentNavigation']['limit'] == 20
    assert (await query('quiet-b'))['recentNavigation']['limit'] == 20
    assert (await query('quiet-b'))['recentNavigation']['total'] == 139
    app.clients.attach('quiet-reload', resume='quiet-a')
    # Reconstruct from durable shell records, not the module's in-memory client.
    app.shell = ShellModules(app)
    restored = await query('quiet-reload')
    assert restored['recentNavigation']['limit'] == 100
    assert restored['view']['navShowAgentCreated'] is True
    assert restored['recentNavigation']['scope']['viewRevision'] == current['recentNavigation']['scope']['viewRevision']
    assert (await query('quiet-reload', 'quiet-second'))['recentNavigation']['limit'] == 20
    assert not app.runtime.started and not app.runtime.sent


async def test_recent_scope_and_count_bound_shrink_without_changing_other_views(app, tmp_path):
    paths, ids = await make_work(app, tmp_path)
    exemplar = deepcopy(app.state['sessions'][0])
    app.state['sessions'] = [dict(exemplar, id=f'prefix-{i}', title=f'Prefix {i}',
                                  recentActivityAt=200-i) for i in range(101)]
    app.state['selectedSessionId'] = None
    app._publish()
    app.clients.attach('prefix-a'); app.clients.attach('prefix-b')
    async def query(client):
        with app.clients.bind(client):
            return (await app.dispatch('shell.query', {'clientId': client, 'instanceId': 'chats'}))['result']
    other = deepcopy(app.shell.client('prefix-b'))
    with app.clients.bind('prefix-a'):
        for limit in (40, 60, 80, 100):
            await app.dispatch('shell.view.update', {'clientId': 'prefix-a', 'instanceId': 'chats',
                                                   'patch': {'navRecentLimit': limit}})
        app.state['sessions'] = app.state['sessions'][:6]
        app._publish()
        # A next-page write may arrive after a catalog shrink. Keep its requested
        # step, not an invalid 6/101 limit, while rows are bounded by live count.
        await app.dispatch('shell.view.update', {'clientId': 'prefix-a', 'instanceId': 'chats',
                                               'patch': {'navRecentLimit': 120}})
        recent = (await query('prefix-a'))['recentNavigation']
        assert (recent['limit'], recent['end'], recent['remaining']) == (120, 6, 0)
        assert recent['scope']['clientId'] == 'prefix-a'
        assert recent['scope']['instanceId'] == 'chats'
        before = deepcopy(app.shell.client('prefix-a'))
        with pytest.raises(AppError):
            await app.dispatch('shell.view.update', {'clientId': 'prefix-a', 'instanceId': 'chats',
                                                   'patch': {'navRecentLimit': 10000}})
        assert app.shell.client('prefix-a') == before
        await app.dispatch('shell.view.update', {'clientId': 'prefix-a', 'instanceId': 'chats',
                                               'patch': {'navFilter': 'nothing', 'navSort': 'name'}})
        assert (await query('prefix-a'))['recentNavigation']['end'] == 6
    assert app.shell.client('prefix-b') == other
    app.clients.attach('prefix-reload', resume='prefix-a')
    assert (await query('prefix-reload'))['recentNavigation']['limit'] == 100
    assert (await query('prefix-a'))['recentNavigation']['limit'] == 120


async def recent_catalog(app, tmp_path):
    paths, ids = await make_work(app, tmp_path)
    exemplar = deepcopy(app.state['sessions'][0])
    app.state['sessions'] = [dict(exemplar, id=f'transient-{i}', title=f'Transient {i}',
                                  recentActivityAt=200-i) for i in range(160)]
    app.state['selectedSessionId'] = None
    app._publish()
    return paths, ids


async def recent_command(app, client, action, **args):
    with app.clients.bind(client):
        return await app.dispatch(action, {'clientId': client, **args})


async def recent_query(app, client, instance='chats'):
    return (await recent_command(app, client, 'shell.query', instanceId=instance))['result']['recentNavigation']


async def test_transient_expansion_copies_do_not_retire_live_source_or_reconnect(app, tmp_path):
    await recent_catalog(app, tmp_path)
    for client, limit in [('live-a', 160), ('live-b', 120), ('live-c', 140)]:
        app.clients.attach(client)
        await recent_command(app, client, 'shell.view.update', instanceId='chats', patch={'navRecentLimit': limit})
        assert (await recent_query(app, client))['limit'] == limit
    original = deepcopy(app.shell.recent_limits)
    assert sorted(original.values()) == [120, 140, 160]
    for copied in ('copy-b', 'copy-c'):
        app.clients.attach(copied, resume='live-a')
        assert (await recent_query(app, copied))['limit'] == 100
        assert (await recent_query(app, 'live-a'))['limit'] == 160
        assert app.shell.recent_limits == original
    with app.clients.bind('live-a'):
        queue = app.subscribe()
        app.unsubscribe(queue)
    app.clients.attach('live-a', resume='live-b')
    assert (await recent_query(app, 'live-a'))['limit'] == 160
    assert app.shell.recent_limits == original
    # Durable records contain only the initial <=100 preference, never the cache.
    import json
    for client, payload in app.db.execute("SELECT id,value FROM shell_records WHERE kind='client'"):
        raw = json.loads(payload)
        assert 'recent_limits' not in raw
        assert all(view.get('view', {}).get('navRecentLimit', 20) <= 100 for view in raw['views'].values())
    await recent_command(app, 'live-a', 'shell.view.update', instanceId='chats', patch={'navRecentLimit': 80})
    assert (await recent_query(app, 'live-a'))['limit'] == 80
    assert all(key[0] != 'live-a' for key in app.shell.recent_limits)
    assert sorted(app.shell.recent_limits.values()) == [120, 140]
    await recent_command(app, 'live-a', 'shell.view.update', instanceId='chats', patch={'navRecentLimit': 100})
    assert all(key[0] != 'live-a' for key in app.shell.recent_limits)


@pytest.mark.parametrize('operation', ['preview', 'apply', 'revert', 'recover-default', 'recover-lastGood', 'removal', 'package'])
async def test_successful_composition_retires_only_superseded_module_keys(app, tmp_path, operation):
    await recent_catalog(app, tmp_path)
    for identity in ('owner', 'other-live'):
        app.clients.attach(identity)
    inspected = app.shell.inspect('owner')
    composition = deepcopy(inspected['composition'])
    composition['instances'].append({'id': 'unchanged', 'package': 'builtin.chats', 'slot': 'navigation'})
    prepared = await recent_command(app, 'owner', 'shell.changes.prepare',
                                    expectedRevision=inspected['revision'], composition=composition)
    await recent_command(app, 'owner', 'shell.changes.apply',
                         expectedRevision=inspected['revision'], changeId=prepared['result']['id'])
    for identity, instance, limit in [('owner', 'chats', 160), ('owner', 'unchanged', 120), ('other-live', 'chats', 140)]:
        await recent_command(app, identity, 'shell.view.update', instanceId=instance, patch={'navRecentLimit': limit})
    other_keys = {key: value for key, value in app.shell.recent_limits.items() if key[0] == 'other-live'}
    original_key = next(key for key in app.shell.recent_limits if key[:2] == ('owner', 'chats'))
    unchanged_key = next(key for key in app.shell.recent_limits if key[:2] == ('owner', 'unchanged'))
    current = app.shell.client('owner')
    if operation.startswith('recover'):
        receipt = await recent_command(app, 'owner', 'shell.recover', expectedRevision=current['revision'],
                                       target='lastGood' if operation.endswith('lastGood') else 'default')
    else:
        target = deepcopy(current['composition'])
        chats = next(row for row in target['instances'] if row['id'] == 'chats')
        if operation == 'removal':
            target['instances'].remove(chats)
        elif operation == 'package':
            chats['package'] = 'builtin.workspaces'
        else:
            chats['scope'] = {'mode': 'all'}
        change = await recent_command(app, 'owner', 'shell.changes.prepare',
                                      expectedRevision=current['revision'], composition=target)
        assert original_key in app.shell.recent_limits, 'prepare is not a successful transition'
        receipt = await recent_command(app, 'owner', 'shell.changes.' + ('preview' if operation in {'preview', 'revert'} else 'apply'),
                                       expectedRevision=current['revision'], changeId=change['result']['id'])
        if operation == 'revert':
            assert original_key not in app.shell.recent_limits
            await recent_command(app, 'owner', 'shell.view.update', instanceId='chats', patch={'navRecentLimit': 160})
            preview_key = next(key for key in app.shell.recent_limits if key[:2] == ('owner', 'chats'))
            receipt = await recent_command(app, 'owner', 'shell.changes.revert',
                                           expectedRevision=app.shell.client('owner')['revision'], changeId=change['result']['id'])
            assert preview_key not in app.shell.recent_limits
    assert receipt['accepted'] is True
    assert original_key not in app.shell.recent_limits
    assert {key: value for key, value in app.shell.recent_limits.items() if key[0] == 'other-live'} == other_keys
    if operation.startswith('recover'):
        assert unchanged_key not in app.shell.recent_limits
    else:
        assert app.shell.recent_limits[unchanged_key] == 120
        assert (await recent_query(app, 'owner', 'unchanged'))['limit'] == 120
    if operation != 'removal':
        assert (await recent_query(app, 'owner'))['limit'] == 100
        await recent_command(app, 'owner', 'shell.view.update', instanceId='chats', patch={'navRecentLimit': 160})
        assert sum(key[:2] == ('owner', 'chats') for key in app.shell.recent_limits) == 1
    # Unchanged appearance/reorder transitions keep the current module ownership.
    kept = deepcopy(app.shell.recent_limits)
    current = app.shell.client('owner')
    target = deepcopy(current['preview']['composition'] if current['preview'] else current['composition'])
    target['presentation']['density'] = 'compact'
    change = await recent_command(app, 'owner', 'shell.changes.prepare',
                                  expectedRevision=current['revision'], composition=target)
    await recent_command(app, 'owner', 'shell.changes.apply',
                         expectedRevision=current['revision'], changeId=change['result']['id'])
    assert app.shell.recent_limits == kept


async def test_rejected_or_deferred_composition_keeps_expanded_prefix_keys(app, tmp_path):
    await recent_catalog(app, tmp_path)
    app.clients.attach('owner')
    await recent_command(app, 'owner', 'shell.view.update', instanceId='chats', patch={'navRecentLimit': 160}, dirty=True)
    prior = deepcopy(app.shell.recent_limits)
    current = app.shell.client('owner')
    target = deepcopy(current['composition'])
    target['instances'] = [row for row in target['instances'] if row['id'] != 'chats']
    change = await recent_command(app, 'owner', 'shell.changes.prepare', expectedRevision=current['revision'], composition=target)
    assert app.shell.recent_limits == prior
    for action in ('shell.changes.preview', 'shell.changes.apply'):
        receipt = await recent_command(app, 'owner', action, expectedRevision=current['revision'], changeId=change['result']['id'])
        assert receipt['accepted'] is False and receipt['result']['status'] == 'deferred'
        assert app.shell.recent_limits == prior
    with pytest.raises(AppError):
        await recent_command(app, 'owner', 'shell.changes.apply', expectedRevision=current['revision'] + 1, changeId=change['result']['id'])
    with pytest.raises(AppError):
        await recent_command(app, 'owner', 'shell.view.update', instanceId='chats', patch={'navRecentLimit': 10000})
    assert app.shell.recent_limits == prior
    assert (await recent_query(app, 'owner'))['limit'] == 160


@pytest.mark.parametrize('registration', ['missing', 'unavailable'])
async def test_missing_pinned_workspace_has_canonical_null_recent_scope_and_restores(app, tmp_path, registration):
    paths, ids = await make_work(app, tmp_path)
    app.clients.attach('pinned-reader')
    current = app.shell.client('pinned-reader')
    composition = deepcopy(current['composition'])
    target = next(row for row in composition['instances'] if row['id'] == 'chats')
    target['scope'] = {'mode': 'pinned', 'workspaceId': ids[0][0]}
    change = await recent_command(app, 'pinned-reader', 'shell.changes.prepare', expectedRevision=current['revision'], composition=composition)
    await recent_command(app, 'pinned-reader', 'shell.changes.apply', expectedRevision=current['revision'], changeId=change['result']['id'])
    registered = deepcopy(next(row for row in app.state['workspaces'] if row['id'] == ids[0][0]))
    if registration == 'missing':
        app.state['workspaces'] = [row for row in app.state['workspaces'] if row['id'] != ids[0][0]]
    else:
        next(row for row in app.state['workspaces'] if row['id'] == ids[0][0])['available'] = False
    app._publish()
    await recent_command(app, 'pinned-reader', 'shell.view.update', instanceId='chats', patch={'navRecentLimit': 40})
    with app.clients.bind('pinned-reader'):
        snapshot = (await app.dispatch('shell.query', {'clientId': 'pinned-reader', 'instanceId': 'chats'}))['result']
    assert snapshot['selectedWorkspaceId'] == ids[0][0]
    assert snapshot['recentScope']['workspaceId'] is None
    assert snapshot['recentNavigation']['scope']['workspaceId'] is None
    assert snapshot['recentNavigation']['items'] == []
    app.state['workspaces'] = [row for row in app.state['workspaces'] if row['id'] != ids[0][0]] + [registered]
    app._publish()
    restored = await recent_query(app, 'pinned-reader')
    assert restored['scope']['workspaceId'] == ids[0][0]
    assert [row['id'] for row in restored['items']] == [ids[0][1]]


async def test_workspace_pin_shell_actions_are_passive_and_preserve_other_clients_and_chat_pages(app, tmp_path, monkeypatch):
    paths, ids = await make_work(app, tmp_path)
    empty = tmp_path / 'empty'; empty.mkdir()
    await app.dispatch('workspace.add', {'path': str(empty)})
    empty_id = app.state['selectedWorkspaceId']
    await app.dispatch('session.pin', {'id': ids[0][1], 'pinned': True})
    app.clients.attach('reader'); app.clients.attach('other')
    with app.clients.bind('other'):
        await app.dispatch('view.update', {'patch': {'workSurface': 'workspace', 'workWorkspaceId': ids[1][0]}})
        other = deepcopy(app.clients.record())
        other_shell = deepcopy(app.shell.client('other'))
    with app.clients.bind('reader'):
        await app.dispatch('session.select', {'id': ids[0][1]})
        await app.dispatch('view.update', {'patch': {'draft': 'Do not change', 'workSurface': 'workspace', 'workWorkspaceId': ids[1][0]}})
        await app.dispatch('shell.view.update', {'clientId': 'reader', 'instanceId': 'chats',
                                              'patch': {'navRecentView': {'navFilter': 'engineering'}}})
        before = deepcopy({key: app.state[key] for key in ('sessions', 'selectedSessionId', 'selectedWorkspaceId', 'view', 'canvas', 'pinnedSessionIds')})
        before_pages = deepcopy(app.shell.inspect('reader', snapshots=True)['snapshots']['chats']['sidebarNavigation'])
        def forbidden(*_args, **_kwargs):
            raise AssertionError('Pin actions must not rewrite sessions, scan folders or flush pending progress')
        with monkeypatch.context() as patch:
            import amplifier_web.session_projection as session_projection
            import amplifier_web.workspace_canvas as workspace_canvas
            patch.setattr(session_projection, 'persist', forbidden)
            patch.setattr(workspace_canvas, 'refresh_workspace_availability', forbidden)
            patch.setattr(app, '_commit_pending_progress', forbidden)
            await app.dispatch('shell.command', {'clientId': 'reader', 'instanceId': 'workspaces',
                                                'action': 'workspace.pin', 'args': {'id': empty_id, 'pinned': True}})
            await app.dispatch('shell.command', {'clientId': 'reader', 'instanceId': 'chats',
                                                'action': 'workspace.pin', 'args': {'id': ids[0][0], 'pinned': True}})
            await app.dispatch('shell.command', {'clientId': 'reader', 'instanceId': 'workspaces',
                                                'action': 'workspace.pinOrder', 'args': {'ids': [ids[0][0], empty_id]}})
        assert all(app.state[key] == value for key, value in before.items())
        query = app.shell.inspect('reader', snapshots=True)['snapshots']['chats']
        assert query['pinnedWorkspaceIds'] == [ids[0][0], empty_id]
        assert [row['workspaceId'] for row in query['workspaceShortcuts'][:2]] == [ids[0][0], empty_id]
        assert query['workspaceShortcuts'][1]['chatCount'] == 0
        assert query['sidebarNavigation'] == before_pages
        workspace_query = app.shell.inspect('reader', snapshots=True)['snapshots']['workspaces']
        assert workspace_query['pinnedWorkspaceIds'] == query['pinnedWorkspaceIds']
        assert workspace_query['workspaceShortcuts'][:2] == query['workspaceShortcuts'][:2]
    with app.clients.bind('other'):
        assert app.clients.record() == other
        assert app.shell.client('other') == other_shell
        assert app.state['pinnedWorkspaceIds'] == [ids[0][0], empty_id]


async def test_workspace_pin_order_preserves_real_pending_progress_and_warm_clients(app, tmp_path):
    from amplifier_web.state_records import load
    from amplifier_web.session_projection import hydrate
    paths, ids = await make_work(app, tmp_path)
    app.clients.attach('reader'); app.clients.attach('other')
    for client in ('reader', 'other'):
        with app.clients.bind(client):
            app.shell.inspect(client, snapshots=True)
            app.browser_state()
    app._save()
    # The ordinary setup save remembers per-chat Canvas presentation. Capture
    # complete memory and durable baselines only after that normalization, but
    # strictly before the runtime delta or any pin action.
    before_clients = {}
    before_durable_clients = {}
    for client in ('reader', 'other'):
        with app.clients.bind(client):
            app.shell.inspect(client, snapshots=True)
            app.browser_state()
            before_clients[client] = deepcopy(app.clients.record())
        before_durable_clients[client] = app.db.execute(
            "SELECT value FROM client_views WHERE id=?", (client,)
        ).fetchone()[0]
    sid = ids[0][1]
    await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'Retained pending stream'})
    assert app._progress_dirty
    pending = app._progress_publish_task
    sessions = deepcopy(app._state['sessions'])
    with app.clients.bind('reader'):
        await app.dispatch('workspace.pin', {'id': ids[0][0], 'pinned': True},
                           command_id='progress-pin-a', include_state=False)
        await app.dispatch('workspace.pin', {'id': ids[1][0], 'pinned': True},
                           command_id='progress-pin-b', include_state=False)
        await app.dispatch('workspace.pinOrder', {'ids': [ids[1][0], ids[0][0]]},
                           command_id='progress-pin-order', include_state=False)
    assert app._progress_dirty and app._progress_publish_task is pending
    assert app._state['sessions'] == sessions
    for client in ('reader', 'other'):
        with app.clients.bind(client):
            query = app.shell.inspect(client, snapshots=True)['snapshots']['workspaces']
            assert query['pinnedWorkspaceIds'] == [ids[1][0], ids[0][0]]
            assert [row['workspaceId'] for row in query['workspaceShortcuts'][:2]] == [ids[1][0], ids[0][0]]
            assert app.clients.record() == before_clients[client]
        assert app.db.execute(
            "SELECT value FROM client_views WHERE id=?", (client,)
        ).fetchone()[0] == before_durable_clients[client]
    await app._flush_pending_progress()
    saved = load(app.db)
    assert saved['pinnedWorkspaceIds'] == [ids[1][0], ids[0][0]]
    # The committed session record owns an immutable SQLite payload reference,
    # not inline display text. Require that reference before restoring it;
    # never fall back to a mutable view file or the live session.
    reference = next(row for row in saved['sessions'] if row['id'] == sid)
    assert reference.get('$viewPayload'), 'Require the committed SQLite payload'
    hydrate(app.data_dir, saved, app.db)
    assert next(row for row in saved['sessions'] if row['id'] == sid)['streaming'] == 'Retained pending stream'
    for client in ('reader', 'other'):
        with app.clients.bind(client):
            assert app.clients.record() == before_clients[client]
        assert app.db.execute(
            "SELECT value FROM client_views WHERE id=?", (client,)
        ).fetchone()[0] == before_durable_clients[client]


@pytest.mark.parametrize('stored_kind', ['list', 'null', 'string', 'object'])
async def test_shell_workspace_pin_vector_is_normalized_without_mutating_preferences(
        app, tmp_path, stored_kind):
    paths, ids = await make_work(app, tmp_path)
    unavailable = next(row for row in app.state['workspaces'] if row['id'] == ids[1][0])
    paths[1].rmdir()
    unavailable['available'] = False
    # More pins than shortcut slots: the query must return the complete vector,
    # not the bounded visible preview or just currently available registrations.
    extras = []
    for number in range(7):
        identity = f'unavailable-registration-{number}'
        app.state['workspaces'].append({'id': identity, 'name': f'Unavailable {number}',
                                       'path': None, 'available': False})
        extras.append(identity)
    normalized = [ids[1][0], ids[0][0], *extras]
    stored = {
        'list': [ids[1][0], 'unregistered-pin', ids[0][0], ids[1][0], None,
                 {'invalid': []}, 17, *extras, extras[0]],
        'null': None,
        'string': ids[1][0],
        'object': {'id': ids[1][0]},
    }[stored_kind]
    expected = normalized if stored_kind == 'list' else []
    app.state['pinnedWorkspaceIds'] = deepcopy(stored)
    app._save()
    app.clients.attach('reader')
    with app.clients.bind('reader'):
        persisted = app.db.execute(
            "SELECT value FROM state_records WHERE kind='global' AND id='pinnedWorkspaceIds'"
        ).fetchone()[0]
        # A browser command includes the ordinary Canvas-view projection.
        # Initialize it before the query baseline, without normalizing pins.
        app.browser_state()
        app._save()
        assert app.state['pinnedWorkspaceIds'] == stored
        assert app.db.execute(
            "SELECT value FROM state_records WHERE kind='global' AND id='pinnedWorkspaceIds'"
        ).fetchone()[0] == persisted
        before = deepcopy(app.clients.record())
        persisted_client = app.db.execute(
            "SELECT value FROM client_views WHERE id='reader'"
        ).fetchone()[0]
        snapshots = {}
        for instance in ('workspaces', 'chats'):
            snapshots[instance] = (await app.dispatch(
                'shell.query', {'clientId': 'reader', 'instanceId': instance}
            ))['result']
            assert snapshots[instance]['pinnedWorkspaceIds'] == expected
            assert len(snapshots[instance]['workspaceShortcuts']) <= 6
        assert app.state['pinnedWorkspaceIds'] == stored
        assert app.clients.record() == before
        assert app.db.execute(
            "SELECT value FROM client_views WHERE id='reader'"
        ).fetchone()[0] == persisted_client
        assert app.db.execute(
            "SELECT value FROM state_records WHERE kind='global' AND id='pinnedWorkspaceIds'"
        ).fetchone()[0] == persisted
        reordered = list(reversed(snapshots['workspaces']['pinnedWorkspaceIds']))
        await app.dispatch('shell.command', {'clientId': 'reader', 'instanceId': 'workspaces',
                                            'action': 'workspace.pinOrder', 'args': {'ids': reordered}})
        assert app.state['pinnedWorkspaceIds'] == reordered
        assert app.clients.record() == before
        assert app.db.execute(
            "SELECT value FROM client_views WHERE id='reader'"
        ).fetchone()[0] == persisted_client
        assert app.shell.inspect('reader', snapshots=True)['snapshots']['chats']['pinnedWorkspaceIds'] == reordered


async def test_row_draft_refuses_removed_registration_from_a_retained_rendered_row(app, tmp_path):
    paths, ids = await make_work(app, tmp_path)
    app.clients.attach('reader')
    with app.clients.bind('reader'):
        await app.dispatch('session.select', {'id': ids[0][1]})
        await app.dispatch('view.update', {'patch': {'draft': 'Keep this draft'}})
        await app.dispatch('canvas.show', {'kind': 'text', 'title': 'Kept Canvas', 'content': 'Kept source'})
        rows = app.shell.inspect('reader', snapshots=True)['snapshots']['workspaces']['workspaceShortcuts']
        retained = deepcopy(next(row for row in rows if row['workspaceId'] == ids[1][0]))
        await app.dispatch('workspace.remove', {'id': ids[1][0]})
        before = deepcopy(app.clients.record())
        sessions = deepcopy(app._state['sessions'])
        with pytest.raises(AppError, match='workspace folder is unavailable'):
            await app.dispatch('session.draft', {'workspaceId': retained['workspaceId'],
                'workspace': retained['path'], 'location': {'kind': 'workspace'}})
        assert app.clients.record() == before
        assert app._state['sessions'] == sessions
