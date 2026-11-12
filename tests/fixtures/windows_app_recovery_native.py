"""Controlled native app-generation restore, NOT public/update-host acceptance.

Only exact artifact acquisition is substituted. Preparation, installed probes,
graph/pointer ownership, private recovery, owned child startup and settlement are
the production implementations. The restart protocol value is an explicitly
synthetic fixture (no listening runtime receipt is published). Never use a user's
data directory or an already-running task.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid


async def run(root, wheel, expected_hash, revision):
    assert os.name == "nt" and sys.version_info[:2] == (3, 13)
    from amplifier_web import app_updates, app_release_artifacts as artifacts
    from amplifier_web import app_component_graph as components, application_generations as generations
    from amplifier_web import windows_app_recovery as recovery, windows_service, windows_task_runtime
    from amplifier_web.deployment import write_private
    from amplifier_web.windows_files import directory

    assert not root.exists(), "Inspect the original fixture outcome instead of replaying it"
    raw = wheel.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected_hash
    with directory(root / "app/config/services", owned_root=root, create=True):
        pass
    report = {"publicAcceptance": False, "automaticUpdateAcceptance": False,
              "qualification": "native-controlled-generation-restore-only",
              "source": revision, "wheelSha256": expected_hash, "status": "FAIL"}
    def save():
        write_private(root / "report.json", json.dumps(report, indent=2), owned_root=root)
    save()
    home = root / "app"
    before_bootstrap = generations.bootstrap_identity(sys.prefix)
    old_artifact = {"url": artifacts.SOURCE + "/releases/download/v0.77.0/" + wheel.name,
                    "sha256": expected_hash, "path": str(wheel)}
    artifacts.verify(old_artifact, "0.77.0", revision)
    old_graph = await components.read_graph(app_updates.process, sys.executable, artifact=old_artifact)
    old_identity = __import__("amplifier_web.update_readiness", fromlist=["running_identity"]).running_identity()
    assert old_identity["revision"] == revision
    state = {"application": {"status": "update", "latest": "v0.77.0", "revision": revision},
             "applicationIntent": {"id": "native-controlled-app-recovery", "scope": "app"}}
    events = []
    class Diagnostics:
        state = {"attemptId": uuid.uuid4().hex}
        def begin(self, *args, **kwargs):
            self.state["attemptId"] = uuid.uuid4().hex
        def record(self, phase, status, **kwargs):
            events.append({"phase": phase, "status": status})
        def clear_failure(self):
            pass
        def sync(self, phase, function, *args, **kwargs):
            value = function(*args, **kwargs)
            self.record(phase, "succeeded")
            return value
        async def run(self, phase, function, *args, **kwargs):
            self.record(phase, "started")
            try:
                value = await function(*args, **kwargs)
            except BaseException as error:
                report["failedPhase"] = phase
                report["failureType"] = type(error).__name__
                report["commandFacts"] = getattr(error, "diagnostic_facts", {})
                report["preparationEvents"] = events
                save()
                raise
            self.record(phase, "succeeded")
            return value
    manager = SimpleNamespace(home=home, directory=home / "updates",
        service=SimpleNamespace(state={"updates": state}), diagnostics=Diagnostics())
    async def publish(**kwargs):
        state.update(kwargs)
        write_private(root / "preparation-state.json", json.dumps(state), owned_root=root)
    manager.publish = publish
    @asynccontextmanager
    async def local_acquisition(client, url):
        base = artifacts.SOURCE + "/releases/download/v0.77.0/"
        assert url in {base + "SHA256SUMS", base + wheel.name}
        data = (expected_hash + "  " + wheel.name + "\n").encode() if url.endswith("SHA256SUMS") else raw
        class Response:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self, size):
                for start in range(0, len(data), size):
                    yield data[start:start + size]
        yield Response()
    original = artifacts._get
    try:
        artifacts._get = local_acquisition
        await app_updates.stage(manager)
    finally:
        artifacts._get = original
    pending = state["pendingApp"]
    folder = generations.directory(home, pending["revision"], pending["generation"])
    validated = json.loads((folder / "validated.json").read_text())
    assert validated["attemptId"] == pending["attemptId"]
    assert validated["hostGraph"] == components.installed_graph()
    promoted = generations.promote(home, validated, sys.prefix)
    generations.verify_candidate(promoted)
    spec = windows_service.TaskSpec(sid=windows_service._owner(),
        python=str(Path(sys.executable).with_name("pythonw.exe")),
        data_dir=str(home), workspace=str(root), state_home=str(root),
        tool_path=os.environ["PATH"], registration_id=uuid.uuid4().hex, supervised=True)
    with windows_task_runtime.process(os.getpid()) as actual:
        synthetic = {"schema": 1, "registration": spec.registration_id,
            "run": uuid.uuid4().hex, "phase": "listening",
            **{key: actual[key] for key in ("pid", "created", "image")}}
    restart = {"schema": 1, "run": synthetic, "target": {
        key: pending[key] for key in ("revision", "generation", "attemptId")}}
    report["restartProtocolSynthetic"] = True
    assert not (home / "config/services/task-runtime.json").exists()
    recovery.prepare(spec, restart, {"pointer": None, "bootstrap": before_bootstrap,
        "installation": before_bootstrap, "identity": old_identity,
        "graph": old_graph, "extras": [], "artifact": old_artifact})
    # Expected failure: this fixture does not install a logon registration.
    # The real target refuses before publishing a runtime identity. The real
    # guard must nevertheless prove complete exit/descendant/I/O settlement.
    assert windows_service._read()[1] is None, "Another real task must not be borrowed"
    observed, code, failed = await windows_service._launch_owned_host(
        spec, str(promoted["prefix"] / "Scripts/pythonw.exe"), restart)
    assert observed is None and code != 0 and failed
    report["expectedUnregisteredTargetExit"] = {"payloadReturncode": code,
        "runtimeReceiptAbsent": True, "ownedTreeAndIOSettled": True}
    recovery.restore(spec, restart)
    assert generations.active(home) is None
    assert recovery.read(spec)["status"] == "restored"
    assert generations.bootstrap_identity(sys.prefix) == before_bootstrap
    assert await components.read_graph(app_updates.process, sys.executable, artifact=old_artifact) == old_graph
    try:
        recovery.restore(spec, restart)
    except ValueError:
        report["duplicateRestoreRefused"] = True
    else:
        raise AssertionError("Completed recovery must not replay")
    report.update(status="PASS", preparationEvents=events, previousGraphUnchanged=True,
                  currentPointerRestored=True, noRuntimeHealthOrRestartAcknowledgementClaim=True,
                  noOriginalUserStateEffects=True, taskInstalled=False, serverStarted=False)
    save()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.root, args.wheel, args.sha256, args.revision))