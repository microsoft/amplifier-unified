"""Live activation presentation never weakens durable replacement uncertainty.

The real stage/activate lifecycle uses the existing synthetic install/probe fixture.
No application, installer, listener or restart process is launched.
"""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import app_updates, browser_demand, update_health as health
from amplifier_web.updates import UpdateManager
from test_update_replacement import (
    managed, stage_replacement, replacement_install, successful_probe,
    fail_replacement, saved_updates,
)
from test_update_current_health import acknowledged_app_with_old_component_history


WAITING = 'Application release validated. Waiting for work to finish before restarting.'
STARTING = 'Application release validated. No work is running. Amplifier installs the update and restarts by itself.'
CONNECTION = ' No AI connection is set up yet. A component update starts only after you set one up in Settings.'
WORK = {'kind': 'conversation', 'sessionId': 'busy'}


def manager_for(state, **attributes):
    return SimpleNamespace(service=SimpleNamespace(state={'updates': state}), **attributes)


@pytest.fixture(autouse=True)
def no_claim_outlives_its_test():
    """Every path of an activation must withdraw its claim, also a failed or a cancelled one."""
    yield
    assert not health._EXECUTING


@pytest.fixture
def executing():
    """Say that this process executes an attempt, as app_updates does at the marker write."""
    claimed = []

    def claim(attempt):
        health.claim_replacement(attempt)
        claimed.append(attempt)

    yield claim
    for attempt in claimed:
        health.release_replacement(attempt)


def prepared(**values):
    return {'phase': 'app-staged', 'pendingApp': {'revision': 'a' * 40},
            'application': {'canInstall': True}, 'blockers': [], **values}


@pytest.mark.parametrize('blockers,expected', [([], 'starting'), ([WORK], 'waiting')])
@pytest.mark.parametrize('change', [
    {}, {'pendingApp': None}, {'pendingApp': {}}, {'phase': 'staged'},
    {'phase': 'error'}, {'phase': 'interrupted'}, {'phase': 'activating'},
    {'error': 'failed'}, {'pendingRestart': {'attemptId': 'restart'}},
    {'pendingReplacement': {'attemptId': 'old'}}, {'pendingReplacement': {}},
    {'pendingReplacement': []}, {'pendingReplacement': 0},
    {'application': {'canInstall': False}},
])
def test_prepared_activation_conditions_are_independent(blockers, expected, change):
    state = prepared(blockers=blockers, **change)
    before = copy.deepcopy(state)
    assert health.activation(manager_for(state)) == (None if change else expected)
    assert state == before


@pytest.mark.parametrize('application', [{}, {'canInstall': None}, {'canInstall': True}])
def test_install_capability_must_be_explicitly_false_to_hold(application):
    assert health.activation(manager_for(prepared(application=application))) == 'starting'


@pytest.mark.parametrize('tools,scope,expected', [
    ([{'id': 'active'}], None, None),
    ([{'id': 'active'}], 'whole', None),
    ([{'id': 'active'}], 'app', 'starting'),
    ([{'runnerSource': 'owned'}], None, 'starting'),
    ([{'failure': {'status': 'failed'}}], None, 'starting'),
    ([{'runnerSource': 'owned'}, {'id': 'active'}], None, None),
])
def test_saved_tool_work_matches_tick_precedence(tools, scope, expected):
    state = prepared(pendingSmartTools=tools)
    if scope:
        state['applicationIntent'] = {'id': 'intent', 'scope': scope}
    assert health.activation(manager_for(state)) == expected


@pytest.mark.parametrize('intent', [{}, {'id': '', 'scope': 'app'}, {'id': 'intent', 'scope': 'other'}])
def test_invalid_saved_intent_does_not_claim_automatic_activation(intent):
    assert health.activation(manager_for(prepared(applicationIntent=intent))) is None


@pytest.mark.parametrize('intent', [None, {'id': 'intent', 'scope': 'app'}, {'id': 'intent', 'scope': 'whole'}])
def test_valid_saved_intent_allows_automatic_activation(intent):
    assert health.activation(manager_for(prepared(applicationIntent=intent))) == 'starting'


