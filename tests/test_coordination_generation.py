"""Grant-free protocol checks. Scripted transports are not model qualification."""
import asyncio
import copy
import json
import sqlite3
import sys

import pytest

from amplifier_web.collaboration import Collaboration
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError, AppService


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


async def agent(app, source, action, args, identity, **transport):
    return await app.app_bridge("dispatch", {
        "action": action, "args": args, "id": identity,
        "_runtimeSessionId": source["id"],
        "_generationId": source["collaborationGeneration"]["id"],
        "_inputBindings": [{"inputId": value} for value in source["collaborationGeneration"]["inputIds"]],
        **transport,
    }, source["id"])


def source_plan(app, source):
    source["selection"] = {"instance": "openai", "model": "gpt-6-astra", "effort": "xhigh"}
    directory = app.data_dir / "sessions" / source["id"]
    directory.mkdir(parents=True, exist_ok=True)
    plan = {"providers": [{"module": "provider-openai", "source": "fixture-openai",
                           "config": {"default_model": "gpt-6-astra"}}],
            "tools": [{"module": "tool-custom", "source": "fixture-custom"}]}
    (directory / "effective-configuration.json").write_text(json.dumps(plan))
    (directory / "control-state.json").write_text(json.dumps({"selection": source["selection"]}))
    return plan


class Transport:
    def __init__(self):
        self.inputs = []

    async def collaboration_input(self, session, arguments, guard, emit):
        reason = guard()
        if reason:
            return {"accepted": False, "reason": reason}
        permission = self.app.collaboration.admission(session["id"], arguments)
        assert permission["admitted"]
        self.inputs.append((session["id"], arguments["inputId"], permission["message"]))
        await emit("runtime.status", {"sessionId": session["id"], "status": "working"})
        return {"accepted": True}

    async def close(self):
        pass

    async def stop(self, sid):
        await self.app.on_runtime_event("runtime.status", {"sessionId": sid, "status": "stopped"})


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    runtime = Transport()
    service = runtime.app = AppService(tmp_path / "app", runtime, workspace=tmp_path)
    source, peer = [service._new_session({"title": title}) for title in ("Source", "Peer")]
    service.state["sessions"] = [source, peer]
    service.state["selectedSessionId"] = source["id"]
    service.state["view"]["draft"] = "Private source draft"
    # Deliberately a peer-woken generation, with no delivered human message.
    source["collaborationGeneration"] = {"id": "peer-woken", "inputIds": ["incoming-peer"], "terminal": False}
    source_plan(service, source)
    service._publish()
    yield service
    await service.close()


async def test_two_distinct_roots_without_grant_inherit_and_return_once(app):
    source = app.state["sessions"][0]
    plan = source_plan(app, source)
    results = []
    for identity in ("first-task", "second-task"):
        args = {"title": identity, "text": "Preserve originals and produce a checked artifact"}
        result = await agent(app, source, "coordination.create", args, identity)
        assert result["accepted"] and result["delivery"] == "creation_pending"
        assert result["requestId"] == identity
        results.append(result)
        duplicate = await agent(app, source, "coordination.create", args, identity)
        assert duplicate["duplicate"] and duplicate["sessionId"] == result["sessionId"]
    await until(lambda: len(app.runtime.inputs) == 2)
    assert len({row["sessionId"] for row in results}) == 2
    assert app.state["selectedSessionId"] == source["id"]
    assert app.state["view"]["draft"] == "Private source draft"
    for result in results:
        target = app._session(result["sessionId"])
        assert target["bundle"] == source["bundle"] and target["workspace"] == source["workspace"]
        assert target["selection"] == source["selection"]
        actual = json.loads((app.data_dir / "sessions" / target["id"] / "configuration.json").read_text())
        assert actual == plan
        envelope = target["messages"][0]["peerEnvelope"]
        assert envelope["sourceGenerationId"] == "peer-woken"
        assert envelope["sourceInputIds"] == ["incoming-peer"]
        assert "grantId" not in envelope
    assert not app.collaboration.current(source["id"])["grants"]
    assert not source.get("approvals")


