"""Exercise real profile ensure with controlled installer/freeze boundaries."""

import asyncio
import copy
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from amplifier_web import runtime_profiles, runtime_qualification, updates
from amplifier_web.host.config import read_config
from amplifier_web.runtime_environment import receipt_directory


async def test_windows_custom_profile_refuses_before_snapshot_lock_or_process(tmp_path, monkeypatch):
    from amplifier_web.host import config as host_config
    from amplifier_web.host import bundle_paths
    home, generation = tmp_path / 'app', 'd' * 32
    receipt = receipt_directory(home, generation)
    receipt.mkdir(parents=True)
    settings = {}
    descriptor = {'profiles': ['offered'], 'configuration': runtime_profiles.configuration_key(settings, home, receipt)}
    (receipt / 'profiles-qualified.json').write_text(json.dumps(descriptor))
    monkeypatch.setattr(runtime_profiles, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(host_config, 'read_config', lambda *args, **kwargs:
                        SimpleNamespace(settings=settings, home=home, workspace=tmp_path))
    monkeypatch.setattr(bundle_paths, 'canonical_bundle_reference', lambda config, bundle: bundle)
    monkeypatch.setattr(bundle_paths, 'local_bundle_path', lambda *args: None)
    monkeypatch.setattr(runtime_profiles, '_local_build_inputs', lambda *args: set())
    async def forbidden(*args, **kwargs): pytest.fail('uncontained preparation process')
    monkeypatch.setattr(updates, 'process', forbidden)
    # Offered and legacy baseline paths remain available without new effects.
    assert await runtime_profiles.ensure(home, None, {}) is None
    assert await runtime_profiles.ensure(home, generation, {'id': 'offered', 'workspace': str(tmp_path), 'bundle': 'offered'}) == generation
    with pytest.raises(RuntimeError, match='Custom runtime preparation is unavailable'):
        await runtime_profiles.ensure(home, generation, {'id': 'custom', 'workspace': str(tmp_path), 'bundle': 'changed'})
    assert not (home / 'updates/profiles').exists()
    assert list((home / 'updates/releases').iterdir()) == [receipt]


@pytest.fixture
def profile_inputs(tmp_path, monkeypatch):
    from amplifier_foundation.modules import preparation

    home = tmp_path / "app"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "non-code"
    outside.mkdir()
    (outside / "note.txt").write_text("Not a build input")
    (workspace / "external-documents").symlink_to(outside, target_is_directory=True)
    artifacts = workspace / "tasks" / "commission-one"
    artifacts.mkdir(parents=True)
    instructions = workspace / "instructions.md"
    instructions.write_text("Work only in the task directory.")
    cache = workspace / ".cache"
    cache.mkdir()
    (cache / "cache.json").write_text("{}")
    policy = {
        "allowed_write_paths": [str(workspace), str(artifacts)],
        "cache_dir": str(workspace / ".cache"),
        "base_path": str(workspace),
        "project_dir": str(workspace),
        # These strings/dictionaries are config, not declarations, even when
        # they resemble a module row or an included local bundle.
        "source": str(workspace),
        "extra": {"module": "not-a-module", "source": str(outside)},
        "includes": [{"bundle": str(workspace)}],
        "instruction": str(instructions),
        "context": {"policy": str(instructions)},
    }
    plan = {
        "session": {
            "orchestrator": {"module": "loop-live", "config": copy.deepcopy(policy)},
            "context": {"module": "context-simple", "config": copy.deepcopy(policy)},
        },
        "providers": [{
            "module": "provider-openai",
            "id": "primary",
            "source": "git+https://example.invalid/provider",
            "config": {"api_key": "${FIXTURE_KEY}", "default_model": "fixture"},
        }],
        "tools": [{"module": "tool-filesystem", "config": copy.deepcopy(policy)}],
        "hooks": [{"module": "hook-fixture", "config": copy.deepcopy(policy)}],
        "agents": {"worker": {
            "tools": [{"module": "tool-agent", "config": copy.deepcopy(policy)}],
            "agents": {"nested": {"hooks": [{"module": "hook-agent"}]}},
            "project_dir": str(workspace),
            "source_base_paths": {"resources": str(workspace)},
        }},
        "instruction": str(instructions),
        "context": {
            "instructions": str(instructions),
            "module": str(instructions),
            "source": str(instructions),
        },
        "project_dir": str(workspace),
        "base_path": str(workspace),
        "source_base_paths": {"work": str(workspace)},
    }
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(shared))
    monkeypatch.setenv("UV_OVERRIDE", "/fixture/caller-policy.txt")
    monkeypatch.setenv("UV_CONSTRAINT", "/fixture/caller-constraints.txt")
    # Ordinary credential references are copied, never expanded for identity.
    (shared / "keys.env").write_text("FIXTURE_KEY=not-a-real-credential\n")
    (shared / "routing").mkdir()
    (shared / "routing" / "matrix.yaml").write_text("fixture: true\n")
    generation = "a" * 32
    receipt = receipt_directory(home, generation)
    (receipt / "foundation").mkdir(parents=True)
    (receipt / "foundation" / "registry.json").write_text(json.dumps({
        "fixture": str(receipt / "foundation" / "cache"),
    }))
    parent = home / "runtime" / ("q-" + "b" * 32)
    parent.mkdir(parents=True)
    (parent / "pyproject.toml").write_text("project = {}")
    (parent / "uv.lock").write_text("version = 1")
    (receipt / "runtime-project.json").write_text(json.dumps({"project": parent.name}))
    harness = SimpleNamespace(
        home=home, workspace=workspace, artifacts=artifacts, shared=shared,
        generation=generation, receipt=receipt, parent=parent, plan=plan,
        settings={"bundle": {"active": "work", "app": []}, "config": {
            "tools": [{"module": "tool-settings", "config": copy.deepcopy(policy)}],
        }},
        calls=[], scans=[], process_hook=None, freeze_hook=None,
    )

    def save_settings():
        (shared / "settings.yaml").write_text(yaml.safe_dump(harness.settings))
        config = read_config(
            workspace, home=home, registry_home=receipt / "foundation"
        )
        (receipt / "profiles-qualified.json").write_text(json.dumps({
            "profiles": ["work"],
            "configuration": runtime_profiles.configuration_key(
                config.settings, receipt / "foundation", home
            ),
        }))

    def child(identity, plan=None, bundle="work"):
        runtime_id = "native-" + identity
        path = home / "sessions" / runtime_id / "configuration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(harness.plan if plan is None else plan))
        return {
            "id": identity, "runtimeSessionId": runtime_id,
            "workspace": str(workspace), "bundle": bundle,
        }

    def signature(path):
        harness.scans.append(Path(path))
        return actual_signature(path)

    actual_signature = preparation.source_signature
    monkeypatch.setattr(preparation, "source_signature", signature)
    monkeypatch.setattr(runtime_profiles.shutil, "which", lambda _: "/fixture/uv")

    async def process(*args, **kwargs):
        harness.calls.append((args, kwargs))
        assert kwargs["timeout"] == 900  # Phase bounds are unchanged.
        if "--install-overrides" in args:
            env = kwargs["env"]
            assert env["UV_OVERRIDE"] == "/fixture/caller-policy.txt"
            assert env["UV_CONSTRAINT"] == "/fixture/caller-constraints.txt"
            assert env["AMPLIFIER_UNIFIED_RELEASE"] == ""
            assert len(env["AMPLIFIER_INSTALL_PREPARATION"]) == 32
        if harness.process_hook:
            await harness.process_hook(*args, **kwargs)
        await asyncio.sleep(0)
        return ""

    async def overrides(project, target, *, owned_root):
        assert owned_root == home
        target.write_text("fixture-installed==1\n")
        return target

    async def freeze(manager, selected, project, *, baseline_manifest):
        assert project != parent
        assert baseline_manifest == (parent / "pyproject.toml").read_bytes()
        final = home / "runtime" / ("q-" + selected)
        final.mkdir()
        (final / "pyproject.toml").write_bytes((project / "pyproject.toml").read_bytes())
        (final / "uv.lock").write_bytes((project / "uv.lock").read_bytes())
        if harness.freeze_hook:
            await harness.freeze_hook(manager, final)
        target = receipt_directory(home, selected)
        (target / "runtime-project.json").write_text(json.dumps({"project": final.name}))
        return final

    monkeypatch.setattr(updates, "process", process)
    monkeypatch.setattr(runtime_qualification, "prepare_overrides", overrides)
    monkeypatch.setattr(runtime_qualification, "freeze", freeze)
    monkeypatch.setattr(runtime_qualification, "verify_recorded", lambda *args: None)
    harness.save_settings = save_settings
    harness.child = child
    save_settings()
    return harness


