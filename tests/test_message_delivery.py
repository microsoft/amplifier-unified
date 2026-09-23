import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from amplifier_web.message_delivery import saved_delivery
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService, AppError
from amplifier_web.session_files import sessions_dir
from test_service import Runtime


class RecoveryRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.evidence = 'unknown'
        self.retries = []

    async def delivery(self, session, input_id):
        return self.evidence

    async def retry(self, session, text, input_id, emit):
        self.retries.append(copy.deepcopy(session))
        await self.send(session, text, input_id, emit)
        return {'accepted': True}


@pytest.fixture
async def recovery(tmp_path):
    runtime = RecoveryRuntime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    sender = runtime.send
    runtime.send = AsyncMock(side_effect=RuntimeError('Reply lost'))
    args = {'sessionId': app._session()['id'], 'text': 'Original request', 'preserveDraft': True}
    with pytest.raises(RuntimeError):
        await app.dispatch('conversation.send', args, command_id='original')
    runtime.send = sender
    yield app, runtime, args
    if not getattr(app, '_test_closed', False):
        await app.close()


def recovery_args(app):
    return {'sessionId': app._session()['id'], 'inputId': 'original'}


async def test_check_is_passive_and_explains_uncertainty(recovery):
    app, runtime, _ = recovery
    for _ in range(2):
        result = await app.dispatch('conversation.delivery', recovery_args(app), origin='agent')
        assert result['result']['delivery'] == 'unknown'
        assert 'does not resend' in result['result']['message']
    assert runtime.sent == [] and runtime.retries == [] and runtime.started == []
    assert app._session()['status'] == 'error'
    with pytest.raises(AppError, match='Confirm'):
        await app.dispatch('conversation.retry', recovery_args(app), origin='agent')


async def test_check_reconciles_positive_evidence_and_original_receipt(recovery):
    app, runtime, args = recovery
    runtime.evidence = 'accepted'
    result = await app.dispatch('conversation.delivery', recovery_args(app))
    assert result['result']['delivery'] == 'accepted'
    duplicate = await app.dispatch('conversation.send', args, command_id='original')
    assert duplicate['delivery'] == 'accepted'
    result = await app.dispatch('conversation.retry', recovery_args(app))
    assert result['result']['resent'] is False
    assert runtime.retries == []


async def test_retry_preserves_message_attachments_draft_and_identity(recovery):
    app, runtime, _ = recovery
    original = app._session()['messages'][-1]
    original['attachments'] = [{'id': 'kept', 'name': 'reference.txt'}]
    before = copy.deepcopy(original)
    app.state['view']['draft'] = 'Unsent next thought'
    args = {**recovery_args(app), 'confirmUncertain': True}
    result = await app.dispatch('conversation.retry', args, origin='agent', command_id='retry-command')
    assert result['result'] == {'delivery': 'accepted', 'resent': True}
    assert runtime.sent == [(app._session()['id'], 'Original request', 'original')]
    users = [m for m in app._session()['messages'] if m['role'] == 'user']
    assert len(users) == 1
    assert {k: users[0][k] for k in before if k != 'delivery'} == {k: v for k, v in before.items() if k != 'delivery'}
    assert runtime.retries[0]['messages'][-1]['attachments'] == before['attachments']
    assert app.state['view']['draft'] == 'Unsent next thought'
    cached = await app.dispatch('conversation.retry', args, origin='agent', command_id='retry-command')
    assert cached['result'] == result['result']
    assert len(runtime.retries) == 1


async def test_concurrent_retry_is_admitted_once(recovery):
    app, runtime, _ = recovery
    entered, finish = asyncio.Event(), asyncio.Event()
    retry = runtime.retry
    async def delayed(*args):
        entered.set()
        await finish.wait()
        return await retry(*args)
    runtime.retry = delayed
    args = {**recovery_args(app), 'confirmUncertain': True}
    task = asyncio.create_task(app.dispatch('conversation.retry', args, command_id='one'))
    await entered.wait()
    duplicate = await app.dispatch('conversation.retry', args, command_id='one')
    assert duplicate['duplicate']
    with pytest.raises(AppError, match='settle'):
        await app.dispatch('conversation.retry', args, command_id='two')
    finish.set()
    await task
    assert len(runtime.retries) == 1


async def test_retry_after_restart_requires_explicit_action_and_not_outbox(recovery, tmp_path):
    app, _, _ = recovery
    sid = app._session()['id']
    await app.close()
    app._test_closed = True
    runtime = RecoveryRuntime()
    restored = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        assert runtime.sent == []
        await restored.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': 'original'})
        assert runtime.sent == []
        await restored.dispatch('conversation.retry', {'sessionId': sid, 'inputId': 'original', 'confirmUncertain': True}, origin='agent')
        assert runtime.sent[0][2] == 'original'
    finally:
        await restored.close()


