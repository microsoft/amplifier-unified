"""Explicit first-install runtime preparation, without a user conversation.

The updater owns graph resolution, dependency preparation, freezing and profile
probes. This entry reuses that pipeline under the exclusive installation lock.
It never sends a prompt, starts a provider inference, or refreshes a recorded
generation. Existing installations continue through the normal update owner.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from . import __version__


def _write(home, path, value):
    from .deployment import write_private
    write_private(path, json.dumps(value) + "\n", owned_root=home)


class _Preparation:
    """The small preparation interface consumed by UpdateManager.validate."""
    def __init__(self, home, stage):
        self.home, self.stage = home, stage
        self.inventory = []
        self.diagnostics = self
        self.state = {"attemptId": stage.name}
        self.events = []

    async def publish(self, **values):
        # Do not save arbitrary progress details, source URLs, plans or output.
        return None

    async def stage_cache_sources(self, stage, rows):
        from .updates import UpdateManager
        return await UpdateManager.stage_cache_sources(self, stage, rows)

    def record(self, phase, status, **facts):
        self.events.append({"phase": phase, "status": status})
        self.events = self.events[-64:]
        _write(self.home, self.stage / "install-progress.json",
               {"schema": 1, "attempt": self.stage.name, "events": self.events})

    async def run(self, phase, function, *args, **kwargs):
        self.record(phase, "running")
        try:
            result = function(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
        except BaseException:
            self.record(phase, "failed")
            raise
        self.record(phase, "succeeded")
        return result


def _inspect(home, generation):
    from .runtime_environment import project_path, receipt_directory
    from .runtime_qualification import verify_recorded
    receipt = receipt_directory(home, generation)
    try:
        marker = json.loads((receipt / "profiles-qualified.json").read_text())
        project = project_path(home, generation)
        verify_recorded(project, receipt)
        graph = json.loads((receipt / "runtime-installed.json").read_text())
        profiles = marker["profiles"]
        if not isinstance(profiles, list) or not profiles or not graph:
            raise ValueError("Incomplete default runtime receipt.")
        return {"ok": True, "prepared": True, "generation": generation,
                "packageCount": len(graph), "profileCount": len(profiles),
                "lockSha256": hashlib.sha256((receipt / "runtime.lock").read_bytes()).hexdigest(),
                "modelRequestSent": False}
    except (OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "prepared": False, "generation": generation,
                "message": "Recorded default runtime needs attention; no repair or refresh was attempted."}


def status(home):
    """Inspect exact installed graph evidence; never resolve or prepare."""
    from .updates import active_release
    generation = active_release(home).get("current")
    if not generation:
        return {"ok": False, "prepared": False, "message": "Default runtime has not been prepared."}
    return _inspect(home, generation)


async def prepare(home):
    from .updates import UpdateManager, active_release
    from .runtime_environment import receipt_directory
    from .reset import repair_lock
    from .windows_files import directory
    home = Path(home)
    if os.name == "nt":
        with directory(home, owned_root=home, create=True):
            pass
    # A running host holds this lock shared; installation cannot mutate beneath
    # it or replace another preparation. Context manager unlocks on failure.
    with repair_lock(home):
        if active_release(home).get("current"):
            return await asyncio.to_thread(status, home)
        generation = uuid.uuid4().hex
        stage = receipt_directory(home, generation)
        if os.name == "nt":
            with directory(stage, owned_root=home, create=True):
                pass
        else:
            stage.mkdir(parents=True, mode=0o700)
        owner = _Preparation(home, stage)
        try:
            await UpdateManager.validate(owner, stage, generation)
            result = await asyncio.to_thread(_inspect, home, generation)
            if not result["ok"]:
                raise RuntimeError("Prepared runtime did not match its recorded installation.")
            # validate froze the complete graph and passed each offered profile's
            # no-model mount probe. Activation is initial-only under our lock.
            if active_release(home).get("current"):
                raise RuntimeError("Runtime pointer changed during initial preparation.")
            _write(home, stage / "validated.json", {
                "generationSchema": 1, "workerProtocol": 1, "hostVersion": __version__,
                "sources": 1, "createdAt": time.time(), "initialInstallation": True})
            _write(home, home / "updates/active.json",
                   {"current": generation, "previous": None, "at": time.time()})
        except BaseException:
            owner.record("initial-installation", "failed")
            raise
        owner.record("initial-installation", "succeeded")
        return result