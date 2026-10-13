"""Caller-owned Canvas actions must never retarget an unrelated client."""
import asyncio
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService
from test_output_media import media_app, media_args, gif_open_args, gif


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


@pytest.mark.parametrize('target_kind', ['other-chat', 'new-chat'])
async def test_agent_cannot_write_another_draft_through_its_own_connected_client(app, target_kind):
    sid = caller(app)
    with app.clients.bind('caller'):
        queue = app.subscribe()
    target = app.clients.records['other']['selectedSessionId'] if target_kind == 'other-chat' else None
    await command(app, 'caller', 'view.update', {'sessionId': target, 'patch': {'draft': 'Private human draft'}})
    before = deepcopy(app.clients.records)
    try:
        with pytest.raises(AppError, match='calling conversation'):
            await agent(app, sid, 'view.update', {'clientId': 'caller', 'sessionId': target,
                'patch': {'draft': 'Agent instructions for the wrong chat'}})
        assert app.clients.records == before
    finally:
        app.unsubscribe(queue)


@pytest.mark.parametrize('target', ['implicit', 'app-id', 'native-id'])
async def test_agent_draft_default_is_calling_chat_and_other_drafts_are_preserved(app, target):
    sid = caller(app)
    with app.clients.bind('caller'):
        queue = app.subscribe()
    other = deepcopy(app.clients.records['other'])
    app._session(sid)['runtimeSessionId'] = 'native-caller-chat'
    args = {'clientId': 'caller', 'patch': {'draft': 'A requested draft'}}
    if target != 'implicit':
        args['sessionId'] = sid if target == 'app-id' else 'native-caller-chat'
    try:
        await agent(app, sid, 'view.update', args)
        assert app.clients.records['caller']['drafts'][sid] == 'A requested draft'
        assert app.clients.records['other'] == other
    finally:
        app.unsubscribe(queue)


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


@pytest.mark.parametrize('binding', ['attached', 'no-input-client', 'navigated-away'])
@pytest.mark.parametrize('linked', [True, False])
async def test_retained_media_metadata_uses_real_worker_caller_not_browser_lifetime(
        media_app, monkeypatch, binding, linked):
    from types import SimpleNamespace
    from amplifier_web import output_media
    from amplifier_web.runtime_worker import Worker
    from test_collaborative_workspaces import generation
    app = media_app
    args = media_args(app)
    sid = args['sessionId']
    app.clients.attach('metadata-owner')
    with app.clients.bind('metadata-owner'):
        queue = app.subscribe()
    session = app._session(sid)
    await generation(app, session, 'metadata-generation', ['metadata-input'])
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(session_id=sid))
    worker.runtime = SimpleNamespace(generation={'id': 'metadata-generation'})
    worker.context_inputs = ['metadata-input']
    worker.context_bindings = {'metadata-input': {'clientId': 'metadata-owner'}}

    async def transport(operation, values):
        # A captured browser context is not permission to borrow that browser
        # when the worker stamps [] for the current input's client bindings.
        with app.clients.bind('metadata-owner'):
            return await app.app_bridge(operation, values, sid)

    worker.bridge = transport
    bridge = worker.app_access_bridge(worker.session.coordinator)
    try:
        saved = (await bridge('dispatch', {'action': 'outputs.attachMedia', 'args': args,
                                          'id': 'agent-metadata-save'}))['result']
        original = app.outputs.record(sid, saved['id'])
        assert original['origin']['source'] == 'agent'
        for index in range(3):
            await app.dispatch('outputs.comment', {'sessionId': sid, 'id': saved['id'],
                                                   'body': f'Bounded review {index}'})
        values = gif_open_args(args, saved)
        with app.clients.bind('metadata-owner'):
            await app.outputs.media.observe({**values, 'generation': 1, 'sequence': 1,
                'renderState': 'ready', 'playback': 'paused', 'presentedFrames': 2}, 'ui')
        if not linked:
            await app.dispatch('outputs.unlink', {'sessionId': sid, 'id': saved['id'],
                                                  'expectedRevision': 1})
        foreign = app._new_session({'title': 'Foreign metadata conversation'})
        app.state['sessions'].append(foreign)
        if binding == 'no-input-client':
            worker.context_inputs = []
            worker.context_bindings = {}
        elif binding == 'navigated-away':
            with app.clients.bind('metadata-owner'):
                await app.dispatch('session.select', {'id': foreign['id']})

        def no_body(*args, **kwargs):
            pytest.fail('Metadata read decoded media bytes')

        with monkeypatch.context() as patch:
            patch.setattr(output_media, '_saved_bytes', no_body)
            patch.setattr(output_media, 'validate_retained_body', no_body)
            result = (await bridge('dispatch', {'action': 'outputs.read', 'args': {
                'id': saved['id'], 'commentOffset': 1, 'commentLimit': 1}}))['result']
        assert result['kind'] == 'media' and result['origin'] == original['origin']
        assert result['versionEvidence'] == 'snapshot'
        assert result['saved'] and result['attached'] is linked
        assert result['comments'][0]['body'] == 'Bounded review 1'
        assert result['nextCommentOffset'] == 2
        assert not {'body', '$resource', 'data', 'canvasSource'} & result.keys()
        expected = 'observed' if binding == 'attached' else 'unbound'
        assert result['browserObservation']['status'] == expected
        if binding != 'attached':
            assert result['playback'] == 'unknown'
            assert 'presentedFrames' not in result['browserObservation']
            with app.clients.bind('metadata-owner' if binding == 'navigated-away' else None):
                for operation in ('bytes', 'head'):
                    with pytest.raises(ValueError, match='client'):
                        await getattr(app.outputs.media, operation)(values)
                with pytest.raises(AppError):
                    await app.dispatch('canvas.openGif', values, include_state=False)
        else:
            with app.clients.bind('metadata-owner'):
                assert await app.outputs.media.bytes(values) == gif()
        with pytest.raises(AppError, match='calling conversation'):
            await bridge('dispatch', {'action': 'outputs.read', 'args': {
                'sessionId': foreign['id'], 'id': saved['id']}})
        # Even with callerSessionId injected, another conversation's output ID
        # is not readable by the foreign root's transport.
        with pytest.raises(AppError, match='another conversation'):
            await app.app_bridge('dispatch', {'action': 'outputs.read',
                'args': {'id': saved['id']}, '_inputClients': []}, foreign['id'])
    finally:
        app.unsubscribe(queue)