@pytest.mark.parametrize('phase', ['draining', 'replacing', 'releasing'])
def test_saved_drain_holds_do_not_claim_starting(phase):
    assert health.activation(manager_for(prepared(drain={'phase': phase}))) is None


def test_unready_saved_waiter_does_not_claim_starting():
    state = prepared(drain={'phase': 'waiting', 'workers': {'busy': {'ready': False}}})
    assert health.activation(manager_for(state)) is None
    state['drain']['workers']['busy']['ready'] = True
    assert health.activation(manager_for(state)) == 'starting'
    state['drain']['workers']['busy'].update(ready=False, suppressed=True)
    assert health.activation(manager_for(state)) == 'starting'


@pytest.mark.parametrize('attribute', ['closed', '_manual_handoff_commit_unknown'])
def test_manager_that_cannot_continue_does_not_claim_starting(attribute):
    assert health.activation(manager_for(prepared(), **{attribute: True})) is None


@pytest.mark.parametrize('change,replacing,expected', [
    ({}, 'attempt', 'installing'), ({}, None, None), ({}, 'other-attempt', None),
    ({'pendingReplacement': None}, 'attempt', None),
    ({'pendingReplacement': []}, 'attempt', None),
    ({'pendingReplacement': {}}, 'attempt', None),
    ({'pendingReplacement': {'unqualified': True}}, 'attempt', None),
    ({'phase': 'app-staged'}, 'attempt', None),
    ({'phase': 'error'}, 'attempt', None),
    ({'phase': 'interrupted'}, 'attempt', None),
    ({'error': 'failed'}, 'attempt', None),
    ({'pendingRestart': {'attemptId': 'restart'}}, 'attempt', None),
])
def test_installing_requires_exact_live_in_memory_attempt(executing, change, replacing, expected):
    state = {'phase': 'activating', 'pendingReplacement': {'attemptId': 'attempt'}, **change}
    manager = manager_for(state)
    if replacing is not None:
        executing(replacing)
    before = copy.deepcopy(state)
    assert health.activation(manager) == expected
    assert state == before


@pytest.mark.parametrize('executes', [None, 'another-attempt'])
def test_a_saved_installing_claim_without_this_process_executing_it_hides_nothing(executing, executes):
    """The field is a copy for readers. A restored state or a cancelled cleanup can leave it behind."""
    if executes:
        executing(executes)
    state = {'activation': 'installing', 'phase': 'activating', 'pendingApp': {'revision': 'a' * 40},
             'pendingReplacement': {'attemptId': 'attempt'}, 'forceRestartTarget': None}
    before = copy.deepcopy(state)
    assert health.browser_view(state) is state
    assert health.current(state)['status'] == 'unverified'
    assert health.activation(manager_for(state)) is None
    assert state == before


@pytest.mark.parametrize('identity', [None, 7, ['attempt'], {'attempt': 1}, ''])
def test_a_marker_identity_that_is_not_text_is_never_live(executing, identity):
    executing('attempt')
    state = {'activation': 'installing', 'phase': 'activating', 'pendingReplacement': {'attemptId': identity}}
    assert health.browser_view(state) is state
    assert health.current(state)['status'] == 'unverified'


@pytest.mark.parametrize('detail', [None, 'Installing the app update and restarting…'])
def test_installing_health_precedes_the_unknown_marker(executing, detail):
    executing('attempt')
    state = {'activation': 'installing', 'phase': 'activating',
             'pendingReplacement': {'attemptId': 'attempt'}, 'detail': detail}
    assert health.current(state) == {
        'status': 'working', 'title': 'Updating Amplifier',
        'detail': detail or 'Installing the app update and restarting…',
        'actionRequired': False, 'action': None, 'component': None,
    }


def test_starting_health_and_blocker_attention_order():
    state = prepared(activation='starting')
    result = health.current(state)
    assert (result['status'], result['title'], result['detail']) == ('working', 'Updating Amplifier', STARTING)
    state['blockers'] = [{'kind': 'checkout-handoff'}]
    assert health.current(state)['title'] == 'Update needs attention'


@pytest.mark.parametrize('change', [{'phase': 'error'}, {'phase': 'interrupted'}, {'error': 'failed'},
                                    {'pendingReplacement': {'unqualified': True}}])
