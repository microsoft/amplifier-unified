"""Resumable, descriptor-anchored observations; never deletion evidence."""
import os
import stat
from pathlib import Path


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)


class Walk:
    def __init__(self, parent, name, path, boundaries, seen, cache):
        self.parent = os.dup(parent)
        self.path, self.boundaries, self.seen, self.cache = path, boundaries, seen, cache
        self.next = (self.parent, name, path)
        self.stack = []
        self.done = False
        self.result = {'bytes': 0, 'files': 0, 'incomplete': False}
        self.fingerprint = None

    def close(self):
        for frame in self.stack:
            frame['entries'].close()
            os.close(frame['fd'])
        self.stack.clear()
        if self.parent is not None:
            os.close(self.parent)
            self.parent = None

    def __del__(self):
        self.close()

    def remember(self, path, info, row):
        if row['incomplete'] or row.get('separateCategory'):
            return
        if len(self.cache) >= 20_000 and stat.S_ISDIR(info.st_mode):
            self.cache.pop(next(iter(self.cache)))
        if len(self.cache) < 20_000:
            self.cache[str(path)] = (fingerprint(info), dict(row))

    def add(self, row):
        for frame in self.stack:
            target = frame['row']
            target['bytes'] += row['bytes']
            target['files'] += row['files']
            target['incomplete'] |= row['incomplete']
            if row.get('measurementNote'):
                notes = set(target.get('measurementNote', '').split(' · ')) - {''}
                notes.update(row['measurementNote'].split(' · '))
                target['measurementNote'] = ' · '.join(sorted(notes))

    def finish_frame(self, error=False):
        frame = self.stack[-1]
        try:
            changed = fingerprint(os.fstat(frame['fd'])) != fingerprint(frame['info'])
        except OSError:
            changed = True
        if error or changed:
            self.add({'bytes': 0, 'files': 0, 'incomplete': True,
                      'measurementNote': 'Folder changed or could not be read during measurement'})
        self.stack.pop()
        frame['entries'].close()
        os.close(frame['fd'])
        self.remember(frame['path'], frame['info'], frame['row'])
        if not self.stack:
            self.result, self.done = frame['row'], True

    def advance(self, budget, expired):
        while not self.done and budget[0] > 0 and not expired():
            if self.next is None:
                if not self.stack:
                    self.done = True
                    break
                frame = self.stack[-1]
                try:
                    entry = next(frame['entries'])
                    self.next = (frame['fd'], entry.name, frame['path']/entry.name)
                except StopIteration:
                    self.finish_frame()
                    continue
                except OSError:
                    self.finish_frame(error=True)
                    continue
            parent, name, path = self.next
            self.next = None
            budget[0] -= 1
            row = {'bytes': 0, 'files': 0, 'incomplete': False}
            info = None
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if path == self.path:
                    self.fingerprint = fingerprint(info)
                saved = self.cache.get(str(path))
                if str(path) in self.boundaries:
                    row['separateCategory'] = True
                elif stat.S_ISLNK(info.st_mode):
                    row.update(linked=True, incomplete=True, measurementNote='Symbolic link excluded')
                elif saved and saved[0] == fingerprint(info):
                    row = dict(saved[1])
                elif stat.S_ISDIR(info.st_mode):
                    if len(self.stack) >= 128:
                        row.update(incomplete=True, measurementNote='Directory depth limit reached')
                    else:
                        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        try:
                            opened = os.fstat(fd)
                            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                                raise OSError('Folder replaced')
                            entries = os.scandir(fd)
                        except BaseException:
                            os.close(fd)
                            raise
                        self.stack.append({'fd': fd, 'entries': entries, 'info': opened,
                                           'path': path, 'row': row})
                        if path == self.path:
                            self.result = row
                            os.close(self.parent)
                            self.parent = None
                        continue
                elif stat.S_ISREG(info.st_mode):
                    key = (info.st_dev, info.st_ino)
                    if info.st_nlink <= 1 or key not in self.seen or self.seen[key] == str(path):
                        if info.st_nlink > 1:
                            if key not in self.seen and len(self.seen) >= 200_000:
                                row.update(incomplete=True, measurementNote='Hard-link accounting limit reached')
                            else:
                                self.seen[key] = str(path)
                                row.update(bytes=info.st_size, files=1)
                        else:
                            row.update(bytes=info.st_size, files=1)
                else:
                    row.update(incomplete=True, measurementNote='Special files excluded')
            except OSError:
                row.update(incomplete=True, measurementNote='Some files could not be read')
            if info is not None:
                self.remember(path, info, row)
            self.add(row)
            if path == self.path:
                self.result, self.done = row, True
        result = dict(self.result)
        if self.done:
            self.close()
        else:
            result.update(incomplete=True, pending=True,
                          measurementNote='Scan limit or time slice reached; measurement will continue')
        return result
