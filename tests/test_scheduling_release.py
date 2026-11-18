"""Publication refuses changed source/bytes and safely resumes only its own draft."""
import copy
import importlib.util
import io
import json
import shutil
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("scheduling_release", Path(__file__).parents[1] / "scripts/scheduling_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def make_candidate(tmp_path, component="scheduling"):
    distribution = "amplifier_" + component.replace("-", "_")
    package = "amplifier_status" if component == "status-emitter" else distribution
    root = tmp_path / "repo"; root.mkdir()
    source = root / ("amplifier_web/status_protocol" if component == "status-emitter" else
                     "components/workspaces" if component == "workspace-metadata" else
                     package if component in {"operations", "kernels"} else "components/" + component)
    source.mkdir(parents=True)
    runtime = "__init__.py" if component in {"operations", "kernels", "status-emitter"} else package + "/__init__.py"
    files = {"pyproject.toml": f'[project]\nname="amplifier-{component}"\nversion="0.1.0"\n',
             runtime: 'value = "selected"\n',
             "README.md": "Scheduling\n", "LICENSE": "MIT\n", "MANIFEST.in": "include README.md\n",
             "tests/test_policy_store.py": "def test_fixture(): assert True\n", ".gitignore": "dist/\n"}
    if component == "kernels":
        files["node_runner.cjs"] = "const selected = true;\n"
    if component == "status-emitter":
        files["pyproject.toml"] += ('license="MIT"\n[project.scripts]\n'
            'amplifier-status="amplifier_status.cli:main"\n'
            '[project.entry-points."amplifier.modules"]\n'
            'hooks-status-emitter="amplifier_status.hook:mount"\n')
    for name, text in files.items():
        path = source / name; path.parent.mkdir(exist_ok=True, parents=True); path.write_text(text)
    def git(*args):
        return release.run("git", *args, cwd=root).decode().strip()
    git("init", "-q"); git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "selected")
    revision = git("rev-parse", "HEAD"); git("update-ref", "refs/remotes/origin/main", revision)
    selected = release.selection(root, revision, component=component)
    dist = tmp_path / "dist"; dist.mkdir()
    wheel, sdist = release.artifact_names(selected)
    with zipfile.ZipFile(dist / wheel, "w") as archive:
        archive.writestr(package + "/__init__.py", files[runtime])
        if component == "kernels":
            archive.writestr(package + "/node_runner.cjs", files["node_runner.cjs"])
        info = distribution + "-0.1.0.dist-info/"
        archive.writestr(info + "METADATA", f"Name: amplifier-{component}\nVersion: 0.1.0\n" +
                        ("License-Expression: MIT\n" if component == "status-emitter" else ""))
        if component == "status-emitter":
            archive.writestr(info + "licenses/LICENSE", files["LICENSE"])
            archive.writestr(info + "entry_points.txt", '[console_scripts]\namplifier-status = amplifier_status.cli:main\n'
                            '[amplifier.modules]\nhooks-status-emitter = amplifier_status.hook:mount\n')
    with tarfile.open(dist / sdist, "w:gz") as archive:
        for name, text in files.items():
            if name == ".gitignore": continue
            raw = text.encode(); info = tarfile.TarInfo(distribution + "-0.1.0/" + name); info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    release.seal(root, revision, dist, component=component)
    return root, revision, dist, git


def make_src_candidate(tmp_path, component, *, worktrees=None):
    """A real root Git tree and archives with public src-layout package inputs."""
    root, _, dist, git = make_candidate(tmp_path, component)
    package = "amplifier_" + component
    author = root / "components" / component
    files = {p.relative_to(author).as_posix(): p.read_bytes() for p in author.rglob("*") if p.is_file()}
    requirements = [] if worktrees is None else [
        "amplifier-worktrees>=0.1.0", "cryptography>=43", "filelock>=3.16"]
    if worktrees is not None:
        files["worktrees-source.json"] = (json.dumps(worktrees, indent=2) + "\n").encode()
    files["pyproject.toml"] += ("dependencies=" + json.dumps(requirements) + "\n").encode()
    files = {("src/" + name if name.startswith(package + "/") else name): raw
             for name, raw in files.items()}
    shutil.rmtree(root / "components")
    for name, raw in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    git("add", "-A"); git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "commit", "-qm", "public src owner")
    revision = git("rev-parse", "HEAD")
    selected = {"schema": 1, "component": component, "repository": f"microsoft/amplifier-{component}",
                "revision": revision, "subdirectory": ".", "tree": git("rev-parse", "HEAD^{tree}"),
                "version": "0.1.0", "tag": component + "-v0.1.0",
                "sourceFiles": {name: release.sha(raw) for name, raw in files.items()}}
    for path in dist.iterdir(): path.unlink()
    wheel, sdist = release.artifact_names(selected)
    with zipfile.ZipFile(dist / wheel, "w") as archive:
        archive.writestr(package + "/__init__.py", files["src/" + package + "/__init__.py"])
        info = package + "-0.1.0.dist-info/"
        archive.writestr(info + "METADATA", f"Name: amplifier-{component}\nVersion: 0.1.0\n" +
                        "".join("Requires-Dist: " + value + "\n" for value in requirements))
        archive.writestr(info + "licenses/LICENSE", files["LICENSE"])
    with tarfile.open(dist / sdist, "w:gz") as archive:
        for name, raw in files.items():
            if name == ".gitignore": continue
            member = tarfile.TarInfo(package + "-0.1.0/" + name); member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    receipt = {**selected, "artifacts": release.package_identity(dist, selected)}
    receipt_name = component + "-source.json"
    (dist / receipt_name).write_text(json.dumps(receipt, indent=2) + "\n")
    (dist / "SHA256SUMS").write_text("".join(
        f"{release.sha((dist / name).read_bytes())}  {name}\n" for name in (wheel, sdist, receipt_name)))
    return root, revision, dist, receipt


