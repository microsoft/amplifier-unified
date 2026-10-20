"""Private durable host binding for the public native execution suspension API.

The loop owns continuation semantics. This owner stores only its explicit,
versioned records, verifies canonical history, and consumes each record before
effects. A consumed record left by a crash is uncertain, never replayed.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import os
from pathlib import Path
import uuid

from ..deployment import write_private
from ..session_files import validate_id


def fingerprint(value):
    from amplifier_module_loop_streaming.suspension import fingerprint as digest
    return digest(value)


def _write(path, value):
    write_private(path, json.dumps(value, sort_keys=True, allow_nan=False))
    # Renaming a flushed file is not enough to promise survival of a restart.
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ExecutionPause:
    def __init__(self, directory, root_id, store, *, authorize, validate, observe, settle=None):
        self.directory = Path(directory)
        self.root_id, self.store = validate_id(root_id), store
        self.authorize, self.validate, self.observe = authorize, validate, observe
        self.settle = settle
        self.lock = asyncio.Lock()
        self.controllers = {}
        self.unsupported = {}
        path = self.directory / "control.json"
        self.control = json.loads(path.read_text()) if path.exists() else {
            "version": 1, "rootSessionId": root_id, "epoch": None, "held": False}
        if self.control.get("version") != 1 or self.control.get("rootSessionId") != root_id:
            raise ValueError("Maintenance execution ownership changed")

    def _path(self, identity):
        return self.directory / "sessions" / (validate_id(identity) + ".json")

    def read(self, identity):
        path = self._path(identity)
        return json.loads(path.read_text()) if path.exists() else None

    async def _save(self, path, value):
        await asyncio.to_thread(_write, path, copy.deepcopy(value))

    async def request(self, epoch, *, dispatch=True):
        validate_id(epoch)
        async with self.lock:
            if self.control["held"] and self.control["epoch"] != epoch:
                raise ValueError("Another maintenance pause owns this execution")
            candidate = {**self.control, "epoch": epoch, "held": True, "revoked": False}
            await self._save(self.directory / "control.json", candidate)
            self.control = candidate
        for controller in tuple(self.controllers.values()) if dispatch else ():
            runtime = controller.coordinator.get_capability("live.runtime")
            if runtime is not None and not runtime.closed:
                await runtime.request_pause(epoch)
        if dispatch and self.unsupported:
            root = self.controllers.get(self.root_id)
            children = root.coordinator.get_capability("live.children") if root else None
            active = [identity for identity in self.unsupported
                      if identity == self.root_id or children is None or
                      (children.rows.get(identity) or {}).get("status") in {"starting", "running", "idle"}]
            if active:
                raise RuntimeError("An owned execution engine has no safe-boundary suspension API; its current operation is still running")
        return self.snapshot()

    async def revoke(self):
        async with self.lock:
            candidate = {**self.control, "held": True, "revoked": True}
            await self._save(self.directory / "control.json", candidate)
            self.control = candidate

    async def begin_fresh(self):
        """A newly admitted input never silently restores an old continuation."""
        async with self.lock:
            candidate = {**self.control, "epoch": None, "held": False, "revoked": False}
            candidate.pop("resumeReceipt", None)
            await self._save(self.directory / "control.json", candidate)
            self.control = candidate

    async def stop_child(self, identity):
        async with self.lock:
            saved = self.read(identity)
            if not saved or saved.get("state") != "paused":
                raise ValueError("This child has no suspended execution")
            await self._save(self._path(identity), {**saved, "state": "cancelled"})
            native = self.store.load(identity)
            if native:
                self.store.save(identity, native[0], {**native[1], "status": "cancelled"})

    def snapshot(self):
        records = [json.loads(path.read_text()) for path in sorted(
            (self.directory / "sessions").glob("*.json"))]
        root = next((row for row in records if row["sessionId"] == self.root_id), {})
        live = root.get("live") or {}
        generation = live.get("generation") or {}
        current = [row for row in records if row.get("epoch") == self.control["epoch"]]
        work = bool(live.get("turn") or live.get("pending") or live.get("jobs") or live.get("steering"))
        jobs = {row["job_id"]: row for row in live.get("pause_jobs", {}).values()}
        jobs.update({row["job_id"]: row for row in live.get("jobs", [])})
        result_origins = live.get("pending_job_results", {})
        generations = [generation, live.get("pause_origin") or {},
                       *(job.get("generation") or {} for job in jobs.values())]
        origins = {}
        for row in generations:
            if row.get("id"):
                origins.setdefault(row["id"], set()).update(row.get("input_ids", []))
        pending = sorted({row["id"] for row in [*live.get("pending", []), *live.get("steering", [])]
                          if row["id"] not in result_origins})
        inputs = sorted(set(pending) | {identity for values in origins.values() for identity in values})
        terminal = live.get("pause_terminal") or {}
        return {
            "sessionId": self.root_id, "runtimeId": self.root_id,
            "epoch": self.control["epoch"], "checkpointId": root.get("checkpointId"),
            "status": ("stopped" if self.control.get("revoked") else
                       ("suspended" if work else "quiescent") if root.get("state") == "paused" and root.get("live") else
                       "uncertain" if root.get("state") == "consumed" else "waiting"),
            "generationId": generation.get("id"),
            "inputIds": inputs,
            "pendingInputIds": pending,
            "pendingResults": [{"inputId": identity, **origin}
                               for identity, origin in sorted(result_origins.items())],
            "ownedGenerations": [{"generationId": identity, "inputIds": sorted(values)}
                                 for identity, values in sorted(origins.items())],
            "rootGenerationTerminal": ({"generationId": terminal["id"],
                                       "inputIds": sorted(terminal.get("input_ids", []))}
                                      if terminal.get("id") else None),
            "jobOrigins": [{"jobId": job["job_id"],
                            "callId": job["call_id"],
                            "generationId": (job.get("generation") or {}).get("id"),
                            "inputIds": sorted((job.get("generation") or {}).get("input_ids", [])),
                            "status": job.get("status")}
                           for job in jobs.values()],
            "children": [{"sessionId": row["sessionId"], "runtimeId": row["sessionId"],
                          "parentSessionId": row.get("parentSessionId"), "callId": row.get("callId"),
                          "generationId": ((row.get("live") or {}).get("generation") or {}).get("id"),
                          "inputIds": sorted(((row.get("live") or {}).get("generation") or {}).get("input_ids", [])),
                          "checkpointId": row.get("checkpointId"),
                          "status": "suspended" if row.get("state") == "paused" else row.get("state")}
                         for row in current if row["sessionId"] != self.root_id],
        }

    async def prepare_resume(self, epoch):
        runtime = self.controllers[self.root_id].coordinator.get_capability("live.runtime")
        # Worker readiness confirms mounting, not the paused loop checkpoint.
        # Await its acknowledged settled boundary before consuming the host's
        # one-shot resume claim; a concurrent startup checkpoint cannot then
        # arrive behind that claim and falsely replace its phase.
        await runtime.wait_for(lambda event: event["type"] == "session.suspended"
                               and event.get("epoch") == epoch, 30)
        receipt = self.snapshot()
        if (epoch != self.control["epoch"] or not self.control["held"]
                or self.control.get("revoked") or receipt["status"] != "suspended"):
            raise ValueError("No matching suspended execution is available")
        if any(row["status"] != "suspended" for row in receipt["children"]):
            raise ValueError("A paused child changed or was stopped")
        return receipt

    async def resume(self, epoch):
        receipt = await self.prepare_resume(epoch)
        runtime = self.controllers[self.root_id].coordinator.get_capability("live.runtime")
        if await self.authorize(receipt) is not True:
            raise ValueError("Current host policy refused maintenance resumption")
        # Authorization is one-shot. A failed write or lost handoff is uncertain,
        # rather than permission to ask the host for a second claim.
        async with self.lock:
            if self.control.get("revoked") or epoch != self.control["epoch"]:
                raise ValueError("Stop or a newer pause replaced this resumption")
            candidate = {**self.control, "held": False, "resumeReceipt": receipt}
            await self._save(self.directory / "control.json", candidate)
            self.control = candidate
        await runtime.resume_paused(epoch)
        event = await runtime.wait_for(lambda event: event["type"] in {
            "session.resumed", "maintenance.resume_refused"} and event.get("epoch") == epoch, 30)
        if event["type"] != "session.resumed":
            raise ValueError("Native runtime refused the recorded maintenance resumption")
        return {**receipt, "status": "resumed"}

    async def install(self, coordinator, *, restore=None):
        identity = coordinator.session_id
        previous = self.controllers.get(identity)
        if previous is not None and previous.coordinator is coordinator:
            return previous
        loop = coordinator.get("orchestrator")
        if not callable(getattr(loop, "resume", None)):
            if restore:
                raise ValueError("This execution engine has no safe-boundary suspension API")
            self.unsupported[identity] = coordinator
            coordinator.register_capability("web.execution_pause", self)
            return None
        controller = Boundary(self, coordinator)
        self.controllers[identity] = controller
        coordinator.register_capability("execution.suspension", controller)
        coordinator.register_capability("execution.resume", controller.claim)
        coordinator.register_capability("live.suspend", controller.live_checkpoint)
        coordinator.register_capability("live.maintenance_admit", controller.admit)
        coordinator.register_capability("web.execution_pause", self)
        saved = self.read(identity)
        if restore is None:
            restore = self.control["held"]
        if restore and saved and saved.get("epoch") == self.control["epoch"]:
            if saved.get("state") != "paused" or self.control.get("revoked"):
                raise ValueError("Previously claimed or stopped work requires inspection; it was not replayed")
            canonical = self.store.load(identity)
            if canonical is None or fingerprint(canonical[0]) != saved["nativeHistory"]:
                raise ValueError("Native history changed after pause")
            if fingerprint(coordinator.config) != saved["mount"]:
                raise ValueError("Mounted execution configuration changed after pause")
            context = coordinator.get("context")
            await context.set_messages(copy.deepcopy(saved["messages"]))
            if saved.get("frame"):
                coordinator.register_capability("execution.restored", copy.deepcopy(saved["frame"]))
            if saved.get("live"):
                coordinator.register_capability("live.suspended_execution", copy.deepcopy(saved["live"]))
        runtime = coordinator.get_capability("live.runtime")
        if runtime is not None and self.control["held"]:
            await runtime.request_pause(self.control["epoch"])
        return controller


class Boundary:
    def __init__(self, owner, coordinator):
        self.owner, self.coordinator = owner, coordinator

    def requested(self):
        return self.owner.control["held"]

    async def _record(self, *, frame=None, live=None):
        coordinator, owner = self.coordinator, self.owner
        if live is not None and coordinator.session_id == owner.root_id and owner.settle is not None:
            # The native loop owns its turns/jobs. Host-owned naming, memory
            # and control operations must also finish before its final shared
            # history checkpoint can honestly be released.
            await owner.settle()
        if not self.requested() or owner.control.get("revoked"):
            raise ValueError("Maintenance no longer owns this pause")
        checkpoint = coordinator.get_capability("live.checkpoint")
        if not callable(checkpoint):
            raise ValueError("Execution has no canonical checkpoint owner")
        result = checkpoint("paused")
        if inspect.isawaitable(result):
            await result
        messages = await coordinator.get("context").get_messages()
        canonical = owner.store.load(coordinator.session_id)
        if canonical is None:
            raise ValueError("Native checkpoint was not retained")
        async with owner.lock:
            previous = owner.read(coordinator.session_id) or {}
            record = {**previous, "version": 1, "sessionId": coordinator.session_id,
                      "parentSessionId": canonical[1].get("parent_id") or canonical[1].get("parent_session_id"),
                      "callId": canonical[1].get("tool_call_id"),
                      "epoch": owner.control["epoch"], "state": "paused",
                      "checkpointId": (frame or {}).get("id") or previous.get("checkpointId") or uuid.uuid4().hex,
                      "nativeHistory": fingerprint(canonical[0]), "messages": messages,
                      "mount": fingerprint(coordinator.config)}
            if frame is not None:
                record["frame"] = copy.deepcopy(frame)
            if live is not None:
                record["live"] = copy.deepcopy(live)
                if not live.get("turn"):
                    record.pop("frame", None)
            await owner._save(owner._path(coordinator.session_id), record)
        return True

    async def checkpoint(self, frame):
        return await self._record(frame=frame)

    async def live_checkpoint(self, envelope):
        if not self.requested():
            # A persistent child remounts its already-retained paused envelope
            # before the original delegate owner explicitly resumes it. This
            # observes that exact proof; it does not create another checkpoint
            # or release the child after the root's one-shot resume claim.
            saved = self.owner.read(self.coordinator.session_id)
            if (self.coordinator.session_id == self.owner.root_id or not saved
                    or saved.get("state") != "paused"
                    or saved.get("epoch") != self.owner.control["epoch"]
                    or fingerprint(saved.get("live")) != fingerprint(envelope)
                    or not await self.admit(self.owner.control["epoch"])):
                raise ValueError("Maintenance no longer owns this pause")
            return True
        await self._record(live=envelope)
        if self.coordinator.session_id == self.owner.root_id:
            # The host owner acknowledges this bounded proof before the runtime
            # reports quiescence. Private frames/history never cross the bridge.
            if await self.owner.observe(self.owner.snapshot()) is not True:
                raise ValueError("The host did not acknowledge this maintenance checkpoint")
        else:
            # A persistent delegated session releases its own native loop. The
            # original delegate remains responsible for its eventual result.
            runtime = self.coordinator.get_capability("live.runtime")
            await runtime.release_paused(self.owner.control["epoch"])
        return True

    async def admit(self, epoch):
        owner = self.owner
        if (owner.control["held"] or owner.control.get("revoked")
                or epoch != owner.control["epoch"]):
            return False
        return await owner.validate(owner.control.get("resumeReceipt") or {}) is True

    async def claim(self, frame):
        owner = self.owner
        if not await self.admit(owner.control["epoch"]):
            return False
        async with owner.lock:
            saved = owner.read(self.coordinator.session_id)
            if (not saved or saved.get("state") != "paused" or saved.get("frame") != frame
                    or owner.control["held"] or owner.control.get("revoked")):
                return False
            await owner._save(owner._path(self.coordinator.session_id),
                              {**saved, "state": "consumed"})
        return True
