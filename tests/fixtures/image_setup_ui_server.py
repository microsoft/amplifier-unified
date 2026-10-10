"""Synthetic catalog with real image-setting persistence and operation receipts."""
import asyncio
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as base

original_probe = base.probe


async def probe(self, action, args, workspace):
    if action != 'providers.imageModels':
        return await original_probe(self, action, args, workspace)
    await asyncio.sleep(.2)
    if args["id"] == "two":
        return {"imageModelsProviderId": args["id"], "imageModelsSupported": False,
                "imageModels": [], "providerMetadata": {"module": "provider-openai"}}
    return {'imageModelsProviderId': args['id'], 'imageModelsSupported': True,
            'imageModels': [{'id': 'image-fixture', 'display_name': 'Fixture image model'}],
            'providerMetadata': {'module': 'provider-openai', 'imageGeneration': {
                'schemaVersion': 1, 'configKey': 'image_generation', 'automaticModel': 'auto'}}}


base.SetupManager.probe = probe


async def serve():
    with tempfile.TemporaryDirectory(prefix='image-setup-ui-') as folder:
        app = await base.main(Path(folder))
        settings_path = Path(folder) / 'native' / 'settings.yaml'
        settings = base.yaml.safe_load(settings_path.read_text())
        settings['config']['providers'][1]['config']['default_model'] = 'other-fixture-model'
        base.write_private(settings_path, base.yaml.safe_dump(settings))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(f'http://127.0.0.1:{port}', flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(serve())
