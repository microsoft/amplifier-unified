"""Portable ownership/composition controls; actual COM/HKCU checks are separate."""
from contextlib import contextmanager, nullcontext
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web import windows_consumer_shell as shell


@pytest.fixture(autouse=True)
def one_at_a_time(monkeypatch):
    """Off Windows the installation lock has a stand-in; tests/test_windows_uninstall.py has its cases."""
    if os.name != "nt":
        from amplifier_web import windows_uninstall
        monkeypatch.setattr(windows_uninstall, "single", lambda root, **options: nullcontext())


@pytest.fixture
def model(tmp_path, monkeypatch):
    from amplifier_web import windows_files, deployment
    root = tmp_path / "install"
    root.mkdir()
    programs = tmp_path / "Programs"
    programs.mkdir()
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    state = {"registry": None, "links": {}, "calls": [], "pins": set(), "writes": []}
    monkeypatch.setattr(windows_files, "address", Path)
    monkeypatch.setattr(shell, "programs", lambda: programs)
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 0)  # One attempt; the wait has its own test.
    @contextmanager
    def directory(path, **kwargs):
        # The real check refuses a link, and a folder that this account does not own alone.
        if path.is_symlink() or (state.get("refuse_folder") and state["refuse_folder"](path)):
            raise PermissionError("The private-folder check refused this path")
        if kwargs.get("create"):
            path.mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            raise FileNotFoundError(path)
        def read(name, limit):
            raw = (path / name).read_bytes()
            assert len(raw) <= limit
            return raw
        def publish(name, content):
            with (path / name).open("xb") as stream:
                stream.write(content)
        @contextmanager
        def removal(name, digest):
            if state.get("before_pin"):
                state["before_pin"](path, name)
            if hashlib.sha256(read(name, 65536)).hexdigest() != digest:
                raise ValueError("Owned private file changed; removal refused.")
            state["pins"].add(name)
            try:
                def remove():
                    assert name in state["pins"]
                    if state.get("before_remove"):
                        state["before_remove"](name)
                    if state.get("link_failure") == name:
                        raise OSError("The shortcut could not be deleted")
                    state["calls"].append(("remove", name))
                    (path / name).unlink()
                yield remove
            finally:
                state["pins"].remove(name)
        yield SimpleNamespace(read_private=read, removal_private=removal, publish_private=publish)
    monkeypatch.setattr(windows_files, "directory", directory)
    def write_private(path, raw, **kw):
        state["writes"].append((path, raw, kw))
        if state.get("refuse_write") and state["refuse_write"](path):
            raise PermissionError(13, "The private write was refused", str(path))
        path.parent.mkdir(parents=True, exist_ok=True)  # The real writer creates private folders.
        path.write_text(raw)
    monkeypatch.setattr(deployment, "write_private", write_private)
    @contextmanager
    def link():
        def create(path, python, args, cwd):
            state["links"][str(path)] = (str(python), args)
            path.write_bytes(json.dumps({"python": str(python), "args": args, "cwd": str(cwd)}).encode())
        yield SimpleNamespace(create=create, read=lambda path: state["links"][str(path)])
    monkeypatch.setattr(shell, "shell_link", link)
    def registry(root, values=None, *, remove=False):
        if remove:
            assert values == state["registry"]
            state["calls"].append(("registry-remove",))
            state["registry"] = None
        elif values is not None:
            assert state["registry"] is None
            state["registry"] = values
        if state["registry"] is not None:
            return {name: (value, state.get("registry_types", {}).get(name, 4 if type(value) is int else 1))
                    for name, value in state["registry"].items()}
    monkeypatch.setattr(shell, "registry", registry)
    def registry_version(root, earlier, version):
        state["calls"].append(("registry-version",))
        if state.get("before_version"):
            state["before_version"]()
        expected = {name: (value, 4 if type(value) is int else 1) for name, value in earlier.items()}
        if registry(root) != expected:
            raise ValueError("The whole earlier key must match, including its version and types")
        if state.get("version_error"):
            raise state["version_error"]
        # The note and new record must precede the registry transaction.
        note = json.loads((root / "app/config/services" / shell.NOTE).read_text())
        row = json.loads((root / shell.FILE).read_text())
        assert note == {"schema": 1, "root": str(root), "from": earlier["DisplayVersion"], "to": version}
        assert row["registry"] == {**earlier, "DisplayVersion": version}
        state["registry"] = {**earlier, "DisplayVersion": version}
    monkeypatch.setattr(shell, "registry_version", registry_version)
    @contextmanager
    def registry_change(root, values, *, remove=False):
        # Every shortcut that still exists is pinned before the registry is staged.
        assert remove and state["pins"] == {name for name in shell.LINKS if (folder / name).exists()}
        if state["registry"] is None:
            raise FileNotFoundError(2, "The installed-app entry is absent")
        if state["registry"] != values:
            raise ValueError("Registry changed")
        def commit():
            if state.get("commit_conflict"):
                raise OSError("Registry transaction conflicted with a later edit")
            registry(root, values, remove=True)
        yield commit
    monkeypatch.setattr(shell, "registry_change", registry_change)
    return root, programs, state


@pytest.fixture
def versioned(model):
    root, programs, state = model
    shell.install(root, "1.0.0")
    state["writes"].clear()
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    return SimpleNamespace(root=root, home=root / "app", state=state,
                           row=json.loads((root / shell.FILE).read_text()),
                           note=root / "app/config/services" / shell.NOTE,
                           shortcuts={path: path.read_bytes() for path in folder.iterdir()})


@pytest.mark.parametrize("change", ["none", "version", "value", "extra", "type", "children", "absent", "commit"])
def test_registry_version_opens_existing_key_compares_it_whole_and_commits(tmp_path, monkeypatch, change):
    root = tmp_path / "install"
    earlier = shell.registration_values(root, "1.0.0")
    actual = {name: (value, 4 if type(value) is int else 1) for name, value in earlier.items()}
    if change == "version": actual["DisplayVersion"] = ("8.0.0", 1)
    elif change == "value": actual["DisplayName"] = ("foreign", 1)
    elif change == "extra": actual["foreign"] = ("keep", 1)
    elif change == "type": actual["DisplayVersion"] = ("1.0.0", 2)
    events, staged = [], []
    def open_key(hive, key, options, access, handle, transaction, extended):
        assert key == shell.REGISTRY + shell.identity(root) and transaction == 55
        assert access == 3  # KEY_READ | KEY_SET_VALUE, no create or delete.
        events.append("open")
        handle._obj.value = 101
        return 2 if change == "absent" else 0
    def unexpected(*args):
        pytest.fail("Version changes must not create or delete a key")
    def set_value(handle, name, reserved, kind, value):
        assert handle == 101 and (name, reserved, kind, value) == ("DisplayVersion", 0, 1, "2.0.0")
        staged.append((name, (value, kind)))
    def commit(transaction):
        events.append("commit")
        if change == "commit":
            return False
        actual.update(staged)
        return True
    def rollback(transaction):
        events.append("rollback")
        return True
    def close_transaction(transaction):
        events.append("close-transaction")
        return True
    def close_key(handle):
        assert handle == 101
        events.append("close-key")
    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=0x80000001, KEY_READ=1, KEY_SET_VALUE=2, REG_SZ=1, REG_DWORD=4,
        QueryInfoKey=lambda handle: (int(change == "children"), len(actual), 0),
        EnumValue=lambda handle, index: (list(actual)[index], *list(actual.values())[index]),
        SetValueEx=set_value, CloseKey=close_key)
    libraries = {
        "kernel32": SimpleNamespace(CloseHandle=close_transaction),
        "ktmw32": SimpleNamespace(CreateTransaction=lambda *args: 55,
                                 CommitTransaction=commit, RollbackTransaction=rollback),
        "advapi32": SimpleNamespace(RegOpenKeyTransactedW=open_key,
                                   RegCreateKeyTransactedW=unexpected, RegDeleteKeyTransactedW=unexpected)}
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(shell.C, "WinDLL", lambda name, **options: libraries[name], raising=False)
    monkeypatch.setattr(shell.C, "WinError", lambda code: OSError(code, "native stand-in"), raising=False)
    monkeypatch.setattr(shell.C, "get_last_error", lambda: 6800, raising=False)
    before = dict(actual)
    if change == "none":
        shell.registry_version(root, earlier, "2.0.0")
        assert actual == {**before, "DisplayVersion": ("2.0.0", 1)}
        assert events == ["open", "commit", "close-key", "close-transaction"]
    else:
        with pytest.raises(OSError if change in ("absent", "commit") else ValueError):
            shell.registry_version(root, earlier, "2.0.0")
        assert actual == before
        assert events[-2:] == ["rollback", "close-transaction"]
        assert staged == ([("DisplayVersion", ("2.0.0", 1))] if change == "commit" else [])


def test_confirmed_version_updates_record_and_key_before_uninstall(versioned):
    v = versioned
    assert shell.set_version(v.root, "2.3.4", home=v.home) == {
        "changed": True, "from": "1.0.0", "to": "2.3.4"}
    assert v.state["registry"] == {**v.row["registry"], "DisplayVersion": "2.3.4"}
    assert json.loads((v.root / shell.FILE).read_text()) == {
        **v.row, "registry": v.state["registry"]}
    assert [(path, options) for path, _, options in v.state["writes"]] == [
        (v.note, {"owned_root": v.home}), (v.root / shell.FILE, {"owned_root": v.root})]
    assert not v.note.exists()
    assert all(path.read_bytes() == raw for path, raw in v.shortcuts.items())
    assert shell.remove(v.root)["installedAppEntryRemoved"]
    assert v.state["registry"] is None


@pytest.mark.parametrize("version", [None, 123, "", "2.0", "2.0.0.0", "v2.0.0", "2.0.0.dev1", "2.0.0\n"])
def test_non_release_version_writes_nothing(versioned, version):
    v = versioned
    assert shell.set_version(v.root, version, home=v.home) == {"changed": False, "reason": "invalid-version"}
    assert not v.state["writes"] and not v.state["calls"] and not v.note.exists()
    assert json.loads((v.root / shell.FILE).read_text()) == v.row


@pytest.mark.parametrize("change", [
    "extra", "schema-bool", "schema-other", "root", "folder", "status", "status-list", "links",
    "missing-link", "registry", "missing-value", "changed-value", "invalid-version", "bool-value", "not-object"])
def test_version_and_removal_share_the_closed_record_check(versioned, change):
    v = versioned
    row = json.loads(json.dumps(v.row))
    if change == "extra": row["extra"] = 1
    elif change == "schema-bool": row["schema"] = True
    elif change == "schema-other": row["schema"] = 2
    elif change == "root": row["root"] = str(v.root / "other")
    elif change == "folder": row["folder"] += "-other"
    elif change == "status": row["status"] = "preparing"
    elif change == "status-list": row["status"] = []
    elif change == "links": row["links"] = []
    elif change == "missing-link": row["links"].pop(next(iter(shell.LINKS)))
    elif change == "registry": row["registry"] = []
    elif change == "missing-value": row["registry"].pop("NoRepair")
    elif change == "changed-value": row["registry"]["UninstallString"] = "foreign"
    elif change == "invalid-version": row["registry"]["DisplayVersion"] = "dev"
    elif change == "bool-value": row["registry"]["NoRepair"] = True
    else: row = []
    raw = json.dumps(row)
    (v.root / shell.FILE).write_text(raw)
    assert shell.set_version(v.root, "2.0.0", home=v.home) == {"changed": False, "reason": "invalid-record"}
    with pytest.raises(ValueError):
        shell.remove(v.root)
    assert not v.state["writes"] and not v.state["calls"]
    assert (v.root / shell.FILE).read_text() == raw and not v.note.exists()


@pytest.mark.parametrize("status,version,reason", [
    ("removed", "1.0.0", "not-installed"), ("removed", "dev", "not-installed"),
    ("installed", "1.0.0", "current")])
def test_removed_or_current_record_needs_no_registry_read(versioned, monkeypatch, status, version, reason):
    v = versioned
    row = {**v.row, "status": status}
    (v.root / shell.FILE).write_text(json.dumps(row))
    def unexpected(root):
        pytest.fail("No key read is needed")
    monkeypatch.setattr(shell, "registry", unexpected)
    assert shell.set_version(v.root, version, home=v.home) == {"changed": False, "reason": reason}
    assert not v.state["writes"] and not v.note.exists()


@pytest.mark.parametrize("change", ["absent", "extra", "missing", "value", "type"])
def test_version_preflight_requires_every_registry_value_and_type(versioned, change):
    v = versioned
    if change == "absent": v.state["registry"] = None
    elif change == "extra": v.state["registry"]["foreign"] = "keep"
    elif change == "missing": v.state["registry"].pop("NoRepair")
    elif change == "value": v.state["registry"]["DisplayName"] = "foreign"
    else: v.state["registry_types"] = {"DisplayName": 2}  # REG_EXPAND_SZ, not REG_SZ.
    before = dict(v.state["registry"]) if v.state["registry"] is not None else None
    assert shell.set_version(v.root, "2.0.0", home=v.home) == {"changed": False, "reason": "registry-changed"}
    assert v.state["registry"] == before and not v.state["writes"] and not v.state["calls"]
    assert json.loads((v.root / shell.FILE).read_text()) == v.row and not v.note.exists()


