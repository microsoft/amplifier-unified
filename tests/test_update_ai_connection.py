"""A component update waits for an AI connection. Waiting is not a failed attempt.

A component update qualifies the selected conversation profile, and a profile
with no provider cannot qualify. The update owner therefore starts no candidate
while the app's own profile has no connection, and it says so on the page.
"""
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from amplifier_foundation import Bundle

from amplifier_web import app_updates, session_files, update_health as health, update_plan, update_sequence, updates
from amplifier_web.attention import snapshot
from amplifier_web.host import config as host_config
from amplifier_web.profile_catalog import characteristics, connection
from amplifier_web.service import AppService
from amplifier_web.update_diagnostics import PROBE_PREFIX, CommandFailure, failure_guidance, probe_failure, probe_record
from amplifier_web.updates import UpdateManager

PROVIDER = {'module': 'provider-anthropic', 'config': {'default_model': 'fixture-model'}}
WAITING = {'status': 'waiting', 'title': 'Components update after you set up an AI connection',
           'detail': 'Nothing failed. Amplifier checks new components together with an AI connection, and none is '
                     'set up yet. Set up an AI connection in Settings. The update can then start.',
           'actionRequired': False, 'action': 'connect', 'component': None}
NEEDED = {'appProfile': True, 'needed': True}
CONNECTED = {'appProfile': True, 'needed': False}
OTHER_PROFILE = {'appProfile': False, 'needed': False}
OTHER_SOURCE = 'git+https://example.invalid/other@main'


def read(tmp_path, settings):
    for name in ('shared', 'home', 'workspace'):
        (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / 'shared/settings.yaml').write_text(yaml.safe_dump(settings))
    return host_config.read_config(tmp_path / 'workspace', home=tmp_path / 'home',
                                   shared_home=tmp_path / 'shared', global_only=True)


def providers(*rows, **settings):
    return {'config': {'providers': list(rows)}, **settings}


# The app's own profile: its providers are the person's connections only.
APP_PROFILE = {
    'new installation': ({}, True),
    'one connection': (providers(PROVIDER), False),
    'connection removed in Settings': (providers(PROVIDER, configurator={'disabled': {'providers': ['anthropic']}}), True),
    'connection disabled by its name': (providers(PROVIDER, overrides={'anthropic': {'enabled': False}}), True),
    'connection disabled by its module': (providers(PROVIDER, overrides={'provider-anthropic': {'enabled': False}}), True),
    'named connection kept': (providers({**PROVIDER, 'id': 'work'}, configurator={'disabled': {'providers': ['anthropic']}}), False),
    'named connection removed': (providers({**PROVIDER, 'id': 'work'}, configurator={'disabled': {'providers': ['work']}}), True),
    'one of two removed': (providers(PROVIDER, {**PROVIDER, 'id': 'second'},
                                     configurator={'disabled': {'providers': ['anthropic']}}), False),
    # Composition merges two rows of one identity into the later module, then drops by that module.
    # The settings reader merges rows of one `id` itself; it keeps two rows of one `instance_id`.
    'two rows of one instance name, the later module disabled': (providers(
        {**PROVIDER, 'instance_id': 'same'}, {'module': 'provider-openai', 'instance_id': 'same'},
        overrides={'provider-openai': {'enabled': False}}), True),
    'two rows of one instance name, the earlier module disabled': (providers(
        {**PROVIDER, 'instance_id': 'same'}, {'module': 'provider-openai', 'instance_id': 'same'},
        overrides={'provider-anthropic': {'enabled': False}}), False),
    'two rows of one name, the later module disabled': (providers(
        {**PROVIDER, 'id': 'same'}, {'module': 'provider-openai', 'id': 'same'},
        overrides={'provider-openai': {'enabled': False}}), True),
    'two rows of one name, the earlier module disabled': (providers(
        {**PROVIDER, 'id': 'same'}, {'module': 'provider-openai', 'id': 'same'},
        overrides={'provider-anthropic': {'enabled': False}}), False),
    'a default behavior switched off': ({'web_bundles': {'excluded': [next(iter(
        __import__('amplifier_web.builtin_behaviors', fromlist=['APPENDED_DEFAULTS']).APPENDED_DEFAULTS))]}}, True),
    'development starter default': ({'bundle': {'releaseDefault': 'amplifier-development'}}, True),
    'another app profile selected': ({'bundle': {'active': 'anchors'}}, True),
}


