"""Restored browser selections load history without a navigation or model turn."""
import asyncio
import json

from amplifier_web.server import create_app
from amplifier_web.service import AppService
from test_automatic_history import ObservedRuntime, files_snapshot, native_session


async def test_refresh_follows_connected_clients_not_inactive_saved_views(tmp_path):
    paths = [native_session(tmp_path / 'workspace', name) for name in ('first', 'second', 'inactive')]
    originals = [files_snapshot(path) for path in paths]
    service = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    queues = []
    try:
        await service.history.refresh()
        assert service.state['selectedSessionId'] is None
        ids = {row['nativeIdentity']: row['id'] for row in service.state['sessions']}
        for identity in ('first', 'second', 'inactive'):
            service.clients.attach(identity)
            with service.clients.bind(identity):
                service.state['selectedSessionId'] = ids[identity]
                service.clients.draft(ids[identity], 'Keep this unsent draft')
                if identity != 'inactive':
                    queues.append(service.subscribe())
        await service.history.refresh()
        for identity in ('first', 'second'):
            session = service._session(ids[identity])
            assert session['historyLoaded'] is True
            assert len(session['messages']) == 2
            assert service.clients.records[identity]['drafts'][ids[identity]] == 'Keep this unsent draft'
        assert service._session(ids['inactive'])['historyLoaded'] is False
        assert service.state['selectedSessionId'] is None
        assert service.runtime.started == service.runtime.sent == []
        assert [files_snapshot(path) for path in paths] == originals
        # A closed browser's retained view must not keep refreshing history.
        service.unsubscribe(queues.pop())
        for path in paths[:2]:
            with (path / 'transcript.jsonl').open('a') as stream:
                stream.write(json.dumps({'role': 'assistant', 'content': 'Later saved reply'}) + '\n')
        await service.history.refresh()
        assert len(service._session(ids['first'])['messages']) == 3
        assert len(service._session(ids['second'])['messages']) == 2
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
    sid = next(row['id'] for row in previous.state['sessions'] if row.get('nativeIdentity') == 'restored-chat')
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
    await service.history.close()  # Isolate reconnect from the periodic refresh.
    await service.history.refresh()
    client = await authenticated_client(app)
    assert service._session(sid)['historyLoaded'] is False
    response = await client.get('/api/events', params={'clientId': 'browser'})
    try:
        async def loaded_state():
            while True:
                line = await response.content.readline()
                if line.startswith(b'data: '):
                    state = json.loads(line[6:])
                    selected = next(row for row in state['sessions'] if row['id'] == sid)
                    if selected.get('historyLoaded'):
                        return state
        state = await asyncio.wait_for(loaded_state(), 2)
        assert state['selectedSessionId'] == sid
        assert state['view']['draft'] == 'Still unsent after reconnect'
        assert service._state['selectedSessionId'] is None
        assert runtime.started == runtime.sent == []
        assert files_snapshot(path) == original
    finally:
        response.close()