@pytest.mark.parametrize("change", ["version", "value", "extra", "type", "absent"])
def test_whole_earlier_key_is_checked_again_inside_the_version_step(versioned, change):
    v = versioned
    def race():
        if change == "version": v.state["registry"]["DisplayVersion"] = "8.0.0"
        elif change == "value": v.state["registry"]["DisplayName"] = "foreign"
        elif change == "extra": v.state["registry"]["foreign"] = "keep"
        elif change == "type": v.state["registry_types"] = {"NoRepair": 1}
        else: v.state["registry"] = None
    v.state["before_version"] = race
    assert shell.set_version(v.root, "2.0.0", home=v.home) == {"changed": False, "reason": "failed"}
    assert json.loads((v.root / shell.FILE).read_text()) == v.row and not v.note.exists()
    assert v.state["calls"] == [("registry-version",)]
    if change == "version":
        assert v.state["registry"]["DisplayVersion"] == "8.0.0"
    elif change == "absent":
        assert v.state["registry"] is None
    else:
        assert v.state["registry"]["DisplayVersion"] == "1.0.0"


def test_refused_version_record_write_does_not_wait_retry_or_leave_a_note(versioned, monkeypatch):
    v = versioned
    v.state["refuse_write"] = lambda path: path == v.root / shell.FILE
    monkeypatch.setattr(shell.time, "sleep", lambda seconds: pytest.fail("No wait is allowed"))
    assert shell.set_version(v.root, "2.0.0", home=v.home) == {"changed": False, "reason": "refused"}
    assert [path for path, _, _ in v.state["writes"]] == [v.note, v.root / shell.FILE]
    assert v.state["registry"] == v.row["registry"] and not v.state["calls"] and not v.note.exists()
    assert json.loads((v.root / shell.FILE).read_text()) == v.row


@pytest.mark.parametrize("error,reason", [(OSError("key failed"), "failed"), (ValueError("changed"), "failed"),
                                       (PermissionError("refused"), "refused")])
def test_failed_key_write_restores_the_record_then_removes_the_note(versioned, error, reason):
    v = versioned
    v.state["version_error"] = error
    assert shell.set_version(v.root, "2.0.0", home=v.home) == {"changed": False, "reason": reason}
    assert json.loads((v.root / shell.FILE).read_text()) == v.row
    assert v.state["registry"] == v.row["registry"] and not v.note.exists()
    assert [path for path, _, _ in v.state["writes"]] == [v.note, v.root / shell.FILE, v.root / shell.FILE]


@pytest.mark.parametrize("failure", ["refused", "silent", "readback"])
def test_rollback_keeps_the_note_until_the_earlier_record_is_read_back(versioned, monkeypatch, failure):
    from amplifier_web import deployment
    v = versioned
    v.state["version_error"] = OSError("key failed")
    write, receipt = deployment.write_private, shell._receipt
    def rollback_fails(path, raw, **options):
        if path == v.root / shell.FILE and json.loads(raw)["registry"]["DisplayVersion"] == "1.0.0":
            if failure == "refused":
                raise PermissionError("rollback refused")
            if failure == "silent":
                return
        write(path, raw, **options)
    def readback(root):
        if failure == "readback" and v.state["calls"]:
            raise OSError("readback failed")
        return receipt(root)
    monkeypatch.setattr(deployment, "write_private", rollback_fails)
    monkeypatch.setattr(shell, "_receipt", readback)
    assert shell.set_version(v.root, "2.0.0", home=v.home) == {
        "changed": False, "reason": "failed", "noteRetained": True}
    assert v.note.exists() and v.state["registry"] == v.row["registry"]
    expected = "1.0.0" if failure == "readback" else "2.0.0"
    assert json.loads((v.root / shell.FILE).read_text())["registry"]["DisplayVersion"] == expected
    # With the cause gone, startup either finishes the key or sees agreement.
    monkeypatch.setattr(deployment, "write_private", write)
    monkeypatch.setattr(shell, "_receipt", receipt)
    v.state["version_error"] = None
    result = shell.settle_version(v.root, home=v.home)
    assert result["state"] == ("agreed-from" if failure == "readback" else "completed")
    assert not v.note.exists()


@pytest.mark.parametrize("record,key", [
    (record, key) for record in ("from", "to", "other", "removed", "missing", "invalid")
    for key in ("from", "to", "other", "absent", "type", "extra")])
def test_startup_settles_every_record_and_key_state(versioned, record, key):
    v = versioned
    versions = {"from": "1.0.0", "to": "2.0.0", "other": "3.0.0"}
    v.note.parent.mkdir(parents=True)
    v.note.write_text(json.dumps({"schema": 1, "root": str(v.root), "from": "1.0.0", "to": "2.0.0"}))
    row = {**v.row, "registry": {**v.row["registry"], "DisplayVersion": versions.get(record, "1.0.0")}}
    if record == "removed": row["status"] = "removed"
    (v.root / shell.FILE).write_text(json.dumps(row) if record != "invalid" else "null")
    if record == "missing": (v.root / shell.FILE).unlink()
    if key == "absent":
        v.state["registry"] = None
    else:
        v.state["registry"] = {**v.row["registry"], "DisplayVersion": versions.get(
            key, "2.0.0" if key == "extra" else "1.0.0")}
        if key == "type": v.state["registry_types"] = {"DisplayName": 2}
        if key == "extra": v.state["registry"]["foreign"] = "keep"
    before_key = dict(v.state["registry"]) if v.state["registry"] else None
    before_row = (v.root / shell.FILE).read_bytes() if record != "missing" else None
    result = shell.settle_version(v.root, home=v.home)
    if record in ("missing", "invalid"):
        expected = "unreadable-entries"
    elif record in ("removed", "other"):
        expected = "discarded"
    elif record == key:
        expected = "agreed-" + record
    elif record == "to":
        expected = "completed" if key == "from" else "rolled-back"
    else:
        expected = "discarded"
    assert result["state"] == expected and not v.note.exists()
    assert result["changed"] is (expected == "completed")
    if expected == "completed":
        assert v.state["registry"]["DisplayVersion"] == "2.0.0"
    else:
        assert v.state["registry"] == before_key
    if expected == "rolled-back":
        assert json.loads((v.root / shell.FILE).read_text()) == v.row
    else:
        assert ((v.root / shell.FILE).read_bytes() if record != "missing" else None) == before_row
    assert all(path.read_bytes() == raw for path, raw in v.shortcuts.items())
    assert v.state["calls"] == ([("registry-version",)] if expected == "completed" else [])


@pytest.mark.parametrize("refuse_restore", [False, True])
@pytest.mark.parametrize("race", ["none", "later", "foreign"])
def test_startup_failed_key_step_rolls_back_or_keeps_the_note(versioned, refuse_restore, race):
    v = versioned
    v.state["version_error"] = OSError("key failed")
    v.state["refuse_write"] = lambda path: path == v.root / shell.FILE and refuse_restore
    v.note.parent.mkdir(parents=True)
    v.note.write_text(json.dumps({"schema": 1, "root": str(v.root), "from": "1.0.0", "to": "2.0.0"}))
    (v.root / shell.FILE).write_text(json.dumps(
        {**v.row, "registry": {**v.row["registry"], "DisplayVersion": "2.0.0"}}))
    if race == "later":
        v.state["before_version"] = lambda: v.state["registry"].update(DisplayVersion="8.0.0")
    elif race == "foreign":
        v.state["registry"]["DisplayName"] = "foreign"
    result = shell.settle_version(v.root, home=v.home)
    assert result["state"] == ("rollback-pending" if refuse_restore else "rolled-back")
    assert v.note.exists() is refuse_restore
    assert v.state["registry"]["DisplayVersion"] == ("8.0.0" if race == "later" else "1.0.0")
    if race == "foreign":
        assert v.state["registry"]["DisplayName"] == "foreign" and not v.state["calls"]
    expected = "2.0.0" if refuse_restore else "1.0.0"
    assert json.loads((v.root / shell.FILE).read_text())["registry"]["DisplayVersion"] == expected


@pytest.mark.parametrize("services_exist", [False, True])
def test_startup_without_a_note_does_not_read_entries(versioned, monkeypatch, services_exist):
    v = versioned
    if services_exist:
        v.note.parent.mkdir(parents=True)
    def unexpected(root):
        pytest.fail("No entry read is needed")
    monkeypatch.setattr(shell, "_receipt", unexpected)
    monkeypatch.setattr(shell, "registry", unexpected)
    assert shell.settle_version(v.root, home=v.home) is None


@pytest.mark.parametrize("change,state", [
    ("root", "foreign-note"), ("root-type", "invalid-note"), ("extra", "invalid-note"),
    ("schema", "invalid-note"), ("schema-other", "invalid-note"), ("from", "invalid-note"),
    ("version", "invalid-note"), ("missing", "invalid-note"), ("list", "invalid-note"),
    ("json", "unreadable-note"), ("bytes", "unreadable-note")])
def test_startup_discards_foreign_or_invalid_notes_without_entry_writes(versioned, change, state):
    v = versioned
    note = {"schema": 1, "root": str(v.root), "from": "1.0.0", "to": "2.0.0"}
    if change == "root": note["root"] += "-other"
    elif change == "root-type": note["root"] = 7
    elif change == "extra": note["extra"] = True
    elif change == "schema": note["schema"] = True
    elif change == "schema-other": note["schema"] = 2
    elif change == "from": note["from"] = "dev"
    elif change == "version": note["to"] = "dev"
    elif change == "missing": note.pop("from")
    elif change == "list": note = []
    raw = b"not json" if change == "json" else b"\xff" if change == "bytes" else json.dumps(note).encode()
    v.note.parent.mkdir(parents=True)
    v.note.write_bytes(raw)
    assert shell.settle_version(v.root, home=v.home) == {"changed": False, "state": state}
    assert not v.note.exists() and not v.state["writes"] and not v.state["calls"]
    assert json.loads((v.root / shell.FILE).read_text()) == v.row
    assert v.state["registry"] == v.row["registry"]


@pytest.mark.parametrize("step", ["note", "record"])
def test_failed_private_write_never_attempts_the_key_step(versioned, monkeypatch, step):
    from amplifier_web import deployment
    v = versioned
    write = deployment.write_private
    def fail(path, raw, **options):
        if path.name == (shell.NOTE if step == "note" else shell.FILE):
            raise OSError("private write failed")
        write(path, raw, **options)
    monkeypatch.setattr(deployment, "write_private", fail)
    result = shell.set_version(v.root, "2.0.0", home=v.home)
    assert result == {"changed": False, "reason": "failed", **({"noteRetained": True} if step == "record" else {})}
    assert v.note.exists() is (step == "record")
    assert not v.state["calls"] and v.state["registry"] == v.row["registry"]
    assert json.loads((v.root / shell.FILE).read_text()) == v.row
    if step == "record":
        assert shell.settle_version(v.root, home=v.home)["state"] == "agreed-from"
        assert not v.note.exists()


@pytest.mark.parametrize("step", ["note", "record", "key"])
def test_unreadable_startup_state_is_reported_without_entry_writes(versioned, monkeypatch, step):
    from amplifier_web import windows_files
    v = versioned
    v.note.parent.mkdir(parents=True)
    v.note.write_text(json.dumps({"schema": 1, "root": str(v.root), "from": "1.0.0", "to": "2.0.0"}))
    def unreadable(*args, **options):
        raise PermissionError("read refused")
    if step == "note":
        directory = windows_files.directory
        @contextmanager
        def unreadable_note(path, **options):
            with directory(path, **options) as owned:
                yield SimpleNamespace(read_private=unreadable) if path == v.note.parent else owned
        monkeypatch.setattr(windows_files, "directory", unreadable_note)
    else:
        monkeypatch.setattr(shell, "_receipt" if step == "record" else "registry", unreadable)
    assert shell.settle_version(v.root, home=v.home) == {
        "changed": False, "state": "unreadable-note" if step == "note" else "unreadable-entries"}
    assert not v.note.exists() and not v.state["writes"] and not v.state["calls"]
    assert json.loads((v.root / shell.FILE).read_text()) == v.row
    assert v.state["registry"] == v.row["registry"]
    if step == "record":
        assert shell.set_version(v.root, "2.0.0", home=v.home)["reason"] == "invalid-record"
    elif step == "key":
        assert shell.set_version(v.root, "2.0.0", home=v.home)["reason"] == "failed"


def test_note_removal_failure_is_reported_and_a_later_start_removes_it(versioned, monkeypatch):
    v = versioned
    unlink = Path.unlink
    def refuse_note(path, **options):
        if path == v.note:
            raise PermissionError("note removal refused")
        return unlink(path, **options)
    monkeypatch.setattr(Path, "unlink", refuse_note)
    assert shell.set_version(v.root, "2.0.0", home=v.home) == {
        "changed": True, "from": "1.0.0", "to": "2.0.0", "noteRetained": True}
    assert v.note.exists()
    assert shell.settle_version(v.root, home=v.home) == {
        "changed": False, "state": "agreed-to", "noteRetained": True}
    monkeypatch.setattr(Path, "unlink", unlink)
    assert shell.settle_version(v.root, home=v.home) == {"changed": False, "state": "agreed-to"}
    assert not v.note.exists()


