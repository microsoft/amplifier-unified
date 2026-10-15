"""Real confirmed-action HTTP/UI; activation stops at the owned busy-lock boundary."""
import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path
from aiohttp import web
from settings_ui_server import main as settings_app


async def serve():
    import amplifier_web
    with tempfile.TemporaryDirectory(prefix='unified-update-restart-') as tmp:
        app = await settings_app(Path(tmp))
        service = app['service']
        manager = service.update_manager
        session = service._session()
        session.update(status='working', configurationBusy=True,
                       worktreeHandoffs=[{'id': 'retained-unknown', 'phase': 'unknown'}])
        await manager.publish(phase='app-staged', initialPreparation=False,
            pendingApp={'revision': 'a'*40, 'generation': 'b'*32, 'attemptId': 'qualified-one'},
            application={'status': 'update', 'canInstall': True, 'latest': 'v99.0.0'})
        # Exercise real action admission and asynchronous rejection without any
        # installation, runtime interruption, pointer promotion or host restart.
        await manager.lock.acquire()
        calls = []
        command = manager.command
        async def observed(action, args=None, command_id=None):
            if action == 'forceRestart':
                calls.append({'action': action, 'args': args, 'requestId': command_id})
            return await command(action, args, command_id)
        manager.command = observed
        async def control(request):
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            if request.method == 'POST':
                async with service.lock:
                    service.state['updates']['pendingApp']['attemptId'] = 'qualified-two'
                await manager.publish()
            return web.json_response({'calls': calls, 'unknown': session['configurationBusy'],
                'target': service.state['updates']['forceRestartTarget']})
        app.router.add_get('/fixture/update-restart', control)
        app.router.add_post('/fixture/update-restart', control)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] |= {url}
        stopped = asyncio.Event()
        if os.name != 'nt':
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
        print(json.dumps({'url': url, 'package': amplifier_web.__file__}), flush=True)
        try:
            await stopped.wait()
        finally:
            manager.lock.release()
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(serve())
