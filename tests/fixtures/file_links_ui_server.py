"""Ephemeral file-link browser fixture with no provider calls or user data."""
import asyncio
from pathlib import Path
import tempfile
from aiohttp import web
import chat_ui_server as fixture


class Runtime(fixture.ChatRuntime):
    async def send(self, session, text, input_id, emit):
        await emit('assistant.message', {'sessionId': session['id'], 'inputId': input_id,
            'text': '[Plan](docs/plan.md), `docs/plan.md:12`, docs/plan.md, '
                    '[Missing](docs/missing.md), [Web](https://example.com/a.md).\n\n'
                    '```text\ndocs/plan.md\n```'})
        await emit('runtime.generation', {'sessionId': session['id'], 'event': 'generation.finished',
            'generation_id': 'fixture', 'input_ids': [input_id], 'text': 'Ready',
            'active_job_ids': [], 'disposition': 'manager_turn_finished'})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})


async def main():
    fixture.fixture.Runtime = Runtime
    with tempfile.TemporaryDirectory(prefix='amplifier-file-links-') as directory:
        home = Path(directory)
        app = await fixture.fixture.main(home)
        (home / 'workspace/docs').mkdir()
        (home / 'workspace/docs/plan.md').write_text('# File opened in Canvas')
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
            app['allowed_origins'] = app['allowed_origins'] | {url}
            print('FIXTURE_URL=' + url, flush=True)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
