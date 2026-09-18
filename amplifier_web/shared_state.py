"""Worker-only helpers for Foundation's shared session authority.

This module intentionally has no Foundation import.  The outer HTTP process can
load it for tests, but the worker imports ``session.shared_state`` only after it
has entered its isolated dependency environment.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path


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


def configuration_paths(workspace: Path, session_id: str, home: Path) -> tuple[Path, ...]:
    """Only stat inputs this host actually consumes during a manager mount."""

    return (
        home / "config" / "settings.yaml",
        home / "config" / "keys.env",
        home / "config" / "workspaces" / (workspace.name + ".unused"),  # retained below only for stable ordering
        workspace / ".amplifier" / "settings.yaml",
        workspace / ".amplifier" / "settings.local.yaml",
        workspace / ".amplifier-unified" / "settings.yaml",
        workspace / ".amplifier-unified" / "settings.local.yaml",
        home / "sessions" / session_id / "configuration.json",
        home / "sessions" / session_id / "control-state.json",
        home / "updates" / "active.json",
    )


def configuration_stamp(workspace: Path, session_id: str, home: Path, stamp) -> tuple[tuple[str, object], ...]:
    """Return metadata-only invalidation data; no config file is parsed."""

    # ``load_config`` names its imported project snapshot by a SHA-256 digest,
    # not the workspace basename.  Derive it here without loading settings.
    import hashlib

    digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:20]
    paths = list(configuration_paths(workspace, session_id, home))
    paths[2] = home / "config" / "workspaces" / (digest + ".yaml")
    return tuple((str(path), stamp(path)) for path in paths)