"""Actual voice Settings/storage; synthetic keys and sample renderer, no provider."""
import asyncio
import json
import os
import signal
import socket
import tempfile
from pathlib import Path

from aiohttp import web
from settings_ui_server import main as settings_app


async def main():
    with tempfile.TemporaryDirectory(prefix='readiness-voice-settings-') as temp:
        os.environ['OPENAI_API_KEY'] = 'synthetic-host-voice-key'
        os.environ.pop('AMPLIFIER_VOICE_API_KEY', None)
        app = await settings_app(Path(temp))
        from amplifier_web.voice import VoiceService
        from amplifier_web import voice_settings
        service = app['service']
        service.voice_service = VoiceService(service)
        service.state['settings'].update(preferredVoice='gpt-live-1', voiceName='retired-fixture-voice')
        samples = []

        async def sample(manager, model, voice):
            samples.append({'model': model, 'voice': voice})
            return voice_settings.wav_audio(bytes(4800))

        voice_settings.render_sample = sample

        async def inspect(request):
            return web.json_response({'samples': samples, 'configuration': voice_settings.configuration(service.voice_service),
                'stateContainsSyntheticKey': 'synthetic-private-voice-key' in json.dumps(service.state),
                'storedKeyMatches': os.environ.get('AMPLIFIER_VOICE_API_KEY') == 'synthetic-private-voice-key'})

        async def unsupported(request):
            service.state['settings'].update(preferredVoice='retired-fixture-model', voiceName='retired-fixture-voice')
            service._publish_changes(globals={'settings'})
            return web.json_response({'ok': True})

        app.router.add_get('/fixture/voice', inspect)
        app.router.add_post('/fixture/unsupported-model', unsupported)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.SockSite(runner, sock)
        await site.start()
        print(json.dumps({'port': port}), flush=True)
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        try:
            await stop.wait()
        finally:
            await service.voice_service.close()
            await runner.cleanup()
            sock.close()


if __name__ == '__main__':
    asyncio.run(main())