@pytest.fixture
def uninstaller(model, monkeypatch):
    """The public uninstall over the model, with a recording Task Scheduler step."""
    from amplifier_web import installation_health, windows_consumer as consumer
    from amplifier_web import windows_installation, windows_service
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    state.update(notices=[], answers=[], working=None, task="running", task_failure=None,
                 certificate=None, windows_answer="removed")
    from amplifier_web import windows_trust
    # `certificate`: one root (bytes) or a list of roots that Windows holds, else None.
    # `windows_answer`: one answer for every question, or a list with one answer for each.
    held = lambda: [state["certificate"]] if isinstance(state["certificate"], bytes) else state["certificate"]
    monkeypatch.setattr(consumer, "_trusted_root", lambda value: held())
    def remove_root(der, store="ROOT"):
        # Windows asks last: the app has stopped and every entry is gone.
        assert der in held() and store == "ROOT" and state["task"] == "absent" and state["registry"] is None
        state["calls"].append(("certificate",))
        answer = state["windows_answer"].pop(0) if isinstance(state["windows_answer"], list) else state["windows_answer"]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(windows_trust, "remove_root", remove_root)
    monkeypatch.setattr(consumer, "installation", lambda value: (value, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    def notice(text, confirm=False):
        state["notices"].append((text, confirm))
        return state["answers"].pop(0) if confirm and state["answers"] else True
    monkeypatch.setattr(windows_installation, "owner_notice", notice)
    async def working(home):
        assert home == root / "app"
        return state["working"]
    monkeypatch.setattr(installation_health, "windows_working", working)
    def uninstall(home, *, progress):
        assert home == root / "app"
        # The app stops while every existing entry is still pinned and present.
        assert state["pins"] == {name for name in shell.LINKS if (folder / name).exists()}
        for step in ("task-read", "stop", "task-remove"):
            progress(step)
            if state["task"] == "absent":
                return
            if state["task_failure"] == step:
                raise windows_service.WindowsServiceError("synthetic-private-detail " + step)
            if step == "stop":
                state["task"] = "stopped"
                state["calls"].append(("stop",))
        state["task"] = "absent"
        state["calls"].append(("task-remove",))
    monkeypatch.setattr(windows_service, "uninstall", uninstall)
    run = lambda: asyncio.run(consumer.maintenance(root, "uninstall"))
    return SimpleNamespace(root=root, folder=folder, state=state, run=run, consumer=consumer)


def test_user_shell_entries_have_windowless_exact_root_and_normal_remove(model):
    root, programs, state = model
    result = shell.install(root, "1.0.0")
    assert result["shortcuts"] == 4 and result["installedAppEntry"]
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    assert set(p.name for p in folder.iterdir()) == set(shell.LINKS)
    for target, args in state["links"].values():
        assert target == str(root / "tools/amplifier-unified/Scripts/pythonw.exe")
        assert "amplifier_web.windows_consumer" in args and str(root) in args
        assert "powershell" not in args.lower() and "cmd.exe" not in args.lower()
    assert state["registry"]["InstallLocation"] == str(root)
    assert "--uninstall" in state["registry"]["UninstallString"]
    sentinel = root / "saved-user-data"
    sentinel.write_bytes(b"keep")
    removed = shell.remove(root)
    assert removed["userDataRetained"] and sentinel.read_bytes() == b"keep"
    # The emptied Start-menu folder goes too; nothing else in Programs does.
    assert state["registry"] is None and not folder.exists() and programs.is_dir()
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    assert state["calls"][0] == ("registry-remove",)
    # A completed removal can be asked again and changes nothing.
    before = list(state["calls"])
    assert shell.remove(root)["shortcutsRemoved"] and state["calls"] == before


def test_entries_can_be_installed_again_after_uninstall(uninstaller):
    root, state = uninstaller.root, uninstaller.state
    assert uninstaller.run()["ok"] and not shell.listed(root)
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    result = shell.install(root, "1.0.0")
    assert result["shortcuts"] == 4 and result["installedAppEntry"] and shell.listed(root)
    assert {path.name for path in uninstaller.folder.iterdir()} == set(shell.LINKS)
    assert state["registry"] is not None
    assert json.loads((root / shell.FILE).read_text())["status"] == "installed"


def test_a_record_that_still_says_installed_with_every_entry_gone_does_not_block(uninstaller):
    """The last write of an uninstall can be refused after every entry is removed."""
    root, state = uninstaller.root, uninstaller.state
    installed = (root / shell.FILE).read_text()
    assert json.loads(installed)["status"] == "installed" and shell.listed(root)
    assert uninstaller.run()["ok"]
    (root / shell.FILE).write_text(installed)  # As if that last write had been refused.
    assert not uninstaller.folder.exists() and state["registry"] is None
    # The record alone never sends a person to a Start-menu entry that is gone.
    assert shell.listed(root) is False
    assert shell.install(root, "1.0.0")["shortcuts"] == 4 and shell.listed(root)
    # With the entries present the same record refuses, and nothing is overwritten.
    before = dict(state["links"])
    with pytest.raises(ValueError, match="already has a receipt"):
        shell.install(root, "1.0.0")
    assert state["links"] == before and shell.listed(root)
    # A person deleted only the entry that opens Amplifier: not listed.
    (uninstaller.folder / next(iter(shell.LINKS))).unlink()
    assert shell.listed(root) is False


def test_a_record_of_entries_in_another_place_is_never_replaced(model, tmp_path):
    """The Start-menu location changed after setup: the recorded entries are still there."""
    root, _, state = model
    elsewhere = tmp_path / "Earlier Programs" / "Amplifier Unified - earlier"
    elsewhere.mkdir(parents=True)
    (elsewhere / "Amplifier Unified.lnk").write_bytes(b"shortcut")
    record = json.dumps({"status": "installed", "folder": str(elsewhere)})
    (root / shell.FILE).write_text(record)
    # Setup and the launcher read the same folder: the one that the record names.
    assert shell.listed(root) is True
    with pytest.raises(ValueError, match="already has a receipt"):
        shell.install(root, "1.0.0")
    assert (root / shell.FILE).read_text() == record and state["registry"] is None and not state["links"]


@pytest.mark.parametrize("raw", [
    '{"status":"installed"}', '{"status":"installed","folder":7}',
    '{"status":"preparing"}', '{"status":"other"}', '{}', '[]', 'null', 'not json'])
def test_only_a_removed_receipt_allows_installing_entries_again(model, raw):
    root, _, state = model
    (root / shell.FILE).write_text(raw)
    with pytest.raises(ValueError) as refused:
        shell.install(root, "1.0.0")
    assert str(refused.value) == "Windows shell integration already has a receipt; inspect it before retrying."
    assert state["registry"] is None and not state["links"]


def test_unreadable_receipt_refuses_install_and_is_not_listed(model, monkeypatch):
    root, _, _ = model
    (root / shell.FILE).write_text('{"status":"removed"}')
    def unreadable(root):
        raise PermissionError("read refused")
    monkeypatch.setattr(shell, "_receipt", unreadable)
    with pytest.raises(ValueError, match="^Windows shell integration already has a receipt; inspect it before retrying\\.$"):
        shell.install(root, "1.0.0")
    assert shell.listed(root) is False


@pytest.mark.parametrize("status,named,shortcut,expected", [
    (None, True, True, False), ("removed", True, True, False), ("installed", True, True, True),
    ("installed", True, False, False), ("installed", False, True, False), ("preparing", True, True, False),
    ("[]", True, True, False), ("not json", True, True, False)])
def test_listed_needs_the_installed_record_and_the_entry_that_opens_amplifier(
        model, status, named, shortcut, expected):
    root, programs, _ = model
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    if status in ("[]", "not json"):
        (root / shell.FILE).write_text(status)
    elif status is not None:
        (root / shell.FILE).write_text(json.dumps(
            {"status": status, **({"folder": str(folder)} if named else {})}))
    if shortcut:
        folder.mkdir()
        (folder / "Amplifier Unified.lnk").write_bytes(b"shortcut")
    assert shell.listed(root) is expected


EXIST = "The Windows app entries already exist; nothing was overwritten."


def _everything(top):
    """Every entry below `top`: its name, what it is, a file's bytes and time of change. No link is followed."""
    found = []
    for folder, folders, files in os.walk(top):
        for name in folders + files:
            path = Path(folder, name)
            what = "link" if path.is_symlink() else "folder" if path.is_dir() else "file"
            found.append((str(path.relative_to(top)), what,
                          (path.read_bytes(), path.stat().st_mtime_ns) if what == "file"
                          else os.readlink(path) if what == "link" else None))
    return sorted(found)


# What is at the path of the Start-menu folder, or directly in that folder, when setup adds the entries.
# Each of these refuses, and nothing is written: not a shortcut, not the record, not the Apps-list entry.
REFUSED = ["apps-list-entry", "shortcut-name", "shortcut-name-in-other-letter-case", "folder-with-a-shortcut-name",
           "all-four-shortcuts", "folder-of-another-owner", "link-to-another-folder", "link-to-nothing",
           "file-at-the-folder-path"]


@pytest.mark.parametrize("record", ['{"status":"removed"}', None])
@pytest.mark.parametrize("there", REFUSED)
def test_entries_are_not_added_where_one_of_them_or_something_foreign_is_there(model, tmp_path, there, record):
    root, programs, state = model
    if record:
        (root / shell.FILE).write_text(record)
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "theirs.txt").write_text("theirs")
    if there == "link-to-another-folder":
        folder.symlink_to(elsewhere, target_is_directory=True)
    elif there == "link-to-nothing":
        folder.symlink_to(tmp_path / "absent", target_is_directory=True)
    elif there == "file-at-the-folder-path":
        folder.write_text("a file, not a folder")
    else:
        folder.mkdir()
        (folder / "My own note.txt").write_text("mine")
    if there == "apps-list-entry":
        state["registry"] = {"foreign": "entry"}
    elif there == "shortcut-name":
        (folder / "Amplifier status.lnk").write_bytes(b"not this installation's")
    elif there == "shortcut-name-in-other-letter-case":
        (folder / "AMPLIFIER Status.LNK").write_bytes(b"not this installation's")
    elif there == "folder-with-a-shortcut-name":
        (folder / "Uninstall Amplifier.lnk").mkdir()
    elif there == "all-four-shortcuts":
        for name in shell.LINKS:
            (folder / name).write_bytes(b"of an earlier installation")
    elif there == "folder-of-another-owner":
        state["refuse_folder"] = lambda path: path == folder
    before = _everything(programs), _everything(elsewhere), state["registry"]
    with pytest.raises(ValueError) as refused:
        shell.install(root, "1.0.0")
    assert str(refused.value) == EXIST
    assert (_everything(programs), _everything(elsewhere), state["registry"]) == before
    assert not state["links"] and not state["writes"]
    assert (root / shell.FILE).read_text() == record if record else not (root / shell.FILE).exists()


@pytest.mark.parametrize("record", ['{"status":"removed"}', None])
def test_entries_are_not_added_when_windows_refuses_the_look_at_the_folder(model, monkeypatch, record):
    # A look that Windows refuses does not say that nothing is there. The folder holds only the
    # person's file, so the entries would be added if the refusal counted as "absent".
    root, programs, state = model
    if record:
        (root / shell.FILE).write_text(record)
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    folder.mkdir()
    (folder / "My own note.txt").write_bytes(b"mine")
    before = _everything(programs)
    look = os.lstat
    def refused(path, *args, **options):
        if Path(path) == folder:
            raise PermissionError(13, "Windows refused the look at this path")
        return look(path, *args, **options)
    with monkeypatch.context() as patch:
        patch.setattr(shell.os, "lstat", refused)
        with pytest.raises(ValueError) as stopped:
            shell.install(root, "1.0.0")
    assert str(stopped.value) == EXIST
    assert _everything(programs) == before and state["registry"] is None
    assert not state["links"] and not state["writes"]
    assert (root / shell.FILE).read_text() == record if record else not (root / shell.FILE).exists()


@pytest.mark.parametrize("record", ['{"status":"removed"}', None])
@pytest.mark.parametrize("there", ["nothing", "own-file", "own-folder-and-near-names"])
def test_entries_are_added_to_a_start_menu_folder_that_stayed_and_holds_none_of_them(model, there, record):
    # The folder stays after an uninstall when the person put a file into it, and when another program
    # still had a shortcut open. The record is gone after an uninstall that deleted the installation folder.
    root, programs, state = model
    if record:
        (root / shell.FILE).write_text(record)
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    folder.mkdir()
    if there != "nothing":
        (folder / "My own note.txt").write_bytes(b"mine")
    if there == "own-folder-and-near-names":
        # Only an entry directly in the folder with exactly one of the four names counts.
        (folder / "Mine").mkdir()
        (folder / "Mine" / "Amplifier status.lnk").write_bytes(b"my copy, in my own folder")
        (folder / "Amplifier status.lnk.txt").write_bytes(b"a longer name")
        (folder / "Amplifier status").write_bytes(b"a shorter name")
    before = _everything(folder)
    assert shell.install(root, "1.0.0") == {"startMenu": str(folder), "installedAppEntry": True, "shortcuts": 4}
    assert shell.listed(root) and state["registry"]["InstallLocation"] == str(root)
    assert json.loads((root / shell.FILE).read_text())["status"] == "installed"
    after = _everything(folder)
    # The four shortcuts are beside what was there. Nothing that was there has another name, kind,
    # content or time of change.
    assert [row for row in after if row[0] not in shell.LINKS] == before
    assert {row[0] for row in after} - {row[0] for row in before} == set(shell.LINKS)


def test_file_that_the_person_put_into_the_start_menu_folder_no_longer_keeps_the_entries_away(uninstaller):
    root, folder, state = uninstaller.root, uninstaller.folder, uninstaller.state
    (folder / "My own note.txt").write_bytes(b"mine")
    mine = [row for row in _everything(folder) if row[0] not in shell.LINKS]
    assert [row[0] for row in mine] == ["My own note.txt"]
    # The uninstall finishes. The folder stays, with the person's file alone.
    assert uninstaller.run()["ok"]
    assert _everything(folder) == mine and state["registry"] is None and not shell.listed(root)
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    # A second setup run adds the entries again, beside that file.
    assert shell.install(root, "1.0.0")["shortcuts"] == 4 and shell.listed(root)
    assert {path.name for path in folder.iterdir()} == set(shell.LINKS) | {"My own note.txt"}
    assert [row for row in _everything(folder) if row[0] not in shell.LINKS] == mine
    assert state["registry"]["InstallLocation"] == str(root)
    # The next uninstall removes them again and leaves the file.
    state["task"] = "running"
    assert uninstaller.run()["ok"]
    assert _everything(folder) == mine and state["registry"] is None


def test_record_that_still_says_installed_beside_a_folder_that_stayed_is_still_refused(uninstaller):
    # Two things at one uninstall: the person's file kept the folder, and the last write of the record
    # was refused. The record then names a folder that exists, and it is not replaced.
    root, folder, state = uninstaller.root, uninstaller.folder, uninstaller.state
    installed = (root / shell.FILE).read_text()
    (folder / "My own note.txt").write_bytes(b"mine")
    assert uninstaller.run()["ok"]
    (root / shell.FILE).write_text(installed)  # As if that last write had been refused.
    with pytest.raises(ValueError, match="^Windows shell integration already has a receipt; inspect it before retrying\\.$"):
        shell.install(root, "1.0.0")
    assert [path.name for path in folder.iterdir()] == ["My own note.txt"] and state["registry"] is None
    assert (root / shell.FILE).read_text() == installed and not shell.listed(root)


def test_shortcut_that_another_program_still_lists_refuses_once_and_then_the_entries_are_added(uninstaller):
    # Another program had a shortcut open and shared deleting: the uninstall finished, and Windows
    # removed the shortcut only when that program closed it. Until then the folder still lists it.
    root, folder, state = uninstaller.root, uninstaller.folder, uninstaller.state
    assert uninstaller.run()["ok"] and not folder.exists()
    folder.mkdir()
    (folder / "Amplifier status.lnk").write_bytes(b"still listed for the other program")
    record = (root / shell.FILE).read_text()
    with pytest.raises(ValueError) as refused:
        shell.install(root, "1.0.0")
    assert str(refused.value) == EXIST and (root / shell.FILE).read_text() == record
    assert [path.name for path in folder.iterdir()] == ["Amplifier status.lnk"] and state["registry"] is None
    (folder / "Amplifier status.lnk").unlink()  # The other program closed it.
    assert shell.install(root, "1.0.0")["shortcuts"] == 4 and shell.listed(root)


def test_removed_folder_keeps_a_file_the_person_added(model):
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    (folder / "My own note.txt").write_text("mine")
    shell.remove(root)
    assert [path.name for path in folder.iterdir()] == ["My own note.txt"]


@pytest.mark.parametrize("change", ["bytes", "binding", "receipt", "null", "registry", "preparing"])
def test_modified_shell_entries_never_remove_anything(model, change):
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    name = next(iter(shell.LINKS))
    if change == "bytes": (folder / name).write_bytes(b"foreign")
    elif change == "binding": state["links"][str(folder / name)] = ("other.exe", "")
    elif change == "receipt":
        row = json.loads((root / shell.FILE).read_text())
        row["folder"] = str(programs / "foreign")
        (root / shell.FILE).write_text(json.dumps(row))
    elif change == "null":
        (root / shell.FILE).write_text("null")
    elif change == "preparing":
        row = json.loads((root / shell.FILE).read_text())
        row["status"] = "preparing"
        (root / shell.FILE).write_text(json.dumps(row))
    else:
        row = json.loads((root / shell.FILE).read_text())
        row["registry"]["UninstallString"] = "foreign program"
        (root / shell.FILE).write_text(json.dumps(row))
    with pytest.raises(ValueError): shell.remove(root)
    assert state["registry"] is not None and not state["calls"]
    assert len(list(folder.iterdir())) == 4


def test_existing_or_partial_registration_does_not_replay(model):
    root, programs, state = model
    shell.install(root, "1.0.0")
    before = dict(state["links"]), state["registry"], (root / shell.FILE).read_text()
    # A second install over existing entries refuses, and nothing is replayed or overwritten.
    with pytest.raises(ValueError, match="already has a receipt"):
        shell.install(root, "1.0.0")
    assert not state["calls"]
    assert (dict(state["links"]), state["registry"], (root / shell.FILE).read_text()) == before


@pytest.mark.parametrize("gone", [
    {"Amplifier Unified.lnk"}, set(shell.LINKS), {"registry"}, set(shell.LINKS) | {"registry"},
    {"folder", "registry"}])
def test_removal_finishes_what_an_interrupted_removal_left(model, gone):
    # The state after a removal that stopped part-way: some entries are gone and
    # the receipt still says "installed". The next removal takes the rest.
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    for name in gone & set(shell.LINKS):
        (folder / name).unlink()
    if "folder" in gone:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
    if "registry" in gone:
        state["registry"] = None
    assert shell.remove(root)["installedAppEntryRemoved"]
    assert state["registry"] is None and not folder.exists() and not state["pins"]
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    removed = {name for kind, *rest in state["calls"] if kind == "remove" for name in rest}
    assert removed == set() if "folder" in gone else removed == set(shell.LINKS) - gone


def test_refused_receipt_write_after_removal_is_reported_not_raised(model):
    # The last step of removal is a private write into the installation folder.
    # Windows refuses it (sharing violation) while a conversation worker is alive
    # whose Foundation is older than e37a946b.
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    state["refuse_write"] = lambda path: path.name == shell.FILE
    result = shell.remove(root)
    assert isinstance(result["receiptError"], PermissionError) and result["shortcutsRemoved"]
    assert state["registry"] is None and not folder.exists()
    assert json.loads((root / shell.FILE).read_text())["status"] == "installed"
    # The stale receipt does not block a later removal, which now records it.
    state["refuse_write"] = None
    assert "receiptError" not in shell.remove(root)
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"


@pytest.mark.parametrize("released_after,recorded", [(3, True), (None, False)])
def test_receipt_write_waits_for_a_closing_app_and_then_reports_instead_of_failing(
        model, monkeypatch, released_after, recorded):
    # The app's worker programs end a moment after its task reports that it
    # stopped. Until then Windows refuses the receipt; setup reads its status.
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    waits = []
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 4)
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def refuse(path):
        # At every attempt the entries are already gone: the pins were released first.
        assert state["registry"] is None and not folder.exists() and not state["pins"]
        return path.name == shell.FILE and (released_after is None or len(waits) < released_after)
    state["refuse_write"] = refuse
    result = shell.remove(root)
    assert result["shortcutsRemoved"] and ("receiptError" not in result) is recorded
    assert json.loads((root / shell.FILE).read_text())["status"] == ("removed" if recorded else "installed")
    # Half a second between attempts; eight attempts and no sleep after the last one.
    assert waits == [0.5] * (released_after if recorded else 7)


