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
import threading
import time
import uuid

import pytest

from amplifier_web import windows_removal as removal

TOKEN = "0123456789abcdef0123456789abcdef"
PARK = "unified-uninstall-last"
COMPLETE = "unified-setup-complete.json"


class Tree:
    """An in-memory folder with the five native steps of the walk.

    A node is {"kind": "folder"|"file"|"link", "children": {}, "held": bool,
    "attributes": listed attributes}. Opens are recorded so that a test can
    show that a link's target was never opened.
    """
    def __init__(self, root):
        self.rootnode, self.handles, self.opened, self.listed, self.before_open = root, {}, [], [], None
        self.closed = []

    def root(self, path):
        self.handles[1] = (self.rootnode, None, None, False)
        return 1

    def close(self, handle):
        node, parent, name, doomed = self.handles.pop(handle)
        self.closed.append(name)
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

    def make(self, parent, name):
        children = self.handles[parent][0]["children"]
        if name in children:
            raise FileExistsError(17, "already there")
        children[name] = folder()
        return self.open(parent, name, enter=True, delete=False)

    def keep(self, parent, name):
        children = self.handles[parent][0]["children"]
        if name in children:
            raise FileExistsError(17, "already there")
        node = file(held=True)
        children[name] = node
        handle = max(self.handles) + 1
        self.handles[handle] = (node, self.handles[parent][0], name, True)
        return handle

    def park(self, handle, destination, name):
        node, parent, old, _ = self.handles[handle]
        target = self.handles[destination][0]
        if name in target["children"]:
            raise FileExistsError(17, "already there")
        del parent["children"][old]
        target["children"][name] = node
        self.handles[handle] = (node, target, name, False)

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
        with pytest.raises(OSError):
            removal._Native.make(native, 1, name)
        with pytest.raises(OSError):
            removal._Native.keep(native, 1, name)
        with pytest.raises(OSError):
            removal._Native.park(native, 1, 2, name)


def test_mapped_file_is_moved_and_its_empty_parents_go(opened):
    mapped = file(mapped=True)
    root = folder(tools=folder(sub=folder(running=mapped), plain=file()))
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        with owned.aside(PARK, TOKEN) as park:
            result = owned.remove(["tools"], park=park)
            assert len(tree.handles) == 4
            assert park.keep == (3, 4)
            assert tree.handles[park.keep[0]][1] is root
            assert tree.handles[park.keep[1]][1] is root["children"][PARK]
            assert root["children"][TOKEN + ".hold"] == file(held=True)
            closed, close = [], tree.close
            def record(handle):
                closed.append(handle)
                close(handle)
            tree.close = record
    assert result == {"removed": 3, "left": [], "leftCount": 0, "parked": 1}
    assert root["children"] == {PARK: folder(**{"1": mapped})}
    assert tree.closed[-4:] == [TOKEN + ".hold", TOKEN + ".hold", PARK, None]
    assert closed == [4, 3, 2, 1]
    assert not tree.handles


@pytest.mark.parametrize("kind", ["link", "folder"])
def test_a_link_or_nonempty_folder_is_never_parked(opened, kind):
    blocked = {**link(folder(precious=file())), "mapped": True} if kind == "link" else folder(busy=file(held=True))
    root = folder(tools=folder(blocked=blocked))
    opened["use"](root)
    called = []
    with removal.folder(opened["path"]) as owned:
        result = owned.remove(["tools"], park=called.append)
    assert not called and result["parked"] == 0
    assert result["left"] == [r"tools\blocked" + (r"\busy" if kind == "folder" else "")]
    assert root["children"]["tools"]["children"]["blocked"] is blocked


def test_a_failed_move_keeps_the_file_and_counts_it(opened):
    root = folder(tools=folder(running=file(mapped=True)))
    opened["use"](root)
    def refused(handle):
        raise OSError(5, "move refused")
    with removal.folder(opened["path"]) as owned:
        result = owned.remove(["tools"], park=refused)
    assert result == {"removed": 0, "left": [r"tools\running"], "leftCount": 1, "parked": 0}
    assert root["children"]["tools"]["children"]["running"]["mapped"]


def test_aside_refuses_an_existing_name_and_leaves_no_handle(opened):
    old = folder(precious=file())
    root = folder(**{PARK: old})
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        with pytest.raises(FileExistsError):
            with owned.aside(PARK, TOKEN):
                pytest.fail("An existing folder must never be used.")
        assert len(tree.handles) == 1 and root["children"][PARK] is old
    assert not tree.handles


@pytest.mark.parametrize("taken", [1, 10])
def test_aside_tries_the_next_number_but_stops_after_ten_collisions(opened, taken):
    root = folder(tools=folder(running=file(mapped=True)))
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        with owned.aside(PARK, TOKEN) as park:
            children = root["children"][PARK]["children"]
            children.update({str(number): file(precious=True) for number in range(1, taken + 1)})
            result = owned.remove(["tools"], park=park)
            assert all(children[str(number)] == file(precious=True) for number in range(1, taken + 1))
    assert result["parked"] == (1 if taken == 1 else 0)
    assert result["left"] == ([] if taken == 1 else [r"tools\running"])
    if taken == 1:
        assert children["2"] == file(mapped=True) and "tools" not in root["children"]
    assert not tree.handles


@pytest.mark.parametrize("changed", ["flat", "root-file", "flat-file"])
def test_aside_refuses_a_changed_folder_or_kept_file_and_closes_every_handle(opened, changed):
    root = folder()
    tree = opened["use"](root)
    keep = tree.keep
    def altered(parent, name):
        handle = keep(parent, name)
        if parent == 2:
            target = {"flat": 2, "root-file": 3, "flat-file": 4}[changed]
            node = tree.handles[target][0]
            node["kind"] = "link"
        return handle
    tree.keep = altered
    with removal.folder(opened["path"]) as owned:
        with pytest.raises(OSError, match="changed into a link"):
            with owned.aside(PARK, TOKEN):
                pytest.fail("A changed folder must never be used.")
        assert len(tree.handles) == 1
        assert tree.closed == [TOKEN + ".hold", TOKEN + ".hold", PARK]
    assert not tree.handles


