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
from amplifier_web.execution import ensure_turn
from amplifier_web.runtime import normalize_event
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
        {'id': 'voice-user', 'role': 'user', 'text': 'Make the requested change.', 'via': 'call', 'voiceId': 'fixture-call', 'voiceInputId': 'voice:fixture-call:request'},
        {'id': 'backend-update', 'role': 'assistant', 'text': 'Checking the exact target.', 'via': 'call', 'source': 'amplifier', 'inputId': 'voice:fixture-call:request', 'generationId': 'fixture-generation'},
        {'id': 'backend-result', 'role': 'assistant', 'text': '**The requested change is complete.**', 'via': 'call', 'source': 'amplifier', 'inputId': 'voice:fixture-call:request', 'generationId': 'fixture-generation',
         'response': {'version': 1, 'status': 'completed', 'phase': 'final', 'generationId': 'fixture-generation', 'finalMessageIds': ['backend-result']}},
        {'id': 'spoken-result', 'role': 'assistant', 'text': 'The requested change is complete.', 'via': 'call', 'voiceId': 'fixture-call', 'voiceItemId': 'spoken-item'},
        {'id': 'voice-user-two', 'role': 'user', 'text': 'Check a second separate target.', 'via': 'call', 'voiceId': 'fixture-call', 'voiceInputId': 'voice:fixture-call:second'},
        {'id': 'spoken-progress-two', 'role': 'assistant', 'text': 'I will check that second target.', 'via': 'call', 'voiceId': 'fixture-call', 'voiceItemId': 'spoken-second'},
        {'id': 'backend-second', 'role': 'assistant', 'text': 'Only the second target was checked.', 'via': 'call', 'source': 'amplifier', 'inputId': 'voice:fixture-call:second', 'generationId': 'second-generation'},
        {'id': 'typed-user', 'role': 'user', 'text': 'A separate typed question.', 'via': 'chat', 'inputId': 'typed-request'},
        {'id': 'typed-result', 'role': 'assistant', 'text': 'The ordinary typed answer remains visible.', 'via': 'chat', 'source': 'amplifier', 'inputId': 'typed-request'},
        {'id': 'unknown-origin', 'role': 'assistant', 'text': 'A retained answer with unknown provenance.', 'via': 'call'},
        {'id': 'orphan-backend', 'role': 'assistant', 'text': 'A saved voice result whose work is outside this page.', 'via': 'call', 'source': 'amplifier', 'inputId': 'voice:older-call:unloaded'},
    ]
    session['messages'] = [{**row, 'createdAt': now + i} for i, row in enumerate(rows)]
    for input_id, anchor, offset in [('voice:fixture-call:request', 'voice-user', 0), ('voice:fixture-call:second', 'voice-user-two', 4), ('typed-request', 'typed-user', 7)]:
        session['execution']['turns'].append({'id': input_id, 'inputId': input_id, 'anchorMessageId': anchor,
            'phase': 'completed', 'startedAt': now + offset + .1, 'endedAt': now + offset + 2})
        session['execution']['nodes'].append({'id': 'model:' + input_id, 'kind': 'llm', 'label': 'Synthetic fixture model',
            'provider': 'fixture', 'model': 'fixture-model', 'phase': 'completed', 'turnId': input_id,
            'sessionId': session['id'], 'rootSessionId': session['id'], 'anchorMessageId': anchor,
            'startedAt': now + offset + .1, 'endedAt': now + offset + 2})
    original = json.dumps(session['messages'], sort_keys=True)
    original_count = len(session['messages'])
    streams = {}

    async def stream(request):
        data = await request.json()
        kind, part = data['kind'], data['part']
        assert kind in {'voice', 'typed', 'unknown'} and part in {'start', 'grow', 'finish'}
        input_id = {'voice': 'voice:fixture-call:stream', 'typed': 'typed-live-request', 'unknown': None}[kind]
        marker = kind.upper() + '_STREAM'
        if part == 'start':
            streams[kind] = {'text': marker + '_FIRST_CHUNK', 'inputId': input_id}
            # A call being connected never turns an unrelated typed/unknown
            # request into voice work. Exact delivered runtime input owns it.
            service.state['voice'] = {'id': 'fixture-call', 'attemptId': 'fixture-attempt', 'status': 'connected',
                                      'provider': 'live', 'model': 'fixture-live', 'sessionId': session['id']}
            user_id = kind + '-stream-user'
            session['messages'].append({'id': user_id, 'role': 'user', 'text': 'Synthetic ' + kind + ' streaming request.',
                'via': 'call' if kind == 'voice' else 'chat', 'createdAt': time.time(),
                **({'voiceInputId': input_id, 'voiceId': 'fixture-call'} if kind == 'voice' else {'inputId': input_id})})
            turn_id = input_id or 'unknown-fixture-turn'
            ensure_turn(session, turn_id, 'Synthetic streaming work')
            await service.on_runtime_event('execution.event', {'id': 'model:' + turn_id, 'kind': 'llm', 'phase': 'running',
                'label': 'Synthetic fixture model', 'model': 'fixture-model', 'provider': 'fixture',
                'turnId': turn_id, 'sessionId': session['id'], 'rootSessionId': session['id'], 'startedAt': time.time()})
            await service.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'working'})
        elif part == 'grow':
            streams[kind]['text'] += '\n\n' + marker + '_SECOND_CHUNK'
        else:
            event = normalize_event({'type': 'assistant.message', 'text': streams[kind]['text'],
                                     'generation_id': kind + '-stream-generation'}, session['id'], input_id)
            await service.on_runtime_event(*event)
            streams[kind]['messageId'] = session['messages'][-1]['id']
            await service.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
            service.state['voice']['status'] = 'ended'
            service._publish()
            return web.json_response(streams[kind])
        text = marker + ('_FIRST_CHUNK' if part == 'start' else '_SECOND_CHUNK')
        # Exercise the runtime boundary that previously lost the exact
        # delivered input ID on the first chunk. Do not infer ownership from
        # the active call, current turn, or a last-message heuristic.
        event = normalize_event({'type': 'assistant.delta', 'text': ('\n\n' if part == 'grow' else '') + text,
                                 'request_id': kind + '-request', 'block_index': 0}, session['id'], input_id)
        await service.on_runtime_event(*event)
        streams[kind]['streamId'] = session.get('streamingId')
        return web.json_response({**streams[kind], 'streamingInputId': session.get('streamingInputId')})

    async def scrollback(request):
        for index in range(95):
            session['messages'].append({'id': f'scrollback-{index}', 'role': 'user' if index % 2 == 0 else 'assistant',
                'text': f'Retained history filler {index}. ' + 'A quiet paragraph. ' * 5, 'via': 'chat',
                'createdAt': time.time() + index / 100})
        service._publish()
        return web.json_response({'count': len(session['messages'])})

    async def inspect(request):
        return web.json_response({'sessionId': session['id'], 'historyUnchanged': original == json.dumps(session['messages'][:original_count], sort_keys=True),
                                  'runtimeSends': len(service.runtime.sent), 'streams': streams})

    app.router.add_get('/fixture/voice-work', inspect)
    app.router.add_post('/fixture/voice-work/stream', stream)
    app.router.add_post('/fixture/voice-work/scrollback', scrollback)
    service._publish()
    return app


server.create_app = create
if __name__ == '__main__':
    sys.argv.append('--chat-controls')
    with tempfile.TemporaryDirectory(prefix='amplifier-voice-work-ui-') as directory:
        asyncio.run(main(Path(directory)))