async def test_unknown_input_not_saved_is_distinct_and_cannot_resend(recovery):
    app, runtime, _ = recovery
    args = {**recovery_args(app), 'inputId': 'missing'}
    assert (await app.dispatch('conversation.delivery', args))['result']['delivery'] == 'not_saved'
    with pytest.raises(AppError, match='not saved'):
        await app.dispatch('conversation.retry', {**args, 'confirmUncertain': True})
    assert not runtime.sent


async def test_retry_never_reorders_later_messages(recovery):
    app, runtime, _ = recovery
    app._message(app._session(), 'user', 'Later request', 'chat', inputId='later')
    with pytest.raises(AppError, match='latest'):
        await app.dispatch('conversation.retry', {**recovery_args(app), 'confirmUncertain': True})
    assert not runtime.sent


async def test_history_check_does_not_start_worker_or_infer_absence(tmp_path):
    session = {'id': 'fixture', 'workspace': str(tmp_path)}
    runtime = RuntimeManager()
    try:
        assert await runtime.delivery(session, 'original') == 'unknown'
        path = sessions_dir(tmp_path) / 'fixture' / 'transcript.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'role': 'user', 'content': 'private', 'metadata': {'amplifier_input': {'id': 'original'}}})+'\n')
        assert await runtime.delivery(session, 'original') == 'accepted'
        assert await runtime.delivery(session, 'absent') == 'unknown'
        assert not runtime.workers
    finally:
        await runtime.close()


@pytest.mark.parametrize('source', ['inbox', 'restored_context'])
async def test_worker_retry_never_resubmits_accepted_input(source):
    worker = Worker()
    worker.execution = object()
    worker.runtime = SimpleNamespace(accepted={'original': object()} if source == 'inbox' else {}, submit=AsyncMock())
    context = SimpleNamespace(get_messages=AsyncMock(return_value=[{'role': 'user', 'metadata': {'amplifier_input': {'id': 'original'}}}]))
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: context))
    with patch('amplifier_web.runtime_worker.publish') as publish:
        await worker._command_serial({'op': 'retry', 'id': 'r', 'input_id': 'original', 'text': 'Same input'})
    assert publish.call_args.args[0]['result']['duplicate'] is True
    worker.runtime.submit.assert_not_awaited()


async def test_duplicate_retry_restores_settled_turn(recovery):
    app, runtime, _ = recovery
    before = copy.deepcopy(app._session()['execution']['turns'][-1])
    runtime.retry = AsyncMock(return_value={'accepted': True, 'duplicate': True})
    result = await app.dispatch('conversation.retry', {**recovery_args(app), 'confirmUncertain': True})
    assert result['result']['resent'] is False
    assert app._session()['status'] == 'error'
    after = app._session()['execution']['turns'][-1]
    for key in ('phase', 'endedAt'):
        assert after.get(key) == before.get(key)
    assert not runtime.sent


async def test_retry_ownership_failure_preserves_history_and_settles_state(recovery):
    from amplifier_web.runtime import SessionInUseError
    app, runtime, _ = recovery
    before = copy.deepcopy(app._session()['messages'])
    runtime.retry = AsyncMock(side_effect=SessionInUseError({'app': 'amplifier-cli', 'pid': 123}))
    args = {**recovery_args(app), 'confirmUncertain': True}
    with pytest.raises(AppError, match='owned elsewhere'):
        await app.dispatch('conversation.retry', args, command_id='retry-owned')
    assert app._session()['messages'] == before
    assert app._session()['ownership']['status'] == 'blocked'
    assert app._session()['execution']['turns'][-1]['phase'] == 'stopped'
    cached = await app.dispatch('conversation.retry', args, command_id='retry-owned')
    assert cached['result']['delivery'] == 'unknown'
    runtime.retry.assert_awaited_once()
    # Inspection still works when this host does not own the conversation.
    runtime.evidence = 'accepted'
    checked = await app.dispatch('conversation.delivery', recovery_args(app))
    assert checked['result']['delivery'] == 'accepted'


async def test_late_acceptance_during_check_is_never_downgraded(recovery):
    app, runtime, _ = recovery
    async def probe(*args):
        app._delivery(app._session(), 'original', 'accepted')
        return 'unknown'
    runtime.delivery = probe
    result = await app.dispatch('conversation.delivery', recovery_args(app))
    assert result['result']['delivery'] == 'accepted'


async def test_worker_retry_requires_ownership_but_check_does_not():
    from amplifier_foundation.session import SessionBusyError
    worker = Worker()
    worker.execution = object()
    worker.runtime = SimpleNamespace(accepted={'original': object()}, submit=AsyncMock())
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: None))
    worker.acquire_for_mutation = AsyncMock(side_effect=SessionBusyError({'app': 'amplifier-cli', 'pid': 123}))
    with patch('amplifier_web.runtime_worker.publish') as publish:
        await worker.command({'op': 'delivery', 'id': 'check', 'input_id': 'original'})
        assert publish.call_args.args[0]['result']['delivery'] == 'accepted'
        worker.acquire_for_mutation.assert_not_awaited()
        await worker.command({'op': 'retry', 'id': 'retry', 'input_id': 'original', 'text': 'Request'})
        assert publish.call_args.args[0]['code'] == 'session_busy'
    worker.acquire_for_mutation.assert_awaited_once()
    worker.runtime.submit.assert_not_awaited()


