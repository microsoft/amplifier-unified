"""Installed consumer entry; native setup owns acquisition, not this shortcut.

The entry only composes the existing installer/service/browser boundaries. It
never downloads on reopen, copies accounts, edits trust or force-stops a host.
"""
from __future__ import annotations

import argparse
import asyncio
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys

RECEIPT = "consumer-install.json"


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
    if (result.returncode or normalized(json.loads(result.stdout), artifact=artifact)
            != validate_graph(value["graph"])):
        raise ValueError("The installed application graph differs from setup's qualified selection.")
    return {"ok": True, "version": value["version"], "revision": value["revision"],
            "packageCount": len(value["graph"]), "modelRequests": 0}


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
             "AMPLIFIER_UNIFIED_IMPORT_HOME": "import", "UV_TOOL_DIR": "tools",
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = audit(args.root) if args.audit else asyncio.run(launch(args.root, open_browser=not args.no_open))
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