"""External export authority, single-input isolation and truthful recovery."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
import test_host_session_resume
from test_companion_authority import attach, create_chat, fixture
from test_service import Runtime

from amplifier_web.errors import AppError
from amplifier_web.external_requests import (
    ExternalRequests,
    admit_command,
    ceiling,
    digest,
)

# Register the shared pytest fixture without an unused/shadowed import.
mounted_host = test_host_session_resume.mounted_host

BODY = {
    "requestId": "slack:T1:Ev1",
    "source": {"kind": "slack", "teamId": "T1", "channelId": "C1", "threadTs": "1.2"},
    "text": "A prepared external request",
}


class ExactRuntime(Runtime):
    def __init__(self, *, hold=False, change=None, text="external answer"):
        super().__init__()
        self.entered, self.release, self.finished = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        self.sessions = []
        self.stop_seen = asyncio.Event()
        self.hold, self.change, self.text = hold, change or {}, text

    async def stop(self, sid, *, reason="stopped"):
        await super().stop(sid, reason=reason)
        self.stop_seen.set()

    async def send(self, session, text, input_id, emit, *, admission_check=None):
        if admission_check:
            admission_check()
        self.sessions.append(copy.deepcopy(session))
        self.sent.append((session["id"], text, input_id))
        sid = session["id"]
        self.entered.set()
        if self.hold:
            await self.release.wait()
        if self.change.get("raise"):
            raise RuntimeError("PRIVATE MODULE FAILURE MUST NOT EXPORT")
        await emit(
            "runtime.generation",
            {
                "sessionId": sid,
                "rootSessionId": sid,
                "event": "generation.started",
                "generation_id": "exact",
                "input_ids": [input_id],
            },
        )
        await emit(
            "assistant.message",
            {
                "sessionId": sid,
                "text": "PRIVATE INTERIM",
                "inputId": input_id,
                "generationId": "exact",
                "runtimeMessage": {"sequence": 1},
            },
        )
        await emit(
            "assistant.message",
            {
                "sessionId": sid,
                "text": self.text,
                "inputId": input_id,
                "generationId": "exact",
                "runtimeMessage": {"sequence": 2},
            },
        )
        await emit(
            "runtime.generation",
            {
                "sessionId": sid,
                "rootSessionId": sid,
                "event": "generation.finished",
                "generation_id": "exact",
                "input_ids": [input_id],
                "active_job_ids": [],
                "disposition": "manager_turn_finished",
                "finalMessageRefs": {
                    "version": 1,
                    "generationId": "exact",
                    "sequences": [2],
                },
                **self.change,
            },
        )
        await emit("runtime.status", {"sessionId": sid, "status": "idle"})
        self.finished.set()


async def setup(aiohttp_client, tmp_path, runtime=None, *, bind=True):
    runtime = runtime or ExactRuntime()
    app, client, headers, device = await fixture(aiohttp_client, tmp_path, runtime)
    await attach(client, headers)
    headers["X-Amplifier-Client"] = "native-review"
    owner = {"Authorization": "Bearer " + app["control_token"]}
    sid, _ = await create_chat(client, owner)
    service = app["service"]
    directory = service.data_dir / "sessions" / sid
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "effective-configuration.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "module": "provider-synthetic",
                        "id": "arbitrary-family",
                        "config": {"model": "settings-selected-model"},
                    }
                ],
                "tools": [{"module": "tool-bash"}],
                "hooks": [{"module": "hooks-private-history"}],
                "session": {
                    "orchestrator": {"module": "loop-streaming"},
                    "context": {"module": "context-simple"},
                },
                "agents": {},
                "context": {},
                "instruction": "PRIVATE PARENT INSTRUCTION",
            }
        )
    )
    (directory / "control-state.json").write_text(
        json.dumps(
            {
                "mode": "private-mode",
                "configurator": {"dangerous": True},
                "selection": {"instance": "arbitrary-family", "model": "selected"},
            }
        )
    )
    base = f"/api/sessions/{sid}/external-requests"
    if bind:
        response = await client.post(
            base + "/bind", headers=owner, json={"deviceId": device["deviceID"]}
        )
        assert response.status == 200, await response.text()
    return app, client, headers, owner, sid, base, runtime, device


async def submit(client, headers, base, body=None):
    response = await client.post(base, headers=headers, json=body or BODY)
    assert response.status == 202, await response.text()
    return await response.json()


async def result(client, headers, base):
    response = await client.get(base + "/" + BODY["requestId"], headers=headers)
    assert response.status == 200, await response.text()
    return await response.json()


async def test_explicit_binding_and_narrow_authorization(aiohttp_client, tmp_path):
    _, client, headers, owner, _, base, runtime, device = await setup(
        aiohttp_client, tmp_path, bind=False
    )
    assert (await client.post(base, headers=headers, json=BODY)).status == 403
    assert (
        await client.post(
            base + "/bind", headers=headers, json={"deviceId": device["deviceID"]}
        )
    ).status == 403
    assert (
        await client.post(
            base + "/bind", headers=owner, json={"deviceId": device["deviceID"]}
        )
    ).status == 200
    assert (await client.post(base, headers=owner, json=BODY)).status == 403
    other, _ = await create_chat(client, owner)
    wrong = f"/api/sessions/{other}/external-requests"
    assert (await client.post(wrong, headers=headers, json=BODY)).status == 403
    assert (await client.get(wrong + "/slack:T1:Ev1", headers=headers)).status == 403
    assert (
        await client.post(
            wrong + "/bind", headers=owner, json={"deviceId": device["deviceID"]}
        )
    ).status == 409
    assert (
        await client.post(
            "/api/actions",
            headers=headers,
            json={
                "action": "coordination.create",
                "args": {"title": "widen", "text": "widen"},
            },
        )
    ).status == 403
    assert runtime.sent == []


async def test_concurrent_parent_canary_never_enters_hidden_execution_or_sealed_result(
    aiohttp_client, tmp_path
):
    runtime = ExactRuntime(hold=True)
    app, client, headers, _, sid, base, _, _ = await setup(
        aiohttp_client, tmp_path, runtime
    )
    service = app["service"]
    parent = service._session(sid)
    service._message(parent, "user", "PRIVATE BEFORE", "chat")
    selected = service.state["selectedSessionId"]
    first = await submit(client, headers, base)
    await asyncio.wait_for(runtime.entered.wait(), 3)
    assert first["executionSessionId"] != sid
    assert service.state["selectedSessionId"] == selected
    execution = service._session(first["executionSessionId"])
    assert (
        execution["sessionKind"] == "internal"
        and execution["sessionPurpose"] == "external-request"
    )
    assert not execution.get("forkContext") and not execution.get("runtimeSessionId")
    assert [m["text"] for m in runtime.sessions[0]["messages"]] == [BODY["text"]]
    assert runtime.sessions[0]["selection"] == {
        "instance": "arbitrary-family",
        "model": "selected",
    }
    service._message(parent, "user", "PRIVATE CONCURRENT LOCAL CANARY", "chat")
    service._message(parent, "assistant", "PRIVATE LOCAL ANSWER", "chat")
    waiting = await result(client, headers, base)
    assert waiting["status"] == "running" and waiting["result"] is None
    runtime.release.set()
    await asyncio.wait_for(runtime.finished.wait(), 3)
    sealed = await result(client, headers, base)
    assert sealed["status"] == "completed"
    assert sealed["result"]["text"] == "external answer"
    assert sealed["result"]["sha256"] == digest("external answer")
    assert sealed["result"]["inputIds"] == [first["inputId"]]
    assert sealed["result"]["generationId"] == "exact"
    assert len(sealed["result"]["finalMessageIds"]) == 1
    assert "PRIVATE" not in json.dumps(sealed)
    assert "credentialDigest" not in sealed and "text" not in sealed
    service._message(execution, "assistant", "LATE UNRELATED", "chat")
    assert await result(client, headers, base) == sealed
    assert [m["text"] for m in parent["messages"]] == [
        "PRIVATE BEFORE",
        "PRIVATE CONCURRENT LOCAL CANARY",
        "PRIVATE LOCAL ANSWER",
    ]
    with pytest.raises(AppError, match="one reserved input"):
        await service.dispatch(
            "conversation.send",
            {"sessionId": execution["id"], "text": "second"},
            include_state=False,
        )


async def test_idempotent_submission_conflict_and_fresh_event_identity(
    aiohttp_client, tmp_path
):
    _, client, headers, _, _, base, runtime, _ = await setup(aiohttp_client, tmp_path)
    first = await submit(client, headers, base)
    await asyncio.wait_for(runtime.finished.wait(), 3)
    repeated = await submit(client, headers, base)
    assert first["executionSessionId"] == repeated["executionSessionId"]
    assert len(runtime.sent) == 1
    response = await client.post(
        base, headers=headers, json={**BODY, "text": "different"}
    )
    assert response.status == 409
    response = await client.post(
        base, headers=headers, json={**BODY, "source": {"channelId": "OTHER"}}
    )
    assert response.status == 409
    second = await submit(client, headers, base, {**BODY, "requestId": "slack:T1:Ev2"})
    assert second["executionSessionId"] != first["executionSessionId"]


@pytest.mark.parametrize(
    "change",
    [
        {"input_ids": ["other", "mixed-local-input"]},
        {"finalMessageRefs": {}},
        {"event": "generation.failed"},
        {"active_job_ids": ["unsettled-child"]},
        {"rootSessionId": "parent-not-execution"},
    ],
)
async def test_never_export_unqualified_terminal(aiohttp_client, tmp_path, change):
    runtime = ExactRuntime(change=change)
    _, client, headers, _, _, base, _, _ = await setup(
        aiohttp_client, tmp_path, runtime
    )
    await submit(client, headers, base)
    await asyncio.wait_for(runtime.finished.wait(), 3)
    saved = await result(client, headers, base)
    assert saved["status"] != "completed" and saved["result"] is None
    assert "PRIVATE INTERIM" not in json.dumps(saved)


async def test_bounded_output_is_refused_not_truncated_or_guessed(
    aiohttp_client, tmp_path
):
    runtime = ExactRuntime(text="界" * 11000)
    _, client, headers, _, _, base, _, _ = await setup(
        aiohttp_client, tmp_path, runtime
    )
    await submit(client, headers, base)
    await asyncio.wait_for(runtime.finished.wait(), 3)
    saved = await result(client, headers, base)
    assert (
        saved["result"] is None
        and saved["reason"] == "final_output_exceeds_export_bound"
    )


async def test_parent_stop_fences_late_completion_and_interrupts_execution(
    aiohttp_client, tmp_path
):
    runtime = ExactRuntime(hold=True)
    app, client, headers, _, sid, base, _, _ = await setup(
        aiohttp_client, tmp_path, runtime
    )
    first = await submit(client, headers, base)
    await asyncio.wait_for(runtime.entered.wait(), 3)
    await app["service"].dispatch(
        "conversation.stop", {"sessionId": sid}, include_state=False
    )
    await asyncio.wait_for(runtime.stop_seen.wait(), 3)
    assert first["executionSessionId"] in runtime.stopped
    runtime.release.set()
    await asyncio.wait_for(runtime.finished.wait(), 3)
    saved = await result(client, headers, base)
    assert saved["result"] is None and saved["status"] == "blocked"


async def test_revocation_fences_model_admission_and_sealing(aiohttp_client, tmp_path):
    runtime = ExactRuntime(hold=True)
    app, client, headers, owner, sid, base, _, device = await setup(
        aiohttp_client, tmp_path, runtime
    )
    await submit(client, headers, base)
    await asyncio.wait_for(runtime.entered.wait(), 3)
    service = app["service"]
    service.external_requests.require_devices().revoke(device["deviceID"])
    record = service.external_requests.record(sid, BODY["requestId"])
    with pytest.raises(AppError, match="revoked"):
        service.external_requests.guard(record)
    runtime.release.set()
    await asyncio.wait_for(runtime.finished.wait(), 3)
    saved = await result(client, owner, base)
    assert saved["result"] is None and saved["status"] == "blocked"


async def test_restart_unknown_never_dispatches_again(aiohttp_client, tmp_path):
    runtime = ExactRuntime(hold=True)
    app, client, headers, _, _, base, _, _ = await setup(
        aiohttp_client, tmp_path, runtime
    )
    first = await submit(client, headers, base)
    await asyncio.wait_for(runtime.entered.wait(), 3)
    service = app["service"]
    # Reconstruct the durable owner as startup does. Construction/read/retry do
    # not recover an execution; the original process might still have effects.
    recovered = ExternalRequests(service)
    recovered.devices = service.external_requests.devices
    service.external_requests = recovered
    again = await submit(client, headers, base)
    assert again["status"] == "unknown"
    assert again["executionSessionId"] == first["executionSessionId"]
    assert len(runtime.sent) == 1 and recovered.active == {}
    runtime.release.set()
    await asyncio.wait_for(runtime.finished.wait(), 3)
    assert (await result(client, headers, base))["result"] is None


async def test_private_app_history_and_configuration_bridges_are_denied(
    aiohttp_client, tmp_path
):
    app, client, headers, _, _, base, runtime, _ = await setup(aiohttp_client, tmp_path)
    first = await submit(client, headers, base)
    await asyncio.wait_for(runtime.finished.wait(), 3)
    for operation in (
        "history",
        "get_state",
        "context.prepare",
        "outputs.image.read",
        "dispatch",
    ):
        with pytest.raises(AppError, match="no app, history or configuration"):
            await app["service"]._app_bridge(operation, {}, first["executionSessionId"])


async def test_finite_parent_budget_is_fail_closed_not_reset(aiohttp_client, tmp_path):
    app, client, headers, _, sid, base, runtime, _ = await setup(
        aiohttp_client, tmp_path
    )
    path = app["service"].data_dir / "sessions" / sid / "control-state.json"
    saved = json.loads(path.read_text())
    saved["capacity"] = {"enabled": True, "maxTotalTokens": 10}
    path.write_text(json.dumps(saved))
    response = await client.post(base, headers=headers, json=BODY)
    assert response.status == 409
    assert "finite parent capacity" in (await response.json())["error"]
    assert runtime.sent == []


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "selection": {"model": "other"}},
        {**BODY, "text": " "},
        {**BODY, "text": "x" * 100001},
        {**BODY, "source": {"context": ["not", "strings"]}},
        {**BODY, "requestId": "../escape"},
    ],
)
async def test_bounded_payload_rejects_authority_or_path_fields(
    aiohttp_client, tmp_path, body
):
    _, client, headers, _, _, base, runtime, _ = await setup(aiohttp_client, tmp_path)
    response = await client.post(base, headers=headers, json=body)
    assert response.status == 400
    assert runtime.sent == []


def test_capability_ceiling_preserves_provider_selection_not_private_capabilities():
    providers = [
        {
            "module": "provider-unlisted-family",
            "config": {"model": "configured", "base_url": "unchanged"},
        }
    ]
    loaded = SimpleNamespace(
        providers=copy.deepcopy(providers),
        session={"context": {"module": "context-simple"}},
        tools=["shell", "history", "memory"],
        hooks=["private-context"],
        agents={"child": {}},
        context={"secret": "PRIVATE"},
        instruction="PRIVATE",
        spawn={"tools": ["shell"]},
    )
    ceiling(loaded)
    assert loaded.providers == providers
    assert (
        loaded.tools == loaded.hooks == []
        and loaded.agents == loaded.context == loaded.spawn == {}
    )
    assert "PRIVATE" not in loaded.instruction


def test_native_input_ceiling_is_exact_single_input_even_after_unknown_submit():
    binding = {"inputId": "one", "textSha256": digest("exact")}
    worker = SimpleNamespace(start_config={"externalRequest": binding})
    for op, args in [
        ("retry", {"input_id": "one", "text": "exact"}),
        ("control", {"operation": "configuration.apply"}),
        ("send", {"input_id": "other", "text": "exact"}),
        ("send", {"input_id": "one", "text": "changed"}),
    ]:
        with pytest.raises(ValueError):
            admit_command(worker, op, args)
    admit_command(worker, "send", {"input_id": "one", "text": "exact"})
    with pytest.raises(ValueError):
        admit_command(worker, "send", {"input_id": "one", "text": "exact"})


async def test_host_mount_applies_ceiling_before_modules_or_saved_settings(
    mounted_host, monkeypatch
):
    import sys

    from amplifier_web.host import session as host

    h = mounted_host
    h.loaded.tools = [{"module": "tool-bash"}]
    h.loaded.context = {"private": "PARENT CANARY"}
    h.loaded.instruction = "PARENT CANARY"
    h.config.settings["configurator"] = {"tools": {"shell": True}}
    prepare = h.loaded.prepare

    async def checked_prepare(**kwargs):
        assert h.loaded.tools == h.loaded.hooks == []
        assert h.loaded.context == {} and h.loaded.agents == {}
        assert "PARENT CANARY" not in h.loaded.instruction
        return prepare.return_value

    prepare.side_effect = checked_prepare
    await host.prepare_manager(
        h.config.workspace, runtime=h.runtime, external_request=True
    )
    h.configurator.apply_saved_settings.assert_not_awaited()
    sys.modules["amplifier_web.host.children"].install_children.assert_not_awaited()
