"""Current Full policy at fitted/native preparation and actual call consumption.

Real installed delivery/worker callbacks and canonical policy records; controlled
provider responses and effect counters, with no live provider or external writes.
"""
import json
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ToolCall

from test_access_mode import save
from test_collaborative_workspaces import app
from test_work_native_effects import joined
from test_workspace_work import read, revise


async def fitted(app, mode):
    adapter, child, required, worker = await joined(app)
    if mode == 'full':
        await save(app, child)
    measured, sent = [], []

    class Provider:
        async def request_budget(self, request, **kwargs):
            measured.append(request.model_dump_json())
            return {'tokens': 100}

        async def complete(self, request, **kwargs):
            sent.append(request.model_dump_json())
            return SimpleNamespace(model_dump=lambda: {'content': []})

    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    request = adapter.request()
    await wrapped.request_budget(request)
    return adapter, child, worker, wrapped, request, measured, sent


@pytest.mark.parametrize('mode', ['ask', 'full'])
@pytest.mark.parametrize('boundary', ['fitted', 'native'])
@pytest.mark.parametrize('fault', ['metadata', 'unavailable'])
async def test_current_full_accepts_unverified_work_version_without_changing_bytes(app, mode, boundary, fault, monkeypatch):
    adapter, child, worker, wrapped, request, measured, sent = await fitted(app, mode)
    delivery = adapter.delivery.begin_work.__self__
    if boundary == 'native':
        prepared = await wrapped._prepare(request, commit=True)
        attempt = delivery.begin_work(prepared)
    if fault == 'metadata':
        owner = app.state['sessions'][0]
        creation = json.loads(app.db.execute("SELECT receipt FROM commands WHERE id='capture-delivered-pages'").fetchone()[0])
        item = (await read(app, owner, creation['result']['item']['id']))['item']
        await revise(app, owner, item, 'after-work-fitting', nextAction='Changed after fitting')
    else:
        original = delivery.bridge
        async def unavailable(operation, args):
            if operation == 'work.invocation.revalidate':
                raise TimeoutError('Synthetic assurance read unavailable')
            return await original(operation, args)
        monkeypatch.setattr(delivery, 'bridge', unavailable)

    async def finish():
        if boundary == 'native':
            await delivery.prepare_native(attempt)
        else:
            await wrapped.complete(request)

    if mode == 'ask':
        with pytest.raises((ValueError, TimeoutError)):
            await finish()
        assert not sent
    else:
        await finish()
        if boundary == 'native':
            assert prepared.model_dump_json() == measured[0]
            coverage = attempt['coverage']
        else:
            assert sent == measured
            coverage = next(reversed(delivery.attempts.values()))['coverage']
        assert coverage['complete'] is False and coverage['effectAuthorized'] is False


@pytest.mark.parametrize('boundary', ['fitted', 'native'])
@pytest.mark.parametrize('fault', ['generation', 'input', 'stop', 'cancel', 'revoke'])
async def test_full_preparation_preserves_native_scope_and_current_policy(app, boundary, fault, monkeypatch):
    adapter, child, worker, wrapped, request, measured, sent = await fitted(app, 'full')
    delivery = adapter.delivery.begin_work.__self__
    if boundary == 'native':
        prepared = await wrapped._prepare(request, commit=True)
        attempt = delivery.begin_work(prepared)
    if fault == 'generation':
        worker.runtime.generation = {'id': 'later-native-generation', 'input_ids': ['later-input']}
    elif fault == 'input':
        worker.runtime.generation['input_ids'].append('later-input')
    elif fault == 'stop':
        worker.stop_revision += 1
    elif fault == 'cancel':
        adapter.coordinator.cancellation.is_cancelled = True
    else:
        await save(app, child, mode='ask')
        original = delivery.bridge
        async def unavailable(operation, args):
            if operation == 'work.invocation.revalidate':
                raise TimeoutError('No retained Full bypass after revocation')
            return await original(operation, args)
        monkeypatch.setattr(delivery, 'bridge', unavailable)
    with pytest.raises((ValueError, TimeoutError)):
        if boundary == 'native':
            await delivery.prepare_native(attempt)
        else:
            await wrapped.complete(request)
    assert not sent


async def test_full_still_rejects_changed_fitted_bytes(app, monkeypatch):
    adapter, child, worker, wrapped, request, measured, sent = await fitted(app, 'full')
    previous = adapter.delivery.revalidate
    async def changed(value):
        value = await previous(value)
        value = value.model_copy(deep=True)
        value.messages[0].content = 'Unmeasured replacement'
        return value
    monkeypatch.setattr(adapter.delivery, 'revalidate', changed)
    with pytest.raises(ValueError, match='refit'):
        await wrapped.complete(request)
    assert not sent


