"""Real update coordinator/HTTP/SSE; deterministic installation peers only."""
import asyncio
import json
from pathlib import Path
import tempfile
from aiohttp import web
from settings_ui_server import main as settings_app


async def serve():
    import amplifier_web
    with tempfile.TemporaryDirectory(prefix='unified-tool-update-') as tmp:
        app = await settings_app(Path(tmp))
        service = app['service']
        manager = service.update_manager
        calls = {'stage': [], 'activate': []}
        service.state['updates'].update(phase='checked', error=None, pendingRelease=None,
                                        pendingApp=None, pendingRestart=None, initialPreparation=False,
                                        sequence={'stage': 'other', 'install': False})
        async def stage(source):
            name = source['installationId']
            calls['stage'].append(name)
            if name == 'unavailable' and calls['stage'].count(name) == 1:
                raise ValueError('private-provider-secret')
            return {'id': name + '-new'}
        async def activate(previous, target):
            calls['activate'].append(previous)
            return {'installationId': target}
        service.smart_tools.stage_update = stage
        service.smart_tools.activate_update = activate
        manager.inventory = [{'installationId': name, 'label': name, 'kind': 'smart tool',
                              'status': 'update', 'eligible': True, 'latest': 'b' * 40}
                             for name in ('unavailable', 'healthy')]
        await manager.installSmartTools()
        async def inspect(request):
            return web.json_response({'calls': calls, 'pending': service.state['updates'].get('pendingSmartTools', [])})
        async def failed(request):
            await manager.publish(phase='error', error='One tool needs attention.', pendingSmartTools=[{
                'previous': 'skip-example', 'revision': 'c' * 40, 'component': 'skip-example',
                'failure': {'phase': 'stage', 'reason': 'smart-tool-update-failed'}}])
            return web.json_response({'ok': True})
        app.router.add_get('/fixture/tool-updates', inspect)
        app.router.add_post('/fixture/tool-updates', failed)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url, 'package': amplifier_web.__file__}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(serve())
