"""An unacknowledged follow-up is not proof that the live turn stopped."""
import asyncio
import copy
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager, RuntimeOperationPending
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager


WORKER = r'''
import json, os, sys, time
pending = None
def emit(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    data = json.loads(line)
    if data['op'] == 'start':
        emit({'type': 'runtime.ready'})
    elif data['op'] == 'send':
        with open(sys.argv[1], 'a') as log: log.write(data['input_id'] + '\n')
        if data['text'] == 'original':
            emit({'type': 'generation.started', 'generation_id': 'original-generation',
                  'initial_input_id': data['input_id'], 'input_ids': [data['input_id']],
                  'interruptionRevision': data['interruptionRevision'], 'observedAt': time.time()})
            emit({'op': 'reply', 'id': data['id'], 'result': {'accepted': True, 'inputId': data['input_id']}})
            emit({'type': 'input.delivered', 'input_id': data['input_id']})
        else:
            pending = data
    elif data['op'] == 'release':
        if data.get('exit'): os._exit(7)
        result = {'accepted': True, 'inputId': pending['input_id']}
        if data.get('mismatch'): result['inputId'] = 'unrelated-input'
        reply = {'op': 'reply', 'id': pending['id'], 'result': result}
        if data.get('error'): reply['error'] = 'Input rejected'
        emit(reply)
    elif data['op'] == 'stop':
        break
'''


@pytest.fixture
async def running(tmp_path, monkeypatch):
    worker, inputs = tmp_path / 'worker.py', tmp_path / 'inputs.txt'
    worker.write_text(WORKER)
    runtime = RuntimeManager(command=[sys.executable, str(worker), str(inputs)],
                             retention={'prewarm_on_select': False})
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await service.dispatch('session.create', {})
    await service.dispatch('conversation.send', {'text': 'original'}, command_id='original-input')
    async with asyncio.timeout(2):
        while (service._session()['status'] != 'working'
               or (service._session().get('collaborationGeneration') or {}).get('inputIds') != ['original-input']
               or not any(turn.get('generationId') == 'original-generation' and turn.get('runtimeOwnerId')
                          for turn in service._session().get('execution', {}).get('turns', []))):
            await asyncio.sleep(0)
    original_wait = asyncio.wait_for

    async def short_ack_timeout(awaitable, timeout):
        return await original_wait(awaitable, .03 if timeout == 30 else timeout)

    monkeypatch.setattr(asyncio, 'wait_for', short_ack_timeout)
    try:
        yield service, inputs
    finally:
        await service.close()


async def timed_out_followup(service):
    with pytest.raises(RuntimeOperationPending, match='may still be running'):
        await service.dispatch('conversation.send', {'text': 'follow-up'}, command_id='followup-input')
    return next(m for m in service._session()['messages'] if m.get('inputId') == 'followup-input')


async def release(service, **args):
    row = service.runtime.workers[service._session()['id']]
    await service.runtime._write(row, {'op': 'release', **args})
    async with asyncio.timeout(2):
        while service.runtime.has_pending_operations():
            await asyncio.sleep(0)
    # The reader publishes acknowledgement after removing its in-flight fence.
    await asyncio.sleep(.02)


async def test_timeout_preserves_live_turn_draft_and_unknown_delivery(running):
    service, inputs = running
    await service.dispatch('view.update', {'patch': {'draft': 'new unsent draft'}})
    message = await timed_out_followup(service)
    session = service._session()
    turn, = session['execution']['turns']
    assert turn['id'] == 'original-input' and turn['phase'] == 'running'
    assert turn['generationId'] == 'original-generation'
    assert turn['runtimeOwnerId'] == service.runtime.workers[session['id']]['runtimeOwnerId']
    assert message['delivery']['status'] == 'unknown'
    assert session['status'] == 'working'
    assert not session.get('error')
    assert service.state['view']['draft'] == 'new unsent draft'
    assert UpdateManager(service).busy()
    await service.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'working',
        'event': 'input.delivered', 'inputId': 'original-input'})
    assert message['delivery']['status'] == 'unknown'
    duplicate = await service.dispatch('conversation.send', {'text': 'follow-up'}, command_id='followup-input')
    assert duplicate['duplicate'] and duplicate['delivery'] == 'unknown'
    assert inputs.read_text().splitlines() == ['original-input', 'followup-input']