def test_receipt_write_does_not_wait_for_a_failure_that_waiting_cannot_cure(model, monkeypatch):
    from amplifier_web import deployment
    root, programs, state = model
    shell.install(root, "1.0.0")
    waits = []
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 4)
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def broken(path, raw, **kw):
        raise ValueError("private files require a bounded absolute local Windows drive path")
    monkeypatch.setattr(deployment, "write_private", broken)
    assert isinstance(shell.remove(root)["receiptError"], ValueError) and not waits


# The last message's sentence about a certificate that Windows did not delete, complete. A No is Windows'
# own answer: it kept the certificate. After another failure the text does not say that Windows kept it.
KEPT = 'Windows kept the certificate "{name}". It works only for Amplifier on this PC.'
UNKNOWN = 'Windows can still hold the certificate "{name}". It works only for Amplifier on this PC.'
KEPT_OF_2 = ('Windows kept at least one of the 2 certificates named "{name}". '
             "Each works only for Amplifier on this PC.")
UNKNOWN_OF_2 = ('Windows can still hold one or more of the 2 certificates named "{name}". '
                "Each works only for Amplifier on this PC.")


@pytest.mark.parametrize("answer,kept", [
    ("removed", None), ("absent", None), ("declined", KEPT), ("blocked", UNKNOWN), (OSError("store"), UNKNOWN)])
def test_uninstall_asks_windows_once_to_delete_its_certificate_and_every_answer_finishes(
        uninstaller, monkeypatch, answer, kept):
    from amplifier_web.windows_trust import ROOT_NAME
    state, consumer = uninstaller.state, uninstaller.consumer
    state["certificate"], state["windows_answer"] = b"this installation's root", answer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    result = uninstaller.run()
    assert result["ok"] and result["certificate"] == ("blocked" if isinstance(answer, Exception) else answer)
    # One attempt, after the stop, the task and every entry.
    assert state["calls"].count(("certificate",)) == 1 and state["calls"][-1] == ("certificate",)
    (question, _), (confirmation, _) = state["notices"]
    assert f'delete the certificate "{ROOT_NAME}"' in question and "Select Yes there to remove it" in question
    assert "Amplifier is uninstalled" in confirmation
    # A certificate that stays is said plainly, and it is not an error. The message ends with that sentence.
    folder = "Your chats, settings, accounts and the program files are still in this folder:\n" + str(uninstaller.root)
    assert confirmation.endswith(folder + ("\n\n" + kept.format(name=ROOT_NAME) if kept else ""))
    assert ("certificate" in confirmation.replace(str(uninstaller.root), "")) is bool(kept)
    assert "could not" not in confirmation and "Reference:" not in confirmation


def test_installation_without_a_trusted_certificate_gets_no_certificate_text_or_question(uninstaller, monkeypatch):
    state, consumer = uninstaller.state, uninstaller.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    assert uninstaller.run()["certificate"] == "none"
    assert ("certificate",) not in state["calls"]
    assert not any("certificate" in text for text, _ in state["notices"])


@pytest.mark.parametrize("failure", ["stop", "task-remove", "commit"])
def test_unfinished_uninstall_keeps_the_certificate_that_the_app_still_needs(uninstaller, failure):
    state, consumer = uninstaller.state, uninstaller.consumer
    state["certificate"] = b"this installation's root"
    state["commit_conflict"], state["task_failure"] = failure == "commit", None if failure == "commit" else failure
    with pytest.raises(consumer.Unfinished):
        uninstaller.run()
    assert ("certificate",) not in state["calls"]


def test_only_roots_that_windows_trusts_for_this_installation_count_as_its_certificates(tmp_path, monkeypatch):
    from amplifier_web import windows_consumer as consumer, windows_tls, windows_trust
    state = {"identity": (b"root", b"leaf"), "trusted": {b"root"}, "record": []}
    monkeypatch.setattr(windows_tls, "keyless_identity", lambda home: state["identity"])
    monkeypatch.setattr(windows_tls, "replaced_roots", lambda home, strict=False: state["record"])
    monkeypatch.setattr(windows_trust, "_own_shape", lambda der: der != b"another shape")
    def contains(der, *, store="ROOT"):
        if isinstance(state["trusted"], Exception):
            raise state["trusted"]
        return der in state["trusted"]
    monkeypatch.setattr(windows_trust, "contains", contains)
    assert consumer._trusted_root(tmp_path) == [b"root"]
    for change in ({"trusted": set()}, {"trusted": OSError("store unavailable")}, {"identity": None}):
        state.update({"identity": (b"root", b"leaf"), "trusted": {b"root"}, **change})
        assert consumer._trusted_root(tmp_path) is None  # Never asked, never removed.
    assert consumer._forget_root(None) == "none"
    # A root that `setup-tls force` replaced and that Windows still holds is this installation's too.
    state.update(identity=(b"root", b"leaf"), record=[b"replaced", b"not held", b"another shape", b"root"],
                 trusted={b"root", b"replaced", b"another shape", b"not on the record"})
    assert consumer._trusted_root(tmp_path) == [b"root", b"replaced"]
    asked = []
    monkeypatch.setattr(windows_trust, "remove_root", lambda der, store="ROOT": asked.append(der) or "removed")
    # Windows is asked once for each of them, and for nothing else.
    assert consumer._forget_root([b"root", b"replaced"]) == "removed" and asked == [b"root", b"replaced"]
    monkeypatch.setattr(windows_trust, "forget_roots", lambda roots: (_ for _ in ()).throw(OSError("store")))
    assert consumer._forget_root([b"root"]) == "blocked"  # Never an error of the uninstall.