@pytest.mark.parametrize("edit_kind", ["policy", "provider-config", "instruction"])
async def test_workspace_policy_paths_and_artifacts_do_not_change_sibling_identity(
    profile_inputs, edit_kind,
):
    h = profile_inputs
    first_child, second_child = h.child("first"), h.child("second")
    parent_descriptor = (h.receipt / "profiles-qualified.json").read_bytes()
    selected = await runtime_profiles.ensure(h.home, h.generation, first_child)
    assert selected != h.generation  # An edited child cannot borrow the offering.
    (h.artifacts / "result.py").write_text("A task artifact, not module code")
    (h.workspace / ".cache" / "cached.py").write_text("Cached data, not declared code")
    (h.workspace / "instructions.md").write_text("Updated non-code instructions")
    assert await runtime_profiles.ensure(h.home, h.generation, second_child) == selected
    assert await runtime_profiles.ensure(h.home, h.generation, first_child) == selected
    assert not h.scans
    assert len(h.calls) == 3
    stage = receipt_directory(h.home, selected)
    assert json.loads((stage / "runtime-plan.json").read_text()) == h.plan
    assert (stage / "shared-config" / "keys.env").read_bytes() == (h.shared / "keys.env").read_bytes()
    assert (stage / "shared-config" / "routing" / "matrix.yaml").is_file()
    assert str(stage / "foundation") in (stage / "foundation" / "registry.json").read_text()
    assert (h.receipt / "profiles-qualified.json").read_bytes() == parent_descriptor
    attempt = json.loads((stage / "profile-attempt.json").read_text())
    assert attempt["status"] == "succeeded" and attempt["errorType"] is None
    for args, _ in h.calls[1:]:
        assert "--runtime-plan" in args
    assert "--read-only" in h.calls[-1][0]
    # Keep the full edited-plan hash, including policy/prompt/provider config.
    edited = copy.deepcopy(h.plan)
    if edit_kind == "policy":
        edited["tools"][0]["config"]["allowed_write_paths"].append(str(h.workspace / "new-task"))
    elif edit_kind == "provider-config":
        edited["providers"][0]["config"]["default_model"] = "changed-fixture"
    else:
        edited["instruction"] = "An explicitly edited instruction"
    changed_child = h.child("changed", edited)
    changed = await runtime_profiles.ensure(h.home, h.generation, changed_child)
    assert changed not in {selected, h.generation}
    assert len(h.calls) == 6 and not h.scans


