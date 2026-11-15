"""Offline source qualification of ordinary Worker + actual OpenAI Responses SDK.

Run only with an already prepared interpreter/PYTHONPATH and a fresh empty root.
The authored custom bundle explicitly opts out of optional app defaults. No
provider, graph, checkpoint, source-lease or generation implementation is mocked.
Only the HTTPX network transport and finite host JSON bridge are fixture-owned.
This is source evidence, NOT an installed-package, live-vendor or DTU claim.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import closing, contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-6.1-sol"
MEDIA = "experimental.media.v1"
DUMMY_KEY = "offline-normal-worker-not-a-credential"
PACKAGES = ("amplifier_core", "amplifier_foundation", "amplifier_module_loop_live",
            "amplifier_module_loop_streaming", "amplifier_module_context_simple",
            "amplifier_module_provider_openai", "amplifier_module_hook_context_intelligence",
            "context_intelligence")


def origin(package):
    spec = importlib.util.find_spec(package)
    assert spec and spec.origin, f"Missing already prepared dependency: {package}"
    return Path(spec.origin).resolve(strict=True)


def catalog_status():
    """Read-only diagnosis; current bytes NEVER become expected source hashes."""
    path = ROOT / "amplifier_web/host/media_sources.json"
    catalog = json.loads(path.read_text())
    mismatches = []
    for logical, expected in catalog["sources"].items():
        package, relative = logical.split("/", 1)
        source = origin(package).parent / relative
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != expected:
            mismatches.append({"path": str(source), "expected": expected, "actual": actual})
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "recipe": catalog["recipe"], "revisions": catalog["revisions"],
            "mismatches": mismatches}


def authored_bundle(root):
    """Ordinary authored declaration, not a prepared/session-derived mount plan."""
    return {
        "bundle": {"name": "normal-media-worker", "version": "0.0.1"},
        "session": {
            "orchestrator": {"module": "loop-live", "source": str(origin("amplifier_module_loop_live").parent)},
            "context": {"module": "context-simple", "source": str(origin("amplifier_module_context_simple").parent.parent)},
        },
        "providers": [{"module": "provider-openai",
                       "source": str(origin("amplifier_module_provider_openai").parent.parent),
                       "config": {"api_key": DUMMY_KEY, "default_model": MODEL,
                                  "base_url": "https://api.openai.com/v1", "use_streaming": False,
                                  "max_output_tokens": 64}}],
        "hooks": [{"module": "hook-context-intelligence",
                   "source": str(origin("amplifier_module_hook_context_intelligence").parent.parent),
                   "config": {"destinations": {}, "context_intelligence_server_url": "",
                              "context_intelligence_api_key": "", "base_path": str(root / "capture")}}],
    }


def response(turn):
    # Same finite non-streaming Responses shape used by the provider's own SDK
    # transport tests; these are offline fixture replies, not vendor observations.
    return {"id": f"resp_offline_{turn}", "object": "response", "created_at": 0,
            "model": MODEL, "status": "completed", "output": [
                {"type": "message", "id": f"msg_offline_{turn}", "role": "assistant",
                 "status": "completed", "content": [
                     {"type": "output_text", "text": "offline image inspected", "annotations": []}]}],
            "usage": {"input_tokens": 30, "output_tokens": 3, "total_tokens": 33}}


def private_png(body):
    images = [block for item in body["input"] if isinstance(item, dict)
              for block in item.get("content", []) if isinstance(block, dict)
              and block.get("type") == "input_image"]
    assert len(images) == 1, "Expected one real private PNG at the SDK wire boundary"
    prefix = "data:image/png;base64,"
    assert images[0]["image_url"].startswith(prefix)
    return base64.b64decode(images[0]["image_url"][len(prefix):], validate=True)


def no_pixels(value, pixels):
    encoded = json.dumps(value, default=str)
    assert base64.b64encode(pixels).decode() not in encoded, "Pixels escaped the private request"
    assert '"type": "image"' not in encoded and "data:image/" not in encoded


class OfflineResponses:
    def __init__(self, pixels):
        self.pixels, self.records, self.requests, self.providers = pixels, [], [], []
        self.failures = []

    async def receive(self, request):
        """Observe real provider scope read-only; never mint/replace a witness."""
        import importlib
        httpx = importlib.import_module(type(request).__module__.split(".")[0])

        try:
            assert request.method == "POST"
            assert str(request.url) in {"https://api.openai.com/v1/responses/input_tokens",
                                        "https://api.openai.com/v1/responses"}, str(request.url)
            from openai import AsyncOpenAI
            from amplifier_module_provider_openai import OpenAIProvider, _media
            assert request.headers["authorization"] == "Bearer " + DUMMY_KEY
            body = json.loads(request.content)
            assert body["model"] == MODEL and body.get("stream") is not True
            assert private_png(body) == self.pixels
            scope = _media.CURRENT.get()  # diagnostic observation only, no ContextVar writes
            assert scope is not None and type(scope.provider) is OpenAIProvider
            assert type(scope.provider.client) is AsyncOpenAI
            assert _media.standard_dispatch(scope.provider.client), "SDK dispatch was replaced"
            assert scope.cap is not None and scope.cap.enabled is True
            images = [block for message in scope.request.messages
                      if isinstance(message.content, list) for block in message.content
                      if getattr(block, "type", None) == "image"]
            assert len(images) == 1
            assert base64.b64decode(images[0].source["data"], validate=True) == self.pixels
            coordinator = scope.provider.coordinator
            store = coordinator.get_capability("web.media.store")
            # Require the real canonical ACK at the point of count AND send.
            refs = [ref for row in await coordinator.get("context").get_messages()
                    for ref in (row.get("metadata") or {}).get(MEDIA, {}).get("refs", [])]
            assert len(refs) == 1 and store.validate_ref(refs[0]) == refs[0]
            phase = "count" if request.url.path.endswith("/input_tokens") else "send"
            self.requests.append(scope.request)
            self.providers.append(scope.provider)
            self.records.append({"phase": phase, "request_id": id(scope.request),
                                 "provider_id": id(scope.provider),
                                 "image_sha256": hashlib.sha256(self.pixels).hexdigest()})
            if phase == "count":
                return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 30})
            return httpx.Response(200, json=response(sum(row["phase"] == "send" for row in self.records)))
        except BaseException as error:
            self.failures.append(error)
            raise

    @contextmanager
    def installed(self):
        """Replace only HTTPX's bottom network boundary, including proxy routes.

        Actual AsyncOpenAI, HTTPX clients, resource methods, auth, provider and
        late witness checks remain untouched. MockTransport never opens a socket.
        """
        import httpx
        import importlib
        import socket
        from contextlib import ExitStack
        from unittest.mock import patch
        self.expected_handles = {}
        def boundary(package):
            transport = package.MockTransport(self.receive)
            async def handle(_transport, request):
                return await transport.handle_async_request(request)
            self.expected_handles[package.AsyncHTTPTransport] = handle
            return handle

        def no_socket(*args, **kwargs):
            raise AssertionError("Offline fixture refused an unintercepted socket connection")
        with ExitStack() as stack:
            packages = [httpx]
            if importlib.util.find_spec("httpx2") is not None:
                packages.append(importlib.import_module("httpx2"))
            for package in packages:
                stack.enter_context(patch.object(package.AsyncHTTPTransport, "handle_async_request", boundary(package)))
            stack.enter_context(patch.object(socket.socket, "connect", no_socket))
            stack.enter_context(patch.object(socket.socket, "connect_ex", no_socket))
            yield

    def check_installed_boundary(self, worker):
        """Read-only diagnosis of actual SDK transport without sending input."""
        import httpx
        providers = worker.session.coordinator.get("providers")
        wrapped = next(iter(providers.values()))
        provider = wrapped
        for _ in range(8):
            if type(provider).__name__ == "OpenAIProvider":
                break
            provider = provider.original
        client = provider.client._client
        transports = [client._transport, *[value for value in client._mounts.values() if value is not None]]
        facts = [{"class": type(value).__module__ + "." + type(value).__qualname__,
                  "matches": getattr(value.handle_async_request, "__func__", None) is self.expected_handles.get(type(value)),
                  "class_identity": type(value) is httpx.AsyncHTTPTransport}
                 for value in transports]
        assert all(row["matches"] for row in facts), facts

    def check_pairs(self, turns):
        if self.failures:
            raise self.failures[0]
        pending, sends = [], 0
        for row, request in zip(self.records, self.requests):
            if row["phase"] == "count":
                pending.append(request)
            else:
                assert pending and all(counted is request for counted in pending), (
                    "The actual provider did not send its counted private request")
                pending.clear()
                sends += 1
        assert not pending and sends == turns, self.records
        assert all(request.messages == [] for request in self.requests), "Private requests were not released"


class HostBridge:
    """The same finite preparation JSON service as warm_worker_probe, no actions."""
    def __init__(self, pixels):
        self.pixels, self.events, self.workers = pixels, [], []

    def write(self, payload):
        # The actual Worker.publish/encode_message path remains unchanged.
        # Its protocol sink receives exactly one bounded JSON frame per write.
        self.publish(json.loads(payload))
        return len(payload)

    def flush(self):
        pass

    def publish(self, event):
        no_pixels(event, self.pixels)
        self.events.append(event)
        if event.get("op") != "bridge":
            return
        owner = next(worker for worker in self.workers if event["id"] in worker.bridges)
        operation = event["operation"]
        context = {"surfaces": [], "inputIds": event["args"].get("_contextInputs", [])}
        current = {"sessionId": owner.session.session_id, "grants": [], "requests": [], "bounded": True}
        if operation == "context.prepare":
            result = {"context.manifest": {"available": True, "value": context},
                      "message.reactions.context": {"available": False},
                      "coordination.current": {"available": True, "value": current},
                      "memory.context": {"available": True, "value": {"items": []}}}
        elif operation == "context.manifest":
            result = context
        elif operation == "coordination.current":
            result = current
        elif operation == "memory.context":
            result = {"items": []}
        elif operation == "capacity.admit" and not owner.controls.capacity.policy["enabled"]:
            result = {"allowed": True, "budgetRevision": owner.controls.capacity.policy["revision"]}
        else:
            raise AssertionError(f"Unexpected normal-worker fixture bridge: {operation}")
        owner.bridges[event["id"]].set_result(result)


async def qualified(worker):
    from warm_worker_probe import require_media
    assert worker.session is not None
    for _ in range(250):
        cap = worker.session.coordinator.get_capability(MEDIA)
        if cap is not None and cap.enabled is True:
            return
        if worker.media_graph_selection is None:
            raise AssertionError({"boundary": "normal authored-declaration selection",
                                  "catalog": catalog_status()})
        await asyncio.sleep(.02)
    # Bounded diagnostic never expands the production selection.
    require_media(worker, worker.media_graph_selection)


async def send(worker, bridge, transport, input_id, upload=None):
    command = {"op": "send", "id": input_id + "-request", "input_id": input_id, "text": input_id}
    if upload is not None:
        command["attachments"] = [upload]
    await worker.command(command)
    async with asyncio.timeout(20):
        while True:
            if transport.failures:
                raise transport.failures[0]
            if worker.idle() and any(event.get("type") == "generation.finished"
                                     and input_id in event.get("input_ids", []) for event in bridge.events):
                break
            errors = [event for event in bridge.events if event.get("type") in
                      {"error", "worker.error", "runtime.error", "generation.failed"}]
            assert not errors, errors
            await asyncio.sleep(.02)
    assert worker.shared_handle.active and not worker.bridges


def idle_custody(worker):
    from amplifier_foundation.session.media import MediaRefusal, SessionMediaStore
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding, VersionedSessionConflict
    media = worker.session.coordinator.get_capability("web.media.store")
    binding = worker.canonical_binding
    binding.check()
    try:
        competitor = SessionMediaStore(media.root, media.session_id)
    except MediaRefusal as error:
        assert "active writer" in str(error)
    else:
        competitor.close()
        raise AssertionError("Idle media writer custody was lost")
    try:
        competitor = VersionedSessionBinding.resume(binding.directory, session_id=binding.session_id,
                                                     expected=binding.expected)
    except VersionedSessionConflict as error:
        assert "retains the source lease" in str(error)
    else:
        competitor.close()
        raise AssertionError("Idle T2 custody was lost")


def source_evidence():
    import importlib.metadata
    imports = {package: {"path": str(origin(package)),
                         "sha256": hashlib.sha256(origin(package).read_bytes()).hexdigest()}
               for package in PACKAGES}
    loaded = {}
    for name, module in tuple(sys.modules.items()):
        if not any(name == package or name.startswith(package + ".") for package in PACKAGES):
            continue
        filename = getattr(module, "__file__", None)
        if filename and Path(filename).is_file():
            path = Path(filename).resolve()
            loaded[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {"mode": "source-only", "installed_package_parity": False, "imports": imports,
            "loaded_modules": loaded, "catalog": catalog_status(), "versions": {
                name: importlib.metadata.version(name) for name in ("amplifier-core", "openai", "httpx")}}


async def main():
    sys.path.insert(0, str(ROOT))
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.attachments import save
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from warm_worker_probe import local_sources, source_overlay, media_checkpoint
    from PIL import Image
    from unittest.mock import patch
    import amplifier_web.runtime_worker as worker_module

    root = Path(sys.argv[1]).resolve(strict=True)
    assert root.is_dir() and not any(root.iterdir()), "Use a fresh empty probe root; never replay a prior run"
    root.chmod(0o700)
    os.environ.update(HOME=str(root / "os-home"), AMPLIFIER_HOME=str(root / "amplifier-home"),
        AMPLIFIER_WEB_HOME=str(root / "home"), AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"), AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
        AMPLIFIER_RUNTIME_IMMUTABLE="1", UV_OFFLINE="1")
    status = catalog_status()
    assert not status["mismatches"], {"boundary": "packaged source catalog", **status}
    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    bundle = root / "fixture.md"
    bundle.write_text("---\n" + json.dumps(authored_bundle(root)) + "\n---\nInspect the image.\n")
    modules = {module: str(origin(package).parent) for module, package in (
        ("loop-live", "amplifier_module_loop_live"), ("hook-context-intelligence", "amplifier_module_hook_context_intelligence"))}
    source_overlay(modules)  # explicit source-only loop locator, not graph selection
    settings = root / "amplifier-home/settings.yaml"
    settings.parent.mkdir(mode=0o700)
    settings.write_text(json.dumps({"bundle": {"active": str(bundle), "app": []},
        "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)}, "sources": {"modules": modules}}))
    raw = io.BytesIO()
    Image.new("RGB", (2, 2), (19, 71, 149)).save(raw, format="PNG")
    pixels = raw.getvalue()
    upload = save(root / "home", "normal.png", base64.b64encode(pixels).decode())
    bridge, transport = HostBridge(pixels), OfflineResponses(pixels)
    config = {"id": "normal-media-worker", "workspace": str(workspace), "bundle": str(bundle)}
    paths = [bundle, *modules.values(), *[Path(row["source"]) for row in authored_bundle(root)["providers"]],
             Path(authored_bundle(root)["session"]["context"]["source"]),
             Path(authored_bundle(root)["hooks"][0]["source"])]
    # Tasks inherit finite resolver admission for startup, warm turns and resume.
    with local_sources(paths), transport.installed(), patch.object(worker_module, "_PROTOCOL", bridge):
        worker = Worker()
        bridge.workers.append(worker)
        assert worker.media_graph_selection is None and worker.canonical_binding is None
        try:
            await worker.start(config)
            assert worker.session is not None, bridge.events
            await qualified(worker)
            transport.check_installed_boundary(worker)
            binding = worker.canonical_binding
            assert type(binding) is VersionedSessionBinding and worker.canonical_resume is False
            assert callable(worker.canonical_binding_factory)
            assert (binding.directory / "transcript.jsonl").is_dir()
            assert (binding.directory / "transcript.jsonl.backup").is_dir()
            session, execution, shared = worker.session, worker.execution, worker.shared_handle
            media = session.coordinator.get_capability("web.media.store")
            await send(worker, bridge, transport, "one", upload)
            transport.check_pairs(1)
            ref = media_checkpoint(worker, binding.load_messages())
            idle_custody(worker)
            await send(worker, bridge, transport, "two")
            transport.check_pairs(2)
            assert worker.session is session and worker.execution is execution and worker.shared_handle is shared
            assert worker.canonical_binding is binding and worker.session.coordinator.get_capability("web.media.store") is media
            media_checkpoint(worker, binding.load_messages(), ref)
            assert binding.last_receipt is not None and binding.last_result.commit == binding.expected
            idle_custody(worker)
            await worker.command({"op": "retire", "id": "retire-one"})
            assert worker.shutdown.is_set()
            await worker.run()
            assert not shared.active
            assert any(event.get("type") == "session.closed" for event in bridge.events)
            assert not [task for task in asyncio.all_tasks() if not task.done()
                        and getattr(task.get_coro(), "cr_code", None) is not None
                        and task.get_coro().cr_code.co_filename.endswith("/amplifier_core/_session_exec.py")]
            prefix = binding.expected
            with closing(SessionMediaStore(media.root, media.session_id)):
                pass
            reopened = VersionedSessionBinding.resume(binding.directory, session_id=binding.session_id, expected=prefix)
            try:
                saved = reopened.load_messages()
            finally:
                await reopened.aclose()
            before = len(transport.records)
            restored = Worker()
            bridge.workers.append(restored)
            await restored.start(config)
            assert restored.session is not None, bridge.events
            await qualified(restored)
            assert restored.canonical_resume is True and restored.canonical_binding is not binding
            assert restored.session is not session and restored.canonical_binding.load_messages() == saved
            assert len(transport.records) == before, "Startup replayed earlier provider input"
            media_checkpoint(restored, saved, ref)
            await send(restored, bridge, transport, "three")
            transport.check_pairs(3)
            sent_providers = [provider for provider, row in zip(transport.providers, transport.records)
                              if row["phase"] == "send"]
            assert sent_providers[0] is sent_providers[1]
            assert sent_providers[2] is not sent_providers[0]
            messages = restored.canonical_binding.load_messages()
            media_checkpoint(restored, messages, ref)
            users = [row for row in messages if (row.get("metadata") or {}).get("amplifier_input", {}).get("kind") == "user"]
            assert [row["content"] if isinstance(row["content"], str) else row["content"][0]["text"] for row in users] == ["one", "two", "three"]
            assert len([event for event in bridge.events if event.get("type") == "assistant.message"]) == 3
            no_pixels(messages, pixels)
            no_pixels(bridge.events, pixels)
            idle_custody(restored)
            final_binding = restored.canonical_binding
            final_shared = restored.shared_handle
            new_media = restored.session.coordinator.get_capability("web.media.store")
            await restored.command({"op": "retire", "id": "retire-two"})
            await restored.run()
            assert not final_shared.active
            assert len([event for event in bridge.events if event.get("type") == "session.closed"]) == 2
            assert not [task for task in asyncio.all_tasks() if not task.done()
                        and getattr(task.get_coro(), "cr_code", None) is not None
                        and task.get_coro().cr_code.co_filename.endswith("/amplifier_core/_session_exec.py")]
            with closing(SessionMediaStore(new_media.root, new_media.session_id)):
                pass
            reopened = VersionedSessionBinding.resume(final_binding.directory, session_id=final_binding.session_id,
                                                       expected=final_binding.expected)
            await reopened.aclose()
            print(json.dumps({"ordinary_worker_constructor": True, "production_catalog_recipe": status["recipe"],
                "actual_vendor_provider_and_sdk": True, "offline_httpx_transport": True,
                "count_send_same_private_request": True, "canonical_t2_ack": True,
                "same_owner_two_turns": True, "idle_physical_custody": True, "retired_safely": True,
                "fresh_resume_without_replay": True, "private_requests_cleared": True,
                "canonical_and_host_events_no_pixels": True, "records": transport.records,
                "source": source_evidence()}))
        finally:
            for owner in bridge.workers:
                if owner.session is not None and owner.shared_handle is not None and owner.shared_handle.active:
                    owner.shutdown.set()
                    await owner.run()


if __name__ == "__main__":
    asyncio.run(main())