@pytest.fixture
def candidate(tmp_path):
    return make_candidate(tmp_path)


def test_exact_source_and_built_payload_round_trip(candidate):
    root, revision, dist, _ = candidate
    result = release.verify(root, revision, dist)
    assert result["revision"] == revision and result["tag"] == "scheduling-v0.1.0"
    assert result["sourceFiles"]["tests/test_policy_store.py"]
    assert len(result["artifacts"]) == 2


@pytest.mark.parametrize("component", [None, "unknown"])
def test_repository_root_requires_supported_explicit_identity(component):
    selected = {"repository": "bkrabach/amplifier-platform-scheduling", "subdirectory": "."}
    if component is not None:
        selected["component"] = component
    with pytest.raises(ValueError, match="Unknown standalone component"):
        release.component_name(selected)


@pytest.mark.parametrize("component", ["publishing", "portability"])
def test_public_src_artifact_keeps_source_and_installed_paths_distinct(tmp_path, component):
    root, revision, dist, receipt = make_src_candidate(tmp_path, component)
    package = "amplifier_" + component
    assert receipt["revision"] == revision
    assert receipt["tree"] == release.run("git", "rev-parse", "HEAD^{tree}", cwd=root).decode().strip()
    assert "src/" + package + "/__init__.py" in receipt["sourceFiles"]
    assert package + "/__init__.py" not in receipt["sourceFiles"]
    assert release.package_identity(dist, receipt) == receipt["artifacts"]
    with zipfile.ZipFile(dist / release.artifact_names(receipt)[0]) as archive:
        assert archive.read(package + "/__init__.py") == (root / "src" / package / "__init__.py").read_bytes()
        assert not any(name.startswith("src/") for name in archive.namelist())


@pytest.mark.parametrize("component", ["publishing", "portability"])
@pytest.mark.parametrize("fault", ["changed", "missing", "extra"])
def test_public_src_wheel_cannot_change_selected_runtime(tmp_path, component, fault):
    _, _, dist, receipt = make_src_candidate(tmp_path, component)
    wheel = dist / release.artifact_names(receipt)[0]
    with zipfile.ZipFile(wheel) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    runtime = f"amplifier_{component}/__init__.py"
    if fault == "changed": files[runtime] = b"unselected = True\n"
    elif fault == "missing": del files[runtime]
    else: files[f"amplifier_{component}/unselected.py"] = b"extra = True\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in files.items(): archive.writestr(name, raw)
    with pytest.raises(ValueError): release.package_identity(dist, receipt)


def test_status_keeps_distribution_import_and_bundled_owner_distinct(tmp_path):
    root, revision, dist, _ = make_candidate(tmp_path, "status-emitter")
    result = release.verify(root, revision, dist, component="status-emitter")
    assert result["subdirectory"] == "amplifier_web/status_protocol"
    assert result["tag"] == "status-emitter-v0.1.0"
    assert set(result["artifacts"]) == {
        "amplifier_status_emitter-0.1.0-py3-none-any.whl", "amplifier_status_emitter-0.1.0.tar.gz"}
    with zipfile.ZipFile(dist / release.artifact_names(result)[0]) as wheel:
        assert "amplifier_status/__init__.py" in wheel.namelist()
        assert not any(name.startswith(("amplifier_web/", "amplifier_status_emitter/")) for name in wheel.namelist())
    assert result["sourceFiles"]["tests/test_policy_store.py"]
    assert (dist / "status-emitter-source.json").exists()