@pytest.mark.parametrize("declaration", [
    "tool", "provider", "orchestrator", "context-module", "agent", "nested-agent",
    "settings-module", "module-override", "bundle-source", "bundle-added",
    "app-bundle", "include", "local-bundle", "local-bundle-file", "local-bundle-uri",
    "relative-module", "spawn-tool", "relative-module-fragment", "escaped-module-uri",
])
async def test_actual_local_source_content_requalifies(profile_inputs, declaration):
    h = profile_inputs
    code = (
        h.workspace / "local-code" if declaration in {"relative-module", "relative-module-fragment"}
        else h.workspace.parent / "local code" if declaration == "escaped-module-uri"
        else h.workspace.parent / "local-code"
    )
    code.mkdir()
    payload = code / "implementation.py"
    if declaration in {"relative-module-fragment", "escaped-module-uri"}:
        # The fragment names real nested source. Only that nested file changes,
        # proving the retained parent-directory signature covers its contents.
        (code / "module").mkdir()
        payload = code / "module" / "implementation.py"
    payload.write_text("VALUE = 1\n")
    manifest = code / "bundle.yaml"
    manifest.write_text("bundle:\n  name: fixture\n")
    source = code.as_uri()
    bundle = "work"
    if declaration == "tool":
        h.plan["tools"][0]["source"] = source
    elif declaration == "spawn-tool":
        h.plan["spawn"] = {"tools": [{"module": "tool-lazy", "source": source}]}
    elif declaration == "relative-module":
        h.plan["tools"][0]["source"] = "./local-code"
    elif declaration == "relative-module-fragment":
        h.plan["tools"][0]["source"] = "./local-code#subdirectory=module"
    elif declaration == "escaped-module-uri":
        h.plan["tools"][0]["source"] = source + "#subdirectory=module"
    elif declaration == "provider":
        h.plan["providers"][0]["source"] = source
    elif declaration == "orchestrator":
        h.plan["session"]["orchestrator"]["source"] = source
    elif declaration == "context-module":
        h.plan["session"]["context"]["source"] = source
    elif declaration == "agent":
        h.plan["agents"]["worker"]["tools"][0]["source"] = source
    elif declaration == "nested-agent":
        h.plan["agents"]["worker"]["agents"]["nested"]["hooks"][0]["source"] = source
    elif declaration == "settings-module":
        h.settings["config"]["tools"][0]["source"] = source
    elif declaration == "module-override":
        h.settings["sources"] = {"modules": {"tool-filesystem": source}}
    elif declaration == "bundle-source":
        h.settings["sources"] = {"bundles": {"work": source}}
    elif declaration == "bundle-added":
        h.settings["bundle"]["added"] = {"custom": source}
    elif declaration == "app-bundle":
        h.settings["bundle"]["app"] = [source + "#subdirectory=bundle.yaml"]
    elif declaration == "include":
        h.plan["includes"] = [{"bundle": source + "#subdirectory=bundle.yaml"}]
    elif declaration == "local-bundle":
        bundle = str(code)
    elif declaration == "local-bundle-uri":
        bundle = source + "#subdirectory=bundle.yaml"
    else:
        bundle = str(manifest)
    h.save_settings()
    child = h.child("source", bundle=bundle)
    selected = await runtime_profiles.ensure(h.home, h.generation, child)
    assert selected != h.generation
    assert await runtime_profiles.ensure(h.home, h.generation, child) == selected
    assert len(h.calls) == 3
    assert set(h.scans) == {code}
    payload.write_text("VALUE = 2\n")
    changed = await runtime_profiles.ensure(h.home, h.generation, child)
    assert changed not in {h.generation, selected}
    assert len(h.calls) == 6
    assert set(h.scans) == {code}


async def test_sibling_local_bundles_share_one_scan_per_check(profile_inputs):
    h = profile_inputs
    code = h.workspace.parent / "local-code"
    code.mkdir()
    bundles = [code / f"{name}.yaml" for name in ("one", "two", "three")]
    for path in bundles:
        path.write_text(f"bundle:\n  name: {path.stem}\n")
    h.plan["includes"] = [{"bundle": str(path)} for path in bundles]
    other = h.workspace.parent / "other-code"
    other.mkdir()
    payload = other / "implementation.py"
    payload.write_text("VALUE = 1\n")
    h.plan["tools"][0]["source"] = str(other)
    child = h.child("siblings")

    selected = await runtime_profiles.ensure(h.home, h.generation, child)
    assert sorted(h.scans) == sorted([code, other])
    h.scans.clear()
    assert await runtime_profiles.ensure(h.home, h.generation, child) == selected
    assert sorted(h.scans) == sorted([code, other])
    assert len(h.calls) == 3

    # A later sibling-file edit must invalidate the previously qualified setup.
    bundles[-1].write_text("bundle:\n  name: changed\n")
    h.scans.clear()
    changed = await runtime_profiles.ensure(h.home, h.generation, child)
    assert changed not in {selected, h.generation}
    assert sorted(h.scans) == sorted([code, other])
    assert len(h.calls) == 6

    # An independent source root remains part of the qualification identity.
    payload.write_text("VALUE = 2\n")
    assert await runtime_profiles.ensure(h.home, h.generation, child) not in {
        selected, changed, h.generation,
    }


