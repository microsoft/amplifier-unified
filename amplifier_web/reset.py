"""Dependency-free emergency repair, also runnable with ``python3 reset.py``.

Only named, regenerable installation paths are retired. Unknown/user state is
never classified as disposable. Backups are kept until the owner removes them.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack, suppress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import uuid

REPOSITORY = "microsoft/amplifier-unified"
REGENERABLE = ("updates", "runtime", "foundation", "source-store")
UNIT = "amplifier-unified.service"
LABEL = "com.microsoft.amplifier-unified"


def options(parser):
    parser.add_argument("--yes", "-y", action="store_true", help="Confirm the displayed repair plan")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be repaired without changing anything")
    parser.add_argument("--source", help="Override the latest published release with a trusted wheel or Git source")
    parser.add_argument("--no-start", action="store_true", help="Leave the managed service stopped after repair")
    # Also accept the customary post-command spelling, retaining a global value.
    parser.add_argument("--data-dir", default=argparse.SUPPRESS, help="Unified data directory to repair")


def home_path(args):
    return Path(getattr(args, "data_dir", None) or os.environ.get("AMPLIFIER_WEB_HOME")
                or os.environ.get("AMPLIFIER_WEB_DATA_DIR") or Path.home() / ".amplifier-unified").expanduser().resolve()


def command(*args, env=None, capture=False, check=True, timeout=120):
    process = subprocess.Popen(list(map(str, args)), env=env, text=True,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        # A timed-out installer must not keep writing after rollback begins.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # The parent can exit while a build child ignores SIGTERM.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    result = subprocess.CompletedProcess(list(args), process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def environment():
    env = os.environ.copy()
    # Never inherit a worker's generation, a development Python path, or uv's
    # project overrides into a recovery installation. Keep tool/bin ownership.
    for key in list(env):
        if key.startswith(("PYTHON", "UV_")) and key not in {
            "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_PYTHON_INSTALL_DIR", "UV_NATIVE_TLS",
        }:
            env.pop(key, None)
    for key in ("VIRTUAL_ENV", "AMPLIFIER_UNIFIED_RELEASE", "AMPLIFIER_APP_GENERATION_EXEC"):
        env.pop(key, None)
    env["UV_NO_CONFIG"] = "1"
    env["UV_NO_CACHE"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if shutil.which("gh"):
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        env.update({f"GIT_CONFIG_KEY_{count}": "credential.https://github.com.helper",
                    f"GIT_CONFIG_VALUE_{count}": "!gh auth git-credential",
                    "GIT_CONFIG_COUNT": str(count + 1)})
    return env


def release_source(env):
    release = json.loads(command("gh", "api", f"repos/{REPOSITORY}/releases/latest",
                                 env=env, capture=True, timeout=30).stdout)
    tag = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("No supported published release was found; use --source with a trusted release wheel.")
    remote = f"https://github.com/{REPOSITORY}"
    output = command("git", "ls-remote", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}",
                     env=env, capture=True, timeout=60).stdout
    rows = dict((ref, sha) for sha, ref in (line.split() for line in output.splitlines()))
    sha = rows.get(f"refs/tags/{tag}^{{}}", rows.get(f"refs/tags/{tag}", ""))
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("The published release could not be resolved to an exact commit.")
    return f"git+{remote}@{sha}"


def service_commands(home):
    """Recognize only our generated service, with an exact data-directory arg."""
    import plistlib
    import shlex

    if sys.platform == "darwin":
        path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
        if not path.exists():
            return None
        data = plistlib.loads(path.read_bytes())
        if data.get("Label") != LABEL or data.get("AmplifierUnifiedMarker") != "amplifier-unified launch agent v1":
            raise ValueError("The launch agent is unmanaged; stop it manually before repairing its installation.")
        args = data.get("ProgramArguments", [])
        stop = ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]
        start = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]
        loaded = ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]
    elif sys.platform == "linux":
        path = Path.home() / ".config/systemd/user" / UNIT
        if not path.exists():
            return None
        text = path.read_text()
        if "# Managed by amplifier-unified; do not edit." not in text:
            raise ValueError("The service is unmanaged; stop it manually before repairing its installation.")
        lines = [line[len("ExecStart="):] for line in text.splitlines() if line.startswith("ExecStart=")]
        args = shlex.split(lines[0]) if len(lines) == 1 else []
        stop = ["systemctl", "--user", "stop", UNIT]
        start = ["systemctl", "--user", "start", UNIT]
        loaded = None
    else:
        raise ValueError("Reset supports Linux, WSL and macOS. Use WSL for Windows installations.")
    if "--data-dir" not in args or args.index("--data-dir") + 1 >= len(args):
        raise ValueError("The service data directory could not be verified.")
    owned = Path(args[args.index("--data-dir") + 1]).expanduser().resolve()
    if owned != home:
        raise ValueError(f"The service uses {owned}. Run reset --data-dir with that directory.")
    return stop, start, loaded


@contextmanager
def repair_lock(home, *, shared=False):
    import fcntl
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / ".reset.lock"
    # Never unlink a lock: waiters must continue to agree on the same inode.
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A host or reset is already using this data directory. Stop manually launched hosts before retrying.") from None
        yield


def reset_update_state(home, backup):
    """Retire saved update fences without rewriting chats, resources or receipts."""
    path = home / "app.sqlite3"
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError("The application database is external; refusing to alter it.")
    with sqlite3.connect(path, timeout=5) as db:
        with sqlite3.connect(backup / "app.sqlite3") as saved:
            db.backup(saved)
        (backup / "app.sqlite3").chmod(0o600)
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "state" in tables:
            row = db.execute("SELECT value FROM state WHERE id=1").fetchone()
            if row:
                state = json.loads(row[0])
                state.pop("updates", None)
                db.execute("UPDATE state SET value=? WHERE id=1", (json.dumps(state),))
        if "state_records" in tables:
            db.execute("DELETE FROM state_records WHERE kind='global' AND id='updates'")


def verify(prefix, env):
    python = prefix / "bin/python"
    command(python, "-I", "-c", "from amplifier_web.app_updates import PROBE; exec(PROBE)",
            env=env, timeout=120)


def assert_no_processes(prefix, home):
    """Older hosts/workers predate the shared lease; never remove their files."""
    output = command("ps", "-axo", "pid=,command=", capture=True).stdout
    roots = (str(prefix) + "/", str(home / "runtime") + "/", str(home / "updates/applications") + "/")
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or int(parts[0]) == os.getpid():
            continue
        text = parts[1]
        if any(root in text for root in roots) or (
            "amplifier_web" in text and str(home) in text
        ):
            raise RuntimeError(f"Process {parts[0]} still uses this installation. Stop its host/workers and retry.")


def repair(home, uv, tool_root, bin_root, source, env, service, no_start):
    prefix = tool_root / "amplifier-unified"
    launcher = bin_root / "amplifier-unified"
    backup = home / "reset-backups" / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700)
    tool_backup = tool_root / (".amplifier-unified-reset-" + backup.name)
    moves = []
    had_launcher = launcher.exists() or launcher.is_symlink()
    # A public launcher may be a symlink or an installer-created script.
    if had_launcher:
        shutil.copy2(launcher, backup / "launcher", follow_symlinks=False)
    (backup / "manifest.json").write_text(json.dumps({
        "dataDir": str(home), "tool": str(prefix), "toolBackup": str(tool_backup),
        "launcher": str(launcher), "regenerable": list(REGENERABLE), "source": source,
    }, indent=2) + "\n")
    print(f"Recovery backup: {backup}", flush=True)
    stopped = False
    installing = False
    locks = ExitStack()
    try:
        if service:
            stop, _, loaded = service
            if loaded is None or command(*loaded, capture=True, check=False).returncode == 0:
                command(*stop)
            stopped = True
        locks.enter_context(repair_lock(home))
        assert_no_processes(prefix, home)
        if prefix.exists() or prefix.is_symlink():
            prefix.rename(tool_backup)
            moves.append((prefix, tool_backup))
        # An empty target forces a genuinely fresh environment even if receipt
        # metadata or installed package files were manually modified.
        installing = True
        command(uv, "tool", "install", "--force", "--python", "3.13", "--from", source, "amplifier-unified",
                env=env, timeout=1800)
        verify(prefix, env)
        for name in REGENERABLE:
            path = home / name
            if path.exists() or path.is_symlink():
                path.rename(backup / name)
                moves.append((path, backup / name))
        reset_update_state(home, backup)
    except BaseException:
        # Preserve failed candidates too, then put the exact old paths back.
        if installing and (prefix.exists() or prefix.is_symlink()):
            prefix.rename(tool_root / (".amplifier-unified-failed-" + backup.name))
        for original, saved in reversed(moves):
            saved.rename(original)
        if had_launcher:
            if launcher.exists() or launcher.is_symlink():
                launcher.unlink()
            shutil.copy2(backup / "launcher", launcher, follow_symlinks=False)
        elif launcher.exists() or launcher.is_symlink():
            launcher.unlink()
        locks.close()
        if stopped and not no_start:
            command(*service[1], check=False)
        raise
    locks.close()
    print("Fresh installation verified. Chats, settings, credentials and workspace files preserved.")
    print(f"Previous tool environment: {tool_backup}" if tool_backup.exists() else "No previous tool environment.")
    if service and not no_start:
        command(*service[1])
        print("Managed service started. Use 'amplifier-unified service status' to inspect it.")
    else:
        print("Start Amplifier Unified when ready. Runtime caches will rebuild on demand.")
    return backup


def run(args):
    try:
        if os.name == "nt":
            raise ValueError("Run reset inside WSL; native Windows hosting is not supported.")
        home = home_path(args)
        if home in {Path.home().resolve(), Path(home.anchor)}:
            raise ValueError("Use a dedicated Unified data directory, not a home or filesystem root.")
        service = service_commands(home)
        print(f"Repair Unified data directory: {home}")
        print("Reinstall: " + (args.source or "latest published Amplifier Unified release"))
        print("Retire to recovery backups: " + ", ".join(REGENERABLE) + ", saved update status, old app environment")
        print("Preserve: chats, settings, keys, attachments, workspace files and all other data")
        print("The managed service will be stopped. Stop any manually launched hosts and workers first.")
        if os.environ.get("AMPLIFIER_SOURCE_STORE"):
            raise ValueError("Unset AMPLIFIER_SOURCE_STORE for reset. External/shared source stores are not reset.")
        if args.dry_run:
            return
        if not args.yes:
            if not sys.stdin.isatty():
                raise ValueError("Use --yes to confirm reset in a non-interactive terminal, or --dry-run to preview.")
            if input("Proceed with emergency repair? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("Reset cancelled.")
                return
        uv = shutil.which("uv")
        if not uv:
            raise ValueError("uv is required. Install uv, then run reset again.")
        env = environment()
        tool_root = Path(command(uv, "tool", "dir", env=env, capture=True).stdout.strip()).resolve()
        bin_root = Path(command(uv, "tool", "dir", "--bin", env=env, capture=True).stdout.strip()).resolve()
        prefix = tool_root / "amplifier-unified"
        # Do not let a nonstandard uv path turn cache cleanup into installation
        # removal, or let a symlink redirect installation writes elsewhere.
        if prefix.is_symlink() or home.is_relative_to(prefix) or prefix.is_relative_to(home):
            raise ValueError("Tool installation and data directory must be separate, without a symlinked tool environment.")
        tool_root.mkdir(parents=True, exist_ok=True)
        bin_root.mkdir(parents=True, exist_ok=True)
        source = args.source or release_source(env)
        if args.source and Path(args.source).expanduser().exists():
            source = str(Path(args.source).expanduser().resolve())
        with repair_lock(tool_root):
            repair(home, uv, tool_root, bin_root, source, env, service, args.no_start)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, sqlite3.Error) as error:
        raise SystemExit(f"Reset did not complete: {error}\nAny recovery backups are retained. Fix the reported problem and retry reset.") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    options(parser)
    run(parser.parse_args())
