"""Run the actual Worker/loop-live warm lifecycle with a local provider.

This fixture deliberately runs in the isolated runtime environment with a
caller-provided Foundation checkout.  It never uses a provider credential,
network model request, real home, or existing session state.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import nullcontext
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from amplifier_web.runtime_worker import Worker

MEDIA = "experimental.media.v1"

# Copied into the fixture's private local provider, not installed or downloaded.
# Only digests/counts/identities are retained; neither count nor send logs pixels.
MEDIA_PROVIDER = '''import base64
import hashlib
import json
import os
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock

class FixtureProvider:
    name = "fixture"
    default_model = "fixture"
    def __init__(self, config=None, *, coordinator=None):
        self.coordinator = coordinator
        self.counted = None
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model": "fixture"})
    async def list_models(self):
        return []
    def get_media_request_support(self, *, model, request_options):
        return {"v": 1, "positions": ["user"]}
    def record(self, phase, request):
        images = [block for message in request.messages
                  for block in message.content if isinstance(message.content, list)
                  if getattr(block, "type", None) == "image"]
        assert len(images) == 1, "Expected exactly one private uploaded image"
        source = images[0].source
        assert source["type"] == "base64" and source["media_type"] == "image/png"
        digest = hashlib.sha256(base64.b64decode(source["data"], validate=True)).hexdigest()
        assert digest == os.environ["WARM_MEDIA_SHA256"]
        row = {"phase": phase, "image_sha256": digest, "image_count": len(images),
               "request_id": id(request), "provider_id": id(self)}
        path = Path(os.environ["WARM_MEDIA_RECORD"])
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\\n")
    async def request_budget(self, request, *, request_options=None, **kwargs):
        self.coordinator.get_capability("experimental.media.v1").validate(
            request, purpose="count", request_options=request_options or {})
        self.record("count", request)
        self.counted = request
        return {"estimated_input_tokens": 50, "input_tokens": 50,
                "input_limit_tokens": 100000, "context_token_budget": 90000,
                "max_output_tokens": 64}
    async def complete(self, request, **kwargs):
        self.coordinator.get_capability("experimental.media.v1").validate(
            request, purpose="transport", request_options=kwargs)
        assert request is self.counted, "Transport did not receive the counted private request"
        self.record("send", request)
        self.counted = None
        return ChatResponse(content=[TextBlock(text="fixture response")])
    def parse_tool_calls(self, response):
        return []

async def mount(coordinator, config=None):
    await coordinator.mount("providers", FixtureProvider(coordinator=coordinator), name="fixture")
'''


def expected_media_plan(root, context_module, capture_module):
    """Fixed declaration plus known host/Foundation preparation transforms.

    Never takes a session, actual plan or construction receipt as an input.
    """
    from amplifier_foundation.bundle._observability import (
        FOUNDATION_OBSERVABILITY_EVENTS, inject_additional_events,
    )
    from amplifier_web.host.session import LOOP_SOURCE
    from amplifier_web.session_files import project_slug
    workspace = root / "workspace"
    plan = {
        "session": {
            "orchestrator": {"module": "loop-live", "source": LOOP_SOURCE,
                             "config": {"configured_bundle": True, "background_delegate": True}},
            "context": {"module": "context-simple", "source": str(Path(context_module).parent.parent)},
        },
        "providers": [{"module": "provider-fixture", "source": str(root / "provider"), "config": {}}],
        "hooks": [{"module": "hook-context-intelligence", "source": str(Path(capture_module).parent),
                   "config": {"destinations": {}, "context_intelligence_server_url": "",
                              "context_intelligence_api_key": "", "base_path": str(root / "capture")}}],
        "agents": {}, "application_host": "Amplifier Web", "root_session_id": "warm-fixture",
        "project_slug": project_slug(workspace), "bundle_name": str(root / "fixture.md"),
        "project_dir": str(workspace), "project_name": "workspace",
    }
    inject_additional_events(plan, FOUNDATION_OBSERVABILITY_EVENTS)
    return plan


def media_selection(root, context_module, capture_module):
    """Explicit cooperative source selection, never inferred from graph evidence."""
    from amplifier_core import HookResult, ModuleCoordinator, ProviderInfo
    from amplifier_core.message_models import ChatResponse, TextBlock
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.media import SourcePolicy
    from amplifier_web.host.media_graph import NamedMediaGraph
    from amplifier_web.memory_delivery import MemoryDelivery
    from amplifier_web.preparation_context import PreparedDelivery
    from functools import partial
    import asyncio
    import time
    import amplifier_web.runtime_worker as worker_module
    import amplifier_module_hook_context_intelligence as capture
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler
    from amplifier_module_hook_context_intelligence.config_resolver import HookConfigResolver

    # Enumerated construction owners only. Do not expand this by scanning a
    # checkout/site-packages/cache or automatically accepting discovered callbacks.
    names = (
        "amplifier_core.loader", "amplifier_module_loop_live.orchestrator",
        "amplifier_module_loop_live.runtime", "amplifier_module_loop_streaming",
        "amplifier_foundation.session.media_hook_bridge",
        "amplifier_foundation.session.media_projection",
        "amplifier_foundation.session.media", "amplifier_foundation.session.checkpoint",
        "amplifier_module_context_simple", "amplifier_module_hook_context_intelligence",
        "amplifier_web.host.media", "amplifier_web.host.session",
        "amplifier_web.host.initialization", "amplifier_web.runtime_worker",
        "amplifier_web.app_guidance", "amplifier_web.memory_delivery",
        "amplifier_web.preparation_context",
        "amplifier_module_hook_context_intelligence.handlers.logging_handler",
        "amplifier_module_hook_context_intelligence.config_resolver",
    )
    selected_modules = [importlib.import_module(name) for name in names]
    sys.path.insert(0, str(root / "provider"))
    provider_module = importlib.import_module("amplifier_module_provider_fixture")
    paths = [Path(__file__).resolve(),
             *[Path(module.__file__).resolve() for module in [*selected_modules, provider_module]]]
    # Global dependencies are explicitly chosen, not harvested from callbacks.
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator, ConversationProviderPin
    from amplifier_module_context_simple import SimpleContextManager
    import amplifier_module_loop_live.orchestrator as live_module
    import amplifier_module_context_simple as context_package
    plan = expected_media_plan(root, context_module, capture_module)
    return NamedMediaGraph(
        "actual-warm-worker-upload", plans=[plan],
        sources={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        dependencies=(HookResult, ModuleCoordinator, ProviderInfo, ChatResponse, TextBlock,
                      BundleLiveOrchestrator, ConversationProviderPin, SimpleContextManager,
                      provider_module.FixtureProvider, asyncio, time, worker_module.publish,
                      live_module.mount, context_package.mount, provider_module.mount, capture.mount,
                      capture.on_session_ready, LoggingHandler.__call__),
        capability_owners=(
            ("capture-handler", "context_intelligence._hook_state", ("logging_handler",)),
            ("capture-resolver", "context_intelligence.hook_config_resolver", ())),
        environments={
            "mount": {"config": None},
            (str(Path(context_module).resolve()), "mount"): {"config": None},
            "Worker.install_activity.<locals>.activity": {"activity_run_id": None},
            "ModuleLoader.load.<locals>.mount_with_config_fs": [
                {"config": plan["session"]["orchestrator"]["config"]},
                {"config": {}},
                {"config": plan["hooks"][0]["config"]},
            ],
        },
        owner_rules=(("media-source-policy", SourcePolicy, ("coordinator",)),
                     ("worker", Worker, ("media_graph_selection", "telemetry", "response_blocks")),
                     ("runtime", Runtime, ("session_id",)),
                     ("coordinator", ModuleCoordinator, ("session_id",)),
                     ("checkpoint", partial, ("func", "args", "keywords")),
                     ("capture-handler", LoggingHandler, ("_resolver",)),
                     ("capture-resolver", HookConfigResolver, ()),
                     ("request-delivery", PreparedDelivery, ("owner", "delivery"))),
    )


def require_media(worker, selection):
    """Bounded diagnostic only; rejected evidence never expands the selection."""
    coordinator = worker.session.coordinator
    capability = coordinator.get_capability(MEDIA)
    if capability is not None and capability.enabled is True:
        return
    graph = coordinator.get_capability("web.media.hook_graph")
    bridge = coordinator.get_capability("web.media.hook_bridge")
    rejected = []
    if graph is not None:
        evidence = graph.participants_evidence()
        for section, key in (("loads", "mount"), ("ready", "callback"), ("registrations", "original")):
            for item in evidence.get(section, ()):
                callback = item[key]
                if selection.accepts_callable(callback):
                    continue
                function = getattr(callback, "__func__", callback)
                code = getattr(function, "__code__", None)
                owner = getattr(callback, "__self__", None)
                captures = []
                for name, cell in zip(getattr(code, "co_freevars", ()),
                                      getattr(function, "__closure__", None) or ()):
                    try:
                        value = cell.cell_contents
                        item = {"name": name, "type": type(value).__module__ + "." + type(value).__qualname__}
                        if type(value) is dict:
                            item["keys"] = sorted(str(key) for key in value)[:24]
                        captures.append(item)
                    except ValueError:
                        captures.append({"name": name, "type": "<empty>"})
                globals_used = [{"name": name, "type": type(value).__module__ + "." + type(value).__qualname__}
                                for name in getattr(code, "co_names", ())
                                if name in getattr(function, "__globals__", {})
                                and (value := function.__globals__[name]) is not None
                                and type(value) not in (str, int, float, bool, bytes)]
                rejected.append({"section": section, "callback": getattr(function, "__qualname__", type(function).__qualname__),
                                 "source": code.co_filename if code else None,
                                 "line": code.co_firstlineno if code else None,
                                 "owner": type(owner).__module__ + "." + type(owner).__qualname__ if owner is not None else None,
                                 "captures": captures, "globals": globals_used})
                rejected[-1]["defaults"] = [
                    {"name": getattr(value, "__qualname__", type(value).__qualname__),
                     "accepted": selection.accepts_callable(value),
                     "module": getattr(value, "__module__", None)}
                    for value in (getattr(function, "__defaults__", None) or ())]
                if code is None and callable(callback):
                    rejected[-1]["binding_valid"] = selection._binding_valid(callback)
                    rejected[-1]["call_function_selected"] = any(
                        getattr(callback.__call__, "__func__", None) is value
                        for value in selection._dependencies)
                if len(rejected) >= 24:
                    break
            if len(rejected) >= 24:
                break
    raise AssertionError(json.dumps({
        "media_enabled": False, "expected_plan_matches": selection.accepts_plan(coordinator.config),
        "actual_plan_keys": sorted(coordinator.config), "has_graph": graph is not None,
        "rejected_callbacks": rejected,
        "bridge": bridge.receipt_status(bridge.graph_receipt) if bridge else None,
        "readiness": coordinator.get_capability("session.media.readiness"),
        "source_join": coordinator.get_capability("web.media.source_join") is not None,
    }, sort_keys=True))


def media_checkpoint(worker, messages, expected_ref=None):
    rows = [row for row in messages if MEDIA in (row.get("metadata") or {})]
    assert len(rows) == 1 and rows[0]["role"] == "user"
    refs = rows[0]["metadata"][MEDIA]["refs"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["provenance"]["kind"] == "upload"
    if expected_ref is not None:
        assert ref == expected_ref
    # committed=True requires the actual saved canonical-checkpoint ACK.
    assert worker.session.coordinator.get_capability("web.media.store").validate_ref(ref) == ref
    return ref


def media_evidence(root, pixels, messages, events):
    records = [json.loads(line) for line in (root / "private-provider.jsonl").read_text().splitlines()]
    assert [row["phase"] for row in records] == ["count", "send"] * 3
    digest = hashlib.sha256(pixels).hexdigest()
    for counted, sent in zip(records[::2], records[1::2]):
        assert counted["request_id"] == sent["request_id"]
        assert counted["provider_id"] == sent["provider_id"]
        assert counted["image_sha256"] == sent["image_sha256"] == digest
        assert counted["image_count"] == sent["image_count"] == 1
    assert records[1]["provider_id"] == records[3]["provider_id"]
    assert records[3]["provider_id"] != records[5]["provider_id"]
    # Check the actual canonical rows and captured event stream without printing
    # either. JSONL events are deliberately never read from disk.
    for value in (messages, events):
        encoded = json.dumps(value, default=str)
        assert base64.b64encode(pixels).decode() not in encoded
        assert '"type": "image"' not in encoded and "data:image/" not in encoded
    return {"counted_private_png": True, "count_send_same_request": True,
            "warm_same_provider": True, "resumed_fresh_provider": True,
            "canonical_ref_ack": True, "canonical_and_host_events_no_pixels": True}


def source_overlay(modules):
    """Opt in to an exact local loop locator, never claim installed parity.

    Production deliberately rejects a PYTHONPATH overlay that disagrees with its
    installed distribution. Only this fixture's locator changes; preparation,
    module loading and graph validation remain production implementations.
    """
    selected = os.environ.get("WARM_LOOP_SOURCE")
    if not selected:
        return None
    selected = Path(selected).resolve(strict=True)
    spec = importlib.util.find_spec("amplifier_module_loop_live")
    assert spec and spec.origin
    assert selected / "__init__.py" == Path(spec.origin).resolve(), (
        "WARM_LOOP_SOURCE must name the actual imported loop-live package", selected, spec.origin)
    import amplifier_web.host.session as host_session
    host_session.installed_loop_source = lambda: str(selected)
    modules["loop-live"] = str(selected)
    return selected


def local_sources(paths):
    """Deny every undeclared source before a resolver can download or install."""
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import source_resolution_policy
    from amplifier_foundation.sources.policy import SourceResolutionDenied

    admitted = {str(Path(path).resolve(strict=True)): Path(path).resolve(strict=True)
                for path in paths}

    def resolve(uri, *, base_path):
        path = admitted.get(uri)
        if path is None:
            raise SourceResolutionDenied(f"Warm fixture source is not explicitly local: {uri}")
        return ResolvedSource(active_path=path, source_root=path if path.is_dir() else path.parent)

    return source_resolution_policy(resolve)


def source_evidence(overlay):
    if overlay is None:
        return {"mode": "installed-locator"}
    imports = {}
    for package in ("amplifier_foundation", "amplifier_module_loop_live",
                    "amplifier_module_loop_streaming"):
        spec = importlib.util.find_spec(package)
        assert spec and spec.origin
        path = Path(spec.origin).resolve()
        imports[package] = {"path": str(path),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {"mode": "source-only", "installed_package_parity": False, "imports": imports}


async def main():
    root = Path(sys.argv[1]).resolve()
    use_media = os.environ.get("WARM_WORKER_MEDIA") == "1"
    use_versioned = os.environ.get("WARM_WORKER_T2") == "1"
    workspace = root / "workspace"
    provider = root / "provider" / "amplifier_module_provider_fixture"
    workspace.mkdir(parents=True)
    provider.mkdir(parents=True)
    if use_media:
        root.chmod(0o700)
    provider.joinpath("__init__.py").write_text(
        MEDIA_PROVIDER if use_media else """from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