async def test_real_module_outward_symlink_still_fails_closed(profile_inputs):
    h = profile_inputs
    code = h.workspace.parent / "unsafe-code"
    code.mkdir()
    (code / "external-input").symlink_to(h.workspace, target_is_directory=True)
    h.plan["tools"][0]["source"] = code.as_uri()
    with pytest.raises(ValueError, match="External source symlink"):
        await runtime_profiles.ensure(h.home, h.generation, h.child("unsafe"))
    assert h.scans == [code]
    assert not h.calls
    # This preflight failure never allocated a UUID attempt or an index.
    assert list((h.home / "updates" / "releases").iterdir()) == [h.receipt]
    assert not (h.home / "updates" / "profiles").exists()


async def test_concurrent_identical_siblings_coalesce_then_cache_without_process(
    profile_inputs,
):
    h = profile_inputs
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_sync(*args, **kwargs):
        if args[1] == "sync":
            entered.set()
            await release.wait()

    h.process_hook = hold_sync
    children = [h.child("first"), h.child("second")]
    first = asyncio.create_task(runtime_profiles.ensure(h.home, h.generation, children[0]))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(runtime_profiles.ensure(h.home, h.generation, children[1]))
        await asyncio.sleep(0)
        (h.artifacts / "result.txt").write_text("Written while the sibling waits")
        release.set()
        one, two = await asyncio.wait_for(asyncio.gather(first, second), 5)
    finally:
        release.set()
        await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
    assert one == two and one != h.generation
    assert len(h.calls) == 3
    assert await runtime_profiles.ensure(h.home, h.generation, children[1]) == one
    assert len(h.calls) == 3
    assert len(list((h.home / "updates" / "profiles").glob("*.json"))) == 1
    assert len(list((h.home / "runtime").glob("prepare-*"))) == 1


def retained_attempt(h, status, error_type):
    stages = [path for path in (h.home / "updates" / "releases").iterdir() if path != h.receipt]
    assert len(stages) == 1
    stage = stages[0]
    attempt = json.loads((stage / "profile-attempt.json").read_text())
    assert attempt["generation"] == stage.name
    assert attempt["parentGeneration"] == h.generation
    assert attempt["status"] == status and attempt["errorType"] == error_type
    assert attempt["finishedAt"] >= attempt["startedAt"]
    assert attempt["elapsedMs"] >= 0
    assert attempt["preflightMs"] >= 0 and attempt["lockWaitMs"] >= 0
    assert all(row.get("elapsedMs", 0) >= 0 for row in attempt["timings"])
    assert attempt["paths"]["receipt"] == str(stage)
    assert attempt["paths"]["foundation"] == str(stage / "foundation")
    assert attempt["paths"]["sharedConfig"] == str(stage / "shared-config")
    assert attempt["cleanup"]["owner"] == "runtime environment owner"
    assert "threads and processes have stopped" in attempt["cleanup"]["responsibility"]
    assert "Never replay" in attempt["cleanup"]["responsibility"]
    assert not (stage / "validated.json").exists()
    assert not (stage / "profiles-qualified.json").exists()
    assert not Path(attempt["paths"]["profileIndex"]).exists()
    return stage, attempt


async def test_successful_phase_timings_stay_private_and_cache_reuse_does_not_rewrite(profile_inputs):
    h = profile_inputs
    child = h.child("timed")
    selected = await runtime_profiles.ensure(h.home, h.generation, child)
    path = receipt_directory(h.home, selected) / "profile-attempt.json"
    saved = path.read_bytes()
    attempt = json.loads(saved)
    assert attempt["status"] == "succeeded"
    assert attempt["elapsedMs"] >= 0
    assert attempt["preflightMs"] >= 0 and attempt["lockWaitMs"] >= 0
    phases = {row["phase"]: row for row in attempt["timings"]}
    assert set(phases) == {"source-snapshot", "runtime-prepare", "installer-policy",
                           "dependency-prepare", "runtime-freeze", "compatibility-probe",
                           "recorded-verification"}
    assert all(row["status"] == "succeeded" and row["elapsedMs"] >= 0 for row in phases.values())
    for private in ("not-a-real-credential", "FIXTURE_KEY", "allowed_write_paths", "Work only in the task directory"):
        assert private not in saved.decode()
    assert await runtime_profiles.ensure(h.home, h.generation, child) == selected
    assert path.read_bytes() == saved
    assert len(h.calls) == 3


