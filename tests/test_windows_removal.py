"""Link-safe folder removal: portable walk logic, and the real thing on native Windows."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
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
