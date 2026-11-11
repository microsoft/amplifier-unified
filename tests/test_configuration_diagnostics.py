"""Exact passive predicate observations, synthetic records only; no provider/worker."""
import copy
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import configuration_recovery as recovery, update_blockers
from amplifier_web.configuration_diagnostics import observation
from amplifier_web.service import AppError
from test_update_terminal_configuration import terminal, originals
from test_targeted_configuration_recovery import manager, invoke


def args(f, **extra):
    saved = f.app.management.configuration_record(f.session['id'], f.attempt['operationId'])
    return dict(sessionId=f.session['id'], operationId=saved['id'], expectedRevision=saved['revision'],
                includeRecovery=True, diagnostic='first_failure', **extra)


async def read(f, supplied=None):
    return (await f.app.dispatch('configuration.operation.read', supplied or args(f)))['result']


async def test_passive_first_failure_selector(terminal):
    f = terminal
    before = originals(f)
    normal = (await f.app.dispatch('configuration.operation.read',
        {k: v for k, v in args(f).items() if k not in {'diagnostic', 'expectedRevision'}}))['result']
    view = await read(f)
    assert {k: v for k, v in view.items() if k != 'diagnostic'} == normal
    detail = view['diagnostic']
    assert detail['expectedRevision'] == view['revision']
    assert detail['loadedComparison'] == 'not_observed'
    assert detail['recovery']['reconcile'] == {
        'availability': 'observed', 'firstFailure': None, 'savedComparison': 'not_observed'}
    assert detail['recovery']['resume']['savedComparison'] == 'match'
    assert detail['appUpdateQuiescence'] == {
        'availability': 'observed', 'firstFailure': None, 'savedComparison': 'match'}
    assert originals(f) == before and not f.app.runtime.workers
    assert not f.transport.calls and not f.transport.inputs


@pytest.mark.parametrize('fault,code', [
    ('terminal', 'saved_terminal'), ('native', 'saved_target'), ('saved', 'saved_comparison'),
    ('missing-saved', 'saved_plan'), ('target', 'original_target'), ('payload', 'original_payload'),
    ('fence', 'operation_fence'), ('phase', 'operation_phase'), ('task', 'configuration_task')])
async def test_recovery_first_failure_and_saved_availability(terminal, monkeypatch, fault, code):
    f = terminal
    if fault == 'terminal':
        recovery.receipt_path(f.attempt, home=f.app.data_dir).write_text(json.dumps(
            recovery.seal(f.attempt, 'saved_not_mounted', terminal=False)))
    elif fault == 'native':
        f.session['runtimeSessionId'] = 'another-native'
    elif fault in {'saved', 'missing-saved'}:
        path = f.app.data_dir / 'sessions' / f.session['id'] / 'configuration.json'
        if fault == 'missing-saved':
            path.unlink()
        else:
            saved = json.loads(path.read_text())
            saved['tools'][0]['config']['SYNTHETIC_SECRET_FIELD'] = ['SECRET', '/secret/path']
            path.write_text(json.dumps(saved))
    elif fault == 'target':
        f.session['executionRevision'] = 1
    elif fault == 'payload':
        f.app.management.queued_path(f.session['id']).write_text('{"SYNTHETIC_SECRET_FIELD":"SECRET"}')
    elif fault == 'fence':
        f.session['configurationOperationFence'] = 'another-operation'
    elif fault == 'phase':
        f.app.management.configuration_transition(f.session, f.attempt['operationId'], phase='queued')
    else:
        monkeypatch.setattr(f.app.management.lock, 'locked', lambda: True)
    before = originals(f)
    view = await read(f)
    detail = view['diagnostic']['recovery']['resume']
    assert not view['recovery']['canResume'] and detail['firstFailure'] == code
    assert detail['savedComparison'] == ('mismatch' if fault == 'saved' else 'not_observed')
    assert view['diagnostic']['loadedComparison'] == 'not_observed'
    assert not any(value in json.dumps(view) for value in ['SYNTHETIC_SECRET_FIELD', 'SECRET', '/secret/path'])
    assert originals(f) == before and not f.transport.calls


@pytest.mark.parametrize('fault,code', [
    ('freshness', 'update_hold'), ('worker', 'update_activity'),
    ('unknown', 'native_terminal'), ('binding', 'update_binding'),
    ('attempts', 'update_attempts')])
