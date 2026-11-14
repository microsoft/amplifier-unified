"""Published component selection stays independent of the author's next edits."""
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from test_scheduling_release import candidate, release  # Existing real Git/artifact fixture.

SPEC = importlib.util.spec_from_file_location("scheduling_delivery", Path(__file__).parents[1] / "scripts/scheduling_delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


@pytest.fixture
def selected(candidate):
    root, revision, dist, source = candidate
    wheel = next(dist.glob("*.whl"))
    # The producer fixture is deliberately minimal; real published wheels carry a license.
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("amplifier_scheduling-0.1.0.dist-info/licenses/LICENSE", "MIT\n")
    (dist / release.RECEIPT).unlink(); (dist / "SHA256SUMS").unlink()
    release.seal(root, revision, dist)
    (root / "pyproject.toml").write_text(
        '[project]\nname="amplifier-unified"\nversion="1.0.0"\ndependencies=[]\n'
        '[tool.hatch.build.targets.wheel]\n'
        'packages=["components/scheduling-delivery/amplifier_scheduling"]\n')
    checksum = delivery.sha((dist / release.RECEIPT).read_bytes())
    delivery.select(root, dist, revision, checksum)
    return root, revision, dist, source, checksum


def test_selection_uses_artifact_while_author_can_advance(selected):
    root, _, _, source, _ = selected
    before = delivery.verify(root)
    author = root / release.PROJECT / "amplifier_scheduling/__init__.py"
    author.write_text('value = "future unpublished implementation"\n')
    assert delivery.verify(root) == before
    copied = root / delivery.DELIVERY / "amplifier_scheduling/__init__.py"
    assert copied.read_text() == source["amplifier_scheduling/__init__.py"]
    assert not list((root / delivery.DELIVERY).glob("*.dist-info"))


def test_artifact_selection_does_not_need_producer_git_history(selected, tmp_path):
    root, revision, dist, _, checksum = selected
    plain = tmp_path / "consumer-without-git"; (plain / "components").mkdir(parents=True)
    shutil.copy2(root / "pyproject.toml", plain / "pyproject.toml")
    assert not (plain / ".git").exists() and not (plain / release.PROJECT).exists()
    delivery.select(plain, dist, revision, checksum)
    assert delivery.verify(plain) == delivery.verify(root)


@pytest.mark.parametrize("fault", ["changed", "missing", "extra", "link", "receipt"])
def test_modified_selection_is_refused(selected, fault):
    root, *_ = selected
    package = root / delivery.DELIVERY / delivery.PACKAGE
    if fault == "changed":
        (package / "__init__.py").write_text("changed\n")
    elif fault == "missing":
        (root / delivery.DELIVERY / "LICENSE").unlink()
    elif fault == "extra":
        (package / "unselected.py").write_text("unexpected\n")
    elif fault == "link":
        (package / "foreign").symlink_to(root, target_is_directory=True)
    else:
        path = root / delivery.RECORD
        value = json.loads(path.read_bytes()); value["source"]["revision"] = "a" * 40
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        delivery.verify(root)


def test_python_bytecode_does_not_change_selected_source_identity(selected):
    root, *_ = selected
    cache = root / delivery.DELIVERY / delivery.PACKAGE / "__pycache__"
    cache.mkdir(); (cache / "__init__.cpython-313.pyc").write_bytes(b"development cache")
    delivery.verify(root)


def test_app_cannot_package_author_and_selected_copy_together(selected):
    root, *_ = selected
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace(
        'packages=[', 'packages=["components/scheduling/amplifier_scheduling",'))
    with pytest.raises(ValueError, match="only the selected"):
        delivery.verify(root)


def test_selected_runtime_dependency_is_required_by_app(selected):
    root, *_ = selected
    path = root / delivery.RECORD
    record = json.loads(path.read_bytes())
    record["runtimeRequirements"] = ['tzdata; sys_platform == "win32"']
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="runtime requirement"):
        delivery.verify(root)
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace('dependencies=[]',
        'dependencies=["tzdata; sys_platform == \'win32\'"]'))
    delivery.verify(root)


@pytest.mark.parametrize("fault", ["receipt", "unsafe-wheel"])
def test_bad_publication_cannot_replace_existing_selection(selected, fault):
    root, revision, dist, _, checksum = selected
    before = delivery.inventory(root / delivery.DELIVERY)
    record = (root / delivery.RECORD).read_bytes()
    if fault == "receipt":
        checksum = "f" * 64
    else:
        with zipfile.ZipFile(next(dist.glob("*.whl")), "a") as archive:
            archive.writestr("../outside.py", "unsafe\n")
    with pytest.raises(ValueError):
        delivery.select(root, dist, revision, checksum)
    assert delivery.inventory(root / delivery.DELIVERY) == before
    assert (root / delivery.RECORD).read_bytes() == record


def test_modified_delivery_is_not_overwritten_by_reselection(selected):
    root, revision, dist, _, checksum = selected
    path = root / delivery.DELIVERY / delivery.PACKAGE / "__init__.py"
    path.write_text("local work\n")
    with pytest.raises(ValueError, match="locally modified"):
        delivery.select(root, dist, revision, checksum)
    assert path.read_text() == "local work\n"


def test_interrupted_swap_restores_original_selection(selected, monkeypatch):
    root, revision, dist, _, checksum = selected
    destination = root / delivery.DELIVERY
    before = delivery.inventory(destination); record = (root / delivery.RECORD).read_bytes()
    rename = Path.rename
    interrupted = False

    def interrupt_after_old_move(path, target):
        nonlocal interrupted
        result = rename(path, target)
        if path == destination and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("interrupted immediately after old selection moved")
        return result

    monkeypatch.setattr(Path, "rename", interrupt_after_old_move)
    with pytest.raises(KeyboardInterrupt):
        delivery.select(root, dist, revision, checksum)
    assert delivery.inventory(destination) == before
    assert (root / delivery.RECORD).read_bytes() == record