@pytest.mark.parametrize("where", ["root", "flat"])
def test_aside_refuses_an_existing_kept_name_and_closes_what_it_created(opened, where):
    root = folder()
    tree = opened["use"](root)
    old = file(precious=True)
    if where == "root":
        root["children"][TOKEN + ".hold"] = old
    else:
        make = tree.make
        def occupied(parent, name):
            handle = make(parent, name)
            tree.handles[handle][0]["children"][TOKEN + ".hold"] = old
            return handle
        tree.make = occupied
    with removal.folder(opened["path"]) as owned:
        with pytest.raises(FileExistsError):
            with owned.aside(PARK, TOKEN):
                pytest.fail("An existing entry must never be used.")
        assert len(tree.handles) == 1
        assert root["children"].get(TOKEN + ".hold") is (old if where == "root" else None)
        if where == "root":
            assert PARK not in root["children"]
        else:
            assert root["children"][PARK]["children"][TOKEN + ".hold"] is old
    assert not tree.handles


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
            self.stdout = io.BytesIO(b"ready\r\n")

    calls, process = [], Process()
    process.inheritance, process.startup = [], []
    monkeypatch.setattr(removal.os, "set_handle_inheritable",
                        lambda handle, value: process.inheritance.append((handle, value)), raising=False)
    def startup(**options):
        process.startup.append(options)
        return options
    monkeypatch.setattr(removal.subprocess, "STARTUPINFO", startup, raising=False)
    monkeypatch.setattr(windows_service, "_powershell",
                        lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    def start(args, **kwargs):
        assert process.inheritance[-2:] == [(101, True), (202, True)]
        assert kwargs["startupinfo"] == {"lpAttributeList": {"handle_list": [101, 202]}}
        calls.append((args, kwargs))
        return process
    monkeypatch.setattr(removal.subprocess, "Popen", start)
    return calls, process, start


def test_finish_static_program_and_stdin_only_payload(finish_start):
    calls, process, _ = finish_start
    token = TOKEN
    assert removal.finish_after_exit(
        r"C:\My [private]\Amplifier", token, park=PARK, keep=(101, 202), folders=["workspace"],
        wait_for=(12, 34), seconds=3, report=r"C:\proof.json",
    ) == 4321
    args, options = calls[0]
    assert args[1:5] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    assert base64.b64decode(args[-1]).decode("utf-16-le") == removal.FINISH_SCRIPT
    assert len(removal.FINISH_SCRIPT) < 9000
    code = "\n".join(line.split("#", 1)[0] for line in removal.FINISH_SCRIPT.splitlines())
    assert "-recurse" not in code.lower()
    assert not re.search(r"Remove-Item[^\n]*-Force", code, re.I)
    assert "]::" not in code and "CurrentDirectory" not in code and "'CreateNew'" not in code
    assert re.findall(r"\$\w+(?:\[\d+\])?\.\w+\s*\([^)]*\)", code) == [
        "$entry.Delete()", "$kept[1].Close()", "$kept[0].Close()", "$file.Close()"]
    # At its end, in every case, the step lets both kept files go before it writes its report.
    end = code.split("finally {")[1]
    assert end.index("foreach ($file in $kept) { $file.Close() }") < end.index("if ($p.report) {")
    assert code.count("New-Object ") == 2
    assert token not in removal.FINISH_SCRIPT
    assert options["cwd"] == r"C:\Windows\System32"
    assert options["creationflags"] == 0x09000200
    assert options["stdin"] == subprocess.PIPE
    assert options["stdout"] == subprocess.PIPE and options["stderr"] == subprocess.DEVNULL
    assert process.stdin.closed and process.stdout.closed
    assert process.stdin.payload == {
        "root": r"C:\My [private]\Amplifier", "token": token, "choice": "delete", "park": PARK,
        "parent": r"C:\My [private]", "keep": [101, 202],
        "folders": ["workspace"], "wait_for": [12, 34],
        "seconds": 3, "report": r"C:\proof.json",
    }
    assert process.inheritance == [(101, True), (202, True), (101, False), (202, False)]


@pytest.mark.parametrize("root", [
    "relative", r"C:relative", r"\rooted", r"\\server\share\Amplifier",
    r"\\?\C:\Amplifier", r"1:\Amplifier",
])
def test_finish_refuses_nonlocal_or_nonabsolute_roots(finish_start, root):
    calls, _, _ = finish_start
    with pytest.raises(ValueError, match="absolute local drive"):
        removal.finish_after_exit(root, TOKEN, park=PARK, keep=(101, 202), folders=[])
    assert not calls


@pytest.mark.parametrize("token", [
    "", None, 32, "a" * 31, "a" * 33, "A" * 32, "g" * 32,
    "data, not code: ' ` $(&exit); \u2603",
])
def test_finish_refuses_tokens_other_than_lowercase_32_hex(finish_start, token):
    calls, _, _ = finish_start
    with pytest.raises(ValueError, match="32 lowercase hex"):
        removal.finish_after_exit(r"C:\Amplifier", token, park=PARK, keep=(101, 202), folders=[])
    assert not calls


@pytest.mark.parametrize("choice", ["DELETE", "Keep", "", None])
def test_finish_refuses_unknown_choices(finish_start, choice):
    calls, _, _ = finish_start
    with pytest.raises(ValueError, match="Choice"):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[], choice=choice)
    assert not calls


def test_finish_normalizes_the_absolute_root_and_sends_keep_choice(finish_start):
    _, process, _ = finish_start
    name = "data, not code: ' ` $(&exit); \u2603".replace(":", "")
    removal.finish_after_exit(
        r"C:/My [private]/../Amplifier/.", TOKEN,
        park=name, keep=(101, 202), folders=[], choice="keep",
    )
    assert process.stdin.payload["root"] == r"C:\Amplifier"
    assert process.stdin.payload["choice"] == "keep"
    assert process.stdin.payload["park"] == name
    assert name not in removal.FINISH_SCRIPT


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "a:b", "a\0b",
                                      "unified-uninstall.json", "UNIFIED-UNINSTALL.JSON",
                                      "unified-setup-complete.json", "UNIFIED-SETUP-COMPLETE.JSON", None, 2])
@pytest.mark.parametrize("group", ["park", "folders"])
def test_finish_refuses_the_whole_payload_before_start(finish_start, name, group):
    calls, _, _ = finish_start
    groups = dict(park=PARK, keep=(101, 202), folders=["workspace"])
    if group == "park":
        groups[group] = name
    else:
        groups[group].append(name)
    with pytest.raises(ValueError):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, **groups)
    assert not calls


def test_finish_retries_only_a_refused_process_start(finish_start, monkeypatch):
    calls, _, start = finish_start
    def refuse_breakaway(args, **kwargs):
        if kwargs["creationflags"] & 0x01000000:
            calls.append((args, kwargs))
            raise OSError("Breakaway refused")
        return start(args, **kwargs)
    monkeypatch.setattr(removal.subprocess, "Popen", refuse_breakaway)
    assert removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[]) == 4321
    assert [kwargs["creationflags"] for _, kwargs in calls] == [0x09000200, 0x08000200]
    assert calls[0][0] == calls[1][0]
    def refuse_both(*args, **kwargs):
        raise OSError("Cannot start")
    monkeypatch.setattr(removal.subprocess, "Popen", refuse_both)
    with pytest.raises(OSError, match="Cannot start"):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[])
    assert finish_start[1].inheritance[-2:] == [(101, False), (202, False)]


def test_finish_reports_a_missing_system_powershell_as_an_os_error(finish_start, monkeypatch):
    from amplifier_web import windows_service

    calls, _, _ = finish_start
    def missing():
        raise windows_service.WindowsServiceError("No system folder")
    monkeypatch.setattr(windows_service, "_powershell", missing)
    with pytest.raises(OSError, match="Cannot locate Windows PowerShell"):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[])
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
    removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[])
    env = calls[0][1]["env"]
    assert (env["TEMP"], env["TMP"]) == (expected_temp, expected_tmp)
    assert os.environ == before and env is not os.environ


@pytest.mark.parametrize("answer", [b"", b"not ready\r\n"])
def test_finish_refuses_end_of_output_or_another_answer(finish_start, answer):
    calls, process, _ = finish_start
    process.stdout = io.BytesIO(answer)
    with pytest.raises(OSError, match="did not take over the kept files"):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[])
    assert len(calls) == 1 and process.stdin.closed


def test_finish_stops_waiting_when_the_answer_does_not_arrive(finish_start):
    calls, process, _ = finish_start
    release = threading.Event()
    class Blocked:
        def readline(self):
            release.wait()
            return b""
    process.stdout = Blocked()
    started = time.monotonic()
    try:
        with pytest.raises(OSError, match="did not take over the kept files"):
            removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[], ready=0.2)
        assert 0.15 <= time.monotonic() - started < 2
        assert len(calls) == 1 and process.stdin.closed
    finally:
        release.set()


