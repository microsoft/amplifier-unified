"""Private Git unpacking controls; native extraction and shell use are CI evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


git = load("private_git", SCRIPTS / "windows_private_git.py")
builder = load("private_git_builder", SCRIPTS / "windows_bootstrap_native.py")
TOOLS = {"cmd/git.exe": b"MZgit", "bin/bash.exe": b"MZbash", "bin/sh.exe": b"MZsh",
         "usr/bin/bash.exe": b"MZreal"}


def archive(path, *extra, files=TOOLS):
    with tarfile.open(path, "w:bz2") as target:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            target.addfile(info, io.BytesIO(data))
        for info, data in extra:
            target.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def entry(name, kind=tarfile.REGTYPE, link="", data=b""):
    info = tarfile.TarInfo(name)
    info.type, info.linkname, info.size = kind, link, len(data)
    return info, data if kind == tarfile.REGTYPE else None


def test_complete_tree_is_created_exclusively_with_copies_and_runtime_links(tmp_path):
    source = archive(tmp_path / "git.tar.bz2",
                     entry("tmp", tarfile.DIRTYPE), entry("dev/shm", tarfile.DIRTYPE),
                     entry("libexec/git.exe", tarfile.LNKTYPE, "cmd/git.exe"),
                     entry("dev/fd", tarfile.SYMTYPE, "/proc/self/fd"),
                     entry("etc/mtab", tarfile.SYMTYPE, "/proc/mounts"))
    target = tmp_path / "git"
    target.mkdir()
    assert git.extract(source, target) == {"entries": 9, "bytes": 26}
    assert (target / "tmp").is_dir() and (target / "dev/shm").is_dir()
    assert (target / "bin/bash.exe").read_bytes() == b"MZbash"
    copy = target / "libexec/git.exe"
    # A separate ordinary file: the app's readers refuse multiply linked files.
    assert copy.read_bytes() == b"MZgit" and copy.stat().st_nlink == 1
    link = target / "dev/fd"
    assert not link.is_symlink()  # Never a link that Windows itself would follow.
    assert link.read_bytes() == b"!<symlink>\xff\xfe" + "/proc/self/fd".encode("utf-16-le") + b"\0\0"
    with pytest.raises(ValueError, match="empty"):
        git.extract(source, target)
    assert (target / "bin/bash.exe").read_bytes() == b"MZbash"


@pytest.mark.parametrize("extra, message", [
    (entry("../escape.exe", data=b"x"), "unsafe entry name"),
    (entry("usr/./bin/tool.exe", data=b"x"), "unsafe entry name"),
    (entry("C:/Windows/tool.exe", data=b"x"), "unsafe entry name"),
    (entry("usr/bin/con.txt", data=b"x"), "unsafe entry name"),
    (entry("usr/bin/trailing. ", data=b"x"), "unsafe entry name"),
    (entry("usr/" + "n" * 117, data=b"x"), "unsafe entry name"),
    (entry("BIN/BASH.EXE", data=b"x"), "repeats an entry"),
    (entry("usr/bin/link", tarfile.SYMTYPE, "/etc/passwd"), "unexpected link"),
    (entry("dev/fd", tarfile.SYMTYPE, "../../outside"), "unexpected link"),
    (entry("usr/bin/copy.exe", tarfile.LNKTYPE, "../outside.exe"), "unsafe entry name"),
    (entry("usr/bin/copy.exe", tarfile.LNKTYPE, "usr/bin/absent.exe"), "links outside"),
    (entry("dev/null", tarfile.CHRTYPE), "special file"),
    (entry("usr/bin/pipe", tarfile.FIFOTYPE), "special file"),
])
def test_unsafe_archive_entries_are_refused(tmp_path, extra, message):
    source = archive(tmp_path / "git.tar.bz2", extra)
    target = tmp_path / "git"
    target.mkdir()
    with pytest.raises(ValueError, match=message):
        git.extract(source, target)
    assert not (tmp_path / "escape.exe").exists() and not (tmp_path / "outside.exe").exists()


def test_incomplete_oversized_or_unprepared_destinations_are_refused(tmp_path, monkeypatch):
    target = tmp_path / "git"
    with pytest.raises(ValueError, match="must already exist"):
        git.extract(archive(tmp_path / "ok.tar.bz2"), target)
    target.mkdir()
    partial = {name: data for name, data in TOOLS.items() if name != "bin/bash.exe"}
    with pytest.raises(ValueError, match="lacks Git or its shell"):
        git.extract(archive(tmp_path / "partial.tar.bz2", files=partial), target)
    for limit, message in (("BYTES", "larger than its budget"), ("ENTRIES", "too many entries")):
        other = tmp_path / limit
        other.mkdir()
        monkeypatch.setattr(git, limit, 3)
        with pytest.raises(ValueError, match=message):
            git.extract(archive(tmp_path / (limit + ".tar.bz2")), other)
        monkeypatch.undo()


def test_entry_refuses_any_archive_other_than_the_pinned_release(tmp_path, capsys):
    source = archive(tmp_path / "git.tar.bz2")
    target = tmp_path / "git"
    target.mkdir()
    with pytest.raises(SystemExit, match="not the pinned official release"):
        git.main(["--archive", str(source), "--destination", str(target)])
    assert list(target.iterdir()) == []
    git.PIN = {**git.PIN, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    try:
        git.main(["--archive", str(source), "--destination", str(target)])
    finally:
        git.PIN = load("private_git_original", SCRIPTS / "windows_private_git.py").PIN
    assert json.loads(capsys.readouterr().out) == {
        "ok": True, "version": "2.56.0.windows.4", "entries": 4, "bytes": 21}


def test_setup_embeds_the_one_exact_official_pin_and_unpacker():
    import base64
    pins = builder.sources(SCRIPTS)["NativePins.cs"].decode()
    for name, key in (("ShellGitVersion", "version"), ("ShellGitUrl", "url"), ("ShellGitHash", "sha256"),
                      ("ShellGitLicenseUrl", "license"), ("ShellGitLicenseHash", "licenseSha256")):
        assert f'{name}="{git.PIN[key]}"' in pins
    assert git.PIN["url"] == ("https://github.com/git-for-windows/git/releases/download/"
                              "v2.56.0.windows.4/Git-2.56.0.4-64-bit.tar.bz2")
    assert git.PIN["sha256"] == "0dae059764ba7c447c9604eb6cb0f4afaf2d90b003ecf1c8cf2dbe5a13e4442c"
    source = (SCRIPTS / "windows_private_git.py").read_text(encoding="utf-8").encode()
    assert 'ShellGitUnpacker="' + base64.b64encode(source).decode() + '"' in pins
    # Standard library only: it runs with -I -S before any package exists.
    imported = {line.split()[1].split(".")[0] for line in source.decode().splitlines()
                if line.lstrip().startswith(("import ", "from "))}
    assert imported <= {"__future__", "argparse", "hashlib", "json", "pathlib", "re", "shutil",
                        "sys", "tarfile", "ctypes"}


@pytest.mark.parametrize("field, value", [
    ("url", "https://example.invalid/Git-2.56.0.4-64-bit.tar.bz2"),
    ("url", "https://github.com/git-for-windows/git/releases/latest/download/Git-2.56.0.4-64-bit.tar.bz2"),
    ("url", "https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/PortableGit-2.56.0.4-64-bit.7z.exe"),
    ("sha256", "0" * 63), ("version", "latest"),
    ("license", "https://raw.githubusercontent.com/git-for-windows/git/main/COPYING"),
    ("licenseSha256", "A" * 64),
])
def test_setup_build_refuses_a_moving_or_unofficial_pin(tmp_path, field, value):
    for name in ("install.ps1", "windows_bootstrap_native.cs", "windows_bootstrap_capsule.py"):
        (tmp_path / name).write_bytes((SCRIPTS / name).read_bytes())
    source = (SCRIPTS / "windows_private_git.py").read_text(encoding="utf-8")
    original = git.PIN[field]
    assert source.count('"' + original + '"') == 1
    (tmp_path / "windows_private_git.py").write_text(
        source.replace('"' + original + '"', '"' + value + '"'), encoding="utf-8")
    with pytest.raises(ValueError, match="exact official release asset"):
        builder.prepare(tmp_path, tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_consumer_setup_unpacks_checks_and_proves_the_shell_before_the_app():
    source = (SCRIPTS / "windows_bootstrap_native.cs").read_text(encoding="utf-8")
    consumer = source.split("static string InstallConsumer(", 1)[1].split("static int ConsumerWindow(", 1)[0]
    order = [consumer.index(text) for text in (
        "CheckHash(gitArchive,NativePins.ShellGitHash)", 'Stage="Installing Python"',
        "PrivateGit(basePython,gitArchive,env)", 'Stage="Installing Amplifier (this takes a few minutes)"')]
    assert order == sorted(order)
    assert "NativePins.GitUrl" not in consumer and "MinGit" not in consumer
    helper = source.split("static string PrivateGit(", 1)[1].split("static void Platform()", 1)[0]
    steps = [helper.index(text) for text in (
        "Storage.CreateDirectory(destination)", '"-I","-S","-B",unpacker',
        "CheckHash(notice,NativePins.ShellGitLicenseHash)",
        'Child(bash,new[]{"-c","command -v git && git --version"},environment)',
        'Regex.IsMatch(shell[0],"^/(ucrt64/bin|cmd|bin)/git$",RegexOptions.CultureInvariant)',
        'shell[1]=="git version "+NativePins.ShellGitVersion')]
    assert steps == sorted(steps)
    # Setup children never run inherited shell startup code or exported functions.
    child = source.split("static string Child(", 1)[1].split("static string Helper(", 1)[0]
    for name in ('upper.StartsWith("BASH_FUNC_")', '"BASH_ENV"'):
        assert name in child
