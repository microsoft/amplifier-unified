"""Destination acceptance executes only a fixed, bounded model request."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_core.message_models import ChatResponse, TextBlock
from amplifier_web import portability_probe
from amplifier_web.portability_policy import readiness_policy
from amplifier_worktrees.git import digest


SECRET = "destination-private-credential"


def receipt(model='selected-model', effort='high'):
    fixed_input = [{'role':'user','content':[{'type':'input_text','text':'Reply with OK.'}]}]
    input_hash = hashlib.sha256(json.dumps(fixed_input, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'version': 1, 'model': model, 'reasoning_effort': effort, 'max_output_tokens': 1024,
            'timeout_seconds': 45.0, 'native_count_requests': 1, 'generation_requests': 1,
            'native_input_tokens': 6, 'retries': 0, 'continuations': 0, 'closed': True,
            'input_sha256': input_hash, 'request_sha256': 'b' * 64}


def completed_response(**changes):
    metadata = {'openai:status': 'completed', 'openai:single_attempt': receipt()}
    metadata.update(changes)
    return ChatResponse(content=[TextBlock(text='OK')], finish_reason='stop', metadata=metadata)


@pytest.fixture
def provider(monkeypatch):
    class Provider:
        instances = []
        requests = []
        response = completed_response()
        failure = None

        def __init__(self, *, api_key=None, config=None):
            self.config = config
            self.closed = False
            if config:
                assert api_key == SECRET
            self.instances.append(self)

        def get_info(self):
            return {'capabilities': ['completion:single_attempt:v1'],
                    "config_fields": [{"id": "api_key", "field_type": "secret", "required": True}]}

        async def list_models(self):
            raise AssertionError("model discovery cannot prove execution access")

        async def complete(self, request, **kwargs):
            self.requests.append((request, kwargs))
            if self.failure:
                raise self.failure
            return self.response

        async def close(self):
            self.closed = True

    monkeypatch.setattr(portability_probe, "provider_class", lambda module: Provider)
    monkeypatch.setenv("DESTINATION_PROBE_KEY", SECRET)
    return Provider


def request():
    return {"module": "provider-fixture", "config": {"api_key": "${DESTINATION_PROBE_KEY}"}, "model": "selected-model",
            'readinessPolicy': readiness_policy('provider-fixture', 'fixture', 'selected-model', 'high')}


async def test_fixed_request_uses_destination_secret_selected_model_and_no_tools(provider):
    original = request()
    before = copy.deepcopy(original)
    result = await portability_probe.probe(original)
    assert result == {"runtimeTransferFence": True, "accountVerified": True,
        "method": "provider.complete", "model": "selected-model", "providerModule": "provider-fixture",
        'reasoningEffort': 'high', 'readinessPolicyHash': digest(original['readinessPolicy']),
        'completionReceipt': receipt(),
        "accountVerificationScope": "selected-model-request-accepted"}
    assert original == before
    assert SECRET not in json.dumps(result)
    assert len(provider.requests) == 1
    prompt, options = provider.requests[0]
    assert prompt.model == "selected-model" and options == {'request_options': {'single_attempt': True}}
    assert prompt.reasoning_effort == 'high'
    assert prompt.max_output_tokens == 1024 and prompt.timeout == 45
    assert prompt.tools is None and prompt.tool_choice is None
    assert prompt.conversation_id is None and prompt.stream is False
    assert [(message.role, message.content) for message in prompt.messages] == [("user", "Reply with OK.")]
    assert len(provider.instances) == 2 and all(instance.closed for instance in provider.instances)


@pytest.mark.parametrize("failure", [PermissionError(SECRET), RuntimeError(SECRET), TimeoutError(SECRET)])
async def test_rejected_auth_unknown_error_or_timeout_never_report_success(provider, failure):
    provider.failure = failure
    result = await portability_probe.probe(request())
    assert "error" in result and not result.get("accountVerified")
    assert "runtimeTransferFence" not in result
    assert SECRET not in json.dumps(result)
    assert all(instance.closed for instance in provider.instances)


async def test_model_discovery_without_complete_is_insufficient(provider):
    provider.complete = None
    result = await portability_probe.probe(request())
    assert result["code"] == "inference_unsupported"
    assert not result.get("accountVerified") and not provider.requests


@pytest.mark.parametrize("response", [
    None,
    {"reachable": True, "models": ["selected-model"]},
    ChatResponse(content=[]),
    ChatResponse(content=[TextBlock(text=" ")]),
    ChatResponse(content=[TextBlock(text="blocked")], finish_reason="content_filter"),
    ChatResponse(content=[TextBlock(text="partial")], finish_reason="length"),
    ChatResponse(content=[TextBlock(text="not executed")], tool_calls=[{"id": "tool", "name": "shell", "arguments": {}}]),
])
async def test_only_a_valid_completed_text_response_accepts_execution(provider, response):
    provider.response = response
    result = await portability_probe.probe(request())
    assert result["code"] == "invalid_response"
    assert not result.get("accountVerified")


@pytest.mark.parametrize('capability', ['acquire_transfer', 'confirm_transfer_commit'])
async def test_missing_runtime_fence_fails_before_provider_construction(provider, monkeypatch, capability):
    from amplifier_foundation.session import SharedSessionStore
    monkeypatch.delattr(SharedSessionStore, capability)
    result = await portability_probe.probe(request())
    assert result["code"] == "runtime_fence_unavailable"
    assert provider.instances == []


async def test_provider_without_bounded_option_fails_before_dispatch(provider, monkeypatch):
    async def complete(self, request):
        self.requests.append((request, {}))
        return self.response
    monkeypatch.setattr(provider, "complete", complete)
    result = await portability_probe.probe(request())
    assert result['code'] == 'single_attempt_unsupported' and provider.requests == []


async def test_unadvertised_option_is_not_sent_to_a_provider_that_ignores_kwargs(provider, monkeypatch):
    monkeypatch.setattr(provider, 'get_info', lambda self: {'capabilities': [], 'config_fields': []})
    result = await portability_probe.probe(request())
    assert result['code'] == 'single_attempt_unsupported'
    assert provider.requests == [] and len(provider.instances) == 2
    assert all(instance.closed for instance in provider.instances)


@pytest.mark.parametrize('ambient,configured,supported', [
    ('https://proxy.invalid/v1', 'https://api.openai.com/v1', True),
    ('https://api.openai.com/v1', 'https://proxy.invalid/v1', False),
])
async def test_capability_uses_materialized_destination_endpoint(provider, monkeypatch, ambient, configured, supported):
    original_info = provider.get_info
    observed = []
    def endpoint_info(self):
        endpoint = self.config.get('base_url') or os.environ['OPENAI_BASE_URL']
        observed.append(endpoint)
        info = original_info(self)
        if endpoint != 'https://api.openai.com/v1':
            info['capabilities'] = []
        return info
    monkeypatch.setenv('OPENAI_BASE_URL', ambient)
    monkeypatch.setenv('DESTINATION_PROBE_URL', configured)
    monkeypatch.setattr(provider, 'get_info', endpoint_info)
    value = request()
    value['config']['base_url'] = '${DESTINATION_PROBE_URL}'
    result = await portability_probe.probe(value)
    assert observed == [ambient, configured]
    assert len(provider.instances) == 2 and all(instance.closed for instance in provider.instances)
    assert provider.instances[1].config['base_url'] == configured
    assert len(provider.requests) == int(supported)
    if supported:
        assert result['accountVerified'] is True
    else:
        assert result['code'] == 'single_attempt_unsupported' and 'accountVerified' not in result


@pytest.mark.parametrize('change', [
    {'version': 2}, {'maxOutputTokens': 16}, {'maxOutputTokens': 2048}, {'retries': True},
    {'continuations': 1}, {'maxGenerationRequests': 2}, {'reasoningEffort': None},
    {'tools': []}, {'prompt': 'Continue the saved task.'}, {'model': 'another-model'},
    {'providerModule': 'provider-other'}, {'extra': 'unsupported'},
])
async def test_unsupported_or_changed_policy_fails_before_provider_construction(provider, change):
    value = request()
    value['readinessPolicy'].update(change)
    result = await portability_probe.probe(value)
    assert result['code'] == 'invalid_readiness_policy' and provider.instances == []


async def test_absent_legacy_policy_never_acquires_new_budget(provider):
    value = request(); value.pop('readinessPolicy')
    assert (await portability_probe.probe(value))['code'] == 'invalid_readiness_policy'
    assert provider.instances == []


@pytest.mark.parametrize('metadata', [
    {}, {'openai:status': 'incomplete'}, {'openai:status': 'failed'},
    {'openai:status': 'completed', 'openai:refusal': 'refused'},
    {'openai:status': 'completed', 'openai:incomplete_reason': 'max_output_tokens'},
])
async def test_nonblank_text_and_none_finish_reason_do_not_prove_completion(provider, metadata):
    provider.response = ChatResponse(content=[TextBlock(text='apparently complete')], metadata=metadata)
    assert (await portability_probe.probe(request()))['code'] == 'invalid_response'
    assert len(provider.requests) == 1


@pytest.mark.parametrize('change', [
    {'version': True}, {'model': 'drifted'}, {'reasoning_effort': 'low'}, {'max_output_tokens': 2048},
    {'native_count_requests': 0}, {'generation_requests': 2}, {'retries': 1}, {'continuations': 1},
    {'closed': False}, {'native_input_tokens': True}, {'input_sha256': 'not-a-hash'},
    {'input_sha256': 'd' * 64}, {'timeout_seconds': 90},
])
async def test_bounded_receipt_must_match_admission_and_confirm_close(provider, change):
    provider.response = completed_response(**{'openai:single_attempt': {**receipt(), **change}})
    assert (await portability_probe.probe(request()))['code'] == 'invalid_completion_receipt'
    assert len(provider.requests) == 1 and all(p.closed for p in provider.instances)


@pytest.mark.parametrize('schema_failure', [True, False])
async def test_close_failure_is_not_suppressed_or_retried(provider, monkeypatch, schema_failure):
    async def close(self):
        self.closed = True
        if bool(self.config) != schema_failure:
            raise RuntimeError(SECRET)
    monkeypatch.setattr(provider, 'close', close)
    result = await portability_probe.probe(request())
    assert result['code'] == 'provider_close_failed' and SECRET not in json.dumps(result)
    assert len(provider.requests) == (0 if schema_failure else 1)


@pytest.mark.parametrize("change", [
    {"messages": [{"role": "user", "content": "private task history"}]},
    {"task": {"goal": "private objective"}},
    {"model": None}, {"module": "../other"}, {"config": "not a mapping"},
])
async def test_task_payloads_and_invalid_selection_are_rejected_before_work(provider, change):
    result = await portability_probe.probe({**request(), **change})
    assert result["code"] == "invalid_request" and provider.instances == []


def test_real_runtime_fence_probe_preserves_native_home_and_creates_no_task_data(tmp_path, monkeypatch):
    home = tmp_path / "untouched-native-home"
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    portability_probe._runtime_transfer_fence()
    assert os.environ["AMPLIFIER_HOME"] == str(home)
    assert not home.exists()


def test_subprocess_protocol_suppresses_credentials_in_native_provider_diagnostics(tmp_path):
    # Use the actual subprocess entrypoint and installed Core/Foundation. Only
    # provider discovery is replaced; neither native runtime proof nor request
    # schema is mocked. Both fd-level and Python diagnostics must stay private.
    driver = tmp_path / "probe_driver.py"
    driver.write_text('''
import os, hashlib, json
from amplifier_core.message_models import ChatResponse, TextBlock
from amplifier_web import portability_probe
class FixtureProvider:
    def __init__(self, *, api_key=None, config=None):
        self.config = config
    def get_info(self):
        print("destination-private-credential")
        return {"config_fields": [], 'capabilities': ['completion:single_attempt:v1']}
    async def complete(self, request, **kwargs):
        os.write(1, b"destination-private-credential")
        os.write(2, b"destination-private-credential")
        assert request.model == "selected-model"
        assert request.max_output_tokens == 1024 and request.tools is None
        assert request.reasoning_effort == 'high' and kwargs == {'request_options': {'single_attempt': True}}
        receipt = {'version': 1, 'model': request.model, 'reasoning_effort': request.reasoning_effort,
            'max_output_tokens': 1024, 'timeout_seconds': 45, 'native_count_requests': 1,
            'generation_requests': 1, 'native_input_tokens': 6, 'retries': 0, 'continuations': 0,
            'closed': True, 'input_sha256': hashlib.sha256(json.dumps(
                [{'role':'user','content':[{'type':'input_text','text':'Reply with OK.'}]}],
                sort_keys=True,separators=(',',':')).encode()).hexdigest(), 'request_sha256': 'b' * 64}
        return ChatResponse(content=[TextBlock(text="OK")], finish_reason="stop",
            metadata={'openai:status': 'completed', 'openai:single_attempt': receipt})
    async def close(self):
        pass
portability_probe.provider_class = lambda module: FixtureProvider
portability_probe.main()
''')
    result = subprocess.run([sys.executable, str(driver)], input=json.dumps(request()),
        capture_output=True, text=True, timeout=20, env={**os.environ, "DESTINATION_PROBE_KEY": SECRET})
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["accountVerified"] and value["runtimeTransferFence"]
    assert SECRET not in result.stdout + result.stderr
    assert result.stderr == ""


def test_direct_script_rejects_malformed_input_without_diagnostics(tmp_path):
    path = Path(portability_probe.__file__)
    result = subprocess.run([sys.executable, str(path)], input="{" + SECRET,
        capture_output=True, text=True, timeout=20, cwd=tmp_path)
    assert json.loads(result.stdout)["code"] == "invalid_request"
    assert result.stderr == "" and SECRET not in result.stdout