@pytest.mark.parametrize("answers,outcome,kept", [
    (["removed", "removed"], "removed", None), (["removed", "declined"], "declined", KEPT_OF_2),
    (["declined", "removed"], "declined", KEPT_OF_2), (["absent", "removed"], "removed", None),
    (["declined", "declined"], "declined", KEPT_OF_2), (["absent", "absent"], "absent", None),
    # A search that failed for one: the program does not know whether Windows holds it.
    ([OSError("store"), "removed"], "blocked", UNKNOWN_OF_2), (["blocked", "blocked"], "blocked", UNKNOWN_OF_2),
    # A No for one and a failure for the other: one stays for certain.
    ([OSError("store"), "declined"], "declined", KEPT_OF_2)])
def test_uninstall_asks_windows_once_for_each_certificate_that_windows_holds(
        uninstaller, monkeypatch, answers, outcome, kept):
    from amplifier_web.windows_trust import ROOT_NAME
    state, consumer = uninstaller.state, uninstaller.consumer
    # The current certificate, and one that `setup-tls force` replaced and the person kept.
    state["certificate"], state["windows_answer"] = [b"current root", b"replaced root"], list(answers)
    monkeypatch.setattr(consumer.sys, "stdout", None)
    result = uninstaller.run()
    assert result["ok"] and result["certificate"] == outcome
    # Two attempts, one for each, after the stop, the task and every entry. A No does not stop the second.
    assert state["calls"][-2:] == [("certificate",), ("certificate",)] and state["calls"].count(("certificate",)) == 2
    (question, _), (confirmation, _) = state["notices"]
    assert (f'At the end, Windows asks 2 times whether to delete a certificate named "{ROOT_NAME}". '
            "Windows holds 2 of them. Select Yes each time to remove them.") in question
    assert "Amplifier is uninstalled" in confirmation
    # The text gives the number that Windows was asked about, and no number of those that stay.
    folder = "Your chats, settings, accounts and the program files are still in this folder:\n" + str(uninstaller.root)
    assert confirmation.endswith(folder + ("\n\n" + kept.format(name=ROOT_NAME) if kept else ""))
    words = confirmation.replace(str(uninstaller.root), "")  # The folder's path is not the product's text.
    assert ("certificate" in words) is bool(kept) and "the certificate" not in words


def test_the_uninstall_question_says_how_often_windows_asks():
    from amplifier_web import windows_consumer as consumer
    from amplifier_web.windows_trust import ROOT_NAME
    assert consumer._certificate_sentence(None, at_the_end=True) == ""
    assert consumer._certificate_sentence([b"a"], at_the_end=True) == (
        f'\n\nAt the end, Windows asks whether to delete the certificate "{ROOT_NAME}". '
        "Select Yes there to remove it.")
    assert consumer._certificate_sentence([b"a"], at_the_end=False) == (
        f'\n\nDuring the uninstall, Windows asks whether to delete the certificate "{ROOT_NAME}". '
        "That is a separate question.")
    assert consumer._certificate_sentence([b"a", b"b", b"c"], at_the_end=False) == (
        f'\n\nDuring the uninstall, Windows asks 3 times whether to delete a certificate named "{ROOT_NAME}". '
        "Windows holds 3 of them. Those are separate questions.")


def saved_record(root):
    from amplifier_web import windows_consumer as consumer
    return root / "app/config/services" / consumer.RECORD


def test_public_uninstall_stops_the_app_before_it_removes_any_entry(uninstaller):
    state = uninstaller.state
    result = uninstaller.run()
    assert result == {"ok": True, "operation": "uninstall", "userDataRetained": True,
                      "installationFilesRetained": True, "certificate": "none"}
    assert state["calls"][:3] == [("stop",), ("task-remove",), ("registry-remove",)]
    assert {call[1] for call in state["calls"][3:]} == set(shell.LINKS) and len(state["calls"]) == 7
    assert state["task"] == "absent" and state["registry"] is None and not uninstaller.folder.exists()
    assert not state["pins"] and not saved_record(uninstaller.root).exists()
    # One question. With a console attached the result is printed, not shown.
    assert [confirm for _, confirm in state["notices"]] == [True]
    assert str(uninstaller.root) in state["notices"][0][0]


@pytest.mark.parametrize("conflict", ["prechanged", "last-pin", "registry"])
def test_changed_entry_refuses_before_the_app_is_stopped(uninstaller, conflict):
    state, folder = uninstaller.state, uninstaller.folder
    last = list(shell.LINKS)[-1]
    if conflict == "prechanged":
        (folder / last).write_bytes(b"changed before uninstall")
    elif conflict == "last-pin":
        def cross(path, name):
            if name == last:
                assert len(state["pins"]) == len(shell.LINKS) - 1
                (path / name).write_bytes(b"changed during set preparation")
        state["before_pin"] = cross
    else:
        state["registry"] = {"foreign": "registration"}
    registration = state["registry"].copy()
    with pytest.raises(uninstaller.consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == "entries-check"
    assert stopped.value.notice.startswith("This uninstall stopped before it changed anything.\n\n")
    assert not state["calls"] and state["task"] == "running" and state["registry"] == registration
    assert len(list(folder.iterdir())) == 4 and not state["pins"]


@pytest.mark.parametrize("step,stopped_app", [("task-read", False), ("stop", False), ("task-remove", True)])
def test_failed_stop_or_task_removal_leaves_every_entry_and_a_second_uninstall_works(
        uninstaller, step, stopped_app):
    state, folder, consumer = uninstaller.state, uninstaller.folder, uninstaller.consumer
    registration = state["registry"].copy()
    state["task_failure"] = step
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == step
    # The person keeps every entry: open, stop and uninstall still exist.
    assert state["registry"] == registration and {path.name for path in folder.iterdir()} == set(shell.LINKS)
    assert json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "installed"
    assert state["calls"] == ([("stop",)] if stopped_app else []) and not state["pins"]
    # Plain cause and next step; a short reference; never the exception text.
    notice = stopped.value.notice
    assert "uninstall again" in notice.lower() and f"Reference: {step}, WindowsServiceError" in notice
    assert "synthetic-private-detail" not in notice
    assert ("has stopped" in notice) is stopped_app
    record = json.loads(saved_record(uninstaller.root).read_text())
    assert (record["operation"], record["step"], record["errorType"]) == ("uninstall", step, "WindowsServiceError")
    assert record["where"] and record["schema"] == 1
    # The cause is gone: the same command now completes.
    state["task_failure"] = None
    assert uninstaller.run()["ok"]
    assert state["task"] == "absent" and state["registry"] is None and not folder.exists()


def test_registry_conflict_after_the_stop_keeps_all_entries_for_a_second_uninstall(uninstaller):
    state, folder, consumer = uninstaller.state, uninstaller.folder, uninstaller.consumer
    registration = state["registry"].copy()
    state["commit_conflict"] = True
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == "apps-entry" and "has stopped" in stopped.value.notice
    assert state["calls"] == [("stop",), ("task-remove",)] and state["task"] == "absent"
    assert state["registry"] == registration and len(list(folder.iterdir())) == 4
    state["commit_conflict"] = False
    assert uninstaller.run()["ok"]
    assert state["registry"] is None and not folder.exists()


def test_failed_shortcut_removal_is_finished_by_a_second_uninstall(uninstaller):
    state, folder, consumer = uninstaller.state, uninstaller.folder, uninstaller.consumer
    names = list(shell.LINKS)
    state["link_failure"] = names[1]
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == "shortcuts"
    # The uninstall shortcut is removed last, so it is still there to finish with.
    assert names[-1] == "Uninstall Amplifier.lnk" and (folder / names[-1]).exists()
    assert state["registry"] is None and state["task"] == "absent"
    assert json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "installed"
    state["link_failure"] = None
    assert uninstaller.run()["ok"]
    assert not folder.exists() and json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "removed"


# Another program (the shell, a search index, a scanner) can have a shortcut open for a moment. Windows
# refuses the uninstall's pin with error 32 while another open of the shortcut does not share deleting.
# It refuses the delete with error 5 while a view of the file is mapped. Hosted Windows runners showed
# both errors for a moment after the shortcuts were made. Which program caused error 5 there is not known.
HELD = [("pin", 32, "entries-check"), ("delete", 5, "shortcuts")]


def held(code):
    """Windows' refusal while another program has the shortcut: a PermissionError with this Windows code."""
    error = PermissionError(13, "Another program has the shortcut open")
    error.winerror = code  # Off Windows the class has no such field; the product reads it by name.
    return error


def another_program(state, place, refuse):
    """`refuse(name)` runs at each attempt to pin (`pin`) or to delete (`delete`) a shortcut."""
    state["before_pin" if place == "pin" else "before_remove"] = lambda *where: refuse(where[-1])


@pytest.mark.parametrize("place,code,step", HELD)
def test_shortcut_that_another_program_holds_for_a_moment_does_not_stop_the_uninstall(
        uninstaller, monkeypatch, place, code, step):
    state, folder, waits = uninstaller.state, uninstaller.folder, []
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def refuse(name):
        if name == "Amplifier status.lnk" and len(waits) < 3:
            raise held(code)
    another_program(state, place, refuse)
    assert uninstaller.run()["ok"]
    assert waits == [0.1] * 3
    assert state["task"] == "absent" and state["registry"] is None and not folder.exists() and not state["pins"]
    assert json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "removed"
    assert not saved_record(uninstaller.root).exists()


@pytest.mark.parametrize("place,code,step", HELD)
def test_shortcut_that_stays_held_stops_the_uninstall_after_a_bounded_wait(
        uninstaller, monkeypatch, place, code, step):
    state, folder, consumer = uninstaller.state, uninstaller.folder, uninstaller.consumer
    registration, waits, attempts = state["registry"].copy(), [], []
    monkeypatch.setattr(shell, "HOLD_SECONDS", 2)
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def refuse(name):
        if name == "Amplifier status.lnk":
            attempts.append(name)
            assert len(attempts) <= 21, "The wait for a held shortcut has no end."
            raise held(code)
    another_program(state, place, refuse)
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert (stopped.value.step, stopped.value.error.winerror) == (step, code)
    # Two seconds: one attempt, then twenty more, a tenth of a second apart. No wait after the last.
    assert waits == [0.1] * 20 and len(attempts) == 21 and not state["pins"]
    if place == "pin":
        # Nothing was changed, and the app still runs.
        assert stopped.value.notice.startswith("This uninstall stopped before it changed anything.\n\n")
        assert not state["calls"] and state["task"] == "running" and state["registry"] == registration
        assert {path.name for path in folder.iterdir()} == set(shell.LINKS)
    else:
        # The app has stopped and the Apps-list entry is gone. The held shortcut and every later one
        # stay. The shortcut that uninstalls is the last, so the person can uninstall again.
        assert "Uninstall again to finish." in stopped.value.notice
        assert state["calls"][:3] == [("stop",), ("task-remove",), ("registry-remove",)]
        assert state["task"] == "absent" and state["registry"] is None
        assert [path.name for path in sorted(folder.iterdir())] == sorted(list(shell.LINKS)[1:])
        assert list(shell.LINKS)[-1] == "Uninstall Amplifier.lnk"
    # The other program has closed the shortcut: the same command completes.
    another_program(state, place, lambda name: None)
    assert uninstaller.run()["ok"]
    assert state["task"] == "absent" and state["registry"] is None and not folder.exists()


@pytest.mark.parametrize("place", ["pin", "delete"])
def test_the_wait_for_held_shortcuts_is_bounded_for_the_whole_step_not_for_each_shortcut(
        uninstaller, monkeypatch, place):
    state, consumer, waits, attempts = uninstaller.state, uninstaller.consumer, [], {}
    code = dict((where, number) for where, number, _ in HELD)[place]
    monkeypatch.setattr(shell, "HOLD_SECONDS", 2)
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def refuse(name):
        # Each of two shortcuts is held for its first fifteen attempts.
        attempts[name] = attempts.get(name, 0) + 1
        if name in ("Amplifier status.lnk", "Stop Amplifier.lnk") and attempts[name] <= 15:
            raise held(code)
    another_program(state, place, refuse)
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.error.winerror == code
    # Fifteen waits for the first of them, and only the five that are left for the second.
    assert waits == [0.1] * 20
    assert (attempts["Amplifier status.lnk"], attempts["Stop Amplifier.lnk"]) == (16, 6)
    assert "Uninstall Amplifier.lnk" not in attempts


def test_held_shortcut_that_changes_during_the_wait_refuses_at_once_when_the_hold_ends(uninstaller, monkeypatch):
    # Each further attempt of a pin reads and compares the shortcut's bytes again. So a shortcut that
    # changed while another program held it is refused, and not removed, when that program lets go.
    state, folder, consumer, waits = uninstaller.state, uninstaller.folder, uninstaller.consumer, []
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    name = "Amplifier status.lnk"
    def refuse(entry):
        if entry == name:
            if len(waits) < 2:
                raise held(32)
            (folder / name).write_bytes(b"changed while another program held it")
    another_program(state, "pin", refuse)
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == "entries-check" and type(stopped.value.error) is ValueError
    assert stopped.value.notice.startswith("This uninstall stopped before it changed anything.\n\n")
    assert waits == [0.1] * 2 and not state["calls"] and state["task"] == "running"
    assert state["registry"] is not None and len(list(folder.iterdir())) == 4 and not state["pins"]


def test_the_pin_and_the_delete_each_have_their_own_wait(uninstaller, monkeypatch):
    # The app stops between the two steps, and that can take long. So the wait of the pin takes
    # nothing from the wait of the delete.
    state, folder, waits, attempts = uninstaller.state, uninstaller.folder, [], {"pin": 0, "delete": 0}
    monkeypatch.setattr(shell, "HOLD_SECONDS", 2)
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def refuse(place):
        def refused(name):
            if name == "Amplifier status.lnk":
                attempts[place] += 1
                if attempts[place] <= 15:
                    raise held(dict((where, number) for where, number, _ in HELD)[place])
        return refused
    state["before_pin"] = lambda path, name: refuse("pin")(name)
    state["before_remove"] = refuse("delete")
    assert uninstaller.run()["ok"]
    assert waits == [0.1] * 30 and attempts == {"pin": 16, "delete": 16}
    assert state["registry"] is None and not folder.exists() and not state["pins"]


@pytest.mark.parametrize("place,code", [("pin", 5), ("delete", 32), ("pin", 33), ("delete", 33),
                                        ("pin", None), ("delete", None)])
def test_only_the_refusal_of_a_held_shortcut_is_tried_again(uninstaller, monkeypatch, place, code):
    # Error 5 at the pin and error 32 at the delete are not what a short hold gives. A PermissionError
    # without a Windows code is the product's own refusal (an owner, an access rule, a replaced file).
    state, consumer, waits = uninstaller.state, uninstaller.consumer, []
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def refuse(name):
        if name == "Amplifier status.lnk":
            raise held(code) if code else PermissionError("Windows private files require trusted ownership")
    another_program(state, place, refuse)
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == ("entries-check" if place == "pin" else "shortcuts")
    assert isinstance(stopped.value.error, PermissionError) and not waits


@pytest.mark.parametrize("change", ["bytes", "binding"])
def test_changed_shortcut_refuses_at_once_while_another_shortcut_was_held(uninstaller, monkeypatch, change):
    state, folder, consumer, waits = uninstaller.state, uninstaller.folder, uninstaller.consumer, []
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    first, changed = list(shell.LINKS)[0], list(shell.LINKS)[2]
    if change == "bytes":
        (folder / changed).write_bytes(b"changed after setup")
    else:
        state["links"][str(folder / changed)] = ("other.exe", "")
    def refuse(name):
        if name == first and len(waits) < 2:
            raise held(32)
    another_program(state, "pin", refuse)
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    # The wait for the first shortcut ended. The changed one then refuses with no further wait.
    assert stopped.value.step == "entries-check" and type(stopped.value.error) is ValueError
    assert waits == [0.1] * 2 and not state["calls"] and state["task"] == "running"
    assert len(list(folder.iterdir())) == 4 and not state["pins"]


def test_app_left_running_without_entries_by_the_earlier_order_can_be_uninstalled(uninstaller):
    # What clean-room runs 2 and 3 left behind: no entry, receipt "installed", app running.
    state, folder = uninstaller.state, uninstaller.folder
    for path in folder.iterdir():
        path.unlink()
    state["registry"] = None
    assert uninstaller.run()["ok"]
    assert state["calls"] == [("stop",), ("task-remove",)] and state["task"] == "absent"
    assert json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "removed"


def test_folder_that_refuses_writes_while_the_app_runs_no_longer_strands_it(uninstaller):
    # Clean-room run 3 (worker Foundation before e37a946b): while a conversation
    # worker lived, Windows refused the private write of a new or replaced file
    # directly in the installation folder. The earlier order
    # met that refusal after it had removed every entry. Workers end with the app.
    state, root = uninstaller.state, uninstaller.root
    state["refuse_write"] = lambda path: path.parent == root and state["task"] == "running"
    assert uninstaller.run()["ok"]
    assert state["calls"][:3] == [("stop",), ("task-remove",), ("registry-remove",)]
    assert state["task"] == "absent" and state["registry"] is None and not uninstaller.folder.exists()
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    assert not saved_record(root).exists()


def test_failed_stop_is_recorded_although_the_folder_refuses_writes(uninstaller):
    state, root, consumer = uninstaller.state, uninstaller.root, uninstaller.consumer
    registration = state["registry"].copy()
    state["refuse_write"] = lambda path: path.parent == root and state["task"] == "running"
    state["task_failure"] = "stop"
    with pytest.raises(consumer.Unfinished):
        uninstaller.run()
    assert state["task"] == "running" and state["registry"] == registration
    assert len(list(uninstaller.folder.iterdir())) == 4
    # The note is beside the service records, which accept writes while the app runs.
    assert json.loads(saved_record(root).read_text())["step"] == "stop"
    assert not (root / consumer.RECORD).exists()


def test_refused_receipt_write_does_not_fail_a_finished_uninstall(uninstaller, monkeypatch):
    state, root, consumer = uninstaller.state, uninstaller.root, uninstaller.consumer
    state["refuse_write"] = lambda path: path.parent == root
    monkeypatch.setattr(consumer.sys, "stdout", None)
    assert uninstaller.run()["ok"]
    assert state["task"] == "absent" and state["registry"] is None and not uninstaller.folder.exists()
    record = json.loads(saved_record(root).read_text())
    assert (record["step"], record["errorType"]) == ("receipt", "PermissionError")
    assert not (root / consumer.RECORD).exists()
    # Windowless: one question, then exactly one confirmation that names the folder.
    assert [confirm for _, confirm in state["notices"]] == [True, False]
    assert "Amplifier is uninstalled" in state["notices"][1][0] and str(root) in state["notices"][1][0]


@pytest.mark.parametrize("working,answer", [(1, False), (3, False), (2, True), (0, None), (None, None)])
def test_running_chat_is_named_and_the_person_chooses_to_wait_or_stop(uninstaller, working, answer):
    state = uninstaller.state
    state["working"] = working
    state["answers"] = [True] + ([answer] if answer is not None else [])
    result = uninstaller.run()
    questions = [text for text, confirm in state["notices"] if confirm]
    if answer is None:
        assert len(questions) == 1 and result.get("operation") == "uninstall"
        return
    assert len(questions) == 2
    assert ("working on a chat right now" if working == 1 else f"working on {working} chats") in questions[1]
    assert "Select No to keep Amplifier installed" in questions[1]
    if answer:
        assert result["operation"] == "uninstall" and state["task"] == "absent"
    else:
        assert result == {"ok": True, "cancelled": True}
        assert not state["calls"] and state["task"] == "running" and state["registry"] is not None
        assert len(list(uninstaller.folder.iterdir())) == 4


def test_declined_first_question_changes_nothing(uninstaller):
    state = uninstaller.state
    state["answers"], state["working"] = [False], 5
    assert uninstaller.run() == {"ok": True, "cancelled": True}
    assert not state["calls"] and len(state["notices"]) == 1 and state["registry"] is not None


@pytest.mark.parametrize("windowless", [True, False])
def test_unfinished_uninstall_is_one_plain_notice_or_one_json_line(uninstaller, monkeypatch, capsys, windowless):
    consumer, state = uninstaller.consumer, uninstaller.state
    state["task_failure"] = "stop"
    if windowless:
        monkeypatch.setattr(consumer.sys, "stdout", None)
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(uninstaller.root), "--uninstall"])
    assert stopped.value.code == 1
    shown = [text for text, confirm in state["notices"] if not confirm]
    if windowless:
        assert len(shown) == 1 and "it did not stop" in shown[0] and "Reference: stop, WindowsServiceError" in shown[0]
        assert "synthetic-private-detail" not in shown[0]
    else:
        assert not shown
        assert json.loads(capsys.readouterr().out) == {"ok": False, "step": "stop",
                                                        "errorType": "WindowsServiceError"}


