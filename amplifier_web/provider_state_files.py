"""Where a provider module keeps its own state files.

A provider module can keep small files of its own. The Anthropic module builds
their default paths from the person's home folder (``~/.amplifier``) and does
not read ``AMPLIFIER_HOME``. On an installation whose shared Amplifier home is
another folder, those files then appear outside the installation: a Windows
installation from the setup file got ``%USERPROFILE%\\.amplifier``.

One rule on every platform: these files are in the shared Amplifier home. With
the default home that is the path that the module uses itself, so nothing
moves. The host sets a path only where the configuration has none. A value
that a person configured stays, also an empty one, which turns the file off.

The paths are plain strings that already name the folder. Set them after
``${NAME}`` references are expanded: a folder name can contain ``$``.
"""
from __future__ import annotations

from pathlib import Path

# Module -> the configuration key that the module reads -> the file name that
# the module uses by default. Add a module only after reading its source.
STATE_FILES = {
    'provider-anthropic': {
        'rate_limit_state_path': 'rate-limit-state.json',
        'fallback_state_path': 'anthropic-fallback-state.json',
    },
}
# What the host set on a provider row, with the values. It is beside the
# configuration, not in it: a saved plan keeps it, so a later start can tell
# the host's own path from one that a person configured.
_MARKER = '_unified_state_file_defaults'


def state_file_defaults(module, home=None):
    """The path keys that the host sets for this module, with their values."""
    names = STATE_FILES.get(module)
    if not names:
        return {}
    if home is None:
        from .session_files import amplifier_home
        home = amplifier_home()
    return {key: str(Path(home) / name) for key, name in names.items()}


def with_state_files(module, config, home=None):
    """One provider configuration plus the host's paths where it has none.

    For a caller that constructs a provider without a mount plan. Pass the
    shared home when the caller's process changes ``AMPLIFIER_HOME``.
    """
    return {**state_file_defaults(module, home), **config}


def clear_state_files(row):
    """Remove from a provider row what the host set there earlier.

    A configured value stays: only a value equal to the recorded one goes.
    Comparisons with saved settings or with a plan that an earlier version
    saved use this, so the host's own path never looks like a changed account.
    """
    previous = row.pop(_MARKER, None)
    config = row.get('config')
    if isinstance(previous, dict) and isinstance(config, dict):
        for key, value in previous.items():
            if key in config and config[key] == value:
                del config[key]
    return row


def apply_state_files(row, home=None):
    """Set the host's paths on one provider row whose configuration has none.

    A saved plan can hold the path of an earlier shared home. It is replaced,
    because the row records that the host set it.
    """
    clear_state_files(row)
    if row.get('enabled') is False:
        return row
    config = row.get('config')
    if config is not None and not isinstance(config, dict):
        return row
    added = {key: value for key, value in state_file_defaults(row['module'], home).items()
             if key not in (config or {})}
    if added:
        row.setdefault('config', {}).update(added)
        row[_MARKER] = added
    return row