@pytest.mark.parametrize('fault', ['coverage_recovery', 'proof_retention'])
async def test_full_unknown_consumed_call_survives_observation_recovery_and_proof_limit(app, fault, monkeypatch):
    adapter, child, required, worker = await joined(app)
    await save(app, child)
    delivery = adapter.delivery.begin_work.__self__
    original = delivery.bridge
    if fault == 'coverage_recovery':
        async def unavailable(operation, args):
            if operation == 'coordination.current':
                raise TimeoutError('No current Work observation')
            return await original(operation, args)
        monkeypatch.setattr(delivery, 'bridge', unavailable)
    else:
        # Represent a large current-generation execution history without
        # generating thousands of unrelated synthetic side effects.
        delivery.executed_calls.update('previous-call-' + str(i) for i in range(4096))
    effects = []
    class UnknownEffect:
        name = 'write_file'
        async def execute(self, arguments):
            effects.append(arguments)
            raise RuntimeError('Effect entered, acknowledgement unavailable')
    await adapter.coordinator.mount('tools', UnknownEffect(), name='write_file')
    arguments = {'path': 'synthetic-owned-effect'}
    class Provider:
        async def complete(self, value, **kwargs):
            return SimpleNamespace(model_dump=lambda: {'content': [{'type': 'tool_call',
                'id': 'consumed-call', 'name': 'write_file', 'input': arguments}]})
    provider = adapter.capabilities['web.provider_transform'](Provider())
    async def execute():
        await adapter.loop._execute_tool_only(ToolCall(id='consumed-call', name='write_file', arguments=arguments),
            adapter.tools, adapter.hooks, None, adapter.coordinator)
    await provider.complete(adapter.request())
    await execute()
    assert effects == [arguments] and 'consumed-call' in delivery.executed_calls
    monkeypatch.setattr(delivery, 'bridge', original)
    await provider.complete(adapter.request())
    await execute()
    assert effects == [arguments] and 'consumed-call' in delivery.executed_calls
    if fault == 'proof_retention':
        assert len(delivery.executed_calls) == 4097


async def test_consumed_ids_reset_only_for_actual_native_generation(app):
    adapter, child, required, worker = await joined(app)
    delivery = adapter.delivery.begin_work.__self__
    assert not delivery.call_consumed('retained')
    delivery.executed_calls.add('retained')
    worker.runtime.generation['input_ids'].append('more-input')
    assert delivery.call_consumed('retained')
    worker.runtime.generation = {'id': 'new-native-generation', 'input_ids': ['new-input']}
    assert not delivery.call_consumed('retained')


async def test_full_preparation_does_not_require_bounded_input_coverage(app):
    adapter, child, required, worker = await joined(app)
    await save(app, child)
    inputs = [*worker.runtime.generation['input_ids'], *('extra-' + str(i) for i in range(17))]
    worker.runtime.generation['input_ids'] = inputs
    child['collaborationGeneration']['inputIds'] = inputs
    assert adapter.coordinator.get_capability('web.work_origin_scope')()['inputCoverage'] == 'overflow'
    sent = []
    class Provider:
        async def complete(self, value, **kwargs):
            sent.append(value)
            return SimpleNamespace(model_dump=lambda: {'content': []})
    await adapter.capabilities['web.provider_transform'](Provider()).complete(adapter.request())
    assert len(sent) == 1
    attempt = next(reversed(adapter.delivery.attempts.values()))
    assert attempt['coverage']['complete'] is False
    assert attempt['coverage']['effectAuthorized'] is False


async def test_worker_full_policy_uses_verified_host_owner_with_native_alias(app):
    adapter, child, required, worker = await joined(app)
    owner = app.state['sessions'][0]
    await save(app, owner)
    child.update(nativeIdentity='older-native-alias', runtimeSessionId='current-native-root')
    app._publish_changes(sessions={child['id']})
    adapter.coordinator.session_id = child['runtimeSessionId']
    worker.start_config = {'ownerSessionId': child['id']}
    current_full = adapter.coordinator.get_capability('web.work_full_access')
    assert current_full()
    worker.start_config['ownerSessionId'] = owner['id']
    assert not current_full()
    worker.start_config['ownerSessionId'] = child['id']
    await save(app, owner, mode='ask')
    assert not current_full()
