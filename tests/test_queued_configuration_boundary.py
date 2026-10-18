"""Synthetic host/worker boundary regressions; not installed-worker qualification.

Management, Collaboration, ConfigurationAuthority and RuntimeControls are real.
The coordinator and process transport are fixture adapters. Events hold a real
await in runtime preparation; peer admission uses the real host command ledger.
No provider, subprocess, service, network, or personal configuration is used.
"""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest

from amplifier_web.management import Management
from amplifier_web.runtime_controls import RuntimeControls, override_path
from amplifier_web.service import AppError, AppService
from test_configuration_authority import finished, grant, revoke
from test_coordination_generation import agent


def plan(limit):
    return {
        "session": {"orchestrator": {"module": "loop-live"},
                    "context": {"module": "context-simple"}},
        "providers": [{"module": "provider-fixture"}],
        "tools": [{"module": "tool-fixture", "config": {"limit": limit}}],
    }


class ControlsTransport:
    """Only process creation/loading is simulated; mutation and idle checks aren't."""

    def __init__(self, home, sid):
        self.home, self.sid = home, sid
        self.native = SimpleNamespace(generation=None, queued_inputs=0)
        self.loop = SimpleNamespace(root_provider=None, jobs={})
        capabilities = {}
        self.coordinator = SimpleNamespace(
            config=plan(1), session_state={},
            get=lambda name: {"orchestrator": self.loop, "providers": {}}.get(name),
            get_capability=capabilities.get,
            register_capability=lambda name, value: capabilities.update({name: value}),
        )
        self.controls = RuntimeControls(
            SimpleNamespace(session_id=sid, coordinator=self.coordinator), self.native)
        assert override_path(sid).is_relative_to(home)
        self.controls.state_path().parent.mkdir(parents=True, exist_ok=True)
        if not self.controls.state_path().exists():
            self.controls.state_path().write_text(json.dumps({
                "configurator": {"disabled": {"tools": ["tool-fixture"]}},
                "selection": {"instance": "fixture", "model": "fixture-model"},
            }))
        self.original_controls = self.controls.state_path().read_bytes()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls, self.inputs = [], []
        self.block_start = True
        self.needs_mount = False
        self.fault = None

    async def start(self, session, emit, **kwargs):
        assert session["id"] == self.sid
        self.calls.append("start")
        if self.block_start:
            self.block_start = False
            self.entered.set()
            await self.release.wait()
        if kwargs.get("guard"):
            kwargs["guard"]()
        if self.needs_mount:
            # Model only the saved-vs-loaded distinction, not a native mount.
            self.coordinator.config = json.loads(override_path(self.sid).read_text())
            self.calls.append("fixture-mounted")
            self.needs_mount = False

    async def stop(self, sid, *, reason="stopped"):
        self.calls.append("stop:" + reason)
        if reason == "configuration":
            self.needs_mount = True
            if self.fault == "stop-after-save":
                raise RuntimeError("Synthetic retirement failure after settings save")

    async def control(self, sid, operation, args, *, guard=None):
        if guard is not None:
            guard()
        assert sid == self.sid
        self.calls.append(operation)
        if operation == "configuration.providers":
            return {"providers": []}  # Not a provider-catalog test.
        if operation == "configuration.apply" and self.fault == "lost-before-dispatch":
            raise RuntimeError("Synthetic transport lost: mutation outcome unknown")
        result = await self.controls.perform(operation, args)
        if operation == "configuration.apply":
            self.calls.append("saved")
            if self.fault == "lost-after-save":
                raise RuntimeError("Synthetic transport lost: mutation outcome unknown")
            if self.fault == "cancel-after-save":
                raise asyncio.CancelledError()
        return result

    async def collaboration_input(self, session, args, guard, emit):
        reason = guard()
        if reason:
            return {"accepted": False, "reason": reason}
        admission = self.app.collaboration.admission(session["id"], args)
        assert admission["admitted"], admission
        self.inputs.append(args["inputId"])
        self.native.queued_inputs += 1
        # Deliberately delay the status event: native admission may precede
        # the host's projection. This is not an all-success fake control.
        return {"accepted": True}

    async def close(self):
        self.release.set()
        await self.controls.close()


@pytest.fixture
async def boundary(tmp_path, monkeypatch):
    home = tmp_path / "synthetic-app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "synthetic-native"))
    app = AppService(home, None, workspace=tmp_path)
    runtime = None
    try:
        owner, peer = [app._new_session({"title": title}) for title in ("Owner", "Peer")]
        app.state["sessions"] = [owner, peer]
        app.state["selectedSessionId"] = owner["id"]
        for row in (owner, peer):
            row["collaborationGeneration"] = {
                "id": "generation-" + row["id"], "inputIds": ["synthetic-human"], "terminal": False}
        runtime = ControlsTransport(home, owner["id"])
        app.runtime = runtime
        runtime.app = app
        app._publish()
        app.management = Management(app)
        yield SimpleNamespace(app=app, owner=owner, peer=peer, runtime=runtime)
    finally:
        if runtime is not None:
            runtime.release.set()
        await app.close()


