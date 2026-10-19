"""Isolated responsive navigation fixture, provider-free and dynamic-port safe."""
import asyncio
import os
from pathlib import Path
import tempfile
from aiohttp import web
import chat_ui_server


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-mobile-navigation-') as tmp:
        app = await chat_ui_server.fixture.main(Path(tmp))
        # Exercise real finite root-proof publication. Only an explicit browser
        # "Open latest work" request may fail/reopen; fixture state publication
        # and releasing the gate never request a read or override readiness.
        service = app['service']
        windows = service.event_log_view.windows
        gate = asyncio.Event()
        gate.set()
        fail_next = set()
        held = set()
        attempts = []
        worker = windows._worker()
        project = worker.project
        async def controlled_project(*args, **kwargs):
            inputs = args[0]
            identity = inputs.get('session', {}).get('id')
            if inputs.get('operation') == 'proof-step':
                attempts.append({'sessionId': identity, 'key': kwargs.get('key'),
                                 'before': inputs.get('before')})
                if identity in fail_next:
                    fail_next.remove(identity)
                    return {'status': 'retry', 'reason': 'fixture-reader-failed'}
                if identity in held:
                    await gate.wait()
            return await project(*args, **kwargs)
        worker.project = controlled_project
        async def work_reader(request):
            body = await request.json()
            identity = body['sessionId']
            if not any(row['id'] == identity for row in service.state['sessions']):
                raise web.HTTPNotFound()
            if body['operation'] == 'retry':
                gate.clear()
                held.add(identity)
                fail_next.add(identity)
                windows.invalidate({identity})
            elif body['operation'] == 'release':
                held.discard(identity)
                gate.set()
            elif body['operation'] == 'status':
                pass
            else:
                raise web.HTTPBadRequest()
            return web.json_response({'accepted': True, 'rootProof': windows._proof_state(identity),
                                      'attempts': [row for row in attempts if row['sessionId'] == identity]})
        app.router.add_post('/api/fixture/work-reader', work_reader)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', int(os.environ.get('AMPLIFIER_TEST_PORT', '0')))
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(f'http://127.0.0.1:{port}', flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
