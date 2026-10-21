"""Production global storage cleanup settings; synthetic runtime, no provider calls."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.storage_cleanup import policy, read
from amplifier_web.runtime_qualification import installed_graph


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def start(self, session, emit, **kwargs):
        self.calls.append({'operation': 'start', 'sessionId': session['id']})
        await super().start(session, emit, **kwargs)

    async def stop(self, sid, **kwargs):
        self.calls.append({'operation': 'stop', 'sessionId': sid})
        await super().stop(sid, **kwargs)


fixture.Runtime = Runtime


async def main(home):
    # Bounded retention subprocesses must load this exact source candidate too.
    os.environ['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    app = await fixture.main(home)
    service = app['service']

    def authorized(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    # Actual owner/qualification/filesystem path with small synthetic installed
    # distributions. No qualification or reference guard is replaced.
    generation = 'a'*32
    project = home/'runtime'/('q-'+'b'*32)
    dist = project/'.venv/lib/python3.13/site-packages/cleanup_fixture-1.0.dist-info'
    dist.mkdir(parents=True)
    (dist/'METADATA').write_text('Name: cleanup-fixture\nVersion: 1.0\n')
    (project/'sample-code.bin').write_bytes(b'x'*4096)
    release = home/'updates/releases'/generation
    release.mkdir(parents=True)
    (release/'validated.json').write_text(json.dumps({'hostVersion':'0.0.fixture','sources':1,'generationSchema':1}))
    (release/'runtime-project.json').write_text(json.dumps({'project':project.name}))
    (release/'runtime-installed.json').write_text(json.dumps(installed_graph(project)))
    preserved = [home/'workspace/project/private-notes.txt', home/'workspace-resources/owned/fixture/source.tar',home/'artifacts/results.txt']
    for path in preserved:
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Saved user work')

    async def observe(request):
        authorized(request)
        return web.json_response({'policy': policy(service), 'runtimeCalls': service.runtime.calls,
            'lastCleanup':read(service)['lastCleanup'], 'unusedExists':release.exists(),
            'savedWorkIntact':all(path.read_text()=='Saved user work' for path in preserved),
            'writes': [json.loads(row[0]) for row in service.db.execute(
                "SELECT receipt FROM commands WHERE json_extract(receipt,'$.commandAction')='storage.cleanup.save'")],
            'selectedSessionId': service.state.get('selectedSessionId')})

    async def concurrent_save(request):
        authorized(request)
        payload = await request.json()
        current = policy(service)
        args = {'expectedRevision':current['revision'], 'enabled':True,
                'keepDays':payload.get('keepDays',2), 'maxUnusedBytes':0}
        return web.json_response(await service.dispatch('storage.cleanup.save',args,
            command_id='fixture-concurrent-save-'+str(current['revision'])))

    app.router.add_get('/fixture/storage-cleanup-observations',observe)
    app.router.add_post('/fixture/storage-cleanup-concurrent-save',concurrent_save)
    return app


async def serve():
    with tempfile.TemporaryDirectory(prefix='amplifier-storage-cleanup-ui-') as tmp:
        app = await main(Path(tmp))
        runner = web.AppRunner(app)
        await runner.setup()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
        for signum in signals:
            loop.add_signal_handler(signum, stopped.set)
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            base = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
            app['allowed_origins'] = app['allowed_origins'] | {base}
            print(json.dumps({'fixture': 'storage-cleanup-ui', 'pid': os.getpid(), 'base': base}), flush=True)
            await stopped.wait()
        finally:
            await runner.cleanup()
            for signum in signals:
                loop.remove_signal_handler(signum)


if __name__ == '__main__':
    asyncio.run(serve())