@pytest.mark.parametrize("options", [
    {"seconds": -1}, {"seconds": 3601}, {"seconds": True}, {"seconds": "1"},
    {"wait_for": [0]}, {"wait_for": [-1]}, {"wait_for": [True]}, {"wait_for": ["12"]},
])
def test_finish_refuses_bad_durations_and_process_ids_before_start(finish_start, options):
    calls, _, _ = finish_start
    with pytest.raises(ValueError):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[], **options)
    assert not calls


def test_finish_refuses_to_leave_temporary_files_inside_the_installation(finish_start, monkeypatch):
    calls, _, _ = finish_start
    monkeypatch.setenv("TEMP", r"C:\Amplifier\temp")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(OSError, match="Cannot relocate"):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=(101, 202), folders=[])
    assert not calls


@pytest.mark.parametrize("root", ["C:\\", "D:/", "C:/folder/.."])
def test_finish_refuses_a_drives_top_folder(finish_start, root):
    calls, _, _ = finish_start
    with pytest.raises(ValueError, match="below a drive"):
        removal.finish_after_exit(root, TOKEN, park=PARK, keep=(101, 202), folders=[])
    assert not calls


@pytest.mark.parametrize("keep", [[], [1], [1, 2, 3], [1, 1], [0, 2], [-1, 2],
                                  [True, 2], ["1", 2], [1.0, 2]])
def test_finish_requires_two_different_positive_integer_handles(finish_start, keep):
    calls, process, _ = finish_start
    with pytest.raises(ValueError, match="Two different open file handles"):
        removal.finish_after_exit(r"C:\Amplifier", TOKEN, park=PARK, keep=keep, folders=[])
    assert not calls and not process.inheritance


def test_step_after_exit_keeps_both_inherited_files_open_until_path_deletion_is_over():
    script = removal.FINISH_SCRIPT
    # A parked file has a number as its name. The step deletes nothing else in the flat folder.
    assert ("                if ($entry.PSIsContainer -or $entry.Name -cnotmatch '^[0-9]+$' -or\n"
            "                    \"$($entry.Attributes)\" -match 'ReparsePoint|ReadOnly|Hidden|System') {\n"
            "                    Keep $entry.FullName\n") in script
    start = script.split("Write-Host 'ready'")[0]
    assert "$handle = New-Object Microsoft.Win32.SafeHandles.SafeFileHandle -ArgumentList ([IntPtr][long]$_), $true" in start
    assert "New-Object System.IO.FileStream -ArgumentList $handle, ([System.IO.FileAccess]'Write')" in start
    assert "$kept.Count -ne 2" in start
    assert "-not (Plain (Join-Path $p.root $own))" in start
    assert "-not (Plain (Join-Path $park $own))" in start
    assert "$own = $p.token + '.hold'" in start
    # The wait ends when waiting cannot help: no program left, no parked file, no named folder.
    assert "$idle = (-not $alive.Count -and -not $inside.Count -and -not $named.Count)" in script
    assert "if ($idle -or (Get-Date) -ge $until) { break }" in script
    tail, order = script.split("} while ((Get-Date) -lt $until)")[1], [0]
    for part in (
            # Both records are checked before anything of the end happens.
            "if (-not (Authorized))", "$journal = Get-Record $mine[2]", "$complete = Get-Record $mine[3]",
            "$last = ($done -and (Only $park @($own)) -and (Only $p.root $mine))",
            "if ($last) {",
            # The journal first. Setup's completion record only if nothing appeared meanwhile.
            "Remove-Item -LiteralPath $journal.FullName",
            "$last = (Only $p.root @($p.park, $own, $mine[3]))",
            "if ($last -and $null -ne $complete) { Remove-Item -LiteralPath $complete.FullName }",
            # Every file is deleted. Only now does the step close its file; then two empty folders go.
            "$kept[1].Close()", "$folder = Get-Item -LiteralPath $park",
            "if ($last -and @(Get-ChildItem -LiteralPath $p.parent -Force).Count -gt 1) {",
            "$kept[0].Close()", "$folder = Get-Item -LiteralPath $p.root"):
        order.append(tail.index(part, order[-1] + 1))  # Each part follows the one before; a missing part fails.
    # A record that is a link, a folder or a protected file is refused before anything of the end happens.
    assert ("    if ($null -ne $file -and ($file.PSIsContainer -or\n"
            "        \"$($file.Attributes)\" -match 'ReparsePoint|ReadOnly|Hidden|System')) { throw 'Record refused' }"
            ) in script
    # No file is deleted after the step closed its own file.
    assert "Remove-Item" not in tail.split("$kept[1].Close()")[1]
    # The step leaves its inner folder once, for the folder above the installation, and stays there.
    assert "CurrentDirectory" not in script
    # Three commands delete a file by its path: a parked file, the journal, setup's record. Never with -Force.
    deleting = [line.strip() for line in script.splitlines() if "Remove-Item " in line and not line.strip().startswith("#")]
    assert len(deleting) == 3 and not any("-Force" in line or "-Recurse" in line for line in deleting)


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
        print("REMOVAL_LINKS " + json.dumps({"first": first, "outsideUnchanged": True,
                                           "longPathLength": len(long), "symlinks": symlinks}))
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
        print("REMOVAL_ROOT_REFUSED " + json.dumps({"linkRefused": True, "missingRefused": True,
                                                  "dataUnchanged": True}))
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
        (root / "unified-uninstall.json").write_text(json.dumps({
            "schema": 1, "choice": "delete", "token": TOKEN,
        }))
        (root / COMPLETE).write_text('{"schema": 1}')
        (outside / "deep").mkdir(parents=True)
        (outside / "keep.txt").write_text("keep outside")
        (outside / "deep" / "also.txt").write_text("keep deep outside")
        yield dict(root=root, outside=outside, links=links, report=tmp_path / "finish.json",
                   outside_before=_outside_state(outside))
    finally:
        for path in links:
            if os.path.lexists(path):
                # Use the link's own attributes, including for dangling links.
                if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                    os.rmdir(path)
                else:
                    os.unlink(path)
        def writable_and_retry(function, path, error):
            if isinstance(error, FileNotFoundError):
                return
            # Only our throwaway trees, after all safety assertions ran.
            os.chmod(path, stat.S_IWRITE)
            function(path)
        for path in (root, outside):
            deadline = time.monotonic() + 5
            while True:
                try:
                    shutil.rmtree(path, onexc=writable_and_retry)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)


def _finish_report(path, *, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            time.sleep(0.1)
    pytest.fail("The after-exit helper wrote no complete report within its time budget.")


def _finish_options(tree):
    return dict(park=PARK, folders=[], seconds=2, report=tree["report"])


def _plain_tree(root, *, exclude=()):
    """Only for trees without links."""
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*") if path not in exclude}


def _wait_until_gone(path, *, timeout=5):
    """A report can arrive just before the helper exits and closes its file."""
    deadline = time.monotonic() + timeout
    while os.path.lexists(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not os.path.lexists(path), f"The helper still holds {path.name}."


def _outside_state(outside):
    return {str(path.relative_to(outside)): (
        path.read_bytes() if path.is_file() else None, path.stat().st_file_attributes)
        for path in outside.rglob("*")}


def _assert_outside_unchanged(tree):
    assert _outside_state(tree["outside"]) == tree["outside_before"]


def _kept_refusals(root, token, outside):
    """Use Win32 opens so every refusal has its measured Windows error code."""
    import ctypes as C
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
                                  C.c_uint32, C.c_uint32, C.c_void_p]
    kernel.CreateFileW.restype = C.c_void_p
    kernel.CloseHandle.argtypes = [C.c_void_p]
    measured = {}
    for label, parent in (("root", root), ("flat", root / PARK)):
        path = parent / (token + ".hold")
        row = {"exists": path.exists()}
        for name, action in (("delete", lambda: os.remove(path)),
                             ("move", lambda: os.rename(path, parent / "moved.hold")),
                             ("renameFolder", lambda: os.rename(parent, str(parent) + "-moved"))):
            try:
                action()
                row[name] = 0
            except OSError as error:
                row[name] = error.winerror
        handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0, None)
        row["read"] = C.get_last_error() if handle == C.c_void_p(-1).value else 0
        if handle != C.c_void_p(-1).value:
            kernel.CloseHandle(handle)
        row["inplace"] = _junction_in_place(parent, outside)["error"]
        measured[label] = row
    return measured