@pytest.mark.parametrize("transport", [
    {"_runtimeSessionId": "foreign-child"},
    {"_generationId": "stale"},
    {"_generationId": None},
])
async def test_actual_root_and_current_generation_required(app, transport):
    source, peer = app.state["sessions"]
    with pytest.raises(AppError):
        await agent(app, source, "coordination.send", {"sessionId": peer["id"], "text": "no"}, "refused", **transport)
    assert not peer["messages"] and not app.runtime.inputs


async def test_missing_transport_cross_workspace_and_peer_control_refused(app):
    source, peer = app.state["sessions"]
    with pytest.raises(AppError):
        await app.dispatch("coordination.send", {"sessionId": peer["id"], "text": "no"},
                           origin="agent", caller_session_id=source["id"])
    peer["workspace"] += "/other"
    with pytest.raises(AppError, match="workspace"):
        await agent(app, source, "coordination.send", {"sessionId": peer["id"], "text": "no"}, "foreign")
    for action, args in [
        ("conversation.stop", {"sessionId": peer["id"]}),
        ("configuration.apply", {"id": peer["id"], "config": {}}),
        ("runtime.control", {"sessionId": peer["id"], "operation": "provider.select", "args": {}}),
        ("session.create", {"title": "raw bypass"}),
    ]:
        with pytest.raises(AppError):
            await agent(app, source, action, args, action)


async def test_notify_alias_and_exact_message_inspection_preserve_selection(app):
    source, peer = app.state["sessions"]
    for action in ("coordination.send", "conversation.send", "coordination.followup"):
        receipt = await agent(app, source, action,
            {"sessionId": peer["id"], "text": action, "mode": "notify"}, action)
        assert receipt["delivery"] == "notified"
    result = await app.dispatch("coordination.read", {"sessionId": peer["id"], "messageId": receipt["messageId"]})
    assert result["result"]["message"]["text"] == "coordination.followup"
    assert app.state["selectedSessionId"] == source["id"]
    assert app.state["view"]["draft"] == "Private source draft"
    assert not app.runtime.inputs


async def test_upgrade_retains_evidence_and_suppresses_legacy_before_drain(app):
    source, peer = app.state["sessions"]
    grant = {"accepted": True, "commandAction": "coordination.grant",
             "result": {"id": "old-grant", "participants": [source["id"], peer["id"]],
                        "workspace": source["workspace"], "revoked": True,
                        "revision": 2, "idleStart": True, "allowCreate": True}}
    app.collaboration.insert("old-grant", "historical", grant)
    for state in ("queued", "submitting", "unknown", "denied", "accepted"):
        receipt = {"accepted": state != "denied", "commandAction": "coordination.send",
                   "requestId": state, "inputId": state, "senderSessionId": source["id"],
                   "target": {"sessionId": peer["id"]}, "grantId": "old-grant",
                   "delivery": state, "subscription": {"status": "waiting", "continuationId": state + ":wait"},
                   "response": {"status": "sealed", "qualified": True, "terminalMessageId": "original-terminal",
                                "references": ["original-artifact"]}}
        app.collaboration.insert(state, state, receipt)
    app.db.commit()
    app.collaboration = Collaboration(app)
    app.collaboration.start()
    await asyncio.gather(*list(app.tasks))
    assert app.collaboration.receipt("old-grant") == grant
    assert app.collaboration.receipt("queued")["delivery"] == "suppressed"
    assert app.collaboration.receipt("submitting")["delivery"] == "unknown"
    assert app.collaboration.receipt("unknown")["delivery"] == "unknown"
    assert app.collaboration.receipt("denied")["accepted"] is False
    for state in ("queued", "submitting", "unknown", "denied", "accepted"):
        receipt = app.collaboration.receipt(state)
        assert receipt["subscription"]["status"] == "suppressed"
        assert receipt["response"]["references"] == ["original-artifact"]
        app.collaboration.enqueue_continuation(receipt)
    assert not app.runtime.inputs
    with pytest.raises(AppError) as retired:
        await app.dispatch("coordination.revoke", {"sessionId": source["id"], "grantId": "old-grant"})
    assert retired.value.status == 410


