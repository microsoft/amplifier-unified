"""Consumer entry composition; real download/shortcut/trust is separate evidence."""
import asyncio
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import windows_consumer as consumer


@pytest.fixture(autouse=True)
def one_at_a_time(monkeypatch):
    """Off Windows the installation lock has a stand-in; tests/test_windows_uninstall.py has its cases."""
    if os.name != "nt":
        from amplifier_web import windows_uninstall
        monkeypatch.setattr(windows_uninstall, "single", lambda root, **options: nullcontext())


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
    # This installation has the earlier store folder, so it keeps that one.
    assert result["AMPLIFIER_SOURCE_STORE"] == str(tmp_path / "source-store")
    assert "PYTHONPATH" not in result
    assert result["GIT_CONFIG_VALUE_0"] == "owner-helper"
    assert result["OPENAI_API_KEY"] == "synthetic-never-persisted"
    assert dict(consumer.os.environ) == before
    assert len(roots) == 13 and all(kwargs == {"owned_root": tmp_path, "create": False} for _, kwargs in roots)


def test_each_installation_resolves_the_source_store_folder_it_has(tmp_path, monkeypatch):
    """New setup makes "s"; an earlier installation keeps "source-store". Nothing is created or moved."""
    from amplifier_web import windows_files
    checked = []
    @contextmanager
    def directory(path, **kwargs):
        # The real owner opens the folder with its checks and never creates it here.
        assert kwargs == {"owned_root": path.parent, "create": False}
        checked.append(path.name)
        if not path.is_dir():
            raise FileNotFoundError(path)
        yield
    monkeypatch.setattr(windows_files, "directory", directory)
    tools = {"uv": "uv/uv.exe", "git": "git/cmd/git.exe"}
    folders = ("app", "shared", "ownership", "capture", "import", "tools", "tool-bin", "cache",
               "python", "python-bin", "temp")

    def installation(name, store):
        root = tmp_path / name
        for folder in (*folders, *store):
            (root / folder).mkdir(parents=True)
        return root

    assert (consumer.SOURCE_STORE, consumer.EARLIER_SOURCE_STORE) == ("s", "source-store")
    new = installation("new", ["s"])
    assert consumer.environment(new, tools)["AMPLIFIER_SOURCE_STORE"] == str(new / "s")
    assert checked[-2:] == ["source-store", "s"]
    earlier = installation("earlier", ["source-store"])
    assert consumer.environment(earlier, tools)["AMPLIFIER_SOURCE_STORE"] == str(earlier / "source-store")
    assert checked[-1] == "source-store"
    # Both folders: the one that an earlier launcher already uses stays in use.
    both = installation("both", ["s", "source-store"])
    assert consumer.source_store(both) == both / "source-store"
    # Neither folder: a damaged installation stops, as for every other private folder.
    damaged = installation("damaged", [])
    with pytest.raises(FileNotFoundError):
        consumer.environment(damaged, tools)
    # A store folder that exists and fails its checks is never replaced by the other name.
    refused = installation("refused", ["s", "source-store"])
    @contextmanager
    def unsafe(path, **kwargs):
        if path.name == "source-store":
            raise PermissionError("Windows private directory ACL differs")
        yield
    monkeypatch.setattr(windows_files, "directory", unsafe)
    with pytest.raises(PermissionError):
        consumer.source_store(refused)
    assert sorted(path.name for path in new.iterdir()) == sorted([*folders, "s"])
    assert sorted(path.name for path in earlier.iterdir()) == sorted([*folders, "source-store"])


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
    async def prepare(root, launcher, env, *, identity, progress):
        assert callable(identity)
        assert progress == steps.append
        progress("ready")
        calls.append((root, launcher, env))
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    steps = []
    assert asyncio.run(consumer.launch(tmp_path, open_browser=False, progress=steps.append))["health"]["ok"]
    assert steps == ["ready"]
    assert calls == [(tmp_path, tmp_path / "tools/amplifier-unified/Scripts/amplifier-unified.exe", {})]


def address_launch(tmp_path, monkeypatch, *, answer, kinds=("in-use",)):
    """Launch with a saved address that another owner holds; record every effect."""
    from amplifier_web import windows_installation
    events = []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    pending = list(kinds)
    async def prepare(root, launcher, env, *, identity, progress):
        if progress is not None:
            progress("address" if pending else "ready")
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
    steps = []
    result = asyncio.run(consumer.launch(tmp_path, progress=steps.append))
    assert result["health"]["ok"] and result["browser"]["opened"] is False
    assert steps == ["address", "ready"]
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
    async def prepared(*args, identity, progress):
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


