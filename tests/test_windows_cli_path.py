"""Portable command content, lossless PATH edits, opt-out and failure controls."""
from contextlib import contextmanager, nullcontext
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import windows_cli_path as commands

# Mutations run the same tests on an isolated copy, never on the source module.
if os.environ.get("CLI_PATH_MUTANT"):
    spec = importlib.util.spec_from_file_location(
        "amplifier_web.windows_cli_path", os.environ["CLI_PATH_MUTANT"])
    commands = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(commands)
    import amplifier_web
    sys.modules[spec.name] = commands
    amplifier_web.windows_cli_path = commands


@pytest.mark.parametrize("value,kind", [
    ("", 1), ("", 2), ("C:\\one", 1), ("C:\\one;", 2),
    ('"C:\\one space";;C:\\two;C:\\two;', 1), ("%HOME%\\tools;;", 2),
])
def test_append_keeps_every_byte_and_type(value, kind):
    folder = "C:\\app\\bin"
    result = commands.append(value, kind, folder)
    assert result == value + (";" if value else "") + folder
    assert commands.append(result, kind, folder) is None


@pytest.mark.parametrize("entry", [
    "C:\\APP\\BIN", "c:/app/bin/", '"c:\\app\\bin\\"', "%CLI_TEST_HOME%\\bin",
    "%cli_test_home%\\bin\\",
])
def test_existing_case_quotes_slashes_and_variables(entry, monkeypatch):
    monkeypatch.setenv("CLI_TEST_HOME", "C:\\app")
    assert commands.append("C:\\before;" + entry + ";C:\\after;", 2, "c:\\app\\bin") is None


@pytest.mark.parametrize("value,kind", [(3, 1), (["a"], 7), ("a", 4), ("a\0b", 1)])
def test_non_string_types_and_nul_are_refused(value, kind):
    with pytest.raises(ValueError):
        commands.append(value, kind, "C:\\app\\bin")


@pytest.mark.parametrize("value", ["a" * 32760, "\U0001f600" * 16380], ids=["ascii-limit", "utf16-limit"])
def test_environment_limit_is_utf16_and_never_truncates(value):
    with pytest.raises(ValueError, match="too long"):
        commands.append(value, 2, "C:\\app\\bin")


def test_limit_is_not_the_setx_1024_character_limit():
    assert commands.append("a" * 2000, 1, "C:\\app\\bin").startswith("a" * 2000 + ";")


def test_content_uses_own_folder_no_absolute_batch_literals(tmp_path):
    home = tmp_path / 'own ! ^ & café' / "app"
    executable = home.parent / "tools/amplifier-unified/Scripts/amplifier-unified.exe"
    files = commands.scripts(home, executable)
    assert set(files) == commands.NAMES
    cmd, bash = files["amplifier-unified.cmd"], files["amplifier-unified"]
    assert b'setlocal DisableDelayedExpansion\r\n' in cmd
    assert b'%%~fI' in cmd  # Canonical data path: the private reader refuses "..".
    assert b'--data-dir "%AMPLIFIER_CLI_HOME%" %*\r\n@exit /b %errorlevel%\r\n' in cmd
    assert b"%~dp0.." in cmd and str(home).encode() not in cmd
    assert b'exec "$bin"/' in bash and b'--data-dir "$home" "$@"\n' in bash
    assert b"amplifier-unified.exe" in cmd and b"amplifier-unified.exe" in bash


@pytest.mark.parametrize("part", ["bad%name", "bad!name", "bad^name", "bad&name", 'bad"name', "café"])
def test_unsafe_literal_relative_path_is_refused(tmp_path, part):
    with pytest.raises(ValueError):
        commands.scripts(tmp_path / "app", tmp_path / part / "amplifier-unified.exe")


@pytest.mark.parametrize("part", ["bad;name", 'bad"name', "bad\nname", "bad%name"])
def test_unrepresentable_path_entry_is_refused(tmp_path, part):
    with pytest.raises(ValueError):
        commands.scripts(tmp_path / part, tmp_path / "exe")