@pytest.mark.parametrize("gate", ["stop", "exception", "timeout", "cancel", "release", "caller-timeout", "separate-bound"])
async def test_real_manager_preworker_gate_receipt_progress_and_settlement(tmp_path, monkeypatch, gate):
    """Real RuntimeManager.ensure boundary; fixture process is NOT a native Worker."""
    from amplifier_web import runtime_profiles
    entered, release, progress = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def ensure(home, generation, session, *, progress=None):
        entered.set()
        await release.wait()
        if gate == "exception":
            raise ValueError("qualification fixture failed")
        return None
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    monkeypatch.setattr(runtime_profiles, "ensure", ensure)
    manager = RuntimeManager(startup_timeout=.15 if gate == "separate-bound" else 3,
                             preparation_timeout=.3 if gate == "timeout" else 3,
                             progress_interval=.02, retention={"prewarm_on_select": False})
    writes = tmp_path / "worker-controls.jsonl"
    fixture = """
import json,sys
for line in sys.stdin:
 data=json.loads(line)
 if data['op']=='start':
  print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
 elif data['op']=='control':
  with open(sys.argv[1],'a') as output: output.write(json.dumps(data)+'\\n')
  print(json.dumps({'op':'reply','id':data['id'],'result':{'accepted':True}}),flush=True)
 elif data['op']=='stop': break
"""
    monkeypatch.setattr(manager, "_command", lambda *a, **kw: [sys.executable, "-c", fixture, str(writes)])
    app = AppService(tmp_path / "app", manager, workspace=tmp_path)
    source = app._new_session({"title": "Source"})
    app.state["sessions"] = [source]
    app.state["selectedSessionId"] = source["id"]
    source["collaborationGeneration"] = {"id": "source-generation", "inputIds": ["peer-input"], "terminal": False}
    source_plan(app, source)
    original_emit = app.on_runtime_event
    events = []
    async def emit(kind, payload):
        events.append((kind, payload))
        await original_emit(kind, payload)
        if payload.get("phase") == "runtime-qualification":
            progress.set()
    app.on_runtime_event = emit
    try:
        args = {"title": "Slow child", "text": "Exactly one brief"}
        # The response window is shorter than preparation, not an arbitrary sleep.
        if gate == "caller-timeout":
            response = {}
            async def lost_response():
                response.update(await agent(app, source, "coordination.create", args, "slow-create"))
                await asyncio.Event().wait()  # Simulated response connection never delivers its bytes.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(lost_response(), .2)
            receipt = response
        else:
            receipt = await asyncio.wait_for(agent(app, source, "coordination.create", args, "slow-create"), .2)
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(progress.wait(), 2)
        assert not release.is_set() and not manager.workers
        assert receipt["sessionId"] and receipt["requestId"]
        # A second connection reads the committed command, not an in-memory return.
        db_path = app.db.execute("PRAGMA database_list").fetchone()[2]
        with sqlite3.connect(db_path) as db:
            retained = json.loads(db.execute("SELECT receipt FROM commands WHERE id='slow-create'").fetchone()[0])
        assert retained["sessionId"] == receipt["sessionId"]
        duplicate = await agent(app, source, "coordination.create", args, "slow-create")
        assert duplicate["duplicate"]
        target = receipt["sessionId"]
        if gate == "separate-bound":
            # Qualification outlives the process-ready deadline but is bounded
            # separately. A response wait is not a worker-start timeout.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(manager._preparations[target]["task"]),
                                       manager.startup_timeout + .05)
            assert not manager._preparations[target]["task"].done()
            assert not writes.exists()
        if gate == "stop":
            await app.dispatch("conversation.stop", {"sessionId": target})
        elif gate == "cancel":
            manager._preparations[target]["task"].cancel()
        elif gate != "timeout":
            release.set()
        await until(lambda: app.collaboration.receipt("slow-create:brief")["delivery"] in {"accepted", "not_sent"})
        outcome = app.collaboration.receipt("slow-create:brief")
        if gate in {"release", "caller-timeout", "separate-bound"}:
            assert outcome["delivery"] == "accepted"
            controls = [json.loads(line) for line in writes.read_text().splitlines()]
            assert len(controls) == 1
            assert controls[0]["arguments"]["inputId"] == "slow-create:brief"
        else:
            assert outcome["delivery"] == "not_sent"
            assert not writes.exists() and not manager.workers
            assert app._session(target)["status"] in {"error", "stopped", "interrupted"}
        assert any(row.get("elapsedSeconds", 0) > 0 for _, row in events)
    finally:
        release.set()
        await app.close()


