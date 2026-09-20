"""Isolated takeover UI, with no provider calls or real session ownership."""
import asyncio
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.runtime import SessionInUseError

OWNER = {'app': 'amplifier-cli', 'hostname': 'fixture', 'pid': 123,
         'handoff': {'version': 1, 'transport': 'unix'}}
class Runtime(fixture.Runtime):
    owned = False
    def __init__(self):
        super().__init__()
        self.takeover_result = 'success'
        self.release = asyncio.Event()
        self.release.set()
        self.takeovers = 0
        self.sends = 0
    async def send(self, session, text, input_id, emit):
        self.sends += 1
        if not self.owned:
            await emit('runtime.ownership', {'sessionId': session['id'], 'status': 'blocked', 'owner': OWNER})
            raise SessionInUseError(OWNER)
        await emit('assistant.message', {'sessionId': session['id'], 'text': 'Continued successfully.', 'inputId': input_id})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    async def takeover(self, session, emit, expected_owner=None):
        self.takeovers += 1
        await self.release.wait()
        if self.takeover_result == 'error':
            raise RuntimeError('Could not reach the session owner.')
        if self.takeover_result == 'conflict':
            raise SessionInUseError(OWNER)
        self.owned = True
fixture.Runtime = Runtime
async def main(home):
    app = await fixture.main(home)
    app['allowed_origins'] = app['allowed_origins'] | {'http://127.0.0.1:8967'}
    app['service'].state['updates'].update(items=[], available=0)
    service = app['service']
    service._session(service.state['selectedSessionId'])['messages'].append({
        'id': 'saved-history', 'role': 'assistant', 'text': 'Saved history stays readable.'})
    async def ownership(request):
        args = await request.json()
        runtime = service.runtime
        runtime.owned = False
        runtime.takeover_result = args.get('result', 'success')
        (runtime.release.clear if args.get('hold') else runtime.release.set)()
        service.state['runtime']['available'] = args.get('runtimeAvailable', True)
        owner = OWNER if args.get('supportsTakeover', True) else {k: v for k, v in OWNER.items() if k != 'handoff'}
        await service.on_runtime_event('runtime.ownership', {
            'sessionId': service.state['selectedSessionId'], 'status': args.get('status', 'blocked'),
            'source': 'Amplifier CLI', 'owner': owner, **({'detail': args['detail']} if 'detail' in args else {})})
        return web.json_response({'ok': True})
    async def finish(request):
        service.runtime.release.set()
        return web.json_response({'ok': True})
    async def inspect(request):
        return web.json_response({'takeovers': service.runtime.takeovers, 'sends': service.runtime.sends})
    app.router.add_post('/fixture/ownership', ownership)
    app.router.add_post('/fixture/finish', finish)
    app.router.add_get('/fixture', inspect)
    return app
if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-ownership-ui-') as tmp:
        web.run_app(main(Path(tmp)), host='127.0.0.1', port=8967, print=None)
