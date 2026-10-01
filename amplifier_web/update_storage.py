"""Private staging copies: copy-on-write where supported, never writable hardlinks."""

from __future__ import annotations

import os
import shutil
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
    return shutil.copytree(source, target, symlinks=True, copy_function=clone_file)
