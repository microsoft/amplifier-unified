"""Exact old-install recovery for the owned Windows launcher.

The launcher never edits application state or invents a restart acknowledgement.
It can restore one unchanged promotion after the failed app has exited. Only a
healthy old host, through the canonical readiness owner, may reopen admission.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time

FILE = "task-app-recovery.json"
LIMIT = 256 * 1024
FIELDS = {"schema", "registration", "restart", "promoted", "previous", "bootstrap",
          "installation", "identity", "graph", "extras", "artifact", "status"}


def pointer(value):
    return {key: copy.deepcopy(item) for key, item in value.items()
            if key not in {"folder", "prefix"}} if value is not None else None


def _validate(value, spec):
    from .app_component_graph import validate_graph
    from .app_updates import validated_extras
    from .windows_task_runtime import restart_record
    if (not isinstance(value, dict) or set(value) != FIELDS
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["registration"] != spec.registration_id
            or not isinstance(value["status"], str)
            or value["status"] not in {"prepared", "restoring", "restored", "acknowledged"}
            or not isinstance(value["identity"], dict)
            or set(value["identity"]) != {"version", "revision", "instanceId"}
            or not isinstance(value["bootstrap"], dict)
            or set(value["bootstrap"]) != {"prefix", "digest"}
            or not isinstance(value["installation"], dict)
            or set(value["installation"]) != {"prefix", "digest"}):
        raise ValueError("Invalid Windows application recovery receipt.")
    restart_record(value["restart"], spec.registration_id)
    graph = validate_graph(value["graph"])
    from .update_readiness import valid_target
    import re
    identity = value["identity"]
    if (not valid_target({**identity, "attemptId": value["restart"]["target"]["attemptId"]})
            or not isinstance(identity["instanceId"], str) or not identity["instanceId"]
            or any(not isinstance(item["prefix"], str) or not item["prefix"]
                   or not isinstance(item["digest"], str) or not re.fullmatch("[a-f0-9]{64}", item["digest"])
                   for item in (value["bootstrap"], value["installation"]))
            or len([row for row in graph if row["name"] == "amplifier-unified"
                    and all(row.get(key) == identity[key] for key in ("version", "revision"))]) != 1):
        raise ValueError("Invalid old application recovery identity.")
    validated_extras(value["extras"])
    return copy.deepcopy(value)


def _write(spec, value):
    from .windows_files import address, directory
    value = _validate(value, spec)
    raw = json.dumps(value).encode()
    if len(raw) > LIMIT:
        raise ValueError("Windows application recovery receipt is too large.")
    home = address(spec.data_dir)
    with directory(home / "config/services", owned_root=home, create=False) as owned:
        owned.publish_private(FILE, raw, replace=True)
    return value


def read(spec):
    from .windows_files import address, directory
    home = address(spec.data_dir)
    try:
        with directory(home / "config/services", owned_root=home, create=False) as owned:
            raw = owned.read_private(FILE, limit=LIMIT)
    except FileNotFoundError:
        return None
    value = json.loads(raw)
    if isinstance(value, dict) and value.get("registration") != spec.registration_id:
        # A new registration never inherits rollback authority.
        return None
    return _validate(value, spec)


def prepare(spec, restart, recovery):
    from .application_generations import active, bootstrap_identity
    from .windows_task_runtime import validate
    validate(restart["run"], spec.registration_id)
    bootstrap = Path(spec.python).parent.parent
    promoted = active(spec.data_dir)
    previous = pointer(recovery["pointer"]) if recovery is not None else None
    immediate_previous = {k: v for k, v in previous.items() if k != "previous"} if previous else None
    if (recovery is None or not promoted
            or any(promoted.get(key) != restart["target"][key] for key in ("revision", "generation"))
            or bootstrap_identity(bootstrap) != recovery["bootstrap"]
            or promoted["bootstrap"] != recovery["bootstrap"]
            or promoted.get("previous") != immediate_previous):
        raise ValueError("The old application recovery binding changed.")
    return _write(spec, {"schema": 1, "registration": spec.registration_id,
        "restart": restart, "promoted": pointer(promoted), "previous": pointer(recovery["pointer"]),
        **{key: recovery[key] for key in ("bootstrap", "installation", "identity", "graph", "extras", "artifact")},
        "status": "prepared"})


def _target(home, value):
    from .application_generations import directory
    if value is None:
        return None
    folder = directory(home, value["revision"], value["generation"])
    return {**value, "folder": folder, "prefix": folder / "tools/amplifier-unified"}


def restore(spec, restart):
    """Called only after the supervisor's exact failed host handle has exited."""
    from . import app_component_graph as components, application_generations as generations
    import subprocess
    value = read(spec)
    if not value or value["restart"] != restart or value["status"] != "prepared":
        raise ValueError("No exact unused Windows recovery binding exists.")
    bootstrap = Path(spec.python).parent.parent
    promoted = generations.active(spec.data_dir)
    if pointer(promoted) != value["promoted"] or generations.bootstrap_identity(bootstrap) != value["bootstrap"]:
        raise ValueError("Application launch target changed before recovery.")
    previous = _target(spec.data_dir, value["previous"])
    prefix = previous["prefix"] if previous else bootstrap
    if generations.bootstrap_identity(prefix) != value["installation"]:
        raise ValueError("The previous installation changed; no rollback was attempted.")
    if previous:
        generations.verify_candidate(previous)
    # Verify the actual old graph, including archive provenance, before selection.
    result = subprocess.run([str(prefix / "Scripts/python.exe"), "-I", "-c", components.GRAPH_PROBE],
                            capture_output=True, text=True, timeout=30, check=False)
    graph = components.normalized(json.loads(result.stdout),
        **({"artifact": value["artifact"]} if value["artifact"] is not None else {})) if result.returncode == 0 else None
    if graph != value["graph"]:
        raise ValueError("The previous application graph changed; no rollback was attempted.")
    _write(spec, {**value, "status": "restoring"})
    generations.restore(spec.data_dir, promoted, previous, bootstrap, expected_bootstrap=value["bootstrap"])
    _write(spec, {**value, "status": "restored"})


