"""Replace the stable Windows launcher with the version of the running app.

Task Scheduler starts a launcher from the folder that setup installed. An
in-app update adds app generations and never replaces that launcher, so a
defect in launcher code stays until the launcher itself is replaced.

Run this with the Python of the ACTIVE APP VERSION, from a process outside the
app (a terminal, setup, a remote shell). The normal stop ends every process
inside the app, so the command refuses to run from inside it:

    <data>\\updates\\a\\<generation>\\tools\\amplifier-unified\\Scripts\\python.exe
        -I -m amplifier_web.windows_launcher_refresh --data-dir <data> [--check | --recover]

It installs the launcher again at the same path, from the active version's own
verified release file and exact package list. The previous launcher folder is
kept beside it. The task definition, port, certificates, settings, chats and
the private Python are not touched.

Before its first effect it saves everything that a rollback needs in one
private record. After any failure it puts the previous launcher and launch
record back, restores the app's running state, and says in plain words what is
true. If this command itself is ended part-way (a closed window, a lost
connection), run it again: it finds that record and first puts the previous
state back. --recover does only that. --check reports and changes nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import tomllib

RECORD = "launcher-refresh.json"
JOURNAL = "launcher-refresh-journal.json"
LOCK = "launcher-refresh.lock"
RECEIPTS = ("consumer-install.json", "consumer-qualified.json")
_WINDOWS = os.name == "nt"


class Refused(RuntimeError):
    """A plain reason. Nothing was changed."""


def _python(prefix, windowless=False):
    if _WINDOWS:
        return Path(prefix) / "Scripts" / ("pythonw.exe" if windowless else "python.exe")
    return Path(prefix) / "bin/python"


def _same(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def _dist_info(prefix):
    prefix = Path(prefix)
    return [*prefix.glob("lib/python*/site-packages/amplifier_unified-*.dist-info"),
            *prefix.glob("Lib/site-packages/amplifier_unified-*.dist-info")]


def launcher_facts(prefix):
    """Version, source revision and identity of an installed launcher or app."""
    from .application_generations import bootstrap_identity
    identity = bootstrap_identity(prefix)
    folder = _dist_info(prefix)[0]
    version = next((line[9:].strip() for line in (folder / "METADATA").read_text(
        encoding="utf-8", errors="replace").splitlines() if line.startswith("Version: ")), None)
    revision = None
    try:
        revision = json.loads((folder / "direct_url.json").read_text()).get("vcs_info", {}).get("commit_id")
    except (OSError, ValueError, AttributeError):
        pass
    if revision is None:
        try:
            revision = json.loads((folder.parent / "amplifier_web/release-source.json").read_text())["revision"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return {"version": version, "revision": revision, "identity": identity}


def _base_python(prefix):
    """The launcher keeps its interpreter: every app version uses the same one."""
    try:
        for line in (Path(prefix) / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home":
                python = Path(value.strip()) / ("python.exe" if _WINDOWS else "python3")
                if python.is_file():
                    return python
    except OSError:
        pass
    raise Refused("The launcher's own Python could not be found.")


def _entry_points(prefix):
    try:
        receipt = tomllib.loads((Path(prefix) / "uv-receipt.toml").read_text(encoding="utf-8"))
        return [Path(row["install-path"]) for row in receipt["tool"]["entrypoints"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _free(path):
    candidate, count = Path(path), 1
    while candidate.exists():
        count += 1
        candidate = Path(f"{path}-{count}")
    return candidate


async def _move(source, target):
    """Rename a folder. A scanner can hold a file in it for a moment after the app stops."""
    for attempt in range(20):
        try:
            os.rename(source, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            await asyncio.sleep(0.5)


async def _discard(folder):
    """Remove a launcher that was never used. Returns the folder if part of it remains."""
    for _ in range(3):
        shutil.rmtree(folder, ignore_errors=True)
        if not Path(folder).exists():
            return None
        await asyncio.sleep(1)
    return folder


def _sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plan(service, home):
    """Decide without changing anything. Raises Refused with a plain reason."""
    from . import app_component_graph as components, application_generations as generations
    from .app_release_artifacts import filename, verify
    from .app_updates import validated_extras, version_tuple
    home, prefix = Path(home), Path(service.launcher)
    if prefix.name != "amplifier-unified" or not _python(prefix, windowless=True).is_file():
        raise Refused("The start-at-sign-in task does not start an Amplifier launcher that this command knows.")
    old = launcher_facts(prefix)
    target = generations.active(home)
    if target is None:
        return {"action": "none", "old": old,
                "reason": "The app runs from the launcher itself. No newer app version is active, "
                          "so there is nothing to refresh."}
    if target["bootstrap"] != old["identity"]:
        raise Refused("The active app version belongs to another launcher than the one the task starts.")
    if not _same(sys.prefix, target["prefix"]):
        raise Refused("Start this command with the Python of the active app version: "
                      + str(_python(target["prefix"])))
    if service.handoff_in_flight():
        raise Refused("An app update is still finishing. Wait until Amplifier has restarted, then run this again.")
    validated = json.loads((target["folder"] / "validated.json").read_text())
    version, revision, artifact = validated["version"], validated["revision"], validated.get("artifact")
    if old["revision"] is not None and old["revision"] == revision:
        return {"action": "none", "old": old, "new": {"version": version, "revision": revision},
                "reason": f"The launcher already has the app's version ({version})."}
    if (version_tuple(old["version"]) and version_tuple(version)
            and version_tuple(old["version"]) > version_tuple(version)):
        raise Refused(f"The launcher (version {old['version']}) is newer than the active app version ({version}).")
    if not artifact:
        raise Refused("The active app version was not installed from an official release file.")
    try:
        if generations.bootstrap_identity(target["prefix"]) != validated.get("installation"):
            raise ValueError("The active app installation changed.")
        graph = components.validate_graph(validated["componentGraph"])
        if components.digest(graph) != target["componentDigest"]:
            raise ValueError("The active app package list changed.")
        extras = validated_extras(validated.get("extras") or [])
        artifact = verify(artifact, version, revision)
        requirements = target["folder"] / "components.txt"
        if requirements.read_text(encoding="utf-8") != components.requirements(graph):
            raise ValueError("The active app package list file changed.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Refused("The active app version did not pass its own checks, so it cannot become the launcher.") from error
    uv = shutil.which("uv", path=os.pathsep.join([service.tool_path, os.environ.get("PATH", "")]))
    if not uv:
        raise Refused("Amplifier's setup helper (uv) was not found.")
    entries = _entry_points(prefix)
    bin_dir = service.launch_roots.get("UV_TOOL_BIN_DIR") or (str(entries[0].parent) if entries else None)
    if not bin_dir:
        raise Refused("The launcher's installation record does not say where its start programs are.")
    store = prefix.parent.parent
    return {"action": "refresh", "old": old, "target": target, "validated": validated, "graph": graph,
            "extras": extras, "source": artifact, "requirements": requirements, "uv": uv,
            "python": _base_python(prefix), "bin": Path(bin_dir), "entries": entries, "store": store,
            "wheel": store / filename(version), "new": {"version": version, "revision": revision}}


def _consumer(home, prefix):
    """Setup's own receipts, when setup made this installation."""
    root = Path(home).parent
    if (Path(home).name == "app" and (root / "consumer-install.json").is_file()
            and _same(prefix, root / "tools/amplifier-unified")):
        return root
    return None


