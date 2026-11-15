"""Offline retained Children with actual Core/Foundation construction and LIVE.

The provider/module-loader and execution-registration adapter are explicit source
fixtures. The adapter uses the same public Foundation registration scope as the
Worker; it delegates preparation to Unified's real StandaloneHostAdapter. This
qualifies Children retention, not Worker.start, an installed wheel or a live host.
No credentials, network model requests, environment mutation or synthetic seals.
"""
from __future__ import annotations

import asyncio
import base64
import copy
from contextlib import closing, contextmanager
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import sys

import pytest
from amplifier_core import AmplifierSession, HookResult, ModuleCoordinator, ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_foundation.session.media import MediaRefusal, SessionMediaStore
from amplifier_module_context_simple import SimpleContextManager
import amplifier_module_loop_live.orchestrator as live
from amplifier_module_loop_live.runtime import Runtime

from amplifier_web.host import children as host_children
from amplifier_web.host import media as host_media
from amplifier_web.host.media_graph import NamedMediaGraph
from amplifier_web.host.storage import SessionStore
from test_host_media_hooks import NamedFixtureLoader
from test_media_child_routes import (
    ChildRouteProvider, ProspectiveChildFactory, _assert_legacy_fences, _assert_qualified,
)

NAMESPACE = host_media.NAMESPACE


class OfflineTool:
    name = "fixture"
    description = "Offline source fixture; the selected provider never requests tools."
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, input):
        return ToolResult(success=False, error={"message": "No tool calls expected"})


class RetainedProvider(ChildRouteProvider):
    """Offline SDK boundary: validate ACK, private identity, and actual PNG."""
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model": "fixture"})

    def get_media_request_support(self, **kwargs):
        return {"v": 1, "positions": ["user"]}

    async def request_budget(self, request, *, request_options=None, **kwargs):
        try:
            _assert_qualified(self.coordinator)
            media = self.coordinator.get_capability("web.media.store")
            canonical = await self.coordinator.get("context").get_messages()
            refs = canonical_refs(canonical, self.coordinator.session_id,
                                  base64.b64encode(self.pixels).decode())
            # Public validation requires committed refs and the actual in-memory
            # canonical ACK, not a serialized checkpoint-kind label.
            assert media.validate_ref(refs[0]) == refs[0]
            if not hasattr(self, "count_stores"):
                self.count_stores = []
            self.count_stores.append(media)
            # Reuses typed-receipt/ref verification and competing FD acquisition.
            await super().request_budget(request, request_options=request_options, **kwargs)
        except BaseException as error:
            self.fixture_failure = error
            raise
        return {"estimated_input_tokens": 50, "input_tokens": 50,
                "input_limit_tokens": 100000, "context_token_budget": 90000,
                "max_output_tokens": 64}

    async def complete(self, request, **kwargs):
        try:
            assert await super().complete(request, **kwargs) == "private image inspected"
        except BaseException as error:
            self.fixture_failure = error
            raise
        return ChatResponse(content=[TextBlock(text="private image inspected")])

    def parse_tool_calls(self, response):
        return []


class RetainedLoader(NamedFixtureLoader):
    async def ready(self, current):
        await super().ready(current)
        current.hooks.register("provider:error", self.post, name="fixture-provider-errors")

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        if module_id == "loop-live":
            # Actual LIVE mount/constructor and shared streaming turn engine.
            async def mount(current):
                await live.mount(current, {})
                loop = current.get("orchestrator")
                if type(loop) is not live.BundleLiveOrchestrator:
                    raise RuntimeError("The selected LIVE constructor was replaced")
                selection = current.get_capability("web.media.graph_selection")
                if not selection.mint_owner(loop, kind="retained-loop", construction=self.native):
                    raise RuntimeError("The declared LIVE owner could not be joined")
            return mount
        return await super().load(module_id, config, source_hint=source_hint,
                                  coordinator=coordinator)

    async def post(self, event, data):
        if self.sentinel in str(data):
            raise RuntimeError("Private pixels reached a public hook")
        self.observed.append(str(data))
        return HookResult()


