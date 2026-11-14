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

An installation must never be made in a folder holding unified-uninstall.json.
Setup installs only into an absent or empty folder. The after-exit step checks
the journal before every named top-level entry.
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
import time

from .windows_files import _INVALID, DWORD, FILE_INFO, HANDLE
from .windows_read_files import _Attributes, _ReadAPI, _Status, _Unicode

_DIRECTORY, _LINK = 0x10, 0x400  # FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT


# All variable data travels on stdin, never in this program. Unlike the pinned
# in-process walk below, this path-based last step deletes links only as links.
# It cannot prevent a same-account program swapping an entry between cmdlets.
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
    # The only method call: nonrecursive deletion of the link itself avoids
    # Windows PowerShell's folder-link removal behavior. Constrained language
    # can refuse this call; then the link stays and prevents root removal.
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
function Clear-Entry($entry) {
    # Type and attributes come from the parent's listing, not a per-file stat.
    if (Later $entry) { return }
    if ("$($entry.Attributes)" -match 'ReparsePoint') { Clear-Link $entry; return }
    $path = $entry.FullName
    try {
        if ($entry.PSIsContainer) {
            if ((Get-Date) -ge $until) { Keep $path; return }
            $children = @(Get-ChildItem -LiteralPath $path -Force)
            $plain = @($children | Where-Object {
                -not (Later $_) -and -not $_.PSIsContainer -and
                "$($_.Attributes)" -notmatch 'ReparsePoint|ReadOnly|Hidden|System'
            })
            if ($plain.Count) {
                # One cmdlet call per folder, even for thousands of plain files.
                Remove-Item -LiteralPath $plain.FullName -ErrorAction SilentlyContinue
            }
            foreach ($child in $children) {
                if ($child.PSIsContainer -or "$($child.Attributes)" -match 'ReparsePoint') {
                    Clear-Entry $child
                } elseif ("$($child.Attributes)" -match 'ReadOnly|Hidden|System') {
                    Clear-File $child
                }
            }
            $remaining = @(Get-ChildItem -LiteralPath $path -Force)
            foreach ($child in $remaining) { Keep $child.FullName }
            if ($remaining.Count) { return }
            Remove-Item -LiteralPath $path -Force
        } else { Clear-File $entry }
    } catch { Keep $path }
}
try {
    # Enumerate the entire stdin pipeline before parsing or touching the tree.
    $text = $input | Out-String
    $p = ConvertFrom-Json -InputObject $text
    if ($p.root -isnot [string] -or -not $p.root -or
        $p.token -isnot [string] -or $p.token -cnotmatch '^[0-9a-f]{32}$' -or
        $p.choice -cnotin @('delete', 'keep')) { throw 'Invalid root, token or choice' }
    $names = @($p.contents) + @($p.folders) + @($p.files)
    foreach ($name in $names) {
        if ($name -isnot [string] -or -not $name -or $name -in @('.', '..') -or
            $name -match '[\\/:\x00]' -or $name -ieq 'unified-uninstall.json') {
            throw 'Not a single entry name'
        }
    }
    if ($p.seconds -lt 0 -or $p.seconds -gt 3600) { throw 'Invalid duration' }
    if (-not (Authorized)) { throw 'Root or token refused' }
    $until = (Get-Date) + (New-TimeSpan -Seconds 30)
    do {
        $alive = @($p.wait_for | ForEach-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        })
        if (-not $alive.Count -or (Get-Date) -ge $until) { break }
        Start-Sleep -Seconds 1
    } while ($true)
    $until = (Get-Date) + (New-TimeSpan -Seconds $p.seconds)
    do {
        if (-not (Authorized)) { throw 'Root or token refused' }
        $passes++
        $script:left = @()
        $script:leftCount = 0
        $script:seen = @{}
        $entries = @(Get-ChildItem -LiteralPath $p.root -Force)
        foreach ($entry in $entries) {
            if (@($p.contents) -ccontains $entry.Name) {
                if (-not (Authorized)) { throw 'Root or token refused' }
                Clear-Entry $entry
            }
        }
        foreach ($entry in @(Get-ChildItem -LiteralPath $p.root -Force)) {
            if (@($p.folders) -ccontains $entry.Name) {
                if (-not (Authorized)) { throw 'Root or token refused' }
                if (Later $entry) { continue }
                if ((Get-Date) -ge $until) { Keep $entry.FullName; continue }
                if ("$($entry.Attributes)" -match 'ReparsePoint') { Clear-Link $entry }
                elseif ((Real-Folder $entry) -and
                    -not @(Get-ChildItem -LiteralPath $entry.FullName -Force).Count) {
                    try { Remove-Item -LiteralPath $entry.FullName -Force }
                    catch { Keep $entry.FullName }
                } else { Keep $entry.FullName }
            }
        }
        foreach ($entry in @(Get-ChildItem -LiteralPath $p.root -Force)) {
            if (@($p.files) -ccontains $entry.Name) {
                if (-not (Authorized)) { throw 'Root or token refused' }
                if (Later $entry) { continue }
                if ("$($entry.Attributes)" -match 'ReparsePoint') { Clear-Link $entry }
                elseif (-not $entry.PSIsContainer) {
                    Clear-File $entry
                } else { Keep $entry.FullName }
            }
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


def finish_after_exit(root, token, *, contents, folders, files, wait_for=(), seconds=60, report=None,
                      choice="delete"):
    """Start the token-guarded last step outside the installation and its Job.

    A successful return means only that the payload was written, not that the
    folder was removed. Failure to escape a Job retries process creation alone;
    no already-started helper is replayed. Links are deleted as links; unnamed
    entries and links whose nonrecursive deletion is refused stay.

    If Windows refuses Job breakaway, the helper may end with the launcher,
    leaving program files. The process wait ends after 30 seconds even if the
    processes still run. Paths longer than 260 characters may stay.
    """
    from .windows_service import WindowsServiceError, _powershell

    root = os.fspath(root)
    drive, _ = ntpath.splitdrive(root)
    if (not ntpath.isabs(root) or len(drive) != 2 or drive[1] != ":"
            or drive[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        raise ValueError("Root must be an absolute local drive path.")
    root = ntpath.normpath(root)
    groups = [list(group) for group in (contents, folders, files)]
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
    payload = json.dumps({
        "root": root, "token": token, "choice": choice,
        "contents": groups[0], "folders": groups[1], "files": groups[2],
        "wait_for": wait_for, "seconds": seconds,
        "report": os.fspath(report) if report is not None else None,
    }, ensure_ascii=True).encode("ascii")
    try:
        program = _powershell()
    except WindowsServiceError as error:
        raise OSError("Cannot locate Windows PowerShell.") from error
    environment = os.environ.copy()
    root_path = ntpath.abspath(root)
    for key, value in list(environment.items()):
        if key.upper() not in {"TEMP", "TMP"}:
            continue
        try:
            inside = ntpath.commonpath((root_path, ntpath.abspath(value))).casefold() == root_path.casefold()
        except ValueError:  # Another drive.
            inside = False
        if inside:
            if not environment.get("LOCALAPPDATA"):
                raise OSError("Cannot relocate the helper's temporary folder.")
            environment[key] = ntpath.join(environment["LOCALAPPDATA"], "Temp")
    arguments = [program, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                 base64.b64encode(FINISH_SCRIPT.encode("utf-16-le")).decode("ascii")]
    options = dict(cwd=str(PureWindowsPath(program).parents[2]), env=environment,
                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(arguments, creationflags=flags | 0x01000000, **options)
    except OSError:
        process = subprocess.Popen(arguments, creationflags=flags, **options)
    try:
        process.stdin.write(payload)
    finally:
        process.stdin.close()
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

    def holding(self, names):
        """Which of these entries of the folder still hold something.

        An absent entry holds nothing, and neither does a real folder with
        nothing in it: Windows keeps such a folder while a program uses it as
        its current folder. A file, a link, a folder with an entry, and an
        entry that cannot be examined all count. A link is never entered.
        """
        wanted, held = set(names), []
        for name, attributes in self._api.children(self._handle):
            if name not in wanted:  # Exact spelling.
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
                held.append(name)
        return sorted(held)

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
