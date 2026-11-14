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


def test_shortcut_and_first_task_put_the_installations_shell_first(tmp_path, monkeypatch):
    from amplifier_web import windows_files
    @contextmanager
    def directory(path, **kwargs):
        yield
    monkeypatch.setattr(windows_files, "directory", directory)
    monkeypatch.setattr(consumer.sys, "platform", "win32")
    monkeypatch.setattr(consumer.sys, "_base_executable", str(tmp_path / "python/python.exe"), raising=False)
    monkeypatch.setenv("PATH", "system32")
    value = {"uv": str(tmp_path / "uv/uv.exe"), "git": str(tmp_path / "git/cmd/git.exe")}
    # An installation whose private Git is still MinGit keeps its existing order.
    assert consumer.environment(tmp_path, value)["PATH"].split(consumer.os.pathsep)[0] == str(tmp_path / "uv")
    (tmp_path / "consumer-install.json").write_text("{}")
    (tmp_path / "git/bin").mkdir(parents=True)
    (tmp_path / "git/bin/bash.exe").write_bytes(b"MZ")
    entries = consumer.environment(tmp_path, value)["PATH"].split(consumer.os.pathsep)
    # System32's bash.exe only starts WSL; the agent's shell tool takes the first bash.
    assert entries[:3] == [str(tmp_path / "git/bin"), str(tmp_path / "uv"), str(tmp_path / "git/cmd")]
    assert entries[-1] == "system32"


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


@pytest.mark.parametrize("change", [None, "null", "missing", "empty", "prefix-type",
                                  "foreign-prefix", "digest-type", "digest-format",
                                  "changed-installation"])
