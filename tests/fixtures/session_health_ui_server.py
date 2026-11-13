"""Disposable failed computer-tool conversation, with no provider calls."""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import tempfile
import time
from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import directory_for, read

async def main(home):
    app = await fixture.main(home)
    if os.environ.get('LIFECYCLE_FIXTURE'):
        return await lifecycle(app, home)
    service = app['service'];source = service._session()
    service._message(source,'user','Investigate the browser')
    service._message(source,'assistant','Checking the browser')
    source.update(status='error',error='RuntimeError: Manager turn failed; no automatic replay',errorAt=1789926000)
    from amplifier_web.session_health import failure_details
    source['failure']={**failure_details(source['error'],'RuntimeError'),'generationId':'image-generation','recordedAt':1789926000}
    source['generations']=[{'event':'generation.started','generation_id':'image-generation','at':1789925800}]
    rows=[{'role':'user','content':'Investigate the browser'},
          {'role':'assistant','content':'Checking the browser','tool_calls':[{'id':'blocked','name':'computer'}]},
          {'role':'tool','tool_call_id':'blocked','content':'halted: human confirmation required'}]
    store=SessionStore.for_app(home,source['workspace']);store.save(source['id'],rows,{})
    folder=store.directory(source['id'])/'context-intelligence';folder.mkdir(exist_ok=True)
    (folder/'events.jsonl').write_text(json.dumps({'event':'provider:error','timestamp':1789925990,'generation_id':'image-generation','data':{'session_id':source['id'],'error':{'type':'InvalidRequestError','msg':'Invalid image_url: invalid base64-encoded value'}}})+'\n')
    if os.environ.get('BRIDGE_TIMEOUT_FIXTURE'):
        from amplifier_web.session_health import failure_details
        source['failure'] = {**failure_details('Manager failed', 'TimeoutError'),
                             'generationId': 'bridge-generation', 'inputIds': ['input-1'],
                             'recordedAt': 1789926000, 'stage': 'manager_turn'}
        source['generations'] = [{'event': 'generation.started', 'generation_id': 'bridge-generation', 'at': 1789925800}]
        (folder/'events.jsonl').write_text(json.dumps({'event': 'provider:error', 'timestamp': 1789925990,
            'data': {'session_id': source['id'], 'error': {'type': 'TimeoutError',
            'msg': 'App bridge response timed out after 60 seconds (transport timeout, not normal coordination.wait expiry).'}}})+'\n')
    if os.environ.get('MODULE_FAILURE_FIXTURE'):
        from amplifier_web.module_failures import persist_failures
        failure = persist_failures(home / 'runtime-reports' / source['id'], [
            {'module': 'hook-fixture', 'type': 'hook', 'reason_code': 'invalid_module_metadata'}])
        await service.on_runtime_event('runtime.error', {
            'sessionId': source['id'], 'error': str(failure),
            'errorType': 'RuntimeStartupError', 'moduleFailures': failure.failures})
    elif os.environ.get('CONTEXT_LIMIT_FIXTURE'):
        await service.on_runtime_event('runtime.error', {
            'sessionId': source['id'], 'errorType': 'ContextLengthError',
            'error': 'provider payload that must not be shown in the alert'})
        if os.environ.get('ACTIVE_WORKER_FIXTURE'):
            source['workers'] = [{'id': 'still-running', 'status': 'working'}]
    original=(store.directory(source['id'])/'transcript.jsonl').read_bytes()
    naming_calls = []
    original_control = service.runtime.control
    async def control(sid, operation, args):
        if operation != 'session.naming':
            return await original_control(sid, operation, args)
        naming_calls.append(sid)
        before = read(directory_for(home, service._session(sid)))
        await asyncio.sleep(.5)
        return {**before, 'name': 'Browser investigation', 'description': 'A fixture naming suggestion'}
    service.runtime.control = control
    async def check(request):
        return web.json_response({'originalUnchanged':original==(store.directory(source['id'])/'transcript.jsonl').read_bytes(),'namingCalls':len(naming_calls),'sessions':len(service.state['sessions']),'selected':service.state['selectedSessionId']})
    app.router.add_get('/fixture/check',check)
    service._publish()
    await serve(app, source)


