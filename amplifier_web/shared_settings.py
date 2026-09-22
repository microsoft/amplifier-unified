"""Shared Amplifier settings, independent of a particular application runtime.

The CLI contract is global < project < local < session, dictionary overlays and
list replacement, with config.providers merged by instance identity. Runtime
bundle composition has separate merge rules; it must not change this contract.
"""
from __future__ import annotations

from pathlib import Path

from .session_files import amplifier_home, project_slug, validate_id


# Provider probes import host configuration without activating a session. Keep
# Foundation imports at the actual settings operation, not at probe startup.
def read_yaml(path):
    from amplifier_foundation.settings import read_yaml as read
    return read(path)


def overlay(base, patch):
    from amplifier_foundation.settings import overlay as merge
    return merge(base, patch)


def atomic_write(path, contents, *, private=False):
    from amplifier_foundation.settings import atomic_write as write
    return write(path, contents, private=private)


def update_settings(path, mutator):
    from amplifier_foundation.settings import update_settings as update
    return update(path, mutator)


def settings_paths(workspace, *, shared_home=None, session_id=None, global_only=False):
    root = Path(shared_home or amplifier_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    paths = {
        "global": root / "settings.yaml",
        "project": workspace / ".amplifier" / "settings.yaml",
        "local": workspace / ".amplifier" / "settings.local.yaml",
    }
    from .managed_chats import metadata
    if global_only or metadata(workspace):
        paths = {"global": paths["global"]}
    if session_id:
        paths["session"] = root / "projects" / project_slug(workspace) / "sessions" / validate_id(session_id) / "settings.yaml"
    return paths


def read_settings(workspace, *, shared_home=None, session_id=None, global_only=False):
    from amplifier_foundation.settings import read_settings as read_scoped_settings
    return read_scoped_settings(settings_paths(workspace, shared_home=shared_home, session_id=session_id, global_only=global_only).values())


def routing_dirs(workspace, *, shared_home=None):
    root = Path(shared_home or amplifier_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    from .managed_chats import metadata
    return [root / "routing"] if metadata(workspace) else [workspace / ".amplifier" / "routing.local", workspace / ".amplifier" / "routing", root / "routing"]