def test_every_step_that_can_fail_has_its_own_plain_text():
    from amplifier_web import windows_consumer as consumer
    for step in ("entries-check", "task-read", "stop", "task-remove", "apps-entry", "shortcuts"):
        notice = consumer.Unfinished(step, OSError(5, "private detail")).notice
        assert "uninstall again" in notice.lower() and f"Reference: {step}, OSError" in notice
        assert "private detail" not in notice and "evidence" not in notice
    # A Windows error code is part of the reference; the error text never is.
    refused = OSError("private detail")
    refused.winerror = 32
    assert consumer.Unfinished("shortcuts", refused).notice.endswith("Reference: shortcuts, OSError 32")


def test_failure_record_tries_the_service_folder_first_and_never_raises(tmp_path, monkeypatch):
    from amplifier_web import deployment, windows_consumer as consumer
    written = []
    def refuse(path, raw, **kw):
        written.append(path)
        raise PermissionError(5, "Access is denied", str(path))
    monkeypatch.setattr(deployment, "write_private", refuse)
    try:
        raise OSError(32, "used by another process", "C:\\secret\\place")
    except OSError as error:
        assert consumer._record(tmp_path, "uninstall", "receipt", error) is False
    assert written == [tmp_path / "app/config/services" / consumer.RECORD, tmp_path / consumer.RECORD]


def _install_native_entries(root):
    """Install the real entries in a throwaway folder. No app is started."""
    from amplifier_web import windows_files
    python, _ = shell.entries(root)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"MZ")  # A shortcut target must exist. Nothing starts it.
    shell.install(root, "1.0.0")
    # A real installation has this folder before its app first runs.
    with windows_files.directory(root / "app" / "config" / "services", owned_root=root, create=True):
        pass
    return shell.registry(root)