@pytest.mark.parametrize("fault", ["import-name", "metadata-name", "dependency", "license", "cli", "hook"])
def test_status_wrong_package_contract_never_reaches_publication(tmp_path, monkeypatch, fault):
    root, revision, dist, _ = make_candidate(tmp_path, "status-emitter")
    path = next(dist.glob("*.whl"))
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    info = "amplifier_status_emitter-0.1.0.dist-info/"
    if fault == "import-name":
        files["amplifier_status_emitter/__init__.py"] = files.pop("amplifier_status/__init__.py")
    elif fault == "metadata-name":
        files[info + "METADATA"] = files[info + "METADATA"].replace(b"amplifier-status-emitter", b"amplifier-status")
    elif fault == "dependency":
        files[info + "METADATA"] += b"Requires-Dist: amplifier-core\n"
    elif fault == "license":
        files[info + "licenses/LICENSE"] = b"different license"
    else:
        files[info + "entry_points.txt"] = files[info + "entry_points.txt"].replace(
            b"amplifier_status.cli:main" if fault == "cli" else b"amplifier_status.hook:mount", b"unselected:entry")
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in files.items(): archive.writestr(name, raw)
    monkeypatch.setattr(release, "api", lambda *a, **k: pytest.fail("Wrong status package reached publication"))
    with pytest.raises(ValueError):
        release.publish(root, revision, dist, component="status-emitter")


def test_status_release_describes_compatible_coinstallation(tmp_path, monkeypatch):
    candidate = make_candidate(tmp_path, "status-emitter")
    remote = Remote(candidate, exists=False)
    remote.install(monkeypatch)
    release.publish(*candidate[:3], component="status-emitter")
    body = next(value for path, args in remote.mutations if path == "releases" for value in args if value.startswith("body="))
    assert "distinct from Unified's bundled copy" in body and "Do not co-install" not in body


def test_unmerged_candidate_can_qualify_but_never_publish(candidate, monkeypatch):
    root, revision, dist, git = candidate
    with pytest.raises(ValueError, match="full merged"):
        release.selection(root, "main")
    (root / "new.txt").write_text("unmerged")
    git("add", "."); git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "unmerged")
    unmerged = git("rev-parse", "HEAD")
    assert release.selection(root, unmerged, qualification=True)["revision"] == unmerged
    monkeypatch.setattr(release, "api", lambda *a, **k: pytest.fail("No network before merged-source verification"))
    with pytest.raises(subprocess.CalledProcessError):
        release.publish(root, unmerged, dist)


@pytest.mark.parametrize("fault", ["wheel-payload", "source-payload", "receipt", "checksums", "unexpected-asset"])
def test_changed_local_bytes_cannot_reach_publication(candidate, monkeypatch, fault):
    root, revision, dist, _ = candidate
    if fault == "wheel-payload":
        wheel = next(dist.glob("*.whl"))
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("amplifier_scheduling/__init__.py", "value='other'")
    elif fault == "source-payload":
        next(dist.glob("*.tar.gz")).write_bytes(b"not a source archive")
    elif fault == "receipt":
        value = json.loads((dist / release.RECEIPT).read_text()); value["revision"] = "f" * 40
        (dist / release.RECEIPT).write_text(json.dumps(value))
    elif fault == "checksums":
        (dist / "SHA256SUMS").write_text("wrong")
    else:
        (dist / "other.whl").write_bytes(b"unselected")
    monkeypatch.setattr(release, "api", lambda *a, **k: pytest.fail("Invalid local bytes reached GitHub"))
    with pytest.raises((ValueError, KeyError, tarfile.TarError)):
        release.publish(root, revision, dist)


class Remote:
    def __init__(self, candidate, *, exists=True, draft=False):
        self.root, self.revision, self.dist, _ = candidate
        self.tag = json.loads(next(self.dist.glob("*-source.json")).read_text())["tag"]
        self.ref = {"object": {"type": "commit", "sha": self.revision}} if exists else None
        self.release = {"id": 10, "tag_name": self.tag, "draft": draft, "prerelease": False, "assets": []} if exists else None
        self.bytes = {}; self.mutations = []; self.swap_on_upload = False
        self.hide_draft_by_tag = False
        self.reads = []
        if exists:
            for path in sorted(self.dist.iterdir()): self.add(path)

    def add(self, path):
        identity = len(self.bytes) + 1; self.bytes[identity] = path.read_bytes()
        self.release["assets"].append({"name": path.name, "id": identity})

    def api(self, path, *args, missing=False):
        if "-X" not in args:
            self.reads.append(path)
            if path.startswith("git/ref/"):
                value = self.ref
            elif path == "releases/latest":
                value = {"tag_name": "v9.0.0"}
            elif path.startswith("releases/tags/"):
                value = None if self.hide_draft_by_tag and self.release and self.release["draft"] else self.release
            elif path.startswith("releases?per_page=100&page="):
                value = [self.release] if self.release and path.endswith("page=1") else []
            elif path == "releases/10":
                value = self.release
            else:
                pytest.fail("Unexpected read: " + path)
            if value is None and not missing:
                raise RuntimeError("GitHub operation failed (HTTP 404)")
            return copy.deepcopy(value)
        self.mutations.append((path, args))
        fields = dict(value.split("=", 1) for value in args if "=" in value)
        if path == "git/refs": self.ref = {"object": {"type": "commit", "sha": fields["sha"]}}
        elif path == "releases":
            assert fields["make_latest"] == "false" and fields["draft"] == "true"
            self.release = {"id": 10, "tag_name": self.tag, "draft": True, "prerelease": False, "assets": []}
        elif path == "releases/10":
            assert fields == {"draft": "false", "make_latest": "false"}
            self.release["draft"] = False
        else: pytest.fail("Unexpected mutation")
        return copy.deepcopy(self.ref if path == "git/refs" else self.release)

    def run(self, *args, **kwargs):
        if args[:2] == ("gh", "api"):
            return self.bytes[int(args[2].rsplit("/", 1)[1])]
        assert args[:3] == ("gh", "release", "upload") and "--clobber" not in args
        self.mutations.append(("upload", args))
        for value in args[4:args.index("--repo")]: self.add(Path(value))
        if self.swap_on_upload: self.ref["object"]["sha"] = "f" * 40
        return b""

    def install(self, monkeypatch):
        original = release.run
        monkeypatch.setattr(release, "api", self.api)
        monkeypatch.setattr(release, "run", lambda *a, **k: self.run(*a, **k) if a[0] == "gh" else original(*a, **k))


