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

    async def control(self, sid, operation, args):
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