async def test_connected_client_recovery_uses_shared_passive_and_explicit_actions(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    runtime = RecoveryRuntime()
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    sender = runtime.send
    runtime.send = AsyncMock(side_effect=RuntimeError('Synthetic lost reply'))
    with pytest.raises(RuntimeError):
        await service.dispatch('conversation.send', {'sessionId': sid, 'text': 'Saved request'}, command_id='saved-input')
    runtime.send = sender
    attached = await client.post('/api/clients/attach', json={'clientId': 'recovery-client', 'kind': 'tui', 'protocolVersion': 1})
    assert attached.status == 200
    endpoint = f'/api/sessions/{sid}/commands'
    headers = {'X-Amplifier-Client': 'recovery-client'}
    async def action(name, identity, **args):
        return await client.post(endpoint, json={'id': identity, 'action': name, 'args': {'inputId': 'saved-input', **args}}, headers=headers)
    checked = await action('conversation.delivery', 'check-once')
    assert checked.status == 200
    assert (await checked.json())['result']['delivery'] == 'unknown'
    assert not runtime.sent and not runtime.retries
    denied = await action('conversation.retry', 'unconfirmed')
    assert denied.status == 409 and not runtime.retries
    wrong = await action('conversation.retry', 'wrong-target', sessionId='elsewhere', confirmUncertain=True)
    assert wrong.status == 400 and not runtime.retries
    resent = await action('conversation.retry', 'explicit-once', confirmUncertain=True)
    assert resent.status == 200 and (await resent.json())['result']['resent']
    duplicate = await action('conversation.retry', 'explicit-once', confirmUncertain=True)
    assert duplicate.status == 200 and (await duplicate.json())['duplicate']
    assert runtime.sent == [(sid, 'Saved request', 'saved-input')]


async def test_real_worker_startup_failure_has_durable_non_delivery_receipt(authenticated_client, tmp_path):
    import sys
    from amplifier_web.server import create_app
    runtime = RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(3)'], startup_timeout=3)
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    payload = {'action': 'conversation.send', 'args': {'sessionId': sid, 'text': 'Keep this request'}, 'id': 'startup-input'}
    response = await client.post('/api/actions', json=payload)
    data = await response.json()
    assert response.status == 503 and data['code'] == 'worker_startup_failed'
    assert data['receipt']['delivery'] == 'failed'
    assert service._session()['messages'][-1]['delivery']['status'] == 'failed'
    assert not runtime.workers
    checked = await service.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': 'startup-input'})
    assert checked['result']['delivery'] == 'failed'
    duplicate = await client.post('/api/actions', json=payload)
    assert (await duplicate.json())['receipt'] == data['receipt']
    assert not runtime.workers
    retry = {'action': 'conversation.retry', 'args': {'sessionId': sid, 'inputId': 'startup-input'}, 'id': 'startup-retry'}
    failed_retry = await client.post('/api/actions', json=retry)
    assert failed_retry.status == 503
    assert (await failed_retry.json())['receipt']['delivery'] == 'failed'
    assert service._session()['messages'][-1]['delivery']['status'] == 'failed'
    repeated_retry = await client.post('/api/actions', json=retry)
    repeated_data = await repeated_retry.json()
    assert repeated_data['result']['delivery'] == repeated_data['receipt']['delivery'] == 'failed'
    assert not runtime.workers
    replacement = RecoveryRuntime()
    service.runtime = replacement
    result = await service.dispatch('conversation.retry', {'sessionId': sid, 'inputId': 'startup-input'}, command_id='explicit-retry')
    assert result['result']['delivery'] == 'accepted'
    assert replacement.sent == [(sid, 'Keep this request', 'startup-input')]
    assert len([m for m in service._session()['messages'] if m['role'] == 'user']) == 1
    confirmed = await client.post('/api/actions', json=payload)
    confirmed_data = await confirmed.json()
    assert confirmed_data['accepted'] is True and confirmed_data['delivery'] == 'accepted'
    assert 'code' not in confirmed_data and len(replacement.sent) == 1
    await runtime.close()


async def test_retry_startup_failure_cannot_claim_earlier_attempt_was_not_delivered(recovery):
    from amplifier_web.runtime import RuntimeStartupError
    app, runtime, _ = recovery
    runtime.retry = AsyncMock(side_effect=RuntimeStartupError('Worker could not start.'))
    args = {**recovery_args(app), 'confirmUncertain': True}
    with pytest.raises(AppError) as failure:
        await app.dispatch('conversation.retry', args, command_id='failed-retry')
    assert failure.value.receipt['delivery'] == 'unknown'
    assert app._session()['messages'][-1]['delivery']['status'] == 'unknown'
    cached = await app.dispatch('conversation.retry', args, command_id='failed-retry')
    assert cached['accepted'] is False and cached['receipt']['delivery'] == 'unknown'
    runtime.retry.assert_awaited_once()