def acknowledge(spec, target):
    """Retire rollback authority only for the authenticated exact new host."""
    value = read(spec)
    if (value and value["status"] in {"prepared", "acknowledged"}
            and all(target.get(key) == value["restart"]["target"][key]
                    for key in ("revision", "generation", "attemptId"))):
        _write(spec, {**value, "status": "acknowledged"})


def automatic_retry_held(state):
    """A changed published revision or explicit install can try again."""
    result = state.get("applicationRecovery") or {}
    failed = result.get("failedTarget") or {}
    application = state.get("application") or {}
    return (result.get("phase") == "restart-rollback"
            and isinstance(failed.get("revision"), str)
            and application.get("revision") == failed["revision"])


async def confirm(manager, health):
    """Prove an old serving host after rollback, never credit the failed update."""
    from . import app_component_graph as components, app_updates, application_generations as generations
    from . import windows_service, windows_task_runtime
    from .auth import data_identity
    from .update_readiness import _observed_artifact
    spec = windows_service.update_owner(manager.home)
    if spec is None:
        return False
    value = read(spec)
    state = manager.service.state["updates"]
    pending = copy.deepcopy(state.get("pendingRestart"))
    if (not value or value["status"] != "restored" or not pending
            or any(pending.get(key) != value["restart"]["target"][key] for key in ("revision", "generation", "attemptId"))
            or any(manager.running_identity.get(key) != value["identity"][key] for key in ("version", "revision"))
            or manager.running_identity["instanceId"] == value["identity"]["instanceId"]
            or pointer(generations.active(manager.home)) != value["previous"]
            or generations.bootstrap_identity(sys.prefix) != value["installation"]
            or not isinstance(health, dict) or health.get("ok") is not True
            or health.get("app") != "amplifier-unified" or health.get("dataIdentity") != data_identity(manager.home)
            or any(health.get(key) != manager.running_identity[key] for key in ("version", "revision", "instanceId"))):
        return False
    run = windows_task_runtime.observe(spec)
    if not run or run["run"] == value["restart"]["run"]["run"]:
        return False
    graph = await components.read_graph(app_updates.process, sys.executable,
        **({"artifact": value["artifact"]} if value["artifact"] is not None else {}))
    if graph != value["graph"] or app_updates.installed_extras() != value["extras"]:
        return False
    proof = {"app": next(row for row in graph if row["name"] == "amplifier-unified"),
             "extras": value["extras"], "dependencyDigest": components.digest(
                 [row for row in graph if row["name"] != "amplifier-unified"])}
    if value["artifact"] is not None:
        proof["artifact"] = value["artifact"]
    async with manager.lock:
        async with manager.service.lock:
            if (state.get("pendingRestart") != pending or read(spec) != value
                    or windows_task_runtime.observe(spec) != run
                    or pointer(generations.active(manager.home)) != value["previous"]
                    or generations.bootstrap_identity(sys.prefix) != value["installation"]):
                return False
            observed = _observed_artifact(manager, proof, value["installation"]) if value["artifact"] is not None else None
            if value["artifact"] is not None and observed is None:
                return False
            before = copy.deepcopy(state)
            state.update(phase="error", pendingRestart=None, pendingReplacement=None, pendingApp=None,
                appAvailable=True, error="The new application could not start. The verified previous application was restored.",
                detail="Your previous application is usable. The failed update and its evidence were retained; it will not be retried automatically.")
            state["applicationRecovery"] = {**manager.running_identity, "phase": "restart-rollback",
                "failedTarget": value["restart"]["target"], "verifiedAt": time.time(),
                "qualification": proof, "installation": value["installation"]}
            # Stop the failed whole-install continuation, without changing the
            # user's update preference or pretending the requested update ran.
            if state.get("sequence"):
                state["sequence"] = {**state["sequence"], "install": False}
                state["sequence"].pop("nextStage", None)
            try:
                manager.service._publish_changes(globals={"updates"})
            except BaseException:
                state.clear()
                state.update(before)
                raise
            manager._running_artifact = observed
    manager.diagnostics.record("replacement-recovery", "succeeded",
        observedVersion=manager.running_identity["version"], observedRevision=manager.running_identity["revision"])
    manager.wakeup.set()
    return True