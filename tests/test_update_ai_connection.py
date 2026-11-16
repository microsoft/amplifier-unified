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


def recorded(failure=RECORDED, *, proven=True, **changes):
    """That installation after 'Check for updates': everything current, the record kept.

    `proven`: the attempt was proven, from its own copy of the settings, to have run with
    the app's own profile and no AI connection (see the tests of that proof below).
    """
    current = {'status': 'current', 'available': 0, 'missing': 0, 'protected': 0, 'issues': 0}
    group = {'id': failure['id'], 'kind': 'ecosystem', 'first': failure, 'latest': failure, 'occurrences': 1}
    return {'phase': 'checked', 'error': None, 'available': 0, 'appAvailable': False,
            'sequence': {'stage': 'complete', 'install': False, 'included': current, 'other': current},
            'application': {'status': 'current'}, 'items': [],
            'diagnostics': {'events': [], 'lastFailure': failure, 'activeFailures': {'ecosystem:cause': group},
                            **({'awaitedConnection': [failure['attemptId']]} if proven else {})},
            **changes}


# Today's settings are not evidence about an earlier attempt: no connection fact changes these results.
@pytest.mark.parametrize('today', [NEEDED, CONNECTED, OTHER_PROFILE, None])
def test_an_installation_that_holds_the_record_reaches_a_plain_state(today):
    # Until this change, and still without a proof, the page concludes a failed update from the record.
    unproven = recorded(proven=False)
    old = health.current(unproven, SETUP_RECORD, connection=today)
    assert (old['title'], old['detail'], old['actionRequired']) == (COULD_NOT_FINISH, NO_OUTCOME, True)
    state = recorded()
    before = copy.deepcopy(state)
    plain = health.current(state, SETUP_RECORD, connection=today)
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


NAMED = changed(probe={'reason': 'ai-connection-needed'})
OTHER_FAILURES = {
    'other function': changed(probe={'failureLocation': 'amplifier_web.host.session:live_plan:1021'}),
    'missing value': changed(probe={'failureLocation': 'amplifier_web.host.config:substitute:216'}),
    'other error': changed(probe={'errorType': 'RuntimeError'}),
    'other probe stage': changed(probe={'stage': 'capabilities'}),
    'other profile gap': changed(probe={'reason': 'incomplete-conversation-profile'}),
    'bundle load': changed(probe={'reason': 'bundle-load-failed'}),
    'disk full': changed(reason='disk-full'),
    'prepare phase': changed(phase='ecosystem-prepare'),
    'compatibility phase': changed(phase='ecosystem-compatibility'),
    'interrupted': changed(status='interrupted'),
    'application': changed(kind='application'),
}


@pytest.mark.parametrize('case', OTHER_FAILURES)
def test_every_other_failure_still_needs_attention(case):
    """Also for an attempt that is marked: the mark covers only the refusal for a missing connection."""
    failure = OTHER_FAILURES[case]
    assert not health.connection_refusal(failure)
    result = health.current(recorded(failure), SETUP_RECORD, connection=NEEDED)
    assert result['actionRequired'] and result['title'] in {COULD_NOT_FINISH, 'Component source needs review'}
    # Also when new components are available: a real failure is not replaced by 'waits'.
    waiting = health.current(checked(diagnostics=recorded(failure)['diagnostics']), SETUP_RECORD, connection=NEEDED)
    assert waiting['actionRequired'] and waiting != WAITING


@pytest.mark.parametrize('failure', [RECORDED, NAMED], ids=['record of 0.98.7', 'named reason'])
def test_the_form_of_a_record_is_not_a_proof(failure):
    """The older refusal covered every incomplete selected profile, and a named one can come from any selection."""
    assert health.connection_refusal(failure)
    for today in (NEEDED, CONNECTED, OTHER_PROFILE, None):
        result = health.current(recorded(failure, proven=False), SETUP_RECORD, connection=today)
        assert (result['title'], result['actionRequired']) == (COULD_NOT_FINISH, True)
    # A mark for another attempt proves nothing about this one.
    state = recorded(failure, proven=False)
    state['diagnostics']['awaitedConnection'] = ['f' * 32]
    assert health.current(state, SETUP_RECORD, connection=NEEDED)['title'] == COULD_NOT_FINISH


# ---- The proof: the settings that the attempt itself copied into its candidate folder.

CUSTOM_PROFILE = {'bundle': {'active': 'other', 'added': {'other': OTHER_SOURCE}}}
ATTEMPT = {RECORDED['attemptId']: RECORDED['revision']}


def attempt_folder(tmp_path, settings='absent', *, copied=True):
    """An update owner's folder with the candidate that the recorded attempt left."""
    directory = tmp_path / 'updates'
    stage = directory / 'releases' / RECORDED['revision']
    stage.mkdir(parents=True)
    if copied:
        (stage / 'shared-config').mkdir()
        if settings != 'absent':
            (stage / 'shared-config/settings.yaml').write_text(yaml.safe_dump(settings))
    return SimpleNamespace(directory=directory, home=tmp_path)