class FixtureProvider:
    name = "fixture"
    def get_info(self): return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    async def complete(self, request, **kwargs): return ChatResponse(content=[TextBlock(text="fixture response")])
    def parse_tool_calls(self, response): return []
async def mount(coordinator, config=None): await coordinator.mount("providers", FixtureProvider(), name="fixture")
"""
    )
    from amplifier_module_context_simple import __file__ as context_module
    from amplifier_module_hook_context_intelligence import __file__ as capture_module

    bundle = root / "fixture.md"
    bundle.write_text(
        f"""---
bundle:
  name: warm-fixture
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
hooks:
  - module: hook-context-intelligence
    source: {Path(capture_module).parent.parent}
    config:
      destinations: {{}}
      context_intelligence_server_url: ''
      context_intelligence_api_key: ''
      base_path: {root / 'capture'}
---
Reply with the fixture response.
"""
    )
    os.environ.update(
        AMPLIFIER_HOME=str(root / "amplifier-home"),
        AMPLIFIER_WEB_HOME=str(root / "home"),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
    )
    pixels, upload = None, None
    if use_media:
        from PIL import Image
        from amplifier_web.attachments import save
        image = io.BytesIO()
        Image.new("RGB", (2, 2), (5, 37, 83)).save(image, format="PNG")
        pixels = image.getvalue()
        upload = save(root / "home", "warm-fixture.png", base64.b64encode(pixels).decode())
        os.environ.update(WARM_MEDIA_SHA256=hashlib.sha256(pixels).hexdigest(),
                          WARM_MEDIA_RECORD=str(root / "private-provider.jsonl"))
    # The fixture exercises worker lifecycle, not downloading optional community
    # behaviors. Keep its empty home and declared local provider deterministic.
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    modules = {}
    for name, package in [('loop-live', 'amplifier_module_loop_live'),
                          ('hook-context-intelligence', 'amplifier_module_hook_context_intelligence')]:
        spec = importlib.util.find_spec(package)
        assert spec and spec.origin, f'Missing prepared lifecycle dependency: {package}'
        modules[name] = str(Path(spec.origin).parent)
    overlay = source_overlay(modules)
    settings = root / "amplifier-home" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"bundle": {"active": str(bundle), "app": []},
                                   "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)},
                                   "sources": {"modules": modules}}))
    events = []
    import amplifier_web.runtime_worker as worker_module

    workers = []
    def publish(event):
        events.append(event)
        if event.get('op') == 'bridge':
            owner = next(worker for worker in workers if event['id'] in worker.bridges)
            operation = event['operation']
            if operation == 'context.prepare':
                result = {
                    'context.manifest': {'available': True, 'value': {
                        'surfaces': [], 'inputIds': event['args'].get('_contextInputs', [])}},
                    'message.reactions.context': {'available': False},
                    'coordination.current': {'available': True, 'value': {
                        'sessionId': owner.session.session_id, 'grants': [],
                        'requests': [], 'bounded': True}},
                    'memory.context': {'available': True, 'value': {'items': []}},
                }
            elif operation == 'context.manifest':
                result = {'surfaces': [], 'inputIds': event['args'].get('_contextInputs', [])}
            elif operation == 'coordination.current':
                result = {'sessionId': owner.session.session_id, 'grants': [], 'requests': [], 'bounded': True}
            elif operation == 'memory.context':
                result = {'items': []}
            elif operation == 'capacity.admit' and not owner.controls.capacity.policy['enabled']:
                result = {'allowed': True, 'budgetRevision': owner.controls.capacity.policy['revision']}
            else:
                raise AssertionError(f'Unexpected fixture bridge: {operation}')
            owner.bridges[event['id']].set_result(result)
    def guarded_publish(event):
        serialized = json.dumps(event, default=str)
        assert base64.b64encode(pixels).decode() not in serialized, "Pixels reached host publication"
        assert '"type": "image"' not in serialized and "data:image/" not in serialized, (
            "An image block reached host publication")
        publish(event)
    worker_module.publish = guarded_publish if use_media else publish
    selection = media_selection(root, context_module, capture_module) if use_media else None
    canonical_binding = None
    if use_versioned:
        from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
        from amplifier_web.session_files import sessions_dir
        destination = sessions_dir(workspace) / "warm-fixture"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.parent.chmod(0o700)
        canonical_binding = VersionedSessionBinding.create_new(
            destination, session_id="warm-fixture", operation_id="prospective-create", admit=lambda: None)
    worker = Worker(media_graph_selection=selection, canonical_binding=canonical_binding,
                    **({"canonical_resume": False} if use_versioned else {}))
    workers.append(worker)
    config = {"id": "warm-fixture", "workspace": str(workspace), "bundle": str(bundle)}
    if use_versioned:
        config.update(forkContext=True, messages=[{"role": "assistant", "text": "prospective fork context"}])
    cold_started = time.monotonic()
    source_scope = local_sources([bundle, provider.parent, Path(context_module).parent.parent,
                                 Path(capture_module).parent.parent, *modules.values()]) if overlay or use_media else nullcontext()
    # Child tasks inherit finite admission through warm turns and fresh resume.
    with source_scope:
        try:
            await worker.start(config)
            cold_seconds = time.monotonic() - cold_started
            assert worker.session is not None, events
            first_session, first_execution = id(worker.session), id(worker.execution)
            first_writer = worker.shared_handle
            if use_media:
                for _ in range(150):
                    if worker.session.coordinator.get_capability(MEDIA).enabled:
                        break
                    await asyncio.sleep(.02)
                require_media(worker, selection)
                assert not worker.session.coordinator.get_capability("live.host").registrations
            if use_versioned:
                assert any(row.get("content") == "prospective fork context"
                           for row in await worker.session.coordinator.get("context").get_messages())
            first_input = {"op": "send", "id": "one-request", "input_id": "one", "text": "one"}
            if use_media:
                first_input["attachments"] = [upload]
            await worker.command(first_input)
            for _ in range(200):
                if worker.idle() and any(event.get("type") == "generation.finished"
                                        and "one" in event.get("input_ids", []) for event in events):
                    break
                await asyncio.sleep(0.02)
            assert worker.idle() and worker.shared_handle.active, events
            assert not worker.bridges
            assert any(event.get('type') == 'runtime.bridge' and event.get('operation') == 'context.prepare'
                       and event.get('phase') == 'completed' for event in events), events
            assert not any(event.get('type') == 'runtime.bridge' and event.get('phase') == 'failed'
                           for event in events), events
            from amplifier_foundation.session.history import SessionHistoryStore
            from amplifier_web.session_files import sessions_dir
            history = SessionHistoryStore(sessions_dir(workspace) / config["id"])
            def authoritative_rows():
                current = next((owner.canonical_binding for owner in reversed(workers)
                                if owner.canonical_binding is not None), None)
                return current.load_messages() if use_versioned else history.load_messages()
            media_ref = media_checkpoint(worker, authoritative_rows()) if use_media else None
            warm_started = time.monotonic()
            await worker.command({"op": "send", "id": "two-request", "input_id": "two", "text": "two"})
            for _ in range(200):
                if worker.idle() and any(event.get("type") == "generation.finished"
                                        and "two" in event.get("input_ids", []) for event in events):
                    break
                await asyncio.sleep(0.02)
            assert worker.idle() and any(event.get("type") == "generation.finished"
                                        and "two" in event.get("input_ids", []) for event in events), events
            warm_seconds = time.monotonic() - warm_started
            checkpoint = {"messages": authoritative_rows()}
            assert worker.shared_store.read() is None
            assert id(worker.session) == first_session
            assert id(worker.execution) == first_execution
            assert worker.shared_handle is first_writer and first_writer.active
            assert [event["text"] for event in events if event.get("type") == "assistant.message"] == [
                "fixture response", "fixture response"]
            if use_media:
                media_checkpoint(worker, checkpoint["messages"], media_ref)
            if use_versioned:
                committed_prefix = canonical_binding.expected
                assert canonical_binding.last_result.commit == committed_prefix
                assert canonical_binding.last_receipt is not None
            else:
                assert [row["content"] for row in checkpoint["messages"] if row["role"] == "user"][-2:] == ["one", "two"]
            await worker.command({"op": "retire", "id": "retire-request"})
            assert worker.shutdown.is_set(), events
            await worker.run()
            assert not first_writer.active
            if use_versioned:
                committed_prefix = canonical_binding.expected  # Includes the actual terminal checkpoint.
            resumed_binding = (VersionedSessionBinding.resume(destination, session_id="warm-fixture",
                expected=committed_prefix) if use_versioned else None)
            restored = Worker(media_graph_selection=selection, canonical_binding=resumed_binding,
                              **({"canonical_resume": True} if use_versioned else {}))
            workers.append(restored)
            sends_before_resume = len([event for event in events if event.get("type") == "assistant.message"])
            await restored.start(config)
            assert restored.session is not None, events
            assert restored.session is not worker.session
            if use_media:
                for _ in range(150):
                    if restored.session.coordinator.get_capability(MEDIA).enabled:
                        break
                    await asyncio.sleep(.02)
                require_media(restored, selection)
                media_checkpoint(restored, authoritative_rows(), media_ref)
                assert len((root / "private-provider.jsonl").read_text().splitlines()) == 4
            assert len([event for event in events if event.get("type") == "assistant.message"]) == sends_before_resume
            await restored.command({"op": "send", "id": "three-request", "input_id": "three", "text": "three"})
            for _ in range(200):
                if restored.idle() and any(event.get("type") == "generation.finished"
                                          and "three" in event.get("input_ids", []) for event in events):
                    break
                await asyncio.sleep(.02)
            assert restored.idle() and restored.shared_handle.active, events
            messages = authoritative_rows()
            user_rows = [row for row in messages if (row.get('metadata') or {}).get('amplifier_input', {}).get('kind') == 'user']
            if use_media:
                assert [row["content"] if isinstance(row["content"], str) else row["content"][0]["text"]
                        for row in user_rows] == ["one", "two", "three"]
                media_checkpoint(restored, messages, media_ref)
            else:
                assert [row["content"] for row in user_rows] == ["one", "two", "three"]
            assert len([event for event in events if event.get('type') == 'assistant.message']) == 3
            evidence = {
                "cold_prepare_seconds": round(cold_seconds, 6),
                "same_owner_dispatch_seconds": round(warm_seconds, 6),
                "same_mounted_session": True, "same_writer": True,
                "two_authoritative_turns": True, "retired_safely": True,
                "resumed_without_replay": True, "source": source_evidence(overlay),
            }
            if use_media:
                evidence["media"] = media_evidence(root, pixels, messages, events)
                evidence["media_source_manifest"] = selection.sources
            if use_versioned:
                assert (destination / "transcript.jsonl").is_dir()
                assert (destination / "transcript.jsonl.backup").is_dir()
                assert resumed_binding.last_receipt is not None
                evidence["prospective_canonical_owner"] = {
                    "explicit_rewrite": True, "typed_ack": True,
                    "same_owner_two_turns": True, "fresh_resume_exact_prefix": True,
                    "legacy_writer_fences": True}
            print(json.dumps(evidence))
        finally:
            for owner in workers:
                if owner.session is not None and owner.shared_handle is not None and owner.shared_handle.active:
                    owner.shutdown.set()
                    await owner.run()


if __name__ == "__main__":
    asyncio.run(main())