@dataclass
class RetainedPrepared(PreparedBundle):
    state: object = None

    async def create_session(self, *, before_initialize=None, **kwargs):
        async def initialize(session):
            coordinator = session.coordinator
            factory = self.state["factory"]
            binding = factory.latest(session.session_id) if factory and kwargs.get("parent_id") else None
            if binding is not None:
                binding.check()
                _assert_legacy_fences(binding.directory)
            provider = RetainedProvider(coordinator, self.state["pixels"], binding)
            coordinator.register_cleanup(provider.observe_cleanup)
            loader = RetainedLoader(provider, SimpleContextManager(), tool=OfflineTool(),
                                    sentinel=self.state["sentinel"])
            loader.observed = []
            loader.native = coordinator
            coordinator.loader = loader
            assert self.state["selection"].mint_owner(
                loader, kind="retained-loader", construction=coordinator)
            self.state["sessions"].append(session)
            self.state["providers"].append(provider)
            self.state["loaders"].append(loader)
            self.state["creations"].append((session.session_id, kwargs.get("parent_id"),
                                             kwargs.get("is_resumed", False)))
            if before_initialize is not None:
                await before_initialize(session)
        session = await super().create_session(before_initialize=initialize, **kwargs)
        assert type(session) is AmplifierSession
        return session


class RetainedRegistration(host_children.StandaloneHostAdapter):
    """Explicit fixture assembly of Worker's public registration-scope pattern.

    This is not an auto-allow or graph diagnostic. Owners come from the exact
    coordinator/runtime created by Children, before LIVE registers its callbacks.
    """
    def __init__(self):
        super().__init__()
        self.registrations = {}

    @contextmanager
    def execution_registration(self, coordinator, hooks):
        native = coordinator
        view = None
        try:
            with host_media.hook_configuration(native):
                view = host_media.registration_coordinator(native)
                self.registrations[id(view)] = (view, native)
                bridge = native.get_capability("web.media.hook_bridge")
                selection = native.get_capability("web.media.graph_selection")
                selection.bind_registration_view(native, bridge, view)
                yield view, view.hooks
            sealed = host_media.seal_hook_graph(native)
            assert sealed
        finally:
            if view is not None:
                self.registrations.pop(id(view), None)

    async def prepare_execution(self, loop, coordinator, providers):
        entry = self.registrations[id(coordinator)]
        assert entry[0] is coordinator
        native = entry[1]
        result = await super().prepare_execution(loop, native, providers)
        selection = native.get_capability("web.media.graph_selection")
        assert selection.mint_owner(result[0], kind="runtime", construction=native)
        # Flush the exact adapters returned by production provider preparation.
        await host_media.flush_media_adapters(native)
        for name, value in result[1].items():
            await host_media.mount_media_adapter(native, "providers", value,
                previous=native.get("providers")[name], name=name)
        return result