def test_completed_retry_is_read_only(candidate, monkeypatch):
    remote = Remote(candidate); remote.install(monkeypatch)
    assert release.publish(*candidate[:3])["status"] == "already-published-exact-bytes"
    assert remote.mutations == []


@pytest.mark.parametrize("fault", ["wrong-tag", "missing-tag", "changed-asset", "missing-asset", "extra-asset", "prerelease"])
def test_existing_publication_conflicts_never_mutate(candidate, monkeypatch, fault):
    remote = Remote(candidate)
    if fault == "wrong-tag": remote.ref["object"]["sha"] = "f" * 40
    elif fault == "missing-tag": remote.ref = None
    elif fault == "changed-asset": remote.bytes[1] = b"changed"
    elif fault == "missing-asset": remote.release["assets"].pop()
    elif fault == "extra-asset": remote.release["assets"].append({"name": "other", "id": 99})
    else: remote.release["prerelease"] = True
    remote.install(monkeypatch)
    with pytest.raises(ValueError): release.publish(*candidate[:3])
    assert remote.mutations == []


@pytest.mark.parametrize("new", [False, True])
def test_new_or_partial_draft_uploads_only_missing_before_publication(candidate, monkeypatch, new):
    remote = Remote(candidate, exists=not new, draft=True)
    if not new: remote.release["assets"] = remote.release["assets"][:1]
    remote.install(monkeypatch)
    assert release.publish(*candidate[:3])["status"] == "published"
    assert remote.release["draft"] is False and len(remote.release["assets"]) == 4
    upload = next(args for path, args in remote.mutations if path == "upload")
    assert len(upload[4:upload.index("--repo")]) == (4 if new else 3)
    assert not any(path == "git/refs" for path, _ in remote.mutations) if not new else True


def test_tag_race_after_upload_leaves_draft_unpublished(candidate, monkeypatch):
    remote = Remote(candidate, draft=True); remote.release["assets"] = remote.release["assets"][:1]
    remote.swap_on_upload = True; remote.install(monkeypatch)
    with pytest.raises(ValueError, match="changed during"):
        release.publish(*candidate[:3])
    assert remote.release["draft"] is True


@pytest.mark.parametrize("state", ["new", "partial", "complete"])
def test_tag_404_uses_exact_draft_id_without_duplicate_creation(candidate, monkeypatch, state):
    remote = Remote(candidate, exists=state != "new", draft=True)
    remote.hide_draft_by_tag = True
    if state == "partial":
        remote.release["assets"] = remote.release["assets"][:1]
    remote.install(monkeypatch)
    assert release.publish(*candidate[:3])["status"] == "published"
    assert remote.release["draft"] is False and len(remote.release["assets"]) == 4
    assert sum(path == "releases" for path, _ in remote.mutations) == (state == "new")
    assert sum(path == "git/refs" for path, _ in remote.mutations) == (state == "new")
    assert sum(path == "upload" for path, _ in remote.mutations) == (state != "complete")
    assert remote.reads.count("releases/10") >= 3
    # Tag lookup may hide a draft; later reads must stay bound to its returned ID.
    assert remote.reads.count("releases/tags/" + remote.tag) == 1


def test_hidden_draft_on_later_page_is_reconciled(candidate, monkeypatch):
    remote = Remote(candidate, draft=True); remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    original = remote.api
    def paged(path, *args, **kwargs):
        if path == "releases?per_page=100&page=1":
            return [{"tag_name": f"unrelated-{i}"} for i in range(100)]
        if path == "releases?per_page=100&page=2":
            return [copy.deepcopy(remote.release)]
        return original(path, *args, **kwargs)
    monkeypatch.setattr(release, "api", paged)
    assert release.publish(*candidate[:3])["status"] == "published"
    assert [path for path, _ in remote.mutations] == ["releases/10"]


