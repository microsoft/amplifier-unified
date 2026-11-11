"""Private startup clocks keep authority, errors and native history unchanged."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import call

import pytest

from amplifier_web.worker_diagnostics import StartupTimings
from test_host_session_resume import mounted_host
from test_worker_install_overrides import PolicyBoundaryReached, incoming_policy, policy_worker


def test_fixed_labels_bounded_rows_private_receipt_and_optional_disk(tmp_path, monkeypatch):
    timing = StartupTimings()
    with timing.phase('untrusted secret text'):
        pass
    for _ in range(100):
        with timing.phase('manager.plan'):
            pass
    timing.save(tmp_path, 'completed')
    path = tmp_path / 'startup-timing.json'
    result = json.loads(path.read_text())
    assert len(result['phases']) == timing.MAX_PHASES
    assert result['elapsedMs'] >= 0 and result['cpuMs'] >= 0
    assert all(row['phase'] == 'manager.plan' and row['status'] == 'succeeded' for row in result['phases'])
    assert 'untrusted' not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    fault_calls = []
    def fail_write(*args, **kwargs):
        fault_calls.append((args, kwargs))
        raise OSError('synthetic disk failure')
    monkeypatch.setattr('amplifier_web.host.config.write_private', fail_write)
    timing.save(tmp_path, 'failed')
    assert len(fault_calls) == 1 and fault_calls[0][0][0] == path
    assert json.loads(path.read_text()) == result


@pytest.mark.parametrize('failure', [RuntimeError('private configuration'), asyncio.CancelledError('private cancellation')])
def test_failure_and_cancellation_are_reraised_exactly_without_details(failure):
    timing = StartupTimings()
    with pytest.raises(type(failure)) as caught:
        with timing.phase('manager.prepare'):
            with timing.phase('manager.session.create'):
                raise failure
    assert caught.value is failure
    result = timing.snapshot('cancelled' if isinstance(failure, asyncio.CancelledError) else 'failed')
    assert all(row['status'] == result['outcome'] for row in result['phases'])
    assert 'private' not in json.dumps(result)
    assert result['phases'][0]['elapsedMs'] >= result['phases'][1]['elapsedMs']


async def test_native_resume_includes_load_restore_and_checkpoint_clocks(mounted_host):
    h = mounted_host
    messages = [{'role': 'user', 'content': 'Retained native history'}, {'role': 'assistant', 'content': 'Retained reply'}]
    h.store.save(h.runtime.session_id, messages, {'bundle_name': 'anchors'})
    timing = StartupTimings()
    _, _, report = await h.prepare(startup_timing=timing)
    assert h.store.load(h.runtime.session_id)[0] == messages
    assert report['resumed'] and report['history_source'] == 'native'
    phases = timing.snapshot()['phases']
    assert all(row['status'] == 'succeeded' for row in phases)
    names = [row['phase'] for row in phases]
    for name in ('manager.history.load', 'manager.bundle.prepare', 'manager.session.create',
                 'manager.context.restore', 'manager.children', 'manager.settings', 'manager.checkpoint'):
        assert names.count(name) == 1
    assert names.index('manager.context.restore') < names.index('manager.checkpoint')
    assert 'Retained' not in json.dumps(phases)


async def test_context_cancellation_keeps_cleanup_and_original_outcome(mounted_host):
    h = mounted_host
    messages = [{'role': 'user', 'content': 'Saved'}]
    h.store.save(h.runtime.session_id, messages, {'bundle_name': 'anchors'})
    failure = asyncio.CancelledError('do not publish')
    async def cancelled(_):
        raise failure
    h.context.set_messages = cancelled
    timing = StartupTimings()
    with pytest.raises(asyncio.CancelledError) as caught:
        await h.prepare(startup_timing=timing)
    assert caught.value is failure
    h.session.cleanup.assert_awaited_once()
    assert h.store.load(h.runtime.session_id)[0] == messages
    rows = timing.snapshot()['phases']
    assert rows[-1]['phase'] == 'manager.context.restore' and rows[-1]['status'] == 'cancelled'
    assert not any(row['phase'] == 'manager.checkpoint' for row in rows)


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('disk_fails', [False, True])
async def test_worker_failure_receipt_does_not_publish_timings(tmp_path, monkeypatch, cancel, disk_fails):
    import sys
    from amplifier_web.runtime_worker import Worker
    from amplifier_foundation.session.activation import ActivationGate
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.host.config.app_home', lambda: tmp_path)
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', lambda *args: None)
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime',
                        SimpleNamespace(Runtime=lambda **kwargs: SimpleNamespace(session_id='fixture')))
    fault_calls = []
    if disk_fails:
        def fail_write(*args, **kwargs):
            fault_calls.append((args, kwargs))
            raise OSError('synthetic disk failure')
        monkeypatch.setattr('amplifier_web.host.config.write_private', fail_write)
    failure = asyncio.CancelledError('secret') if cancel else ValueError('secret')
    async def failed(*args, **kwargs):
        raise failure
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', failed)
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker = Worker()
    worker.shared_store = worker.shared_handle = SimpleNamespace()
    worker.activation_gate = ActivationGate()
    with pytest.raises(type(failure)) as caught:
        await worker.start({'id': 'fixture', 'workspace': str(tmp_path)}, recover_bundle=False, raise_errors=True)
    assert caught.value is failure
    path = tmp_path / 'runtime-reports/fixture/startup-timing.json'
    if disk_fails:
        assert len(fault_calls) == (1 if cancel else 2)
        assert sum(args[0] == path for args, _ in fault_calls) == 1
        assert all(kwargs == {'owned_root': tmp_path} for _, kwargs in fault_calls)
        assert not path.exists()
        assert 'startup-timing' not in json.dumps(events)
        return
    receipt = json.loads(path.read_text())
    assert receipt['outcome'] == ('cancelled' if cancel else 'failed')
    assert receipt['phases'][-1]['phase'] == 'manager.prepare'
    assert receipt['phases'][-1]['status'] == receipt['outcome']
    assert not any(row['phase'] == 'worker.ready' for row in receipt['phases'])
    assert 'startup-timing' not in json.dumps(events)
    assert 'secret' not in json.dumps(receipt)


def verification_receipt(state):
    path = state.home / 'runtime-reports' / state.config['id'] / 'startup-timing.json'
    receipt = json.loads(path.read_text())
    assert path.stat().st_mode & 0o777 == 0o600
    assert dict(os.environ) == state.parent_env
    assert state.worker.execution is None
    assert not any(event.get('type') == 'runtime.ready' for event in state.events)
    assert 'startup-timing' not in json.dumps(state.events)
    assert str(state.qualified) not in json.dumps(receipt)
    assert 'UV_OVERRIDE' not in json.dumps(receipt)
    state.capture.close.assert_called_once()
    rows = [row for row in receipt['phases'] if row['phase'].startswith('worker.runtime.verify.')]
    for row in rows:
        assert row['elapsedMs'] >= 0 and row['cpuMs'] >= 0 and row['offsetMs'] >= 0
    return receipt, rows


@pytest.mark.parametrize('branch', ['qualified', 'legacy', 'explicit'])
async def test_worker_verification_clocks_follow_actual_policy_branches(policy_worker, monkeypatch, branch):
    """The maintained fixture stops after postcheck; this is not a ready-worker proof."""
    state = policy_worker
    incoming_policy(state, monkeypatch, 'custom' if branch == 'explicit' else 'absent')
    state.selected = None if branch == 'legacy' else state.qualified
    with pytest.raises(PolicyBoundaryReached):
        await state.worker.start(state.config, raise_errors=True, recover_bundle=False)

    receipt, rows = verification_receipt(state)
    expected = [] if branch == 'explicit' else ['worker.runtime.verify.before_prepare']
    if branch == 'qualified':
        expected.append('worker.runtime.verify.after_prepare')
    assert [row['phase'] for row in rows] == expected
    assert all(row['status'] == 'succeeded' and 'errorType' not in row for row in rows)
    assert receipt['outcome'] == 'failed'  # Deliberate PolicyBoundaryReached after the checks.
    names = [row['phase'] for row in receipt['phases']]
    if expected:
        assert names.index(expected[0]) < names.index('manager.prepare')
    if branch == 'qualified':
        assert names.index('manager.prepare') < names.index(expected[1])
    calls = [] if branch == 'explicit' else [call(state.home)]
    if branch == 'qualified':
        calls.append(call(state.home, str(state.qualified)))
    assert state.policy.call_args_list == calls
    assert state.order == (['prepare'] if branch == 'explicit' else
                           ['policy', 'prepare', 'policy'] if branch == 'qualified' else ['policy', 'prepare'])
    assert state.options['install_overrides'] == (
        None if branch == 'explicit' else state.qualified if branch == 'qualified' else state.compatibility)
    state.prepare.assert_awaited_once()


@pytest.mark.parametrize('failed_call', [1, 2], ids=['before_prepare', 'after_prepare'])
@pytest.mark.parametrize('cancel', [False, True], ids=['refusal', 'cancellation'])
async def test_worker_verification_clocks_preserve_exact_refusal_and_cancellation(
        policy_worker, monkeypatch, failed_call, cancel):
    state = policy_worker
    incoming_policy(state, monkeypatch, 'absent')
    failure = (asyncio.CancelledError('private verification cancellation') if cancel else
               ValueError('The worker graph changed after qualification; its recorded generation was preserved.'))
    original_args = failure.args

    def verify(*args):
        assert dict(os.environ) == state.parent_env
        state.order.append('policy')
        if state.policy.call_count == failed_call:
            raise failure
        return state.qualified

    state.policy.side_effect = verify
    with pytest.raises(type(failure)) as caught:
        await state.worker.start(state.config, raise_errors=True, recover_bundle=False)
    assert caught.value is failure and caught.value.args == original_args
    receipt, rows = verification_receipt(state)
    expected = ['worker.runtime.verify.before_prepare', 'worker.runtime.verify.after_prepare'][:failed_call]
    assert [row['phase'] for row in rows] == expected
    assert receipt['outcome'] == ('cancelled' if cancel else 'failed')
    assert rows[-1]['status'] == receipt['outcome']
    assert rows[-1]['errorType'] == type(failure).__name__
    assert all(row['status'] == 'succeeded' for row in rows[:-1])
    assert receipt['phases'][-1] == rows[-1]
    assert str(failure) not in json.dumps(receipt)
    calls = [call(state.home)]
    if failed_call == 2:
        calls.append(call(state.home, str(state.qualified)))
        state.prepare.assert_awaited_once()
        assert state.options['install_overrides'] == state.qualified
    else:
        state.prepare.assert_not_awaited()
    assert state.policy.call_args_list == calls
    assert state.order == (['policy'] if failed_call == 1 else ['policy', 'prepare', 'policy'])
    if cancel:
        state.capture.save.assert_not_called()
