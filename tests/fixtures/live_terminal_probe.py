"""Real HTTP/SSE clients and worker, with a deterministic credential-free provider."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def main():
    root = Path(sys.argv[1]).resolve()
    workspace = root / "workspace"
    provider = root / "provider" / "amplifier_module_provider_fixture"
    workspace.mkdir(parents=True)
    provider.mkdir(parents=True)
    gates = root / "gates"
    gates.mkdir()
    provider.joinpath("__init__.py").write_text(
        f"GATES = {str(gates)!r}\n" + '''import asyncio
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
class FixtureProvider:
    name = "fixture"
    def get_info(self): return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        users = [str(m.content) for m in request.messages if m.role == "user"]
        # Host context may follow the input as an ephemeral user-role message.
        # Select the newest owned fixture marker, not that context trailer.
        marker = next((key for text in reversed(users)
                       for key in ("terminal-first", "terminal-detach", "terminal-stop")
                       if key in text), "other")
        with (Path(GATES) / (marker + ".calls")).open("a") as stream:
            stream.write("call\\n")
        if marker in {"terminal-detach", "terminal-stop"}:
            try:
                while not (Path(GATES) / (marker + ".release")).exists():
                    await asyncio.sleep(.02)
            except asyncio.CancelledError:
                (Path(GATES) / (marker + ".cancelled")).touch()
                raise
        return ChatResponse(content=[TextBlock(text="completed " + marker)])
async def mount(coordinator, config=None): await coordinator.mount("providers", FixtureProvider(), name="fixture")
''')
    from amplifier_module_context_simple import __file__ as context_module

    bundle = root / "fixture.md"
    bundle.write_text(f"""---
bundle:
  name: terminal-fixture
  version: 0.0.1
session:
  orchestrator:
    module: loop-live
  context:
    module: context-simple
    source: {Path(context_module).parent.parent}
providers:
  - module: provider-fixture
    source: {provider.parent}
---
Reply with the fixture response.
""")
    os.environ.update(
        AMPLIFIER_HOME=str(root / "amplifier-home"),
        AMPLIFIER_WEB_HOME=str(root / "home"),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"),
    )
    # This terminal acceptance exercises local runtime semantics, not fetching
    # the ordinary optional community behaviors into an empty test home.
    from amplifier_web.builtin_behaviors import IMAGEGEN_BEHAVIOR_URI
    settings = root / "amplifier-home" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "bundle": {"active": str(bundle), "app": []},
        "web_bundles": {"excluded": [IMAGEGEN_BEHAVIOR_URI]},
    }))
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient

    app = await create_app(root / "app", workspace=str(workspace), voice=False,
                           background_updates=False, preload_providers=False)
    service = app["service"]
    service.runtime.command = [sys.executable, str(ROOT / "amplifier_web/runtime_worker.py")]
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    latest = {}
    tasks = []

    async def follow(client, sid):
        async for snapshot in client.snapshots(sid, reconnect=False):
            latest[client.client_id] = snapshot

    async def until(predicate):
        try:
            async with asyncio.timeout(45):
                while not predicate():
                    for task in tasks:
                        if task.done() and not task.cancelled():
                            task.result()
                    await asyncio.sleep(.02)
        except TimeoutError:
            # Retain a small, credential-free explanation instead of losing
            # preparation/transport evidence behind a generic polling timeout.
            print(json.dumps({"terminal_timeout": {
                client: {key: snapshot.get('session', {}).get(key)
                         for key in ('status', 'error', 'activity', 'historyError')}
                for client, snapshot in latest.items()
            }, "workerCount": len(service.runtime.workers)}), file=sys.stderr)
            raise

    def complete(client, marker):
        return any(m.get("text") == "completed " + marker for m in
                   latest.get(client, {}).get("session", {}).get("messages", []))

    try:
        async with SessionClient(url, app["control_token"], "terminal-b") as b:
            async with SessionClient(url, app["control_token"], "terminal-a") as a:
                created = await a.create_session({"title": "Terminal lifecycle", "bundle": str(bundle),
                                                   "workspace": str(workspace)}, command_id="create-terminal")
                sid = created["state"]["selectedSessionId"]
                tasks.extend(asyncio.create_task(follow(client, sid)) for client in (a, b))
                await until(lambda: len(latest) == 2)
                await a.command(sid, "conversation.send", {"text": "terminal-first"}, command_id="first")
                await until(lambda: all(
                    snapshot.get('session', {}).get('status') == 'idle'
                    and any(m.get('role') == 'assistant' for m in snapshot.get('session', {}).get('messages', []))
                    for snapshot in latest.values()))
                assert all(complete(c, "terminal-first") for c in ("terminal-a", "terminal-b")), {
                    "messages": {client: snapshot.get('session', {}).get('messages', []) for client, snapshot in latest.items()},
                    "calls": [path.name for path in gates.glob('*.calls')],
                }
                await until(lambda: all(complete(c, "terminal-first") for c in ("terminal-a", "terminal-b")))
                await until(lambda: latest["terminal-b"]["session"]["status"] == "idle")
                # Idle return plus host control is the activation-race regression path.
                await a.command(sid, "runtime.control", {"operation": "configuration.inspect"}, command_id="inspect")
                await a.command(sid, "conversation.send", {"text": "terminal-detach"}, command_id="detach")
                await until(lambda: (gates / "terminal-detach.calls").exists())
                duplicate = await a.command(sid, "conversation.send", {"text": "terminal-detach"}, command_id="detach")
                assert duplicate["duplicate"] is True
                tasks[0].cancel()
                with suppress(asyncio.CancelledError):
                    await tasks[0]
            # A has exited while the actual worker is still waiting on its provider.
            assert sid in service.runtime.workers
            (gates / "terminal-detach.release").touch()
            # Assistant text can precede the terminal lifecycle event. This
            # next input exercises a new turn, not steering the finishing run.
            await until(lambda: complete("terminal-b", "terminal-detach")
                        and latest["terminal-b"]["session"]["status"] == "idle")
            async with SessionClient(url, app["control_token"], "terminal-a") as a:
                restored = await a.snapshot(sid)
                assert sum(m.get("text") == "terminal-detach" for m in restored["session"]["messages"]) == 1
                assert sum(m.get("text") == "completed terminal-detach" for m in restored["session"]["messages"]) == 1
                assert (gates / "terminal-detach.calls").read_text().splitlines() == ["call"]
                receipt = await a.command(sid, "conversation.send", {"text": "terminal-stop"}, command_id="stop-input")
                admission = {key: receipt.get(key) for key in ("delivery", "steering")}
                assert admission == {"delivery": "accepted", "steering": None}, admission
                await until(lambda: (gates / "terminal-stop.calls").exists())
                await b.command(sid, "conversation.stop", {}, command_id="stop-run")
                await until(lambda: sid not in service.runtime.workers)
                await until(lambda: latest["terminal-b"]["session"]["status"] == "stopped")
                assert (gates / "terminal-stop.cancelled").exists()
                assert not complete("terminal-b", "terminal-stop")
                final = await a.snapshot(sid)
                assert final["session"]["status"] == "stopped"
            print(json.dumps({"two_live_clients": True, "idle_resume": True, "duplicate_runs_once": True,
                              "detach_keeps_work": True, "reconnect_restores_history": True,
                              "explicit_stop_cancels_provider": True}))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()


def _producer_cases():
    return [
        {"slot": 1, "input": {"slot": 1, "body": "INPUT:" + "漢字é𝄞" * 5000 + ":INPUT-END"},
         "output": {"slot": 1, "body": "OUTPUT:" + "漢字é𝄞" * 5000 + ":OUTPUT-END"}},
        {"slot": 2, "input": {"slot": 2, "body": "second input"},
         "output": {"slot": 2, "body": "second output"}},
    ]


def _producer_result_encodings(output):
    """Exact public ToolResult encodings exercised by test_surface_context."""
    from amplifier_core import ToolResult

    result = ToolResult(success=True, output=output)
    return [output, result.model_dump(mode="json"),
            result.model_dump(mode="json", exclude_none=True),
            result.model_dump(mode="json", exclude={"success"}),
            json.loads(result.get_serialized_output())]


def _producer_record(directory, name, value):
    """Exclusive fixture accounting, never a canonical event/transcript writer."""
    path = Path(directory) / (name + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)


_SCHEMA_MODULE = "amplifier_module_provider_regularproducer"
_DIAGNOSTIC_CLASSES = {
    "builtins": {"ModuleNotFoundError", "ImportError", "AttributeError", "TypeError",
                 "ValueError", "RuntimeError", "OSError", "FileNotFoundError",
                 "PermissionError", "AssertionError", "TimeoutError"},
    "asyncio.exceptions": {"CancelledError"},
    "amplifier_web.module_failures": {"ConfiguredModuleError"},
    "amplifier_web.session_client": {"SessionClientError"},
}
_DIAGNOSTIC_LOCATIONS = (
    "amplifier_web/provider_environment.py", "amplifier_web/module_failures.py",
    "amplifier_web/runtime_worker.py", "amplifier_web/runtime_preparation.py",
    "tests/fixtures/live_terminal_probe.py",
)
_STARTUP_PHASES = {"runtime-setup", "bundle-preparation", "session-mount", "worker_startup"}


def _producer_frame_location(filename, line, root):
    """No source text, function name, locals, or arbitrary path components."""
    paths = {str(ROOT / name): "repo/" + name for name in _DIAGNOSTIC_LOCATIONS}
    paths[str(root / "provider-regularproducer" / _SCHEMA_MODULE / "__init__.py")] = "owned/provider"
    paths[str(root / "bootstrap/sitecustomize.py")] = "owned/bootstrap"
    return {"location": paths.get(filename, "other"),
            "line": line if type(line) is int and 0 < line < 1_000_000 else None}


def _producer_exception_shape(error, root):
    cls = type(error)
    allowed = cls.__name__ in _DIAGNOSTIC_CLASSES.get(cls.__module__, set())
    frames, tb, count = [], error.__traceback__, 0
    while tb is not None and count < 64:
        frames.append(_producer_frame_location(tb.tb_frame.f_code.co_filename, tb.tb_lineno, root))
        frames = frames[-20:]
        count += 1
        tb = tb.tb_next
    missing = error.name if type(error) is ModuleNotFoundError else None
    return {"exceptionClass": cls.__name__ if allowed else "other",
            "missingModule": missing if missing in {
                _SCHEMA_MODULE, "live_terminal_probe", "amplifier_core", "amplifier_web"
            } else ("other" if missing is not None else None),
            "frames": frames, "framesCapped": count > 20 or tb is not None}


def _producer_schema_observer(root, original, record):
    """Observe the real worker helper, not a replacement schema or loader."""
    ordinal = 0

    def retain(outcome, *, schema=None, error=None):
        nonlocal ordinal
        ordinal += 1
        # Observation failure must not replace an original return or exception.
        try:
            if ordinal > 4:
                record("overflow", {"status": "overflow"})
                return
            package = root / "provider-regularproducer" / _SCHEMA_MODULE / "__init__.py"
            loaded = sys.modules.get(_SCHEMA_MODULE)
            helper = sys.modules.get("live_terminal_probe")
            row = {
                "status": outcome, "ordinal": ordinal,
                "ownedPackageExists": package.is_file() and not package.is_symlink(),
                "ownedPackageOriginMatches": bool(
                    loaded and getattr(loaded, "__file__", None) == str(package)),
                "fixtureHelperOriginMatches": bool(
                    helper and getattr(helper, "__file__", None) == str(Path(__file__))),
                "schemaHelperOriginMatches": original.__code__.co_filename == str(
                    ROOT / "amplifier_web/provider_environment.py"),
                "expectedParentInSysPath": str(package.parent.parent) in sys.path,
            }
            if error is not None:
                row["exception"] = _producer_exception_shape(error, root)
            else:
                fields = schema.get("fields") if type(schema) is dict else None
                row["schemaShape"] = {
                    "mapping": type(schema) is dict, "fieldsList": type(fields) is list,
                    "fieldCount": min(len(fields), 1024) if type(fields) is list else None,
                    "fieldCountCapped": type(fields) is list and len(fields) > 1024}
            record(f"schema-{ordinal}", row)
        except BaseException:
            # An absent row is an explicit collection gap, never synthetic success.
            pass

    async def observed(module_id, *, config=None, environment=None):
        if module_id != "provider-regularproducer":
            return await original(module_id, config=config, environment=environment)
        try:
            result = await original(module_id, config=config, environment=environment)
        except BaseException as error:
            retain("raised", error=error)
            raise
        retain("returned", schema=result)
        return result

    return observed


def _observe_worker_schema(root):
    from amplifier_web import provider_environment

    directory = root / "schema-diagnostics"
    original = provider_environment.provider_config_schema
    provider_environment.provider_config_schema = _producer_schema_observer(
        root, original, lambda name, row: _producer_record(directory, name, row))
    try:
        _producer_record(directory, "installed", {"status": "installed"})
    except Exception:
        pass  # Missing installation evidence is a gap, not a new startup error.


def _producer_owned_bytes(root, path, limit):
    """Read a bounded regular file via no-follow directory descriptors."""
    import stat

    parts = path.relative_to(root).parts
    if not parts or any(p in {"", ".", ".."} for p in parts):
        raise ValueError("Invalid owned path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(child)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise ValueError("Not a bounded owned regular file")
            with os.fdopen(child, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Owned file grew beyond limit")
            return data
        finally:
            os.close(child)
    finally:
        os.close(fd)


def _producer_startup_diagnostic(root, name):
    """Only a sanitized derivative of this synthetic worker's exact receipt."""
    import re
    from amplifier_web.worker_diagnostics import receipt_path

    if type(name) is not str or not re.fullmatch(r"startup-[a-f0-9]{32}\.log", name):
        return {"status": "invalid-name", "present": None, "name": None}
    result = {"status": "unavailable", "present": None, "name": name}
    try:
        path = receipt_path(name, home=root / "app")
        if path is None:
            return result
        expected = root / "app/logs/workers" / name
        if path != expected:
            return {**result, "status": "refused-path"}
        raw = _producer_owned_bytes(root, path, 60_000)
        result["present"] = True
        # Never copy stderr or best-effort-redacted exception messages. Only the
        # strict header and StartupCapture's structured frame locations qualify.
        header = json.loads(raw.split(b"\n", 1)[0])
        if type(header) is not dict:
            raise ValueError("Malformed receipt header")
        detail = json.loads(header["failure"]) if type(header.get("failure")) is str else {}
        if (type(detail) is not dict or type(detail.get("frames", [])) is not list
                or any(type(f) is not dict or type(f.get("file")) is not str
                       or type(f.get("line")) is not int for f in detail.get("frames", [])[-20:])):
            raise ValueError("Malformed receipt detail")
        allowed_names = set().union(*_DIAGNOSTIC_CLASSES.values())
        frames = [_producer_frame_location(f.get("file"), f.get("line"), root)
                  for f in detail.get("frames", [])[-20:] if type(f) is dict]
        return {**result, "status": "read" if "errorType" in detail and "frames" in detail else "metadata-only",
                "phase": header.get("phase") if header.get("phase") in _STARTUP_PHASES else "other",
                "exitCode": header.get("exitCode") if type(header.get("exitCode")) is int
                            and -128 <= header["exitCode"] <= 255 else None,
                "exceptionClass": detail.get("errorType")
                                  if detail.get("errorType") in allowed_names else "other",
                "frames": frames}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {**result, "status": "malformed-or-unavailable"}


