"""Artifact custody and rollback, not a second SDK/type/example test suite."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("shell_sdk_delivery", ROOT / "scripts/shell_sdk_delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def assets(directory, *, version="1.0.0", extra=None):
    directory.mkdir()
    files = {name: (ROOT / delivery.SDK / name).read_bytes() for name in delivery.PACKAGE_FILES}
    package = json.loads(files["package.json"])
    package["version"] = version
    files["package.json"] = delivery.encoded(package)
    filename = f"amplifier-shell-sdk-{version}.tgz"
    with tarfile.open(directory / filename, "w:gz") as archive:
        for name, raw in files.items():
            member = tarfile.TarInfo("package/" + name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        if extra is not None:
            archive.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
    receipt = copy.deepcopy(json.loads((ROOT / delivery.RECORD).read_text())["qualification"]["receipt"])
    receipt.update(version=version, tarball=filename,
                   tarballSha256=delivery.sha((directory / filename).read_bytes()),
                   files=[{"path": name, "size": len(raw)} for name, raw in files.items()])
    (directory / "qualification.json").write_bytes(delivery.encoded(receipt))
    (directory / "SHA256SUMS").write_text(receipt["tarballSha256"] + "  " + filename + "\n")
    return directory


def select(root, dist):
    receipt = json.loads((dist / "qualification.json").read_text())
    return delivery.select(root, dist, "1" * 40, "2" * 40, receipt["tarballSha256"],
                           delivery.sha((dist / "qualification.json").read_bytes()))


@pytest.fixture
def selected(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    dist = assets(tmp_path / "assets")
    select(root, dist)
    return root, dist


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_selected_public_package_and_legacy_example_are_exact_without_git(selected):
    root, _ = selected
    record = delivery.verify(root)
    assert not (root / ".git").exists()
    assert set(record["files"]) == set(delivery.PACKAGE_FILES)
    for name in delivery.EXAMPLE_FILES:
        assert (root / delivery.EXAMPLE / name).read_bytes() == (root / delivery.SDK / delivery.EXAMPLE / name).read_bytes()
    assert (root / delivery.SDK / "ORIGIN.json").read_bytes() == (ROOT / delivery.SDK / "ORIGIN.json").read_bytes()
    assert not list((root / "components").glob(".shell-sdk-selection-*"))


@pytest.mark.parametrize("path", [delivery.SDK + "/index.js", delivery.SDK + "/LICENSE",
                                  delivery.EXAMPLE + "/index.js", delivery.RECORD])
def test_changed_selected_bytes_refuse_verification_and_reselection(selected, path):
    root, dist = selected
    target = root / path
    target.write_bytes(target.read_bytes() + b"changed")
    before = snapshot(root)
    with pytest.raises(ValueError):
        delivery.verify(root)
    with pytest.raises(ValueError):
        select(root, dist)
    assert snapshot(root) == before


@pytest.mark.parametrize("kind", ["archive", "qualification", "checksum", "extra"])
def test_original_assets_are_checked_before_changing_selection(selected, kind):
    root, dist = selected
    before = snapshot(root)
    record = delivery.verify(root)
    target = {"archive": "amplifier-shell-sdk-1.0.0.tgz", "qualification": "qualification.json",
              "checksum": "SHA256SUMS", "extra": "unexpected.txt"}[kind]
    with (dist / target).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError):
        delivery.select(root, dist, "1" * 40, "2" * 40, record["artifact"]["sha256"],
                        record["qualification"]["sha256"])
    assert snapshot(root) == before


@pytest.mark.parametrize("kind", ["traversal", "link", "duplicate", "extra"])
def test_nonregular_or_unexpected_tar_members_do_not_create_delivery(tmp_path, kind):
    member = tarfile.TarInfo({"traversal": "package/../../outside", "link": "package/link",
                              "duplicate": "package/index.js", "extra": "package/unselected.js"}[kind])
    member.size = 1
    if kind == "link":
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
    dist = assets(tmp_path / "assets", extra=member)
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    with pytest.raises(ValueError, match="tarball member"):
        select(root, dist)
    assert snapshot(root) == {}
    assert not (tmp_path / "outside").exists()


def test_initial_selection_does_not_overwrite_unselected_authored_source(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    target = root / delivery.SDK / "index.js"
    target.parent.mkdir(parents=True)
    target.write_text("unselected work")
    with pytest.raises(ValueError, match="Preserve unselected"):
        select(root, assets(tmp_path / "assets"))
    assert target.read_text() == "unselected work"


def test_failed_receipt_commit_restores_both_delivery_directories(selected, tmp_path, monkeypatch):
    root, _ = selected
    before = snapshot(root)
    successor = assets(tmp_path / "successor", version="1.0.1")
    replace = Path.replace

    def fail_record(path, target):
        if target == root / delivery.RECORD:
            raise OSError("receipt commit refused")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_record)
    with pytest.raises(OSError, match="receipt commit refused"):
        select(root, successor)
    assert snapshot(root) == before
    assert delivery.verify(root)["source"]["version"] == "1.0.0"
    assert not list((root / "components").glob(".shell-sdk-selection-*"))


@pytest.mark.parametrize("interruption", [OSError, KeyboardInterrupt])
def test_unrestored_selection_retains_recoverable_original_bytes(selected, tmp_path, monkeypatch, interruption):
    root, _ = selected
    before = snapshot(root)
    successor = assets(tmp_path / "successor", version="1.0.1")
    rename = Path.rename

    def fail_record(path, target):
        raise OSError("receipt commit refused")

    def fail_restore(path, target):
        if path.parent.name == "previous":
            raise interruption("restoration refused")
        return rename(path, target)

    monkeypatch.setattr(Path, "replace", fail_record)
    monkeypatch.setattr(Path, "rename", fail_restore)
    expected = ValueError if interruption is OSError else interruption
    with pytest.raises(expected):
        select(root, successor)
    [recovery] = (root / "components").glob(".shell-sdk-selection-*")
    assert (recovery / "previous/record.json").read_bytes() == before[delivery.RECORD]
    for index, directory in enumerate((delivery.SDK, delivery.EXAMPLE)):
        for name in delivery.PACKAGE_FILES if index == 0 else delivery.EXAMPLE_FILES:
            assert (recovery / "previous" / str(index) / name).read_bytes() == before[directory + "/" + name]


@pytest.mark.parametrize("change", [None, "extra", "changed", "missing-record"])
def test_wheel_preserves_exact_sdk_example_and_receipt_members(selected, tmp_path, change):
    root, _ = selected
    members = {"amplifier_web/bundle_data/" + directory + "/" + name: (root / directory / name).read_bytes()
               for directory, names in ((delivery.SDK, delivery.PACKAGE_FILES),
                                        (delivery.EXAMPLE, delivery.EXAMPLE_FILES)) for name in names}
    members[delivery.INSTALLED_RECORD] = (root / delivery.RECORD).read_bytes()
    if change == "extra":
        members["amplifier_web/bundle_data/packages/shell-sdk/node_modules/unowned.js"] = b"unowned"
    elif change == "changed":
        members["amplifier_web/bundle_data/packages/shell-sdk/index.js"] += b"changed"
    elif change == "missing-record":
        del members[delivery.INSTALLED_RECORD]
    wheel = tmp_path / "app.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    if change is None:
        delivery.verify_wheel(root, wheel)
    else:
        with pytest.raises(ValueError):
            delivery.verify_wheel(root, wheel)