def _services(home):
    return Path(home) / "config/services"


def _write(owner, path, raw):
    from .deployment import write_private
    write_private(Path(path), raw, **({"owned_root": Path(owner)} if _WINDOWS else {}))


def _record(home, value):
    try:
        _write(home, _services(home) / RECORD, json.dumps({"schema": 1, "at": time.time(), **value}))
    except (OSError, ValueError, RuntimeError):
        pass  # A support record must never change the outcome.


def _retire(home):
    try:
        (_services(home) / JOURNAL).unlink(missing_ok=True)
    except OSError:
        pass  # A record that says "done" is ignored; one that does not is settled again.


def unfinished(home):
    """The saved record of a refresh that did not finish, or None. Reads only."""
    path = _services(home) / JOURNAL
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise Refused(f"The record of an earlier, unfinished launcher refresh cannot be read: {path}") from error
    if isinstance(value, dict) and value.get("done") is True:
        return None  # That refresh finished; only its record could not be removed.
    if (not isinstance(value, dict) or value.get("schema") != 1
            or not all(isinstance(value.get(key), str) for key in ("prefix", "previous", "unused", "pointer"))
            or not isinstance(value.get("old"), dict) or not isinstance(value.get("entries"), dict)):
        raise Refused(f"The record of an earlier, unfinished launcher refresh cannot be read: {path}")
    return value