def _producer_valid_schema_record(row, name):
    """Validate every uploaded leaf, including corrupted diagnostic records."""
    if name in {"installed", "overflow"}:
        return row == {"status": name}
    if type(row) is not dict or row.get("status") not in {"raised", "returned"}:
        return False
    bools = {"ownedPackageExists", "ownedPackageOriginMatches", "fixtureHelperOriginMatches",
             "schemaHelperOriginMatches", "expectedParentInSysPath"}
    extra = "exception" if row["status"] == "raised" else "schemaShape"
    if (set(row) != bools | {"status", "ordinal", extra}
            or type(row["ordinal"]) is not int or name != f"schema-{row['ordinal']}"
            or not all(type(row[k]) is bool for k in bools)):
        return False
    detail = row[extra]
    if type(detail) is not dict:
        return False
    if extra == "schemaShape":
        return (set(detail) == {"mapping", "fieldsList", "fieldCount", "fieldCountCapped"}
                and all(type(detail[k]) is bool for k in ("mapping", "fieldsList", "fieldCountCapped"))
                and (detail["fieldCount"] is None or
                     type(detail["fieldCount"]) is int and 0 <= detail["fieldCount"] <= 1024))
    if (set(detail) != {"exceptionClass", "missingModule", "frames", "framesCapped"}
            or detail["exceptionClass"] not in set().union(*_DIAGNOSTIC_CLASSES.values(), {"other"})
            or detail["missingModule"] not in {None, "other", _SCHEMA_MODULE,
                                               "live_terminal_probe", "amplifier_core", "amplifier_web"}
            or type(detail["framesCapped"]) is not bool or type(detail["frames"]) is not list
            or len(detail["frames"]) > 20):
        return False
    locations = {"other", "owned/provider", "owned/bootstrap"} | {
        "repo/" + path for path in _DIAGNOSTIC_LOCATIONS}
    return all(type(frame) is dict and set(frame) == {"location", "line"}
               and frame["location"] in locations
               and (frame["line"] is None or type(frame["line"]) is int and 0 < frame["line"] < 1_000_000)
               for frame in detail["frames"])


