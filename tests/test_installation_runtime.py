"""Installation calls the canonical no-model qualifier, never a user worker."""
import asyncio
from contextlib import contextmanager
import json

import pytest

from amplifier_web import installation_runtime as setup
from amplifier_web import runtime_environment, runtime_qualification, updates


@pytest.fixture
def qualifier(tmp_path, monkeypatch):
    calls = []
    async def validate(owner, stage, generation):
        calls.append(("validate", owner.home, stage, generation))
        assert owner.inventory == []
        (stage / "profiles-qualified.json").write_text(json.dumps({"profiles": ["work"]}))
        (stage / "runtime-installed.json").write_text(json.dumps([{"name": "synthetic", "version": "1"}]))
        (stage / "runtime.lock").write_text("exact-locked-graph")
        project = owner.home / "runtime" / ("q-" + "a" * 32)
        project.mkdir(parents=True)
        (stage / "runtime-project.json").write_text(json.dumps({"project": project.name}))
    monkeypatch.setattr(updates.UpdateManager, "validate", validate)
    monkeypatch.setattr(setup, "_needs_connection", lambda home: False)
    monkeypatch.setattr(runtime_qualification, "verify_recorded",
                        lambda project, receipt: calls.append(("verify", project, receipt)))
    return calls


def test_initial_prepare_qualifies_and_verifies_before_pointer(tmp_path, qualifier, monkeypatch):
    original = setup._write
    writes = []
    def write(home, path, value):
        if path.name == "active.json":
            assert any(row[0] == "verify" for row in qualifier)
        writes.append(path.name)
        original(home, path, value)
    monkeypatch.setattr(setup, "_write", write)
    result = asyncio.run(setup.prepare(tmp_path))
    assert result["ok"] and result["prepared"] and result["modelRequestSent"] is False
    pointer = updates.active_release(tmp_path)
    assert pointer == {**pointer, "current": result["generation"], "previous": None}
    assert writes.index("validated.json") < writes.index("active.json")
    assert [row[0] for row in qualifier] == ["validate", "verify"]
    assert setup.status(tmp_path)["generation"] == result["generation"]


