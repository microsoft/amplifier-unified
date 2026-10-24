"""Bounded, non-following file-content accounting, never deletion authority."""
import hashlib
import os
from pathlib import Path
import stat
import time


def measure(paths, *, limit=200000, seconds=4):
    deadline = time.monotonic() + seconds
    seen, rows = set(), []
    content = exclusive = files = links = 0
    for index, raw in enumerate(paths):
        root = Path(raw)
        if root.is_symlink() or root.resolve() != root.absolute():
            raise ValueError('A storage root is redirected.')
        stack = [root]
        while stack:
            if len(rows) >= limit or time.monotonic() >= deadline:
                raise ValueError('Storage measurement reached its limit.')
            path = stack.pop()
            info = path.lstat()
            # Moving a retirement root changes its ctime; its children retain
            # theirs. Include child ctime to catch timestamp-preserving edits.
            rows.append([index, str(path.relative_to(root)), info.st_dev, info.st_ino, info.st_mode,
                         info.st_size, info.st_mtime_ns, info.st_nlink,
                         info.st_ctime_ns if path != root else None])
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    for entry in entries:
                        if len(rows) + len(stack) >= limit or time.monotonic() >= deadline:
                            raise ValueError('Storage measurement reached its limit.')
                        stack.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode):
                key = info.st_dev, info.st_ino
                if key not in seen:
                    seen.add(key)
                    files += 1
                    content += info.st_size
                    if info.st_nlink == 1:
                        exclusive += info.st_size
            elif stat.S_ISLNK(info.st_mode):
                links += 1
            else:
                raise ValueError('A storage folder contains a special file.')
    return {'fileBytes': content, 'exclusiveFileBytes': exclusive,
            'files': files, 'linksNotFollowed': links,
            'stamp': hashlib.sha256(repr(sorted(rows)).encode()).hexdigest()}
