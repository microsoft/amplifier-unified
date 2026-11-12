"""Explicit Full root recovery of an idle, fenced original; no target turn."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web.configuration_recovery import receipt_path
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppError
from test_coordination_generation import agent
from test_configuration_authority import revoke
from test_full_operator_configuration_recovery import operator_failure
from test_queued_configuration_boundary import (
    operation, plan, process_apply, process_boundary,
)
from test_owner_update_wait import OwnerRuntime, UpdateWait


async def manager(b, mode='full'):
    source = b.app._new_session({'title': 'Recovery operator'})
    source['collaborationGeneration'] = {
        'id': 'operator-generation', 'inputIds': ['operator-human'], 'terminal': False}
    b.app.state['sessions'].append(source)
    b.app._publish()
    await mode_for(b, source, mode)
    return source


async def mode_for(b, source, mode):
    snapshot = (await b.app.dispatch('access.get', {'sessionId': source['id']}))['result']
    await b.app.dispatch('access.set', {'sessionId': source['id'], 'scope': 'session',
        'mode': mode, 'expectedRevision': snapshot['revision']})


async def invoke(b, source, verb, identity=None, **extra):
    saved = operation(b)
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'], **extra}
    if verb != 'read':
        args.setdefault('expectedRevision', saved['revision'])
    return await agent(b.app, source, 'configuration.operation.' + verb, args,
        identity or 'operator-' + verb + '-' + str(saved['revision']))


async def saved_failure(b, monkeypatch):
    await b.runtime._request(b.owner['id'], 'fixture.lose_reply')
    original = asyncio.wait_for
    async def shortened(awaitable, timeout):
        return await original(awaitable, .5 if timeout == 30 else timeout)
    monkeypatch.setattr(asyncio, 'wait_for', shortened)
    assert (await process_apply(b))['phase'] == 'error'
    saved = operation(b)
    assert saved['state']['effect'] == 'unknown'
    # This synthetic worker emits runtime.ready, not the real park/idle event.
    # Model the settled host boundary explicitly; the native probe owns lifecycle.
    b.owner['status'] = 'idle'
    return saved


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_operator_discovers_operation_then_reconciles_and_saves_future_bundle(process_boundary, monkeypatch):
    b = process_boundary
    saved = await operator_failure(b)
    b.owner['status'] = 'idle'
    source = await manager(b)
    messages = deepcopy(b.owner['messages'])
    drain = AsyncMock()
    monkeypatch.setattr(b.app.collaboration, 'drain', drain)
    choices = (await agent(b.app, source, 'conversation.configuration',
        {'sessionId': b.owner['id']}, 'discover-held-operation'))['result']
    discovered = choices['pendingOperation']
    assert discovered['availability'] == 'retained'
    assert discovered['operationId'] == saved['id']
    assert discovered['revision'] == saved['revision']
    args = {'sessionId': b.owner['id'], 'operationId': discovered['operationId'],
            'expectedRevision': discovered['revision']}
    view = (await agent(b.app, source, 'configuration.operation.read',
        {**args, 'includeRecovery': True, 'diagnostic': 'first_failure'}, 'inspect-discovered'))['result']
    assert view['nativeEffect'] == 'none' and view['recovery']['canReconcile']
    await agent(b.app, source, 'configuration.operation.reconcile', args, 'reconcile-discovered')
    current = (await agent(b.app, source, 'conversation.configuration',
        {'sessionId': b.owner['id']}, 'read-before-future-save'))['result']
    assert current['blockedReason'] is None
    result = await agent(b.app, source, 'conversation.configure',
        {'sessionId': b.owner['id'], 'expectedRevision': current['nextMessageRevision'],
         'bundle': 'work-amp-dev'}, 'future-bundle-after-reconcile')
    assert result['result']['mounted'] is False
    final = (await agent(b.app, source, 'conversation.configuration',
        {'sessionId': b.owner['id']}, 'read-future-save'))['result']
    assert final['pendingBundleSelection'] == {'bundle': 'work-amp-dev', 'resetModel': False}
    assert final['nextMessageRevision'] == 1
    assert b.owner['messages'] == messages
    assert operation(b)['attempts'] == saved['attempts']
    assert not override_path(b.owner['runtimeSessionId']).exists()
    drain.assert_not_awaited()


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('effect', ['none', 'saved', 'unknown-saved'])
async def test_fresh_cancel_then_future_bundle_keeps_original_authority_and_inputs(process_boundary, monkeypatch, effect):
    from test_saved_configuration_cancel import saved_mount_failure, stop_worker
    from amplifier_web.configuration_recovery import suppress_input
    from amplifier_web.management import Management
    b = process_boundary
    if effect == 'none':
        original = await operator_failure(b)
    elif effect == 'saved':
        original = await saved_mount_failure(b)
    else:
        original = await saved_failure(b, monkeypatch)
    before_settings = override_path(b.owner['runtimeSessionId']).read_bytes() if effect != 'none' else None
    before_receipt = receipt_path(original['attempts'][-1]).read_bytes()
    await stop_worker(b)
    # A fresh operator can abandon proven effects; it may never renew this
    # revoked/stopped original's authority to resume its effect.
    await mode_for(b, b.owner, 'ask')
    source = await manager(b)
    with pytest.raises(AppError):
        await invoke(b, source, 'resume')
    messages = deepcopy(b.owner['messages'])
    old = {'acceptedAt': 1, 'dependencyRequestId': None}
    drains = AsyncMock()
    monkeypatch.setattr(b.app.collaboration, 'drain', drains)
    saved = operation(b)
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'], 'expectedRevision': saved['revision']}
    choices = (await agent(b.app, source, 'conversation.configuration',
        {'sessionId': b.owner['id']}, 'discover-cancel'))['result']
    assert choices['pendingOperation']['operationId'] == saved['id']
    observed = (await agent(b.app, source, 'configuration.operation.read',
        {**args, 'includeRecovery': True}, 'read-cancel'))['result']
    assert not observed['recovery']['canResume']
    assert observed['recovery']['canCancel']
    receipt = await agent(b.app, source, 'configuration.operation.cancel', args, 'cancel-exact')
    cancelled = operation(b)
    assert cancelled['state']['phase'] == 'cancelled'
    assert cancelled['state']['effect'] == ('none' if effect == 'none' else 'saved_not_mounted')
    assert cancelled['request'] == original['request']
    assert cancelled['attempts'] == original['attempts']
    assert cancelled['history'][:len(original['history'])] == original['history']
    assert b.owner['messages'] == messages
    assert not b.owner['configurationBusy'] and not b.runtime.workers
    assert receipt_path(original['attempts'][-1]).read_bytes() == before_receipt
    if before_settings is not None:
        assert override_path(b.owner['runtimeSessionId']).read_bytes() == before_settings
    else:
        assert not override_path(b.owner['runtimeSessionId']).exists()
    # Lost reply recovery reads the command, not another effect.
    status = (await agent(b.app, source, 'configuration.operation.read',
        {**args, 'commandId': 'cancel-exact'}, 'read-cancel-receipt'))['result']
    assert status['command']['accepted'] and status['command']['action'] == 'configuration.operation.cancel'
    duplicate = await agent(b.app, source, 'configuration.operation.cancel', args, 'cancel-exact')
    assert duplicate['duplicate'] and duplicate['result'] == receipt['result']
    # Startup reconstruction keeps the original marker, not a new cutoff.
    marker = deepcopy(b.owner['cancelledConfigurationInput'])
    b.app.management = Management(b.app)
    assert b.owner['cancelledConfigurationInput'] == marker
    assert suppress_input(b.owner, old)
    result = await agent(b.app, source, 'conversation.configure',
        {'sessionId': b.owner['id'], 'expectedRevision': 0, 'bundle': 'work-amp-dev'}, 'future-after-cancel')
    assert result['result']['mounted'] is False
    assert b.owner['pendingBundleSelection']['bundle'] == 'work-amp-dev'
    b.owner['collaborationGeneration'] = {'id': 'genuinely-new-turn', 'inputIds': ['new']}
    assert suppress_input(b.owner, old)
    assert not suppress_input(b.owner, {'acceptedAt': marker['acceptedAt'] + 1})
    drains.assert_not_awaited()


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('change', ['running', 'saved', 'missing-receipt', 'partial', 'native',
    'placement', 'revision', 'payload', 'refresh', 'writer', 'ask', 'child'])
async def test_operator_cancel_refuses_unproven_boundary(process_boundary, monkeypatch, change):
    from test_saved_configuration_cancel import saved_mount_failure
    from amplifier_web.configuration_recovery import seal
    from amplifier_foundation.session import SharedSessionStore
    b = process_boundary
    original = await saved_mount_failure(b)
    b.owner['status'] = 'idle'
    source = await manager(b)
    if change != 'running':
        await b.runtime.stop(b.owner['id'], reason='configuration')
    transport, held = {}, None
    if change == 'saved':
        override_path(b.owner['runtimeSessionId']).write_text(json.dumps(plan(3)))
    elif change == 'missing-receipt':
        receipt_path(original['attempts'][-1]).unlink()
    elif change == 'partial':
        attempt = original['attempts'][-1]
        receipt_path(attempt).write_text(json.dumps(seal(attempt, 'unknown', terminal=True)))
    elif change == 'native':
        b.owner['runtimeSessionId'] = 'another-native-target'
    elif change == 'placement':
        b.owner['executionRevision'] = 1
    elif change == 'payload':
        b.app.management.queued_path(b.owner['id']).write_text(json.dumps(plan(3)))
    elif change == 'refresh':
        b.owner['configurationRefresh'] = {'phase': 'applying'}
    elif change == 'writer':
        held = SharedSessionStore(b.owner['workspace'], b.owner['runtimeSessionId']).acquire(app='independent-writer')
    elif change == 'ask':
        await mode_for(b, source, 'ask')
    elif change == 'child':
        transport['_childOrigin'] = {}
    args = {'sessionId': b.owner['id'], 'operationId': original['id'],
            'expectedRevision': original['revision'] + (1 if change == 'revision' else 0)}
    try:
        with pytest.raises((AppError, ValueError, RuntimeError)):
            await agent(b.app, source, 'configuration.operation.cancel', args, 'refused-cancel', **transport)
    finally:
        if held:
            held.release()
    assert operation(b) == original
    assert b.owner['configurationBusy']


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('change', ['full-off-on', 'caller-stop', 'target-stop', 'placement', 'native'])
async def test_cancel_capture_survives_no_dispatch_wait_renewal(process_boundary, monkeypatch, change):
    b = process_boundary
    original = await operator_failure(b)
    await b.runtime.stop(b.owner['id'], reason='configuration')
    b.owner['status'] = 'idle'
    source = await manager(b)
    entered, release = asyncio.Event(), asyncio.Event()
    route = b.app.collaboration.route
    async def delayed(action, *args):
        if action == 'configuration.operation.cancel':
            entered.set()
            await release.wait()
        return await route(action, *args)
    monkeypatch.setattr(b.app.collaboration, 'route', delayed)
    pending = asyncio.create_task(invoke(b, source, 'cancel', identity='cancel-wait'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if change == 'full-off-on':
            await mode_for(b, source, 'ask')
            await mode_for(b, source, 'full')
        elif change == 'caller-stop':
            source['interruptionRevision'] = 1
        elif change == 'target-stop':
            b.owner['interruptionRevision'] = 1
        elif change == 'placement':
            b.owner['executionRevision'] = 1
        else:
            b.owner['runtimeSessionId'] = 'different-native'
    finally:
        release.set()
    with pytest.raises(AppError):
        await pending
    assert operation(b) == original


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('effect', ['none', 'saved'])
async def test_cancel_durable_commit_recovers_projection_without_replaying(process_boundary, monkeypatch, effect):
    from test_saved_configuration_cancel import saved_mount_failure, stop_worker
    from amplifier_web.management import Management
    b = process_boundary
    original = await (operator_failure(b) if effect == 'none' else saved_mount_failure(b))
    await stop_worker(b)
    source = await manager(b)
    before = deepcopy(b.owner)
    publish = b.app._publish_changes
    def crash(*args, **kwargs):
        if b.owner['pendingConfiguration'].get('phase') == 'cancelled':
            raise OSError('Injected publication loss after durable cancellation')
        return publish(*args, **kwargs)
    monkeypatch.setattr(b.app, '_publish_changes', crash)
    with pytest.raises(AppError):
        await invoke(b, source, 'cancel', identity='cancel-lost-publication')
    committed = operation(b)
    marker = committed['recoveryNoInputBinding']
    assert committed['state']['phase'] == 'cancelled'
    # Restore the lagging projection as it existed before the durable file commit.
    b.owner.clear()
    b.owner.update(before)
    monkeypatch.setattr(b.app, '_publish_changes', publish)
    b.app.management = Management(b.app)
    assert b.owner['pendingConfiguration']['phase'] == 'cancelled'
    assert b.owner['cancelledConfigurationInput'] == marker
    assert not b.owner['configurationBusy']
    assert operation(b) == committed
    assert committed['request'] == original['request'] and committed['attempts'] == original['attempts']
    assert not b.runtime.workers


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_cancel_availability_read_never_acquires_native_ownership(process_boundary, monkeypatch):
    from contextlib import contextmanager
    b = process_boundary
    original = await operator_failure(b)
    await b.runtime.stop(b.owner['id'], reason='configuration')
    b.owner['status'] = 'idle'
    source = await manager(b)
    acquisitions = []
    @contextmanager
    def forbidden_enter(session):
        acquisitions.append(session['id'])
        raise AssertionError('Read acquired native ownership')
        yield
    monkeypatch.setattr(b.runtime, 'stopped_boundary', forbidden_enter)
    result = (await invoke(b, source, 'read', includeRecovery=True))['result']
    assert result['recovery']['canCancel']
    assert not acquisitions and operation(b) == original


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_cancel_rechecks_saved_plan_after_acquiring_native_lease(process_boundary, monkeypatch):
    from contextlib import contextmanager
    from test_saved_configuration_cancel import saved_mount_failure, stop_worker
    b = process_boundary
    original = await saved_mount_failure(b)
    await stop_worker(b)
    source = await manager(b)
    stopped = b.runtime.stopped_boundary
    @contextmanager
    def intervening_writer(session):
        # A writer finished and released its lease after the initial read.
        override_path(session['runtimeSessionId']).write_text(json.dumps(plan(3)))
        with stopped(session) as owned:
            yield owned
    monkeypatch.setattr(b.runtime, 'stopped_boundary', intervening_writer)
    with pytest.raises(AppError):
        await invoke(b, source, 'cancel', identity='cancel-raced-save')
    assert operation(b) == original and b.owner['configurationBusy']


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('kind', ['missing', 'nonterminal', 'forged', 'unknown', 'contradictory-attempt'])
async def test_cancel_does_not_erase_unknown_no_effect_evidence(process_boundary, kind):
    from amplifier_web.configuration_recovery import seal
    b = process_boundary
    original = await operator_failure(b)
    await b.runtime.stop(b.owner['id'], reason='configuration')
    b.owner['status'] = 'idle'
    source = await manager(b)
    attempt = original['attempts'][-1]
    path = receipt_path(attempt)
    if kind == 'missing':
        path.unlink()
    elif kind == 'forged':
        raw = json.loads(path.read_text())
        raw['signature'] = '00' * 32
        path.write_text(json.dumps(raw))
    elif kind == 'contradictory-attempt':
        raw = deepcopy(original)
        raw['attempts'] = []
        b.app.management.configuration_record_path(b.owner['id'], raw['id']).write_text(json.dumps(raw))
    else:
        path.write_text(json.dumps(seal(attempt, 'none' if kind == 'nonterminal' else 'unknown',
                                       terminal=kind != 'nonterminal')))
    before = operation(b)
    with pytest.raises(AppError):
        await invoke(b, source, 'cancel', identity='unknown-cancel')
    assert operation(b) == before
    assert 'cancelledConfigurationInput' not in b.owner


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('peer_mode', ['ask', 'narrow-grant', 'full'])
async def test_cancel_intersects_full_peer_lineage(process_boundary, peer_mode):
    b = process_boundary
    original = await operator_failure(b)
    await b.runtime.stop(b.owner['id'], reason='configuration')
    b.owner['status'] = 'idle'
    source, sender = await manager(b), await manager(b, 'full' if peer_mode == 'full' else 'ask')
    if peer_mode == 'narrow-grant':
        await b.app.dispatch('capability.grant', {'sessionId': sender['id'],
            'effects': ['runtime_configuration'], 'scopes': ['session'], 'reason': 'Owner-local only'})
    request = await agent(b.app, sender, 'coordination.send',
        {'sessionId': source['id'], 'text': 'Abandon the other configuration', 'mode': 'notify'}, 'cancel-peer')
    source['collaborationGeneration'] = {'id': 'cancel-from-peer', 'terminal': False,
                                         'inputIds': [request['requestId']]}
    if peer_mode == 'full':
        result = await invoke(b, source, 'cancel', identity='peer-cancel')
        assert result['result']['state']['phase'] == 'cancelled'
    else:
        with pytest.raises(AppError):
            await invoke(b, source, 'cancel', identity='peer-cancel')
        assert operation(b) == original


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_targeted_saved_resume_never_rewrites_or_wakes(process_boundary, monkeypatch):
    b = process_boundary
    saved = await saved_failure(b, monkeypatch)
    source = await manager(b)
    assert b.owner['configurationBusy'] and b.owner['status'] == 'idle'
    settings = override_path(b.owner['runtimeSessionId']).read_bytes()
    seal = receipt_path(saved['attempts'][0]).read_bytes()
    drain = AsyncMock()
    monkeypatch.setattr(b.app.collaboration, 'drain', drain)
    before_messages = deepcopy(b.owner['messages'])
    view = (await invoke(b, source, 'read', includeRecovery=True))['result']
    assert view['nativeEffect'] == 'saved_not_mounted'
    assert view['recovery']['canReconcile'] and not view['recovery']['canResume']
    assert operation(b) == saved
    reconciled = (await invoke(b, source, 'reconcile'))['result']
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'],
        'expectedRevision': reconciled['revision']}
    accepted = await agent(b.app, source, 'configuration.operation.resume', args, 'operator-saved-resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task:
        await asyncio.wait_for(task, 10)
    # Present stale mount is observed, not replaced or declared successful.
    assert operation(b)['state']['effect'] == 'saved_not_mounted'
    assert operation(b)['state']['phase'] == 'error'
    assert b.owner['configurationBusy']
    await b.runtime.stop(b.owner['id'], reason='configuration')
    assert b.owner['id'] not in b.runtime.workers
    cold = (await invoke(b, source, 'read', includeRecovery=True, commandId='operator-saved-resume'))['result']
    assert cold['command']['accepted'] and cold['command']['phase'] == 'queued'
    assert b.owner['id'] not in b.runtime.workers  # Read never warms.
    await invoke(b, source, 'resume', identity='operator-absent-resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task:
        await asyncio.wait_for(task, 12)
    final = operation(b)
    assert final['state']['phase'] == 'ready' and final['state']['effect'] == 'mounted'
    assert final['request'] == saved['request'] and final['requestHash'] == saved['requestHash']
    assert final['attempts'] == saved['attempts']
    assert final['history'][:len(saved['history'])] == saved['history']
    assert final['resumptions'][0]['chain'][0]['sessionId'] == source['id']
    assert final['history'][len(saved['history'])]['recoverySource']['sessionId'] == source['id']
    assert override_path(b.owner['runtimeSessionId']).read_bytes() == settings
    assert receipt_path(saved['attempts'][0]).read_bytes() == seal
    assert b.owner['messages'] == before_messages
    assert b.owner['status'] in {'idle', 'ready'}
    drain.assert_not_awaited()
    duplicate = await agent(b.app, source, 'configuration.operation.resume', args, 'operator-saved-resume')
    assert duplicate['duplicate'] and duplicate['result'] == accepted['result']
    other = await manager(b)
    with pytest.raises(AppError, match='different contents'):
        await agent(b.app, other, 'configuration.operation.resume', args, 'operator-saved-resume')
    assert operation(b) == final
    public = json.dumps(cold)
    assert all(key not in public for key in ('recoverySource', 'resumptions', 'payloadHash',
        'accessRevision', 'workerPid', 'signature', '"config"'))


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('failure', ['ask', 'workspace', 'missing-operation', 'forged',
    'missing-principal', 'child', 'system', 'stop', 'assignment', 'task', 'refence'])
async def test_target_route_fails_closed(process_boundary, failure):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b, 'ask' if failure == 'ask' else 'full')
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'],
        'expectedRevision': saved['revision']}
    transport = {}
    if failure == 'workspace':
        source['workspace'] += '-elsewhere'
    elif failure == 'missing-operation':
        args.pop('operationId')
    elif failure == 'forged':
        transport['_runtimeSessionId'] = b.owner['runtimeSessionId']
    elif failure == 'missing-principal':
        transport['_runtimeSessionId'] = None
    elif failure == 'child':
        transport['_childOrigin'] = {}
    elif failure == 'stop':
        source['interruptionRevision'] = 1
    elif failure == 'assignment':
        monkey = lambda owner: 'stopped'
        b.app.assignments.blocked = monkey
    elif failure == 'task':
        b.app.coordination.task = lambda sid: {'id': 'task', 'status': 'paused'}
    elif failure == 'refence':
        b.owner['configurationOperationFence'] = 'other-operation'
    with pytest.raises((AppError, ValueError)):
        if failure == 'system':
            await b.app.dispatch('configuration.operation.reconcile', args,
                origin='system', caller_session_id=source['id'])
        else:
            await agent(b.app, source, 'configuration.operation.reconcile', args, 'refused-target', **transport)
    assert operation(b) == saved
    assert not override_path(b.owner['runtimeSessionId']).exists()


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('change', ['caller-off-on', 'caller-stop', 'caller-placement',
    'original-off-on', 'target-stop', 'target-placement', 'target-refence', 'target-native', 'saved-plan', 'partial'])
async def test_saved_resume_retains_both_authority_boundaries(process_boundary, monkeypatch, change):
    b = process_boundary
    saved = await saved_failure(b, monkeypatch)
    source = await manager(b)
    await invoke(b, source, 'reconcile')
    before = override_path(b.owner['runtimeSessionId']).read_bytes()
    if change.endswith('off-on'):
        selected = source if change.startswith('caller') else b.owner
        await mode_for(b, selected, 'ask')
        if change.startswith('caller'):
            # Capture before the await below; off/on is tested at final admission.
            await mode_for(b, selected, 'full')
    if change == 'target-stop':
        b.owner['interruptionRevision'] = 1
    elif change == 'target-placement':
        b.owner['executionRevision'] = 1
    elif change == 'target-refence':
        b.owner['configurationOperationFence'] = 'other-operation'
    elif change == 'target-native':
        b.owner['runtimeSessionId'] = 'changed-native-owner'
    elif change == 'saved-plan':
        override_path(b.owner['runtimeSessionId']).write_text(json.dumps(plan(3)))
    elif change == 'partial':
        from amplifier_web.configuration_recovery import seal
        receipt_path(saved['attempts'][0]).write_text(json.dumps(seal(saved['attempts'][0], 'unknown', terminal=True)))
    if change.startswith('caller'):
        entered, release = asyncio.Event(), asyncio.Event()
        original = b.runtime.control
        async def hold(*args, **kwargs):
            if args[1] == 'configuration.inspect':
                entered.set()
                await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(b.runtime, 'control', hold)
        await invoke(b, source, 'resume')
        task = b.app.management.pending_config_tasks[b.owner['id']]
        await asyncio.wait_for(entered.wait(), 5)
        if change == 'caller-off-on':
            await mode_for(b, source, 'ask')
            await mode_for(b, source, 'full')
        elif change == 'caller-stop':
            source['interruptionRevision'] = 1
        else:
            source['executionRevision'] = 1
        release.set()
        await asyncio.wait_for(task, 10)
        assert operation(b)['state']['phase'] == 'error'
    else:
        with pytest.raises(AppError):
            await invoke(b, source, 'resume')
    assert operation(b)['attempts'] == saved['attempts']
    assert operation(b)['request'] == saved['request']
    if change != 'saved-plan':
        assert override_path(saved['attempts'][0]['nativeSessionId']).read_bytes() == before


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_exact_command_read_never_exposes_foreign_ledger(process_boundary):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b)
    # Neither a suggestive ID nor an unrelated receipt establishes a recovery binding.
    for identity in ('missing-resume', 'operator-apply'):
        view = (await invoke(b, source, 'read', commandId=identity))['result']
        assert view['command']['status'] == 'unconfirmed' and view['command']['accepted'] is None
    await invoke(b, source, 'reconcile', identity='retained-reconcile')
    view = (await invoke(b, source, 'read', commandId='retained-reconcile'))['result']
    assert view['command']['status'] == 'accepted'
    assert view['command']['expectedRevision'] == saved['revision']
    assert view['command']['operationId'] == saved['id']
    assert operation(b)['attempts'] == saved['attempts']


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_stale_rejected_recovery_is_read_without_replay(process_boundary):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b)
    # Match the stable GUI command form, but only a retained binding is proof.
    identity = f"configuration-recovery-{saved['id']}-{saved['revision'] - 1}-resume"
    with pytest.raises(AppError):
        await b.app.dispatch('configuration.operation.resume', {
            'sessionId': b.owner['id'], 'operationId': saved['id'],
            'expectedRevision': saved['revision'] - 1}, command_id=identity)
    first = operation(b)
    view = (await invoke(b, source, 'read', commandId=identity))['result']
    assert view['command']['status'] == 'rejected'
    assert view['command']['errorCategory'] == 'recovery_refused'
    assert view['command']['expectedRevision'] == saved['revision'] - 1
    assert 'error' not in view['command']
    assert operation(b) == first == saved


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('peer_mode', ['ask', 'full', 'grant'])
async def test_recovery_cannot_launder_peer_authority(process_boundary, monkeypatch, peer_mode):
    b = process_boundary
    saved = await saved_failure(b, monkeypatch)
    source = await manager(b)
    sender = await manager(b, 'ask' if peer_mode == 'grant' else peer_mode)
    authorized = None
    if peer_mode == 'grant':
        authorized = (await b.app.dispatch('capability.grant', {'sessionId': sender['id'],
            'effects': ['runtime_configuration'], 'scopes': ['session'],
            'reason': 'Authorize this exact recovery task'}))['result']
    request = await agent(b.app, sender, 'coordination.send', {
        'sessionId': source['id'], 'text': 'Recover the original', 'mode': 'notify'}, 'recovery-peer')
    source['collaborationGeneration'] = {'id': 'peer-requested-recovery',
        'inputIds': [request['requestId']], 'terminal': False}
    if peer_mode == 'ask':
        with pytest.raises(AppError, match='peer'):
            await invoke(b, source, 'reconcile')
        assert operation(b) == saved
        return
    await invoke(b, source, 'reconcile')
    entered, release = asyncio.Event(), asyncio.Event()
    original = b.runtime.control
    async def hold(*args, **kwargs):
        if args[1] == 'configuration.inspect':
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(b.runtime, 'control', hold)
    await invoke(b, source, 'resume')
    task = b.app.management.pending_config_tasks[b.owner['id']]
    await asyncio.wait_for(entered.wait(), 5)
    retained = operation(b)['resumptions'][0]['chain']
    assert retained[1]['sessionId'] == sender['id']
    if authorized:
        await revoke(b.app, authorized)
    else:
        await mode_for(b, sender, 'ask')
        await mode_for(b, sender, 'full')
    release.set()
    await asyncio.wait_for(task, 10)
    assert operation(b)['state']['phase'] == 'error'
    assert operation(b)['attempts'] == saved['attempts']


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_recovery_preserves_revoked_original_grant(process_boundary):
    b = process_boundary
    await mode_for(b, b.owner, 'ask')
    # Original discrete grants remain independent of the manager's Full choice.
    from test_configuration_authority import grant
    b.authorized = await grant(b.app, ['runtime_configuration'], ['session'])
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=1)
    assert (await process_apply(b))['phase'] == 'error'
    saved = operation(b)
    source = await manager(b)
    await revoke(b.app, b.authorized)
    with pytest.raises(AppError):
        await invoke(b, source, 'reconcile')
    assert operation(b) == saved


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_cross_owner_no_effect_recovery_holds_existing_queued_input(process_boundary):
    b = process_boundary
    saved = await operator_failure(b)
    b.owner['status'] = 'idle'
    source = await manager(b)
    await agent(b.app, source, 'coordination.send', {'sessionId': b.owner['id'],
        'text': 'Do not run incidentally during recovery', 'mode': 'queue'}, 'queued-before-recovery')
    assert b.app.collaboration.receipt('queued-before-recovery')['delivery'] == 'queued'


    await invoke(b, source, 'reconcile')
    await b.app.collaboration.drain(b.owner['id'])
    assert b.app.collaboration.receipt('queued-before-recovery')['delivery'] == 'queued'
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=0)
    await invoke(b, source, 'resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task:
        await asyncio.wait_for(task, 12)
    assert operation(b)['state']['effect'] == 'mounted'
    assert len(operation(b)['attempts']) == 2
    assert operation(b)['attempts'][0] == saved['attempts'][0]
    await b.app.collaboration.drain(b.owner['id'])
    assert b.app.collaboration.receipt('queued-before-recovery')['delivery'] == 'queued'
    assert b.owner['status'] in {'idle', 'ready'}
    admitted = []
    async def input_adapter(session, args, guard, emit):
        assert not guard()
        result = b.app.collaboration.admission(session['id'], args)
        assert result['admitted'], result
        admitted.append(args['inputId'])
        return {'accepted': True}
    b.runtime.collaboration_input = input_adapter
    # Fixture startup reports ready but never publishes the production parked
    # idle event. Fresh queue-only work must wait for that explicit boundary.
    b.owner['status'] = 'idle'
    await agent(b.app, source, 'coordination.send', {'sessionId': b.owner['id'],
        'text': 'Genuinely fresh explicit work after recovery', 'mode': 'queue'}, 'fresh-after-recovery')
    async with asyncio.timeout(5):
        while not admitted:
            await asyncio.sleep(.01)
    assert admitted == ['fresh-after-recovery']
    assert b.app.collaboration.receipt('queued-before-recovery')['delivery'] == 'queued'


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('pending_state', ['removed', 'replacement'])
async def test_cancelled_cutoff_survives_pending_replacement_during_drain(process_boundary, monkeypatch, pending_state):
    b = process_boundary
    original = await operator_failure(b)
    b.owner['status'] = 'idle'
    source = await manager(b)
    # Retain genuine queued peer receipts on both sides of cancellation. Only
    # the transport is adapted; queue selection, fences and admission are real.
    drain = b.app.collaboration.drain
    monkeypatch.setattr(b.app.collaboration, 'drain', AsyncMock())
    args = {'sessionId': b.owner['id'], 'text': 'Old queued input', 'mode': 'queue'}
    await agent(b.app, source, 'coordination.send', args, 'cutoff-old-peer')
    assert b.app.collaboration.receipt('cutoff-old-peer')['delivery'] == 'queued'
    await b.runtime.stop(b.owner['id'], reason='configuration')
    await invoke(b, source, 'cancel', identity='cancel-before-pending-change')
    marker = deepcopy(b.owner['cancelledConfigurationInput'])
    cancelled = operation(b)
    assert cancelled['request'] == original['request']
    assert cancelled['state']['phase'] == 'cancelled'
    if pending_state == 'removed':
        b.owner.pop('pendingConfiguration')
    else:
        # A later settled operation has replaced the cancelled projection. It
        # must neither supply nor erase the durable old-input boundary.
        b.owner['pendingConfiguration'] = {'operationId': 'replacement-operation',
            'phase': 'ready', 'effect': 'mounted'}
    b.owner['status'] = 'idle'
    await agent(b.app, source, 'coordination.send',
        {**args, 'text': 'Fresh explicitly requested input'}, 'cutoff-fresh-peer')
    assert b.app.collaboration.receipt('cutoff-fresh-peer')['acceptedAt'] > marker['acceptedAt']
    admitted = []
    async def input_adapter(session, values, guard, emit):
        assert guard() is None
        result = b.app.collaboration.admission(session['id'], values)
        assert result['admitted'], result
        admitted.append(values['inputId'])
        return {'accepted': True}
    monkeypatch.setattr(b.runtime, 'collaboration_input', input_adapter)
    await drain(b.owner['id'])
    assert admitted == ['cutoff-fresh-peer']
    assert b.app.collaboration.receipt('cutoff-old-peer')['delivery'] == 'queued'
    assert b.app.collaboration.receipt('cutoff-fresh-peer')['delivery'] == 'accepted'
    assert b.owner['cancelledConfigurationInput'] == marker
    assert not b.runtime.workers  # No model or native execution in this adapter.
    assert b.app.management.configuration_record(b.owner['id'], original['id']) == cancelled


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_startup_none_bookkeeping_preserves_old_queue_but_allows_fresh_explicit_input(process_boundary, monkeypatch):
    from amplifier_web.management import Management
    b = process_boundary
    original = await operator_failure(b)
    b.owner['status'] = 'idle'
    source = await manager(b)
    drain = b.app.collaboration.drain
    monkeypatch.setattr(b.app.collaboration, 'drain', AsyncMock())
    args = {'sessionId': b.owner['id'], 'text': 'Retained input before interrupted settings', 'mode': 'queue'}
    await agent(b.app, source, 'coordination.send', args, 'startup-old-peer')
    await b.runtime.stop(b.owner['id'], reason='configuration')
    b.owner['status'] = 'stopped'
    b.app.management = Management(b.app)
    await asyncio.gather(*list(b.app.tasks))
    assert not b.owner['configurationBusy']
    marker = deepcopy(b.owner['cancelledConfigurationInput'])
    settled = operation(b)
    assert settled['request'] == original['request'] and settled['attempts'] == original['attempts']
    b.app.management = Management(b.app)
    await asyncio.gather(*list(b.app.tasks))
    assert b.owner['cancelledConfigurationInput'] == marker and operation(b) == settled
    # A later retained operator observation may have tightened the cutoff.
    # Startup must not restore its older persisted marker over the newer fence.
    newer = {**marker, 'acceptedAt': marker['acceptedAt'] + .001}
    b.owner['pendingConfiguration']['recoveryNoInput'] = newer
    b.app.management = Management(b.app)
    await asyncio.gather(*list(b.app.tasks))
    assert b.owner['cancelledConfigurationInput'] == newer
    settled = operation(b)
    assert b.app.collaboration.receipt('startup-old-peer')['delivery'] == 'queued'
    b.owner['status'] = 'idle'  # Synthetic new operator intent, not startup resuming Stop.
    await agent(b.app, source, 'coordination.send', {**args, 'text': 'Fresh explicit work'}, 'startup-fresh-peer')
    admitted = []
    async def input_adapter(session, values, guard, emit):
        assert guard() is None
        assert b.app.collaboration.admission(session['id'], values)['admitted']
        admitted.append(values['inputId'])
        return {'accepted': True}
    monkeypatch.setattr(b.runtime, 'collaboration_input', input_adapter)
    await drain(b.owner['id'])
    assert admitted == ['startup-fresh-peer']
    assert b.app.collaboration.receipt('startup-old-peer')['delivery'] == 'queued'
    assert not b.runtime.workers
    assert operation(b) == settled


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('change', ['full-off-on', 'placement', 'task-epoch', 'target-native'])
async def test_operator_capture_precedes_dispatch_wait(process_boundary, monkeypatch, change):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b)
    entered, release = asyncio.Event(), asyncio.Event()
    route = b.app.collaboration.route
    async def delayed(action, *args):
        if action == 'configuration.operation.reconcile':
            entered.set()
            await release.wait()
        return await route(action, *args)
    monkeypatch.setattr(b.app.collaboration, 'route', delayed)
    pending = asyncio.create_task(invoke(b, source, 'reconcile'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if change == 'full-off-on':
            await mode_for(b, source, 'ask')
            await mode_for(b, source, 'full')
        elif change == 'placement':
            source['executionRevision'] = 1
        elif change == 'task-epoch':
            source['task'] = {'id': 'new-task', 'status': 'active', 'continuationEpoch': 2}
        else:
            b.owner['runtimeSessionId'] = 'different-native-target'
    finally:
        release.set()
    with pytest.raises(AppError):
        await pending
    assert operation(b) == saved


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_late_old_wait_stays_evidence_without_waking_recovered_target(process_boundary):
    b = process_boundary
    source = await manager(b)
    source['status'] = 'working'
    watched = await agent(b.app, b.owner, 'coordination.watch', {
        'sessionId': b.owner['id'], 'targetSessionId': source['id'],
        'condition': 'idle', 'expiresInSeconds': 60}, 'old-target-wait',
        _runtimeSessionId=b.owner['runtimeSessionId'])
    saved = await operator_failure(b)
    await invoke(b, source, 'reconcile')
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=0)
    await invoke(b, source, 'resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task:
        await asyncio.wait_for(task, 12)
    before = deepcopy(b.owner['messages'])
    async with b.app.lock:
        current = b.app.collaboration.receipt('old-target-wait')
        b.app.collaboration.watches.finish(current, 'observed', 'Late old result after recovery')
        # A backpressured return revisited later is the same old work.
        current = b.app.collaboration.receipt('old-target-wait')
        current['subscription']['status'] = 'backpressured'
        b.app.collaboration.save('old-target-wait', current)
        b.app.collaboration.resume_outcomes(b.owner['id'])
    await b.app.collaboration.drain(b.owner['id'])
    result = b.app.collaboration.receipt('old-target-wait')
    assert result['terminalOutcome']['status'] == 'observed'
    assert result['subscription']['status'] == 'backpressured'
    assert not b.app.db.execute('SELECT 1 FROM commands WHERE id=?',
        (watched['subscription']['continuationId'],)).fetchone()
    assert b.owner['messages'] == before and b.owner['status'] in {'idle', 'ready'}
    assert operation(b)['attempts'][0] == saved['attempts'][0]


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('fault', [None, 'wrong-operation', 'missing-generation', 'revoked-original'])
async def test_own_settings_return_keeps_exact_original_lineage(process_boundary, fault):
    """Real shared settings-return producer; controlled native task boundary."""
    b = process_boundary
    source = await manager(b)
    original = {'origin': 'agent', 'callerSessionId': source['id'],
        'generation': deepcopy(source['collaborationGeneration']),
        'target': b.app.management.configuration_target_binding(source),
        'continuation': {'generationId': source['collaborationGeneration']['id'],
            'taskId': 'settings-task', 'taskRevision': 1, 'taskContinuationEpoch': 1,
            'lastInputId': None, 'assignmentId': None}}
    authority = {'sessionId': source['id'], 'workspace': source['workspace'],
        'accessRevision': b.app.access_mode.snapshot(source)['revision'],
        'interruptionRevision': 0, 'effect': 'runtime_configuration', 'scope': 'session'}
    record = b.app.management.create_configuration_operation(source, plan(2), 'own-settings', authority, original)
    b.app.management.configuration_transition(source, record['id'], phase='ready', effect='mounted',
        boundary={'ready': True, 'yieldedGoal': {'cap': None, 'turns_used': 1}})
    source['task'] = {'id': 'settings-task', 'status': 'active', 'revision': 1, 'continuationEpoch': 1}
    source['status'] = 'working'
    # The settings-return owner validates the current task/last input in its
    # ordinary path; no manual notification or peer-authored lineage is used.
    from amplifier_web import configuration_continuation
    from unittest.mock import patch
    with (patch.object(configuration_continuation, 'intent_reason', return_value=None),
          patch.object(b.app.collaboration, 'drain', new=AsyncMock())):
        configuration_continuation.publish_outcome(b.app.management, source, record['id'])
    wait_id = 'configuration-wait:' + record['id']
    wait = b.app.collaboration.receipt(wait_id)
    returned = b.app.collaboration.receipt(wait['subscription']['continuationId'])
    source['collaborationGeneration'] = {'id': 'own-settings-return',
        'inputIds': [returned['requestId']], 'terminal': False}
    if fault == 'wrong-operation':
        wait['configurationOperationId'] = '00000000-0000-0000-0000-000000000001'
        b.app.collaboration.save(wait_id, wait)
    elif fault == 'missing-generation':
        path = b.app.management.configuration_record_path(source['id'], record['id'])
        current = b.app.management.configuration_record(source['id'], record['id'])
        current['request']['source']['generation'] = {}
        from amplifier_web.configuration_authority import fingerprint
        current['requestHash'] = fingerprint(current['request'])
        path.write_text(json.dumps(current))
        source['pendingConfiguration']['requestHash'] = current['requestHash']
    elif fault == 'revoked-original':
        await mode_for(b, source, 'ask')
        await mode_for(b, source, 'full')
    saved = await operator_failure(b)
    if fault:
        with pytest.raises((AppError, ValueError)):
            await invoke(b, source, 'reconcile')
        assert operation(b) == saved
    else:
        await invoke(b, source, 'reconcile')
        await b.runtime._request(b.owner['id'], 'fixture.busy', count=0)
        await invoke(b, source, 'resume')
        task = b.app.management.pending_config_tasks.get(b.owner['id'])
        if task:
            await asyncio.wait_for(task, 12)
        assert operation(b)['state']['effect'] == 'mounted'
        assert operation(b)['resumptions'][0]['chain'][1] == authority


async def update_return(b, tmp_path, monkeypatch, outcome, *, sender=None):
    """Real wait, native goal checkpoint and return producer on the recovery host.

    Only transport routing and successor health are fixture adapters; the target
    retains the real subprocess/native effect and saved operation.
    """
    from amplifier_web import update_drain
    from amplifier_web.updates import UpdateManager
    h = UpdateWait(tmp_path)
    h.app, h.runtime = b.app, OwnerRuntime()
    h.runtime.app = b.app
    h.manager = UpdateManager(b.app)
    b.app.update_manager = h.manager
    control, collaboration_input = b.runtime.control, b.runtime.collaboration_input

    async def route_control(sid, *args, **kwargs):
        if sid in h.runtime.controllers:
            return await h.runtime.control(sid, *args, **kwargs)
        return await control(sid, *args, **kwargs)

    async def route_input(session, *args, **kwargs):
        if session['id'] == h.sid:
            return await h.runtime.collaboration_input(session, *args, **kwargs)
        return await collaboration_input(session, *args, **kwargs)

    monkeypatch.setattr(b.runtime, 'control', route_control)
    monkeypatch.setattr(b.runtime, 'collaboration_input', route_input)
    monkeypatch.setattr(b.runtime, 'handoff_boundary', h.runtime.handoff_boundary)
    h.sid = await h.new_task('Update recovery operator', full_access=True)
    source = b.app._session(h.sid)
    if sender is not None:
        await agent(b.app, sender, 'coordination.send', {
            'sessionId': h.sid, 'text': 'Lower authority request', 'mode': 'notify'}, 'lower-peer')
        source['collaborationGeneration']['inputIds'] = ['lower-peer']
    # Only this owner's native checkpoint lives in the in-process adapter.
    b.runtime.workers[h.sid] = h.runtime.workers[h.sid]
    try:
        h.stage()
        await h.ready()
        if outcome == 'update-withdrawn':
            b.app.state['updates']['pendingApp'] = None
            await update_drain.observe_waiters(h.manager)
            await h.settle()
        else:
            h.replace()
            # Explicit health fixture, not an installed update/restart test.
            b.app.state['updates']['drain']['hostInstanceId'] = 'retired-fixture-host'
            h.manager.running_identity = {**h.release, 'instanceId': b.app.instance_id}
            b.app.state['updates'].update(phase='idle', pendingApp=None, pendingRestart=None,
                pendingReplacement=None, applicationResult={**h.release, 'phase': 'restart-ack'})
            await h.finish()
        assert len(h.runtime.admitted) == 1, {
            'returns': [(r.get('delivery'), r.get('detail'), r.get('admission'))
                        for r in h.receipts('coordination.send')],
            'guards': h.runtime.guard_reasons, 'calls': h.runtime.admission_calls,
            'status': source['status'], 'busy': source.get('configurationBusy')}
        returned = b.app.collaboration.receipt(h.runtime.admitted[0]['inputId'])
        original = b.app.collaboration.receipt(returned['dependencyRequestId'])
        assert original['terminalOutcome']['outcome'] == outcome
        source['collaborationGeneration'] = {'id': 'update-return-generation',
            'inputIds': [returned['requestId']], 'terminal': False}
        return source, original
    finally:
        b.runtime.workers.pop(h.sid, None)
        await h.runtime.close()


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('outcome', ['update-ready', 'update-withdrawn'])
async def test_real_update_return_preserves_targeted_full_recovery(process_boundary, tmp_path, monkeypatch, outcome):
    b = process_boundary
    source, original = await update_return(b, tmp_path, monkeypatch, outcome)
    saved = await saved_failure(b, monkeypatch)
    settings = override_path(b.owner['runtimeSessionId']).read_bytes()
    seal = receipt_path(saved['attempts'][0]).read_bytes()
    before_messages = deepcopy(b.owner['messages'])
    view = (await invoke(b, source, 'read', includeRecovery=True))['result']
    assert view['recovery']['canReconcile'] and not view['recovery']['canResume']
    await invoke(b, source, 'reconcile')
    await b.runtime.stop(b.owner['id'], reason='configuration')
    await invoke(b, source, 'resume')
    pending = b.app.management.pending_config_tasks.get(b.owner['id'])
    if pending:
        await asyncio.wait_for(pending, 12)
    final = operation(b)
    assert final['state']['effect'] == 'mounted'
    assert final['request'] == saved['request'] and final['requestHash'] == saved['requestHash']
    assert final['attempts'] == saved['attempts']
    assert override_path(b.owner['runtimeSessionId']).read_bytes() == settings
    assert receipt_path(saved['attempts'][0]).read_bytes() == seal
    assert b.owner['messages'] == before_messages
    assert original['senderSessionId'] == source['id']


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('outcome', ['update-ready', 'update-withdrawn'])
@pytest.mark.parametrize('fault', [
    'missing-proof', 'missing-inputs', 'malformed-inputs', 'overflow', 'generation',
    'wait-id', 'wrong-owner', 'wrong-workspace', 'peer-ask',
])
async def test_real_update_return_cannot_launder_unknown_or_peer_authority(
        process_boundary, tmp_path, monkeypatch, outcome, fault):
    b = process_boundary
    sender = await manager(b, 'ask') if fault == 'peer-ask' else None
    source, original = await update_return(b, tmp_path, monkeypatch, outcome, sender=sender)
    saved = await operator_failure(b)
    if fault != 'peer-ask':
        wait = b.app.collaboration.receipt(original['updateYield']['commandId'])
        if fault == 'missing-proof':
            for key in ('sourceInputIds', 'sourceGenerationId', 'sourceInterruptionRevision'):
                wait.pop(key, None)
        elif fault == 'missing-inputs':
            wait['sourceInputIds'] = []
        elif fault == 'malformed-inputs':
            wait['sourceInputIds'] = [None]
        elif fault == 'overflow':
            wait['sourceInputIds'] = ['input-' + str(n) for n in range(129)]
        elif fault == 'generation':
            wait['sourceGenerationId'] = 'unrelated-generation'
        elif fault in {'wrong-owner', 'wrong-workspace'}:
            if fault == 'wrong-owner':
                original['target']['sessionId'] = b.owner['id']
            else:
                original['workspace'] += '-elsewhere'
            b.app.collaboration.save(original['requestId'], original)
        else:
            original['updateYield']['commandId'] = 'unrelated-wait'
            b.app.collaboration.save(original['requestId'], original)
        b.app.collaboration.save(wait['requestId'], wait)
    with pytest.raises((AppError, ValueError)):
        await invoke(b, source, 'reconcile')
    assert operation(b) == saved
    assert not override_path(b.owner['runtimeSessionId']).exists()


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('outcome', ['update-ready', 'update-withdrawn'])
@pytest.mark.parametrize('changed', ['peer-full', 'caller-stop', 'target-native'])
async def test_update_return_keeps_recovery_ceilings_at_final_readback(
        process_boundary, tmp_path, monkeypatch, outcome, changed):
    b = process_boundary
    sender = await manager(b)
    source, _ = await update_return(b, tmp_path, monkeypatch, outcome, sender=sender)
    saved = await saved_failure(b, monkeypatch)
    await invoke(b, source, 'reconcile')
    before = override_path(b.owner['runtimeSessionId']).read_bytes()
    entered, release = asyncio.Event(), asyncio.Event()
    control = b.runtime.control

    async def hold(*args, **kwargs):
        if args[1] == 'configuration.inspect':
            entered.set()
            await release.wait()
        return await control(*args, **kwargs)

    monkeypatch.setattr(b.runtime, 'control', hold)
    await invoke(b, source, 'resume')
    pending = b.app.management.pending_config_tasks[b.owner['id']]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        chain = operation(b)['resumptions'][0]['chain']
        assert chain[1]['sessionId'] == sender['id']
        if changed == 'peer-full':
            await mode_for(b, sender, 'ask')
            await mode_for(b, sender, 'full')
        elif changed == 'caller-stop':
            source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        else:
            b.owner['runtimeSessionId'] = 'superseding-native-owner'
    finally:
        release.set()
    await asyncio.wait_for(pending, 12)
    assert operation(b)['state']['phase'] == 'error'
    assert operation(b)['attempts'] == saved['attempts']
    assert override_path(saved['attempts'][0]['nativeSessionId']).read_bytes() == before