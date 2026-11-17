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
them. `Folder.aside` creates one flat folder and two kept files (one in the
installation folder, one in the flat folder), `Folder.remove(..., park=...)`
moves such a file into the flat folder through its handle, and the step after
exit inherits the two kept files open. A folder cannot be emptied, renamed or
turned into a link while it holds a file that a program keeps open. So the
step deletes the few parked files where their path cannot lead anywhere else.
Everything else it removes is a folder, by RemoveDirectory, plus the journal
and setup's completion record at the end.
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
# works with paths, so it deletes a file only while its path cannot lead
# anywhere else. It walks no tree.
#
# A folder cannot be replaced while it holds a file that a program keeps open
# without any sharing: nobody can delete or move that file, so the folder is
# never empty, Windows turns only an empty folder into a link, and Windows
# refuses to rename a folder with an open file below it. The same holds for
# every folder above.
#
# The uninstall program creates two such files through handles, one in the
# installation folder and one in the flat folder, and this step inherits both
# handles when it is created. Windows deletes each file when its last handle
# closes. This step creates no file and opens no existing file.
#
# - While both are open, the step deletes the parked files directly in the
#   flat folder and, at the end, the journal and setup's completion record
#   directly in the installation folder.
# - It closes the file in the flat folder and removes that folder while the
#   file in the installation folder is still open.
# - It closes that last file and removes the installation folder, if the
#   folder above holds something else.
# Folders go by RemoveDirectory: an empty folder goes, a link goes as a link,
# a folder with entries refuses.
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
function Clear-Folder($entry) {
    # RemoveDirectory only: no file is deleted here. A plain file stays.
    if (Later $entry) { return }
    try {
        if ($entry.PSIsContainer) { $entry.Delete() } else { Keep $entry.FullName }
    } catch { Keep $entry.FullName }
}
function Plain($path) {
    $file = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    return ($null -ne $file -and -not $file.PSIsContainer -and "$($file.Attributes)" -notmatch 'ReparsePoint')
}
function Get-Record($name) {
    # One of the two records. A link, a folder or a protected file is refused.
    $file = Get-Item -LiteralPath (Join-Path $p.root $name) -Force -ErrorAction SilentlyContinue
    if ($null -ne $file -and ($file.PSIsContainer -or
        "$($file.Attributes)" -match 'ReparsePoint|ReadOnly|Hidden|System')) { throw 'Record refused' }
    return $file
}
function Only($path, $names) {
    # That folder holds these names and nothing else.
    return -not @(Get-ChildItem -LiteralPath $path -Force | Where-Object { $names -cnotcontains $_.Name }).Count
}
try {
    # Enumerate the entire stdin pipeline before parsing or touching the tree.
    $text = $input | Out-String
    . {
        $p = ConvertFrom-Json -InputObject $text
        if ($p.root -isnot [string] -or -not $p.root -or
            $p.token -isnot [string] -or $p.token -cnotmatch '^[0-9a-f]{32}$' -or
            $p.choice -cnotin @('delete', 'keep')) { throw 'Invalid root, token or choice' }
        foreach ($name in @($p.park) + @($p.folders)) {
            if ($name -isnot [string] -or -not $name -or $name -in @('.', '..') -or
                $name -match '[\\/:\x00]' -or $name -ieq 'unified-uninstall.json' -or
                $name -ieq 'unified-setup-complete.json') {
                throw 'Not a single folder name'
            }
        }
        if ($p.seconds -lt 0 -or $p.seconds -gt 3600) { throw 'Invalid duration' }
        $park = Join-Path $p.root $p.park
        $own = $p.token + '.hold'
        if (-not (Authorized)) { throw 'Root or token refused' }
        # The two kept files, open since before this program existed: [0] in
        # the installation folder, [1] in the flat folder.
        $kept = @($p.keep | ForEach-Object {
            $handle = New-Object Microsoft.Win32.SafeHandles.SafeFileHandle -ArgumentList ([IntPtr][long]$_), $true
            New-Object System.IO.FileStream -ArgumentList $handle, ([System.IO.FileAccess]'Write')
        })
        if ($kept.Count -ne 2 -or -not (Real-Folder (Get-Item -LiteralPath $park -Force)) -or
            -not (Plain (Join-Path $p.root $own)) -or -not (Plain (Join-Path $park $own))) {
            throw 'Kept files refused'
        }
        Write-Host 'ready'
        $until = (Get-Date) + (New-TimeSpan -Seconds $p.seconds)
        $done = $false
        # 1. The parked program files, directly in the flat folder, and the
        #    named folders that are empty by now.
        do {
            if (-not (Authorized)) { throw 'Root or token refused' }
            $passes++
            Reset
            $alive = @($p.wait_for | ForEach-Object {
                Get-Process -Id $_ -ErrorAction SilentlyContinue
            })
            foreach ($entry in @(Get-ChildItem -LiteralPath $park -Force)) {
                if ($entry.Name -ceq $own) { continue }
                # A parked file has a number as its name. Anything else stays.
                if ($entry.PSIsContainer -or $entry.Name -cnotmatch '^[0-9]+$' -or
                    "$($entry.Attributes)" -match 'ReparsePoint|ReadOnly|Hidden|System') {
                    Keep $entry.FullName
                } elseif (-not (Later $entry)) {
                    # Never -Force: no attribute of any file is changed.
                    Remove-Item -LiteralPath $entry.FullName -ErrorAction SilentlyContinue
                }
            }
            foreach ($entry in @(Get-ChildItem -LiteralPath $p.root -Force)) {
                if (@($p.folders) -ccontains $entry.Name) {
                    if (-not (Authorized)) { throw 'Root or token refused' }
                    Clear-Folder $entry
                }
            }
            $inside = @(Get-ChildItem -LiteralPath $park -Force | Where-Object { $_.Name -cne $own })
            foreach ($entry in $inside) { Keep $entry.FullName }
            $mine = @($p.park, $own, 'unified-uninstall.json', 'unified-setup-complete.json')
            $rest = @(Get-ChildItem -LiteralPath $p.root -Force | Where-Object { $mine -cnotcontains $_.Name })
            foreach ($entry in $rest) { Keep $entry.FullName }
            $done = (-not $alive.Count -and -not $inside.Count -and -not $rest.Count)
            # Waiting helps only for a live program, a parked file or a
            # named folder that is not empty yet.
            $named = @($rest | Where-Object { @($p.folders) -ccontains $_.Name })
            $idle = (-not $alive.Count -and -not $inside.Count -and -not $named.Count)
            if ($idle -or (Get-Date) -ge $until) { break }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $until)
        if (-not (Authorized)) { throw 'Root or token refused' }
        # 2. Both records are checked before anything of the end happens.
        $journal = Get-Record $mine[2]
        $complete = Get-Record $mine[3]
        $last = ($done -and (Only $park @($own)) -and (Only $p.root $mine))
        if ($last) {
            # 3. Nothing but the kept files and the two records is left. The
            #    journal goes first. Setup's completion record goes only if
            #    nothing appeared meanwhile: with it, setup changes nothing here.
            Remove-Item -LiteralPath $journal.FullName
            $last = (Only $p.root @($p.park, $own, $mine[3]))
            if ($last -and $null -ne $complete) { Remove-Item -LiteralPath $complete.FullName }
        }
        # 4. No file is deleted from here. The flat folder goes while the file
        #    in the installation folder is still open.
        $kept[1].Close()
        $folder = Get-Item -LiteralPath $park -Force -ErrorAction SilentlyContinue
        if ($null -ne $folder) { Clear-Folder $folder }
        # 5. The installation folder, if the folder above holds something else:
        #    that folder then cannot be emptied and turned into a link.
        if ($last -and @(Get-ChildItem -LiteralPath $p.parent -Force).Count -gt 1) {
            $kept[0].Close()
            $folder = Get-Item -LiteralPath $p.root -Force -ErrorAction SilentlyContinue
            if ($null -ne $folder) { Clear-Folder $folder }
            $removed = -not (Test-Path -LiteralPath $p.root)
        }
    } | Out-Null
} catch { $reason = $_.Exception.Message }
finally {
    # Let both kept files go; Windows deletes each one then.
    foreach ($file in $kept) { $file.Close() }
    if ($p.report) {
        @{ passes = $passes; left = @($script:left); leftCount = $script:leftCount
           elapsed = [double]('{0:F1}' -f ((Get-Date) - $started).TotalSeconds)
           folderRemoved = $removed; reason = $reason } |
            ConvertTo-Json -Compress -Depth 4 |
            Set-Content -LiteralPath $p.report -Encoding UTF8
    }
}
"""


def finish_after_exit(root, token, *, park, keep, folders, wait_for=(), seconds=60, report=None,
                      choice="delete", ready=30):
    """Start the token-guarded last step with two kept files, outside the installation's Job.

    `park` names a folder directly in `root`. `keep` are the two open handles
    that `Folder.aside` made: a file `<token>.hold` in `root` and one in
    `park`. The step inherits both when it is created, so there is no moment
    in which nobody holds them. This call returns only when the step has said
    that it runs; the caller then closes its own two handles. Until the step
    has deleted its last file, no program can empty, rename or replace the
    flat folder, the installation folder or a folder above, also when the
    caller has ended.

    The step deletes the parked files directly in `park` (never with -Force),
    and removes the `folders` only when they are empty. When nothing but the
    kept files and the two records is left, it deletes the journal and then
    setup's completion record. Then it removes the flat folder and `root`.
    Folders go by RemoveDirectory: a link goes as a link, a folder with
    entries refuses. The step creates no file and opens no existing file.

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
    parent = ntpath.dirname(root)
    if not parent or parent == root:
        raise ValueError("Root must be a folder below a drive's top folder.")
    names = [park, *folders]
    for name in names:
        if (not isinstance(name, str) or not name or name in {".", ".."}
                or any(mark in name for mark in "\\/:\0")
                or name.casefold() in {"unified-uninstall.json", "unified-setup-complete.json"}):
            raise ValueError("Not a single folder name.")
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
    keep = list(keep)
    if len(keep) != 2 or keep[0] == keep[1] or any(type(handle) is not int or handle <= 0 for handle in keep):
        raise ValueError("Two different open file handles are required.")
    try:
        program = _powershell()
    except WindowsServiceError as error:
        raise OSError("Cannot locate Windows PowerShell.") from error
    outside = str(PureWindowsPath(program).parents[2])  # The Windows folder.
    payload = json.dumps({
        "root": root, "parent": parent, "token": token, "choice": choice, "park": park,
        "keep": keep, "folders": names[1:], "wait_for": wait_for, "seconds": seconds,
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
    # The step gets exactly these two handles (and its pipes) from this
    # process, at its creation. Its current folder is outside the installation.
    for handle in keep:
        os.set_handle_inheritable(handle, True)
    options = dict(cwd=outside, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL,
                   startupinfo=subprocess.STARTUPINFO(lpAttributeList={"handle_list": list(keep)}))
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        try:
            process = subprocess.Popen(arguments, creationflags=flags | 0x01000000, **options)
        except OSError:
            process = subprocess.Popen(arguments, creationflags=flags, **options)
    finally:
        for handle in keep:  # No later child of this process gets them.
            os.set_handle_inheritable(handle, False)
    try:
        process.stdin.write(payload)
    finally:
        process.stdin.close()
    # The step writes one line when it has taken over the two kept files and
    # its checks passed. Until then the caller must not let its handles go.
    answer = []
    reader = threading.Thread(target=lambda: answer.append(process.stdout.readline()), daemon=True)
    reader.start()
    reader.join(ready)
    if not answer or answer[0].strip() != b"ready":
        raise OSError("The last step did not take over the kept files.")
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
        # DELETE | SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY;
        # shares read and write, never delete. FILE_CREATE: an existing entry
        # of that name refuses. A folder; synchronous; never through a link.
        # DELETE lets the creator take back its own empty folder.
        result = self.create(C.byref(handle), 0x110081, C.byref(attributes), C.byref(status),
                             None, 0, 3, 2, 0x200021, None, 0)
        if result < 0:
            raise C.WinError(self.dos_error(result))
        return handle.value

    def keep(self, parent, name):
        """Create one new, empty file in an open folder and return it open. Nobody else can open it.

        Windows deletes the file when its last handle closes. An existing
        entry of that name refuses, also a link: nothing is created elsewhere.
        """
        if not name or name in {".", ".."} or any(mark in name for mark in "\\/:"):
            raise OSError(22, "not a single entry name")
        text = C.create_unicode_buffer(name)
        size = len(name.encode("utf-16-le", "surrogatepass"))
        unicode = _Unicode(size, size + 2, C.cast(text, C.c_wchar_p))
        attributes = _Attributes(C.sizeof(_Attributes), parent, C.pointer(unicode), 0, None, None)
        handle, status = HANDLE(), _Status()
        # DELETE | SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_WRITE_DATA; an
        # ordinary file; no sharing at all; FILE_CREATE: an existing entry
        # refuses. A file; synchronous; FILE_DELETE_ON_CLOSE; and
        # FILE_OPEN_REPARSE_POINT: a link of that name is an existing entry.
        # Without it Windows follows a dangling file link and creates the file
        # at the link's target.
        result = self.create(C.byref(handle), 0x110082, C.byref(attributes), C.byref(status),
                             None, 0x80, 0, 2, 0x201060, None, 0)
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
    def aside(self, name, hold):
        """Create one new folder directly in this folder, and two kept files; hold all three open.

        The kept files are named `<hold>.hold`: one in this folder, one in the
        new folder. Nobody else can open, delete or move them, and Windows
        deletes each when its last handle closes. While a folder holds such a
        file it is never empty, Windows turns only an empty folder into a
        link, and Windows refuses to rename it. An entry with one of the three
        names that exists already refuses, and nothing is created elsewhere.

        Yields the function that moves an open file into the new folder under
        the next free number. Its attribute `keep` is the two open handles,
        the file in this folder first, for `finish_after_exit`.
        """
        handle, kept = self._api.make(self._handle, name), []
        try:
            for parent in (self._handle, handle):
                kept.append(self._api.keep(parent, hold + ".hold"))
            # The new folder was empty for a moment. It must still be a folder.
            if self._api.kind(handle) != "folder" or any(self._api.kind(file) != "file" for file in kept):
                raise OSError(22, "the held folder changed into a link")
            count = [0]

            def park(file):
                for _ in range(10):  # A number is taken only if a program put an entry there.
                    count[0] += 1
                    try:
                        return self._api.park(file, handle, str(count[0]))
                    except FileExistsError:
                        continue
                raise OSError(17, "no free name in the held folder")
            park.keep = tuple(kept)
            yield park
        except BaseException:
            for file in reversed(kept):
                self._api.close(file)
            kept.clear()
            try:
                self._api.delete(handle)  # Only this new entry; a nonempty folder refuses.
            except OSError:
                pass
            raise
        finally:
            for file in reversed(kept):
                self._api.close(file)
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
