"""Synthetic/public media fixture. Never proof about an original reporter file."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from aiohttp import web
from amplifier_web import auth
from amplifier_web.server import create_app
from test_output_media import gif


class NoModel:
    async def start(self, *args, **kwargs):
        raise AssertionError('Unexpected model startup')

    async def send(self, *args, **kwargs):
        raise AssertionError('Unexpected model input')

    async def control(self, *args, **kwargs):
        raise AssertionError('Unexpected SDK control/probe')

    async def stop(self, *args, **kwargs):
        pass

    async def close(self):
        pass


async def main():
    with tempfile.TemporaryDirectory(prefix='unified-inline-media-') as temporary:
        root = Path(temporary)
        os.environ.update(AMPLIFIER_HOME=str(root / 'native'), AMPLIFIER_WEB_HOME=str(root / 'app'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'))
        auth.authenticate_pam = lambda user, password: (user, password) == ('media-fixture', 'fixture-password')
        app = await create_app(root / 'app', workspace=root, runtime=NoModel(), voice=False,
                               preload_providers=False, background_updates=False)
        service = app['service']
        # Display catalog reads otherwise create an independent SDK probe even
        # when NoModel.start/send never run. Intercept before that producer.
        original_perform = service.management.perform

        async def perform(action, args, command_id=None):
            if action == 'runtime.control' and args.get('operation') == 'configuration.catalog':
                return {'phase': 'ready', 'providers': [], 'models': []}
            return await original_perform(action, args, command_id)

        service.management.perform = perform
        app['control_token'] = 'fixture-inline-media'
        await service.dispatch('session.create', {'title': 'Inline media fixture'})
        sid = service._session()['id']
        await service.on_runtime_event('assistant.message', {
            'sessionId': sid, 'text': 'Saved media belongs inside this assistant article.',
            'generationId': 'fixture-commentary',
        })
        message_id = service._session()['messages'][-1]['id']
        red_blue = gif()
        prefix, suffix = red_blue.rsplit(b'\x44\x01', 1)
        samples = {'gif': prefix + b'\x4c\x01' + suffix}
        for name, variable in [('gif', 'MEDIA319_GIF'), ('flower', 'MEDIA319_FLOWER'), ('large', 'MEDIA319_LARGE_GIF')]:
            if os.environ.get(variable):
                samples[name] = Path(os.environ[variable]).read_bytes()
        for name, data in samples.items():
            (root / name).write_bytes(data)

        async def control(request):
            args = await request.json()
            name = args.get('name', 'gif')
            if name not in samples:
                raise web.HTTPBadRequest(text='Requested verification fixture was not supplied')
            result = await service.dispatch('outputs.attachMedia', {
                'sessionId': sid, 'messageId': message_id, 'title': 'Synthetic/public ' + name,
                'path': name, 'expectedSha256': hashlib.sha256(samples[name]).hexdigest(),
            }, command_id='fixture-attach-' + name)
            return web.json_response(result)

        app.router.add_post('/api/fixture/media', control)
        runner = web.AppRunner(app)
        try:
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            app['allowed_origins'] |= {f'http://127.0.0.1:{port}'}
            print(json.dumps({'url': f'http://127.0.0.1:{port}', 'sessionId': sid, 'messageId': message_id,
                              'samples': list(samples)}), flush=True)
            stopped = asyncio.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
            await stopped.wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())