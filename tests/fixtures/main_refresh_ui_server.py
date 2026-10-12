"""Synthetic redesign preview, with no provider calls or host state."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from aiohttp import web
import chat_ui_server as fixture


async def main(port):
    with tempfile.TemporaryDirectory(prefix='unified-ui-preview-') as tmp:
        app = await fixture.fixture.main(Path(tmp))
        service = app['service']
        session = service._session()
        session['title'] = 'Homepage direction'
        service._message(session, 'user', 'Help me plan a simpler, more welcoming homepage. Keep the useful details easy to find.')
        service._message(session, 'assistant', '**Lead with what someone can do here.** The homepage should help a visitor choose their next step.\n\n- One clear introduction and a primary action.\n- A short example of the experience.\n- Deeper features a little further down.\n\nI put the proposed structure and open decisions into a brief we can refine together.')
        session.setdefault('execution', {})['plan'] = {'items': [{'content': text, 'status': 'completed'} for text in ['Review the current site', 'Propose a simpler structure', 'Prepare the brief']]}
        await service.dispatch('session.pin', {'id': session['id'], 'pinned': True})
        await service.dispatch('canvas.show', {'kind': 'markdown', 'title': 'Website refresh brief', 'content': '# A clearer first impression\n\nHelp a new visitor understand the product, see a useful example, and take their first step.\n\n## Proposed page structure\n\n1. **Welcome.** A short promise and one clear action.\n2. **See it in use.** Show a real task from question to useful result.\n3. **Go further.** Introduce tools, workspaces, and ways to customize.\n\n## One decision to make\n\nShould the opening example focus on everyday work or a specific audience?'})
        service.state['canvas']['open'] = False
        await service.dispatch('view.update', {'patch': {'navPinned': True, 'navWidth': 240, 'scheme': 'light'}})
        service._save()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', port)
        await site.start()
        actual = site._server.sockets[0].getsockname()[1]
        url = f'http://127.0.0.1:{actual}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    asyncio.run(main(parser.parse_args().port))