def test_consumer_delivery_capture_composes_real_current_binding_checks(tmp_path, monkeypatch, change):
    import copy
    from amplifier_web import update_readiness as readiness, application_generations as generations
    from amplifier_web import app_component_graph as graph, app_release_artifacts as artifacts, windows_files
    root = tmp_path
    binding = {"prefix": str(root / "tools/amplifier-unified"), "digest": "d" * 64}
    artifact = {"url": "https://github.com/microsoft/amplifier-unified/releases/download/v1.0.0/amplifier_unified-1.0.0-py3-none-any.whl",
                "path": str(root / "amplifier_unified-1.0.0-py3-none-any.whl"), "sha256": "a" * 64}
    app = {"name": "amplifier-unified", "version": "1.0.0",
           "url": "https://github.com/microsoft/amplifier-unified", "revision": "b" * 40,
           "delivery": {key: artifact[key] for key in ("url", "sha256")}}
    receipt = {"graph": [app], "artifact": artifact}
    qualification = {"app": app, "artifact": artifact, "extras": [], "dependencyDigest": graph.digest([])}
    proof = {"schema": 1, "receiptDigest": consumer._receipt_digest(receipt),
             "installation": copy.deepcopy(binding), "qualification": qualification}
    if change == "null": proof["installation"] = None
    elif change == "missing": del proof["installation"]
    elif change == "empty": proof["installation"] = {}
    elif change == "prefix-type": proof["installation"]["prefix"] = 5
    elif change == "foreign-prefix": proof["installation"]["prefix"] = str(root / "foreign")
    elif change == "digest-type": proof["installation"]["digest"] = None
    elif change == "digest-format": proof["installation"]["digest"] = "not-a-digest"
    monkeypatch.setattr(consumer, "installation", lambda selected: (root, receipt))
    monkeypatch.setattr(windows_files, "address", Path)
    @contextmanager
    def directory(path, **kwargs):
        yield SimpleNamespace(read_private=lambda name, limit: json.dumps(proof).encode())
    monkeypatch.setattr(windows_files, "directory", directory)
    monkeypatch.setattr(generations, "active", lambda home: None)
    observed_binding = {**binding, "digest": "e" * 64} if change == "changed-installation" else binding
    monkeypatch.setattr(generations, "bootstrap_identity", lambda prefix: observed_binding)
    # The actual normalized PEP610+source-stamp reader and _observed_artifact
    # execute, rather than replacing the very optional-binding check under test.
    raw = {"name": "amplifier-unified", "version": "1.0.0",
           "direct": {"url": Path(artifact["path"]).as_uri(), "archive_info": {}},
           "stamp": {"schema": 1, "repository": "microsoft/amplifier-unified",
                     "version": "1.0.0", "revision": "b" * 40}}
    monkeypatch.setattr(artifacts, "distribution_row", lambda dist: raw)
    dist = SimpleNamespace(locate_file=lambda name: Path(readiness.__file__).parent)
    monkeypatch.setattr(readiness.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(readiness.sys, "platform", "win32")
    manager = SimpleNamespace(home=root / "app", running_identity={"version": "1.0.0", "revision": "b" * 40},
                              service=SimpleNamespace(state={"updates": {}}))
    result = readiness.capture_artifact(manager)
    if change is None:
        assert result["installation"] == binding and result["app"] == app
    else:
        assert result is None
    assert manager.service.state == {"updates": {}}


def test_no_browser_launch_composes_existing_background_owner_without_reinstall(tmp_path, monkeypatch):
    from amplifier_web import windows_installation
    calls = []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    async def prepare(root, launcher, env, *, identity):
        assert callable(identity)
        calls.append((root, launcher, env))
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    assert asyncio.run(consumer.launch(tmp_path, open_browser=False))["health"]["ok"]
    assert calls == [(tmp_path, tmp_path / "tools/amplifier-unified/Scripts/amplifier-unified.exe", {})]


def address_launch(tmp_path, monkeypatch, *, answer, kinds=("in-use",)):
    """Launch with a saved address that another owner holds; record every effect."""
    from amplifier_web import windows_installation
    events = []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    pending = list(kinds)
    async def prepare(root, launcher, env, *, identity):
        events.append(("prepare", launcher))
        if pending:
            raise windows_installation.AddressUnavailable(pending.pop(0), 23456)
        return {"background": True, "created": False, "health": {"ok": True}}
    async def change(root, launcher, env):
        events.append(("change", launcher))
        return {"port": 31007, "previous": 23456}
    def notice(message, *, confirm=False):
        events.append(("notice", message, confirm))
        return answer
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    monkeypatch.setattr(windows_installation, "change_address", change)
    monkeypatch.setattr(windows_installation, "owner_notice", notice)
    return events, tmp_path / "tools/amplifier-unified/Scripts/amplifier-unified.exe"


@pytest.mark.parametrize("kind,phrase", [("in-use", "another program is using"),
                                        ("reserved", "Windows has reserved")])
def test_taken_saved_address_asks_before_moving_and_declining_changes_nothing(tmp_path, monkeypatch, kind, phrase):
    events, launcher = address_launch(tmp_path, monkeypatch, answer=False, kinds=(kind,))
    result = asyncio.run(consumer.launch(tmp_path))
    assert result == {"background": False, "address": {"available": False, "reason": kind}}
    assert [event[0] for event in events] == ["prepare", "notice"]  # No change, no second start.
    assert phrase in events[1][1] and events[1][2] is True


def test_confirmed_address_repair_selects_once_then_starts_the_same_installation(tmp_path, monkeypatch):
    from amplifier_web import windows_installation, deployment, browser_entry
    events, launcher = address_launch(tmp_path, monkeypatch, answer=True)
    # Browser entry after the repaired start is the ordinary path, covered below.
    monkeypatch.setattr(deployment, "load_server_config", lambda home: {})
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (["private-target"], "private-pin"))
    monkeypatch.setattr(windows_installation, "review_browser_trust", lambda *a, **kw: False)
    result = asyncio.run(consumer.launch(tmp_path))
    assert result["health"]["ok"] and result["browser"]["opened"] is False
    assert [event[0] for event in events] == ["prepare", "notice", "change", "prepare"]
    assert events[2][1] == events[3][1] == launcher


def test_second_refusal_after_repair_is_reported_not_repaired_again(tmp_path, monkeypatch):
    events, _ = address_launch(tmp_path, monkeypatch, answer=True, kinds=("in-use", "reserved"))
    with pytest.raises(RuntimeError, match="adopted or stopped"):
        asyncio.run(consumer.launch(tmp_path))
    assert [event[0] for event in events] == ["prepare", "notice", "change", "prepare"]


def test_noninteractive_launch_gets_the_refusal_without_a_dialog_or_change(tmp_path, monkeypatch):
    events, _ = address_launch(tmp_path, monkeypatch, answer=True)
    with pytest.raises(RuntimeError, match="adopted or stopped"):
        asyncio.run(consumer.launch(tmp_path, open_browser=False))
    assert [event[0] for event in events] == ["prepare"]


