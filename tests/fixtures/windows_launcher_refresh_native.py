"""CI-only native check of the Windows launcher refresh. Not clean-room acceptance.

A real earlier launcher (the published 0.93.2 release file) and a real app
generation of the candidate are installed with uv in one private folder below
the runner's temporary folder, in setup's layout. The product command's own
code then refreshes the launcher. No Scheduled Task exists on a runner, so the
task's stop, start and readiness are a recorded stand-in; every file
operation, uv installation, package check, launch-record rebind, rollback and
recovery after an ended run is real. The clean room proves the task part on a
setup-made installation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
from types import SimpleNamespace
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://github.com/microsoft/amplifier-unified"
# The earlier launcher: the publisher's release file, by its published digest.
OLD = {"version": "0.93.2", "sha256": "d22da1aed1ece281f76d375ac46b5f1e6924a3c6b309a1fbe826763847b7eca2",
       "revision": "691732506d2d189b5c6159b5e816eedb0d5643f8"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def evidence():
    return Path(os.environ["RUNNER_TEMP"]).resolve() / "launcher-refresh-evidence"


def save(name, value):
    evidence().mkdir(parents=True, exist_ok=True)
    (evidence() / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def require_ci():
    assert sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
    assert sys.version_info[:2] == (3, 13) and sys.maxsize > 2 ** 32


def helpers():
    # The maintained fixture owns the temporary-folder and file-owner patterns.
    return load("launcher_refresh_helpers", ROOT / "tests/fixtures/manual_app_handoff_native.py")


def command(argv, *, env, cwd, timeout):
    result = subprocess.run([str(item) for item in argv], env=env, cwd=cwd, timeout=timeout,
                            capture_output=True, text=True, errors="replace")
    if result.returncode:
        # Runner paths only. Shown so that a failed setup step can be diagnosed.
        print(result.stderr[-3000:], flush=True)
        raise RuntimeError("fixture command failed: " + Path(str(argv[0])).name + " " + str(argv[1]))


def prepare():
    report = {"status": "FAIL", "stage": "candidate", "cleanRoomAcceptance": False}
    try:
        shared = helpers()
        git = lambda *args: subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
        assert not git("status", "--porcelain")
        head = git("rev-parse", "HEAD")
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        assert version != OLD["version"]
        root = shared.canonical_temp() / ("launcher-refresh-" + uuid.uuid4().hex[:12])
        assert not root.exists()
        save("ownership.json", {"root": str(root)})
        files = load("launcher_refresh_private_setup", ROOT / "amplifier_web/windows_files.py")
        home = root / "app"
        for path in (root, root / "tools", root / "tool-bin", home, home / "updates", root / "workspace",
                     root / "shared", root / "ownership", root / "uv", root / "git", root / "git/cmd"):
            with files.directory(path, owned_root=root, create=True):
                pass
        uv = shutil.which("uv")
        assert uv
        environment = {**os.environ, "UV_LINK_MODE": "copy"}
        with shared.token_user_default_owner():
            report["stage"] = "earlier-release-file"
            old_wheel = root / f"amplifier_unified-{OLD['version']}-py3-none-any.whl"
            with urllib.request.urlopen(f"{SOURCE}/releases/download/v{OLD['version']}/{old_wheel.name}",
                                        timeout=120) as response:
                raw = response.read(64 * 1024 * 1024)
            assert hashlib.sha256(raw).hexdigest() == OLD["sha256"]
            old_wheel.write_bytes(raw)
            with zipfile.ZipFile(old_wheel) as archive:
                assert json.loads(archive.read("amplifier_web/release-source.json"))["revision"] == OLD["revision"]
            report["stage"] = "candidate-wheel"
            release = load("launcher_refresh_release_source", ROOT / "scripts/release.py")
            release.stage_source(ROOT, "microsoft/amplifier-unified", head, version)
            command([uv, "build", "--wheel", "--out-dir", root / "build"], env=environment, cwd=ROOT, timeout=300)
            built = list((root / "build").glob("*.whl"))
            assert len(built) == 1 and built[0].name == f"amplifier_unified-{version}-py3-none-any.whl"
            report["stage"] = "earlier-launcher-install"
            command([uv, "tool", "install", "--python", sys.executable, "--no-build-package", "amplifier-core",
                     old_wheel],
                    env={**environment, "UV_TOOL_DIR": str(root / "tools"), "UV_TOOL_BIN_DIR": str(root / "tool-bin")},
                    cwd=root, timeout=900)
            report["stage"] = "candidate-generation-install"
            generation = uuid.uuid4().hex
            folder = home / "updates" / "a" / generation
            folder.mkdir(parents=True)
            wheel = folder / built[0].name
            shutil.copyfile(built[0], wheel)
            command([uv, "tool", "install", "--python", sys.executable, "--no-build-package", "amplifier-core", wheel],
                    env={**environment, "UV_TOOL_DIR": str(folder / "tools"), "UV_TOOL_BIN_DIR": str(folder / "bin")},
                    cwd=root, timeout=900)
            # Setup's private helper paths. Only their presence is read here.
            shutil.copyfile(uv, root / "uv/uv.exe")
            shutil.copyfile(shutil.which("git"), root / "git/cmd/git.exe")
        python = folder / "tools/amplifier-unified/Scripts/python.exe"
        assert python.is_file() and (root / "tools/amplifier-unified/Scripts/pythonw.exe").is_file()
        save("prepared.json", {"root": str(root), "generation": generation, "old": OLD,
                               "new": {"version": version, "revision": head, "wheel": str(wheel),
                                       "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}})
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write("REFRESH_PYTHON=" + str(python) + "\n")
        report.update(status="PASS", stage="prepared", earlier=OLD["version"], candidate=version)
    except BaseException as error:
        report["errorType"] = type(error).__name__
    finally:
        save("preparation.json", report)
    return report["status"] == "PASS"


class Service:
    """The task's facts, with recorded stop/start in place of a Scheduled Task."""

    def __init__(self, root, ready):
        self.launcher = root / "tools/amplifier-unified"
        self.tool_path = os.environ["PATH"]
        self.launch_roots = {"UV_TOOL_BIN_DIR": str(root / "tool-bin")}
        self.github_access, self.up, self.results, self.events = "anonymous", True, list(ready), []

    def running(self):
        return self.up

    def inside_host(self):
        return False

    def handoff_in_flight(self):
        return False

    def stop(self):
        self.events.append("stop")
        self.up = False

    def start(self):
        self.events.append("start")
        self.up = True

    async def ready(self, wait):
        # Scripted answers first; after them the app answers when it runs.
        return self.results.pop(0) if self.results else self.up


