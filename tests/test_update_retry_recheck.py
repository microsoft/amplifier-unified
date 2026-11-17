"""Explicit failed-component retries refresh sources, without changing owner retries."""
import copy
import time
from unittest.mock import AsyncMock

import pytest

from amplifier_web import update_health as health, updates
from test_update_ai_connection import PROVIDER, manager, providers


RETRIED = ' Amplifier checked the sources again before this attempt.'
NOTHING = ' Amplifier checked the sources again. Nothing is left to install.'
NOT_STARTED = ' Amplifier checked the sources again. No new attempt started.'


async def failed_component(manager):
    manager.save(providers(PROVIDER))
    await manager.check()
    await manager.command('install', None, 'a' * 32)
    assert manager.service.state['updates']['phase'] == 'error'
    assert len(manager.stages) == 1
    return copy.deepcopy(manager.diagnostics.state['lastFailure'])


def check_spy(manager, monkeypatch):
    check = AsyncMock(wraps=manager.check)
    monkeypatch.setattr(manager, 'check', check)
    return check


async def test_failed_retry_refreshes_sources_and_binds_the_new_attempt(manager, monkeypatch):
    old_failure = await failed_component(manager)
    state = manager.service.state['updates']
    before = time.time()

    async def moved(*args, **kw):
        return 'c' * 40 + ' refs/heads/main'

    monkeypatch.setattr(updates, 'process', moved)
    validate = manager.validate
    seen = []

    async def observe(stage, revision):
        seen.append(manager.inventory[0]['latest'])
        await validate(stage, revision)

    monkeypatch.setattr(manager, 'validate', observe)
    check = check_spy(manager, monkeypatch)
    published = AsyncMock(wraps=manager.publish)
    monkeypatch.setattr(manager, 'publish', published)
    await manager.command('install', None, 'b' * 32)
    check.assert_awaited_once_with(fresh=True)
    assert seen == ['c' * 40] and len(manager.stages) == 2
    assert old_failure in manager.diagnostics.state['events']
    retry = state['retryCheck']
    assert before <= retry['at'] <= time.time()
    assert retry == {'at': retry['at'], 'attemptId': manager.diagnostics.state['batch']['attemptId'],
                     'result': 'retried'}
    assert retry['attemptId'] != old_failure['attemptId']
    assert state['applicationIntent'] == {'id': 'b' * 32, 'scope': 'whole'}
    assert state['currentStatus']['detail'].endswith(RETRIED)
    assert any(call.kwargs.get('retryCheck') == retry for call in published.await_args_list)


async def test_retry_record_survives_existing_settings_race_recovery(manager, monkeypatch):
    await failed_component(manager)
    check = manager.check
    calls = []

    async def check_then_change(**kwargs):
        await check(**kwargs)
        calls.append(kwargs)
        if len(calls) == 1:
            manager.save(providers(PROVIDER, {'module': 'provider-openai'}))

    monkeypatch.setattr(manager, 'check', check_then_change)
    await manager.command('install', None, 'b' * 32)
    # The existing bounded recovery owns the second check. It clears intent,
    # but this new component attempt still belongs to the explicit retry.
    assert calls == [{'fresh': True}, {'fresh': True, 'automatic': False}]
    assert len(manager.stages) == 2
    retry = manager.service.state['updates']['retryCheck']
    assert retry['result'] == 'retried'
    assert retry['attemptId'] == manager.diagnostics.state['batch']['attemptId']


async def test_retry_result_does_not_replace_a_newer_requests_record(manager, monkeypatch):
    await failed_component(manager)
    saved = {'at': 1, 'attemptId': 'newer', 'result': 'retried'}

    async def superseded(admitted):
        await manager.publish(applicationIntent={'id': 'c' * 32, 'scope': 'whole'}, retryCheck=saved)

    monkeypatch.setattr(manager, 'install_checked', superseded)
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'b' * 32)
    check.assert_awaited_once_with(fresh=True)
    assert manager.service.state['updates']['retryCheck'] == saved


async def test_first_install_uses_its_successful_check_without_retry_record(manager, monkeypatch):
    manager.save(providers(PROVIDER))
    await manager.check()
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'a' * 32)
    check.assert_not_awaited()
    assert len(manager.stages) == 1
    assert manager.service.state['updates'].get('retryCheck') is None


