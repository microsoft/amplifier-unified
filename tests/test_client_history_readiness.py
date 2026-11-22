"""Restored browser selections load history without a navigation or model turn."""
import asyncio
import copy
import json

import pytest

from amplifier_web.server import create_app
from amplifier_web.service import AppService
from test_automatic_history import ObservedRuntime, files_snapshot, native_session
from test_state_transport import apply, read_event


async def test_terminal_history_follows_stream_target_until_disconnect(tmp_path):
    path = native_session(tmp_path / 'workspace', 'terminal-chat')
    saved = native_session(tmp_path / 'workspace', 'saved-selection')
    originals = [files_snapshot(folder) for folder in (path, saved)]
    service = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    queue = None
    try:
        await service.history.refresh()
        ids = {row['nativeIdentity']: row['id'] for row in service.directory.select() if row.get('nativeIdentity')}
        assert not set(ids.values()) & {row['id'] for row in service._state['sessions']}
        service._state['selectedSessionId'] = ids['saved-selection']
        service.clients.attach('terminal', kind='tui')
        with service.clients.bind('terminal'):
            queue = service.subscribe(session_id=ids['terminal-chat'])
        await service.history.refresh()
        resident = {row['id']: row for row in service._state['sessions']}
        assert resident[ids['terminal-chat']]['historyLoaded'] is True
        assert ids['saved-selection'] not in resident
        assert [files_snapshot(folder) for folder in (path, saved)] == originals
        service.unsubscribe(queue)
        queue = None
        with (path / 'transcript.jsonl').open('a') as stream:
            stream.write(json.dumps({'role': 'assistant', 'content': 'While disconnected'}) + '\n')
        appended = files_snapshot(path)
        await service.history.refresh()
        assert len(service._session(ids['terminal-chat'])['messages']) == 2
        with service.clients.bind('terminal'):
            queue = service.subscribe(session_id=ids['terminal-chat'])
        await service.history.refresh()
        assert service._session(ids['terminal-chat'])['messages'][-1]['text'] == 'While disconnected'
        assert service.clients.records['terminal']['selectedSessionId'] == ids['saved-selection']
        assert service._state['selectedSessionId'] == ids['saved-selection']
        assert ids['saved-selection'] not in {row['id'] for row in service._state['sessions']}
        assert files_snapshot(path) == appended and files_snapshot(saved) == originals[1]
        assert service.runtime.started == service.runtime.sent == []
    finally:
        if queue is not None:
            service.unsubscribe(queue)
        await service.close()


