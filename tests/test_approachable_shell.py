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
        assert len(snapshot['recentShortcuts']) == 8
        assert snapshot['recentShortcuts'][0]['id'] == 'chat-69'
        assert all(row['id']!='chat-0' for row in snapshot['recentShortcuts'])
        assert len(snapshot['workspaceShortcuts']) <= 6
        assert snapshot['sidebarNavigation']['recent']['total'] == 11
        assert [row['id'] for row in snapshot['sidebarNavigation']['pinned']['items']] == ['chat-0']
        await app.dispatch('shell.view.update', {'clientId':'reader','instanceId':'chats','patch':{'navRecentView':{'navFilter':''}}})
        page=app.shell.inspect('reader',snapshots=True)['snapshots']['chats']['sidebarNavigation']['recent']
        assert page['total']==69 and len(page['items'])==40 and page['pages']==2


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
