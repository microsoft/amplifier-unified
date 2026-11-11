"""Portable ownership/composition controls; actual COM/HKCU checks are separate."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import windows_consumer_shell as shell


@pytest.fixture
def model(tmp_path, monkeypatch):
    from amplifier_web import windows_files, deployment
    root = tmp_path / "install"
    root.mkdir()
    programs = tmp_path / "Programs"
    programs.mkdir()
    state = {"registry": None, "links": {}, "calls": []}
    monkeypatch.setattr(windows_files, "address", Path)
    monkeypatch.setattr(shell, "programs", lambda: programs)
    @contextmanager
    def directory(path, **kwargs):
        if kwargs.get("create"):
            path.mkdir(parents=True, exist_ok=True)
        def read(name, limit):
            raw = (path / name).read_bytes()
            assert len(raw) <= limit
            return raw
        def remove(name, digest):
            import hashlib
            assert hashlib.sha256(read(name, 65536)).hexdigest() == digest
            state["calls"].append(("remove", name))
            (path / name).unlink()
        yield SimpleNamespace(read_private=read, remove_private=remove)
    monkeypatch.setattr(windows_files, "directory", directory)
    monkeypatch.setattr(deployment, "write_private", lambda path, raw, **kw: path.write_text(raw))
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
        return state["registry"]
    monkeypatch.setattr(shell, "registry", registry)
    return root, programs, state


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
    assert state["registry"] is None and not list(folder.iterdir())
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    assert state["calls"][0] == ("registry-remove",)


@pytest.mark.parametrize("change", ["bytes", "binding", "receipt", "null", "registry"])
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
    with pytest.raises(ValueError, match="receipt"): shell.install(root, "1.0.0")
    assert not state["calls"]