def opening_launch(tmp_path, monkeypatch, outcome, *, listed=True):
    """Launch with trust established, up to the one `open` command; record every effect."""
    from amplifier_web import windows_installation, windows_consumer_shell, deployment, browser_entry, updates
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {"private": "environment"})
    monkeypatch.setattr(windows_consumer_shell, "listed", lambda root: listed)
    async def prepared(*args, identity, progress):
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepared)
    monkeypatch.setattr(deployment, "load_server_config", lambda home: {})
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (["private-target"], "private-pin"))
    events = []
    monkeypatch.setattr(windows_installation, "review_browser_trust",
                        lambda *a, fresh: events.append(("trust",)) or True)
    async def process(*args, env, timeout):
        events.append(("command", args, env, timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    monkeypatch.setattr(updates, "process", process)
    monkeypatch.setattr(windows_installation, "owner_notice",
                        lambda message, **kw: events.append(("notice", message, kw)) or True)
    return asyncio.run(consumer.launch(tmp_path)), events


def test_first_start_reports_the_browser_on_the_open_commands_own_confirmation(tmp_path, monkeypatch):
    from amplifier_web import browser_entry
    from amplifier_web.update_diagnostics import CommandOutput
    result, events = opening_launch(tmp_path, monkeypatch, CommandOutput(
        "Amplifier is not ready yet.\n" + browser_entry.OPENED))
    assert result["browser"] == {"opened": True} and result["health"]["ok"]
    launcher = tmp_path / "tools/amplifier-unified/Scripts/amplifier-unified.exe"
    # Trust first, then the one command; no message.
    assert events == [("trust",), ("command", (launcher, "--data-dir", tmp_path / "app", "--workspace",
                                               tmp_path / "workspace", "open"),
                                   {"private": "environment"}, 120)]
    # `open` waits for a restarting host and then needs about 10 seconds for
    # its last two requests. The launcher adds 50 seconds for the start of the
    # command (the generation check alone may take 30) and the browser call.
    assert events[1][3] == browser_entry.HOST_WAIT + 60 > browser_entry.HOST_WAIT + 10 + 30


def unconfirmed():
    from amplifier_web.update_diagnostics import CommandFailure, CommandOutput, CommandTimeout
    return [
        # Windows, after a generation took the command over: exit status 0 and
        # no confirmation. This was the silent first start of November 15.
        CommandOutput(""),
        CommandOutput("Browser opening disabled; no browser grant was issued."),
        CommandFailure({"returncode": 1}), CommandTimeout(120.0), OSError("launcher missing"),
    ]


@pytest.mark.parametrize("outcome", unconfirmed(), ids=lambda value: type(value).__name__)
def test_browser_that_did_not_open_gives_one_plain_message_and_no_second_attempt(tmp_path, monkeypatch, outcome):
    result, events = opening_launch(tmp_path, monkeypatch, outcome)
    assert result["browser"] == {"opened": False, "reason": "not-confirmed"} and result["health"]["ok"]
    assert [event[0] for event in events] == ["trust", "command", "notice"]  # One command, one message.
    message, options = events[2][1:]
    assert options == {}  # A statement, not a question.
    # Not "did not open" and not "is running": after its limit the launcher
    # knows neither. A command that a generation took over can still open the
    # browser, so the person looks for a tab first.
    assert message == ("Amplifier may not have opened in your browser.\n\n"
                       "Look for an Amplifier tab in your browser. If there is none, "
                       "open Amplifier from the Start menu: Amplifier Unified. "
                       "If this message appears again, restart this PC.")


def test_browser_message_names_setup_when_the_start_menu_entry_does_not_exist(tmp_path, monkeypatch):
    # After an uninstall the folder can run without its Start-menu entry. The
    # next step must exist: the setup file adds the entry again.
    from amplifier_web.update_diagnostics import CommandOutput
    result, events = opening_launch(tmp_path, monkeypatch, CommandOutput(""), listed=False)
    assert result["browser"] == {"opened": False, "reason": "not-confirmed"}
    assert [event[0] for event in events] == ["trust", "command", "notice"]
    assert events[2][1] == ("Amplifier may not have opened in your browser.\n\n"
                            "Look for an Amplifier tab in your browser. If there is none, "
                            "run the Amplifier setup file again. It keeps your chats and settings.")


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
    async def prepare(root, launcher, env, *, identity, progress):
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
    # An installation without Windows entries: only the normal owner stop runs.
    from amplifier_web import installation_health, windows_installation, windows_service
    events = []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(windows_installation, "owner_notice", lambda text, confirm:
                        events.append(("confirm", confirm)) or confirmed)
    async def working(home):
        return None
    monkeypatch.setattr(installation_health, "windows_working", working)
    monkeypatch.setattr(windows_service, "uninstall", lambda home, progress: events.append(("uninstall", home)))
    monkeypatch.setattr(windows_service, "command", lambda name, home: events.append((name, home)))
    retained = tmp_path / "user-data"
    retained.write_bytes(b"original")
    result = asyncio.run(consumer.maintenance(tmp_path, operation))
    assert events == [("confirm", True)] + ([(operation, tmp_path / "app")] if confirmed else [])
    assert retained.read_bytes() == b"original"
    assert result == ({"ok": True, "operation": operation, "userDataRetained": True,
                       "installationFilesRetained": True, **({"certificate": "none"} if operation == "uninstall" else {})}
                      if confirmed else {"ok": True, "cancelled": True})
    assert result.get("userDataRetained", result.get("cancelled")) is True

@pytest.mark.parametrize("operation,step,phrase", [
    ("stop", "stop-app", "could not stop its background app"),
    ("status", "status", "could not read its status"),
    ("uninstall", "task-read", "could not read its start-at-sign-in entry")])
def test_failed_maintenance_names_its_cause_and_next_step_not_the_old_generic_text(
        tmp_path, monkeypatch, operation, step, phrase):
    from amplifier_web import deployment, installation_health, windows_installation, windows_service
    notices, written = [], []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(consumer.sys, "stdout", None)
    monkeypatch.setattr(windows_installation, "owner_notice",
                        lambda text, confirm=False: notices.append((text, confirm)) or True)
    monkeypatch.setattr(deployment, "write_private", lambda path, raw, **kw: written.append((path, json.loads(raw))))
    def refuse(*args, **kwargs):
        if kwargs.get("progress"):
            kwargs["progress"]("task-read")
        raise windows_service.WindowsServiceError("synthetic-private-detail")
    async def status(home):
        refuse()
    async def working(home):
        return None
    monkeypatch.setattr(windows_service, "command", refuse)
    monkeypatch.setattr(windows_service, "uninstall", refuse)
    monkeypatch.setattr(installation_health, "status", status)
    monkeypatch.setattr(installation_health, "windows_working", working)
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(tmp_path), "--" + operation])
    assert stopped.value.code == 1
    shown = [text for text, confirm in notices if not confirm]
    assert len(shown) == 1 and phrase in shown[0] and "restart this pc" in shown[0].lower()
    assert shown[0].endswith(f"Reference: {step}, WindowsServiceError")
    assert "synthetic-private-detail" not in shown[0] and "evidence" not in shown[0]
    # Stop and uninstall keep one private note of the failed step; a status read keeps none.
    if operation == "status":
        assert not written
    else:
        (path, record), = written
        assert path == tmp_path / "app/config/services" / consumer.RECORD
        assert (record["operation"], record["step"]) == (operation, step)


