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
