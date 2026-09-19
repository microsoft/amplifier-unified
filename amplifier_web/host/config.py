"""App-owned settings with an explicit, one-time import of existing setup.

The compatibility reader understands the old YAML file format; it never imports
or invokes the former application. Imported sources and credentials are private
local configuration, not part of the distribution.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil

from filelock import FileLock
import yaml

from ..deployment import write_private
from ..shared_state import workspace_snapshot_path

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


def read_yaml(path):
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text()) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Settings must be a mapping: {path}")
    return value


def _copy_private(source: Path, target: Path):
    if source.is_file() and not target.exists():
        write_private(target, source.read_text())


def _import_global(home: Path, legacy: Path):
    target = home / "config" / "settings.yaml"
    if target.exists():
        return
    settings = read_yaml(legacy / "settings.yaml")
    # Registry URIs remain useful without any registry implementation from the
    # prior host. App ownership of the source cache preserves pinned checkouts.
    old_registry = legacy / "registry.json"
    registry = json.loads(old_registry.read_text()) if old_registry.is_file() else {"version": 1, "bundles": {}}
    foundation_home = home / "foundation"
    old_cache = legacy / "cache"
    new_cache = foundation_home / "cache"
    if old_cache.is_dir() and not new_cache.exists():
        shutil.copytree(old_cache, new_cache, symlinks=True)
    for row in registry.get("bundles", {}).values():
        local = row.get("local_path")
        if local:
            try:
                relative = Path(local).resolve().relative_to(old_cache.resolve())
                row["local_path"] = str(new_cache / relative)
            except ValueError:
                # Explicit local workspace bundles remain user-owned sources.
                pass
    write_private(foundation_home / "registry.json", json.dumps(registry, indent=2))
    _copy_private(legacy / "keys.env", home / "config" / "keys.env")
    # Routing matrix content is user-authored configuration, copied once.
    if (legacy / "routing").is_dir() and not (foundation_home / "routing").exists():
        shutil.copytree(legacy / "routing", foundation_home / "routing")
    settings["_migration"] = {"source": str(legacy), "importedAt": datetime.now(UTC).isoformat(), "version": 1}
    write_private(target, yaml.safe_dump(settings, sort_keys=False))


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
        raise ValueError(f"Configured environment variable {name} is not set; update the app's config/keys.env or environment.")
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}", substitute, value)


@dataclass
class HostConfig:
    home: Path
    workspace: Path
    settings: dict
    registry_home: Path

    @property
    def active_bundle(self):
        return self.settings.get("bundle", {}).get("active") or "anchors"

    @property
    def app_bundles(self):
        return self.settings.get("bundle", {}).get("app", [])

    @property
    def providers(self):
        rows=self.settings.get("config", {}).get("providers", [])
        order={identity:index for index,identity in enumerate(self.settings.get("provider_order",[]))}
        return sorted(rows,key=lambda row:order.get(row.get("id") or row.get("instance_id") or row["module"].removeprefix("provider-"),len(order)))

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


def load_config(workspace, *, home=None, legacy_home=None):
    home = Path(home or app_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve(strict=True)
    legacy = Path(legacy_home or os.environ.get("AMPLIFIER_UNIFIED_IMPORT_HOME", Path.home() / ".amplifier")).expanduser().resolve()
    (home / "config").mkdir(parents=True, exist_ok=True, mode=0o700)
    project_snapshot = workspace_snapshot_path(workspace, home)
    with FileLock(str(home / "config" / ".migration.lock")):
        _import_global(home, legacy)
        if not project_snapshot.exists():
            project = merge(read_yaml(workspace / ".amplifier" / "settings.yaml"), read_yaml(workspace / ".amplifier" / "settings.local.yaml"))
            project["_workspace"] = str(workspace)
            write_private(project_snapshot, yaml.safe_dump(project, sort_keys=False))
    _load_keys(home / "config" / "keys.env")
    global_settings = read_yaml(home / "config" / "settings.yaml")
    settings = merge(global_settings, read_yaml(project_snapshot))
    # The app's own workspace file is live configuration; imported legacy files
    # above are only read once. Explicit local project sources remain supported.
    settings = merge(settings, read_yaml(workspace / ".amplifier-unified" / "settings.yaml"))
    settings = merge(settings, read_yaml(workspace / ".amplifier-unified" / "settings.local.yaml"))
    managed = settings.get("web_bundles", {})
    entries = [row for row in managed.get("entries", []) if row.get("role") in {"behavior", "app"}]
    if entries or managed.get("excluded"):
        excluded = set(managed.get("excluded", [])) | {row["uri"] for row in entries if not row.get("enabled", True)}
        ordered = [row["uri"] for row in entries if row.get("enabled", True) and row["uri"] not in excluded]
        inherited = settings.get("bundle", {}).get("app", [])
        settings.setdefault("bundle", {})["app"] = list(dict.fromkeys(ordered + [uri for uri in inherited if uri not in excluded]))
    from ..updates import foundation_home
    registry_home = foundation_home(home)
    # Keep explicit app-cache source overrides aligned with the active snapshot.
    def relocate(value):
        if isinstance(value, dict): return {k:relocate(v) for k,v in value.items()}
        if isinstance(value, list): return [relocate(v) for v in value]
        if isinstance(value, str): return value.replace(str(home / "foundation"), str(registry_home))
        return value
    return HostConfig(home, workspace, relocate(settings), registry_home)
