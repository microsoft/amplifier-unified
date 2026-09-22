"""Caller-owned Canvas actions must never retarget an unrelated client."""
import asyncio
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService


async def command(app, client, action, args):
    with app.clients.bind(client):
        return await app.dispatch(action, args)


async def agent(app, sid, action, args):
    return await app.app_bridge('dispatch', {'action': action, 'args': args}, sid)


async def read(app, sid, path=None, **args):
    return await app.app_bridge('get_state', {**({'path': path} if path else {}), **args}, sid)


@pytest.fixture
async def app(tmp_path):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Default chat'})
    await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Default artifact'})
    app.clients.attach('other')
    app.clients.attach('caller')
    await command(app, 'caller', 'workspace.create', {'path': str(tmp_path/'caller-workspace')})
    await command(app, 'caller', 'session.create', {'workspace': str(tmp_path/'caller-workspace')})
    await command(app, 'other', 'view.update', {'patch': {'draft': 'Keep other draft'}})
    yield app
    await app.close()


def caller(app):
    return app.clients.records['caller']['selectedSessionId']


async def publish(app, sid):
    await agent(app, sid, 'canvas.show', {'kind': 'text', 'title': 'Caller artifact', 'content': 'Caller content'})
    return app.state['canvasArtifacts'][-1]['id']


async def test_background_agent_selects_only_its_matching_client_and_reads_same_canvas(app):
    sid = caller(app)
    default = deepcopy({key: app.state[key] for key in ('selectedSessionId', 'selectedWorkspaceId', 'canvas', 'view')})
    other = deepcopy(app.clients.records['other'])
    artifact = await publish(app, sid)
    result = await agent(app, sid, 'canvas.select', {'id': artifact})
    assert result['state']['selectedSessionId'] == sid
    assert result['state']['canvas']['id'] == artifact
    assert result['state']['canvas']['content'] == 'Caller content'
    assert result['state']['canvasContext']['clientId'] == 'caller'
    assert (await read(app, sid, '/canvas/id'))['value'] == artifact
    assert (await read(app, sid, '/selectedSessionId'))['value'] == sid
    assert app.clients.records['caller']['canvas']['id'] == artifact
    assert app.clients.records['other'] == other
    assert {key: app.state[key] for key in default} == default


async def test_foreign_artifact_and_foreign_client_are_rejected(app):
    sid = caller(app)
    artifact = await publish(app, sid)
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError, match='another chat'):
        await agent(app, sid, 'canvas.select', {'id': app.state['canvas']['id']})
    for identity in ('other', 'missing'):
        with pytest.raises(AppError, match='calling conversation'):
            await agent(app, sid, 'canvas.select', {'id': artifact, 'clientId': identity})
        with pytest.raises(AppError, match='calling conversation'):
            await read(app, sid, clientId=identity)
    assert app.clients.records == before


async def test_unattached_caller_gets_placeholder_and_actionable_selection_error(app):
    sid = caller(app)
    artifact = await publish(app, sid)
    await command(app, 'caller', 'session.select', {'id': app.state['selectedSessionId']})
    before = deepcopy(app.clients.records)
    state = await read(app, sid)
    assert state['selectedSessionId'] == sid
    assert state['canvas']['placeholder'] and not state['canvas']['open']
    assert state['canvasContext']['status'] == 'unattached'
    assert state['canvasArtifacts']['items'][0]['id'] == artifact
    assert 'Default artifact' not in str(await read(app, sid, '/canvas'))
    assert 'Keep other draft' not in str(state['view'])
    with pytest.raises(AppError, match='Open the calling conversation') as error:
        await agent(app, sid, 'canvas.select', {'id': artifact})
    assert error.value.code == 'canvas_client_required'
    assert app.clients.records == before


async def test_multiple_matching_clients_require_explicit_selection_and_readback(app):
    sid = caller(app)
    artifact = await publish(app, sid)
    app.clients.attach('second-caller', resume='caller')
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError, match='Multiple clients'):
        await agent(app, sid, 'canvas.select', {'id': artifact})
    state = await read(app, sid)
    assert state['canvasContext']['status'] == 'ambiguous'
    assert set(state['canvasContext']['clientIds']) == {'caller', 'second-caller'}
    assert app.clients.records == before
    result = await agent(app, sid, 'canvas.select', {'id': artifact, 'clientId': 'second-caller'})
    assert result['state']['canvasContext']['clientId'] == 'second-caller'
    assert result['state']['canvas']['id'] == artifact
    assert (await read(app, sid, '/canvas/id', clientId='second-caller'))['value'] == artifact
    assert app.clients.records['caller'] == before['caller']
    assert app.clients.records['other'] == before['other']