def _producer_schema_diagnostics(root):
    """Only fixed observer files; never search private app logs or event files."""
    rows, gaps = [], []
    for name in ("installed", "schema-1", "schema-2", "schema-3", "schema-4", "overflow"):
        try:
            raw = _producer_owned_bytes(root, root / "schema-diagnostics" / (name + ".json"), 16_384)
            row = json.loads(raw)
            # The only writer is the allowlisted observer above. Reject malformed
            # records rather than copying arbitrary content into the report.
            if not _producer_valid_schema_record(row, name):
                raise ValueError("Invalid observer record")
            rows.append(row)
        except FileNotFoundError:
            if name == "installed":
                gaps.append("observer-not-installed")
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            gaps.append("unreadable-" + name)
    if not any(row.get("status") in {"returned", "raised"} for row in rows):
        gaps.append("schema-outcome-not-observed")
    if any(row.get("status") == "overflow" for row in rows):
        gaps.append("schema-observations-capped")
    return {"rows": rows, "gaps": gaps}


def _observe_worker_appends(root):
    """Retain exact worker-side diagnostic receipts without changing the write."""
    from amplifier_web import session_files

    append = session_files.append_event
    ordinal = 0

    def observed(workspace, identity, event, data):
        nonlocal ordinal
        receipt = append(workspace, identity, event, data)
        ordinal += 1
        assert ordinal <= 512, "Worker diagnostic receipt budget exceeded"
        _producer_record(Path(root) / "receipts", f"{os.getpid()}-{ordinal:04d}", {
            "id": f"event:{identity}:{receipt['offset']}:{receipt['sha256'][:16]}",
            "receipt": receipt, "event": event, "writer": "runtime-worker",
            "fields": {key: data.get(key) for key in
                       ("kind", "input_id", "turnId", "generation_id", "tool_call_id")},
        })
        return receipt

    session_files.append_event = observed

    # Positive capture-writer evidence. Preserve the real append exactly once;
    # only publish an occurrence identity when the measured byte range matches
    # that writer's exact serialization and no concurrent append is observed.
    import hashlib
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
        LoggingHandler, _canonical_json)
    capture_append = LoggingHandler._append_event
    capture_ordinal = 0

    def captured(session_dir, event, data, workspace):
        nonlocal capture_ordinal
        path = session_dir / "events.jsonl"
        raw = (_canonical_json({"event": event, "workspace": workspace or "",
                                "timestamp": data.get("timestamp", ""), "data": data}) + "\n").encode()
        before = path.stat().st_size if path.exists() else 0
        result = capture_append(session_dir, event, data, workspace)
        after = path.stat().st_size
        capture_ordinal += 1
        assert capture_ordinal <= 512, "Capture writer receipt budget exceeded"
        digest = hashlib.sha256(raw).hexdigest()
        identity = session_dir.parent.name
        exact = after - before == len(raw)
        _producer_record(Path(root) / "capture-receipts",
                         f"{os.getpid()}-{capture_ordinal:04d}", {
            "id": f"event:{identity}:{before}:{digest[:16]}" if exact else None,
            "writer": "hook-context-intelligence", "event": event, "exactRange": exact,
            "offset": before, "bytes": len(raw), "sha256": digest,
            "sourceSessionId": identity, "path": str(path)})
        return result

    LoggingHandler._append_event = staticmethod(captured)

    # Observe entry into the real installed streaming implementation without
    # replacing it or inferring execution from imported-module presence.
    def streaming_entry(frame, event, arg):
        if (event == "call" and frame.f_code.co_name == "execute"
                and frame.f_globals.get("__name__") == "amplifier_module_loop_streaming"):
            _producer_record(Path(root) / "guard", f"streaming-{os.getpid()}", {
                "kind": "streaming-execute", "module": frame.f_globals["__name__"],
                "function": frame.f_code.co_name, "file": frame.f_code.co_filename})
            sys.setprofile(None)

    sys.setprofile(streaming_entry)


def _producer_spawn_spec(root, executable, argv, cwd, environment, *, cold=False, control=False):
    """Exact fixture child allowlist; no argument/flag prefix matching."""
    root = Path(root).resolve()
    python = str(Path(sys.executable).absolute())  # Keep the venv launcher, not its symlink target.
    if not isinstance(argv, (list, tuple)) or not all(isinstance(p, str) for p in argv):
        return None
    if executable != python or not argv or argv[0] != python:
        return None
    expected_path = os.pathsep.join((str(root / "bootstrap"), str(Path(__file__).parent), str(ROOT)))
    if (environment.get("PYTHONPATH") != expected_path
            or environment.get("UNIFIED_PRODUCER_ROOT") != str(root)
            or environment.get("PYTHONNOUSERSITE") != "1"
            or environment.get("PYTHONDONTWRITEBYTECODE") != "1"
            or any(k.startswith(("LD_", "DYLD_")) or k in {"PYTHONHOME", "PYTHONSTARTUP"}
                   for k in environment)):
        return None
    effective_cwd = Path(cwd if cwd is not None else os.getcwd()).resolve()
    if not (effective_cwd == ROOT.resolve() or effective_cwd.is_relative_to(root)):
        return None
    tail = list(argv[1:])
    if tail == [str(ROOT / "amplifier_web/runtime_worker.py")]:
        return None if cold else "runtime-worker"
    if control and tail == [str(Path(__file__)), str(root), "--guard-child"]:
        return "guard-control"
    # Actual ProcessProjector._start argv, no -c/-I/-E/-S/combined flags or
    # arbitrary modules. Data directories must remain in this fixture's app.
    if (len(tail) == 8 and tail[:2] in [
            ["-m", "amplifier_web.event_window_worker"],
            ["-m", "amplifier_web.history_projection_worker"]]
            and tail[2::2] == ["--spool", "--cache-home", "--memory-limit"]
            and tail[-1] == str(768 * 1024 * 1024)
            and Path(tail[5]).resolve() == root / "app"
            and Path(tail[3]).is_absolute()
            and Path(tail[3]).resolve().is_relative_to(root / "app")
            and Path(tail[3]).parent.name.startswith("projection-spool-")):
        return "history-reader"
    return None


def _producer_spawn_evidence(root, executable, argv, cwd, environment):
    """Retain exact safe paths/literals and redacted shapes, never raw env."""
    allowed_words = {"-m", "-u", "-I", "-E", "-S", "-c", "-Es", "--guard-child",
                     "--spool", "--cache-home", "--memory-limit", "805306368",
                     "amplifier_web.event_window_worker", "amplifier_web.history_projection_worker"}

    def safe(value):
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if isinstance(value, str) and "\n" not in value and "\0" not in value:
            if value in allowed_words:
                return value
            if value == str(Path(sys.executable).absolute()):
                return value
            if Path(value).is_absolute() and (
                    Path(value).is_relative_to(root) or Path(value).is_relative_to(ROOT)):
                return value
        return {"redacted": True, "type": type(value).__name__,
                "length": len(value) if isinstance(value, (str, bytes)) else None}

    expected_path = os.pathsep.join((str(root / "bootstrap"), str(Path(__file__).parent), str(ROOT)))
    # Safe executable paths are evidence, not authority to execute them.
    executable_evidence = safe(executable)
    if isinstance(executable, str) and Path(executable).is_absolute() and Path(executable).parent in {
            Path("/bin"), Path("/usr/bin"), Path("/usr/local/bin")}:
        executable_evidence = executable
    return {"executable": executable_evidence,
            "argvShape": [safe(p) for p in argv[:16]] if isinstance(argv, (tuple, list)) else safe(argv),
            "argc": len(argv) if isinstance(argv, (tuple, list)) else None,
            "cwd": safe(cwd if cwd is not None else os.getcwd()),
            "explicitCwd": cwd is not None,
            "binding": {"pythonPathExact": environment.get("PYTHONPATH") == expected_path,
                        "rootExact": environment.get("UNIFIED_PRODUCER_ROOT") == str(root),
                        "noUserSite": environment.get("PYTHONNOUSERSITE") == "1",
                        "noBytecode": environment.get("PYTHONDONTWRITEBYTECODE") == "1",
                        "loaderEnvironmentPresent": any(
                            k.startswith(("LD_", "DYLD_")) or k in {"PYTHONHOME", "PYTHONSTARTUP"}
                            for k in environment)}}


