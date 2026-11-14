"""Link-safe folder removal: portable walk logic, and the real thing on native Windows."""
from contextlib import contextmanager
import base64
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

import pytest

from amplifier_web import windows_removal as removal


class Tree:
    """An in-memory folder with the five native steps of the walk.

    A node is {"kind": "folder"|"file"|"link", "children": {}, "held": bool,
    "attributes": listed attributes}. Opens are recorded so that a test can
    show that a link's target was never opened.
    """
    def __init__(self, root):
        self.rootnode, self.handles, self.opened, self.listed, self.before_open = root, {}, [], [], None

    def root(self, path):
        self.handles[1] = (self.rootnode, None, None, False)
        return 1

    def close(self, handle):
        node, parent, name, doomed = self.handles.pop(handle)
        if doomed and parent is not None and parent["children"].get(name) is node:
            del parent["children"][name]

    def children(self, handle):
        node = self.handles[handle][0]
        assert node["kind"] == "folder", "only a real folder is ever listed"
        self.listed.append(node)
        return [(name, child.get("attributes", {"folder": 0x10, "file": 0x20, "link": 0x410}[child["kind"]]))
                for name, child in node["children"].items()]

    def open(self, parent, name, *, enter, delete=True):
        if self.before_open:
            self.before_open(self.handles[parent][0], name)
        node = self.handles[parent][0]["children"].get(name)
        if node is None:
            raise FileNotFoundError(2, "gone")
        if node.get("held") and delete:
            raise PermissionError(13, "in use")
        self.opened.append((name, enter, delete))
        handle = max(self.handles) + 1
        self.handles[handle] = (node, self.handles[parent][0], name, False)
        return handle

    def kind(self, handle):
        return self.handles[handle][0]["kind"]

    def delete(self, handle):
        node, parent, name, _ = self.handles[handle]
        if node["kind"] == "folder" and node["children"]:
            raise OSError(145, "not empty")
        if node.get("mapped"):
            raise PermissionError(5, "a running program")
        self.handles[handle] = (node, parent, name, True)


def folder(_held=False, **children):
    return {"kind": "folder", "children": children, "held": _held}


def file(**extra):
    return {"kind": "file", **extra}


def link(target):
    return {"kind": "link", "target": target}


@pytest.fixture
def opened(monkeypatch, tmp_path):
    from amplifier_web import windows_files
    state = {}
    @contextmanager
    def directory(path, **kwargs):
        assert kwargs == {"owned_root": path}
        state["checked"] = path
        yield None
    monkeypatch.setattr(windows_files, "address", Path)
    monkeypatch.setattr(windows_files, "directory", directory)
    def use(root):
        tree = Tree(root)
        monkeypatch.setattr(removal, "_Native", lambda: tree)
        return tree
    state["use"] = use
    state["path"] = tmp_path
    return state


def test_removes_named_entries_with_everything_in_them_and_nothing_else(opened):
    root = folder(app=folder(chats=folder(a=file(), b=folder(c=file())), db=file()), shared=folder(keys=file()),
                  mine=file(), APP=folder(other=file()))
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        assert owned.names() == ["APP", "app", "mine", "shared"]
        result = owned.remove(["app", "shared", "absent"])
    assert result == {"removed": 8, "left": [], "leftCount": 0}
    # Exact names only: a differently spelled entry and an unnamed one stay.
    assert sorted(root["children"]) == ["APP", "mine"] and root["children"]["APP"]["children"]
    assert opened["checked"] == opened["path"] and not tree.handles


def test_link_is_removed_as_a_link_and_its_target_is_never_opened_or_listed(opened):
    outside = folder(precious=file(), more=folder(deep=file()))
    root = folder(app=folder(junction=link(outside), filelink=link(file()), plain=file()))
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        assert owned.remove(["app"])["leftCount"] == 0
    assert "app" not in root["children"]
    assert outside["children"].keys() == {"precious", "more"} and outside not in tree.listed
    assert not any(name in {"precious", "more", "deep"} for name, _, _ in tree.opened)
    # A link is opened without list access: it is never entered.
    assert ("junction", False, True) in tree.opened and ("filelink", False, True) in tree.opened