@pytest.mark.parametrize("fault", ["failure", "not-list", "malformed-entry", "duplicate", "duplicate-later-page", "incomplete"])
def test_failed_or_ambiguous_listing_never_creates(candidate, monkeypatch, fault):
    remote = Remote(candidate, draft=True); remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    original = remote.api
    def broken(path, *args, **kwargs):
        if path.startswith("releases?"):
            if fault == "failure":
                raise RuntimeError("Listing unavailable")
            if fault == "not-list":
                return {"message": "unavailable"}
            if fault == "malformed-entry":
                return [None]
            if fault == "duplicate":
                return [remote.release, remote.release]
            if fault == "duplicate-later-page":
                return ([remote.release] + [{"tag_name": f"unrelated-{i}"} for i in range(99)]
                        if path.endswith("page=1") else [remote.release])
            return [{"tag_name": f"unrelated-{i}"} for i in range(100)]
        return original(path, *args, **kwargs)
    monkeypatch.setattr(release, "api", broken)
    with pytest.raises((ValueError, RuntimeError)):
        release.publish(*candidate[:3])
    assert remote.mutations == []


@pytest.mark.parametrize("fault", ["wrong-ref", "missing-ref", "changed-asset", "extra-asset", "prerelease"])
def test_hidden_draft_conflicts_never_mutate(candidate, monkeypatch, fault):
    remote = Remote(candidate, draft=True); remote.hide_draft_by_tag = True
    if fault == "wrong-ref": remote.ref["object"]["sha"] = "f" * 40
    elif fault == "missing-ref": remote.ref = None
    elif fault == "changed-asset": remote.bytes[1] = b"changed"
    elif fault == "extra-asset": remote.release["assets"].append({"name": "other", "id": 99})
    else: remote.release["prerelease"] = True
    remote.install(monkeypatch)
    with pytest.raises(ValueError):
        release.publish(*candidate[:3])
    assert remote.mutations == []


@pytest.mark.parametrize("fault", ["id", "tag_name", "prerelease", "draft", "assets", "not-found"])
def test_listed_draft_requires_matching_exact_id_readback(candidate, monkeypatch, fault):
    remote = Remote(candidate, draft=True); remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    original = remote.api
    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == "releases/10" and not args:
            if fault == "not-found":
                raise RuntimeError("Exact draft not found")
            value[fault] = {"id": 11, "tag_name": "other-tag", "prerelease": True,
                            "draft": "true", "assets": None}[fault]
        return value
    monkeypatch.setattr(release, "api", changed)
    with pytest.raises((ValueError, RuntimeError)):
        release.publish(*candidate[:3])
    assert remote.mutations == []


@pytest.mark.parametrize("phase", ["created", "uploaded", "published"])
def test_draft_identity_drift_stops_at_exact_id_checkpoint(candidate, monkeypatch, phase):
    remote = Remote(candidate, exists=phase != "created", draft=True)
    remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    original = remote.api
    checkpoint = {"created": 1, "uploaded": 2, "published": 3}[phase]
    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == "releases/10" and not args and remote.reads.count(path) == checkpoint:
            value["id"] = 11
        return value
    monkeypatch.setattr(release, "api", changed)
    with pytest.raises(ValueError, match="identity changed"):
        release.publish(*candidate[:3])
    assert sum(path == "releases" for path, _ in remote.mutations) == (phase == "created")
    assert sum(path == "releases/10" for path, _ in remote.mutations) == (phase == "published")


@pytest.mark.parametrize("fault", ["missing-asset", "app-latest", "still-draft"])
def test_publication_readback_rechecks_assets_and_app_latest(candidate, monkeypatch, fault):
    remote = Remote(candidate, draft=True); remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    original = remote.api
    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == "releases/10" and not args and remote.reads.count(path) == 3:
            if fault == "missing-asset":
                value["assets"].pop()
            elif fault == "still-draft":
                value["draft"] = True
        if path == "releases/latest" and fault == "app-latest":
            value["tag_name"] = remote.tag
        return value
    monkeypatch.setattr(release, "api", changed)
    with pytest.raises(ValueError):
        release.publish(*candidate[:3])
    assert [path for path, _ in remote.mutations] == ["releases/10"]


def test_api_listing_failure_is_not_an_empty_success(monkeypatch):
    calls = []
    def failed(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "gh: unavailable (HTTP 404)" if len(calls) == 1 else "gh: unavailable (HTTP 503)")
    monkeypatch.setattr(release.subprocess, "run", failed)
    with pytest.raises(RuntimeError):
        release.find_release("scheduling-v0.1.0")
    assert len(calls) == 2 and "releases?per_page=100&page=1" in calls[1][0][2]


