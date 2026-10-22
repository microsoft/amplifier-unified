"""Disposable, synthetic reply/reaction acceptance; no model or external service."""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from aiohttp import web
import settings_ui_server as fixture
from chat_ui_server import ChatRuntime

class ReactionRuntime(ChatRuntime):
    def __init__(self):
        super().__init__()
        self.hold = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, *args):
        if self.hold:
            self.entered.set()
            await self.release.wait()
        return await super().send(*args)

    async def close(self):
        self.release.set()
        await super().close()


fixture.Runtime = ReactionRuntime

async def main(home, port):
    app = await fixture.main(home)
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    service = app['service']
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done', 'panel': None}})
    session = service._session()
    for index in range(75):
        service._message(session, 'user' if index % 2 == 0 else 'assistant', f'Saved message {index}: a reference point.')
    service._publish()
    admissions = []
    dispatch = service.dispatch

    async def observe_dispatch(action, args=None, *values, **options):
        if action != 'message.reaction':
            return await dispatch(action, args, *values, **options)
        row = {'args': dict(args or {}), 'arrivedAt': time.time(),
               'generationBlocked': service.runtime.entered.is_set() and not service.runtime.release.is_set()}
        admissions.append(row)
        # Deliberately broken fixture control: the browser check must fail if
        # annotation persistence is put behind conversational execution.
        if os.environ.get('REACTION_TEST_TURN_GATE') and row['generationBlocked']:
            await service.runtime.release.wait()
        result = await dispatch(action, args, *values, **options)
        row['settledAt'] = time.time()
        return result

    service.dispatch = observe_dispatch

    async def control(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        args = await request.json()
        runtime = service.runtime
        if args.get('op') == 'hold':
            runtime.hold = True
            runtime.entered.clear()
            runtime.release.clear()
        elif args.get('op') == 'release':
            runtime.hold = False
            runtime.release.set()
        elif args.get('op') != 'inspect':
            raise web.HTTPBadRequest()
        return web.json_response({'sessionId': session['id'],
            'blocked': runtime.entered.is_set() and not runtime.release.is_set(),
            'sends': len(runtime.sent), 'admissions': admissions,
            'annotations': session.get('messageAnnotations', {})})

    app.router.add_post('/fixture/reactions', control)
    return app

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-message-interactions-') as tmp:
        web.run_app(main(Path(tmp), int(sys.argv[1])), host='127.0.0.1', port=int(sys.argv[1]), print=None)
