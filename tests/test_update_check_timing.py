"""When the update owner checks by itself: the timed check, and the check at a host start.

The timed check keeps its interval (4 hours unless a person chose another).
A host start adds one check when the last check or attempt was at least 1 hour
old at that moment. A PC that restarts therefore does not wait the rest of an
interval for a release that came out meanwhile.

The real update owner runs here: its tick, its check and its installation
admission. A host start is a new update owner on the state that the last one
saved. The clock of the update owner moves; no saved time is rewritten.
"""
import time

import pytest

from amplifier_web import app_updates, update_checks, updates
from amplifier_web.updates import (DEFAULT_CHECK_INTERVAL_HOURS, HOST_START_CHECK_AFTER_HOURS, UpdateManager,
                                   work_paused)
from test_pending_app_updates import waiting  # noqa: F401  (fixture)
from test_update_ai_connection import PROVIDER, manager, providers  # noqa: F401  (fixture)

HOUR = 3600
START_LIMIT = HOST_START_CHECK_AFTER_HOURS * HOUR
INTERVAL = DEFAULT_CHECK_INTERVAL_HOURS * HOUR
INSTALLED, MOVED = 'b' * 40, 'a' * 40  # The fixture's remote branch is at 'b' * 40.
APP_ONLY = {'id': 'c' * 32, 'scope': 'app'}  # What "update the application only" saves (UpdateManager.command).


@pytest.fixture
def clock(monkeypatch):
    """Move the update owner's clock: forward, or back as a corrected clock does."""
    real, offset = time.time, [0]
    monkeypatch.setattr(updates.time, 'time', lambda: real() + offset[0])

    def later(seconds):
        offset[0] += seconds
    return later


@pytest.fixture
def release_checks(manager, monkeypatch):
    """Each look for a published release, as the application check makes it."""
    calls = []

    async def check():
        calls.append(time.time())
        return {'id': 'application', 'kind': 'app', 'status': 'current'}
    monkeypatch.setattr(app_updates, 'check', check)
    return calls


async def nothing_newer(manager):
    """A host with an AI connection that has just checked and found nothing newer."""
    manager.save(providers(PROVIDER))
    manager.installed = INSTALLED
    await manager.check()
    state = manager.service.state['updates']
    assert (state['phase'], state['available'], state['sequence']['stage']) == ('checked', 0, 'complete')
    return state


async def host_start(manager):
    """The old update owner stops. A new one reads the state that it saved."""
    await manager.close()
    new = UpdateManager(manager.service)
    manager.service.update_manager = new
    for name in ('inventory_sources', 'validate'):  # The fixture's inventory and its stage recorder.
        setattr(new, name, getattr(manager, name))
    new._storage_last_pass, new._storage_policy_due = time.time(), False
    return new


def requests(owner):
    """The checks that the owner asks for, with their options."""
    seen, real = [], owner.check

    async def check(**options):
        seen.append(options)
        return await real(**options)
    owner.check = check
    return seen


@pytest.mark.parametrize('age', [START_LIMIT, START_LIMIT + 60, 2 * HOUR, INTERVAL - 60, INTERVAL + 60, 3 * INTERVAL])
async def test_a_host_start_checks_once_when_the_last_check_was_older_than_the_limit(manager, clock, release_checks, age):
    """Also after more than the interval: the check at a start is a start check, and it asks again."""
    await nothing_newer(manager)
    checks = len(release_checks)
    clock(age)
    new = await host_start(manager)
    asked = requests(new)
    await new.tick()
    assert len(release_checks) == checks + 1
    # It asks again: the answers saved for the timed interval are not reused.
    assert asked == [{'fresh': True, 'automatic': True}]
    state = manager.service.state['updates']
    assert state['lastCheck'] >= new._host_started and state['phase'] == 'checked'
    for _ in range(3):
        await new.tick()
    assert len(release_checks) == checks + 1 and len(asked) == 1