def _assert_kept(measured, name):
    if any(not row["exists"] or row["delete"] != 32 or row["move"] != 32 or row["read"] != 32
           or not row["renameFolder"] or row["inplace"] != 145 for row in measured.values()):
        print(name + " " + json.dumps(measured, separators=(",", ":")))
        pytest.exit("The inherited files did not keep both folders. Stop; inspect the numbers.", 1)


@pytest.mark.skipif(os.name != "nt", reason="A handle-relative create must never follow a dangling file link")
def test_native_keep_refuses_a_dangling_file_link_without_creating_its_target(finish_tree):
    tree, root, outside = finish_tree, finish_tree["root"], finish_tree["outside"]
    victim = outside / "victim.txt"
    entry, probe = root / (TOKEN + ".hold"), root / "probe.hold"
    os.symlink(victim, entry)
    os.symlink(victim, probe)
    tree["links"] += [entry, probe]
    measured = {}
    with removal.folder(root) as owned:
        for name, action in (("aside", lambda: owned.aside(PARK, TOKEN).__enter__()),
                             ("keep", lambda: owned._api.keep(owned._handle, "probe.hold"))):
            try:
                action()
                measured[name] = 0
            except FileExistsError as error:
                measured[name] = error.winerror
            if victim.exists() or not measured[name]:
                measured["targetCreated"] = victim.exists()
                print("KEEP_NO_LINK " + json.dumps(measured, separators=(",", ":")))
                pytest.exit("The handle-relative create followed or accepted a dangling link. Stop.", 1)
        assert not (root / PARK).exists()
        assert entry.is_symlink() and probe.is_symlink() and not victim.exists()
    _assert_outside_unchanged(tree)
    # A separate target measures the old path-based operation, not the safe one.
    old_target, old_link = outside / "old-path.txt", root / "old.hold"
    os.symlink(old_target, old_link)
    tree["links"].append(old_link)
    try:
        with open(old_link, "x") as stream:
            stream.write("created by path")
        measured["pathError"] = 0
    except OSError as error:
        measured["pathError"] = error.winerror
    measured.update(createdByPath=old_target.exists(), targetCreated=False, flatGone=True)
    if old_target.exists():
        old_target.unlink()
    _assert_outside_unchanged(tree)
    print("KEEP_NO_LINK " + json.dumps(measured, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="The child must retain both handle-created files after the parent closes")
def test_native_kept_files_live_until_the_child_that_inherits_them_ends(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    child = None
    code = r"""
import ctypes as C, json, sys
kernel = C.WinDLL("kernel32", use_last_error=True)
kernel.GetFileType.argtypes = [C.c_void_p]
kernel.GetFileType.restype = C.c_uint32
handles = [int(value) for value in sys.argv[1:]]
types = [kernel.GetFileType(handle) for handle in handles]
assert types == [1, 1], types
print(json.dumps(types), flush=True)
sys.stdin.buffer.readline()
"""
    try:
        with removal.folder(root) as owned:
            with owned.aside(PARK, TOKEN) as park:
                for handle in park.keep:
                    os.set_handle_inheritable(handle, True)
                try:
                    child = subprocess.Popen(
                        [sys.executable, "-I", "-S", "-c", code, *map(str, park.keep)],
                        startupinfo=subprocess.STARTUPINFO(lpAttributeList={"handle_list": list(park.keep)}),
                        close_fds=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                finally:
                    for handle in park.keep:
                        os.set_handle_inheritable(handle, False)
                answer = []
                reader = threading.Thread(target=lambda: answer.append(child.stdout.readline()), daemon=True)
                reader.start()
                reader.join(10)
                assert answer and json.loads(answer[0]) == [1, 1], answer
        # The caller has closed every copy and its folder handle.
        measured = _kept_refusals(root, TOKEN, tree["outside"])
        _assert_kept(measured, "KEEP_INHERITED")
        assert child.poll() is None
    finally:
        if child:
            output, error = child.communicate(input=b"end\n", timeout=10)
            assert child.returncode == 0 and not error, error.decode("utf-8", "replace")[-1500:]
    gone = [not (parent / (TOKEN + ".hold")).exists() for parent in (root, root / PARK)]
    assert gone == [True, True], gone
    _assert_outside_unchanged(tree)
    print("KEEP_INHERITED " + json.dumps({"files": measured, "goneAfterExit": gone}, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="Real Windows venv redirector and after-exit helper")
@pytest.mark.parametrize("completion", [True, False], ids=["completion", "no-completion"])
def test_native_finish_survives_the_real_redirector_and_python_exit(finish_tree, tmp_path, completion):
    tree, root = finish_tree, finish_tree["root"]
    if not completion:
        (root / COMPLETE).unlink()
    (root / "workspace").mkdir()
    (root / "python" / "probe").mkdir(parents=True)
    source = next(path for path in (Path(sys.base_prefix) / "vcruntime140.dll",
                                    Path(os.environ["SystemRoot"]) / "System32/version.dll") if path.exists())
    shutil.copyfile(source, root / "python/probe/probe.dll")
    venv = root / "tools" / "amplifier-unified"
    made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                          capture_output=True, text=True, timeout=60)
    assert made.returncode == 0, made.stderr[-1500:]
    redirector = venv / "Scripts" / "python.exe"
    evidence = tmp_path / "in-process.json"
    code = r"""
import ctypes, hashlib, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from amplifier_web import windows_removal as removal
from amplifier_web.windows_service import _powershell
root, evidence, report = map(Path, sys.argv[2:5])
images = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in (
    ('start', root / 'tools/amplifier-unified/Scripts/python.exe'),
    ('dll', root / 'python/probe/probe.dll'))}
library = ctypes.WinDLL(str(root / 'python/probe/probe.dll'))
os.chdir(Path(_powershell()).parents[2])
in_job = ctypes.c_int()
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
kernel.IsProcessInJob.restype = ctypes.c_int
assert kernel.IsProcessInJob(ctypes.c_void_p(-1), None, ctypes.byref(in_job))
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
with removal.folder(root) as owned:
    with owned.aside('unified-uninstall-last', '0123456789abcdef0123456789abcdef') as park:
        pid = removal.finish_after_exit(root, '0123456789abcdef0123456789abcdef',
            park='unified-uninstall-last', keep=park.keep, folders=['python', 'tools', 'workspace'],
            wait_for=(os.getpid(), os.getppid()), seconds=30, report=report)
        workspace = owned.remove(['workspace'])
        first = owned.remove(['python', 'tools'], park=park)
        parked = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (root / 'unified-uninstall-last').iterdir()
                  if path.is_file() and not path.name.endswith('.hold')}
        left = owned.names()
evidence.write_text(json.dumps(dict(
    inProcess=first, workspace=workspace, parked=parked, images=images, left=left, pid=pid,
    realPid=os.getpid(), redirectorPid=os.getppid(), inJob=bool(in_job.value),
    attempts=attempts)))
"""
    environment = os.environ.copy()
    environment.update(TEMP=str(root / "workspace"), TMP=str(root / "workspace"))
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
    assert first["inProcess"]["parked"] >= 2
    assert set(first["images"].values()) <= set(first["parked"].values())
    assert set(first["left"]) <= {PARK, TOKEN + ".hold", "unified-uninstall.json", COMPLETE, "workspace"}
    assert (COMPLETE in first["left"]) is completion
    assert first["inProcess"]["left"] == [] and first["inProcess"]["leftCount"] == 0
    assert first["workspace"]["left"] in ([], ["workspace"])
    result = _finish_report(tree["report"])
    assert result["reason"] == "", result
    assert result["folderRemoved"] and result["passes"] >= 1 and not root.exists(), result
    assert not result["left"] and result["leftCount"] == 0
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_CHAIN " + json.dumps({"process": first, "afterExit": result}, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell token refusal")
def test_native_finish_wrong_token_changes_nothing(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    with removal.folder(root) as owned:
        with owned.aside(PARK, "f" * 32) as park:
            (root / PARK / "old.txt").write_text("must stay")
            excluded = (root / ("f" * 32 + ".hold"), root / PARK / ("f" * 32 + ".hold"))
            before = _plain_tree(root, exclude=excluded)
            with pytest.raises(OSError, match="did not take over the kept files"):
                removal.finish_after_exit(root, "f" * 32, keep=park.keep, **_finish_options(tree))
            result = _finish_report(tree["report"])
            assert _plain_tree(root, exclude=excluded) == before
    assert result["passes"] == 0 and not result["folderRemoved"]
    assert result["reason"] == "Root or token refused"
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_WRONG_TOKEN " + json.dumps(result))


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell journal validation")
@pytest.mark.parametrize("journal", [
    "", '{"schema":1,"choice":"delete","token":',
    json.dumps({"token": TOKEN}),
    json.dumps({"schema": 1, "choice": "keep", "token": TOKEN}),
    json.dumps({"schema": 2, "choice": "delete", "token": TOKEN}),
    json.dumps([{"schema": 1, "choice": "delete", "token": TOKEN}]),
], ids=["empty", "cut-off", "token-only", "wrong-choice", "wrong-schema", "list"])
def test_native_finish_invalid_journal_changes_nothing(finish_tree, journal):
    tree, root = finish_tree, finish_tree["root"]
    (root / "unified-uninstall.json").write_text(journal)
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            (root / PARK / "old.txt").write_text("must stay")
            excluded = (root / (TOKEN + ".hold"), root / PARK / (TOKEN + ".hold"))
            before = _plain_tree(root, exclude=excluded)
            with pytest.raises(OSError, match="did not take over the kept files"):
                removal.finish_after_exit(root, TOKEN, keep=park.keep, **_finish_options(tree))
            result = _finish_report(tree["report"])
            assert _plain_tree(root, exclude=excluded) == before
    assert result["passes"] == 0 and not result["folderRemoved"]
    assert result["reason"] == "Root or token refused"
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_BAD_JOURNAL " + json.dumps({"journal": journal, "report": result}))


@pytest.mark.skipif(os.name != "nt", reason="The caller must refuse an existing kept name before any step starts")
@pytest.mark.parametrize("where", ["root", "flat"])
def test_native_aside_refuses_an_existing_kept_file(finish_tree, where):
    tree, root = finish_tree, finish_tree["root"]
    with removal.folder(root) as owned:
        if where == "root":
            entry = root / (TOKEN + ".hold")
        else:
            # Insert an entry after make, before the two kept-file creates.
            make = owned._api.make
            def occupied(parent, name):
                handle = make(parent, name)
                (root / PARK / (TOKEN + ".hold")).write_bytes(b"earlier")
                return handle
            owned._api.make = occupied
            entry = root / PARK / (TOKEN + ".hold")
        if where == "root":
            entry.write_bytes(b"earlier")
        with pytest.raises(FileExistsError):
            with owned.aside(PARK, TOKEN):
                pytest.fail("An existing kept file must refuse before starting a step.")
        assert entry.read_bytes() == b"earlier"
        assert not tree["report"].exists()
        if where == "root":
            assert not (root / PARK).exists()
        else:
            assert not (root / (TOKEN + ".hold")).exists()
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_SECOND " + json.dumps({"where": where, "refused": True, "stepStarted": False}))


@pytest.mark.skipif(os.name != "nt", reason="The helper must refuse when the kept file at the root is absent")
def test_native_finish_refuses_when_the_expected_root_kept_file_is_missing(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    with removal.folder(root) as owned:
        api = owned._api
        flat = api.make(owned._handle, PARK)
        kept = []
        try:
            kept.append(api.keep(flat, TOKEN + ".hold"))
            kept.append(api.keep(flat, "other.hold"))
            excluded = (root / PARK / (TOKEN + ".hold"), root / PARK / "other.hold")
            before = _plain_tree(root, exclude=excluded)
            with pytest.raises(OSError, match="did not take over the kept files"):
                removal.finish_after_exit(root, TOKEN, keep=kept, **_finish_options(tree))
            result = _finish_report(tree["report"])
            assert result["reason"] == "Kept files refused" and result["passes"] == 0, result
            assert _plain_tree(root, exclude=excluded) == before
            assert not (root / (TOKEN + ".hold")).exists()
        finally:
            for handle in reversed(kept):
                api.close(handle)
            api.close(flat)
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_MISSING_KEPT " + json.dumps(result, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="The helper's inherited files keep both folders after the caller lets go")
def test_native_finish_holds_the_root_and_flat_folder_while_it_waits(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    sleeper = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import sys; sys.stdin.buffer.read(1)"],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors = {}
    try:
        with removal.folder(root) as owned:
            with owned.aside(PARK, TOKEN) as park:
                removal.finish_after_exit(root, TOKEN, keep=park.keep, **{
                    **_finish_options(tree), "wait_for": [sleeper.pid], "seconds": 20})
        # No caller handle remains. The helper alone must keep these folders in place.
        for name, source, destination in (
                ("root", root, Path(str(root) + "-moved")),
                ("flat", root / PARK, root / "moved-flat")):
            with pytest.raises(OSError) as refused:
                os.rename(source, destination)
            errors[name] = refused.value.winerror
        assert sleeper.poll() is None and not tree["report"].exists()
    finally:
        sleeper.stdin.close()
        sleeper.wait(timeout=10)
    result = _finish_report(tree["report"])
    assert result["reason"] == "" and result["folderRemoved"] and not root.exists(), result
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_HOLD " + json.dumps({"refused": errors, "report": result}))


@pytest.mark.skipif(os.name != "nt", reason="The step inherits both kept files without sharing")
def test_native_finish_keeps_both_files_open_until_the_wait_ends(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    flat, anchor = root / PARK, root / PARK / (TOKEN + ".hold")
    sleeper = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import sys; sys.stdin.buffer.read(1)"],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors = {}
    try:
        with removal.folder(root) as owned:
            with owned.aside(PARK, TOKEN) as park:
                removal.finish_after_exit(root, TOKEN, keep=park.keep, **{
                    **_finish_options(tree), "wait_for": [sleeper.pid], "seconds": 20})
        assert sorted(os.listdir(flat)) == [TOKEN + ".hold"]
        errors = _kept_refusals(root, TOKEN, tree["outside"])
        _assert_kept(errors, "AFTER_EXIT_KEPT")
        inplace = _junction_in_place(flat, tree["outside"])
        if inplace != {"ok": False, "error": 145, "stage": "set"}:
            print("AFTER_EXIT_KEPT " + json.dumps({"refused": errors, "flat": inplace}))
            pytest.exit("The open anchor did not keep the flat folder nonempty. Stop.", 1)
        assert sleeper.poll() is None and not tree["report"].exists()
    finally:
        sleeper.stdin.close()
        sleeper.wait(timeout=10)
    result = _finish_report(tree["report"])
    _wait_until_gone(root)
    assert result["reason"] == "" and result["folderRemoved"], result
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_KEPT " + json.dumps({"refused": errors, "flat": inplace, "report": result},
                                           separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="The installation is removed only when its parent holds another entry")
@pytest.mark.parametrize("sibling", [False, True], ids=["only-installation", "with-sibling"])
def test_native_finish_keeps_the_folders_above_the_installation_in_place(tmp_path, sibling):
    from amplifier_web import windows_files

    tag = uuid.uuid4().hex[:10]
    above = windows_files.address(Path.home() / ("au-above-test-" + tag))
    root, report = above / "installation", tmp_path / "finish.json"
    with windows_files.directory(root, owned_root=above, create=True):
        pass
    sleeper = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import sys; sys.stdin.buffer.read(1)"],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors = {}
    try:
        (root / "unified-uninstall.json").write_text(json.dumps({"schema": 1, "choice": "delete", "token": TOKEN}))
        (root / COMPLETE).write_text('{"schema": 1}')
        if sibling:
            (above / "mine.txt").write_text("not the installation")
        # Without the helper the folder above can be renamed: the refusal below comes from the hold.
        os.rename(above, str(above) + "-free")
        os.rename(str(above) + "-free", above)
        try:
            with removal.folder(root) as owned:
                with owned.aside(PARK, TOKEN) as park:
                    removal.finish_after_exit(root, TOKEN, park=PARK, keep=park.keep, folders=[], seconds=20, report=report,
                                              wait_for=[sleeper.pid])
            for name, source in (("above", above), ("root", root)):
                with pytest.raises(OSError) as refused:
                    os.rename(source, str(source) + "-moved")
                errors[name] = refused.value.winerror
            assert sleeper.poll() is None and not report.exists()
        finally:
            sleeper.stdin.close()
            sleeper.wait(timeout=10)
        result = _finish_report(report)
        assert result["reason"] == "" and result["folderRemoved"] is sibling, result
        _wait_until_gone(root / (TOKEN + ".hold"))
        if sibling:
            assert not root.exists() and (above / "mine.txt").read_text() == "not the installation"
            assert os.listdir(above) == ["mine.txt"]
        else:
            assert root.is_dir() and list(root.iterdir()) == []
            assert os.listdir(above) == ["installation"]
        print("AFTER_EXIT_ABOVE " + json.dumps({"sibling": sibling, "refused": errors,
                                               "report": result}, separators=(",", ":")))
    finally:
        shutil.rmtree(above, ignore_errors=True)
        shutil.rmtree(str(above) + "-free", ignore_errors=True)


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell keeps folders and links in the flat folder")
@pytest.mark.parametrize("kind", ["junction", "folder", "filelink", "held", "later", "readonly", "readme"])
def test_native_finish_keeps_entries_that_are_not_free_old_plain_files(finish_tree, kind):
    import _winapi

    tree, root = finish_tree, finish_tree["root"]
    held = None
    try:
        with removal.folder(root) as owned:
            with owned.aside(PARK, TOKEN) as park:
                entry = root / PARK / ("readme.txt" if kind == "readme" else "7")
                if kind == "junction":
                    _winapi.CreateJunction(str(tree["outside"]), str(entry))
                    tree["links"].append(entry)
                elif kind == "filelink":
                    os.symlink(tree["outside"] / "keep.txt", entry)
                    tree["links"].append(entry)
                elif kind == "folder":
                    entry.mkdir()
                    (entry / "inside.txt").write_text("must not enter")
                elif kind == "held":
                    held = open(entry, "w")
                    held.write("must stay")
                    held.flush()
                elif kind == "readonly":
                    entry.write_text("read-only stays")
                    os.chmod(entry, stat.S_IREAD)
                elif kind == "readme":
                    entry.write_text("a name that was never parked")
                (root / PARK / "8").write_text("old parked file")
                options = {**_finish_options(tree), "wait_for": [os.getpid()]}
                removal.finish_after_exit(root, TOKEN, keep=park.keep, **options)
                if kind == "later":
                    entry.write_text("made after ready")
        result = _finish_report(tree["report"])
        assert result["reason"] == "" and not result["folderRemoved"], result
        assert os.path.lexists(entry) and (root / "unified-uninstall.json").exists()
        assert (root / COMPLETE).exists() and not (root / PARK / TOKEN).exists()
        _wait_until_gone(root / (TOKEN + ".hold"))
        assert not (root / PARK / (TOKEN + ".hold")).exists()
        assert not (root / PARK / "8").exists()
        assert str(entry) in result["left"] and result["leftCount"] >= 1
        if kind == "folder":
            assert (entry / "inside.txt").read_text() == "must not enter"
        elif kind in {"held", "later", "readonly", "readme"}:
            assert entry.read_text() == {"held": "must stay", "later": "made after ready",
                                         "readonly": "read-only stays", "readme": "a name that was never parked"}[kind]
    finally:
        if held:
            held.close()
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_FLAT_KEPT " + json.dumps({"kind": kind, "report": result}))


def _junction_in_place(path, target):
    """Set mount-point reparse data on an existing folder; never rename it."""
    import ctypes as C
    import struct

    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
                                  C.c_uint32, C.c_uint32, C.c_void_p]
    kernel.CreateFileW.restype = C.c_void_p
    kernel.DeviceIoControl.argtypes = [C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32,
                                      C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32), C.c_void_p]
    kernel.DeviceIoControl.restype = C.c_int
    kernel.CloseHandle.argtypes = [C.c_void_p]
    substitute = ("\\??\\" + str(target)).encode("utf-16-le")
    printed = str(target).encode("utf-16-le")
    names = substitute + b"\0\0" + printed + b"\0\0"
    data = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printed)) + names
    buffer = C.create_string_buffer(struct.pack("<IHH", 0xA0000003, len(data), 0) + data)
    handle = kernel.CreateFileW(str(path), 0x40000000, 7, None, 3, 0x02200000, None)
    if handle == C.c_void_p(-1).value:
        return {"ok": False, "error": C.get_last_error(), "stage": "open"}
    try:
        returned = C.c_uint32()
        ok = bool(kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(buffer.raw) - 1,
                                         None, 0, C.byref(returned), None))
        return {"ok": ok, "error": 0 if ok else C.get_last_error(), "stage": "set"}
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Measure Windows folder protection from an open delete-on-close file")
def test_native_an_open_file_keeps_its_folder_and_the_folder_above_in_place(finish_tree):
    import ctypes as C

    tree, root, outside = finish_tree, finish_tree["root"], finish_tree["outside"]
    parent, control = root / "F", root / "control"
    flat = parent / "flat"
    flat.mkdir(parents=True)
    control.mkdir()
    anchor = flat / "anchor"
    # Prove that a rename works before the open file is there.
    os.rename(flat, parent / "free-flat")
    os.rename(parent / "free-flat", flat)
    os.rename(parent, root / "free-F")
    os.rename(root / "free-F", parent)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
                                  C.c_uint32, C.c_uint32, C.c_void_p]
    kernel.CreateFileW.restype = C.c_void_p
    kernel.CloseHandle.argtypes = [C.c_void_p]
    handle = kernel.CreateFileW(str(anchor), 0x40000000, 0, None, 1, 0x04000000, None)
    assert handle != C.c_void_p(-1).value, C.get_last_error()
    measured = {}
    try:
        measured["flat"] = _junction_in_place(flat, outside)
        measured["parent"] = _junction_in_place(parent, outside)
        measured["control"] = _junction_in_place(control, outside)
        if measured["control"]["ok"]:
            tree["links"].append(control)
        for label, action in (
                ("renameFlat", lambda: os.rename(flat, parent / "moved-flat")),
                ("renameParent", lambda: os.rename(parent, root / "moved-F")),
                ("removeAnchor", lambda: os.remove(anchor)),
                ("renameAnchor", lambda: os.rename(anchor, flat / "moved-anchor"))):
            try:
                action()
                measured[label] = 0
            except OSError as error:
                measured[label] = error.winerror
        required = {"ok": False, "error": 145, "stage": "set"}
        if (measured["flat"] != required or measured["parent"] != required
                or measured["control"] != {"ok": True, "error": 0, "stage": "set"}
                or not measured["renameFlat"] or not measured["renameParent"]
                or measured["removeAnchor"] != 32 or measured["renameAnchor"] != 32):
            print("OPEN_FILE_KEEPS_FOLDER " + json.dumps(measured, separators=(",", ":")))
            pytest.exit("The required open-file protection differed. Stop; inspect the numbers.", 1)
    finally:
        kernel.CloseHandle(handle)
    assert not anchor.exists()
    measured["afterClose"] = _junction_in_place(flat, outside)
    if measured["afterClose"]["ok"]:
        tree["links"].append(flat)
    print("OPEN_FILE_KEEPS_FOLDER " + json.dumps(measured, separators=(",", ":")))
    assert measured["afterClose"] == {"ok": True, "error": 0, "stage": "set"}, measured
    _assert_outside_unchanged(tree)


