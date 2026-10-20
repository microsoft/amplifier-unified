"""Production global model-capacity settings; synthetic runtime, no provider calls."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.model_capacity_settings import policy


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
    app = await fixture.main(home)
    service = app['service']

    def authorized(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def observe(request):
        authorized(request)
        return web.json_response({'policy': policy(service), 'runtimeCalls': service.runtime.calls,
            'waiters': len(getattr(service, '_model_admission_waiters', [])),
            'writes': [json.loads(row[0]) for row in service.db.execute(
                "SELECT receipt FROM commands WHERE receipt LIKE '%modelCapacity.set%'")],
            'selectedSessionId': service.state.get('selectedSessionId')})

    async def concurrent_save(request):
        authorized(request)
        payload = await request.json()
        current = policy(service)
        args = {'expectedRevision': current['revision'],
                'maxWaitingCalls': payload.get('maxWaitingCalls', current['maxWaitingCalls'] + 1),
                'waitTimeoutSeconds': payload.get('waitTimeoutSeconds', current['waitTimeoutSeconds'])}
        return web.json_response(await service.dispatch('modelCapacity.set', args,
            command_id='fixture-concurrent-save-' + str(current['revision'])))

    app.router.add_get('/fixture/model-capacity-observations', observe)
    app.router.add_post('/fixture/model-capacity-concurrent-save', concurrent_save)
    return app


async def serve():
    with tempfile.TemporaryDirectory(prefix='amplifier-model-capacity-ui-') as tmp:
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
            print(json.dumps({'fixture': 'model-capacity-ui', 'pid': os.getpid(), 'base': base}), flush=True)
            await stopped.wait()
        finally:
            await runner.cleanup()
            for signum in signals:
                loop.remove_signal_handler(signum)


if __name__ == '__main__':
    asyncio.run(serve())