@pytest.mark.parametrize('age', [60, START_LIMIT - 60])
async def test_a_host_start_with_a_younger_last_check_starts_none(manager, clock, release_checks, age):
    await nothing_newer(manager)
    checks = len(release_checks)
    clock(age)
    new = await host_start(manager)
    for _ in range(3):
        await new.tick()
    assert len(release_checks) == checks
    # The start does not shorten the interval: the host later checks on time, as before.
    clock(INTERVAL - age - 60)
    await new.tick()
    assert len(release_checks) == checks
    asked = requests(new)
    clock(120)
    await new.tick()
    assert len(release_checks) == checks + 1 and asked == [{'fresh': False, 'automatic': True}]


async def test_a_running_host_keeps_its_timed_interval(manager, clock, release_checks):
    await nothing_newer(manager)
    checks = len(release_checks)
    asked = requests(manager)
    clock(INTERVAL - 60)
    await manager.tick()
    assert len(release_checks) == checks and asked == []
    clock(120)
    await manager.tick()
    assert len(release_checks) == checks + 1 and asked == [{'fresh': False, 'automatic': True}]


async def test_a_host_start_checks_nothing_when_automatic_checks_are_off(manager, clock, release_checks):
    await nothing_newer(manager)
    manager.service.state['settings']['updates']['autoCheck'] = False
    checks = len(release_checks)
    clock(2 * HOUR)
    new = await host_start(manager)
    for _ in range(3):
        await new.tick()
    assert len(release_checks) == checks


async def test_a_persons_own_check_after_the_start_is_the_check(manager, clock, release_checks):
    await nothing_newer(manager)
    clock(2 * HOUR)
    new = await host_start(manager)
    await new.check()
    checks = len(release_checks)
    for _ in range(3):
        await new.tick()
    assert len(release_checks) == checks


async def test_a_clock_that_is_set_back_brings_no_second_start_check(manager, clock, release_checks):
    """A PC whose clock was hours ahead when the host started, and is corrected after that."""
    await nothing_newer(manager)
    clock(3 * HOUR)
    new = await host_start(manager)
    clock(-2 * HOUR)  # Corrected. The last check is 1 hour old now, and the start is 2 hours ahead.
    checks = len(release_checks)
    asked = requests(new)
    for _ in range(4):
        await new.tick()
    # The one check ended the start reason. The saved times alone would name it again on every pass.
    assert asked == [{'fresh': True, 'automatic': True}] and len(release_checks) == checks + 1
    assert new._host_started - manager.service.state['updates']['lastCheck'] >= START_LIMIT
    assert new.check_due() is None


async def test_a_check_that_is_refused_does_not_end_the_start_reason(manager, clock, release_checks):
    state = await nothing_newer(manager)
    clock(2 * HOUR)
    new = await host_start(manager)
    checks = len(release_checks)
    state['pendingRelease'] = 'f' * 32  # A prepared component update waits for its activation: no check is admitted.
    await new.check()
    assert len(release_checks) == checks and new.check_due() == 'start'
    state['pendingRelease'] = None
    asked = requests(new)
    for _ in range(3):
        await new.tick()
    assert asked == [{'fresh': True, 'automatic': True}] and len(release_checks) == checks + 1


async def test_a_start_check_runs_where_a_person_asked_for_the_application_alone(manager, clock, release_checks):
    """The saved intent of an earlier 'update the application only' request. Nothing is staged."""
    state = await nothing_newer(manager)
    clock(2 * HOUR)
    new = await host_start(manager)
    state['applicationIntent'] = dict(APP_ONLY)
    checks = len(release_checks)
    asked = requests(new)
    for _ in range(3):
        await new.tick()
    assert asked == [{'fresh': True, 'automatic': True}] and len(release_checks) == checks + 1


