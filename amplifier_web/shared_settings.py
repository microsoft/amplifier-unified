"""Shared Amplifier settings, independent of a particular application runtime.

The CLI contract is global < project < local < session, dictionary overlays and
list replacement, with config.providers merged by instance identity. Runtime
bundle composition has separate merge rules; it must not change this contract.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
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


class SettingsReadCache:
    """Bounded settings reuse for one read-only inventory, never runtime state.

    Different session paths with no settings file have the same effective
    inputs. Foundation still owns every merge; this cache only avoids repeating
    that merge and YAML parsing for an unchanged ordered set of files.
    """

    def __init__(self, *, limit=128):
        self.limit = limit
        self._values = OrderedDict()

    @staticmethod
    def _signature(paths):
        result = []
        for path in paths:
            try:
                info = path.stat()
            except FileNotFoundError:
                continue
            result.append((str(path), info.st_dev, info.st_ino, info.st_mode,
                           info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        return tuple(result)

    def read(self, paths):
        from amplifier_foundation.settings import read_settings as read_scoped_settings
        paths = tuple(paths)
        key = self._signature(paths)
        if key in self._values:
            self._values.move_to_end(key)
            return copy.deepcopy(self._values[key])
        value = read_scoped_settings(paths)
        # A file can be created/replaced during the read. Preserve the normal
        # read result, but do not reuse it as a stable settings snapshot.
        if self.limit > 0 and self._signature(paths) == key:
            self._values[key] = copy.deepcopy(value)
            while len(self._values) > self.limit:
                self._values.popitem(last=False)
        return value


def read_settings(workspace, *, shared_home=None, session_id=None, global_only=False, cache=None):
    from amplifier_foundation.settings import read_settings as read_scoped_settings
    paths = settings_paths(workspace, shared_home=shared_home, session_id=session_id, global_only=global_only).values()
    return cache.read(paths) if cache is not None else read_scoped_settings(paths)


def routing_dirs(workspace, *, shared_home=None, global_only=False):
    root = Path(shared_home or amplifier_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    from .managed_chats import metadata
    return [root / "routing"] if global_only or metadata(workspace) else [workspace / ".amplifier" / "routing.local", workspace / ".amplifier" / "routing", root / "routing"]