def test_pending_certificate_review_does_not_issue_browser_grant(tmp_path, monkeypatch):
    from amplifier_web import windows_installation, deployment, browser_entry, updates
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    async def prepared(*args, identity):
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepared)
    monkeypatch.setattr(deployment, "load_server_config", lambda home: {})
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (["private-target"], "private-pin"))
    reviews = []
    monkeypatch.setattr(windows_installation, "review_browser_trust",
                        lambda *a, fresh: reviews.append(a) or False)
    async def forbidden(*args, **kwargs):
        pytest.fail("No browser command or transient grant before certificate decision")
    monkeypatch.setattr(updates, "process", forbidden)
    result = asyncio.run(consumer.launch(tmp_path))
    assert result["browser"] == {"opened": False, "reason": "certificate-review-pending"}
    assert result["health"]["ok"] and reviews == [(tmp_path, ["private-target"], "private-pin")]


@pytest.mark.parametrize("first_setup", [True, False])
def test_first_setup_creates_the_identity_in_this_process_and_hands_windows_that_root(
        tmp_path, monkeypatch, first_setup):
    from amplifier_web import windows_installation, deployment, browser_entry, windows_tls
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    config = {"saved": "configuration"}
    monkeypatch.setattr(deployment, "load_server_config", lambda home: config)
    created = []
    fresh = b"root-created-in-this-process"
    monkeypatch.setattr(windows_tls, "create_identity", lambda home, value:
                        created.append((home, value)) or fresh)
    async def prepare(root, launcher, env, *, identity):
        if first_setup:
            identity()
            # A repeated call finds the material and creates nothing; the first
            # in-memory root stays the one that Windows is asked about.
            monkeypatch.setattr(windows_tls, "create_identity", lambda home, value: None)
            identity()
        return {"background": True, "created": first_setup, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (["private-target"], "private-pin"))
    reviews = []
    monkeypatch.setattr(windows_installation, "review_browser_trust",
                        lambda *a, fresh: reviews.append((a, fresh)) or False)
    asyncio.run(consumer.launch(tmp_path))
    assert created == ([(tmp_path / "app", config)] if first_setup else [])
    # The exact in-memory root for a new identity; a later start reads the private store.
    assert reviews == [((tmp_path, ["private-target"], "private-pin"), fresh if first_setup else None)]
    assert reviews[0][1] is (fresh if first_setup else None)


def test_os_entries_require_audited_first_install_proof(tmp_path, monkeypatch):
    from amplifier_web import windows_consumer_shell
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {"version": "1.0.0"}))
    monkeypatch.setattr(consumer, "delivery_proof", lambda home: None)
    calls = []
    monkeypatch.setattr(windows_consumer_shell, "install", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="audit"):
        consumer.integrate(tmp_path)
    assert not calls
    monkeypatch.setattr(consumer, "delivery_proof", lambda home: {"audited": True})
    consumer.integrate(tmp_path)
    assert calls == [(tmp_path, "1.0.0")]


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

@pytest.mark.parametrize("registered, expected", [
    (False, "could not finish its first start"), (True, "Amplifier could not start.")])
def test_a_shortcut_that_cannot_start_the_app_says_the_cause_and_the_next_step(
        tmp_path, monkeypatch, capsys, registered, expected):
    from amplifier_web import windows_installation, windows_service
    shown = []

    async def failing(root, *, open_browser=True):
        raise RuntimeError("Runtime dependency preparation did not complete; private detail")
    monkeypatch.setattr(consumer, "launch", failing)
    monkeypatch.setattr(windows_service, "managed", lambda home: registered)
    monkeypatch.setattr(windows_installation, "owner_notice", lambda message, **kw: shown.append(message))
    with pytest.raises(SystemExit):
        consumer.main(["--root", str(tmp_path)])
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and expected in result["message"] and "private detail" not in json.dumps(result)
    assert "Start menu: Amplifier Unified" in result["message"]
    if not registered:
        assert "connected to the internet" in result["message"] and "do not need to install it again" in result["message"]
    # Without a console (the shortcut uses pythonw) the same text is shown in a window.
    monkeypatch.setattr(consumer.sys, "stdout", None)
    with pytest.raises(SystemExit):
        consumer.main(["--root", str(tmp_path)])
    assert shown == [result["message"]]


def test_other_operations_keep_their_own_failure_text(tmp_path, monkeypatch, capsys):
    async def failing(root, operation):
        raise RuntimeError("stop failed")
    monkeypatch.setattr(consumer, "maintenance", failing)
    with pytest.raises(SystemExit):
        consumer.main(["--root", str(tmp_path), "--stop"])
    assert json.loads(capsys.readouterr().out) == {"ok": False, "errorType": "RuntimeError"}
