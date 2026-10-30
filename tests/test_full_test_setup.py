"""Full setup through real dispatch/mounted children; backend effects are controlled."""
import asyncio
from copy import deepcopy
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_setup as setup
from amplifier_web.service import AppError
from test_access_mode import save
from test_configuration_authority import app, grant
from test_coordination_generation import agent
from test_full_child_configuration import mounted, dispatch


def arguments(app, source, **extra):
    workspace = next(row for row in app.state['workspaces'] if row['path'] == source['workspace'])
    return {'workspaceId': workspace['id'], 'sessionId': source['id'],
            'requestId': 'owned-setup', 'backend': 'docker', **extra}


def record(app):
    latest = setup.read_json(setup.root(app) / 'latest.json')
    return setup.read_json(setup.root(app) / (latest['id'] + '.json'))


async def settle(app):
    await asyncio.wait_for(asyncio.gather(*list(getattr(app, '_test_setup_tasks', {}).values())), 5)
    return record(app)


@pytest.fixture
def backend(app, monkeypatch):
    monkeypatch.setattr(setup.sys, 'platform', 'linux')
    installed = AsyncMock(return_value={'id': 'a' * 24, 'commit': owner.DTU_COMMIT, 'extras': []})
    monkeypatch.setattr(app, 'smart_tools', SimpleNamespace(
        install=installed, state={'installations': []}, close=AsyncMock()))
    monkeypatch.setattr(owner, 'configuration', lambda app: (
        setup.read_json(app.data_dir / 'test-environments.json'), 'disabled', 'Controlled runner'))
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda app, value: (value, 'ready', 'Qualified'))
    monkeypatch.setattr(setup, 'discover', lambda: [{'id': 'docker', 'available': True,
        'reason': 'Controlled backend', 'dockerHost': 'unix:///controlled.sock'}])
    monkeypatch.setattr(setup, 'inspect_base_image', lambda _: None)
    monkeypatch.setattr(setup, 'readiness', AsyncMock(return_value={'status': 'ready'}))
    entered, release = asyncio.Event(), asyncio.Event()
    release.set()
    value = SimpleNamespace(installed=installed, entered=entered, release=release, calls=[], unknown=False)

    async def prepare(base, selected, owned, row, step):
        await step('checking_profile', 'Controlled owned probe')
        value.calls.append('probe')
        entered.set()
        await release.wait()
        if value.unknown:
            raise TimeoutError('The original probe outcome is unknown')
        return {**base, 'dockerHost': selected['dockerHost'], 'image': owner.APPROVED_IMAGE}, {
            'probeRemoved': True, 'offline': True, 'daemon': 'controlled-daemon'}

    monkeypatch.setattr(setup, 'setup_docker', prepare)
    yield value
    release.set()


async def test_mounted_full_child_setup_survives_completed_turn_without_browser(mounted, backend):
    h = mounted
    await save(h.app)
    backend.release.clear()
    receipts = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await dispatch(tools['app_control'], 'workspace.environments.setup',
            arguments(h.app, h.source), 'child-setup')
        assert result.success, result.error
        receipts.append(result.output)
        return 'setup accepted'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'set up unattended tests', h.parent, sub_session_id='child')
    await asyncio.wait_for(backend.entered.wait(), 5)
    h.source['collaborationGeneration']['terminal'] = True
    h.source['status'] = 'idle'
    backend.release.set()
    retained = await settle(h.app)
    assert receipts[0]['accepted'] and retained['state'] == 'completed'
    assert retained['actor'] == {'sessionId': h.source['id'], 'principal': 'child',
                                 'origin': 'agent', 'generationId': 'generation-1'}
    assert retained['authority']['inputIds'] == ['assigned-input']
    assert retained['authority']['interruptionRevision'] == 0
    assert retained['authority']['accessRevision'] == h.app.access_mode.snapshot(h.source)['revision']
    assert setup.read_json(h.app.data_dir / 'test-environments.json') == retained['candidateConfiguration']
    assert all(row.get('clientId') is None for row in h.envelopes[-1]['_inputBindings'])
    assert not h.source['approvals'] and h.app.configuration_authority.rows(h.source['id']) == []
    h.approval_prompt.assert_not_awaited()
    backend.installed.assert_awaited_once()


