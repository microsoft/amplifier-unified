"""Owned synthetic decisions; real persisted questions, grants, host and UI."""
import asyncio
import json
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
        self.decisions = []

    async def send(self, session, text, input_id, emit):
        self.sent.append({'sessionId': session['id'], 'inputId': input_id, 'text': text})
        await asyncio.sleep(.05)
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})

    async def approval(self, sid, identity, decision):
        self.decisions.append({'sessionId': sid, 'id': identity, 'decision': decision})


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    runtime = Runtime()
    service.runtime = app['runtime'] = runtime
    session = service._session()
    sid = session['id']
    await service.dispatch('session.rename', {'id': sid, 'title': 'Decision audit'})

    async def ask(prompt, required=False, **extra):
        return (await service.app_bridge('dispatch', {'action': 'question.create', 'args': {
            'prompt': prompt, 'required': required, 'dependency': 'Prepare a synthetic report', **extra}}, sid))['result']

    required = await ask('Choose report detail', True, options=[{'id': 'full', 'label': 'Full report'}])
    optional = await ask('Choose report title')
    skipped = await ask('Choose optional subtitle')
    unknown = await ask('Previously saved answer')
    # A retained receipt with unknown delivery is fixture data, not a submitted
    # answer. It must never be mistaken for a fresh actionable question.
    unknown.update(status='answered', revision=2,
        answer={'text': 'Retained uncertain answer', 'provenance': {'via': 'chat'}},
        delivery={'status': 'unknown', 'inputId': 'retained-not-replayed', 'updatedAt': time.time()})
    service.questions.store.put(unknown)
    for identity in ['native-allow', 'native-deny', 'native-expired']:
        await service.on_runtime_event('approval.requested', {'sessionId': sid, 'id': identity,
            'title': identity, 'prompt': 'Permit only the named synthetic operation?'})
    expired = next(row for row in session['approvals'] if row['id'] == 'native-expired')
    expired.update(status='expired', resolutionReason='host_restarted', detail='The prior native waiter ended; nothing was replayed.')
    grant = (await service.dispatch('capability.request', {'sessionId': sid, 'effects': ['modules'],
        'scopes': ['project'], 'reason': 'Review a bounded synthetic configuration request'},
        origin='ui', command_id='fixture-grant-request'))['result']
    service._save()
    service._publish_changes(sessions={sid})

    async def check(request):
        return web.json_response({'sent': runtime.sent, 'decisions': runtime.decisions,
            'questions': service.questions.store.all(sid), 'approvals': service._session(sid)['approvals'],
            'grants': service.configuration_authority.snapshot(service._session(sid)),
            'selectedSessionId': service.state['selectedSessionId']})

    app.router.add_get('/fixture/check', check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid, 'required': required['id'],
        'optional': optional['id'], 'skipped': skipped['id'], 'unknown': unknown['id'], 'grant': grant['id']}), flush=True)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-standard-decisions-') as directory:
        asyncio.run(main(Path(directory)))
