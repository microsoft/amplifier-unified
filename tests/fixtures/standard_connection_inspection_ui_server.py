"""Exact synthetic connection probes and capability metadata; no provider network."""
import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path
from aiohttp import web


async def main(home):
    calls = []
    defaults_gate = asyncio.Event()
    if '--composer' in sys.argv:
        import empty_host_ui_server as base
        os.environ.update(AMPLIFIER_HOME=str(home/'shared-settings'), AMPLIFIER_WEB_HOME=str(home/'app'), AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'))
        from configured_browser import seed_connection
        from amplifier_web.server import create_app
        seed_connection(home/'shared-settings')
        workspace=home/'workspace';workspace.mkdir()
        from amplifier_web.setup import SetupManager
        from amplifier_web import draft_defaults
        rows = [
            {'id': 'test-provider', 'module': 'provider-test', 'info': {'display_name': 'Effort provider', 'defaults': {'model': 'first'}}, 'configSchema': {'fields': [{'id': 'reasoning_effort', 'choices': ['low', 'high']}]}},
            {'id': 'no-effort', 'module': 'provider-test', 'info': {'display_name': 'Simple provider', 'defaults': {'model': 'simple'}}, 'configSchema': {'fields': []}},
        ]
        async def defaults(*args, **kwargs):
            calls.append({'action':'configuration.defaults'})
            await defaults_gate.wait()
            return {'bundle': 'work', 'effective': {'instance': 'test-provider', 'model': 'first'}, 'providers': rows}
        async def catalog(self, action, args, workspace):
            provider = next(row for row in rows if row['id'] == args['id'])
            calls.append({'action': action, 'id': args['id']})
            return {'modelsProviderId': args['id'], 'models': [{'id': provider['info']['defaults']['model']}], 'providerMetadata': provider}
        draft_defaults.resolve_defaults = defaults
        SetupManager.cached_probe = catalog
        SetupManager.provider_rows=lambda self, workspace:[{'id':row['id'],'module':'provider-test','config':{'model':row['info']['defaults']['model']},'enabled':True}for row in rows]
        runtime=base.Runtime()
        app=await create_app(home/'app',workspace=str(workspace),runtime=runtime,voice=False,background_updates=False)
        app['control_token']='fixture-browser-control-token';runtime.service=app['service']
        await app['service'].dispatch('view.update', {'patch': {'setupJourney': 'done'}})
    else:
        import settings_ui_server as base
        async def probe(self, action, args, workspace):
            calls.append({'action': action, 'id': args.get('id')})
            await asyncio.sleep(.05)
            if action == 'providers.test':
                return {'test': {'providerId': args['id'], 'reachable': args['id'] == 'one', 'modelCount': 7 if args['id'] == 'one' else 0, 'method': 'provider.list_models', 'error': 'Second instance refused' if args['id'] == 'two' else None}}
            return {'modelsProviderId': args.get('id'), 'models': [{'id': 'fixture-model'}], 'providerMetadata': {'module': 'provider-openai', 'configSchema': {'fields': []}}}
        base.SetupManager.probe = probe
        app = await base.main(home)
        await app['service'].dispatch('view.update', {'patch': {'panel': None, 'setupJourney': 'done'}})
    service = app['service']
    async def inspect(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        return web.json_response({'calls': calls, 'test': service.state.get('setup', {}).get('test'), 'sessionIds': [row['id'] for row in service._state['sessions']], 'sent': getattr(service.runtime, 'sent', []), 'started': getattr(service.runtime, 'started', [])})
    app.router.add_get('/fixture/inspection', inspect)
    async def release_defaults(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        defaults_gate.set()
        return web.json_response({'released':True})
    app.router.add_post('/fixture/release-defaults', release_defaults)
    return app


async def serve():
    with tempfile.TemporaryDirectory(prefix='standard-connection-inspection-') as tmp:
        app = await main(Path(tmp));runner = web.AppRunner(app);await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0);await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        done = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, done.set)
        print(json.dumps({'url': url}), flush=True)
        try:await done.wait()
        finally:await runner.cleanup()


if __name__ == '__main__':asyncio.run(serve())