# Run by the refreshed launcher's own Python: the checks that the task's
# launcher and every Start-menu shortcut make at their next start.
AFTER = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from amplifier_web import __version__, windows_consumer, windows_service
root = Path(sys.argv[1])
python, target = windows_service._launch_python(SimpleNamespace(
    python=str(Path(sys.prefix) / "Scripts/pythonw.exe"), data_dir=str(root / "app")))
_, receipt = windows_consumer.installation(root)
proof = windows_consumer.delivery_proof(root / "app")
print(json.dumps({"launcher": __version__, "starts": Path(python).name, "generation": target["generation"],
                  "receipt": receipt["version"], "proof": proof is not None}))
'''


def run():
    report = {"status": "FAIL", "stage": "imports", "cleanRoomAcceptance": False, "scheduledTask": "stand-in",
              "checks": []}
    def need(condition, label):
        report["stage"] = label
        assert condition, label
        report["checks"].append(label)
    try:
        prepared = json.loads((evidence() / "prepared.json").read_text())
        root, new, old = Path(prepared["root"]), prepared["new"], prepared["old"]
        home, launcher = root / "app", root / "tools/amplifier-unified"
        os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(root / "shared"),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"))
        from amplifier_web import app_component_graph as components, application_generations as generations
        from amplifier_web import windows_launcher_refresh as refresher
        from amplifier_web.deployment import write_private
        from amplifier_web.updates import process
        prefix = Path(sys.prefix)
        folder = generations.directory(home, new["revision"], prepared["generation"])
        need(refresher._same(prefix, folder / "tools/amplifier-unified"), "this is the generation's Python")
        with helpers().token_user_default_owner():
            artifact = {"url": f"{SOURCE}/releases/download/v{new['version']}/{Path(new['wheel']).name}",
                        "sha256": new["sha256"], "path": new["wheel"]}
            graph = asyncio.run(components.read_graph(process, sys.executable, artifact=artifact))
            app = next(row for row in graph if row["name"] == "amplifier-unified")
            need(app["revision"] == new["revision"] and app["version"] == new["version"], "candidate identity")
            validated = {"revision": new["revision"], "version": new["version"], "generation": prepared["generation"],
                         "attemptId": uuid.uuid4().hex, "extras": [], "componentGraph": graph,
                         "componentDigest": components.digest(graph), "artifact": artifact,
                         "installation": generations.bootstrap_identity(prefix)}
            (folder / "validated.json").write_text(json.dumps(validated))
            (folder / "components.txt").write_text(components.requirements(graph), encoding="utf-8")
            generations.promote(home, validated, launcher)
            earlier = refresher.launcher_facts(launcher)
            need((earlier["version"], earlier["revision"]) == (old["version"], old["revision"]), "earlier launcher identity")
            old_wheel = root / f"amplifier_unified-{old['version']}-py3-none-any.whl"
            write_private(root / "consumer-install.json", json.dumps({
                "schema": 1, "kind": "windows-consumer-preview", "version": old["version"], "revision": old["revision"],
                "artifact": {"url": f"{SOURCE}/releases/download/v{old['version']}/{old_wheel.name}",
                             "sha256": old["sha256"], "path": str(old_wheel)},
                "graph": [], "uv": str(root / "uv/uv.exe"), "git": str(root / "git/cmd/git.exe")}), owned_root=root)
            pointer = home / "updates/application-active.json"
            entry = root / "tool-bin/amplifier-unified.exe"
            before = {"pointer": pointer.read_bytes(), "entry": entry.read_bytes(),
                      "receipt": (root / "consumer-install.json").read_bytes()}

            def untouched(label):
                need(pointer.read_bytes() == before["pointer"], label + ": launch record unchanged")
                need(refresher.launcher_facts(launcher) == earlier, label + ": earlier launcher in place")
                need(entry.read_bytes() == before["entry"], label + ": start program unchanged")
                need((root / "consumer-install.json").read_bytes() == before["receipt"], label + ": setup record unchanged")
                need(not list(root.glob("launcher-" + old["version"] + "-*")), label + ": previous launcher is not set aside")
                need(not (root / "consumer-qualified.json").exists(), label + ": no new setup proof")
                need(not (root / Path(new["wheel"]).name).exists(), label + ": no copied release file")
                need(not list(root.glob("launcher-not-used*")), label + ": no unused launcher folder")
                need(refresher.unfinished(home) is None, label + ": nothing is left to recover")
                version = subprocess.run([str(launcher / "Scripts/python.exe"), "-I", "-c",
                                          "import amplifier_web; print(amplifier_web.__version__)"],
                                         capture_output=True, text=True, timeout=120)
                need(version.returncode == 0 and version.stdout.strip() == old["version"], label + ": earlier launcher runs")

            def refresh(service, **options):
                started = time.monotonic()
                result = asyncio.run(refresher.refresh(service, home, wait=1, **options))
                report.setdefault("seconds", []).append(round(time.monotonic() - started))
                return result

            service = Service(root, [])
            result = refresh(service, check=True)
            need(result["ok"] and result["action"] == "refresh" and not result["changed"] and service.events == [],
                 "check reports a refresh and changes nothing")
            untouched("check")

            async def refused(*args, **options):
                if [str(item) for item in args[1:3]] == ["tool", "install"]:
                    raise RuntimeError("fixture: installation refused")
                return await process(*args, **options)
            service = Service(root, [])
            result = refresh(service, process=refused)
            need(not result["ok"] and result["state"] == "rolled-back" and result["running"]
                 and result["step"] == "installing the launcher" and service.events == ["stop", "start"],
                 "a failed installation is rolled back")
            untouched("failed installation")

            # The real installation succeeds, then the app does not become ready.
            service = Service(root, [False, False])
            result = refresh(service)
            need(not result["ok"] and result["state"] == "rolled-back" and result["running"]
                 and result["step"] == "starting Amplifier"
                 and service.events == ["stop", "start", "stop", "start"],
                 "an app that is not ready is rolled back")
            untouched("not ready")

            # The command itself is ended after the real installation and the
            # rebind: nothing of its own rollback runs. The next run recovers.
            class Ended(BaseException):
                pass

            async def ended_at_audit(*args, **options):
                if "amplifier_web.windows_consumer" in [str(item) for item in args]:
                    raise Ended()
                return await process(*args, **options)

            async def no_rollback(*args, **options):
                raise Ended()
            undo, service = refresher._undo, Service(root, [])
            refresher._undo = no_rollback
            try:
                refresh(service, process=ended_at_audit)
                need(False, "the ended run must not return")
            except Ended:
                pass
            finally:
                refresher._undo = undo
            journal = refresher.unfinished(home)
            need(journal is not None and Path(journal["previous"]).is_dir() and not service.up,
                 "an ended run leaves its saved record and the previous launcher")
            need(generations.active(home)["bootstrap"] != earlier["identity"]
                 and refresher.launcher_facts(launcher)["revision"] == new["revision"],
                 "the ended run had installed and bound the new launcher")
            result = refresh(service, check=True)
            need(result["action"] == "recover" and not service.up, "check sees the unfinished refresh and changes nothing")
            result = refresh(service)
            need(result["ok"] and result["action"] == "recover" and result["state"] == "rolled-back"
                 and result["running"] and service.events == ["stop", "start"],
                 "the next run puts the previous launcher back")
            untouched("ended run")

            service = Service(root, [])
            result = refresh(service)
            need(result["ok"] and result["changed"] and result["running"] and service.events == ["stop", "start"],
                 "refresh succeeds")
            fresh = refresher.launcher_facts(launcher)
            need((fresh["version"], fresh["revision"]) == (new["version"], new["revision"]),
                 "launcher has the app's version")
            need(fresh["identity"]["prefix"] == earlier["identity"]["prefix"], "launcher is at the same path")
            need(generations.active(home)["bootstrap"] == fresh["identity"], "launch record names the new launcher")
            previous = Path(result["previousLauncher"])
            need(generations.bootstrap_identity(previous)["digest"] == earlier["identity"]["digest"],
                 "previous launcher is kept")
            after = subprocess.run([str(launcher / "Scripts/python.exe"), "-I", "-c", AFTER, str(root)],
                                   capture_output=True, text=True, timeout=300)
            if after.returncode:
                print(after.stderr[-3000:], flush=True)
            need(after.returncode == 0, "new launcher accepts the app and setup's records")
            facts = json.loads(after.stdout.strip().splitlines()[-1])
            need(facts == {"launcher": new["version"], "starts": "pythonw.exe", "generation": prepared["generation"],
                           "receipt": new["version"], "proof": True}, "new launcher starts the active app version")
            service = Service(root, [])
            result = refresh(service)
            need(result["ok"] and result["action"] == "none" and service.events == [], "a second run does nothing")
            # A later update that fails must still roll back: its saved launch
            # record now names the new launcher.
            current = generations.active(home)
            promoted = generations.promote(home, validated, launcher)
            generations.restore(home, promoted, current, launcher, expected_bootstrap=fresh["identity"])
            need(generations.active(home) == current, "a later update can still roll back")
        report.update(status="PASS", stage="complete", earlier=old["version"], candidate=new["version"])
    except BaseException as error:
        report["errorType"] = type(error).__name__
    finally:
        save("result.json", report)
        print(json.dumps(report), flush=True)
    return report["status"] == "PASS"


def cleanup():
    owner = evidence() / "ownership.json"
    if not owner.exists():
        return True
    root = Path(json.loads(owner.read_text())["root"])
    assert root.parent == helpers().canonical_temp() and root.name.startswith("launcher-refresh-")
    shutil.rmtree("\\\\?\\" + str(root), ignore_errors=True)
    save("cleanup.json", {"removed": not root.exists()})
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "run", "cleanup"))
    require_ci()
    raise SystemExit(0 if globals()[parser.parse_args().operation]() else 1)