@contextmanager
def _lock(home):
    """One refresh per installation, from its saved record to its settled result."""
    from filelock import FileLock, Timeout
    folder = _services(home)
    with ExitStack() as stack:
        if _WINDOWS:
            from .windows_files import directory
            stack.enter_context(directory(folder, owned_root=home, create=True))
        else:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = FileLock(folder / LOCK, timeout=0, mode=0o600)
        try:
            lock.acquire()
        except Timeout:
            raise Refused("Another launcher refresh is running for this installation.") from None
        try:
            yield
        finally:
            lock.release()


async def _observe(service, wait):
    """What is true now: (running, answers). None means that it could not be read."""
    try:
        running = await asyncio.to_thread(service.running)
        return running, (await service.ready(wait) if running else False)
    except Exception:
        return None, None


def _state_text(running, ready):
    if running is None:
        return "It could not be read whether Amplifier is running."
    if not running:
        return "Amplifier is NOT running."
    return "Amplifier is running." if ready else "Amplifier is running, but it does not answer yet."


async def refresh(service, home, *, check=False, recover=False, wait=180, process=None):
    """Refresh, or first settle an unfinished refresh. The result has a plain `message`."""
    home = Path(home)
    if service.inside_host():
        raise Refused("This command was started from inside the running app. The refresh stops the app, "
                      "which would end this command too. Start it from outside the app.")
    if _same(sys.prefix, service.launcher):
        raise Refused("This command was started with the launcher's own Python. Start it with the Python "
                      "of the active app version.")
    if check:
        if unfinished(home) is not None:
            return {"ok": True, "changed": False, "action": "recover",
                    "message": "An earlier launcher refresh did not finish. The next run of this command "
                               "first puts the previous launcher back. Nothing was changed now."}
        return _report(plan(service, home))
    with _lock(home):
        journal = unfinished(home)  # Read under the lock: another run may have settled it.
        if journal is not None:
            result = await _undo(service, home, journal, wait, earlier=True)
            _record(home, {"result": "recovered:" + result["state"], "from": journal.get("from"),
                           "to": journal.get("to")})
            return {"ok": result["state"] != "rollback-incomplete" and result["settled"], "action": "recover",
                    **result, "message": "An earlier launcher refresh did not finish. " + result["message"]
                    + (" Run this command again to refresh the launcher." if result["state"] != "rollback-incomplete" else "")}
        _retire(home)  # Only the record of a finished refresh can be here now.
        if recover:
            return {"ok": True, "changed": False, "action": "none",
                    "message": "No unfinished launcher refresh was found. Nothing was changed."}
        return await _run(service, home, wait, process)


def _report(selected):
    old = selected["old"]
    facts = {"launcher": {"version": old["version"], "revision": old["revision"]}, "app": selected.get("new")}
    if selected["action"] == "none":
        return {"ok": True, "changed": False, "action": "none", **facts, "message": selected["reason"]}
    return {"ok": True, "changed": False, "action": "refresh", **facts,
            "message": f"The launcher has version {old['version']}. The app has version "
                       f"{selected['new']['version']}. A refresh will stop Amplifier, replace the launcher "
                       "and start Amplifier again. Nothing was changed now."}


