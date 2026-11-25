"""Installation calls the canonical no-model qualifier, never a user worker."""
import asyncio
from contextlib import contextmanager
import json
import os
import threading

import pytest

from amplifier_web import installation_runtime as setup
from amplifier_web import runtime_environment, runtime_qualification, updates


def configured_inputs(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    settings = {"config": {"providers": [{"id": "fixture-account", "module": "provider-fixture",
        "source": "git+https://example.invalid/provider@" + "a" * 40,
        "config": {"api_key": "${FIXTURE_KEY}", "default_model": "offline"}}]},
        "sources": {"modules": {"tool-fixture": "git+https://example.invalid/tool@" + "b" * 40}}}
    (shared / "settings.yaml").write_text(json.dumps(settings))
    (shared / "keys.env").write_text("FIXTURE_KEY=synthetic-private-value\n")
    (shared / "keys.env").chmod(0o600)
    (shared / "routing").mkdir()
    (shared / "routing/custom.yaml").write_text("name: configured-fixture\n")
    for excluded in ("projects", "cache", "registry", "oauth"):
        (shared / excluded).mkdir()
        (shared / excluded / "retained").write_text("must stay at its existing owner")
    monkeypatch.setenv("AMPLIFIER_HOME", str(shared))
    return shared, settings


async def test_configured_prepare_snapshots_actual_shared_inputs_before_qualification(tmp_path, monkeypatch):
    from amplifier_web.host.config import read_config
    shared, settings = configured_inputs(tmp_path, monkeypatch)
    home = tmp_path / "app"
    home.mkdir()
    before = {p.relative_to(shared): p.read_bytes() for p in shared.rglob("*") if p.is_file()}
    phases = []

    async def validate(owner, stage, generation):
        # Exercise the real global settings reader at the same staging boundary
        # as UpdateManager.validate; dependency installation is outside this test.
        config = read_config(stage, home=stage, shared_home=stage / "shared-config", global_only=True)
        assert len(config.providers) == 1
        assert config.providers[0]["id"] == "fixture-account"
        assert config.settings == settings
        snapshot = stage / "shared-config"
        assert {p.name for p in snapshot.iterdir()} == {"settings.yaml", "keys.env", "routing"}
        for name in ("settings.yaml", "keys.env", "routing/custom.yaml"):
            assert (snapshot / name).read_bytes() == (shared / name).read_bytes()
            assert not (snapshot / name).samefile(shared / name)
        if os.name != "nt":
            assert snapshot.stat().st_mode & 0o777 == 0o700
            assert (snapshot / "keys.env").stat().st_mode & 0o777 == 0o600
        phases.append("validate")
        (stage / "profiles-qualified.json").write_text(json.dumps({"profiles": ["work"]}))
        (stage / "runtime-installed.json").write_text('[{"name":"synthetic","version":"1"}]')
        (stage / "runtime.lock").write_text("exact-locked-graph")
        project = owner.home / "runtime" / ("q-" + "a" * 32)
        project.mkdir(parents=True)
        (stage / "runtime-project.json").write_text(json.dumps({"project": project.name}))

    monkeypatch.setattr(updates.UpdateManager, "validate", validate)
    monkeypatch.setattr(runtime_qualification, "verify_recorded", lambda *a: phases.append("verify"))
    result = await setup.prepare(home)
    assert result["prepared"] and result["modelRequestSent"] is False
    assert phases == ["validate", "verify"]
    assert updates.active_release(home)["current"] == result["generation"]
    assert before == {p.relative_to(shared): p.read_bytes() for p in shared.rglob("*") if p.is_file()}
    # The existing-generation path inspects its receipt; it must not copy again.
    from amplifier_web import update_storage
    async def forbidden(*args):
        pytest.fail("Recorded runtime cannot snapshot or refresh configuration")
    monkeypatch.setattr(update_storage, "snapshot_shared_config", forbidden)
    assert await setup.prepare(home) == result


async def test_shared_snapshot_failure_retains_attempt_without_validation(tmp_path, monkeypatch):
    from amplifier_web import update_storage
    configured_inputs(tmp_path, monkeypatch)
    home = tmp_path / "app"
    home.mkdir()
    original = update_storage.shutil.copy2
    def failed(source, target):
        if source.name == "keys.env":
            raise OSError("synthetic-private-value")
        return original(source, target)
    monkeypatch.setattr(update_storage.shutil, "copy2", failed)
    async def forbidden(*args):
        pytest.fail("Incomplete private snapshot must not reach validation")
    monkeypatch.setattr(updates.UpdateManager, "validate", forbidden)
    with pytest.raises(OSError):
        await setup.prepare(home)
    stage, = (home / "updates/releases").iterdir()
    assert (stage / "shared-config/settings.yaml").is_file()
    assert not (stage / "validated.json").exists() and updates.active_release(home) == {}
    failure = json.loads((stage / "install-failure.json").read_text())
    assert failure["phase"] == "initial-shared-config"
    assert "synthetic-private-value" not in json.dumps(failure)


@pytest.mark.parametrize("unsettled", [False, True])
async def test_cancelled_snapshot_never_validates_or_erases_inflight_copy(tmp_path, monkeypatch, unsettled):
    from amplifier_web import update_storage
    configured_inputs(tmp_path, monkeypatch)
    home = tmp_path / "app"
    home.mkdir()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = update_storage.shutil.copy2
    def held(source, target):
        started.set()
        assert release.wait(5), "owned test copy was not released"
        try:
            return original(source, target)
        finally:
            finished.set()
    monkeypatch.setattr(update_storage.shutil, "copy2", held)
    async def forbidden(*args):
        pytest.fail("Cancelled snapshot cannot validate or activate")
    monkeypatch.setattr(updates.UpdateManager, "validate", forbidden)
    task = asyncio.create_task(setup.prepare(home))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)  # candidate_copy waits for its shielded copy.
        if unsettled:
            task.cancel()  # Cancellation of the join does not stop its thread.
        else:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() is not unsettled
        stage, = (home / "updates/releases").iterdir()
        assert stage.is_dir() and (stage / "shared-config").is_dir()
        assert not (stage / "validated.json").exists() and updates.active_release(home) == {}
        progress = json.loads((stage / "install-progress.json").read_text())
        assert progress["events"][-1]["status"] == "failed"
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        await asyncio.gather(task, return_exceptions=True)


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
    async def overrides(project, target, *, owned_root=None):
        assert owned_root == tmp_path
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


