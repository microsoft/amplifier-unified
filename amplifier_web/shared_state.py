"""Shared host/worker helpers for Foundation's shared session authority.

This module intentionally has no Foundation import, letting configuration and
the outer HTTP process use its pure helpers. The worker imports Foundation's
``session.shared_state`` only after entering its isolated dependency environment.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

def shared_state_home() -> Path:
    """Resolve Foundation's state-root convention without importing its runtime."""
    configured = os.environ.get("AMPLIFIER_SESSION_STATE_HOME")
    if configured:
        return Path(configured).expanduser().absolute()
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return (Path(state_home).expanduser() / "amplifier" / "sessions").absolute()
    return Path.home() / ".local" / "state" / "amplifier" / "sessions"


@dataclass(frozen=True)
class Activation:
    """An opaque, per-acquisition authority token."""

    number: int


class ActivationGate:
    """Reject writes from a released or superseded worker activation."""

    def __init__(self):
        self._number = 0
        self._active: Activation | None = None
        self._current: ContextVar[Activation | None] = ContextVar("amplifier_web_activation", default=None)

    def activate(self) -> Activation:
        self._number += 1
        self._active = Activation(self._number)
        # Mount-time checkpoints run before the first Input exists.  A spawned
        # turn still binds its own captured token explicitly.
        self._current.set(self._active)
        return self._active

    def release(self, activation: Activation) -> None:
        self.check(activation)
        self._active = None

    def check(self, activation: Activation | None) -> None:
        if activation is None or activation is not self._active:
            raise RuntimeError("This session activation has been released or superseded.")

    def bind(self, activation: Activation):
        self.check(activation)
        return self._current.set(activation)

    def reset(self, token) -> None:
        self._current.reset(token)

    def check_current(self) -> None:
        self.check(self._current.get())

    def current(self) -> Activation | None:
        """Capture the calling task's token, never the latest owner's token."""
        return self._current.get()

    @property
    def current_valid(self) -> bool:
        return self._active is not None and self._current.get() is self._active


def workspace_snapshot_path(workspace: Path, home: Path) -> Path:
    """Return the app-owned snapshot path for an imported workspace."""

    digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:20]
    return home / "config" / "workspaces" / (digest + ".yaml")


def configuration_paths(workspace: Path, session_id: str, home: Path) -> tuple[Path, ...]:
    """Only stat inputs this host actually consumes during a manager mount."""

    return (
        home / "config" / "settings.yaml",
        home / "config" / "keys.env",
        workspace_snapshot_path(workspace, home),
        workspace / ".amplifier" / "settings.yaml",
        workspace / ".amplifier" / "settings.local.yaml",
        workspace / ".amplifier-unified" / "settings.yaml",
        workspace / ".amplifier-unified" / "settings.local.yaml",
        home / "sessions" / session_id / "configuration.json",
        home / "sessions" / session_id / "control-state.json",
        home / "updates" / "active.json",
    )


def configuration_stamp(workspace: Path, session_id: str, home: Path, stamp, *, extra_paths=()) -> tuple[tuple[str, object], ...]:
    """Return metadata-only invalidation data; no config file is parsed."""

    paths = [*configuration_paths(workspace, session_id, home)]
    for path in extra_paths:
        path = Path(path)
        if path not in paths:
            paths.append(path)
    return tuple((str(path), stamp(path)) for path in paths)