@pytest.mark.parametrize("phase", ["create", "upload", "patch"])
def test_unknown_mutation_reply_is_not_retried(candidate, monkeypatch, phase):
    remote = Remote(candidate, exists=phase != "create", draft=True)
    remote.hide_draft_by_tag = True
    if phase == "upload":
        remote.release["assets"] = remote.release["assets"][:1]
    remote.install(monkeypatch)
    original_api, original_run = remote.api, release.run
    def uncertain_api(path, *args, **kwargs):
        value = original_api(path, *args, **kwargs)
        if "-X" in args and path == ("releases" if phase == "create" else "releases/10") and phase != "upload":
            raise RuntimeError("Unknown mutation response")
        return value
    def uncertain_run(*args, **kwargs):
        value = original_run(*args, **kwargs)
        if phase == "upload" and args[:3] == ("gh", "release", "upload"):
            raise RuntimeError("Unknown upload response")
        return value
    monkeypatch.setattr(release, "api", uncertain_api)
    monkeypatch.setattr(release, "run", uncertain_run)
    with pytest.raises(RuntimeError):
        release.publish(*candidate[:3])
    expected = {"create": ["git/refs", "releases"], "upload": ["upload"], "patch": ["releases/10"]}[phase]
    assert [path for path, _ in remote.mutations] == expected


def test_explicit_scheduling_selection_preserves_default_receipt(candidate):
    root, revision, dist, _ = candidate
    assert release.verify(root, revision, dist) == release.verify(
        root, revision, dist, component="scheduling")
    assert {path.name for path in dist.iterdir()} == {
        "amplifier_scheduling-0.1.0-py3-none-any.whl",
        "amplifier_scheduling-0.1.0.tar.gz", "scheduling-source.json", "SHA256SUMS"}


def test_worktrees_artifact_identity_and_explicit_selection(tmp_path):
    root, revision, dist, _ = make_candidate(tmp_path, "worktrees")
    receipt = release.verify(root, revision, dist, component="worktrees")
    assert receipt["tag"] == "worktrees-v0.1.0"
    assert receipt["subdirectory"] == "components/worktrees"
    assert {path.name for path in dist.iterdir()} == {
        "amplifier_worktrees-0.1.0-py3-none-any.whl",
        "amplifier_worktrees-0.1.0.tar.gz", "worktrees-source.json", "SHA256SUMS"}
    # An omitted selection still means scheduling, never whichever files exist.
    with pytest.raises(subprocess.CalledProcessError):
        release.verify(root, revision, dist)


@pytest.mark.parametrize("state", ["new", "partial", "complete"])
def test_worktrees_uses_existing_immutable_publication_policy(tmp_path, monkeypatch, state):
    selected = make_candidate(tmp_path, "worktrees")
    remote = Remote(selected, exists=state != "new", draft=state != "complete")
    if state == "partial":
        remote.release["assets"] = remote.release["assets"][:1]
        remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    result = release.publish(*selected[:3], component="worktrees")
    assert result["tag"] == "worktrees-v0.1.0"
    assert result["status"] == ("already-published-exact-bytes" if state == "complete" else "published")
    assert not remote.release["draft"] and len(remote.release["assets"]) == 4
    if state == "complete":
        assert remote.mutations == []


def test_changed_worktrees_public_asset_is_never_overwritten(tmp_path, monkeypatch):
    selected = make_candidate(tmp_path, "worktrees")
    remote = Remote(selected)
    remote.bytes[1] = b"different release"
    remote.install(monkeypatch)
    with pytest.raises(ValueError, match="bytes differ"):
        release.publish(*selected[:3], component="worktrees")
    assert remote.mutations == []


def test_component_selection_is_finite_and_cannot_relabel_scheduling(candidate, monkeypatch):
    root, revision, dist, _ = candidate
    monkeypatch.setattr(release, "api", lambda *a, **k: pytest.fail("Wrong component reached GitHub"))
    with pytest.raises(ValueError, match="Unknown standalone"):
        release.selection(root, revision, component="../unselected")
    with pytest.raises(subprocess.CalledProcessError):
        release.publish(root, revision, dist, component="worktrees")


def test_portability_uses_distinct_source_and_asset_identity(tmp_path):
    root, revision, dist, _ = make_candidate(tmp_path, "portability")
    selected = release.verify(root, revision, dist, component="portability")
    assert selected["tag"] == "portability-v0.1.0"
    assert selected["subdirectory"] == "components/portability"
    assert set(path.name for path in dist.iterdir()) == {
        "amplifier_portability-0.1.0-py3-none-any.whl",
        "amplifier_portability-0.1.0.tar.gz", "portability-source.json", "SHA256SUMS",
    }
    with pytest.raises(subprocess.CalledProcessError):
        release.verify(root, revision, dist, component="worktrees")


