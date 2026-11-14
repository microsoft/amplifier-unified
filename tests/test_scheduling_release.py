"""Publication refuses changed source/bytes and safely resumes only its own draft."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("scheduling_release", Path(__file__).parents[1] / "scripts/scheduling_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    source = root / release.PROJECT; source.mkdir(parents=True)
    files = {"pyproject.toml": '[project]\nname="amplifier-scheduling"\nversion="0.1.0"\n',
             "amplifier_scheduling/__init__.py": 'value = "selected"\n',
             "README.md": "Scheduling\n", "LICENSE": "MIT\n", "MANIFEST.in": "include README.md\n",
             "tests/test_policy_store.py": "def test_fixture(): assert True\n", ".gitignore": "dist/\n"}
    for name, text in files.items():
        path = source / name; path.parent.mkdir(exist_ok=True, parents=True); path.write_text(text)
    def git(*args):
        return release.run("git", *args, cwd=root).decode().strip()
    git("init", "-q"); git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "selected")
    revision = git("rev-parse", "HEAD"); git("update-ref", "refs/remotes/origin/main", revision)
    selected = release.selection(root, revision)
    dist = tmp_path / "dist"; dist.mkdir()
    wheel, sdist = release.artifact_names(selected)
    with zipfile.ZipFile(dist / wheel, "w") as archive:
        archive.writestr("amplifier_scheduling/__init__.py", files["amplifier_scheduling/__init__.py"])
        archive.writestr("amplifier_scheduling-0.1.0.dist-info/METADATA", "Name: amplifier-scheduling\nVersion: 0.1.0\n")
    with tarfile.open(dist / sdist, "w:gz") as archive:
        for name, text in files.items():
            if name == ".gitignore": continue
            raw = text.encode(); info = tarfile.TarInfo("amplifier_scheduling-0.1.0/" + name); info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    release.seal(root, revision, dist)
    return root, revision, dist, git


def test_exact_source_and_built_payload_round_trip(candidate):
    root, revision, dist, _ = candidate
    result = release.verify(root, revision, dist)
    assert result["revision"] == revision and result["tag"] == "scheduling-v0.1.0"
    assert result["sourceFiles"]["tests/test_policy_store.py"]
    assert len(result["artifacts"]) == 2


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
        self.tag = "scheduling-v0.1.0"
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