def test_a_stale_installing_claim_keeps_the_unknown_marker_health(executing, change):
    executing('attempt')
    state = {'activation': 'installing', 'phase': 'activating', 'pendingReplacement': {'attemptId': 'attempt'},
             **change}
    assert health.current(state)['status'] == 'unverified'


@pytest.mark.parametrize('change', [
    {'blockers': [WORK]}, {'application': {'canInstall': False}},
    {'applicationIntent': {}}, {'pendingSmartTools': [{'id': 'active'}]},
    {'drain': {'phase': 'replacing'}}, {'drain': {'phase': 'waiting', 'workers': {'busy': {'ready': False}}}},
])
def test_a_stale_starting_claim_does_not_say_that_the_update_starts(change):
    result = health.current(prepared(activation='starting', **change))
    assert (result['status'], result['title']) == ('waiting', 'Update ready; waiting for work')


@pytest.mark.parametrize('activation', [None, 'waiting', 'starting'])
def test_marker_not_live_installing_keeps_exact_unknown_health(activation):
    state = {'activation': activation, 'pendingReplacement': {'unqualified': True}}
    assert health.current(state) == {
        'status': 'unverified', 'title': 'Installation needs verification',
        'detail': 'The application replacement outcome is not confirmed. Work remains protected; inspect the update diagnostics before another installation.',
        'actionRequired': True, 'action': 'diagnostics', 'component': None,
    }


@pytest.mark.parametrize('status', ['check_failed', 'current', 'release_channel_needed'])
def test_connection_wait_distinguishes_failed_application_check(status):
    state = {'application': {'status': status}, 'items': [{'kind': 'bundle', 'status': 'update'}]}
    result = health.current(state, connection={'needed': True})
    assert (result['status'], result['title'], result['action']) == (
        'waiting', 'Components update after you set up an AI connection', 'connect')
    assert result['detail'] == (
        'The application release could not be checked. The component update waits for another reason. Amplifier checks new components together with an AI connection, and none is set up yet. Set up an AI connection in Settings. The component update can then start.'
        if status == 'check_failed' else
        'Nothing failed. Amplifier checks new components together with an AI connection, and none is set up yet. Set up an AI connection in Settings. The update can then start.')


@pytest.mark.parametrize('title', ['Update could not finish', 'Component source needs review',
                                  'Application updated; earlier component issue needs review'])
@pytest.mark.parametrize('needed', [True, False])
def test_component_failure_keeps_its_result_and_names_missing_connection(title, needed):
    if title.startswith('Application updated'):
        state, evidence = acknowledged_app_with_old_component_history()
    else:
        failure = {'kind': 'ecosystem', 'status': 'failed', 'phase': 'ecosystem-stage', 'at': 1}
        if title == 'Component source needs review':
            failure['reason'] = 'runtime-source-conflict'
        state, evidence = {'diagnostics': {'lastFailure': failure}}, {}
    before = copy.deepcopy(state)
    original = health.current(state, evidence)
    result = health.current(state, evidence, connection={'needed': needed})
    assert result['title'] == title
    assert result == {**original, 'detail': original['detail'] + (CONNECTION if needed else '')}
    assert state == before


@pytest.mark.parametrize('where', ['reason', 'probe'])
def test_a_record_that_names_the_connection_itself_is_not_told_twice(where):
    failure = {'kind': 'ecosystem', 'status': 'failed', 'phase': 'ecosystem-stage', 'at': 1}
    failure.update({'reason': 'ai-connection-needed'} if where == 'reason'
                   else {'probe': {'reason': 'ai-connection-needed'}})
    state = {'diagnostics': {'lastFailure': failure}}
    result = health.current(state, connection={'needed': True})
    assert result == health.current(state)
    assert result['detail'].startswith('This update needs an AI connection') and CONNECTION not in result['detail']


@pytest.mark.parametrize('reason', [None, 'runtime-source-conflict'])
def test_noncomponent_failure_does_not_get_missing_connection_suffix(reason):
    failure = {'kind': 'application', 'status': 'failed', 'at': 1, 'reason': reason}
    state = {'diagnostics': {'lastFailure': failure}}
    assert health.current(state, connection={'needed': True}) == health.current(state)