@pytest.mark.parametrize("phase,error_type", [
    (1, TimeoutError), (2, RuntimeError), (3, ValueError),
])
async def test_failed_qualification_retains_attempt_without_admission(
    profile_inputs, phase, error_type,
):
    h = profile_inputs

    async def fail(*args, **kwargs):
        if len(h.calls) == phase:
            raise error_type("fixture failure")

    h.process_hook = fail
    with pytest.raises(error_type):
        await runtime_profiles.ensure(h.home, h.generation, h.child("failed"))
    stage, attempt = retained_attempt(h, "failed", error_type.__name__)
    failed = [row for row in attempt["timings"] if row["status"] == "failed"]
    assert failed[-1]["phase"] == {1: "runtime-prepare", 2: "dependency-prepare", 3: "compatibility-probe"}[phase]
    assert failed[-1]["errorType"] == error_type.__name__
    assert Path(attempt["paths"]["preparationProject"]).is_dir()
    assert (stage / "foundation").is_dir()
    if phase == 3:
        assert Path(attempt["paths"]["qualifiedProject"]).is_dir()
    await asyncio.sleep(0)
    assert len(h.calls) == phase  # No automatic restart/replay after failure.


async def test_freeze_failure_records_retained_qualified_project(profile_inputs):
    h = profile_inputs

    async def fail_freeze(manager, final):
        async def failure(*args):
            raise ValueError("fixture graph mismatch")

        await manager.diagnostics.run(
            "ecosystem-runtime-freeze-install", failure,
            "uv", "sync", "--project", str(final),
        )

    h.freeze_hook = fail_freeze
    with pytest.raises(ValueError, match="graph mismatch"):
        await runtime_profiles.ensure(h.home, h.generation, h.child("freeze"))
    _, attempt = retained_attempt(h, "failed", "ValueError")
    assert attempt["phase"] == "ecosystem-runtime-freeze-install"
    assert [row["phase"] for row in attempt["timings"] if row["status"] == "failed"] == [
        "runtime-freeze", "ecosystem-runtime-freeze-install"]
    assert Path(attempt["paths"]["qualifiedProject"]).is_dir()
    assert len(h.calls) == 2


async def test_publication_failure_cannot_leave_validated_or_index(profile_inputs, monkeypatch):
    from amplifier_web.host import config as config_module

    h = profile_inputs
    write = config_module.write_private

    def fail_index(path, content):
        write(path, content)
        if path.parent == h.home / "updates" / "profiles" and path.suffix == ".json":
            # Even an error reported after the atomic index write landed must
            # not leave an apparently qualified failure available to a sibling.
            raise OSError("fixture publication failure")

    monkeypatch.setattr(config_module, "write_private", fail_index)
    with pytest.raises(OSError, match="publication failure"):
        await runtime_profiles.ensure(h.home, h.generation, h.child("publication"))
    _, attempt = retained_attempt(h, "failed", "OSError")
    assert Path(attempt["paths"]["qualifiedProject"]).is_dir()
    assert len(h.calls) == 3


@pytest.mark.parametrize("status", ["preparing", "failed", "cancelled"])
async def test_incomplete_attempt_cannot_be_an_index_hit(profile_inputs, status):
    h = profile_inputs
    child = h.child("incomplete")
    selected = await runtime_profiles.ensure(h.home, h.generation, child)
    stage = receipt_directory(h.home, selected)
    path = stage / "profile-attempt.json"
    attempt = json.loads(path.read_text())
    attempt["status"] = status
    path.write_text(json.dumps(attempt))
    # A stale/partial publication is not an admission receipt. Explicit ensure
    # qualifies a distinct attempt; it must not resume or rewrite the old one.
    changed = await runtime_profiles.ensure(h.home, h.generation, child)
    assert changed not in {selected, h.generation}
    assert len(h.calls) == 6
    assert json.loads(path.read_text())["status"] == status


async def test_cancelled_process_attempt_is_retained_and_not_replayed(profile_inputs):
    h = profile_inputs
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    h.process_hook = blocked
    task = asyncio.create_task(runtime_profiles.ensure(h.home, h.generation, h.child("cancel")))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    _, attempt = retained_attempt(h, "cancelled", "CancelledError")
    assert any(row["status"] == "cancelled" and row["phase"] == "runtime-prepare" for row in attempt["timings"])
    assert Path(attempt["paths"]["preparationProject"]).is_dir()
    await asyncio.sleep(0)
    assert len(h.calls) == 1


@pytest.mark.parametrize("seam", ["copy", "verify"])
async def test_cancel_retains_stage_while_to_thread_writer_finishes(
    profile_inputs, monkeypatch, seam,
):
    from amplifier_web import update_storage

    h = profile_inputs
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_copy = update_storage.copy_snapshot
    written = []

    def writer(first, target):
        if seam == "copy":
            original_copy(first, target)
        entered.set()
        try:
            assert release.wait(5)
            marker = Path(target) / "late-thread-output.txt"
            marker.write_text("Writer completed after coroutine cancellation")
            written.append(marker)
        finally:
            finished.set()

    if seam == "copy":
        monkeypatch.setattr(update_storage, "copy_snapshot", writer)
    else:
        monkeypatch.setattr(runtime_qualification, "verify_recorded", writer)
    task = asyncio.create_task(runtime_profiles.ensure(h.home, h.generation, h.child("thread")))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        stage, attempt = retained_attempt(h, "cancelled", "CancelledError")
        assert not finished.is_set()
        assert (stage / "foundation").is_dir()
        if seam == "verify":
            assert Path(attempt["paths"]["qualifiedProject"]).is_dir()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    assert len(written) == 1 and written[0].is_file()
    retained_attempt(h, "cancelled", "CancelledError")
    assert len(h.calls) == (0 if seam == "copy" else 3)