def _install_producer_guard(root):
    """Inherited by Python readers too; no installer or unguarded child escape."""
    import ipaddress
    import socket
    import subprocess
    import threading

    if hasattr(sys, "_unified_producer_guard"):
        return
    root = Path(root).resolve()
    state = {"cold": False, "ordinal": 0, "controlCase": None, "controlActive": False}
    sys._unified_producer_guard = state
    directory = Path(root) / "guard"
    pending = threading.local()
    record_lock = threading.RLock()

    def record(kind, detail):
        with record_lock:
            state["ordinal"] += 1
            assert state["ordinal"] <= 512, "Guard observation budget exceeded"
            _producer_record(directory, f"{os.getpid()}-{state['ordinal']:04d}",
                             {"kind": kind, "pid": os.getpid(),
                              "controlCase": state["controlCase"], **detail})

    def popen_owner():
        # CPython3.13: subprocess.Popen audit precedes _posix_spawn within
        # _execute_child, with the same Popen instance. A direct os.posix_spawn
        # has no such frame and cannot redeem a leftover Popen allowance.
        frame = sys._getframe(1)
        try:
            for _ in range(16):
                if frame is None:
                    break
                if (frame.f_code is subprocess.Popen._execute_child.__code__
                        and frame.f_globals is vars(subprocess)):
                    return frame.f_locals.get("self"), frame.f_locals.get("shell", True)
                frame = frame.f_back
            return None, True
        finally:
            del frame

    def env_fingerprint(environment):
        import hashlib
        # Private correlation only, never persisted or printed.
        return hashlib.sha256(json.dumps(dict(environment), sort_keys=True).encode()).digest()

    def local(host):
        if host in {"localhost", b"localhost"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def audit(event, args):
        allowed = True
        detail = {}
        if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.sendto",
                     "socket.sendmsg"}:
            family = getattr(args[0], "family", None)
            address = args[-1]
            if address is None and event == "socket.sendmsg":
                address = args[0].getpeername()
            allowed = (family == socket.AF_UNIX or
                       (family in {socket.AF_INET, socket.AF_INET6}
                        and isinstance(address, tuple) and local(address[0])))
        elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            allowed = local(args[0])
        elif event == "socket.getnameinfo":
            allowed = local(args[0][0])
        elif event in {"subprocess.Popen", "os.posix_spawn"}:
            executable, argv = args[:2]
            cwd, env = (args[2], args[3]) if event == "subprocess.Popen" else (None, args[2])
            child_env = os.environ if env is None else env
            spec = _producer_spawn_spec(root, executable, argv, cwd, child_env,
                                        cold=state["cold"], control=state["controlActive"])
            owner, shell = popen_owner()
            detail = {**_producer_spawn_evidence(root, executable, argv, cwd, child_env),
                      "command": spec, "runtimeWorker": spec == "runtime-worker"}
            allowed = spec is not None and owner is not None and not shell
            # One-use, same-thread, same actual Popen, full env/argv/cwd match.
            # The token is not a capability for another spawn or arbitrary code.
            if event == "subprocess.Popen":
                pending.value = None
                if allowed:
                    pending.value = (owner, executable, tuple(argv), cwd,
                                     os.getcwd(), env_fingerprint(child_env))
            else:
                token, pending.value = getattr(pending, "value", None), None
                paired = (token is not None and token[0] is owner
                          and token[1:3] == (executable, tuple(argv)) and token[3] is None
                          and token[4] == os.getcwd() and token[5] == env_fingerprint(child_env))
                allowed = allowed and paired
                detail["pairedPopen"] = paired
            record("spawn" if event == "subprocess.Popen" else "posix-spawn",
                   {"event": event, "allowed": bool(allowed), "cold": state["cold"], **detail})
        elif event in {"os.system", "os.exec", "os.fork", "os.forkpty"}:
            allowed = False
        else:
            return
        if not allowed:
            # Endpoints/ambient environment are deliberately not copied.
            record("denied", {"event": event, "cold": state["cold"], **detail})
            raise RuntimeError("Producer audit guard denied " + event)

    sys.addaudithook(audit)
    record("installed", {"argv": sys.argv, "loopbackAndUnixOnly": True})


def _producer_guard_preflight(root):
    """Run only in the admitted native runner, before real producer admission."""
    import subprocess
    import socket

    state = sys._unified_producer_guard
    assert state["controlCase"] is None and not state["controlActive"] and not state["cold"]
    python = str(Path(sys.executable).absolute())
    command = [python, str(Path(__file__)), str(root), "--guard-child"]
    environment = dict(os.environ)
    results = []

    def rows(case):
        return [row for path in (root / "guard").glob("*.json")
                if (row := json.loads(path.read_text())).get("controlCase") == case
                and row["pid"] == os.getpid()]

    def rejected(case, action):
        state["controlCase"] = case
        try:
            action()
        except RuntimeError as error:
            assert str(error).startswith("Producer audit guard denied "), str(error)
            denied = [row for row in rows(case) if row["kind"] == "denied"]
            assert len(denied) == 1
            results.append({"case": case, "status": "refused", "event": denied[0]["event"]})
        else:
            raise AssertionError("Guard unexpectedly allowed " + case)
        finally:
            state["controlCase"] = None

    def run(argv, *, env=None, **kwargs):
        return subprocess.run(argv, env=environment if env is None else env,
                              capture_output=True, text=True, timeout=5, **kwargs)

    state["controlActive"] = True
    try:
        state["controlCase"] = "paired-python-child"
        child = run(command, close_fds=False)
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout) == {"guardInstalled": True, "cold": False}
        observed = rows(state["controlCase"])
        assert sum(r["kind"] == "spawn" and r["allowed"] for r in observed) == 1
        assert sum(r["kind"] == "posix-spawn" and r["allowed"] and r["pairedPopen"]
                   for r in observed) == 1, "Owned runner did not exercise stdlib posix_spawn"
        results.append({"case": state["controlCase"], "status": "passed",
                        "actualPopenAndPosixSpawn": True})
        state["controlCase"] = None

        for flag in ("-I", "-E", "-S", "-Es", "-c"):
            rejected("python-flag:" + flag, lambda flag=flag: run([python, flag, *command[1:]]))
        rejected("arbitrary-script", lambda: run([python, str(Path(__file__)), str(root), "--not-allowed"]))
        rejected("arbitrary-module", lambda: run([python, "-m", "json.tool"]))
        rejected("missing-bootstrap", lambda: run(command, env={**environment, "PYTHONPATH": str(ROOT)}))
        rejected("wrong-root", lambda: run(command, env={**environment, "UNIFIED_PRODUCER_ROOT": str(ROOT)}))
        rejected("loader-injection", lambda: run(command, env={**environment, "LD_PRELOAD": "fixture-denied"}))
        rejected("wrong-cwd", lambda: run(command, cwd="/"))
        rejected("shell", lambda: run(["exit", "0"], shell=True))

        def direct_spawn():
            # If this unexpectedly starts, it is only the harmless guard child;
            # reap it before reporting the failed negative control.
            pid = os.posix_spawn(python, command, environment)
            os.waitpid(pid, 0)
        rejected("unpaired-posix-spawn", direct_spawn)
        rejected("fork-audit", lambda: sys.audit("os.fork"))
        state["cold"] = True
        rejected("cold-runtime-start", lambda: run([python, str(ROOT / "amplifier_web/runtime_worker.py")]))
        state["cold"] = False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            rejected("non-loopback-network", lambda: sys.audit(
                "socket.connect", connection, ("192.0.2.1", 443)))
    finally:
        state.update(controlCase=None, controlActive=False, cold=False)
    return {"status": "passed", "cases": results,
            "scope": "Actual harmless Popen+posix_spawn; refused argv/environment/cwd/direct-spawn; fork/network audit controls"}


class RegularProducerProvider:
    """Public metadata construction is pure; execution requires a real mount."""
    name = "regularproducer"

    def __init__(self, config=None):
        self.calls = 0
        self.ids = []
        self.coordinator = None

    def get_info(self):
        from amplifier_core import ProviderInfo
        return ProviderInfo(id=self.name, display_name="Local regular-work producer",
                            capabilities=["tools", "streaming"],
                            defaults={"model": "offline", "context_window": 200000})

    def get_config_schema(self):
        return {"fields": []}

    async def list_models(self):
        return []

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    async def complete(self, request, **kwargs):
        from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
        import uuid
        assert self.coordinator is not None, "Provider was not mounted"
        coordinator = self.coordinator
        directory = Path(os.environ["UNIFIED_PRODUCER_ROOT"]) / "counts"
        assert hasattr(sys, "_unified_producer_guard"), "Worker network guard missing"
        self.calls += 1
        _producer_record(directory, f"provider-attempt-{self.calls}",
                         {"ordinal": self.calls, "pid": os.getpid(), "attempt": "provider"})
        if self.calls > 3:
            _producer_record(directory, "provider-over-budget", {"calls": self.calls})
            raise RuntimeError("Producer exceeded three completions")
        resolver = coordinator.get_capability("context_intelligence.hook_config_resolver")
        assert resolver is not None, "Actual capture hook did not mount"
        assert resolver.destinations == {} and resolver.validate_destinations() == {}, (
            "Actual capture hook resolved a remote destination")
        # Verify the runtime really fed the previous tool's full result back.
        consumed = None
        if self.calls > 1:
            prior = next((m for m in reversed(request.messages)
                          if m.role == "tool" and m.tool_call_id == self.ids[-1]), None)
            assert prior is not None, "Native tool result missing from provider request"
            content = prior.content
            if not isinstance(content, str):
                content = "".join(getattr(block, "text", "") for block in content or [])
            consumed = json.loads(content)
            assert consumed in _producer_result_encodings(_producer_cases()[self.calls - 2]["output"])
        call = None
        text = ("Producer interim: first tool." if self.calls == 1 else
                "Producer interim: second tool." if self.calls == 2 else
                "Producer final: two tools complete.")
        if self.calls <= 2:
            identity = "producer-" + uuid.uuid4().hex
            self.ids.append(identity)
            call = ToolCall(id=identity, name="regular_echo",
                            arguments=_producer_cases()[self.calls - 1]["input"])
        _producer_record(directory, f"provider-{self.calls}", {
            "ordinal": self.calls, "pid": os.getpid(), "toolCallId": self.ids[-1] if call else None,
            "captureResolverPresent": True, "destinations": resolver.validate_destinations(),
            "streamingImported": "amplifier_module_loop_streaming" in sys.modules,
            "text": text, "consumedToolResult": consumed,
        })
        # Provider stream deltas only. The real orchestrator/worker creates
        # admission, generation, message and execution lifecycle events.
        request_id = uuid.uuid4().hex
        for chunk in (text[:12], text[12:]):
            await coordinator.hooks.emit("llm:stream_block_delta", {
                "block_type": "text", "text": chunk, "request_id": request_id, "block_index": 0})
        return ChatResponse(content=[TextBlock(text=text)], tool_calls=[call] if call else None,
                            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))