@pytest.mark.parametrize("operation,step", [("uninstall", "preparation"), ("stop", "stop-app"), ("status", "status")])
def test_maintenance_on_a_folder_that_fails_the_installation_check_writes_nothing(
        tmp_path, monkeypatch, capsys, operation, step):
    from amplifier_web import deployment
    def foreign(root):
        raise ValueError("Use this consumer installation's original shortcut.")
    monkeypatch.setattr(consumer, "installation", foreign)
    def unused(*args, **kwargs):
        raise AssertionError("No record may be written into an unchecked folder.")
    monkeypatch.setattr(deployment, "write_private", unused)
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(tmp_path), "--" + operation])
    assert stopped.value.code == 1 and list(tmp_path.iterdir()) == []
    assert json.loads(capsys.readouterr().out) == {"ok": False, "step": step, "errorType": "ValueError"}
    if operation == "uninstall":
        assert "This uninstall stopped before it changed anything." in consumer.Unfinished(step, ValueError()).notice


@pytest.mark.parametrize("listed", [False, True])
@pytest.mark.parametrize("step", ["prepare", "ready", None])
def test_start_failure_message_matrix(tmp_path, monkeypatch, listed, step):
    from amplifier_web import windows_consumer_shell
    monkeypatch.setattr(windows_consumer_shell, "listed", lambda root: listed)
    next_step = ("open Amplifier from the Start menu: Amplifier Unified" if listed else
                 "run the Amplifier setup file again. It keeps your chats and settings")
    if step == "prepare":
        expected = ("Amplifier could not prepare the remaining parts of the app. It downloads them from the internet.\n\n"
                    "Check that this PC is connected to the internet. Then " + next_step + "." +
                    (" You do not need to install it again." if listed else ""))
    else:
        expected = "Amplifier could not start.\n\nRestart Windows. Then " + next_step + "."
    assert consumer.start_failure(tmp_path, step) == expected
    if not listed:
        assert "Start menu" not in expected


