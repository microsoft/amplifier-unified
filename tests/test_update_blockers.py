from types import SimpleNamespace
import copy
import itertools
import sqlite3
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


@pytest.fixture
def cold_execution(tmp_path):
    """Real immutable pages, one current turn/tool, and retained receipt bodies."""
    from amplifier_web import execution_records as records
    from amplifier_web.cold_display import ColdRecord, MARKER

    connections = []
    def create(size=8, phase='running'):
        home = tmp_path / str(len(connections))
        home.mkdir()
        db = sqlite3.connect(home / 'app.sqlite3')
        connections.append(db)
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        tree = {'currentTurnId': 'current',
            'turns': [{'id': 'old', 'phase': 'completed'}, {'id': 'current', 'phase': phase}],
            'nodes': [{'id': f'tool-{i}', 'turnId': 'old', 'kind': 'tool', 'phase': 'completed',
                       'detail': {'values': [i], 'text': 'retained output ' * 32}} for i in range(size)],
            'retiredUsageNodes': [{'id': f'call-{i}', 'sessionId': 'chat', 'kind': 'llm',
                'phase': 'completed', 'usage': {'inputTokens': i, 'outputTokens': 2}}
                for i in range(size)]}
        tree['nodes'].append({'id': 'active-tool', 'turnId': 'current', 'kind': 'tool',
                              'phase': 'running', 'revision': 1, 'detail': {'values': ['current']}})
        reference = records.import_tree(db, ('chat',), tree)
        db.commit()
        session = ColdRecord({'id': 'chat', 'status': 'ready', 'externalAgent': True,
            'messages': [], 'workers': [], MARKER: {'execution': reference}}, db)
        return home, db, session, tree

    yield create
    for db in connections:
        db.close()


def execution_service(session, path):
    service = legacy_service()
    service.state['sessions'] = [session]
    if path.startswith('held-'):
        session.update(status=path.removeprefix('held-'), configurationBusy=True,
                       pendingConfiguration={'phase': 'error', 'recoveryRequired': True})
    return service


@pytest.mark.parametrize('path', ['ready', 'held-idle', 'held-error'])
@pytest.mark.parametrize('phase', [
    'running', 'completed', 'stopped', 'error', 'unknown',
    'working', 'starting', 'queued', 'pending', 'retrying', 'idle'])
@pytest.mark.parametrize('resident_owner', [False, True])
def test_update_metadata_keeps_execution_cold_and_exact(
        cold_execution, monkeypatch, path, phase, resident_owner):
    from amplifier_web import execution_records as records
    from amplifier_web.cold_display import MARKER
    from amplifier_web.execution_working_set import WorkingExecution, working, snapshot
    from amplifier_web.update_blockers import assess, working_sessions

    _, db, session, original = cold_execution(phase=phase)
    service = execution_service(session, path)
    owner = working(session) if resident_owner else None
    reference = copy.deepcopy(dict.get(session, MARKER)['execution'])
    def forbidden(*args, **kwargs):
        pytest.fail('Update metadata must not materialize completed execution')

    with monkeypatch.context() as guard:
        guard.setattr(records, 'materialize', forbidden)
        guard.setattr(records, 'read_collection', forbidden)
        guard.setattr(WorkingExecution, 'expose', forbidden)
        observed = assess(service, session_id='chat')
        assert bool(observed['blockers']) == (phase == 'running')
        assert working_sessions(service) == int(path == 'ready' and phase == 'running')
        if path.startswith('held-'):
            assert observed['selectedHold']['quiescent'] == (phase != 'running')
            assert observed['selectedHold']['reasonCode'] == (
                'running-turn' if phase == 'running' else 'idle-configuration-hold')
        if owner is not None:
            assert getattr(session, '_working_execution', None) is owner
        assert not dict.__contains__(session, 'execution')
        assert dict.get(session, MARKER)['execution'] == reference

    assert snapshot(session) == original
    assert records.materialize(db, reference) == original


@pytest.mark.parametrize('path', ['ready', 'held-idle', 'held-error'])
def test_update_metadata_observes_unsaved_turn_edits_without_replacing_owner(cold_execution, path):
    from amplifier_web.cold_display import MARKER
    from amplifier_web.execution_working_set import current, working
    from amplifier_web.update_blockers import assess

    _, _, session, _ = cold_execution(phase='running')
    service = execution_service(session, path)
    owner = working(session)
    turn = current(session)['turns'][-1]
    # The saved root still says running throughout these uncommitted edits.
    for phase in ('completed', 'running', 'stopped', 'running'):
        turn['phase'] = phase
        assert bool(assess(service)['blockers']) == (phase == 'running')
        assert getattr(session, '_working_execution', None) is owner
        assert current(session)['turns'][-1] is turn
        assert not dict.__contains__(session, 'execution')
        assert dict.get(session, MARKER)['execution']['pendingWork']


