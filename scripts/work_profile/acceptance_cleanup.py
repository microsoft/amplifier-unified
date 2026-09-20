"""Literal credential cleanup for a caller-owned, stopped synthetic run only."""
import os
from pathlib import Path
import re


def credential_values(provider):
    values = set()
    def collect(value, key=''):
        if isinstance(value, dict):
            for name, item in value.items():
                collect(item, name)
        elif isinstance(value, list):
            for item in value:
                collect(item, key)
        elif isinstance(value, str) and len(value) > 12 and '[REDACTED]' not in value and re.search(
                r'api.?key|secret|password|authorization|access.?token|refresh.?token', key, re.I):
            values.add(value.encode())
    collect(provider)
    return values


def redact_generated_credentials(folder, secrets):
    """No cache/source traversal, no symlinks, no database mutation or values out.

    The caller must finish its app/worker cleanup first. These are fixture copies;
    the user's original settings and history are never passed to this function.
    """
    folder = Path(folder).resolve()
    if not (folder / 'report.json').is_file() or not (folder / 'workspace').is_dir():
        raise ValueError('Only a completed synthetic acceptance folder is eligible')
    skip = {'cache', 'runtime', 'foundation', 'node_modules', '.git', '.venv', '__pycache__', 'shell-packages'}
    files = []
    for directory, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = [name for name in dirs if name not in skip and not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink() and (path.suffix in {'.json', '.jsonl', '.yaml', '.yml', '.txt', '.log', '.html', '.py', '.md'} or '.sqlite3' in name):
                files.append(path)
    changed = binary = 0
    for path in files:
        data = path.read_bytes()
        if not any(secret in data for secret in secrets):
            continue
        if '.sqlite3' in path.name:
            binary += 1
            continue
        for secret in secrets:
            data = data.replace(secret, b'[REDACTED]')
        path.write_bytes(data)
        changed += 1
    remaining = sum(any(secret in path.read_bytes() for secret in secrets) for path in files)
    return {'scanned_files': len(files), 'redacted_files': changed,
            'database_matches': binary, 'remaining_matches': remaining,
            'scope': 'completed synthetic run only; caches excluded; databases observed, never edited'}
