"""Installed consumer entry; native setup owns acquisition, not this shortcut.

The entry only composes the existing installer/service/browser boundaries. It
never downloads on reopen, copies accounts, edits trust or force-stops a host.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys

RECEIPT = "consumer-install.json"
QUALIFICATION = "consumer-qualified.json"


def _receipt_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def installation(root):
    from .windows_files import address, directory
    from .app_release_artifacts import validate_descriptor, source_revision
    root = address(root)
    with directory(root, owned_root=root) as owned:
        value = json.loads(owned.read_private(RECEIPT, limit=256 * 1024))
    if (not isinstance(value, dict) or set(value) != {
            "schema", "kind", "version", "revision", "artifact", "graph", "uv", "git"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["kind"] != "windows-consumer-preview"):
        raise ValueError("The consumer installation receipt is invalid.")
    artifact = validate_descriptor(value["artifact"], value["version"])
    if Path(artifact["path"]) != root / Path(artifact["path"]).name:
        raise ValueError("The installation artifact belongs to another root.")
    prefix = root / "tools/amplifier-unified"
    dist = metadata.distribution("amplifier-unified")
    if (Path(sys.prefix) != prefix or Path(dist.locate_file("amplifier_web")) != Path(__file__).parent
            or dist.version != value["version"] or source_revision(dist) != value["revision"]):
        raise ValueError("Use this consumer installation's original shortcut.")
    expected = {"uv": root / "uv/uv.exe", "git": root / "git/cmd/git.exe"}
    from .windows_read_files import open_file
    for name, path in expected.items():
        if value[name] != str(path):
            raise ValueError("The private helper binding changed.")
        with directory(path.parent, owned_root=root), open_file(path) as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("The installation's private helper is missing.")
    return root, value


def audit(root):
    """Setup-only full audit; an ordinary shortcut does not reinstall or resolve."""
    from .app_release_artifacts import verify
    from .manual_app_handoff import _payload
    from .app_component_graph import GRAPH_PROBE, normalized, validate_graph
    root, value = installation(root)
    artifact = verify(value["artifact"], value["version"], value["revision"])
    # Existing native payload verifier owns RECORD/archive/member checks.
    _payload({"artifact": artifact, "binding": {
        "version": value["version"], "revision": value["revision"]}})
    result = subprocess.run([sys.executable, "-I", "-c", GRAPH_PROBE],
                            capture_output=True, text=True, timeout=30, check=False)
    graph = normalized(json.loads(result.stdout), artifact=artifact) if result.returncode == 0 else None
    if graph != validate_graph(value["graph"]):
        raise ValueError("The installed application graph differs from setup's qualified selection.")
    from .application_generations import bootstrap_identity
    from .app_component_graph import digest
    from .app_updates import installed_extras
    from .deployment import write_private
    proof = {"schema": 1, "receiptDigest": _receipt_digest(value),
             "installation": bootstrap_identity(sys.prefix),
             "qualification": {
                 "app": next(row for row in graph if row["name"] == "amplifier-unified"),
                 "artifact": artifact, "extras": installed_extras(),
                 "dependencyDigest": digest([row for row in graph if row["name"] != "amplifier-unified"])}}
    # Produced only AFTER the full setup audit, never a restart ACK or pointer.
    write_private(root / QUALIFICATION, json.dumps(proof), owned_root=root)
    return {"ok": True, "version": value["version"], "revision": value["revision"],
            "packageCount": len(value["graph"]), "modelRequests": 0}


def delivery_proof(home):
    """Read first-install qualification, bound to this exact consumer bootstrap."""
    from .windows_files import address, directory
    from .app_component_graph import digest, validate_graph
    home = address(home)
    root = home.parent
    if home != root / "app":
        return None
    root, receipt = installation(root)
    with directory(root, owned_root=root) as owned:
        proof = json.loads(owned.read_private(QUALIFICATION, limit=256 * 1024))
    if (not isinstance(proof, dict) or set(proof) != {
            "schema", "receiptDigest", "installation", "qualification"}
            or type(proof["schema"]) is not int or proof["schema"] != 1
            or proof["receiptDigest"] != _receipt_digest(receipt)):
        raise ValueError("The consumer setup qualification changed.")
    binding = proof["installation"]
    if (not isinstance(binding, dict) or set(binding) != {"prefix", "digest"}
            or not isinstance(binding["prefix"], str)
            or binding["prefix"] != str(root / "tools/amplifier-unified")
            or not isinstance(binding["digest"], str)
            or not re.fullmatch("[a-f0-9]{64}", binding["digest"])):
        raise ValueError("The consumer audit installation binding is missing or invalid.")
    graph = validate_graph(receipt["graph"])
    qualification = proof["qualification"]
    if (not isinstance(qualification, dict) or set(qualification) != {
            "app", "artifact", "extras", "dependencyDigest"}
            or qualification["artifact"] != receipt["artifact"]
            or [qualification["app"]] != [row for row in graph if row["name"] == "amplifier-unified"]
            or qualification["dependencyDigest"] != digest(
                [row for row in graph if row["name"] != "amplifier-unified"])):
        raise ValueError("The consumer setup graph qualification changed.")
    return proof


def environment(root, value):
    from .windows_files import directory
    env = dict(os.environ)
    for name in list(env):
        if name.upper().startswith(("UV_", "PIP_")) or name.upper() in {
                "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}:
            del env[name]
    env.update({"UV_NO_CONFIG": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    roots = {"AMPLIFIER_WEB_HOME": "app", "AMPLIFIER_HOME": "shared",
             "AMPLIFIER_SESSION_STATE_HOME": "ownership",
             "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": "capture",
             "AMPLIFIER_UNIFIED_IMPORT_HOME": "import", "AMPLIFIER_SOURCE_STORE": "source-store",
             "UV_TOOL_DIR": "tools",
             "UV_TOOL_BIN_DIR": "tool-bin", "UV_CACHE_DIR": "cache",
             "UV_PYTHON_INSTALL_DIR": "python", "UV_PYTHON_BIN_DIR": "python-bin",
             "TEMP": "temp", "TMP": "temp"}
    for name, suffix in roots.items():
        with directory(root / suffix, owned_root=root, create=False):
            pass
        env[name] = str(root / suffix)
    env["PATH"] = os.pathsep.join([str(Path(value["uv"]).parent), str(Path(value["git"]).parent),
        str(root / "tool-bin"), str(root / "tools/amplifier-unified/Scripts"),
        str(Path(sys._base_executable).parent), env.get("PATH", "")])
    return env


async def launch(root, *, open_browser=True):
    from .windows_installation import prepare_background, owner_notice
    root, value = installation(root)
    env = environment(root, value)
    os.environ.update(env)  # Own shortcut process only; no saved settings.
    launcher = root / "tools/amplifier-unified/Scripts/amplifier-unified.exe"
    result = await prepare_background(root, launcher, env)
    if open_browser:
        # Never silently import a CA or tell a user to bypass a browser warning.
        from . import browser_entry
        from .deployment import load_server_config
        config = load_server_config(root / "app")
        _urls, pin = browser_entry._owner_probe_targets(root / "app", config)
        if pin is None:
            raise RuntimeError("The installation's private HTTPS identity is unavailable.")
        # The normal owner entry controls browser authentication and trust
        # behavior. Do not print or expose its transient grant in this process.
        from .updates import process
        try:
            await process(str(launcher), "--data-dir", str(root / "app"),
                          "--workspace", str(root / "workspace"), "open", env=env, timeout=30)
        except (OSError, ValueError, RuntimeError, TimeoutError):
            owner_notice("Amplifier is running in the background, but browser entry did not finish. "
                         "Use the installation's retained status information to inspect the result. "
                         "No certificate trust or security policy was changed.")
    return result


async def maintenance(root, operation):
    from .windows_installation import owner_notice
    from .installation_health import status
    from .windows_service import command, uninstall
    root, value = installation(root)
    os.environ.update(environment(root, value))
    if operation == "status":
        result = await status(root / "app")
        if sys.stdout is None:
            owner_notice(result["message"])
        return result
    if operation not in {"stop", "uninstall"}:
        raise ValueError("Unknown consumer operation.")
    if not owner_notice(
            "Stop Amplifier's background app?" if operation == "stop" else
            "Remove Amplifier's start-at-sign-in registration and stop its background app? "
            "Your accounts, settings, history and installation files will be retained.",
            confirm=True):
        return {"ok": True, "cancelled": True}
    if operation == "uninstall":
        from . import windows_consumer_shell
        def remove():
            if (root / windows_consumer_shell.FILE).exists():
                # Prepare and retain every shell object BEFORE touching the
                # Scheduler task. Conflicts leave all registrations unchanged.
                with windows_consumer_shell.removal(root) as finish:
                    # Registry transaction commit can detect a concurrent key
                    # edit. Settle that conflict BEFORE removing the task.
                    finish()
                    uninstall(root / "app")
            else:
                uninstall(root / "app")
        await asyncio.to_thread(remove)
    else:
        await asyncio.to_thread(command, "stop", root / "app")
    return {"ok": True, "operation": operation, "userDataRetained": True,
            "installationFilesRetained": True}

def integrate(root):
    """Setup-only OS entries after source/payload/graph and dependency checks."""
    root, value = installation(root)
    if delivery_proof(root / "app") is None:
        raise ValueError("The consumer payload audit has not qualified this installation.")
    from .windows_consumer_shell import install
    return install(root, value["version"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--audit", action="store_true")
    operations.add_argument("--integrate", action="store_true")
    operations.add_argument("--status", action="store_true")
    operations.add_argument("--stop", action="store_true")
    operations.add_argument("--uninstall", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        operation = next((name for name in ("status", "stop", "uninstall") if getattr(args, name)), None)
        result = (audit(args.root) if args.audit else integrate(args.root) if args.integrate
                  else asyncio.run(maintenance(args.root, operation))
                  if operation else asyncio.run(launch(args.root, open_browser=not args.no_open)))
        if sys.stdout is not None:
            print(json.dumps(result))
    except (OSError, ValueError, RuntimeError, TimeoutError, metadata.PackageNotFoundError) as error:
        from .windows_installation import owner_notice
        if sys.stdout is None:
            owner_notice("Amplifier could not complete this operation. Its private setup/service evidence "
                         "is retained. No automatic retry, force-stop or security-policy change was attempted.")
        else:
            print(json.dumps({"ok": False, "errorType": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()