def live(activation):
    """The saved state that each live claim describes."""
    if activation == 'installing':
        return {'phase': 'activating', 'pendingApp': {'revision': 'a' * 40}, 'blockers': [],
                'pendingReplacement': {'attemptId': 'live'}, 'activation': 'installing'}
    return prepared(activation=activation)


@pytest.mark.parametrize('activation', ['starting', 'installing'])
def test_browser_view_is_shallow_and_does_not_change_saved_state(executing, activation):
    executing('live')
    state = live(activation)
    before = copy.deepcopy(state)
    view = health.browser_view(state)
    assert view is not state and view['phase'] == 'activating'
    assert 'pendingReplacement' not in view
    assert view['pendingApp'] is state['pendingApp']
    assert state == before


@pytest.mark.parametrize('activation', [None, 'waiting', 'failed'])
def test_other_browser_views_are_identical_objects_and_keep_real_failure_marker(activation):
    state = {'activation': activation, 'phase': 'error', 'pendingReplacement': {'attemptId': 'unknown'}}
    assert health.browser_view(state) is state
    assert 'pendingReplacement' in health.browser_view(state)


@pytest.mark.parametrize('activation,change', [
    ('installing', {'phase': 'error'}), ('installing', {'phase': 'interrupted'}),
    ('installing', {'phase': 'app-staged'}), ('installing', {'error': 'failed'}),
    ('installing', {'pendingRestart': {'attemptId': 'restart'}}),
    ('installing', {'pendingReplacement': None}), ('installing', {'pendingReplacement': {}}),
    ('installing', {'pendingReplacement': {'unqualified': True}}), ('installing', {'pendingReplacement': []}),
    ('starting', {'blockers': [WORK]}), ('starting', {'phase': 'error'}), ('starting', {'phase': 'activating'}),
    ('starting', {'error': 'failed'}), ('starting', {'pendingRestart': {'attemptId': 'restart'}}),
    ('starting', {'pendingReplacement': {'attemptId': 'unknown'}}), ('starting', {'pendingApp': None}),
    ('starting', {'application': {'canInstall': False}}),
    # Every saved condition that allowed the claim, and the exact attempt.
    ('installing', {'pendingReplacement': {'attemptId': 'another'}}),
    ('starting', {'applicationIntent': {}}), ('starting', {'pendingSmartTools': [{'id': 'active'}]}),
    ('starting', {'drain': {'phase': 'draining'}}), ('starting', {'drain': {'phase': 'replacing'}}),
    ('starting', {'drain': {'phase': 'releasing'}}),
    ('starting', {'drain': {'phase': 'waiting', 'workers': {'busy': {'ready': False}}}}),
])
def test_a_claim_that_the_state_no_longer_supports_changes_nothing(executing, activation, change):
    """Other owners publish `updates` without deriving `activation` again.

    A stale claim must never hide a marker, an error or a failed phase from the page.
    """
    executing('live')
    state = {**live(activation), **change}
    before = copy.deepcopy(state)
    assert health.browser_view(state) is state
    assert state == before


TARGET = {'hostInstanceId': 'host', 'revision': 'a' * 40, 'generation': 'b' * 32, 'attemptId': 'attempt'}


@pytest.mark.parametrize('activation', ['starting', 'installing'])
def test_the_page_gets_no_interrupt_offer_while_the_update_runs_by_itself(executing, activation):
    executing('live')
    state = {**live(activation), 'forceRestartTarget': dict(TARGET)}
    before = copy.deepcopy(state)
    assert health.browser_view(state)['forceRestartTarget'] is None
    assert state == before


@pytest.mark.parametrize('platform,offered', [('linux', True), ('darwin', True), ('win32', False)])
def test_the_page_gets_the_interrupt_offer_only_where_a_forced_restart_can_work(monkeypatch, platform, offered):
    """While work delays the update. Windows refuses every forced restart."""
    monkeypatch.setattr(health, 'sys', SimpleNamespace(platform=platform))
    state = prepared(activation='waiting', blockers=[WORK], forceRestartTarget=dict(TARGET))
    before = copy.deepcopy(state)
    view = health.browser_view(state)
    assert (view['forceRestartTarget'] == TARGET) is offered
    assert (view is state) is offered
    # Nothing else changes for the page, and the saved identity stays for task.waitForUpdate.
    assert {**view, 'forceRestartTarget': TARGET} == before and state == before


