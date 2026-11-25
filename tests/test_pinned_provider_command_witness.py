"""Portable controls: no uv, Git, provider, network or application preparation."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from fixtures import pinned_provider_command_witness as witness
from test_partial_update_native import _transport


SECRET = "UNRELATED_PRIVATE_VALUE"
ROOT = "/fixture"
SOURCES = {"https://example.invalid/provider": "provider", "file:///fixture/core": "core"}


def capture(text, truncated=False):
    return SimpleNamespace(data=bytearray(text.encode()), truncated=truncated)


def records(folder):
    return [json.loads(path.read_bytes()) for path in sorted(folder.glob("*.json"))]


@pytest.mark.parametrize("ref,expected", [
    ("main", "main"), ("origin/main^0", "origin-main-peel"),
    ("refs/remotes/origin/tags/main^0", "remote-tag-main-peel"),
    ("+refs/heads/main:refs/remotes/origin/main", "branch-refspec"),
    ("a" * 40, "commit"), ("a" * 40 + "^0", "commit-peel"),
    (SECRET, "unknown"),
])
def test_exact_ref_vocabulary_never_outputs_input(ref, expected):
    row = witness.git_record(["rev-parse", ref], ROOT + "/uv-cache/git-v1/db/private", ROOT, SOURCES)
    assert row == {"verb": "rev-parse", "ref": expected, "source": "unknown",
                   "cwd": "uv-database", "overflow": False}
    assert SECRET not in json.dumps(row) and "private" not in json.dumps(row)


def test_source_exact_match_and_cwd_do_not_manufacture_cache_identity():
    row = witness.git_record(["fetch", next(iter(SOURCES)),
                              "+refs/heads/main:refs/remotes/origin/main"],
                             ROOT + "/uv-cache/git-v1/db/opaque", ROOT, SOURCES)
    assert row["source"] == "provider" and row["ref"] == "branch-refspec"
    assert row["cwd"] == "uv-database"
    row = witness.git_record(["fetch", "https://example.invalid/provider.git", "main"], ROOT, ROOT, SOURCES)
    assert row["source"] == "unknown"
    row = witness.git_record(["-C", "core", "rev-parse", "main"], ROOT, ROOT, SOURCES)
    assert row["cwd"] == "core" and row["source"] == "unknown"
    row = witness.git_record(["fetch", *SOURCES, "main"], ROOT, ROOT, SOURCES)
    assert row["source"] == "ambiguous"
    assert witness.git_record(["--git-dir", SECRET, "rev-parse", "main"], ROOT, ROOT, SOURCES)["verb"] == "unknown"
    assert witness.git_record(["rev-parse", "main", "origin/main^0"], ROOT, ROOT, {})["ref"] == "ambiguous"


@pytest.mark.parametrize("args,expected", [
    (["rev-parse", "main", SECRET], "ambiguous"),
    (["rev-parse", "main", "--", SECRET], "unknown"),
    (["rev-parse", "--prefix", SECRET, "main"], "unknown"),
    (["rev-parse", "--short=8", "main"], "unknown"),
    (["rev-parse", "--verify", "origin/main^0"], "origin-main-peel"),
    (["fetch", next(iter(SOURCES)), "main", SECRET], "ambiguous"),
])
def test_unrecognized_operand_is_not_dropped(args, expected):
    assert witness.git_record(args, ROOT, ROOT, SOURCES)["ref"] == expected


@pytest.mark.parametrize("args", [["rev-parse", "x" * 2049], ["main"] * 65])
def test_git_input_bounds_are_explicit(args):
    row = witness.git_record(args, ROOT, ROOT, SOURCES)
    assert row["overflow"] and row["verb"] == row["ref"] == row["cwd"] == "unknown"


def test_header_context_and_nested_error_are_separate_closed_facts():
    value = capture(
        "Failed to download `amplifier-module-provider-openai @ https://user:secret@example.invalid`\n"
        "failed to find branch or tag `main`\n"
        "process didn't exit successfully: `/private/bin/git rev-parse 'origin/main^0'` (exit status: 128)\n"
        "--- stderr\nfatal: ambiguous argument 'origin/main^0': unknown revision\n" + SECRET)
    before = bytes(value.data)
    row = witness.stderr_record(value)
    assert row == {"headerVerb": "rev-parse", "headerRef": "origin-main-peel", "contextRef": "main",
                   "nestedRef": "origin-main-peel", "packageContext": "provider", "headerSeen": True,
                   "nestedMarkerSeen": True, "overflow": False, "captureTruncated": False,
                   "unsupportedControl": False}
    assert bytes(value.data) == before
    assert all(marker not in json.dumps(row) for marker in (SECRET, "private", "example.invalid", "user:secret"))
    assert witness.stderr_record(capture("--- stderr\nprocess didn't exit successfully: `git rev-parse main`"))["headerVerb"] == "unknown"


def test_stderr_unknown_ambiguous_and_invalid_shapes():
    row = witness.stderr_record(capture(
        "Failed to download `amplifier-core`\nFailed to download `amplifier-foundation`\n"
        "process didn't exit successfully: `git rev-parse main`\n"
        "process didn't exit successfully: `git rev-parse origin/main^0`\n"))
    assert row["packageContext"] == "ambiguous" and row["headerRef"] == "ambiguous"
    assert row["headerVerb"] == "rev-parse"
    row = witness.stderr_record(capture("Failed to download `amplifier-core-" + SECRET + "`\n"
        "process didn't exit successfully: `not-git rev-parse main`"))
    assert row["packageContext"] == row["headerVerb"] == "unknown"
    row = witness.stderr_record(capture("process didn't exit successfully: `git 'unterminated`"))
    assert row["headerSeen"] and row["headerRef"] == "unknown"


@pytest.mark.parametrize("text", [
    "Failed to fetch into: /private/amplifier-core",
    "Failed to download `https://example.invalid/amplifier-core`",
    "Failed to download `/private/amplifier-core`",
    "Unrelated message: Failed to download `amplifier-core`",
    "Unrelated message: process didn't exit successfully: `git rev-parse main`",
    "\x1b[31mFailed to download `amplifier-core`",
    "\x00process didn't exit successfully: `git rev-parse main`",
    "Failed to download `amplifier-core` and `amplifier-foundation`",
])
def test_stderr_embedded_names_headers_and_controls_are_not_attribution(text):
    row = witness.stderr_record(capture(text))
    assert row["packageContext"] == row["headerVerb"] == row["headerRef"] == "unknown"


@pytest.mark.parametrize("separator", ["--- stderr\r", "\x1b[31m--- stderr", "--- stderr\x00",
                                       "\x85--- stderr", "\t--- stderr"])
def test_unsupported_control_cannot_erase_nested_boundary(separator):
    row = witness.stderr_record(capture(
        "Failed to download `amplifier-core`\n" + separator + "\n"
        "process didn't exit successfully: `git rev-parse main`\n"))
    assert row["unsupportedControl"] and not row["overflow"] and not row["captureTruncated"]
    assert not row["headerSeen"] and not row["nestedMarkerSeen"]
    assert all(row[field] == "unknown" for field in (
        "headerVerb", "headerRef", "contextRef", "nestedRef", "packageContext"))


@pytest.mark.parametrize("text,truncated", [("x" * 2049, False), ("\n" * 257, False),
    ("x" * (witness.SCAN_BYTES + 1), False), ("failed to find branch `main`", True)])
def test_partial_capture_cannot_certify_ref_or_package(text, truncated):
    row = witness.stderr_record(capture(text, truncated))
    assert row["overflow"] or row["captureTruncated"]
    assert all(row[field] == "unknown" for field in ("headerVerb", "headerRef", "contextRef", "nestedRef", "packageContext"))


def test_private_exclusive_slots_collisions_and_explicit_saturation(tmp_path, monkeypatch):
    monkeypatch.setattr(witness, "SLOTS", 4)
    folder = tmp_path / "witness"
    folder.mkdir(mode=0o700)
    row = witness.git_record(["rev-parse", "main"], ROOT, ROOT, {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: witness.emit(folder, "git", row), range(9)))
    values = records(folder)
    assert len(values) == 5 and sum(r["kind"] == "git" for r in values) == 4
    assert {"kind": "overflow", "overflow": True} in values
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in folder.iterdir())
    assert all(path.stat().st_size <= witness.RECORD_BYTES for path in folder.iterdir())
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    with pytest.raises(ValueError):
        witness.emit(folder, "git", {**row, "ref": SECRET})
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    with pytest.raises(ValueError):
        witness.emit(folder, "prepare", {"entered": True, "refusedSecond": 0, "outcome": "raised"})


def test_partial_slot_remains_invalid_not_rewritten(tmp_path, monkeypatch):
    original_write = os.write
    monkeypatch.setattr(witness.os, "write", lambda fd, raw: original_write(fd, raw[:7]))
    witness.observe_git(tmp_path, ["rev-parse", "main"], ROOT, ROOT, {})
    partial = (tmp_path / "git-000.json").read_bytes()
    with pytest.raises(json.JSONDecodeError):
        json.loads(partial)
    monkeypatch.setattr(witness.os, "write", original_write)
    witness.observe_git(tmp_path, ["rev-parse", "main"], ROOT, ROOT, {})
    assert (tmp_path / "git-000.json").read_bytes() == partial
    assert json.loads((tmp_path / "git-001.json").read_bytes())["ref"] == "main"


@pytest.mark.parametrize("fault", ["classify", "write", "none"])
def test_redactor_forwarding_is_exact_despite_observer_errors(tmp_path, monkeypatch, fault):
    value, environment, answer = capture(SECRET), {"SECRET": SECRET}, object()
    original = Mock(return_value=answer)
    if fault != "none":
        def broken(*args):
            raise OSError(SECRET)
        monkeypatch.setattr(witness, "stderr_record" if fault == "classify" else "emit", broken)
    assert witness.excerpt_wrapper(original, tmp_path)(value, environment) is answer
    original.assert_called_once_with(value, environment)
    error = ValueError(SECRET)
    original = Mock(side_effect=error)
    with pytest.raises(ValueError) as caught:
        witness.excerpt_wrapper(original, tmp_path)(value, environment)
    assert caught.value is error
    original.assert_called_once_with(value, environment)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["returned", "failure", "cancelled"])
async def test_one_prepare_preserves_original_failure_or_stops_before_tail(tmp_path, monkeypatch, result):
    error = asyncio.CancelledError() if result == "cancelled" else RuntimeError(SECRET)
    error.diagnostic_facts = {"exitCode": 1, "stderrBytes": 1030}
    original = AsyncMock(return_value={"ok": True}, side_effect=None if result == "returned" else error)
    observed = witness.OnePreparation(original, tmp_path)
    home, token = object(), object()
    with pytest.raises(witness.InitialPrepareReturned if result == "returned" else type(error)) as caught:
        await observed(home, token=token)
        pytest.fail("Update tail was reached")
    if result != "returned":
        assert caught.value is error and error.diagnostic_facts == {"exitCode": 1, "stderrBytes": 1030}
    original.assert_awaited_once_with(home, token=token)
    with pytest.raises(witness.InitialPrepareReturned):
        await observed(home, token=token)
    original.assert_awaited_once()
    assert records(tmp_path)[0]["outcome"] == {"returned": "returned", "failure": "raised", "cancelled": "interrupted"}[result]
    assert records(tmp_path)[1] == {"kind": "prepare", "entered": 1, "refusedSecond": 1, "outcome": "refused"}


def shim_file(tmp_path):
    transport = tmp_path / "transport.json"
    transport.write_text(json.dumps({next(iter(SOURCES)): "file:///local-mirror"}))
    _transport(tmp_path, transport, tmp_path / "no-failure.json", tmp_path / "app")
    return tmp_path / "git"


@pytest.mark.parametrize("observer_failure", [False, True])
@pytest.mark.parametrize("args", [
    ["rev-parse", "main"],
    ["fetch", "--force", next(iter(SOURCES)), "+refs/heads/main:refs/remotes/origin/main"],
    ["clone", next(iter(SOURCES)), "/synthetic/destination"],
])
def test_generated_shim_keeps_same_exec_arguments_and_exception(tmp_path, monkeypatch, observer_failure, args):
    shim = shim_file(tmp_path)
    before = shim.read_bytes()
    redactor = Mock()
    details = SimpleNamespace(local_excerpt=redactor)
    monkeypatch.setitem(sys.modules, "pinned_provider_command_witness", witness)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", [str(shim), *args])
    error = OSError("original exec failure")
    execute = Mock(side_effect=error)
    monkeypatch.setattr(os, "execv", execute)
    with pytest.raises(OSError) as baseline:
        exec(compile(before, "original-maintained-shim", "exec"), {})
    assert baseline.value is error
    expected = execute.call_args
    if args[0] in {"fetch", "clone"}:
        assert expected.args == ("/usr/bin/git", ["/usr/bin/git", "-c",
            "url.file:///local-mirror.insteadOf=" + next(iter(SOURCES)), *args])
    else:
        assert expected.args == ("/usr/bin/git", ["/usr/bin/git", *args])
    execute.reset_mock()
    if observer_failure:
        monkeypatch.setattr(witness, "git_record", Mock(side_effect=ValueError(SECRET)))
    with pytest.raises(OSError) as caught:
        with witness.installed_witness(tmp_path, shim, SOURCES, details):
            exec(compile(shim.read_text(), "synthetic-shim", "exec"), {})
    assert caught.value is error
    assert execute.call_count == 1 and execute.call_args == expected
    assert shim.read_bytes() == before and details.local_excerpt is redactor
    assert {"kind": "lifecycle", "restored": True} in records(tmp_path / "command-witness")
    assert SECRET not in json.dumps(records(tmp_path / "command-witness"))


def test_restore_failure_does_not_replace_active_preparation_failure(tmp_path, monkeypatch):
    shim = shim_file(tmp_path)
    details = SimpleNamespace(local_excerpt=Mock())
    redactor = details.local_excerpt
    error = RuntimeError(SECRET)
    with pytest.raises(RuntimeError) as caught:
        with witness.installed_witness(tmp_path, shim, SOURCES, details):
            monkeypatch.setattr(Path, "write_text", Mock(side_effect=OSError("restore failed")))
            raise error
    assert caught.value is error and details.local_excerpt is redactor
    assert records(tmp_path / "command-witness") == [{"kind": "lifecycle", "restored": False}]