@pytest.mark.skipif(os.name != "nt", reason="Measure in-place reparse data while the helper holds the inner folder")
def test_native_nonempty_flat_folder_cannot_become_a_junction_in_place(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    control = root.parent / ("au-inplace-control-" + uuid.uuid4().hex[:10])
    control.mkdir()
    sleeper = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import sys; sys.stdin.buffer.read(1)"],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    measured = {}
    try:
        with removal.folder(root) as owned:
            with owned.aside(PARK, TOKEN) as park:
                removal.finish_after_exit(root, TOKEN, keep=park.keep, **{
                    **_finish_options(tree), "wait_for": [sleeper.pid], "seconds": 20})
        # All caller handles are closed. The helper alone holds the inner folder.
        measured["control"] = _junction_in_place(control, tree["outside"])
        measured["flat"] = _junction_in_place(root / PARK, tree["outside"])
        if (measured["flat"] != {"ok": False, "error": 145, "stage": "set"}
                or measured["control"] != {"ok": True, "error": 0, "stage": "set"}):
            print("AFTER_EXIT_INPLACE " + json.dumps(measured))
            pytest.exit("The required in-place junction measurement differed. Stop; inspect the numbers.", 1)
        assert sleeper.poll() is None and not tree["report"].exists()
    finally:
        sleeper.stdin.close()
        sleeper.wait(timeout=10)
        # This is either the separate empty control or its junction, never its target.
        os.rmdir(control)
    result = _finish_report(tree["report"])
    measured["report"] = result
    print("AFTER_EXIT_INPLACE " + json.dumps(measured, separators=(",", ":")))
    assert result["reason"] == "" and result["folderRemoved"] and not root.exists(), result
    _assert_outside_unchanged(tree)


@pytest.mark.skipif(os.name != "nt", reason="What the step's file-deleting command does when its path is a link")
def test_native_plain_remove_item_never_deletes_behind_a_link(finish_tree):
    """The step deletes a file with `Remove-Item -LiteralPath <path>`: no -Recurse, no -Force.

    If a program swaps that file for a link between the step's listing and this command, the
    command must delete nothing behind the link. Each kind of link is given to the same command,
    in the same non-interactive Windows PowerShell.
    """
    import _winapi
    from amplifier_web.windows_service import _powershell

    tree, root, outside = finish_tree, finish_tree["root"], finish_tree["outside"]
    (outside / "empty").mkdir()
    tree["outside_before"] = _outside_state(outside)
    _winapi.CreateJunction(str(outside), str(root / "junction-to-full"))
    _winapi.CreateJunction(str(outside / "empty"), str(root / "junction-to-empty"))
    os.symlink(outside, root / "folderlink", target_is_directory=True)
    os.symlink(outside / "keep.txt", root / "filelink")
    os.link(outside / "keep.txt", root / "hardlink")
    names = ["junction-to-full", "junction-to-empty", "folderlink", "filelink", "hardlink"]
    tree["links"] += [root / name for name in names]
    program, gone = _powershell(), {}
    for name in names:
        for ending in (" -ErrorAction SilentlyContinue", ""):  # Both forms that the step uses.
            subprocess.run(
                [program, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                 "Remove-Item -LiteralPath '" + str(root / name) + "'" + ending],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=60, cwd=str(Path(program).parents[2]))
            # After every single command: every outside name, content and attribute is as before.
            _assert_outside_unchanged(tree)
        gone[name] = not os.path.lexists(root / name)
    print("REMOVE_ITEM_LINKS " + json.dumps(gone))
    # A link to a file goes as a link. The outside file keeps its content and is still one file.
    assert gone["filelink"] and gone["hardlink"]
    assert (outside / "keep.txt").read_text() == "keep outside" and (outside / "keep.txt").stat().st_nlink == 1


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell hard-link attribute preservation")
@pytest.mark.parametrize("location", ["flat", "root-files"])
def test_native_finish_never_clears_attributes_through_a_hard_link(finish_tree, location):
    import ctypes
    tree, root = finish_tree, finish_tree["root"]
    outside = tree["outside"] / "keep.txt"
    os.chmod(outside, stat.S_IREAD)
    tree["outside_before"] = _outside_state(tree["outside"])
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.SetFileAttributesW.restype = ctypes.c_int
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            parent = root / PARK if location == "flat" else root
            hardlink, readonly, hidden, system = (parent / name for name in ("1", "2", "3", "4"))
            os.link(outside, hardlink)
            assert hardlink.stat().st_nlink == 2
            readonly.write_text("single read-only file")
            os.chmod(readonly, stat.S_IREAD)
            hidden.write_text("single hidden file")
            system.write_text("single system file")
            assert kernel.SetFileAttributesW(str(hidden), stat.FILE_ATTRIBUTE_HIDDEN)
            assert kernel.SetFileAttributesW(str(system), stat.FILE_ATTRIBUTE_SYSTEM)
            before = {path: (path.read_bytes(), path.stat().st_file_attributes)
                      for path in (hardlink, readonly, hidden, system)}
            removal.finish_after_exit(root, TOKEN, keep=park.keep, **_finish_options(tree))
    result = _finish_report(tree["report"])
    assert result["reason"] == "" and not result["folderRemoved"], result
    assert {path: (path.read_bytes(), path.stat().st_file_attributes) for path in before} == before
    assert set(map(str, before)) <= set(result["left"])
    assert outside.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY and outside.stat().st_nlink == 2
    assert (root / "unified-uninstall.json").exists() and (root / COMPLETE).exists()
    _wait_until_gone(root / (TOKEN + ".hold"))
    if location == "flat":
        assert sorted(path.name for path in (root / PARK).iterdir()) == ["1", "2", "3", "4"]
    else:
        assert not (root / PARK).exists()
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_HARDLINK " + json.dumps({"location": location, "links": 2,
                                             "outsideAttributes": outside.stat().st_file_attributes,
                                             "report": result}))


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell removes only empty folders and folder links")
def test_native_finish_removes_named_folders_but_never_a_file_or_nonempty_folder(finish_tree):
    import _winapi

    tree, root = finish_tree, finish_tree["root"]
    (root / "nonempty").mkdir()
    (root / "nonempty" / "inside.txt").write_text("not entered")
    (root / "empty").mkdir()
    (root / "plain.txt").write_text("named file")
    junction = root / "junction"
    _winapi.CreateJunction(str(tree["outside"]), str(junction))
    tree["links"].append(junction)
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            removal.finish_after_exit(root, TOKEN, keep=park.keep, **{
                **_finish_options(tree), "folders": ["junction", "empty", "nonempty", "plain.txt"]})
    result = _finish_report(tree["report"])
    _wait_until_gone(root / (TOKEN + ".hold"))
    assert result["reason"] == "" and not result["folderRemoved"], result
    assert sorted(os.listdir(root)) == ["nonempty", "plain.txt", COMPLETE, "unified-uninstall.json"]
    assert (root / "plain.txt").read_text() == "named file"
    assert (root / "nonempty" / "inside.txt").read_text() == "not entered"
    assert set(result["left"]) == {str(root / "nonempty"), str(root / "plain.txt")}
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_NAMED " + json.dumps(result))


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell leaves a foreign entry and its journal")
def test_native_finish_leaves_a_foreign_top_level_entry(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    (root / "mine.txt").write_text("not Amplifier's")
    records = {name: (root / name).read_bytes() for name in ("unified-uninstall.json", COMPLETE)}
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            removal.finish_after_exit(root, TOKEN, keep=park.keep, **_finish_options(tree))
    result = _finish_report(tree["report"])
    assert result["reason"] == "" and not result["folderRemoved"], result
    _wait_until_gone(root / (TOKEN + ".hold"))
    assert sorted(os.listdir(root)) == ["mine.txt", COMPLETE, "unified-uninstall.json"]
    assert (root / "mine.txt").read_text() == "not Amplifier's"
    assert result["left"] == [str(root / "mine.txt")] and result["leftCount"] == 1
    _wait_until_gone(root / PARK / (TOKEN + ".hold"))
    assert {name: (root / name).read_bytes() for name in records} == records
    assert not (root / PARK).exists() and not (root / PARK / TOKEN).exists()
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_JOURNAL_KEPT " + json.dumps({
        "journalUnchanged": True, "completeUnchanged": True, "holdGone": True,
        "flatGone": True, "anchorGone": True, "report": result}, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell refuses a damaged completion record")
