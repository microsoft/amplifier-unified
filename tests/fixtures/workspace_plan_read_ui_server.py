"""Private ordinary-workspace plan receipt and request-order fixture."""
import asyncio
import hashlib
import json
import signal
import tempfile
from pathlib import Path

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.event_log_view import event_path


async def main(home):
    app = await fixture.main(home)
    service, runtime = app['service'], app['runtime']
    await service.history.close()
    await service.event_log_view.close()
    selected = service._session()
    workspace = selected['workspace']
    paths = []
    for index in range(45):
        made = await service.dispatch('session.create', {
            'title': f'Plan chat {index:02}', 'workspace': workspace, 'select': False})
        session = service._session(made['sessionId'])
        identity = session.get('runtimeSessionId') or session['id']
        path = event_path(session, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'event': 'tool:post', 'timestamp': index + 1, 'data': {
            'session_id': identity, 'tool_name': 'todo', 'tool_call_id': f'plan-{index}',
            'result': {'success': True, 'output': {'todos': [
                {'content': f'Accepted task {index:02}', 'status': 'completed'}]}}}}) + '\n')
        paths.append(path)
    await service.dispatch('session.select', {'id': selected['id']})
    runtime_calls = []
    for name in ('start', 'stop', 'control'):
        original = getattr(runtime, name)

        async def observed(*args, _name=name, _original=original, **kwargs):
            runtime_calls.append(_name)
            return await _original(*args, **kwargs)

        setattr(runtime, name, observed)

    async def inspect(_request):
        return web.json_response({'selected': selected['id'], 'runtimeCalls': runtime_calls,
            'canonical': {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}})

    app.router.add_get('/fixture/plan-read', inspect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='workspace-plan-read-') as directory:
        asyncio.run(main(Path(directory)))
