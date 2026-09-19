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
    async def send(self, session, text, input_id, emit):
        if not self.owned:
            await emit('runtime.ownership', {'sessionId': session['id'], 'status': 'blocked', 'owner': OWNER})
            raise SessionInUseError(OWNER)
        await emit('assistant.message', {'sessionId': session['id'], 'text': 'Continued successfully.', 'inputId': input_id})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    async def takeover(self, session, emit, expected_owner=None):
        await asyncio.sleep(.15)
        self.owned = True
fixture.Runtime = Runtime
async def main(home):
    app = await fixture.main(home)
    app['allowed_origins'] = app['allowed_origins'] | {'http://127.0.0.1:8967'}
    app['service'].state['updates'].update(items=[], available=0)
    return app
if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-ownership-ui-') as tmp:
        web.run_app(main(Path(tmp)), host='127.0.0.1', port=8967, print=None)