async def test_update_quiescence_is_independent_canonical_observation(terminal, fault, code):
    f = terminal
    if fault == 'freshness':
        f.session['configurationPending'] = True
    elif fault == 'worker':
        f.app.runtime.workers[f.session['id']] = {'process': SimpleNamespace(returncode=0)}
    elif fault == 'unknown':
        recovery.receipt_path(f.attempt, home=f.app.data_dir).write_text(json.dumps(
            recovery.seal(f.attempt, 'unknown', terminal=True)))
    elif fault == 'binding':
        f.session['pendingConfiguration']['payloadHash'] = 'not-the-payload'
    else:
        path = f.app.management.configuration_record_path(f.session['id'], f.attempt['operationId'])
        saved = json.loads(path.read_text()); saved['attempts'] = []
        path.write_text(json.dumps(saved))
    before = originals(f)
    with pytest.raises(ValueError):
        with observation() as baseline:
            update_blockers.operation_snapshot(f.app, f.session)
    view = await read(f)
    assert view['diagnostic']['appUpdateQuiescence'] == baseline
    assert baseline['firstFailure'] == code
    assert originals(f) == before and not f.transport.calls


@pytest.mark.parametrize('missing', ['operationId', 'expectedRevision', 'includeRecovery'])
async def test_missing_exact_context_refuses_before_record_read(terminal, monkeypatch, missing):
    f = terminal
    supplied = args(f); supplied.pop(missing)
    spy = monkeypatch.setattr
    def forbidden(*a, **kw):
        raise AssertionError('No operation IO before explicit observation context')
    spy(f.app.management, 'configuration_record', forbidden)
    with pytest.raises(AppError):
        await read(f, supplied)


async def test_stale_context_refuses_before_native_evidence(terminal, monkeypatch):
    f = terminal
    supplied = args(f); supplied['expectedRevision'] -= 1
    monkeypatch.setattr(recovery, 'evidence', lambda *a, **kw: pytest.fail('No native evidence after revision refusal'))
    with pytest.raises(AppError, match='cannot be verified'):
        await read(f, supplied)


@pytest.mark.parametrize('diagnostic', [False, True])
async def test_exact_read_keeps_target_full_gate(terminal, diagnostic):
    f = terminal; f.owner = f.session
    source = await manager(f, mode='ask')
    before = originals(f)
    with pytest.raises(AppError):
        await invoke(f, source, 'read', includeRecovery=True,
            **({'diagnostic': 'first_failure', 'expectedRevision': args(f)['expectedRevision']} if diagnostic else {}))
    assert originals(f) == before and not f.transport.calls


async def test_passive_same_recovery_io_order_and_no_effects(terminal, monkeypatch):
    f = terminal
    expected = args(f)
    original_check = f.app.management.check_configuration_operation
    original_matches = f.app.management.configuration_readback_matches
    original_evidence = recovery.evidence
    original_record = f.app.management.configuration_record
    calls = []
    def wrap(name, original):
        def call(*a, **kw):
            calls.append(name)
            return original(*a, **kw)
        return call
    for obj, name, original in [
        (f.app.management, 'check_configuration_operation', original_check),
        (f.app.management, 'configuration_readback_matches', original_matches),
        (recovery, 'evidence', original_evidence),
        (f.app.management, 'configuration_record', original_record)]:
        monkeypatch.setattr(obj, name, wrap(name, original))
    for name in ['start', 'control', 'stop']:
        monkeypatch.setattr(f.app.runtime, name, AsyncMock(side_effect=AssertionError('No runtime effect')))
    monkeypatch.setattr(f.app.management, 'schedule_configuration', lambda *a: pytest.fail('No schedule'))
    monkeypatch.setattr(f.app, 'refresh_configuration', AsyncMock(side_effect=AssertionError('No refresh')))
    monkeypatch.setattr(recovery, 'write_private', lambda *a: pytest.fail('No operation write'))
    before = originals(f)
    grants = copy.deepcopy(f.app.configuration_authority.rows(f.session['id']))
    tasks = set(f.app.tasks)
    writes = []
    f.app.db.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().split()[0].upper()
        in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'} else None)
    await read(f, {k: v for k, v in expected.items() if k not in {'diagnostic', 'expectedRevision'}})
    baseline = calls[:]; calls.clear()
    view = await read(f, expected)
    # Diagnostic recovery evaluates the same two validations once each.
    assert calls[:len(baseline)] == baseline
    # The separately requested app predicate performs its own existing observation.
    assert calls[len(baseline):] == ['configuration_record', 'evidence', 'configuration_readback_matches']
    assert originals(f) == before and view['workReplayed'] is False
    assert not writes and set(f.app.tasks) == tasks
    assert f.app.configuration_authority.rows(f.session['id']) == grants
    f.app.db.set_trace_callback(None)


