"""Opt-in, closed-vocabulary observation for the pinned-provider fixture only.

No output text, argv, paths, URL, timestamps or persistent path identities enter
records. A Git record witnesses pre-exec intent, NOT completion or event order.
Package context and cwd class are independent; neither identifies a cache owner.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import sys

SLOTS = 128
RECORD_BYTES = 2048
SCAN_BYTES = 128 * 1024
VERBS = frozenset({"fetch", "clone", "ls-remote", "rev-parse", "init", "reset",
                   "submodule", "config", "rev-list", "unknown", "ambiguous"})
REFS = {
    "main": "main", "main^0": "main-peel", "HEAD": "head",
    "origin/main": "origin-main", "origin/main^0": "origin-main-peel",
    "refs/heads/main": "heads-main",
    "refs/remotes/origin/main": "remote-main",
    "refs/remotes/origin/tags/main^0": "remote-tag-main-peel",
    "+refs/heads/main:refs/remotes/origin/main": "branch-refspec",
    "+refs/tags/main:refs/remotes/origin/tags/main": "tag-refspec",
}
REF_VALUES = frozenset(REFS.values()) | {"commit", "commit-peel", "absent", "unknown", "ambiguous"}
ROLES = frozenset({"provider", "core", "foundation", "fixture-provider", "unknown", "ambiguous"})
CWDS = frozenset({"fixture-root", "provider-mirror", "core", "foundation",
                  "fixture-provider", "uv-database", "uv-checkout", "uv-git", "other", "unknown"})
SCHEMA = {
    "git": {"verb": VERBS, "ref": REF_VALUES, "source": ROLES, "cwd": CWDS, "overflow": bool},
    "stderr": {"headerVerb": VERBS, "headerRef": REF_VALUES, "contextRef": REF_VALUES,
               "nestedRef": REF_VALUES, "packageContext": ROLES,
               "headerSeen": bool, "nestedMarkerSeen": bool, "overflow": bool,
               "captureTruncated": bool, "unsupportedControl": bool},
    "prepare": {"entered": {0, 1}, "refusedSecond": {0, 1},
                "outcome": {"raised", "interrupted", "returned", "refused"}},
    "overflow": {"overflow": {True}},
    "lifecycle": {"restored": bool},
}


def _choice(values, empty="unknown"):
    values = set(values)
    return next(iter(values)) if len(values) == 1 else "ambiguous" if values else empty


def _ref(value):
    if value in REFS:
        return REFS[value]
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        return "commit"
    if value.endswith("^0") and _ref(value[:-2]) == "commit":
        return "commit-peel"
    return "unknown"


def _cwd(cwd, root):
    # Lexical classification only: no resolve(), stat, repository query or cache marker.
    try:
        relative = Path(os.path.normpath(cwd)).relative_to(Path(os.path.normpath(root)))
    except (ValueError, TypeError):
        return "other"
    parts = relative.parts
    if not parts:
        return "fixture-root"
    known = {"provider-module.git": "provider-mirror", "core": "core",
             "foundation": "foundation", "provider": "fixture-provider"}
    if parts[0] in known:
        return known[parts[0]]
    if len(parts) >= 2 and parts[:2] in {("uv-cache", "git-v0"), ("uv-cache", "git-v1")}:
        return {"db": "uv-database", "checkouts": "uv-checkout"}.get(
            parts[2] if len(parts) > 2 else "", "uv-git")
    return "other"


def git_record(args, cwd, root, sources):
    result = {"verb": "unknown", "ref": "unknown", "source": "unknown",
              "cwd": "unknown", "overflow": False}
    if len(args) > 64 or sum(map(len, args)) > 8192 or any(len(a) > 2048 for a in args):
        result["overflow"] = True
        return result
    args = list(args)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-c", "-C"} and index + 1 < len(args):
            if arg == "-C":
                cwd = os.path.join(cwd, args[index + 1])
            index += 2
        elif arg in {"--no-pager", "--no-optional-locks"}:
            index += 1
        else:
            break
    result["cwd"] = _cwd(cwd, root)
    if index == len(args) or args[index] not in VERBS - {"unknown", "ambiguous"}:
        return result
    result["verb"] = args[index]
    operands = args[index + 1:]
    result["source"] = _choice(sources[a] for a in operands if a in sources and sources[a] in ROLES)
    if result["verb"] == "rev-parse":
        while operands and operands[0] in {"--verify", "--short", "--quiet", "-q"}:
            operands = operands[1:]
        if any(a.startswith("-") for a in operands):
            return result  # Includes -- and unsupported option/value shapes.
        if len(operands) > 1:
            result["ref"] = "ambiguous"
        else:
            result["ref"] = _ref(operands[0]) if operands else "absent"
    elif result["verb"] == "fetch":
        options = {"--force", "-f", "--tags", "--no-tags", "--update-head-ok",
                   "--recurse-submodules=no", "--depth=1"}
        operands = [a for a in operands if a not in options and a not in sources]
        refs = [_ref(a) for a in operands]
        result["ref"] = _choice(refs, "absent")
    return result


def stderr_record(capture):
    data = capture.data
    result = {"headerVerb": "unknown", "headerRef": "unknown", "contextRef": "unknown",
              "nestedRef": "unknown", "packageContext": "unknown", "headerSeen": False,
              "nestedMarkerSeen": False, "overflow": len(data) > SCAN_BYTES,
              "captureTruncated": capture.truncated, "unsupportedControl": False}
    headers, contexts, nested, packages = [], [], [], []
    names = {"amplifier-module-provider-openai": "provider", "amplifier-core": "core",
             "amplifier-foundation": "foundation"}
    lines = bytes(data[:SCAN_BYTES]).decode("utf-8", errors="replace").split("\n")
    result["overflow"] |= len(lines) > 256
    in_stderr = False
    for line in lines[:256]:
        if len(line) > 2048:
            result["overflow"] = True
            continue
        if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in line):
            result["unsupportedControl"] = True
            continue
        # Only known display decoration is removable, not arbitrary prefixes.
        line = line.lstrip(" \u2502\u251c\u2514\u2570\u2500\u25b6\u00d7")
        if line.rstrip() == "--- stderr":
            in_stderr = result["nestedMarkerSeen"] = True
            continue
        # Only the fixed cargo-util header shape is a command assertion. Text in
        # nested stderr is never promoted to an outer failed-command header.
        header = re.fullmatch(
            r"process didn't exit successfully: `([^`]+)`(?: \(exit status: [0-9]+\))?\s*", line)
        if header and not in_stderr:
            result["headerSeen"] = True
            try:
                argv = shlex.split(header[1])
            except ValueError:
                argv = []
            if argv and Path(argv[0]).name in {"git", "git.exe"}:
                headers.append(git_record(argv[1:], "/", "/", {}))
            else:
                headers.append({"verb": "unknown", "ref": "unknown"})
        if in_stderr:
            match = re.search(r"(?:ambiguous argument|unknown revision) ['`]([^'`]+)['`]", line)
            if match:
                nested.append(_ref(match[1]))
        else:
            context = re.fullmatch(
                r"failed to (?:find|fetch) (?:branch or tag|branch|tag|revision|commit) [`']([^`']+)[`']\s*",
                line, re.I)
            if context:
                contexts.append(_ref(context[1]))
            package = re.fullmatch(r"failed to (?:download|fetch|build) `([^`]+)`\s*", line, re.I)
            if package:
                # A package field, not a name occurring anywhere inside a URL/path.
                match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:==[A-Za-z0-9_.+!-]+| @ \S+)?", package[1])
                packages.append(names.get(match[1], "unknown") if match else "unknown")
    result.update(headerVerb=_choice(row["verb"] for row in headers),
                  headerRef=_choice(row["ref"] for row in headers),
                  contextRef=_choice(contexts), nestedRef=_choice(nested),
                  packageContext=_choice(packages))
    # A prefix is not complete command evidence. Preserve that the shape was seen,
    # but do not certify its reference/package classification.
    if result["overflow"] or result["captureTruncated"] or result["unsupportedControl"]:
        for field in ("headerVerb", "headerRef", "contextRef", "nestedRef", "packageContext"):
            result[field] = "unknown"
        if result["unsupportedControl"]:
            result["headerSeen"] = result["nestedMarkerSeen"] = False
    return result


def _payload(kind, record):
    schema = SCHEMA[kind]
    if type(record) is not dict or record.keys() != schema.keys():
        raise ValueError("Invalid witness record")
    for key, allowed in schema.items():
        value = record[key]
        if allowed is bool:
            valid = type(value) is bool
        else:
            valid = any(type(value) is type(option) and value == option for option in allowed)
        if not valid:
            raise ValueError("Invalid witness value")
    raw = json.dumps({"kind": kind, **record}, sort_keys=True).encode() + b"\n"
    if len(raw) > RECORD_BYTES:
        raise ValueError("Witness record too large")
    return raw


def emit(folder, kind, record):
    """At most SLOTS immutable records per kind; no lock, wait or unbounded retry."""
    raw = _payload(kind, record)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        for slot in range(SLOTS):
            try:
                fd = os.open(f"{kind}-{slot:03}.json", flags, 0o600, dir_fd=directory)
            except FileExistsError:
                continue
            try:
                if os.write(fd, raw) != len(raw):
                    raise OSError("Incomplete witness write")
            finally:
                os.close(fd)
            return
        try:
            fd = os.open("overflow.json", flags, 0o600, dir_fd=directory)
        except FileExistsError:
            return
        try:
            os.write(fd, _payload("overflow", {"overflow": True}))
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def observe_git(folder, args, cwd, root, sources):
    try:
        emit(folder, "git", git_record(args, cwd, root, sources))
    except Exception:
        pass  # No text fallback; missing/incomplete evidence never certifies success.


def excerpt_wrapper(original, folder):
    def observed(capture, environment):
        try:
            emit(folder, "stderr", stderr_record(capture))
        except Exception:
            pass
        return original(capture, environment)
    return observed


class InitialPrepareReturned(RuntimeError):
    """Intentional diagnostic stop, not a passing native test."""


class OnePreparation:
    def __init__(self, original, folder):
        self.original, self.folder = original, folder
        self.entered = self.refused = 0

    async def __call__(self, *args, **kwargs):
        if self.entered:
            self.refused = 1
            self._record("refused")
            raise InitialPrepareReturned("Second diagnostic preparation refused")
        self.entered = 1
        try:
            await self.original(*args, **kwargs)
        except Exception:
            self._record("raised")
            raise
        except BaseException:
            self._record("interrupted")
            raise
        self._record("returned")
        raise InitialPrepareReturned("Initial preparation returned; diagnostic stops before update tail")

    def _record(self, outcome):
        try:
            emit(self.folder, "prepare", {"entered": self.entered,
                 "refusedSecond": self.refused, "outcome": outcome})
        except Exception:
            pass


@contextmanager
def installed_witness(work, shim, sources, command_details):
    """Install only around the one initial preparation; restore on every exit."""
    folder = work / "command-witness"
    folder.mkdir(mode=0o700)
    before = shim.read_text()
    original = command_details.local_excerpt
    anchor = "os.execv('/usr/bin/git', ['/usr/bin/git', *flags, *args])"
    if before.count(anchor) != 1:
        raise ValueError("Unexpected Git fixture forwarding boundary")
    injection = (
        "try:\n"
        f"    sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "    from pinned_provider_command_witness import observe_git\n"
        f"    observe_git({str(folder)!r}, args, os.getcwd(), {str(work)!r}, {sources!r})\n"
        "except Exception:\n    pass\n"
    )
    try:
        shim.write_text(before.replace(anchor, injection + anchor))
        command_details.local_excerpt = excerpt_wrapper(original, folder)
        yield folder
    finally:
        command_details.local_excerpt = original
        active_error = sys.exc_info()[0] is not None
        restored = False
        try:
            shim.write_text(before)
            restored = True
        except Exception:
            if not active_error:
                raise
        finally:
            try:
                emit(folder, "lifecycle", {"restored": restored})
            except Exception:
                pass