@pytest.mark.parametrize('web_observer', [False, True])
async def test_terminal_http_stream_reads_only_its_cold_target(tmp_path, authenticated_client, web_observer):
    paths = [native_session(tmp_path / 'workspace', name)
             for name in ('terminal-chat', 'saved-selection', 'unrelated')]
    originals = [files_snapshot(path) for path in paths]
    runtime = ObservedRuntime()
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
                           voice=False, preload_providers=False, background_updates=False,
                           background_work=False)
    service = app['service']
    service.history.task.cancel()
    await asyncio.gather(service.history.task, return_exceptions=True)
    service.history.task = None
    await service.history.refresh()
    ids = {row['nativeIdentity']: row['id'] for row in service.directory.select() if row.get('nativeIdentity')}
    service._state['selectedSessionId'] = ids['saved-selection']
    client = await authenticated_client(app)
    attached = await client.post('/api/clients/attach', json={
        'clientId': 'terminal', 'kind': 'tui', 'protocolVersion': 1, 'includeState': False})
    assert attached.status == 200
    assert not set(ids.values()) & {row['id'] for row in service._state['sessions']}
    # The real terminal adapter resolves/loads before subscribing. This is
    # transport coverage, not a replacement for the cold direct-demand case.
    response = await client.get(f"/api/sessions/{ids['terminal-chat']}/events",
                                params={'clientId': 'terminal'})
    web_queue = None
    try:
        assert response.status == 200
        kind, payload = await asyncio.wait_for(read_event(response), 2)
        assert kind == 'snapshot'
        assert payload['session']['id'] == ids['terminal-chat']
        assert [row['text'] for row in payload['session']['messages']] == [
            'Saved CLI question', 'Saved CLI answer']
        assert [files_snapshot(path) for path in paths] == originals
        if web_observer:
            service.clients.attach('browser')
            with service.clients.bind('browser'):
                service.state['selectedSessionId'] = ids['terminal-chat']
                web_queue = service.subscribe()
        with (paths[0] / 'transcript.jsonl').open('a') as stream:
            stream.write(json.dumps({'role': 'assistant', 'content': 'Connected append'}) + '\n')
        appended = files_snapshot(paths[0])
        await service.history.refresh()
        async def appended_snapshot():
            while True:
                kind, payload = await read_event(response)
                assert kind == 'snapshot' and payload['session']['id'] == ids['terminal-chat']
                if payload['session']['messages'][-1]['text'] == 'Connected append':
                    return
        await asyncio.wait_for(appended_snapshot(), 2)
        assert service.clients.records['terminal']['selectedSessionId'] == ids['saved-selection']
        assert service._state['selectedSessionId'] == ids['saved-selection']
        resident = {row['id'] for row in service._state['sessions']}
        assert ids['saved-selection'] not in resident and ids['unrelated'] not in resident
        assert runtime.started == runtime.sent == runtime.stopped == []
        assert files_snapshot(paths[0]) == appended
        assert [files_snapshot(path) for path in paths[1:]] == originals[1:]
        if web_observer:
            projected = service.history.windows.project(service._session(ids['terminal-chat'], hydrate=False))
            assert projected['messages'][-1]['text'] == 'Connected append'
    finally:
        if web_queue is not None:
            service.unsubscribe(web_queue)
        response.close()


async def test_cold_stream_disconnected_before_admission_stays_nonresident(tmp_path):
    native_session(tmp_path / 'workspace', 'disconnected')
    service = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    queue = task = None
    try:
        await service.history.refresh()
        sid = service.directory.resolve('disconnected')['id']
        assert not service._state['sessions']
        service.clients.attach('terminal', kind='tui')
        with service.clients.bind('terminal'):
            queue = service.subscribe(session_id=sid)
        async with service.lock:
            task = asyncio.create_task(service.history._refresh_selected_history())
            await asyncio.sleep(0)  # The cold reader now waits on admission.
            assert not task.done()
            service.unsubscribe(queue)
            queue = None
        await asyncio.wait_for(task, 2)
        assert not service._state['sessions']
        assert service.runtime.started == service.runtime.sent == []
    finally:
        if queue is not None:
            service.unsubscribe(queue)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


