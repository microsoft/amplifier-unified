"""Revision tokens include coalesced work before external reads or CAS commands."""
import asyncio
import json

import pytest

from amplifier_web.service import AppError
from amplifier_web.server import create_app
from test_automatic_history import app_factory
from test_service import Runtime


async def create_chat(app):
    await app.dispatch('session.create', {})
    return app.state['selectedSessionId']


async def test_cas_rejects_revision_before_pending_stream_and_keeps_delta(app_factory):
    app = app_factory(); sid = await create_chat(app)
    revision = app.state['revision']; before = app.browser_state(); queue = app.subscribe()
    await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'pending answer'})
    assert app.state['revision'] == revision and queue.empty()
    assert app.browser_state() is before
    with pytest.raises(AppError, match='app changed'):
        await app.dispatch('view.update', {'patch':{'scheme':'dark'}}, expected_revision=revision)
    assert app.state['view']['scheme'] == 'system'
    assert app.state['revision'] == revision + 1
    assert queue.get_nowait()['sessions'][0]['streaming'] == 'pending answer'
    saved = json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert saved['revision'] == revision + 1
    assert not app._progress_dirty
    await asyncio.sleep(.3)
    assert queue.empty(), 'The cancelled timer must not republish the flushed update'
    app.unsubscribe(queue)


async def test_agent_reads_flush_progress_and_reject_old_page_tokens(app_factory):
    app = app_factory(); sid = await create_chat(app); revision = app.state['revision']
    await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'first'})
    with pytest.raises(ValueError, match='App state changed'):
        await app.app_bridge('get_state', {'path':'/sessions/0/streaming','revision':revision}, sid)
    await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':' second'})
    result = await app.app_bridge('get_state', {'path':'/sessions/0/streaming'}, sid)
    assert result['value'] == 'first second'
    assert result['revision'] == revision + 2 == app.browser_state()['revision']
    overview = await app.app_bridge('get_state', {}, sid)
    assert overview['revision'] == result['revision']
    assert not app._progress_dirty


async def test_agent_action_overview_flushes_stream_emitted_during_admission(app_factory):
    app = app_factory(); sid = await create_chat(app)
    async def send(session, text, input_id, emit):
        await emit('assistant.delta', {'sessionId':session['id'],'text':'accepted stream'})
    app.runtime.send = send
    result = await app.app_bridge('dispatch', {'action':'conversation.send','args':{'text':'A request'}}, sid)
    assert result['state']['revision'] == app.state['revision']
    assert not app._progress_dirty
    assert app.browser_state()['sessions'][0]['streaming'] == 'accepted stream'
    # The receipt identifies admission; the overview can include later progress.
    assert result['state']['revision'] > result['revision']


async def http_app(tmp_path, authenticated_client):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    await app['service'].history.close()
    await app['service'].diagnostics.close()
    client = await authenticated_client(app)
    await create_chat(app['service'])
    return app['service'], client


async def test_http_detail_and_scoped_state_have_published_revisions(tmp_path, authenticated_client):
    service, client = await http_app(tmp_path, authenticated_client)
    target = service.state['selectedSessionId']
    await service.dispatch('session.create', {'title':'Browser chat'})
    selected = service.state['selectedSessionId']; revision = service.state['revision']
    target_index = next(index for index,row in enumerate(service.state['sessions']) if row['id']==target)
    await service.on_runtime_event('assistant.delta', {'sessionId':target,'text':'background'})
    response = await client.get('/api/state/detail', params={'path':f'/sessions/{target_index}/streaming','revision':str(revision)})
    assert response.status == 400
    assert 'App state changed' in (await response.json())['error']
    await service.on_runtime_event('assistant.delta', {'sessionId':target,'text':' continuation'})
    response = await client.get('/api/state', params={'sessionId':target})
    scoped = await response.json()
    assert response.status == 200 and scoped['revision'] == revision + 2
    assert scoped['selectedSessionId'] == selected == service.state['selectedSessionId']
    assert next(row for row in scoped['sessions'] if row['id']==target)['streaming'] == 'background continuation'
    response = await client.get('/api/state/detail', params={'path':f'/sessions/{target_index}/streaming','revision':str(scoped['revision'])})
    detail = await response.json()
    assert detail['value'] == 'background continuation' and detail['revision'] == scoped['revision']
    await service.on_runtime_event('assistant.delta', {'sessionId':target,'text':' more'})
    response = await client.get('/api/state/detail', params={'path':f'/sessions/{target_index}/streaming'})
    detail = await response.json()
    assert detail['value'] == 'background continuation more' and detail['revision'] == scoped['revision'] + 1


async def test_initial_sse_snapshot_flushes_pending_progress(tmp_path, authenticated_client):
    service, client = await http_app(tmp_path, authenticated_client)
    sid = service.state['selectedSessionId']; revision = service.state['revision']
    await service.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'before reconnect'})
    async with client.get('/api/events') as response:
        assert response.status == 200
        event = await response.content.readline()
        identity = await response.content.readline()
        data = await response.content.readline()
        assert event == b'event: state\n'
        snapshot = json.loads(data.removeprefix(b'data: '))
        assert identity == f"id: {revision + 1}\n".encode()
        assert snapshot['revision'] == revision + 1
        assert snapshot['sessions'][0]['streaming'] == 'before reconnect'


async def test_failed_flush_does_not_return_changed_data_under_old_revision(app_factory, monkeypatch):
    app = app_factory(); sid = await create_chat(app); revision = app.state['revision']
    await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'retained'})
    save = app._save
    def fail(): raise OSError('fixture disk unavailable')
    monkeypatch.setattr(app, '_save', fail)
    with pytest.raises(OSError, match='disk unavailable'):
        await app.app_bridge('get_state', {'path':'/sessions/0/streaming'}, sid)
    assert app.state['revision'] == revision and app._progress_dirty
    monkeypatch.setattr(app, '_save', save)
    result = await app.app_bridge('get_state', {'path':'/sessions/0/streaming'}, sid)
    assert result['value'] == 'retained' and result['revision'] == revision + 1


async def test_cas_lock_admission_cannot_accept_a_later_queued_runtime_mutation(app_factory):
    app = app_factory(); sid = await create_chat(app); revision = app.state['revision']
    command_queued, runtime_queued = asyncio.Event(), asyncio.Event()
    async def command():
        command_queued.set()
        return await app.dispatch('view.update', {'patch':{'scheme':'dark'}}, expected_revision=revision)
    async def delta():
        runtime_queued.set()
        await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'later queued mutation'})
    # Queue a command followed by a runtime callback behind another owner. A
    # separate flush-lock/reacquire-lock would allow that later callback to slip
    # between the command's barrier and comparison without changing revision.
    await app.lock.acquire()
    command_task = asyncio.create_task(command()); await command_queued.wait()
    runtime_task = asyncio.create_task(delta()); await runtime_queued.wait()
    app.lock.release()
    result = await command_task
    await runtime_task
    assert result['accepted']
    assert 'streaming' not in result['state']['sessions'][0]
    assert app._session(sid)['streaming'] == 'later queued mutation'
    assert app._progress_dirty