async def _run(service, home, wait, process):
    from . import app_component_graph as components, application_generations as generations
    from .app_updates import PROBE, install_requirement
    from .update_diagnostics import probe_record
    if process is None:
        from .updates import process
    selected = plan(service, home)
    report = _report(selected)
    if selected["action"] == "none":
        return report
    old, facts = selected["old"], {key: report[key] for key in ("launcher", "app")}
    prefix, target, validated = Path(service.launcher), selected["target"], selected["validated"]
    pointer, wheel = home / "updates/application-active.json", selected["wheel"]
    root = _consumer(home, prefix)
    receipts = {name: (root / name).read_bytes() if (root / name).is_file() else None
                for name in RECEIPTS} if root is not None else {}
    previous = _free(selected["store"] / f"launcher-{old['version']}-{old['identity']['digest'][:8]}")
    journal = {"schema": 1, "prefix": str(prefix), "previous": str(previous), "old": old["identity"],
               "from": facts["launcher"], "to": facts["app"],
               "wasRunning": await asyncio.to_thread(service.running),
               "pointer": base64.b64encode(pointer.read_bytes()).decode("ascii"),
               "root": str(root) if root is not None else None,
               "consumer": {name: None if raw is None else base64.b64encode(raw).decode("ascii")
                            for name, raw in receipts.items()},
               "entries": {str(path): _digest(path) for path in selected["entries"]},
               "unused": str(_free(selected["store"] / "launcher-not-used")),
               "wheel": None if wheel.exists() else str(wheel)}
    # Everything that a rollback needs is saved before the first effect. If this
    # process is ended, the next run puts the previous state back from it.
    _write(home, _services(home) / JOURNAL, json.dumps(journal))
    state = {"step": "stopping Amplifier"}

    async def stopped():
        if await asyncio.to_thread(service.running):
            raise RuntimeError("The app is running.")

    try:
        if journal["wasRunning"]:
            await asyncio.to_thread(service.stop)
        await stopped()
        # Decide again with the app stopped: an update could have finished meanwhile.
        state["step"] = "checking again after the stop"
        again = plan(service, home)
        if (again["action"] != "refresh" or again["new"] != selected["new"] or again["old"] != old
                or pointer.read_bytes() != base64.b64decode(journal["pointer"])):
            raise Refused("The installation changed while Amplifier stopped.")
        state["step"] = "copying the release file"
        if wheel.exists():
            if _sha(wheel) != selected["source"]["sha256"]:
                raise ValueError("Another file has the release file's name.")
        else:
            partial = wheel.with_name(wheel.name + ".part")
            shutil.copyfile(selected["source"]["path"], partial)
            os.replace(partial, wheel)
        artifact = {**{key: selected["source"][key] for key in ("url", "sha256")}, "path": str(wheel)}
        state["step"] = "keeping the previous launcher"
        await stopped()
        await _move(prefix, previous)
        state["step"] = "installing the launcher"
        from .reset import repository_environment
        environment = repository_environment({
            **os.environ, "PATH": service.tool_path, **service.launch_roots,
            "AMPLIFIER_GITHUB_ACCESS": service.github_access})
        environment.update(UV_TOOL_DIR=str(prefix.parent), UV_TOOL_BIN_DIR=str(selected["bin"]),
                           UV_NO_CONFIG="1", UV_NO_PROGRESS="1", UV_NO_BUILD_PACKAGE="amplifier-core")
        await process(selected["uv"], "tool", "install", "--force", "--python", str(selected["python"]),
                      "--link-mode", "copy", "--overrides", str(selected["requirements"]),
                      "--with-requirements", str(selected["requirements"]),
                      install_requirement(selected["new"]["revision"], selected["extras"], artifact),
                      env=environment, timeout=900)
        state["step"] = "checking the new launcher"
        python = _python(prefix)
        if not python.is_file() or not _python(prefix, windowless=True).is_file():
            raise ValueError("The new launcher has no Python entry.")
        probe = probe_record(await process(python, "-I", "-c", PROBE, env=environment, timeout=120))
        if not probe or probe.get("ok") is not True or probe.get("version") != selected["new"]["version"]:
            raise ValueError("The new launcher does not start as the expected version.")
        if await components.read_graph(process, python, artifact=artifact) != selected["graph"]:
            raise ValueError("The new launcher's packages differ from the app's packages.")
        # The new launcher's own reader must accept the active app before it owns it.
        if not await generations.can_read_candidate(process, prefix, _python(target["prefix"]),
                                                    validated, home=home):
            raise ValueError("The new launcher cannot read the active app version.")
        fresh = launcher_facts(prefix)
        if fresh["revision"] != selected["new"]["revision"] or fresh["identity"] == old["identity"]:
            raise ValueError("The new launcher identity is not the expected one.")
        state["step"] = "binding the app to the new launcher"
        await stopped()
        generations.rebind(home, old["identity"], prefix)
        if root is not None:
            state["step"] = "updating setup's records"
            receipt = json.loads(receipts["consumer-install.json"])
            receipt.update(version=selected["new"]["version"], revision=selected["new"]["revision"],
                           artifact=artifact, graph=selected["graph"])
            _write(root, root / "consumer-install.json", json.dumps(receipt))
            await process(python, "-I", "-B", "-m", "amplifier_web.windows_consumer", "--root", str(root),
                          "--audit", env=environment, timeout=300)
        state["step"] = "starting Amplifier"
        await asyncio.to_thread(service.start)
        # A first start of new files can be slow: one more wait while the task still runs.
        if not (await service.ready(wait) or (await asyncio.to_thread(service.running)
                                              and await service.ready(wait))):
            raise RuntimeError("The app did not become ready with the new launcher.")
        # From here the refresh stands. A record that says so is never rolled
        # back, even if it cannot be removed afterwards.
        state["step"] = "recording the result"
        _write(home, _services(home) / JOURNAL, json.dumps({**journal, "done": True}))
    except BaseException as error:
        result = await _undo(service, home, journal, wait)
        _record(home, {"result": result["state"], "step": state["step"], "errorType": type(error).__name__,
                       "from": facts["launcher"], "to": facts["app"]})
        if not isinstance(error, Exception):
            raise
        return {"ok": False, "action": "refresh", **facts, "step": state["step"],
                "errorType": type(error).__name__, **result,
                "message": (str(error) + " " if isinstance(error, Refused) else
                            f"The launcher refresh did not finish. It stopped at this step: {state['step']}. ")
                           + result["message"]}
    _retire(home)
    fresh = {"version": selected["new"]["version"], "revision": selected["new"]["revision"]}
    _record(home, {"result": "refreshed", "from": facts["launcher"], "to": fresh, "previous": str(previous)})
    return {"ok": True, "changed": True, "action": "refresh", "launcher": fresh, "app": fresh,
            "previousLauncher": str(previous), "state": "refreshed", "running": True, "ready": True,
            "message": f"The launcher now has version {fresh['version']} (it had {old['version']}). "
                       "Amplifier is running. The previous launcher is kept in this folder, which you "
                       f"can delete later: {previous}"}


