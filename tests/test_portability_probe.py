"""Destination acceptance executes only a fixed, bounded model request."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_core.message_models import ChatResponse, TextBlock
from amplifier_web import portability_probe


SECRET = "destination-private-credential"


@pytest.fixture
def provider(monkeypatch):
    class Provider:
        instances = []
        requests = []
        response = ChatResponse(content=[TextBlock(text="OK")], finish_reason="stop")
        failure = None

        def __init__(self, *, api_key=None, config=None):
            self.config = config
            self.closed = False
            if config:
                assert api_key == SECRET
            self.instances.append(self)

        def get_info(self):
            return {"config_fields": [{"id": "api_key", "field_type": "secret", "required": True}]}

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
    return {"module": "provider-fixture", "config": {"api_key": "${DESTINATION_PROBE_KEY}"}, "model": "selected-model"}


async def test_fixed_request_uses_destination_secret_selected_model_and_no_tools(provider):
    original = request()
    before = copy.deepcopy(original)
    result = await portability_probe.probe(original)
    assert result == {"runtimeTransferFence": True, "accountVerified": True,
        "method": "provider.complete", "model": "selected-model", "providerModule": "provider-fixture",
        "accountVerificationScope": "selected-model-request-accepted"}
    assert original == before
    assert SECRET not in json.dumps(result)
    assert len(provider.requests) == 1
    prompt, options = provider.requests[0]
    assert prompt.model == "selected-model" and options == {"model": "selected-model"}
    assert prompt.max_output_tokens == 16 and prompt.timeout == 45
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


async def test_provider_without_model_keyword_receives_core_model_field(provider, monkeypatch):
    async def complete(self, request):
        self.requests.append((request, {}))
        return self.response
    monkeypatch.setattr(provider, "complete", complete)
    result = await portability_probe.probe(request())
    assert result["accountVerified"] and provider.requests[0][0].model == "selected-model"


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
import os
from amplifier_core.message_models import ChatResponse, TextBlock
from amplifier_web import portability_probe
class FixtureProvider:
    def __init__(self, *, api_key=None, config=None):
        self.config = config
    def get_info(self):
        print("destination-private-credential")
        return {"config_fields": []}
    async def complete(self, request, **kwargs):
        os.write(1, b"destination-private-credential")
        os.write(2, b"destination-private-credential")
        assert request.model == "selected-model"
        assert request.max_output_tokens == 16 and request.tools is None
        return ChatResponse(content=[TextBlock(text="OK")], finish_reason="stop")
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