@pytest.mark.parametrize('settings,proven', [
    ('absent', True),                                   # A new installation: no settings file existed.
    ({}, True),
    (providers(PROVIDER, configurator={'disabled': {'providers': ['anthropic']}}), True),
    (providers(PROVIDER), False),                       # It had a connection: the refusal had another cause.
    (CUSTOM_PROFILE, False),                            # Another profile can lack more than a provider.
    ({'sources': {'bundles': {'foundation': OTHER_SOURCE}}}, False),
    ({'bundle': {'app': [OTHER_SOURCE + '#subdirectory=behaviors/other.yaml']}}, False),
], ids=['no settings file', 'empty settings', 'connection removed', 'had a connection', 'other profile',
        'bundle source override', 'other behavior'])
def test_an_attempt_is_proven_from_its_own_copy_of_the_settings(tmp_path, settings, proven):
    manager = attempt_folder(tmp_path, settings)
    assert health.awaited_attempts(manager, dict(ATTEMPT)) == ([RECORDED['attemptId']] if proven else [])


def test_an_attempt_with_no_evidence_is_not_proven(tmp_path):
    # The candidate never copied the settings: an absent folder is not "no settings".
    assert health.awaited_attempts(attempt_folder(tmp_path / 'a', copied=False), dict(ATTEMPT)) == []
    # A reset of the cache removed the candidate, or it was never there.
    gone = SimpleNamespace(directory=tmp_path / 'b' / 'updates', home=tmp_path / 'b')
    assert health.awaited_attempts(gone, dict(ATTEMPT)) == []
    # Unreadable settings prove nothing.
    broken = attempt_folder(tmp_path / 'c')
    (broken.directory / 'releases' / RECORDED['revision'] / 'shared-config/settings.yaml').write_text('config: [')
    assert health.awaited_attempts(broken, dict(ATTEMPT)) == []
    # Another attempt's folder is not this attempt's evidence.
    other = attempt_folder(tmp_path / 'd')
    assert health.awaited_attempts(other, {RECORDED['attemptId']: 'e' * 32}) == []


@pytest.mark.skipif(not hasattr(__import__('os'), 'symlink'), reason='needs symbolic links')
def test_a_linked_candidate_folder_is_not_evidence(tmp_path):
    real = attempt_folder(tmp_path / 'elsewhere')
    directory = tmp_path / 'updates'
    (directory / 'releases').mkdir(parents=True)
    try:
        (directory / 'releases' / RECORDED['revision']).symlink_to(
            real.directory / 'releases' / RECORDED['revision'], target_is_directory=True)
    except OSError:
        pytest.skip('this account cannot create symbolic links')
    assert health.awaited_attempts(SimpleNamespace(directory=directory, home=tmp_path), dict(ATTEMPT)) == []


def test_a_failed_update_is_reported_until_a_check_replaces_it():
    """The failed stage's own phase and text stay until the next check. That check clears them."""
    text = 'Ecosystem update failed during ecosystem source policy. Current sources remain active; no conversation work was replayed.'
    result = health.current(recorded(phase='error', error=text), SETUP_RECORD, connection=NEEDED)
    assert (result['title'], result['detail'], result['actionRequired']) == (COULD_NOT_FINISH, text, True)


def test_a_record_that_is_history_gives_no_text_to_another_error():
    assert failure_guidance(NAMED).startswith('This update needs an AI connection')
    other = 'Update check failed. Your installed sources are unchanged.'
    result = health.current(recorded(NAMED, phase='error', error=other), SETUP_RECORD, connection=CONNECTED)
    assert (result['title'], result['detail'], result['actionRequired']) == (COULD_NOT_FINISH, other, True)
    # An unproven record is the failure, with its own text.
    own = health.current(recorded(NAMED, proven=False, phase='error', error=other), SETUP_RECORD, connection=CONNECTED)
    assert own['detail'] == failure_guidance(NAMED)


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


@pytest.mark.parametrize('proven', [True, False])
def test_a_proven_record_stays_history_while_changed_settings_wait_for_the_refresh(proven):
    """The proof is about the earlier attempt, so a settings change does not suspend it."""
    manager = SimpleNamespace(
        service=SimpleNamespace(state={'updates': recorded(proven=proven), 'configurationRevision': 2}),
        _update_connection=NEEDED, _update_health_configuration_revision=1,
        _active_update_evidence={'generation': 'a' * 32, 'verifiedAt': 2, 'startedAt': 1,
                                 'packages': set(), 'components': set()})
    health.publish(manager)
    status = manager.service.state['updates']['currentStatus']
    assert (status['title'], status['actionRequired']) == (
        ('Checking current configuration', False) if proven else (COULD_NOT_FINISH, True))


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
async def test_a_second_settings_change_during_the_recheck_is_reported_and_not_repeated(manager, who):
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
    assert installs == [{'automatic': True}] if who == 'owner' else installs == [{}]


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


