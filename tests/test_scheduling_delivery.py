"""Published component selection stays independent of the author's next edits."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from test_scheduling_release import candidate, release, make_candidate  # Existing real Git/artifact fixture.

SPEC = importlib.util.spec_from_file_location("scheduling_delivery", Path(__file__).parents[1] / "scripts/scheduling_delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


@pytest.fixture(params=["monorepo", "repository-root"])
def selected(candidate, request):
    root, revision, dist, source = candidate
    wheel = next(dist.glob("*.whl"))
    # The producer fixture is deliberately minimal; real published wheels carry a license.
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("amplifier_scheduling-0.1.0.dist-info/licenses/LICENSE", "MIT\n")
    (dist / release.RECEIPT).unlink(); (dist / "SHA256SUMS").unlink()
    release.seal(root, revision, dist)
    if request.param == "repository-root":
        # The consumer trusts the selected receipt checksum, not a producer checkout.
        # Exercise both published receipt layouts with the same synthetic artifacts.
        path = dist / release.RECEIPT
        receipt = json.loads(path.read_bytes())
        receipt.update(repository="bkrabach/amplifier-platform-scheduling",
                       component="scheduling", subdirectory=".")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        (dist / "SHA256SUMS").write_text("".join(
            f"{release.sha((dist / name).read_bytes())}  {name}\n"
            for name in (*release.artifact_names(receipt), release.RECEIPT)))
    (root / "pyproject.toml").write_text(
        '[project]\nname="amplifier-unified"\nversion="1.0.0"\ndependencies=[]\n'
        '[tool.hatch.build.targets.wheel]\n'
        'packages=["components/scheduling-delivery/amplifier_scheduling"]\n')
    checksum = delivery.sha((dist / release.RECEIPT).read_bytes())
    delivery.select(root, dist, revision, checksum)
    return root, revision, dist, source, checksum


def test_selection_uses_artifact_while_author_can_advance(selected):
    root, *_ = selected
    before = delivery.verify(root)
    author = root / release.PROJECT / "amplifier_scheduling/__init__.py"
    published = author.read_text()
    author.write_text('value = "future unpublished implementation"\n')
    assert delivery.verify(root) == before
    copied = root / delivery.DELIVERY / "amplifier_scheduling/__init__.py"
    assert copied.read_text() == published
    assert not list((root / delivery.DELIVERY).glob("*.dist-info"))


def test_artifact_selection_does_not_need_producer_git_history(selected, tmp_path):
    root, revision, dist, _, checksum = selected
    plain = tmp_path / "consumer-without-git"; (plain / "components").mkdir(parents=True)
    shutil.copy2(root / "pyproject.toml", plain / "pyproject.toml")
    assert not (plain / ".git").exists() and not (plain / release.PROJECT).exists()
    delivery.select(plain, dist, revision, checksum)
    assert delivery.verify(plain) == delivery.verify(root)


def test_root_receipt_can_roll_back_to_original_artifact(selected):
    root, revision, dist, _, _ = selected
    source = delivery.verify(root)["source"]
    if source["subdirectory"] == ".":
        assert source["component"] == "scheduling"
        assert source["repository"] == "bkrabach/amplifier-platform-scheduling"
    (dist / release.RECEIPT).unlink(); (dist / "SHA256SUMS").unlink()
    original = release.seal(root, revision, dist)
    checksum = delivery.sha((dist / release.RECEIPT).read_bytes())
    delivery.select(root, dist, revision, checksum)
    assert delivery.verify(root)["source"] == original
    assert original["subdirectory"] == "components/scheduling"
    assert "component" not in original


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


@pytest.fixture(params=["monorepo", "repository-root"])
def worktrees_selection(tmp_path, request):
    root, revision, dist, _ = make_candidate(tmp_path, "worktrees")
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    info = "amplifier_worktrees-0.1.0.dist-info/"
    members[info + "licenses/LICENSE"] = b"MIT\n"
    members[info + "METADATA"] += b"Requires-Dist: filelock<5,>=4.2.1\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    (dist / "worktrees-source.json").unlink(); (dist / "SHA256SUMS").unlink()
    release.seal(root, revision, dist, component="worktrees")
    if request.param == "repository-root":
        path = dist / "worktrees-source.json"
        receipt = json.loads(path.read_bytes())
        receipt.update(repository="bkrabach/amplifier-platform-worktrees",
                       component="worktrees", subdirectory=".")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        (dist / "SHA256SUMS").write_text("".join(
            f"{release.sha((dist / name).read_bytes())}  {name}\n"
            for name in (*release.artifact_names(receipt), "worktrees-source.json")))
    (root / "pyproject.toml").write_text(
        '[project]\nname="amplifier-unified"\nversion="1.0.0"\n'
        'dependencies=["filelock>=4.2.1,<5"]\n'
        '[tool.hatch.build.targets.wheel]\n'
        'packages=["components/worktrees-delivery/amplifier_worktrees"]\n')
    checksum = delivery.sha((dist / "worktrees-source.json").read_bytes())
    delivery.select(root, dist, revision, checksum, component="worktrees")
    return root, revision, dist, checksum


def test_worktrees_artifact_remains_selected_without_author_or_git(worktrees_selection, tmp_path):
    root, revision, dist, checksum = worktrees_selection
    before = delivery.verify(root, component="worktrees")
    (root / "components/worktrees/amplifier_worktrees/__init__.py").write_text("future = True\n")
    assert delivery.verify(root, component="worktrees") == before
    plain = tmp_path / "consumer"; (plain / "components").mkdir(parents=True)
    shutil.copy2(root / "pyproject.toml", plain / "pyproject.toml")
    delivery.select(plain, dist, revision, checksum, component="worktrees")
    assert delivery.verify(plain, component="worktrees") == before
    assert not (plain / "components/worktrees").exists() and not (plain / ".git").exists()
    assert before["runtimeRequirements"] == ["filelock<5,>=4.2.1"]
    (plain / "pyproject.toml").write_text((plain / "pyproject.toml").read_text().replace(
        '"filelock>=4.2.1,<5"', '"filelock>=4.2.1,<6"'))
    with pytest.raises(ValueError, match="runtime requirement"):
        delivery.verify(plain, component="worktrees")


def test_worktrees_public_receipt_can_restore_original_selection(worktrees_selection):
    root, revision, dist, _ = worktrees_selection
    original_runtime = delivery.inventory(root / "components/worktrees-delivery")
    (dist / "worktrees-source.json").unlink(); (dist / "SHA256SUMS").unlink()
    original = release.seal(root, revision, dist, component="worktrees")
    checksum = delivery.sha((dist / "worktrees-source.json").read_bytes())
    delivery.select(root, dist, revision, checksum, component="worktrees")
    assert delivery.verify(root, component="worktrees")["source"] == original
    assert original["subdirectory"] == "components/worktrees"
    assert delivery.inventory(root / "components/worktrees-delivery") == original_runtime


@pytest.fixture
def selected_pair(tmp_path):
    """The app-selected records suffice; future authoring trees are not inputs."""
    project = Path(__file__).parents[1]
    root = tmp_path / "consumer"; (root / "components").mkdir(parents=True)
    shutil.copy2(project / "pyproject.toml", root / "pyproject.toml")
    for component in ("worktrees", "portability"):
        shutil.copy2(project / f"components/{component}.source.json", root / "components")
        shutil.copytree(project / f"components/{component}-delivery", root / f"components/{component}-delivery")
    return root


def test_portability_selection_is_independent_of_author_dependency_selection(selected_pair):
    root = selected_pair
    before = delivery.verify(root, component="portability")
    author = root / "components/portability"; author.mkdir()
    (author / "worktrees-source.json").write_text('{"future": "unpublished dependency"}')
    runtime = author / "amplifier_portability"; runtime.mkdir()
    (runtime / "__init__.py").write_text('future = "not the app default"\n')
    assert delivery.verify(root, component="portability") == before
    assert before["dependencySources"]["worktrees"] == delivery.verify(root, component="worktrees")["source"]
    assert not (root / ".git").exists()


@pytest.mark.parametrize("change", ["artifact", "runtime", "revision"])
def test_portability_refuses_other_worktrees_bytes_at_same_version(selected_pair, change):
    root = selected_pair
    path = root / "components/worktrees.source.json"
    record = json.loads(path.read_bytes())
    original_version = record["source"]["version"]
    wheel = f"amplifier_worktrees-{original_version}-py3-none-any.whl"
    if change == "artifact":
        record["artifact"]["sha256"] = "a" * 64
        record["source"]["artifacts"][wheel]["sha256"] = "a" * 64
    elif change == "runtime":
        name = "amplifier_worktrees/__init__.py"
        raw = b'future = "different implementation"\n'
        (root / "components/worktrees-delivery" / name).write_bytes(raw)
        record["files"][name] = record["source"]["sourceFiles"][name] = delivery.sha(raw)
    else:
        record["source"]["revision"] = "a" * 40
    record["sourceReceiptSha256"] = delivery.sha((json.dumps(record["source"], indent=2) + "\n").encode())
    path.write_text(json.dumps(record, indent=2) + "\n")
    # A coherent different worktrees selection cannot silently satisfy portability.
    assert delivery.verify(root, component="worktrees")["source"]["version"] == original_version
    with pytest.raises(ValueError, match="exact selected worktrees artifact"):
        delivery.verify(root, component="portability")


def test_portability_dependency_receipt_is_bound_to_published_source(selected_pair):
    root = selected_pair
    path = root / "components/portability.source.json"
    record = json.loads(path.read_bytes())
    record["dependencySources"]["worktrees"]["tree"] = "a" * 40
    path.write_text(json.dumps(record, indent=2) + "\n")
    with pytest.raises(ValueError, match="exact selected worktrees artifact"):
        delivery.verify(root, component="portability")


@pytest.fixture(params=[
    ("operations", "amplifier_operations"),
    ("operations", "components/operations"),
    ("operations", "."),
    ("kernels", "amplifier_kernels"),
    ("kernels", "."),
])
def flat_selection(tmp_path, request):
    component, subdirectory = request.param
    root, revision, dist, _ = make_candidate(tmp_path, component)
    with zipfile.ZipFile(next(dist.glob("*.whl")), "a") as archive:
        archive.writestr(f"amplifier_{component}-0.1.0.dist-info/licenses/LICENSE", "MIT\n")
    (dist / f"{component}-source.json").unlink(); (dist / "SHA256SUMS").unlink()
    release.seal(root, revision, dist, component=component)
    receipt_path = dist / f"{component}-source.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["subdirectory"] = subdirectory
    if subdirectory == ".":
        receipt.update(repository=f"bkrabach/amplifier-platform-{component}", component=component)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    (dist / "SHA256SUMS").write_text("".join(
        f"{release.sha((dist / name).read_bytes())}  {name}\n"
        for name in (*release.artifact_names(receipt), receipt_path.name)))
    # All source layouts select the same flat runtime, without a producer clone.
    plain = tmp_path / "consumer"; (plain / "components").mkdir(parents=True)
    (plain / "pyproject.toml").write_text(
        '[project]\nname="amplifier-unified"\nversion="1.0.0"\ndependencies=[]\n'
        f'[tool.hatch.build.targets.wheel]\npackages=["amplifier_{component}"]\n')
    checksum = delivery.sha((dist / f"{component}-source.json").read_bytes())
    delivery.select(plain, dist, revision, checksum, component=component)
    return plain, revision, dist, checksum, component


def test_flat_selection_does_not_shadow_independent_author(flat_selection):
    root, _, dist, _, component = flat_selection
    before = delivery.verify(root, component=component)
    author = root / f"components/{component}"; author.mkdir()
    (author / "__init__.py").write_text('raise RuntimeError("unpublished author change")\n')
    assert delivery.verify(root, component=component) == before
    assert before["source"] == json.loads((dist / f"{component}-source.json").read_bytes())
    assert set(before["files"]) == {"__init__.py", "LICENSE"} | ({"node_runner.cjs"} if component == "kernels" else set())
    if component == "kernels":
        assert (root / "amplifier_kernels/node_runner.cjs").read_bytes() == b"const selected = true;\n"
    assert not (root / ".git").exists()
    # This is the ordinary cwd import used by source/editable development.
    result = subprocess.check_output([sys.executable, "-c",
        f'import amplifier_{component} as p; print(p.value); print(p.__file__)'], cwd=root, text=True)
    assert result.splitlines() == ["selected", str(root / f"amplifier_{component}/__init__.py")]


def test_flat_selection_can_restore_original_artifact(flat_selection):
    root, revision, dist, _, component = flat_selection
    producer = root.parent / "repo"
    (dist / f"{component}-source.json").unlink()
    (dist / "SHA256SUMS").unlink()
    historical = release.seal(producer, revision, dist, component=component)
    checksum = delivery.sha((dist / f"{component}-source.json").read_bytes())
    delivery.select(root, dist, revision, checksum, component=component)
    assert delivery.verify(root, component=component)["source"] == historical


def test_flat_selection_refuses_author_packaging(flat_selection):
    root, _, _, _, component = flat_selection
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace('packages=[', f'packages=["components/{component}",'))
    with pytest.raises(ValueError, match="only the selected"):
        delivery.verify(root, component=component)


@pytest.mark.parametrize("recorded", [True, False])
def test_flat_selection_preserves_modified_root_snapshot(flat_selection, recorded):
    root, revision, dist, checksum, component = flat_selection
    path = root / f"amplifier_{component}/__init__.py"
    path.write_text("local_work = True\n")
    record_path = root / f"components/{component}.source.json"
    record = record_path.read_bytes()
    if not recorded:
        record_path.unlink()  # An unmoved author tree is never a replaceable selection.
    with pytest.raises(ValueError, match="locally modified"):
        delivery.select(root, dist, revision, checksum, component=component)
    assert path.read_text() == "local_work = True\n"
    if recorded:
        assert record_path.read_bytes() == record
    else:
        assert not record_path.exists()


@pytest.fixture(params=[
    ("workspace-metadata", "workspaces", "amplifier_workspace_metadata"),
    ("workspace-metadata", ".", "amplifier_workspace_metadata"),
    ("outputs", "outputs", "amplifier_outputs"),
    ("outputs", ".", "amplifier_outputs"),
    ("publishing", "publishing", "amplifier_publishing"),
    ("recall", "recall", "amplifier_recall"),
    ("recall", ".", "amplifier_recall"),
])
def nested_selection(tmp_path, request):
    component, author_directory, package = request.param
    root, revision, dist, _ = make_candidate(tmp_path, component)
    with zipfile.ZipFile(next(dist.glob("*.whl")), "a") as archive:
        archive.writestr(f"{package}-0.1.0.dist-info/licenses/LICENSE", "MIT\n")
    (dist / f"{component}-source.json").unlink(); (dist / "SHA256SUMS").unlink()
    original_receipt = release.seal(root, revision, dist, component=component)
    if author_directory == ".":
        path = dist / f"{component}-source.json"
        receipt = json.loads(path.read_bytes())
        repository = {"workspace-metadata": "workspaces", "outputs": "resources", "recall": "recall"}[component]
        receipt.update(repository=f"bkrabach/amplifier-platform-{repository}",
                       component=component, subdirectory=".")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        (dist / "SHA256SUMS").write_text("".join(
            f"{release.sha((dist / name).read_bytes())}  {name}\n"
            for name in (*release.artifact_names(receipt), path.name)))
    plain = tmp_path / "consumer"; (plain / "components").mkdir(parents=True)
    (plain / "pyproject.toml").write_text(
        '[project]\nname="amplifier-unified"\nversion="1.0.0"\ndependencies=[]\n'
        '[tool.hatch.build.targets.wheel]\n'
        f'packages=["components/{component}-delivery/{package}"]\n')
    checksum = delivery.sha((dist / f"{component}-source.json").read_bytes())
    delivery.select(plain, dist, revision, checksum, component=component)
    return plain, component, author_directory, package, revision, dist, original_receipt


def test_nested_selection_preserves_published_names_without_author_or_git(nested_selection):
    root, component, author_directory, package, *_ = nested_selection
    before = delivery.verify(root, component=component)
    expected_source = "." if author_directory == "." else f"components/{author_directory}"
    assert before["source"]["subdirectory"] == expected_source
    assert before["source"]["tag"] == f"{component}-v0.1.0"
    assert before["artifact"]["url"].endswith(f"/{package}-0.1.0-py3-none-any.whl")
    assert set(before["files"]) == {"LICENSE", f"{package}/__init__.py"}
    assert before["runtimeRequirements"] == []
    assert not (root / ".git").exists()
    if author_directory == ".":
        author_directory = "workspaces" if component == "workspace-metadata" else component
    assert not (root / "components" / author_directory).exists()
    author = root / "components" / author_directory / package; author.mkdir(parents=True)
    (author / "__init__.py").write_text('raise RuntimeError("unselected author")\n')
    assert delivery.verify(root, component=component) == before


def test_nested_selection_refuses_two_installed_owners(nested_selection):
    root, component, author_directory, package, *_ = nested_selection
    if author_directory == ".":
        author_directory = "workspaces" if component == "workspace-metadata" else component
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace(
        'packages=[', f'packages=["components/{author_directory}/{package}",'))
    with pytest.raises(ValueError, match="only the selected"):
        delivery.verify(root, component=component)


def test_nested_reselection_preserves_local_delivery(nested_selection):
    root, component, _, package, revision, dist, _ = nested_selection
    record = root / f"components/{component}.source.json"
    before = record.read_bytes()
    runtime = root / f"components/{component}-delivery/{package}/__init__.py"
    runtime.write_text("local_work = True\n")
    checksum = delivery.sha((dist / f"{component}-source.json").read_bytes())
    with pytest.raises(ValueError, match="locally modified"):
        delivery.select(root, dist, revision, checksum, component=component)
    assert record.read_bytes() == before
    assert runtime.read_text() == "local_work = True\n"


def test_nested_selection_can_restore_original_artifact(nested_selection):
    root, component, _, _, revision, dist, original_receipt = nested_selection
    before = delivery.inventory(root / f"components/{component}-delivery")
    receipt_path = dist / f"{component}-source.json"
    receipt_path.write_text(json.dumps(original_receipt, indent=2) + "\n")
    (dist / "SHA256SUMS").write_text("".join(
        f"{release.sha((dist / name).read_bytes())}  {name}\n"
        for name in (*release.artifact_names(original_receipt), receipt_path.name)))
    delivery.select(root, dist, revision, delivery.sha(receipt_path.read_bytes()), component=component)
    assert delivery.verify(root, component=component)["source"] == original_receipt
    assert delivery.inventory(root / f"components/{component}-delivery") == before


@pytest.fixture(params=["monorepo", "repository-root"])
def status_selection(tmp_path, request):
    producer, revision, dist, _ = make_candidate(tmp_path, "status-emitter")
    if request.param == "repository-root":
        receipt_path = dist / "status-emitter-source.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt.update(repository="bkrabach/amplifier-platform-status", subdirectory=".", component="status-emitter")
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        (dist / "SHA256SUMS").write_text("".join(
            f"{release.sha((dist / name).read_bytes())}  {name}\n"
            for name in (*release.artifact_names(receipt), receipt_path.name)))
    root = tmp_path / "status-consumer"
    (root / "components").mkdir(parents=True)
    (root / "amplifier_web").mkdir()
    (root / "amplifier_web/__init__.py").write_text("")
    (root / "pyproject.toml").write_text(
        '[project]\nname="amplifier-unified"\nversion="1.0.0"\ndependencies=[]\n'
        '[tool.hatch.build.targets.wheel]\npackages=["amplifier_web"]\n')
    checksum = delivery.sha((dist / "status-emitter-source.json").read_bytes())
    delivery.select(root, dist, revision, checksum, component="status-emitter")
    return root, revision, dist, checksum


def test_status_private_delivery_ignores_author_and_preserves_original_receipt(status_selection):
    root, _, dist, _ = status_selection
    before = delivery.verify(root, component="status-emitter")
    assert before["source"] == json.loads((dist / "status-emitter-source.json").read_bytes())
    assert before["source"]["subdirectory"] in {"amplifier_web/status_protocol", "."}
    assert set(before["files"]) == {"LICENSE", "__init__.py"}
    assert not (root / ".git").exists()
    author = root / "components/status"; author.mkdir()
    (author / "__init__.py").write_text('raise RuntimeError("unselected author")\n')
    assert delivery.verify(root, component="status-emitter") == before
    result = subprocess.check_output([sys.executable, "-c",
        "import amplifier_web.status_protocol as s; print(s.value); print(s.__file__)"],
        cwd=root, text=True)
    assert result.splitlines() == ["selected", str(root / "amplifier_web/status_protocol/__init__.py")]
    assert not (root / "amplifier_status").exists()


def test_status_selection_can_restore_original_artifact(status_selection):
    root, revision, dist, _ = status_selection
    before = delivery.inventory(root / "amplifier_web/status_protocol")
    (dist / "status-emitter-source.json").unlink()
    (dist / "SHA256SUMS").unlink()
    historical = release.seal(root.parent / "repo", revision, dist, component="status-emitter")
    checksum = delivery.sha((dist / "status-emitter-source.json").read_bytes())
    delivery.select(root, dist, revision, checksum, component="status-emitter")
    assert delivery.verify(root, component="status-emitter")["source"] == historical
    assert delivery.inventory(root / "amplifier_web/status_protocol") == before


@pytest.mark.parametrize("extra", ["components/status", "amplifier_status", "amplifier_web/status_protocol"])
def test_status_private_delivery_rejects_another_packaged_owner(status_selection, extra):
    root, *_ = status_selection
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace('packages=[', f'packages=["{extra}",'))
    with pytest.raises(ValueError, match="only the selected"):
        delivery.verify(root, component="status-emitter")


@pytest.mark.parametrize("fault", [None, "private-body", "public-namespace", "missing-receipt"])
def test_status_app_wheel_has_exact_private_selection(status_selection, fault):
    root, *_ = status_selection
    directory, record, package, installed_record, installed_license = delivery.locations("status-emitter")
    selection = delivery.verify(root, component="status-emitter")
    files = {package + "/" + name: (root / directory / name).read_bytes()
             for name in selection["files"] if name != "LICENSE"}
    files.update({installed_record: (root / record).read_bytes(),
                  installed_license: (root / directory / "LICENSE").read_bytes(),
                  "amplifier_unified-1.0.0.dist-info/METADATA": b"Name: amplifier-unified\nVersion: 1.0.0\n"})
    if fault == "private-body":
        files[package + "/__init__.py"] = b"unselected = True\n"
    elif fault == "public-namespace":
        files["amplifier_status/__init__.py"] = files[package + "/__init__.py"]
    elif fault == "missing-receipt":
        del files[installed_record]
    wheel = root / "app.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    if fault:
        with pytest.raises(ValueError):
            delivery.verify_wheel(root, wheel, component="status-emitter")
    else:
        delivery.verify_wheel(root, wheel, component="status-emitter")


def test_status_reselection_preserves_modified_private_runtime(status_selection):
    root, revision, dist, checksum = status_selection
    path = root / "amplifier_web/status_protocol/__init__.py"
    path.write_text("local_work = True\n")
    before = (root / "components/status-emitter.source.json").read_bytes()
    with pytest.raises(ValueError, match="locally modified"):
        delivery.select(root, dist, revision, checksum, component="status-emitter")
    assert path.read_text() == "local_work = True\n"
    assert (root / "components/status-emitter.source.json").read_bytes() == before