async def mount_producer_provider(coordinator, config=None):
    directory = Path(os.environ["UNIFIED_PRODUCER_ROOT"]) / "counts"
    provider = RegularProducerProvider(config)
    provider.coordinator = coordinator
    _producer_record(directory, "worker-mounted", {"pid": os.getpid()})
    await coordinator.mount("providers", provider, name=provider.name)


async def mount_producer_tools(coordinator, config=None):
    from amplifier_core import ToolResult

    directory = Path(os.environ["UNIFIED_PRODUCER_ROOT"]) / "counts"

    class Echo:
        name = "regular_echo"
        description = "Return a deterministic local Unicode value; no external effects."
        input_schema = {"type": "object", "properties": {
            "slot": {"type": "integer", "enum": [1, 2]}, "body": {"type": "string"}},
            "required": ["slot", "body"], "additionalProperties": False}

        async def execute(self, arguments):
            assert hasattr(sys, "_unified_producer_guard")
            ordinal = len(list(directory.glob("tool-attempt-*.json"))) + 1
            _producer_record(directory, f"tool-attempt-{ordinal}",
                             {"ordinal": ordinal, "pid": os.getpid(), "attempt": "tool"})
            if ordinal > 2:
                _producer_record(directory, "tool-over-budget", {"calls": ordinal})
                raise RuntimeError("Producer exceeded two tool executions")
            case = _producer_cases()[ordinal - 1]
            assert arguments == case["input"]
            result = ToolResult(success=True, output=case["output"])
            _producer_record(directory, f"tool-{ordinal}", {
                "pid": os.getpid(), "ordinal": ordinal, "input": arguments,
                "output": case["output"], "result": result.model_dump(mode="json")})
            return result

    await coordinator.mount("tools", Echo(), name="regular_echo")