@pytest.mark.parametrize('case', APP_PROFILE)
def test_the_update_owner_and_the_probe_use_one_rule(tmp_path, case):
    """The decision agrees with what composition and the probe's completeness test conclude."""
    from amplifier_web.host.session import _apply_settings
    settings, needed = APP_PROFILE[case]
    config = read(tmp_path, settings)
    assert connection(config) == {'appProfile': True, 'needed': needed}
    # The app's own profiles declare an orchestrator and a context, and no provider.
    profile = Bundle(name='app-profile', session={'orchestrator': {'module': 'loop-streaming'},
                                                  'context': {'module': 'context-simple'}})
    facts = characteristics(_apply_settings(profile, config))
    assert facts['missing'] == (['providers'] if needed else [])
    assert facts['complete'] is not needed


@pytest.mark.parametrize('settings', [
    {'bundle': {'active': 'other', 'added': {'other': OTHER_SOURCE}}},
    {'bundle': {'added': {'work': OTHER_SOURCE}}},
    {'sources': {'bundles': {'foundation': OTHER_SOURCE}}},
    {'bundle': {'app': [OTHER_SOURCE + '#subdirectory=behaviors/other.yaml']}},
], ids=['other profile', 'app profile from another source', 'bundle source override', 'other behavior'])
def test_any_other_selection_is_left_to_the_probe(tmp_path, settings):
    """Such a selection can declare a provider itself. Only its composition can tell."""
    assert connection(read(tmp_path, settings)) == OTHER_PROFILE
    assert connection(read(tmp_path, providers(PROVIDER, **settings))) == OTHER_PROFILE