async def lifecycle(app, home):
    """Real host projections, synthetic runtime transport, no model or tool calls."""
    service = app['service']
    source = service._session()
    sid = source['id']
    calls = []

    async def generation(event, input_id, generation_id, **values):
        await service.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'rootSessionId': sid, 'event': event,
            'generation_id': generation_id, 'initial_input_id': input_id,
            'input_ids': [input_id], 'observedAt': time.time(),
            'runtimeOwnerId': 'lifecycle-fixture-owner', **values})

    async def complete(input_id, text):
        generation_id = 'generation-' + input_id
        await generation('generation.started', input_id, generation_id)
        await service.on_runtime_event('assistant.message', {
            'sessionId': sid, 'rootSessionId': sid, 'inputId': input_id,
            'generationId': generation_id, 'text': text,
            'runtimeMessage': {'sequence': 1}})
        await generation('generation.finished', input_id, generation_id,
            finalMessageRefs={'version': 1, 'generationId': generation_id, 'sequences': [1]},
            active_job_ids=[], disposition='manager_turn_finished')
        await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})

    service._message(source, 'user', 'A completed question', inputId='completed-input')
    await complete('completed-input', 'The saved answer is complete.')
    completed_messages = copy.deepcopy(source['messages'])
    if os.environ['LIFECYCLE_FIXTURE'] == 'request-failure':
        service._message(source, 'user', 'A request that failed', inputId='failed-input')
        await generation('generation.started', 'failed-input', 'failed-generation')
        await generation('generation.failed', 'failed-input', 'failed-generation',
            error_category='provider_unavailable', error_type='ServiceUnavailableError',
            error_stage='provider_request', retryable=True)
        await service.on_runtime_event('runtime.error', {
            'sessionId': sid, 'rootSessionId': sid, 'inputId': 'failed-input',
            'runtimeOwnerId': 'lifecycle-fixture-owner',
            'errorType': 'ServiceUnavailableError', 'error': 'PRIVATE provider wrapper /synthetic/workspace'})

    store = SessionStore.for_app(home, source['workspace'])
    store.save(sid, [{'role': row['role'], 'content': row['text']} for row in source['messages']], {})
    transcript = store.directory(sid) / 'transcript.jsonl'
    original = transcript.read_bytes()
    original_control = service.runtime.control
    original_start = service.runtime.start

    async def start(session, emit, **kwargs):
        calls.append({'operation': 'start'})
        return await original_start(session, emit, **kwargs)

    async def control(session_id, operation, args, **kwargs):
        calls.append({'operation': operation})
        return await original_control(session_id, operation, args, **kwargs)

    async def send(session, text, input_id, emit, **kwargs):
        calls.append({'operation': 'send', 'inputId': input_id})
        await complete(input_id, 'A fresh explicit message worked.')

    service.runtime.start = start
    service.runtime.control = control
    service.runtime.send = send
    faulted = False

    def authorized(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def fault(request):
        nonlocal faulted
        authorized(request)
        if faulted:
            raise web.HTTPConflict(text='The one late fault was already injected.')
        faulted = True
        await service.on_runtime_event('runtime.error', {
            'sessionId': sid, 'rootSessionId': sid, 'runtimeOwnerId': 'lifecycle-fixture-owner',
            'errorType': 'RuntimeError', 'phase': 'worker_idle',
            'error': 'PRIVATE idle callback failed /synthetic/workspace'})
        return web.json_response({'injected': True})

    async def check(request):
        authorized(request)
        from amplifier_web.input_status import annotate
        return web.json_response({
            'originalUnchanged': original == transcript.read_bytes(),
            'completedUnchanged': source['messages'][:len(completed_messages)] == completed_messages,
            'messages': [annotate(source, row) for row in source['messages']],
            'status': source['status'], 'failure': source.get('failure'),
            'runtimeFault': source.get('runtimeFault'), 'calls': calls,
            'sessions': len(service.state['sessions']), 'selected': service.state['selectedSessionId']})

    app.router.add_post('/fixture/late-runtime-fault', fault)
    app.router.add_get('/fixture/check', check)
    service._publish()
    await serve(app, source)


async def serve(app, source):
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}";app['allowed_origins']=app['allowed_origins']|{url}
    print(json.dumps({'url':url,'sessionId':source['id']}),flush=True)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
    for signum in signals:
        loop.add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        for signum in signals:
            loop.remove_signal_handler(signum)

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-health-ui-') as temp:asyncio.run(main(Path(temp)))
