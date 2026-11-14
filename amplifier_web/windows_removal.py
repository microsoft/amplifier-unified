"""Delete folders of one installation on native Windows without following a link.

Uninstall can delete a person's installation folder. That cannot be undone, so
this walk never trusts a path twice:

- The folder is opened once. Its type, exact spelling, owner and access list
  are checked on that handle, and the handle stays open. It shares read and
  write but not delete: no program can rename or replace the folder meanwhile,
  and other programs can still create and rename files inside it.
- Names come from listing an open handle. Each entry is opened relative to its
  parent's handle, as the entry itself, checked on its own handle and deleted
  through that handle. No path is built or resolved again.
- A link of any kind (junction, symbolic link, mount point, placeholder) is
  removed as a link. Its target is never opened.
- No attribute is changed. A read-only file is deleted as it is, so a hard
  link elsewhere keeps its file exactly as it was.
- An entry that cannot be deleted (in use) is counted and left. Nothing is
  forced, and the walk continues.

Requires Windows 10 1809 or later on local NTFS. There is no fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
import time

from .windows_files import _INVALID, DWORD, FILE_INFO, HANDLE
from .windows_read_files import _Attributes, _ReadAPI, _Status, _Unicode

_DIRECTORY, _LINK = 0x10, 0x400  # FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT


class _Entry(C.Structure):
    """FILE_FULL_DIR_INFO without its name, which follows at offset 68."""
    _fields_ = [("next", DWORD), ("index", DWORD), ("times", C.c_int64 * 4), ("end", C.c_int64),
                ("allocation", C.c_int64), ("attributes", DWORD), ("length", DWORD), ("ea", DWORD)]


class _Native(_ReadAPI):
    """The five native steps of the walk. Every handle is closed by the caller."""

    def root(self, path):
        # READ_CONTROL | SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY;
        # share read and write, never delete; the folder itself, not a link target.
        handle = self.CreateFileW(str(path), 0x120081, 3, None, 3, 0x2200000, None)
        if handle == _INVALID:
            raise C.WinError(C.get_last_error())
        try:
            self.info(handle, path, directory=True)  # a real folder with this exact spelling
            self.validate_acl(handle, private=True)  # this person's own; no other account
            return handle
        except BaseException:
            self.close(handle)
            raise

    def children(self, handle):
        """Every (name, attributes) in the open folder, read from the handle."""
        rows, buffer, restart = [], C.create_string_buffer(64 * 1024), True
        while True:
            # FileFullDirectoryRestartInfo first, then FileFullDirectoryInfo.
            if not self.basic(handle, 15 if restart else 14, buffer, len(buffer)):
                error = C.get_last_error()
                if error == 18:  # ERROR_NO_MORE_FILES
                    return rows
                raise C.WinError(error)
            restart, offset = False, 0
            while True:
                entry = _Entry.from_buffer(buffer, offset)
                name = C.wstring_at(C.addressof(buffer) + offset + 68, entry.length // 2)
                if name not in {".", ".."}:
                    rows.append((name, entry.attributes))
                if not entry.next:
                    break
                offset += entry.next

    def open(self, parent, name, *, enter, delete=True):
        """Open one entry of an open folder as itself; never through a link."""
        if not name or name in {".", ".."} or any(mark in name for mark in "\\/:"):
            raise OSError(22, "not a single entry name")
        text = C.create_unicode_buffer(name)
        size = len(name.encode("utf-16-le", "surrogatepass"))
        unicode = _Unicode(size, size + 2, C.cast(text, C.c_wchar_p))
        # Exact name, relative to the parent handle. No OBJ_CASE_INSENSITIVE.
        attributes = _Attributes(C.sizeof(_Attributes), parent, C.pointer(unicode), 0, None, None)
        handle, status = HANDLE(), _Status()
        # SYNCHRONIZE | FILE_READ_ATTRIBUTES, plus DELETE and/or FILE_LIST_DIRECTORY.
        access = 0x100080 | (0x10000 if delete else 0) | (1 if enter else 0)
        # A folder that the walk enters denies delete sharing: it cannot be
        # renamed or replaced while its contents go. FILE_OPEN; synchronous;
        # FILE_OPEN_REPARSE_POINT opens a link itself.
        result = self.create(C.byref(handle), access, C.byref(attributes), C.byref(status),
                             None, 0, 3 if enter else 7, 1, 0x200020, None, 0)
        if result < 0:
            raise C.WinError(self.dos_error(result))
        return handle.value

    def kind(self, handle):
        info = FILE_INFO()
        self.ok(self.GetFileInformationByHandle(handle, C.byref(info)))
        if info.attributes & _LINK:
            return "link"
        return "folder" if info.attributes & _DIRECTORY else "file"

    def delete(self, handle):
        # FileDispositionInfoEx: DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE.
        # The name goes when this handle closes, also while other handles exist.
        flags = DWORD(0x13)
        self.ok(self.SetFileInformationByHandle(handle, 21, C.byref(flags), C.sizeof(flags)))


class _Tally:
    def __init__(self):
        self.removed, self.left, self.count = 0, [], 0

    def keep(self, trail):
        self.count += 1
        if len(self.left) < 20:
            self.left.append("\\".join(trail))


def _remove(api, parent, name, attributes, trail, tally):
    """Delete one entry and everything in it. True when it is gone."""
    listed_folder = bool(attributes & _DIRECTORY) and not attributes & _LINK
    handle, deletable = None, True
    try:
        try:
            handle = api.open(parent, name, enter=listed_folder)
        except OSError:
            if not listed_folder:
                raise
            # Another program holds this folder. Empty it as far as possible.
            handle, deletable = api.open(parent, name, enter=True, delete=False), False
        if api.kind(handle) == "folder":
            if not listed_folder:
                raise OSError(22, "the entry changed into a folder")  # Not entered without list access.
            if not _empty(api, handle, trail, tally):
                return False  # What stayed inside is already counted.
        # A link, a file or an empty folder: the entry itself goes.
        if not deletable:
            raise OSError(32, "the folder is in use")
        api.delete(handle)
        tally.removed += 1
        return True
    except OSError:
        tally.keep(trail)
        return False
    finally:
        if handle is not None:
            api.close(handle)


def _empty(api, handle, trail, tally):
    empty = True
    for name, attributes in api.children(handle):
        if not _remove(api, handle, name, attributes, [*trail, name], tally):
            empty = False
    return empty


class Folder:
    """One proven, open folder. Use it only inside `folder()`."""

    def __init__(self, api, handle):
        self._api, self._handle = api, handle

    def names(self):
        """The exact names directly in the folder, read from its handle."""
        return sorted(name for name, _ in self._api.children(self._handle))

    def _each(self, names):
        tally, wanted = _Tally(), set(names)
        for name, attributes in self._api.children(self._handle):
            if name in wanted:  # Exact spelling; nothing is matched by pattern here.
                _remove(self._api, self._handle, name, attributes, [name], tally)
        return tally

    def remove(self, names, *, settle=0, wait=time.sleep):
        """Delete these entries of the folder, each with everything in it.

        `settle` seconds are allowed for files that a closing program still
        holds. Returns the number removed and what stayed (at most 20 names).
        """
        removed, deadline = 0, time.monotonic() + settle
        while True:
            tally = self._each(names)
            removed += tally.removed
            if not tally.count or time.monotonic() >= deadline:
                return {"removed": removed, "left": tally.left, "leftCount": tally.count}
            wait(0.5)


@contextmanager
def folder(path):
    """Open one private folder of this person, prove it on the handle, hold it.

    Every folder above it is checked first (owner, access for other accounts,
    not a link, exact spelling). Refuses with OSError and changes nothing.
    """
    from .windows_files import address, directory
    path = address(path)
    api = _Native()
    with directory(path, owned_root=path):
        handle = api.root(path)
    try:
        yield Folder(api, handle)
    finally:
        api.close(handle)
