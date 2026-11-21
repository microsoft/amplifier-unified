from types import SimpleNamespace
import copy
import itertools
import pytest

from amplifier_web.update_blockers import blockers


def test_background_worker_links_parent_and_blocked_task_is_not_active():
    service = SimpleNamespace(state={'sessions': [
        {'id': 'parent', 'title': 'My chat', 'status': 'idle', 'task': {'status': 'blocked'}},
        {'id': 'worker', 'parentId': 'parent', 'status': 'working'}]})
    assert blockers(service) == [{'kind': 'conversation', 'label': 'Conversation work is running or finishing',
                                   'sessionId': 'parent', 'title': 'My chat'}]
    service.state['sessions'][1]['status'] = 'idle'
    assert blockers(service) == []


def test_persistent_idle_worker_and_nonchat_activity_are_explicit():
    service = SimpleNamespace(state={'sessions': [{'id': 'chat', 'workers': [{'persistent': True, 'status': 'idle'}]}],
                                     'smartTools': {'operations': [{'status': 'running'}]},
                                     'feedback': {'requests': [{'status': 'sending'}]}})
    reasons = blockers(service)
    assert {row['kind'] for row in reasons} == {'worker', 'smart-tools', 'feedback'}
    assert next(row for row in reasons if row['kind']=='worker')['sessionId'] == 'chat'


def test_pending_runtime_links_preparing_chat():
    service = SimpleNamespace(state={'sessions': [{'id': 'chat', 'title': 'Starting chat'}]},
        runtime=SimpleNamespace(has_pending_operations=lambda: True, pending_session_ids=lambda: ['chat']))
    assert blockers(service)[0]['title'] == 'Starting chat'


def legacy_service():
    return SimpleNamespace(
        state={'revision': 17, 'sessions': [{'id': 'old-chat', 'title': 'Old recovery',
            'status': 'error', 'configurationBusy': True,
            'pendingConfiguration': {'phase': 'error', 'recoveryRequired': True}}]},
        management=SimpleNamespace(pending_config_tasks={}, lock=SimpleNamespace(locked=lambda: False)),
        runtime=SimpleNamespace(has_pending_operations=lambda: False, workers={}, _preparations={},
                                stopped_boundary=lambda: None))


def test_legacy_hold_global_wait_is_not_attributed_to_old_chat():
    """Old-code falsifier: the old gate calls this 'settings are being applied'."""
    service = legacy_service()
    service.runtime.has_pending_operations = lambda: True
    service.runtime.pending_session_ids = lambda: ['other-chat']
    service.state['sessions'].append({'id': 'other-chat', 'title': 'Other work', 'status': 'idle'})
    rows = blockers(service)
    runtime = next(row for row in rows if row['kind'] == 'runtime')
    hold = next(row for row in rows if row['kind'] == 'configuration')
    assert runtime['sessionId'] == 'other-chat'
    assert hold.get('reasonCode') == 'runtime-operations-pending', rows
    assert hold['scope'] == 'global' and 'sessionId' not in hold
    assert 'being applied' not in hold['label']


def test_configuration_gate_depends_on_activity_not_saved_recovery():
    from amplifier_web.update_blockers import assess
    # Independent combinations exercise precedence, not only one guard at a time.
    for bits in itertools.product((False, True), repeat=10):
        service = legacy_service()
        session = service.state['sessions'][0]
        operation, fence, pending, refresh, turn, worker, persistent, task, lock, runtime = bits
        session['pendingConfiguration']['operationId'] = 'held' if operation else None
        session.update(configurationOperationFence='held' if fence else None,
            configurationPending=pending, configurationRefresh={'phase': 'applying' if refresh else 'idle'},
            execution={'turns': [{'phase': 'running' if turn else 'done'}]},
            workers=[{'status': 'working' if worker else 'idle', 'persistent': persistent}])
        service.management.pending_config_tasks = {'old-chat': SimpleNamespace(done=lambda: not task)}
        service.management.lock = SimpleNamespace(locked=lambda: lock)
        service.runtime.has_pending_operations = lambda: runtime
        before = copy.deepcopy(service.state)
        observed = assess(service)
        active = turn or worker or persistent or task or lock or runtime
        assert observed['observation']['configurationHolds'][0]['quiescent'] == (not active), bits
        assert bool(observed['blockers']) == active, bits
        assert service.state == before


