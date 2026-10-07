"""Agent coordination receipts, not live model-efficacy or token-budget proof.

Byte comparisons reconstruct the former bridge wrapper from the *actual*
dispatch receipt and an explicit app_bridge get_state overview at the same
revision. They do not execute old private source or substitute the unbounded
service state for the overview agents previously received.
"""
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_operations.coordination import fingerprint
from amplifier_web import agent_canvas
from amplifier_web.automatic_history import directory, display_identity
from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.service import AppError
from test_collaborative_workspaces import (
    app,
    agent_action,
    declared_result,
    finish,
    generation,
    settled,
)


def json_bytes(value):
    """One deterministic UTF-8 encoding for both sides; not a token estimate."""
    return len(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def without_state(value):
    return {key: item for key, item in value.items() if key != "state"}


@pytest.fixture
def bridge_probe(app, monkeypatch):
    """Observe real dispatch and the expensive agent snapshot, never replace them."""
    calls = []
    dispatch = app.dispatch
    canvas_state = Mock(wraps=agent_canvas.state)
    monkeypatch.setattr(agent_canvas, "state", canvas_state)

    async def observed(action, args=None, *positional, **options):
        call = {
            "action": action, "args": copy.deepcopy(args), "options": options,
            "principal": PRINCIPAL.get(), "binding": copy.deepcopy(BINDING.get()),
            "client": app.clients.current.get(),
        }
        calls.append(call)
        result = await dispatch(action, args, *positional, **options)
        call["result"] = copy.deepcopy(result)
        return result

    monkeypatch.setattr(app, "dispatch", observed)
    return SimpleNamespace(calls=calls, canvas_state=canvas_state)


def assert_compact(response, raw, source, revision):
    # Compare all fields, including absent/present effects, not just "result".
    assert "state" not in raw
    assert without_state(response) == without_state(raw)
    assert set(response["state"]) == {"revision", "sessionId", "_stateAccess"}
    assert response["state"]["revision"] == revision
    assert response["state"]["sessionId"] == source["id"]
    assert set(response["state"]["_stateAccess"]) == {"note"}
    note = response["state"]["_stateAccess"]["note"]
    assert "full action result" in note and "get_state" in note and "JSON Pointer" in note
    assert json_bytes(response["state"]) < 512


async def compact_call(app, probe, source, action, args, identity, *, small=True):
    before = len(probe.calls)
    snapshots = probe.canvas_state.call_count
    response = await agent_action(app, source, action, args, identity)
    calls = [call for call in probe.calls[before:]
             if call["action"] == action and call["options"].get("command_id") == identity]
    assert len(calls) == 1
    call = calls[0]
    assert call["options"]["include_state"] is False
    assert call["options"]["origin"] == "agent"
    assert call["options"]["caller_session_id"] == source["id"]
    assert call["principal"] == (
        source.get("runtimeSessionId") or source.get("nativeIdentity") or source["id"]
    )
    assert call["binding"]["_generationId"] == source["collaborationGeneration"]["id"]
    assert probe.canvas_state.call_count == snapshots
    assert_compact(response, call["result"], source, app.state["revision"])
    if small:
        assert json_bytes(response) < 10_000
    return response, call["result"]


async def measure_pair(app, source, response, raw, record_property, label):
    overview = await app.app_bridge("get_state", {}, source["id"])
    assert overview["revision"] == response["state"]["revision"]
    # Exactly the old wrapper's effects projection and explicit scoped overview.
    old = {
        **raw,
        "effects": [{"id": effect.get("id"), "type": effect.get("type")}
                    for effect in raw.get("effects", [])],
        "state": overview,
    }
    old_bytes, new_bytes = json_bytes(old), json_bytes(response)
    record_property(label + "_old_projected_json_bytes", old_bytes)
    record_property(label + "_compact_json_bytes", new_bytes)
    record_property(label + "_saved_json_bytes", old_bytes - new_bytes)
    assert old_bytes > new_bytes
    return old_bytes, new_bytes


async def checkpoint_terminal(app, target, request, **patch):
    """Small real saved native row with qualified host terminal evidence."""
    text = "Candidate retained"
    target.update(nativeProject="fixture", nativeIdentity=target["id"])
    path = directory(target)
    path.mkdir(parents=True, exist_ok=True)
    rows = [
        {"role": "user", "content": "Check the interface dependency",
         "metadata": {"amplifier_input": {"version": 1, "kind": "user", "id": request}}},
        {"role": "assistant", "content": text},
    ]
    (path / "transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )
    anchor = {
        "messageId": display_identity(target, 1, "assistant", text),
        "nativeIndex": 1, "nativeText": text, "textDigest": fingerprint(text),
        "rootSessionId": target["id"], "generationId": "recipient-" + request,
    }
    await app.on_runtime_event("runtime.collaboration_checkpoint", {
        "sessionId": target["id"], "rootSessionId": target["id"],
        "generation_id": "recipient-" + request, "messageAnchors": [anchor],
    })
    await finish(app, target, request, nativeTerminal=anchor, **patch)
    return anchor


async def test_transport_bound_protocol_returns_are_small_lossless_and_wake_once(
    app, bridge_probe, record_property,
):
    source, target = app.state["sessions"]
    selection, draft = app.state["selectedSessionId"], app.state["view"]["draft"]
    context, raw = await compact_call(app, bridge_probe, source, "coordination.context",
                                      {"sessionId": source["id"]}, "context")
    assert context["result"]["workspace"] == source["workspace"]
    await measure_pair(app, source, context, raw, record_property, "context")
    listing, _ = await compact_call(app, bridge_probe, source, "coordination.list",
                                    {"limit": 2}, "list")
    assert {row["target"]["sessionId"] for row in listing["result"]["items"]} == {
        source["id"], target["id"],
    }
    request = "compact-request"
    sent, _ = await compact_call(app, bridge_probe, source, "coordination.send", {
        "sessionId": target["id"], "text": "Check the interface dependency", "mode": "queue",
    }, request)
    assert sent["requestId"] == sent["inputId"] == request
    assert sent["sourceGenerationId"] == "source-generation"
    assert sent["sourceInputIds"] == ["incoming-peer"]
    await settled(app, request, "accepted")
    await generation(app, target, "recipient-" + request, [request])
    subscribed, _ = await compact_call(app, bridge_probe, source, "coordination.subscribe", {
        "sessionId": source["id"], "requestId": request,
    }, "subscribe")
    reference = "candidate.txt@sha256:fixture"
    reply, _ = await compact_call(app, bridge_probe, target, "coordination.reply", {
        "requestId": request, "kind": "result", "outcome": "success",
        "text": "Checked candidate claim", "references": [reference],
    }, "reply")
    assert reply["result"]["status"] == "staged" and not reply["result"]["qualified"]
    anchor = await checkpoint_terminal(app, target, request)
    result, raw = await compact_call(app, bridge_probe, source, "coordination.result",
                                    {"requestId": request}, "result")
    domain = result["result"]
    assert domain["qualified"]
    assert domain["receipt"]["inputId"] == request
    assert domain["receipt"]["delivery"] == "accepted"
    assert domain["receipt"]["admission"]["accepted"] is True
    link, = domain["results"]
    assert link["inputId"] == request and link["sessionId"] == target["id"]
    assert link["messageId"] == anchor["messageId"]
    declaration = link["declaration"]
    assert declaration["requestId"] == request
    assert declaration["recipientSessionId"] == target["id"]
    assert declaration["generationId"] == anchor["generationId"]
    assert declaration["nativeTerminal"] == anchor
    assert declaration["references"] == [reference]
    assert declaration["status"] == "sealed"
    assert declaration["independentArtifactVerification"] is False
    await measure_pair(app, source, result, raw, record_property, "qualified_result")
    read, _ = await compact_call(app, bridge_probe, source, "coordination.read", {
        "sessionId": target["id"], "messageId": link["messageId"],
    }, "read")
    message = read["result"]["message"]
    assert link["messageId"] in {message["id"], message.get("nativeMessageId")}
    assert message["nativeIndex"] == anchor["nativeIndex"]
    assert message["generationId"] == anchor["generationId"]
    assert message["text"] == anchor["nativeText"] and not message["truncated"]
    continuation = subscribed["result"]["continuationId"]
    assert app.collaboration.receipt(continuation)["dependencyRequestId"] == request
    assert app.collaboration.receipt(continuation)["delivery"] == "queued"
    # Repeated terminal events and reads cannot enqueue or execute a second wake.
    await finish(app, target, request, nativeTerminal=anchor)
    await compact_call(app, bridge_probe, source, "coordination.result",
                       {"requestId": request}, "result-again")
    source["status"] = "idle"
    await app.collaboration.drain(source["id"])
    await settled(app, continuation, "accepted")
    await app.collaboration.drain(source["id"])
    assert [identity for _, identity, _ in app.runtime.inputs] == [request, continuation]
    assert app.db.execute("SELECT COUNT(*) FROM commands WHERE id=?", (continuation,)).fetchone()[0] == 1
    assert app.state["selectedSessionId"] == selection and app.state["view"]["draft"] == draft


async def test_create_receipt_preserves_inheritance_and_initial_input_link(app, bridge_probe):
    source = app.state["sessions"][0]
    path = app.data_dir / "sessions" / source["id"]
    path.mkdir(parents=True, exist_ok=True)
    (path / "effective-configuration.json").write_text(
        json.dumps({"providers": [], "tools": []}), encoding="utf-8",
    )
    created, _ = await compact_call(app, bridge_probe, source, "coordination.create", {
        "title": "Compact task", "text": "Checked brief",
        "references": ["brief.txt@sha256:fixture"],
    }, "compact-create")
    assert created["delivery"] == "creation_pending"
    assert created["requestId"] == "compact-create"
    assert created["initialInputId"] == "compact-create:brief"
    assert created["result"]["creatorSessionId"] == source["id"]
    assert created["result"]["sourceGenerationId"] == "source-generation"
    assert created["result"]["configurationHash"]
    assert created["result"]["references"] == ["brief.txt@sha256:fixture"]
    await settled(app, "compact-create", "created")
    await settled(app, created["initialInputId"], "accepted")
    assert [identity for _, identity, _ in app.runtime.inputs] == [created["initialInputId"]]
    assert app.state["selectedSessionId"] == source["id"]


@pytest.mark.parametrize("action", ["coordination.result", "coordination.read"])
async def test_unrelated_catalog_messages_and_workspace_growth_do_not_grow_returns(
    app, bridge_probe, record_property, action,
):
    source, target = app.state["sessions"]
    request = "growth-request"
    await declared_result(app, request=request)
    anchor = await checkpoint_terminal(app, target, request)
    args = ({"requestId": request} if action == "coordination.result" else
            {"sessionId": target["id"], "messageId": anchor["messageId"]})
    before, raw = await compact_call(app, bridge_probe, source, action, args, "growth-before")
    await measure_pair(app, source, before, raw, record_property, "before_growth")
    # Change only unrelated state. No publish/revision change or request mutation.
    marker = "UNRELATED_OVERVIEW_"
    baseline_state_bytes = json_bytes(app.get_state())
    source["messages"].extend({
        "id": f"unrelated-message-{index}", "role": "assistant", "text": marker * 2000,
        "createdAt": index,
    } for index in range(200))
    app.state["smartTools"]["catalog"] = [
        {"id": f"catalog-{index}", "description": marker * 2000} for index in range(200)
    ]
    app.state["workspaces"].extend({
        "id": f"workspace-{index}", "path": str(app.data_dir / f"unrelated-{index}"),
        "name": marker * 1000, "available": True,
    } for index in range(100))
    inflated_state_bytes = json_bytes(app.get_state())
    assert inflated_state_bytes - baseline_state_bytes > 10_000_000
    after, raw = await compact_call(app, bridge_probe, source, action, args, "growth-after")
    assert after == before
    assert json_bytes(after) == json_bytes(before)
    assert marker not in json.dumps(after)
    await measure_pair(app, source, after, raw, record_property, "after_growth")
    record_property("unrelated_state_growth_json_bytes", inflated_state_bytes - baseline_state_bytes)
    record_property("coordination_return_growth_json_bytes", json_bytes(after) - json_bytes(before))


async def test_large_legitimate_result_is_not_compacted_or_truncated(app, bridge_probe, record_property):
    source, target = app.state["sessions"]
    # Within the existing declaration schema: sixteen long, distinct references.
    references = [f"candidate-{index}-" + "é" * 1900 for index in range(16)]
    await declared_result(app, request="large-result", refs=references)
    await finish(app, target, "large-result")
    result, raw = await compact_call(app, bridge_probe, source, "coordination.result",
                                    {"requestId": "large-result"}, "large-read", small=False)
    assert result["result"]["qualified"]
    assert result["result"]["receipt"]["response"]["references"] == references
    assert result["result"]["results"][0]["declaration"]["references"] == references
    assert json_bytes(result) > 100_000
    assert json_bytes(without_state(result)) == json_bytes(without_state(raw))
    await measure_pair(app, source, result, raw, record_property, "large_legitimate_result")


async def test_uncertain_admission_duplicate_and_conflict_do_not_replay_input(app, bridge_probe):
    source, target = app.state["sessions"]
    app.runtime.fail = True
    request = "uncertain-request"
    args = {"sessionId": target["id"], "text": "Retained input", "mode": "queue"}
    await compact_call(app, bridge_probe, source, "coordination.send", args, request)
    saved = await settled(app, request, "unknown")
    result, _ = await compact_call(app, bridge_probe, source, "coordination.result",
                                  {"requestId": request}, "uncertain-result")
    assert result["result"]["receipt"] == saved
    assert result["result"]["receipt"]["admission"] == {
        "reason": "Admission outcome unknown; no replay.",
    }
    assert not result["result"]["qualified"] and result["result"]["results"] == []
    duplicate, _ = await compact_call(app, bridge_probe, source, "coordination.send", args, request)
    assert duplicate["duplicate"] is True and duplicate["delivery"] == "unknown"
    assert {key: item for key, item in without_state(duplicate).items()
            if key != "duplicate"} == saved
    with pytest.raises(AppError, match="different contents") as error:
        await agent_action(app, source, "coordination.send", {**args, "text": "Changed"}, request)
    assert error.value.status == 409
    assert app.collaboration.receipt(request) == saved
    assert [identity for _, identity, _ in app.runtime.inputs] == [request]
    assert len(target["messages"]) == 1
    bridge_probe.canvas_state.assert_not_called()


@pytest.mark.parametrize("failure", ["generation", "declared_outcome"])
async def test_terminal_failure_information_and_no_automatic_success_survive(
    app, bridge_probe, failure,
):
    source, target = app.state["sessions"]
    request = "failed-request"
    await declared_result(app, request=request,
                          outcome="failed" if failure == "declared_outcome" else "success")
    wait, _ = await compact_call(app, bridge_probe, source, "coordination.subscribe",
                                {"sessionId": source["id"], "requestId": request}, "failed-wait")
    await finish(app, target, request, **(
        {"event": "generation.failed", "error_type": "ContextLengthError"} if failure == "generation" else {}
    ))
    result, _ = await compact_call(app, bridge_probe, source, "coordination.result",
                                  {"requestId": request}, "failed-result")
    assert not result["result"]["qualified"] and result["result"]["results"] == []
    response = result["result"]["receipt"]["response"]
    assert response["requestId"] == request and response["generationId"] == "recipient-" + request
    assert response["references"] == ["candidate.txt@sha256:fixture"]
    if failure == "generation":
        assert response["status"] == "rejected"
        assert response["detail"] == "Terminal evidence or exact linkage failed."
    else:
        assert response["status"] == "sealed" and response["outcome"] == "failed"
        assert response["nativeTerminal"]["generationId"] == response["generationId"]
    assert not app.db.execute(
        "SELECT 1 FROM commands WHERE id=?", (wait["result"]["continuationId"],),
    ).fetchone()
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize("failure", ["stale_generation", "child", "workspace", "foreign_context", "missing_request"])
async def test_bridge_errors_restore_transport_and_browser_context_without_effects(
    app, bridge_probe, failure,
):
    source, target = app.state["sessions"]
    transport = {
        "_runtimeSessionId": source["id"], "_generationId": "source-generation",
        "_inputBindings": [{"inputId": "incoming-peer", "clientId": None}],
        "_inputClients": [],
    }
    action = "coordination.send"
    args = {"sessionId": target["id"], "text": "Must not execute", "mode": "notify"}
    status = 403
    if failure == "stale_generation":
        transport["_generationId"] = "stale"
    elif failure == "child":
        transport["_runtimeSessionId"] = "actual-child"
    elif failure == "workspace":
        target["workspace"] = str(app.data_dir / "foreign-workspace")
    elif failure == "foreign_context":
        action, args = "coordination.context", {"sessionId": target["id"]}
    else:
        action, args, status = "coordination.result", {"requestId": "not-retained"}, 404
    envelope = {"action": action, "args": args, "id": "rejected", **transport}
    client = app.clients.attach("outer-browser")
    client["selectedSessionId"] = target["id"]
    client["view"]["draft"] = "Untouched browser draft"
    before_client = copy.deepcopy(client)
    before_messages = copy.deepcopy([source["messages"], target["messages"]])
    outer_binding = {"outer": "binding"}
    principal_token = PRINCIPAL.set("outer-principal")
    binding_token = BINDING.set(outer_binding)
    try:
        with app.clients.bind("outer-browser"):
            with pytest.raises(AppError) as error:
                await app.app_bridge("dispatch", envelope, source["id"])
            assert error.value.status == status
            assert PRINCIPAL.get() == "outer-principal"
            assert BINDING.get() is outer_binding
            assert app.clients.current.get() == "outer-browser"
            # A successful compact return resets the same contexts as an error.
            success = {
                **envelope, "action": "coordination.context",
                "args": {"sessionId": source["id"]},
                "_runtimeSessionId": source["id"], "_generationId": "source-generation",
                "expectedRevision": app.state["revision"],
            }
            result = await app.app_bridge("dispatch", success, source["id"])
            call = bridge_probe.calls[-1]
            assert_compact(result, call["result"], source, app.state["revision"])
            assert call["principal"] == source["id"] and call["binding"] == success
            assert call["client"] is None and call["options"]["include_state"] is False
            assert call["options"]["command_id"] == success["id"]
            assert call["options"]["expected_revision"] == success["expectedRevision"]
            assert PRINCIPAL.get() == "outer-principal" and BINDING.get() is outer_binding
            assert app.clients.current.get() == "outer-browser"
    finally:
        PRINCIPAL.reset(principal_token)
        BINDING.reset(binding_token)
    assert client == before_client
    assert [source["messages"], target["messages"]] == before_messages
    assert not app.runtime.inputs
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='rejected'").fetchone()
    bridge_probe.canvas_state.assert_not_called()


async def test_noncoordination_bridge_ui_and_explicit_state_keep_existing_contract(app, bridge_probe):
    source = app.state["sessions"][0]
    response = await agent_action(app, source, "session.pin",
                                  {"id": source["id"], "pinned": True}, "pin")
    call = bridge_probe.calls[-1]
    assert call["options"]["include_state"] is True
    assert bridge_probe.canvas_state.call_count == 1
    assert without_state(response) == without_state(call["result"])
    explicit = await app.app_bridge("get_state", {}, source["id"])
    assert response["state"] == explicit
    assert explicit["session"]["id"] == source["id"] and explicit["session"]["pinned"]
    assert "conversations" in explicit and "sections" in explicit["_stateAccess"]
    pointer = await app.app_bridge("get_state", {"path": "/view/draft"}, source["id"])
    assert pointer["value"] == "Private unsent draft"
    ui = await app.dispatch("session.pin", {"id": source["id"], "pinned": False},
                            origin="ui", command_id="ui-pin")
    assert ui["accepted"] and ui["state"] == app.browser_state()
    assert ui["state"]["selectedSessionId"] == source["id"]
    assert app.get_state()["sessions"][0]["id"] == source["id"]
    assert not app.runtime.inputs


async def test_existing_smart_tool_receipt_and_read_guidance_are_unchanged(app, bridge_probe):
    from amplifier_web.smart_canvas import SmartCanvas
    from amplifier_web.smart_tools import SmartToolsManager

    app.smart_tools = SmartToolsManager(app)
    app.smart_canvas = SmartCanvas(app)
    source = app.state["sessions"][0]

    async def execute(action, args, origin="ui"):
        return {"tools": [{"name": "fixture-tool"}]}

    app.smart_tools.execute = execute
    receipt = await agent_action(app, source, "smartTools.discover",
                                 {"id": "fixture"}, "smart-discover")
    call = next(call for call in bridge_probe.calls if call["action"] == "smartTools.discover")
    assert call["options"]["include_state"] is False
    assert receipt["operationId"] == "smart-discover"
    assert receipt["read"] == {
        "action": "smartTools.readResult", "args": {"operationId": "smart-discover"},
    }
    assert receipt["effects"] == [
        {"id": effect.get("id"), "type": effect.get("type")}
        for effect in call["result"]["effects"]
    ]
    assert {key: item for key, item in without_state(receipt).items()
            if key not in {"effects", "read"}} == {
        key: item for key, item in without_state(call["result"]).items() if key != "effects"
    }
    expected_state = {
        "revision": app.state["revision"], "sessionId": source["id"],
        "_stateAccess": {"note": "Use smartTools.readResult with the receipt operationId to read status and results. Use get_state with a JSON Pointer for other app state."},
    }
    assert receipt["state"] == expected_state and json_bytes(receipt) < 1500
    await app.wait_smart_tool(receipt["operationId"])
    result = await agent_action(app, source, "smartTools.readResult",
                                {"operationId": receipt["operationId"]}, "smart-read")
    assert result["result"]["operation"]["result"] == {"tools": [{"name": "fixture-tool"}]}
    assert result["state"]["_stateAccess"] == expected_state["_stateAccess"]
    assert bridge_probe.calls[-1]["options"]["include_state"] is False
    bridge_probe.canvas_state.assert_not_called()