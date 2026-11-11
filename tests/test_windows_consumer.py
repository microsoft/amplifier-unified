"""Consumer entry composition; real download/shortcut/trust is separate evidence."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import windows_consumer as consumer


def test_process_local_roots_preserve_accounts_and_user_git_policy(tmp_path, monkeypatch):
    from amplifier_web import windows_files
    roots = []
    @contextmanager
    def directory(path, **kwargs):
        roots.append((path, kwargs))
        yield
    monkeypatch.setattr(windows_files, "directory", directory)
    monkeypatch.setenv("UV_TOOL_DIR", "other")
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "owner-helper")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-persisted")
    before = dict(consumer.os.environ)
    result = consumer.environment(tmp_path, {"uv": str(tmp_path / "uv/uv.exe"),
                                            "git": str(tmp_path / "git/cmd/git.exe")})
    assert result["UV_TOOL_DIR"] == str(tmp_path / "tools")
    assert result["AMPLIFIER_HOME"] == str(tmp_path / "shared")
    assert result["AMPLIFIER_SESSION_STATE_HOME"] == str(tmp_path / "ownership")
    assert result["AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"] == str(tmp_path / "capture")
    assert result["AMPLIFIER_SOURCE_STORE"] == str(tmp_path / "source-store")
    assert "PYTHONPATH" not in result
    assert result["GIT_CONFIG_VALUE_0"] == "owner-helper"
    assert result["OPENAI_API_KEY"] == "synthetic-never-persisted"
    assert dict(consumer.os.environ) == before
    assert len(roots) == 13 and all(kwargs == {"owned_root": tmp_path, "create": False} for _, kwargs in roots)


def test_audit_reuses_native_archive_payload_reader_and_exact_graph(tmp_path, monkeypatch):
    from amplifier_web import app_component_graph as graph, app_release_artifacts, manual_app_handoff
    from amplifier_web import application_generations, app_updates, deployment, windows_files
    artifact = {"url": "https://github.com/microsoft/amplifier-unified/releases/download/v1.0.0/amplifier_unified-1.0.0-py3-none-any.whl",
                "path": str(tmp_path / "amplifier_unified-1.0.0-py3-none-any.whl"), "sha256": "a" * 64}
    value = {"artifact": artifact, "version": "1.0.0", "revision": "b" * 40,
             "graph": [{"name": "amplifier-unified", "version": "1.0.0",
                       "url": "https://github.com/microsoft/amplifier-unified", "revision": "b" * 40,
                       "delivery": {key: artifact[key] for key in ("url", "sha256")}}]}
    raw = [{"name": "amplifier-unified", "version": "1.0.0",
            "direct": {"url": Path(artifact["path"]).as_uri(), "archive_info": {}},
            "stamp": {"schema": 1, "repository": "microsoft/amplifier-unified",
                      "version": "1.0.0", "revision": "b" * 40}}]
    calls = []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, value))
    monkeypatch.setattr(app_release_artifacts, "verify", lambda *a: calls.append(("artifact", a)) or artifact)
    monkeypatch.setattr(manual_app_handoff, "_payload", lambda proof: calls.append(("payload", proof)))
    monkeypatch.setattr(consumer.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps(raw)))
    binding = {"prefix": str(tmp_path / "tools/amplifier-unified"), "digest": "d" * 64}
    monkeypatch.setattr(application_generations, "bootstrap_identity", lambda prefix: binding)
    monkeypatch.setattr(app_updates, "installed_extras", lambda: [])
    saved = []
    monkeypatch.setattr(deployment, "write_private", lambda path, contents, **kw:
                        saved.append((path, json.loads(contents), kw)))
    assert consumer.audit(tmp_path) == {"ok": True, "version": "1.0.0", "revision": "b" * 40,
                                      "packageCount": 1, "modelRequests": 0}
    assert [row[0] for row in calls] == ["artifact", "payload"]
    assert calls[1][1]["binding"] == {"version": "1.0.0", "revision": "b" * 40}
    assert saved[0][0] == tmp_path / consumer.QUALIFICATION
    assert saved[0][2] == {"owned_root": tmp_path}
    proof = saved[0][1]
    assert proof["installation"] == binding
    assert proof["qualification"] == {"app": value["graph"][0], "artifact": artifact,
                                     "extras": [], "dependencyDigest": graph.digest([])}
    monkeypatch.setattr(windows_files, "address", Path)
    @contextmanager
    def directory(path, **kw):
        assert path == tmp_path and kw == {"owned_root": tmp_path}
        yield SimpleNamespace(read_private=lambda name, limit: json.dumps(proof).encode())
    monkeypatch.setattr(windows_files, "directory", directory)
    assert consumer.delivery_proof(tmp_path / "app") == proof
    for key, changed in (("receiptDigest", "0" * 64), ("schema", True)):
        old = proof[key]
        proof[key] = changed
        with pytest.raises(ValueError):
            consumer.delivery_proof(tmp_path / "app")
        proof[key] = old
    proof["qualification"]["dependencyDigest"] = "0" * 64
    with pytest.raises(ValueError, match="graph"):
        consumer.delivery_proof(tmp_path / "app")
    monkeypatch.setattr(consumer.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='[{"name":"other","version":"1"}]'))
    with pytest.raises(ValueError, match="graph"):
        consumer.audit(tmp_path)
    assert len(saved) == 1  # Failed graph cannot create new first-install proof.


def test_first_install_capture_uses_audited_proof_without_inventing_restart(tmp_path, monkeypatch):
    from amplifier_web import update_readiness as readiness, application_generations as generations
    monkeypatch.setattr(generations, "active", lambda home: None)
    monkeypatch.setattr(readiness.sys, "platform", "win32")
    manager = SimpleNamespace(home=tmp_path / "app", running_identity={},
                              service=SimpleNamespace(state={"updates": {}}))
    before = json.dumps(manager.service.state, sort_keys=True)
    proof = {"qualification": {"artifact": "test-only"}, "installation": {"digest": "a" * 64}}
    monkeypatch.setattr(consumer, "delivery_proof", lambda home: proof)
    calls = []
    monkeypatch.setattr(readiness, "_observed_artifact", lambda *args:
                        calls.append(args) or {"verified": True})
    assert readiness.capture_artifact(manager) == {"verified": True}
    assert calls == [(manager, proof["qualification"], proof["installation"])]
    assert json.dumps(manager.service.state, sort_keys=True) == before
    def missing(home):
        raise FileNotFoundError
    monkeypatch.setattr(consumer, "delivery_proof", missing)
    assert readiness.capture_artifact(manager) is None


def test_no_browser_launch_composes_existing_background_owner_without_reinstall(tmp_path, monkeypatch):
    from amplifier_web import windows_installation
    calls = []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    async def prepare(root, launcher, env):
        calls.append((root, launcher, env))
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    assert asyncio.run(consumer.launch(tmp_path, open_browser=False))["health"]["ok"]
    assert calls == [(tmp_path, tmp_path / "tools/amplifier-unified/Scripts/amplifier-unified.exe", {})]


def test_windowless_failure_is_bounded_owner_notice_not_stderr_or_retry(tmp_path, monkeypatch):
    from amplifier_web import windows_installation
    def invalid(root):
        raise ValueError("synthetic-private-details-must-not-be-shown")
    monkeypatch.setattr(consumer, "audit", invalid)
    monkeypatch.setattr(consumer.sys, "stdout", None)
    notices = []
    monkeypatch.setattr(windows_installation, "owner_notice", notices.append)
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(tmp_path), "--audit"])
    assert stopped.value.code == 1 and len(notices) == 1
    assert "synthetic-private" not in notices[0] and "No automatic retry" in notices[0]


@pytest.mark.parametrize("operation,confirmed", [("stop", True), ("uninstall", True), ("uninstall", False)])
def test_consumer_maintenance_uses_normal_owner_stop_and_preserves_files(tmp_path, monkeypatch, operation, confirmed):
    from amplifier_web import windows_installation, windows_service
    events = []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(windows_installation, "owner_notice", lambda text, confirm:
                        events.append(("confirm", confirm)) or confirmed)
    monkeypatch.setattr(windows_service, "uninstall", lambda home: events.append(("uninstall", home)))
    monkeypatch.setattr(windows_service, "command", lambda name, home: events.append((name, home)))
    retained = tmp_path / "user-data"
    retained.write_bytes(b"original")
    result = asyncio.run(consumer.maintenance(tmp_path, operation))
    assert events == [("confirm", True)] + ([(operation, tmp_path / "app")] if confirmed else [])
    assert retained.read_bytes() == b"original"
    assert result.get("userDataRetained", result.get("cancelled")) is True