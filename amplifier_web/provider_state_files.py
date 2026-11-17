"""Where a provider module keeps its own state files.

A provider module can keep small files of its own. The Anthropic module builds
their default paths from the person's home folder (``~/.amplifier``) and does
not read ``AMPLIFIER_HOME``. On an installation whose shared Amplifier home is
another folder, those files then appear outside the installation: a Windows
installation from the setup file got ``%USERPROFILE%\\.amplifier``.

One rule on every platform: these files are in the shared Amplifier home.
Where that home is ``~/.amplifier``, the module puts them there itself and the
host sets nothing. Elsewhere the host gives the module the paths that its
configuration does not hold. A value that a person configured stays, also an
empty one, which turns the file off.

The module gets the paths when it is loaded or constructed. They are never
written into a mount plan. A plan is saved, hashed, compared and exported, and
a path of this PC must not look there like a setting that a person made.
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


def state_file_defaults(module):
    """The path keys that the host sets for this module, with their values.

    Call this while ``AMPLIFIER_HOME`` still names the shared home: a process
    that prepares a module source changes it for its own registry.
    """
    names = STATE_FILES.get(module)
    if not names:
        return {}
    from .session_files import amplifier_home
    home = amplifier_home()
    if home == Path(os.path.expanduser('~'), '.amplifier').resolve():
        return {}  # The module's own folder.
    return {key: str(home / name) for key, name in names.items()}


def with_state_files(module, config):
    """One provider configuration plus the host's paths where it has none.

    The result is a new mapping when the host adds a path. Otherwise it is
    the given configuration itself, so nothing changes for any other module
    or for the default home.
    """
    defaults = state_file_defaults(module)
    return {**defaults, **(config or {})} if defaults else config


def uses_state_files(rows):
    """Does one of these provider rows name a module with state files?"""
    return any(isinstance(row, dict) and row.get('module') in STATE_FILES for row in rows or ())


def install(coordinator):
    """Give each module that this session loads its state-file paths.

    Core loads every module through ``coordinator.loader`` and makes its own
    loader only when it finds none. Call this before the session initializes.
    The session's configuration stays as it is.
    """
    loader = getattr(coordinator, 'loader', None)
    if loader is None:
        from amplifier_core.loader import ModuleLoader
        loader = ModuleLoader(coordinator=coordinator)  # The loader that Core makes itself.
        coordinator.loader = loader
    load = loader.load

    async def load_with_state_files(module_id, config=None, *arguments, **options):
        return await load(module_id, with_state_files(module_id, config), *arguments, **options)

    loader.load = load_with_state_files
