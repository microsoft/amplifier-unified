"""Locate installation helpers in terminals and background services.

Only standard per-user/platform executable directories supplement PATH. Never
search a workspace, read shell startup files, or resolve dispatcher symlinks.
"""
import os
from pathlib import Path
import shutil
import sys


class MissingHostDependency(RuntimeError):
    """Safe, app-authored guidance; may be shown during provider sign-in."""


def tool_path(path=None):
    current = os.environ.get('PATH', os.defpath) if path is None else path
    entries = current.split(os.pathsep) if current else os.defpath.split(os.pathsep)
    additions = [Path.home() / '.local/bin', Path.home() / '.cargo/bin']
    if sys.platform == 'darwin':
        additions += [Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
    for directory in additions:
        if str(directory) not in entries:
            entries.append(str(directory))
    return os.pathsep.join(entries)


def prepare_environment():
    # Include absent standard directories: a later helper installation becomes
    # visible to the running service on its next preparation attempt.
    os.environ['PATH'] = tool_path()


def require_uv():
    executable = shutil.which('uv')
    if not executable:
        executable = shutil.which('uv', path=tool_path())
    if executable:
        return executable
    raise MissingHostDependency(
        "Amplifier cannot find its setup helper (uv) on the computer running the app. "
        "Run the Amplifier installer again as the same user, then retry. "
        "If uv works in a terminal but is installed in a custom location, refresh the "
        "background service with amplifier-unified service install --replace, keeping "
        "any original --data-dir and --workspace options. Your saved chats are kept.")