async def test_refresh_follows_connected_clients_not_inactive_saved_views(tmp_path):
    paths = [native_session(tmp_path / 'workspace', name) for name in ('first', 'second', 'inactive')]
    originals = [files_snapshot(path) for path in paths]
    service = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    queues = []
    try:
        await service.history.refresh()
        assert service.state['selectedSessionId'] is None
        ids = {row['nativeIdentity']: row['id'] for row in service.directory.select() if row.get('nativeIdentity')}
        for identity in ('first', 'second', 'inactive'):
            service.clients.attach(identity)
            with service.clients.bind(identity):
                service.state['selectedSessionId'] = ids[identity]
                service.clients.draft(ids[identity], 'Keep this unsent draft')
                if identity != 'inactive':
                    queues.append(service.subscribe())
        await service.history.refresh()
        for identity in ('first', 'second'):
            session = service._session(ids[identity], hydrate=False)
            projected = service.history.windows.project(session)
            assert projected['historyLoading'] is False and projected['historyError'] is None
            assert [row['text'] for row in projected['messages']] == ['Saved CLI question', 'Saved CLI answer']
            assert all(row['displayOnly'] for row in projected['messages'])
            assert projected['messageWindow']['coverage']['kind'] == 'native-saved'
            assert session['historyLoaded'] is False and dict.get(session, 'messages', []) == []
            assert service.clients.records[identity]['drafts'][ids[identity]] == 'Keep this unsent draft'
        assert service._session(ids['inactive'])['historyLoaded'] is False
        assert service.state['selectedSessionId'] is None
        assert service.runtime.started == service.runtime.sent == []
        assert [files_snapshot(path) for path in paths] == originals
        # A closed browser's retained view must not keep refreshing history.
        second_window = copy.deepcopy(service.history.windows.project(
            service._session(ids['second'], hydrate=False)))
        service.unsubscribe(queues.pop())
        for path in paths[:2]:
            with (path / 'transcript.jsonl').open('a') as stream:
                stream.write(json.dumps({'role': 'assistant', 'content': 'Later saved reply'}) + '\n')
        await service.history.refresh()
        first = service._session(ids['first'], hydrate=False)
        second = service._session(ids['second'], hydrate=False)
        assert [row['text'] for row in service.history.windows.project(first)['messages']] == [
            'Saved CLI question', 'Saved CLI answer', 'Later saved reply']
        assert service.history.windows.project(second) == second_window
        for identity in ids.values():
            canonical = service._session(identity, hydrate=False)
            assert canonical['historyLoaded'] is False and dict.get(canonical, 'messages', []) == []
        for identity in ('first', 'second', 'inactive'):
            assert service.clients.records[identity]['drafts'][ids[identity]] == 'Keep this unsent draft'
        assert service.runtime.started == service.runtime.sent == []
    finally:
        for queue in queues:
            service.unsubscribe(queue)
        await service.close()


async def test_event_reconnect_loads_restored_selection_without_reselecting(tmp_path, authenticated_client):
    path = native_session(tmp_path / 'workspace', 'restored-chat')
    original = files_snapshot(path)
    home = tmp_path / 'app'
    previous = AppService(home, ObservedRuntime(), workspace=tmp_path)
    await previous.history.refresh()
    sid = previous.directory.resolve('restored-chat')['id']
    previous.clients.attach('browser')
    with previous.clients.bind('browser'):
        previous.state['selectedSessionId'] = sid
        previous.clients.draft(sid, 'Still unsent after reconnect')
        previous._save()
    await previous.close()
    runtime = ObservedRuntime()
    app = await create_app(home, workspace=tmp_path, runtime=runtime,
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    # Isolate reconnect from the periodic refresh, not from discovery itself.
    # Closing the index now permanently rejects scans for safe shutdown.
    service.history.task.cancel()
    await asyncio.gather(service.history.task, return_exceptions=True)
    service.history.task = None
    await service.history.refresh()
    client = await authenticated_client(app)
    assert service._session(sid)['historyLoaded'] is False
    from amplifier_web.shell_resources import scope_for
    with service.clients.bind('browser'):
        service.shell.resources.register('browser', 1, [], scope_for(service.state))
    response = await client.get('/api/events', params={'clientId': 'browser', 'transport': 'delta-v1'})
    try:
        async def loaded_state():
            state = None
            while True:
                kind, payload = await read_event(response)
                if kind == 'shell':
                    continue
                if kind == 'state':
                    state = payload
                else:
                    assert kind == 'state-delta' and state is not None
                    state = apply(state, payload)
                selected = next(row for row in state['sessions'] if row['id'] == sid)
                if not selected.get('historyLoading') and selected.get('messageWindow', {}).get('windowKey'):
                    assert selected.get('historyError') is None
                    assert [row['text'] for row in selected['messages']] == ['Saved CLI question', 'Saved CLI answer']
                    assert all(row['displayOnly'] for row in selected['messages'])
                    return state
        state = await asyncio.wait_for(loaded_state(), 2)
        assert state['selectedSessionId'] == sid
        assert state['view']['draft'] == 'Still unsent after reconnect'
        assert service._state['selectedSessionId'] is None
        canonical = service._session(sid, hydrate=False)
        assert canonical['historyLoaded'] is False and dict.get(canonical, 'messages', []) == []
        assert runtime.started == runtime.sent == []
        assert files_snapshot(path) == original
    finally:
        response.close()