async def test_new_nonretry_request_clears_an_old_retry_record(manager, monkeypatch):
    await failed_component(manager)
    await manager.check()
    state = manager.service.state['updates']
    state['retryCheck'] = {'at': 1, 'attemptId': 'old', 'result': 'retried'}
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'b' * 32)
    check.assert_not_awaited()
    assert state['retryCheck'] is None and len(manager.stages) == 2


async def test_retry_recheck_finds_nothing_and_starts_no_stage(manager, monkeypatch):
    await failed_component(manager)
    manager.installed = 'b' * 40
    check = check_spy(manager, monkeypatch)
    install = AsyncMock(wraps=manager.install)
    monkeypatch.setattr(manager, 'install', install)
    await manager.command('install', None, 'b' * 32)
    check.assert_awaited_once_with(fresh=True)
    install.assert_not_awaited()
    state = manager.service.state['updates']
    assert len(manager.stages) == 1 and state['phase'] != 'error'
    assert state['retryCheck'] == {'at': state['retryCheck']['at'], 'attemptId': None,
                                   'result': 'nothing-to-install'}
    assert state['applicationIntent'] == {'id': 'b' * 32, 'scope': 'whole'}


async def test_retry_recheck_failure_keeps_the_checks_own_failure(manager, monkeypatch):
    await failed_component(manager)
    inventory = AsyncMock(side_effect=RuntimeError('fixture: inventory unavailable'))
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    check = check_spy(manager, monkeypatch)
    install = AsyncMock(wraps=manager.install)
    monkeypatch.setattr(manager, 'install', install)
    await manager.command('install', None, 'b' * 32)
    check.assert_awaited_once_with(fresh=True)
    install.assert_not_awaited()
    state = manager.service.state['updates']
    assert len(manager.stages) == 1
    assert (state['phase'], state['error']) == ('error', 'Update check failed. Your installed sources are unchanged.')
    assert state['retryCheck'] == {'at': state['retryCheck']['at'], 'attemptId': None,
                                   'result': 'check-failed'}
    assert state['applicationIntent'] == {'id': 'b' * 32, 'scope': 'whole'}


async def test_retry_recheck_interruption_cannot_use_saved_inventory(manager, monkeypatch):
    await failed_component(manager)

    async def interrupted(**kwargs):
        assert not manager.lock.locked()
        await manager.publish(phase='interrupted', error='Fixture interrupted check.')

    check = AsyncMock(side_effect=interrupted)
    monkeypatch.setattr(manager, 'check', check)
    install = AsyncMock(wraps=manager.install)
    monkeypatch.setattr(manager, 'install', install)
    await manager.command('install', None, 'b' * 32)
    check.assert_awaited_once_with(fresh=True)
    install.assert_not_awaited()
    state = manager.service.state['updates']
    assert state['phase'] == 'interrupted' and state['error'] == 'Fixture interrupted check.'
    assert state['retryCheck']['result'] == 'check-failed'
    assert state['retryCheck']['attemptId'] is None


@pytest.mark.parametrize('path', ['checked', 'direct', 'tick'])
async def test_automatic_paths_do_not_recheck_or_change_retry_record(manager, monkeypatch, path):
    await failed_component(manager)
    state = manager.service.state['updates']
    saved = {'at': 1, 'attemptId': 'earlier', 'result': 'retried'}
    state['retryCheck'] = copy.deepcopy(saved)
    check = check_spy(manager, monkeypatch)
    if path == 'checked':
        await manager.install_checked(lambda: True, automatic=True)
    elif path == 'direct':
        await manager.install(automatic=True)
    else:
        await manager.tick()
    check.assert_not_awaited()
    assert len(manager.stages) == 1 and state['retryCheck'] == saved


@pytest.mark.parametrize('fence', ['interrupted', 'inspect', 'batch-inspect'])
async def test_interrupted_and_inspect_retries_keep_existing_handling(manager, monkeypatch, fence):
    await failed_component(manager)
    if fence == 'interrupted':
        manager.service.state['updates']['phase'] = 'interrupted'
    elif fence == 'inspect':
        manager.diagnostics.state['componentPreparation']['retry']['mode'] = 'inspect'
    else:
        manager.diagnostics.state.pop('componentPreparation')
        manager.diagnostics.state['batch']['retry']['mode'] = 'inspect'
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'b' * 32)
    check.assert_not_awaited()
    assert manager.service.state['updates'].get('retryCheck') is None


