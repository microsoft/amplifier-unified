"""Opt-in idle-only host admission; synthetic HTTP and real offline native loop."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientConnectionError
from jsonschema import ValidationError, validate

from amplifier_web.device_pairing import permits_companion
from amplifier_web.runtime import RuntimeManager, RuntimeOperationPending
from amplifier_web.service import ACTION_DEFINITIONS, AppError
from test_companion_authority import fixture, attach, create_chat
from test_service import Runtime as FixtureRuntime
from test_steering_runtime import owned_worker


def payload(identity='attempt-1', **extra):
    return {'id': identity, 'action': 'conversation.send', 'args': {
        'text': 'A distinct follow-up', 'via': 'chat', 'preserveDraft': True,
        'attachmentIds': [], 'onlyIfIdle': True, **extra}}


@pytest.mark.parametrize('value', [True, False, None, 1, 'true', {}])
async def test_schema_and_restricted_policy_agree_on_boolean(value):
    body = payload(onlyIfIdle=value)
    request = SimpleNamespace(method='POST', path='/api/sessions/chat/commands',
                              json=AsyncMock(return_value=body))
    assert await permits_companion(request) is (type(value) is bool)
    if type(value) is bool:
        validate(body['args'], ACTION_DEFINITIONS['conversation.send'][1])
    else:
        with pytest.raises(ValidationError):
            validate(body['args'], ACTION_DEFINITIONS['conversation.send'][1])


@pytest.mark.parametrize('extra', [
    {'expectedGenerationId': 'active'}, {'recoveryAllowance': {}}, {'recipientAgentId': 'external'},
    {'attachmentIds': ['file']}, {'preserveDraft': False}, {'via': 'call'},
])
async def test_option_does_not_expand_companion_authority(extra):
    request = SimpleNamespace(method='POST', path='/api/sessions/chat/commands',
                              json=AsyncMock(return_value=payload(**extra)))
    assert not await permits_companion(request)


class IdleFixtureRuntime(FixtureRuntime):
    def __init__(self):
        super().__init__()
        self.idle_calls = []
        self.result = {'accepted': False, 'effect': 'none', 'code': 'input_busy'}

    async def send_if_idle(self, session, text, input_id, emit, *, admission_check=None):
        if admission_check:
            admission_check()
        self.idle_calls.append(input_id)
        if isinstance(self.result, Exception):
            raise self.result
        return {**self.result, 'inputId': input_id}


async def paired(aiohttp_client, tmp_path, runtime):
    app, client, headers, device = await fixture(aiohttp_client, tmp_path, runtime)
    assert (await attach(client, headers)).status == 200
    headers = {**headers, 'X-Amplifier-Client': 'native-review'}
    sid, _ = await create_chat(client, headers)
    return app, client, headers, device, sid


async def test_http_busy_receipt_is_stable_and_new_attempt_has_new_identity(aiohttp_client, tmp_path):
    runtime = IdleFixtureRuntime()
    app, client, headers, _, sid = await paired(aiohttp_client, tmp_path, runtime)
    service = app['service']
    target = '/api/sessions/' + sid + '/commands'
    first = await client.post(target, headers=headers, json=payload())
    result = await first.json()
    assert first.status == 200 and result['accepted'] is False
    assert result['code'] == 'input_busy' and result['delivery'] == 'failed'
    assert 'state' not in result and 'inputId' not in result  # Existing companion projection.
    saved = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?', ('attempt-1',)).fetchone()[0])
    assert saved['accepted'] is False and saved['inputId'] == 'attempt-1'
    assert saved['code'] == 'input_busy'
    runtime.result = {'accepted': True}
    replay = await (await client.post(target, headers=headers, json=payload())).json()
    assert replay['accepted'] is False and replay['code'] == 'input_busy'
    assert runtime.idle_calls == ['attempt-1']
    changed = await client.post(target, headers=headers, json=payload(onlyIfIdle=False))
    assert changed.status == 409
    accepted = await (await client.post(target, headers=headers, json=payload('attempt-2'))).json()
    assert accepted['accepted'] is True and accepted['delivery'] == 'accepted'
    assert runtime.idle_calls == ['attempt-1', 'attempt-2'] and runtime.sent == []
    with service.clients.bind('native-review'), pytest.raises(AppError, match='ordinary Retry'):
        await service.dispatch('conversation.retry', {'sessionId': sid, 'inputId': 'attempt-1'})


@pytest.mark.parametrize('option', [False, None])
async def test_normal_companion_send_unchanged(aiohttp_client, tmp_path, option):
    runtime = IdleFixtureRuntime()
    _, client, headers, _, sid = await paired(aiohttp_client, tmp_path, runtime)
    body = payload(onlyIfIdle=option)
    if option is None:
        body['args'].pop('onlyIfIdle')
    response = await client.post('/api/sessions/' + sid + '/commands', headers=headers, json=body)
    assert response.status == 200 and (await response.json())['accepted']
    assert len(runtime.sent) == 1 and not runtime.idle_calls


@pytest.mark.parametrize('route', ['browser', 'unbound-native', 'unsupported-runtime',
                                      'pendingModelSelection', 'pendingBundleSelection', 'forkTranscript'])
async def test_http_unsupported_routes_do_not_fall_back(aiohttp_client, tmp_path, route):
    runtime = FixtureRuntime() if route == 'unsupported-runtime' else IdleFixtureRuntime()
    app, client, headers, _, sid = await paired(aiohttp_client, tmp_path, runtime)
    service = app['service']
    if route in {'browser', 'unbound-native'}:
        headers = {'Authorization': 'Bearer ' + app['control_token'], 'X-Amplifier-Client': 'ordinary'}
        response = await client.post('/api/clients/attach', headers=headers, json={
            'clientId': 'ordinary', 'kind': 'web' if route == 'browser' else 'native', 'protocolVersion': 1})
        assert response.status == 200
    elif route != 'unsupported-runtime':
        service._session(sid)[route] = {'fixture': True}
    response = await client.post('/api/sessions/' + sid + '/commands', headers=headers, json=payload())
    assert response.status in {403, 409}, await response.text()
    assert not runtime.sent and not getattr(runtime, 'idle_calls', [])


@pytest.mark.parametrize('failure', [RuntimeError('InputBusyError: ambiguous worker failure'),
                                    RuntimeOperationPending('send')])
async def test_lost_or_ambiguous_ack_replays_unknown_not_busy(aiohttp_client, tmp_path, failure):
    runtime = IdleFixtureRuntime()
    runtime.result = failure
    app, client, headers, _, sid = await paired(aiohttp_client, tmp_path, runtime)
    target = '/api/sessions/' + sid + '/commands'
    response = await client.post(target, headers=headers, json=payload())
    assert response.status >= 400
    saved = json.loads(app['service'].db.execute('SELECT receipt FROM commands WHERE id=?', ('attempt-1',)).fetchone()[0])
    assert saved['delivery'] == 'unknown' and saved.get('code') != 'input_busy'
    result = await (await client.post(target, headers=headers, json=payload())).json()
    assert result['delivery'] == 'unknown' and result.get('code') != 'input_busy'
    assert runtime.idle_calls == ['attempt-1'] and not runtime.sent


async def test_disconnected_http_caller_reconciles_exact_accepted_receipt(aiohttp_client, tmp_path):
    runtime = IdleFixtureRuntime()
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = runtime.send_if_idle
    async def held(*args, **kwargs):
        started.set()
        await release.wait()
        runtime.result = {'accepted': True}
        result = await original(*args, **kwargs)
        finished.set()
        return result
    runtime.send_if_idle = held
    app, client, headers, _, sid = await paired(aiohttp_client, tmp_path, runtime)
    target = '/api/sessions/' + sid + '/commands'
    request = asyncio.create_task(client.post(target, headers=headers, json=payload()))
    try:
        await asyncio.wait_for(started.wait(), 5)
        request.cancel()  # The shielded service retains the actual admission.
        await asyncio.gather(request, return_exceptions=True)
        release.set()
        await asyncio.wait_for(finished.wait(), 5)
        result = await (await client.post(target, headers=headers, json=payload())).json()
        assert result['accepted'] is True and result['delivery'] == 'accepted'
        assert runtime.idle_calls == ['attempt-1']
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


async def test_revocation_after_host_receipt_before_native_admission(aiohttp_client, tmp_path, monkeypatch):
    runtime = IdleFixtureRuntime()
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.send_if_idle
    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    runtime.send_if_idle = held
    app, client, headers, device, sid = await paired(aiohttp_client, tmp_path, runtime)
    tasks = []
    dispatch = app['service'].dispatch
    async def observed(*args, **kwargs):
        tasks.append(asyncio.current_task())
        return await dispatch(*args, **kwargs)
    monkeypatch.setattr(app['service'], 'dispatch', observed)
    request = asyncio.create_task(client.post('/api/sessions/' + sid + '/commands', headers=headers, json=payload()))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        app['terminal_setup'].devices.revoke(device['deviceID'])
        release.set()
        try:
            response = await request
            assert response.status == 403
        except ClientConnectionError:
            pass  # Revocation may close the connection before a response.
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        assert len(results) == 1 and isinstance(results[0], AppError) and results[0].status == 403
        assert not runtime.idle_calls and not runtime.sent
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize('blocked', ['cold', 'old-worker', 'pendingModelSelection',
                                    'pendingBundleSelection', 'fileAccessPending', 'forkTranscript', 'externalAgent'])
async def test_manager_never_prepares_or_uses_configuration_shortcuts(blocked):
    manager = RuntimeManager()
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    manager.workers['chat'] = {'process': SimpleNamespace(returncode=None), 'ready': ready,
                               'idleOnlyAdmission': blocked != 'old-worker'}
    session = {'id': 'chat', 'status': 'idle'}
    if blocked == 'cold':
        manager.workers.clear()
    elif blocked not in {'old-worker'}:
        session[blocked] = {'fixture': True}
    manager._admit = AsyncMock()
    manager._start_for_input = AsyncMock()
    manager.send = AsyncMock()
    result = await manager.send_if_idle(session, 'follow-up', 'input', AsyncMock())
    assert result['accepted'] is False and result['effect'] == 'none'
    assert result['code'] != 'input_busy'
    manager._admit.assert_not_awaited()
    manager._start_for_input.assert_not_awaited()
    manager.send.assert_not_awaited()


async def test_manager_rechecks_stop_epoch_before_writing_idle_input():
    manager = RuntimeManager()
    manager.bind_execution_state(lambda sid: {
        'directory': '/fixture', 'revision': 0, 'fenced': False, 'interruptionRevision': 1})
    manager._admit = AsyncMock()
    result = await manager.send_if_idle({'id': 'chat', 'workspace': '/fixture', 'interruptionRevision': 0},
                                        'stopped intent', 'input', AsyncMock())
    assert result['code'] == 'input_stopped' and result['effect'] == 'none'
    manager._admit.assert_not_awaited()


@pytest.mark.parametrize('result', [None, {}, {'accepted': True}, {'accepted': False, 'code': 'input_busy'},
                                      {'accepted': False, 'effect': 'none', 'code': 'input_busy', 'inputId': 'foreign'}])
async def test_manager_ambiguous_worker_results_are_not_busy_or_accepted(result):
    manager = RuntimeManager()
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    manager.workers['chat'] = {'process': SimpleNamespace(returncode=None), 'ready': ready, 'idleOnlyAdmission': True}
    manager._admit = AsyncMock(return_value=('row', 'rpc', 'future'))
    manager._reply = AsyncMock(return_value=result)
    with pytest.raises(RuntimeError, match='not confirmed'):
        await manager.send_if_idle({'id': 'chat'}, 'follow-up', 'input', AsyncMock())
    assert manager._admit.call_args.args[2]['only_if_idle'] is True


@pytest.mark.parametrize('support', [False, True])
async def test_manager_worker_pipe_requires_positive_support_witness(tmp_path, support):
    script = r'''
import json, sys
support = sys.argv[1] == 'True'
for line in sys.stdin:
    data = json.loads(line)
    if data['op'] == 'start':
        print(json.dumps({'type': 'runtime.ready', 'idleOnlyAdmission': support}), flush=True)
    elif data['op'] == 'send':
        print(json.dumps({'op': 'reply', 'id': data['id'], 'result': {
            'accepted': True, 'inputId': data['input_id'], 'nativeFlag': data.get('only_if_idle')}}), flush=True)
    elif data['op'] == 'stop':
        break
'''
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', script, str(support)], startup_timeout=3)
    manager.home = tmp_path / 'app'
    session = {'id': 'pipe', 'workspace': str(tmp_path), 'bundle': 'fixture', 'status': 'working'}
    try:
        await manager.start(session, AsyncMock())
        assert manager.workers['pipe']['idleOnlyAdmission'] is support
        result = await manager.send_if_idle(session, 'independent turn', 'input', AsyncMock())
        assert result['accepted'] is support
        if support:
            assert result['nativeFlag'] is True
        else:
            assert result['code'] == 'input_idle_unsupported'
    finally:
        await manager.close()


@pytest.fixture
async def native_loop(monkeypatch, tmp_path):
    from amplifier_core import AmplifierSession
    from amplifier_module_loop_live.runtime import Runtime
    requests, responses = asyncio.Queue(), asyncio.Queue()
    class Provider:
        async def complete(self, request, **kwargs):
            await requests.put(request)
            return await responses.get()
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        def get_info(self):
            return SimpleNamespace(default_model='fixture')
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    runtime = Runtime()
    session = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
        'config': {'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=runtime.session_id)
    await session.initialize()
    await session.coordinator.mount('providers', Provider(), name='fixture')
    session.coordinator.register_capability('live.runtime', runtime)
    task = asyncio.create_task(session.execute(''))
    try:
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', 5)
        worker = owned_worker(runtime, session, task)
        yield runtime, session, worker, requests, responses
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()


@pytest.mark.parametrize('host_status', ['idle', 'working'])
async def test_actual_native_idle_admission_and_busy_through_manager(monkeypatch, native_loop, host_status):
    from amplifier_core.message_models import ChatResponse, TextBlock
    runtime, session, worker, requests, responses = native_loop
    manager = RuntimeManager()
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    row = {'process': SimpleNamespace(returncode=None), 'ready': ready, 'pending': {},
           'inflight': set(), 'idleOnlyAdmission': worker.supports_idle_input()}
    manager.workers['chat'] = row
    writes, replies = [], []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    async def write(row, data):
        writes.append(data)
        await worker.command(data)
        reply = replies[-1]
        row['inflight'].discard(data['id'])
        future = row['pending'][data['id']]
        if reply.get('error'):
            future.set_exception(RuntimeError(reply['error']))
        else:
            future.set_result(reply['result'])
    monkeypatch.setattr(manager, '_write', write)
    snapshot = {'id': 'chat', 'status': host_status, 'collaborationGeneration': {'id': 'stale-host'}}
    result = await manager.send_if_idle(snapshot, 'First independent turn', 'first', AsyncMock())
    assert result == {'accepted': True, 'inputId': 'first'}
    await asyncio.wait_for(requests.get(), 5)
    generation = runtime.generation['id']
    result = await manager.send_if_idle(snapshot, 'Do not steer', 'second', AsyncMock())
    assert result == {'accepted': False, 'effect': 'none', 'inputId': 'second', 'code': 'input_busy'}
    assert 'second' not in runtime.accepted and 'second' not in worker.context_bindings
    assert runtime.accepted['first'].only_if_idle is True
    assert all(data['only_if_idle'] is True for data in writes)
    await responses.put(ChatResponse(content=[TextBlock(text='First complete')]))
    done = await runtime.wait_for(lambda e: e['type'] == 'generation.finished', 5)
    await runtime.wait_for(lambda e: e['type'] == 'session.idle' and e['sequence'] > done['sequence'], 5)
    result = await manager.send_if_idle(snapshot, 'Next separate turn', 'third', AsyncMock())
    assert result['accepted']
    request = await asyncio.wait_for(requests.get(), 5)
    assert 'Do not steer' not in str(request.messages)
    assert runtime.generation['id'] != generation


@pytest.mark.parametrize('phase', ['input.queued', 'observation.received'])
async def test_worker_native_refuses_pre_generation_observation_race(monkeypatch, native_loop, phase):
    from amplifier_module_loop_live.runtime import Input
    runtime, _, worker, _, _ = native_loop
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    service = phase == 'observation.received'
    await runtime.submit(Input('service' if service else 'user', 'already admitted', id='prior',
                               source='fixture' if service else 'user'))
    async with runtime.changed:
        await asyncio.sleep(0)
        assert runtime.events[-1]['type'] == phase and runtime.generation is None
        await worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'next', 'text': 'Separate turn',
                              'only_if_idle': True})
        assert replies[-1]['result']['code'] == 'input_busy'
        assert 'next' not in runtime.accepted


@pytest.mark.parametrize('shortcut', ['configuration_if_idle', 'applied_fork_choice', 'recovery_allowance'])
async def test_worker_blocks_configuration_shortcuts(monkeypatch, native_loop, shortcut):
    runtime, _, worker, _, _ = native_loop
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    await worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'next', 'text': 'Separate turn',
                          'only_if_idle': True, shortcut: True})
    assert replies[-1]['result']['code'] == 'input_idle_configuration'
    assert 'next' not in runtime.accepted


async def test_worker_missing_native_support_is_not_ordinary_fallback(monkeypatch, native_loop):
    import amplifier_module_loop_live.runtime as live
    runtime, _, worker, _, _ = native_loop
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    monkeypatch.delattr(live, 'InputBusyError')
    assert not worker.supports_idle_input()
    await worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'next', 'text': 'Separate turn', 'only_if_idle': True})
    assert replies[-1]['result']['code'] == 'input_idle_unsupported'
    assert 'next' not in runtime.accepted


async def test_worker_preparation_error_named_busy_remains_ambiguous(monkeypatch, native_loop):
    from amplifier_module_loop_live.runtime import InputBusyError
    runtime, _, worker, _, _ = native_loop
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input',
                        AsyncMock(side_effect=InputBusyError('not an admission witness')))
    await worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'next', 'text': 'Separate turn', 'only_if_idle': True})
    assert 'next' not in runtime.accepted
    assert 'error' in replies[-1] and replies[-1].get('code') != 'input_busy'


async def test_worker_stop_during_preparation_never_admits_idle_input(monkeypatch, native_loop):
    runtime, _, worker, _, _ = native_loop
    replies, entered, release = [], asyncio.Event(), asyncio.Event()
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    async def prepare(*args, **kwargs):
        entered.set()
        await release.wait()
        return 'stopped input'
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    task = asyncio.create_task(worker.command({
        'op': 'send', 'id': 'rpc', 'input_id': 'next', 'text': 'Separate turn', 'only_if_idle': True}))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await worker.command({'op': 'stop'})
        release.set()
        await asyncio.wait_for(task, 5)
        result = next(row['result'] for row in replies if row.get('id') == 'rpc')
        assert result['code'] == 'input_stopped' and result['effect'] == 'none'
        assert 'next' not in runtime.accepted
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_worker_post_reservation_failure_never_claims_busy(monkeypatch, native_loop):
    from amplifier_module_loop_live.runtime import InputBusyError
    runtime, _, worker, _, _ = native_loop
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    async def fail_after_reservation(*args, **kwargs):
        raise InputBusyError('notification failed after reservation')
    monkeypatch.setattr(runtime, 'emit', fail_after_reservation)
    await worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'next', 'text': 'Separate turn', 'only_if_idle': True})
    assert 'next' in runtime.accepted
    assert 'error' in replies[-1] and replies[-1].get('code') != 'input_busy'
    assert 'result' not in replies[-1]