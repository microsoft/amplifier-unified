"""Verify one operator-selected change between retained generation-owned skill caches."""
import copy
import json
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path


PACKAGE = "amplifier-module-tool-skills"
REPOSITORY = "https://github.com/microsoft/amplifier-bundle-skills"
SUBDIRECTORY = "modules/tool-skills"


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def valid_choice(choice):
    return (isinstance(choice, dict) and set(choice) == {"cacheMoveId", "fromGeneration", "toGeneration"}
            and _hex(choice["cacheMoveId"], 64) and _hex(choice["fromGeneration"], 32)
            and _hex(choice["toGeneration"], 32) and choice["fromGeneration"] != choice["toGeneration"])


def _open(stack, name, parent, *, directory=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=parent)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or (not directory and (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        raise ValueError("Unrecognized owner or file")
    def verify_entry():
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (not os.path.samestat(info, current)
                or current.st_mode != info.st_mode or current.st_uid != info.st_uid
                or not directory and (current.st_ctime_ns != info.st_ctime_ns or current.st_size != info.st_size)):
            raise ValueError("Changed skill-cache proof")
    stack.callback(verify_entry)
    return fd


def _receipt(stack, fd, name):
    child = _open(stack, name, fd)
    info = os.fstat(child)
    limit = 2_000_000
    if not 0 < info.st_size <= limit:
        raise ValueError("Unrecognized receipt")
    with os.fdopen(os.dup(child), "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) != info.st_size or os.fstat(child).st_mtime_ns != info.st_mtime_ns:
        raise ValueError("Changed receipt")
    return json.loads(data)


def _generation(stack, releases, generation):
    from .context_continuity import digest
    fd = _open(stack, generation, releases, directory=True)
    if os.fstat(fd).st_mode & 0o022:
        raise ValueError("Unprotected generation")
    qualified = _receipt(stack, fd, "validated.json")
    if (type(qualified["generationSchema"]) is not int or qualified["generationSchema"] != 1
            or type(qualified["workerProtocol"]) is not int or qualified["workerProtocol"] != 1):
        raise ValueError("Unqualified generation")
    source = _receipt(stack, fd, "runtime-sources.json")[PACKAGE]
    package, = [row for row in _receipt(stack, fd, "runtime-installed.json") if row["name"] == PACKAGE]
    installed = package["cacheSource"]
    if (source["url"] != REPOSITORY or source["subdirectory"] != SUBDIRECTORY or not _hex(source["revision"], 40)
            or installed["dirty"] is not False or not _hex(installed["trackedContentSha256"], 64)
            or not isinstance(package["version"], str) or not package["version"]
            or any(source[key] != installed[key] for key in ("revision", "url", "subdirectory"))):
        raise ValueError("Unverified skill module")
    directory = fd
    for name in ("foundation", "cache", "skills"):
        directory = _open(stack, name, directory, directory=True)
        if os.fstat(directory).st_mode & 0o022:
            raise ValueError("Unprotected cache")
    # Populated clones, links, external paths and ambiguous cache contents need
    # their own comparison. The empty lock files here contain no skill source.
    names = []
    before = os.fstat(directory)
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(names) >= 64 or not re.fullmatch(r"\.[A-Za-z0-9._-]{1,180}\.lock", entry.name):
                raise ValueError("Unsupported skill cache")
            lock = _open(stack, entry.name, directory)
            info = os.fstat(lock)
            if info.st_size or info.st_mode & 0o022:
                raise ValueError("Unexpected lock contents")
            names.append(entry.name)
    def verify_contents():
        if before.st_mtime_ns != os.fstat(directory).st_mtime_ns:
            raise ValueError("Changed skill cache")
    stack.callback(verify_contents)
    return (source["revision"], installed["trackedContentSha256"], package["version"],
            digest(sorted(names)), len(names), source["ref"])


def verified_move(home, registry_home, injected_slots, contract, old_sha, from_generation):
    """Return only proof of the exact previous raw digest and equivalent qualified cache."""
    from .context_continuity import digest
    if os.name != "posix" or not _hex(from_generation, 32) or not _hex(old_sha, 64) or not injected_slots:
        return None
    home, registry = Path(home), Path(registry_home)
    to_generation = registry.parent.name
    releases = home / "updates" / "releases"
    if (not _hex(to_generation, 32) or from_generation == to_generation
            or registry != releases / to_generation / "foundation" or not home.is_absolute()):
        return None
    source_path = str(releases / from_generation / "foundation" / "cache" / "skills")
    current_path = str(registry / "cache" / "skills")
    previous = copy.deepcopy(contract)
    try:
        sources = set()
        for slot in injected_slots:
            slot_row = previous["plan"]
            for key in slot:
                slot_row = slot_row[key]
            if slot_row["module"] != "tool-skills" or slot_row["config"]["cache_dir"] != current_path:
                return None
            sources.add(slot_row["source"])
            slot_row["config"]["cache_dir"] = source_path
        if digest(previous) != old_sha:
            return None
        with ExitStack() as stack:
            if home.resolve(strict=True) != home:
                return None
            root = _open(stack, home, None, directory=True)
            updates = _open(stack, "updates", root, directory=True)
            if os.fstat(updates).st_mode & 0o022:
                return None
            directory = _open(stack, "releases", updates, directory=True)
            old = _generation(stack, directory, from_generation)
            if old != _generation(stack, directory, to_generation):
                return None
            revision, content, version, cache, count, ref = old
            if sources != {f"git+{REPOSITORY}@{ref}#subdirectory={SUBDIRECTORY}"}:
                return None
        proof = {"fromGeneration": from_generation, "toGeneration": to_generation,
                 "oldContractSha256": old_sha, "newContractSha256": digest(contract),
                 "slotCount": len(injected_slots), "lockFiles": count, "cacheSha256": cache,
                 "packageRevision": revision, "packageContentSha256": content, "packageVersion": version}
        return {**proof, "cacheMoveId": digest(proof)}
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None
