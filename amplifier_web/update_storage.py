"""Private staging copies: copy-on-write where supported, never writable hardlinks."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def clone_file(source, target):
    source, target = Path(source), Path(target)
    try:
        if sys.platform == "darwin":
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            if libc.clonefile(os.fsencode(source), os.fsencode(target), 0) != 0:
                raise OSError(ctypes.get_errno(), "File clone unavailable")
        elif sys.platform.startswith("linux"):
            import fcntl

            with source.open("rb") as original, target.open("xb") as copy:
                fcntl.ioctl(copy.fileno(), 0x40049409, original.fileno())  # FICLONE
        else:
            raise OSError("File clone unavailable")
        shutil.copystat(source, target)
        return str(target)
    except OSError:
        if target.exists():
            target.unlink()
        return shutil.copy2(source, target)


def copy_snapshot(source, target):
    """Distinct file identities prevent staged changes reaching live workers."""
    copied = shutil.copytree(source, target, symlinks=True, copy_function=clone_file)
    _refresh_copied_indexes(Path(target))
    return copied


def _refresh_copied_indexes(target):
    """Refresh only new cache copies, after copytree has applied final modes.

    Git's read-only checks cannot persist stat entries for the new file identities.
    Keep that write here, inside the caller's existing copy/cancellation owner.
    This is metadata preparation, not admission: dirty/staged changes stay intact.
    """
    from .runtime_qualification import _installed_git_environment, _installed_checkout_environment
    from .staged_sources import _metadata_owned

    cache = target / "cache"
    if cache.is_symlink() or not cache.is_dir():
        return
    for root in cache.iterdir():
        try:
            _metadata_owned(root, target)
        except ValueError:
            # Linked or redirected layouts retain the original copy behavior.
            # Their ordinary downstream admission/verification still applies.
            continue
        git_dir, index = root / ".git", root / ".git/index"
        if not index.is_file():
            continue
        env = dict(_installed_git_environment() or os.environ)
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"):
            env.pop(name, None)
        env = _installed_checkout_environment(root, env)
        env.update(GIT_OPTIONAL_LOCKS="1", GIT_INDEX_FILE=str(index.resolve()))
        git_mode, index_mode = stat.S_IMODE(git_dir.stat().st_mode), stat.S_IMODE(index.stat().st_mode)
        previous_shared = set(git_dir.glob("sharedindex.*"))
        try:
            git_dir.chmod(git_mode | stat.S_IWUSR)
            index.chmod(index_mode | stat.S_IWUSR)
            subprocess.run(
                ["git", "--git-dir", str(git_dir.resolve()), "--work-tree", str(root.resolve()),
                 "status", "--porcelain", "--untracked-files=no"],
                cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                check=True, timeout=30,
            )
        finally:
            try:
                index.chmod(index_mode)
                # A split index may create another derived file in .git.
                for shared in set(git_dir.glob("sharedindex.*")) - previous_shared:
                    if stat.S_ISREG(shared.lstat().st_mode):
                        shared.chmod(index_mode)
            finally:
                git_dir.chmod(git_mode)
