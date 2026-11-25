"""Launcher refresh composition with real pointer code; native installs are CI evidence."""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
import hashlib
import json
import os
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
        self.registration = "a" * 32
        self.current = {"schema": 1, "registration": self.registration, "run": "b" * 32,
                        "pid": 77, "created": "123", "image": r"C:\Python\pythonw.exe"}
        self.exact_stops = []

    def running(self):
        return self.up

    def inside_host(self):
        return self.inside

    def handoff_in_flight(self):
        return self.handoff

    def stop(self):
        self.events.append("stop")
        self.up = False

    def run(self):
        return dict(self.current) if self.up else None

    def stop_exact(self, run):
        assert run == self.run()
        self.exact_stops.append(run)
        self.stop()

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
    # Windows tries the current folder first and then gives a path that is not
    # absolute. Such a program is never used: only the task's own path counts.
    with monkeypatch.context() as lookup:
        for current_folder in (os.path.join(os.curdir, "uv.EXE"), "uv.exe"):
            lookup.setattr(refresher.shutil, "which", lambda *args, **kwargs: current_folder)
            assert "setup helper (uv)" in refused(installed, service)
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
    # Measured in the clean room (worker Foundation before e37a946b): while the
    # app's workers are alive, a rename
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


def request_for(installed, service, *, hold=True):
    request = {"schema": 1, "id": "e" * 32, "registration": service.registration,
               "launcher": installed.old, "target": {
                   key: installed.validated[key] for key in ("generation", "revision", "version")},
               "status": "open", "result": None, "hold": service.run() if hold else None,
               "repair": False, "at": 1}
    refresher.write_json(installed.home, refresher.REQUEST, request)
    return request


def auto(installed, service, request, **options):
    return asyncio.run(refresher.automatic(
        installed.home, request["id"], service=service, process=installed.process,
        **{"hold_wait": 0, "recover_wait": 0, "stop_wait": 0, "poll": 0.001, **options}))


def saved(installed, name=refresher.RECORD):
    return refresher.read_json(installed.home, name)


def interrupted(installed, service, request, monkeypatch, *, point="install"):
    undo = refresher._undo

    async def ended(*args, **kwargs):
        raise Ended()

    monkeypatch.setattr(refresher, "_undo", ended)
    if point == "install":
        installed.fail["install"] = True
    elif point == "audit":
        installed.fail["audit"] = Ended()
    with pytest.raises(Ended):
        auto(installed, service, request)
    installed.fail.clear()
    monkeypatch.setattr(refresher, "_undo", undo)
    return refresher.unfinished(installed.home)


def test_automatic_first_run_refreshes_and_merges_its_record(installed):
    service = Service(installed.root)
    request = request_for(installed, service)
    result = auto(installed, service, request)
    assert result == saved(installed)
    assert (result["state"], result["result"], result["request"]) == ("settled", "refreshed", request["id"])
    assert result["stoppedApp"] and result["attempts"] == 0
    assert result["from"] == dict(zip(("version", "revision"), OLD))
    assert result["to"] == dict(zip(("version", "revision"), NEW))
    assert service.exact_stops == [request["hold"]] and service.events == ["stop", "start"]
    assert saved(installed, refresher.KEPT)["paths"] == [result["previous"]]
    assert Path(result["previous"]).is_dir()  # The first automatic run deletes nothing.
    assert saved(installed, refresher.REQUEST) == request  # Only the host writes requests.
    calls = installed.calls[:]
    assert auto(installed, service, request) == result  # Case 3 has no second effect.
    assert installed.calls == calls and service.events == ["stop", "start"]


@pytest.mark.parametrize("failure, expected", [
    ({"install": True}, "rolled-back"), ({"version": "0.1.0"}, "rolled-back"),
])
def test_automatic_failure_rolls_back_with_step_and_error(installed, failure, expected):
    service = Service(installed.root)
    request = request_for(installed, service)
    installed.fail.update(failure)
    result = auto(installed, service, request)
    assert (result["state"], result["result"]) == ("settled", expected)
    assert result["step"] in {"installing the launcher", "checking the new launcher"}
    assert result["errorType"] in {"RuntimeError", "ValueError"} and result["stoppedApp"]
    assert "private output" not in json.dumps(result)
    assert generations.bootstrap_identity(installed.launcher) == installed.old
    assert service.up and service.exact_stops == [request["hold"]]


@pytest.mark.parametrize("condition", ["refused", "none", "launcher", "generation", "revision"])
def test_automatic_plan_must_still_match_the_request(installed, monkeypatch, condition):
    service = Service(installed.root)
    request = request_for(installed, service)
    original = refresher.plan

    def changed(*args):
        if condition == "refused":
            raise refresher.Refused("Not safe.")
        selected = original(*args)
        if condition == "none":
            selected["action"] = "none"
        elif condition == "launcher":
            selected["old"] = {**selected["old"], "identity": {"digest": "changed"}}
        else:
            selected["target"] = {**selected["target"], condition: "changed"}
        return selected

    monkeypatch.setattr(refresher, "plan", changed)
    assert auto(installed, service, request)["result"] == "refused"
    assert service.events == [] and installed.calls == []
    assert refresher.unfinished(installed.home) is None