@pytest.fixture
def model(tmp_path, monkeypatch):
    from amplifier_web import windows_files, windows_uninstall, deployment
    home = tmp_path / "app"
    home.mkdir()
    state = SimpleNamespace(home=home, value="%TOOLS%\\bin;;C:\\other;",
                            kind=2, writes=[], broadcasts=0, fail=None, conflict=False)
    monkeypatch.setattr(commands.sys, "platform", "win32")
    monkeypatch.setattr(commands.sys, "prefix", str(tmp_path / "tools/amplifier-unified"))
    monkeypatch.setattr(windows_files, "address", Path)
    monkeypatch.setattr(windows_uninstall, "named", lambda *a, **kw: nullcontext())
    @contextmanager
    def directory(path, *, owned_root, create=False):
        assert owned_root == home
        if create:
            path.mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            raise FileNotFoundError(path)
        def read(name, *, limit):
            raw = (path / name).read_bytes()
            assert len(raw) <= limit
            return raw
        yield SimpleNamespace(read_private=read)
    monkeypatch.setattr(windows_files, "directory", directory)
    def write(path, raw, *, owned_root):
        assert owned_root == home
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.encode() if isinstance(raw, str) else raw)
    monkeypatch.setattr(deployment, "write_private", write)
    @contextmanager
    def environment(*, write=False):
        before = state.value, state.kind
        pending = []
        def commit():
            if state.conflict:
                raise OSError("Concurrent registry edit")
            state.value, state.kind = pending[-1]
            state.writes.append(pending[-1])
        state.pending = pending
        yield state, commit
        if not pending:
            assert (state.value, state.kind) == before
    monkeypatch.setattr(commands, "environment", environment)
    monkeypatch.setattr(commands, "_read", lambda handle: (state.value, state.kind))
    def set_value(handle, value, kind):
        if state.fail:
            raise state.fail
        state.pending.append((value, kind))
    monkeypatch.setattr(commands, "_set", set_value)
    def broadcast():
        state.broadcasts += 1
    monkeypatch.setattr(commands, "_broadcast", broadcast)
    state.record = lambda: json.loads((home / "config/services" / commands.RECORD).read_text())
    return state


@pytest.mark.parametrize("before,kind", [(None, 2), ("", 1), ("", 2), ("C:\\a;;C:\\b;", 1)])
def test_once_then_exact_uninstall_restores_absent_empty_text_and_type(model, before, kind):
    model.value, model.kind = before, kind
    commands.ensure(model.home)
    assert model.value == (before + ";" if before else "") + str(model.home / "bin")
    assert model.kind == kind and model.record()["state"] == "added"
    assert model.broadcasts == 1
    commands.ensure(model.home)
    assert len(model.writes) == 1
    commands.remove(model.home)
    assert (model.value, model.kind) == (before, kind)
    assert model.broadcasts == 2
    commands.remove(model.home)
    assert len(model.writes) == 2


def test_manual_removal_is_not_undone_at_later_start(model):
    commands.ensure(model.home)
    model.value = "C:\\the-person-removed-it;"
    commands.ensure(model.home)
    assert model.value == "C:\\the-person-removed-it;" and len(model.writes) == 1


def test_preexisting_entry_is_not_owned_or_removed(model):
    model.value += ";" + str(model.home / "bin")
    earlier = model.value
    commands.ensure(model.home)
    assert model.record()["state"] == "present" and model.writes == []
    commands.remove(model.home)
    assert model.value == earlier and model.writes == []


def test_other_entries_edits_order_and_type_survive_uninstall(model):
    commands.ensure(model.home)
    model.value = 'C:\\new;;' + model.value + ';%ADDED_LATER%\\bin;'
    model.kind = 1
    expected = model.value.replace(";" + str(model.home / "bin") + ";", ";")
    commands.remove(model.home)
    assert model.value == expected and model.kind == 1


def test_duplicates_or_changed_spelling_are_not_deleted(model):
    commands.ensure(model.home)
    model.value += ";" + str(model.home / "bin")
    earlier = model.value
    commands.remove(model.home)
    assert model.value == earlier