def test_saved_phase_and_recovery_do_not_replace_activity_observation():
    from amplifier_web.update_blockers import assess
    for status, phase, recovery, observer in itertools.product(
            ('idle', 'stopped', 'interrupted', 'error', 'working', 'starting', 'stopping', 'ready', None),
            ('error', 'queued', 'applying', 'unknown', None), (True, False, None),
            ('present', 'no-management', 'no-tasks', 'no-lock', 'no-runtime')):
        service = legacy_service()
        session = service.state['sessions'][0]
        session.update(status=status, pendingConfiguration={'phase': phase, 'recoveryRequired': recovery})
        if observer == 'no-management':
            del service.management
        elif observer == 'no-tasks':
            service.management.pending_config_tasks = None
        elif observer == 'no-lock':
            service.management.lock = None
        elif observer == 'no-runtime':
            service.runtime.has_pending_operations = None
        observed = assess(service)
        blocked = status not in {'idle', 'stopped', 'interrupted', 'error'} or observer != 'present'
        assert observed['observation']['configurationHolds'][0]['quiescent'] == (not blocked)
        assert bool(observed['blockers']) == blocked


def test_bounded_observation_does_not_bound_the_safety_gate():
    from amplifier_web.update_blockers import assess
    service = legacy_service()
    template = service.state['sessions'][0]
    service.state['sessions'] = [{**copy.deepcopy(template), 'id': f'private-chat-{i}'} for i in range(101)]
    service.state['sessions'][-1]['workers'] = [{'status': 'working'}]
    observed = assess(service)
    detail = observed['observation']
    assert detail['blocked'] and len(observed['blockers']) == 1
    assert detail['configurationHoldCount'] == 101
    assert len(detail['configurationHolds']) == 100 and detail['omittedConfigurationHolds'] == 1
    assert detail['stateRevision'] == 17 and detail['observedAt'] > 0
    assert all(row['quiescent'] for row in detail['configurationHolds'])
    assert detail['configurationReasonCounts'] == {'idle-configuration-hold': 100, 'active-worker': 1}


def test_other_session_configuration_task_blocks_and_runtime_is_sampled_once():
    from amplifier_web.update_blockers import assess
    service = legacy_service()
    calls = []
    service.runtime.has_pending_operations = lambda: calls.append('runtime') or False
    service.management.lock.locked = lambda: calls.append('lock') or False
    service.management.pending_config_tasks['other-chat'] = SimpleNamespace(done=lambda: False)
    assert assess(service)['observation']['blocked']
    assert calls == ['runtime', 'lock']
    calls.clear()
    service.management.pending_config_tasks.clear()
    service.management.pending_config_tasks['old-chat'] = SimpleNamespace(done=lambda: calls.append('task') or False)
    assert assess(service)['observation']['configurationHolds'][0]['reasonCode'] == 'configuration-task-pending'
    assert calls == ['runtime', 'lock', 'task']


def test_missing_runtime_observer_is_reported_without_changing_gate_policy():
    from amplifier_web.update_blockers import assess
    for runtime in (None, SimpleNamespace(), SimpleNamespace(has_pending_operations=None),
                    SimpleNamespace(has_pending_operations=lambda: False)):
        service = SimpleNamespace(state={'sessions': []}, runtime=runtime)
        observation = assess(service)['observation']
        assert observation['blocked'] is False  # Existing gate policy, not proof of inactivity.
        assert observation['initialRuntimeObservation'] == (
            'clear' if callable(getattr(runtime, 'has_pending_operations', None)) else 'unavailable')


def test_one_activity_observation_is_shared_by_all_holds():
    from amplifier_web.update_blockers import assess
    service = legacy_service()
    service.state['sessions'] = [
        {**copy.deepcopy(service.state['sessions'][0]), 'id': f'held-{number}'}
        for number in range(4)]
    samples = iter((False, True))
    calls = []
    service.runtime.has_pending_operations = lambda: calls.append('runtime') or next(samples)
    service.management.lock.locked = lambda: calls.append('lock') or False
    observation = assess(service)['observation']
    assert calls == ['runtime', 'lock']
    assert observation['initialRuntimeObservation'] == 'clear'
    assert observation['configurationHolds'][0]['reasonCode'] == 'idle-configuration-hold'
    assert observation['configurationHoldCount'] == 4
    assert observation['blocked'] is False


@pytest.mark.parametrize('delivery', ['sending', 'accepted', 'failed', 'unknown', 'held', 'queued_for_update'])
def test_ready_admitted_input_is_work_but_retained_delivery_is_not(delivery):
    service = SimpleNamespace(state={'sessions': [{'id': 'chat', 'status': 'ready',
        'messages': [{'inputId': 'input', 'delivery': {'status': delivery}}]}]})
    assert bool(blockers(service)) == (delivery == 'sending')
    service.state['sessions'][0]['status'] = 'stopped'
    assert not blockers(service)  # Stop is not reopened by an old message.


def test_update_activity_does_not_load_cold_messages(monkeypatch):
    from amplifier_web import cold_display
    session = cold_display.ColdRecord({'id': 'cold', 'status': 'ready',
        '_coldFields': {'messages': {'$resource': 'never-read'}}}, None)
    monkeypatch.setattr(cold_display, 'load', lambda *args: pytest.fail('No cold message scan'))
    service = SimpleNamespace(state={'sessions': [session]})
    assert not blockers(service)