async def queue(boundary, identity="original-apply"):
    app, owner = boundary.app, boundary.owner
    boundary.authorized = await grant(app, ["runtime_configuration"], ["session"])
    owner["status"] = "working"
    boundary.args = {"id": owner["id"], "config": plan(2), "whenIdle": True}
    boundary.receipt = await agent(app, owner, "configuration.apply", boundary.args, identity)
    assert (await finished(app, identity))["phase"] == "ready"
    assert owner["pendingConfiguration"]["phase"] == "queued"
    assert not boundary.runtime.calls  # Receipt is not evidence of application.
    boundary.authority = copy.deepcopy(owner["pendingConfiguration"]["authority"])
    boundary.payload = app.management.queued_path(owner["id"]).read_bytes()
    boundary.queued_task = app.management.pending_config_tasks[owner["id"]]
    return boundary


async def reach_apply(boundary):
    boundary.owner["status"] = "idle"
    await asyncio.wait_for(boundary.runtime.entered.wait(), 3)
    assert boundary.owner["configurationBusy"]
    assert boundary.owner["pendingConfiguration"]["phase"] == "applying"


async def settle(boundary):
    boundary.runtime.release.set()
    await asyncio.wait_for(boundary.queued_task, 3)


async def scheduler_barrier():
    """Give constructor-scheduled tasks a bounded chance to enter, not a sleep."""
    entered = asyncio.Event()
    asyncio.get_running_loop().call_soon(entered.set)
    await asyncio.wait_for(entered.wait(), 1)


@pytest.mark.parametrize("busy", ["generation", "queued_inputs"])
async def test_real_native_guard_refuses_before_either_settings_write(boundary, busy):
    runtime = boundary.runtime
    setattr(runtime.native, busy, object() if busy == "generation" else 1)
    with pytest.raises(ValueError, match="Wait for the current response"):
        await runtime.controls.perform("configuration.apply", {"config": plan(2)})
    assert not override_path(runtime.sid).exists()
    assert runtime.controls.state_path().read_bytes() == runtime.original_controls
    assert runtime.coordinator.config == plan(1)


async def test_acceptance_save_and_loaded_readback_are_separate(boundary):
    await queue(boundary)
    assert not override_path(boundary.owner["id"]).exists()
    await reach_apply(boundary)
    assert boundary.runtime.coordinator.config == plan(1)
    await settle(boundary)
    assert boundary.owner["pendingConfiguration"]["phase"] == "ready"
    assert boundary.runtime.calls.index("saved") < boundary.runtime.calls.index("fixture-mounted")
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    expected_public = plan(2)
    expected_public["hooks"] = []
    for section in ("providers", "tools"):
        for row in expected_public[section]:
            row["enabled"] = True
    assert boundary.owner["configuration"]["plan"] == expected_public
    assert not boundary.app.management.queued_path(boundary.owner["id"]).exists()


async def test_later_generation_reuses_still_valid_original_authority(boundary):
    await queue(boundary)
    boundary.owner["collaborationGeneration"] = {
        "id": "later-generation", "inputIds": ["another-human-input"], "terminal": True}
    await reach_apply(boundary)
    await settle(boundary)
    assert boundary.owner["pendingConfiguration"]["phase"] == "ready"
    assert len(boundary.app.configuration_authority.rows(boundary.owner["id"])) == 1
    assert boundary.runtime.calls.count("configuration.apply") == 1


async def test_native_accepted_peer_is_not_erased_to_make_configuration_idle(boundary):
    app, owner, peer, runtime = boundary.app, boundary.owner, boundary.peer, boundary.runtime
    incoming = await agent(app, peer, "coordination.send",
                           {"sessionId": owner["id"], "text": "Already admitted input", "mode": "queue"},
                           "native-admitted-peer")
    await app.collaboration.drain(owner["id"])
    assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "accepted"
    assert runtime.native.queued_inputs == 1
    await queue(boundary)
    await reach_apply(boundary)
    await settle(boundary)
    assert not override_path(owner["id"]).exists()
    assert runtime.controls.state_path().read_bytes() == runtime.original_controls
    assert runtime.inputs == ["native-admitted-peer"]
    assert runtime.native.queued_inputs == 1
    assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "accepted"
    assert app.management.queued_path(owner["id"]).read_bytes() == boundary.payload