async def test_later_full_child_observes_and_cleans_original_unknown_setup(mounted, backend, monkeypatch):
    h = mounted
    await save(h.app)
    backend.unknown = True
    results = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        args = arguments(h.app, h.source)
        if coordinator.session_id == 'first-child':
            result = await dispatch(tools['app_control'], 'workspace.environments.setup', args, 'first')
            assert result.success, result.error
        else:
            duplicate = await dispatch(tools['app_control'], 'workspace.environments.setup', args, 'later-retry')
            assert duplicate.success, duplicate.error
            assert duplicate.output['result']['duplicate']
            assert duplicate.output['result']['operation']['state'] == 'outcome_unknown'
            changed = await dispatch(tools['app_control'], 'workspace.environments.setup',
                {**args, 'backend': 'incus'}, 'different-intent')
            assert not changed.success and 'another intent' in str(changed.error)
            args.pop('backend')
            for action in ('reconcile', 'cleanup'):
                result = await dispatch(tools['app_control'], 'workspace.environments.setup.' + action,
                    args, 'later-' + action)
                assert result.success, result.error
                results.append(result.output)
        return 'accepted'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'setup', h.parent, sub_session_id='first-child')
    original = await settle(h.app)
    assert original['state'] == 'outcome_unknown'
    observed = []
    effects = []

    def observe(app, row):
        observed.append(row['id'])
        return {'backend': 'docker', 'absent': bool(effects), 'prefix': ['controlled-docker'],
                'probe': {'name': 'owned-probe'}, 'observedResources': ['probe']}

    monkeypatch.setattr(setup, 'observe_partial', observe)
    monkeypatch.setattr(setup, 'command', lambda argv, **kw: effects.append(argv) or '')
    h.worker.runtime.generation = {'id': 'generation-2', 'input_ids': ['followup-input']}
    h.worker.context_inputs = ['followup-input']
    h.worker.context_bindings = {'followup-input': {'clientId': None, 'targets': []}}
    h.source['collaborationGeneration'] = {'id': 'generation-2', 'inputIds': ['followup-input'],
        'interruptionRevision': 0, 'terminal': False}
    await h.registry.spawn('worker', 'recover earlier setup', h.parent, sub_session_id='later-child')
    retained = await settle(h.app)
    assert len(results) == 2 and retained['state'] == 'recovered'
    assert retained['actor'] == original['actor'] and retained['authority'] == original['authority']
    assert retained['originalState'] == original['state'] == 'outcome_unknown'
    assert retained['reconciliation']['actor']['principal'] == 'later-child'
    assert retained['cleanup']['actor']['principal'] == 'later-child'
    assert retained['cleanup']['authority']['generationId'] == 'generation-2'
    assert [command[-2:] for command in effects] == [['stop', 'owned-probe'], ['rm', 'owned-probe']]
    assert backend.calls == ['probe'] and len(set(observed)) == 1
    backend.installed.assert_awaited_once()
    # A subsequent observation belongs to its own actor and must not erase the
    # actual cleanup caller or expose the captured authority in public status.
    cleanup = deepcopy(retained['cleanup'])
    args = arguments(h.app, h.source)
    args.pop('backend')
    reconciled = await h.app.dispatch('workspace.environments.setup.reconcile', args,
        origin='system', caller_session_id=h.source['id'])
    latest = record(h.app)
    assert latest['cleanup']['actor'] == cleanup['actor']
    assert latest['cleanup']['authority'] == cleanup['authority']
    assert latest['cleanup']['createdAt'] == cleanup['createdAt']
    assert latest['reconciliation']['actor']['origin'] == 'system'
    assert latest['reconciliation']['actor']['principal'] is None
    assert latest['actor'] == original['actor'] and latest['authority'] == original['authority']
    public_cleanup = reconciled['result']['operation']['cleanup']
    assert 'actor' not in public_cleanup and 'authority' not in public_cleanup
    assert public_cleanup['state'] == 'completed' and public_cleanup['resourcesAbsent']
    assert public_cleanup['createdAt'] == cleanup['createdAt']
    assert len(effects) == 2 and backend.calls == ['probe']


