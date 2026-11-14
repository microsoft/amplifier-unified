"""Steering contracts run with worker dependencies, without the host service."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.conversation_steering import submit


def owned_worker(runtime, session, execution):
    from amplifier_web.runtime_worker import Worker
    from amplifier_foundation.session.activation import ActivationGate
    worker = Worker()
    worker.runtime, worker.session, worker.execution = runtime, session, execution
    worker.shared_handle = SimpleNamespace(owner={'app': 'fixture'})
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.controls = SimpleNamespace(preserve_configuration=False)
    return worker


async def test_worker_rechecks_target_after_input_preparation(monkeypatch):
    pytest.importorskip('amplifier_module_loop_live.runtime')
    runtime = SimpleNamespace(closed=False, generation={'id': 'old'}, max_input_chars=10000)
    capability = {'version': 1, 'mode': 'request_boundary', 'submit': AsyncMock()}
    controls = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=lambda name: capability if name == 'live.steering' else None))
    async def prepare(*args, **kwargs):
        runtime.generation = {'id': 'new'}
        return 'pause'
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    result = await submit(controls, runtime, {'text': 'pause', 'inputId': 'correction',
        'targetGenerationId': 'old'}, None, lambda: 0)
    assert result['accepted'] is False
    capability['submit'].assert_not_awaited()


@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('route', ['strict', 'peer', 'ordinary', 'ordinary_attachment_quote'])
async def test_real_loop_drains_bursts_including_final_checkpoint_without_new_generation(monkeypatch, tmp_path, late, route):
    """Real core/context/loop, scripted provider/tool; no model account required."""
    live = pytest.importorskip('amplifier_module_loop_live.runtime')
    pytest.importorskip('amplifier_module_context_simple')
    from amplifier_core import AmplifierSession, ToolResult
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    requests, responses = asyncio.Queue(), asyncio.Queue()
    class Provider:
        async def complete(self, request, **kwargs):
            await requests.put(request)
            return await responses.get()
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        def get_info(self):
            return SimpleNamespace(default_model='fixture')
    class Step:
        name, description, input_schema = 'step', 'One step', {'type': 'object', 'properties': {}}
        async def execute(self, args):
            return ToolResult(success=True, output={'done': True})
    runtime = live.Runtime()
    session = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
        'config': {'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=runtime.session_id)
    task = None
    try:
        await session.initialize()
        await session.coordinator.mount('providers', Provider(), name='fixture')
        await session.coordinator.mount('tools', Step(), name='step')
        from amplifier_web.attachments import save, encode
        import base64
        attachment = save(tmp_path, 'reference.txt', base64.b64encode(b'Exact attached reference').decode())
        monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
        session.coordinator.register_capability('live.attachments.encode', encode)
        session.coordinator.register_capability('live.runtime', runtime)
        task = asyncio.create_task(session.execute(''))
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', 5)
        await runtime.submit(live.Input('user', 'Do several steps', id='original'))
        await asyncio.wait_for(requests.get(), 5)
        generation = runtime.generation['id']
        worker = owned_worker(runtime, session, task)
        replies = []
        monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
        entered, release = asyncio.Event(), asyncio.Event()
        if late:
            async def checkpoint(*args):
                entered.set()
                await release.wait()
            session.coordinator.register_capability('live.checkpoint', checkpoint)
            await responses.put(ChatResponse(content=[TextBlock(text='Already finished')]))
            await asyncio.wait_for(entered.wait(), 5)
        bodies = []
        for i in range(3):
            args = {'text': f'Correction {i}', 'inputId': f'correction-{i}',
                    'targetGenerationId': generation}
            controls = SimpleNamespace(coordinator=session.coordinator)
            body = args['text']
            if route == 'peer':
                from amplifier_web.collaboration_input import steer
                from amplifier_operations.coordination import peer_input
                controls.tasks = SimpleNamespace(record=lambda: {}, continuation_allowed=AsyncMock(return_value=True))
                message = {'peerEnvelope': {'inputId': args['inputId'], 'requestId': args['inputId'],
                    'recipientSessionId': runtime.session_id, 'senderSessionId': 'source',
                    'mode': 'steer'}, 'text': body}
                authorize = AsyncMock(return_value={'admitted': True, 'message': message})
                result = await steer(controls, runtime, args, None, authorize)
                assert authorize.await_count == 2
                body = peer_input(message['peerEnvelope'], body)
            elif route.startswith('ordinary'):
                payload = {}
                if route == 'ordinary_attachment_quote':
                    from amplifier_web.message_interactions import quoted_text
                    quote = {'sessionId': runtime.session_id, 'messageId': f'quoted-{i}', 'role': 'assistant',
                             'excerpt': f'Historical source {i}', 'truncated': False}
                    payload = {'reply_context': quote, 'attachments': [attachment]}
                    body = quoted_text(body, quote)
                await worker.command({'op': 'send', 'id': args['inputId'], 'input_id': args['inputId'],
                                      'text': args['text'], 'interruptionRevision': 0, **payload})
                assert replies[-1]['result'] == {'accepted': True, 'inputId': args['inputId']}
                if not late:
                    await runtime.wait_for(lambda e: e['type'] == 'input.queued'
                                           and e.get('input_id') == args['inputId'], 5)
                result = {'accepted': True, 'disposition': 'queued'}
            else:
                result = await submit(controls, runtime, args, None, lambda: 0)
            bodies.append(body)
            assert result['accepted'] and result['disposition'] == 'queued'
        if late:
            release.set()
        else:
            await responses.put(ChatResponse(content=[], tool_calls=[ToolCall(id='step-1', name='step', arguments={})]))
        request = await asyncio.wait_for(requests.get(), 5)
        text = str(request.messages)
        positions = [text.index(f'Correction {i}') for i in range(3)]
        assert positions == sorted(positions)
        assert all(text.count(f'Correction {i}') == 1 for i in range(3))
        assert text.count('Do several steps') == 1
        if route == 'ordinary_attachment_quote':
            assert text.count('Exact attached reference') == 3
            assert all(text.count(f'Historical source {i}') == 1 for i in range(3))
        await responses.put(ChatResponse(content=[TextBlock(text='All corrections considered')]))
        finished = await runtime.wait_for(lambda e: e['type'] == 'generation.finished'
                                           and e['generation_id'] == generation, 5)
        await runtime.wait_for(lambda e: e['type'] == 'session.idle' and e['sequence'] > finished['sequence'], 5)
        assert len([e for e in runtime.events if e['type'] == 'generation.started']) == 1
        assert requests.empty()
        assert finished['generation_id'] == generation
        assert all(f'correction-{i}' in finished['input_ids'] for i in range(3))
        # The producer already persists steering identity. The display reader
        # must not discard it merely because delivery occurred mid-generation.
        from amplifier_web.automatic_history import display_message
        transcript = await session.coordinator.get('context').get_messages()
        inputs = [row for row in transcript if row.get('role') == 'user'
                  and (row.get('metadata') or {}).get('amplifier_input')]
        markers = [(row.get('metadata') or {}).get('amplifier_input') for row in inputs]
        assert [(marker['id'], marker['kind']) for marker in markers] == [
            ('original', 'user'), *((f'correction-{i}', 'user' if route.startswith('ordinary') else 'steer') for i in range(3))]
        assert [display_message(row, i, {'id': runtime.session_id}).get('nativeInputId')
                for i, row in enumerate(inputs)] == ['original', *[f'correction-{i}' for i in range(3)]]
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()


@pytest.mark.parametrize('flag', ['steering_closed', 'stop_requested'])
async def test_worker_rejects_closing_or_stopped_run_before_admission(monkeypatch, flag):
    pytest.importorskip('amplifier_module_loop_live.runtime')
    runtime = SimpleNamespace(closed=False, generation={'id': 'run'}, max_input_chars=10000)
    setattr(runtime, flag, True)
    capability = {'version': 1, 'mode': 'request_boundary', 'submit': AsyncMock()}
    controls = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=lambda name: capability if name == 'live.steering' else None))
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', AsyncMock(return_value='pause'))
    result = await submit(controls, runtime, {'text': 'pause', 'inputId': 'late',
        'targetGenerationId': 'run'}, None, lambda: 0)
    assert result['accepted'] is False
    capability['submit'].assert_not_awaited()


@pytest.mark.parametrize('stop_at', ['preparation', 'admission_lock'])
async def test_outer_worker_stop_fences_waiting_ordinary_input(monkeypatch, stop_at):
    from amplifier_module_loop_live.runtime import Runtime
    runtime = Runtime()
    worker = owned_worker(runtime, SimpleNamespace(coordinator=object()), object())
    replies, entered, release = [], asyncio.Event(), asyncio.Event()
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    async def prepare(*args, **kwargs):
        entered.set()
        await release.wait()
        return 'prepared quote and text'
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    if stop_at == 'admission_lock':
        await worker.command_lock.acquire()
    task = asyncio.create_task(worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'old-intent', 'text': 'ordinary'}))
    if stop_at == 'preparation':
        await asyncio.wait_for(entered.wait(), 1)
    else:
        await asyncio.sleep(0)
    await worker.command({'op': 'stop'})
    release.set()
    if stop_at == 'admission_lock':
        worker.command_lock.release()
    await asyncio.wait_for(task, 1)
    result = next(row['result'] for row in replies if row.get('id') == 'rpc')
    assert result['accepted'] is False and result['effect'] == 'none'
    assert 'old-intent' not in runtime.accepted
    assert runtime.stop_requested


async def test_outer_worker_rechecks_owner_and_preserves_payload_dedup(monkeypatch):
    from amplifier_module_loop_live.runtime import Runtime
    runtime = Runtime()
    worker = owned_worker(runtime, SimpleNamespace(coordinator=object()), object())
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    prepare = AsyncMock(return_value='prepared quote and text')
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    args = {'op': 'send', 'id': 'rpc', 'input_id': 'ordinary', 'text': 'original text',
            'reply_context': {'id': 'quote'}, 'attachments': [{'id': 'file'}],
            'interruptionRevision': 7, 'context_binding': {'clientId': 'browser', 'targets': []}}
    await worker.command(args)
    await worker.command({**args, 'id': 'duplicate-rpc'})
    assert runtime.queued_inputs == 1
    command = runtime.accepted['ordinary']
    assert command.kind == 'user' and command.target_generation_id is None
    assert command.text == 'prepared quote and text' and command.attachments == ({'id': 'file'},)
    assert worker.context_bindings['ordinary']['interruptionRevision'] == 7
    assert worker.context_bindings['ordinary']['clientId'] == 'browser'
    prepare.assert_awaited_with(worker.session.coordinator, 'original text', {'id': 'quote'}, max_chars=16000)
    prepare.return_value = 'different prepared contents'
    await worker.command({**args, 'id': 'changed-rpc'})
    assert 'identity reused' in replies[-1]['error']
    assert runtime.queued_inputs == 1
    worker.ownership.yielding = True
    await worker.command({**args, 'id': 'foreign-rpc', 'input_id': 'foreign'})
    assert replies[-1]['code'] == 'session_busy'
    assert 'foreign' not in runtime.accepted


@pytest.mark.parametrize('active', [False, True])
async def test_pending_configuration_is_decided_by_actual_worker_before_admission(monkeypatch, active):
    from amplifier_module_loop_live.runtime import Runtime
    runtime = Runtime()
    if active:
        runtime.generation = {'id': 'actual-current', 'input_ids': ['original']}
    worker = owned_worker(runtime, SimpleNamespace(coordinator=object()), object())
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    prepare = AsyncMock(return_value='ordinary guidance')
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    await worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'ordinary', 'text': 'guidance',
                          'configuration_if_idle': True})
    result = replies[-1]['result']
    assert result['accepted'] is active
    assert ('ordinary' in runtime.accepted) is active
    if not active:
        assert result['effect'] == 'none' and result['configurationRequired'] is True
        prepare.assert_not_awaited()


@pytest.mark.parametrize('owner_result', [
    {'accepted': True, 'inputId': 'ordinary'},
    {'accepted': False, 'effect': 'none', 'configurationRequired': True},
    {'accepted': False, 'disposition': 'unknown'},
])
async def test_manager_pending_configuration_uses_finite_owner_admission_only(owner_result):
    from amplifier_web.runtime import RuntimeManager
    manager = RuntimeManager()
    ready = asyncio.get_running_loop().create_future()
    ready.set_result(True)
    manager.workers['chat'] = {'process': SimpleNamespace(returncode=None), 'ready': ready}
    manager._start_for_input = AsyncMock(return_value=None)
    manager._admit = AsyncMock(return_value=('row', 'rpc', 'future'))
    manager._reply = AsyncMock(return_value=owner_result)
    manager._request = AsyncMock(return_value={'accepted': True, 'inputId': 'ordinary'})
    snapshot = {'id': 'chat', 'status': 'idle',
                'pendingModelSelection': {'instance': 'next', 'model': 'next-model'}}
    result = await manager.send(snapshot, 'ordinary', 'ordinary', AsyncMock())
    assert manager._admit.call_args.args[1] == 'send'
    assert manager._admit.call_args.args[2]['configuration_if_idle'] is True
    if not owner_result.get('configurationRequired'):
        manager._start_for_input.assert_not_awaited()
        manager._request.assert_not_awaited()
    else:
        manager._start_for_input.assert_awaited_once()
        manager._request.assert_awaited_once()
    assert result == (owner_result if not owner_result.get('configurationRequired')
                      else {'accepted': True, 'inputId': 'ordinary'})
    assert snapshot['pendingModelSelection']['model'] == 'next-model'


async def test_active_configuration_capture_survives_normal_completion_during_preparation(monkeypatch):
    from amplifier_module_loop_live.runtime import Runtime
    runtime = Runtime()
    runtime.generation = {'id': 'actual-current'}
    worker = owned_worker(runtime, SimpleNamespace(coordinator=object()), object())
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    async def prepare(*args, **kwargs):
        runtime.generation = None  # Normal completion, not Stop.
        return 'ordinary guidance under captured mounted settings'
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    await worker.command({'op': 'send', 'id': 'rpc', 'input_id': 'ordinary', 'text': 'guidance',
                          'configuration_if_idle': True})
    assert replies[-1]['result']['accepted']
    assert runtime.accepted['ordinary'].target_generation_id is None
    assert runtime.queued_inputs == 1


async def test_pending_file_policy_idle_no_effect_cannot_apply_or_submit():
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    manager = RuntimeManager()
    ready = asyncio.get_running_loop().create_future()
    ready.set_result(True)
    manager.workers['chat'] = {'process': SimpleNamespace(returncode=None), 'ready': ready}
    manager._admit = AsyncMock(return_value=('row', 'rpc', 'future'))
    manager._reply = AsyncMock(return_value={'accepted': False, 'effect': 'none', 'configurationRequired': True})
    manager._start_for_input = AsyncMock()
    manager._request = AsyncMock()
    with pytest.raises(RuntimeStartupError, match='permissions'):
        await manager.send({'id': 'chat', 'fileAccessPending': True}, 'ordinary', 'input', AsyncMock())
    manager._start_for_input.assert_not_awaited()
    manager._request.assert_not_awaited()