@pytest.mark.parametrize('key', ['pendingApp', 'pendingRelease', 'pendingRollback',
                                 'pendingRestart', 'pendingReplacement', 'pendingSmartTools'])
async def test_pending_operations_are_not_component_retry_rechecks(manager, monkeypatch, key):
    await failed_component(manager)
    manager.service.state['updates'][key] = [{'failure': {}}] if key == 'pendingSmartTools' else {'id': 'pending'}
    # The pending domain still owns its normal install handling, not this test.
    monkeypatch.setattr(manager, 'install_checked', AsyncMock())
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'b' * 32)
    check.assert_not_awaited()


@pytest.mark.parametrize('change', ['no-preparation', 'other-attempt', 'application', 'not-failed'])
async def test_only_a_matching_retained_component_failure_rechecks(manager, monkeypatch, change):
    await failed_component(manager)
    diagnostics = manager.diagnostics.state
    if change == 'no-preparation':
        diagnostics.pop('componentPreparation')
        diagnostics['batch'] = {'kind': 'check'}
    else:
        failure = diagnostics['componentPreparation']['failure']
        if change == 'other-attempt':
            failure['attemptId'] = 'f' * 32
        elif change == 'application':
            failure['kind'] = 'application'
        else:
            failure['status'] = 'succeeded'
    monkeypatch.setattr(manager, 'install_checked', AsyncMock())
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'b' * 32)
    check.assert_not_awaited()


async def test_matching_legacy_batch_failure_also_rechecks(manager, monkeypatch):
    await failed_component(manager)
    manager.diagnostics.state.pop('componentPreparation')
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'b' * 32)
    check.assert_awaited_once_with(fresh=True)
    assert len(manager.stages) == 2


async def test_retry_still_waits_for_an_ai_connection_without_an_attempt(manager, monkeypatch):
    old = await failed_component(manager)
    manager.save({})
    check = check_spy(manager, monkeypatch)
    await manager.command('install', None, 'b' * 32)
    check.assert_awaited_once_with(fresh=True)
    assert len(manager.stages) == 1
    assert manager.diagnostics.state['lastFailure'] == old
    state = manager.service.state['updates']
    # The record does not say "retried": this request started no attempt.
    assert {key: state['retryCheck'][key] for key in ('attemptId', 'result')} == {
        'attemptId': None, 'result': 'not-started'}
    assert state['retryCheck']['at'] > old['at']
    # The earlier record keeps its priority, and the page says what the request did.
    assert state['currentStatus']['title'] == 'Update could not finish'
    assert NOT_STARTED in state['currentStatus']['detail']


@pytest.mark.parametrize('reason,status', [('runtime-source-changed', 'action-required'),
                                           ('runtime-resolution-mismatch', 'protected')])
@pytest.mark.parametrize('attempt,result,annotated', [('retry', 'retried', True),
                                                     ('another', 'retried', False),
                                                     ('retry', 'check-failed', False),
                                                     (None, 'retried', False)])
def test_current_failure_sentence_is_bound_to_the_selected_attempt(reason, status, attempt, result, annotated):
    failure = {'kind': 'ecosystem', 'status': 'failed', 'phase': 'ecosystem-validation',
               'attemptId': 'retry', 'at': 10, 'reason': reason}
    state = {'phase': 'error', 'error': 'Fixture failure.', 'diagnostics': {'lastFailure': failure},
             'retryCheck': {'at': 11, 'attemptId': attempt, 'result': result}}
    before = copy.deepcopy(state)
    baseline = health.current({**state, 'retryCheck': None})
    projected = health.current(state)
    assert projected == {**baseline, 'detail': baseline['detail'] + (RETRIED if annotated else '')}
    assert projected['status'] == status
    assert state == before


