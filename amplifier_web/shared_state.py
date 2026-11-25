"""Shared host/worker helpers for Foundation's session ownership lock.

Path and configuration stamps remain runtime-free helpers. Foundation owns
activation authority and session locking inside the isolated worker dependency
environment; navigation uses its native-history reader.
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

MAX_CONFIGURATION_DIGEST_BYTES = 8 * 1024 * 1024

def shared_state_home() -> Path:
    """Resolve Foundation's state-root convention without importing its runtime."""
    configured = os.environ.get("AMPLIFIER_SESSION_STATE_HOME")
    if configured:
        return Path(configured).expanduser().absolute()
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return (Path(state_home).expanduser() / "amplifier" / "sessions").absolute()
    return Path.home() / ".local" / "state" / "amplifier" / "sessions"


def configuration_paths(workspace: Path, session_id: str, home: Path) -> tuple[Path, ...]:
    """Only stat inputs this host actually consumes during a manager mount."""

    from .shared_settings import settings_paths, routing_dirs
    from .session_files import amplifier_home
    paths = settings_paths(workspace, session_id=session_id)
    matrices = [path for directory in routing_dirs(workspace) for path in sorted(directory.glob("*.yaml"))]
    return (
        *paths.values(),
        amplifier_home() / "keys.env",
        *routing_dirs(workspace),
        *matrices,
        home / "sessions" / session_id / "configuration.json",
        home / "sessions" / session_id / "control-state.json",
        home / "updates" / "active.json",
        home / "diagnostics" / "config.json",
    )


def configuration_stamp(workspace: Path, session_id: str, home: Path, stamp, *, extra_paths=(), include_controls=True) -> tuple[tuple[str, object], ...]:
    """Return metadata-only invalidation data; no config file is parsed."""

    paths = [*configuration_paths(workspace, session_id, home)]
    if not include_controls:
        # Live controls are owned/persisted by this worker and the host's guarded
        # configuration operation, not external mount inputs.
        local = home / "sessions" / session_id
        paths = [path for path in paths if path not in
                 {local / "configuration.json", local / "control-state.json"}]
    for path in extra_paths:
        path = Path(path)
        if path not in paths:
            paths.append(path)
    return tuple((str(path), stamp(path)) for path in paths)


def configuration_path_stamp(path):
    """Return one JSON-stable metadata witness used by mounted stamps."""
    try:
        value = Path(path).stat()
    except FileNotFoundError:
        return None
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def configuration_path_digest(path):
    """Hash one bounded regular file without following blocking stream semantics."""
    flags = os.O_RDONLY
    for name in ('O_NONBLOCK', 'O_CLOEXEC', 'O_BINARY'):
        flags |= getattr(os, name, 0)
    try:
        handle = os.open(Path(path), flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError('The configuration file cannot be read safely.') from exc
    try:
        before = os.fstat(handle)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError('The configuration path is not a regular file.')
        if before.st_size > MAX_CONFIGURATION_DIGEST_BYTES:
            raise RuntimeError('The configuration file is too large for live reconciliation.')
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(handle, min(remaining, 64 * 1024))
            if not chunk:
                raise RuntimeError('The configuration file changed while it was read.')
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(handle, 1):
            raise RuntimeError('The configuration file changed while it was read.')
        after = os.fstat(handle)
        before_witness = (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns)
        after_witness = (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns)
        if after_witness != before_witness:
            raise RuntimeError('The configuration file changed while it was read.')
        return digest.hexdigest()
    except OSError as exc:
        raise RuntimeError('The configuration file cannot be read safely.') from exc
    finally:
        os.close(handle)


def mounted_configuration_stamp(workspace, session_id, home, *, extra_paths=()):
    """Runtime-free private mount comparison; JSON-stable metadata, not content."""
    return [[path, value] for path, value in configuration_stamp(
        Path(workspace), session_id, Path(home), configuration_path_stamp,
        extra_paths=extra_paths, include_controls=False)]