async def test_a_start_check_while_a_component_preparation_is_unresolved_checks_the_application_only(
        manager, clock, release_checks):
    """An interrupted preparation waits for a person. The start check looks for a release and replays nothing."""
    state = await nothing_newer(manager)
    manager.installed = MOVED  # A component has a newer revision. It must not be prepared here.
    clock(2 * HOUR)
    new = await host_start(manager)
    new.diagnostics.state['componentPreparation'] = {
        'kind': 'ecosystem', 'attemptId': 'a' * 32, 'revision': 'b' * 32,
        'retry': {'mode': 'inspect', 'inputKey': 'retained-configuration'},
        'preparation': {'candidate': 'b' * 32, 'boundary': 'copy-only', 'copySettled': False},
        'terminal': {'phase': 'ecosystem-stage', 'status': 'interrupted', 'attemptId': 'a' * 32}}
    checks = len(release_checks)
    asked = requests(new)
    for _ in range(3):
        await new.tick()
    assert asked == [{'tier': 'application', 'fresh': True, 'automatic': True}]
    assert len(release_checks) == checks + 1 and manager.stages == []
    assert state['lastAttempt'] >= new._host_started


@pytest.mark.parametrize('keep_up_to_date', [True, False])
async def test_a_start_check_finds_a_release_that_came_out_meanwhile(manager, clock, monkeypatch, keep_up_to_date):
    """The restart of journey 2: a newer release existed, and the last check was 66 minutes old."""
    published = {'status': 'current'}

    async def check():
        async def lookup():
            return dict(published)
        return {'id': 'application', 'kind': 'app', **await update_checks.cached(['fixture-release'], lookup)}
    monkeypatch.setattr(app_updates, 'check', check)
    state = await nothing_newer(manager)  # This check saved the answer "current" for the interval.
    published.update(status='update', latest='v99.0.0', revision='c' * 40, canInstall=True)
    clock(66 * 60)
    new = await host_start(manager)
    started = []

    async def app():
        started.append('application update')
    new.app = app
    manager.service.state['settings']['updates']['autoInstall'] = keep_up_to_date
    await new.tick()
    assert state['appAvailable'] is True and state['application']['latest'] == 'v99.0.0'
    assert (state['phase'], state['available']) == ('available', 1)
    # With "Keep Amplifier up to date" on, the update starts with no click.
    assert started == (['application update'] if keep_up_to_date else [])


async def test_a_start_check_starts_a_component_update_and_retries_no_failed_one(manager, clock, release_checks):
    await nothing_newer(manager)
    manager.installed = MOVED  # A component's branch has a newer revision now.
    clock(2 * HOUR)
    new = await host_start(manager)
    await new.tick()
    assert len(manager.stages) == 1  # One component stage, with no click.
    # The fixture's stage fails. The next host start checks, and prepares the same inputs no second time.
    failure = new.diagnostics.state['lastFailure']
    assert (failure['phase'], failure['status']) == ('ecosystem-validation', 'failed')
    clock(2 * HOUR)
    again = await host_start(new)
    checks = len(release_checks)
    for _ in range(3):
        await again.tick()
    assert len(release_checks) == checks + 1
    assert len(manager.stages) == 1
    assert len(list((again.directory / 'releases').iterdir())) == 1


@pytest.mark.parametrize('intent', [None, APP_ONLY])
async def test_a_start_check_does_not_interrupt_running_work(waiting, intent):
    """A staged application update, a chat that works, and a last check that is 2 hours old."""
    w = waiting
    w.service.state['settings']['updates']['intervalHours'] = DEFAULT_CHECK_INTERVAL_HOURS
    if intent:  # The person asked for the application alone, as the 'app' action saves it.
        w.state.update(applicationIntent=dict(intent), pendingApp={**w.state['pendingApp'], 'updateIntent': dict(intent)})
    assert time.time() - w.state['lastCheck'] < INTERVAL and w.manager.check_due() == 'start'  # No timed check is due.
    await w.manager.tick()
    assert w.checks == 1 and w.state['pendingApp']['revision'] == 'b' * 40  # It found the newest release.
    assert not w.promotions and not w.restarts and not work_paused(w.service.state)
    assert w.service._session()['status'] == 'working'
    w.service._session()['status'] = 'idle'
    await w.manager.tick()
    assert [restart['version'] for restart in w.restarts] == ['99.0.1']