@pytest.mark.parametrize("listed", [False, True])
@pytest.mark.parametrize("steps", [[], ["address", "tls", "prepare"], ["address", "task", "ready"]])
def test_a_shortcut_that_cannot_start_the_app_says_the_cause_and_the_next_step(
        tmp_path, monkeypatch, capsys, listed, steps):
    from amplifier_web import windows_installation, windows_consumer_shell
    shown = []

    async def failing(root, *, open_browser=True, progress):
        for step in steps:
            progress(step)
        raise RuntimeError("Runtime dependency preparation did not complete; private detail")
    monkeypatch.setattr(consumer, "launch", failing)
    monkeypatch.setattr(windows_consumer_shell, "listed", lambda root: listed)
    monkeypatch.setattr(windows_installation, "owner_notice", lambda message, **kw: shown.append(message))
    with pytest.raises(SystemExit):
        consumer.main(["--root", str(tmp_path)])
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and "private detail" not in json.dumps(result)
    assert result["message"] == consumer.start_failure(tmp_path, steps[-1] if steps else None)
    if not listed:
        assert "Start menu" not in result["message"]
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


ENTRY = "AmplifierUnified-S-1-5-21-100-200-300-1001"


@pytest.mark.parametrize("listed", [True, False])
@pytest.mark.parametrize("cause", ["not-confirmed", "not-checked", "unreadable"])
def test_a_start_that_cannot_use_the_sign_in_entry_names_that_cause_and_not_the_internet(
        tmp_path, monkeypatch, capsys, cause, listed):
    # Measured after a computer rename: the entry existed, the earlier check
    # refused it, and the person was told to check the internet connection.
    from amplifier_web import windows_consumer_shell, windows_installation, windows_service
    shown = []
    problem = {"not-confirmed": windows_service.TaskNotConfirmed(ENTRY),
               "not-checked": windows_service.TaskNotConfirmed(ENTRY, retry=True),
               "unreadable": windows_service.WindowsServiceError("Task Scheduler refused; private detail")}[cause]

    async def failing(root, *, open_browser=True, progress):
        raise problem  # The start reads the task before it names any step.
    monkeypatch.setattr(consumer, "launch", failing)
    monkeypatch.setattr(windows_consumer_shell, "listed", lambda root: listed)
    monkeypatch.setattr(windows_installation, "owner_notice", lambda message, **kw: shown.append(message))
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(tmp_path)])
    assert stopped.value.code == 1
    result = json.loads(capsys.readouterr().out)
    text = result["message"]
    assert result["errorType"] == type(problem).__name__ and text.startswith("Amplifier could not start")
    for wrong in ("internet", "first start", "private detail", "install it again", "changes only"):
        assert wrong not in text
    if cause == "not-confirmed":
        # The cause without a claim that someone changed the entry, the exact
        # entry, what deleting it does, and the case in which not to delete it.
        assert ("could not confirm that its start-at-sign-in entry in Windows belongs to this installation. "
                "It uses only an entry that it can confirm.\n\n") in text
        assert text.count(ENTRY) == 2 and "Task Scheduler Library" in text and "press the Delete key" in text
        assert "no longer starts when you sign in, until it makes a new entry" in text
        assert "another Amplifier installation" in text
        assert "do not delete the entry: use that installation, or uninstall it first." in text
        assert ("4. Restart this PC.\n5. " + ("Open Amplifier from the Start menu: Amplifier Unified."
                                               if listed else "Run the Amplifier setup file again.")
                + " It makes a new entry. Your chats and settings stay.") in text
        assert ("Start menu: Amplifier Unified" in text) == listed
        assert "was changed" not in text and "tamper" not in text.lower()
    elif cause == "not-checked":
        # Windows gave no answer about the account. That can pass: nothing is deleted.
        assert "Windows could not check the account of Amplifier's start-at-sign-in entry" in text
        assert "2. Restart this PC.\n3. " + ("Open Amplifier from the Start menu: Amplifier Unified." if listed
                                             else "Run the Amplifier setup file again.") in text
        assert "delete" not in text.lower() and ENTRY not in text
    else:
        # Any other failure keeps the text for a start that named no step.
        assert text == consumer.start_failure(tmp_path) and "Restart Windows" in text
        assert "delete" not in text.lower() and ENTRY not in text
    # Without a console (the shortcut uses pythonw) the same text is shown in a window.
    monkeypatch.setattr(consumer.sys, "stdout", None)
    with pytest.raises(SystemExit):
        consumer.main(["--root", str(tmp_path)])
    assert shown == [text]


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("operation,step,opening", [
    ("uninstall", "task-read", "This uninstall stopped before it changed anything."),
    ("stop", "stop-app", "Amplifier did not stop its background app.")])
