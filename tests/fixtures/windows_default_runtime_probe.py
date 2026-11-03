"""CI-only cold default-command check; not browser/default-bundle acceptance.

Run with a noneditable candidate host Python and its exact wheel. No worker
project, lock, venv, uv process or prepare_project double is supplied. Network
resolution/builds from declared sources are allowed in this GitHub setup check;
only the conversation provider is scripted. Private logs stay under --root.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import struct
import sys
import tomllib
import types
import zipfile


APP_FILES = ("__init__.py", "runtime.py", "runtime_worker.py", "runtime_bootstrap.py",
             "runtime_environment.py", "windows_jobs.py", "windows_launch_guard.py")
IMPORTS = {
    "amplifier_core": "amplifier-core",
    "amplifier_foundation": "amplifier-foundation",
    "amplifier_module_loop_live": "amplifier-module-loop-live",
}
CHECKS = ("installedHost", "coldDefaultCommand", "workerIdentity", "scriptedTurn",
          "workerProvenance", "ownedJobCleanup")
CHECKPOINTS = frozenset({
    "runtime_preflight", "input_paths", "private_root", "private_log", "fixture_home", "journey_imports",
    "installed_import", "installed_distribution", "installed_direct_url", "installed_origin",
    "installed_members", "installed_manifest", "journey", "complete",
    "outer_run", "outer_close", "parent_output_json", "parent_output_schema",
    "inner_report_schema",
})
ERROR_CLASSES = {kind: kind.__name__ for kind in (
    AssertionError, OSError, PermissionError, FileNotFoundError, NotADirectoryError,
    IsADirectoryError, ImportError, ModuleNotFoundError, ValueError, TypeError,
    AttributeError, KeyError, RuntimeError, NotImplementedError, TimeoutError,
    UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError,
)}
CODE_LABELS = {
    "cold_main": (__name__, "main"),
    "cold_journey": (__name__, "journey"),
    "cold_installed_host": (__name__, "installed_host"),
    "cold_outer": (__name__, "run_owned_probe"),
    "private_directory": ("amplifier_web.windows_files", "PinnedDirectory.__init__"),
    "private_acl": ("amplifier_web.windows_files", "_API.validate_acl"),
    "private_file_info": ("amplifier_web.windows_files", "_API.info"),
}
# Retain the outer lifeline and its scratch identity when the primitive cannot
# confirm cleanup. A public FAIL record never authorizes a retry or deletion.
FAILED_OUTER_OWNERS = []
PROVIDER = r'''
import hashlib, importlib, importlib.metadata as metadata, json, os, sys
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
__amplifier_module_type__ = "provider"
class Provider:
    name = "fixture"
    def __init__(self, config=None, *, coordinator=None):
        # Metadata is read before mount, without a coordinator or live config.
        # Only mount binds the actual worker for the provenance-producing turn.
        self.coordinator, self.config = coordinator, config or {}
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Scripted fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        if (self.coordinator.session_id == "test-session"
                or not any(m.role == "user" and "DEFAULT_COMMAND_TURN" in str(m.content) for m in request.messages)):
            return ChatResponse(content=[TextBlock(text="Fixture validation")])
        config = self.config
        prefix = Path(config["project"]) / ".venv"
        assert Path(sys.prefix).resolve() == prefix.resolve()
        assert Path(sys.executable).samefile(prefix / "Scripts/python.exe")
        app = importlib.import_module("amplifier_web")
        app_dir = Path(app.__file__).resolve().parent
        assert app_dir == Path(config["app"]).resolve()
        assert Path(sys.modules["__main__"].__file__).resolve() == app_dir / "runtime_worker.py"
        # Actual shipped bootstrap exposes app packages, NOT the host's whole
        # site-packages directory or distribution metadata to worker resolution.
        assert app_dir.parent not in {Path(p).resolve() for p in sys.path if p}
        try: metadata.distribution("amplifier-unified")
        except metadata.PackageNotFoundError: pass
        else: raise AssertionError("Host distribution metadata leaked into worker")
        for name, expected in config["appHashes"].items():
            assert hashlib.sha256((app_dir / name).read_bytes()).hexdigest() == expected
        imports = {}
        for package, distribution in config["imports"].items():
            module = importlib.import_module(package)
            origin = Path(module.__file__).resolve()
            dist = metadata.distribution(distribution)
            assert origin.is_relative_to(prefix.resolve())
            assert origin in {Path(dist.locate_file(f)).resolve() for f in dist.files or ()}
            direct = json.loads(dist.read_text("direct_url.json") or "{}")
            assert not direct.get("dir_info", {}).get("editable")
            imports[package] = {"version":dist.version,
                "commit":direct.get("vcs_info", {}).get("commit_id"),
                "sha256":hashlib.sha256(origin.read_bytes()).hexdigest()}
        proof = {"pid":os.getpid(), "imports":imports, "appHashes":config["appHashes"]}
        # Exactly one real request, separate from Core's validation request.
        with Path(config["proof"]).open("x", encoding="utf-8") as stream:
            json.dump(proof, stream)
        return ChatResponse(content=[TextBlock(text="Default command caf\u00e9 verified")])
async def mount(coordinator, config=None):
    await coordinator.mount("providers", Provider(config, coordinator=coordinator), name="fixture")
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def empty_inner_report():
    return {"schema": 1, "status": "FAIL", "stage": "preflight",
            "checks": dict.fromkeys(CHECKS, False), "wheelSha256": None,
            "manifestSha256": None, "lockSha256": None, "appHashes": {}, "workerImports": {}}


def checkpoint(report, name):
    assert name in CHECKPOINTS
    report["diagnostic"] = {"checkpoint": name, "errors": []}


def failure_facts(error, name):
    """Fixed labels only; no exception text, filenames, symbols or frame locals."""
    try:
        return _failure_facts(error, name)
    except BaseException:
        # A diagnostic fault cannot skip the caller's retained cleanup/report.
        # Original outcome remains FAIL; an unknown class is not a cause claim.
        return {"checkpoint": name, "errors": [{"class": "other", "frames": []}]}


def _failure_facts(error, name):
    assert name in CHECKPOINTS
    codes = {}
    for label, (module_name, qualname) in CODE_LABELS.items():
        value = sys.modules.get(module_name)
        for part in qualname.split("."):
            value = getattr(value, part, None) if value is not None else None
        code = getattr(value, "__code__", None)
        if isinstance(code, types.CodeType):
            # Code equality is structural. Retain each exact loaded object so
            # an equal-but-distinct clone cannot claim its diagnostic label.
            codes[id(code)] = (code, label)
    rows, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(rows) < 4:
        seen.add(id(error))
        kind = type(error)
        row = {"class": ERROR_CLASSES.get(kind, "other"), "frames": []}
        if kind in ERROR_CLASSES and isinstance(error, OSError):
            for key in ("errno", "winerror"):
                number = getattr(error, key, None)
                if type(number) is int and -(2**31) <= number < 2**32:
                    row[key] = number
        tb, scanned = BaseException.__getattribute__(error, "__traceback__"), 0
        while tb is not None and scanned < 128:
            scanned += 1
            code = tb.tb_frame.f_code
            known = codes.get(id(code))
            if known is not None and known[0] is code:
                row["frames"].append({"source": known[1],
                    "offset": max(0, min(tb.tb_lineno - code.co_firstlineno, 10**6))})
                row["frames"] = row["frames"][-5:]
            tb = tb.tb_next
        rows.append(row)
        cause = BaseException.__getattribute__(error, "__cause__")
        error = cause if cause is not None else (
            None if BaseException.__getattribute__(error, "__suppress_context__")
            else BaseException.__getattribute__(error, "__context__"))
    return {"checkpoint": name, "errors": rows}


def validate_diagnostic(value):
    assert type(value) is dict and set(value) == {"checkpoint", "errors"}
    assert type(value["checkpoint"]) is str and value["checkpoint"] in CHECKPOINTS
    assert type(value["errors"]) is list and len(value["errors"]) <= 4
    for row in value["errors"]:
        assert type(row) is dict and {"class", "frames"} <= set(row) <= {"class", "frames", "errno", "winerror"}
        assert type(row["class"]) is str and row["class"] in {*ERROR_CLASSES.values(), "other"}
        for key in ("errno", "winerror"):
            if key in row:
                assert type(row[key]) is int and -(2**31) <= row[key] < 2**32
        assert type(row["frames"]) is list and len(row["frames"]) <= 5
        for frame in row["frames"]:
            assert type(frame) is dict and set(frame) == {"source", "offset"}
            assert type(frame["source"]) is str and frame["source"] in CODE_LABELS
            assert type(frame["offset"]) is int and 0 <= frame["offset"] <= 10**6


def parse_inner_output(raw, *, outer_failure=None):
    """An empty/malformed child result is not evidence of inner preflight."""
    try:
        report = json.loads(raw)
    except Exception as error:
        report = empty_inner_report()
        report["diagnostic"] = outer_failure or failure_facts(error, "parent_output_json")
        return report
    try:
        validate_inner_report(report)
    except Exception as error:
        report = empty_inner_report()
        report["diagnostic"] = failure_facts(error, "parent_output_schema")
    return report


def validate_inner_report(report):
    """Only bounded fixed fields may leave the private test directory."""
    assert type(report) is dict
    assert set(report) - {"diagnostic"} == set(empty_inner_report())
    if "diagnostic" in report:
        validate_diagnostic(report["diagnostic"])
    assert report["schema"] == 1 and report["status"] in {"PASS", "FAIL"}
    assert report["stage"] in {"preflight", "fixture", "preparation", "turn", "stop", "complete"}
    assert set(report["checks"]) == set(CHECKS)
    assert all(type(v) is bool for v in report["checks"].values())
    for name in ("wheelSha256", "manifestSha256", "lockSha256"):
        assert report[name] is None or re.fullmatch(r"[0-9a-f]{64}", report[name])
    assert set(report["appHashes"]) <= set(APP_FILES)
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in report["appHashes"].values())
    assert set(report["workerImports"]) <= set(IMPORTS)
    for row in report["workerImports"].values():
        assert set(row) == {"version", "commit", "sha256"}
        assert re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", row["version"])
        assert row["commit"] is None or re.fullmatch(r"[0-9a-f]{40,64}", row["commit"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
    if report["status"] == "PASS":
        assert "diagnostic" not in report or not report["diagnostic"]["errors"]
        assert all(report["checks"].values()) and report["stage"] == "complete"
        assert all(report[name] is not None for name in ("wheelSha256", "manifestSha256", "lockSha256"))
        assert set(report["appHashes"]) == set(APP_FILES)
        assert set(report["workerImports"]) == set(IMPORTS)


def validate_report(report):
    """The public result requires parent-observed cleanup, not child testimony."""
    assert set(report) - {"diagnostic"} == set(empty_inner_report()) | {"outerJob"}
    assert report["schema"] == 2
    validate_inner_report({key: (1 if key == "schema" else report[key])
                           for key in report if key != "outerJob"})
    outer = report["outerJob"]
    assert set(outer) == {"status", "timedOut", "activeProcesses", "treeEmpty",
                          "ioFinalized", "outputDiscarded"}
    assert outer["status"] in {"confirmed", "unknown"}
    assert all(type(outer[key]) is bool for key in
               ("timedOut", "treeEmpty", "ioFinalized", "outputDiscarded"))
    assert (outer["activeProcesses"] is None or
            type(outer["activeProcesses"]) is int and 0 <= outer["activeProcesses"] <= 10**9)
    if outer["status"] == "confirmed":
        assert outer["activeProcesses"] == 0 and outer["treeEmpty"] and outer["ioFinalized"]
    if report["status"] == "PASS":
        assert outer["status"] == "confirmed" and not outer["timedOut"] and not outer["outputDiscarded"]


async def run_owned_probe(argv, *, cwd, env, scratch, timeout=1200, output_limit=64 * 1024):
    """Own the redirector AND actual host/inner jobs until bounded native cleanup.

    No subprocess.run/communicate/PID signals. stdout is a bounded single JSON
    result (overflow fails); stderr is continuously drained with a bounded tail.
    WindowsOwnedProcess owns its finite launch and close deadlines. Unknown
    cleanup keeps its lifeline/scratch identity, not a claim that effects ended.
    """
    from amplifier_web.windows_jobs import CleanupError, WindowsOwnedProcess
    owner = result = group = None
    tasks = []
    stdout, stderr = bytearray(), bytearray()
    timed_out = succeeded = cleanup_confirmed = False
    failure = None

    async def read_stdout():
        while chunk := await owner.stdout.read(4096):
            room = output_limit - len(stdout)
            stdout.extend(chunk[:room])
            if len(chunk) > room:
                raise ValueError("outer probe result exceeded its bound")

    async def read_stderr():
        while chunk := await owner.stderr.read(4096):
            stderr.extend(chunk)
            del stderr[:-output_limit]

    try:
        try:
            owner = await WindowsOwnedProcess.start(
                argv, cwd=cwd, env=env, trusted_python=sys._base_executable,
                cleanup_timeout=5)
        except CleanupError as error:
            owner = error.owner  # start may fail while retaining native handles
            raise
        owner.stdin.close()
        tasks = [asyncio.create_task(owner.wait()), asyncio.create_task(read_stdout()),
                 asyncio.create_task(read_stderr())]
        group = asyncio.gather(*tasks)
        async with asyncio.timeout(timeout):
            result, _, _ = await asyncio.shield(group)
        succeeded = result.payload_returncode == 0 and not result.output_discarded
    except TimeoutError as error:
        timed_out = True
        failure = failure_facts(error, "outer_run")
    except Exception as error:
        failure = failure_facts(error, "outer_run")
    finally:
        if owner is not None:
            try:
                # CleanupError from start already records a settled, failed
                # finalizer. Do not retry that native operation.
                if owner.state != "cleanup_failed":
                    result = await owner.close()
                    cleanup_confirmed = (result.active_processes == 0
                                         and owner.tree_empty and owner.io_finalized)
            except Exception as error:
                cleanup_confirmed = False
                failure = failure_facts(error, "outer_close")
            finally:
                if not cleanup_confirmed:
                    FAILED_OUTER_OWNERS.append({"owner": owner, "scratch": scratch})
        # No second unbounded EOF/communicate after deadline. Pending reads and
        # observers are settled only AFTER the retained native close finishes.
        for task in tasks:
            if not task.done():
                task.cancel()
        if group is not None:
            await asyncio.gather(group, *tasks, return_exceptions=True)
    outer = {"status": "confirmed" if cleanup_confirmed else "unknown",
             "timedOut": timed_out,
             "activeProcesses": result.active_processes if result is not None else None,
             "treeEmpty": bool(owner is not None and owner.tree_empty),
             "ioFinalized": bool(owner is not None and owner.io_finalized),
             "outputDiscarded": bool(owner is not None and owner.output_discarded)}
    return {"stdout": bytes(stdout), "stderr": bytes(stderr), "outerJob": outer,
            "succeeded": succeeded and cleanup_confirmed and not timed_out and not outer["outputDiscarded"],
            "tasksSettled": all(task.done() for task in tasks), "failure": failure}


def installed_host(wheel, report=None):
    """Bind used app/manifest bytes to the installed candidate wheel, not checkout."""
    if report is not None:
        checkpoint(report, "installed_import")
    import amplifier_web
    app = Path(amplifier_web.__file__).resolve().parent
    if report is not None:
        checkpoint(report, "installed_distribution")
    dist = metadata.distribution("amplifier-unified")
    if report is not None:
        checkpoint(report, "installed_direct_url")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert direct.get("url") == wheel.resolve().as_uri()
    assert not direct.get("dir_info", {}).get("editable")
    if report is not None:
        checkpoint(report, "installed_origin")
    assert app.is_relative_to(Path(sys.prefix).resolve())
    hashes = {}
    if report is not None:
        checkpoint(report, "installed_members")
    with zipfile.ZipFile(wheel) as archive:
        for name in (*APP_FILES, "runtime_deps/pyproject.toml", "runtime_deps/compatibility.txt"):
            raw = archive.read("amplifier_web/" + name)
            assert (app / name).read_bytes() == raw
            assert Path(dist.locate_file("amplifier_web/" + name)).resolve() == app / name
            if name in APP_FILES:
                hashes[name] = digest(raw)
    return app, hashes


def verify_worker_lock(project, imports):
    """Bind the three observed imports to the actual uv-installed resolution."""
    lock = tomllib.loads((project / "uv.lock").read_text(encoding="utf-8"))
    for package, distribution in IMPORTS.items():
        rows = [row for row in lock["package"] if row["name"] == distribution]
        assert len(rows) == 1 and rows[0]["version"] == imports[package]["version"]
        source = rows[0]["source"]
        if "git" in source:
            assert imports[package]["commit"] == source["git"].rsplit("#", 1)[1]
        else:
            assert "registry" in source and imports[package]["commit"] is None


def prepare_fixture_home(root):
    """Create and bind isolated homes BEFORE imports can evaluate Path.home()."""
    from amplifier_web.windows_files import directory
    home, shared, workspace = root / "app", root / "shared", root / "workspace"
    user = root / "user"
    for path in (home, shared, workspace, user, user / "roaming", user / "local",
                 user / "cache", root / "ownership", root / "captures"):
        with directory(path, owned_root=root, create=True):
            pass
    # Do not inherit a real profile to fix missing-home imports. Each consumer
    # sees only privately created scratch; the worker project stays absent.
    os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(shared),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "captures"),
        USERPROFILE=str(user), HOME=str(user),
        APPDATA=str(user / "roaming"), LOCALAPPDATA=str(user / "local"),
        XDG_CONFIG_HOME=str(user), XDG_CACHE_HOME=str(user / "cache"))


async def journey(root, wheel, report):
    checkpoint(report, "fixture_home")
    prepare_fixture_home(root)
    checkpoint(report, "journey_imports")
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.deployment import write_private
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_environment import manifest_path, project_path
    from amplifier_web.runtime_profiles import ensure
    from amplifier_web.service import AppService
    from amplifier_web.session_files import sessions_dir
    from amplifier_web.windows_files import directory
    from amplifier_foundation.session.history import SessionHistoryStore

    app, hashes = installed_host(wheel, report)
    checkpoint(report, "installed_manifest")
    report.update(wheelSha256=digest(wheel.read_bytes()), appHashes=hashes,
                  manifestSha256=digest(manifest_path().read_bytes()))
    report["checks"]["installedHost"] = True
    checkpoint(report, "journey")
    report["stage"] = "fixture"
    home, shared, workspace = root / "app", root / "shared", root / "workspace"
    settings = {"bundle": {"app": []}, "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)}}
    write_private(shared / "settings.yaml", json.dumps(settings), owned_root=shared)
    provider = root / "provider/amplifier_module_provider_default_fixture"
    with directory(provider, owned_root=root, create=True):
        pass
    write_private(provider / "__init__.py", PROVIDER, owned_root=root)
    project = project_path(home)
    assert not project.exists()  # No pre-resolved project or installed worker graph.
    bundle = root / "bundle.yaml"
    write_private(bundle, json.dumps({
        "bundle": {"name": "default-command-fixture"},
        "session": {"orchestrator": {"module": "loop-live", "config": {
            "use_streaming": False, "min_delay_between_calls_ms": 0}},
            "context": {"module": "context-simple",
                "source": "git+https://github.com/microsoft/amplifier-module-context-simple@main"}},
        "providers": [{"module": "provider-default-fixture", "source": str(provider.parent),
            "config": {"project": str(project), "app": str(app), "appHashes": hashes,
                       "imports": IMPORTS, "proof": str(root / "worker-proof.json")}}],
    }), owned_root=root)
    runtime = RuntimeManager(startup_timeout=900, retention={"prewarm_on_select": False})
    assert runtime.command is None
    runtime.home = home
    service = AppService(home, runtime=runtime, workspace=workspace)
    runtime.app_bridge = service.app_bridge
    owners = []
    try:
        await service.dispatch("session.create", {"bundle": str(bundle)})
        session = service._session()
        sid = session["id"]
        # Exercise the actual legacy no-generation ensure branch, not a stub.
        assert await ensure(home, None, session) is None
        report["stage"] = "preparation"
        await runtime.start(session, service.on_runtime_event)
        row = runtime.workers[sid]
        owner = row["windows_owner"]
        owners.append(owner)
        assert row["ready"].done() and row["ready"].exception() is None
        assert row["generation"] is None and row["source_generation"] is None
        # Calling the unchanged builder again must identify the very same
        # project/manifest/worker. It is not installed as a replacement command.
        command = runtime._command()
        assert command[1] == "run" and command[-1] == str(app / "runtime_worker.py")
        assert Path(RuntimeManager.project_path(command)) == project
        assert command[-3:-1] == ["3.13", "python"]
        assert (project / "pyproject.toml").read_bytes() == manifest_path().read_bytes()
        assert (project / ".venv/Scripts/python.exe").is_file()
        assert (project / "uv.lock").is_file()
        report["lockSha256"] = digest((project / "uv.lock").read_bytes())
        report["checks"]["coldDefaultCommand"] = True
        assert row["native_worker_pid"] == row["process"].pid
        assert row["process"].pid != owner.job.identity.guard_pid
        owner.job.verify_member(row["process"].pid, owner.created)
        report["checks"]["workerIdentity"] = True
        report["stage"] = "turn"
        await service.dispatch("conversation.send", {"sessionId": sid, "text": "DEFAULT_COMMAND_TURN"},
                               command_id="default-command-input")
        history_store = SessionHistoryStore(sessions_dir(workspace) / sid)
        async with asyncio.timeout(90):
            while True:
                assert service._session(sid).get("status") != "error"
                # The first idle/parked notification may precede delivery of
                # this input. Require this turn's actual saved response too.
                if row.get("parked"):
                    history = history_store.load_messages()
                    if any(m["role"] == "assistant" and "Default command caf\u00e9 verified" in str(m.get("content"))
                           for m in history):
                        break
                await asyncio.sleep(.05)
        report["checks"]["scriptedTurn"] = True
        proof = json.loads((root / "worker-proof.json").read_text(encoding="utf-8"))
        assert set(proof) == {"pid", "imports", "appHashes"} and proof["pid"] == row["process"].pid
        assert proof["appHashes"] == hashes and set(proof["imports"]) == set(IMPORTS)
        verify_worker_lock(project, proof["imports"])
        report["workerImports"] = proof["imports"]
        report["checks"]["workerProvenance"] = True
        report["stage"] = "stop"
        await runtime.stop(sid)
        assert owner.result is not None and owner.result.active_processes == 0
        assert owner.job.tree_empty and owner.job.io_finalized
        assert sid not in runtime.workers
        assert digest((project / "uv.lock").read_bytes()) == report["lockSha256"]
        report["checks"]["ownedJobCleanup"] = True
    finally:
        for row in runtime.workers.values():
            if row.get("windows_owner") and row["windows_owner"] not in owners:
                owners.append(row["windows_owner"])
        await service.close()
        # close() gathers failures; only the retained owners' proof counts.
        assert not runtime.workers and not runtime._preparations
        assert all(o.result is not None and o.result.active_processes == 0
                   and o.job.tree_empty and o.job.io_finalized for o in owners)
    report.update(status="PASS", stage="complete")
    checkpoint(report, "complete")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    report = empty_inner_report()
    checkpoint(report, "runtime_preflight")
    try:
        assert sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
        assert sys.version_info[:2] == (3, 13) and struct.calcsize("P") == 8
        checkpoint(report, "input_paths")
        assert not args.root.exists() and args.wheel.is_file()
        checkpoint(report, "private_root")
        from amplifier_web.windows_files import directory
        with directory(args.root, owned_root=args.root, create=True):
            pass
        checkpoint(report, "private_log")
        with (args.root / "private-probe.log").open("x", encoding="utf-8") as logs:
            with redirect_stdout(logs), redirect_stderr(logs):
                asyncio.run(journey(args.root, args.wheel, report))
    except Exception as error:
        report["status"] = "FAIL"  # Never publish exception text/paths/private logs.
        report["diagnostic"] = failure_facts(error, report["diagnostic"]["checkpoint"])
    try:
        validate_inner_report(report)
    except Exception as error:
        # Do not emit even a partially valid arbitrary worker-supplied record.
        report = empty_inner_report()
        report["diagnostic"] = failure_facts(error, "inner_report_schema")
        validate_inner_report(report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
