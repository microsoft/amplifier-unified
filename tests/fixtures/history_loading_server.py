"""File-backed saved-chat timing fixture. Synthetic data, no provider calls."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from amplifier_web import auth
from amplifier_web.server import create_app
from amplifier_web.session_files import project_slug


class NoModel:
    def __init__(self): self.calls = []
    async def start(self, *args, **kwargs):
        self.calls.append('start')
        raise AssertionError('Saved history must not start a model')
    async def send(self, *args, **kwargs):
        self.calls.append('send')
        raise AssertionError('Saved history must not send work')
    async def stop(self, *args, **kwargs): pass
    async def close(self): pass


async def main():
    with tempfile.TemporaryDirectory(prefix='unified-history-loading-') as folder:
        root = Path(folder)
        os.environ.update(AMPLIFIER_HOME=str(root/'native'), AMPLIFIER_WEB_HOME=str(root/'app'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'),
                          AMPLIFIER_SESSION_STATE_HOME=str(root/'sessions'))
        paths, cases = {}, []
        for profile in ('local', 'remote'):
            for count in (1000, 10000):
                identity = f'{profile}-{count}'
                directory = root/'native'/'projects'/project_slug(root)/'sessions'/identity
                directory.mkdir(parents=True)
                (directory/'metadata.json').write_text(json.dumps({'session_id': identity,
                    'working_dir': str(root), 'name': identity, 'bundle': 'bundle:anchors'}))
                path = directory/'transcript.jsonl'
                with path.open('w') as stream:
                    for i in range(count):
                        text = f'Saved message {i}\n\n' + ('A synthetic paragraph with **formatting** and ordinary work content.\n\n' * 30)
                        if i == count-1: text += f'\n\nEND-{identity}'
                        stream.write(json.dumps({'role': 'user' if i%2 == 0 else 'assistant', 'content': text})+'\n')
                paths[identity] = path
                cases.append({'identity': identity, 'profile': profile, 'messages': count,
                              'sourceBytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        runtime = NoModel()
        auth.authenticate_pam = lambda u, p: (u, p) == ('history-fixture', 'fixture-password')
        app = await create_app(root/'app', workspace=root, runtime=runtime, voice=False,
                               preload_providers=False, background_updates=False)
        app['control_token'] = 'fixture-history-loading'
        service = app['service']
        if service.history.task:
            service.history.task.cancel()
            await asyncio.gather(service.history.task, return_exceptions=True)
            service.history.task = None
        await service.history.refresh()
        for case in cases:
            row = next(s for s in service.state['sessions'] if s.get('nativeIdentity') == case['identity'])
            case['id'] = row['id']
            assert not row.get('historyLoaded')
        await service.dispatch('session.create', {'title': 'Timing home'})
        home = service.state['selectedSessionId']
        timings = []
        original = service.history.load
        async def measured(identity, **kwargs):
            start = time.perf_counter()
            try: return await original(identity, **kwargs)
            finally: timings.append({'id': identity, 'milliseconds': (time.perf_counter()-start)*1000,
                                     'onlyIfChanged': kwargs.get('only_if_changed', False)})
        service.history.load = measured
        async def metrics(request):
            return web.json_response({'cases': cases, 'home': home, 'loads': timings, 'runtimeCalls': runtime.calls,
                'unchanged': all(hashlib.sha256(paths[c['identity']].read_bytes()).hexdigest() == c['sha256'] for c in cases)})
        app.router.add_get('/api/fixture/history-metrics', metrics)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url}), flush=True)
        try: await asyncio.Event().wait()
        finally: await runner.cleanup()


if __name__ == '__main__': asyncio.run(main())