async def test_outer_drain_cancellation_persists_not_sent_and_leaves_next_input(tmp_path, monkeypatch):
    from amplifier_web import runtime_profiles
    entered = asyncio.Event()
    async def ensure(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    monkeypatch.setattr(runtime_profiles, "ensure", ensure)
    manager = RuntimeManager(preparation_timeout=3, retention={"prewarm_on_select": False})
    app = AppService(tmp_path / "app", manager, workspace=tmp_path)
    source, target = [app._new_session({"title": title}) for title in ("Source", "Target")]
    app.state["sessions"] = [source, target]
    source["collaborationGeneration"] = {"id": "source", "inputIds": ["peer"], "terminal": False}
    target["status"] = "ready"
    try:
        for identity in ("first", "second"):
            await agent(app, source, "coordination.send", {"sessionId": target["id"], "text": identity}, identity)
        await asyncio.gather(*list(app.tasks))
        second = copy.deepcopy(app.collaboration.receipt("second"))
        target["status"] = "idle"
        drain = asyncio.create_task(app.collaboration.drain(target["id"]))
        await asyncio.wait_for(entered.wait(), 2)
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain
        assert drain.cancelled()
        assert app.collaboration.receipt("first")["delivery"] == "not_sent"
        assert app.collaboration.receipt("second") == second
        assert second["delivery"] == "queued"
        assert not manager.workers and not manager._preparations
        assert target["id"] not in app.collaboration.draining
        duplicate = await agent(app, source, "coordination.send",
                                {"sessionId": target["id"], "text": "first"}, "first")
        assert duplicate["duplicate"] and duplicate["delivery"] == "not_sent"
        assert app.collaboration.receipt("second") == second
    finally:
        await app.close()


@pytest.mark.parametrize("state", ["ready", "stopped", "paused"])
async def test_warmth_releases_only_existing_guarded_queue_once(app, state):
    source, target = app.state["sessions"]
    target["status"] = "ready"
    await agent(app, source, "coordination.send", {"sessionId": target["id"], "text": "queued"}, "warm-queue")
    await asyncio.gather(*list(app.tasks))
    assert not app.runtime.inputs
    if state == "stopped":
        await app.dispatch("conversation.stop", {"sessionId": target["id"]})
    elif state == "paused":
        target["task"] = {"id": "paused-task", "revision": 1, "status": "paused"}
    before = copy.deepcopy(target.get("collaborationGeneration"))
    for _ in range(2):
        await app.on_runtime_event("runtime.warmth", {"sessionId": target["id"], "status": "warm"})
    await asyncio.gather(*list(app.tasks))
    assert target.get("collaborationGeneration") == before  # warmth itself invents no turn
    if state == "ready":
        assert len(app.runtime.inputs) == 1
        assert app.collaboration.receipt("warm-queue")["delivery"] == "accepted"
    else:
        assert not app.runtime.inputs
        assert app.collaboration.receipt("warm-queue")["delivery"] in {"suppressed", "queued"}
    # Passive warmth without a new queued input never sends a second one.
    await app.on_runtime_event("runtime.warmth", {"sessionId": target["id"], "status": "warm"})
    await asyncio.gather(*list(app.tasks))
    assert len(app.runtime.inputs) == (1 if state == "ready" else 0)


async def test_idle_history_and_terminal_unknown_creation_do_not_exhaust_cap(app):
    source = app.state["sessions"][0]
    old = {}
    for index in range(8):
        root = app._new_session({"title": f"Finished {index}"})
        root.update(status="idle", collaboration={"creatorSessionId": source["id"]})
        app.state["sessions"].append(root)
        for prefix, sid, protocol in (("known", root["id"], 2), ("absent", "absent-" + str(index), None)):
            identity = f"{prefix}-{index}"
            receipt = {"commandAction": "coordination.create", "senderSessionId": source["id"],
                       "sessionId": sid, "protocol": protocol, "delivery": "unknown", "accepted": True}
            app.collaboration.insert(identity, identity, receipt)
            old[identity] = copy.deepcopy(receipt)
    result = await agent(app, source, "coordination.create", {"title": "Ninth", "text": "new work"}, "ninth")
    await until(lambda: app.collaboration.receipt("ninth")["delivery"] == "created")
    assert result["sessionId"] not in {r["sessionId"] for r in old.values()}
    assert all(app.collaboration.receipt(k) == v for k, v in old.items())


@pytest.mark.parametrize("work", ["queued", "generation", "preparation", "durable", "durable-projection"])
async def test_unknown_root_with_actual_work_still_occupies_single_cap_slot(app, work):
    source = app.state["sessions"][0]
    roots = []
    for index in range(8):
        root = app._new_session({"title": f"Outstanding {index}"})
        root.update(status="idle", collaboration={"creatorSessionId": source["id"]})
        app.state["sessions"].append(root)
        roots.append(root)
        app.collaboration.insert(f"unknown-{index}", str(index), {
            "commandAction": "coordination.create", "senderSessionId": source["id"],
            "sessionId": root["id"], "protocol": 2, "delivery": "unknown", "accepted": True})
        if work == "queued":
            app.collaboration.insert(f"queue-{index}", str(index), {
                "commandAction": "coordination.send", "requestId": f"queue-{index}",
                "senderSessionId": source["id"], "target": {"sessionId": root["id"]},
                "protocol": 2, "delivery": "queued"})
        elif work == "generation":
            root["collaborationGeneration"] = {"id": str(index), "terminal": False}
        elif work == "preparation":
            root["status"] = "starting"
        elif work == "durable":
            root["task"] = {"id": str(index), "status": "active"}
        else:
            app.state.setdefault("runtimeControl", {})[root["id"]] = {
                "task.get": {"task": {"id": str(index), "status": "active"}}}
    args = {"title": "Ninth", "text": "new work"}
    with pytest.raises(AppError, match="eight outstanding"):
        await agent(app, source, "coordination.create", args, "bounded")
    # Release one actual slot; the unknown creation must not double-count it.
    roots[0].update(status="idle", collaborationGeneration={"terminal": True}, task=None)
    app.state.setdefault("runtimeControl", {}).pop(roots[0]["id"], None)
    if work == "queued":
        receipt = app.collaboration.receipt("queue-0")
        receipt["delivery"] = "unknown"
        app.collaboration.save("queue-0", receipt)
    result = await agent(app, source, "coordination.create", args, "bounded")
    assert result["delivery"] == "creation_pending"
    assert app.collaboration.receipt("unknown-0")["delivery"] == "unknown"


async def test_concurrent_pending_creations_are_bounded_before_roots_exist(app, monkeypatch):
    source = app.state["sessions"][0]
    release = asyncio.Event()
    entered = []
    finish = app.collaboration._finish_create
    async def delayed(*args):
        entered.append(args[4])
        await release.wait()
        await finish(*args)
    monkeypatch.setattr(app.collaboration, "_finish_create", delayed)
    try:
        receipts = await asyncio.gather(*(agent(app, source, "coordination.create",
            {"title": str(index), "text": "bounded"}, f"pending-{index}") for index in range(8)))
        assert len({row["sessionId"] for row in receipts}) == 8
        assert len(app.state["sessions"]) == 2
        with pytest.raises(AppError, match="eight outstanding"):
            await agent(app, source, "coordination.create", {"title": "Ninth", "text": "bounded"}, "ninth")
    finally:
        release.set()
        await asyncio.gather(*list(app.tasks))