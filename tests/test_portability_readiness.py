"""Frozen readiness intent precedes effects and never upgrades an old attempt."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_portability.capsule import read_capsule, write_capsule
from amplifier_web.portability import Portability
from amplifier_web.portability_policy import effective_effort, validate_policy
from amplifier_web.service import AppError
from amplifier_worktrees.git import digest
from test_portability import setup_hosts, export, host_env
from test_portability_activation import released_hosts, assert_no_task_execution
from test_portability_probe import receipt


@pytest.fixture
def destination(tmp_path, monkeypatch):
    config = SimpleNamespace(active_bundle='work', registry_home=tmp_path/'native', module_sources={}, providers=[{
        'module':'provider-openai', 'id':'terra', 'config':{'reasoning_effort':'high', 'api_key':'DUMMY'}}])
    from amplifier_web.setup import SetupManager
    monkeypatch.setattr(SetupManager, 'config', lambda self, workspace: config)
    adapter = Portability.__new__(Portability)
    adapter.app = SimpleNamespace(data_dir=tmp_path/'app')
    adapter.node = SimpleNamespace(directory=tmp_path/'portability')
    payload = {'intent': {'bundle':'work', 'selection': {'instance':'terra', 'model':'gpt-5.6-terra', 'effort':'low'}}}
    return adapter, config, payload


@pytest.mark.parametrize('selection,config,wanted', [
    ({'effort':'low'}, {'reasoning_effort':'high'}, 'low'),
    ({}, {'reasoning_effort':'high', 'reasoning':{'effort':'medium'}}, 'high'),
    ({'effort':None}, {'reasoning':{'effort':'medium'}}, 'medium'),
])
def test_effective_selection_precedence(selection, config, wanted):
    assert effective_effort(selection, config) == wanted


def test_policy_rejects_unresolved_implicit_effort(destination):
    adapter, config, payload = destination
    payload['intent']['selection'].pop('effort')
    config.providers[0]['config'].pop('reasoning_effort')
    with pytest.raises(ValueError, match='explicit'):
        adapter.destination_policy(payload, Path('/unused'))


@pytest.mark.parametrize('explicit', [True, False])
async def test_child_receives_frozen_selected_effort_despite_destination_default_drift(destination, monkeypatch, tmp_path, explicit):
    adapter, config, payload = destination
    if not explicit:
        payload['intent']['selection'].pop('effort')
    policy = adapter.destination_policy(payload, tmp_path)
    assert policy['reasoningEffort'] == ('low' if explicit else 'high')
    config.providers[0]['config']['reasoning_effort'] = 'medium'
    observed = []
    class Child:
        returncode = 0
        async def communicate(self, raw):
            value = json.loads(raw); observed.append(value)
            assert value['readinessPolicy'] == policy
            result = {'runtimeTransferFence':True, 'accountVerified':True, 'method':'provider.complete',
                'providerModule':policy['providerModule'], 'model':policy['model'], 'reasoningEffort':policy['reasoningEffort'],
                'readinessPolicyHash':digest(policy), 'completionReceipt':receipt(policy['model'],policy['reasoningEffort']),
                'accountVerificationScope':'selected-model-request-accepted'}
            return json.dumps(result).encode(), None
    monkeypatch.setattr('amplifier_web.runtime.RuntimeManager._command', lambda self: ['python','worker.py'])
    spawn = AsyncMock(return_value=Child())
    monkeypatch.setattr('amplifier_web.portability.asyncio.create_subprocess_exec', spawn)
    checks = await adapter.destination_checks(payload, tmp_path, policy)
    assert checks['reasoningEffort'] == policy['reasoningEffort'] and checks['readinessPolicy'] == policy
    assert observed[0]['config']['reasoning_effort'] == 'medium'
    spawn.assert_awaited_once()


async def test_unsupported_provider_explains_required_update_without_raw_diagnostics(destination, monkeypatch, tmp_path):
    adapter, _, payload = destination
    policy = adapter.destination_policy(payload, tmp_path)
    class Child:
        returncode = 0
        async def communicate(self, raw):
            return json.dumps({'error':'private provider diagnostic', 'code':'single_attempt_unsupported'}).encode(), None
    monkeypatch.setattr('amplifier_web.runtime.RuntimeManager._command', lambda self: ['python','worker.py'])
    monkeypatch.setattr('amplifier_web.portability.asyncio.create_subprocess_exec', AsyncMock(return_value=Child()))
    with pytest.raises(ValueError, match='update it or use a supported provider') as error:
        await adapter.destination_checks(payload, tmp_path, policy)
    assert 'private' not in str(error.value)


async def test_policy_is_durable_before_restore_and_duplicate_never_rederives(tmp_path, monkeypatch):
    import amplifier_web.portability as module
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        outgoing, _ = await export(app, target, sid, root)
        host_env(monkeypatch, destination)
        restore = module.restore_workspace
        seen = []
        def observed(*args):
            row = json.loads(target.portability.node.path(outgoing['id']).read_bytes())
            assert row['phase'] == 'staging'
            assert validate_policy(row['readinessPolicy'],row['readinessPolicyHash']) == row['readinessPolicy']
            assert row['readinessPolicy']['maxOutputTokens'] == 1024
            seen.append(row['readinessPolicyHash'])
            return restore(*args)
        monkeypatch.setattr(module, 'restore_workspace', observed)
        args = {'path':outgoing['package'],'repository':str(destrepo)}
        staged = (await target.dispatch('portability.stage',args))['result']
        assert seen == [staged['readinessPolicyHash']]
        assert staged['checks']['readinessPolicyHash'] == seen[0]
        assert staged['readyReceipt']['body']['checksHash'] == digest(staged['checks'])
        policy = AsyncMock(side_effect=AssertionError('duplicate cannot derive a larger policy'))
        probe = AsyncMock(side_effect=AssertionError('duplicate cannot probe'))
        monkeypatch.setattr(Portability,'destination_policy',policy)
        monkeypatch.setattr(Portability,'destination_checks',probe)
        assert (await target.dispatch('portability.stage',args))['result']['duplicate']
        policy.assert_not_called(); probe.assert_not_awaited()
        with pytest.raises(AppError, match='different contents'):
            await target.dispatch('portability.stage',{**args,'repository':str(tmp_path/'changed')})
    finally:
        host_env(monkeypatch,source); await app.close()
        host_env(monkeypatch,destination); await target.close()


@pytest.mark.parametrize('change', ['checks','ready-signature','policy','hash','legacy','unknown-version'])
async def test_changed_or_legacy_policy_stops_before_activation_effect(released_hosts, monkeypatch, change):
    hosts = released_hosts
    row = hosts.target.portability.node.get(hosts.staged['id'])
    if change == 'checks':
        row['checks']['accountVerified'] = False
    elif change == 'ready-signature':
        row['checks']['accountVerified'] = False
        row['readyReceipt']['body']['checksHash'] = digest(row['checks'])
        cert = read_capsule(Path(hosts.args['path']))
        body = {**cert['body'],'readyHash':digest(row['readyReceipt'])}
        write_capsule(Path(hosts.args['path']),hosts.app.portability.node.sign(body))
    elif change == 'policy':
        row['readinessPolicy']['maxOutputTokens'] = 2048
        row['readinessPolicyHash'] = digest(row['readinessPolicy'])
    elif change == 'hash':
        row['readinessPolicyHash'] = '0' * 64
    else:
        if change == 'legacy':
            for key in ('readinessPolicy','readinessPolicyHash'):
                row.pop(key); row['checks'].pop(key)
        else:
            row['readinessPolicy']['version'] = 2
            row['readinessPolicyHash'] = digest(row['readinessPolicy'])
            row['checks'].update(readinessPolicy=copy.deepcopy(row['readinessPolicy']),readinessPolicyHash=row['readinessPolicyHash'])
        # Model a genuinely signed older/newer policy, not a signature failure.
        body = {**row['readyReceipt']['body'],'checksHash':digest(row['checks'])}
        row['readyReceipt'] = hosts.target.portability.node.sign(body)
        cert = read_capsule(Path(hosts.args['path']))
        body = {**cert['body'],'readyHash':digest(row['readyReceipt'])}
        write_capsule(Path(hosts.args['path']),hosts.app.portability.node.sign(body))
    hosts.target.portability.node.save(row)
    probe = AsyncMock(side_effect=AssertionError('no budget may be acquired'))
    monkeypatch.setattr(Portability,'destination_checks',probe)
    monkeypatch.setattr('amplifier_web.portability.capture_workspace',lambda *_:pytest.fail('policy must precede workspace effects'))
    with pytest.raises(AppError):await hosts.target.dispatch('portability.activate',hosts.args)
    assert hosts.target.portability.node.get(row['id']) == row
    probe.assert_not_awaited(); assert_no_task_execution(hosts.target)


async def test_activation_reuses_exact_staged_policy(released_hosts, monkeypatch):
    hosts = released_hosts
    policy = copy.deepcopy(hosts.staged['readinessPolicy'])
    derive = AsyncMock(side_effect=AssertionError('activation cannot derive a new policy'))
    monkeypatch.setattr(Portability,'destination_policy',derive)
    async def check(payload, workspace, admitted):
        saved = hosts.target.portability.node.get(hosts.staged['id'])
        assert saved['phase'] == 'activating' and saved['readinessPolicy'] == policy == admitted
        assert saved['readinessPolicyHash'] == digest(admitted)
        return copy.deepcopy(hosts.staged['checks'])
    probe = AsyncMock(side_effect=check)
    monkeypatch.setattr(Portability,'destination_checks',probe)
    active = (await hosts.target.dispatch('portability.activate',hosts.args))['result']
    assert active['phase'] == 'active'
    # Already-active legacy finalization must not acquire the new policy budget.
    active.pop('readinessPolicy'); active.pop('readinessPolicyHash')
    hosts.target.portability.node.save(active)
    result = (await hosts.target.dispatch('portability.activate',hosts.args))['result']
    assert result['duplicate'] and result['phase'] == 'active'
    derive.assert_not_called(); probe.assert_awaited_once(); assert_no_task_execution(hosts.target)
