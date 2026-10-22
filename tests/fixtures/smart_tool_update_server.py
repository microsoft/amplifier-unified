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
            return web.json_response({'calls': calls, 'pending': service.state['updates'].get('pendingSmartTools', []),
                                      'pendingApp': service.state['updates'].get('pendingApp'),
                                      'phase': service.state['updates'].get('phase'),
                                      'error': service.state['updates'].get('error'),
                                      'applicationStatus': service.state['updates'].get('application', {}).get('status'),
                                      'checkQueued': bool(manager.check_task and not manager.check_task.done())})
        async def failed(request):
            await manager.publish(phase='error', error='One tool needs attention.', pendingSmartTools=[{
                'previous': 'skip-example', 'revision': 'c' * 40, 'component': 'skip-example',
                'failure': {'phase': 'stage', 'reason': 'smart-tool-update-failed'}}])
            return web.json_response({'ok': True})
        app.router.add_get('/fixture/tool-updates', inspect)
        app.router.add_post('/fixture/tool-updates', failed)
        async def runner_wait(request):
            # The real dispatcher/update coordinator sees an independent runner
            # receipt plus a newer app. Only app preparation is synthetic: never
            # replace or restart the fixture's installation.
            async def stage_app():
                calls.setdefault('application', []).append('stage')
                await manager.publish(phase='app-staged', pendingApp={'latest': 'v9.9.9'},
                                      detail='Waiting for work to finish.')
            manager.app = stage_app
            manager.inventory = []
            service.state['settings']['updates']['autoInstall'] = False
            await manager.publish(phase='available', error=None, appAvailable=True, available=1,
                runnerAvailabilityCheck=True, sequence={'stage': 'application', 'install': False},
                application={'id': 'application', 'label': 'Amplifier Unified', 'kind': 'app',
                             'status': 'update', 'current': amplifier_web.__version__,
                             'latest': 'v9.9.9', 'releaseNotes': [{'version': '9.9.9', 'title': 'Fixture release',
                             'changes': ['New app remains available while the runner waits.'], 'notices': []}]},
                items=[], pendingSmartTools=[{'previous': 'a' * 24, 'target': 'b' * 24,
                    'runnerUpdate': 'c' * 32, 'runnerWaiting': 'unknown',
                    'runnerSource': {'runnerKind': 'managed-dtu-core'}}])
            return web.json_response({'ok': True})
        app.router.add_post('/fixture/runner-update', runner_wait)
        observation_release = asyncio.Event()
        observation_task = None
        async def hold_observation(request):
            nonlocal observation_task
            from amplifier_web import app_updates
            offered = dict(service.state['updates']['application'])
            async def available():
                calls.setdefault('checks', []).append('available')
                return offered
            app_updates.check = available
            await manager.publish(phase='staged', pendingApp=None, appAvailable=False,
                                  available=0, detail='Waiting for runner capacity.')
            entered = asyncio.Event()
            async def observe():
                async with manager.lock:
                    entered.set()
                    await observation_release.wait()
            observation_task = asyncio.create_task(observe())
            await entered.wait()
            return web.json_response({'ok': True})
        async def release_observation(request):
            observation_release.set()
            await observation_task
            return web.json_response({'ok': True})
        app.router.add_post('/fixture/runner-observation/hold', hold_observation)
        app.router.add_post('/fixture/runner-observation/release', release_observation)
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