def test_windows_without_a_target_keeps_the_identical_object(monkeypatch):
    monkeypatch.setattr(health, 'sys', SimpleNamespace(platform='win32'))
    state = {'phase': 'checked', 'forceRestartTarget': None}
    assert health.browser_view(state) is state


async def windows_with_work(managed, monkeypatch):
    """A prepared app, a working chat, and every module that asks says Windows."""
    from unittest.mock import Mock
    from amplifier_web import update_restart
    service, manager = managed.service, managed.manager
    service._session()['status'] = 'working'
    await stage_replacement(managed, 'app')
    args = {**update_restart.target(manager), 'confirm': True}
    windows = SimpleNamespace(platform='win32')
    monkeypatch.setattr(update_restart, 'sys', windows)
    # Without the early refusal the request goes on into activation. The cases
    # then fail: the refusal text is another one or absent, and the state changes.
    monkeypatch.setattr(app_updates, 'sys', windows)
    owner = Mock(return_value=None)
    monkeypatch.setattr('amplifier_web.windows_service.update_owner', owner)
    return args, owner


def unchanged(manager, before, events):
    state = manager.service.state['updates']
    assert (state['phase'], state.get('error'), state['pendingApp']) == ('app-staged', None, before['pendingApp'])
    assert state['activation'] == 'waiting' and not state.get('pendingReplacement')
    assert len(manager.diagnostics.state.get('events', [])) == events
    assert state['currentStatus'] == before['currentStatus']


async def test_windows_refuses_a_forced_restart_before_it_changes_the_update(managed, monkeypatch):
    """A stale page can still send the request. The prepared update must stay as it is."""
    from amplifier_web import update_restart
    service, manager = managed.service, managed.manager
    args, owner = await windows_with_work(managed, monkeypatch)
    before = copy.deepcopy(service.state['updates'])
    events = len(manager.diagnostics.state.get('events', []))
    with pytest.raises(update_restart.RestartRejected, match='cannot interrupt work'):
        update_restart.require(manager, args)
    await manager.command('forceRestart', args, 'stale-page-request')
    state = service.state['updates']
    assert state['forceRestartResult'] == {'requestId': 'stale-page-request', 'status': 'rejected',
                                           'detail': update_restart.WINDOWS_REFUSAL}
    owner.assert_not_called()
    unchanged(manager, before, events)


async def test_windows_refuses_a_forced_restart_at_dispatch_before_it_is_recorded(managed, monkeypatch):
    from amplifier_web.errors import AppError
    service, manager = managed.service, managed.manager
    args, owner = await windows_with_work(managed, monkeypatch)
    before = copy.deepcopy(service.state['updates'])
    events = len(manager.diagnostics.state.get('events', []))
    with pytest.raises(AppError, match='cannot interrupt work') as refused:
        await service.dispatch('updates.forceRestart', args, 'ui', 'c' * 32)
    assert refused.value.status == 409
    assert 'forceRestartResult' not in service.state['updates']
    owner.assert_not_called()
    unchanged(manager, before, events)


@pytest.mark.parametrize('page', ['updates', 'ai-connections'])
@pytest.mark.parametrize('activation', ['starting', 'installing', None])
def test_browser_demand_uses_the_same_view_before_page_filter(executing, page, activation):
    executing('live')
    state = live(activation or 'installing')
    state['activation'] = activation
    before = copy.deepcopy(state)
    result = browser_demand.project({'updates': state, 'view': {'panel': 'settings', 'settingsExpanded': [page]}})
    assert state == before
    if activation:
        assert result['updates']['phase'] == 'activating'
        assert 'pendingReplacement' not in result['updates']
    else:
        assert result['updates']['pendingReplacement'] == state['pendingReplacement']
        assert result['updates']['phase'] == 'activating'


