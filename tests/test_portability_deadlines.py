"""Versioned deadlines preserve legacy admission and never create replay budget."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import portability_probe
from amplifier_web.portability import Portability
from amplifier_web.portability_policy import completion_receipt, policy_digest, readiness_policy, validate_policy
from test_portability_activation import released_hosts, assert_no_task_execution
from test_portability_probe import provider, request, receipt, completed_response
from test_portability_readiness import destination


def legacy_policy(module='provider-openai', instance='terra', model='gpt-5.6-terra', effort='high'):
    return {**readiness_policy(module, instance, model, effort), 'version': 1,
            'capability': 'completion:single_attempt:v1', 'timeoutSeconds': 45}


def test_historical_v1_exact_hash_and_closed_receipt_are_still_verified():
    policy = legacy_policy()
    expected_hash = 'ee50bc947d9fe3ffc26d169658378ef85bbd2d9638b1391c0674a612ca355ad3'
    assert policy_digest(policy) == expected_hash
    assert validate_policy(policy, expected_hash) == policy
    evidence = receipt('gpt-5.6-terra', version=1, timeout=45.0)
    assert completion_receipt(evidence, policy) == evidence
    with pytest.raises(ValueError):
        validate_policy({**policy, 'timeoutSeconds': None})
    with pytest.raises(ValueError):
        completion_receipt({**evidence, 'version': 2, 'timeout_seconds': None}, policy)


@pytest.mark.parametrize('timeout', [None, 45, 0.01])
def test_v2_deadline_is_nullable_or_explicit_and_signed(timeout):
    policy = readiness_policy('provider-openai', 'terra', 'gpt-5.6-terra', 'high', timeout_seconds=timeout)
    assert policy['version'] == 2 and policy['capability'] == 'completion:single_attempt:v2'
    assert policy['timeoutSeconds'] == timeout
    assert validate_policy(policy, policy_digest(policy)) == policy
    assert completion_receipt(receipt('gpt-5.6-terra', timeout=timeout), policy)['timeout_seconds'] == timeout
    altered = {**policy, 'timeoutSeconds': 1 if timeout is None else None}
    with pytest.raises(ValueError):
        validate_policy(altered, policy_digest(policy))


@pytest.mark.parametrize('timeout', [True, False, 0, -1, float('inf'), float('nan'), '45'])
def test_invalid_explicit_experiment_deadline_is_not_admitted(timeout):
    with pytest.raises(ValueError):
        readiness_policy('provider-openai', 'terra', 'gpt-5.6-terra', 'high', timeout_seconds=timeout)


async def test_old_provider_capability_does_not_silently_accept_new_mode(provider, monkeypatch):
    monkeypatch.setattr(provider, 'get_info', lambda self: {'capabilities': ['completion:single_attempt:v1'], 'config_fields': []})
    result = await portability_probe.probe(request())
    assert result['code'] == 'single_attempt_unsupported' and provider.requests == []


async def test_historical_v1_probe_keeps_original_options_and_deadline(provider):
    value = request()
    value['readinessPolicy'] = legacy_policy('provider-fixture', 'fixture', 'selected-model', 'high')
    provider.response = completed_response(**{'openai:single_attempt': receipt(version=1, timeout=45)})
    result = await portability_probe.probe(value)
    assert result['accountVerified'] is True
    prompt, options = provider.requests[0]
    assert prompt.timeout == 45 and options == {'request_options': {'single_attempt': True}}
    assert result['readinessPolicyHash'] == policy_digest(value['readinessPolicy'])


async def test_new_probe_has_no_implicit_completion_deadline_and_cancellation_closes(provider, monkeypatch):
    deadlines = []
    actual_timeout = asyncio.timeout
    def observed_timeout(seconds):
        deadlines.append(seconds)
        return actual_timeout(seconds)
    monkeypatch.setattr(portability_probe.asyncio, 'timeout', observed_timeout)
    entered = asyncio.Event()
    async def wait(self, prompt, **kwargs):
        assert prompt.timeout is None
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(provider, 'complete', wait)
    task = asyncio.create_task(portability_probe.probe(request()))
    await entered.wait()
    assert deadlines == [None] and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(instance.closed for instance in provider.instances)


async def test_explicit_experiment_probe_expires_and_closes_once(provider, monkeypatch):
    calls = []
    async def wait(self, prompt, **kwargs):
        calls.append(prompt.timeout)
        await asyncio.Event().wait()
    monkeypatch.setattr(provider, 'complete', wait)
    value = request()
    value['readinessPolicy']['timeoutSeconds'] = 0.01
    result = await portability_probe.probe(value)
    assert result['code'] == 'probe_timeout' and calls == [0.01]
    assert all(instance.closed for instance in provider.instances)


async def test_subprocess_has_no_implicit_wall_deadline_and_cancel_cleans_owned_child(destination, monkeypatch, tmp_path):
    adapter, _, payload = destination
    policy = adapter.destination_policy(payload, tmp_path)
    assert policy['version'] == 2 and policy['timeoutSeconds'] is None
    entered = asyncio.Event()
    class Child:
        pid = 987654321  # synthetic only: os.killpg is replaced below
        returncode = None
        async def communicate(self, raw):
            assert json.loads(raw)['readinessPolicy'] == policy
            entered.set()
            await asyncio.Event().wait()
        async def wait(self):
            self.returncode = -9
    child = Child()
    monkeypatch.setattr('amplifier_web.runtime.RuntimeManager._command', lambda self: ['python', 'worker.py'])
    monkeypatch.setattr('amplifier_web.portability.asyncio.create_subprocess_exec', AsyncMock(return_value=child))
    actual_wait_for = asyncio.wait_for
    deadlines = []
    async def checked_wait_for(awaitable, timeout):
        deadlines.append(timeout)
        return await actual_wait_for(awaitable, timeout)
    monkeypatch.setattr('amplifier_web.portability.asyncio.wait_for', checked_wait_for)
    signals = []
    monkeypatch.setattr('amplifier_web.portability.os.killpg', lambda pid, sig: signals.append((pid, sig)))
    task = asyncio.create_task(adapter.destination_checks(payload, tmp_path, policy))
    await entered.wait()
    assert deadlines == [] and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(signals) == 1 and signals[0][0] == child.pid and child.returncode == -9


@pytest.fixture
def historical_admission(monkeypatch):
    monkeypatch.setattr('test_portability.readiness_policy', legacy_policy)


@pytest.fixture
async def historical_hosts(historical_admission, released_hosts):
    return released_hosts


async def test_signed_historical_v1_activation_reuses_exact_policy_without_upgrade(historical_hosts, monkeypatch):
    hosts = historical_hosts
    policy = copy.deepcopy(hosts.staged['readinessPolicy'])
    assert policy['version'] == 1 and policy['timeoutSeconds'] == 45
    signed_checks = hosts.target.portability.node.readiness_checks(hosts.staged)
    assert signed_checks['readinessPolicy'] == policy
    derive = AsyncMock(side_effect=AssertionError('cannot upgrade historical policy'))
    monkeypatch.setattr(Portability, 'destination_policy', derive)
    async def check(payload, workspace, admitted):
        assert admitted == policy
        return copy.deepcopy(hosts.staged['checks'])
    probe = AsyncMock(side_effect=check)
    monkeypatch.setattr(Portability, 'destination_checks', probe)
    result = (await hosts.target.dispatch('portability.activate', hosts.args))['result']
    assert result['phase'] == 'active' and result['readinessPolicy'] == policy
    assert (await hosts.target.dispatch('portability.activate', hosts.args))['result']['duplicate']
    derive.assert_not_called()
    probe.assert_awaited_once()
    assert_no_task_execution(hosts.target)
