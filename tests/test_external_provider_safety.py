"""No provider request-override escape hatch across response-only ingress."""

import asyncio
import copy
import importlib.metadata
import json
import os
import socket
import tomllib
from pathlib import Path

import pytest
import test_host_session_resume
from amplifier_core.message_models import ChatRequest, Message
from amplifier_module_provider_openai import OpenAIProvider
from test_external_requests import BODY, setup, submit

from amplifier_web.external_requests import validate_external_providers
from amplifier_web.image_defaults import apply_defaults
from amplifier_web.provider_environment import HOST_CREDENTIAL

mounted_host = test_host_session_resume.mounted_host
CANARY = "PRIVATE_PROVIDER_OVERRIDE_CANARY"
OVERRIDES = {
    "instructions": CANARY,
    "input": [{"role": "user", "content": CANARY}],
    "tools": [{"type": "file_search", "vector_store_ids": ["vs_private_canary"]}],
    "previous_response_id": "resp_private_canary",
}
ORDINARY = {
    # Same field/type shape as current ordinary configuration, synthetic values.
    "api_key": "${FIXTURE_OPENAI_KEY}",
    "base_url": "https://fixture.invalid/v1",
    "default_model": "settings-selected-model",
    "priority": 1,
    "reasoning_effort": "high",
    "reasoning_summary": "auto",
    "prompt_cache_retention": "24h",
    "enable_1m_context": "false",
    "enable_long_context": True,
}


class OfflineImageMetadata:
    """Real capability metadata; model availability is an offline fixture."""

    def __init__(self, config):
        self.provider = OpenAIProvider(api_key="fixture", config={})

    def get_image_generation_info(self):
        return self.provider.get_image_generation_info()

    async def list_image_models(self):
        return [{"id": "offline-image-model"}]

    async def close(self):
        assert self.provider._client is None


def provider(config, module="provider-unlisted-family"):
    return {"module": module, "id": "arbitrary-family", "config": copy.deepcopy(config)}


def save_provider(service, sid, config):
    path = service.data_dir / "sessions" / sid / "effective-configuration.json"
    plan = json.loads(path.read_text())
    plan["providers"] = [provider(config)]
    path.write_text(json.dumps(plan))
    return path


async def assert_refused(client, headers, base, service, runtime, path):
    before = path.read_bytes()
    sessions = copy.deepcopy(service.state["sessions"])
    count = service.db.execute("SELECT COUNT(*) FROM external_requests").fetchone()[0]
    response = await client.post(base, headers=headers, json=BODY)
    assert response.status == 409, await response.text()
    message = await response.text()
    assert "response-only" in message
    assert CANARY not in message and "vs_private_canary" not in message
    assert (
        service.db.execute("SELECT COUNT(*) FROM external_requests").fetchone()[0]
        == count
    )
    assert service.state["sessions"] == sessions
    assert not runtime.sent and not runtime.sessions
    assert path.read_bytes() == before  # No stripping or config rewrite.


@pytest.mark.parametrize(
    "config",
    [
        {"extra_request_params": OVERRIDES},
        {"extra_request_params": {"extra_body": OVERRIDES}},
        {"extra_request_params": {"temperature": 0}},  # Opaque, even if benign today.
        {"extra_request_params": "${EXTERNAL_OVERRIDE}"},
        {"extra_body": {"tools": OVERRIDES["tools"]}},
        {"tools": OVERRIDES["tools"]},
        {"file_search": {"vector_store_ids": ["vs_private_canary"]}},
        {"retrieval": True},
        {"previous_response_id": "resp_private_canary"},
        {"conversation": "private-conversation"},
        {"instructions": CANARY},
        {"input": CANARY},
        {"messages": [{"role": "system", "content": CANARY}]},
        {"tool_search": {"mode": "enabled"}},
        {"raw": True},
        {"auto_continue": True},
        {"reasoning_context": CANARY},
        {"reasoning_replay_scope": "conversation"},
        {"unknown_future_override": 1},
        {"base_url": {"opaque": CANARY}},
        {"temperature": {"opaque": CANARY}},
        {"enable_long_context": {"opaque": CANARY}},
    ],
)
async def test_unsafe_provider_config_refused_before_reservation(
    aiohttp_client, tmp_path, config
):
    app, client, headers, _, sid, base, runtime, _ = await setup(
        aiohttp_client, tmp_path
    )
    service = app["service"]
    path = save_provider(service, sid, config)
    await assert_refused(client, headers, base, service, runtime, path)


