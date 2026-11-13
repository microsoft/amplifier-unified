"""Real HTTP/SSE delivery recovery with isolated data and a synthetic runtime."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
import time
from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.checked = []
        self.retried = []
        self.known = set()
        self.accepted = None
        self.status_events = []
        self.captured_sends = []

    async def send(self, session, text, input_id, emit):
        if text.startswith('Captured target '):
            self.captured_sends.append({'sessionId': session['id'], 'inputId': input_id, 'text': text})
            await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
            return {'accepted': True}
        raise RuntimeError('Synthetic lost reply')

    async def delivery(self, session, input_id):
        self.checked.append(input_id)
        return 'accepted' if input_id in self.known else 'unknown'

    async def retry(self, session, text, input_id, emit):
        self.retried.append(input_id)
        if input_id in self.known:
            return {'accepted': True, 'duplicate': True}
        self.known.add(input_id)
        self.sent.append({'inputId': input_id, 'text': text})
        # Retain the real admission callback; fixture controls never manufacture
        # display annotations or accept a browser-supplied session/input binding.
        self.accepted = (session, input_id, emit)
        await emit('assistant.message', {'sessionId': session['id'], 'inputId': input_id, 'text': 'Recovery succeeded: one synthetic submission received.'})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
        return {'accepted': True}


fixture.Runtime = Runtime


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    service.state['updates'].update(items=[], available=0)
    target_a = service._session()['id']
    await service.dispatch('session.create', {})
    target_b = service._session()['id']
    await service.dispatch('session.rename', {'id': target_b, 'title': 'Captured target B'})
    await service.dispatch('session.select', {'id': target_a})
    await service.dispatch('session.rename', {'id': service._session()['id'], 'title': 'Delivery recovery fixture'})
    attachment = await service.dispatch('attachment.add', {'name': 'reference.txt', 'base64': 'UmV0YWluIHRoaXMgYXR0YWNobWVudC4='})
    attachment_id = attachment['state']['sessions'][0]['draftAttachments'][0]['id']
    try:
        await service.dispatch('conversation.send', {'text': 'Recover this synthetic message with its attachment.', 'attachmentIds': [attachment_id]}, command_id='original-fixture-input')
    except RuntimeError:
        pass
    async def inspect(request):
        runtime = service.runtime
        binding = runtime.accepted
        return web.json_response({'sent': runtime.sent, 'checked': runtime.checked, 'retried': runtime.retried,
            'accepted': {'sessionId': binding[0]['id'], 'inputId': binding[1]} if binding else None,
            'statusEvents': runtime.status_events, 'capturedSends': runtime.captured_sends,
            'targetA': target_a, 'targetB': target_b})
    status_lock = asyncio.Lock()
    async def status(request):
        if request.headers.get('Authorization') != f"Bearer {app['control_token']}":
            raise web.HTTPForbidden()
        payload = await request.json()
        steps = ('ambient', 'unrelated', 'started', 'finished')
        if not isinstance(payload, dict) or set(payload) != {'step'} or payload['step'] not in steps:
            raise web.HTTPBadRequest(text='Choose a predefined status step.')
        async with status_lock:
            runtime = service.runtime
            if not runtime.accepted:
                raise web.HTTPConflict(text='No accepted retry binding.')
            if len(runtime.status_events) >= len(steps) or payload['step'] != steps[len(runtime.status_events)]:
                raise web.HTTPConflict(text='Status steps must run once, in order.')
            accepted, input_id, emit = runtime.accepted
            session_id = accepted['id']
            current = service._session(session_id)
            step = payload['step']
            if step == 'ambient':
                kind = 'runtime.status'
                event = {'sessionId': session_id, 'status': 'working', 'detail': 'Unrelated fixture activity'}
            else:
                kind = 'runtime.generation'
                event = {'sessionId': session_id, 'rootSessionId': session_id,
                    'generation_id': 'unrelated-fixture-generation' if step == 'unrelated' else 'accepted-fixture-generation',
                    'observedAt': time.time(), 'interruptionRevision': current.get('interruptionRevision', 0)}
                if step == 'finished':
                    event.update(event='generation.finished', input_ids=[input_id], active_job_ids=[])
                else:
                    event.update(event='generation.started',
                        initial_input_id='unrelated-fixture-input' if step == 'unrelated' else input_id)
            # The accepted retry's emitter is service.on_runtime_event: exercise
            # its real reducer, persistence, projection and SSE publication.
            await emit(kind, event)
            observed = {'step': step, 'kind': kind, 'payload': event}
            runtime.status_events.append(observed)
            return web.json_response(observed)
    app.router.add_get('/fixture', inspect)
    app.router.add_post('/fixture/status', status)
    runner = web.AppRunner(app)
    await runner.setup()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    shutdown_signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
    for signum in shutdown_signals:
        loop.add_signal_handler(signum, stopped.set)
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        app['allowed_origins'] = app['allowed_origins'] | {base}
        print(json.dumps({'fixture': 'message-delivery-ui', 'pid': os.getpid(), 'base': base}), flush=True)
        await stopped.wait()
    finally:
        try:
            await runner.cleanup()
        finally:
            for signum in shutdown_signals:
                loop.remove_signal_handler(signum)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-delivery-ui-') as directory:
        asyncio.run(main(Path(directory)))