async def test_a_retry_that_starts_an_update_of_another_kind_does_not_say_not_started(manager, monkeypatch):
    """The new check can find an application release. The application then comes first."""
    await failed_component(manager)
    check_spy(manager, monkeypatch)

    async def application_first(admitted, **kw):
        manager.diagnostics.begin('application', 'c' * 40)

    monkeypatch.setattr(manager, 'install_checked', application_first)
    await manager.command('install', None, 'b' * 32)
    state = manager.service.state['updates']
    assert {key: state['retryCheck'][key] for key in ('attemptId', 'result')} == {
        'attemptId': None, 'result': 'other-update'}
    assert NOT_STARTED not in state['currentStatus']['detail'] and RETRIED not in state['currentStatus']['detail']


async def test_a_new_check_takes_back_the_refusal_text_of_a_rollback(manager):
    """A rollback that changed nothing leaves only its text. No pass of the owner tries a rollback again."""
    manager.save(providers(PROVIDER))
    state = manager.service.state['updates']
    state['error'] = health.STALE_STAGE_KEYS
    assert health.current(state)['detail'] == health.STALE_STAGE_KEYS
    await manager.check()
    assert state.get('error') is None and state['currentStatus']['detail'] != health.STALE_STAGE_KEYS


async def test_a_retry_that_leaves_no_attempt_record_does_not_say_that_another_update_started(manager, monkeypatch):
    await failed_component(manager)
    check_spy(manager, monkeypatch)

    async def nothing_started(admitted, **kw):
        manager.diagnostics.state.pop('batch', None)

    monkeypatch.setattr(manager, 'install_checked', nothing_started)
    await manager.command('install', None, 'b' * 32)
    state = manager.service.state['updates']
    assert {key: state['retryCheck'][key] for key in ('attemptId', 'result')} == {
        'attemptId': None, 'result': 'not-started'}


@pytest.mark.parametrize('result,at,sentence', [
    ('nothing-to-install', 11, NOTHING), ('not-started', 11, NOT_STARTED),
    # A record that is not newer than the failure says nothing about it.
    ('nothing-to-install', 10, ''), ('not-started', 9, ''), ('not-started', None, ''),
    ('check-failed', 11, ''), ('retried', 11, ''), ('other-update', 11, ''),
])
@pytest.mark.parametrize('reason,status', [('runtime-source-changed', 'action-required'),
                                           ('runtime-resolution-mismatch', 'protected')])
def test_an_earlier_failure_that_keeps_its_priority_says_what_the_later_request_did(reason, status, result, at, sentence):
    failure = {'kind': 'ecosystem', 'status': 'failed', 'phase': 'ecosystem-validation',
               'attemptId': 'earlier', 'at': 10, 'reason': reason}
    state = {'phase': 'checked', 'diagnostics': {'lastFailure': failure},
             'retryCheck': {'at': at, 'attemptId': None, 'result': result}}
    before = copy.deepcopy(state)
    baseline = health.current({**state, 'retryCheck': None})
    projected = health.current(state)
    assert projected == {**baseline, 'detail': baseline['detail'] + sentence}
    assert projected['status'] == status
    assert state == before


def test_a_failure_of_another_kind_gets_no_recheck_sentence():
    failure = {'kind': 'application', 'status': 'failed', 'attemptId': 'earlier', 'at': 10}
    state = {'phase': 'error', 'error': 'Fixture failure.', 'diagnostics': {'lastFailure': failure},
             'retryCheck': {'at': 11, 'attemptId': None, 'result': 'not-started'}}
    assert health.current(state) == health.current({**state, 'retryCheck': None})


@pytest.mark.parametrize('evidence', [{'setupRecord': True}, {'verifiedAt': 20}])
def test_current_nothing_to_install_sentence_preserves_quiet_projection(evidence):
    state = {'phase': 'checked', 'sequence': {'stage': 'complete'}, 'application': {'status': 'current'},
             'retryCheck': {'at': 11, 'attemptId': None, 'result': 'nothing-to-install'}}
    before = copy.deepcopy(state)
    baseline = health.current({**state, 'retryCheck': None}, evidence)
    projected = health.current(state, evidence)
    assert projected == {**baseline, 'detail': baseline['detail'] + NOTHING}
    assert projected['status'] == 'current'
    assert state == before
    # No evidence, working state and current failures keep their existing priority/text.
    for changes, proof in [({}, {}), ({'phase': 'checking'}, evidence),
                           ({'phase': 'error', 'error': 'Fixture failure.'}, evidence)]:
        changed = {**state, **changes}
        assert health.current(changed, proof) == health.current({**changed, 'retryCheck': None}, proof)
