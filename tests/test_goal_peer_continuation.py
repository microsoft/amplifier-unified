"""Offline service/Worker/Core/live+streaming goal/peer ordering discriminator.

Only provider replies and goal evaluation are scripted. The existing native
probe supplies serial transport in place of a worker process, not fake
generation events, queue admission, terminal sealing, or execute().
"""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("amplifier_module_loop_live.orchestrator")
pytest.importorskip("amplifier_module_loop_streaming")

from fixtures.collaboration_native_probe import (
    DirectNativeHost, ScriptedProvider, message_text, peer_envelopes, tool_output,
)
from amplifier_web import runtime_worker
from amplifier_web.management import Management
from amplifier_web.service import AppService


HUMAN = "ordering-human-input"
REASON = "Continue the synthetic objective after checking the exact peer result."


class OrderingHost(DirectNativeHost):
    async def start(self, session, emit):
        # Already mounted through PreparedBundle in this transport fixture.
        assert session["id"] in self.roots

    async def control(self, sid, operation, args):
        return await self.roots[sid].controls.perform(operation, args)


class Parent(ScriptedProvider):
    def __init__(self, host, target):
        super().__init__()
        self.host, self.target = host, target
        self.waiting, self.release = asyncio.Event(), asyncio.Event()
        self.next_request, self.release_next = asyncio.Event(), asyncio.Event()
        self.request_id = self.continuation_id = None
        self.boundaries = []

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.dispatch("ordering-send", "coordination.send", {
                "sessionId": self.target, "mode": "queue",
                "text": "Return the synthetic result reference.",
            })
        if self.calls == 2:
            receipt = tool_output(request, "ordering-send")
            assert receipt["accepted"] and receipt["delivery"] == "queued", receipt
            self.request_id = receipt["requestId"]
            return self.dispatch("ordering-subscribe", "coordination.subscribe", {
                "sessionId": self.host.parent_id, "requestId": self.request_id,
            })
        if self.calls == 3:
            receipt = tool_output(request, "ordering-subscribe")
            assert receipt["accepted"] and receipt["result"]["status"] == "waiting"
            self.continuation_id = receipt["result"]["continuationId"]
            self.waiting.set()
            await asyncio.wait_for(self.release.wait(), 15)
            return self.final("First parent answer; the qualified peer result is queued.")
        last_user = next(m for m in reversed(request.messages) if m.role == "user")
        text = message_text(last_user)
        peer = next((e for e, t in peer_envelopes(request)
                     if e["inputId"] == self.continuation_id and t == text), None)
        worker = self.host.roots[self.host.parent_id]
        self.boundaries.append({
            "kind": "peer" if peer else "goal",
            "generationId": worker.runtime.generation["id"],
            "inputIds": list(worker.runtime.generation["input_ids"]),
            "goal": copy.deepcopy(worker.session.coordinator.session_state.get("goal")),
            "text": text,
        })
        if len(self.boundaries) == 1:
            self.next_request.set()
            await asyncio.wait_for(self.release_next.wait(), 15)
        assert peer or text == REASON, text
        return self.final("Peer processed." if peer else "Synthetic goal work continued.")


class Peer(ScriptedProvider):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.request_id = None

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            envelope, _ = peer_envelopes(request)[0]
            self.request_id = envelope["inputId"]
            await asyncio.wait_for(self.release.wait(), 15)
            return self.dispatch("ordering-reply", "coordination.reply", {
                "requestId": self.request_id, "kind": "result", "outcome": "success",
                "text": "Synthetic result is ready.",
                "references": ["artifact:synthetic-result"],
            })
        assert self.calls == 2
        receipt = tool_output(request, "ordering-reply")
        assert receipt["accepted"] and receipt["result"]["status"] == "staged"
        return self.final("Synthetic result ready for independent verification.")