def test_recorded_prepare_is_inspect_only_and_never_refreshes(tmp_path, qualifier):
    result = asyncio.run(setup.prepare(tmp_path))
    before = (tmp_path / "updates/active.json").read_bytes()
    qualifier.clear()
    second = asyncio.run(setup.prepare(tmp_path))
    assert second == result and [row[0] for row in qualifier] == ["verify"]
    assert (tmp_path / "updates/active.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["qualification", "verification"])
def test_failed_prepare_never_activates_and_keeps_evidence(tmp_path, qualifier, monkeypatch, failure):
    if failure == "qualification":
        async def fail(*args):
            raise ValueError("synthetic private error")
        monkeypatch.setattr(updates.UpdateManager, "validate", fail)
    else:
        def fail(*args):
            raise ValueError("synthetic graph mismatch")
        monkeypatch.setattr(runtime_qualification, "verify_recorded", fail)
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(setup.prepare(tmp_path))
    assert updates.active_release(tmp_path) == {}
    attempts = list((tmp_path / "updates/releases").iterdir())
    assert len(attempts) == 1
    progress = json.loads((attempts[0] / "install-progress.json").read_text())
    assert progress["events"][-1] == {"phase": "initial-installation", "status": "failed"}
    assert "private error" not in json.dumps(progress) and "graph mismatch" not in json.dumps(progress)


def test_status_does_not_prepare_or_create_missing_home(tmp_path, monkeypatch):
    home = tmp_path / "absent"
    monkeypatch.setattr(runtime_environment, "prepare_project",
                        lambda *a, **kw: pytest.fail("Status cannot prepare"))
    assert setup.status(home)["prepared"] is False
    assert not home.exists()


def test_existing_damaged_generation_is_not_rebuilt(tmp_path, qualifier, monkeypatch):
    result = asyncio.run(setup.prepare(tmp_path))
    receipt = tmp_path / "updates/releases" / result["generation"]
    (receipt / "runtime-installed.json").write_text("[]")
    qualifier.clear()
    inspected = asyncio.run(setup.prepare(tmp_path))
    assert not inspected["ok"] and inspected["generation"] == result["generation"]
    assert all(row[0] != "validate" for row in qualifier)


def test_exclusive_install_lock_refuses_before_qualifier(tmp_path, qualifier, monkeypatch):
    from amplifier_web import reset
    @contextmanager
    def locked(home):
        raise RuntimeError("host is already running")
        yield
    monkeypatch.setattr(reset, "repair_lock", locked)
    with pytest.raises(RuntimeError, match="already running"):
        asyncio.run(setup.prepare(tmp_path))
    assert qualifier == [] and updates.active_release(tmp_path) == {}


def test_progress_retains_only_bounded_stage_facts(tmp_path):
    stage = tmp_path / "a"
    owner = setup._Preparation(tmp_path, stage)
    for _ in range(100):
        owner.record("ecosystem-prepare", "succeeded", output="SECRET", argv=["SECRET"])
    value = json.loads((stage / "install-progress.json").read_text())
    assert len(value["events"]) == 64
    assert "SECRET" not in json.dumps(value)


@pytest.mark.parametrize("corruption", [None, "marker", "missing-marker", "lock", "manifest", "inventory"])
def test_no_account_installs_only_declared_dependencies_without_claiming_worker_readiness(tmp_path, monkeypatch, corruption):
    from pathlib import Path
    monkeypatch.setattr(setup, "_needs_connection", lambda home: True)
    calls = []
    async def command(*argv, **kwargs):
        calls.append(argv[1])
        if argv[1] == "lock":
            project = Path(argv[argv.index("--project") + 1])
            (project / "uv.lock").write_text('version = 1\n')
        return ""
    monkeypatch.setattr(updates, "process", command)
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(runtime_qualification, "installed_graph",
                        lambda path: [{"name": "amplifier-core", "version": "2.0.2"}])
    async def overrides(project, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("amplifier-core==2.0.2\n")
    monkeypatch.setattr(runtime_qualification, "prepare_overrides", overrides)
    monkeypatch.setattr(runtime_qualification, "verify_recorded", lambda *a, **kw: None)
    monkeypatch.setattr(runtime_qualification, "verify_install_overrides", lambda *a: None)
    async def forbidden(*args):
        pytest.fail("No account must not create a fake provider or mount a conversation")
    monkeypatch.setattr(updates.UpdateManager, "validate", forbidden)
    first = asyncio.run(setup.prepare(tmp_path))
    assert first["ok"] and first["prepared"] and first["connectionRequired"]
    assert first["workerReady"] is False and first["profileCount"] == 0
    assert first["modelRequestSent"] is False and first["generation"] is None
    assert calls == ["lock", "sync", "pip"]
    assert not (tmp_path / "updates/active.json").exists()
    calls.clear()
    assert asyncio.run(setup.prepare(tmp_path)) == first
    assert calls == []  # An installed baseline is inspected, never re-resolved.
    if corruption:
        receipt = runtime_environment.receipt_directory(tmp_path)
        project = runtime_environment.project_path(tmp_path)
        if corruption == "missing-marker":
            (receipt / "installation-prepared.json").unlink()
            saved = {p.name: p.read_bytes() for p in receipt.iterdir() if p.is_file()}
            inspected = asyncio.run(setup.prepare(tmp_path))
            assert inspected["ok"] is False and calls == []
            assert saved == {p.name: p.read_bytes() for p in receipt.iterdir() if p.is_file()}
            return
        target, value = {
            "marker": (receipt / "installation-prepared.json", "[]"),
            "lock": (project / "uv.lock", "changed"),
            "manifest": (project / "pyproject.toml", "changed"),
            "inventory": (receipt / "runtime-installed.json", "[1]"),
        }[corruption]
        target.write_text(value)
        inspected = asyncio.run(setup.prepare(tmp_path))
        assert inspected["ok"] is False and calls == []
        assert target.read_text() == value and not updates.active_release(tmp_path)


def test_runtime_preparation_failure_keeps_closed_command_facts(tmp_path):
    from amplifier_web.update_diagnostics import CommandFailure
    owner = setup._Preparation(tmp_path, tmp_path / "attempt")
    async def fail():
        raise CommandFailure({"exitCode": 1, "stderrBytes": 42, "stdoutBytes": 0,
                              "durationMs": 12, "text": "SECRET", "argv": ["SECRET"]})
    with pytest.raises(CommandFailure):
        asyncio.run(owner.run("initial-runtime-lock", fail))
    saved = json.loads((owner.stage / "install-failure.json").read_text())
    assert saved["exitCode"] == 1 and saved["stderrBytes"] == 42
    assert "SECRET" not in json.dumps(saved)