def _current(path):
    return path.read_bytes() if path.is_file() else None


def _digest(path):
    return _sha(path) if Path(path).is_file() else None


def _restore_entries(prefix, entries, added):
    """The start programs are copies of the launcher's own; take them from the restored launcher."""
    known = {os.path.normcase(name) for name in entries}
    for name, digest in entries.items():
        path = Path(name)
        source = _python(prefix).parent / path.name
        if _digest(path) != digest and source.is_file() and _digest(path) != _digest(source):
            shutil.copyfile(source, path)
    for path in added:
        if os.path.normcase(str(path)) not in known:
            Path(path).unlink(missing_ok=True)


def _entries_restored(prefix, entries):
    """Each start program has its saved bytes, or is a copy of the launcher's own."""
    for name, digest in entries.items():
        now = _digest(name)
        if now != digest and (now is None or now != _digest(_python(prefix).parent / Path(name).name)):
            return False
    return True


def _pending(home, journal, *, unused=True):
    """Whether anything that a rollback puts back still differs. Read from the disk only.

    This is the one gate for "the previous state is back": the saved record is
    removed only when it returns False. It never trusts that an earlier
    rollback finished because one of its steps did. The launch record counts by
    its launcher binding: an app update that finished meanwhile under the
    previous launcher is not this refresh's change.
    """
    from . import application_generations as generations
    home, prefix = Path(home), Path(journal["prefix"])
    root = Path(journal["root"]) if journal.get("root") else None
    try:
        if Path(journal["previous"]).exists() or (unused and Path(journal["unused"]).exists()):
            return True
        for name, text in (journal.get("consumer") or {}).items():
            if _current(root / name) != (None if text is None else base64.b64decode(text)):
                return True
        target = generations.active(home)
        return (generations.bootstrap_identity(prefix) != journal["old"] or target is None
                or target["bootstrap"] != journal["old"]
                or not _entries_restored(prefix, journal["entries"]))
    except (OSError, ValueError, KeyError, TypeError):
        return True


