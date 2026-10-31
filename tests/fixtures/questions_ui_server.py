"""Real service/browser question flow with a deterministic, isolated runtime."""
import asyncio
import json
import signal
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.starts = []
        self.retries = []
        self.delivery_checks = []
        self.timeout_inputs = set()
        self.accepted_inputs = set()

    async def start(self, session, emit, **kwargs):
        self.starts.append(session['id'])
        await super().start(session, emit, **kwargs)

    async def send(self, session, text, input_id, emit):
        self.sent.append({'sessionId': session['id'], 'inputId': input_id, 'text': text})
        if (session['id'], input_id) in self.timeout_inputs:
            raise TimeoutError('Synthetic answer acknowledgement timed out.')
        await asyncio.sleep(.15)
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})

    async def delivery(self, session, input_id):
        self.delivery_checks.append({'sessionId': session['id'], 'inputId': input_id})
        return 'accepted' if (session['id'], input_id) in self.accepted_inputs else 'unknown'

    async def retry(self, session, text, input_id, emit):
        self.retries.append({'sessionId': session['id'], 'inputId': input_id})
        raise AssertionError('Delivery reconciliation must never retry an answer.')


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
    network_attempts = []

    async def reviews(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        markdown = (await service.app_bridge('dispatch', {'action': 'canvas.show', 'args': {
            'kind': 'markdown', 'title': 'Peer presentation proposal with a deliberately long descriptive title',
            'content': '# Pinned document v1\n\n**Original** content.',
        }}, sid))['result']
        probe_url = str(request.url.origin()) + '/fixture/network-probe'
        html = (await service.app_bridge('dispatch', {'action': 'canvas.show', 'args': {
            'kind': 'html', 'title': 'Interactive peer presentation mockup',
            'content': '<h1>HTML mockup v1</h1><button id="count">Try mockup</button><p id="value">0</p>'
                       '<script>document.querySelector("#count").onclick=()=>document.querySelector("#value").textContent="1";'
                       'try { parent.document.body.dataset.untrusted="yes"; } catch { document.body.dataset.parentBlocked="yes"; }'
                       f'fetch({json.dumps(probe_url)}).catch(()=>document.body.dataset.networkBlocked="yes");</script>',
        }}, sid))['result']
        q = await ask('# Choose the **peer presentation**\n\nKeep the sender visible.\nPreserve this line.\n\n'
                      '- Read the proposal\n- Try the mockup\n\n```text\nexact source\n```\n\n'
                      '[Safe link](https://example.com) [Unsafe](javascript:alert(1))\n\n<script>parent.bad=true</script>',
                      True, artifacts=[{'id': markdown['id'], 'version': 1}, {'id': html['id'], 'version': 1}])
        await service.app_bridge('dispatch', {'action': 'canvas.versions.revise', 'args': {
            'id': markdown['id'], 'expectedRevision': 1, 'content': '# Latest document v2',
        }}, sid)
        return web.json_response({'question': q, 'markdown': markdown, 'html': html})

    async def network_probe(request):
        network_attempts.append(True)
        return web.Response(text='reachable', headers={'Access-Control-Allow-Origin': '*'})

    async def check(request):
        observed_sid = request.query.get('sessionId', sid)
        return web.json_response({'sent': runtime.sent, 'questions': service.questions.store.all(observed_sid),
                                 'starts': runtime.starts, 'retries': runtime.retries,
                                 'deliveryChecks': runtime.delivery_checks,
                                 'messages': [{key: message.get(key) for key in ('id', 'inputId', 'questionId', 'text', 'delivery')}
                                              for message in service._session(observed_sid)['messages']
                                              if message.get('role') == 'user'],
                                 'networkAttempts': len(network_attempts),
                                 'sessionIds': [session['id'] for session in service.state['sessions']],
                                 'selectedSessionId': service.state['selectedSessionId']})

    async def delivery_questions(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        mode = (await request.json()).get('mode')
        if mode not in ('event', 'passive'):
            raise web.HTTPBadRequest()
        created = await service.dispatch('session.create', {
            'title': f'Delivery reconciliation {mode}', 'workspace': str(home / 'workspace'),
            'bundle': 'anchors', 'select': False})
        target_sid = created['result']['sessionId']
        document = (await service.app_bridge('dispatch', {'action': 'canvas.show', 'args': {
            'kind': 'markdown', 'title': 'Delivery decision document', 'presentation': 'saved',
            'content': '# Delivery document v1\n\nThe exact version reviewed with this answer.',
        }}, target_sid))['result']
        questions = []
        for prompt, artifacts in [
            ('Use the pinned document for this report?', [{'id': document['id'], 'version': 1}]),
            ('What should the next report cover?', []),
        ]:
            questions.append((await service.app_bridge('dispatch', {'action': 'question.create', 'args': {
                'prompt': prompt, 'required': True, 'dependency': 'Prepare this report',
                'artifacts': artifacts,
            }}, target_sid))['result'])
        await service.app_bridge('dispatch', {'action': 'canvas.versions.revise', 'args': {
            'id': document['id'], 'expectedRevision': 1, 'content': '# Delivery document v2',
        }}, target_sid)
        input_id = f"question:{questions[0]['id']}:answer"
        runtime.timeout_inputs.add((target_sid, input_id))
        return web.json_response({'sessionId': target_sid, 'first': questions[0],
                                  'second': questions[1], 'document': document, 'inputId': input_id})

    async def delivery_evidence(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        args = await request.json()
        key = (args.get('sessionId'), args.get('inputId'))
        if (key not in runtime.timeout_inputs or args.get('mode') not in ('event', 'passive')
                or not any((row['sessionId'], row['inputId']) == key for row in runtime.sent)):
            raise web.HTTPBadRequest()
        if args['mode'] == 'event':
            await service.on_runtime_event('runtime.status', {
                'sessionId': key[0], 'status': 'working', 'event': 'input.delivered', 'inputId': key[1]})
            await service.on_runtime_event('runtime.status', {'sessionId': key[0], 'status': 'idle'})
        else:
            # Only seed the runtime's evidence. The real conversation.delivery
            # action must read it and update the question; this route cannot.
            runtime.accepted_inputs.add(key)
        return web.json_response({'mode': args['mode'], 'sessionId': key[0], 'inputId': key[1]})

    async def propose_handoff(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        args = await request.json()
        result = await service.app_bridge('dispatch', {'action': 'session.handoff.propose',
            'id': 'questions-handoff-proposal', 'args': {
                'requestId': 'questions-handoff-review', 'sessionId': sid,
                'clientId': args['clientId'], 'workspace': str(home / 'workspace' / 'project'),
                'title': 'Question companion review', 'text': 'Do not start this cancelled review.',
                'bundle': 'anchors', 'selection': {'instance': 'one', 'model': 'fixture-model'}}}, sid)
        return web.json_response(result)

    async def voice(request):
        q = await ask('Which style should the spoken summary use?', options=[{'id': 'plain', 'label': 'Plain language'}])
        await service.record_voice_transcript('user', 'Plain language, please', voice_id='fixture-call', item_id='fixture-answer', session_id=sid)
        source = service._session(sid)['messages'][-1]
        result = await service.app_bridge('dispatch', {'action': 'question.answer', 'args': {
            'id': q['id'], 'expectedRevision': 1, 'optionId': 'plain', 'sourceMessageId': source['id']}}, sid)
        return web.json_response(result['result'])

    async def usage(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        count = (await request.json()).get('count')
        if count not in (6, 12):
            raise web.HTTPBadRequest()
        for index in range(count):
            await service.on_runtime_event('execution.event', {
                'id': f'questions-usage-{index}', 'kind': 'llm',
                'sessionId': sid, 'rootSessionId': sid, 'turnId': 'questions-usage',
                'producerId': 'questions-fixture', 'revision': count,
                'liveObservation': True, 'phase': 'completed',
                'startedAt': 1792220000 + index, 'endedAt': 1792220001 + index,
                'provider': f'fixture-provider-{index}',
                'model': f'Long descriptive fixture model name {index} for narrow layout',
                'usage': {'inputTokens': 100, 'outputTokens': 10, 'totalTokens': 110}})
        return web.json_response({'calls': count})

    app.router.add_post('/fixture/usage', usage)
    app.router.add_get('/fixture/check', check)
    app.router.add_post('/fixture/voice', voice)
    app.router.add_post('/fixture/handoff', propose_handoff)
    app.router.add_post('/fixture/reviews', reviews)
    app.router.add_post('/fixture/delivery-questions', delivery_questions)
    app.router.add_post('/fixture/delivery-evidence', delivery_evidence)
    app.router.add_get('/fixture/network-probe', network_probe)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins'] = app['allowed_origins'] | {url}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    print(json.dumps({'url': url, 'sessionId': sid, 'required': required['id'], 'optional': optional['id'], 'skipped': skipped['id']}), flush=True)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-questions-ui-') as temp:
        asyncio.run(main(Path(temp)))