@pytest.mark.parametrize('initiator', ['root', 'child'])
@pytest.mark.parametrize('revoked', [False, True])
async def test_ui_recovers_same_conversation_full_setup_after_actor_or_mode_changes(
        mounted, backend, monkeypatch, tmp_path, initiator, revoked):
    h = mounted
    await save(h.app)
    backend.unknown = True
    args = arguments(h.app, h.source)

    async def start(control):
        result = await dispatch(control, 'workspace.environments.setup', args, 'full-setup')
        assert result.success, result.error

    if initiator == 'root':
        await start(h.parent.coordinator.get('tools')['app_control'])
    else:
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            await start(tools['app_control'])
            return 'accepted'
        h.install_execute(execute)
        await h.registry.spawn('worker', 'setup', h.parent, sub_session_id='child')
    original = deepcopy(await settle(h.app))
    assert original['actor']['principal'] == ('native-root' if initiator == 'root' else 'child')
    if revoked:
        await save(h.app, mode='ask')
        denied = await dispatch(h.parent.coordinator.get('tools')['app_control'],
            'workspace.environments.setup', args, 'ask-retry')
        assert not denied.success and 'Configuration authority is required' in str(denied.error)

    retry = await h.app.dispatch('workspace.environments.setup', args)
    assert retry['result']['duplicate'] and retry['result']['operation']['state'] == 'outcome_unknown'
    effects = []
    monkeypatch.setattr(setup, 'observe_partial', lambda app, row: {
        'backend': 'docker', 'absent': bool(effects), 'prefix': ['controlled-docker'],
        'probe': {'name': 'owned-probe'}, 'observedResources': ['probe']})
    monkeypatch.setattr(setup, 'command', lambda argv, **kw: effects.append(argv) or '')
    recovery = {key: value for key, value in args.items() if key != 'backend'}
    # Operator origin does not turn a different target into the original owner.
    peer = next(row for row in h.app.state['sessions'] if row['id'] != h.source['id'])
    h.app.state['workspaces'].append({**h.app.state['workspaces'][0],
        'id': 'foreign-workspace', 'path': str(tmp_path / 'foreign')})
    for action in ('reconcile', 'cleanup'):
        for foreign in ({**recovery, 'sessionId': peer['id']},
                        {**recovery, 'workspaceId': 'foreign-workspace'}):
            with pytest.raises(AppError):
                await h.app.dispatch('workspace.environments.setup.' + action, foreign)
    assert record(h.app) == original and effects == []

    reconciled = await h.app.dispatch('workspace.environments.setup.reconcile', recovery)
    assert reconciled['result']['operation']['state'] == 'outcome_unknown'
    accepted = await h.app.dispatch('workspace.environments.setup.cleanup', recovery)
    assert accepted['accepted']
    retained = await settle(h.app)
    assert retained['state'] == 'recovered'
    assert retained['actor'] == original['actor'] and retained['authority'] == original['authority']
    assert retained['cleanup']['actor']['origin'] == 'ui' and retained['cleanup']['authority'] is None
    cleanup = deepcopy(retained['cleanup'])
    observed = await h.app.dispatch('workspace.environments.setup.reconcile', recovery)
    final = record(h.app)
    assert final['cleanup']['actor'] == cleanup['actor'] and final['cleanup']['authority'] is None
    assert final['reconciliation']['actor']['origin'] == 'ui'
    public_cleanup = observed['result']['operation']['cleanup']
    assert 'actor' not in public_cleanup and 'authority' not in public_cleanup
    assert public_cleanup['state'] == 'completed' and public_cleanup['resourcesAbsent']
    assert len(effects) == 2 and backend.calls == ['probe']
    backend.installed.assert_awaited_once()


async def test_ui_publishes_verified_child_candidate_after_revocation_without_replaying(mounted, backend, monkeypatch):
    h = mounted
    await save(h.app)
    prepare = setup.setup_docker

    async def prepare_then_revoke(*args):
        candidate, proof = await prepare(*args)
        await save(h.app, mode='ask')
        return candidate, proof

    monkeypatch.setattr(setup, 'setup_docker', prepare_then_revoke)
    args = arguments(h.app, h.source)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await dispatch(tools['app_control'], 'workspace.environments.setup', args, 'child-candidate')
        assert result.success, result.error
        return 'accepted'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'setup', h.parent, sub_session_id='child')
    original = deepcopy(await settle(h.app))
    assert original['state'] == 'outcome_unknown' and original['proof']['probeRemoved']
    assert original['actor']['principal'] == 'child' and not h.app.access_mode.full(h.source)
    assert not (h.app.data_dir / 'test-environments.json').exists()
    monkeypatch.setattr(owner, 'invoke', lambda *args: {
        'ok': True, 'capacity': {'scope': {'daemon': 'controlled-daemon'}}})
    completed = await h.app.dispatch('workspace.environments.setup', args)
    final = record(h.app)
    assert completed['result']['duplicate'] and final['state'] == 'completed'
    assert final['actor'] == original['actor'] and final['authority'] == original['authority']
    assert final['completion']['actor']['origin'] == 'ui' and final['completion']['authority'] is None
    assert setup.read_json(h.app.data_dir / 'test-environments.json') == original['candidateConfiguration']
    assert backend.calls == ['probe']
    backend.installed.assert_awaited_once()