@pytest.mark.parametrize('settled', [False, True])
async def test_late_ack_reconciles_exact_input_without_restarting_turn(running, settled):
    service, inputs = running
    message = await timed_out_followup(service)
    session = service._session()
    if settled:
        await service.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    status = session['status']
    turns = copy.deepcopy(session['execution']['turns'])
    await release(service)
    assert message['delivery']['status'] == 'accepted'
    assert session['status'] == status
    assert session['execution']['turns'] == turns
    duplicate = await service.dispatch('conversation.send', {'text': 'follow-up'}, command_id='followup-input')
    assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
    assert inputs.read_text().splitlines() == ['original-input', 'followup-input']


@pytest.mark.parametrize('result', [{'mismatch': True}, {'error': True}])
async def test_unrelated_or_rejected_reply_does_not_invent_acceptance(running, result):
    service, _ = running
    message = await timed_out_followup(service)
    await release(service, **result)
    assert message['delivery']['status'] == 'unknown'


async def test_real_process_exit_still_reports_failure(running):
    service, _ = running
    await timed_out_followup(service)
    row = service.runtime.workers[service._session()['id']]
    await service.runtime._write(row, {'op': 'release', 'exit': True})
    async with asyncio.timeout(2):
        while 'exited (code 7)' not in service._session().get('error', ''):
            await asyncio.sleep(0)
    assert service._session()['status'] == 'error'


async def test_http_timeout_reports_uncertainty_without_a_stopped_banner(tmp_path, authenticated_client):
    from amplifier_web.server import create_app

    class PendingRuntime:
        async def start(self, session, emit):
            pass

        async def send(self, session, text, input_id, emit):
            await emit('runtime.status', {'sessionId': session['id'], 'status': 'working'})
            raise RuntimeOperationPending('send')

        async def close(self):
            pass

    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=PendingRuntime(),
                           voice=False, preload_providers=False, background_updates=False)
    client = await authenticated_client(app)
    created = await client.post('/api/actions', json={'action': 'session.create', 'args': {}})
    assert created.status == 200
    response = await client.post('/api/actions', json={'id': 'uncertain-send',
        'action': 'conversation.send', 'args': {'text': 'one input'}})
    result = await response.json()
    assert response.status == 504
    assert result['code'] == 'runtime_pending' and result['delivery'] == 'unknown'
    assert 'accepted' not in result
    state = await (await client.get('/api/state')).json()
    session = next(s for s in state['sessions'] if s['id'] == state['selectedSessionId'])
    assert session['status'] == 'working' and not session.get('error')
    assert session['messages'][-1]['delivery']['status'] == 'unknown'


