"""Disposable, synthetic reply/reaction acceptance; no model or external service."""
import asyncio
import sys
import tempfile
from pathlib import Path
from aiohttp import web
import settings_ui_server as fixture
from chat_ui_server import ChatRuntime

fixture.Runtime = ChatRuntime

async def main(home, port):
    app = await fixture.main(home)
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    service = app['service']
    session = service._session()
    for index in range(75):
        service._message(session, 'user' if index % 2 == 0 else 'assistant', f'Saved message {index}: a reference point.')
    service._publish()
    return app

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-message-interactions-') as tmp:
        web.run_app(main(Path(tmp), int(sys.argv[1])), host='127.0.0.1', port=int(sys.argv[1]), print=None)
