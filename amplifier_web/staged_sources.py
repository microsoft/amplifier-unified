"""Ownership checks for unpublished source copies and registry receipts."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit


def owned_path(path, base):
    path, base = Path(path), Path(base)
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('A staged source is outside its candidate.')
    if any(part.is_symlink() for part in (path, *path.parents)
           if part == base or part.is_relative_to(base)):
        raise ValueError('A staged source uses a symbolic link.')
    return path


def _metadata_owned(root, base):
    git_dir = owned_path(root / '.git', base)
    if not git_dir.is_dir():
        raise ValueError('A staged managed source is not an owned Git checkout.')
    # A .git directory alone is insufficient: linked common metadata, object
    # stores and symbolic refs/index paths can redirect ordinary Git writes.
    for name in ('commondir', 'gitdir', 'objects/info/alternates', 'objects/info/http-alternates'):
        if (git_dir / name).exists():
            raise ValueError('A staged Git checkout uses redirected metadata.')
    for parent, directories, files in os.walk(git_dir, followlinks=False):
        for name in directories + files:
            if (Path(parent) / name).is_symlink():
                raise ValueError('A staged Git checkout uses symbolic metadata.')


async def owned_git_checkout(root, base):
    """Check both copied metadata and Git's effective paths before mutation."""
    from .updates import process
    root, base = Path(root), Path(base)
    await asyncio.to_thread(_metadata_owned, root, base)
    worktree = await process('git', 'config', '--default', '', '--get', 'core.worktree',
                             cwd=root, timeout=10)
    if worktree and (root / '.git' / worktree).resolve() != root.resolve():
        raise ValueError('A staged Git checkout configures a redirected working tree.')
    paths = await process('git', 'rev-parse', '--show-toplevel', '--absolute-git-dir',
                          '--git-common-dir', cwd=root, timeout=10)
    paths = paths.splitlines()
    expected = (root.resolve(), (root / '.git').resolve(), (root / '.git').resolve())
    if len(paths) != 3 or any((root / value).resolve() != target
                              for value, target in zip(paths, expected)):
        raise ValueError('A staged Git checkout redirects its working tree or metadata.')
    # Environment overrides also affect Git's object and index paths. Resolve
    # through Git rather than assuming the normal paths inside .git are used.
    paths = await process('git', 'rev-parse', '--git-path', 'objects', '--git-path', 'index',
                          '--git-path', 'refs', '--git-path', 'logs', '--git-path', 'FETCH_HEAD',
                          cwd=root, timeout=10)
    if len(paths.splitlines()) != 5:
        raise ValueError('A staged Git checkout has unknown metadata paths.')
    for value in paths.splitlines():
        owned_path(root / value, root / '.git')


def _suffix(value, root):
    if not isinstance(value, str):
        return None
    if value.startswith('file:'):
        uri = urlsplit(value)
        if uri.netloc not in ('', 'localhost'):
            return None
        value = unquote(uri.path)
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        # Normalize lexical '..' for retaining local references, without
        # traversing or rewriting an independently owned filesystem object.
        return Path(os.path.normpath(path)).relative_to(root)
    except ValueError:
        return None


def _references(value, root):
    if isinstance(value, dict):
        return any(_references(item, root) for item in value.values())
    if isinstance(value, list):
        return any(_references(item, root) for item in value)
    return _suffix(value, root) is not None


def retarget_registry(registry, url, ref, old, new):
    """Retarget matching authored Git registrations; return any retained uses.

    local_path is only a receipt. Shared old paths do not authorize changes to
    another ref, an explicit local URI, arbitrary metadata, or unknown shapes.
    """
    from .update_sequence import git_source
    from .updates import source_key
    registry, old, new = Path(registry), Path(old), Path(new)
    if not registry.exists():
        return False
    owned_path(registry, registry.parent)
    data = json.loads(registry.read_text())
    rows = data.get('bundles') if isinstance(data, dict) else None
    changed = False
    if isinstance(rows, dict):
        for row in rows.values():
            if not isinstance(row, dict):
                continue
            try:
                source = git_source(row.get('uri'))
                matches = source is not None and source_key(source['url'], source['ref']) == source_key(url, ref)
            except ValueError:
                matches = False
            if not matches:
                continue
            suffix = _suffix(row.get('local_path'), old)
            if suffix is not None:
                row['local_path'] = str(new / suffix)
                changed = True
    if changed:
        registry.write_text(json.dumps(data))
    return _references(data, old)