@pytest.mark.parametrize("becomes", ["link", "file"])
def test_folder_swapped_after_listing_is_judged_on_its_own_handle(opened, becomes):
    outside = folder(precious=file())
    root = folder(app=folder(cache=folder(x=file())))
    tree = opened["use"](root)
    def swap(parent, name):
        if name == "cache" and parent["children"]["cache"]["kind"] == "folder":
            parent["children"]["cache"] = link(outside) if becomes == "link" else file()
    tree.before_open = swap
    with removal.folder(opened["path"]) as owned:
        assert owned.remove(["app"])["leftCount"] == 0
    assert "app" not in root["children"] and outside["children"] == {"precious": outside["children"]["precious"]}
    assert outside not in tree.listed


def test_entry_listed_as_a_file_that_became_a_folder_is_left_not_entered(opened):
    grown = folder(inside=file())
    root = folder(app=folder(thing=file()))
    tree = opened["use"](root)
    def swap(parent, name):
        if name == "thing":
            parent["children"]["thing"] = grown
    tree.before_open = swap
    with removal.folder(opened["path"]) as owned:
        result = owned.remove(["app"])
    # Only the entry that could not go is reported; its parent stays because of it.
    assert result == {"removed": 0, "left": ["app\\thing"], "leftCount": 1}
    assert grown["children"] and grown not in tree.listed


def test_file_in_use_is_counted_and_left_and_the_rest_still_goes(opened):
    root = folder(tools=folder(a=file(), busy=file(held=True), sub=folder(running=file(mapped=True), z=file())))
    opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        result = owned.remove(["tools"])
    assert result == {"removed": 2, "left": ["tools\\busy", "tools\\sub\\running"], "leftCount": 2}
    assert sorted(root["children"]["tools"]["children"]) == ["busy", "sub"]
    assert sorted(root["children"]["tools"]["children"]["sub"]["children"]) == ["running"]


def test_folder_that_another_program_holds_is_emptied_and_left(opened):
    root = folder(temp=folder(_held=True, a=file(), b=folder(c=file())))
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        result = owned.remove(["temp"])
    assert result == {"removed": 3, "left": ["temp"], "leftCount": 1}
    assert root["children"]["temp"]["children"] == {} and ("temp", True, False) in tree.opened


def test_settle_waits_for_a_closing_program_and_then_stops(opened):
    root = folder(app=folder(db=file(held=True), x=file()))
    opened["use"](root)
    waits = []
    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            root["children"]["app"]["children"]["db"]["held"] = False
    with removal.folder(opened["path"]) as owned:
        result = owned.remove(["app"], settle=30, wait=wait)
    assert result == {"removed": 3, "left": [], "leftCount": 0} and waits == [0.5, 0.5]
    # Without time to settle there is exactly one pass.
    root["children"]["app"] = folder(db=file(held=True))
    with removal.folder(opened["path"]) as owned:
        assert owned.remove(["app"], wait=waits.append)["leftCount"] == 1 and len(waits) == 2


def test_entry_names_are_single_components(opened):
    native = object.__new__(removal._Native)
    for name in ("", ".", "..", "a\\b", "a/b", "file:stream"):
        with pytest.raises(OSError):
            removal._Native.open(native, 1, name, enter=False)


def test_many_leftovers_are_counted_in_full_and_named_in_part(opened):
    root = folder(cache=folder(**{f"f{index}": file(held=True) for index in range(50)}))
    opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        result = owned.remove(["cache"])
    assert result["leftCount"] == 50 and len(result["left"]) == 20 and result["removed"] == 0