def test_completed_portability_release_retry_is_read_only(tmp_path, monkeypatch):
    candidate = make_candidate(tmp_path, "portability")
    remote = Remote(candidate)
    remote.install(monkeypatch)
    root, revision, dist, _ = candidate
    assert release.publish(root, revision, dist, component="portability") == {
        "tag": "portability-v0.1.0", "status": "already-published-exact-bytes",
    }
    assert remote.mutations == []


def test_operations_flat_project_matches_wheel_and_completed_release_identity(tmp_path, monkeypatch):
    candidate = make_candidate(tmp_path, "operations")
    root, revision, dist, _ = candidate
    selected = release.verify(root, revision, dist, component="operations")
    assert selected["tag"] == "operations-v0.1.0"
    assert selected["subdirectory"] == "amplifier_operations"
    assert "__init__.py" in selected["sourceFiles"]
    assert "amplifier_operations/__init__.py" not in selected["sourceFiles"]
    assert set(path.name for path in dist.iterdir()) == {
        "amplifier_operations-0.1.0-py3-none-any.whl",
        "amplifier_operations-0.1.0.tar.gz", "operations-source.json", "SHA256SUMS",
    }
    remote = Remote(candidate); remote.install(monkeypatch)
    assert release.publish(root, revision, dist, component="operations") == {
        "tag": "operations-v0.1.0", "status": "already-published-exact-bytes",
    }
    assert remote.mutations == []


def test_flat_project_tests_cannot_become_installed_runtime(tmp_path, monkeypatch):
    root, revision, dist, _ = make_candidate(tmp_path, "operations")
    with zipfile.ZipFile(next(dist.glob("*.whl")), "a") as archive:
        archive.writestr("amplifier_operations/tests/test_policy_store.py", "assert True\n")
    monkeypatch.setattr(release, "api", lambda *a, **k: pytest.fail("Invalid flat package reached GitHub"))
    with pytest.raises(ValueError, match="second package or non-runtime"):
        release.publish(root, revision, dist, component="operations")


def test_workspace_metadata_preserves_distribution_import_and_source_identity(tmp_path):
    root, revision, dist, _ = make_candidate(tmp_path, "workspace-metadata")
    selected = release.verify(root, revision, dist, component="workspace-metadata")
    assert selected["tag"] == "workspace-metadata-v0.1.0"
    assert selected["subdirectory"] == "components/workspaces"
    assert "amplifier_workspace_metadata/__init__.py" in selected["sourceFiles"]
    assert {path.name for path in dist.iterdir()} == {
        "amplifier_workspace_metadata-0.1.0-py3-none-any.whl",
        "amplifier_workspace_metadata-0.1.0.tar.gz", "workspace-metadata-source.json", "SHA256SUMS",
    }
    with pytest.raises(subprocess.CalledProcessError):
        release.verify(root, revision, dist, component="worktrees")
    with pytest.raises(ValueError, match="Unknown standalone"):
        release.selection(root, revision, component="workspaces")


@pytest.mark.parametrize("component", ["outputs", "publishing", "recall"])
def test_nested_component_preserves_source_and_artifact_identity(tmp_path, component):
    root, revision, dist, _ = make_candidate(tmp_path, component)
    selected = release.verify(root, revision, dist, component=component)
    assert selected["tag"] == component + "-v0.1.0"
    assert selected["subdirectory"] == "components/" + component
    assert f"amplifier_{component}/__init__.py" in selected["sourceFiles"]
    assert {path.name for path in dist.iterdir()} == {
        f"amplifier_{component}-0.1.0-py3-none-any.whl",
        f"amplifier_{component}-0.1.0.tar.gz", component + "-source.json", "SHA256SUMS",
    }
    with pytest.raises(subprocess.CalledProcessError):
        release.verify(root, revision, dist)


@pytest.mark.parametrize("component", ["workspace-metadata", "outputs", "publishing", "recall", "status-emitter"])
@pytest.mark.parametrize("state", ["new", "partial", "complete"])
def test_nested_component_uses_existing_exact_publication_recovery(tmp_path, monkeypatch, state, component):
    candidate = make_candidate(tmp_path, component)
    remote = Remote(candidate, exists=state != "new", draft=state != "complete")
    if state == "partial":
        remote.release["assets"] = remote.release["assets"][:1]
        remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    result = release.publish(*candidate[:3], component=component)
    assert result["tag"] == component + "-v0.1.0"
    assert result["status"] == ("already-published-exact-bytes" if state == "complete" else "published")
    assert not remote.release["draft"] and len(remote.release["assets"]) == 4
    if state == "complete":
        assert remote.mutations == []


