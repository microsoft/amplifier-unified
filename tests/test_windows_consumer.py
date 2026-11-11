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
    assert "PYTHONPATH" not in result
    assert result["GIT_CONFIG_VALUE_0"] == "owner-helper"
    assert result["OPENAI_API_KEY"] == "synthetic-never-persisted"
    assert dict(consumer.os.environ) == before
    assert len(roots) == 12 and all(kwargs == {"owned_root": tmp_path, "create": False} for _, kwargs in roots)


def test_audit_reuses_native_archive_payload_reader_and_exact_graph(tmp_path, monkeypatch):
    from amplifier_web import app_component_graph as graph, app_release_artifacts, manual_app_handoff
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
    assert consumer.audit(tmp_path) == {"ok": True, "version": "1.0.0", "revision": "b" * 40,
                                      "packageCount": 1, "modelRequests": 0}
    assert [row[0] for row in calls] == ["artifact", "payload"]
    assert calls[1][1]["binding"] == {"version": "1.0.0", "revision": "b" * 40}
    monkeypatch.setattr(consumer.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='[{"name":"other","version":"1"}]'))
    with pytest.raises(ValueError, match="graph"):
        consumer.audit(tmp_path)


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