async def test_queued_peer_respects_mount_plan_fence_and_can_drain_after_release(boundary):
    app, owner, peer, runtime = boundary.app, boundary.owner, boundary.peer, boundary.runtime
    await queue(boundary)
    incoming = await agent(app, peer, "coordination.send",
                           {"sessionId": owner["id"], "text": "Synthetic earlier ACK", "mode": "queue"},
                           "older-peer-ack")
    await app.collaboration.drain(owner["id"])
    assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "queued"
    await reach_apply(boundary)
    assert owner.get("configurationRefresh", {}).get("phase") != "applying"
    try:
        await app.collaboration.drain(owner["id"])
        assert not runtime.inputs, "Peer admission crossed the claimed mount-plan boundary"
        assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "queued"
    finally:
        await settle(boundary)
    assert owner["pendingConfiguration"]["phase"] == "ready"
    # Explicit drain proves eventual eligibility, not the production automatic
    # release trigger. Native lifecycle-event integration remains a separate check.
    await app.collaboration.drain(owner["id"])
    assert runtime.inputs == ["older-peer-ack"], "The fix must release, not starve, the peer"
    assert runtime.calls.count("configuration.apply") == 1


@pytest.mark.parametrize("busy", ["generation", "queued_inputs"])
async def test_prewrite_refusal_retains_original_authority_and_exact_payload(boundary, busy):
    await queue(boundary)
    await reach_apply(boundary)
    setattr(boundary.runtime.native, busy, object() if busy == "generation" else 1)
    await settle(boundary)
    # This injection holds the real native guard at a known pre-write boundary.
    pending = boundary.owner["pendingConfiguration"]
    assert pending["phase"] == "error"
    assert "Wait for the current response" in pending["error"]
    assert not override_path(boundary.owner["id"]).exists()
    assert boundary.runtime.controls.state_path().read_bytes() == boundary.runtime.original_controls
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    assert pending.get("authority") == boundary.authority, "Do not discard the approved recovery binding"


@pytest.mark.parametrize("change", ["stop", "revoke", "expired", "workspace"])
async def test_delayed_apply_rechecks_original_authority_before_native_write(boundary, change):
    await queue(boundary)
    await reach_apply(boundary)
    app, owner = boundary.app, boundary.owner
    if change == "stop":
        await app.dispatch("conversation.stop", {"sessionId": owner["id"]})
    elif change == "revoke":
        await revoke(app, boundary.authorized)
    elif change == "expired":
        saved = app.configuration_authority.record(boundary.authorized["id"])
        saved["expiresAt"] = time.time() - 1
        app.configuration_authority.save(saved)
        app.db.commit()
    else:
        owner["workspace"] += "-moved"
    await settle(boundary)
    assert owner["pendingConfiguration"]["phase"] == "error"
    assert "configuration.apply" not in boundary.runtime.calls
    assert not override_path(owner["id"]).exists()
    assert app.management.queued_path(owner["id"]).read_bytes() == boundary.payload


async def test_peer_root_cannot_borrow_existing_owner_grant(boundary):
    await queue(boundary)
    with pytest.raises(AppError, match="Configuration authority is required"):
        await agent(boundary.app, boundary.peer, "configuration.apply",
                    {**boundary.args, "id": boundary.peer["id"]}, "peer-replay")
    assert not boundary.runtime.calls
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


