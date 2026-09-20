"""Shared Amplifier settings, independent of a particular application runtime.

The CLI contract is global < project < local < session, dictionary overlays and
list replacement, with config.providers merged by instance identity. Runtime
bundle composition has separate merge rules; it must not change this contract.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile

from filelock import FileLock
import yaml

from .session_files import amplifier_home, project_slug, validate_id


def settings_paths(workspace, *, shared_home=None, session_id=None):
    root = Path(shared_home or amplifier_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    paths = {
        "global": root / "settings.yaml",
        "project": workspace / ".amplifier" / "settings.yaml",
        "local": workspace / ".amplifier" / "settings.local.yaml",
    }
    if session_id:
        paths["session"] = root / "projects" / project_slug(workspace) / "sessions" / validate_id(session_id) / "settings.yaml"
    return paths


def read_yaml(path):
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Settings must be a mapping: {path}")
    return value


def overlay(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        result = copy.deepcopy(base)
        for key, value in patch.items():
            result[key] = overlay(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(patch)


def read_settings(workspace, *, shared_home=None, session_id=None):
    result, providers = {}, []
    for path in settings_paths(workspace, shared_home=shared_home, session_id=session_id).values():
        value = read_yaml(path)
        result = overlay(result, value)
        for row in value.get("config", {}).get("providers", []):
            identity = row.get("id") or row.get("module")
            index = next((i for i, p in enumerate(providers) if (p.get("id") or p.get("module")) == identity), None)
            if index is None:
                providers.append(copy.deepcopy(row))
            else:
                providers[index] = overlay(providers[index], row)
    if providers:
        result.setdefault("config", {})["providers"] = providers
    return result


def atomic_write(path, contents, *, private=False):
    """Replace a file without changing permissions on a shared directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = 0o600 if private or not path.exists() else path.stat().st_mode & 0o777
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update_settings(path, mutator):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Same per-file lock as amplifier-app-cli, covering the entire transaction.
    with FileLock(str(path) + ".lock", timeout=10):
        value = read_yaml(path)
        result = mutator(value)
        if result is not None:
            value = result
        atomic_write(path, yaml.safe_dump(value, sort_keys=False, allow_unicode=True))
    return copy.deepcopy(value)


def routing_dirs(workspace, *, shared_home=None):
    root = Path(shared_home or amplifier_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    return [workspace / ".amplifier" / "routing.local", workspace / ".amplifier" / "routing", root / "routing"]
