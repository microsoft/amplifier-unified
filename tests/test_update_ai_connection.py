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


def recorded(failure=RECORDED, **changes):
    """That installation after 'Check for updates': everything current, the record kept."""
    current = {'status': 'current', 'available': 0, 'missing': 0, 'protected': 0, 'issues': 0}
    group = {'id': failure['id'], 'kind': 'ecosystem', 'first': failure, 'latest': failure, 'occurrences': 1}
    return {'phase': 'checked', 'error': None, 'available': 0, 'appAvailable': False,
            'sequence': {'stage': 'complete', 'install': False, 'included': current, 'other': current},
            'application': {'status': 'current'}, 'items': [],
            'diagnostics': {'events': [], 'lastFailure': failure, 'activeFailures': {'ecosystem:cause': group}},
            **changes}


@pytest.mark.parametrize('known', [NEEDED, CONNECTED])
def test_an_installation_that_holds_the_record_reaches_a_plain_state(known):
    state = recorded()
    before = copy.deepcopy(state)
    # Until this change the page concluded a failed update from the record.
    old = health.current(state, SETUP_RECORD)
    assert (old['title'], old['detail'], old['actionRequired']) == (COULD_NOT_FINISH, NO_OUTCOME, True)
    plain = health.current(state, SETUP_RECORD, connection=known)
    assert (plain['status'], plain['title'], plain['actionRequired']) == ('current', 'You\u2019re up to date', False)
    # The record stays in history: the projection changes nothing.
    assert state == before
    assert not [item for item in snapshot({'updates': {**state, 'currentStatus': plain}})['items']
                if item['id'] == 'updates:current']
    # With new components available and still no connection, the update waits.
    again = health.current(checked(diagnostics=state['diagnostics']), SETUP_RECORD, connection=NEEDED)
    assert again == WAITING


def changed(**changes):
    probe = {**RECORDED['probe'], **changes.pop('probe', {})}
    return {**RECORDED, **changes, 'probe': probe}


@pytest.mark.parametrize('failure', [
    changed(probe={'failureLocation': 'amplifier_web.host.session:live_plan:1021'}),
    changed(probe={'failureLocation': 'amplifier_web.host.config:substitute:216'}),
    changed(probe={'errorType': 'RuntimeError'}),
    changed(probe={'stage': 'capabilities'}),
    changed(probe={'reason': 'incomplete-conversation-profile'}),
    changed(probe={'reason': 'bundle-load-failed'}),
    changed(reason='disk-full'),
    changed(phase='ecosystem-prepare'),
    changed(phase='ecosystem-compatibility'),
    changed(status='interrupted'),
    changed(kind='application'),
], ids=['other function', 'missing value', 'other error', 'other probe stage', 'other profile gap', 'bundle load',
        'disk full', 'prepare phase', 'compatibility phase', 'interrupted', 'application'])
def test_every_other_failure_still_needs_attention(failure):
    result = health.current(recorded(failure), SETUP_RECORD, connection=NEEDED)
    assert result['actionRequired'] and result['title'] in {COULD_NOT_FINISH, 'Component source needs review'}
    # Also when new components are available: a real failure is not replaced by 'waits'.
    waiting = health.current(checked(diagnostics=recorded(failure)['diagnostics']), SETUP_RECORD, connection=NEEDED)
    assert waiting['actionRequired'] and waiting != WAITING


def test_the_record_is_a_failure_for_a_selection_that_can_declare_its_own_provider():
    for failure in (RECORDED, changed(probe={'reason': 'ai-connection-needed'})):
        result = health.current(recorded(failure), SETUP_RECORD, connection=OTHER_PROFILE)
        assert (result['title'], result['actionRequired']) == (COULD_NOT_FINISH, True)
    # The app's own profile with the named reason: nothing but the connection was missing.
    named = health.current(recorded(changed(probe={'reason': 'ai-connection-needed'})), SETUP_RECORD, connection=NEEDED)
    assert named['status'] == 'current'


def test_a_failed_update_is_reported_until_a_check_replaces_it():
    """The failed stage's own phase and text stay until the next check. That check clears them."""
    text = 'Ecosystem update failed during ecosystem source policy. Current sources remain active; no conversation work was replayed.'
    result = health.current(recorded(phase='error', error=text), SETUP_RECORD, connection=NEEDED)
    assert (result['title'], result['detail'], result['actionRequired']) == (COULD_NOT_FINISH, text, True)