def retained_selection(plan):
    """Fixed owner/source declarations, never expanded from observed evidence."""
    from amplifier_web.host import prompt_events, initialization
    from amplifier_web import provider_recording
    from amplifier_foundation.session import media_hook_bridge, media_projection
    import amplifier_module_loop_live.runtime as runtime_module
    import amplifier_module_loop_streaming as streaming
    # These are the reviewed fixture, host, Foundation and LIVE registration
    # owners; no checkout/site-packages traversal and no callback harvest.
    paths = [Path(__file__), Path(__file__).with_name("test_host_media_hooks.py"),
             Path(__file__).with_name("test_media_child_routes.py"),
             *[Path(module.__file__) for module in (
                 host_children, host_media, prompt_events, initialization,
                 provider_recording, live, runtime_module, streaming,
                 media_hook_bridge, media_projection)]]
    overlay = plan["agents"]["worker"]
    child_plan = host_children.child_plan(plan, overlay)
    # Resume reapplies the same effective child policy to saved declarations.
    resumed_plan = host_children.child_plan(plan, overlay, retained_image_tools=[], retained_image_policy={})
    return NamedMediaGraph("actual-retained-live-child-private-png",
        plans=[plan, child_plan, resumed_plan],
        sources={str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        dependencies=(HookResult, live, live.mount, RetainedLoader),
        owner_rules=(("retained-loader", RetainedLoader,
                      ("sentinel", "provider", "context", "tool", "native")),
                     ("coordinator", ModuleCoordinator, ("session_id",)),
                     ("runtime", Runtime, ("session_id",)),
                     ("retained-loop", live.BundleLiveOrchestrator, ("config",)),
                     ("media-source-policy", host_media.SourcePolicy, ("coordinator",))),
        environments={"NamedFixtureLoader.load.<locals>.mount": [
            {"module_id": name} for name in ("fixture-loop", "fixture-context", "fixture-provider", "fixture-hooks")]})


async def wait_idle(task, registry, *, reports):
    async with asyncio.timeout(12):
        while True:
            if task.done():
                # Surface the original lifecycle/selection error, not a timeout.
                try:
                    await task
                except Exception:
                    for provider in registry.prepared["parent"].state["providers"]:
                        if getattr(provider, "fixture_failure", None) is not None:
                            raise provider.fixture_failure
                    state = registry.prepared["parent"].state
                    diagnostics = {
                        "hooks": [loader.observed for loader in state["loaders"]],
                        "runtime": [getattr(session.coordinator.get_capability("live.runtime"), "events", [])
                                    for session in state["sessions"]]}
                    raise AssertionError(diagnostics)
                raise AssertionError("Retained child returned before explicit finish")
            row = registry.rows.get("retained-child", {})
            if row.get("status") == "idle" and row.get("reports", 0) >= reports:
                return row
            await asyncio.sleep(.01)


def canonical_refs(rows, session_id, sentinel):
    serialized = str(rows)
    assert sentinel not in serialized and "'type': 'image'" not in serialized
    refs = [ref for row in rows for ref in row.get("metadata", {}).get(NAMESPACE, {}).get("refs", [])]
    assert len(refs) == 1 and refs[0]["session_id"] == session_id
    return refs


def persistent_task(coro):
    # Same private selection used by PersistentDelegate, scoped to task creation.
    token = host_children._PERSISTENT.set(True)
    try:
        return asyncio.create_task(coro)
    finally:
        host_children._PERSISTENT.reset(token)


def assert_idle_custody(media, binding, factory):
    # Failed competing opens are closed by the actual constructors. Both legacy
    # and prospective paths keep the physical media writer, not just a flag.
    with pytest.raises(MediaRefusal, match="active writer"):
        SessionMediaStore(media.root, media.session_id)
    if binding is not None:
        binding.check()
        with pytest.raises(factory.conflict_type, match="retains the source lease"):
            factory.binding_type.resume(binding.directory, session_id=binding.session_id,
                                        expected=binding.expected)


@pytest.mark.parametrize("prospective", [False, True], ids=["legacy", "prospective-t2"])
async def test_actual_live_child_retains_provider_writer_until_finish_and_resumes_exact_prefix(
        tmp_path, monkeypatch, prospective):
    from PIL import Image
    if prospective and sys.platform != "linux":
        pytest.skip("Prospective source lifetime is qualified on Linux only")
    # Only filesystem locators are replaced; no process-environment mutation,
    # model/provider/graph/checkpoint/lifetime operations are mocked.
    from amplifier_web import runtime_controls, native_provider
    monkeypatch.setattr(runtime_controls, "app_home", lambda: tmp_path / "app")
    monkeypatch.setattr(native_provider, "app_home", lambda: tmp_path / "app")
    factory = ProspectiveChildFactory() if prospective else None
    raw = io.BytesIO()
    Image.new("RGB", (2, 2), (19, 71, 149)).save(raw, format="PNG")
    pixels = raw.getvalue()
    plan = {"session": {"orchestrator": {"module": "fixture-loop"},
                        "context": {"module": "fixture-context"}},
            "providers": [{"module": "fixture-provider"}],
            "hooks": [{"module": "fixture-hooks"}],
            "agents": {"worker": {"session": {"orchestrator": {"module": "loop-live"}}}}}
    selection = retained_selection(plan)
    state = {"selection": selection, "pixels": pixels,
             "sentinel": base64.b64encode(pixels).decode(), "factory": factory,
             "sessions": [], "providers": [], "loaders": [], "creations": []}
    prepared = RetainedPrepared(copy.deepcopy(plan), BundleModuleResolver({}),
        Bundle(name="retained-child", session=plan["session"], agents=plan["agents"]), state=state)
    store = SessionStore(tmp_path / "sessions")
    async def initialize(session):
        host_media.bind_media(session, store, selection=selection)
    parent = await prepared.create_session(session_id="parent", session_cwd=tmp_path,
                                           before_initialize=initialize)
    tasks = []
    try:
        async def checkpoint():
            rows = await parent.coordinator.get("context").get_messages()
            canonical = store.save("parent", rows, {})
            host_media.acknowledge_media(parent.coordinator, store, "parent", canonical)
        parent.coordinator.register_capability("session.durable_checkpoint", checkpoint)
        sealed = host_media.seal_hook_graph(parent.coordinator)
        _assert_qualified(parent.coordinator)
        assert sealed
        cap = parent.coordinator.get_capability(NAMESPACE)
        origin = {"kind": "upload", "session_id": "parent", "id": "upload-1"}
        ref = (await cap.ingest(pixels, source_format="image-bytes", origin=origin, policy=None))["value"]
        await cap.publish([ref], origin=origin, disposition="retain")
        rows = [{"role": "user", "content": "Inspect the inherited image",
                 "metadata": {NAMESPACE: {"refs": [ref]}}}]
        await parent.coordinator.get("context").set_messages(rows)
        await checkpoint()
        rows = await parent.coordinator.get("context").get_messages()
        parent_saved = store.load("parent")
        adapter = RetainedRegistration()
        registry = await host_children.install_children(parent, prepared, Runtime("parent"), store, None,
            host_adapter=adapter, canonical_binding_factory=factory)
        task = persistent_task(registry.spawn("worker", "one", parent,
            sub_session_id="retained-child", parent_messages=rows))
        tasks.append(task)
        row = await wait_idle(task, registry, reports=1)
        child = registry.sessions["retained-child"]
        provider = state["providers"][1]
        media = child.coordinator.get_capability("web.media.store")
        assert media is not parent.coordinator.get_capability("web.media.store")
        runtime = row["runtime"]
        writer = factory.latest("retained-child") if factory else None
        assert type(child.coordinator.get("orchestrator")) is live.BundleLiveOrchestrator
        assert len(provider.counted) == len(provider.sent) == 1
        assert not task.done() and not runtime.closed and row["_owner"] is not None
        assert_idle_custody(media, writer, factory)
        first_rows = await child.coordinator.get("context").get_messages()
        first_refs = canonical_refs(first_rows, "retained-child", state["sentinel"])
        assert first_refs[0] != ref
        if writer:
            writer.check()
            assert not provider.cleanup_checked
        assert (await registry.control("retained-child", "message", "two", input_id="two"))["accepted"]
        await wait_idle(task, registry, reports=2)
        assert registry.sessions["retained-child"] is child
        assert state["providers"][1] is provider and len(state["providers"]) == 2
        assert registry.rows["retained-child"]["runtime"] is runtime
        assert len(provider.counted) == len(provider.sent) == 2
        assert provider.count_stores == [media, media]
        assert child.coordinator.get_capability("web.media.store") is media
        assert_idle_custody(media, writer, factory)
        if writer:
            assert factory.latest("retained-child") is writer
            writer.check()
            assert len(provider.count_receipts) == 2 and not provider.cleanup_checked
        final_rows = await child.coordinator.get("context").get_messages()
        assert canonical_refs(final_rows, "retained-child", state["sentinel"]) == first_refs
        assert (await registry.control("retained-child", "finish"))["accepted"]
        assert (await asyncio.wait_for(task, 12))["output"] == "private image inspected"
        assert "retained-child" not in registry.sessions
        # Successful fresh acquisition is physical writer retirement evidence.
        with closing(SessionMediaStore(media.root, media.session_id)):
            pass
        assert provider.cleanup_checked is prospective
        if writer:
            with pytest.raises(factory.conflict_type, match="closed"):
                writer.check()
            prefix = writer.expected
            reopened = factory.binding_type.resume(writer.directory, session_id="retained-child", expected=prefix)
            try:
                saved = reopened.load_messages()
                assert saved == final_rows
            finally:
                await reopened.aclose()
        else:
            saved, _ = store.load("retained-child")
            assert saved == final_rows
        # Fresh native child, own source, exact closed prefix; no old turn replay.
        task = persistent_task(registry.resume("retained-child", "three", parent))
        tasks.append(task)
        await wait_idle(task, registry, reports=1)
        resumed = registry.sessions["retained-child"]
        assert resumed is not child and state["creations"][-1] == ("retained-child", "parent", True)
        new_provider = state["providers"][-1]
        new_media = resumed.coordinator.get_capability("web.media.store")
        assert new_media is not media and new_media.root == media.root
        assert new_provider is not provider and len(new_provider.sent) == 1
        resumed_runtime = registry.rows["retained-child"]["runtime"]
        assert len(provider.sent) == 2
        resumed_rows = await resumed.coordinator.get("context").get_messages()
        assert resumed_rows[:len(saved)] == saved
        assert canonical_refs(resumed_rows, "retained-child", state["sentinel"]) == first_refs
        assert [message["content"] for message in resumed_rows if message["role"] == "user"][-3:] == ["one", "two", "three"]
        if factory:
            assert factory.openings == [("retained-child", False), ("retained-child", True)]
            assert factory.bindings[-1] is not writer
        assert (await registry.control("retained-child", "finish"))["accepted"]
        await asyncio.wait_for(task, 12)
        with closing(SessionMediaStore(new_media.root, new_media.session_id)):
            pass
        if factory:
            assert new_provider.cleanup_checked
            closed = factory.latest("retained-child")
            with pytest.raises(factory.conflict_type, match="closed"):
                closed.check()
            reacquired = factory.binding_type.resume(closed.directory, session_id=closed.session_id,
                                                    expected=closed.expected)
            await reacquired.aclose()
        assert all(request.messages == [] for current in (provider, new_provider) for request in current.sent)
        assert provider.digests == [hashlib.sha256(pixels).hexdigest()] * 2
        assert new_provider.digests == [hashlib.sha256(pixels).hexdigest()]
        assert not adapter.registrations and not registry.admissions
        assert set(registry.sessions) == {"parent"}
        assert await parent.coordinator.get("context").get_messages() == rows
        assert store.load("parent") == parent_saved
        assert not state["providers"][0].sent
        assert state["sentinel"] not in str(registry.snapshot())
        assert state["sentinel"] not in str(runtime.events)
        assert state["sentinel"] not in str(resumed_runtime.events)
        for loader in state["loaders"]:
            assert all(state["sentinel"] not in event for event in loader.observed)
        for path in tmp_path.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".jsonl"}:
                assert state["sentinel"].encode() not in path.read_bytes()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await parent.cleanup()