def test_automatic_rechecks_the_plan_before_writing_the_journal(installed, monkeypatch):
    service = Service(installed.root)
    request, original, calls = request_for(installed, service), refresher.plan, []

    def changed(*args):
        selected = original(*args)
        calls.append(1)
        if len(calls) == 2:
            selected["target"] = {**selected["target"], "generation": "changed"}
        return selected

    monkeypatch.setattr(refresher, "plan", changed)
    assert auto(installed, service, request)["result"] == "refused"
    assert len(calls) == 2 and service.events == [] and refresher.unfinished(installed.home) is None


@pytest.mark.parametrize("up, has_run", [(False, True), (True, False)])
def test_automatic_no_running_host_withdraws_without_starting(installed, up, has_run):
    service = Service(installed.root, up=up)
    request = request_for(installed, service)
    if not has_run:
        service.run = lambda: None
    assert auto(installed, service, request)["result"] == "withdrawn"
    assert service.events == [] and installed.calls == []


@pytest.mark.parametrize("change", ["closed", "missing", "id", "registration", "run", "timeout"])
def test_automatic_preparation_needs_the_same_open_request_and_run(installed, monkeypatch, change):
    service = Service(installed.root)
    request = request_for(installed, service, hold=False)
    record = refresher._automatic_record

    def after_ready(*args, **kwargs):
        result = record(*args, **kwargs)
        if result["state"] == "ready":
            if change == "missing":
                refresher.remove_json(installed.home, refresher.REQUEST)
            elif change == "run":
                service.current["run"] = "f" * 32
            elif change != "timeout":
                value = {**request, {"closed": "status"}.get(change, change): "closed"}
                refresher.write_json(installed.home, refresher.REQUEST, value)
        return result

    monkeypatch.setattr(refresher, "_automatic_record", after_ready)
    result = auto(installed, service, request)
    assert result["result"] == ("not-started" if change == "timeout" else "withdrawn")
    assert service.events == [] and installed.calls == [] and refresher.unfinished(installed.home) is None


def test_automatic_hold_arrives_during_preparation(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service, hold=False)
    waits = []

    async def go(seconds):
        waits.append(seconds)
        refresher.write_json(installed.home, refresher.REQUEST, {**request, "hold": service.run()})

    monkeypatch.setattr(refresher.asyncio, "sleep", go)
    assert auto(installed, service, request, hold_wait=1)["result"] == "refreshed"
    assert waits == [0.001]


def test_automatic_handover_at_the_last_check_withdraws(installed, monkeypatch):
    service = Service(installed.root)
    request, record = request_for(installed, service), refresher._automatic_record

    def handover(*args, **kwargs):
        result = record(*args, **kwargs)
        service.handoff = True
        return result

    monkeypatch.setattr(refresher, "_automatic_record", handover)
    assert auto(installed, service, request)["result"] == "withdrawn"
    assert service.events == [] and refresher.unfinished(installed.home) is None


@pytest.mark.parametrize("case", ["down", "none", "wrong", "no run", "closed", "void", "handover", "arrives"])
def test_rule_s_requires_an_exact_hold_for_every_stop(installed, monkeypatch, case):
    service = Service(installed.root, up=case != "down")
    request = request_for(installed, service, hold=case not in {"none", "arrives"})
    if case == "wrong":
        refresher.write_json(installed.home, refresher.REQUEST, {**request, "hold": {**service.current, "pid": 99}})
    if case == "no run":
        service.run = lambda: None
    if case == "closed":
        refresher.write_json(installed.home, refresher.REQUEST, {**request, "status": "closed"})
    if case == "void":
        refresher.write_json(installed.home, refresher.REQUEST, {**request, "registration": "f" * 32})
    service.handoff = case == "handover"
    waits = []

    def go(seconds):
        waits.append(seconds)
        refresher.write_json(installed.home, refresher.REQUEST, {**request, "hold": service.run()})

    monkeypatch.setattr(refresher.time, "sleep", go)
    guarded = refresher._HeldService(service, installed.home, request, 1 if case == "arrives" else 0, 0.25)
    with refresher.try_lock(installed.home) as held:
        assert held
        if case in {"none", "wrong", "no run", "closed", "void", "handover"}:
            with pytest.raises(refresher.NoHold):
                guarded.stop()
        else:
            guarded.stop()
    assert len(service.exact_stops) == (1 if case == "arrives" else 0)
    assert waits == ([0.25] if case == "arrives" else [])