def record_publications(service, monkeypatch):
    records = []
    publish = service._publish_changes

    def observe(*args, **kwargs):
        result = publish(*args, **kwargs)
        state = service.state['updates']
        # Exercise the actual browser boundary even against pre-fix product code.
        view = browser_demand.project({'updates': state, 'view': {
            'panel': 'settings', 'settingsExpanded': ['updates']}})['updates']
        status = state.get('currentStatus', {})
        records.append((state.get('phase'), state.get('activation'), status.get('status'),
                        status.get('title'), view.get('forceRestartTarget') is None,
                        view.get('pendingReplacement') is not None, view.get('phase'), state.get('detail')))
        return result

    monkeypatch.setattr(service, '_publish_changes', observe)
    return records


async def test_real_app_stage_through_restart_never_claims_attention_or_waiting(managed, monkeypatch):
    service, manager = managed.service, managed.manager
    records = record_publications(service, monkeypatch)
    original = app_updates.process
    durable = []

    async def process(*args, **kwargs):
        if replacement_install(args, kwargs):
            marker = copy.deepcopy(service.state['updates']['pendingReplacement'])
            assert saved_updates(service)['pendingReplacement'] == marker
            durable.append((marker, getattr(manager, '_replacing', None), set(health._EXECUTING)))
        if app_updates.PROBE in args:
            return successful_probe()
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, 'process', process)
    await stage_replacement(managed, 'app')
    await app_updates.activate(manager)
    first = next(i for i, row in enumerate(records) if row[0] == 'app-staged')
    journey = records[first:]
    print('A13 successful publication tuples:', repr(journey))
    assert journey and durable
    assert all(row[2] not in {'unverified', 'action-required', 'protected'} for row in journey)
    assert all(row[4] and not row[5] and row[6] == 'activating' for row in journey)
    assert journey[0][1] == 'starting' and journey[0][7] == STARTING
    assert any(row[1] == 'installing' for row in journey)
    assert durable[0][1] == durable[0][0]['attemptId'] and durable[0][2] == {durable[0][1]}
    assert service.state['updates']['pendingRestart']
    assert not getattr(manager, '_replacing', None)
    managed.restart.assert_awaited_once_with(manager)


async def test_real_probe_failure_keeps_marker_visible_and_health_unverified(managed, monkeypatch):
    await fail_replacement(managed, monkeypatch, 'app', 'probe')
    state = managed.service.state['updates']
    assert state['phase'] == 'error'
    assert state.get('activation') is None
    assert health.browser_view(state) is state and state['pendingReplacement']
    assert state['currentStatus']['status'] == 'unverified'
    assert not getattr(managed.manager, '_replacing', None)


async def test_real_cancelled_replacement_keeps_marker_visible_and_health_unverified(managed, monkeypatch):
    await stage_replacement(managed, 'app')
    original = app_updates.process
    entered = asyncio.Event()

    async def process(*args, **kwargs):
        if replacement_install(args, kwargs):
            entered.set()
            await asyncio.Event().wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, 'process', process)
    task = asyncio.create_task(app_updates.activate(managed.manager))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = managed.service.state['updates']
    assert state['phase'] == 'interrupted' and state.get('activation') is None
    assert health.browser_view(state) is state and state['pendingReplacement']
    assert state['currentStatus']['status'] == 'unverified'
    assert not getattr(managed.manager, '_replacing', None)


async def test_failed_marker_publication_does_not_leave_a_live_claim(managed, monkeypatch):
    """The marker is in memory, its publication fails, and a tick returns early on a marker."""
    service, manager = managed.service, managed.manager
    await stage_replacement(managed, 'app')
    publish = service._publish_changes
    failed = []

    def fail_once(*args, **kwargs):
        if service.state['updates'].get('pendingReplacement') and not failed:
            failed.append(service.state['updates']['activation'])
            raise OSError('synthetic state write failure')
        return publish(*args, **kwargs)

    monkeypatch.setattr(service, '_publish_changes', fail_once)
    with pytest.raises(OSError):
        await app_updates.activate(manager)
    state = service.state['updates']
    assert failed == ['installing']
    assert state['pendingReplacement'] and state['phase'] == 'activating'
    assert not getattr(manager, '_replacing', None)
    assert state['activation'] is None
    assert state['currentStatus']['status'] == 'unverified'
    assert health.browser_view(state) is state
    # The corrected fields were also saved, not only kept in memory.
    assert saved_updates(service)['activation'] is None
    await manager.tick()
    assert state['activation'] is None and state['currentStatus']['status'] == 'unverified'