@pytest.mark.parametrize('mode,origin,status', [
    ('reply', 'worker_reply', 'valid'),
    ('decode-noise', 'worker_reply', 'valid'),
    ('malformed', 'worker_reply', 'invalid'),
    ('missing', 'worker_reply', 'invalid'),
    ('disconnect', 'reader_disconnect', 'not_applicable'),
])
async def test_send_failure_witness_distinguishes_transport_without_replay(tmp_path, mode, origin, status):
    """Real pipe reader/reply and durable send owner; no native provider needed."""
    facts = {'errors': [{'class': 'PermissionError', 'errno': 13, 'winerror': 5, 'frames': []}],
             'truncated': False}
    worker = tmp_path / 'failure_worker.py'
    inputs = tmp_path / 'inputs.txt'
    worker.write_text("""
import json, os, sys
def emit(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    data = json.loads(line)
    if data['op'] == 'start': emit({'type':'runtime.ready'})
    elif data['op'] == 'send':
        with open(sys.argv[1], 'a') as log: log.write(data['input_id'] + '\\n')
        mode = sys.argv[2]
        if mode == 'disconnect': os._exit(7)
        row = {'op':'reply','id':data['id'],'error':'PRIVATE exception /secret/path'}
        if mode == 'decode-noise':
            sys.stdout.buffer.write(b'\\xff\\nnot-json\\n');sys.stdout.buffer.flush()
        if mode in ('reply','decode-noise'): row['failureFacts'] = json.loads(sys.argv[3])
        if mode == 'malformed': row['failureFacts'] = {'message':'PRIVATE','errors':[],'truncated':False}
        emit(row)
    elif data['op'] == 'stop': break
""")
    runtime = RuntimeManager(command=[sys.executable, str(worker), str(inputs), mode, json.dumps(facts)],
                             retention={'prewarm_on_select': False})
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    try:
        await service.dispatch('session.create', {})
        with pytest.raises(RuntimeError) as caught:
            await service.dispatch('conversation.send', {'text': 'one input'}, command_id='witness-input')
        from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
        witness = validate_runtime_failure_witness(caught.value._runtime_failure_witness)
        expected = {'schema': 1, 'origin': origin, 'factsStatus': status,
                    **(facts if status == 'valid' else {'errors': [], 'truncated': False})}
        if mode == 'disconnect':
            expected['reader'] = {'reason': 'eof', 'phase': 'settle_execution', 'frame': 'ready',
                'exitObserved': True, 'exitCode': 7, 'closing': False,
                'stopReason': 'none', 'cleanup': 'not_requested'}
        assert witness == expected
        assert 'PRIVATE' not in json.dumps(witness)
        assert 'accepted' not in witness and 'retry' not in witness
        session = service._session()
        assert session['messages'][-1]['delivery']['status'] == 'unknown'
        duplicate = await service.dispatch('conversation.send', {'text': 'one input'}, command_id='witness-input')
        assert duplicate['duplicate'] and duplicate['delivery'] == 'unknown'
        assert inputs.read_text().splitlines() == ['witness-input']
        assert '_runtime_failure_witness' not in json.dumps(service.browser_state())
        assert 'PRIVATE' not in json.dumps(service.browser_state())
    finally:
        await service.close()