async def test_real_locked_openai_assembler_reproduces_canary_and_ingress_refuses_it(
    aiohttp_client, tmp_path, monkeypatch
):
    # No importorskip or simulated assembler: this must exercise uv.lock's code.
    locked = tomllib.loads((Path(__file__).parents[1] / "uv.lock").read_text())
    source = next(
        p["source"]["git"]
        for p in locked["package"]
        if p["name"] == "amplifier-module-provider-openai"
    )
    distribution = importlib.metadata.distribution("amplifier-module-provider-openai")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert direct["vcs_info"]["commit_id"] == source.split("#")[-1]

    def no_network(*args, **kwargs):
        raise AssertionError(
            "The real-assembler regression must not contact a provider"
        )

    original_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", no_network)
    request = ChatRequest(
        model="gpt-5.6-terra",
        messages=[
            Message(role="system", content="Response-only instruction"),
            Message(role="user", content="Fresh external input"),
        ],
        tools=[],
    )
    ordinary = OpenAIProvider(
        api_key="fixture", config={"default_model": request.model}
    )
    baseline, _, _ = ordinary._assemble_initial_responses_params(request)
    unsafe_config = {
        "default_model": request.model,
        "extra_request_params": copy.deepcopy(OVERRIDES),
    }
    unsafe = OpenAIProvider(api_key="fixture", config=unsafe_config)
    assembled, _, _ = unsafe._assemble_initial_responses_params(request)
    assert all(assembled[key] == value for key, value in OVERRIDES.items())
    assert baseline["instructions"] != CANARY
    assert baseline["input"] != OVERRIDES["input"]
    assert "previous_response_id" not in baseline
    assert not baseline.get("tools")
    assert ordinary._client is None and unsafe._client is None
    print(
        "Real locked OpenAI assembler reproduced tools/input/instructions/continuation override; no client/network."
    )
    # Allow loopback HTTP for the synthetic host after proving no provider I/O.
    monkeypatch.setattr(socket.socket, "connect", original_connect)
    app, client, headers, _, sid, base, runtime, _ = await setup(
        aiohttp_client, tmp_path
    )
    service = app["service"]
    path = save_provider(service, sid, unsafe_config)
    await assert_refused(client, headers, base, service, runtime, path)
    print(
        "Unsafe provider config refused with 409 before reservation or runtime dispatch; settings unchanged."
    )


async def test_ordinary_current_provider_shape_preserves_selection_and_wire_settings(
    aiohttp_client, tmp_path
):
    app, client, headers, _, sid, base, runtime, _ = await setup(
        aiohttp_client, tmp_path
    )
    service = app["service"]
    path = save_provider(service, sid, ORDINARY)
    before = path.read_bytes()
    receipt = await submit(client, headers, base)
    await asyncio.wait_for(runtime.finished.wait(), 3)
    assert (
        service.external_requests.record(sid, BODY["requestId"])["status"]
        == "completed"
    )
    assert runtime.sessions[0]["selection"] == {
        "instance": "arbitrary-family",
        "model": "selected",
    }
    inherited = (
        service.data_dir
        / "sessions"
        / receipt["executionSessionId"]
        / "configuration.json"
    )
    assert json.loads(inherited.read_text())["providers"] == [provider(ORDINARY)]
    assert path.read_bytes() == before


def test_allowlist_is_roster_agnostic_and_rejects_unsafe_unselected_provider():
    rows = [
        provider(ORDINARY, module=name)
        for name in ("provider-openai", "provider-other", "not-a-known-roster")
    ]
    original = copy.deepcopy(rows)
    validate_external_providers(rows)
    assert rows == original
    rows.append(provider({"extra_request_params": OVERRIDES}))
    with pytest.raises(ValueError, match="response-only"):
        validate_external_providers(rows)


