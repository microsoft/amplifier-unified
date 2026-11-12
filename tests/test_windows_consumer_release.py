"""Consumer build/publication binding controls; native compilation is separate."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from windows_bootstrap_native import consumer_plan
import windows_consumer_release as release


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def built(tmp_path, monkeypatch):
    revision = "a" * 40
    wheel = tmp_path / "amplifier_unified-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("amplifier_web/release-source.json", json.dumps({
            "schema": 1, "repository": "microsoft/amplifier-unified", "version": "1.2.3", "revision": revision}))
    artifact = {"url": "https://github.com/microsoft/amplifier-unified/releases/download/v1.2.3/" + wheel.name,
                "sha256": release.sha(wheel), "bytes": wheel.stat().st_size}
    plan = {"schema": 1, "kind": "windows-consumer-preview", "version": "1.2.3", "source": revision,
            "python": "3.13.18", "artifact": artifact,
            "graph": [{"name": "amplifier-unified", "version": "1.2.3",
                       "url": "https://github.com/microsoft/amplifier-unified", "revision": revision,
                       "delivery": {k: artifact[k] for k in ("url", "sha256")}}]}
    directory = tmp_path / "consumer"
    directory.mkdir()
    (directory / release.PLAN).write_bytes(consumer_plan(plan))
    exe = directory / release.names("1.2.3")
    exe.write_bytes(b"MZsynthetic-not-executable")
    inputs = {"NativeInstaller.cs": b"synthetic compiler input"}
    monkeypatch.setattr(release, "sources", lambda *a, **kw: inputs)
    report = {"schema": 1, "source": revision, "version": "1.2.3", "wheelSha256": release.sha(wheel),
              "planSha256": release.sha(directory / release.PLAN),
              "exe": {"name": exe.name, "sha256": release.sha(exe), "bytes": exe.stat().st_size},
              "buildInputs": {n: {"sha256": sha(v), "bytes": len(v)} for n, v in inputs.items()},
              "installedPayloadVerified": True, "signed": False, "publicAcceptance": False}
    def save_report():
        (directory / release.REPORT).write_text(json.dumps(report))
        assets = [exe, directory / release.PLAN, directory / release.REPORT]
        (directory / release.SUMS).write_text("".join(f"{release.sha(p)}  {p.name}\n" for p in assets))
    save_report()
    return directory, wheel, revision, plan, report, save_report


def test_exact_consumer_asset_set_is_bound_to_original_wheel(built):
    directory, wheel, revision, _, _, _ = built
    paths = release.verify(directory, wheel, revision, "1.2.3")
    assert [p.name for p in paths] == [release.names("1.2.3"), release.PLAN, release.REPORT, release.SUMS]
    with pytest.raises(ValueError, match="source"):
        release.verify(directory, wheel, "b" * 40, "1.2.3")


@pytest.mark.parametrize("changed", ["exe", "name", "count", "wheel", "plan", "graph", "inputs", "qualified", "signed", "sums"])
def test_changed_consumer_build_refuses_before_publication(built, changed):
    directory, wheel, revision, plan, report, save = built
    if changed == "exe": (directory / report["exe"]["name"]).write_bytes(b"MZchanged")
    elif changed == "name": report["exe"]["name"] = "../other.exe"
    elif changed == "count": report["exe"]["bytes"] = True
    elif changed == "wheel": report["wheelSha256"] = "b" * 64
    elif changed == "inputs": report["buildInputs"] = {}
    elif changed == "qualified": report["installedPayloadVerified"] = False
    elif changed == "signed": report["signed"] = True
    elif changed in {"plan", "graph"}:
        if changed == "plan": plan["source"] = "b" * 40
        else: plan["graph"][0]["revision"] = "b" * 40
        (directory / release.PLAN).write_text(json.dumps(plan))
        report["planSha256"] = release.sha(directory / release.PLAN)
    save()
    if changed == "sums": (directory / release.SUMS).write_text("changed")
    with pytest.raises((ValueError, FileNotFoundError)):
        release.verify(directory, wheel, revision, "1.2.3")


def test_producer_is_build_only_no_target_compiler_or_trust_mutation():
    text = (SCRIPTS / "windows_consumer_release.py").read_text()
    assert 'def build(' in text and 'def verify(' in text
    assert 'CREATE_NO_WINDOW' in text and '_payload(' in text
    assert 'certutil' not in text and 'service install' not in text


def test_release_requires_consumer_bytes_only_for_sources_with_producer(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("consumer_release_owner", SCRIPTS / "release.py")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    dist = tmp_path / "dist"
    dist.mkdir()
    assert owner.consumer_assets(tmp_path, dist, "1.2.3", "a" * 40) == []
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/windows_consumer_release.py").write_text("selected producer")
    (dist / "fixture.whl").write_bytes(b"fixture")
    monkeypatch.setattr(owner, "run", lambda *a, **kw: json.dumps(["../foreign.exe"]))
    with pytest.raises(ValueError, match="unexpected asset set"):
        owner.consumer_assets(tmp_path, dist, "1.2.3", "a" * 40)


def test_workflow_builds_original_wheel_before_normal_publisher():
    import yaml
    workflow = yaml.safe_load((SCRIPTS.parent / ".github/workflows/release.yml").read_text())
    native = workflow["jobs"]["windows-consumer"]
    assert native["needs"] == "prepare" and native["runs-on"] == "windows-latest"
    body = "\n".join(step.get("run", "") for step in native["steps"])
    assert "uv pip install" in body and "uv pip check" in body
    assert "windows_consumer_release.py build" in body and "uv build" not in body
    assert workflow["jobs"]["release"]["needs"] == ["prepare", "windows-consumer"]