@pytest.fixture
def finish_start(monkeypatch):
    from amplifier_web import windows_service

    class Pipe(io.BytesIO):
        def close(self):
            if not self.closed and self.getvalue():
                self.payload = json.loads(self.getvalue())
            super().close()

    class Process:
        pid = 4321

        def __init__(self):
            self.stdin = Pipe()

    calls, process = [], Process()
    monkeypatch.setattr(windows_service, "_powershell",
                        lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    def start(args, **kwargs):
        calls.append((args, kwargs))
        return process
    monkeypatch.setattr(removal.subprocess, "Popen", start)
    return calls, process, start


def test_finish_static_program_and_stdin_only_payload(finish_start):
    calls, process, _ = finish_start
    token = "data, not code: ' ` $(&exit); \u2603"
    assert removal.finish_after_exit(
        r"C:\My [private]\Amplifier", token, contents=["tools"], folders=["workspace"],
        files=["receipt.json"], wait_for=(12, 34), seconds=3, report=r"C:\proof.json",
    ) == 4321
    args, options = calls[0]
    assert args[1:5] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    assert base64.b64decode(args[-1]).decode("utf-16-le") == removal.FINISH_SCRIPT
    assert len(removal.FINISH_SCRIPT) < 9000
    assert "-recurse" not in removal.FINISH_SCRIPT.lower()
    assert "]::" not in removal.FINISH_SCRIPT
    assert re.findall(r"\$\w+\.\w+\s*\([^)]*\)", removal.FINISH_SCRIPT) == ["$entry.Delete()"]
    assert re.findall(r"\.\w+\s*\(", removal.FINISH_SCRIPT) == [".Delete("]
    assert token not in removal.FINISH_SCRIPT
    assert options["cwd"] == r"C:\Windows\System32"
    assert options["creationflags"] == 0x09000200
    assert options["stdin"] == subprocess.PIPE
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert process.stdin.closed
    assert process.stdin.payload == {
        "root": r"C:\My [private]\Amplifier", "token": token, "contents": ["tools"],
        "folders": ["workspace"], "files": ["receipt.json"], "wait_for": [12, 34],
        "seconds": 3, "report": r"C:\proof.json",
    }


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "a:b", "a\0b",
                                      "unified-uninstall.json", "UNIFIED-UNINSTALL.JSON", None, 2])
@pytest.mark.parametrize("group", ["contents", "folders", "files"])
def test_finish_refuses_the_whole_payload_before_start(finish_start, name, group):
    calls, _, _ = finish_start
    groups = dict(contents=["tools"], folders=["workspace"], files=["receipt.json"])
    groups[group].append(name)
    with pytest.raises(ValueError):
        removal.finish_after_exit(r"C:\Amplifier", "token", **groups)
    assert not calls


def test_finish_retries_only_a_refused_process_start(finish_start, monkeypatch):
    calls, _, start = finish_start
    def refuse_breakaway(args, **kwargs):
        if kwargs["creationflags"] & 0x01000000:
            calls.append((args, kwargs))
            raise OSError("Breakaway refused")
        return start(args, **kwargs)
    monkeypatch.setattr(removal.subprocess, "Popen", refuse_breakaway)
    assert removal.finish_after_exit(r"C:\Amplifier", "x", contents=[], folders=[], files=[]) == 4321
    assert [kwargs["creationflags"] for _, kwargs in calls] == [0x09000200, 0x08000200]
    assert calls[0][0] == calls[1][0]
    def refuse_both(*args, **kwargs):
        raise OSError("Cannot start")
    monkeypatch.setattr(removal.subprocess, "Popen", refuse_both)
    with pytest.raises(OSError, match="Cannot start"):
        removal.finish_after_exit(r"C:\Amplifier", "x", contents=[], folders=[], files=[])


def test_finish_reports_a_missing_system_powershell_as_an_os_error(finish_start, monkeypatch):
    from amplifier_web import windows_service

    calls, _, _ = finish_start
    def missing():
        raise windows_service.WindowsServiceError("No system folder")
    monkeypatch.setattr(windows_service, "_powershell", missing)
    with pytest.raises(OSError, match="Cannot locate Windows PowerShell"):
        removal.finish_after_exit(r"C:\Amplifier", "x", contents=[], folders=[], files=[])
    assert not calls


