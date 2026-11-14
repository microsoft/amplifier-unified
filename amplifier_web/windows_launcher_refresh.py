"""Replace the stable Windows launcher with the version of the running app.

Task Scheduler starts a launcher from the folder that setup installed. An
in-app update adds app generations and never replaces that launcher, so a
defect in launcher code stays until the launcher itself is replaced.

Run this with the Python of the ACTIVE APP VERSION, from a process outside the
app (a terminal, setup, a remote shell). The normal stop ends every process
inside the app, so the command refuses to run from inside it:

    <data>\\updates\\a\\<generation>\\tools\\amplifier-unified\\Scripts\\python.exe
        -I -m amplifier_web.windows_launcher_refresh --data-dir <data> [--check]

It installs the launcher again at the same path, from the active version's own
verified release file and exact package list. The previous launcher folder is
kept beside it. The task definition, port, certificates, settings, chats and
the private Python are not touched. After any failure it puts the previous
launcher and launch record back, starts the app again if it was running, and
says in plain words what is true.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import tomllib

RECORD = "launcher-refresh.json"
SAVED = "launcher-refresh-pointer.json"
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
    if service.inside_host():
        raise Refused("This command was started from inside the running app. The refresh stops the app, "
                      "which would end this command too. Start it from outside the app.")
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


def _write(owner, path, raw):
    from .deployment import write_private
    write_private(Path(path), raw, **({"owned_root": Path(owner)} if _WINDOWS else {}))


def _record(home, value):
    try:
        _write(home, Path(home) / "config/services" / RECORD, json.dumps({"schema": 1, "at": time.time(), **value}))
    except (OSError, ValueError, RuntimeError):
        pass  # A support record must never change the outcome.


async def refresh(service, home, *, check=False, wait=120, process=None):
    """Run the plan. The result has a plain `message` and says whether the app runs."""
    from . import app_component_graph as components, application_generations as generations
    from .app_updates import PROBE, install_requirement
    from .update_diagnostics import probe_record
    if process is None:
        from .updates import process
    home = Path(home)
    selected = plan(service, home)
    old = selected["old"]
    facts = {"launcher": {"version": old["version"], "revision": old["revision"]}, "app": selected.get("new")}
    if selected["action"] == "none":
        return {"ok": True, "changed": False, "action": "none", **facts, "message": selected["reason"]}
    if check:
        return {"ok": True, "changed": False, "action": "refresh", **facts,
                "message": f"The launcher has version {old['version']}. The app has version "
                           f"{selected['new']['version']}. A refresh will stop Amplifier, replace the launcher "
                           "and start Amplifier again. Nothing was changed now."}
    prefix, target, validated = Path(service.launcher), selected["target"], selected["validated"]
    pointer = home / "updates/application-active.json"
    root = _consumer(home, prefix)
    saved = {"pointer": pointer.read_bytes(), "consumer": {},
             "entries": {path: path.read_bytes() for path in selected["entries"] if path.is_file()}}
    if root is not None:
        saved["consumer"] = {name: (root / name).read_bytes() if (root / name).is_file() else None
                             for name in ("consumer-install.json", "consumer-qualified.json")}
    state = {"step": "stopping Amplifier", "moved": None, "rebound": False, "wheel": None}
    was_running = await asyncio.to_thread(service.running)
    # A trace before the first effect: if this process is ended, the record says so.
    _record(home, {"result": "started", "from": facts["launcher"], "to": facts["app"]})
    try:
        if was_running:
            await asyncio.to_thread(service.stop)
        if await asyncio.to_thread(service.running):
            raise RuntimeError("The app did not stop.")
        # Decide again with the app stopped: an update could have finished meanwhile.
        state["step"] = "checking again after the stop"
        again = plan(service, home)
        if (again["action"] != "refresh" or again["new"] != selected["new"]
                or again["old"] != old or pointer.read_bytes() != saved["pointer"]):
            raise Refused("The installation changed while Amplifier stopped.")
        state["step"] = "saving the launch record"
        _write(home, home / "config/services" / SAVED, saved["pointer"])
        state["step"] = "copying the release file"
        wheel = selected["wheel"]
        if wheel.exists():
            if _sha(wheel) != selected["source"]["sha256"]:
                raise ValueError("Another file has the release file's name.")
        else:
            partial = wheel.with_name(wheel.name + ".part")
            shutil.copyfile(selected["source"]["path"], partial)
            os.replace(partial, wheel)
            state["wheel"] = wheel
        artifact = {**{key: selected["source"][key] for key in ("url", "sha256")}, "path": str(wheel)}
        state["step"] = "keeping the previous launcher"
        previous = _free(selected["store"] / f"launcher-{old['version']}-{old['identity']['digest'][:8]}")
        await _move(prefix, previous)
        state["moved"] = previous
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
        report = probe_record(await process(python, "-I", "-c", PROBE, env=environment, timeout=120))
        if not report or report.get("ok") is not True or report.get("version") != selected["new"]["version"]:
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
        generations.rebind(home, old["identity"], prefix)
        state["rebound"] = True
        if root is not None:
            state["step"] = "updating setup's records"
            receipt = json.loads(saved["consumer"]["consumer-install.json"])
            receipt.update(version=selected["new"]["version"], revision=selected["new"]["revision"],
                           artifact=artifact, graph=selected["graph"])
            _write(root, root / "consumer-install.json", json.dumps(receipt))
            await process(python, "-I", "-B", "-m", "amplifier_web.windows_consumer", "--root", str(root),
                          "--audit", env=environment, timeout=300)
        state["step"] = "starting Amplifier"
        await asyncio.to_thread(service.start)
        if not await service.ready(wait):
            raise RuntimeError("The app did not become ready with the new launcher.")
    except BaseException as error:
        result = await _undo(service, home, prefix, saved, state, root, was_running, wait)
        _record(home, {"result": result["state"], "step": state["step"], "errorType": type(error).__name__,
                       "from": facts["launcher"], "to": facts["app"]})
        if not isinstance(error, Exception):
            raise
        return {"ok": False, "changed": False, "action": "refresh", **facts, "step": state["step"],
                "errorType": type(error).__name__, **result,
                "message": (str(error) + " " if isinstance(error, Refused) else
                            f"The launcher refresh did not finish. It stopped at this step: {state['step']}. ")
                           + result["message"]}
    fresh = {"version": selected["new"]["version"], "revision": selected["new"]["revision"]}
    _record(home, {"result": "refreshed", "from": facts["launcher"], "to": fresh, "previous": str(previous)})
    return {"ok": True, "changed": True, "action": "refresh", "launcher": fresh, "app": fresh,
            "previousLauncher": str(previous), "state": "refreshed", "running": True,
            "message": f"The launcher now has version {fresh['version']} (it had {old['version']}). "
                       "Amplifier is running. The previous launcher is kept in this folder, which you "
                       f"can delete later: {previous}"}


async def _undo(service, home, prefix, saved, state, root, was_running, wait):
    """Put back what this run changed, restore the app's running state, and say what is true."""
    from . import application_generations as generations
    problems, changed, leftover = [], state["moved"] is not None, None
    if changed:
        try:
            # Only this run can have started the app from the new launcher.
            if await asyncio.to_thread(service.running):
                await asyncio.to_thread(service.stop)
            if await asyncio.to_thread(service.running):
                raise RuntimeError("still running")
        except Exception:
            problems.append("the app could not be stopped")
        if not problems:
            try:
                if state["rebound"]:
                    _write(home, home / "updates/application-active.json", saved["pointer"])
                for name, raw in saved["consumer"].items():
                    path = root / name
                    if raw is None:
                        path.unlink(missing_ok=True)
                    elif not path.is_file() or path.read_bytes() != raw:
                        _write(root, path, raw)
                unused = None
                if prefix.exists():
                    unused = _free(prefix.parent.parent / "launcher-not-used")
                    await _move(prefix, unused)
                await _move(state["moved"], prefix)
                for path, raw in saved["entries"].items():
                    if not path.is_file() or path.read_bytes() != raw:
                        path.write_bytes(raw)
                pointer = generations.active(home)
                if pointer is None or pointer["bootstrap"] != generations.bootstrap_identity(prefix):
                    raise ValueError("The previous launcher and the launch record disagree.")
                if unused is not None:
                    leftover = await _discard(unused)
            except Exception:
                problems.append("the previous launcher could not be put back")
    if state["wheel"] is not None and not problems:
        try:
            Path(state["wheel"]).unlink(missing_ok=True)
        except OSError:
            pass
    running = False
    if not problems:
        try:
            running = await asyncio.to_thread(service.running)
            if was_running:
                # A stop that was asked for and not confirmed can still take effect:
                # report "running" only for an app that answers.
                if not (running and await service.ready(wait)):
                    if await asyncio.to_thread(service.running):
                        problems.append("the app does not answer")
                    else:
                        await asyncio.to_thread(service.start)
                        if not await service.ready(wait):
                            problems.append("the app did not become ready again")
                running = not problems
        except Exception:
            problems.append("the app could not be started again")
    if problems:
        return {"rolledBack": False, "state": "rollback-incomplete", "running": False,
                "message": "The previous state could not be fully restored: " + "; ".join(problems) + ". "
                           "Amplifier is NOT running. "
                           + (f"The previous launcher is in {state['moved']}. " if changed else "")
                           + f"The saved launch record is {home / 'config/services' / SAVED}. "
                           "Start Amplifier from the Start menu. If it does not start, run setup again."}
    return {"rolledBack": changed, "state": "rolled-back" if changed else "unchanged", "running": running,
            "message": ("The previous launcher is back. " if changed else "Nothing was changed. ")
                       + ("Amplifier is running." if running else
                          "Amplifier was not running before and is not running now.")
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
    parser.add_argument("--check", action="store_true", help="Say what a refresh would do; change nothing")
    parser.add_argument("--wait", type=int, default=120, help="Seconds to wait for the app to become ready")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("The launcher refresh is for native Windows installations.")
    try:
        service = TaskService(args.data_dir)
        # The same private roots as the task's own host, for this process only.
        os.environ.update(AMPLIFIER_WEB_HOME=str(args.data_dir), AMPLIFIER_SESSION_STATE_HOME=service.spec.state_home,
                          AMPLIFIER_GITHUB_ACCESS=service.github_access, **service.launch_roots)
        result = asyncio.run(refresh(service, args.data_dir, check=args.check, wait=args.wait))
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
