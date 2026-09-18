"""Linux systemd --user integration for the generated Amplifier Unified unit."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import time

from .deployment import write_private

MARKER = "# Managed by amplifier-unified; do not edit."
UNIT_NAME = "amplifier-unified.service"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def managed_unit(data_dir: Path | None = None) -> bool:
    path = unit_path()
    if not path.is_file():
        return False
    content = path.read_text(errors="replace")
    return MARKER in content and (data_dir is None or str(Path(data_dir).expanduser().resolve()) in content)


def _systemctl(*args: str, check: bool = True):
    if sys.platform != "linux" or not shutil.which("systemctl"):
        raise RuntimeError("This command requires Linux systemd user services.")
    return subprocess.run(["systemctl", "--user", *args], check=check)


def _backup(path: Path, content: str) -> None:
    timestamp = int(time.time())
    for suffix in range(1_000):
        separator = "" if suffix == 0 else f"-{suffix}"
        backup = path.with_suffix(path.suffix + f".backup-{timestamp}{separator}")
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return
    raise RuntimeError(f"Could not create a unique backup for {path}")


def install(data_dir: Path, workspace: str | Path | None = None, *, replace: bool = False) -> None:
    # The runtime discovers uv by PATH, just like the installer's shell. A
    # fixed short PATH loses Snap, Homebrew, or custom installs. Preserve
    # lexical paths: /snap/bin/uv is a dispatcher, not its resolved target.
    service_path = os.environ.get("PATH") or f"{Path.home() / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    if any(ord(char) < 32 or ord(char) == 127 for char in service_path):
        raise ValueError("PATH must not contain control characters.")
    # Escape systemd's quoted assignment and percent specifiers, not shell syntax.
    service_path = service_path.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    path = unit_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        previous = path.read_text()
    except FileNotFoundError:
        previous = None
    if previous is not None:
        if MARKER not in previous and not replace:
            raise RuntimeError(f"Refusing to replace unmanaged unit: {path}")
        if not replace:
            raise RuntimeError(f"Generated unit already exists: {path}. Re-run with --replace to back it up and replace it.")
        _backup(path, previous)
    executable = shutil.which("amplifier-unified")
    command = [executable] if executable else [sys.executable, "-m", "amplifier_web"]
    command += ["--no-open", "--data-dir", str(Path(data_dir).expanduser().resolve()),
                "--workspace", str(Path(workspace or Path.cwd()).expanduser().resolve()), "serve"]
    content = f"""{MARKER}
[Unit]
Description=Amplifier Unified
After=network.target

[Service]
Type=simple
ExecStart={" ".join(shlex.quote(item) for item in command)}
Restart=on-failure
RestartSec=5
Environment="PATH={service_path}"

[Install]
WantedBy=default.target
"""
    write_private(path, content)
    _systemctl("daemon-reload")
    _systemctl("enable", UNIT_NAME)
    # enable --now leaves an already-running host's old environment in place.
    _systemctl("restart", UNIT_NAME)


def uninstall() -> None:
    path = unit_path()
    if not path.exists():
        return
    if not managed_unit():
        raise RuntimeError(f"Refusing to remove unmanaged unit: {path}")
    _systemctl("disable", "--now", UNIT_NAME)
    path.unlink(missing_ok=True)
    _systemctl("daemon-reload")


def current_process_is_unit_managed(data_dir: Path) -> bool:
    """Prove this process, rather than merely the installation, belongs to our unit."""
    if not os.environ.get("INVOCATION_ID") or not managed_unit(data_dir):
        return False
    try:
        cgroups = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError:
        return False
    return any(UNIT_NAME in line.rsplit(":", 1)[-1].split("/") for line in cgroups)


def command(name: str) -> None:
    if not managed_unit():
        raise RuntimeError("Amplifier Unified's generated service is not installed.")
    if name == "start":
        _systemctl("start", UNIT_NAME)
    elif name == "stop":
        _systemctl("stop", UNIT_NAME)
    elif name == "restart":
        _systemctl("restart", UNIT_NAME)
    elif name == "status":
        _systemctl("status", UNIT_NAME, "--no-pager", check=False)
    elif name == "logs":
        if sys.platform != "linux" or not shutil.which("systemctl") or not shutil.which("journalctl"):
            raise RuntimeError("This command requires Linux systemd user services and journalctl.")
        subprocess.run(["journalctl", "--user", "-u", UNIT_NAME, "--no-pager"], check=False)
    else:
        raise ValueError(f"Unknown service command: {name}")