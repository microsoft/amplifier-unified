"""Synthetic voice/backend provenance in the real host and built client; no model calls."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import main
from amplifier_web import server
from aiohttp import web

original_create = server.create_app


async def create(*args, **kwargs):
    app = await original_create(*args, **kwargs)
    service = app['service']
    if service.history.task:
        service.history.task.cancel()
        await asyncio.gather(service.history.task, return_exceptions=True)
        service.history.task = None
    await service.dispatch('session.create', {})
    session = service._session()
    now = time.time() - 30
    session.update(historyManaged=False, historyLoaded=True, historyLoading=False,
                   status='idle', messages=[], execution={'nodes': [], 'turns': []})
    rows = [
        {'id': 'voice-user', 'role': 'user', 'text': 'Make the requested change.', 'via': 'call', 'voiceId': 'fixture-call'},
        {'id': 'backend-update', 'role': 'assistant', 'text': 'Checking the exact target.', 'via': 'call', 'source': 'amplifier', 'inputId': 'voice:fixture-call:request', 'generationId': 'fixture-generation'},
        {'id': 'backend-result', 'role': 'assistant', 'text': '**The requested change is complete.**', 'via': 'call', 'source': 'amplifier', 'inputId': 'voice:fixture-call:request', 'generationId': 'fixture-generation',
         'response': {'version': 1, 'status': 'completed', 'phase': 'final', 'generationId': 'fixture-generation', 'finalMessageIds': ['backend-result']}},
        {'id': 'spoken-result', 'role': 'assistant', 'text': 'The requested change is complete.', 'via': 'call', 'voiceId': 'fixture-call', 'voiceItemId': 'spoken-item'},
        {'id': 'typed-user', 'role': 'user', 'text': 'A separate typed question.', 'via': 'chat', 'inputId': 'typed-request'},
        {'id': 'typed-result', 'role': 'assistant', 'text': 'The ordinary typed answer remains visible.', 'via': 'chat', 'source': 'amplifier', 'inputId': 'typed-request'},
        {'id': 'unknown-origin', 'role': 'assistant', 'text': 'A retained answer with unknown provenance.', 'via': 'call'},
    ]
    session['messages'] = [{**row, 'createdAt': now + i} for i, row in enumerate(rows)]
    original = json.dumps(session['messages'], sort_keys=True)

    async def inspect(request):
        return web.json_response({'sessionId': session['id'], 'historyUnchanged': original == json.dumps(session['messages'], sort_keys=True),
                                  'runtimeSends': len(service.runtime.sent)})

    app.router.add_get('/fixture/voice-work', inspect)
    service._publish()
    return app


server.create_app = create
if __name__ == '__main__':
    sys.argv.append('--chat-controls')
    with tempfile.TemporaryDirectory(prefix='amplifier-voice-work-ui-') as directory:
        asyncio.run(main(Path(directory)))