def test_baseline_composes_real_override_writer_with_explicit_private_owner(tmp_path, monkeypatch):
    """Do not stub prepare_overrides: the missing root was hidden by that seam."""
    from pathlib import Path
    from amplifier_web import deployment
    original_write = deployment.write_private
    writes, commands = [], []

    def require_owner(path, text, *, owned_root=None):
        # Same precondition as the native writer, without changing host os.name.
        assert owned_root == tmp_path, "Windows private writes require an explicit owned_root"
        assert Path(path).is_relative_to(owned_root)
        writes.append(Path(path).name)
        return original_write(path, text, owned_root=owned_root)

    async def command(*argv, **kwargs):
        commands.append(argv[1])
        if argv[1] == "lock":
            project = Path(argv[argv.index("--project") + 1])
            (project / "uv.lock").write_text("version = 1\n")
        if argv[1] == "export":
            assert "--frozen" in argv and "--offline" in argv
            return "amplifier-core==2.0.2\n"
        return ""

    monkeypatch.setattr(deployment, "write_private", require_owner)
    monkeypatch.setattr(updates, "process", command)
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(runtime_qualification, "installed_graph",
                        lambda path: [{"name": "amplifier-core", "version": "2.0.2"}])
    # Only the later inventory recheck is outside this writer-composition control.
    monkeypatch.setattr(setup, "_baseline_status", lambda home: {"prepared": True})
    assert asyncio.run(setup._prepare_baseline(tmp_path)) == {"prepared": True}
    receipt = runtime_environment.receipt_directory(tmp_path)
    assert (receipt / "runtime-install-overrides.txt").read_text() == "amplifier-core==2.0.2\n"
    policy = json.loads((receipt / "runtime-install-policy.json").read_text())
    assert policy["version"] == 2
    assert writes.index("runtime-install-overrides.txt") < writes.index("runtime-install-policy.json")
    assert writes.index("runtime-install-policy.json") < writes.index("installation-prepared.json")
    assert commands == ["lock", "sync", "pip", "export"]
    assert not (tmp_path / "updates/active.json").exists()
