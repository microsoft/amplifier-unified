"""Delete folders of one installation on native Windows without following a link.

Uninstall can delete a person's installation folder. That cannot be undone, so
this walk never trusts a path twice:

- The folder is opened once. Its type, exact spelling, owner and access list
  are checked on that handle, and the handle stays open. It shares read and
  write but not delete: no program can rename or replace the folder meanwhile,
  and other programs can still create and rename files inside it.
- Names come from listing an open handle. Each entry is opened relative to its
  parent's handle, as the entry itself, checked on its own handle and deleted
  through that handle. No path is built or resolved again. While an entry is
  open it does not share delete, so no program can move it elsewhere first.
- A link of any kind (junction, symbolic link, mount point, placeholder) is
  removed as a link. Its target is never opened.
- No attribute is changed. A read-only file is deleted as it is, so a hard
  link elsewhere keeps its file exactly as it was.
- An entry that cannot be deleted (in use) is counted and left. Nothing is
  forced, and the walk continues.

Requires Windows 10 1809 or later on local NTFS. There is no fallback.

An installation must never be made in a folder holding unified-uninstall.json.
Setup installs only into an absent or empty folder. The after-exit step checks
the journal before every named top-level entry.

A running program cannot delete the files that it runs from. It can move
them. `Folder.aside` holds one flat folder, `Folder.remove(..., park=...)`
moves such a file into it through its handle, and the step after exit starts
with that folder as its current folder. Windows keeps a program's current
folder open without delete sharing. So the folder that the uninstall program
held stays held when that program has ended, and no program can rename or
replace it, the installation folder or a folder above it. The step deletes
the few parked files there. Everything else it removes is an empty folder.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import ctypes as C
import json
import ntpath
import os
from pathlib import PureWindowsPath
import subprocess
import threading
import time

from .windows_files import _INVALID, DWORD, FILE_INFO, FILE_RENAME_INFO, HANDLE
from .windows_read_files import _Attributes, _ReadAPI, _Status, _Unicode

_DIRECTORY, _LINK = 0x10, 0x400  # FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT


def same_name(name):
    """A key that is equal for every spelling that Windows can take for one name.

    Windows compares names with its own upper-case table. This key is at least
    as wide for the plain names that an installation uses: it also joins
    spellings that Windows keeps apart, which only makes a caller more careful.
    """
    return name.upper().casefold()


# All variable data travels on stdin, never in this program. This last step
# works with paths. It never walks a tree: it deletes plain files directly in
# one held folder, removes named folders only when they are empty, and deletes
# named plain files directly in the held installation folder.
#
# "Held": Windows keeps a program's current folder open without delete sharing.
# The uninstall program starts this step in the flat folder while it holds that
# folder itself. From then on no program can rename or replace that folder, the
# installation folder or a folder above it. Before the step removes the flat
# folder it makes the installation folder its current folder, and it gives that
# one free only to remove it when it is empty.
FINISH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$p = $null
$passes = 0
$removed = $false
$started = Get-Date
$startedUtc = ([datetimeoffset]$started).UtcDateTime
$script:left = @()
$script:leftCount = 0
$script:seen = @{}
$reason = ''
function Keep($path) {
    if (-not $script:seen[$path]) {
        $script:seen[$path] = $true
        $script:leftCount++
        if ($script:left.Count -lt 20) { $script:left += $path }
    }
}
function Reset {
    $script:left = @()
    $script:leftCount = 0
    $script:seen = @{}
}
function Real-Folder($item) {
    return ($item.PSIsContainer -and "$($item.Attributes)" -notmatch 'ReparsePoint')
}
function Later($entry) {
    if ($entry.CreationTimeUtc -gt $startedUtc) { Keep $entry.FullName; return $true }
    return $false
}
function Authorized {
    try {
        $root = Get-Item -LiteralPath $p.root -Force
        if (-not (Real-Folder $root) -or (Later $root)) { return $false }
        $journal = Get-Item -LiteralPath (Join-Path $p.root 'unified-uninstall.json') -Force
        if ($journal.PSIsContainer -or "$($journal.Attributes)" -match 'ReparsePoint' -or
            (Later $journal)) {
            return $false
        }
        $text = Get-Content -LiteralPath $journal.FullName -Raw
        if ($text -notmatch '^\s*\{') { return $false }
        $record = ConvertFrom-Json -InputObject $text
        return ($record -is [pscustomobject] -and
            ($record.schema -is [int] -or $record.schema -is [long]) -and $record.schema -eq 1 -and
            $record.choice -is [string] -and $record.choice -ceq $p.choice -and $record.token -is [string] -and
            $record.token -ceq $p.token -and $record.token -cmatch '^[0-9a-f]{32}$')
    } catch { return $false }
}
function Clear-Link($entry) {
    if (Later $entry) { return }
    # Nonrecursive deletion of the link itself avoids Windows PowerShell's
    # folder-link removal behavior. Constrained language can refuse this call;
    # then the link stays and prevents root removal.
    try { $entry.Delete() } catch { Keep $entry.FullName }
}
function Clear-File($entry) {
    if (Later $entry) { return }
    try {
        if ("$($entry.Attributes)" -match 'ReadOnly|Hidden|System') {
            if ($entry.LinkType -eq 'HardLink') { Keep $entry.FullName; return }
            Remove-Item -LiteralPath $entry.FullName -Force
        } else {
            Remove-Item -LiteralPath $entry.FullName -ErrorAction SilentlyContinue
        }
    } catch { Keep $entry.FullName }
}
function Clear-Folder($entry) {
    # Only an empty folder goes, and never with -Recurse: no file is deleted
    # here, whatever the folder is by the time Windows removes it.
    if (Later $entry) { return }
    if ("$($entry.Attributes)" -match 'ReparsePoint') { Clear-Link $entry; return }
    try {
        if ((Real-Folder $entry) -and -not @(Get-ChildItem -LiteralPath $entry.FullName -Force).Count) {
            Remove-Item -LiteralPath $entry.FullName -Force
        } else { Keep $entry.FullName }
    } catch { Keep $entry.FullName }
}
try {
    # Enumerate the entire stdin pipeline before parsing or touching the tree.
    $text = $input | Out-String
    . {
        $p = ConvertFrom-Json -InputObject $text
        if ($p.root -isnot [string] -or -not $p.root -or
            $p.token -isnot [string] -or $p.token -cnotmatch '^[0-9a-f]{32}$' -or
            $p.choice -cnotin @('delete', 'keep')) { throw 'Invalid root, token or choice' }
        $names = @($p.park) + @($p.folders) + @($p.files)
        foreach ($name in $names) {
            if ($name -isnot [string] -or -not $name -or $name -in @('.', '..') -or
                $name -match '[\\/:\x00]' -or $name -ieq 'unified-uninstall.json') {
                throw 'Not a single entry name'
            }
        }
        if ($p.seconds -lt 0 -or $p.seconds -gt 3600) { throw 'Invalid duration' }
        $park = Join-Path $p.root $p.park
        # This program's own current folder is the held flat folder. PowerShell's
        # location is a separate thing; it moves away so that no cmdlet refuses.
        if ([Environment]::CurrentDirectory -ne $park) { throw 'Not started in the held folder' }
        Set-Location -LiteralPath $p.outside
        if (-not (Authorized)) { throw 'Root or token refused' }
        Write-Host 'ready'
        $until = (Get-Date) + (New-TimeSpan -Seconds $p.seconds)
        # 1. The parked program files, in the folder that is held. This ends
        #    when the uninstall program has ended and the folder is empty.
        do {
            if (-not (Authorized)) { throw 'Root or token refused' }
            $passes++
            Reset
            $alive = @($p.wait_for | ForEach-Object {
                Get-Process -Id $_ -ErrorAction SilentlyContinue
            })
            if (-not (Real-Folder (Get-Item -LiteralPath $park -Force))) { throw 'Held folder refused' }
            foreach ($entry in @(Get-ChildItem -LiteralPath $park -Force)) {
                if ($entry.PSIsContainer -or "$($entry.Attributes)" -match 'ReparsePoint') {
                    Keep $entry.FullName
                } else { Clear-File $entry }
            }
            $inside = @(Get-ChildItem -LiteralPath $park -Force)
            foreach ($entry in $inside) { Keep $entry.FullName }
            if ((-not $alive.Count -and -not $inside.Count) -or (Get-Date) -ge $until) { break }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $until)
        # 2. Hold the installation folder itself. That gives the flat folder free.
        [Environment]::CurrentDirectory = $p.root
        do {
            if (-not (Authorized)) { throw 'Root or token refused' }
            $passes++
            Reset
            foreach ($entry in @(Get-ChildItem -LiteralPath $p.root -Force)) {
                if ($names -cnotcontains $entry.Name) { continue }
                if (-not (Authorized)) { throw 'Root or token refused' }
                if ($entry.Name -ceq $p.park -or @($p.folders) -ccontains $entry.Name) {
                    Clear-Folder $entry
                } elseif ("$($entry.Attributes)" -match 'ReparsePoint') { Clear-Link $entry }
                elseif (-not $entry.PSIsContainer) { Clear-File $entry }
                else { Keep $entry.FullName }
            }
            $remaining = @(Get-ChildItem -LiteralPath $p.root -Force |
                Where-Object { $names -ccontains $_.Name })
            if (-not $remaining.Count -or (Get-Date) -ge $until) { break }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $until)
        if (-not (Authorized)) { throw 'Root or token refused' }
        $remaining = @(Get-ChildItem -LiteralPath $p.root -Force)
        if ($remaining.Count -eq 1 -and $remaining[0].Name -ceq 'unified-uninstall.json') {
            Clear-File $remaining[0]
            # 3. Give the installation folder free. It goes only when it is empty.
            [Environment]::CurrentDirectory = $p.outside
            if (-not @(Get-ChildItem -LiteralPath $p.root -Force).Count) {
                Remove-Item -LiteralPath $p.root -Force
                $removed = $true
            }
        } else {
            foreach ($entry in $remaining) {
                if ($entry.Name -cne 'unified-uninstall.json' -and
                    $script:left -cnotcontains $entry.FullName) { Keep $entry.FullName }
            }
        }
    } | Out-Null
} catch { $reason = $_.Exception.Message }
finally {
    if ($p.report) {
        @{ passes = $passes; left = @($script:left); leftCount = $script:leftCount
           elapsed = [double]('{0:F1}' -f ((Get-Date) - $started).TotalSeconds)
           folderRemoved = $removed; reason = $reason } |
            ConvertTo-Json -Compress -Depth 4 |
            Set-Content -LiteralPath $p.report -Encoding UTF8
    }
}
"""