def test_stop_and_uninstall_name_an_entry_that_was_not_confirmed_and_a_next_step_that_fits(
        tmp_path, monkeypatch, operation, step, opening, retry):
    from amplifier_web import deployment, installation_health, windows_installation, windows_service
    notices, written = [], []
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(consumer.sys, "stdout", None)
    monkeypatch.setattr(windows_installation, "owner_notice",
                        lambda text, confirm=False: notices.append((text, confirm)) or True)
    monkeypatch.setattr(deployment, "write_private", lambda path, raw, **kw: written.append(json.loads(raw)))

    def refuse(*args, **kwargs):
        if kwargs.get("progress"):
            kwargs["progress"]("task-read")
        raise windows_service.TaskNotConfirmed(ENTRY, retry=retry)

    async def working(home):
        return None
    monkeypatch.setattr(windows_service, "command", refuse)
    monkeypatch.setattr(windows_service, "uninstall", refuse)
    monkeypatch.setattr(installation_health, "windows_working", working)
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(tmp_path), "--" + operation])
    assert stopped.value.code == 1
    (text,) = [text for text, confirm in notices if not confirm]
    assert text.startswith(opening + "\n\n") and text.endswith(f"Reference: {step}, TaskNotConfirmed")
    # The earlier texts for these steps name a wrong cause or a wrong next step.
    # No claim about changes either: a start can make an entry and then not confirm it.
    for wrong in ("You can keep using it", "could not read its start-at-sign-in entry",
                  "could not stop its background app", "Wait a few minutes", "changes only"):
        assert wrong not in text
    if operation == "stop":
        # One next step for both causes: the way to stop the app now.
        assert ("Windows could not check the account" if retry else
                "could not confirm that its start-at-sign-in entry") in text
        assert text.endswith("To stop the app now, sign out of Windows or restart this PC.\n\n"
                             "Reference: stop-app, TaskNotConfirmed")
        assert "delete" not in text.lower() and "1. " not in text
    elif retry:
        assert "Windows could not check the account" in text and "2. Restart this PC." in text
        assert "delete" not in text.lower() and "3. Uninstall Amplifier again." in text
    elif operation == "uninstall":
        assert "could not confirm that its start-at-sign-in entry" in text and text.count(ENTRY) == 2
        assert "Amplifier then no longer starts when you sign in.\n1. " in text
        assert "4. Restart this PC.\n5. Uninstall Amplifier again.\n\nReference" in text
        assert "makes a new entry" not in text
    (record,) = written
    assert (record["operation"], record["step"], record["errorType"]) == (operation, step, "TaskNotConfirmed")


@pytest.mark.parametrize("entry", [None, {"name": ENTRY, "retry": False}, {"name": ENTRY, "retry": True}])
def test_status_window_names_an_entry_that_was_not_confirmed(tmp_path, monkeypatch, entry):
    from amplifier_web import installation_health, windows_installation
    shown = []
    result = {"ok": False, "service": "unavailable", "message": "Health text.", "nextStep": "Health step.",
              **({"entry": entry} if entry else {})}

    async def status(home):
        return result
    monkeypatch.setattr(consumer, "installation", lambda root: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(consumer.sys, "stdout", None)
    monkeypatch.setattr(installation_health, "status", status)
    monkeypatch.setattr(windows_installation, "owner_notice", lambda text, **kw: shown.append(text) or True)
    assert asyncio.run(consumer.maintenance(tmp_path, "status")) == result
    (text,) = shown
    if entry is None:
        assert text == "Health text."
    else:
        assert text == consumer.entry_notice("status", ENTRY, entry["retry"])
        assert text.startswith("Amplifier could not read its status.\n\n")
        assert ("delete" not in text.lower()) == entry["retry"]

