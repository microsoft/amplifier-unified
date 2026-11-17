"""Where a provider module keeps its own state files.

A provider module can keep small files of its own. The Anthropic module builds
their default paths from the person's home folder (``~/.amplifier``) and does
not read ``AMPLIFIER_HOME``. On an installation whose shared Amplifier home is
another folder, those files then appear outside the installation: a Windows
installation from the setup file got ``%USERPROFILE%\\.amplifier``.

One rule on every platform: these files are in the shared Amplifier home.
Where that home is ``~/.amplifier``, the module puts them there itself and the
host sets nothing, so the mounted plan is the one that a person configured.
Elsewhere the host sets the paths that the configuration does not hold. A
value that a person configured stays, also an empty one, which turns the file
off.

The paths are plain strings that already name the folder. Set them after
``${NAME}`` references are expanded: a folder name can contain ``$``.

The mounted plan then holds these paths. Saved settings do not. Call
``clear_state_files`` on a copy of a mounted row before it is compared with
settings or with a saved plan, and before it leaves this PC.
"""
from __future__ import annotations

import os
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
    """The path keys that the host sets for this module, with their values.

    ``home`` is the resolved shared home. Pass it when this process changes
    ``AMPLIFIER_HOME`` before it constructs the provider.
    """
    names = STATE_FILES.get(module)
    if not names:
        return {}
    if home is None:
        from .session_files import amplifier_home
        home = amplifier_home()
    home = Path(home)
    if home == Path(os.path.expanduser('~'), '.amplifier').resolve():
        return {}  # The module's own folder.
    return {key: str(home / name) for key, name in names.items()}


def clear_state_files(row):
    """Remove from a provider row what the host set there earlier.

    A configured value stays: only a value equal to the recorded one goes.
    """
    previous = row.pop(_MARKER, None)
    config = row.get('config')
    if isinstance(previous, dict) and isinstance(config, dict):
        for key, value in previous.items():
            if key in config and config[key] == value:
                del config[key]
    return row


def apply_state_files(row):
    """Set the host's paths on one provider row whose configuration has none.

    A saved plan can hold the path of an earlier shared home. It is replaced,
    because the row records that the host set it.
    """
    clear_state_files(row)
    config = row.get('config') or {}
    added = {key: value for key, value in state_file_defaults(row['module']).items() if key not in config}
    if added:
        row['config'] = {**config, **added}
        row[_MARKER] = added
    return row