@pytest.mark.parametrize("kind", ["folder", "filelink", "readonly"])
def test_native_finish_refuses_a_completion_record_that_is_not_a_plain_writable_file(finish_tree, kind):
    tree, root = finish_tree, finish_tree["root"]
    record = root / COMPLETE
    record.unlink()
    if kind == "folder":
        record.mkdir()
    elif kind == "filelink":
        os.symlink(tree["outside"] / "keep.txt", record)
        tree["links"].append(record)
    else:
        record.write_text('{"schema": 1}')
        os.chmod(record, stat.S_IREAD)
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            removal.finish_after_exit(root, TOKEN, keep=park.keep, **_finish_options(tree))
    result = _finish_report(tree["report"])
    assert result["reason"] == "Record refused" and not result["folderRemoved"], result
    assert os.path.lexists(record) and (root / "unified-uninstall.json").exists()
    _wait_until_gone(root / PARK / (TOKEN + ".hold"))
    _wait_until_gone(root / (TOKEN + ".hold"))
    assert sorted(os.listdir(root)) == sorted([PARK, COMPLETE, "unified-uninstall.json"])
    assert list((root / PARK).iterdir()) == []
    if kind == "folder":
        assert record.is_dir() and list(record.iterdir()) == []
    elif kind == "filelink":
        assert record.is_symlink()
    else:
        assert record.read_text() == '{"schema": 1}'
        assert record.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_RECORD " + json.dumps({"kind": kind, "report": result}))