async def test_trusted_system_full_setup_has_explicit_owner_and_no_fake_native_actor(app, backend):
    source = app.state['sessions'][0]
    await save(app)
    source['collaborationGeneration']['terminal'] = True
    result = await app.dispatch('workspace.environments.setup', arguments(app, source),
        origin='system', caller_session_id=source['id'])
    retained = await settle(app)
    assert result['accepted'] and retained['state'] == 'completed'
    assert retained['actor'] == {'sessionId': source['id'], 'principal': None,
                                'origin': 'system', 'generationId': None}
    assert retained['authority']['runtimeSessionId'] is None
    assert retained['authority']['inputCoverage'] == 'unavailable' and retained['authority']['inputIds'] == []
    assert not source['approvals']
    followup = {key: value for key, value in arguments(app, source).items() if key != 'backend'}
    observed = await app.dispatch('workspace.environments.setup.reconcile', followup,
        origin='system', caller_session_id=source['id'])
    assert observed['result']['operation']['state'] == 'completed'
    assert record(app)['actor'] == retained['actor']
    backend.installed.assert_awaited_once()


@pytest.mark.parametrize('action', ['setup', 'setup.cleanup', 'setup.reconcile'])
async def test_full_setup_never_adopts_another_conversations_result(app, backend, action):
    source, peer = app.state['sessions']
    await save(app)
    await save(app, peer)
    backend.unknown = True
    await agent(app, source, 'workspace.environments.setup', arguments(app, source), 'original')
    original = deepcopy(await settle(app))
    args = arguments(app, source)
    if action != 'setup':
        args.pop('backend')
    with pytest.raises(AppError):
        await app.dispatch('workspace.environments.' + action, args, origin='system', caller_session_id=peer['id'])
    # Selecting one's own conversation with the original request ID cannot find
    # another owner's journal entry either.
    if action != 'setup':
        with pytest.raises(AppError, match='another root or workspace'):
            await app.dispatch('workspace.environments.' + action, {**args, 'sessionId': peer['id']},
                origin='system', caller_session_id=peer['id'])
    assert record(app) == original and backend.calls == ['probe']


@pytest.mark.parametrize('change', ['revoke', 'off-on', 'stop', 'move'])
async def test_full_setup_rechecks_after_final_validation_before_publication(app, backend, monkeypatch, tmp_path, change):
    source = app.state['sessions'][0]
    await save(app)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def validate(app, config):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), 'test did not release final validation'
        return config, 'ready', 'Qualified'

    monkeypatch.setattr(owner, 'validate_configuration_value', validate)
    await agent(app, source, 'workspace.environments.setup', arguments(app, source), 'delayed')
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if change in {'revoke', 'off-on'}:
            await save(app, mode='ask')
            if change == 'off-on':
                await save(app)
        elif change == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': source['id']})
        else:
            source['workingDirectory'] = str(tmp_path / 'moved')
            source['executionRevision'] = source.get('executionRevision', 0) + 1
    finally:
        release.set()
    retained = await settle(app)
    assert retained['state'] == 'outcome_unknown' and retained['phase'] == 'saving'
    assert retained['candidateConfiguration'] and retained['proof']['probeRemoved']
    assert not (app.data_dir / 'test-environments.json').exists()
    assert not list(setup.root(app).glob('verified-*.json'))
    assert backend.calls == ['probe']


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_requires_real_agent_binding_or_trusted_system_owner(app, backend, origin):
    source = app.state['sessions'][0]
    await save(app)
    with pytest.raises(AppError):
        await app.dispatch('workspace.environments.setup', arguments(app, source), origin=origin,
            **({'caller_session_id': source['id']} if origin == 'agent' else {}))
    backend.installed.assert_not_awaited()


async def test_ask_setup_preserves_root_grant_and_rejects_unattended_system(app, backend):
    source = app.state['sessions'][0]
    with pytest.raises(AppError):
        await app.dispatch('workspace.environments.setup', arguments(app, source),
            origin='system', caller_session_id=source['id'])
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, 'workspace.environments.setup', arguments(app, source), 'unapproved')
    await grant(app, ['test_host_setup'], ['global'])
    accepted = await agent(app, source, 'workspace.environments.setup', arguments(app, source), 'approved')
    assert accepted['accepted'] and (await settle(app))['state'] == 'completed'
    backend.installed.assert_awaited_once()


async def test_mounted_ask_child_cannot_borrow_setup_grant(mounted, backend):
    h = mounted
    await grant(h.app, ['test_host_setup'], ['global'])
    visited = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await dispatch(tools['app_control'], 'workspace.environments.setup',
            arguments(h.app, h.source), 'ask-child')
        assert not result.success and 'child cannot borrow' in str(result.error)
        visited.append(coordinator.session_id)
        return 'not admitted'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'setup', h.parent, sub_session_id='child')
    assert visited == ['child']
    backend.installed.assert_not_awaited()