@pytest.mark.parametrize('outcome', ['failure', 'cancellation'])
async def test_index_preparation_cannot_publish_failed_or_cancelled_profile(
    profile_inputs, monkeypatch, outcome,
):
    from amplifier_web import update_storage

    h = profile_inputs
    original = h.receipt / 'foundation/cache/source'
    original.mkdir(parents=True)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=original)
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (original / 'source.txt').write_text('unchanged source\n')
    git('add', '.')
    git('commit', '-m', 'fixture')
    source_index = (original / '.git/index').read_bytes()
    source_head, source_stage = git('rev-parse', 'HEAD'), git('ls-files', '--stage', '-z')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    actual_run = subprocess.run
    actual_copy = update_storage.copy_snapshot
    copies = []

    def refresh(command, **kwargs):
        if command[-3:] != ['status', '--porcelain', '--untracked-files=no']:
            return actual_run(command, **kwargs)
        copied = Path(kwargs['cwd'])
        assert copied != original and copied.is_relative_to(h.home / 'updates/releases')
        copies.append(copied)
        entered.set()
        if outcome == 'failure':
            raise subprocess.CalledProcessError(1, command)
        assert release.wait(5)
        return actual_run(command, **kwargs)

    def copy(source, target):
        try:
            return actual_copy(source, target)
        finally:
            finished.set()

    monkeypatch.setattr(update_storage.subprocess, 'run', refresh)
    monkeypatch.setattr(update_storage, 'copy_snapshot', copy)
    task = asyncio.create_task(runtime_profiles.ensure(h.home, h.generation, h.child('index')))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if outcome == 'failure':
            with pytest.raises(subprocess.CalledProcessError):
                await task
            status, error_type = 'failed', 'CalledProcessError'
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not finished.is_set()
            status, error_type = 'cancelled', 'CancelledError'
        stage, attempt = retained_attempt(h, status, error_type)
        assert copies == [stage / 'foundation/cache/source']
        assert attempt['timings'][-1]['phase'] == 'source-snapshot'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
    retained_attempt(h, status, error_type)
    assert not h.calls  # No installer, probe or qualified generation publication.
    assert (original / '.git/index').read_bytes() == source_index
    assert git('rev-parse', 'HEAD') == source_head
    assert git('ls-files', '--stage', '-z') == source_stage
    assert (original / 'source.txt').read_text() == 'unchanged source\n'


@pytest.mark.parametrize('declaration', ['added-default', 'added-shadowed'])
async def test_redundant_bundle_registration_reuses_offered_profile(profile_inputs, declaration):
    from amplifier_web.host.config import WORK_SOURCE
    h = profile_inputs
    if declaration == 'added-shadowed':
        h.settings['sources'] = {'bundles': {'work': 'git+https://example.invalid/qualified-work'}}
        h.save_settings()
    h.settings['bundle']['added'] = {'work': WORK_SOURCE}
    # Change the live settings without rewriting the qualification receipt.
    (h.shared / 'settings.yaml').write_text(yaml.safe_dump(h.settings))
    session = {'id': 'unchanged', 'workspace': str(h.workspace), 'bundle': 'work'}
    assert await runtime_profiles.ensure(h.home, h.generation, session) == h.generation
    assert not h.calls


async def test_new_lazy_bundle_registration_still_requires_qualification(profile_inputs):
    h = profile_inputs
    h.settings['bundle']['added'] = {'lazy-agent': 'git+https://example.invalid/new-agent'}
    (h.shared / 'settings.yaml').write_text(yaml.safe_dump(h.settings))
    session = {'id': 'changed', 'workspace': str(h.workspace), 'bundle': 'work'}
    selected = await runtime_profiles.ensure(h.home, h.generation, session)
    assert selected != h.generation
    assert len(h.calls) == 3
    assert await runtime_profiles.ensure(h.home, h.generation, session) == selected
    assert len(h.calls) == 3


@pytest.mark.parametrize('kind', ['module', 'bundle'])
async def test_removing_last_source_reuses_original_qualified_profile(profile_inputs, kind):
    from amplifier_web.registry import RegistryManager
    h = profile_inputs
    manager = RegistryManager(h.home)
    args = {'kind': kind, 'name': 'temporary', 'scope': 'global',
            'workspace': str(h.workspace), 'source': 'git+https://example.invalid/temporary'}
    baseline = runtime_profiles.configuration_key(h.settings)
    await manager.perform('sources.save', args)
    edited = manager.store.read(h.workspace, 'global')
    assert runtime_profiles.configuration_key(edited) != baseline
    await manager.perform('sources.remove', args)
    assert manager.store.read(h.workspace, 'global')['sources'][kind+'s'] == {}
    session = {'id': 'restored', 'workspace': str(h.workspace), 'bundle': 'work'}
    assert await runtime_profiles.ensure(h.home, h.generation, session) == h.generation
    assert not h.calls and not h.scans