def test_the_helper_looks_at_the_app_once_in_five_seconds_while_it_waits(installed, monkeypatch):
    """Each look is a Task Scheduler query, a PowerShell process. A wait can last 10 minutes."""
    import inspect
    assert inspect.signature(refresher.automatic).parameters["poll"].default == 5.0
    assert inspect.signature(refresher._HeldService.__init__).parameters["poll"].default == 5.0
    service = Service(installed.root)
    request = request_for(installed, service, hold=False)
    looks, waits = [], []
    run = service.run

    def counted():
        looks.append(1)
        return run()

    service.run = counted
    clock = [0.0]
    monkeypatch.setattr(refresher.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(refresher.time, "sleep", sleep)
    guarded = refresher._HeldService(service, installed.home, request, 90)
    with refresher.try_lock(installed.home) as held:
        assert held
        with pytest.raises(refresher.NoHold):
            guarded.stop()
    assert waits == [5.0] * 18 and len(looks) == 19
    assert service.exact_stops == []


@pytest.mark.parametrize("boundary", ["before journal", "after journal"])
def test_a_successor_between_the_last_check_and_the_stop_is_never_stopped(installed, monkeypatch, boundary):
    service = Service(installed.root)
    request, write, plan = request_for(installed, service), refresher._write, refresher.plan
    plans = []

    def successor():
        service.current = {**service.current, "run": "f" * 32, "pid": 99}

    def journal_written(owner, path, raw):
        write(owner, path, raw)
        if Path(path).name == refresher.JOURNAL:
            assert json.loads(raw)["registration"] == service.registration
            assert json.loads(raw)["request"] == request["id"]
            if boundary == "after journal":
                successor()

    def planned(*args):
        result = plan(*args)
        plans.append(1)
        if len(plans) == 2 and boundary == "before journal":
            successor()
        return result

    monkeypatch.setattr(refresher, "_write", journal_written)
    monkeypatch.setattr(refresher, "plan", planned)
    result = auto(installed, service, request)
    assert result["result"] == "refused" and result["errorType"] == "NoHold"
    assert service.exact_stops == [] and service.events == [] and installed.calls == []
    assert refresher.unfinished(installed.home) is None  # No disk change needs recovery.


@pytest.mark.parametrize("boundary", ["before journal", "after journal"])
def test_a_host_that_ended_at_the_journal_is_not_stopped(installed, monkeypatch, boundary):
    service = Service(installed.root)
    request, write, plan = request_for(installed, service), refresher._write, refresher.plan
    plans = []

    def journal_written(owner, path, raw):
        write(owner, path, raw)
        if Path(path).name == refresher.JOURNAL and boundary == "after journal":
            service.up = False

    def planned(*args):
        result = plan(*args)
        plans.append(1)
        if len(plans) == 2 and boundary == "before journal":
            service.up = False
        return result

    monkeypatch.setattr(refresher, "_write", journal_written)
    monkeypatch.setattr(refresher, "plan", planned)
    auto(installed, service, request)
    assert service.exact_stops == [] and "stop" not in service.events


@pytest.mark.parametrize("point", ["install", "audit"])
def test_automatic_case_one_recovers_an_interrupted_refresh(installed, monkeypatch, point):
    pointer, service = installed.pointer.read_bytes(), Service(installed.root)
    request = request_for(installed, service)
    journal = interrupted(installed, service, request, monkeypatch, point=point)
    assert (journal["registration"], journal["request"]) == (service.registration, request["id"])
    assert refresher.started(installed.home, request)
    result = auto(installed, service, request)
    assert (result["state"], result["result"], result["attempts"]) == ("settled", "rolled-back", 1)
    assert result["lastAttempt"] is not None and service.up
    unchanged(installed, pointer)


def test_automatic_recovery_with_nothing_to_put_back_just_settles(installed, monkeypatch):
    service = Service(installed.root)
    request, write = request_for(installed, service), refresher._write

    def ended(owner, path, raw):
        write(owner, path, raw)
        if Path(path).name == refresher.JOURNAL:
            raise Ended()

    monkeypatch.setattr(refresher, "_write", ended)
    with pytest.raises(Ended):
        auto(installed, service, request)
    assert refresher.unfinished(installed.home) and service.up
    monkeypatch.setattr(refresher, "_write", write)
    assert auto(installed, service, request)["result"] == "rolled-back"
    assert service.events == [] and refresher.unfinished(installed.home) is None


def test_incomplete_automatic_rollback_waits_and_starts_nothing(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    interrupted(installed, service, request, monkeypatch)

    def denied(*args):
        raise PermissionError("Cannot restore.")

    monkeypatch.setattr(generations, "restore_binding", denied)
    before = service.events[:]
    result = auto(installed, service, request)
    assert result["state"] == "waiting" and result["attempts"] == 1
    assert service.events == before and not service.up and refresher.unfinished(installed.home)


def test_automatic_first_rollback_incomplete_stays_waiting(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    installed.fail["install"] = True
    monkeypatch.setattr(generations, "restore_binding", lambda *args: (_ for _ in ()).throw(OSError()))
    assert auto(installed, service, request)["state"] == "waiting"
    assert service.events == ["stop"] and not service.up


@pytest.mark.parametrize("recovery", [False, True])
def test_complete_rollback_that_cannot_restart_stays_waiting(installed, monkeypatch, recovery):
    service = Service(installed.root)
    request = request_for(installed, service)
    if recovery:
        interrupted(installed, service, request, monkeypatch)
    else:
        installed.fail["install"] = True
    service.start = lambda: None  # A start that does not produce a running instance.
    result = auto(installed, service, request)
    assert result["state"] == "waiting" and result["stoppedApp"]
    assert refresher.unfinished(installed.home) is None


@pytest.mark.parametrize("go", [False, True])
def test_recovery_waits_for_the_host_that_runs_then(installed, monkeypatch, go):
    service = Service(installed.root)
    request = request_for(installed, service)
    interrupted(installed, service, request, monkeypatch, point="audit")
    service.up = True
    service.current = {**service.current, "run": "f" * 32}
    calls = []

    async def hold(seconds):
        calls.append(seconds)
        refresher.write_json(installed.home, refresher.REQUEST, {**request, "hold": service.run()})

    monkeypatch.setattr(refresher.asyncio, "sleep", hold)
    before = len(service.exact_stops)
    result = auto(installed, service, request, recover_wait=1 if go else 0)
    if go:
        assert result["result"] == "rolled-back" and calls == [0.001]
        assert len(service.exact_stops) == before + 1
        assert service.exact_stops[-1]["run"] == "f" * 32
    else:
        assert result["state"] == "ready" and result["run"] == service.run()
        assert len(service.exact_stops) == before and refresher.unfinished(installed.home)


def test_recovery_of_a_new_host_during_a_handover_sends_no_stop(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    interrupted(installed, service, request, monkeypatch, point="audit")
    service.up = service.handoff = True
    before = service.events[:]
    assert auto(installed, service, request)["state"] == "waiting"
    assert service.events == before


@pytest.mark.parametrize("state, changed, expected", [
    ("ready", False, "withdrawn"), ("waiting", False, "rolled-back"),
    ("stopped", False, "rolled-back"), ("ready", True, "refreshed"),
    ("waiting", True, "refreshed"), ("stopped", True, "refreshed"),
])
def test_case_two_settles_from_disk_without_a_second_refresh(installed, state, changed, expected):
    service = Service(installed.root)
    request = request_for(installed, service)
    if changed:
        request["launcher"] = {**installed.old, "digest": "different"}
        refresher.write_json(installed.home, refresher.REQUEST, request)
    refresher._automatic_record(installed.home, request, state=state, attempts=4, lastAttempt=10,
                                **{"from": {"version": OLD[0]}})
    assert refresher.started(installed.home, request) == (state != "ready")
    result = auto(installed, service, request)
    assert (result["state"], result["result"], result["attempts"], result["lastAttempt"]) == (
        "settled", expected, 4, 10)
    assert result["from"] == {"version": OLD[0]} and installed.calls == [] and service.events == []


@pytest.mark.parametrize("starts", [False, True])
def test_case_two_restarts_only_an_app_the_helper_stopped(installed, starts):
    service = Service(installed.root, up=False)
    request = request_for(installed, service)
    refresher._automatic_record(installed.home, request, state="waiting", stoppedApp=True)
    if not starts:
        service.start = lambda: None
    result = auto(installed, service, request)
    assert result["state"] == ("settled" if starts else "waiting")
    assert installed.calls == [] and result["stoppedApp"]


@pytest.mark.parametrize("state", ["ready", "waiting"])
def test_case_two_does_not_start_an_app_without_a_recorded_stop(installed, state):
    service = Service(installed.root, up=False)
    request = request_for(installed, service)
    refresher._automatic_record(installed.home, request, state=state)
    assert auto(installed, service, request)["state"] == "settled"
    assert service.events == [] and installed.calls == []


@pytest.mark.parametrize("attempts, age, tries", [(5, 1, True), (6, 3599, False), (6, 3600, True), (8, 4000, True)])
def test_recovery_backoff_is_hourly_only_after_six_attempts(installed, monkeypatch, attempts, age, tries):
    service = Service(installed.root)
    request = request_for(installed, service)
    interrupted(installed, service, request, monkeypatch)
    monkeypatch.setattr(refresher.time, "time", lambda: 10000)
    refresher._automatic_record(installed.home, request, state="waiting", attempts=attempts, lastAttempt=10000 - age)
    before = saved(installed)
    result = auto(installed, service, request)
    assert result["attempts"] == attempts + int(tries)
    if tries:
        assert result["result"] == "rolled-back" and service.up
    else:
        assert result == before and not service.up


@pytest.mark.parametrize("condition", ["missing", "id", "closed", "void"])
def test_automatic_ignores_inapplicable_requests_and_retires_only_void_ones(installed, monkeypatch, condition):
    service = Service(installed.root)
    request = request_for(installed, service)
    if condition == "missing":
        refresher.remove_json(installed.home, refresher.REQUEST)
    else:
        field = {"id": "id", "closed": "status", "void": "registration"}[condition]
        refresher.write_json(installed.home, refresher.REQUEST, {**request, field: "other"})
    retired = []
    monkeypatch.setattr(refresher, "_retire_task", lambda home: retired.append(home))
    assert auto(installed, service, request) == {}
    assert retired == ([installed.home] if condition == "void" else [])
    assert saved(installed) is None and service.events == [] and installed.calls == []


@pytest.mark.parametrize("registered", [False, True])
def test_an_unfinished_manual_journal_is_not_used_by_automatic(installed, monkeypatch, registered):
    service = Service(installed.root)
    request = request_for(installed, service)
    journal = interrupted(installed, service, request, monkeypatch)
    journal.pop("request")
    if not registered:
        journal.pop("registration")
    refresher.write_json(installed.home, refresher.JOURNAL, journal)
    refresher.remove_json(installed.home, refresher.RECORD)
    before, events = installed.pointer.read_bytes(), service.events[:]
    result = auto(installed, service, request)
    assert (result["state"], result["result"], result["step"]) == (
        "settled", "refused", "an unfinished manual refresh")
    assert saved(installed, refresher.JOURNAL) == journal and installed.pointer.read_bytes() == before
    assert service.events == events


def test_stale_journal_is_never_recovered_after_a_new_registration(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    journal = interrupted(installed, service, request, monkeypatch, point="audit")
    previous = Path(journal["previous"])
    before = (generations.bootstrap_identity(installed.launcher), installed.pointer.read_bytes(),
              generations.bootstrap_identity(previous))
    service.registration = service.current["registration"] = "f" * 32
    request["registration"] = service.registration
    refresher.write_json(installed.home, refresher.REQUEST, request)
    assert refresher.journal_kind(installed.home, service.registration) == "stale"
    for check in (True, False):
        with pytest.raises(refresher.Refused, match="belongs to an earlier installation.*It was not used"):
            run(installed, service, check=check)
    assert str(refresher._services(installed.home) / refresher.JOURNAL) in str(
        _stale_refusal(installed, service))
    events = service.events[:]
    # A settled record isolates stale-journal handling from case 2's restart.
    refresher._automatic_record(installed.home, request, state="settled", result="refused")
    auto(installed, service, request)
    stale = list(refresher._services(installed.home).glob("launcher-refresh-journal-stale-*.json"))
    assert len(stale) == 1 and json.loads(stale[0].read_text()) == journal
    assert refresher.unfinished(installed.home) is None and service.events == events
    assert (generations.bootstrap_identity(installed.launcher), installed.pointer.read_bytes(),
            generations.bootstrap_identity(previous)) == before


def _stale_refusal(installed, service):
    with pytest.raises(refresher.Refused) as reason:
        run(installed, service)
    return reason.value


def test_manual_journals_name_the_registration_and_manual_request(installed, monkeypatch):
    service = Service(installed.root)
    installed.fail["audit"] = Ended()

    async def ended(*args, **kwargs):
        raise Ended()

    monkeypatch.setattr(refresher, "_undo", ended)
    with pytest.raises(Ended):
        run(installed, service)
    journal = refresher.unfinished(installed.home)
    assert journal["registration"] == service.registration and journal["request"] is None


def test_request_is_read_only_after_the_lock_is_acquired(installed, monkeypatch):
    service = Service(installed.root)
    request, lock, attempts = request_for(installed, service), refresher.try_lock, []

    @contextmanager
    def delayed(home):
        attempts.append(1)
        if len(attempts) == 1:
            refresher.write_json(home, refresher.REQUEST, {**request, "status": "closed"})
            yield False
        else:
            with lock(home) as held:
                yield held

    monkeypatch.setattr(refresher, "try_lock", delayed)
    assert auto(installed, service, request) == {} and len(attempts) == 2
    assert service.events == [] and saved(installed) is None


def test_try_lock_never_raises_for_a_held_lock_and_automatic_gives_up(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    with refresher.try_lock(installed.home) as first:
        with refresher.try_lock(installed.home) as second:
            assert first and not second
        clock = iter([0, 16])
        monkeypatch.setattr(refresher.time, "monotonic", lambda: next(clock))
        # Avoid asyncio's use of the patched clock by driving the no-await path.
        call = refresher.automatic(installed.home, request["id"], service=service)
        with pytest.raises(StopIteration) as result:
            call.send(None)
        assert result.value.value == {}
    assert saved(installed) is None and service.events == []


def test_json_interface_retries_only_permission_errors(installed, monkeypatch):
    path = refresher._services(installed.home) / refresher.REQUEST
    assert refresher.read_json(installed.home, refresher.REQUEST) is None
    refresher.write_json(installed.home, refresher.REQUEST, {"schema": 1})
    read, attempts, waits = Path.read_bytes, [], []

    def replacing(self):
        if self == path:
            attempts.append(1)
            if len(attempts) <= 5:
                raise PermissionError()
        return read(self)

    monkeypatch.setattr(Path, "read_bytes", replacing)
    monkeypatch.setattr(refresher.time, "sleep", waits.append)
    assert saved(installed, refresher.REQUEST) == {"schema": 1}
    assert len(attempts) == 6 and waits == [0.1] * 5
    attempts.clear()
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        saved(installed, refresher.REQUEST)
    monkeypatch.setattr(Path, "read_bytes", read)
    path.write_text("[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        saved(installed, refresher.REQUEST)
    refresher.remove_json(installed.home, refresher.REQUEST)
    refresher.remove_json(installed.home, refresher.REQUEST)


def test_started_ignores_other_requests_manual_and_done_records(installed):
    service = Service(installed.root)
    request = request_for(installed, service)
    assert not refresher.started(installed.home, request)
    for record in ({"state": "waiting"}, {"request": "other", "state": "stopped"},
                   {"request": request["id"], "state": "ready"}):
        refresher.write_json(installed.home, refresher.RECORD, record)
        assert not refresher.started(installed.home, request)
    refresher.write_json(installed.home, refresher.JOURNAL, {"done": True})
    assert refresher.journal_kind(installed.home, service.registration) == "none"
    assert refresher.set_stale_aside(installed.home) is None
    assert refresher.run_identity(None) is None
    assert refresher.run_identity({**service.current, "phase": "listening"}) == service.current


def test_kept_cleanup_deletes_only_a_recorded_previous_automatic_folder(installed):
    service = Service(installed.root)
    request = request_for(installed, service)
    first = auto(installed, service, request)
    first_path = Path(first["previous"])
    second_path = installed.root / "launcher-0.94.0-1234abcd-2"
    second_path.mkdir()
    manual = installed.root / "launcher-0.86.0-1234abcd"
    manual.mkdir()
    foreign = installed.root.parent / "launcher-0.80.0-1234abcd"
    foreign.mkdir()
    wrong = installed.root / "not-a-launcher"
    wrong.mkdir()
    link = installed.root / "launcher-0.81.0-1234abcd"
    link.symlink_to(manual, target_is_directory=True)
    refresher.write_json(installed.home, refresher.KEPT, {
        "schema": 1, "paths": [str(first_path), str(foreign), str(wrong), str(link)]})
    refresher._keep_previous(installed.home, installed.launcher, str(second_path))
    assert not first_path.exists() and second_path.is_dir()
    assert manual.is_dir() and foreign.is_dir() and wrong.is_dir() and link.is_symlink()
    assert str(first_path) not in saved(installed, refresher.KEPT)["paths"]
    assert str(second_path) in saved(installed, refresher.KEPT)["paths"]


@pytest.mark.parametrize("reason", ["journal", "stale", "denied"])
def test_kept_cleanup_leaves_a_protected_or_undeletable_folder(installed, monkeypatch, reason):
    old, new = [installed.root / name for name in ("launcher-0.87.0-1234abcd", "launcher-0.94.0-abcdef12")]
    old.mkdir()
    new.mkdir()
    refresher.write_json(installed.home, refresher.KEPT, {"schema": 1, "paths": [str(old)]})
    if reason in {"journal", "stale"}:
        name = refresher.JOURNAL if reason == "journal" else "launcher-refresh-journal-stale-1.json"
        refresher.write_json(installed.home, name, {"previous": str(old)})
    else:
        monkeypatch.setattr(refresher.shutil, "rmtree", lambda path: (_ for _ in ()).throw(PermissionError()))
    refresher._keep_previous(installed.home, installed.launcher, str(new))
    assert old.is_dir() and saved(installed, refresher.KEPT)["paths"] == [str(old), str(new)]


def test_cleanup_runs_after_the_refresh_lock_is_released(installed, monkeypatch):
    service = Service(installed.root)
    request, checked = request_for(installed, service), []

    def cleanup(home, launcher, previous):
        with refresher.try_lock(home) as held:
            checked.append(held)

    monkeypatch.setattr(refresher, "_keep_previous", cleanup)
    assert auto(installed, service, request)["result"] == "refreshed"
    assert checked == [True]


@pytest.mark.parametrize("condition, code, retired", [
    ("success", 0, False), ("no task", 0, True), ("unreadable", 1, False),
    ("console", 1, False), ("inside", 1, False), ("launcher python", 1, False),
    ("waiting", 1, False),
])
def test_automatic_main_is_silent_and_retires_only_for_no_main_task(
        installed, monkeypatch, condition, code, retired):
    from amplifier_web import windows_hidden_console, windows_installation
    service = Service(installed.root)
    request = request_for(installed, service)
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(refresher.shutil, "which", lambda *args, **kwargs: str(installed.root / "uv/uv"))
    monkeypatch.setattr(refresher, "_task_environment", lambda *args: calls.append("environment"))
    monkeypatch.setattr(refresher, "_retire_task", lambda home: calls.append("retire"))
    monkeypatch.setattr(windows_installation, "owner_notice", lambda *args: pytest.fail("No message box."))

    def console():
        calls.append("console")
        if condition == "console":
            raise RuntimeError("No console.")

    def task(home):
        calls.append("task")
        if condition == "no task":
            raise refresher.Refused("No task.")
        if condition == "unreadable":
            raise OSError("Cannot read task.")
        return service

    if condition == "inside":
        service.inside = True
    if condition == "launcher python":
        monkeypatch.setattr(sys, "prefix", str(service.launcher))
    if condition == "waiting":
        refresher._automatic_record(installed.home, request, state="waiting", stoppedApp=True)
        service.up = False
        service.start = lambda: None
    monkeypatch.setattr(windows_hidden_console, "ensure_hidden_console", console)
    monkeypatch.setattr(refresher, "TaskService", task)
    # Reuse the real automatic body with the fixture's non-executing process fake.
    automatic = refresher.automatic

    async def automatic_main(home, request_id, **kwargs):
        try:
            return await automatic(home, request_id, **kwargs, process=installed.process, stop_wait=0)
        except Exception as error:
            calls.append((type(error).__name__, str(error)))
            raise

    monkeypatch.setattr(refresher, "automatic", automatic_main)
    text = base64.urlsafe_b64encode(json.dumps({"home": str(installed.home), "request": request["id"]}).encode()).decode()
    with pytest.raises(SystemExit) as exit:
        refresher.main(["--automatic", text])
    assert exit.value.code == code and calls[0] == "console", calls
    assert ("retire" in calls) == retired
    if condition == "console":
        assert calls == ["console"]
    if code == 1:
        assert service.events == []


def test_automatic_main_rejects_bad_payload_and_conflicting_options(installed, monkeypatch):
    from amplifier_web import windows_hidden_console
    monkeypatch.setattr(windows_hidden_console, "ensure_hidden_console", lambda: None)
    monkeypatch.setattr(sys, "platform", "win32")
    for options in (["--automatic", "not base64"], ["--automatic", "x", "--check"],
                    ["--automatic", "x", "--recover"]):
        with pytest.raises(SystemExit) as exit:
            refresher.main(options)
        assert exit.value.code != 0


def test_task_service_checks_live_identity_and_passes_exact_stop(tmp_path, monkeypatch):
    from amplifier_web import windows_service, windows_task_runtime as runtime
    record = {"schema": 1, "registration": "a" * 32, "run": "b" * 32,
              "pid": 77, "created": "123", "image": r"C:\Python\pythonw.exe", "phase": "listening"}
    spec = SimpleNamespace(python=str(tmp_path / "tools/amplifier-unified/Scripts/pythonw.exe"),
                           tool_path="", launch_roots={}, github_access="auto", registration_id="a" * 32)
    value, actual, stops = {"instances": 1}, {"exited": lambda: False}, []
    monkeypatch.setattr(windows_service, "_read", lambda home: (value, spec))
    monkeypatch.setattr(runtime, "read", lambda spec: record)
    monkeypatch.setattr(runtime, "matches", lambda record, actual: actual.get("matches", True))

    @contextmanager
    def process(pid):
        assert pid == 77
        yield actual

    monkeypatch.setattr(runtime, "process", process)
    monkeypatch.setattr(windows_service, "_stop", lambda value, spec, **kw: stops.append(kw))
    service = refresher.TaskService(tmp_path / "app")
    assert service.registration == record["registration"]
    assert service.run() == refresher.run_identity(record)
    service.stop_exact(service.run())
    assert stops == [{"expected": refresher.run_identity(record)}]
    actual["matches"] = False
    assert service.run() is None
    actual["matches"] = True
    actual["exited"] = lambda: True
    assert service.run() is None
    actual = None
    assert service.run() is None
    value["instances"] = 0
    assert service.run() is None
    monkeypatch.setattr(windows_service, "_read", lambda home: (value, None))
    with pytest.raises(RuntimeError, match="task changed"):
        service.stop_exact(refresher.run_identity(record))


def test_retire_task_is_best_effort_and_uses_the_small_task_module(installed, monkeypatch):
    module, calls = SimpleNamespace(retire_task=lambda home: calls.append(home)), []
    monkeypatch.setitem(sys.modules, "amplifier_web.windows_launcher_task", module)
    refresher._retire_task(installed.home)
    assert calls == [installed.home]
    module.retire_task = lambda home: (_ for _ in ()).throw(RuntimeError())
    refresher._retire_task(installed.home)


@pytest.mark.parametrize("matching", [False, True])
def test_automatic_rollback_needs_the_new_hosts_own_hold(installed, matching):
    service = Service(installed.root, ready=(False, False))
    request, start = request_for(installed, service), service.start

    def new_host():
        start()
        service.current = {**service.current, "run": "f" * 32, "pid": 99}
        if matching:
            refresher.write_json(installed.home, refresher.REQUEST, {**request, "hold": service.run()})

    service.start = new_host
    result = auto(installed, service, request)
    assert len(service.exact_stops) == (2 if matching else 1)
    assert result["state"] == ("settled" if matching else "waiting")
    if matching:
        assert result["result"] == "rolled-back" and service.exact_stops[-1]["pid"] == 99
    else:
        assert refresher.unfinished(installed.home) and service.up


def test_legacy_manual_journal_is_still_recovered_by_the_manual_command(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    journal = interrupted(installed, service, request, monkeypatch)
    journal.pop("registration")
    journal.pop("request")
    refresher.write_json(installed.home, refresher.JOURNAL, journal)
    assert refresher.journal_kind(installed.home, service.registration) == "manual"
    assert run(installed, service, recover=True)["ok"] and service.up


@pytest.mark.parametrize("record", [{"result": "refreshed", "attempts": 9},
                                  {"request": "other", "state": "waiting", "attempts": 9}])
def test_a_manual_or_other_requests_record_does_not_block_the_first_run(installed, record):
    service = Service(installed.root)
    request = request_for(installed, service)
    refresher.write_json(installed.home, refresher.RECORD, record)
    result = auto(installed, service, request)
    assert result["result"] == "refreshed" and result["attempts"] == 0
    assert result["request"] == request["id"]


def test_record_merges_preserve_recovery_facts_without_writing_each_poll(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    write, writes = refresher.write_json, []

    def counted(home, name, value):
        writes.append(name)
        write(home, name, value)

    monkeypatch.setattr(refresher, "write_json", counted)
    first = refresher._automatic_record(installed.home, request, state="ready", run=service.run(),
                                        attempts=3, stoppedApp=True, **{"from": {"version": OLD[0]}})
    assert refresher._automatic_record(installed.home, request, state="ready", run=service.run()) == first
    assert writes == [refresher.RECORD]  # The repeated poll performs no fsync/replace.
    result = refresher._automatic_record(installed.home, request, state="waiting", stoppedApp=False)
    assert result["stoppedApp"] and result["attempts"] == 3
    assert result["from"] == first["from"] and result["to"] == first["to"]
    assert writes == [refresher.RECORD, refresher.RECORD]


def test_case_two_start_exception_keeps_recovery_waiting(installed):
    service = Service(installed.root, up=False)
    request = request_for(installed, service)
    refresher._automatic_record(installed.home, request, state="stopped", stoppedApp=True)

    def failed():
        raise OSError("A private task failure.")

    service.start = failed
    result = auto(installed, service, request)
    assert result["state"] == "waiting" and result["errorType"] == "OSError"
    assert "private task failure" not in json.dumps(result) and installed.calls == []


def test_manual_journal_does_not_overwrite_this_requests_existing_record(installed, monkeypatch):
    service = Service(installed.root)
    request = request_for(installed, service)
    journal = interrupted(installed, service, request, monkeypatch)
    journal["request"] = None
    refresher.write_json(installed.home, refresher.JOURNAL, journal)
    record = saved(installed)
    assert auto(installed, service, request) == record and saved(installed) == record


def test_kept_cleanup_matches_journal_paths_by_folder_identity(installed):
    old, new = [installed.root / name for name in ("launcher-0.87.0-1234abcd", "launcher-0.94.0-abcdef12")]
    old.mkdir()
    new.mkdir()
    refresher.write_json(installed.home, refresher.KEPT, {"schema": 1, "paths": [str(old)]})
    alias = installed.root / "tools/.." / old.name
    refresher.write_json(installed.home, refresher.JOURNAL, {"previous": str(alias)})
    refresher._keep_previous(installed.home, installed.launcher, str(new))
    assert old.is_dir()


def test_native_fixture_stand_in_has_every_name_that_the_manual_command_reads(tmp_path):
    # The native fixture's `Service` stands in for `TaskService`. A name that
    # the manual command reads and the stand-in lacks fails only on the Windows
    # runner (`registration`, run 44133982586). This finds it on every platform.
    import ast
    import importlib.util
    manual = {"refresh", "_outside", "_refuse_stale", "plan", "_report", "_run", "_undo", "_observe"}
    functions = [node for node in ast.parse(Path(refresher.__file__).read_text(encoding="utf-8")).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in manual]
    assert {node.name for node in functions} == manual
    read = {item.attr for node in functions for item in ast.walk(node)
            if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "service"}
    assert {"registration", "launcher", "stop", "start", "ready"} <= read
    path = Path(__file__).parent / "fixtures/windows_launcher_refresh_native.py"
    spec = importlib.util.spec_from_file_location("launcher_refresh_native_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    stand_in = fixture.Service(tmp_path, [])
    assert sorted(name for name in read if not hasattr(stand_in, name)) == []
    assert fixture.Service(tmp_path, [], registration="f" * 32).registration != stand_in.registration


@pytest.mark.parametrize("already", [None, "set by the caller"])
def test_task_environment_keeps_the_current_folder_out_of_program_lookup(monkeypatch, already):
    # Found on a Windows runner (run 44158304642): the helper's folder is the app
    # generation's Scripts folder, which has a uv.exe of its own. Windows tries the
    # current folder before PATH, so the helper ran that program and not setup's.
    name = "NoDefaultCurrentDirectoryInExePath"
    names = (name, "AMPLIFIER_WEB_HOME", "AMPLIFIER_SESSION_STATE_HOME", "AMPLIFIER_GITHUB_ACCESS", "UV_CACHE_DIR")
    for item in names:
        monkeypatch.setenv(item, "before")  # Recorded, so that the test's end restores it.
    if already is None:
        os.environ.pop(name)
    else:
        os.environ[name] = already
    service = SimpleNamespace(spec=SimpleNamespace(state_home="state"), github_access="anonymous",
                              launch_roots={"UV_CACHE_DIR": "cache"})
    refresher._task_environment(service, Path("home"))
    assert os.environ[name] == (already or "1")
    assert [os.environ[item] for item in names[1:]] == ["home", "state", "anonymous", "cache"]
    # Both ways into the command set it before the first lookup of a program.
    source = Path(refresher.__file__).read_text(encoding="utf-8")
    main = source[source.index("def main(argv=None):"):source.index("def _task_environment(")]
    automatic, manual = main.split("if args.data_dir is None:")
    assert automatic.index("_task_environment(service, home)") < automatic.index("asyncio.run(automatic(")
    assert manual.index("_task_environment(service, args.data_dir)") < manual.index("asyncio.run(refresh(")