async def test_duplicate_delivery_after_prewrite_failure_is_passive(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.native.queued_inputs = 1
    await settle(boundary)
    previous = copy.deepcopy(boundary.owner["pendingConfiguration"])
    calls = list(boundary.runtime.calls)
    duplicate = await agent(boundary.app, boundary.owner, "configuration.apply",
                            boundary.args, "original-apply")
    assert duplicate["duplicate"]
    await scheduler_barrier()
    assert boundary.runtime.calls == calls
    assert boundary.owner["pendingConfiguration"] == previous
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


async def test_delayed_failure_remains_correlated_with_the_original_command(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.native.queued_inputs = 1
    await settle(boundary)
    status = boundary.app.state["actionStatus"]["configuration.apply"]
    assert status["phase"] == "error"
    assert status["commandId"] == "original-apply", (
        "A generated internal command must not replace the caller's operation identity")


async def test_same_command_id_cannot_replace_the_queued_payload(boundary):
    await queue(boundary)
    with pytest.raises(AppError, match="different"):
        await agent(boundary.app, boundary.owner, "configuration.apply",
                    {**boundary.args, "config": plan(3)}, "original-apply")
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    assert boundary.owner["pendingConfiguration"]["authority"] == boundary.authority
    assert not boundary.runtime.calls


async def test_failure_between_native_writes_is_not_reported_as_mounted(boundary, monkeypatch):
    from amplifier_web import runtime_controls
    await queue(boundary)
    await reach_apply(boundary)
    write = runtime_controls.write_private
    writes = []

    def fail_second(path, content):
        writes.append(path)
        if path == boundary.runtime.controls.state_path():
            raise OSError("Synthetic second-write failure")
        return write(path, content)

    monkeypatch.setattr(runtime_controls, "write_private", fail_second)
    await settle(boundary)
    assert writes == [override_path(boundary.owner["id"]), boundary.runtime.controls.state_path()]
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    assert boundary.runtime.controls.state_path().read_bytes() == boundary.runtime.original_controls
    assert boundary.runtime.coordinator.config == plan(1)
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert "fixture-mounted" not in boundary.runtime.calls
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


@pytest.mark.parametrize("fault", ["lost-before-dispatch", "lost-after-save", "stop-after-save"])
async def test_failed_apply_keeps_failure_and_does_not_replay_on_reopen(boundary, fault):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = fault
    await settle(boundary)
    owner, runtime = boundary.owner, boundary.runtime
    pending = copy.deepcopy(owner["pendingConfiguration"])
    assert pending["phase"] == "error"
    written = fault != "lost-before-dispatch"
    assert override_path(owner["id"]).exists() == written
    assert runtime.coordinator.config == plan(1)  # Not mounted despite an optional save.
    if written:
        assert json.loads(override_path(owner["id"]).read_text()) == plan(2)
    assert boundary.app.management.queued_path(owner["id"]).read_bytes() == boundary.payload
    await boundary.app.close()
    fresh = ControlsTransport(boundary.app.data_dir, owner["id"])
    restored = AppService(boundary.app.data_dir, fresh, workspace=boundary.app.default_workspace)
    fresh.app = restored
    try:
        restored.management = Management(restored)
        await scheduler_barrier()
        assert restored._session(owner["id"])["pendingConfiguration"] == pending
        assert owner["id"] not in restored.management.pending_config_tasks
        assert not fresh.calls
    finally:
        await restored.close()


async def test_cancelled_native_result_is_not_automatically_requeued_after_reopen(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = "cancel-after-save"
    boundary.runtime.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(boundary.queued_task, 3)
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    assert boundary.runtime.coordinator.config == plan(1)
    pending = copy.deepcopy(boundary.owner["pendingConfiguration"])
    assert pending["phase"] == "applying"
    await boundary.app.close()
    fresh = ControlsTransport(boundary.app.data_dir, boundary.owner["id"])
    restored = AppService(boundary.app.data_dir, fresh, workspace=boundary.app.default_workspace)
    fresh.app = restored
    try:
        restored.management = Management(restored)
        await scheduler_barrier()
        current = restored._session(boundary.owner["id"])
        assert current["pendingConfiguration"]["phase"] != "queued", (
            "An interrupted apply may have written settings; reconnect cannot silently replay it")
        assert boundary.owner["id"] not in restored.management.pending_config_tasks
        assert not fresh.calls
    finally:
        await restored.close()


async def test_cancel_before_admission_clears_pending_without_native_effect(boundary):
    await queue(boundary)
    await boundary.app.dispatch("configuration.cancel", {"id": boundary.owner["id"]},
                                command_id="cancel-original")
    assert (await finished(boundary.app, "cancel-original"))["phase"] == "ready"
    assert not boundary.app.management.queued_path(boundary.owner["id"]).exists()
    assert "pendingConfiguration" not in boundary.owner
    assert not boundary.runtime.calls


# Additional repair contracts. The original 22 baseline cases above remain
# unchanged, including their assertions that produced the retained hosted RED.
def operation(boundary):
    pending = boundary.owner["pendingConfiguration"]
    return boundary.app.management.configuration_record(boundary.owner["id"], pending["operationId"])


async def test_private_original_record_survives_success_with_ordered_effect_stages(boundary):
    await queue(boundary)
    original = operation(boundary)
    assert original["request"]["commandId"] == "original-apply"
    assert original["request"]["config"] == plan(2)
    assert original["request"]["authority"] == boundary.authority
    assert original["request"]["source"]["origin"] == "agent"
    assert original["request"]["source"]["callerSessionId"] == boundary.owner["id"]
    assert "config" not in boundary.owner["pendingConfiguration"]
    private = boundary.app.management.configuration_record_path(boundary.owner["id"], original["id"])
    assert private.stat().st_mode & 0o777 == 0o600
    await reach_apply(boundary)
    await settle(boundary)
    saved = operation(boundary)
    assert saved["request"] == original["request"]
    assert saved["requestHash"] == original["requestHash"]
    assert saved["history"][:len(original["history"])] == original["history"]
    assert [row["revision"] for row in saved["history"]] == list(range(1, saved["revision"] + 1))
    stages = [row["stage"] for row in saved["history"]]
    assert stages.index("preparing") < stages.index("dispatching") < stages.index("saved") < stages.index("readback")
    assert saved["state"]["phase"] == "ready"
    assert saved["state"]["effect"] == "mounted"
    assert private.exists()


async def test_ui_origin_and_internal_no_command_identity_are_explicit(boundary):
    app, owner = boundary.app, boundary.owner
    owner["status"] = "working"
    await app.dispatch("configuration.apply", {"id": owner["id"], "config": plan(2), "whenIdle": True},
                       command_id="user-apply")
    assert (await finished(app, "user-apply"))["phase"] == "ready"
    original = operation(boundary)
    assert original["request"]["authorization"] == "ui"
    assert original["request"]["authority"] is None
    await app.management.perform("configuration.cancel", {"id": owner["id"]})
    await app.management.perform("configuration.apply",
                                {"id": owner["id"], "config": plan(3), "whenIdle": True})
    internal = operation(boundary)
    assert internal["request"]["authorization"] == "internal"
    assert internal["request"]["commandId"] == internal["id"]
    assert app.management.configuration_record(owner["id"], original["id"])["state"]["phase"] == "cancelled"


async def test_ambient_callback_cannot_replace_a_missing_queued_grant(boundary):
    from amplifier_web.configuration_authority import CURRENT
    boundary.owner["status"] = "working"
    token = CURRENT.set(lambda **kwargs: None)
    try:
        with pytest.raises(ValueError, match="original configuration authority"):
            await boundary.app.management.perform("configuration.apply",
                {"id": boundary.owner["id"], "config": plan(2), "whenIdle": True})
    finally:
        CURRENT.reset(token)
    assert "pendingConfiguration" not in boundary.owner
    assert not boundary.runtime.calls


@pytest.mark.parametrize("change", ["executionRevision", "workingDirectory"])
async def test_original_placement_binding_is_checked_before_dispatch(boundary, change):
    await queue(boundary)
    if change == "executionRevision":
        boundary.owner[change] = 1
    else:
        boundary.owner[change] = boundary.owner["workspace"] + "/different-placement"
    boundary.owner["status"] = "idle"
    await asyncio.wait_for(boundary.queued_task, 3)
    assert not boundary.runtime.calls
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert operation(boundary)["state"]["effect"] == "none"
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


@pytest.mark.parametrize("change", ["raw-payload", "private-request", "missing-record"])
async def test_corrupt_operation_binding_never_reaches_runtime(boundary, change):
    await queue(boundary)
    record = operation(boundary)
    path = boundary.app.management.configuration_record_path(boundary.owner["id"], record["id"])
    if change == "raw-payload":
        boundary.app.management.queued_path(boundary.owner["id"]).write_text(json.dumps(plan(9)))
    elif change == "private-request":
        altered = copy.deepcopy(record)
        altered["request"]["authority"] = None
        altered["request"]["authorization"] = "ui"
        path.write_text(json.dumps(altered))
    else:
        path.unlink()
    boundary.owner["status"] = "idle"
    await asyncio.wait_for(boundary.queued_task, 3)
    assert not boundary.runtime.calls
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert not override_path(boundary.owner["id"]).exists()
    # No corruption repair rewrites the private request as a trusted user action.
    if change == "private-request":
        assert json.loads(path.read_text()) == altered


@pytest.mark.parametrize("has_authority", [False, True])
async def test_legacy_pending_without_operation_binding_is_not_resumed(boundary, has_authority):
    app, owner = boundary.app, boundary.owner
    if has_authority:
        await queue(boundary)
        legacy = {"phase": "queued", "authority": boundary.authority}
    else:
        legacy = {"phase": "queued"}
        app.management.queued_path(owner["id"]).write_text(json.dumps(plan(2)))
    owner["pendingConfiguration"] = legacy
    owner["status"] = "working"
    app._publish()
    await app.close()
    fresh = ControlsTransport(app.data_dir, owner["id"])
    restored = AppService(app.data_dir, fresh, workspace=app.default_workspace)
    fresh.app = restored
    try:
        restored.management = Management(restored)
        await scheduler_barrier()
        pending = restored._session(owner["id"])["pendingConfiguration"]
        assert pending["phase"] == "error" and pending["recoveryRequired"]
        assert pending.get("authority") == legacy.get("authority")
        assert not fresh.calls
        assert owner["id"] not in restored.management.pending_config_tasks
        assert json.loads(restored.management.queued_path(owner["id"]).read_text()) == plan(2)
    finally:
        await restored.close()


async def test_new_request_cannot_replace_pending_or_unknown_original(boundary):
    await queue(boundary)
    before = operation(boundary)
    with pytest.raises(ValueError, match="original pending"):
        await boundary.app.management.perform("configuration.apply",
            {"id": boundary.owner["id"], "config": plan(3), "whenIdle": True},
            command_id="replacement")
    assert operation(boundary) == before
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    await reach_apply(boundary)
    boundary.runtime.native.queued_inputs = 1
    await settle(boundary)
    failed = operation(boundary)
    assert failed["state"]["effect"] == "unknown"  # Native error text is not a no-effect receipt.
    with pytest.raises(ValueError, match="unresolved"):
        await boundary.app.management.perform("configuration.cancel", {"id": boundary.owner["id"]})
    with pytest.raises(ValueError, match="original pending"):
        await boundary.app.management.perform("configuration.apply",
            {"id": boundary.owner["id"], "config": plan(3), "whenIdle": True})
    assert operation(boundary) == failed


async def test_cancel_and_next_operation_retain_old_record_and_new_task(boundary):
    await queue(boundary)
    original = operation(boundary)
    await boundary.app.management.perform("configuration.cancel", {"id": boundary.owner["id"]})
    cancelled = boundary.app.management.configuration_record(boundary.owner["id"], original["id"])
    assert cancelled["request"] == original["request"]
    assert cancelled["history"][:1] == original["history"]
    assert cancelled["state"]["phase"] == "cancelled"
    await boundary.app.management.perform("configuration.apply",
        {"id": boundary.owner["id"], "config": plan(3), "whenIdle": True}, command_id="next-apply")
    next_record = operation(boundary)
    assert next_record["id"] != original["id"]
    next_task = boundary.app.management.pending_config_tasks[boundary.owner["id"]]
    await scheduler_barrier()
    assert boundary.app.management.pending_config_tasks[boundary.owner["id"]] is next_task
    assert not next_task.done()
    assert boundary.app.management.configuration_record(boundary.owner["id"], original["id"]) == cancelled


async def test_claim_waits_for_peer_already_submitting_without_retracting_it(boundary):
    from test_coordination_generation import until
    app, owner, peer, runtime = boundary.app, boundary.owner, boundary.peer, boundary.runtime
    entered, release = asyncio.Event(), asyncio.Event()
    original_input = runtime.collaboration_input

    async def held_input(*args):
        entered.set()
        await release.wait()
        return await original_input(*args)

    runtime.collaboration_input = held_input
    incoming = await agent(app, peer, "coordination.send",
                           {"sessionId": owner["id"], "text": "In-flight admission", "mode": "queue"},
                           "submitting-peer")
    draining = asyncio.create_task(app.collaboration.drain(owner["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "submitting"
        await queue(boundary)
        owner["status"] = "idle"
        assert not app.management.configuration_idle(owner)
        await scheduler_barrier()
        assert not runtime.entered.is_set()
        assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "submitting"
    finally:
        release.set()
        await asyncio.wait_for(draining, 3)
    await until(lambda: app.collaboration.receipt(incoming["requestId"])["delivery"] == "accepted")
    await reach_apply(boundary)
    await settle(boundary)
    assert runtime.inputs == ["submitting-peer"]
    assert runtime.native.queued_inputs == 1
    assert not override_path(owner["id"]).exists()


async def test_confirmed_application_automatically_releases_waiting_peer(boundary):
    from test_coordination_generation import until
    await queue(boundary)
    await agent(boundary.app, boundary.peer, "coordination.send",
        {"sessionId": boundary.owner["id"], "text": "Wait for configuration", "mode": "queue"},
        "automatically-released")
    await reach_apply(boundary)
    assert not boundary.runtime.inputs
    await settle(boundary)
    await until(lambda: boundary.runtime.inputs == ["automatically-released"])
    assert boundary.owner["pendingConfiguration"]["phase"] == "ready"
    assert not boundary.owner["configurationBusy"]


@pytest.mark.parametrize("fault", ["lost-after-save", "stop-after-save"])
async def test_unresolved_mount_keeps_admission_and_never_warms_on_inspection(boundary, fault):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = fault
    await settle(boundary)
    calls = list(boundary.runtime.calls)
    assert boundary.owner["configurationBusy"]
    with pytest.raises(ValueError, match="unresolved"):
        await boundary.app.management.perform("configuration.inspect", {"id": boundary.owner["id"]})
    assert boundary.runtime.calls == calls
    # Stop remains an available supported action; it never clears an unknown effect.
    await boundary.app.dispatch("conversation.stop", {"sessionId": boundary.owner["id"]})
    assert operation(boundary)["state"]["effect"] in {"unknown", "saved_not_mounted"}


async def test_raw_apply_route_uses_the_same_original_operation_and_unknown_fence(boundary):
    app, owner, runtime = boundary.app, boundary.owner, boundary.runtime
    await grant(app, ["runtime_configuration"], ["session"])
    owner["status"] = "idle"
    runtime.fault = "lost-after-save"
    await agent(app, owner, "runtime.control",
        {"sessionId": owner["id"], "operation": "configuration.apply", "args": {"config": plan(2)}},
        "raw-original")
    await asyncio.wait_for(runtime.entered.wait(), 3)
    runtime.release.set()
    assert (await finished(app, "raw-original"))["phase"] == "error"
    saved = operation(boundary)
    assert saved["request"]["commandId"] == "raw-original"
    assert saved["request"]["source"]["origin"] == "agent"
    assert saved["state"]["effect"] == "unknown"
    assert owner["configurationBusy"]
    assert json.loads(override_path(owner["id"]).read_text()) == plan(2)


@pytest.mark.parametrize("route", ["configuration.apply", "runtime.control"])
@pytest.mark.parametrize("change", ["stop", "executionRevision", "workingDirectory"])
async def test_ui_admission_binds_target_before_management_lock_wait(boundary, route, change):
    app, owner = boundary.app, boundary.owner
    values = ({"id": owner["id"], "config": plan(2)} if route == "configuration.apply" else
              {"sessionId": owner["id"], "operation": "configuration.apply", "args": {"config": plan(2)}})
    await app.management.lock.acquire()
    try:
        await app.dispatch(route, values, command_id="before-wait")
        if change == "stop":
            await app.dispatch("conversation.stop", {"sessionId": owner["id"]})
        elif change == "executionRevision":
            owner[change] = 1
        else:
            owner[change] = owner["workspace"] + "/moved"
    finally:
        app.management.lock.release()
    assert (await finished(app, "before-wait"))["phase"] == "error"
    assert "configuration.apply" not in boundary.runtime.calls
    assert not override_path(owner["id"]).exists()
    assert "pendingConfiguration" not in owner


@pytest.mark.parametrize("action", ["absent-cancel", "queued-cancel", "queued-revoke"])
async def test_mount_plan_cleanup_does_not_release_an_independent_refresh(boundary, action):
    if action != "absent-cancel":
        await queue(boundary)
    owner = boundary.owner
    owner.update(configurationBusy=True, configurationRefresh={"phase": "applying"})
    if action == "queued-revoke":
        await revoke(boundary.app, boundary.authorized)
        await asyncio.wait_for(boundary.queued_task, 3)
        assert boundary.app.state["managementResults"]["original-apply"]["phase"] == "error"
    else:
        await boundary.app.management.perform("configuration.cancel", {"id": owner["id"]})
        await scheduler_barrier()
    assert owner["configurationBusy"]
    assert owner["configurationRefresh"]["phase"] == "applying"
    assert not boundary.runtime.inputs


async def test_cancelled_preparation_is_cancellable_without_erasing_original(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    original = operation(boundary)["request"]
    boundary.queued_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(boundary.queued_task, 3)
    current = operation(boundary)
    assert current["state"]["phase"] == "error"
    assert current["state"]["effect"] == "none"
    assert not boundary.owner["configurationBusy"]
    await boundary.app.management.perform("configuration.cancel", {"id": boundary.owner["id"]})
    record = boundary.app.management.configuration_record(boundary.owner["id"], current["id"])
    assert record["request"] == original
    assert record["state"]["phase"] == "cancelled"


@pytest.mark.parametrize("stage", ["prepare", "restart"])
@pytest.mark.parametrize("invalidation", ["stop", "revoke"])
async def test_runtime_start_receives_original_guard_across_its_wait(boundary, stage, invalidation):
    runtime = boundary.runtime
    entered, release = asyncio.Event(), asyncio.Event()
    start = runtime.start
    runtime.block_start = False

    async def guarded_start(session, emit, **kwargs):
        assert callable(kwargs.get("guard"))
        if runtime.needs_mount == (stage == "restart"):
            entered.set()
            await release.wait()
            kwargs["guard"]()
        await start(session, emit, **kwargs)

    runtime.start = guarded_start
    await queue(boundary)
    boundary.owner["status"] = "idle"
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if invalidation == "stop":
            await boundary.app.dispatch("conversation.stop", {"sessionId": boundary.owner["id"]})
        else:
            await revoke(boundary.app, boundary.authorized)
    finally:
        release.set()
    await asyncio.wait_for(boundary.queued_task, 3)
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert "fixture-mounted" not in runtime.calls
    assert override_path(boundary.owner["id"]).exists() == (stage == "restart")
    assert operation(boundary)["state"]["effect"] == ("saved_not_mounted" if stage == "restart" else "none")


async def test_unchanged_plan_readback_is_not_a_confirmed_mount(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    control = boundary.runtime.control

    async def stale(sid, name, args, **kwargs):
        if name == "configuration.inspect":
            return {"plan": plan(1)}
        return await control(sid, name, args, **kwargs)

    boundary.runtime.control = stale
    await settle(boundary)
    assert operation(boundary)["state"]["effect"] == "saved_not_mounted"
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert boundary.owner["configurationBusy"]
    assert "does not match" in boundary.owner["pendingConfiguration"]["error"]


@pytest.mark.parametrize("loaded_limit", [1, 2])
async def test_wrong_saved_ack_cannot_replace_the_immutable_request(boundary, loaded_limit):
    await queue(boundary)
    original = copy.deepcopy(operation(boundary)["request"])
    await reach_apply(boundary)
    control = boundary.runtime.control
    observed = {}

    async def wrong_ack(sid, name, args, **kwargs):
        result = await control(sid, name, args, **kwargs)
        if name == "configuration.apply":
            observed["saved"] = plan(1)
            return {**result, "configuration": plan(1)}
        if name == "configuration.inspect":
            observed["loaded"] = plan(loaded_limit)
            return {**result, "plan": plan(loaded_limit)}
        return result

    boundary.runtime.control = wrong_ack
    await settle(boundary)
    # loaded_limit=1 is the discriminator: both reports agree with each other,
    # but neither is the authorized plan(2). The correct readback variant also
    # requires checking the saved acknowledgement, not silently ignoring it.
    assert observed == {"saved": plan(1), "loaded": plan(loaded_limit)}
    record = operation(boundary)
    assert record["request"] == original
    assert original["config"] == plan(2)
    assert original["authority"] == boundary.authority
    assert record["state"]["phase"] == "error"
    assert record["state"]["effect"] == "saved_not_mounted"
    assert boundary.owner["configurationBusy"]
    assert "acknowledgement does not match" in record["state"]["error"]
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    calls = list(boundary.runtime.calls)
    duplicate = await agent(boundary.app, boundary.owner, "configuration.apply",
                            boundary.args, "original-apply")
    assert duplicate["duplicate"]
    await scheduler_barrier()
    assert boundary.runtime.calls == calls
    assert boundary.runtime.calls.count("configuration.apply") == 1
    assert operation(boundary) == record


@pytest.mark.parametrize("legacy", [False, True])
async def test_keyed_cold_owner_restores_unresolved_admission_fence(boundary, legacy):
    app, owner = boundary.app, boundary.owner
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = "lost-after-save"
    await settle(boundary)
    if legacy:
        owner["pendingConfiguration"] = {"phase": "error", "error": "Old unclassified failure"}
    app.state["selectedSessionId"] = boundary.peer["id"]
    app._publish()
    await app.close()
    fresh = ControlsTransport(app.data_dir, owner["id"])
    restored = AppService(app.data_dir, fresh, workspace=app.default_workspace)
    fresh.app = restored
    try:
        # Exercise keyed hydration directly if startup's working set already
        # included this owner; evict only the in-memory view, not saved evidence.
        restored._state["sessions"] = [s for s in restored._state["sessions"] if s["id"] != owner["id"]]
        restored.projections.invalidate(state=restored._state)
        restored.management = Management(restored)
        current = restored._session(owner["id"])
        assert current["configurationBusy"]
        assert not restored.warmup.allowed(current)
        with pytest.raises(AppError, match="settings"):
            await restored.dispatch("conversation.send", {"sessionId": owner["id"], "text": "Do not start"})
        assert not fresh.calls
    finally:
        await restored.close()


@pytest.mark.parametrize("invalidated", [False, True])
async def test_real_transport_admission_rechecks_grant_without_serializing_callback(boundary, invalidated, monkeypatch):
    from amplifier_web.runtime import RuntimeManager
    await queue(boundary)
    manager = RuntimeManager()
    sid = boundary.owner["id"]
    row = {"process": SimpleNamespace(returncode=None), "pending": {}, "inflight": set()}
    manager.workers[sid] = row
    writes = []

    async def write(target, payload):
        writes.append(payload)
        target["pending"][payload["id"]].set_result({"fixture": True})

    monkeypatch.setattr(manager, "_write", write)
    def guard():
        boundary.app.configuration_authority.check(boundary.authority)

    try:
        async with manager._admission(sid):
            pending = asyncio.create_task(manager.control(sid, "configuration.apply", {"config": plan(2)}, guard=guard))
            await scheduler_barrier()
            assert not writes
            if invalidated:
                await revoke(boundary.app, boundary.authorized)
        if invalidated:
            with pytest.raises(AppError, match="revoked"):
                await asyncio.wait_for(pending, 3)
            assert not writes and not row["inflight"]
        else:
            assert await asyncio.wait_for(pending, 3) == {"fixture": True}
            assert len(writes) == 1 and "guard" not in writes[0]
    finally:
        manager.workers.pop(sid, None)
        await manager.close()


@pytest.mark.parametrize("change", ["removed-agent", "removed-config-key", "nested-step-order", "provider-order"])
async def test_readback_rejects_retained_or_reordered_configuration(boundary, change):
    desired = plan(2)
    stale = copy.deepcopy(desired)
    if change == "removed-agent":
        desired["agents"] = {}
        stale["agents"] = {"old-agent": {"module": "agent-fixture"}}
    elif change == "removed-config-key":
        stale["tools"][0]["config"]["removed"] = "old-value"
    elif change == "nested-step-order":
        desired["tools"][0]["config"]["steps"] = [{"id": "validate"}, {"id": "execute"}]
        stale["tools"][0]["config"]["steps"] = [{"id": "execute"}, {"id": "validate"}]
    else:
        desired["providers"] = [{"module": "provider-fixture", "id": "first"},
                                {"module": "provider-fixture", "id": "second"}]
        stale["providers"] = list(reversed(copy.deepcopy(desired["providers"])))
    assert not Management.configuration_readback_matches(desired, stale)
    # Full mutation path must retain the same unconfirmed saved state.
    boundary.owner["status"] = "working"
    await boundary.app.management.perform("configuration.apply",
        {"id": boundary.owner["id"], "config": desired, "whenIdle": True})
    boundary.queued_task = boundary.app.management.pending_config_tasks[boundary.owner["id"]]
    control = boundary.runtime.control
    async def stale_control(sid, name, args, **kwargs):
        if name == "configuration.inspect":
            return {"plan": stale}
        return await control(sid, name, args, **kwargs)
    boundary.runtime.control = stale_control
    await reach_apply(boundary)
    await settle(boundary)
    assert operation(boundary)["state"]["effect"] == "saved_not_mounted"
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert boundary.owner["configurationBusy"]