@pytest.mark.parametrize('shutdown', [False, True])
@pytest.mark.parametrize('phase', ['read', 'dispatch'])
async def test_cancelled_reader_records_terminal_branch_without_replaying_input(
        tmp_path, monkeypatch, record_property, shutdown, phase):
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    from amplifier_web.runtime_event_batches import BufferedLines
    worker, inputs = tmp_path / 'cancel_worker.py', tmp_path / 'inputs.txt'
    worker.write_text("""
import json,sys
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start': print(json.dumps({'type':'runtime.ready'}),flush=True)
    elif data['op']=='send':
        with open(sys.argv[1],'a') as log:log.write(data['input_id']+'\\n')
    elif data['op']=='stop':break
""")
    runtime = RuntimeManager(command=[sys.executable, str(worker), str(inputs)],
                             retention={'prewarm_on_select': False})
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    ready_dispatch, release_ready, reading = asyncio.Event(), asyncio.Event(), asyncio.Event()
    emit = service.on_runtime_event
    next_line = BufferedLines.next
    reads = 0

    async def observed_emit(kind, payload):
        if kind == 'runtime.status' and payload.get('status') == 'ready':
            ready_dispatch.set()
            await release_ready.wait()
        await emit(kind, payload)

    async def observed_next(lines):
        nonlocal reads
        reads += 1
        if reads == 2:
            reading.set()
        return await next_line(lines)

    monkeypatch.setattr(service, 'on_runtime_event', observed_emit)
    monkeypatch.setattr(BufferedLines, 'next', observed_next)
    task = None
    try:
        await service.dispatch('session.create', {})
        task = asyncio.create_task(service.dispatch('conversation.send', {'text': 'one input'},
                                                   command_id='cancel-input'))
        async with asyncio.timeout(3):
            while not inputs.exists() or not inputs.read_text().endswith('\n'):
                await asyncio.sleep(.005)
            await ready_dispatch.wait()
        row = runtime.workers[service._session()['id']]
        # The send has crossed the real pipe while readiness publication still
        # waits. A worker input log alone never establishes the reader's phase.
        assert inputs.read_text().splitlines() == ['cancel-input']
        assert row['ready'].done() and not reading.is_set() and reads == 1
        assert not task.done() and runtime.has_pending_operations()
        inflight = set(row['inflight'])
        assert len(inflight) == 1
        if phase == 'read':
            release_ready.set()
            async with asyncio.timeout(3):
                await reading.wait()
            assert reads == 2
        if shutdown:
            # Use the same admission fence as AppService.close, not a partial
            # worker-row mutation that leaves the service accepting input.
            service.begin_shutdown()
            assert service.closed and runtime._closed
            assert row['stop_reason'] == 'shutdown' and row['closing']
        else:
            assert not service.closed and not row['closing']
        row['reader'].cancel()
        await asyncio.gather(row['reader'], return_exceptions=True)
        with pytest.raises(RuntimeError) as caught:
            await task
        witness = validate_runtime_failure_witness(caught.value._runtime_failure_witness)
        assert witness['origin'] == 'reader_disconnect' and witness['factsStatus'] == 'valid'
        assert witness['errors'][0]['class'] == 'CancelledError'
        assert witness['reader'] == {'reason': 'cancelled', 'phase': phase, 'frame': 'ready',
            'exitObserved': False, 'exitCode': None, 'closing': shutdown,
            'stopReason': 'shutdown' if shutdown else 'none', 'cleanup': 'not_requested'}
        assert row['process'].returncode is None  # Cancellation is not process death.
        assert service._session()['messages'][-1]['delivery']['status'] == 'unknown'
        duplicate = await service.dispatch('conversation.send', {'text': 'one input'}, command_id='cancel-input')
        assert duplicate['duplicate'] and duplicate['delivery'] == 'unknown'
        assert inputs.read_text().splitlines() == ['cancel-input']
        assert not row['pending']  # The caller wait has settled, not the input.
        assert row['inflight'] == inflight and runtime.has_pending_operations()
        receipt = json.loads(service.db.execute(
            'SELECT receipt FROM commands WHERE id=?', ('cancel-input',)).fetchone()[0])
        assert receipt['delivery'] == 'unknown'
        record_property('cancelled_reader_boundary', json.dumps({
            'expectedPhase': phase, 'shutdown': shutdown, 'readCalls': reads,
            'readyDispatchEntered': ready_dispatch.is_set(), 'readerEnteredRead': reading.is_set(),
            'reader': witness['reader'], 'delivery': receipt['delivery'],
            'inflightRetained': sorted(row['inflight']), 'callerWaits': len(row['pending']),
            'inputs': inputs.read_text().splitlines()}))
    finally:
        release_ready.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.parametrize('outer', [False, True])
async def test_worker_captures_original_error_before_string_reply(monkeypatch, outer):
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.worker_diagnostics import validate_command_failure_facts
    from unittest.mock import Mock
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    worker = Worker()
    if outer:
        monkeypatch.setattr(worker, 'bind_activation',
                            Mock(side_effect=PermissionError(13, 'PRIVATE', '/secret/path')))
        await worker.command({'op': 'send', 'id': 'failure-fixture'})
        worker.bind_activation.assert_called_once_with()
    else:
        await worker._command_serial({'op': 'unknown-fixture', 'id': 'failure-fixture'})
    reply, = [row for row in replies if row.get('op') == 'reply']
    facts = validate_command_failure_facts(reply['failureFacts'])
    assert facts is not None
    # The inner path rejects an unstarted worker before interpreting its op.
    assert facts['errors'][0]['class'] == ('PermissionError' if outer else 'RuntimeError')
    if outer:
        assert facts['errors'][0]['errno'] == 13
    assert 'PRIVATE' not in json.dumps(facts) and '/secret' not in json.dumps(facts)
    assert reply['id'] == 'failure-fixture' and 'result' not in reply
