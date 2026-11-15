"""Offline source proof of the real runtime_worker.py file and JSON-lines pipes.

The fresh child's explicit sitecustomize installs only a finite source resolver,
the source-only loop locator and bottom HTTPX/HTTPX2 transports. It never creates
a Worker or changes a provider, SDK resource, witness, graph, binding or publisher.
This is not installed-package, DTU, live-account or real-inference evidence.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
SESSION = "normal-media-file-entry"


def helpers():
    # Importing this helper does not run its main or construct its Worker.
    import normal_media_worker_probe
    return normal_media_worker_probe


def private_parent(root):
    assert root.is_dir() and not root.is_symlink()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700, "Probe parent must already be private (0700)"
    assert not any(root.iterdir()), "Use a fresh empty root; never replay an earlier probe"


def save_json(path, value):
    path.write_text(json.dumps(value, default=str))
    path.chmod(0o600)


def deny_socket_connections(stack, report):
    """Deny and record both socket connection entry points, before SDK imports."""
    import socket
    from unittest.mock import patch
    denied = []

    def no_socket(*args, **kwargs):
        denied.append({"operation": "socket.connect", "attempt": len(denied) + 1})
        save_json(report, denied)
        raise AssertionError("Offline pipe fixture denied an unintercepted socket connection")

    stack.enter_context(patch.object(socket.socket, "connect", no_socket))
    stack.enter_context(patch.object(socket.socket, "connect_ex", no_socket))
    return denied


def finite_sources(paths, modules):
    """Same finite source policy as warm_worker_probe, without its app import.

    Delay only the explicit source-only loop locator until ordinary bundle
    resolution. The file's own canonical entry must bootstrap the app package;
    neither this fixture nor its PYTHONPATH preloads the Worker or fixes aliases.
    """
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import source_resolution_policy
    from amplifier_foundation.sources.policy import SourceResolutionDenied
    admitted = {str(Path(path).resolve(strict=True)): Path(path).resolve(strict=True) for path in paths}
    selected = Path(modules["loop-live"]).resolve(strict=True)
    assert helpers().origin("amplifier_module_loop_live").parent == selected
    located = False

    def resolve(uri, *, base_path):
        nonlocal located
        path = admitted.get(uri)
        if path is None:
            raise SourceResolutionDenied("Pipe fixture source is not explicitly local: " + uri)
        if not located:
            host = sys.modules.get("amplifier_web.host.session")
            assert host is not None, "Ordinary app bootstrap must precede source resolution"
            host.installed_loop_source = lambda: str(selected)
            located = True
        return ResolvedSource(active_path=path, source_root=path if path.is_dir() else path.parent)

    return source_resolution_policy(resolve)


def bootstrap():
    """Explicit fixture-only child bootstrap, before importing any vendor SDK.

    Socket denial is installed FIRST, including HTTPX2's independent path. Even
    failed/unexpected routes cannot contact a real account. The profile observer
    is unnecessary: transport-local GC inspection reads the real Worker only.
    """
    import atexit
    from unittest.mock import patch

    root = Path(os.environ["NORMAL_MEDIA_PIPE_ROOT"]).resolve(strict=True)
    label = os.environ["NORMAL_MEDIA_PIPE_CHILD"]
    report = root / (label + "-observer.json")
    stack = ExitStack()
    denied = deny_socket_connections(stack, root / (label + "-socket-denials.json"))
    # No imports capable of starting an SDK precede those two guards.
    fixture = helpers()
    import importlib
    import importlib.util
    import gc
    import httpx

    class PipeResponses(fixture.OfflineResponses):
        def __init__(self, pixels):
            super().__init__(pixels)
            self.owners, self.snapshots = [], []
            self.expected_handles = {}

        async def receive(self, request):
            try:
                import amplifier_web.runtime_worker as module
                owners = [obj for obj in gc.get_objects() if type(obj) is module.Worker]
                assert len(owners) == 1, "Actual file entry did not use the canonical Worker class"
                owner = owners[0]
                assert owner.command.__func__ is module.Worker.command
                assert owner.command.__func__.__globals__["publish"] is module.publish
                assert module.publish.__module__ == "amplifier_web.runtime_worker"
                assert module._PROTOCOL is not None
                assert str(ROOT) not in sys.path, "Outer app/source root leaked into the child dependency path"
                assert fixture.catalog_status() == catalog
                assert owner.media_graph_selection is not None
                assert owner.canonical_binding is not None
                assert owner.canonical_resume is (label == "second")
                assert not owner.controls.capacity.policy["enabled"]
                self.check_installed_boundary(owner)
                if not self.owners:
                    self.owners.append(owner)
                else:
                    assert self.owners == [owner]
                result = await super().receive(request)
                media = owner.session.coordinator.get_capability("web.media.store")
                self.snapshots.append({
                    "worker_class": type(owner).__module__ + "." + type(owner).__qualname__,
                    "wire_http_package": type(request).__module__.split(".")[0],
                    "worker_id": id(owner), "session_id": id(owner.session),
                    "execution_id": id(owner.execution), "shared_id": id(owner.shared_handle),
                    "binding_id": id(owner.canonical_binding), "media_id": id(media),
                    "canonical_directory": str(owner.canonical_binding.directory),
                    "media_root": str(media.root), "media_session_id": media.session_id,
                    "resumed": owner.canonical_resume,
                })
                self.dump(False)
                return result
            except BaseException as error:
                if not any(error is item for item in self.failures):
                    self.failures.append(error)
                self.dump(False)
                raise

        def dump(self, final):
            save_json(report, {
                "final": final, "pid": os.getpid(), "records": self.records,
                "snapshots": self.snapshots, "socket_denials": denied,
                "failures": [type(item).__name__ + ": " + str(item) for item in self.failures],
            })

    pixels = (root / "private.png").read_bytes()
    transport = PipeResponses(pixels)
    # Deliberately do not use OfflineResponses.installed(): this bootstrap must
    # install socket denial before importing either independent HTTP package.
    packages = [httpx]
    if importlib.util.find_spec("httpx2") is not None:
        packages.append(importlib.import_module("httpx2"))
    for package in packages:
        mock = package.MockTransport(transport.receive)

        def boundary(mock_transport):
            async def handle(_transport, request):
                return await mock_transport.handle_async_request(request)
            return handle

        handle = boundary(mock)
        transport.expected_handles[package.AsyncHTTPTransport] = handle
        stack.enter_context(patch.object(package.AsyncHTTPTransport, "handle_async_request", handle))

    declaration = fixture.authored_bundle(root)
    modules = json.loads((root / "source-modules.json").read_text())
    paths = [root / "fixture.md", *modules.values(),
             declaration["session"]["context"]["source"],
             *[row["source"] for row in declaration["providers"]],
             *[row["source"] for row in declaration["hooks"]]]
    stack.enter_context(finite_sources(paths, modules))
    catalog = json.loads((root / "catalog-observed.json").read_text())
    assert not catalog["mismatches"], {"boundary": "unchanged packaged catalog", **catalog}
    assert "amplifier_web.runtime_worker" not in sys.modules, "Fixture must not preload the Worker"
    transport.dump(False)

    def finish():
        try:
            assert not transport.failures
            assert not denied, "An unintercepted connection was attempted"
            transport.check_pairs(2 if label == "first" else 1)
            assert len(transport.owners) == 1
            owner = transport.owners[0]
            assert owner._cleanup_settled and owner.shared_handle is None
            assert owner.canonical_binding is None and owner.execution.done()
            assert owner.execution.exception() is None
            assert fixture.catalog_status() == catalog, "Catalog or dependency source changed during the probe"
            transport.dump(True)
            value = json.loads(report.read_text())
            value.update(private_requests_cleared=True, canonical_cleanup_joined=True,
                         file_bootstrapped_app_without_outer_root=True,
                         source=fixture.source_evidence(),
                         transport_packages=[package.__name__ for package in packages])
            fixture.no_pixels(value, pixels)
            save_json(report, value)
        except BaseException as error:
            transport.failures.append(error)
            transport.dump(True)
        finally:
            stack.close()

    atexit.register(finish)


def host_result(event):
    """Finite known-service fixture; unknown operations fail closed."""
    operation, args = event["operation"], event["args"]
    context = {"surfaces": [], "inputIds": args.get("_contextInputs", [])}
    current = {"sessionId": SESSION, "grants": [], "requests": [], "bounded": True}
    if operation == "context.prepare":
        return {"context.manifest": {"available": True, "value": context},
                "message.reactions.context": {"available": False},
                "coordination.current": {"available": True, "value": current},
                "memory.context": {"available": True, "value": {"items": []}}}
    if operation == "context.manifest":
        return context
    if operation == "coordination.current":
        return current
    if operation == "memory.context":
        return {"items": []}
    if operation == "capacity.admit":
        assert args["call"]["sessionId"] == SESSION
        # This fresh fixture has the ordinary disabled budget (also independently
        # checked on the actual child owner at count/send), not an account change.
        return {"allowed": True, "budgetRevision": 0}
    raise AssertionError("Unexpected pipe fixture bridge: " + operation)


class Pipe:
    def __init__(self, process, pixels):
        self.process, self.pixels = process, pixels
        self.events, self.commands = [], []
        self.changed, self.eof = asyncio.Event(), False
        self.eof_index = None
        self.reader = asyncio.create_task(self.read())
        self.stderr = asyncio.create_task(process.stderr.read(2_000_000))

    async def write(self, command):
        from amplifier_web.runtime_protocol import encode_message
        self.commands.append(command["op"])
        self.process.stdin.write(encode_message(command))
        await self.process.stdin.drain()

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                event = json.loads(line)
                helpers().no_pixels(event, self.pixels)
                self.events.append(event)
                assert event.get("type") not in {
                    "error", "runtime.error", "worker.error", "generation.failed"}, event
                assert not (event.get("op") == "reply" and event.get("error")), event
                if event.get("op") == "bridge":
                    await self.write({"op": "bridge.result", "id": event["id"],
                                      "result": host_result(event)})
                self.changed.set()
            self.eof_index = len(self.events)
        finally:
            self.eof = True
            self.changed.set()

    async def until(self, predicate, *, after=0):
        async with asyncio.timeout(35):
            while True:
                self.changed.clear()
                for event in self.events[after:]:
                    if predicate(event):
                        return event
                if self.reader.done():
                    await self.reader
                    raise AssertionError("Worker EOF before required JSON reply")
                await self.changed.wait()

    async def turn(self, identity, upload=None):
        start = len(self.events)
        command = {"op": "send", "id": identity + "-request", "input_id": identity, "text": identity}
        if upload is not None:
            command["attachments"] = [upload]
        await self.write(command)
        reply = await self.until(lambda row: row.get("op") == "reply"
                                 and row.get("id") == identity + "-request", after=start)
        assert reply["result"]["accepted"] is True
        await self.until(lambda row: row.get("type") == "generation.finished"
                         and identity in row.get("input_ids", []), after=start)
        await self.until(lambda row: row.get("type") == "session.idle", after=start)

    async def retire(self, label):
        start = len(self.events)
        await self.write({"op": "retire", "id": label})
        reply = await self.until(lambda row: row.get("op") == "reply"
                                 and row.get("id") == label, after=start)
        assert reply["result"] == {"retired": True}, reply
        # Observing the exact reply in the real stdout stream precedes observing
        # EOF there. stdin remains OPEN until the child has exited by itself.
        assert not self.process.stdin.is_closing()
        async with asyncio.timeout(15):
            await self.reader
            await self.process.wait()
        assert self.process.returncode == 0
        self.process.stdin.close()
        stderr = (await self.stderr).decode(errors="replace")
        helpers().no_pixels(stderr, self.pixels)
        forbidden = ("Traceback (most recent call last)", "Task was destroyed",
                     "Task exception was never retrieved", "coroutine was never awaited",
                     "Exception ignored in", "Error in sitecustomize")
        assert not any(text in stderr for text in forbidden), stderr
        allowed_naming_warning = (
            "Error generating name for session " + SESSION[:8] +
            ": Naming cannot attribute the selected provider request; no call was sent.")
        assert all(line == allowed_naming_warning for line in stderr.splitlines()), stderr
        assert sum(row.get("type") == "session.closed" for row in self.events) == 1
        reply_index = next(index for index, row in enumerate(self.events) if row is reply)
        assert self.eof_index is not None and reply_index < self.eof_index
        return {"reply_before_eof": reply_index < self.eof_index,
                "retirement_reply_index": reply_index, "stdout_eof_index": self.eof_index,
                "exit_code": self.process.returncode,
                "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
                "stderr_lines": stderr.splitlines(), "event_count": len(self.events),
                "commands": self.commands,
                "bridge_operations": sorted({row["operation"] for row in self.events if row.get("op") == "bridge"})}


def manager_command(python, root):
    """Read the real manager's final file path, without preparation/provisioning.

    Only its finite launcher setup lookups are substituted. We run the selected
    prepared interpreter directly, not uv, and do not claim manager preparation.
    """
    from unittest.mock import patch
    from amplifier_web.runtime import RuntimeManager
    project = python.parent.parent
    with patch("amplifier_web.host_dependencies.require_uv", return_value="uv"), \
            patch("amplifier_web.runtime_environment.prepare_project", return_value=project), \
            patch("amplifier_web.runtime_environment.receipt_directory", return_value=project):
        selected = RuntimeManager()._command(release=None, home=root / "home")
    script = Path(selected[-1]).resolve(strict=True)
    assert script == ROOT / "amplifier_web/runtime_worker.py"
    return [str(python), str(script)], selected


def checkpoint(directory, pixels):
    from amplifier_foundation.session.transcript_source import open_committed_source
    with open_committed_source(directory) as source:
        assert source.commit.message_count < 100
        messages = source.window(0, 100).messages
        commit = source.commit
    helpers().no_pixels(messages, pixels)
    rows = [row for row in messages if helpers().MEDIA in (row.get("metadata") or {})]
    assert len(rows) == 1 and len(rows[0]["metadata"][helpers().MEDIA]["refs"]) == 1
    return commit, messages, rows[0]["metadata"][helpers().MEDIA]["refs"][0]


def custody(snapshot, commit, held):
    from amplifier_foundation.session.media import MediaRefusal, SessionMediaStore
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding, VersionedSessionConflict
    try:
        media = SessionMediaStore(snapshot["media_root"], snapshot["media_session_id"])
    except MediaRefusal as error:
        assert held and "active writer" in str(error)
    else:
        media.close()
        assert not held, "Idle child lost its physical media writer"
    try:
        binding = VersionedSessionBinding.resume(snapshot["canonical_directory"],
                                                session_id=SESSION, expected=commit)
    except VersionedSessionConflict as error:
        assert held and "retains the source lease" in str(error)
    else:
        binding.close()
        assert not held, "Idle child lost its physical canonical writer"


async def main():
    fixture = helpers()
    root = Path(sys.argv[1]).absolute()
    private_parent(root)  # never repair/chmod an existing nonprivate parent
    os.environ.update(HOME=str(root / "os-home"), AMPLIFIER_HOME=str(root / "amplifier-home"),
        AMPLIFIER_WEB_HOME=str(root / "home"), AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
        AMPLIFIER_RUNTIME_IMMUTABLE="1", UV_OFFLINE="1")
    catalog = fixture.catalog_status()
    assert not catalog["mismatches"], {"boundary": "packaged source catalog", **catalog}
    save_json(root / "catalog-observed.json", catalog)
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.attachments import save
    from PIL import Image

    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    bundle = root / "fixture.md"
    bundle.write_text("---\n" + json.dumps(fixture.authored_bundle(root)) + "\n---\nInspect the image.\n")
    modules = {name: str(fixture.origin(package).parent) for name, package in (
        ("loop-live", "amplifier_module_loop_live"),
        ("hook-context-intelligence", "amplifier_module_hook_context_intelligence"))}
    save_json(root / "source-modules.json", modules)
    settings = root / "amplifier-home/settings.yaml"
    settings.parent.mkdir(mode=0o700)
    save_json(settings, {"bundle": {"active": str(bundle), "app": []},
                         "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)},
                         "sources": {"modules": modules}})
    raw = io.BytesIO()
    Image.new("RGB", (2, 2), (19, 71, 149)).save(raw, format="PNG")
    pixels = raw.getvalue()
    (root / "private.png").write_bytes(pixels)
    (root / "private.png").chmod(0o600)
    upload = save(root / "home", "normal.png", base64.b64encode(pixels).decode())
    bootstrap_dir = root / "fixture-bootstrap"
    bootstrap_dir.mkdir(mode=0o700)
    # Explicit child PYTHONPATH, not a production test hook or sys.modules alias.
    (bootstrap_dir / "sitecustomize.py").write_text(
        "from normal_media_pipe_probe import bootstrap\nbootstrap()\n")
    python = Path(sys.executable).absolute()
    command, selected = manager_command(python, root)
    dependencies = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                    if entry and Path(entry).resolve() != ROOT]
    env = {**os.environ, "NORMAL_MEDIA_PIPE_ROOT": str(root),
           "WARM_LOOP_SOURCE": modules["loop-live"],
           "PYTHONPATH": os.pathsep.join([str(bootstrap_dir), str(Path(__file__).parent),
                                         *dependencies])}
    config = {"id": SESSION, "workspace": str(workspace), "bundle": str(bundle)}
    children, receipts, reports = [], [], []
    saved = ref = None
    try:
        for label, turns in (("first", ("one", "two")), ("second", ("three",))):
            process = await asyncio.create_subprocess_exec(*command, cwd=workspace,
                env={**env, "NORMAL_MEDIA_PIPE_CHILD": label},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=2_000_000)
            pipe = Pipe(process, pixels)
            children.append(pipe)
            await pipe.write({"op": "start", "session": config})
            ready = await pipe.until(lambda row: row.get("type") == "runtime.ready")
            assert ready["workerIdentity"] == {"pid": process.pid, "sessionId": SESSION}
            assert ready["report"]["resumed"] is (label == "second")
            report_path = root / (label + "-observer.json")
            # Protocol roundtrip fences startup; no counted/sent request may have
            # occurred while resuming the exact saved transcript.
            await pipe.write({"op": "dependencies", "id": label + "-readback", "integrationValidation": True})
            await pipe.until(lambda row: row.get("op") == "reply" and row.get("id") == label + "-readback")
            assert not json.loads(report_path.read_text())["records"], "Startup replayed provider input"
            if saved is not None:
                _, restored, restored_ref = checkpoint(snapshot["canonical_directory"], pixels)
                assert restored == saved and restored_ref == ref
            for identity in turns:
                await pipe.turn(identity, upload if identity == "one" else None)
                # session.idle is an event, not writer release. Give the loop one
                # scheduling tick; all custody probes below are passive failures.
                await asyncio.sleep(.05)
                observed = json.loads(report_path.read_text())
                assert not observed["failures"] and not observed["socket_denials"], observed
                snapshot = observed["snapshots"][-1]
                commit, messages, current_ref = checkpoint(snapshot["canonical_directory"], pixels)
                if ref is None:
                    ref = current_ref
                assert current_ref == ref
                custody(snapshot, commit, True)
            receipts.append(await pipe.retire("retire-" + label))
            report = json.loads(report_path.read_text())
            assert report["final"] and not report["failures"] and not report["socket_denials"], report
            assert report["private_requests_cleared"] and report["canonical_cleanup_joined"]
            reports.append(report)
            commit, saved, current_ref = checkpoint(snapshot["canonical_directory"], pixels)
            assert current_ref == ref
            custody(snapshot, commit, False)
        assert children[0].process.pid != children[1].process.pid
        users = [row for row in saved if (row.get("metadata") or {}).get("amplifier_input", {}).get("kind") == "user"]
        assert [row["content"] if isinstance(row["content"], str) else row["content"][0]["text"]
                for row in users] == ["one", "two", "three"]
        assert sum(row.get("type") == "assistant.message" for pipe in children for row in pipe.events) == 3
        assert fixture.catalog_status() == catalog
        for report in reports:
            snapshots = report["snapshots"]
            assert all(row == snapshots[0] for row in snapshots)
        evidence = {"mode": "source-only", "installed_package_parity": False,
                    "real_file_entry": command, "manager_selected_command_not_executed": selected,
                    "actual_vendor_provider_and_sdk": True, "offline_transport_not_inference": True,
                    "two_turn_idle_custody": True, "fresh_process_resume_without_replay": True,
                    "canonical_t2_ack": True, "canonical_and_protocol_no_pixels": True,
                    "receipts": receipts, "children": reports}
        fixture.no_pixels(evidence, pixels)
        print(json.dumps(evidence))
    finally:
        # Only our own newly launched test children. No unknown live worker,
        # daemon, DTU, account or managed-resource mutation is involved.
        for pipe in children:
            if pipe.process.returncode is None:
                pipe.process.terminate()
                try:
                    await asyncio.wait_for(pipe.process.wait(), 10)
                except TimeoutError:
                    pipe.process.kill()
                    await pipe.process.wait()
            await asyncio.gather(pipe.reader, pipe.stderr, return_exceptions=True)
            stderr = pipe.stderr.result()
            fixture.no_pixels(stderr.decode(errors="replace"), pixels)
            save_json(root / ("child-" + str(pipe.process.pid) + "-stderr.json"),
                      {"stderr": stderr.decode(errors="replace")})


if __name__ == "__main__":
    asyncio.run(main())