def test_empty_source_normalization_keeps_nonempty_and_unknown_declarations():
    key = runtime_profiles.configuration_key
    assert key({'sources': {'modules': {}, 'bundles': {}}}) == key({})
    for sources in ({'modules': {'provider-one': '/private/source'}},
                    {'bundles': {'work': 'git+https://example.invalid/work'}},
                    {'unknown': {}}):
        assert key({'sources': sources}) != key({})


@pytest.fixture
async def profile_freeze(tmp_path, monkeypatch):
    """Real offline uv preparation/freeze; only native bundle probes are synthetic."""
    import hashlib
    import shutil
    import zipfile
    from amplifier_web import runtime_environment as environments
    from amplifier_web.host import bundle_paths, config as host_config
    from test_runtime_updates import Diagnostics

    uv = shutil.which('uv')
    assert uv, 'The prepared Python test profile must include uv'
    for key in ('UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_PROJECT_ENVIRONMENT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('UV_OFFLINE', 'true')
    monkeypatch.setenv('UV_NO_INDEX', 'true')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    home, shared, workspace = (tmp_path / name for name in ('app', 'shared', 'workspace'))
    shared.mkdir()
    workspace.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))

    # Small importable wheels with no registry/build/backend dependencies.
    def wheel(name):
        path = tmp_path / f'{name}-1.0-py3-none-any.whl'
        metadata = f'{name}-1.0.dist-info'
        files = {
            f'{name}/__init__.py': b'VALUE = 1\n',
            f'{metadata}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n'.encode(),
            f'{metadata}/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        }
        files[f'{metadata}/RECORD'] = ''.join(f'{name},,\n' for name in files).encode()
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        return path

    wheels = {name: wheel(name) for name in ('fixture_old', 'fixture_new', 'fixture_lazy', 'fixture_unexpected')}
    def manifest(names):
        return ('[project]\nname="fixture-worker"\nversion="1"\nrequires-python=">=3.13"\n'
                'dependencies=' + json.dumps([
                    name.replace('_', '-') + ' @ ' + wheels[name].as_uri() for name in names
                ]) + '\n[tool.uv]\npackage=false\n').encode()

    baseline_a = manifest(['fixture_old'])
    baseline_b = manifest(['fixture_old', 'fixture_new'])
    monkeypatch.setattr(environments, 'declared_manifest', lambda **kwargs: baseline_a)
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    generation = 'a' * 32
    receipt = receipt_directory(home, generation)
    (receipt / 'foundation').mkdir(parents=True)
    prepared = environments.prepare_project(home, generation, content=baseline_a)
    process = updates.process
    await process(uv, 'lock', '--project', str(prepared), '--python', '3.13')
    await process(uv, 'sync', '--locked', '--project', str(prepared), '--python', '3.13')
    parent = await runtime_qualification.freeze(manager, generation, prepared)
    (receipt / 'profiles-qualified.json').write_text(json.dumps({
        'profiles': ['offered'], 'configuration': runtime_profiles.configuration_key({}),
    }))
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    monkeypatch.setattr(environments, 'declared_manifest', lambda **kwargs: baseline_b)
    config = SimpleNamespace(settings={}, config_home=shared, workspace=workspace,
                             active_bundle='work', module_sources={}, bundle_sources={}, home=home)
    monkeypatch.setattr(host_config, 'read_config', lambda *args, **kwargs: config)
    monkeypatch.setattr(bundle_paths, 'canonical_bundle_reference', lambda config, bundle: bundle)
    monkeypatch.setattr(bundle_paths, 'local_bundle_path', lambda *args: None)
    h = SimpleNamespace(home=home, generation=generation, receipt=receipt, parent=parent,
                        manager=manager, a=baseline_a, b=baseline_b, uv=uv, process=process,
                        session={'id': 'fixture', 'workspace': str(workspace), 'bundle': 'work'},
                        prepared_graphs=[], origins=[], commands=[], corruption=None, wheels=wheels)

    async def controlled_process(*args, **kwargs):
        h.commands.append(args)
        if any(str(arg).endswith('/update_probe.py') for arg in args):
            project = Path(args[args.index('--project') + 1])
            if '--read-only' not in args:
                # A profile-only package makes the newly frozen project
                # distinct from the reusable immutable parent qualification.
                await process(uv, 'pip', 'install', '--python', str(project / '.venv/bin/python'),
                              '--no-deps', str(wheels['fixture_lazy']))
            graph = runtime_qualification.installed_graph(project)
            h.prepared_graphs.append(graph)
            # Run the selected worker's actual Python, without Foundation/provider
            # composition. Assert the installed import origin, not just metadata.
            result = await process(
                str(project / '.venv/bin/python'), '-I', '-B', '-c',
                'import fixture_old, json, pathlib, sys; '
                'assert pathlib.Path(fixture_old.__file__).is_relative_to(sys.prefix); '
                'print(json.dumps({"python":sys.executable,"origin":fixture_old.__file__}))')
            h.origins.append(json.loads(result))
            return ''
        result = await process(*args, **kwargs)
        if '--project' in args and args[1] == 'sync':
            project = Path(args[args.index('--project') + 1])
            if project.name.startswith('q-'):
                if h.corruption == 'addition':
                    await process(uv, 'pip', 'install', '--python', str(project / '.venv/bin/python'),
                                  '--no-deps', str(wheels['fixture_unexpected']))
                elif h.corruption == 'payload':
                    next(project.glob('.venv/lib/python*/site-packages/fixture_old/__init__.py')).write_text('VALUE = 2\n')
        return result

    monkeypatch.setattr(updates, 'process', controlled_process)
    def snapshot():
        return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
                for root in (parent, receipt) for path in root.rglob('*') if path.is_file()}
    h.snapshot = snapshot
    h.before = snapshot()
    return h


