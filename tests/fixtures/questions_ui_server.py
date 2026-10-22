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
        return web.json_response({'sent': runtime.sent, 'questions': service.questions.store.all(sid),
                                 'networkAttempts': len(network_attempts),
                                 'sessionIds': [session['id'] for session in service.state['sessions']],
                                 'selectedSessionId': service.state['selectedSessionId']})

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
    app.router.add_get('/fixture/network-probe', network_probe)
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