async def test_bound_matching_client_is_preferred_and_visible_ui_matches_it(app):
    sid = caller(app)
    artifact = await publish(app, sid)
    app.clients.attach('second-caller', resume='caller')
    await app.update_device({'clientId': 'caller', 'controls': [{'label': 'Caller control'}]})
    await app.update_device({'clientId': 'other', 'controls': [{'label': 'Foreign control'}]})
    with app.clients.bind('caller'):
        result = await agent(app, sid, 'canvas.select', {'id': artifact})
        assert app.clients.current.get() == 'caller'
    assert result['state']['visibleUI']['clientId'] == 'caller'
    assert 'Foreign control' not in str(result['state'])
    assert 'visibleUI' not in await read(app, sid)


async def test_removed_callers_readback_does_not_report_some_other_chat(app):
    sid = caller(app)
    # An action can remove the caller before its post-action readback.
    app.state['sessions'] = [row for row in app.state['sessions'] if row['id'] != sid]
    with app.clients.bind('caller'):
        pass
    state = await read(app, sid)
    assert state['session'] is None
    assert state['canvas']['placeholder'] and not state['canvas']['open']
    assert state['canvasContext']['status'] == 'unattached'


async def test_target_dirty_guard_blocks_replacement_but_other_dirty_client_does_not(app):
    sid = caller(app)
    artifact = await publish(app, sid)
    for identity in ('caller', 'other'):
        await command(app, identity, 'canvas.show', {'kind': 'text', 'content': 'Unsaved viewer'})
        with app.clients.bind(identity):
            view = app.canvas_views.summary('primary')
        target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
        await command(app, identity, 'canvas.views.dirty', {**target, 'dirty': True})
        if identity == 'caller':
            caller_target = target
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError, match='primary viewer edit'):
        await agent(app, sid, 'canvas.select', {'id': artifact})
    assert app.clients.records == before
    await command(app, 'caller', 'canvas.views.dirty', {**caller_target, 'dirty': False})
    result = await agent(app, sid, 'canvas.select', {'id': artifact})
    assert result['state']['canvas']['id'] == artifact
    assert app.clients.records['other'] == before['other']


async def test_client_navigation_while_selection_waits_cannot_retarget_new_chat(app, monkeypatch):
    sid = caller(app)
    artifact = await publish(app, sid)
    entered = asyncio.Event()
    original = app.dispatch

    async def observed(action, *args, **kwargs):
        if action == 'canvas.select':
            entered.set()
        return await original(action, *args, **kwargs)

    monkeypatch.setattr(app, 'dispatch', observed)
    async with app.lock:
        task = asyncio.create_task(agent(app, sid, 'canvas.select', {'id': artifact}))
        await entered.wait()
        record = app.clients.records['caller']
        record['selectedSessionId'] = app.state['selectedSessionId']
        record['selectedWorkspaceId'] = app.state['selectedWorkspaceId']
        before = deepcopy(record['canvas'])
    with pytest.raises(AppError, match='client changed chats'):
        await task
    assert record['canvas'] == before
    assert record['selectedSessionId'] == app.state['selectedSessionId']


async def test_managed_background_artifact_has_no_ambient_workspace(app):
    await command(app, 'caller', 'session.create', {'location': {'kind': 'managed'}})
    sid = caller(app)
    artifact = await publish(app, sid)
    assert app.state['canvasArtifacts'][-1]['workspaceId'] is None
    result = await agent(app, sid, 'canvas.select', {'id': artifact})
    assert result['state']['selectedWorkspaceId'] is None
    assert result['state']['canvas']['id'] == artifact


async def test_navigation_after_commit_preserves_successful_selection_receipt(app, monkeypatch):
    sid = caller(app)
    artifact = await publish(app, sid)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app._flush_pending_progress

    async def delayed_flush():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(app, '_flush_pending_progress', delayed_flush)
    task = asyncio.create_task(agent(app, sid, 'canvas.select', {'id': artifact}))
    await entered.wait()
    assert app.clients.records['caller']['canvas']['id'] == artifact
    try:
        await command(app, 'caller', 'session.select', {'id': app.state['selectedSessionId']})
    finally:
        release.set()
    result = await task
    assert result['accepted']
    assert result['state']['selectedSessionId'] == sid
    assert result['state']['canvasContext']['status'] == 'detached'
    assert result['state']['canvas']['placeholder']
    assert app.clients.records['caller']['selectedSessionId'] == app.state['selectedSessionId']
    with pytest.raises(AppError, match='calling conversation'):
        await read(app, sid, clientId='caller')


async def test_legacy_default_without_attached_clients_still_works(tmp_path):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        artifact = await publish(app, sid)
        result = await agent(app, sid, 'canvas.select', {'id': artifact})
        assert result['state']['canvas']['id'] == artifact
        assert result['state']['canvasContext']['status'] == 'default'
    finally:
        await app.close()
