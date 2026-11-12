"""Explicit first-install runtime preparation, without a user conversation.

The updater owns configured graph preparation, freezing and profile probes.
Accountless setup prepares only the declared baseline and reports that an AI
connection and conversation-worker qualification are still required.
It never sends a prompt, starts a provider inference, or refreshes a recorded
generation. Existing installations continue through the normal update owner.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
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
        except BaseException as error:
            self.record(phase, "failed")
            from .update_diagnostics import exception_facts
            facts = exception_facts(error)
            detail = getattr(error, "diagnostic_facts", {})
            for key in ("exitCode", "stdoutBytes", "stderrBytes", "durationMs"):
                if type(detail.get(key)) is int:
                    facts[key] = detail[key]
            _write(self.home, self.stage / "install-failure.json",
                   {"schema": 1, "attempt": self.stage.name, "phase": phase, **facts})
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
        return _baseline_status(Path(home))
    return _inspect(home, generation)


def _needs_connection(home):
    from .host.config import read_config
    # Ordinary new installations have no connection yet. Do not invent a
    # credential/provider selection merely to pass the updater's mount probe.
    return not read_config(home, home=home, global_only=True).providers


def _baseline_status(home):
    from .runtime_environment import project_path, receipt_directory
    from .runtime_qualification import verify_recorded, verify_install_overrides
    receipt = receipt_directory(home)
    try:
        value = json.loads((receipt / "installation-prepared.json").read_text())
        project = project_path(home)
        lock = (receipt / "runtime.lock").read_bytes()
        if (not isinstance(value, dict) or value.get("schema") != 1
                or value.get("lockSha256") != hashlib.sha256(lock).hexdigest()
                or (project / "uv.lock").read_bytes() != lock
                or (project / "pyproject.toml").read_bytes() != (receipt / "runtime.toml").read_bytes()):
            raise ValueError("Initial dependency receipt changed.")
        graph = json.loads((receipt / "runtime-installed.json").read_text())
        if (not isinstance(graph, list) or not graph
                or any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
                       or not isinstance(row.get("version"), str) for row in graph)):
            raise ValueError("No valid initial dependency inventory.")
        verify_recorded(project, receipt, allow_additions=True)
        verify_install_overrides(project, receipt / "runtime-install-overrides.txt")
        return {"ok": True, "prepared": True, "generation": None,
                "packageCount": len(graph), "profileCount": 0,
                "lockSha256": value["lockSha256"], "connectionRequired": True,
                "workerReady": False, "modelRequestSent": False}
    except FileNotFoundError:
        return {"ok": False, "prepared": False, "message": "Default runtime has not been prepared."}
    except (OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "prepared": False,
                "message": "Initial dependency evidence needs attention; no refresh was attempted."}


async def _prepare_baseline(home):
    from .runtime_environment import prepare_project, receipt_directory, declared_manifest
    from .runtime_qualification import installed_graph, prepare_overrides
    from .updates import process
    from .deployment import write_private
    receipt = receipt_directory(home)
    if (receipt / "installation-prepared.json").exists():
        return await asyncio.to_thread(_baseline_status, home)
    if any((receipt / name).exists() for name in
           ("runtime.toml", "runtime.lock", "runtime-installed.json", "runtime-install-policy.json")):
        # A missing final marker does not authorize re-resolving a recorded or
        # partially published graph. Preserve the original evidence for repair.
        return {"ok": False, "prepared": False,
                "message": "Initial dependency evidence is incomplete; no refresh was attempted."}
    project = prepare_project(home)
    owner = _Preparation(home, receipt)
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("Install uv to prepare the default runtime.")
    await owner.run("initial-runtime-lock", process, uv, "lock", "--project", str(project),
                    "--python", "3.13", timeout=900)
    await owner.run("initial-runtime-install", process, uv, "sync", "--locked",
                    "--project", str(project), "--python", "3.13", timeout=900)
    await owner.run("initial-runtime-check", process, uv, "pip", "check", "--python",
                    str(project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
                    timeout=90)
    graph = await asyncio.to_thread(installed_graph, project)
    if not graph or not any(row["name"] == "amplifier-core" for row in graph):
        raise RuntimeError("Initial runtime dependencies are incomplete.")
    await owner.run("initial-runtime-policy", prepare_overrides, project,
                    receipt / "runtime-install-overrides.txt")
    for name, raw in (("runtime.toml", declared_manifest()),
                      ("runtime.lock", (project / "uv.lock").read_bytes())):
        write_private(receipt / name, raw, owned_root=home)
    _write(home, receipt / "runtime-installed.json", graph)
    _write(home, receipt / "installation-prepared.json",
           {"schema": 1, "lockSha256": hashlib.sha256((project / "uv.lock").read_bytes()).hexdigest(),
            "connectionRequired": True, "workerReady": False})
    return await asyncio.to_thread(_baseline_status, home)


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
        if _needs_connection(home):
            return await _prepare_baseline(home)
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