@pytest.mark.parametrize('legacy', [False, True])
async def test_lazy_profile_freezes_exact_prepared_baseline_not_current_app(profile_freeze, legacy):
    from amplifier_web import runtime_environment as environments
    h = profile_freeze
    if legacy:
        (h.receipt / 'runtime-base.toml').unlink()
        h.before = h.snapshot()
    selected = await runtime_profiles.ensure(h.home, h.generation, h.session)
    final = environments.project_path(h.home, selected)
    receipt = receipt_directory(h.home, selected)
    assert selected != h.generation and final != h.parent
    assert [row['name'] for row in runtime_qualification.installed_graph(final)] == ['fixture-lazy', 'fixture-old']
    assert h.prepared_graphs[0] == h.prepared_graphs[1]
    baseline = (h.parent / 'pyproject.toml').read_bytes() if legacy else h.a
    assert (receipt / 'runtime-base.toml').read_bytes() == baseline
    assert (receipt / 'runtime-base.toml').read_bytes() != h.b
    assert environments.project_path(h.home, h.generation) == h.parent
    assert h.origins[0]['origin'].startswith(str(h.home / 'runtime/prepare-'))
    assert h.origins[1]['origin'].startswith(str(final / '.venv'))
    assert h.origins[1]['python'] == str(final / '.venv/bin/python')
    assert sum(args[1] == 'sync' for args in h.commands) == 2
    assert sum(args[1] == 'lock' for args in h.commands) == 1
    assert h.snapshot() == h.before
    # Warm reuse must not repeat preparation, freeze, probes or receipt writes.
    commands = list(h.commands)
    assert await runtime_profiles.ensure(h.home, h.generation, h.session) == selected
    assert h.commands == commands
    assert h.snapshot() == h.before

    # Real predecessor discriminator: preparing A but freezing from B adds one
    # distribution. This is deliberately not an admitted generation.
    wrong = h.home / 'wrong-freeze'
    wrong.mkdir()
    (wrong / 'pyproject.toml').write_bytes(runtime_qualification.frozen_manifest(h.b, h.prepared_graphs[0]))
    await h.process(h.uv, 'lock', '--project', str(wrong), '--python', '3.13')
    await h.process(h.uv, 'sync', '--locked', '--project', str(wrong), '--python', '3.13')
    assert [row['name'] for row in runtime_qualification.installed_graph(wrong)] == ['fixture-lazy', 'fixture-new', 'fixture-old']


async def test_fresh_component_generation_adopts_b_before_freeze(profile_freeze):
    from amplifier_web import runtime_environment as environments
    h = profile_freeze
    generation = 'b' * 32
    receipt = receipt_directory(h.home, generation)
    receipt.mkdir(parents=True)
    update = next(row for row in environments.inventory(h.home) if row['id'] == 'runtime:environment')
    project = await environments.stage(h.manager, generation, [update], finalize=False)
    await h.process(h.uv, 'sync', '--locked', '--project', str(project), '--python', '3.13')
    graph = runtime_qualification.installed_graph(project)
    assert [row['name'] for row in graph] == ['fixture-new', 'fixture-old']
    final = await runtime_qualification.freeze(h.manager, generation, project)
    assert runtime_qualification.installed_graph(final) == graph
    assert (receipt / 'runtime-base.toml').read_bytes() == h.b
    assert h.snapshot() == h.before


@pytest.mark.parametrize('corruption', ['addition', 'payload'])
async def test_profile_freeze_keeps_strict_graph_and_payload_refusal(profile_freeze, corruption):
    h = profile_freeze
    h.corruption = corruption
    with pytest.raises(ValueError, match='differs from its prepared source graph'):
        await runtime_profiles.ensure(h.home, h.generation, h.session)
    stages = [path for path in (h.home / 'updates/releases').iterdir() if path != h.receipt]
    assert len(stages) == 1
    stage = stages[0]
    attempt = json.loads((stage / 'profile-attempt.json').read_text())
    assert attempt['status'] == 'failed'
    final = Path(attempt['paths']['qualifiedProject'])
    prepared = Path(attempt['paths']['preparationProject'])
    assert final.exists() and prepared.exists()
    graph = runtime_qualification.installed_graph(final)
    if corruption == 'addition':
        assert [row['name'] for row in graph] == ['fixture-lazy', 'fixture-old', 'fixture-unexpected']
    else:
        assert graph == h.prepared_graphs[0]  # Payload guard, not graph mismatch.
        assert runtime_qualification.package_content(final) != runtime_qualification.package_content(prepared)
    assert not (stage / 'validated.json').exists()
    assert not (stage / 'runtime-installed.json').exists()
    assert not (stage / 'runtime.lock').exists()
    assert not list((h.home / 'updates/profiles').glob('*.json'))
    assert h.snapshot() == h.before
