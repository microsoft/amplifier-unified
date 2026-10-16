"""Synthetic catalog with real image-setting persistence and operation receipts."""
import asyncio
from pathlib import Path
import tempfile
import signal

from aiohttp import web
import settings_ui_server as base

original_probe = base.probe
observations = {'probes': [], 'sends': [], 'controls': []}
original_control = base.Runtime.control


async def send(self, session, text, input_id, emit):
    observations['sends'].append({'sessionId': session['id'], 'inputId': input_id})
    raise AssertionError('Inspecting image preferences must not submit a model request')


async def control(self, sid, operation, args):
    observations['controls'].append(operation)
    return await original_control(self, sid, operation, args)


base.Runtime.send = send
base.Runtime.control = control


async def probe(self, action, args, workspace):
    observations['probes'].append(action)
    if action != 'providers.imageModels':
        return await original_probe(self, action, args, workspace)
    await asyncio.sleep(.2)
    if args["id"] == "two":
        return {"imageModelsProviderId": args["id"], "imageModelsSupported": False,
                "imageModels": [], "providerMetadata": {"module": "provider-openai"}}
    return {'imageModelsProviderId': args['id'], 'imageModelsSupported': True,
            'imageModels': [{'id': 'image-fixture', 'display_name': 'Fixture image model'}],
            'providerMetadata': {'module': 'provider-gemini' if args['id']=='three' else 'provider-openai', 'imageGeneration': {
                'schemaVersion': 1, 'configKey': 'image_generation', 'automaticModel': 'auto'}}}


base.SetupManager.probe = probe


async def serve():
    with tempfile.TemporaryDirectory(prefix='image-setup-ui-') as folder:
        app = await base.main(Path(folder))
        settings_path = Path(folder) / 'native' / 'settings.yaml'
        settings = base.yaml.safe_load(settings_path.read_text())
        settings['config']['providers'][1]['config']['default_model'] = 'other-fixture-model'
        settings['config']['providers'][2]['module'] = 'provider-gemini'
        settings['config']['providers'].append({'id':'no-key','module':'provider-gemini','config':{'default_model':'fixture-model'}})
        base.write_private(settings_path, base.yaml.safe_dump(settings))
        async def effects(request):
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            return web.json_response(observations)
        app.router.add_get('/fixture/image-effects', effects)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(f'http://127.0.0.1:{port}', flush=True)
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        try:
            await stop.wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(serve())