@pytest.mark.parametrize("goal_on", [False, True], ids=["no-goal", "active-goal"])
async def test_qualified_peer_precedes_automatic_goal_turn(tmp_path, monkeypatch, goal_on):
    host = OrderingHost()
    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = host.app = AppService(tmp_path / "app", host, workspace=workspace)
    app.management = Management(app)
    app.state["sessions"] = [
        app._new_session({"title": title, "bundle": "synthetic-ordering"})
        for title in ("Ordering parent", "Ordering peer")
    ]
    source, target = app.state["sessions"]
    host.parent_id = source["id"]
    app._publish()
    parent, peer = Parent(host, target["id"]), Peer()
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    host.pump = asyncio.create_task(host.route_events())
    trace = {"goalOn": goal_on, "oracle": "eligible exact peer input before generic goal prompt"}
    try:
        async with asyncio.timeout(45):
            worker = await host.mount(source, parent, paths)
            peer_worker = await host.mount(target, peer, paths)
            evaluations = []

            async def evaluate(*args):
                evaluations.append(copy.deepcopy(worker.session.coordinator.session_state["goal"]))
                # Three finite turns, far below the cap. The very first next
                # provider request discriminates ordering, not timeout/cap exit.
                return len(evaluations) >= 3, REASON

            loop = worker.session.coordinator.get("orchestrator")
            loop._evaluate_goal = evaluate
            if goal_on:
                result = await app.dispatch("task.create", {
                    "sessionId": source["id"], "expectedRevision": 0,
                    "objective": "Check the synthetic peer result, then continue.", "maxTurns": 8,
                }, origin="ui", command_id="ordering-task", include_state=False)
                assert result["accepted"]
            task_before = copy.deepcopy(worker.controls.tasks.record())
            epoch_before = source.get("interruptionRevision", 0)
            policy_before = copy.deepcopy(worker.controls.capacity.policy)
            await app.dispatch("conversation.send", {
                "sessionId": source["id"], "text": "Coordinate the synthetic peer.",
            }, origin="ui", command_id=HUMAN, include_state=False)
            await asyncio.wait_for(parent.waiting.wait(), 15)
            original_generation = worker.runtime.generation["id"]
            peer.release.set()
            await host.outcome(peer_worker, parent.request_id)
            receipt = app.collaboration.receipt(parent.request_id)
            queued = app.collaboration.receipt(parent.continuation_id)
            assert receipt["response"]["qualified"] and receipt["response"]["status"] == "sealed"
            assert queued["delivery"] == "queued"
            assert not app.collaboration.guard(queued)
            assert parent.continuation_id not in worker.runtime.accepted
            assert worker.runtime.generation["id"] == original_generation
            trace["queued"] = copy.deepcopy(queued)
            trace["sealed"] = copy.deepcopy(receipt)
            parent.release.set()
            await asyncio.wait_for(parent.next_request.wait(), 15)
            trace["firstNextRequest"] = copy.deepcopy(parent.boundaries[0])
            # Preserve the discriminator before cleanup lets later turns finish.
            trace["orderingPassed"] = parent.boundaries[0]["kind"] == "peer"
            parent.release_next.set()
            first = await host.outcome(worker, HUMAN)
            final = await host.outcome(worker, parent.continuation_id)
            await app.collaboration.drain(source["id"])
            app.collaboration.start()  # Restart only the observation/drain path.
            await host.flush()
            while app.tasks:
                await asyncio.wait_for(asyncio.gather(*list(app.tasks)), 5)
            assert final["generation_id"] != original_generation == first["generation_id"]
            assert final["input_ids"] == [parent.continuation_id]
            assert sum(a["inputId"] == parent.continuation_id for a in host.admissions) == 1
            assert app.collaboration.receipt(parent.continuation_id)["delivery"] == "accepted"
            current = worker.controls.tasks.record()
            assert (current or {}).get("revision") == (task_before or {}).get("revision")
            assert (current or {}).get("status") == (task_before or {}).get("status")
            assert source.get("interruptionRevision", 0) == epoch_before
            assert worker.controls.capacity.policy == policy_before
            native = worker.runtime.events
            finished = next(e for e in native if e["type"] == "generation.finished"
                            and e["generation_id"] == original_generation)
            idle = next(e for e in native if e["type"] == "session.idle"
                        and e["sequence"] > finished["sequence"])
            started = next(e for e in native if e["type"] == "generation.started"
                           and e["initial_input_id"] == parent.continuation_id)
            assert finished["sequence"] < idle["sequence"] < started["sequence"]
            trace.update(boundaries=parent.boundaries, evaluations=evaluations,
                         finalReceipt=app.collaboration.receipt(parent.continuation_id))
            if goal_on and trace["orderingPassed"]:
                preserved = parent.boundaries[0]["goal"]
                assert preserved["turns_used"] == 1 and preserved["continuations"] == 0
                assert preserved["cap"] == 8 and preserved["reasons"] == [REASON]
                assert parent.boundaries[1]["kind"] == "goal"
                assert len(evaluations) == 3
            assert trace["orderingPassed"], (
                "Generic goal prompt reached the provider in the original generation "
                "before the already-qualified exact peer continuation was admitted"
            )
    finally:
        parent.release.set()
        parent.release_next.set()
        peer.release.set()
        trace["events"] = host.published
        trace["admissions"] = host.admissions
        trace["bridges"] = host.bridges
        evidence = os.environ.get("GOAL_PEER_EVIDENCE")
        if evidence:
            path = Path(evidence)
            path.mkdir(parents=True, exist_ok=True)
            (path / f"ordering-{'goal' if goal_on else 'control'}.json").write_text(
                json.dumps(trace, indent=2, default=str) + "\n")
        await host.close()
        await app.close()