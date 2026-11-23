"""Revision tokens include coalesced work before external reads or CAS commands."""
from amplifier_web.state_records import load as load_saved_state
from copy import deepcopy
import asyncio
import json

import pytest

from amplifier_web.service import AppError
from amplifier_web.server import create_app
from test_automatic_history import app_factory
from test_service import Runtime
from test_appearance_preferences import report as appearance_report, ack as appearance_ack


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
    saved = load_saved_state(app.db)
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
    response = await client.get('/api/state/detail', params={'path':'/sessions'})
    directory_page = await response.json()
    target_path = next(item['path'] for item in directory_page['items'] if item['value']['id']==target)
    await service.on_runtime_event('assistant.delta', {'sessionId':target,'text':'background'})
    response = await client.get('/api/state/detail', params={'path':target_path+'/streaming','revision':str(revision)})
    assert response.status == 400
    assert 'App state changed' in (await response.json())['error']
    await service.on_runtime_event('assistant.delta', {'sessionId':target,'text':' continuation'})
    response = await client.get('/api/state', params={'sessionId':target})
    scoped = await response.json()
    assert response.status == 200 and scoped['revision'] == revision + 2
    assert scoped['selectedSessionId'] == selected == service.state['selectedSessionId']
    assert next(row for row in scoped['sessions'] if row['id']==target)['streaming'] == 'background continuation'
    response = await client.get('/api/state/detail')
    overview = await response.json()
    assert response.status == 200
    response = await client.get('/api/state/detail', params={'path':target_path+'/streaming','revision':str(scoped['revision']),'directoryRevision':overview['directoryRevision']})
    detail = await response.json()
    assert response.status == 200, detail
    assert detail['value'] == 'background continuation' and detail['revision'] == scoped['revision']
    await service.on_runtime_event('assistant.delta', {'sessionId':target,'text':' more'})
    response = await client.get('/api/state/detail', params={'path':target_path+'/streaming'})
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


@pytest.mark.parametrize('runtime_first', [False, True], ids=['command-first', 'runtime-first'])
async def test_cas_lock_admission_cannot_accept_a_later_queued_runtime_mutation(app_factory, monkeypatch, runtime_first):
    app = app_factory(); sid = await create_chat(app)
    client_id = 'cas-browser'
    app.clients.attach(client_id)
    with app.clients.bind(client_id):
        queue = app.subscribe()
    tasks, waiting, admission, commits = {}, {}, [], []
    real_acquire, real_commit = app.lock.acquire, app._commit_pending_progress

    async def observed_acquire():
        event = waiting.get(asyncio.current_task())
        if event is not None and not event.is_set():
            assert app.lock.locked()
            event.set()
        return await real_acquire()

    def observed_commit():
        commits.append((asyncio.current_task(), app.lock.locked(),
                        app.state['revision'], app._session(sid).get('streaming')))
        return real_commit()

    def admitted():
        admission.append((app.lock.locked(), app.state['revision'],
                          app._session(sid).get('streaming'), app._progress_dirty))

    async def command():
        with app.clients.bind(client_id):
            return await app.dispatch('view.update', {'patch': {'scheme': 'dark'}},
                                      expected_revision=revision, admission_check=admitted)

    async def delta():
        await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'queued runtime mutation'})
        return app.state['revision'], app._progress_dirty

    # Queue a command followed by a runtime callback behind another owner. A
    # separate flush-lock/reacquire-lock would allow that later callback to slip
    # between the command's barrier and comparison without changing revision.
    # Reverse order is the negative control: the command itself must publish the
    # earlier delta under this same lock, then refuse the now-stale revision.
    try:
        await appearance_report(app, client=client_id)
        before = app.appearance.read(client_id)
        shared_before = deepcopy(app.appearance.shared())
        revision = app.state['revision']
        monkeypatch.setattr(app.lock, 'acquire', observed_acquire)
        monkeypatch.setattr(app, '_commit_pending_progress', observed_commit)
        order = [('runtime', delta), ('command', command)] if runtime_first else [('command', command), ('runtime', delta)]
        async with app.lock:
            for name, operation in order:
                task = asyncio.create_task(operation())
                tasks[name] = task
                event = waiting[task] = asyncio.Event()
                await asyncio.wait_for(event.wait(), 2)
        results = await asyncio.wait_for(asyncio.gather(tasks['command'], tasks['runtime'],
                                                      return_exceptions=True), 2)
        result, runtime_observation = results
        assert runtime_observation == (revision if runtime_first else revision + 1, True)
        assert app._session(sid)['streaming'] == 'queued runtime mutation'
        assert app.appearance.shared() == shared_before
        if runtime_first:
            assert isinstance(result, AppError) and result.status == 409
            assert 'app changed' in str(result)
            assert commits == [(tasks['command'], True, revision, 'queued runtime mutation')]
            assert admission == [(True, revision + 1, 'queued runtime mutation', False)]
            assert app.appearance.read(client_id) == before
            assert app.state['revision'] == load_saved_state(app.db)['revision'] == revision + 1
            assert not app._progress_dirty
        else:
            assert not isinstance(result, BaseException), result
            assert result['accepted'] is True
            assert result['revision'] == result['state']['revision'] == revision + 1
            target = next(row for row in result['state']['sessions'] if row['id'] == sid)
            assert 'streaming' not in target
            assert admission == [(True, revision, None, False)]
            assert commits == [] and app._progress_dirty
            assert result['result']['status'] == 'awaiting-storage'
            assert result['result']['stored'] is False and result['result']['activated'] is False
            pending = app.appearance.read(client_id)
            assert pending['effective'] == before['effective']
            assert pending['command']['after'] == {
                'activeScope': 'client', 'values': {**before['effective'], 'scheme': 'dark'}}
            # Storage ACK is later, not part of CAS admission or inferred render.
            stored = await appearance_ack(app, client=client_id)
            assert stored['stored'] and stored['activated'] and not stored['rendered']
            assert app.appearance.read(client_id)['effective']['scheme'] == 'dark'
            assert app.appearance.shared() == shared_before
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        try:
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks.values(), return_exceptions=True), 2)
        finally:
            app.unsubscribe(queue)
