"""Launcher refresh composition with real pointer code; native installs are CI evidence."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest

from amplifier_web import app_component_graph as components, application_generations as generations
from amplifier_web import windows_launcher_refresh as refresher
from amplifier_web.app_updates import PROBE
from amplifier_web.update_diagnostics import PROBE_PREFIX

SOURCE = "https://github.com/microsoft/amplifier-unified"
OLD, NEW = ("0.87.0", "a" * 40), ("0.94.0", "b" * 40)


def wheel(folder, version, revision):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"amplifier_unified-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("amplifier_web/release-source.json", json.dumps({
            "schema": 1, "repository": "microsoft/amplifier-unified", "revision": revision, "version": version}))
    return path


def install(prefix, version, revision, source, base):
    """An installed app environment as uv leaves it, with only the facts the refresh reads."""
    site = prefix / "lib/python3.13/site-packages"
    info = site / f"amplifier_unified-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: amplifier-unified\nVersion: {version}\n")
    (info / "direct_url.json").write_text(json.dumps({"url": Path(source).as_uri(), "archive_info": {}}))
    (site / "amplifier_web").mkdir()
    (site / "amplifier_web/release-source.json").write_text(json.dumps({
        "schema": 1, "repository": "microsoft/amplifier-unified", "revision": revision, "version": version}))
    (prefix / "bin").mkdir()
    (prefix / "bin/python").write_text("launcher " + version)
    (prefix / "bin/amplifier-unified").write_text("entry " + version)
    (prefix / "pyvenv.cfg").write_text(f"home = {base}\n")


class Service:
    """The task's facts and controls, without a Scheduled Task."""

    def __init__(self, root, *, up=True, ready=()):
        self.launcher = root / "tools/amplifier-unified"
        self.tool_path, self.launch_roots = str(root / "uv"), {"UV_TOOL_BIN_DIR": str(root / "tool-bin")}
        self.github_access, self.up, self.results, self.events = "anonymous", up, list(ready), []
        self.inside = self.handoff = False

    def running(self):
        return self.up

    def inside_host(self):
        return self.inside

    def handoff_in_flight(self):
        return self.handoff

    def stop(self):
        self.events.append("stop")
        self.up = False

    def start(self):
        self.events.append("start")
        self.up = True

    async def ready(self, wait):
        # Scripted answers first; after them the app answers when it runs.
        return self.results.pop(0) if self.results else self.up


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A setup-made installation whose app has updated itself past its launcher."""
    monkeypatch.setattr(refresher, "_WINDOWS", False)
    root, home = tmp_path / "Amplifier", tmp_path / "Amplifier/app"
    base = tmp_path / "Amplifier/python/cpython"
    for folder in (home, base, root / "uv", root / "tool-bin"):
        folder.mkdir(parents=True)
    (base / "python3").write_text("")
    uv = root / "uv/uv"
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    old_wheel = wheel(root, *OLD)
    launcher = root / "tools/amplifier-unified"
    install(launcher, *OLD, old_wheel, base)
    entry = root / "tool-bin/amplifier-unified"
    entry.write_text("entry " + OLD[0])
    (launcher / "uv-receipt.toml").write_text(
        '[tool]\nentrypoints = [{ name = "amplifier-unified", install-path = ' + json.dumps(str(entry))
        + ', from = "amplifier-unified" }]\n')
    generation = "c" * 32
    folder = generations.directory(home, NEW[1], generation)
    source = wheel(folder, *NEW)
    artifact = {"url": f"{SOURCE}/releases/download/v{NEW[0]}/{source.name}",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "path": str(source)}
    prefix = folder / "tools/amplifier-unified"
    install(prefix, *NEW, source, base)
    graph = [{"name": "amplifier-unified", "version": NEW[0], "url": SOURCE, "revision": NEW[1],
              "delivery": {key: artifact[key] for key in ("url", "sha256")}},
             {"name": "zeta", "version": "1.0"}]
    validated = {"revision": NEW[1], "version": NEW[0], "generation": generation, "attemptId": "d" * 32,
                 "extras": [], "componentGraph": graph, "componentDigest": components.digest(graph),
                 "artifact": artifact, "installation": generations.bootstrap_identity(prefix)}
    (folder / "validated.json").write_text(json.dumps(validated))
    (folder / "components.txt").write_text(components.requirements(graph))
    generations.promote(home, validated, launcher)
    (root / "consumer-install.json").write_text(json.dumps({
        "schema": 1, "kind": "windows-consumer-preview", "version": OLD[0], "revision": OLD[1],
        "artifact": {"url": "old", "sha256": "0" * 64, "path": str(old_wheel)}, "graph": [],
        "uv": str(uv), "git": str(root / "git/cmd/git.exe")}))
    (root / "consumer-qualified.json").write_text("proof of the old launcher")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    calls, fail = [], {}

    async def process(*args, env=None, timeout=None, **kwargs):
        args = [str(item) for item in args]
        if args[1:3] == ["tool", "install"]:
            calls.append("install")
            assert env["UV_TOOL_DIR"] == str(launcher.parent) and "--force" in args
            assert args[args.index("--python") + 1] == str(base / "python3")
            assert args[args.index("--overrides") + 1] == str(folder / "components.txt")
            # The launcher is installed from its own copy of the app's verified release file.
            assert args[-1] == (root / source.name).as_uri() and not launcher.exists()
            if fail.get("install"):
                (launcher / "half").mkdir(parents=True)
                raise RuntimeError("uv failed with private output")
            install(launcher, *NEW, root / source.name, base)
            added = root / "tool-bin/amplifier-unified-new"
            (launcher / "uv-receipt.toml").write_text(
                "[tool]\nentrypoints = [{ name = \"a\", install-path = " + json.dumps(str(entry))
                + " }, { name = \"b\", install-path = " + json.dumps(str(added)) + " }]\n")
            entry.write_text("entry " + NEW[0])
            added.write_text("a start program that only the new version has")
            return ""
        if "amplifier_web.windows_consumer" in args:
            calls.append("audit")
            assert args[0] == str(launcher / "bin/python") and args[-3:] == ["--root", str(root), "--audit"]
            if fail.get("audit"):
                raise fail["audit"]
            (root / "consumer-qualified.json").write_text("proof of the new launcher")
            return ""
        script = args[args.index("-c") + 1]
        if script == PROBE:
            calls.append("probe")
            return PROBE_PREFIX + json.dumps({"ok": True, "version": fail.get("version", NEW[0])})
        if script == components.GRAPH_PROBE:
            calls.append("graph")
            return json.dumps([
                {"name": "amplifier-unified", "version": NEW[0],
                 "direct": {"url": (root / source.name).as_uri(), "archive_info": {}},
                 "stamp": {"schema": 1, "repository": "microsoft/amplifier-unified",
                           "revision": NEW[1], "version": NEW[0]}},
                {"name": "zeta", "version": fail.get("zeta", "1.0"), "direct": None}])
        assert "generations.directory" in script and args[0] == str(launcher / "bin/python")
        calls.append("reader")
        return "compatible" if not fail.get("reader") else "unsupported"

    return SimpleNamespace(root=root, home=home, launcher=launcher, entry=entry, prefix=prefix, folder=folder,
                           process=process, calls=calls, fail=fail, validated=validated,
                           pointer=home / "updates/application-active.json",
                           old=generations.bootstrap_identity(launcher))


def run(installed, service, **options):
    return asyncio.run(refresher.refresh(service, installed.home, process=installed.process, **options))


def unchanged(installed, pointer):
    assert installed.pointer.read_bytes() == pointer
    assert generations.bootstrap_identity(installed.launcher) == installed.old
    assert generations.active(installed.home)["bootstrap"] == installed.old
    assert installed.entry.read_text() == "entry " + OLD[0]
    assert (installed.root / "consumer-qualified.json").read_text() == "proof of the old launcher"
    assert json.loads((installed.root / "consumer-install.json").read_text())["version"] == OLD[0]
    assert not list(installed.root.glob("launcher-*"))
    assert not (installed.root / f"amplifier_unified-{NEW[0]}-py3-none-any.whl").exists()
    assert not (installed.root / "tool-bin/amplifier-unified-new").exists()
    assert refresher.unfinished(installed.home) is None  # nothing is left to recover


def test_check_reports_the_plan_and_changes_nothing(installed):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root)
    result = run(installed, service, check=True)
    assert result["ok"] and not result["changed"] and result["action"] == "refresh"
    assert result["launcher"]["version"] == OLD[0] and result["app"]["version"] == NEW[0]
    assert "Nothing was changed now." in result["message"]
    assert service.events == [] and installed.calls == []
    unchanged(installed, pointer)


def test_refresh_replaces_the_launcher_at_its_path_and_keeps_everything_else(installed):
    service = Service(installed.root)
    result = run(installed, service)
    assert result["ok"] and result["changed"] and result["running"] and result["state"] == "refreshed"
    assert service.events == ["stop", "start"]
    assert installed.calls == ["install", "probe", "graph", "reader", "audit"]
    facts = refresher.launcher_facts(installed.launcher)
    assert (facts["version"], facts["revision"]) == NEW and facts["identity"] != installed.old
    assert facts["identity"]["prefix"] == installed.old["prefix"]  # the same folder: task and shortcuts hold
    target = generations.active(installed.home)
    assert target["bootstrap"] == facts["identity"] and target["generation"] == "c" * 32
    # The previous launcher is kept, unchanged, beside the installation.
    previous = Path(result["previousLauncher"])
    assert previous.parent == installed.root and previous.name.startswith("launcher-" + OLD[0] + "-")
    assert generations.bootstrap_identity(previous)["digest"] == installed.old["digest"]
    receipt = json.loads((installed.root / "consumer-install.json").read_text())
    assert (receipt["version"], receipt["revision"]) == NEW and receipt["graph"] == installed.validated["componentGraph"]
    assert receipt["artifact"]["path"] == str(installed.root / f"amplifier_unified-{NEW[0]}-py3-none-any.whl")
    assert receipt["uv"].endswith("uv") and receipt["kind"] == "windows-consumer-preview"
    assert (installed.root / "consumer-qualified.json").read_text() == "proof of the new launcher"
    record = json.loads((installed.home / "config/services" / refresher.RECORD).read_text())
    assert record["result"] == "refreshed" and record["to"]["version"] == NEW[0]
    assert refresher.unfinished(installed.home) is None
    # A second run finds nothing to do and does not stop the app.
    again = run(installed, service)
    assert again["ok"] and again["action"] == "none" and "already has the app's version" in again["message"]
    assert service.events == ["stop", "start"]


@pytest.mark.parametrize("failure, step, calls", [
    ({"install": True}, "installing the launcher", ["install"]),
    ({"version": "0.1.0"}, "checking the new launcher", ["install", "probe"]),
    ({"zeta": "2.0"}, "checking the new launcher", ["install", "probe", "graph"]),
    ({"reader": True}, "checking the new launcher", ["install", "probe", "graph", "reader"]),
])
def test_a_failed_refresh_puts_the_previous_launcher_back_and_restarts_the_app(installed, failure, step, calls):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root)
    installed.fail.update(failure)
    result = run(installed, service)
    assert not result["ok"] and result["rolledBack"] and result["running"] and result["state"] == "rolled-back"
    assert result["changed"] is False and result["ready"] is True
    assert result["step"] == step and installed.calls == calls
    assert result["message"] == (f"The launcher refresh did not finish. It stopped at this step: {step}. "
                                 "The previous launcher is back. Amplifier is running.")
    assert "private output" not in json.dumps(result)
    assert service.events == ["stop", "start"] and service.up
    unchanged(installed, pointer)
    record = json.loads((installed.home / "config/services" / refresher.RECORD).read_text())
    assert record["result"] == "rolled-back" and record["step"] == step


def test_an_app_that_is_not_ready_after_the_refresh_is_rolled_back_completely(installed):
    pointer = installed.pointer.read_bytes()
    # Not ready in the first wait and not in the one extra wait for a slow first start.
    service = Service(installed.root, ready=(False, False))
    result = run(installed, service)
    assert not result["ok"] and result["rolledBack"] and result["running"]
    assert result["step"] == "starting Amplifier"
    # Stop, start with the new launcher, stop again, start with the previous launcher.
    assert service.events == ["stop", "start", "stop", "start"]
    assert installed.calls == ["install", "probe", "graph", "reader", "audit"]
    unchanged(installed, pointer)


def test_an_app_that_was_stopped_stays_stopped_after_a_failed_refresh(installed):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root, up=False)
    installed.fail["install"] = True
    result = run(installed, service)
    assert not result["ok"] and result["rolledBack"] and result["running"] is False
    assert result["message"].endswith("Amplifier was not running before and is not running now.")
    assert service.events == []
    unchanged(installed, pointer)


def test_a_slow_first_start_gets_one_more_wait(installed):
    service = Service(installed.root, ready=(False,))
    result = run(installed, service)
    assert result["ok"] and result["changed"] and service.events == ["stop", "start"]


def test_an_undo_that_cannot_stop_the_app_says_what_is_true_and_can_be_finished_later(installed):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root, ready=(False, False))
    stop, stops = service.stop, []

    def failing():
        stops.append(1)
        if len(stops) == 2:
            raise RuntimeError("the task did not stop")
        stop()
    service.stop = failing
    result = run(installed, service)
    # The app still runs from the new launcher. Nothing claims otherwise.
    assert not result["ok"] and result["state"] == "rollback-incomplete" and result["rolledBack"] is False
    assert result["changed"] is True and result["running"] is True and result["ready"] is True
    assert "the app could not be stopped" in result["message"] and "Amplifier is running." in result["message"]
    assert "NOT running" not in result["message"] and "--recover" in result["message"]
    assert "launcher-" + OLD[0] in result["message"]
    assert refresher.unfinished(installed.home)["old"] == installed.old
    # The next run finishes the rollback from the saved record.
    service.stop = stop
    again = run(installed, service, recover=True)
    assert again["ok"] and again["action"] == "recover" and again["state"] == "rolled-back" and again["running"]
    assert again["message"].startswith("An earlier launcher refresh did not finish. The previous launcher is back.")
    unchanged(installed, pointer)


class Ended(BaseException):
    """The process was ended: nothing after this point runs."""


@pytest.mark.parametrize("point", ["during the installation", "after the rebind", "during the rollback",
                                   "after the rollback moved the launcher back"])
def test_a_run_that_was_ended_is_put_back_by_the_next_run(installed, monkeypatch, point):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root)
    undo, move = refresher._undo, refresher._move

    async def ended(*args, **kwargs):
        raise Ended()

    async def dying_move(source, target):
        if Path(target) == installed.launcher:  # the rollback's own move of the previous launcher
            if point == "after the rollback moved the launcher back":
                await move(source, target)
            raise Ended()
        await move(source, target)

    if point == "during the installation":
        installed.fail["install"] = True
        monkeypatch.setattr(refresher, "_undo", ended)
    elif point == "after the rebind":
        installed.fail["audit"] = Ended()
        monkeypatch.setattr(refresher, "_undo", ended)
    else:
        installed.fail["audit"] = RuntimeError("audit failed")
        monkeypatch.setattr(refresher, "_move", dying_move)
    with pytest.raises(Ended):
        run(installed, service)
    monkeypatch.setattr(refresher, "_undo", undo)
    monkeypatch.setattr(refresher, "_move", move)
    # The app is down, the launcher is missing, half installed or new, and the record says so.
    journal = refresher.unfinished(installed.home)
    assert journal["old"] == installed.old and journal["wasRunning"] is True and not service.up
    if point == "after the rollback moved the launcher back":
        # The launcher, the launch record and setup's receipts are back. Only the
        # start programs are not. That alone must keep the rollback unfinished,
        # even if the set-aside new launcher is already gone.
        assert not Path(journal["previous"]).exists() and installed.pointer.read_bytes() == pointer
        assert generations.bootstrap_identity(installed.launcher) == installed.old
        assert installed.entry.read_text() == "entry " + NEW[0]
        added = installed.root / "tool-bin/amplifier-unified-new"
        assert added.exists()
        import shutil
        shutil.rmtree(journal["unused"])
        assert refresher._pending(installed.home, journal) is True
        added.unlink()  # nothing names it any more; the start program itself is the open item
    else:
        assert Path(journal["previous"]).is_dir() or point == "during the rollback"
    if point == "after the rebind":
        assert generations.active(installed.home)["bootstrap"] != installed.old
    check = run(installed, service, check=True)
    assert check["action"] == "recover" and "Nothing was changed now." in check["message"] and not service.up
    result = run(installed, service)  # a plain run settles the earlier one first
    assert result["ok"] and result["action"] == "recover" and result["state"] == "rolled-back"
    assert result["running"] and service.up and service.events[-1] == "start"
    unchanged(installed, pointer)
    assert run(installed, service, recover=True)["message"] == (
        "No unfinished launcher refresh was found. Nothing was changed.")
    # And the refresh itself then works.
    installed.fail.clear()
    assert run(installed, service)["changed"]


def test_a_rebind_whose_write_landed_and_whose_check_failed_is_still_put_back(installed, monkeypatch):
    pointer, service, rebind = installed.pointer.read_bytes(), Service(installed.root), generations.rebind

    def landed(*args):
        rebind(*args)
        raise ValueError("the check after the write failed")
    monkeypatch.setattr(generations, "rebind", landed)
    result = run(installed, service)
    assert not result["ok"] and result["state"] == "rolled-back" and result["running"]
    assert result["step"] == "binding the app to the new launcher"
    unchanged(installed, pointer)


def test_a_launch_record_that_something_else_changed_is_never_overwritten(installed):
    service = Service(installed.root)
    installed.fail["audit"] = RuntimeError("audit failed")
    undo = refresher._undo

    async def later_update(*args, **kwargs):
        record = json.loads(installed.pointer.read_text())
        record["version"] = "9.9.9"  # a promotion by the app, not this refresh's change
        installed.pointer.write_text(json.dumps(record))
        return await undo(*args, **kwargs)
    refresher._undo = later_update
    try:
        result = run(installed, service)
    finally:
        refresher._undo = undo
    assert not result["ok"] and result["state"] == "rollback-incomplete" and result["changed"] is True
    assert "launch record was changed by something else" in result["message"]
    # No retry can help here, so the message names the way out instead of --recover.
    assert str(installed.home / "config/services" / refresher.JOURNAL) in result["message"]
    assert "--recover" not in result["message"]
    assert json.loads(installed.pointer.read_text())["version"] == "9.9.9"
    assert refresher.unfinished(installed.home) is not None  # the saved record stays
    # The new launcher stays with the record that names it; nothing was half restored.
    assert refresher.launcher_facts(installed.launcher)["revision"] == NEW[1]
    assert result["running"] is False and "Amplifier is NOT running." in result["message"]
    with pytest.raises(ValueError, match="changed after the launcher refresh"):
        generations.restore_binding(installed.home, b'{"bootstrap": {"prefix": "x"}}')


def test_an_app_update_that_finished_while_the_app_stopped_ends_cleanly(installed):
    service = Service(installed.root)
    stop = service.stop

    def stop_after_an_update():
        # The app promoted a version under the same launcher just before it stopped.
        generations.promote(installed.home, installed.validated, installed.launcher)
        stop()
    service.stop = stop_after_an_update
    result = run(installed, service)
    assert not result["ok"] and result["state"] == "unchanged" and result["changed"] is False
    assert result["message"] == ("The installation changed while Amplifier stopped. Nothing was changed. "
                                 "Amplifier is running.")
    # The update's own launch record stays, nothing waits for a recovery, and a new run can decide again.
    assert "previous" in json.loads(installed.pointer.read_text())
    assert refresher.unfinished(installed.home) is None and service.up and installed.calls == []
    assert generations.bootstrap_identity(installed.launcher) == installed.old
    assert run(installed, service, check=True)["action"] == "refresh"


def test_a_finished_refresh_is_never_rolled_back_because_its_record_stayed(installed, monkeypatch):
    service, retire = Service(installed.root), refresher._retire
    monkeypatch.setattr(refresher, "_retire", lambda home: None)  # the record cannot be removed
    result = run(installed, service)
    assert result["ok"] and result["changed"]
    record = installed.home / "config/services" / refresher.JOURNAL
    assert json.loads(record.read_text())["done"] is True and refresher.unfinished(installed.home) is None
    assert run(installed, service, check=True)["action"] == "none"
    monkeypatch.setattr(refresher, "_retire", retire)
    again = run(installed, service)
    assert again["ok"] and again["action"] == "none" and service.events == ["stop", "start"]
    assert not record.exists() and refresher.launcher_facts(installed.launcher)["revision"] == NEW[1]


def test_the_end_state_text_matches_what_was_measured(installed):
    service, answers = Service(installed.root), []
    installed.fail["install"] = True

    async def never(wait):
        answers.append(wait)
        return False
    service.ready = never
    result = run(installed, service)
    # The previous launcher is back and the app runs, but it does not answer.
    assert result["state"] == "rolled-back" and result["running"] is True and result["ready"] is False
    assert result["message"].endswith("The previous launcher is back. Amplifier is running, but it does not "
                                      "answer yet. Wait a minute, then open Amplifier.")
    assert "Start menu" not in result["message"]


def test_a_launch_record_that_cannot_be_written_is_not_blamed_on_something_else(installed, monkeypatch):
    service = Service(installed.root)
    installed.fail["audit"] = RuntimeError("audit failed")

    def denied(home, saved):
        raise PermissionError("the file is in use")
    monkeypatch.setattr(generations, "restore_binding", denied)
    result = run(installed, service)
    assert result["state"] == "rollback-incomplete" and result["changed"] is True
    assert "the app's launch record could not be put back" in result["message"]
    assert "changed by something else" not in result["message"]
    assert refresher.unfinished(installed.home) is not None


def test_the_saved_record_goes_only_when_the_disk_shows_the_previous_state(installed, monkeypatch):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root)
    installed.fail["audit"] = RuntimeError("audit failed")
    monkeypatch.setattr(refresher, "_restore_entries", lambda *args: None)  # a step that silently does nothing
    result = run(installed, service)
    assert result["state"] == "rollback-incomplete" and "could not be put back" in result["message"]
    assert refresher.unfinished(installed.home) is not None
    monkeypatch.undo()
    monkeypatch.setattr(refresher, "_WINDOWS", False)
    monkeypatch.setattr(sys, "prefix", str(installed.prefix))
    again = run(installed, service, recover=True)
    assert again["ok"] and again["state"] == "rolled-back"
    unchanged(installed, pointer)


def test_only_one_refresh_runs_for_an_installation(installed):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root)
    with refresher._lock(installed.home):
        with pytest.raises(refresher.Refused, match="Another launcher refresh is running"):
            run(installed, service)
    assert service.events == [] and installed.calls == []
    unchanged(installed, pointer)


def test_a_stop_that_is_not_confirmed_changes_nothing_and_reports_an_app_that_answers(installed):
    pointer, service, asked = installed.pointer.read_bytes(), Service(installed.root), []

    def stop():
        raise RuntimeError("Task still has a running instance; no force-stop attempted.")

    async def ready(wait):
        asked.append(wait)
        return True
    service.stop, service.ready = stop, ready
    result = run(installed, service)
    assert not result["ok"] and result["state"] == "unchanged" and result["running"] is True
    assert result["changed"] is False and result["step"] == "stopping Amplifier" and installed.calls == []
    assert result["message"].endswith("Nothing was changed. Amplifier is running.")
    assert service.events == [] and asked  # "running" is reported only for an app that answers
    unchanged(installed, pointer)


def refused(installed, service):
    pointer = installed.pointer.read_bytes()
    with pytest.raises(refresher.Refused) as reason:
        run(installed, service)
    assert service.events == [] and installed.calls == []
    unchanged(installed, pointer)
    return str(reason.value)


def test_refusals_change_nothing_and_say_why(installed, monkeypatch):
    service = Service(installed.root)
    service.inside = True
    assert "from inside the running app" in refused(installed, service)
    service.inside = False
    monkeypatch.setattr(sys, "prefix", str(installed.launcher))
    assert "the launcher's own Python" in refused(installed, service)
    monkeypatch.setattr(sys, "prefix", str(installed.prefix))
    service.handoff = True
    assert "An app update is still finishing" in refused(installed, service)
    service.handoff = False
    monkeypatch.setattr(sys, "prefix", str(installed.root / "another"))
    assert str(installed.prefix / "bin/python") in refused(installed, service)
    monkeypatch.setattr(sys, "prefix", str(installed.prefix))
    (installed.folder / "components.txt").write_text("changed==1.0\n")
    assert "did not pass its own checks" in refused(installed, service)
    (installed.folder / "components.txt").write_text(components.requirements(installed.validated["componentGraph"]))
    (installed.root / "uv/uv").unlink()
    monkeypatch.setenv("PATH", str(installed.root / "empty"))
    assert "setup helper (uv)" in refused(installed, service)


def test_a_pointer_of_another_launcher_is_never_rebound(installed):
    other = installed.root / "other/tools/amplifier-unified"
    install(other, "0.90.0", "e" * 40, installed.root / "x.whl", installed.root / "python/cpython")
    service = Service(installed.root)
    service.launcher = other
    assert "belongs to another launcher" in refused(installed, service)
    with pytest.raises(ValueError, match="does not belong to the replaced launcher"):
        generations.rebind(installed.home, generations.bootstrap_identity(other), installed.launcher)
    with pytest.raises(ValueError, match="does not belong to the replaced launcher"):
        generations.rebind(installed.home, installed.old, other)  # a different folder
    with pytest.raises(ValueError, match="does not belong to the replaced launcher"):
        generations.rebind(installed.home, installed.old, installed.launcher)  # nothing was replaced


@pytest.mark.parametrize("held", ["the release file copy", "setup's receipt", "the receipt during a rollback"])
def test_a_folder_that_a_closing_program_still_holds_is_waited_for(installed, monkeypatch, held):
    # Measured in the clean room: while the app's workers are alive, a rename
    # into the installation folder fails with "used by another process". They
    # end a moment after the task reports that it stopped.
    waits, service, pointer = [], Service(installed.root), installed.pointer.read_bytes()

    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(refresher.asyncio, "sleep", sleep)
    name, original, busy = ("_copy" if held == "the release file copy" else "_write"), None, {"left": 3}
    original = getattr(refresher, name)

    def holding(*args):
        receipt = name == "_write" and Path(args[1]).name == "consumer-install.json"
        if (name == "_copy" or receipt) and busy["left"] and (
                held != "the receipt during a rollback" or busy.get("armed")):
            busy["left"] -= 1
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return original(*args)
    monkeypatch.setattr(refresher, name, holding)
    if held == "the receipt during a rollback":
        undo = refresher._undo

        async def armed(*args, **kwargs):
            busy["armed"] = True
            return await undo(*args, **kwargs)
        monkeypatch.setattr(refresher, "_undo", armed)
        service.results = [False, False]  # the app does not become ready: roll back
        result = run(installed, service)
        assert result["state"] == "rolled-back" and result["running"] and busy["left"] == 0
        unchanged(installed, pointer)
    else:
        result = run(installed, service)
        assert result["ok"] and result["changed"] and busy["left"] == 0
    assert waits.count(0.5) == 3


def test_a_folder_that_stays_held_stops_the_refresh_and_is_rolled_back(installed, monkeypatch):
    pointer, service, waits = installed.pointer.read_bytes(), Service(installed.root), []

    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(refresher.asyncio, "sleep", sleep)

    def held(*args):
        raise PermissionError(13, "used by another process")
    monkeypatch.setattr(refresher, "_copy", held)
    result = run(installed, service)
    assert not result["ok"] and result["step"] == "copying the release file" and result["running"]
    assert result["state"] == "unchanged" and len(waits) == refresher.SETTLE_SECONDS * 2 - 1
    unchanged(installed, pointer)


def test_restore_binding_puts_back_only_the_launcher_binding(installed):
    saved = installed.pointer.read_bytes()
    assert generations.restore_binding(installed.home, saved) is False  # nothing differs
    # A later app update under the same launcher: the binding is already the saved one.
    generations.promote(installed.home, installed.validated, installed.launcher)
    later = installed.pointer.read_bytes()
    assert later != saved and generations.restore_binding(installed.home, saved) is False
    assert installed.pointer.read_bytes() == later  # the update's record is not overwritten
    # This refresh's own change: only the binding differs, at the same folder.
    record = json.loads(saved)
    record["bootstrap"] = {"prefix": installed.old["prefix"], "digest": "f" * 64}
    installed.pointer.write_text(json.dumps(record))
    assert generations.restore_binding(installed.home, saved) is True
    assert installed.pointer.read_bytes() == saved
    # Another folder, or another change beside the binding, is never overwritten.
    for change in ({"bootstrap": {"prefix": "/another/folder", "digest": "f" * 64}},
                   {"bootstrap": record["bootstrap"], "version": "9.9.9"}):
        installed.pointer.write_text(json.dumps({**json.loads(saved), **change}))
        with pytest.raises(generations.BindingChanged):
            generations.restore_binding(installed.home, saved)
        assert json.loads(installed.pointer.read_text())["bootstrap"] == change["bootstrap"]


def test_nothing_to_refresh_without_an_app_update_or_with_a_newer_launcher(installed):
    service = Service(installed.root)
    installed.pointer.unlink()
    result = run(installed, service)
    assert result["ok"] and result["action"] == "none" and "runs from the launcher itself" in result["message"]
    assert service.events == []


def test_task_service_reads_the_owned_task_and_uses_the_normal_stop(tmp_path, monkeypatch):
    from amplifier_web import windows_app_recovery, windows_service, windows_task_runtime
    spec = SimpleNamespace(python=str(tmp_path / "tools/amplifier-unified/Scripts/pythonw.exe"),
                           tool_path="tools", launch_roots={"UV_TOOL_BIN_DIR": "bin"}, github_access="auto",
                           registration_id="r" * 32)
    task, commands = {"instances": 1}, []
    monkeypatch.setattr(windows_service, "_read", lambda home: (task, spec))
    monkeypatch.setattr(windows_service, "command", lambda name, home: commands.append((name, home)))
    service = refresher.TaskService(tmp_path / "app")
    assert service.launcher == tmp_path / "tools/amplifier-unified" and service.launch_roots == {"UV_TOOL_BIN_DIR": "bin"}
    assert service.running()
    task["instances"] = 0
    assert not service.running()
    service.stop()
    service.start()
    assert commands == [("stop", tmp_path / "app"), ("start", tmp_path / "app")]
    monkeypatch.setattr(windows_app_recovery, "read", lambda value: {"status": "prepared"})
    assert service.handoff_in_flight()
    monkeypatch.setattr(windows_app_recovery, "read", lambda value: {"status": "acknowledged"})
    assert not service.handoff_in_flight()
    # A process that the app started is a descendant of its host.
    import os
    parents = {os.getpid(): 500, 500: 77, 77: 4}
    monkeypatch.setattr(windows_task_runtime, "read", lambda value: {"pid": 77})
    monkeypatch.setattr(windows_task_runtime, "parent_pid", lambda pid: parents[pid])
    assert service.inside_host()
    monkeypatch.setattr(windows_task_runtime, "read", lambda value: {"pid": 9999})
    parents[4] = 0
    assert not service.inside_host()
    monkeypatch.setattr(windows_service, "_read", lambda home: (task, None))
    with pytest.raises(refresher.Refused, match="No Amplifier start-at-sign-in task"):
        refresher.TaskService(tmp_path / "app")


def test_help_says_where_the_command_must_run():
    assert "from a process outside the\napp" in refresher.__doc__
    assert "refuses to run from inside it" in refresher.__doc__
    assert "-I -m amplifier_web.windows_launcher_refresh --data-dir <data> [--check | --recover]" in refresher.__doc__
    assert "run it again" in refresher.__doc__