def test_a_record_that_is_history_gives_no_text_to_another_error():
    named = changed(probe={'reason': 'ai-connection-needed'})
    assert failure_guidance(named).startswith('This update needs an AI connection')
    other = 'Update check failed. Your installed sources are unchanged.'
    result = health.current(recorded(named, phase='error', error=other), SETUP_RECORD, connection=CONNECTED)
    assert (result['title'], result['detail'], result['actionRequired']) == (COULD_NOT_FINISH, other, True)
    # For a selection that can declare its own provider the record is the failure, with its own text.
    own = health.current(recorded(named, phase='error', error=other), SETUP_RECORD, connection=OTHER_PROFILE)
    assert own['detail'] == failure_guidance(named)


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


def test_changed_settings_do_not_show_the_old_record_as_a_failure_until_the_refresh():
    """Between a settings change and the owner's refresh, a quiet page stays quiet."""
    def manager(known):
        return SimpleNamespace(service=SimpleNamespace(state={'updates': recorded(), 'configurationRevision': 2}),
                               _update_connection=known, _update_health_configuration_revision=1,
                               _active_update_evidence={'generation': 'a' * 32, 'verifiedAt': 2, 'startedAt': 1,
                                                        'packages': set(), 'components': set()})
    quiet = manager(NEEDED)
    health.publish(quiet)
    status = quiet.service.state['updates']['currentStatus']
    assert (status['title'], status['actionRequired']) == ('Checking current configuration', False)
    # With no fact from the earlier settings, the record keeps its priority.
    for known in (None, OTHER_PROFILE):
        unknown = manager(known)
        health.publish(unknown)
        assert unknown.service.state['updates']['currentStatus']['title'] == COULD_NOT_FINISH


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


async def test_an_installation_with_the_old_record_is_plain_after_its_next_check(manager, monkeypatch):
    """The state that v0.98.7 left: the failed stage's phase and text, its record and its retry witness."""
    monkeypatch.setattr(health, 'setup_evidence', lambda m: dict(SETUP_RECORD))  # The installed runtime is a fixture.
    text = 'Ecosystem update failed during ecosystem source policy. Current sources remain active; no conversation work was replayed.'
    state = manager.service.state['updates']
    diagnostics = manager.diagnostics.state
    diagnostics.update(attemptId=RECORDED['attemptId'], kind='ecosystem', revision=RECORDED['revision'],
                       lastFailure=copy.deepcopy(RECORDED),
                       batch={'attemptId': RECORDED['attemptId'], 'kind': 'ecosystem',
                              'retry': {'inputKey': 'recorded-inputs', 'mode': 'changed-inputs'}})
    manager.diagnostics._track_failure(copy.deepcopy(RECORDED))
    await manager.publish(phase='error', error=text)
    assert (state['currentStatus']['title'], state['currentStatus']['detail']) == (COULD_NOT_FINISH, text)
    history = copy.deepcopy(diagnostics['activeFailures'])
    assert len(history) == 1 and next(iter(history.values()))['displayError'] == text
    # Measured in that clean room: the check after the failed stage found every component current.
    manager.installed = 'b' * 40
    await manager.check()
    assert state['currentStatus']['status'] == 'current' and state['currentStatus']['title'] == 'You\u2019re up to date'
    assert not update_attention(manager.service.state)
    # History is kept as it was.
    assert diagnostics['lastFailure'] == RECORDED and diagnostics['activeFailures'] == history
    # A later component update still waits for a connection, and then runs.
    manager.installed = 'a' * 40
    await manager.check()
    await manager.tick()
    assert state['currentStatus'] == WAITING and manager.stages == []
    manager.save(providers(PROVIDER))
    await manager.tick()
    assert len(manager.stages) == 1


async def test_a_selection_with_its_own_providers_is_qualified_and_its_refusal_is_reported(manager):
    """No claim is made for another profile: its stage runs, and the probe decides."""
    from amplifier_web.profile_catalog import incomplete
    manager.save({'bundle': {'active': 'other', 'added': {'other': OTHER_SOURCE}}})
    manager.refusal = CommandFailure({'probe': probe_failure(incomplete({'missing': ['providers']}), 'prepare')})
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
