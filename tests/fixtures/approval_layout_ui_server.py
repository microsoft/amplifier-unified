"""Pending approval layout on the real host/UI, with synthetic data only."""
import asyncio
import copy
import json
from pathlib import Path
import signal
import tempfile

from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.decisions = []

    async def approval(self, sid, identity, decision):
        self.decisions.append({'sessionId': sid, 'id': identity, 'decision': decision})


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    runtime = Runtime()
    service.runtime = app['runtime'] = runtime
    session = service._session()
    sid = session['id']
    await service.dispatch('session.rename', {'id': sid, 'title': 'Approval layout'})
    service._message(session, 'user', 'Keep approval requests within the chat column.')
    service._message(session, 'assistant', 'Review the request below. Resizing must not answer it.')
    token = 'synthetic-identifier-' + 'x' * 240
    details = {'scope': 'session', 'path': '/fixture/' + token,
               'changes': [f'Review item {i}: {token}' for i in range(12)], 'end': 'DETAILS END'}
    for identity, title, value in [
        ('layout-object', 'Review ' + token, details),
        ('layout-string', 'Review a second operation', json.dumps(details, indent=2)),
    ]:
        await service.on_runtime_event('approval.requested', {
            'sessionId': sid, 'id': identity, 'title': title,
            'prompt': ('Only the named synthetic operation is requested. ' * 6) + token,
            'details': value,
        })
    grant = (await service.dispatch('capability.request', {
        'sessionId': sid, 'effects': ['modules'], 'scopes': ['session'],
        'reason': 'Review a bounded synthetic configuration request',
    }, origin='ui', command_id='fixture-layout-grant'))['result']
    await service.dispatch('view.update', {'patch': {'navPinned': True, 'navWidth': 240}})
    service._save()
    service._publish_changes(sessions={sid})

    async def check(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            return web.json_response({
                'decisions': copy.deepcopy(runtime.decisions),
                'approvals': copy.deepcopy(service._session(sid)['approvals']),
                'grants': service.configuration_authority.snapshot(service._session(sid)),
            })

    app.router.add_get('/fixture/check', check)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url, 'sessionId': sid, 'grant': grant['id']}), flush=True)
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-approval-layout-') as directory:
        asyncio.run(main(Path(directory)))