@pytest.mark.parametrize("error", [PermissionError("refused"), OSError("full"), RuntimeError("unavailable")])
def test_registry_failure_never_stops_start_and_is_not_replayed(model, error, monkeypatch):
    from amplifier_web import cli, reset
    model.fail = error
    reached = []
    monkeypatch.setattr(reset, "repair_lock", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(cli, "_serve_unlocked", lambda *a, **kw: reached.append(True))
    cli._serve(SimpleNamespace(), model.home)
    assert reached == [True] and model.writes == [] and model.record()["state"] == "adding"
    model.fail = None
    cli._serve(SimpleNamespace(), model.home)
    assert reached == [True, True] and model.writes == []


def test_commit_conflict_does_not_overwrite_another_writer(model):
    before = model.value
    model.conflict = True
    commands.ensure(model.home)
    assert model.value == before and model.writes == []


def test_private_record_failure_precedes_any_path_change(model, monkeypatch):
    monkeypatch.setattr(commands, "_save", lambda *a: (_ for _ in ()).throw(PermissionError()))
    commands.ensure(model.home)
    assert model.writes == []


def test_pending_commit_removed_only_when_full_value_and_type_match(model, monkeypatch):
    save = commands._save
    def fail_after_commit(home, row):
        if row["state"] == "added":
            raise OSError("Interrupted final record")
        save(home, row)
    monkeypatch.setattr(commands, "_save", fail_after_commit)
    before = model.value
    commands.ensure(model.home)
    assert model.record()["state"] == "adding"
    assert model.broadcasts == 1  # A final record failure must not hide the new environment.
    commands.remove(model.home)
    assert model.value == before


def test_pending_commit_with_changed_value_is_not_adopted(model, monkeypatch):
    model.fail = OSError("write refused")
    commands.ensure(model.home)
    model.fail = None
    model.value += ";" + str(model.home / "bin")
    model.value += ";C:\\later"
    earlier = model.value
    commands.remove(model.home)
    assert model.value == earlier


def test_new_generation_and_rollback_change_only_small_files(model, monkeypatch):
    commands.ensure(model.home)
    bin_path = model.home / "bin"
    before = {p.name: p.read_bytes() for p in bin_path.iterdir()}
    monkeypatch.setattr(commands.sys, "prefix", str(model.home / "updates/a/new/tools/amplifier-unified"))
    commands.ensure(model.home)
    after = {p.name: p.read_bytes() for p in bin_path.iterdir()}
    assert before != after and len(model.writes) == 1 and set(after) == commands.NAMES
    assert b"new" in after["amplifier-unified.cmd"]
    times = {p.name: p.stat().st_mtime_ns for p in bin_path.iterdir()}
    commands.ensure(model.home)
    assert {p.name: p.stat().st_mtime_ns for p in bin_path.iterdir()} == times
    monkeypatch.setattr(commands.sys, "prefix", str(model.home / "updates/a/old/tools/amplifier-unified"))
    commands.ensure(model.home)
    assert b"old" in (bin_path / "amplifier-unified.cmd").read_bytes()
    assert len(model.writes) == 1


def test_unexpected_bin_file_never_puts_folder_on_path(model):
    path = model.home / "bin"
    path.mkdir()
    (path / "python.exe").write_bytes(b"not ours")
    commands.ensure(model.home)
    assert model.writes == []
    assert (path / "python.exe").read_bytes() == b"not ours"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_change_nothing(model, monkeypatch, platform):
    monkeypatch.setattr(commands.sys, "platform", platform)
    commands.ensure(model.home)
    commands.remove(model.home)
    assert list(model.home.iterdir()) == [] and model.writes == []


def test_doctor_on_and_off_path_is_read_only(model):
    assert commands.doctor(model.home)["ok"] is False
    assert "Environment Variables" in commands.doctor(model.home)["message"]
    commands.ensure(model.home)
    result = commands.doctor(model.home)
    assert result["ok"] is True and "first matching command" in result["message"]
    assert len(model.writes) == 1


def test_expansion_and_combined_path_cannot_cross_environment_limit(monkeypatch):
    monkeypatch.setenv("LONG_CLI_TEST", "x" * 32760)
    with pytest.raises(ValueError, match="too long"):
        commands.append("%LONG_CLI_TEST%", 2, "C:\\app\\bin")
    monkeypatch.setenv("PATH", "x" * 32760)
    with pytest.raises(ValueError, match="too long"):
        commands.append("C:\\a", 1, "C:\\app\\bin")


def test_changed_value_with_nul_is_never_rewritten(model):
    commands.ensure(model.home)
    model.value += ";a\0b"
    before = model.value
    commands.remove(model.home)
    assert model.value == before and len(model.writes) == 1