async def test_early_failure_does_not_evaluate_later_predicates_and_errors_stay_private(terminal, monkeypatch):
    f = terminal
    f.app.management.configuration_transition(f.session, f.attempt['operationId'], phase='queued')
    monkeypatch.setattr(f.app.management, 'check_configuration_operation',
        lambda *a: pytest.fail('Later authority/payload IO not allowed'))
    view = await read(f)
    assert view['diagnostic']['recovery']['resume']['firstFailure'] == 'operation_phase'
    # Return to settled state, then emulate an unavailable private saved file.
    f.app.management.configuration_transition(f.session, f.attempt['operationId'], phase='error')
    monkeypatch.undo()
    original = Path.read_text
    def unreadable(path, *a, **kw):
        if path.name == 'configuration.json':
            raise OSError('SECRET /private/secret arbitrary-field')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', unreadable)
    view = await read(f)
    assert view['diagnostic']['recovery']['resume']['firstFailure'] == 'saved_plan'
    assert view['diagnostic']['recovery']['resume']['availability'] == 'unavailable'
    assert 'SECRET' not in json.dumps(view) and 'arbitrary-field' not in json.dumps(view)


async def test_other_pending_operation_never_observes_replacement(terminal, monkeypatch):
    f = terminal
    supplied = args(f)
    f.session['pendingConfiguration']['operationId'] = 'new-operation'
    monkeypatch.setattr(recovery, 'evidence', lambda *a, **kw: pytest.fail('No replacement/native read'))
    monkeypatch.setattr(update_blockers, 'operation_snapshot', lambda *a: pytest.fail('No replacement snapshot'))
    with pytest.raises(AppError, match='cannot be verified'):
        await read(f, supplied)


async def test_exact_full_observer_cannot_turn_diagnostic_into_permission(terminal):
    f = terminal; f.owner = f.session
    source = await manager(f)
    expected = args(f)
    before = originals(f)
    result = (await invoke(f, source, 'read', includeRecovery=True,
        diagnostic='first_failure', expectedRevision=expected['expectedRevision']))['result']
    assert result['recovery']['canResume'] and result['diagnostic']['loadedComparison'] == 'not_observed'
    assert originals(f) == before and not f.transport.calls
    f.session['executionRevision'] = 1
    with pytest.raises(AppError):
        await invoke(f, source, 'resume')
    assert originals(f) == before and not f.transport.calls


async def test_observation_context_nested_exception_and_task_isolation():
    from amplifier_web.configuration_diagnostics import checkpoint, PredicateFailure
    async def inspect(code):
        with pytest.raises(PredicateFailure):
            with observation() as row:
                checkpoint(code)
                await asyncio.sleep(0)
                raise PredicateFailure(code, 'SECRET')
        return row
    first, second = await asyncio.gather(inspect('saved_terminal'), inspect('native_target'))
    assert first['firstFailure'] == 'saved_terminal' and second['firstFailure'] == 'native_target'
    with observation() as outer:
        checkpoint('original_target')
        with observation() as inner:
            checkpoint('saved_comparison')
        assert inner['availability'] == 'observed'
    assert outer['availability'] == 'observed' and outer['firstFailure'] is None
    # No failed observation leaks into the next call.
    with observation() as clean:
        pass
    assert clean == {'availability': 'observed', 'firstFailure': None, 'savedComparison': 'not_observed'}


def test_unknown_comparison_and_hostile_discriminator_stay_closed():
    from amplifier_web.configuration_diagnostics import checkpoint, PredicateFailure
    with pytest.raises(TypeError):
        with observation() as unknown:
            checkpoint('saved_comparison')
            raise TypeError('SECRET arbitrary-source/path')
    assert unknown == {'availability': 'unavailable', 'firstFailure': 'saved_comparison',
                       'savedComparison': 'unknown'}
    error = PredicateFailure('saved_plan', 'SECRET')
    error.code = 'SECRET/arbitrary'
    with pytest.raises(PredicateFailure):
        with observation() as hostile:
            raise error
    assert hostile['firstFailure'] == 'not_observed' and 'SECRET' not in json.dumps(hostile)


@pytest.mark.parametrize('fault', ['workspace', 'child', 'forged', 'missing-principal', 'system'])
async def test_diagnostic_cannot_borrow_target_full_authority(terminal, fault):
    from test_coordination_generation import agent
    f = terminal; f.owner = f.session
    source = await manager(f)
    supplied = args(f)
    transport = {}
    if fault == 'workspace':
        source['workspace'] += '-elsewhere'
    elif fault == 'child':
        transport['_childOrigin'] = {}
    elif fault == 'forged':
        transport['_runtimeSessionId'] = f.session['id']
    elif fault == 'missing-principal':
        transport['_runtimeSessionId'] = None
    before = originals(f)
    with pytest.raises((AppError, ValueError)):
        if fault == 'system':
            await f.app.dispatch('configuration.operation.read', supplied, origin='system',
                caller_session_id=source['id'])
        else:
            await agent(f.app, source, 'configuration.operation.read', supplied, 'diagnostic-refused', **transport)
    assert originals(f) == before and not f.transport.calls