"""Resumable file-content measurement; observations are never deletion evidence."""
import os
import stat
from pathlib import Path


def metadata(entry):
    return entry.stat(follow_symlinks=False) if isinstance(entry, os.DirEntry) else entry.lstat()


class Walk:
    """Retain a directory cursor, not a list of every file in the tree.

    A time slice pauses traversal without discarding the unexplored frontier.
    Completed subtrees are reusable when the user opens a deeper view.
    """
    def __init__(self, path, boundaries, seen, cache):
        self.path, self.boundaries, self.seen, self.cache = path, boundaries, seen, cache
        self.stack = []
        self.next = path
        self.result = {'bytes': 0, 'files': 0, 'incomplete': False, 'directory': False}
        self.done = False

    def close(self):
        for _, _, iterator in self.stack:
            iterator.close()
        self.stack.clear()

    def __del__(self):
        self.close()

    def remember(self, path, row):
        if not row['incomplete']:
            # Bound memory, favoring directories over individual file entries.
            if len(self.cache) >= 20000 and row['directory']:
                self.cache.pop(next(iter(self.cache)))
            if len(self.cache) < 20000:
                self.cache[path] = dict(row)

    def add(self, row):
        for _, parent, _ in self.stack:
            parent['bytes'] = (parent['bytes'] or 0) + (row['bytes'] or 0)
            parent['files'] += row['files']
            parent['incomplete'] |= row['incomplete']
            if row.get('measurementNote'):
                parent['measurementNote'] = row['measurementNote']

    def advance(self, budget, expired):
        while not self.done and budget[0] > 0 and not expired():
            if self.next is None:
                if not self.stack:
                    self.done = True
                    break
                path, row, iterator = self.stack[-1]
                try:
                    self.next = next(iterator)
                except StopIteration:
                    iterator.close()
                    self.stack.pop()
                    self.remember(path, row)
                    if not self.stack:
                        self.result = row
                        self.done = True
                    continue
                except OSError:
                    self.add({'bytes': 0, 'files': 0, 'incomplete': True,
                              'measurementNote': 'Some files could not be read'})
                    iterator.close()
                    self.stack.pop()
                    if not self.stack:
                        self.result = row
                        self.done = True
                    continue
            entry, self.next = self.next, None
            path = Path(entry.path) if isinstance(entry, os.DirEntry) else entry
            if path != self.path and path in self.boundaries:
                continue
            budget[0] -= 1
            if path in self.cache:
                row = dict(self.cache[path])
            else:
                row = {'bytes': 0, 'files': 0, 'incomplete': False, 'directory': False}
                try:
                    info = metadata(entry)
                    if stat.S_ISDIR(info.st_mode):
                        row['directory'] = True
                        if len(self.stack) >= 256:
                            row.update(incomplete=True, measurementNote='Directory depth limit reached')
                        else:
                            self.stack.append((path, row, os.scandir(path)))
                            if path == self.path:
                                self.result = row
                            continue
                    elif stat.S_ISREG(info.st_mode):
                        key = (info.st_dev, info.st_ino)
                        # Only hard-linked files require cross-root identity retention.
                        if info.st_nlink <= 1 or key not in self.seen or self.seen[key] == path:
                            if info.st_nlink > 1:
                                self.seen[key] = path
                            row.update(bytes=info.st_size, files=1)
                    else:
                        row.update(incomplete=True, measurementNote='Links or special files excluded')
                except FileNotFoundError:
                    if path != self.path:
                        row.update(incomplete=True, measurementNote='Files changed during measurement')
                except OSError:
                    row.update(bytes=None, incomplete=True, measurementNote='Some files could not be read')
                self.remember(path, row)
            self.add(row)
            if path == self.path:
                self.result = row
                self.done = True
        result = dict(self.result)
        if not self.done:
            result.update(incomplete=True, pending=True,
                          measurementNote='Still measuring; the total will increase as more files are checked')
        return result
