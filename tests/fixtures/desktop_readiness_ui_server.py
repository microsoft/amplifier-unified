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

    # Exercise the real shared updater with synthetic package/process boundaries.
    # No fixture path can install a package, spawn a restart or kill this host.
    from amplifier_web import app_features, app_updates, deployment_service
    from amplifier_web.app_feature_probe import DEPENDENCY_PROBE
    from amplifier_web.auth import data_identity
    manager = service.update_manager
    feature = SimpleNamespace(supported=False, fail=False, extras=[], installed=False, calls=[],
        entered=asyncio.Event(), release=asyncio.Event())
    host_app = {'name':'amplifier-unified', 'version':app_updates.__version__, 'url':app_updates.SOURCE, 'revision':'a'*40}
    baseline = [{'name':'httpx', 'version':'0.28.1'}]
    addition = {'name':'amplifier-module-tool-computer-use', 'version':'0.1.0',
        'url':'https://github.com/microsoft/amplifier-bundle-computer-use', 'revision':'b'*40,
        'subdirectory':'modules/tool-computer-use'}
    graph = sorted([host_app, *baseline, addition], key=lambda row:row['name'])
    real_application = app_features.running_application
    app_features.running_application = lambda manager: copy.deepcopy(host_app) if feature.supported else real_application(manager)
    app_updates.installed_extras = lambda: list(feature.extras)
    app_updates.components.installed_graph = lambda: copy.deepcopy(sorted([*baseline, addition], key=lambda row:row['name']) if feature.installed else baseline)
    async def read_graph(*args): return copy.deepcopy(graph)
    app_updates.components.read_graph = read_graph
    async def installed_target():
        return '/synthetic/uv', '/synthetic/launcher', home/'synthetic/python', {'version':app_updates.__version__, 'source':app_updates.SOURCE}
    app_updates.installed_target = installed_target
    real_which = app_updates.shutil.which
    app_updates.shutil.which = lambda name: '/synthetic/uv' if name=='uv' else real_which(name)
    async def process(*args, **kwargs):
        feature.calls.append(list(map(str,args)))
        if 'install' in args:
            root = kwargs.get('env',{}).get('UV_TOOL_DIR')
            if root:
                feature.entered.set(); await feature.release.wait()
                if feature.fail: raise ValueError('Synthetic dependency qualification failed.')
                python = Path(root)/'amplifier-unified/bin/python'
                python.parent.mkdir(parents=True,exist_ok=True);python.touch()
            else:
                feature.installed=True;feature.extras=['native-desktop']
            return ''
        if app_updates.PROBE in args: return app_updates.__version__
        if DEPENDENCY_PROBE in args: return 'Feature dependency metadata is compatible.'
        raise AssertionError('No real process may run in the feature fixture')
    app_updates.process = process
    deployment_service.current_process_is_unit_managed = lambda home: True
    async def restart(manager): feature.calls.append(['synthetic-restart-request'])
    app_updates.request_managed_restart = restart

    async def scenario(request):
        data = await request.json()
        if 'featureSupported' in data:
            feature.supported=data['featureSupported']
            manager.running_identity.update(version=host_app['version'],revision=host_app['revision'])
        if 'featureFail' in data:
            feature.fail=data['featureFail'];feature.entered.clear();feature.release.clear()
        if data.get('featureRelease'): feature.release.set()
        if data.get('featureConfirm'):
            manager.running_identity['instanceId']='synthetic-new-host'
            health={**manager.running_identity,'ok':True,'app':'amplifier-unified','dataIdentity':data_identity(manager.home)}
            assert await manager.confirm_readiness(health)
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
            'sent': runtime.sent, 'delayEntered': native.entered.is_set(), 'grant': service.voice_visual.grant,
            'featureCalls':feature.calls,'featureEntered':feature.entered.is_set()})
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