@pytest.mark.parametrize("temp,tmp,expected_temp,expected_tmp", [
    (r"C:\Amplifier\cache", r"c:\AMPLIFIER", r"C:\Users\Me\AppData\Local\Temp",
     r"C:\Users\Me\AppData\Local\Temp"),
    (r"C:\Amplifier-other", r"D:\temp", r"C:\Amplifier-other", r"D:\temp"),
    (r"C:\Elsewhere\..\Amplifier\cache", r"C:\Amplifier\..\outside",
     r"C:\Users\Me\AppData\Local\Temp", r"C:\Amplifier\..\outside"),
])
def test_finish_moves_only_temporary_paths_inside_root(
    finish_start, monkeypatch, temp, tmp, expected_temp, expected_tmp,
):
    calls, _, _ = finish_start
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Me\AppData\Local")
    monkeypatch.setenv("TEMP", temp)
    monkeypatch.setenv("TMP", tmp)
    before = os.environ.copy()
    removal.finish_after_exit(r"C:\Amplifier", "x", contents=[], folders=[], files=[])
    env = calls[0][1]["env"]
    assert (env["TEMP"], env["TMP"]) == (expected_temp, expected_tmp)
    assert os.environ == before and env is not os.environ


LINKS_REQUIRED = os.environ.get("AMPLIFIER_REQUIRE_LINK_CHECKS") == "1"


@pytest.mark.skipif(os.name != "nt", reason="Real NTFS links, sharing and handle-relative deletion")
def test_native_removal_never_leaves_the_folder_or_follows_a_link():
    """A real tree on NTFS with every kind of link, removed through handles.

    The outside folder stands for the rest of the person's disk. Nothing in it
    may change. No app, task or trust store is involved.
    """
    import _winapi
    from amplifier_web import windows_files
    tag = uuid.uuid4().hex[:10]
    root = windows_files.address(Path.home() / ("au-removal-test-" + tag))
    outside = Path.home() / ("au-removal-outside-" + tag)
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    links = []
    try:
        (outside / "deep").mkdir(parents=True)
        (outside / "precious.txt").write_text("keep me")
        (outside / "deep" / "also.txt").write_text("keep me too")
        (outside / "hardlinked.txt").write_text("one file, two names")
        os.chmod(outside / "hardlinked.txt", stat.S_IREAD)
        app = root / "app"
        (app / "chats" / "one").mkdir(parents=True)
        (app / "chats" / "one" / "message.txt").write_text("hello")
        (app / "readonly.txt").write_text("r")
        os.chmod(app / "readonly.txt", stat.S_IREAD)
        (root / "shared").mkdir()
        (root / "shared" / "keys.env").write_text("not a real key")
        (root / "mine.txt").write_text("not Amplifier's")
        # A path far beyond 260 characters.
        long = "\\\\?\\" + str(app / "long")
        for index in range(8):
            long += "\\" + (f"segment-{index:02d}-" + "x" * 40)
            os.mkdir(long) if index else os.makedirs(long)
        with open(long + "\\leaf.txt", "w") as stream:
            stream.write("deep")
        assert len(long) > 400
        # Links of every kind, each pointing out of the folder.
        _winapi.CreateJunction(str(outside), str(app / "junction"))
        links.append(app / "junction")
        os.link(outside / "hardlinked.txt", app / "hardlink.txt")
        symlinks = True
        try:
            os.symlink(outside, app / "folderlink", target_is_directory=True)
            links.append(app / "folderlink")
            os.symlink(outside / "precious.txt", app / "filelink.txt")
            links.append(app / "filelink.txt")
        except OSError:
            symlinks = False
        assert symlinks or not LINKS_REQUIRED, "This lane must be able to create symbolic links."
        # The links work before removal: the target is reachable through them.
        assert (app / "junction" / "precious.txt").read_text() == "keep me"

        busy = open(app / "busy.txt", "w")  # Another handle, without delete sharing.
        try:
            with removal.folder(root) as owned:
                assert owned.names() == ["app", "mine.txt", "shared"]
                # While the folder is held, other programs can still rename into it...
                (root / "probe.tmp").write_text("x")
                os.replace(root / "probe.tmp", root / "probe-2.tmp")
                os.remove(root / "probe-2.tmp")
                # ...but nothing can rename or replace the folder itself.
                with pytest.raises(OSError):
                    os.rename(root, str(root) + "-moved")
                first = owned.remove(["shared", "app"])
            assert first["left"] == ["app\\busy.txt"] and first["leftCount"] == 1
            assert sorted(path.name for path in app.iterdir()) == ["busy.txt"]
            assert not (root / "shared").exists()
        finally:
            busy.close()
        links.clear()  # All removed by the walk.
        with removal.folder(root) as owned:
            assert owned.remove(["app"], settle=5) == {"removed": 2, "left": [], "leftCount": 0}
            assert owned.names() == ["mine.txt"]
        # The unnamed entry stayed, and the folder itself is never removed here.
        assert (root / "mine.txt").read_text() == "not Amplifier's"
        # The outside folder is exactly as it was, including the read-only bit
        # of the file that had a second name inside.
        assert sorted(path.name for path in outside.iterdir()) == ["deep", "hardlinked.txt", "precious.txt"]
        assert (outside / "precious.txt").read_text() == "keep me"
        assert (outside / "deep" / "also.txt").read_text() == "keep me too"
        assert (outside / "hardlinked.txt").read_text() == "one file, two names"
        assert not os.access(outside / "hardlinked.txt", os.W_OK)
        assert os.stat(outside / "hardlinked.txt").st_nlink == 1
    finally:
        for path in links:  # A link that a failed run left: remove the link, never its target.
            try:
                os.rmdir(path) if path.is_dir() else os.remove(path)
            except OSError:
                pass
        for path in (outside / "hardlinked.txt", root / "app" / "hardlink.txt", root / "app" / "readonly.txt"):
            try:
                os.chmod(path, stat.S_IWRITE)
            except OSError:
                pass
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