async def _undo(service, home, journal, wait, *, earlier=False):
    """Put back what a refresh changed, from its saved record and what is on disk now.

    Every step looks at the actual state first, so it can run again after it
    was itself ended. The saved record is removed only when the disk shows the
    previous state again.
    """
    from . import application_generations as generations
    home = Path(home)
    prefix, previous, unused = Path(journal["prefix"]), Path(journal["previous"]), Path(journal["unused"])
    root = Path(journal["root"]) if journal.get("root") else None
    saved = base64.b64decode(journal["pointer"])
    receipts = {name: None if text is None else base64.b64decode(text)
                for name, text in (journal.get("consumer") or {}).items()}
    was_running, problems, leftover, conflict = bool(journal.get("wasRunning")), [], None, False
    changed = _pending(home, journal)
    if changed:
        try:
            # Whatever runs now runs from a launcher that is about to be replaced.
            if await asyncio.to_thread(service.running):
                await asyncio.to_thread(service.stop)
            if await asyncio.to_thread(service.running):
                raise RuntimeError("still running")
        except Exception:
            problems.append("the app could not be stopped")
        if not problems:
            try:
                # First, and only over this refresh's own change: a record that
                # changed in another way belongs to a later update.
                generations.restore_binding(home, saved)
            except generations.BindingChanged:
                conflict = True
                problems.append("the app's launch record was changed by something else")
            except Exception:
                problems.append("the app's launch record could not be put back")
        if not problems:
            try:
                for name, raw in receipts.items():
                    if raw is None:
                        (root / name).unlink(missing_ok=True)
                    elif _current(root / name) != raw:
                        _write(root, root / name, raw)
                if previous.exists():
                    if prefix.exists():
                        await _move(prefix, unused)
                    await _move(previous, prefix)
                # The new launcher is set aside now, by this run or by an earlier
                # rollback that was ended. Its record names the start programs it added.
                _restore_entries(prefix, journal["entries"], _entry_points(unused) if unused.exists() else [])
            except Exception:
                problems.append("the previous launcher could not be put back")
        if not problems and _pending(home, journal, unused=False):
            problems.append("the previous launcher could not be put back")
        # The set-aside new launcher goes only after the disk shows the previous
        # state: until then its record is the list of start programs to remove.
        if not problems and unused.exists():
            leftover = await _discard(unused)
    if problems:
        running, ready = await _observe(service, min(wait, 10))
        return {"rolledBack": False, "state": "rollback-incomplete", "changed": True, "settled": False,
                "running": running, "ready": ready,
                "message": "The previous state could not be restored: " + "; ".join(problems) + ". "
                           + _state_text(running, ready)
                           + (f" The previous launcher is in {previous}." if previous.exists() else "")
                           + (" The app updated itself after the new launcher was installed, so the launcher "
                              "cannot be put back. If Amplifier works, the new launcher can stay: delete the file "
                              f"{_services(home) / JOURNAL} to end this refresh." if conflict else
                              " Run this command again with --recover. It changes only what this refresh changed.")}
    if journal.get("wheel"):
        try:
            Path(journal["wheel"]).unlink(missing_ok=True)
        except OSError:
            pass
    _retire(home)
    # The previous state is confirmed on disk. Now the app's own running state.
    running, ready = await _observe(service, wait if was_running else min(wait, 10))
    if was_running and running is not None and not (running and ready):
        # A stop that was asked for and not confirmed can still take effect.
        try:
            if not await asyncio.to_thread(service.running):
                await asyncio.to_thread(service.start)
                running, ready = await _observe(service, wait)
        except Exception:
            running, ready = None, None
    settled = bool(running and ready) if was_running else running is not None
    if not was_running and running is False:
        text = "Amplifier was not running before and is not running now."
    else:
        text = _state_text(running, ready)
        if running is False:
            text += " Open Amplifier from the Start menu, or sign out of Windows and sign in again."
        elif running and not ready:
            text += " Wait a minute, then open Amplifier."
    return {"rolledBack": changed, "state": "rolled-back" if changed else "unchanged", "changed": False,
            "settled": settled, "running": running, "ready": ready,
            "message": ("The previous launcher is back. " if changed else
                        "The previous launcher is in place. " if earlier else "Nothing was changed. ") + text
                       + (f" You can delete this unused folder: {leftover}" if leftover else "")}


