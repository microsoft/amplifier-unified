"""Actual host resources/SSE with synthetic question transitions; no provider."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def main(home):
    from aiohttp import web
    from configured_browser import seed_connection
    from empty_host_ui_server import Runtime
    from amplifier_web.server import create_app
    from amplifier_web.shell_query_owner import PendingQuery

    seed_connection(home / 'native')
    folder = home / 'workspace'
    folder.mkdir()
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=str(folder), runtime=runtime,
        voice=False, background_updates=False, background_work=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    runtime.service = service
    rows = []
    for index in range(26):
        result = await service.dispatch('session.create', {'workspace': str(folder),
            'title': 'Question owner' if index == 12 else f'Reading row {index:02}', 'bundle': 'anchors'})
        row = service._session(result['result']['sessionId'])
        row.update(historyManaged=False, historyLoaded=True, deferRuntimeUntilInteraction=True,
                   messages=[{'id': row['id'] + '-saved', 'role': 'user', 'text': 'Synthetic saved history'}])
        rows.append(row)
    owner, peer = rows[12], rows[13]
    for row in rows:
        await service.dispatch('session.pin', {'id': row['id'], 'pinned': True})
    await service.dispatch('session.select', {'id': owner['id']})
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done', 'navPinned': True,
        'navChatScope': 'all', 'navRecentLimit': 40, 'panel': None}})
    histories = {row['id']: hashlib.sha256(json.dumps(row['messages'], sort_keys=True).encode()).hexdigest()
                 for row in rows}
    held = False
    for name in ('chats', 'pinned_navigation'):
        original = getattr(service.projections, name)
        def projection(*args, _original=original, **kwargs):
            if held:
                raise PendingQuery()
            return _original(*args, **kwargs)
        setattr(service.projections, name, projection)

    def configure(mode):
        nonlocal held
        held = mode != 'fresh'
        owner.update(status='working', approvals=[], error=None,
            questions=[{'id': 'fixture-question', 'revision': 1, 'prompt': 'Choose the fixture route',
                        'required': True, 'status': 'pending', 'options': [], 'allowFreeText': True,
                        'dependency': 'Choose a route', 'createdAt': 1, 'updatedAt': 1,
                        'artifacts': [], 'questionIds': []}])
        if mode not in {'pending', 'initial'}:
            owner['questions'][0].update(status='answered', answer={'text': 'Use the fixture route', 'questionRevision': 1}, delivery={'status':
                mode if mode in {'unknown', 'rejected'} else 'accepted'})
        if mode == 'multiple':
            owner['questions'].append({'id': 'fixture-second', 'revision': 1,
                'prompt': 'Another choice', 'required': True, 'status': 'pending', 'options': [],
                'allowFreeText': True, 'dependency': 'Choose another route', 'createdAt': 1,
                'updatedAt': 1, 'artifacts': [], 'questionIds': []})
        service._publish_changes(sessions={owner['id']})

    def auth(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def change(request):
        auth(request)
        payload = await request.json()
        mode = payload.get('mode')
        if mode not in {'hold', 'answered', 'pending', 'unknown', 'rejected', 'multiple', 'fresh'}:
            raise web.HTTPBadRequest()
        async with service.lock:
            configure('pending' if mode == 'hold' else mode)
        return web.json_response({'mode': mode, 'held': held})

    async def info(request):
        auth(request)
        return web.json_response({'owner': owner['id'], 'peer': peer['id'], 'held': held,
            'runtimeStarts': runtime.started, 'runtimeSends': runtime.sent,
            'questions': owner['questions'], 'status': owner['status'],
            'historyUnchanged': all(hashlib.sha256(json.dumps(row['messages'], sort_keys=True).encode()).hexdigest()
                                    == histories[row['id']] for row in rows)})

    configure('initial')
    held = False
    app.router.add_post('/fixture/activity', change)
    app.router.add_get('/fixture/activity', info)
    runner = web.AppRunner(app)
    await runner.setup()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        app['allowed_origins'] |= {url}
        print(json.dumps({'url': url, 'owner': owner['id'], 'peer': peer['id']}), flush=True)
        await stop.wait()
    finally:
        await runner.cleanup()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-retained-activity-') as directory:
        home = Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(home / 'native'), AMPLIFIER_WEB_HOME=str(home / 'app'),
            AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'),
            AMPLIFIER_UNIFIED_IMPORT_HOME=str(home / 'legacy'))
        asyncio.run(main(home))