def _producer_environment(root):
    """Use only fresh owned homes; installed dependency origins are read later."""
    keep = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE",
                                           "UNIFIED_PRODUCER_PREPARATION")
            if key in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    for name in ("workspace", "counts", "guard", "receipts", "capture-receipts", "schema-diagnostics",
                 "bootstrap", "user", "amplifier-home"):
        (root / name).mkdir(parents=True)
    os.environ.update(
        HOME=str(root / "user"), XDG_CONFIG_HOME=str(root / "user/config"),
        XDG_CACHE_HOME=str(root / "user/cache"), XDG_DATA_HOME=str(root / "user/data"),
        AMPLIFIER_HOME=str(root / "amplifier-home"), AMPLIFIER_WEB_HOME=str(root / "app"),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"),
        AMPLIFIER_TERMINAL_HOME=str(root / "terminal"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL="",
        AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY="",
        AMPLIFIER_RUNTIME_IMMUTABLE="1", UV_OFFLINE="true",
        PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
        UNIFIED_PRODUCER_ROOT=str(root),
        PYTHONPATH=os.pathsep.join((str(root / "bootstrap"), str(Path(__file__).parent), str(ROOT))),
    )
    # Python startup normally swallows sitecustomize exceptions. Fail closed
    # instead, including in the real runtime_worker and WorkWindows reader.
    (root / "bootstrap/sitecustomize.py").write_text(
        "import os, sys\n"
        "try:\n"
        "    from pathlib import Path\n"
        "    from live_terminal_probe import _install_producer_guard, _observe_worker_appends, _observe_worker_schema\n"
        "    _install_producer_guard(Path(os.environ['UNIFIED_PRODUCER_ROOT']))\n"
        "    if sys.argv[0].endswith('/amplifier_web/runtime_worker.py'):\n"
        "        _observe_worker_schema(Path(os.environ['UNIFIED_PRODUCER_ROOT']))\n"
        "        _observe_worker_appends(Path(os.environ['UNIFIED_PRODUCER_ROOT']))\n"
        "except BaseException:\n"
        "    os._exit(97)\n", encoding="utf-8")
    _install_producer_guard(root)


def _producer_dependencies(report, save):
    import importlib
    import importlib.metadata as metadata

    result, sources = {}, {}
    report["dependencies"] = result
    for module, distribution in (
        ("amplifier_core", "amplifier-core"),
        ("amplifier_foundation", "amplifier-foundation"),
        ("amplifier_module_loop_live", "amplifier-module-loop-live"),
        ("amplifier_module_loop_streaming", "amplifier-module-loop-streaming"),
        ("amplifier_module_context_simple", "amplifier-module-context-simple"),
        ("context_intelligence", "amplifier-bundle-context-intelligence"),
        ("amplifier_module_hook_context_intelligence", "amplifier-module-hook-context-intelligence"),
    ):
        report["phase"] = "installed-import:" + module
        save()
        installed = importlib.import_module(module)  # Import failure is setup failure, never a skip.
        dist = metadata.distribution(distribution)
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        origin = Path(installed.__file__).resolve(strict=True)
        result[distribution] = {"version": dist.version, "origin": str(origin),
                                "commit": direct.get("vcs_info", {}).get("commit_id")}
        if module.startswith("amplifier_module_"):
            sources[module.removeprefix("amplifier_module_").replace("_", "-")] = str(origin.parent)
        save()
    report["phase"] = "installed-provenance"
    assert result["amplifier-core"]["version"] == "2.0.2", result
    expected_loop = "08de4d442fde0210c4c89f7628c17846c5dfd67b"
    loop_dist = metadata.distribution("amplifier-module-loop-live")
    direct = json.loads(loop_dist.read_text("direct_url.json") or "{}")
    if result["amplifier-module-loop-live"]["commit"] != expected_loop:
        # Existing CI preparation installs its authenticated checkout by path.
        # Validate that retained receipt and direct URL, not an invented VCS
        # field on a path-installed wheel or a fetch from this guarded process.
        receipt_path = os.environ.get("UNIFIED_PRODUCER_PREPARATION")
        assert receipt_path, "Path-installed loop-live requires native preparation receipt"
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
        checkout = receipt["loopCheckout"]
        assert checkout["commit"] == expected_loop
        assert Path(receipt["source"]).resolve() == ROOT.resolve()
        assert checkout["declared"]["git"] == "https://github.com/microsoft/amplifier-module-loop-live"
        assert direct.get("url") == Path(checkout["path"]).resolve().as_uri()
        result["amplifier-module-loop-live"]["checkoutCommit"] = checkout["commit"]
    save()
    return result, sources


async def _producer_get(client, query):
    """Only a changed initial window may be reopened; field refusals are evidence."""
    for attempt in range(3):
        async with asyncio.timeout(10):
            response = await client.get("/api/conversation/detail", params=query)
            body = await response.json()
        if (query.get("part") == "groups" and "before" not in query and response.status == 400
                and body.get("error") ==
                "Invalid request: The requested conversation changed. Open it again."):
            refreshed = await client.get("/api/state")
            assert refreshed.status == 200
            await refreshed.read()
            continue
        if response.status != 200:
            raise ValueError(json.dumps({"query": query, "status": response.status, "body": body}))
        return body
    raise RuntimeError("Initial Work window did not settle in three attempts")


async def _producer_field(client, sid, row, field, expected):
    reference = row.get(field + "Detail")
    evidence = {"reference": reference, "inlinePresent": field in row}
    if reference is None and field not in row:
        return {**evidence, "status": "missing"}
    value = row.get(field)
    if reference is not None:
        assert reference["sessionId"] == sid and reference["id"] == row["id"]
        assert reference["part"] == "nodes" and reference["field"] == field
        chunks, offset = [], 0
        try:
            for _ in range(64):
                page = await _producer_get(client, {**reference, "offset": offset, "limit": 4096})
                assert isinstance(page["value"], str) and len(page["value"]) <= 4096
                chunks.append(page["value"])
                following = page["nextOffset"]
                if following is None:
                    break
                assert following == offset + len(page["value"]) and following > offset
                offset = following
            else:
                raise RuntimeError("Field exceeded 64 bounded pages")
        except ValueError as error:
            return {**evidence, "status": "unavailable", "error": str(error)}
        value = "".join(chunks)
        evidence["chunks"] = len(chunks)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = value
    else:
        decoded = value
    # Store the entire decoded value, not a tail/hash substituted for equality.
    evidence.update(status="read", value=decoded)
    if expected is not None:
        evidence["exact"] = any(decoded == candidate for candidate in expected)
    return evidence


async def _producer_observation(client, sid, calls, diagnostics, result):
    """Read occurrence windows and the current selected web projection separately."""
    # The caller owns this dict before I/O, so a partial read is retained on failure.
    result.update(savedGroups=[], occurrences=[], groups=[], groupNodes=[],
                  coverage=[], readErrors=[])
    expected = {row["toolCallId"]: _producer_cases()[row["ordinal"] - 1]
                for row in calls if row.get("toolCallId")}

    async def nodes(group, target):
        before, cursors = None, set()
        for _ in range(8):
            query = {"sessionId": sid, "part": "nodes", "group": group["id"],
                     "revision": group["detailRevision"]}
            if before is not None:
                query["before"] = before
            try:
                page = await _producer_get(client, query)
            except ValueError as error:
                result["readErrors"].append({"group": group["id"], "error": str(error)})
                return
            assert page["group"] == group["id"] and page["revision"] == group["detailRevision"]
            assert len(page["items"]) <= 100
            for row in page["items"]:
                entry = {"row": row, "groupId": group["id"], "fields": {}}
                case = expected.get(row.get("toolCallId"))
                if row.get("kind") in {"tool", "llm"} or case is not None:
                    fields = ("request", "response") if row.get("kind") == "llm" else ("input", "output")
                    for field in fields:
                        candidates = None
                        if case:
                            candidates = [case[field]]
                            if field == "output":
                                candidates = _producer_result_encodings(case[field])
                        entry["fields"][field] = await _producer_field(
                            client, sid, row, field, candidates)
                target.append(entry)
            before = page["before"]
            if before is None:
                return
            assert before not in cursors, "Repeated node cursor"
            cursors.add(before)
        raise RuntimeError("Nodes exceeded eight bounded pages")

    before, cursors = None, set()
    for _ in range(8):
        query = {"sessionId": sid, "part": "groups"}
        if before is not None:
            query["before"] = before
        page = await _producer_get(client, query)
        result["coverage"].append({key: page.get(key) for key in
                                   ("coverage", "unavailableRanges", "hasEarlier", "before")})
        for group in page["items"]:
            result["savedGroups"].append(group)
            await nodes(group, result["occurrences"])
        before = page["before"]
        if before is None:
            break
        assert before not in cursors, "Repeated group cursor"
        cursors.add(before)
    else:
        raise RuntimeError("Saved observations exceeded eight bounded pages")

    # Never pass sessionId here: that would bypass the registered-web projection.
    deadline = asyncio.get_running_loop().time() + 5
    async with asyncio.timeout(10):
        while True:
            response = await client.get("/api/state")
            assert response.status == 200
            state = await response.json()
            assert state["selectedSessionId"] == sid
            selected = next(row for row in state["sessions"] if row["id"] == sid)
            readiness = selected.get("workProjection", {})
            if readiness.get("current") is True and readiness.get("status") == "ready":
                break
            if asyncio.get_running_loop().time() >= deadline:
                # Loading/unavailable is an observation, not a prescribed old failure.
                result["readErrors"].append({"projection": readiness, "reason": "readiness deadline"})
                break
            await asyncio.sleep(.05)
    result["workProjection"] = readiness
    result["messages"] = (await _producer_get(client, {"sessionId": sid, "part": "messages"}))["items"]
    result["groups"] = selected.get("execution", {}).get("segments", [])
    assert len(result["groups"]) <= 32, "Unexpected group count"
    for group in result["groups"]:
        if not group.get("observationWindow"):
            await nodes(group, result["groupNodes"])

    from collections import Counter
    occurrence_counts = Counter(entry["row"]["id"] for entry in result["occurrences"])
    group_node_counts = Counter(entry["row"]["id"] for entry in result["groupNodes"])
    result["duplicateOccurrenceCount"] = sum(count - 1 for count in occurrence_counts.values())
    # Do not collapse two records because they share a call ID or timestamp.
    # Exact returned occurrence IDs are compared only with real append receipts.
    for path in sorted((Path(os.environ["UNIFIED_PRODUCER_ROOT"]) / "receipts").glob("*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        diagnostics[receipt["id"]] = receipt
    capture_receipts = {}
    for path in sorted((Path(os.environ["UNIFIED_PRODUCER_ROOT"]) / "capture-receipts").glob("*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("exactRange") and receipt.get("id"):
            capture_receipts[receipt["id"]] = receipt
    for entry in result["occurrences"]:
        identity = entry["row"]["id"]
        entry["origin"] = ("host-diagnostic-receipt" if identity in diagnostics else
                           "capture-writer-receipt" if identity in capture_receipts else "unknown")
        entry["diagnosticReceipt"] = diagnostics.get(identity)
        entry["captureReceipt"] = capture_receipts.get(identity)
    users = [row for row in result["messages"] if row.get("role") == "user"]
    interims = [row for row in result["messages"] if row.get("role") == "assistant"
                and row.get("text", "").startswith("Producer interim:")]
    result["observedIdentities"] = {
        "messages": [{key: row.get(key) for key in ("id", "role", "inputId", "response")}
                     for row in result["messages"]],
        "sourceOccurrences": [{key: entry["row"].get(key) for key in
                               ("id", "sessionId", "inputId", "generationId", "toolCallId", "requestId", "event")}
                              for entry in result["occurrences"]],
    }
    result["association"] = []
    for message in interims:
        inputs = message.get("response", {}).get("inputIds", [])
        candidates = [group["id"] for group in result["groups"]
                      if not group.get("observationWindow")
                      and group.get("anchorMessageId") == message["id"]
                      and group.get("turnId") in inputs]
        result["association"].append({"messageId": message["id"], "inputIds": inputs,
                                      "groupIds": candidates, "count": len(candidates)})
    fields = [field for entry in result["groupNodes"] for field in entry["fields"].values()]
    result["toolOccurrenceRefs"] = [
        {"toolCallId": identity, "event": event,
         "occurrenceIds": [entry["row"]["id"] for entry in result["occurrences"]
                           if entry["row"].get("toolCallId") == identity
                           and entry["row"].get("event") == event]}
        for identity in expected for event in ("tool:pre", "tool:post")]
    group_calls = {entry["row"].get("toolCallId") for entry in result["groupNodes"]}
    result["counts"] = {
        "userMessages": len(users), "interimMessages": len(interims),
        "missingInterimMessages": max(0, 2 - len(interims)),
        "missingToolOccurrences": sum(not row["occurrenceIds"] for row in result["toolOccurrenceRefs"]),
        "missingGroupCalls": len(expected.keys() - group_calls),
        "missingAssociations": sum(row["count"] == 0 for row in result["association"]),
        "ambiguousAssociations": sum(row["count"] > 1 for row in result["association"]),
        "missingGroupFields": sum(field["status"] == "missing" for field in fields),
        "unavailableGroupFields": sum(field["status"] == "unavailable" for field in fields),
        "diagnosticOccurrences": sum(entry["origin"] == "host-diagnostic-receipt"
                                     for entry in result["occurrences"]),
        "captureOccurrences": sum(entry["origin"] == "capture-writer-receipt"
                                  for entry in result["occurrences"]),
        "unknownOccurrences": sum(entry["origin"] == "unknown" for entry in result["occurrences"]),
        "duplicateOccurrences": result["duplicateOccurrenceCount"],
        "duplicateGroupNodeIds": sum(count - 1 for count in group_node_counts.values()),
    }
    return result


def _producer_diagnostic_step(root, report, phase, operation):
    """Diagnostic failures are secondary, never replacements for product work."""
    try:
        operation()
        return True
    except BaseException as error:
        report["status"] = "failed"
        report["diagnosticIncomplete"] = True
        failures = report.setdefault("diagnosticFailures", [])
        if len(failures) < 8:
            try:
                shape = _producer_exception_shape(error, root)
            except BaseException:
                shape = {"exceptionClass": "other", "frames": []}
            failures.append({"phase": phase, **shape})
        secondary = report.setdefault("secondaryFailures", [])
        marker = {"phase": "diagnostics", "reason": "Diagnostic collection or persistence failed"}
        if marker not in secondary:
            secondary.append(marker)
        return False


def _producer_event_observer(root, report, save, single):
    async def observed_event(kind, payload):
        def observe():
            assert len(report["runtimeEvents"]) < 512, "Runtime event budget exceeded"
            public = payload
            if kind == "runtime.error":
                public = {"phase": payload.get("phase") if payload.get("phase") in _STARTUP_PHASES else "other",
                          "errorType": "RuntimeStartupError" if payload.get("errorType") == "RuntimeStartupError"
                                       else "other"}
                failures = payload.get("moduleFailures")
                failures = failures if type(failures) is list else []
                public["providerSchemaFailed"] = any(
                    type(row) is dict and row.get("module") == "provider-regularproducer"
                    and row.get("reason_code") == "provider_schema_failed"
                    for row in failures[:16])
                if payload.get("sessionId") == report.get("sessionId"):
                    name = payload.get("diagnosticReceipt")
                    if len(report["startupDiagnostics"]) < 2 and not any(
                            row.get("name") == name for row in report["startupDiagnostics"]):
                        report["startupDiagnostics"].append(_producer_startup_diagnostic(root, name))
                        _producer_diagnostic_step(root, report, "event-receipt-save", save)
            report["runtimeEvents"].append({"kind": kind, "payload": json.loads(json.dumps(public))})

        _producer_diagnostic_step(root, report, "event-receipt-extraction", observe)
        # Outside the diagnostic catch: exactly once, identical payload/result,
        # and the original callback's exception remains the primary exception.
        return await single(kind, payload)

    return observed_event


async def _producer_cleanup_with_diagnostics(root, report, save, cleanup):
    def collect():
        report["workerSchemaDiagnostics"] = _producer_schema_diagnostics(root)
        report["startupDiagnosticGaps"] = [
            row["status"] for row in report.get("startupDiagnostics", []) if row["status"] != "read"]

    _producer_diagnostic_step(root, report, "precleanup-collection", collect)
    _producer_diagnostic_step(root, report, "precleanup-save", save)
    # The real socket/task/client/service cleanup runs even when either
    # observational step failed. Do not catch its result or exception here.
    return await cleanup()


async def producer_main(root, report, save):
    _producer_environment(root)
    report["phase"] = "process-guard-preflight"
    save()
    report["processGuardPreflight"] = _producer_guard_preflight(root)
    report["phase"] = "installed-imports"
    save()
    report["dependencies"], sources = _producer_dependencies(report, save)
    from aiohttp.test_utils import TestClient, TestServer
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient
    from amplifier_web import session_files

    for name, mount in (("provider-regularproducer", "mount_producer_provider"),
                        ("tool-regularproducer", "mount_producer_tools")):
        package = root / name / ("amplifier_module_" + name.replace("-", "_"))
        package.mkdir(parents=True)
        package.joinpath("__init__.py").write_text(
            f"from live_terminal_probe import {mount} as mount\n"
            + ("from live_terminal_probe import RegularProducerProvider\n"
               if name == "provider-regularproducer" else "")
            + f"__amplifier_module_type__ = {name.split('-')[0]!r}\n", encoding="utf-8")
        sources[name] = str(package)
    # Exercise the actual public setup contract before any conversation input.
    # The fresh generated module is placed on sys.path just as module activation
    # does; no patched schema loader or forged runtime configuration is used.
    report["phase"] = "provider-schema-preflight"
    save()
    from amplifier_web.provider_environment import (
        provider_class, construct_schema_provider, config_schema, provider_config_schema)
    sys.path.insert(0, str(Path(sources["provider-regularproducer"]).parent))
    before_counts = sorted((root / "counts").iterdir())
    schema_class = provider_class("provider-regularproducer")
    assert schema_class.__name__ == "RegularProducerProvider"
    schema_provider = construct_schema_provider(schema_class, {}, environment={})
    assert schema_provider.coordinator is None and schema_provider.calls == 0
    assert schema_provider.get_info().id == "regularproducer"
    assert await config_schema(schema_provider) == {"fields": []}
    assert await provider_config_schema("provider-regularproducer", config={}, environment={}) == {"fields": []}
    assert sorted((root / "counts").iterdir()) == before_counts
    report["providerSchemaPreflight"] = {
        "publicClass": schema_class.__name__, "fields": [], "coordinatorRequired": False,
        "providerAttempts": 0, "toolAttempts": 0, "hostValidationUsed": True}
    save()
    bundle = root / "producer.yaml"
    hook_config = {"base_path": str(root / "capture"), "destinations": {},
                   "context_intelligence_server_url": "", "context_intelligence_api_key": ""}
    provider = {"module": "provider-regularproducer", "source": sources["provider-regularproducer"]}
    bundle.write_text(json.dumps({
        "bundle": {"name": "regular-producer", "version": "0.0.1"},
        "session": {"orchestrator": {"module": "loop-live", "source": sources["loop-live"],
                                    "config": {"use_streaming": True}},
                    "context": {"module": "context-simple", "source": sources["context-simple"]}},
        "providers": [provider],
        "tools": [{"module": "tool-regularproducer", "source": sources["tool-regularproducer"]}],
        "hooks": [{"module": "hook-context-intelligence", "source": sources["hook-context-intelligence"],
                   "config": hook_config}],
    }), encoding="utf-8")
    (root / "amplifier-home/settings.yaml").write_text(json.dumps({
        "bundle": {"active": str(bundle), "app": []},
        "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)},
        "sources": {"modules": sources},
        "config": {"providers": [provider]},
    }), encoding="utf-8")
    report["captureConfiguration"] = hook_config
    report["phase"] = "app-start"
    save()
    clients, services, tasks, sockets = [], [], [], []
    diagnostics = report["diagnosticReceipts"] = {}
    original_append = session_files.append_event

    def observed_append(workspace, identity, event, data):
        receipt = original_append(workspace, identity, event, data)
        key = f"event:{identity}:{receipt['offset']}:{receipt['sha256'][:16]}"
        assert len(diagnostics) < 512, "Diagnostic receipt budget exceeded"
        diagnostics[key] = {"receipt": receipt, "event": event, "writer": "app-parent",
                            "fields": {k: data.get(k) for k in
                                       ("kind", "input_id", "turnId", "generation_id", "tool_call_id")}}
        return receipt

    session_files.append_event = observed_append

    async def start_app():
        runtime = RuntimeManager(
            command=[sys.executable, str(ROOT / "amplifier_web/runtime_worker.py")],
            startup_timeout=45, preparation_timeout=45)
        app = await create_app(root / "app", workspace=str(root / "workspace"), runtime=runtime,
                               voice=False, background_updates=False, preload_providers=False)
        service = app["service"]
        services.append(service)
        runtime.app_bridge = service.app_bridge
        client = TestClient(TestServer(app, host="127.0.0.1"))
        clients.append(client)
        await client.start_server()
        client.session.headers["Authorization"] = "Bearer " + app["control_token"]
        url = str(client.make_url("/")).rstrip("/")
        app["allowed_origins"] = app["allowed_origins"] | {url}
        return app, service, client, url

    async def browser(service, client, sid):
        identity = "producer-detail-browser"
        service.clients.attach(identity, kind="web")
        with service.clients.bind(identity):
            await service.dispatch("session.select", {"id": sid})
        assert service.clients.records[identity]["selectedSessionId"] == sid
        client.session.headers["X-Amplifier-Client"] = identity
        ws = await client.ws_connect("/api/events/ws?transport=delta-v1")
        sockets.append(ws)
        initial = await ws.receive_json(timeout=5)
        assert initial["event"] == "state"

        async def drain():
            async for _ in ws:
                pass
        tasks.append(asyncio.create_task(drain()))

    def counts():
        rows = [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((root / "counts").glob("*.json"))]
        guard = [json.loads(path.read_text(encoding="utf-8"))
                 for path in sorted((root / "guard").glob("*.json"))]
        return {"provider": sum("captureResolverPresent" in row for row in rows),
                "tool": sum("result" in row for row in rows),
                "providerAttempts": sum(row.get("attempt") == "provider" for row in rows),
                "toolAttempts": sum(row.get("attempt") == "tool" for row in rows),
                "workerMounts": sum("ordinal" not in row and "pid" in row for row in rows),
                "workerLaunches": sum(row["kind"] == "spawn" and row["runtimeWorker"] for row in guard),
                "denials": [row for row in guard if row["kind"] == "denied" and row.get("controlCase") is None],
                "controlDenials": [row for row in guard if row["kind"] == "denied" and row.get("controlCase")],
                "overBudget": [path.name for path in (root / "counts").glob("*over-budget.json")]}

    try:
        app, service, client, url = await start_app()
        report["runtimeEvents"] = []
        report["startupDiagnostics"] = []
        single = service.on_runtime_event.single

        service.on_runtime_event.single = _producer_event_observer(root, report, save, single)
        async with SessionClient(url, app["control_token"], "producer-controller") as controller:
            created = await controller.create_session(
                {"title": "Regular producer observation", "bundle": str(bundle),
                 "workspace": str(root / "workspace"),
                 "selection": {"instance": "regularproducer", "model": "offline"}},
                command_id="create-regular-producer")
            sid = created["state"]["selectedSessionId"]
            report["sessionId"] = sid
            await browser(service, client, sid)
            report["phase"] = "producer-admission"
            save()
            report["admission"] = await controller.command(
                sid, "conversation.send", {"text": "Run the two local producer tools."},
                command_id="regular-producer-input")
            assert report["admission"].get("delivery") == "accepted", report["admission"]
            report["phase"] = "producer-completion"
            async with asyncio.timeout(60):
                while True:
                    snapshot = await controller.snapshot(sid)
                    session = snapshot["session"]
                    report["lastRuntimeState"] = {"status": session.get("status")}
                    if session.get("status") in {"error", "stopped"}:
                        raise RuntimeError(json.dumps(report["lastRuntimeState"]))
                    if (session.get("status") == "idle" and any(
                            m.get("text") == "Producer final: two tools complete."
                            for m in session.get("messages", []))):
                        break
                    await asyncio.sleep(.05)
            capture = session.get("runtimeReport", {}).get("contextIntelligence", {})
            report["actualCaptureState"] = capture
            assert capture.get("enabled") is True, "Real runtime did not report enabled capture"
            calls = [json.loads(path.read_text(encoding="utf-8"))
                     for path in sorted((root / "counts").glob("provider-[123].json"))]
            report["providerCalls"] = calls
            assert len(calls) == 3 and all(row["captureResolverPresent"] for row in calls)
            assert all(row["destinations"] == {} for row in calls)
            assert any(row["streamingImported"] for row in calls), "Streaming runtime was not imported"
            report["streamingExecution"] = [
                json.loads(path.read_text()) for path in (root / "guard").glob("streaming-*.json")]
            assert len(report["streamingExecution"]) == 1, "Actual streaming execute entry not observed"
            report["phase"] = "live-work-read"
            save()
            report["live"] = {}
            await _producer_observation(client, sid, calls, diagnostics, report["live"])

        report["phase"] = "app-cleanup-and-capture-flush"
        save()
        before = counts()
        assert before["provider"] == 3 and before["tool"] == 2 and before["workerMounts"] == 1, before
        assert before["providerAttempts"] == 3 and before["toolAttempts"] == 2, before
        assert before["workerLaunches"] == 1 and not before["denials"] and not before["overBudget"], before
        workers = [row["process"] for row in service.runtime.workers.values()]
        await asyncio.wait_for(sockets[0].close(), 3)
        await asyncio.wait_for(client.close(), 15)
        assert service.closed and not service.runtime.workers
        assert all(process.returncode is not None for process in workers)
        report["cleanup"] = {"firstAppClosed": True, "workerReturnCodes": [p.returncode for p in workers]}
        report["countsBeforeCold"] = before
        sys._unified_producer_guard["cold"] = True
        report["phase"] = "new-app-same-stores"
        save()
        _, cold_service, cold_client, _ = await start_app()
        assert cold_service is not service and not cold_service.runtime.workers
        await browser(cold_service, cold_client, sid)
        report["phase"] = "cold-work-read"
        save()
        report["cold"] = {}
        await _producer_observation(cold_client, sid, calls, diagnostics, report["cold"])
        report["countsAfterCold"] = counts()
        assert counts() == before and not cold_service.runtime.workers, report["countsAfterCold"]
        report["noExtraColdExecution"] = True
        live_ids = {entry["row"]["id"] for entry in report["live"]["occurrences"]}
        cold_ids = {entry["row"]["id"] for entry in report["cold"]["occurrences"]}
        report["occurrenceComparison"] = {
            "liveOnly": sorted(live_ids - cold_ids), "coldOnly": sorted(cold_ids - live_ids),
            "retainedExactIds": sorted(live_ids & cold_ids)}
        # Require actual post-cleanup capture evidence, not manufactured hook
        # configuration or a success label. Missing individual bodies/ownership
        # remain observations; this is not a product-repair regression oracle.
        call_ids = {call["toolCallId"] for call in calls if call.get("toolCallId")}
        captured = [entry["row"]["id"] for entry in report["cold"]["occurrences"]
                    if entry["origin"] == "capture-writer-receipt"
                    and entry["row"].get("toolCallId") in call_ids
                    and entry["row"].get("event") in {"tool:pre", "tool:post"}]
        assert captured, "No real tool capture occurrences visible after actual app cleanup"
        report["captureFlushEvidence"] = {"closedApp": True, "coldOccurrenceIds": captured}
        report["fieldIntegrityErrors"] = [
            {"view": label, "id": entry["row"]["id"], "field": name}
            for label in ("live", "cold")
            for entry in report[label]["occurrences"] + report[label]["groupNodes"]
            for name, field in entry["fields"].items()
            if field.get("reference") is not None and field.get("exact") is False]
        save()
        assert not report["fieldIntegrityErrors"], "Returned owned fields did not preserve exact values"
        report["status"] = "observed"
    except BaseException as error:
        report["primaryFailure"] = {"phase": report["phase"],
                                    **_producer_exception_shape(error, root)}
        _producer_diagnostic_step(root, report, "primary-failure-save", save)
        raise
    finally:
        report["phaseBeforeCleanup"] = report["phase"]

        async def cleanup():
            cleanup_errors = []
            for ws in sockets:
                try:
                    await asyncio.wait_for(ws.close(), 3)
                except Exception as error:
                    cleanup_errors.append(_producer_exception_shape(error, root))
            for task in tasks:
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
            except Exception as error:
                cleanup_errors.append(_producer_exception_shape(error, root))
            for client in reversed(clients):
                try:
                    await asyncio.wait_for(client.close(), 15)
                except Exception as error:
                    cleanup_errors.append(_producer_exception_shape(error, root))
            for service in services:
                if not service.closed:
                    try:
                        await asyncio.wait_for(service.close(), 10)
                    except Exception as error:
                        cleanup_errors.append(_producer_exception_shape(error, root))
            return cleanup_errors

        cleanup_errors = await _producer_cleanup_with_diagnostics(root, report, save, cleanup)
        session_files.append_event = original_append
        report["finalCounts"] = counts()
        report["finalCleanup"] = {"allServicesClosed": all(s.closed for s in services),
                                 "remainingWorkers": sum(len(s.runtime.workers) for s in services),
                                 "errors": cleanup_errors}
        report["guardObservations"] = [json.loads(path.read_text(encoding="utf-8"))
                                      for path in sorted((root / "guard").glob("*.json"))]
        secondary = report.setdefault("secondaryFailures", [])
        if report.get("workerSchemaDiagnostics", {"gaps": ["unavailable"]})["gaps"]:
            secondary.append({"phase": "diagnostics", "reason": "Worker schema observation incomplete"})
        if cleanup_errors or not report["finalCleanup"]["allServicesClosed"]:
            secondary.append({"phase": "cleanup", "reason": "Services did not close cleanly"})
        if report["finalCleanup"]["remainingWorkers"]:
            secondary.append({"phase": "cleanup", "reason": "Workers remain"})
        if report["finalCounts"]["denials"] or report["finalCounts"]["overBudget"]:
            secondary.append({"phase": "audit", "reason": "Unplanned refusal or count budget exceeded"})
        report["secondaryFailures"] = secondary
        _producer_diagnostic_step(root, report, "final-cleanup-save", save)
        # Preserve the original admission/producer error. Cleanup/audit evidence
        # still fails an otherwise successful check, but never replaces it.
        if secondary and "primaryFailure" not in report:
            raise RuntimeError("Producer cleanup/audit failed; see secondaryFailures")


def run_producer():
    root = Path(sys.argv[1]).resolve()
    evidence = Path(os.environ.get("UNIFIED_PRODUCER_EVIDENCE",
                                  str(root.parent / (root.name + "-evidence.json")))).resolve()
    if evidence.is_relative_to(root):
        raise ValueError("UNIFIED_PRODUCER_EVIDENCE must be outside the producer scratch directory")
    report = {"schema": 1, "mode": "regular-work-real-producer", "status": "running", "phase": "setup",
              "limits": [
                  "Deterministic local provider and tool; no external inference or credential qualification.",
                  "Real app/worker/Core/loop-live/capture; no authored canonical lifecycle records.",
                  "WorkWindows occurrence and registered-web detail API evidence, not rendered UI acceptance.",
                  "Missing bodies/associations and distinct diagnostic occurrences are observed, not prescribed.",
                  "At most three provider completions, two tool executions and one runtime worker.",
              ]}

    def save():
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def bounded():
        async with asyncio.timeout(145):
            await producer_main(root, report, save)

    save()
    try:
        asyncio.run(bounded())
    except BaseException as error:
        failure = {"phase": report["phase"], **_producer_exception_shape(error, root)}
        report.setdefault("primaryFailure", failure)
        report.update(status="failed", failure=report["primaryFailure"])
        report["guardObservations"] = [json.loads(path.read_text(encoding="utf-8"))
                                      for path in sorted((root / "guard").glob("*.json"))]
        _producer_diagnostic_step(root, report, "terminal-failure-save", save)
        print(json.dumps({"status": "failed", "phase": report["phase"], "evidence": str(evidence)}))
        # The exact exception was bare-rethrown at its worker observation site.
        # Do not leak its message/traceback via wrapper stdout/stderr/JUnit.
        raise SystemExit(1) from None
    report["phase"] = "complete"
    if not _producer_diagnostic_step(root, report, "terminal-complete-save", save):
        raise SystemExit(1)
    print(json.dumps({"status": report["status"], "evidence": str(evidence),
                      "counts": report["finalCounts"], "noExtraColdExecution": True}))


if __name__ == "__main__":
    if sys.argv[2:] == ["--guard-child"]:
        assert hasattr(sys, "_unified_producer_guard"), "Guard bootstrap missing"
        print(json.dumps({"guardInstalled": True, "cold": sys._unified_producer_guard["cold"]}))
    elif sys.argv[2:] == ["--producer"]:
        run_producer()
    elif len(sys.argv) == 2:
        asyncio.run(main())
    else:
        raise SystemExit("usage: live_terminal_probe.py SCRATCH [--producer]")