@pytest.mark.parametrize('session,reason', [
    ({'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}, 'ai-connection-needed'),
    ({'context': {'module': 'context-simple'}}, 'incomplete-conversation-profile'),
])
async def test_the_probe_names_why_a_selected_profile_is_refused(tmp_path, monkeypatch, session, reason):
    from amplifier_web.host import session as host_session
    monkeypatch.chdir(tmp_path)
    loaded = Bundle(name='selected', session=session)
    loaded.prepare = AsyncMock()
    config = SimpleNamespace(workspace=tmp_path, active_bundle='selected', registry_home=tmp_path / 'foundation',
                             module_sources={})
    monkeypatch.setattr(host_session, 'load_config', lambda *args, **kw: config)
    monkeypatch.setattr('amplifier_web.host.config.prepare_registry', lambda c: None)
    monkeypatch.setattr(host_session, 'load_root_bundle', AsyncMock(return_value=(None, loaded, 'selected')))
    with pytest.raises(ValueError, match='The selected conversation profile is incomplete: ') as refused:
        await host_session.prepare_dependencies(tmp_path, bundle='selected', profile_catalog={})
    assert loaded.prepare.await_count == 0
    facts = probe_failure(refused.value, 'prepare')
    assert facts['reason'] == reason
    # The stage keeps only this sanitized line of the probe's output.
    kept = probe_record(PROBE_PREFIX + json.dumps({**facts, 'stage': 'prepare'}))
    assert kept['reason'] == reason and kept['errorType'] == 'ValueError'
    assert kept['failureLocation'].startswith('amplifier_web.host.session:prepare_dependencies:')


def test_a_profile_with_another_fault_is_not_named_as_waiting_for_a_connection():
    from amplifier_web.profile_catalog import incomplete
    def reason(**facts):
        return incomplete(facts).diagnostic_facts['reason']
    assert reason(missing=['providers'], supportedLoop=True) == 'ai-connection-needed'
    assert reason(missing=['providers'], supportedLoop=False) == 'incomplete-conversation-profile'
    assert reason(missing=['context', 'providers'], supportedLoop=True) == 'incomplete-conversation-profile'
    assert reason(missing=[], supportedLoop=False) == 'incomplete-conversation-profile'


# ---- The page state: a pure projection.

APPLICATION = {'id': 'application', 'kind': 'app', 'status': 'current'}


def checked(**changes):
    """The Updates state of a new installation after a check found four missing included components."""
    tier = {'status': 'available', 'available': 4, 'missing': 4, 'protected': 0, 'issues': 0}
    waiting = {'status': 'waiting', 'available': 0, 'missing': 0, 'protected': 0, 'issues': 0}
    rows = [{'id': 'included:' + name, 'kind': 'included source', 'updateTier': 'included', 'status': 'update',
             'missing': True, 'current': None, 'latest': 'b' * 40}
            for name in ('imagegen', 'computer-use', 'context-intelligence', 'routing-matrix')]
    return {'phase': 'available', 'error': None, 'available': 4, 'appAvailable': False,
            'sequence': {'stage': 'included', 'install': False, 'included': tier, 'other': waiting},
            'application': {'status': 'current'}, 'items': rows + [APPLICATION],
            'diagnostics': {'events': []}, **changes}


def test_page_says_that_the_update_waits_for_a_connection():
    state = checked()
    before = copy.deepcopy(state)
    assert health.current(state, {}, connection=NEEDED) == WAITING
    assert state == before
    for known in (CONNECTED, OTHER_PROFILE, None):
        assert health.current(state, {}, connection=known)['title'] == 'Updates available'
    # A new application release is the next step; it does not wait for a connection.
    assert health.current(checked(appAvailable=True), {}, connection=NEEDED)['title'] == 'Updates available'
    # Neither does a Smart Tool update.
    tool = {'id': 'tool', 'kind': 'smart tool', 'updateTier': 'other', 'status': 'update'}
    other = {'status': 'available', 'available': 1, 'missing': 0, 'protected': 0, 'issues': 0}
    tools_only = checked(available=1, items=[tool, APPLICATION],
                         sequence={'stage': 'other', 'install': False, 'included': {'status': 'current'}, 'other': other})
    assert health.current(tools_only, {}, connection=NEEDED)['title'] == 'Updates available'
    # A check is in progress.
    assert health.current(checked(phase='checking'), {}, connection=NEEDED)['status'] == 'working'


# Windows Sandbox, v0.98.7, November 15: the record of the update that the app started by itself.
RECORDED = {'id': '9fc14bcae44f428f93fc8399421b7cb2', 'attemptId': 'b1766b6a409c4fe790cfab4cb2edcdaf',
            'revision': '81bc1ba1df914e61bf4c8801c70e884c', 'commandId': 'b39e55ca7e16463c9d3fb1332c91682e',
            'at': 1794786790.3972569, 'durationMs': 17199, 'exitCode': 1, 'stdoutBytes': 199, 'stderrBytes': 0,
            'phase': 'ecosystem-source-policy', 'kind': 'ecosystem', 'status': 'failed', 'errorType': 'CommandFailure',
            'probe': {'ok': False, 'stage': 'prepare', 'errorType': 'ValueError',
                      'failureLocation': 'amplifier_web.host.session:prepare_dependencies:1021',
                      'elapsedMs': 16757, 'profileIndex': 3}}
SETUP_RECORD = {'setupRecord': True, 'packageCount': 5}
COULD_NOT_FINISH = 'Update could not finish'
NO_OUTCOME = 'The prior attempt has no applicable verified outcome. Current installation is retained; inspect diagnostics before retrying.'
# The record keeps its own text. The missing connection is named after it, because a retry cannot start without one.
NAMES_CONNECTION = ' No AI connection is set up yet. A component update starts only after you set one up in Settings.'


def recorded(failure=RECORDED, **changes):
    """That installation after 'Check for updates': everything current, the record kept."""
    current = {'status': 'current', 'available': 0, 'missing': 0, 'protected': 0, 'issues': 0}
    group = {'id': failure['id'], 'kind': 'ecosystem', 'first': failure, 'latest': failure, 'occurrences': 1}
    return {'phase': 'checked', 'error': None, 'available': 0, 'appAvailable': False,
            'sequence': {'stage': 'complete', 'install': False, 'included': current, 'other': current},
            'application': {'status': 'current'}, 'items': [],
            'diagnostics': {'events': [], 'lastFailure': failure, 'activeFailures': {'ecosystem:cause': group}},
            **changes}


def changed(**changes):
    probe = {**RECORDED['probe'], **changes.pop('probe', {})}
    return {**RECORDED, **changes, 'probe': probe}


NAMED = changed(probe={'reason': 'ai-connection-needed'})
FAILURES = {
    'record of 0.98.7': RECORDED,
    'named reason': NAMED,
    'other profile gap': changed(probe={'reason': 'incomplete-conversation-profile'}),
    'bundle load': changed(probe={'reason': 'bundle-load-failed'}),
    'disk full': changed(reason='disk-full'),
    'compatibility phase': changed(phase='ecosystem-compatibility'),
    'interrupted': changed(status='interrupted'),
}
TODAY = {'no connection': NEEDED, 'connected': CONNECTED, 'other profile': OTHER_PROFILE, 'not read': None}


@pytest.mark.parametrize('today', TODAY)
@pytest.mark.parametrize('case', FAILURES)
def test_an_earlier_failure_record_is_not_reclassified(case, today):
    """A record cannot prove that only a connection was missing.

    The refusal that releases before this rule recorded named no reason and covered every
    incomplete selected profile. So no record becomes "waiting", whatever today's settings are.
    """
    state = recorded(FAILURES[case])
    before = copy.deepcopy(state)
    result = health.current(state, SETUP_RECORD, connection=TODAY[today])
    assert result['actionRequired'] and result['title'] == COULD_NOT_FINISH
    assert state == before
    # New components and no connection: the record keeps its priority over the waiting state.
    available = health.current(checked(diagnostics=state['diagnostics']), SETUP_RECORD, connection=NEEDED)
    assert available['actionRequired'] and available['title'] == COULD_NOT_FINISH


def test_the_record_of_0987_reads_as_the_clean_room_showed_it():
    result = health.current(recorded(), SETUP_RECORD, connection=NEEDED)
    assert (result['title'], result['detail'], result['actionRequired']) == (
        COULD_NOT_FINISH, NO_OUTCOME + NAMES_CONNECTION, True)
    # With a connection the record reads exactly as that clean room showed it.
    assert health.current(recorded(), SETUP_RECORD, connection=CONNECTED)['detail'] == NO_OUTCOME
    # A refusal that this release names carries its guidance.
    named = health.current(recorded(NAMED), SETUP_RECORD, connection=OTHER_PROFILE)
    assert named['detail'] == failure_guidance(NAMED)
    assert named['detail'].startswith('This update needs an AI connection')


def test_a_later_completed_component_update_makes_the_record_history():
    """The existing rule for an older preparation failure. This change adds no other way."""
    completed = {'generation': 'a' * 32, 'attemptId': 'c' * 32, 'configuration': 'fixture',
                 'packages': set(), 'components': set(),
                 'startedAt': RECORDED['at'] + 60, 'verifiedAt': RECORDED['at'] + 120}
    for failure in (RECORDED, NAMED):
        state = recorded(failure)
        before = copy.deepcopy(state)
        plain = health.current(state, completed, connection=CONNECTED)
        assert (plain['status'], plain['title'], plain['actionRequired']) == ('current', 'You\u2019re up to date', False)
        assert state == before
        # An update that completed before the record does not cover it.
        earlier = {**completed, 'startedAt': RECORDED['at'] - 120, 'verifiedAt': RECORDED['at'] - 60}
        assert health.current(state, earlier, connection=CONNECTED)['title'] == COULD_NOT_FINISH


def test_unreadable_settings_make_no_claim(tmp_path):
    """The gate then lets the update's own preparation report them, as before this rule."""
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'shared/settings.yaml').write_text('config: [')
    manager = SimpleNamespace(home=tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(host_config, 'amplifier_home', lambda: tmp_path / 'shared')
        patch.setattr(session_files, 'amplifier_home', lambda: tmp_path / 'shared')
        assert health.read_connection(manager) == {}
        (tmp_path / 'shared/settings.yaml').write_text('{}')
        assert health.read_connection(manager) == NEEDED


def test_page_uses_the_connection_fact_only_for_the_configuration_it_was_read_from():
    def manager(read_at):
        return SimpleNamespace(service=SimpleNamespace(state={'updates': checked(), 'configurationRevision': 2}),
                               _update_connection=NEEDED, _update_health_configuration_revision=read_at)
    current = manager(2)
    health.publish(current)
    assert current.service.state['updates']['currentStatus'] == WAITING
    stale = manager(1)
    health.publish(stale)
    assert stale.service.state['updates']['currentStatus']['title'] == 'Updates available'


# ---- The update owner: real check, install, tick and receipts. Remote reads and qualification are fixtures.

class Runtime:
    async def close(self):
        pass


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    shared = manager.home / 'shared'
    shared.mkdir()
    monkeypatch.setattr(host_config, 'amplifier_home', lambda: shared)
    monkeypatch.setattr(session_files, 'amplifier_home', lambda: shared)
    manager.save = lambda settings: host_config.write_private(shared / 'settings.yaml', yaml.safe_dump(settings))

    async def application():
        return {'id': 'application', 'kind': 'app', 'status': 'current'}

    manager.installed = 'a' * 40

    async def inventory(**kw):
        return [{'id': 'included', 'kind': 'runtime dependency', 'package': 'amplifier-foundation',
                 'updateTier': 'included', 'label': 'included', 'url': 'https://example.invalid/included',
                 'ref': 'main', 'current': manager.installed, 'status': 'not_checked', 'eligible': True,
                 'appSelection': update_sequence.app_selection(manager.home)['witness']}]

    async def remote(*args, **kw):
        return 'b' * 40 + ' refs/heads/main'

    async def build(*args):
        return {'mode': 'worker', 'reason': 'fixture', 'activation': 'per-worker'}

    manager.stages = []
    manager.refusal = TimeoutError('fixture stops after the stage was admitted')

    async def validate(stage, revision):
        manager.stages.append(revision)
        raise manager.refusal

    monkeypatch.setattr(app_updates, 'check', application)
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(updates, 'process', remote)
    monkeypatch.setattr(updates, 'foundation_home', lambda home: home / 'absent')
    monkeypatch.setattr(update_plan, 'build', build)
    monkeypatch.setattr(manager, 'validate', validate)
    # Keep unrelated periodic storage work out of this scheduler fixture.
    manager._storage_last_pass = time.time()
    manager._storage_policy_due = False
    yield manager
    await service.close()


def update_attention(state):
    """The update owner's own attention rows; a release's notices are not its subject."""
    return [item for item in snapshot(state)['items']
            if item['id'] == 'updates:current' or item['id'].startswith('source:')]


def no_attempt(manager):
    state = manager.service.state['updates']
    diagnostics = manager.diagnostics.state
    releases = manager.directory / 'releases'
    assert manager.stages == []
    assert not releases.exists() or not list(releases.iterdir())
    assert 'lastFailure' not in diagnostics and diagnostics['activeFailures'] == {}
    assert not [event for event in diagnostics['events'] if event['status'] in {'failed', 'interrupted'}]
    assert not [event for event in diagnostics['events'] if event['kind'] == 'ecosystem']
    assert 'retry' not in diagnostics.get('batch', {}) and 'componentPreparation' not in diagnostics
    assert state['phase'] == 'available' and state['available'] == 1 and state['error'] is None
    assert state['currentStatus'] == WAITING
    # Nothing for the person to repair, and no source row to act on.
    assert not update_attention(manager.service.state)


async def test_first_start_without_a_connection_starts_no_update(manager):
    """What the app does by itself: check, then admit an installation."""
    await manager.tick()
    no_attempt(manager)
    assert manager.service.state['updates']['sequence']['stage'] == 'included'
    # Waiting is quiet: later owner passes publish nothing and start nothing.
    revision = manager.service.state['revision']
    for _ in range(10):
        await manager.tick()
    assert manager.service.state['revision'] == revision
    no_attempt(manager)


@pytest.mark.parametrize('action', ['install', 'check'])
async def test_the_persons_own_request_without_a_connection_records_no_failure(manager, action):
    await manager.check()
    no_attempt(manager)
    for _ in range(2):
        await manager.command(action, None, 'c' * 32)
        no_attempt(manager)


async def test_update_starts_by_itself_after_the_first_connection(manager):
    await manager.tick()
    no_attempt(manager)
    manager.save(providers(PROVIDER))
    await manager.tick()
    # One stage, admitted from a new check of the changed settings.
    assert len(manager.stages) == 1
    assert [path.name for path in (manager.directory / 'releases').iterdir()] == manager.stages
    events = manager.diagnostics.state['events']
    assert not [event for event in events if event['phase'] == 'background-update']
    # The fixture's stage then fails. A stage that fails says so.
    failure = manager.diagnostics.state['lastFailure']
    assert (failure['phase'], failure['status'], failure['errorType']) == ('ecosystem-validation', 'failed', 'TimeoutError')
    state = manager.service.state['updates']
    assert state['phase'] == 'error' and state['currentStatus']['title'] == COULD_NOT_FINISH
    # The same connection removed again: the failed stage stays reported.
    manager.save({})
    await manager.check()
    assert manager.service.state['updates']['currentStatus']['title'] == COULD_NOT_FINISH


async def test_the_persons_update_after_the_first_connection_checks_again(manager):
    """With automatic installation off this is the person's ordinary path out of the waiting state."""
    manager.service.state['settings']['updates'].update(autoCheck=False, autoInstall=False)
    state = manager.service.state['updates']
    await manager.command('check', None, 'a' * 32)
    assert state['currentStatus'] == WAITING and manager.stages == []
    manager.save(providers(PROVIDER))
    # The owner installs nothing by itself: no saved intent, and automatic installation is off.
    for _ in range(3):
        await manager.tick()
    assert manager.stages == [] and state['sequence']['install'] is False
    await manager.command('install', None, 'b' * 32)
    # One stage, from a new check of the changed settings. No refusal is recorded for the request itself.
    assert len(manager.stages) == 1
    failed = [event for event in manager.diagnostics.state['events'] if event['status'] == 'failed']
    assert {event['phase'] for event in failed} == {'ecosystem-validation', 'ecosystem-stage'}
    assert manager.diagnostics.state['lastFailure']['errorType'] == 'TimeoutError'  # The fixture's stage.
    assert state['sequence']['install'] is True


@pytest.mark.parametrize('who', ['owner', 'person'])
async def test_a_second_settings_change_during_the_recheck_is_refused_once_and_starts_no_stage(manager, who):
    from amplifier_web.update_sequence import StaleAppSelection
    await manager.tick()
    no_attempt(manager)
    manager.save(providers(PROVIDER))
    checks = []
    check = manager.check

    async def check_then_change(**arguments):
        result = await check(**arguments)
        checks.append(arguments)
        manager.save(providers(PROVIDER, {'module': 'provider-openai'}))
        return result

    manager.check = check_then_change
    state = manager.service.state['updates']
    if who == 'owner':
        # The owner's loop records this as a failed background update, as before this change.
        with pytest.raises(StaleAppSelection):
            await manager.tick()
        assert checks == [{'fresh': False, 'automatic': True}]
    else:
        await manager.command('install', None, 'c' * 32)
        assert checks == [{'fresh': True, 'automatic': False}]
        assert state['phase'] == 'error' and state['error'].startswith('Update failed during ')
        assert state['currentStatus']['title'] == COULD_NOT_FINISH
    assert manager.stages == []


@pytest.mark.parametrize('who', ['owner', 'person'])
async def test_a_recheck_that_fails_installs_nothing(manager, monkeypatch, who):
    await manager.tick()
    no_attempt(manager)
    manager.save(providers(PROVIDER))

    async def unreadable(**arguments):
        raise RuntimeError('fixture: the inventory cannot be read')

    monkeypatch.setattr(manager, 'inventory_sources', unreadable)
    installs = []
    install = manager.install

    async def counted(**arguments):
        installs.append(arguments)
        return await install(**arguments)

    monkeypatch.setattr(manager, 'install', counted)
    if who == 'owner':
        await manager.tick()
    else:
        await manager.command('install', None, 'c' * 32)
    state = manager.service.state['updates']
    assert manager.stages == []
    assert (state['phase'], state['error']) == ('error', 'Update check failed. Your installed sources are unchanged.')
    # The one installation request that was refused; nothing is requested again after the failed check.
    assert installs == ([{'automatic': True}] if who == 'owner' else [{}])


async def test_the_persons_update_whose_new_check_finds_nothing_installs_nothing(manager, monkeypatch):
    manager.service.state['settings']['updates'].update(autoCheck=False, autoInstall=False)
    state = manager.service.state['updates']
    await manager.command('check', None, 'a' * 32)
    assert state['currentStatus'] == WAITING
    manager.save(providers(PROVIDER))
    manager.installed = 'b' * 40  # The new check finds nothing to install.
    installs = []
    install = manager.install

    async def counted(**arguments):
        installs.append(arguments)
        return await install(**arguments)

    monkeypatch.setattr(manager, 'install', counted)
    await manager.command('install', None, 'b' * 32)
    assert manager.stages == [] and installs == [{}]
    assert (state['phase'], state['available'], state['error']) == ('checked', 0, None)
    assert state['detail'] == 'Component check complete.'
    assert not [event for event in manager.diagnostics.state['events'] if event['status'] == 'failed']


async def test_after_the_first_connection_only_a_newly_checked_selection_is_installed(manager):
    await manager.tick()
    no_attempt(manager)
    manager.save(providers(PROVIDER))
    manager.installed = 'b' * 40  # The new check finds nothing to install.
    await manager.tick()
    assert manager.stages == []
    state = manager.service.state['updates']
    assert (state['phase'], state['available'], state['error']) == ('checked', 0, None)
    # The owner's own check was the last word: no request to check before installing.
    assert state['detail'] == 'Component check complete.'
    assert not [event for event in manager.diagnostics.state['events'] if event['status'] == 'failed']


async def test_an_installation_with_the_old_record_keeps_it_and_updates_after_a_connection(manager, monkeypatch):
    """The state that v0.98.7 left: its record and its retry witness. The record is not reclassified."""
    monkeypatch.setattr(health, 'setup_evidence', lambda m: dict(SETUP_RECORD))  # The installed runtime is a fixture.
    state = manager.service.state['updates']
    diagnostics = manager.diagnostics.state
    diagnostics.update(attemptId=RECORDED['attemptId'], kind='ecosystem', revision=RECORDED['revision'],
                       lastFailure=copy.deepcopy(RECORDED),
                       batch={'attemptId': RECORDED['attemptId'], 'kind': 'ecosystem',
                              'retry': {'inputKey': 'recorded-inputs', 'mode': 'changed-inputs'}})
    manager.diagnostics._track_failure(copy.deepcopy(RECORDED))
    history = copy.deepcopy(diagnostics['activeFailures'])
    assert len(history) == 1
    # Measured in that clean room: the check after the failed stage found every component current,
    # and the page then said this.
    manager.installed = 'b' * 40
    await manager.check()
    assert (state['currentStatus']['title'], state['currentStatus']['detail']) == (
        COULD_NOT_FINISH, NO_OUTCOME + NAMES_CONNECTION)
    assert [item['id'] for item in update_attention(manager.service.state)] == ['updates:current']
    # New components and still no connection: no attempt starts, and the record keeps its priority.
    manager.installed = 'a' * 40
    await manager.check()
    await manager.tick()
    assert manager.stages == [] and state['currentStatus']['title'] == COULD_NOT_FINISH
    # After the first connection the update starts by itself: the saved witness of the old inputs does not hold it.
    manager.save(providers(PROVIDER))
    await manager.tick()
    assert len(manager.stages) == 1
    # The old record is kept as it was. (The fixture's stage then failed and added its own record.)
    assert all(diagnostics['activeFailures'][key] == group for key, group in history.items())
    assert RECORDED in [group['latest'] for group in diagnostics['activeFailures'].values()]


async def test_a_selection_with_its_own_providers_is_qualified_and_its_refusal_is_reported(manager):
    """No claim is made for another profile: its stage runs, and the probe decides."""
    from amplifier_web.profile_catalog import incomplete
    manager.save({'bundle': {'active': 'other', 'added': {'other': OTHER_SOURCE}}})
    manager.refusal = CommandFailure({'probe': probe_failure(incomplete({'missing': ['providers'], 'supportedLoop': True}), 'prepare')})
    await manager.tick()
    assert len(manager.stages) == 1
    failure = manager.diagnostics.state['lastFailure']
    assert failure['status'] == 'failed' and failure['probe']['reason'] == 'ai-connection-needed'
    state = manager.service.state['updates']
    guidance = failure_guidance(failure)
    assert guidance == ('This update needs an AI connection, and the selected conversation profile has none. '
                        'Nothing was changed. Set up an AI connection in Settings, then update again.')
    assert state['phase'] == 'error' and state['error'] == guidance
    assert (state['currentStatus']['title'], state['currentStatus']['detail']) == (COULD_NOT_FINISH, guidance)
    # Unchanged inputs are not tried again by the owner; the record stays a failure after a check.
    await manager.check()
    await manager.tick()
    assert len(manager.stages) == 1
    assert manager.service.state['updates']['currentStatus']['title'] == COULD_NOT_FINISH
    # Also after the person selects the app's own profile: today's settings do not reclassify that attempt.
    manager.save({})
    await manager.check()
    assert manager.service.state['updates']['currentStatus']['title'] == COULD_NOT_FINISH