@pytest.mark.parametrize(
    "rows",
    [
        None,
        {},
        ["provider-opaque"],
        [{"config": CANARY}],
        [{"config": {}, "opaque_constructor_options": OVERRIDES}],
    ],
)
def test_opaque_provider_rows_are_not_admitted(rows):
    with pytest.raises(ValueError, match="response-only"):
        validate_external_providers(rows)


def test_empty_override_and_exact_credential_binding_are_proven_noops():
    rows = [provider({"api_key": HOST_CREDENTIAL, "extra_request_params": {}})]
    before = copy.deepcopy(rows)
    validate_external_providers(rows)
    assert rows == before


@pytest.mark.parametrize("stage", ["composed", "materialized"])
async def test_worker_rechecks_provider_safety_before_mounting(
    mounted_host, monkeypatch, stage
):
    from amplifier_web.host import session as host

    h = mounted_host
    unsafe = [provider({"extra_request_params": OVERRIDES})]
    h.loaded.providers = unsafe if stage == "composed" else []
    if stage == "materialized":

        async def introduce_override(*args, **kwargs):
            h.prepared.mount_plan["providers"] = unsafe

        monkeypatch.setattr(host, "materialize_bundle_providers", introduce_override)
    with pytest.raises(ValueError, match="response-only"):
        await host.prepare_manager(
            h.config.workspace, runtime=h.runtime, external_request=True
        )
    h.prepared.create_session.assert_not_awaited()


async def test_host_image_defaults_metadata_is_admitted_without_changing_chat_payload(
    aiohttp_client, tmp_path, monkeypatch
):
    def no_network(*args, **kwargs):
        raise AssertionError("Image metadata composition must not contact a provider")

    plan = {"providers": [provider(ORDINARY)]}
    original = copy.deepcopy(plan)
    with monkeypatch.context() as offline:
        offline.setattr(socket.socket, "connect", no_network)
        chosen = await apply_defaults(
            plan, {}, class_loader=lambda _: OfflineImageMetadata
        )
        assert chosen == "arbitrary-family"
        metadata = plan["providers"][0]["config"]["image_generation"]
        assert metadata == {"enabled": True, "id": "images", "model": "auto"}
        request = ChatRequest(
            model="gpt-5.6-terra",
            messages=[Message(role="user", content="Fresh external request")],
            tools=[],
        )
        baseline = OpenAIProvider(api_key="fixture", config={})
        with_metadata = OpenAIProvider(
            api_key="fixture", config={"image_generation": copy.deepcopy(metadata)}
        )
        baseline_params, _, _ = baseline._assemble_initial_responses_params(request)
        metadata_params, _, _ = with_metadata._assemble_initial_responses_params(
            request
        )
        assert metadata_params == baseline_params
        assert not metadata_params.get("tools")
        assert "image_generation" not in metadata_params
        assert "previous_response_id" not in metadata_params
        assert baseline._client is None and with_metadata._client is None
    rows_before = copy.deepcopy(plan["providers"])
    validate_external_providers(plan["providers"])
    assert plan["providers"] == rows_before
    # Only the actual host metadata differs from the precomposition plan.
    without_metadata = copy.deepcopy(plan)
    del without_metadata["providers"][0]["config"]["image_generation"]
    assert without_metadata == original
    app, client, headers, _, sid, base, runtime, _ = await setup(
        aiohttp_client, tmp_path
    )
    path = save_provider(app["service"], sid, rows_before[0]["config"])
    before = path.read_bytes()
    receipt = await submit(client, headers, base)
    await asyncio.wait_for(runtime.finished.wait(), 3)
    inherited = (
        app["service"].data_dir
        / "sessions"
        / receipt["executionSessionId"]
        / "configuration.json"
    )
    assert json.loads(inherited.read_text())["providers"] == rows_before
    assert path.read_bytes() == before
    assert runtime.sessions[0]["selection"] == {
        "instance": "arbitrary-family",
        "model": "selected",
    }