def finish_after_exit(root, token, *, park, folders, files, wait_for=(), seconds=60, report=None,
                      choice="delete", ready=30):
    """Start the token-guarded last step in a held folder, outside the installation's Job.

    `park` names a folder directly in `root` that the caller holds open, as
    `Folder.aside` does. The step starts with that folder as its current
    folder, and this call returns only when the step has said that it runs.
    From then on Windows keeps that folder open for the step, so no program
    can rename or replace it, the installation folder or a folder above it,
    also when the caller has ended.

    The step deletes plain files directly in `park`, removes `park` and the
    `folders` only when they are empty, deletes the plain `files` directly in
    `root`, then the journal, then `root` only when it is empty. Links are
    deleted as links. Entries that are not named stay.

    A return does not mean that anything was removed. A step that does not
    answer within `ready` seconds raises OSError. If Windows refuses Job
    breakaway, the step may end with the launcher; then the parked files stay.
    """
    from .windows_service import WindowsServiceError, _powershell

    root = os.fspath(root)
    drive, _ = ntpath.splitdrive(root)
    if (not ntpath.isabs(root) or len(drive) != 2 or drive[1] != ":"
            or drive[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        raise ValueError("Root must be an absolute local drive path.")
    root = ntpath.normpath(root)
    groups = [[park], list(folders), list(files)]
    for group in groups:
        for name in group:
            if (not isinstance(name, str) or not name or name in {".", ".."}
                    or any(mark in name for mark in "\\/:\0")
                    or name.casefold() == "unified-uninstall.json"):
                raise ValueError("Not a single entry name.")
    if (not isinstance(token, str) or len(token) != 32
            or any(char not in "0123456789abcdef" for char in token)):
        raise ValueError("Token must be exactly 32 lowercase hex characters.")
    if choice not in ("delete", "keep"):
        raise ValueError("Choice must be delete or keep.")
    if type(seconds) not in (int, float) or not 0 <= seconds <= 3600:
        raise ValueError("Duration must be between zero and 3600 seconds.")
    wait_for = list(wait_for)
    if any(type(pid) is not int or pid <= 0 for pid in wait_for):
        raise ValueError("Wait targets must be positive process IDs.")
    try:
        program = _powershell()
    except WindowsServiceError as error:
        raise OSError("Cannot locate Windows PowerShell.") from error
    outside = str(PureWindowsPath(program).parents[2])  # The Windows folder.
    payload = json.dumps({
        "root": root, "token": token, "choice": choice, "park": park, "outside": outside,
        "folders": groups[1], "files": groups[2], "wait_for": wait_for, "seconds": seconds,
        "report": os.fspath(report) if report is not None else None,
    }, ensure_ascii=True).encode("ascii")
    environment = os.environ.copy()
    for key, value in list(environment.items()):
        if key.upper() not in {"TEMP", "TMP"}:
            continue
        try:
            inside = ntpath.commonpath((root, ntpath.abspath(value))).casefold() == root.casefold()
        except ValueError:  # Another drive.
            inside = False
        if inside:
            if not environment.get("LOCALAPPDATA"):
                raise OSError("Cannot relocate the helper's temporary folder.")
            environment[key] = ntpath.join(environment["LOCALAPPDATA"], "Temp")
    arguments = [program, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                 base64.b64encode(FINISH_SCRIPT.encode("utf-16-le")).decode("ascii")]
    # The held folder is the step's current folder from its first moment.
    options = dict(cwd=ntpath.join(root, park), env=environment,
                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(arguments, creationflags=flags | 0x01000000, **options)
    except OSError:
        process = subprocess.Popen(arguments, creationflags=flags, **options)
    try:
        process.stdin.write(payload)
    finally:
        process.stdin.close()
    # The step writes one line when it runs in the held folder and its checks
    # passed. Until then the caller must not let the folder go.
    answer = []
    reader = threading.Thread(target=lambda: answer.append(process.stdout.readline()), daemon=True)
    reader.start()
    reader.join(ready)
    if not answer or answer[0].strip() != b"ready":
        raise OSError("The last step did not start in the held folder.")
    process.stdout.close()
    return process.pid


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
        # The name comes from the parent's own listing, relative to its handle.
        attributes = _Attributes(C.sizeof(_Attributes), parent, C.pointer(unicode), 0, None, None)
        handle, status = HANDLE(), _Status()
        # SYNCHRONIZE | FILE_READ_ATTRIBUTES, plus DELETE and/or FILE_LIST_DIRECTORY.
        access = 0x100080 | (0x10000 if delete else 0) | (1 if enter else 0)
        # No entry shares delete while the walk holds it. A folder cannot be
        # renamed or replaced while its contents go, and a file or a link
        # cannot be moved out of the folder between this open and its
        # deletion. An entry that another program holds for renaming or
        # deletion refuses here and is left. FILE_OPEN; synchronous;
        # FILE_OPEN_REPARSE_POINT opens a link itself.
        result = self.create(C.byref(handle), access, C.byref(attributes), C.byref(status),
                             None, 0, 3, 1, 0x200020, None, 0)
        if result < 0:
            raise C.WinError(self.dos_error(result))
        return handle.value

    def make(self, parent, name):
        """Create one new folder in an open folder and return it open, held as an entered folder."""
        if not name or name in {".", ".."} or any(mark in name for mark in "\\/:"):
            raise OSError(22, "not a single entry name")
        text = C.create_unicode_buffer(name)
        size = len(name.encode("utf-16-le", "surrogatepass"))
        unicode = _Unicode(size, size + 2, C.cast(text, C.c_wchar_p))
        attributes = _Attributes(C.sizeof(_Attributes), parent, C.pointer(unicode), 0, None, None)
        handle, status = HANDLE(), _Status()
        # SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY; shares read
        # and write, never delete. FILE_CREATE: an existing entry of that name
        # refuses. A folder; synchronous; never through a link.
        result = self.create(C.byref(handle), 0x100081, C.byref(attributes), C.byref(status),
                             None, 0, 3, 2, 0x200021, None, 0)
        if result < 0:
            raise C.WinError(self.dos_error(result))
        return handle.value

    def park(self, handle, folder, name):
        """Move an open file into an open folder under a single name. No path is used.

        A file that a running program has mapped cannot be deleted, but it can
        be moved. An existing name refuses; nothing is replaced.
        """
        if not name or name in {".", ".."} or any(mark in name for mark in "\\/:"):
            raise OSError(22, "not a single entry name")
        native = C.WinDLL("ntdll")
        native.NtSetInformationFile.argtypes = [HANDLE, C.c_void_p, C.c_void_p, DWORD, C.c_int]
        native.NtSetInformationFile.restype = C.c_long
        encoded = name.encode("utf-16-le")
        buffer = C.create_string_buffer(C.sizeof(FILE_RENAME_INFO) + len(encoded))
        info = FILE_RENAME_INFO.from_buffer(buffer)
        info.Flags, info.RootDirectory, info.FileNameLength = 0, folder, len(encoded)
        C.memmove(C.addressof(buffer) + FILE_RENAME_INFO.FileName.offset, encoded, len(encoded))
        block = (C.c_void_p * 2)()
        # FileRenameInformation: the target is the folder handle plus one name.
        result = native.NtSetInformationFile(handle, C.byref(block), buffer, len(buffer), 10)
        if result < 0:
            raise C.WinError(self.dos_error(result))

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
        self.removed, self.left, self.count, self.parked = 0, [], 0, 0

    def keep(self, trail):
        self.count += 1
        if len(self.left) < 20:
            self.left.append("\\".join(trail))


def _remove(api, parent, name, attributes, trail, tally, park=None):
    """Delete one entry and everything in it. True when it is gone.

    With `park`, a plain file that cannot be deleted is moved, through its
    handle, into the folder that the caller holds; it is then gone from here.
    """
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
            if not _empty(api, handle, trail, tally, park):
                return False  # What stayed inside is already counted.
        # A link, a file or an empty folder: the entry itself goes.
        if not deletable:
            raise OSError(32, "the folder is in use")
        try:
            api.delete(handle)
        except OSError:
            # A file that a running program has mapped cannot be deleted, but
            # it can be moved. Only a plain file, never a link or a folder.
            if park is None or api.kind(handle) != "file":
                raise
            park(handle)
            tally.parked += 1
            return True
        tally.removed += 1
        return True
    except OSError:
        tally.keep(trail)
        return False
    finally:
        if handle is not None:
            api.close(handle)


def _empty(api, handle, trail, tally, park=None):
    empty = True
    for name, attributes in api.children(handle):
        if not _remove(api, handle, name, attributes, [*trail, name], tally, park):
            empty = False
    return empty


class Folder:
    """One proven, open folder. Use it only inside `folder()`."""

    def __init__(self, api, handle):
        self._api, self._handle = api, handle

    def names(self):
        """The exact names directly in the folder, read from its handle."""
        return sorted(name for name, _ in self._api.children(self._handle))

    def holding(self, names):
        """Which of these entries of the folder still hold something.

        An absent entry holds nothing, and neither does a real folder with
        nothing in it: Windows keeps such a folder while a program uses it as
        its current folder. A file, a link, a folder with an entry, and an
        entry that cannot be examined all count. A link is never entered.

        An entry under another spelling of a name (`App` for `app`) is the
        same entry to Windows. It is never examined or deleted, and the name
        counts as holding something. One listing decides for every spelling,
        so a program cannot hide an entry by renaming it between two looks.
        """
        wanted, held = {same_name(name): name for name in names}, set()
        for name, attributes in self._api.children(self._handle):
            own = wanted.get(same_name(name))
            if own is None:
                continue
            if name != own:
                held.add(own)
                continue
            handle, empty = None, False
            try:
                if attributes & _DIRECTORY and not attributes & _LINK:
                    handle = self._api.open(self._handle, name, enter=True, delete=False)
                    empty = self._api.kind(handle) == "folder" and not self._api.children(handle)
            except OSError:
                pass
            finally:
                if handle is not None:
                    self._api.close(handle)
            if not empty:
                held.add(name)
        return sorted(held)

    def _each(self, names, park=None):
        tally, wanted = _Tally(), set(names)
        for name, attributes in self._api.children(self._handle):
            if name in wanted:  # Exact spelling; nothing is matched by pattern here.
                _remove(self._api, self._handle, name, attributes, [name], tally, park)
        return tally

    def remove(self, names, *, settle=0, wait=time.sleep, park=None):
        """Delete these entries of the folder, each with everything in it.

        `settle` seconds are allowed for files that a closing program still
        holds. Returns the number removed and what stayed (at most 20 names).

        `park` is the function that `aside` yields. With it, a plain file that
        cannot be deleted because a running program has mapped it is moved
        into the held folder, and the result also counts those as `parked`.
        Use it only for the running program's own folders, never for data.
        """
        removed, parked, deadline = 0, 0, time.monotonic() + settle
        while True:
            tally = self._each(names, park)
            removed, parked = removed + tally.removed, parked + tally.parked
            if not tally.count or time.monotonic() >= deadline:
                result = {"removed": removed, "left": tally.left, "leftCount": tally.count}
                return result if park is None else {**result, "parked": parked}
            wait(0.5)

    @contextmanager
    def aside(self, name):
        """Create one new folder directly in this folder and hold it open.

        Yields the function that moves an open file into it under the next
        free number. While the folder is held, no program can rename or
        replace it. An entry of that name that exists already refuses.
        """
        handle = self._api.make(self._handle, name)
        count = [0]

        def park(file):
            for _ in range(10):  # A number is taken only if a program put an entry there.
                count[0] += 1
                try:
                    return self._api.park(file, handle, str(count[0]))
                except FileExistsError:
                    continue
            raise OSError(17, "no free name in the held folder")
        try:
            yield park
        finally:
            self._api.close(handle)


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