@pytest.mark.skipif(os.name != "nt", reason="The last step must never delete an own file at the top level")
def test_native_finish_keeps_a_leftover_own_file_and_both_records(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    (root / "install.log").write_text("own file held earlier")
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            removal.finish_after_exit(root, TOKEN, keep=park.keep, **_finish_options(tree))
    result = _finish_report(tree["report"])
    assert not result["folderRemoved"] and result["reason"] == "", result
    _wait_until_gone(root / (TOKEN + ".hold"))
    assert sorted(os.listdir(root)) == ["install.log", COMPLETE, "unified-uninstall.json"]
    assert (root / "install.log").read_text() == "own file held earlier"
    assert result["left"] == [str(root / "install.log")]
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_OWN_FILE " + json.dumps(result))


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell stops when another removal replaces the journal")
def test_native_finish_stops_when_the_journal_token_changes_while_it_waits(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    (root / "named.txt").write_text("must stay")
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            (root / PARK / "9").write_text("witness the first pass")
            removal.finish_after_exit(root, TOKEN, keep=park.keep, **{
                **_finish_options(tree), "wait_for": [os.getpid()], "seconds": 20})
    # Wait for a deletion, not just ready: the token must change during a real wait.
    witness = root / PARK / "9"
    deadline = time.monotonic() + 10
    while witness.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not witness.exists(), "The first pass never reached the flat folder."
    # A live caller keeps the helper waiting. No record deletion can have run yet.
    assert not tree["report"].exists() and (root / "named.txt").exists()
    (root / "unified-uninstall.json").write_text(json.dumps({
        "schema": 1, "choice": "delete", "token": "f" * 32}))
    (root / PARK / "later.txt").write_text("after the token changed")
    # Its own open anchor is not readable and is deleted by Windows at helper exit.
    anchor = root / PARK / (TOKEN + ".hold")
    root_kept = root / (TOKEN + ".hold")
    before = _plain_tree(root, exclude=(anchor, root_kept))
    result = _finish_report(tree["report"])
    assert result["reason"] == "Root or token refused" and not result["folderRemoved"], result
    assert result["passes"] >= 1
    _wait_until_gone(anchor)
    _wait_until_gone(root_kept)
    assert _plain_tree(root) == before
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_NEW_TOKEN " + json.dumps(result))


@pytest.mark.skipif(os.name != "nt", reason="Real PowerShell with a zero retry budget still walks no tree")
def test_native_finish_with_no_retry_time_keeps_a_nonempty_folder(finish_tree):
    tree, root = finish_tree, finish_tree["root"]
    (root / "nonempty").mkdir()
    (root / "nonempty" / "keep.txt").write_text("no tree walk")
    with removal.folder(root) as owned:
        with owned.aside(PARK, TOKEN) as park:
            removal.finish_after_exit(root, TOKEN, keep=park.keep, **{
                **_finish_options(tree), "folders": ["nonempty"], "seconds": 0})
    result = _finish_report(tree["report"])
    assert result["passes"] == 1 and result["reason"] == "" and not result["folderRemoved"], result
    assert (root / "nonempty" / "keep.txt").read_text() == "no tree walk"
    assert (root / "unified-uninstall.json").exists()
    _assert_outside_unchanged(tree)
    print("AFTER_EXIT_ZERO_TIME " + json.dumps(result))
