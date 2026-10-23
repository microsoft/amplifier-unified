"""Real host/actions/private drafts; controlled synthetic runtime, never a provider.

input.delivered precedes the awaited send RPC return in the real runtime event
contract. Hold only that return, not a fabricated state/projection revision.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.runtime import RuntimeOperationPending, RuntimeStartupError, normalize_event

TOKEN = 'fixture-browser-control-token'


class ConsumptionRuntime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.next_mode = 'held'
        self.sends = {}

    async def send(self, session, text, input_id, emit):
        if input_id in self.sends:
            raise AssertionError('Fixture observed an unexpected runtime replay')
        mode, self.next_mode = self.next_mode, 'held'
        row = {'sessionId': session['id'], 'inputId': input_id, 'mode': mode,
               'text': text, 'acceptedEvent': False, 'returned': False,
               'returnGate': asyncio.Event(), 'emit': emit}
        self.sends[input_id] = row
        # Read the committed command before runtime.status can publish anything.
        row['initial'] = self.snapshot(input_id)
        if mode == 'failed':
            row['returned'] = True
            raise RuntimeStartupError('Synthetic preparation rejection before input admission')
        if mode == 'unknown':
            row['returned'] = True
            raise RuntimeOperationPending('send')
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'working'})
        try:
            await asyncio.wait_for(row['returnGate'].wait(), 35)
        except TimeoutError:
            row['timedOut'] = True
            raise
        row['returned'] = True
        row['final'] = self.snapshot(input_id)
        return {'accepted': True}

    async def accept(self, input_id):
        row = self.sends[input_id]
        if row['mode'] != 'held' or row['acceptedEvent']:
            raise ValueError('Acceptance must be emitted once for a held send')
        # Use the production event normalizer and service event handler. No
        # mutation of client drafts, messages, revision, or projection caches.
        event = normalize_event({'type': 'input.delivered', 'input_id': input_id},
                                row['sessionId'], input_id)
        await row['emit'](*event)
        row['acceptedEvent'] = True
        row['accepted'] = self.snapshot(input_id)

    async def finish(self, input_id):
        row = self.sends[input_id]
        if not row['acceptedEvent']:
            raise ValueError('Finish requires positive input evidence')
        await row['emit']('assistant.message', {
            'sessionId': row['sessionId'], 'inputId': input_id,
            'text': 'Synthetic response complete.'})
        await row['emit'](*normalize_event({'type': 'session.idle'}, row['sessionId'], input_id))
        row['returnGate'].set()

    async def close(self):
        for row in self.sends.values():
            row['returnGate'].set()


fixture.Runtime = ConsumptionRuntime


async def main(home):
    app = await fixture.main(home)
    service, runtime = app['service'], app['service'].runtime

    def snapshot(input_id):
        saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()
        return {'receipt': json.loads(saved[0]) if saved else None,
                'stateRevision': service._state['revision']}

    runtime.snapshot = snapshot
    if os.environ.get('AMPLIFIER_TEST_PRECOMMIT_NOTIFY') == '1':
        # Explicit negative control: restore the old notification timing, not
        # message/attachment/revision data. The unchanged browser oracle must fail.
        invalidate = service.event_log_view.invalidate

        def precommit_invalidate(session_ids=None, *, notify=True):
            return invalidate(session_ids, notify=True)

        service.event_log_view.invalidate = precommit_invalidate

    def authorized(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()

    async def observe(request):
        authorized(request)
        client_id = request.query.get('clientId')
        async with service.lock:
            record = service.clients.records.get(client_id)
            if record is None:
                raise web.HTTPNotFound(text='Synthetic client not found')
            rows = [{key: copy.deepcopy(row.get(key)) for key in
                     ('sessionId', 'inputId', 'mode', 'acceptedEvent', 'returned', 'timedOut',
                      'initial', 'accepted', 'final')}
                    for row in runtime.sends.values()]
            return web.json_response({
                'revision': service._state['revision'], 'hostInstanceId': service.instance_id,
                'clientId': client_id, 'selectedSessionId': record.get('selectedSessionId'),
                'drafts': copy.deepcopy(record.get('drafts', {})),
                'attachments': copy.deepcopy(record.get('attachments', {})),
                'sends': rows,
                'workStatus': copy.deepcopy(service.event_log_view.windows.status),
                'sessions': [{key: copy.deepcopy(row.get(key)) for key in ('id', 'messages', 'status')}
                             for row in service._state['sessions']],
            })

    async def control(request):
        authorized(request)
        body = await request.json()
        op = body.get('op')
        if op == 'next-mode' and body.get('mode') in {'held', 'failed', 'unknown'}:
            runtime.next_mode = body['mode']
        elif op == 'read-work':
            await asyncio.wait_for(service.event_log_view.windows.read(body['sessionId']), 5)
        elif op == 'accept':
            await asyncio.wait_for(runtime.accept(body['inputId']), 5)
        elif op == 'finish':
            await asyncio.wait_for(runtime.finish(body['inputId']), 5)
        elif op == 'release-all':
            await runtime.close()
        else:
            raise web.HTTPBadRequest(text='Unknown fixed fixture control')
        return web.json_response({'ok': True})

    app.router.add_get('/fixture/attachment-consumption', observe)
    app.router.add_post('/fixture/attachment-consumption', control)
    async def browser_session(request):
        # Chromium's native download does not carry Playwright renderer headers.
        # Use the existing real session-cookie contract, never bypass attachment auth.
        authorized(request)
        from amplifier_web.auth import SESSION_COOKIE, new_session
        response = web.json_response({'issued': True})
        response.set_cookie(SESSION_COOKIE, new_session(app['session_secret']),
                            httponly=True, samesite='Strict', path='/')
        return response
    app.router.add_post('/fixture/browser-session', browser_session)
    return app


async def serve():
    # Temporary app/native state exists only inside the approved isolated runner.
    with tempfile.TemporaryDirectory(prefix='attachment-consumption-') as tmp:
        app = await main(Path(tmp))
        runner = web.AppRunner(app, shutdown_timeout=5)
        await runner.setup()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stopped.set)
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            app['allowed_origins'] = app['allowed_origins'] | {base}
            import amplifier_web
            package = Path(amplifier_web.__file__).parent
            build = package / 'static' / 'build.json'
            print(json.dumps({'fixture': 'attachment-consumption', 'pid': os.getpid(),
                              'base': base, 'packageRoot': str(package),
                              'buildSha256': hashlib.sha256(build.read_bytes()).hexdigest()}), flush=True)
            await stopped.wait()
        finally:
            try:
                await app['service'].runtime.close()
                await asyncio.wait_for(runner.cleanup(), 8)
            finally:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)


if __name__ == '__main__':
    asyncio.run(serve())