@pytest.mark.parametrize("component", ["workspace-metadata", "outputs", "publishing", "recall", "status-emitter"])
def test_nested_component_changed_asset_remains_an_immutable_conflict(tmp_path, monkeypatch, component):
    candidate = make_candidate(tmp_path, component)
    remote = Remote(candidate)
    remote.bytes[1] = b"changed component artifact"
    remote.install(monkeypatch)
    with pytest.raises(ValueError, match="bytes differ"):
        release.publish(*candidate[:3], component=component)
    assert remote.mutations == []



@pytest.mark.parametrize("component", ["operations", "kernels", "status-emitter"])
def test_flat_author_relocation_preserves_original_release_verification(tmp_path, component):
    root, revision, dist, git = make_candidate(tmp_path, component)
    before = (dist / f"{component}-source.json").read_bytes()
    historical = release.verify(root, revision, dist, component=component)
    (root / "components").mkdir()
    original = historical["subdirectory"]
    author = release.COMPONENTS[component][0]
    git("mv", original, author)
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Relocate author only")
    current_revision = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", current_revision)
    current = release.selection(root, current_revision, component=component)
    assert current["subdirectory"] == author
    assert current["tree"] == historical["tree"]
    assert current["sourceFiles"] == historical["sourceFiles"]
    assert release.verify(root, revision, dist, component=component) == historical
    assert (dist / f"{component}-source.json").read_bytes() == before
    assert release.artifact_names(current) == release.artifact_names(historical)
    # Two simultaneous author projects are ambiguous, never chosen by path order.
    shutil.copytree(root / author, root / original)
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Ambiguous author fixture")
    with pytest.raises(ValueError, match=f"one {component} author"):
        release.selection(root, git("rev-parse", "HEAD"), qualification=True, component=component)


def test_kernels_flat_python_and_node_payload_identity(tmp_path):
    root, revision, dist, _ = make_candidate(tmp_path, "kernels")
    receipt = release.verify(root, revision, dist, component="kernels")
    assert receipt["tag"] == "kernels-v0.1.0"
    assert receipt["subdirectory"] == "amplifier_kernels"
    assert receipt["sourceFiles"]["node_runner.cjs"]
    assert {path.name for path in dist.iterdir()} == {
        "amplifier_kernels-0.1.0-py3-none-any.whl",
        "amplifier_kernels-0.1.0.tar.gz", "kernels-source.json", "SHA256SUMS",
    }
    with pytest.raises(subprocess.CalledProcessError):
        release.verify(root, revision, dist)


@pytest.mark.parametrize("artifact", ["wheel", "sdist"])
@pytest.mark.parametrize("fault", ["missing", "changed", "unselected"])
def test_kernels_node_runner_is_bound_before_publication(tmp_path, monkeypatch, artifact, fault):
    root, revision, dist, _ = make_candidate(tmp_path, "kernels")
    if artifact == "wheel":
        path = next(dist.glob("*.whl"))
        with zipfile.ZipFile(path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        runner = "amplifier_kernels/node_runner.cjs"
    else:
        path = next(dist.glob("*.tar.gz"))
        with tarfile.open(path) as archive:
            files = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
        runner = "amplifier_kernels-0.1.0/node_runner.cjs"
    if fault == "missing":
        del files[runner]
    elif fault == "changed":
        files[runner] = b"const unselected = true;\n"
    else:
        files[runner.replace("node_runner.cjs", "other_runner.cjs")] = b"const extra = true;\n"
    if artifact == "wheel":
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
    else:
        with tarfile.open(path, "w:gz") as archive:
            for name, raw in files.items():
                info = tarfile.TarInfo(name); info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
    monkeypatch.setattr(release, "api", lambda *a, **k: pytest.fail("Invalid runner reached GitHub"))
    with pytest.raises(ValueError):
        release.publish(root, revision, dist, component="kernels")


@pytest.mark.parametrize("state", ["new", "partial", "complete"])
def test_kernels_reuses_exact_draft_and_completed_publication(tmp_path, monkeypatch, state):
    candidate = make_candidate(tmp_path, "kernels")
    remote = Remote(candidate, exists=state != "new", draft=state != "complete")
    if state == "partial":
        remote.release["assets"] = remote.release["assets"][:1]
        remote.hide_draft_by_tag = True
    remote.install(monkeypatch)
    result = release.publish(*candidate[:3], component="kernels")
    assert result["tag"] == "kernels-v0.1.0"
    assert result["status"] == ("already-published-exact-bytes" if state == "complete" else "published")
    assert not remote.release["draft"] and len(remote.release["assets"]) == 4
    if state == "complete":
        assert remote.mutations == []


def test_changed_published_kernels_asset_is_not_overwritten(tmp_path, monkeypatch):
    candidate = make_candidate(tmp_path, "kernels")
    remote = Remote(candidate)
    remote.bytes[1] = b"changed kernels artifact"
    remote.install(monkeypatch)
    with pytest.raises(ValueError, match="bytes differ"):
        release.publish(*candidate[:3], component="kernels")
    assert remote.mutations == []