def left_by_0987(manager, monkeypatch, settings='absent', *, candidate=True):
    """The state that v0.98.7 left: the failed stage's phase and text, its record, its retry witness and its candidate."""
    monkeypatch.setattr(health, 'setup_evidence', lambda m: dict(SETUP_RECORD))  # The installed runtime is a fixture.
    diagnostics = manager.diagnostics.state
    diagnostics.update(attemptId=RECORDED['attemptId'], kind='ecosystem', revision=RECORDED['revision'],
                       lastFailure=copy.deepcopy(RECORDED),
                       batch={'attemptId': RECORDED['attemptId'], 'kind': 'ecosystem',
                              'retry': {'inputKey': 'recorded-inputs', 'mode': 'changed-inputs'}})
    manager.diagnostics._track_failure(copy.deepcopy(RECORDED))
    stage = manager.directory / 'releases' / RECORDED['revision']
    if candidate:
        (stage / 'shared-config').mkdir(parents=True)
        if settings != 'absent':
            (stage / 'shared-config/settings.yaml').write_text(yaml.safe_dump(settings))
    return stage


FAILED_STAGE = 'Ecosystem update failed during ecosystem source policy. Current sources remain active; no conversation work was replayed.'


async def test_an_installation_with_the_old_record_is_plain_after_its_next_check(manager, monkeypatch):
    import shutil
    stage = left_by_0987(manager, monkeypatch)
    state = manager.service.state['updates']
    diagnostics = manager.diagnostics.state
    await manager.publish(phase='error', error=FAILED_STAGE)
    assert (state['currentStatus']['title'], state['currentStatus']['detail']) == (COULD_NOT_FINISH, FAILED_STAGE)
    history = copy.deepcopy(diagnostics['activeFailures'])
    assert len(history) == 1 and next(iter(history.values()))['displayError'] == FAILED_STAGE
    # Measured in that clean room: the check after the failed stage found every component current.
    manager.installed = 'b' * 40
    await manager.check()
    assert state['currentStatus']['status'] == 'current' and state['currentStatus']['title'] == 'You\u2019re up to date'
    assert not update_attention(manager.service.state)
    # History is kept as it was. The proof is saved beside it.
    assert diagnostics['lastFailure'] == RECORDED and diagnostics['activeFailures'] == history
    assert diagnostics['awaitedConnection'] == [RECORDED['attemptId']]
    # A reset of the cache removes the candidate. The saved proof stays, also for a restarted host
    # and after the person selects another profile.
    shutil.rmtree(stage)
    manager.save(CUSTOM_PROFILE)
    await manager.check()
    assert state['currentStatus']['title'] == 'You\u2019re up to date'
    restarted = UpdateManager(manager.service)
    assert restarted.diagnostics.state['awaitedConnection'] == [RECORDED['attemptId']]
    assert health.current(manager.service.state['updates'], SETUP_RECORD)['status'] == 'current'
    manager.save({})
    # A later component update still waits for a connection, and then runs.
    manager.installed = 'a' * 40
    await manager.check()
    await manager.tick()
    assert state['currentStatus'] == WAITING and manager.stages == []
    manager.save(providers(PROVIDER))
    await manager.tick()
    assert len(manager.stages) == 1


@pytest.mark.parametrize('evidence', ['candidate removed', 'other profile at the time', 'connection at the time'])
async def test_an_old_record_without_proof_stays_a_failure(manager, monkeypatch, evidence):
    """What cannot be proven from the attempt's own inputs is not called waiting, whatever today's settings are."""
    left_by_0987(manager, monkeypatch, candidate=evidence != 'candidate removed',
                 settings={'other profile at the time': CUSTOM_PROFILE,
                           'connection at the time': providers(PROVIDER)}.get(evidence, 'absent'))
    state = manager.service.state['updates']
    manager.installed = 'b' * 40
    # Today: the app's own profile and no connection.
    await manager.check()
    assert (state['currentStatus']['title'], state['currentStatus']['detail']) == (COULD_NOT_FINISH, NO_OUTCOME)
    assert 'awaitedConnection' not in manager.diagnostics.state
    assert [item['id'] for item in update_attention(manager.service.state)] == ['updates:current']
    # New components and no connection: the record keeps its priority over the waiting state.
    manager.installed = 'a' * 40
    await manager.check()
    await manager.tick()
    assert state['currentStatus']['title'] == COULD_NOT_FINISH and manager.stages == []


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
    # Unchanged inputs are not tried again by the owner; the record stays a failure after a check:
    # the attempt's own copy of the settings names another profile, so nothing proves a wait.
    await manager.check()
    await manager.tick()
    assert len(manager.stages) == 1
    assert manager.service.state['updates']['currentStatus']['title'] == COULD_NOT_FINISH
    assert 'awaitedConnection' not in manager.diagnostics.state
    # Also after the person selects the app's own profile: today's settings do not reclassify that attempt.
    manager.save({})
    await manager.check()
    assert manager.service.state['updates']['currentStatus']['title'] == COULD_NOT_FINISH