def test_native_uninstall_order_failure_and_second_uninstall(monkeypatch):
    """The real Windows entries of a throwaway installation folder.

    Real: ShellLink COM, the per-user Start-menu folder, the HKCU registry
    transaction, pinned private files and NTFS sharing. Stand-ins: Task
    Scheduler (a runner cannot register the sign-in task), the dialogs and the
    installation receipt check. No app is started.

    While the stand-in app "runs", the test holds the installation folder open
    for listing without write sharing. That kind of handle gives the refusal
    that the clean room measured with a live conversation worker (a rename into
    the folder fails with WinError 32). The real owner was Foundation's session
    ownership before revision e37a946b, and a worker that still runs such a
    revision gives this refusal. This is the same refusal, not the same owner;
    tests/test_windows_live_worker_folders.py holds the real ownership at the
    locked revision. The stand-in releases
    the folder about one second after its stop reports, because real worker
    programs end a moment after the task reports that it stopped.
    """
    if os.name != "nt":
        if os.environ.get("AMPLIFIER_REQUIRE_WINDOWS_SHELL_CHECKS") == "1":
            pytest.fail("This lane requires the real Windows shell checks.")
        pytest.skip("Real Start-menu shortcuts, HKCU transaction and private NTFS files")
    import ctypes
    import shutil
    import threading
    import winreg
    from amplifier_web import installation_health, windows_consumer as consumer
    from amplifier_web import windows_files, windows_installation, windows_service
    from amplifier_web.deployment import write_private
    root = windows_files.address(Path.home() / ("au-uninstall-test-" + uuid.uuid4().hex[:12]))
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    folder = shell.programs() / ("Amplifier Unified - " + shell.identity(root))
    key = shell.REGISTRY + shell.identity(root)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    held, events, failure, present, timers = [], [], [], set(shell.LINKS), []

    def hold():
        # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_SHARE_READ only,
        # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
        handle = kernel.CreateFileW(str(root), 0x100081, 1, None, 3, 0x2200000, None)
        assert handle and handle != ctypes.c_void_p(-1).value, ctypes.WinError(ctypes.get_last_error())
        held.append(handle)

    def release():
        while held:
            assert kernel.CloseHandle(held.pop())

    monkeypatch.setattr(consumer, "installation", lambda value: (windows_files.address(value), {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(windows_installation, "owner_notice", lambda text, confirm=False: True)
    async def working(home):
        return None
    monkeypatch.setattr(installation_health, "windows_working", working)
    def uninstall(home, *, progress):
        progress("task-read")
        progress("stop")
        # While the app stops, each remaining shortcut exists and real NTFS
        # sharing refuses another program that tries to change it.
        names = {path.name for path in folder.iterdir()} if folder.exists() else set()
        assert names == present
        if names:
            with pytest.raises(OSError):
                (folder / "Uninstall Amplifier.lnk").open("wb")
        if failure:
            raise windows_service.WindowsServiceError(failure[0])
        # A normal stop ends the app; its worker programs end a moment later.
        timers.append(threading.Timer(1.0, release))
        timers[-1].start()
        events.append("task")
    monkeypatch.setattr(windows_service, "uninstall", uninstall)
    try:
        registration = _install_native_entries(root)
        assert registration and {path.name for path in folder.iterdir()} == set(shell.LINKS)
        with windows_files.directory(root, owned_root=root) as owned:
            receipt = owned.read_private(shell.FILE, limit=65536)
        # 1. The "app runs": the folder refuses a replaced file, as in the clean room.
        hold()
        with pytest.raises(PermissionError) as refused:
            write_private(root / shell.FILE, receipt, owned_root=root)
        assert refused.value.winerror == 32
        # 2. The stop fails: nothing is removed, and the step is recorded privately
        #    beside the service records although the folder refuses writes.
        failure.append("Task still has a running instance; no force-stop attempted.")
        with pytest.raises(consumer.Unfinished) as stopped:
            asyncio.run(consumer.maintenance(root, "uninstall"))
        assert stopped.value.step == "stop" and not events
        assert "it did not stop" in stopped.value.notice and "Task still has" not in stopped.value.notice
        assert shell.registry(root) == registration
        assert {path.name for path in folder.iterdir()} == set(shell.LINKS)
        with windows_files.directory(root / "app" / "config" / "services", owned_root=root) as owned:
            record = json.loads(owned.read_private(consumer.RECORD, limit=65536))
        assert (record["step"], record["errorType"]) == ("stop", "WindowsServiceError")
        with windows_files.directory(root, owned_root=root) as owned:
            assert owned.read_private(shell.FILE, limit=65536) == receipt
        # 3. An interrupted removal took one shortcut and the Apps-list entry away.
        with windows_files.directory(folder, owned_root=folder) as owned:
            name = "Amplifier status.lnk"
            with owned.removal_private(name, hashlib.sha256(owned.read_private(name, limit=65536)).hexdigest()) as drop:
                drop()
        present.discard(name)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        # 4. The second uninstall completes: the stop first, then the remaining
        #    entries and the emptied Start-menu folder. The receipt is written
        #    although the folder is still held when the stop reports: the last
        #    step waits for the closing programs.
        failure.clear()
        result = asyncio.run(consumer.maintenance(root, "uninstall"))
        assert result["ok"] and events == ["task"] and not held
        # This throwaway folder has no certificate: the real read finds none and
        # Windows is never asked. No trust store is changed by this test.
        assert result["certificate"] == "none"
        assert shell.registry(root) is None and not folder.exists()
        with windows_files.directory(root, owned_root=root) as owned:
            assert json.loads(owned.read_private(shell.FILE, limit=65536))["status"] == "removed"
        with windows_files.directory(root / "app" / "config" / "services", owned_root=root) as owned:
            # No new note: the wait succeeded, so the earlier "stop" note is still the only one.
            assert json.loads(owned.read_private(consumer.RECORD, limit=65536))["step"] == "stop"
        # 5. Asking again changes nothing and is not an error.
        present.clear()
        assert asyncio.run(consumer.maintenance(root, "uninstall"))["ok"] and events == ["task", "task"]
        # 6. Setup restores the real entries over the completed removal receipt.
        assert not shell.listed(root)
        restored = shell.install(root, "1.0.0")
        assert restored["shortcuts"] == 4 and restored["installedAppEntry"] and shell.listed(root)
        assert {path.name for path in folder.iterdir()} == set(shell.LINKS)
        python, links = shell.entries(root)
        for name, args in links.items():
            with shell.shell_link() as link:
                assert link.read(folder / name) == (str(python), args)
        assert shell.registry(root) == registration
        with windows_files.directory(root, owned_root=root) as owned:
            assert json.loads(owned.read_private(shell.FILE, limit=65536))["status"] == "installed"
        # 7. The public uninstall removes the restored entries for cleanup.
        present.update(shell.LINKS)
        assert asyncio.run(consumer.maintenance(root, "uninstall"))["ok"]
        assert not shell.listed(root) and shell.registry(root) is None and not folder.exists()
    finally:
        # Only this test's own throwaway objects, by their exact names.
        for timer in timers:
            timer.cancel()
            timer.join()
        release()
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def native_uninstall(monkeypatch):
    """The public uninstall over the real Windows entries of a throwaway installation folder.

    Real: ShellLink COM, the per-user Start-menu folder, the HKCU registry
    transaction, pinned private files and NTFS sharing. Stand-ins: Task
    Scheduler, the dialogs and the installation receipt check. No app is
    started. `waits` holds each wait that the uninstall asked for; the wait
    itself is real. `at_wait[n]` is called at the n-th wait of a tenth of a
    second: what another program does at that moment.
    """
    if os.name != "nt":
        if os.environ.get("AMPLIFIER_REQUIRE_WINDOWS_SHELL_CHECKS") == "1":
            pytest.fail("This lane requires the real Windows shell checks.")
        pytest.skip("Real Start-menu shortcuts, NTFS sharing and a mapped view of a file")
    import shutil
    import winreg
    from amplifier_web import installation_health, windows_consumer as consumer
    from amplifier_web import windows_files, windows_installation, windows_service
    root = windows_files.address(Path.home() / ("au-held-test-" + uuid.uuid4().hex[:12]))
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    folder = shell.programs() / ("Amplifier Unified - " + shell.identity(root))
    events, waits, at_wait, sleep = [], [], {}, time.sleep
    monkeypatch.setattr(consumer, "installation", lambda value: (windows_files.address(value), {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(windows_installation, "owner_notice", lambda text, confirm=False: True)
    async def working(home):
        return None
    monkeypatch.setattr(installation_health, "windows_working", working)
    def uninstall(home, *, progress):
        for step in ("task-read", "stop", "task-remove"):
            progress(step)
        events.append("stop")
    monkeypatch.setattr(windows_service, "uninstall", uninstall)
    def wait(seconds):
        waits.append(seconds)
        if seconds == 0.1 and waits.count(0.1) in at_wait:
            at_wait.pop(waits.count(0.1))()
        sleep(seconds)
    monkeypatch.setattr(shell.time, "sleep", wait)
    try:
        registration = _install_native_entries(root)
        assert registration and {path.name for path in folder.iterdir()} == set(shell.LINKS)
        yield SimpleNamespace(root=root, folder=folder, registration=registration, events=events, waits=waits,
                              at_wait=at_wait,
                              consumer=consumer, run=lambda: asyncio.run(consumer.maintenance(root, "uninstall")))
    finally:
        # Only this test's own throwaway objects, by their exact names.
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, shell.REGISTRY + shell.identity(root))
        except OSError:
            pass
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


def _another_program_holds(path, *, mapped):
    """What another program can do with a shortcut for a moment. Returns the function that ends it.

    Not mapped: an open for reading that shares reading and writing and does
    not share deleting. Many programs open a file like that. Mapped: a view of
    the file. The program closed its handles and kept the view.

    Windows keeps sharing for each open of a file and a view for each mapping,
    not for each process. So this process's own second open and its own view
    are refused exactly like another program's.
    """
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileMappingW.restype = ctypes.c_void_p
    kernel.CreateFileMappingW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                          ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.MapViewOfFile.restype = ctypes.c_void_p
    kernel.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_size_t]
    kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # GENERIC_READ; FILE_SHARE_READ | FILE_SHARE_WRITE, and for the view also FILE_SHARE_DELETE; OPEN_EXISTING.
    handle = kernel.CreateFileW(str(path), 0x80000000, 7 if mapped else 3, None, 3, 0, None)
    assert handle and handle != ctypes.c_void_p(-1).value, ctypes.WinError(ctypes.get_last_error())
    view = None
    if mapped:
        mapping = kernel.CreateFileMappingW(handle, None, 2, 0, 0, None)  # PAGE_READONLY
        assert mapping, ctypes.WinError(ctypes.get_last_error())
        view = kernel.MapViewOfFile(mapping, 4, 0, 0, 0)  # FILE_MAP_READ
        assert view, ctypes.WinError(ctypes.get_last_error())
        assert kernel.CloseHandle(mapping) and kernel.CloseHandle(handle)
    once = [True]

    def release():
        if once:
            once.clear()
            assert kernel.UnmapViewOfFile(view) if mapped else kernel.CloseHandle(handle)
    return release


@pytest.mark.parametrize("mapped", [False, True], ids=["open-without-delete-sharing", "mapped-view"])
def test_native_shortcut_that_another_program_holds_is_waited_for_and_the_wait_has_an_end(
        native_uninstall, monkeypatch, mapped):
    """The two stops that hosted Windows runners showed, made on purpose with real NTFS handles.

    An open without delete sharing refuses the uninstall's pin: Windows error
    32 at `entries-check`, and nothing is changed. A mapped view lets the pin
    pass and refuses the delete: Windows error 5 at `shortcuts`, after the app
    stopped and the Apps-list entry went.

    1. The shortcut stays held: the uninstall waits its bounded time, then
       stops with that refusal.
    2. The other program lets go at the third wait of the next uninstall:
       that uninstall waits three times and finishes.
    """
    native, consumer = native_uninstall, native_uninstall.consumer
    name = "Amplifier status.lnk"  # The second of the four in the order of removal.
    step, code = ("shortcuts", 5) if mapped else ("entries-check", 32)
    longest = shell.HOLD_SECONDS
    release = _another_program_holds(native.folder / name, mapped=mapped)
    try:
        monkeypatch.setattr(shell, "HOLD_SECONDS", 1)
        began = time.monotonic()
        with pytest.raises(consumer.Unfinished) as stopped:
            native.run()
        seconds = round(time.monotonic() - began, 2)  # The whole uninstall, with its ten waits.
        left = sorted(path.name for path in native.folder.iterdir())
        # The workflow reads the first three keys of this line, in this order.
        seen = {"step": stopped.value.step, "error": type(stopped.value.error).__name__,
                "code": getattr(stopped.value.error, "winerror", None), "waits": native.waits.count(0.1),
                "events": list(native.events), "left": left, "appsEntry": shell.registry(native.root) is not None,
                "seconds": seconds}
        print("UNINSTALL_HELD " + json.dumps(seen))
        assert (seen["step"], seen["error"], seen["code"]) == (step, "PermissionError", code), seen
        if mapped:
            # The app has stopped and the Apps-list entry is gone. The shortcut before the held one is
            # gone. The held one and every later one stay: the person can still uninstall again.
            assert seen["events"] == ["stop"] and not seen["appsEntry"], seen
            assert left == sorted(list(shell.LINKS)[1:]) and "Uninstall Amplifier.lnk" in left, seen
            assert "Uninstall again to finish." in stopped.value.notice
        else:
            # Nothing was changed, and the app was not asked to stop.
            assert not seen["events"] and shell.registry(native.root) == native.registration, seen
            assert left == sorted(shell.LINKS), seen
            assert stopped.value.notice.startswith("This uninstall stopped before it changed anything.\n\n")
        # One second: one attempt, then ten more, a tenth of a second apart.
        assert seen["waits"] == 10, seen
        # The other program lets go at the third wait of the next uninstall.
        monkeypatch.setattr(shell, "HOLD_SECONDS", longest)
        del native.waits[:]
        native.at_wait[3] = release
        result = native.run()
        assert result["ok"] and result["certificate"] == "none", result
        assert native.waits.count(0.1) == 3 and not native.at_wait, native.waits
        assert shell.registry(native.root) is None and not native.folder.exists()
        from amplifier_web import windows_files
        with windows_files.directory(native.root, owned_root=native.root) as owned:
            assert json.loads(owned.read_private(shell.FILE, limit=65536))["status"] == "removed"
    finally:
        release()


def test_native_held_shortcut_that_changes_during_the_wait_refuses_when_the_hold_ends(native_uninstall):
    """The held shortcut itself gets one more byte at the second wait, and the other program lets go at the third.

    The next attempt of the pin opens the shortcut, reads its bytes again and
    refuses them: nothing is removed, and the app is not asked to stop.
    """
    native, consumer = native_uninstall, native_uninstall.consumer
    path = native.folder / "Amplifier status.lnk"
    release = _another_program_holds(path, mapped=False)  # It shares writing, so the file can change.
    def change():
        with open(path, "ab") as stream:
            stream.write(b"\0")
    try:
        native.at_wait[2], native.at_wait[3] = change, release
        with pytest.raises(consumer.Unfinished) as stopped:
            native.run()
        assert stopped.value.step == "entries-check" and type(stopped.value.error) is ValueError, stopped.value.error
        assert native.waits.count(0.1) == 3 and not native.at_wait and not native.events
        assert shell.registry(native.root) == native.registration
        assert {entry.name for entry in native.folder.iterdir()} == set(shell.LINKS)
    finally:
        release()


@pytest.mark.parametrize("change", ["bytes", "target"])
def test_native_changed_shortcut_still_refuses_at_once(native_uninstall, change):
    """A shortcut that differs from setup's record is not a held shortcut: no wait, and nothing is removed."""
    native, consumer = native_uninstall, native_uninstall.consumer
    path = native.folder / "Amplifier status.lnk"
    if change == "bytes":
        path.write_bytes(path.read_bytes() + b"\0")
    else:
        python, links = shell.entries(native.root)
        other = python.with_name("other.exe")
        other.write_bytes(b"MZ")  # A shortcut target must exist. Nothing starts it.
        with shell.shell_link() as link:
            link.create(path, other, links[path.name], native.root / "workspace")
    with pytest.raises(consumer.Unfinished) as stopped:
        native.run()
    assert stopped.value.step == "entries-check" and type(stopped.value.error) is ValueError, stopped.value.error
    assert 0.1 not in native.waits and not native.events
    assert shell.registry(native.root) == native.registration
    assert {path.name for path in native.folder.iterdir()} == set(shell.LINKS)


def _native_record(root):
    from amplifier_web import windows_files
    with windows_files.directory(root, owned_root=root) as owned:
        return json.loads(owned.read_private(shell.FILE, limit=65536))


def _native_entries(folder):
    """What is at the Start-menu folder's path: each entry directly in it, with a file's bytes and time of change."""
    if os.path.isjunction(folder):
        return ["a link to", os.readlink(folder)]
    if folder.is_file():
        return ["a file", folder.read_bytes(), folder.stat().st_mtime_ns]
    return sorted((path.name, "folder" if path.is_dir() else "file",
                   None if path.is_dir() else (path.read_bytes(), path.stat().st_mtime_ns))
                  for path in folder.iterdir())


def test_native_entries_are_added_again_beside_a_file_that_the_person_put_into_the_start_menu_folder(
        native_uninstall):
    """A second setup run after an uninstall that left the Start-menu folder, on the real folder.

    A file that the person put into Amplifier's Start-menu folder keeps that
    folder at an uninstall. Setup's step for the entries then adds the four
    shortcuts and the Apps-list entry again, beside that file. The file keeps
    its name, its bytes and its time of change, here and at the next uninstall.
    """
    native, root, folder = native_uninstall, native_uninstall.root, native_uninstall.folder
    (folder / "My own note.txt").write_bytes(b"the person's own file")
    mine = [row for row in _native_entries(folder) if row[0] not in shell.LINKS]
    assert [row[0] for row in mine] == ["My own note.txt"]
    assert native.run()["ok"]
    # The uninstall finished, and the folder stays with the person's file alone.
    assert _native_entries(folder) == mine and shell.registry(root) is None
    assert _native_record(root)["status"] == "removed" and not shell.listed(root)
    result = shell.install(root, "1.0.0")
    python, links = shell.entries(root)
    for name, args in links.items():
        with shell.shell_link() as link:
            assert link.read(folder / name) == (str(python), args)
    after = _native_entries(folder)
    # The workflow reads this line from its start to the end of "unchanged".
    seen = {"shortcuts": result["shortcuts"], "appsEntry": shell.registry(root) == native.registration,
            "kept": [row[0] for row in after if row[0] not in shell.LINKS],
            "unchanged": [row for row in after if row[0] not in shell.LINKS] == mine,
            "listed": shell.listed(root), "record": _native_record(root)["status"]}
    print("ENTRIES_AGAIN " + json.dumps(seen))
    assert seen == {"shortcuts": 4, "appsEntry": True, "kept": ["My own note.txt"], "unchanged": True,
                    "listed": True, "record": "installed"}, seen
    assert {row[0] for row in after} == set(shell.LINKS) | {"My own note.txt"}
    # The next uninstall removes the entries again and leaves the file.
    assert native.run()["ok"]
    assert _native_entries(folder) == mine and shell.registry(root) is None


@pytest.mark.parametrize("there", ["shortcut-name-in-other-letter-case", "folder-with-a-shortcut-name",
                                   "three-of-the-four-shortcut-names", "link-to-another-folder", "link-to-nothing",
                                   "file-at-the-folder-path", "folder-that-everyone-can-change", "apps-list-entry"])
def test_native_entries_are_not_added_where_one_of_them_or_something_foreign_is_there(native_uninstall, there):
    """Each of these refuses on the real folder, and nothing is written: no shortcut, no record, no Apps-list entry."""
    import _winapi
    import subprocess
    from amplifier_web import windows_files
    native, root, folder = native_uninstall, native_uninstall.root, native_uninstall.folder
    assert native.run()["ok"] and not folder.exists() and shell.registry(root) is None
    elsewhere = root / "elsewhere"
    with windows_files.directory(elsewhere, owned_root=root, create=True):
        pass
    (elsewhere / "theirs.txt").write_bytes(b"theirs")
    try:
        if there == "link-to-another-folder":
            _winapi.CreateJunction(str(elsewhere), str(folder))
        elif there == "link-to-nothing":
            gone = root / "gone"
            gone.mkdir()
            _winapi.CreateJunction(str(gone), str(folder))
            gone.rmdir()
            assert os.path.lexists(folder) and not folder.exists()
        elif there == "file-at-the-folder-path":
            folder.write_bytes(b"a file, not a folder")
        else:
            # The folder as the first installation makes it: this account's own private folder.
            with windows_files.directory(folder, owned_root=folder, create=True):
                pass
            (folder / "My own note.txt").write_bytes(b"mine")
        if there == "shortcut-name-in-other-letter-case":
            (folder / "AMPLIFIER Status.LNK").write_bytes(b"not this installation's")
        elif there == "folder-with-a-shortcut-name":
            (folder / "Uninstall Amplifier.lnk").mkdir()
        elif there == "three-of-the-four-shortcut-names":
            # As after an installation folder that was deleted by hand, and one shortcut with it.
            for name in list(shell.LINKS)[1:]:
                (folder / name).write_bytes(b"of an earlier installation")
        elif there == "folder-that-everyone-can-change":
            # The group Everyone (S-1-1-0) gets the right to change the folder: it is not private any more.
            granted = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"), str(folder),
                                      "/grant", "*S-1-1-0:(OI)(CI)M"], capture_output=True, text=True, timeout=60)
            assert granted.returncode == 0, granted.stdout[-500:] + granted.stderr[-500:]
            with pytest.raises(PermissionError):
                with windows_files.directory(folder, owned_root=folder):
                    pass
        elif there == "apps-list-entry":
            shell.registry(root, shell.registration_values(root, "1.0.0"))
        before = _native_record(root), shell.registry(root), _native_entries(folder), _native_entries(elsewhere)
        assert before[0]["status"] == "removed"
        with pytest.raises(ValueError) as refused:
            shell.install(root, "1.0.0")
        assert str(refused.value) == EXIST
        assert (_native_record(root), shell.registry(root), _native_entries(folder),
                _native_entries(elsewhere)) == before
    finally:
        if os.path.isjunction(folder):
            os.rmdir(folder)  # The link itself, never its target.
        elif folder.is_file():
            folder.unlink()


def test_native_set_from_which_a_person_deleted_one_shortcut_is_refused_by_its_record(native_uninstall):
    """The record says that the entries are installed, and three of them are there: nothing is added or changed."""
    native, root, folder = native_uninstall, native_uninstall.root, native_uninstall.folder
    (folder / "Amplifier status.lnk").unlink()
    before = _native_record(root), shell.registry(root), _native_entries(folder)
    assert before[0]["status"] == "installed" and len(before[2]) == 3
    with pytest.raises(ValueError) as refused:
        shell.install(root, "1.0.0")
    assert str(refused.value) == "Windows shell integration already has a receipt; inspect it before retrying."
    assert (_native_record(root), shell.registry(root), _native_entries(folder)) == before


@pytest.mark.parametrize("made", ["as-a-private-file-of-this-account", "as-a-plain-file"])
def test_native_shortcut_name_that_appears_after_the_check_is_not_overwritten(native_uninstall, monkeypatch, made):
    """The real writer refuses a name that exists: a file that appears after the look keeps its bytes.

    Another program makes a file with a shortcut's name in the instant after
    setup's step looked at the folder and before it writes that shortcut. The
    step stops with the writer's refusal. It adds no Apps-list entry, and its
    record does not say that the entries are installed.

    The other program's file is made in two ways. As a private file of this
    account (its owner is this account, as for a person's own program on a
    PC): the writer answers that the name exists. As a plain file: the writer
    can refuse that file at one of its own checks of an existing file before
    it answers that the name exists. Both are the writer's refusals, and
    neither carries a Windows error code. The case prints which refusal it
    got, with the refusal's text.
    """
    from amplifier_web import windows_files
    native, root, folder = native_uninstall, native_uninstall.root, native_uninstall.folder
    (folder / "My own note.txt").write_bytes(b"mine")
    assert native.run()["ok"] and [path.name for path in folder.iterdir()] == ["My own note.txt"]
    name, theirs = "Amplifier status.lnk", b"another program's file"
    publish = windows_files.PinnedDirectory.publish_private
    def appears(self, entry, content, *, replace=False):
        if entry == name and not (folder / name).exists():
            if made == "as-a-plain-file":
                (folder / name).write_bytes(theirs)
            else:
                publish(self, name, theirs)
        return publish(self, entry, content, replace=replace)
    monkeypatch.setattr(windows_files.PinnedDirectory, "publish_private", appears)
    with pytest.raises((FileExistsError, PermissionError)) as refused:
        shell.install(root, "1.0.0")
    print("NAME_APPEARED " + json.dumps({"made": made, "refusal": type(refused.value).__name__,
                                         "code": getattr(refused.value, "winerror", None),
                                         "text": str(refused.value).replace(str(folder), "<folder>")}))
    assert getattr(refused.value, "winerror", None) is None, refused.value  # Not a sharing refusal of Windows.
    if made != "as-a-plain-file":
        assert type(refused.value) is FileExistsError, refused.value
    assert (folder / name).read_bytes() == theirs and (folder / "My own note.txt").read_bytes() == b"mine"
    assert sorted(path.name for path in folder.iterdir()) == sorted(
        ["My own note.txt", name, next(iter(shell.LINKS))])  # The first shortcut was written before the stop.
    assert shell.registry(root) is None and not shell.listed(root)
    assert _native_record(root)["status"] != "installed"


def test_native_file_put_at_a_new_shortcut_path_before_the_shell_saves_is_written_over(native_uninstall, monkeypatch):
    """The first moment that setup's step does not cover, on real files. A known limit, not a promise.

    The writer makes each shortcut as a new, empty file, and the Windows shell
    then saves the shortcut to that file's path. Here another program of this
    account replaces the new file between the two. It uses the private writer,
    so that its file is this account's own, as a file of a person's program is
    on a PC. The shell saves to the path, and the other program's bytes are
    gone. A change that closes this moment changes this case and the sentence
    of the design that names the moment.
    """
    from amplifier_web import windows_files
    native, root, folder = native_uninstall, native_uninstall.root, native_uninstall.folder
    assert native.run()["ok"] and not folder.exists()
    name, theirs, put = "Amplifier status.lnk", b"another program's file", []
    publish = windows_files.PinnedDirectory.publish_private
    def replaced(self, entry, content, *, replace=False):
        publish(self, entry, content, replace=replace)
        if entry == name and not put:
            publish(self, name, theirs, replace=True)
            assert (folder / name).read_bytes() == theirs
            put.append(os.stat(folder / name).st_ino)
    monkeypatch.setattr(windows_files.PinnedDirectory, "publish_private", replaced)
    result = shell.install(root, "1.0.0")
    after = (folder / name).read_bytes()
    print("SAVED_BY_PATH " + json.dumps({
        "writtenOver": after != theirs, "shortcut": after[:4] == b"L\x00\x00\x00",
        "sameFileObject": os.stat(folder / name).st_ino == put[0],
        "shortcuts": result["shortcuts"], "record": _native_record(root)["status"]}))
    assert put and after != theirs and after[:4] == b"L\x00\x00\x00"


def test_native_file_put_at_a_saved_shortcut_path_before_the_record_reads_it_is_recorded(native_uninstall, monkeypatch):
    """The second moment that setup's step does not cover, on real files. A known limit, not a promise.

    After the Windows shell saved a shortcut and read its target back, the
    step reads the shortcut's bytes by its name for its record. Here another
    program of this account replaces the saved shortcut between the two, with
    a private file of its own. The record then holds the hash of that file as
    this installation's shortcut. A change that closes this moment changes
    this case and the sentence of the design that names the moment.
    """
    from amplifier_web import windows_files
    native, root, folder = native_uninstall, native_uninstall.root, native_uninstall.folder
    assert native.run()["ok"] and not folder.exists()
    name, theirs, saved = "Amplifier status.lnk", b"another program's file", []
    read = windows_files.PinnedDirectory.read_private
    publish = windows_files.PinnedDirectory.publish_private
    def replaced(self, entry, *, limit):
        if entry == name and not saved:
            saved.append(read(self, name, limit=limit))  # The shortcut that this run saved.
            publish(self, name, theirs, replace=True)
        return read(self, entry, limit=limit)
    monkeypatch.setattr(windows_files.PinnedDirectory, "read_private", replaced)
    result = shell.install(root, "1.0.0")
    monkeypatch.setattr(windows_files.PinnedDirectory, "read_private", read)
    record = _native_record(root)
    seen = {"savedWasAShortcut": bool(saved) and saved[0][:4] == b"L\x00\x00\x00",
            "recordedTheirs": record["links"][name] == hashlib.sha256(theirs).hexdigest(),
            "fileIsTheirs": (folder / name).read_bytes() == theirs,
            "shortcuts": result["shortcuts"], "record": record["status"]}
    print("RECORDED_BY_NAME " + json.dumps(seen))
    assert seen == {"savedWasAShortcut": True, "recordedTheirs": True, "fileIsTheirs": True,
                    "shortcuts": 4, "record": "installed"}, seen