@pytest.mark.skipif(os.name != "nt", reason="Real folder checks on native Windows")
def test_native_folder_refuses_a_link_and_a_folder_that_another_account_can_change(tmp_path):
    import _winapi
    from amplifier_web import windows_files
    tag = uuid.uuid4().hex[:10]
    real = windows_files.address(Path.home() / ("au-removal-real-" + tag))
    alias = Path.home() / ("au-removal-alias-" + tag)
    with windows_files.directory(real, owned_root=real, create=True):
        pass
    try:
        (real / "data.txt").write_text("stay")
        _winapi.CreateJunction(str(real), str(alias))
        with pytest.raises((OSError, ValueError)):
            with removal.folder(alias) as owned:  # A link in place of the folder.
                owned.remove(["data.txt"])
        with pytest.raises((OSError, ValueError)):
            with removal.folder(real / "absent") as owned:
                owned.remove(["data.txt"])
        assert (real / "data.txt").read_text() == "stay"
    finally:
        try:
            os.rmdir(alias)
        except OSError:
            pass
        shutil.rmtree(real, ignore_errors=True)


@pytest.fixture
def finish_tree(tmp_path):
    """Only our private throwaway tree and outside sentinel; links go first."""
    from amplifier_web import windows_files

    tag = uuid.uuid4().hex[:10]
    root = windows_files.address(Path.home() / ("au-finish-test-" + tag))
    outside = Path.home() / ("au-finish-outside-" + tag)
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    links = []
    try:
        for name in ("tools", "app", "shared", "cache"):
            (root / name / "data[1]").mkdir(parents=True)
            (root / name / "data[1]" / "one.txt").write_text("throwaway")
        (root / "workspace").mkdir()
        (root / "receipt.json").write_text("throwaway")
        (root / "unified-uninstall.json").write_text(json.dumps({"token": "Exact-TOKEN"}))
        (outside / "deep").mkdir(parents=True)
        (outside / "keep.txt").write_text("keep outside")
        (outside / "deep" / "also.txt").write_text("keep deep outside")
        yield dict(root=root, outside=outside, links=links, report=tmp_path / "finish.json")
    finally:
        for path in links:
            if os.path.lexists(path):
                # Use the link's own attributes, including for dangling links.
                if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                    os.rmdir(path)
                else:
                    os.unlink(path)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def _finish_report(path, *, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            time.sleep(0.1)
    pytest.fail("The after-exit helper wrote no complete report within its time budget.")


def _finish_options(tree):
    return dict(contents=["tools", "app", "shared", "cache"], folders=["workspace"],
                files=["receipt.json"], seconds=2, report=tree["report"])


def _assert_outside_unchanged(tree):
    outside = tree["outside"]
    assert sorted(str(path.relative_to(outside)) for path in outside.rglob("*")) == [
        "deep", str(Path("deep") / "also.txt"), "keep.txt",
    ]
    assert (outside / "keep.txt").read_text() == "keep outside"
    assert (outside / "deep" / "also.txt").read_text() == "keep deep outside"


@pytest.mark.skipif(os.name != "nt", reason="Real Windows venv redirector and after-exit helper")
def test_native_finish_survives_the_real_redirector_and_python_exit(finish_tree, tmp_path):
    tree, root = finish_tree, finish_tree["root"]
    venv = root / "tools" / "amplifier-unified"
    made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                          capture_output=True, text=True, timeout=60)
    assert made.returncode == 0, made.stderr[-1500:]
    redirector = venv / "Scripts" / "python.exe"
    evidence = tmp_path / "in-process.json"
    code = r"""
import ctypes, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from amplifier_web import windows_removal as removal
from amplifier_web.windows_service import _powershell
root, evidence, report = map(Path, sys.argv[2:5])
os.chdir(Path(_powershell()).parents[2])
in_job = ctypes.c_int()
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
kernel.IsProcessInJob.restype = ctypes.c_int
assert kernel.IsProcessInJob(ctypes.c_void_p(-1), None, ctypes.byref(in_job))
with removal.folder(root) as owned:
    first = owned.remove(['shared', 'app', 'cache', 'workspace', 'tools', 'receipt.json'])
redirector_left = (root / 'tools' / 'amplifier-unified' / 'Scripts' / 'python.exe').exists()
workspace_removed = not (root / 'workspace').exists()
attempts = []
popen = removal.subprocess.Popen
def record_start(*args, **kwargs):
    row = {'flags': kwargs['creationflags']}
    attempts.append(row)
    try:
        process = popen(*args, **kwargs)
    except OSError as error:
        row.update(accepted=False, winerror=error.winerror)
        raise
    row['accepted'] = True
    return process
removal.subprocess.Popen = record_start
pid = removal.finish_after_exit(root, 'Exact-TOKEN',
    contents=['tools', 'app', 'shared', 'cache'], folders=['workspace'],
    files=['receipt.json'], wait_for=(os.getpid(), os.getppid()), seconds=30, report=report)
evidence.write_text(json.dumps(dict(
    inProcess=first, redirectorLeft=redirector_left,
    workspaceRemovedInProcess=workspace_removed, pid=pid,
    realPid=os.getpid(), redirectorPid=os.getppid(), inJob=bool(in_job.value),
    attempts=attempts)))
"""
    environment = os.environ.copy()
    environment.update(TEMP=str(root / "cache"), TMP=str(root / "cache"))
    process = subprocess.Popen(
        [str(redirector), "-I", "-S", "-c", code, str(Path(__file__).resolve().parents[1]),
         str(root), str(evidence), str(tree["report"])],
        cwd=root / "workspace", env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _, stderr = process.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()  # Only the throwaway redirector this test started.
        process.communicate()
        raise
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")[-2000:]
    first = json.loads(evidence.read_text())
    assert first["realPid"] != process.pid and first["redirectorPid"] == process.pid
    assert first["redirectorLeft"], "The real redirector must still be mapped during in-process removal."
    assert r"tools\amplifier-unified\Scripts\python.exe" in first["inProcess"]["left"]
    assert first["inProcess"]["leftCount"] > 0
    print("AFTER_EXIT_CHAIN " + json.dumps(first, separators=(",", ":")))
    assert not any((root / name).exists() for name in ("app", "shared", "cache", "receipt.json"))
    result = _finish_report(tree["report"])
    assert result["reason"] == "", result
    assert result["folderRemoved"] and result["passes"] >= 1 and not root.exists(), result
    assert not result["left"] and result["leftCount"] == 0
    _assert_outside_unchanged(tree)


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell token refusal")
def test_native_finish_wrong_token_changes_nothing(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    before = {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
              for path in root.rglob("*")}
    removal.finish_after_exit(root, "exact-TOKEN", **_finish_options(tree))
    result = _finish_report(tree["report"])
    assert result["passes"] == 0 and not result["folderRemoved"]
    assert result["reason"] == "Root or token refused"
    after = {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
             for path in root.rglob("*")}
    assert after == before
    _assert_outside_unchanged(tree)


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell link deletion")
@pytest.mark.parametrize("kind", ["junction", "folderlink", "filelink", "insidejunction"])
def test_native_finish_deletes_only_the_link_and_leaves_outside_untouched(finish_tree, kind):
    import _winapi

    tree, root = finish_tree, finish_tree["root"]
    link = root / "tools" / kind
    if kind == "insidejunction":
        target = root / "python" / "cpython-3.13.17-windows-x86_64-none"
        target.mkdir(parents=True)
        (target / "python.exe").write_text("stand-in, never executed")
        link = root / "python" / "cpython-3.13-windows-x86_64-none"
        _winapi.CreateJunction(str(target), str(link))
    elif kind == "junction":
        _winapi.CreateJunction(str(tree["outside"]), str(link))
    else:
        target = tree["outside"] if kind == "folderlink" else tree["outside"] / "keep.txt"
        # A native lane that cannot make either symbolic link fails, never skips.
        os.symlink(target, link, target_is_directory=kind == "folderlink")
    tree["links"].append(link)
    assert os.path.lexists(link)
    options = _finish_options(tree)
    options["contents"].insert(0, "python")
    removal.finish_after_exit(root, "Exact-TOKEN", **options)
    result = _finish_report(tree["report"])
    assert result["reason"] == "", result
    assert result["passes"] >= 1 and result["folderRemoved"] and not root.exists(), result
    assert not os.path.lexists(link) and not result["left"] and result["leftCount"] == 0
    _assert_outside_unchanged(tree)
    print(f"AFTER_EXIT_LINK {kind}: removed; outside names and contents unchanged")


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell file sharing refusal")
def test_native_finish_leaves_a_file_held_without_delete_sharing(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    held = root / "tools" / "held.txt"
    with open(held, "w") as stream:
        stream.write("held")
        stream.flush()
        removal.finish_after_exit(root, "Exact-TOKEN", **_finish_options(tree))
        result = _finish_report(tree["report"])
        assert result["reason"] == "", result
        assert result["passes"] >= 2 and not result["folderRemoved"]
        assert str(held) in result["left"] and held.read_text() == "held"
        assert sorted(path.name for path in root.iterdir()) == ["tools", "unified-uninstall.json"]
        assert sorted(path.name for path in (root / "tools").iterdir()) == ["held.txt"]
        assert json.loads((root / "unified-uninstall.json").read_text())["token"] == "Exact-TOKEN"
    _assert_outside_unchanged(tree)
