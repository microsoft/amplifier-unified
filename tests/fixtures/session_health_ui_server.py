"""Disposable failed computer-tool conversation, with no provider calls."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

if installed := os.environ.get('AMPLIFIER_TEST_INSTALLED_PACKAGE'):
    if not Path(installed).is_absolute() or not sys.flags.isolated or not sys.dont_write_bytecode:
        raise ValueError('Installed fixture requires an absolute package path and Python -I -B')
    import amplifier_web
    package = Path(installed).resolve()
    if (Path(amplifier_web.__file__).resolve() != package / '__init__.py'
            or {Path(path).resolve() for path in amplifier_web.__path__} != {package}):
        raise ValueError('Fixture imported a different amplifier_web package')
    # Validate the installed package before exposing only test-helper siblings,
    # never the checkout root excluded by -I.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

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
    naming_calls, calls = [], []
    original_start, original_stop = service.runtime.start, service.runtime.stop
    original_control = service.runtime.control
    async def start(session, emit, **kwargs):
        calls.append({'sessionId': session['id'], 'operation': 'start'})
        return await original_start(session, emit, **kwargs)
    async def stop(sid, **kwargs):
        calls.append({'sessionId': sid, 'operation': 'stop'})
        return await original_stop(sid, **kwargs)
    async def no_send(session, text, input_id, emit, **kwargs):
        calls.append({'sessionId': session['id'], 'operation': 'send', 'inputId': input_id})
        raise AssertionError('Health inspection and recovery copying must not send model input')
    async def control(sid, operation, args, **kwargs):
        calls.append({'sessionId': sid, 'operation': operation})
        if operation != 'session.naming':
            return await original_control(sid, operation, args, **kwargs)
        if kwargs.get('guard'):
            kwargs['guard']()
        naming_calls.append(sid)
        before = read(directory_for(home, service._session(sid)))
        await asyncio.sleep(.5)
        return {**before, 'name': 'Browser investigation', 'description': 'A fixture naming suggestion'}
    service.runtime.start, service.runtime.stop = start, stop
    service.runtime.send = no_send
    service.runtime.control = control
    async def check(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        assert len(calls) <= 64, 'Unexpected runtime activity in bounded health journey'
        recovered = []
        for session in service.state['sessions']:
            if (session.get('recovery') or {}).get('sourceSessionId') != source['id']:
                continue
            recovered_store = SessionStore.for_app(home, session['workspace'])
            raw = (recovered_store.directory(session['runtimeSessionId'])/'transcript.jsonl').read_bytes()
            assert len(raw) <= 65536, 'Synthetic recovery transcript must remain bounded'
            messages = [json.loads(line) for line in raw.splitlines() if line.strip()]
            recovered.append({
                'id': session['id'], 'status': session['status'],
                'sourceSessionId': session['recovery']['sourceSessionId'],
                'workReplayed': session['recovery']['workReplayed'],
                'selection': copy.deepcopy(session.get('selection')), 'bundle': session['bundle'],
                'transcriptSha256': hashlib.sha256(raw).hexdigest(), 'messageCount': len(messages),
                'hasExecutableToolHistory': any(row.get('role') == 'tool' or row.get('tool_calls')
                    or any(isinstance(block, dict) and block.get('type') in
                           {'tool_call', 'tool_use', 'tool_result', 'image', 'image_url', 'input_image'}
                           for block in (row['content'] if isinstance(row.get('content'), list) else []))
                    for row in messages),
                'hasSafetyReference': any('Any tool safety stop remains in effect.' in str(row.get('content'))
                    and (row.get('metadata') or {}).get('amplifier_recovery_reference') for row in messages),
            })
        assert len(recovered) <= 1, 'Recovery must not duplicate the requested copy'
        current = service._session(source['id'], hydrate=False)
        return web.json_response({
            'originalUnchanged': original == (store.directory(source['id'])/'transcript.jsonl').read_bytes(),
            'originalSha256': hashlib.sha256(original).hexdigest(), 'calls': calls,
            'namingCalls': len(naming_calls), 'sessions': len(service.state['sessions']),
            'selected': service.state['selectedSessionId'],
            'source': {'id': current['id'], 'status': current['status'], 'bundle': current['bundle'],
                       'selection': copy.deepcopy(current.get('selection')),
                       'failureCategory': (current.get('failure') or {}).get('category')},
            'recovered': recovered})
    app.router.add_get('/fixture/check',check)
    service._publish()
    await serve(app, source)


async def lifecycle(app, home):
    """Real host projections, synthetic runtime transport, no model or tool calls."""
    service = app['service']
    source = service._session()
    sid = source['id']
    calls = []
    from amplifier_web import feedback
    original_which = feedback.shutil.which
    feedback.shutil.which = lambda name: None if name == 'gh' else original_which(name)

    async def no_delivery(*args, **kwargs):
        raise AssertionError('Lifecycle diagnostics must not submit or upload feedback')

    feedback.github_api = no_delivery
    feedback.create_issue = no_delivery

    async def generation(event, input_id, generation_id, **values):
        await service.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'rootSessionId': sid, 'event': event,
            'generation_id': generation_id, 'initial_input_id': input_id,
            'input_ids': [input_id], 'observedAt': time.time(),
            'interruptionRevision': source.get('interruptionRevision', 0),
            'runtimeOwnerId': 'lifecycle-fixture-owner', **values})
        if event == 'generation.started':
            await service.on_runtime_event('runtime.status', {
                'sessionId': sid, 'status': 'working', 'event': 'input.delivered',
                'inputId': input_id, 'runtimeOwnerId': 'lifecycle-fixture-owner'})

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
    retry_preparation = os.environ['LIFECYCLE_FIXTURE'] == 'retry-preparation'
    if os.environ['LIFECYCLE_FIXTURE'] == 'request-failure' or retry_preparation:
        service._message(source, 'user', 'A request that failed', inputId='failed-input')
        await generation('generation.started', 'failed-input', 'failed-generation')
        await generation('generation.failed', 'failed-input', 'failed-generation',
            error_category='context_compaction' if retry_preparation else 'provider_unavailable',
            error_type='CompactionError' if retry_preparation else 'ServiceUnavailableError',
            error_code='summary_output_limit' if retry_preparation else None,
            error_stage='context_preparation' if retry_preparation else 'provider_request', retryable=True)
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
        if retry_preparation:
            await emit('runtime.status', {'sessionId': sid, 'status': 'starting',
                                         'phase': 'runtime-setup', 'detail': 'Preparing synthetic continuation'})
        elif text == 'A later different failure':
            await generation('generation.started', input_id, 'later-generation')
            await generation('generation.failed', input_id, 'later-generation',
                error_category='authentication', error_type='AuthenticationError',
                error_stage='provider_request', retryable=False)
            await service.on_runtime_event('runtime.error', {
                'sessionId': sid, 'rootSessionId': sid, 'generationId': 'later-generation',
                'errorType': 'AuthenticationError', 'error': 'PRIVATE later credentials wrapper'})
        else:
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
        # Match the original process-close ordering, not only a direct error.
        from amplifier_web.runtime import normalize_event
        closed_kind, closed_payload = normalize_event({'type': 'session.closed'}, sid)
        await service.on_runtime_event(closed_kind, closed_payload)
        await service.on_runtime_event('runtime.error', {
            'sessionId': sid, 'rootSessionId': sid, 'runtimeOwnerId': 'lifecycle-fixture-owner',
            'errorType': 'RuntimeError', 'phase': 'worker_idle',
            'error': 'PRIVATE idle callback failed /synthetic/workspace'})
        await service.on_runtime_event('runtime.ended', {
            'sessionId': sid, 'rootSessionId': sid, 'runtimeOwnerId': 'lifecycle-fixture-owner',
            'status': 'interrupted'})
        return web.json_response({'injected': True})

    async def human_stop(request):
        authorized(request)
        await service.dispatch('conversation.stop', {'sessionId': sid},
                               command_id='lifecycle-explicit-human-stop')
        await service.on_runtime_event('runtime.status', {
            'sessionId': sid, 'status': 'stopped', 'event': 'session.closed'})
        await service.on_runtime_event('runtime.error', {
            'sessionId': sid, 'rootSessionId': sid, 'errorType': 'RuntimeError',
            'error': 'PRIVATE callback after explicit human Stop'})
        return web.json_response({'stopped': True})

    async def failed_save(request):
        authorized(request)
        service._message(source, 'user', 'An interrupted unsaved request', inputId='unsaved-input')
        await generation('generation.started', 'unsaved-input', 'unsaved-generation')
        await generation('generation.detached', 'unsaved-input', 'unsaved-generation')
        from amplifier_web.runtime import normalize_event
        closed_kind, closed_payload = normalize_event({'type': 'session.closed'}, sid)
        await service.on_runtime_event(closed_kind, closed_payload)
        await service.on_runtime_event('runtime.error', {
            'sessionId': sid, 'rootSessionId': sid, 'event': 'persistence.failed',
            'errorType': 'OSError', 'error': 'PRIVATE failed-save path /synthetic/workspace'})
        await service.on_runtime_event('runtime.ended', {
            'sessionId': sid, 'rootSessionId': sid, 'status': 'interrupted'})
        return web.json_response({'injected': True})

    async def newer_failure(request):
        authorized(request)
        # Use ordinary explicit admission, not a synthetic message appended
        # behind the current execution turn's back.
        await service.dispatch('conversation.send', {
            'sessionId': sid, 'text': 'A later different failure'},
            command_id='later-input')
        return web.json_response({'injected': True})

    async def check(request):
        authorized(request)
        from amplifier_web.input_status import annotate
        return web.json_response({
            'originalUnchanged': original == transcript.read_bytes(),
            'completedUnchanged': source['messages'][:len(completed_messages)] == completed_messages,
            'messages': [annotate(source, row) for row in source['messages']],
            'status': source['status'], 'failure': source.get('failure'),
            'attention': service.browser_state()['attention'], 'activity': source.get('activity'),
            'interruptionRevision': source.get('interruptionRevision', 0),
            'runtimeFaults': [row for row in source.get('runtimeEvents', [])
                              if row.get('type') == 'runtime.error' and row.get('scope') == 'runtime'],
            'calls': calls,
            'sessions': len(service.state['sessions']), 'selected': service.state['selectedSessionId']})

    async def preparation_phase(request):
        authorized(request)
        if not retry_preparation:
            raise web.HTTPNotFound()
        phase = (await request.json())['phase']
        input_id = calls[-1]['inputId']
        if phase == 'warm':
            await service.on_runtime_event('runtime.warmth', {'sessionId': sid, 'status': 'warm'})
        elif phase == 'ready':
            await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'ready'})
        elif phase == 'admitted':
            await complete(input_id, 'A fresh explicit message worked.')
        elif phase == 'startup-error':
            await service.on_runtime_event('runtime.error', {'sessionId': sid,
                'errorType': 'RuntimeStartupError', 'error': 'Synthetic new worker could not start.'})
        else:
            raise web.HTTPBadRequest()
        return web.json_response({'phase': phase})

    app.router.add_post('/fixture/preparation-phase', preparation_phase)
    app.router.add_post('/fixture/late-runtime-fault', fault)
    app.router.add_post('/fixture/newer-failure', newer_failure)
    app.router.add_post('/fixture/human-stop', human_stop)
    app.router.add_post('/fixture/failed-save', failed_save)
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
