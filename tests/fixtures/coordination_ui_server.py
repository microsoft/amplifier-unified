"""Production service/assets with deterministic runtime emulation, no provider.

Generation bindings and terminal anchors here are scripted adapter evidence,
not checkpoints from a native runtime or evidence of live model cooperation.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Runtime:
    def __init__(self):
        self.messages, self.sent, self.stops = [], [], []
        self.contexts, self.terminals, self.events, self.bridge_calls = {}, {}, [], []
        self.approvals, self.approval_responses, self.failures = [], [], []
        self.approval_futures = {}
        self.natural_grant = None
        self.artifact = None
        self.task_id = None

    async def event(self, kind, payload, emit):
        self.events.append({"kind": kind, **copy.deepcopy(payload)})
        await emit(kind, payload)

    async def start(self, session, emit):
        await self.event("runtime.status", {"sessionId": session["id"], "status": "ready",
                                          "runtimeSessionId": session["id"]}, emit)

    async def begin(self, session, input_id, emit):
        sid = session["id"]
        context = {"sessionId": sid, "generationId": "fixture-generation:" + input_id,
                   "inputIds": [input_id], "active": True, "emit": emit}
        self.contexts[sid] = context
        await self.event("runtime.generation", {
            "sessionId": sid, "rootSessionId": sid, "event": "generation.started",
            "generation_id": context["generationId"], "input_ids": [input_id],
        }, emit)
        await self.event("runtime.status", {
            "sessionId": sid, "rootSessionId": sid, "runtimeSessionId": sid,
            "status": "working", "event": "input.delivered", "inputId": input_id,
        }, emit)
        return context

    async def bridge(self, actor, action, args, identity):
        """Like the worker bridge, replace transport metadata from model context.

        The HTTP caller can select a scripted role, not an arbitrary principal,
        generation, or delivered input set.
        """
        sid = {"source": self.source_id, "recipient": self.task_id}.get(actor)
        if not sid or sid not in self.contexts:
            raise ValueError("This scripted actor has no delivered runtime context.")
        context = self.contexts[sid]
        metadata = {"_runtimeSessionId": sid, "_generationId": context["generationId"],
                    "_inputBindings": [{"inputId": i, "clientId": None} for i in context["inputIds"]]}
        self.bridge_calls.append({"actor": actor, "action": action, "id": identity,
                                  "sessionId": sid, **copy.deepcopy(metadata)})
        return await self.service.app_bridge("dispatch", {
            "action": action, "args": args, "id": identity, **metadata,
        }, sid)

    async def send(self, session, text, input_id, emit):
        self.sent.append({"sessionId": session["id"], "text": text, "inputId": input_id, "kind": "human"})
        await self.begin(session, input_id, emit)
        # Only this actual composer submission initiates the scripted grant.
        if session["id"] == self.source_id and text == "Coordinate with Other conversation on this task":
            self.service._task(self.propose_grant(input_id))
        return {"accepted": True, "inputId": input_id, "completed": False}

    async def propose_grant(self, input_id):
        try:
            source = self.service._session(self.source_id)
            human = next(row for row in source["messages"] if row.get("inputId") == input_id)
            self.natural_grant = await self.bridge("source", "coordination.grant", {
                "sessionId": source["id"], "sourceMessageId": human["id"],
                "participants": [self.other_id], "purpose": "Coordinate the fixture candidate",
                "modes": ["notify", "queue", "steer"], "idleStart": True, "allowCreate": True,
            }, "fixture-natural-grant")
            # Keep the source generation active while it sends/subscribes.
            # /fixture/finish settles this turn before the dependency terminal.
        except Exception as exc:
            self.failures.append({"stage": "natural-grant", "error": str(exc)})

    async def collaboration_approval(self, sid, prompt):
        identity = "fixture-collaboration-approval"
        future = asyncio.get_running_loop().create_future()
        self.approval_futures[(sid, identity)] = future
        self.approvals.append({"sessionId": sid, "id": identity, "prompt": prompt})
        await self.event("approval.requested", {
            "sessionId": sid, "id": identity, "title": "Authorize task collaboration", "prompt": prompt,
        }, self.service.on_runtime_event)
        return await future

    async def approval(self, sid, identity, decision):
        future = self.approval_futures.pop((sid, identity))
        self.approval_responses.append({"sessionId": sid, "id": identity, "decision": decision})
        await self.event("approval.resolved", {
            "sessionId": sid, "id": identity, "decision": decision,
        }, self.service.on_runtime_event)
        future.set_result({"allowed": decision == "allow"})

    def candidate(self, session, message):
        namespace = session.get("collaboration", {}).get("outputNamespace")
        if namespace:
            candidate = Path(session["workspace"]) / namespace / "candidate.txt"
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(message["text"])
            self.artifact = candidate
            self.task_id = session["id"]

    def read_artifact(self):
        if self.artifact is None:
            return None
        # Read the actual file at inspection time, not a cached claim.
        data = self.artifact.read_bytes()
        return {"path": str(self.artifact), "text": data.decode(),
                "sha256": hashlib.sha256(data).hexdigest()}

    def admit(self, session, args, guard):
        reason = guard()
        if reason:
            return {"admitted": False, "reason": reason}
        return self.service.collaboration.admission(session["id"], args)

    def record_peer(self, session, args, message, kind):
        from amplifier_operations.coordination import peer_input
        self.sent.append({"sessionId": session["id"],
                          "text": peer_input(message["peerEnvelope"], message["text"]),
                          "inputId": args["inputId"], "kind": kind,
                          "peerEnvelope": copy.deepcopy(message["peerEnvelope"])})

    async def collaboration_input(self, session, args, guard, emit):
        allowed = self.admit(session, args, guard)
        if not allowed["admitted"]:
            return {"accepted": False, "reason": allowed["reason"]}
        message = allowed["message"]
        self.record_peer(session, args, message, "peer")
        await self.begin(session, args["inputId"], emit)
        self.candidate(session, message)
        if session["id"] == self.source_id:
            # A dependency continuation uses the same guarded queue adapter,
            # records its envelope, completes a generation, then returns idle.
            await self.finish(session["id"], "Deterministic continuation received the exact result references.")
        # Task generations remain active for an in-flight correction.
        return {"accepted": True, "inputId": args["inputId"], "completed": False}

    async def collaboration_steer(self, session, args, guard, emit):
        allowed = self.admit(session, args, guard)
        if not allowed["admitted"]:
            return {"accepted": False, "reason": allowed["reason"]}
        context = self.contexts[session["id"]]
        if not context["active"] or context["generationId"] != args["targetGenerationId"]:
            return {"accepted": False, "reason": "The deterministic generation is no longer active."}
        self.record_peer(session, args, allowed["message"], "steer")
        context["inputIds"].append(args["inputId"])
        self.candidate(session, allowed["message"])
        await self.event("runtime.steering", {
            "sessionId": session["id"], "rootSessionId": session["id"],
            "event": "steering.applied", "input_id": args["inputId"],
            "target_generation_id": args["targetGenerationId"],
        }, emit)
        return {"accepted": True, "inputId": args["inputId"], "completed": False}

    async def finish(self, sid, text):
        from amplifier_operations.coordination import fingerprint
        from amplifier_web.automatic_history import display_identity
        context = self.contexts[sid]
        if not context["active"]:
            # Duplicate terminal observation is safe; never replay admission.
            payload = self.terminals[sid]
            await self.event("runtime.generation", payload, context["emit"])
            return payload
        session = self.service._session(sid)
        native_index = len(session.get("generations", []))
        anchor = {"messageId": display_identity(session, native_index, "assistant", text),
                  "nativeIndex": native_index, "textDigest": fingerprint(text),
                  "rootSessionId": sid, "generationId": context["generationId"],
                  "deterministicEmulation": True}
        payload = {"sessionId": sid, "rootSessionId": sid, "generation_id": context["generationId"],
                   "event": "generation.finished", "input_ids": list(context["inputIds"]),
                   "text": text, "nativeTerminal": anchor, "active_job_ids": [],
                   "disposition": "manager_turn_finished"}
        self.terminals[sid] = copy.deepcopy(payload)
        context["active"] = False
        await self.event("runtime.generation", payload, context["emit"])
        await self.event("runtime.status", {"sessionId": sid, "status": "idle"}, context["emit"])
        return payload

    async def message_worker(self, sid, wid, text, input_id=None):
        self.messages.append({"sessionId": sid, "workerId": wid, "text": text, "inputId": input_id})
        await self.service.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "status": "running"})
        return {"accepted": True, "completed": False, "inputId": input_id}

    async def stop_worker(self, sid, wid):
        self.stops.append({"sessionId": sid, "workerId": wid})
        await self.service.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "status": "interrupted"})
        return {"accepted": True, "completed": False}

    async def stop(self, sid):
        self.stops.append({"sessionId": sid})

    async def close(self):
        pass


async def main(home):
    from aiohttp import web
    from amplifier_web.server import create_app
    workspace = home / "workspace"
    workspace.mkdir()
    os.environ["AMPLIFIER_HOME"] = str(home / "shared")
    runtime = Runtime()
    app = await create_app(home / "app", workspace=str(workspace), runtime=runtime, voice=False, background_updates=False)
    app["control_token"] = "fixture-browser-control-token"
    service = runtime.service = app["service"]
    for title in ("Selected conversation", "Other conversation"):
        await service.dispatch("session.create", {"title": title})
    selected = next(row for row in service.state["sessions"] if row["title"] == "Selected conversation")
    other = next(row for row in service.state["sessions"] if row["title"] == "Other conversation")
    runtime.source_id, runtime.other_id = selected["id"], other["id"]
    directory = service.data_dir / "sessions" / selected["id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "effective-configuration.json").write_text(json.dumps({"providers": [], "tools": []}))
    await service.dispatch("session.select", {"id": selected["id"]})
    for wid, title in (("worker-a", "First worker"), ("worker-b", "Second worker")):
        await service.on_runtime_event("worker.updated", {"sessionId": other["id"], "id": wid, "name": title, "kind": "session", "parentSessionId": other["id"], "runId": wid + "-run", "status": "running", "persistent": True})

    async def inspect(request):
        return web.json_response({"selected": selected["id"], "other": other["id"], "messages": runtime.messages, "sent": runtime.sent, "stops": runtime.stops,
            "tasks": [{"id": row["id"], "title": row["title"], "collaboration": row["collaboration"]} for row in service.state["sessions"] if row.get("collaboration")],
            "artifact": runtime.read_artifact(), "grant": runtime.natural_grant,
            "coordination": service.collaboration.current(selected["id"]),
            "humanMessages": [row for row in selected["messages"] if row.get("inputOrigin") in {"ui", "user", "voice"}],
            "contexts": {sid: {key: value for key, value in row.items() if key != "emit"}
                         for sid, row in runtime.contexts.items()},
            "statuses": {row["id"]: row["status"] for row in service.state["sessions"]},
            "events": runtime.events, "terminals": runtime.terminals, "bridgeCalls": runtime.bridge_calls,
            "approvals": runtime.approvals, "approvalResponses": runtime.approval_responses,
            "failures": runtime.failures, "deterministicRuntime": True, "nativeRuntime": False, "providerCalls": False})

    async def artifact(request):
        return web.json_response(runtime.read_artifact())

    async def emit(request):
        data = await request.json()
        await service.on_runtime_event("worker.updated", {"sessionId": other["id"], **data})
        return web.json_response({"ok": True})

    async def peer(request):
        data = await request.json()
        if set(data) != {"actor", "action", "args", "id"} or data["actor"] not in {"source", "recipient"}:
            raise web.HTTPBadRequest(text="Only a scripted actor/action is accepted; transport metadata is runtime-owned.")
        result = await runtime.bridge(data["actor"], data["action"], data["args"], data["id"])
        return web.json_response(result)

    async def finish(request):
        # No request-supplied generation, inputs, text, principal or anchor.
        # Finish only the known task/candidate and settle the initial source
        # before its wait can admit a continuation.
        if await request.json() != {} or not runtime.task_id or not runtime.artifact:
            raise web.HTTPBadRequest(text="A known candidate is required; terminal metadata is runtime-owned.")
        source_context = runtime.contexts.get(runtime.source_id)
        if source_context and source_context["active"]:
            await runtime.finish(runtime.source_id, "Deterministic sender saved its request-specific wait.")
        candidate = runtime.read_artifact()
        terminal = await runtime.finish(runtime.task_id, "Deterministic candidate retained: " + candidate["text"])
        return web.json_response({"terminal": terminal, "deterministicEmulation": True})

    app.router.add_get("/fixture", inspect)
    app.router.add_get("/fixture/artifact", artifact)
    app.router.add_post("/fixture/emit", emit)
    app.router.add_post("/fixture/peer", peer)
    app.router.add_post("/fixture/finish", finish)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    print(json.dumps({"url": url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-coordination-") as directory:
        asyncio.run(main(Path(directory)))
