"""Disposable paged conversation fixture, with no provider or real history access."""
import asyncio
import json
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.automatic_history import display_message
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_files import project_slug


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    source = service._session()
    rows = [{'role': 'user' if index % 2 == 0 else 'assistant', 'content': f'Export message {index}'} for index in range(140)]
    rows[0]['content'] = 'The earliest question'
    rows[-1]['content'] = '```python\nprint("exact α")  \n```\n'
    SessionStore.for_app(home, source['workspace']).save(source['id'], rows, {})
    source.update(nativeProject=project_slug(source['workspace']), nativeIdentity=source['id'],
                  historyManaged=True, historyLoaded=True, sharedHistoryOffset=120, sharedHistoryTotal=140,
                  messages=[display_message(row, index, source) for index, row in enumerate(rows) if index >= 120])
    source['messages'].append({'id': 'voice', 'role': 'user', 'text': 'Spoken follow-up', 'via': 'call', 'voiceId': 'call'})
    service.state['canvasArtifacts'].append({'id': 'saved-diagram', 'title': 'Export diagram', 'sessionId': source['id'], 'kind': 'markdown', 'messageId': 'voice'})
    service.state['view'].update(panel='settings', settingsSection='setup', settingsExpanded=['conversation'])
    service._publish()
    async def append_after_preview(request):
        source['messages'].append({'id': 'after-preview', 'role': 'assistant', 'text': 'Appended after review'})
        service._publish()
        return web.json_response({'ok': True})
    app.router.add_post('/api/fixture/exportAppend', append_after_preview)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-export-ui-') as temp:
        asyncio.run(main(Path(temp)))