@pytest.mark.parametrize('resident_owner', [False, True])
def test_ready_sending_input_short_circuits_cold_execution(cold_execution, monkeypatch, resident_owner):
    from amplifier_web import update_blockers
    from amplifier_web.execution_working_set import working
    from amplifier_web.update_blockers import working_sessions

    _, _, session, _ = cold_execution(phase='completed')
    owner = working(session) if resident_owner else None
    session['messages'].append({'inputId': 'input', 'delivery': {'status': 'sending'}})
    service = execution_service(session, 'ready')
    # Sending admission alone already blocks; even a current-page read is unused.
    def forbidden(*args):
        pytest.fail('Sending input already establishes active work')
    monkeypatch.setattr(update_blockers, 'current_execution', forbidden)
    assert blockers(service)[0]['kind'] == 'conversation'
    assert working_sessions(service) == 1
    assert getattr(session, '_working_execution', None) is owner
    assert not dict.__contains__(session, 'execution')


@pytest.mark.parametrize('path', ['ready', 'held-idle', 'held-error'])
def test_update_metadata_checks_running_turn_outside_current_turn(cold_execution, path):
    from amplifier_web import execution_records as records
    from amplifier_web.cold_display import MARKER
    from amplifier_web.update_blockers import assess, working_sessions

    _, db, session, original = cold_execution(phase='completed')
    # The older running turn precedes a full page of settled turns. Its ID is
    # intentionally not currentTurnId: the old gate checked *any* running turn.
    original['turns'] = [{'id': 'old', 'phase': 'running'}] + [
        {'id': f'settled-{i}', 'phase': 'completed'} for i in range(records.PAGE_ROWS)
    ] + [original['turns'][-1]]
    session[MARKER]['execution'] = records.import_tree(db, ('chat',), original)
    db.commit()
    service = execution_service(session, path)
    assert bool(assess(service)['blockers'])
    assert working_sessions(service) == int(path == 'ready')
    assert not dict.__contains__(session, 'execution')


@pytest.mark.parametrize('path', ['ready', 'held-idle', 'held-error'])
def test_update_metadata_keeps_plain_mutable_execution_semantics(path):
    from amplifier_web.update_blockers import assess, working_sessions

    turn = {'phase': 'running'}  # Legacy turns need not have keyed-record IDs.
    tree = {'turns': [turn]}
    session = {'id': 'chat', 'status': 'ready', 'execution': tree}
    service = execution_service(session, path)
    for phase in ('running', 'completed', 'queued', 'running'):
        turn['phase'] = phase
        assert bool(assess(service)['blockers']) == (phase == 'running')
        assert working_sessions(service) == int(path == 'ready' and phase == 'running')
        assert session['execution'] is tree
        assert session['execution']['turns'][0] is turn


@pytest.mark.parametrize('path', ['ready', 'held-idle', 'held-error'])
def test_update_metadata_then_changed_save_keeps_paged_history(cold_execution, monkeypatch, path):
    from amplifier_web import execution_records as records, resource_files, session_projection
    from amplifier_web.cold_display import MARKER, load
    from amplifier_web.execution_preparation import ExecutionPreparation
    from amplifier_web.execution_working_set import current
    from amplifier_web.update_blockers import assess, working_sessions

    measurements = []
    # Both cases leave the active tool on a one-row final page, with the same
    # turn page. Only completed node and receipt history grows (8x).
    for size in (128, 1024):
        home, db, session, original = cold_execution(size=size)
        service = execution_service(session, path)
        before = copy.deepcopy(dict.get(session, MARKER)['execution'])
        decoded_rows, decoded_bytes, prepared_bytes = [], [], []
        read, put = resource_files.read_detached, resource_files.put_prepared
        def counted_read(source, **kwargs):
            value = read(source, **kwargs)
            decoded_rows.append(len(value.get('order', [])))
            signature = resource_files.detached_signature(source)
            decoded_bytes.append(signature[2] if len(signature) == 5 else len(source.indexed.encode()))
            return value
        def counted_put(db, prepared, **kwargs):
            prepared_bytes.append(prepared.size)
            return put(db, prepared, **kwargs)

        with monkeypatch.context() as counter:
            counter.setattr(resource_files, 'read_detached', counted_read)
            counter.setattr(resource_files, 'put_prepared', counted_put)
            assert bool(assess(service)['blockers'])
            assert working_sessions(service) == int(path == 'ready')
            current(session)['nodes'][-1]['detail']['values'].append('changed')
            preparation, pending = ExecutionPreparation(), {}
            saved = session_projection.persist(home, service.state, {}, db=db,
                execution_preparation=preparation, pending_execution=pending)
            db.commit()
            preparation.committed(pending, {'chat': session})

        selected = saved['sessions'][0][MARKER]['execution']
        measurement = (sum(decoded_rows), sum(prepared_bytes),
                       selected.get('executionProjection'), dict.__contains__(session, 'execution'),
                       sum(decoded_bytes))
        measurements.append(measurement)
        print(f'{path}: history={size}, decoded_rows={measurement[0]}, '
              f'decoded_bytes={measurement[4]}, prepared_bytes={measurement[1]}, projection={measurement[2]}')
        expected = copy.deepcopy(original)
        expected['nodes'][-1]['detail']['values'].append('changed')
        assert load(db, selected) == expected
        assert records.materialize(db, before) == original
    for rows, byte_count, projection, resident, read_bytes in measurements:
        assert projection == 2
        # A current-page mutation must not encode a full node/receipt snapshot.
        # This fixture changes one small tool and retains a two-row turn page.
        assert rows < 128
        assert byte_count < 32 * 1024
        assert read_bytes < 32 * 1024
        assert not resident
    assert measurements[0][0] == measurements[1][0]
