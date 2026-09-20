"""Real service/browser question flow with a deterministic, isolated runtime."""
import asyncio
import json
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def send(self, session, text, input_id, emit):
        self.sent.append({'sessionId': session['id'], 'inputId': input_id, 'text': text})
        await asyncio.sleep(.15)
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    runtime = Runtime()
    service.runtime = runtime
    app['runtime'] = runtime
    sid = service._session()['id']
    await service.dispatch('session.rename', {'id': sid, 'title': 'Durable questions'})
    async def ask(prompt, required=False, **extra):
        return (await service.app_bridge('dispatch', {'action': 'question.create', 'args': {
            'prompt': prompt, 'required': required, 'dependency': 'Prepare the report', **extra}}, sid))['result']
    required = await ask('Which report should I prepare?', True, options=[
        {'id': 'summary', 'label': 'Summary', 'description': 'A brief view of the main findings.'},
        {'id': 'full', 'label': 'Full report', 'description': 'Findings, supporting evidence, and next steps.'}])
    optional = await ask('What title would you like?')
    skipped = await ask('Would you like a subtitle?')

    async def check(request):
        return web.json_response({'sent': runtime.sent, 'questions': service.questions.store.all(sid),
                                 'selectedSessionId': service.state['selectedSessionId']})

    async def voice(request):
        q = await ask('Which style should the spoken summary use?', options=[{'id': 'plain', 'label': 'Plain language'}])
        await service.record_voice_transcript('user', 'Plain language, please', voice_id='fixture-call', item_id='fixture-answer', session_id=sid)
        source = service._session(sid)['messages'][-1]
        result = await service.app_bridge('dispatch', {'action': 'question.answer', 'args': {
            'id': q['id'], 'expectedRevision': 1, 'optionId': 'plain', 'sourceMessageId': source['id']}}, sid)
        return web.json_response(result['result'])

    app.router.add_get('/fixture/check', check)
    app.router.add_post('/fixture/voice', voice)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid, 'required': required['id'], 'optional': optional['id'], 'skipped': skipped['id']}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-questions-ui-') as temp:
        asyncio.run(main(Path(temp)))