async def test_cancelled_cleanup_after_a_failed_marker_publication_keeps_the_marker_visible(managed, monkeypatch):
    """The owner ends, and the wait for the state lock in its cleanup is cancelled."""
    service, manager = managed.service, managed.manager
    await stage_replacement(managed, 'app')
    publish = service._publish_changes
    original = service.lock

    class Held:
        """A state lock that another owner holds for the whole case."""
        def __init__(self):
            self.wanted = asyncio.Event()

        async def __aenter__(self):
            self.wanted.set()
            await asyncio.Event().wait()

        async def __aexit__(self, *error):
            return False

        def locked(self):
            return True

    held = Held()
    claims = []

    def fail(*args, **kwargs):
        state = service.state['updates']
        if state.get('pendingReplacement') and not claims:
            claims.append((state['activation'], set(health._EXECUTING)))
            service.lock = held  # The activation leaves its own lock; the cleanup then meets this one.
            raise OSError('synthetic state write failure')
        return publish(*args, **kwargs)

    monkeypatch.setattr(service, '_publish_changes', fail)
    task = asyncio.create_task(app_updates.activate(manager))
    try:
        await asyncio.wait_for(held.wanted.wait(), 5)
        state = service.state['updates']
        attempt = state['pendingReplacement']['attemptId']
        assert claims == [('installing', {attempt})]
        # Already true while the cleanup still waits: nothing has to finish first.
        assert not health._EXECUTING and state['activation'] is None
        assert health.browser_view(state) is state
        assert state['currentStatus']['status'] == 'unverified'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        service.lock = original
    state = service.state['updates']
    assert state['pendingReplacement'] and state['phase'] == 'activating'
    assert not health._EXECUTING and not getattr(manager, '_replacing', None)
    assert state['activation'] is None and health.activation(manager) is None
    assert health.browser_view(state) is state
    assert state['currentStatus']['status'] == 'unverified'


async def test_an_earlier_pause_text_does_not_follow_the_update_into_its_installation(managed, monkeypatch):
    """A hold could not be confirmed once. The next pass confirms it and installs."""
    from amplifier_web import update_blockers
    service, manager = managed.service, managed.manager
    await stage_replacement(managed, 'app')
    retain = update_blockers.retain_operation_holds
    refusals = []

    def once(*args, **kwargs):
        if not refusals:
            refusals.append(True)
            raise RuntimeError('synthetic unconfirmed hold')
        return retain(*args, **kwargs)

    monkeypatch.setattr(update_blockers, 'retain_operation_holds', once)
    await app_updates.activate(manager)
    state = service.state['updates']
    assert state['phase'] == 'app-staged' and state['error'].startswith('Automatic restart is paused')
    assert state['currentStatus']['status'] == 'action-required' and not state.get('pendingReplacement')
    records = record_publications(service, monkeypatch)
    original = app_updates.process

    async def process(*args, **kwargs):
        return successful_probe() if app_updates.PROBE in args else await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, 'process', process)
    await app_updates.activate(manager)
    assert state['pendingRestart'] and not state.get('error')
    assert records and any(row[1] == 'installing' for row in records)
    assert all(row[2] not in {'unverified', 'action-required', 'protected'} for row in records)
    assert all(not row[5] for row in records)  # The page never got the marker.


async def test_the_owners_next_pass_installs_the_app_that_claims_starting(managed, monkeypatch):
    """The text says that Amplifier installs the update by itself. One pass of the owner does it."""
    import time
    service, manager = managed.service, managed.manager
    await stage_replacement(managed, 'app')
    state = service.state['updates']
    # Automatic installation controls staging, not the activation of this validated app.
    service.state['settings']['updates']['autoInstall'] = False
    now = time.time()
    state.update(pendingAppCheckAt=now, lastCheck=now)  # No check is due in this pass.
    manager._storage_last_pass = now                     # No storage maintenance is due.
    manager._update_health_initial = False
    await manager.publish()
    assert state['activation'] == 'starting' and state['detail'] == STARTING
    assert not state.get('pendingRestart')
    original = app_updates.process

    async def process(*args, **kwargs):
        return successful_probe() if app_updates.PROBE in args else await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, 'process', process)
    await manager.tick()
    assert state['pendingRestart'] and not state.get('pendingReplacement') and not state.get('error')
    managed.restart.assert_awaited_once_with(manager)


