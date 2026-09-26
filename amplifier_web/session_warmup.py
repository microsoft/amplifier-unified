"""Optional preparation after selection, independent of rendering and input."""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path


class SessionWarmup:
    def __init__(self, service):
        self.service = service
        self.pending = {}

    async def schedule(self, identity):
        if self.service.closed or identity in self.pending or not callable(getattr(self.service.runtime, "prewarm", None)):
            return
        task = self.service._task(self.run(identity))
        self.pending[identity] = task
        task.add_done_callback(lambda done: self.pending.pop(identity, None))

    def allowed(self, session):
        from .updates import work_paused
        return (not self.service.closed and not work_paused(self.service.state)
                and not session.get("configurationBusy")
                and not session.get("configurationPending")
                and session.get("workspaceAvailable") is not False
                and not session.get("historyReadOnlyReason")
                and session.get("status") not in {"starting", "working", "running", "stopping"}
                and session.get("ownership", {}).get("status") not in {
                    "blocked", "yielding", "yielded", "yield-failed", "taking-over"}
                and bool(session.get("workspace")) and Path(session["workspace"]).is_dir())

    async def run(self, identity):
        try:
            session = self.service._session(identity)
            if not self.allowed(session):
                return
            await self.service.history.ensure_loaded(identity)
            async with self.service.lock:
                session = self.service._session(identity)
                if not self.allowed(session):
                    return
                source = copy.deepcopy(session)

            preparing = True

            async def emit(kind, payload):
                if not preparing:
                    return await self.service.on_runtime_event(kind, payload)
                if kind == "runtime.status" and payload.get("status") == "ready":
                    # Visiting a saved chat can prepare a fresh worker. Its
                    # readiness is not a new successful turn and cannot erase
                    # the last turn's failure or change its settled status.
                    async with self.service.lock:
                        current = self.service._session(identity)
                        current["preparation"] = {"status": "ready"}
                        if payload.get("report"):
                            current["runtimeReport"] = payload["report"]
                            if payload["report"].get("root_bundle"):
                                current["bundle"] = payload["report"]["root_bundle"]
                        if payload.get("runtimeSessionId"):
                            current["runtimeSessionId"] = payload["runtimeSessionId"]
                        self.service._publish_progress()
                    return
                # Preparation failures are local readiness information, not a
                # failed user turn or a repeated attention notification.
                if kind == "runtime.error" or (kind == "runtime.status" and payload.get("status") in {"starting", "stopped"}):
                    async with self.service.lock:
                        current = self.service._session(identity)
                        current["preparation"] = {"status": "unavailable" if kind == "runtime.error" else "preparing" if payload.get("status") == "starting" else "cold",
                                                  "detail": payload.get("error") or payload.get("detail", "")}
                        self.service._publish_progress()
                    return
                await self.service.on_runtime_event(kind, payload)

            try:
                await self.service.runtime.prewarm(source, emit)
            finally:
                preparing = False
        except asyncio.CancelledError:
            raise
        except Exception:
            # A later explicit interaction reports its actual startup error.
            # Merely visiting a saved chat must leave its history readable.
            return

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
