"""Shared configuration and application-owned runtime source caches."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil

from filelock import FileLock

from ..deployment import write_private
from ..shared_settings import read_yaml, read_settings
from ..session_files import amplifier_home

FOUNDATION_SOURCE = "git+https://github.com/microsoft/amplifier-foundation@b3bdab2adcc2a8fe477aca64c20b77528a95e1df"
_KEY_FILE_VALUES = {}


def app_home() -> Path:
    return Path(os.environ.get("AMPLIFIER_WEB_HOME", os.environ.get("AMPLIFIER_WEB_DATA_DIR", Path.home() / ".amplifier-unified"))).expanduser().resolve()


def merge(base, overlay):
    """Config dictionaries deep-merge; named module lists merge by identity."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            result[key] = merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    if isinstance(base, list) and isinstance(overlay, list) and all(isinstance(r, dict) and "module" in r for r in base + overlay):
        result = copy.deepcopy(base)
        indexes = {r.get("id") or r.get("instance_id") or r["module"]: i for i, r in enumerate(result)}
        for row in overlay:
            key = row.get("id") or row.get("instance_id") or row["module"]
            if key in indexes:
                result[indexes[key]] = merge(result[indexes[key]], row)
            else:
                indexes[key] = len(result)
                result.append(copy.deepcopy(row))
        return result
    return copy.deepcopy(overlay)


def _import_registry(home: Path, shared: Path):
    # Runtime checkouts remain isolated from the CLI. Configuration is never
    # copied: the original shared files are authoritative on every mount.
    foundation_home = home / "foundation"
    if (foundation_home / "registry.json").exists():
        return
    old_registry = shared / "registry.json"
    registry = json.loads(old_registry.read_text()) if old_registry.is_file() else {"version": 1, "bundles": {}}
    old_cache, new_cache = shared / "cache", foundation_home / "cache"
    if old_cache.is_dir() and not new_cache.exists():
        shutil.copytree(old_cache, new_cache, symlinks=True)
    for row in registry.get("bundles", {}).values():
        if local := row.get("local_path"):
            try:
                row["local_path"] = str(new_cache / Path(local).resolve().relative_to(old_cache.resolve()))
            except ValueError:
                pass
    write_private(foundation_home / "registry.json", json.dumps(registry, indent=2))


def _load_keys(path):
    values = {}
    if path.exists():
        lines = path.read_text().splitlines()
    else:
        lines = ()
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            parsed = shlex.split(value, comments=True)
            values[name] = " ".join(parsed)
    # Keep explicitly supplied process environment authoritative, while values
    # this loader previously installed are refreshed (or removed) on remount.
    for name, previous in tuple(_KEY_FILE_VALUES.items()):
        if name not in values and os.environ.get(name) == previous:
            os.environ.pop(name, None)
            _KEY_FILE_VALUES.pop(name, None)
    for name, value in values.items():
        previous = _KEY_FILE_VALUES.get(name)
        if name not in os.environ or (previous is not None and os.environ.get(name) == previous):
            os.environ[name] = value
            _KEY_FILE_VALUES[name] = value


def worker_environment():
    """Let a fresh worker load file-managed keys itself, so remounts can refresh.

    Explicit launch-environment credentials remain authoritative. File-managed
    values must not become indistinguishable from explicit credentials merely
    because a worker inherited the server's environment.
    """
    return {name: value for name, value in os.environ.items()
            if name not in _KEY_FILE_VALUES or value != _KEY_FILE_VALUES[name]}


def expand_environment(value, *, environment=None):
    values = os.environ if environment is None else environment
    if isinstance(value, dict):
        return {key: expand_environment(item, environment=values) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_environment(item, environment=values) for item in value]
    if not isinstance(value, str):
        return value
    def substitute(match):
        name, fallback = match.groups()
        shell_default = fallback is not None and fallback.startswith("-")
        current = values.get(name)
        if current is not None and (current or not shell_default):
            return current
        if fallback is not None:
            return fallback[1:] if shell_default else fallback
        raise ValueError(f"Configured environment variable {name} is not set; update the shared Amplifier keys.env or environment.")
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}", substitute, value)


@dataclass
class HostConfig:
    home: Path
    workspace: Path
    settings: dict
    registry_home: Path
    config_home: Path | None = None

    @property
    def settings_file(self):
        return (self.config_home or amplifier_home()) / "settings.yaml"

    @property
    def active_bundle(self):
        return self.settings.get("bundle", {}).get("active") or "anchors"

    @property
    def app_bundles(self):
        from ..builtin_behaviors import app_behaviors
        return app_behaviors(self.settings)

    @property
    def providers(self):
        rows=self.settings.get("config", {}).get("providers", [])
        return sorted(rows, key=lambda row: row.get("config", {}).get("priority", 100))

    @property
    def module_sources(self):
        result = dict(self.settings.get("sources", {}).get("modules", {}))
        result.update({key:row["source"] for key,row in self.settings.get("overrides", {}).items()
            if isinstance(row, dict) and "source" in row})
        result.update({row["module"]:row["source"] for row in self.providers if row.get("source")})
        return result

    @property
    def bundle_sources(self):
        return self.settings.get("sources", {}).get("bundles", {})

    @property
    def registrations(self):
        return {"foundation": FOUNDATION_SOURCE, "anchors": "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors/bundle.md", **self.settings.get("bundle", {}).get("added", {}), **self.bundle_sources}

    def resolve_source(self, source):
        # Source overrides also support namespace-relative includes.
        if source in self.bundle_sources:
            return self.bundle_sources[source]
        if ":" in source and not source.startswith(("git+", "https:", "http:", "file:")):
            namespace, suffix = source.split(":", 1)
            replacement = self.bundle_sources.get(namespace)
            if replacement:
                if replacement.startswith("git+"):
                    base, _, fragment = replacement.partition("#")
                    sub = fragment.removeprefix("subdirectory=").rstrip("/") if fragment else ""
                    return base + "#subdirectory=" + "/".join(v for v in (sub, suffix) if v)
                return str(Path(replacement.removeprefix("file://")) / suffix)
        return None


def prepare_registry(config):
    """Copy a runtime cache only in the worker preparing its first session."""
    (config.home / "config").mkdir(parents=True, exist_ok=True, mode=0o700)
    with FileLock(str(config.home / "config" / ".migration.lock")):
        _import_registry(config.home, config.config_home or amplifier_home())


def load_config(workspace, *, home=None, legacy_home=None, session_id=None):
    workspace = Path(workspace).expanduser().resolve(strict=True)
    shared = Path(legacy_home or amplifier_home()).expanduser().resolve()
    _load_keys(shared / "keys.env")
    return read_config(workspace, home=home, shared_home=shared, session_id=session_id)


def read_config(workspace, *, home=None, shared_home=None, session_id=None):
    """Read runtime settings without loading keys, preparing caches or writing."""
    home = Path(home or app_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve(strict=True)
    shared = Path(shared_home or amplifier_home()).expanduser().resolve()
    settings = read_settings(workspace, shared_home=shared, session_id=session_id)
    from ..updates import foundation_home
    registry_home = foundation_home(home)
    # Keep explicit app-cache source overrides aligned with the active snapshot.
    def relocate(value):
        if isinstance(value, dict): return {k:relocate(v) for k,v in value.items()}
        if isinstance(value, list): return [relocate(v) for v in value]
        if isinstance(value, str): return value.replace(str(home / "foundation"), str(registry_home))
        return value
    return HostConfig(home, workspace, relocate(settings), registry_home, shared)
