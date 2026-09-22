"""Production UI/actions with synthetic desktop facts; no OS or provider access."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'), AMPLIFIER_WEB_HOME=str(home/'app'), AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_web.desktop_readiness import worker_report

    class DesktopRuntime(Runtime):
        mode = 'unavailable'
        calls = []

        async def desktop_readiness(self, sid):
            if self.mode == 'absent':
                return {'status': 'unavailable', 'sessionId': sid, 'reason': 'No ready conversation runtime.'}
            names = ['desktop', 'computer'] if self.mode == 'mounted' else ['computer_use_unavailable']
            tools = {name: SimpleNamespace(input_schema={'properties': {'action': {'enum': ['doctor']}}}) for name in names}
            controls = SimpleNamespace(catalog_revision='synthetic-catalog', coordinator=SimpleNamespace(get=lambda _: tools),
                configuration=lambda: {'modules': [{'section': 'tools', 'module': 'tool-computer-use', 'id': 'tool-computer-use', 'enabled': True}]})
            result = worker_report(controls)
            result.update(sessionId=sid, python={'path': '/synthetic/worker/bin/python', 'version': '3.13'},
                computerUsePackage={'status': 'installed', 'version': 'fixture', 'importVerified': False})
            return result

        async def control(self, sid, operation, args):
            self.calls.append({'sessionId': sid, 'operation': operation, 'args': copy.deepcopy(args)})
            if operation == 'catalog.inspect':
                return {'tools': [{'name': 'desktop', 'description': 'Synthetic doctor only', 'inputSchema': {'properties': {'action': {'enum': ['doctor']}}}}]}
            if operation == 'tool.invoke':
                assert args['name'] == 'desktop' and args['arguments'] == {'action': 'doctor'}
                return {'callId': 'synthetic-doctor', 'result': {'success': True, 'output': json.dumps({
                    'bound_target': {'kind': 'remote', 'target': 'ssh://fixture-host'},
                    'permissions': {'source': 'connect-time snapshot', 'snapshot_age_s': 321},
                    'action_surface': {'works': ['capture'], 'blocked': {}},
                    'safety_state': {'halted': True},
                })}}
            return {}

    runtime = DesktopRuntime()
    app = await create_app(home/'app', workspace=str(home), runtime=runtime, voice=False, background_updates=False, preload_providers=False)
    service = app['service']; runtime.service = service
    app['control_token'] = 'fixture-browser-control-token'
    class Native:
        mode = 'missing'
        calls = []
        delay = False
        entered = asyncio.Event()
        release = asyncio.Event()
        async def run(self, operation):
            assert operation == 'status', 'No content access is allowed in this fixture'
            self.calls.append(operation)
            if self.delay:
                self.entered.set()
                await self.release.wait()
            if self.mode == 'error':
                raise TimeoutError()
            return {'missing': {'available': False, 'status': 'unavailable', 'code': 'backend_not_installed'},
                'permission': {'available': False, 'status': 'permission_required', 'permission': 'required'},
                'unsupported': {'available': False, 'status': 'unavailable', 'code': 'unsupported'},
                'ready': {'available': True, 'status': 'ready', 'permission': 'granted'}}[self.mode]
    service.voice_visual.native = native = Native()
    service.voice_service = SimpleNamespace(call=None)
    # Match the synthetic native availability in screenshots. The interpreter
    # path stays real; only optional package installation is a fixture fact.
    from amplifier_web import desktop_readiness
    real_environment = desktop_readiness.environment
    def fixture_environment():
        result = real_environment()
        if native.mode in {'ready', 'permission'}:
            result['computerUsePackage'] = {'status': 'installed', 'version': 'synthetic-fixture', 'importVerified': False}
        return result
    desktop_readiness.environment = fixture_environment

    async def scenario(request):
        data = await request.json()
        native.mode = data.get('native', native.mode)
        runtime.mode = data.get('worker', runtime.mode)
        if data.get('delay'):
            native.delay = True; native.entered.clear(); native.release.clear()
        if data.get('release'):
            native.delay = False; native.release.set()
        if data.get('source'):
            sid = data['sessionId']; call = SimpleNamespace(id='fixture-call', session_id=sid, closed=False, closing=False, client_id=service.clients.current.get())
            service.voice_service.call = call
            await service.set_voice_status({'id': call.id, 'sessionId': sid, 'status': 'connected'})
            await service.voice_visual.grant_source({'sessionId': sid, 'callId': call.id, 'source': {'kind': 'browser', 'label': data['source']}})
        if data.get('end'):
            service.voice_service.call.closed = True
            await service.set_voice_status({'status': 'ended'})
        return web.json_response({'ok': True})

    async def inspect(request):
        return web.json_response({'nativeCalls': native.calls, 'runtimeCalls': runtime.calls, 'started': runtime.started,
            'sent': runtime.sent, 'delayEntered': native.entered.is_set(), 'grant': service.voice_visual.grant})
    async def agent(request):
        data = await request.json()
        return web.json_response(await service.app_bridge('dispatch', data['payload'], data['caller']))
    app.router.add_post('/fixture/scenario', scenario)
    app.router.add_get('/fixture', inspect)
    app.router.add_post('/fixture/agent', agent)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='desktop-readiness-') as home:
        asyncio.run(main(Path(home)))