class TaskService:
    """The owned start-at-sign-in task: where the launcher is, and how the app stops and starts."""

    def __init__(self, home):
        from . import windows_service
        self._service, self.home = windows_service, Path(home)
        _, spec = windows_service._read(self.home)
        if spec is None:
            raise Refused("No Amplifier start-at-sign-in task exists for this data folder and this Windows account.")
        self.spec = spec
        self.launcher = Path(spec.python).parent.parent
        self.tool_path, self.launch_roots = spec.tool_path, dict(spec.launch_roots)
        self.github_access = spec.github_access

    def running(self):
        value, spec = self._service._read(self.home)
        if spec != self.spec:
            raise RuntimeError("The start-at-sign-in task changed.")
        return value.get("instances", 0) != 0

    def inside_host(self):
        # A process that the app started is a descendant of its host.
        from .windows_task_runtime import parent_pid, read
        record, pid, seen = read(self.spec), os.getpid(), set()
        while record and pid and pid not in seen:
            if pid == record["pid"]:
                return True
            seen.add(pid)
            try:
                pid = parent_pid(pid)
            except (OSError, ProcessLookupError):
                break
        return False

    def handoff_in_flight(self):
        from .windows_app_recovery import read
        value = read(self.spec)
        return bool(value and value["status"] in {"prepared", "restoring"})

    def stop(self):
        self._service.command("stop", self.home)

    def start(self):
        self._service.command("start", self.home)

    async def ready(self, wait):
        from .installation_health import status
        result = await status(self.home, wait=wait)
        return result.get("ok") is True and result.get("processVerified") is True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Amplifier's data folder (the task's data folder)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Say what a run would do; change nothing")
    mode.add_argument("--recover", action="store_true",
                      help="Only put back the previous state after a refresh that did not finish")
    parser.add_argument("--wait", type=int, default=180, help="Seconds to wait for the app to become ready")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("The launcher refresh is for native Windows installations.")
    try:
        service = TaskService(args.data_dir)
        # The same private roots as the task's own host, for this process only.
        os.environ.update(AMPLIFIER_WEB_HOME=str(args.data_dir), AMPLIFIER_SESSION_STATE_HOME=service.spec.state_home,
                          AMPLIFIER_GITHUB_ACCESS=service.github_access, **service.launch_roots)
        result = asyncio.run(refresh(service, args.data_dir, check=args.check, recover=args.recover, wait=args.wait))
    except Refused as reason:
        result = {"ok": False, "changed": False, "refused": True, "message": str(reason) + " Nothing was changed."}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        result = {"ok": False, "changed": False, "errorType": type(error).__name__,
                  "message": "The launcher refresh could not finish its checks. Nothing was changed."}
    if sys.stdout is None:
        from .windows_installation import owner_notice
        owner_notice(result["message"])
    else:
        print(json.dumps(result))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