@pytest.mark.parametrize(
    "image",
    [
        {},
        {"enabled": True},
        {"enabled": True, "id": "images"},
        {"enabled": 1, "id": "images", "model": "auto"},
        {"enabled": "true", "id": "images", "model": "auto"},
        {"enabled": True, "id": "aliased", "model": "auto"},
        {"enabled": True, "id": {"input": CANARY}, "model": "auto"},
        {"enabled": True, "id": "images", "model": {"instructions": CANARY}},
        {"enabled": True, "id": "images", "model": ["auto"]},
        {"enabled": True, "id": "images", "model": "auto\n" + CANARY},
        {"enabled": True, "id": "images", "model": ""},
        {"enabled": True, "id": "images", "model": "${OPAQUE_IMAGE_PARAMS}"},
        {
            "enabled": True,
            "id": "images",
            "model": "auto",
            "extra_request_params": OVERRIDES,
        },
        {"enabled": True, "id": "images", "model": "auto", "extra_body": OVERRIDES},
        {"enabled": True, "id": "images", "model": "auto", "tools": OVERRIDES["tools"]},
        {
            "enabled": True,
            "id": "images",
            "model": "auto",
            "request_overrides": OVERRIDES,
        },
    ],
)
async def test_image_metadata_rejects_nested_or_aliased_overrides_before_dispatch(
    aiohttp_client, tmp_path, image
):
    app, client, headers, _, sid, base, runtime, _ = await setup(
        aiohttp_client, tmp_path
    )
    service = app["service"]
    path = save_provider(service, sid, {**ORDINARY, "image_generation": image})
    await assert_refused(client, headers, base, service, runtime, path)


@pytest.mark.parametrize(
    "config",
    [
        {
            "image_generation": {"enabled": True, "id": "images", "model": "auto"},
            "extra_request_params": OVERRIDES,
        },
        {
            "extra_request_params": {
                "image_generation": {"enabled": True, "id": "images", "model": "auto"}
            }
        },
        {
            "extra_body": {
                "image_generation": {"enabled": True, "id": "images", "model": "auto"}
            }
        },
        {"imageGeneration": {"enabled": True, "id": "images", "model": "auto"}},
        {
            "metadata": {
                "image_generation": {"enabled": True, "id": "images", "model": "auto"}
            }
        },
    ],
)
def test_image_metadata_rule_is_not_recursive_or_an_override_alias(config):
    with pytest.raises(ValueError, match="response-only"):
        validate_external_providers([provider(config)])


@pytest.mark.parametrize("enabled", [True, False])
def test_exact_image_metadata_shape_is_roster_agnostic(enabled):
    rows = [
        provider(
            {"image_generation": {"enabled": enabled, "id": "images", "model": "auto"}},
            module=name,
        )
        for name in ("provider-openai", "provider-unlisted-family")
    ]
    before = copy.deepcopy(rows)
    validate_external_providers(rows)
    assert rows == before


async def test_target_effective_plan_accepted_read_only_after_host_image_composition(
    monkeypatch,
):
    # Explicit opt-in lets an operator verify the actual deployment plan without
    # checking private provider settings/credentials into a test fixture.
    target = os.environ.get("EXTERNAL_TARGET_EFFECTIVE_PLAN")
    if not target:
        pytest.skip("Set EXTERNAL_TARGET_EFFECTIVE_PLAN for the read-only target check")
    path = Path(target)
    before = path.read_bytes()
    rows = json.loads(before)["providers"]
    original = copy.deepcopy(rows)
    metadata = [
        r["config"]["image_generation"]
        for r in rows
        if "image_generation" in r.get("config", {})
    ]
    assert metadata and all(set(m) == {"enabled", "id", "model"} for m in metadata)
    validate_external_providers(rows)
    assert rows == original
    # Recompose an in-memory copy through the real host path. Availability is
    # offline and only capability metadata is read from the installed provider.
    plan = {"providers": copy.deepcopy(rows)}
    for row in plan["providers"]:
        row["config"].pop("image_generation", None)

    def no_network(*args, **kwargs):
        raise AssertionError("Target validation must not contact provider endpoints")

    with monkeypatch.context() as offline:
        offline.setattr(socket.socket, "connect", no_network)
        await apply_defaults(plan, {}, class_loader=lambda _: OfflineImageMetadata)
    validate_external_providers(plan["providers"])
    assert plan["providers"] == original
    assert path.read_bytes() == before
    print(
        f"Actual target: {len(rows)} provider rows accepted; host image metadata recomposed offline; source bytes unchanged."
    )
