"""Unpack the pinned complete Git for Windows into a private setup directory.

The agent's shell tool needs bash.exe and core utilities. MinGit has neither,
so consumer setup acquires the complete official distribution. Its only
complete non-interactive archive is tar.bz2, which the in-box .NET Framework
cannot read. Setup downloads and hashes the archive, then runs this file with
its private Python (-I -S), before the application exists. Standard library
only: no network, PATH, registry, trust or policy change, and no overwrite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

# Official release metadata inspected 2026-11-14. SHA-256 values are the
# publisher's GitHub asset digests, not claims of signature verification.
PIN = {
    "version": "2.56.0.windows.4",
    "url": "https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/Git-2.56.0.4-64-bit.tar.bz2",
    "sha256": "0dae059764ba7c447c9604eb6cb0f4afaf2d90b003ecf1c8cf2dbe5a13e4442c",
    # The archive keeps its components' notices but not Git's own license.
    # Retain that from the exact tagged source commit.
    "license": "https://raw.githubusercontent.com/git-for-windows/git/581291682ad5a238c08c93d1f5bd4518382a3230/COPYING",
    "licenseSha256": "5b2198d1645f767585e8a88ac0499b04472164c0d2da22e75ecf97ef443ab32e",
}
ENTRIES = 20000
BYTES = 1 << 30
# The archive's only symbolic links. The MSYS2 runtime resolves them itself;
# Windows never follows them, and no other link target is accepted.
LINKS = {"dev/fd": "/proc/self/fd", "dev/stdin": "/proc/self/fd/0",
         "dev/stdout": "/proc/self/fd/1", "dev/stderr": "/proc/self/fd/2",
         "etc/mtab": "/proc/mounts"}
REQUIRED = ("cmd/git.exe", "bin/bash.exe", "bin/sh.exe", "usr/bin/bash.exe")
_DEVICE = re.compile(r"(con|prn|aux|nul|conin\$|conout\$|com[1-9]|lpt[1-9])(\.|$)", re.I)
_UNSAFE = re.compile(r'[<>:"|?*\\\x00-\x1f]')


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(1 << 20):
            value.update(block)
    return value.hexdigest()


def _name(value):
    parts = value.split("/")
    # Short enough that the private folder plus any entry stays within
    # Windows' ordinary 260-character path limit. The pinned maximum is 85.
    if not value or len(value) > 120 or any(
            not part or part in {".", ".."} or part.endswith((".", " "))
            or _UNSAFE.search(part) or _DEVICE.match(part) for part in parts):
        raise ValueError("The Git archive has an unsafe entry name.")
    return parts


def _link(path, target):
    # The MSYS2 runtime's own link format: an ordinary system file, never an
    # NTFS reparse point that Windows or another program would follow.
    with open(path, "xb") as stream:
        stream.write(b"!<symlink>\xff\xfe" + target.encode("utf-16-le") + b"\0\0")
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.SetFileAttributesW.restype = ctypes.c_int
        if not kernel.SetFileAttributesW(str(path), 0x4):  # FILE_ATTRIBUTE_SYSTEM
            raise ctypes.WinError(ctypes.get_last_error())


def extract(archive, destination):
    """Create every entry exclusively below an existing private directory."""
    destination = Path(destination)
    if (not destination.is_dir() or destination.is_symlink() or destination.is_junction()
            or any(destination.iterdir())):
        raise ValueError("The private Git directory must already exist and be empty.")
    files, seen, total = set(), set(), 0
    with tarfile.open(archive, "r:bz2") as source:
        for count, member in enumerate(source, 1):
            if count > ENTRIES:
                raise ValueError("The Git archive has too many entries.")
            parts = _name(member.name)
            key = "/".join(parts).casefold()
            if key in seen:
                raise ValueError("The Git archive repeats an entry.")
            seen.add(key)
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isreg():
                total += member.size
                if total > BYTES:
                    raise ValueError("The Git archive is larger than its budget.")
                with source.extractfile(member) as data, open(target, "xb") as output:
                    shutil.copyfileobj(data, output, 1 << 20)
                    if output.tell() != member.size:
                        raise ValueError("The Git archive has a truncated entry.")
            elif member.islnk():
                # A separate copy, not an NTFS hard link: setup's private
                # storage refuses multiply linked executables.
                origin = "/".join(_name(member.linkname))
                if origin.casefold() not in files:
                    raise ValueError("The Git archive links outside its own files.")
                original = destination.joinpath(*origin.split("/"))
                total += original.stat().st_size
                if total > BYTES:
                    raise ValueError("The Git archive is larger than its budget.")
                with open(original, "rb") as data, open(target, "xb") as output:
                    shutil.copyfileobj(data, output, 1 << 20)
            elif member.issym():
                if LINKS.get("/".join(parts)) != member.linkname:
                    raise ValueError("The Git archive has an unexpected link.")
                _link(target, member.linkname)
                continue
            else:
                raise ValueError("The Git archive has a special file.")
            files.add(key)
    if any(name not in files for name in REQUIRED):
        raise ValueError("The Git archive lacks Git or its shell.")
    return {"entries": len(seen), "bytes": total}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    if digest(args.archive) != PIN["sha256"]:
        raise SystemExit("The Git archive is not the pinned official release.")
    try:
        result = extract(args.archive, args.destination)
    except (OSError, ValueError, tarfile.TarError) as error:
        raise SystemExit(f"Private Git was not installed: {error}") from None
    print(json.dumps({"ok": True, "version": PIN["version"], **result}))


if __name__ == "__main__":
    main()
