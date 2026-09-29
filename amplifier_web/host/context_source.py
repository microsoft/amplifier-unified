"""Unify byte-identical copies of the shared context-simple dependency.

Boundary and persistent context managers import context-simple as a Python
dependency. Later child/remount plans may select that same revision from a
Foundation checkout. Core correctly rejects mixing the two package locations.
Only prove equivalence here; never replace a custom source or purge imports.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

_PACKAGE = 'amplifier_module_context_simple'
_DISTRIBUTION = 'amplifier-module-context-simple'
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')


def _repository(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http', 'ssh') or not parsed.hostname or parsed.query or parsed.fragment:
        return None
    return parsed.scheme, parsed.hostname.lower(), parsed.port, parsed.path.rstrip('/').removesuffix('.git')


def _files(package):
    """Bound work to this small package, not the environment or bundle cache."""
    def unreadable(error):
        raise error
    result, size, entries = {}, 0, 0
    for directory, directories, files in os.walk(package, onerror=unreadable):
        directories[:] = [name for name in directories if name != '__pycache__']
        entries += len(directories) + len(files)
        if entries > 128:
            raise ValueError('Package equivalence budget exceeded')
        for name in directories + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError('Symlinked package source cannot establish equivalence')
        for name in files:
            path = Path(directory) / name
            if not path.is_file() or len(result) >= 64 or path.stat().st_size > 4 * 1024 * 1024 - size:
                raise ValueError('Package equivalence budget exceeded')
            with path.open('rb') as handle:
                data = handle.read(4 * 1024 * 1024 - size + 1)
            size += len(data)
            if size > 4 * 1024 * 1024:
                raise ValueError('Package equivalence budget exceeded')
            result[str(path.relative_to(package))] = hashlib.sha256(data).digest()
    return result


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                          text=True, timeout=1, check=False)


class _InstalledSource:
    def __init__(self, package):
        self.package = package

    def resolve(self):
        return self.package


def equivalent_context_source(module_id, hint, source):
    """Return the installed package only after proving the selected source equal.

    Refusal leaves the original resolver and Core's source checks intact. Git
    URL/ref, full commit, clean checkout and every package file must agree.
    Nothing imports or mounts a module, installs a package, or accesses a remote.
    """
    if module_id != 'context-simple' or not isinstance(hint, str) or not hint.startswith('git+'):
        return source
    try:
        parsed = urlsplit(hint[4:])
        repo_path, at, revision = parsed.path.rpartition('@')
        if not at or not revision or parsed.fragment or parsed.query:
            return source
        requested_repo = _repository(parsed._replace(path=repo_path).geturl())
        if requested_repo is None:
            return source
        distribution = importlib.metadata.distribution(_DISTRIBUTION)
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        installed_commit = direct.get('vcs_info', {}).get('commit_id', '')
        if (direct.get('dir_info') or direct.get('subdirectory')
                or direct.get('vcs_info', {}).get('vcs') != 'git'
                or not _COMMIT.fullmatch(installed_commit)
                or _repository(direct.get('url', '')) != requested_repo):
            return source
        # This also verifies normal Python resolution agrees with the wheel.
        from .components import installed_package_source
        installed = Path(installed_package_source(_DISTRIBUTION, _PACKAGE))
        selected = Path(source.resolve()).resolve()
        if selected == installed:
            return source
        root = selected.parent if selected.name == _PACKAGE else selected
        package = root / _PACKAGE
        metadata = json.loads((root / '.amplifier_cache_meta.json').read_text())
        if (metadata.get('commit') != installed_commit
                or _repository(metadata.get('git_url', '')) != requested_repo
                or (metadata.get('ref') != revision and revision != installed_commit)):
            return source
        head = _git(root, 'rev-parse', 'HEAD')
        if head.returncode or head.stdout.strip() != installed_commit:
            return source
        # Reject tracked changes anywhere (including dependency declarations),
        # and extra untracked package files. Foundation's own cache metadata is
        # allowed outside the package; generated __pycache__ files are ignored.
        if _git(root, 'diff', '--no-ext-diff', '--no-textconv', '--quiet', 'HEAD').returncode:
            return source
        untracked = _git(root, 'ls-files', '--others', '--exclude-standard', '--', _PACKAGE)
        if untracked.returncode or untracked.stdout.strip():
            return source
        if not (package / '__init__.py').is_file() or _files(package) != _files(installed):
            return source
        return _InstalledSource(installed)
    except (OSError, ValueError, TypeError, AttributeError, importlib.metadata.PackageNotFoundError,
            subprocess.TimeoutExpired):
        # Missing/unverifiable provenance is not permission to switch sources.
        return source
