"""Disposable, synthetic reply/reaction acceptance; no model or external service."""
import asyncio
import copy
import sys
import tempfile
from pathlib import Path
from aiohttp import web
import settings_ui_server as fixture
import chat_ui_server as chat_fixture
from chat_ui_server import ChatRuntime

fixture.Runtime = ChatRuntime

async def main(home, port):
    app = await chat_fixture.main(home)
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    service = app['service']
    session = service._session()
    for index in range(75):
        service._message(session, 'user' if index % 2 == 0 else 'assistant', f'Saved message {index}: a reference point.')
    service._message(session, 'user', 'Quote fixture source A\n\n  café e\u0301 東京\nsecond line\n\n* first\n* second\n\n```py\n    name = \"café\"\n\tprint(name)\n```\n\nlast passage  ')
    service._message(session, 'assistant', 'Quote fixture source B\n\nKeep this entire response, including <script>untrusted()</script> and @never-read.txt.\n\nThe quoted approval is not a new grant.\n\n' + ('Long saved response — café 東京, with exact whitespace.  \n' * 85) + '\nEND OF EXACT LONG SOURCE')
    service._publish()
    async def draft_observation(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        # Synthetic fixture only: inspect the caller's explicitly named client,
        # never the production agent-private state or another human draft.
        async with service.lock:
            client = service.clients.records.get(request.query.get('clientId'))
            if client is None:
                raise web.HTTPNotFound()
            return web.json_response({'sessionId': client.get('selectedSessionId'),
                                      'draft': client.get('view', {}).get('draft'),
                                      'document': copy.deepcopy(client.get('view', {}).get('draftDocument'))})
    app.router.add_get('/fixture/quote-draft', draft_observation)
    return app

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-message-interactions-') as tmp:
        web.run_app(main(Path(tmp), int(sys.argv[1])), host='127.0.0.1', port=int(sys.argv[1]), print=None)