async def test_a_prepared_app_never_waits_for_an_earlier_restart_check(managed):
    """`starting` needs no rule for awaiting_restart(): a prepared state excludes each of its branches."""
    service, manager = managed.service, managed.manager
    await stage_replacement(managed, 'app')
    manager.readiness_task = asyncio.get_running_loop().create_future()  # A check that still runs.
    try:
        assert not manager.awaiting_restart()
        assert health.activation(manager) == 'starting'
    finally:
        manager.readiness_task.cancel()
        manager.readiness_task = None


@pytest.mark.parametrize('legacy', [False, True])
def test_successor_does_not_reuse_saved_installing_state(managed, legacy):
    state = managed.service.state['updates']
    marker = {'unqualified': True} if legacy else {'attemptId': 'earlier'}
    state.update(phase='activating', pendingReplacement=marker, activation='installing')
    successor = UpdateManager(managed.service)
    assert state['activation'] is None
    assert state['currentStatus']['status'] == 'unverified'
    assert health.browser_view(state) is state
    assert not getattr(successor, '_replacing', None)


@pytest.mark.parametrize('detail', [WAITING, STARTING, 'A different owner detail.'])
async def test_busy_prepared_app_waits_then_starts_without_interrupt_offer(managed, detail):
    service, manager = managed.service, managed.manager
    service._session()['status'] = 'working'
    await stage_replacement(managed, 'app')
    await manager.publish(detail=detail)
    state = service.state['updates']
    assert state['activation'] == 'waiting'
    assert state['detail'] == (detail if detail == 'A different owner detail.' else WAITING)
    assert state['forceRestartTarget'] and health.browser_view(state) is state
    service._session()['status'] = 'idle'
    # Auto-install controls staging, not activation of this qualified candidate.
    service.state['settings']['updates']['autoInstall'] = False
    await manager.publish()
    assert state['activation'] == 'starting'
    assert state['detail'] == (detail if detail == 'A different owner detail.' else STARTING)
    view = health.browser_view(state)
    assert view['phase'] == 'activating' and view['forceRestartTarget'] is None
    # The saved field is also the identity that task.waitForUpdate names: it stays.
    from amplifier_web.update_restart import target
    assert state['forceRestartTarget'] == target(manager) and state['forceRestartTarget']
    assert saved_updates(service)['phase'] == 'app-staged'


@pytest.mark.parametrize('when', ['prepare', 'final-admission'])
async def test_declined_activation_republishes_waiting_only_when_derived_state_changed(managed, monkeypatch, when):
    service, manager = managed.service, managed.manager
    await stage_replacement(managed, 'app')
    records = record_publications(service, monkeypatch)
    if when == 'prepare':
        service._session()['status'] = 'working'
    else:
        target = app_updates.installed_target

        async def work_started():
            service._session()['status'] = 'working'
            return await target()

        monkeypatch.setattr(app_updates, 'installed_target', work_started)
    await app_updates.activate(manager)
    state = service.state['updates']
    assert state['activation'] == 'waiting' and state['detail'] == WAITING
    assert state['forceRestartTarget'] and health.browser_view(state) is state
    assert records and not state.get('pendingReplacement')
    # A second refusal with the same observations must not emit redundant state.
    count = len(records)
    await app_updates.activate(manager)
    assert len(records) == count


def test_activation_projection_is_bounded_and_does_not_read_files(monkeypatch):
    def no_io(*args, **kwargs):
        pytest.fail('activation projection must not read files')

    monkeypatch.setattr(Path, 'read_text', no_io)
    monkeypatch.setattr(Path, 'read_bytes', no_io)
    state = prepared(diagnostics={'events': [object()] * 10000})
    for _ in range(100):
        assert health.activation(manager_for(state)) == 'starting'
        view = health.browser_view({**state, 'activation': 'starting'})
        assert view['diagnostics'] is state['diagnostics']
