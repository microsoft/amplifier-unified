"""Offline child-route integration through real Core, Foundation and Children.

Only the module/provider boundary is a selected source fixture. Foundation owns
construction/ready and its public bridge; Unified's actual Children owns child
plans, initialization, adoption, checkpoint ACK, provider wrapping and cleanup.
This is not qualification of loop-live, an installed Unified wheel or a live host.
"""
from __future__ import annotations

import base64
import asyncio
import copy
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import stat
import sys

import pytest
from amplifier_core import AmplifierSession, HookResult, ModuleCoordinator
from amplifier_core.message_models import ChatRequest, Message
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_module_context_simple import SimpleContextManager
from amplifier_module_loop_live.runtime import Runtime

from amplifier_web.host import children as host_children
from amplifier_web.host import media as host_media
from amplifier_web.host.media_graph import NamedMediaGraph
from amplifier_web.host.session import SelectedProvider
from amplifier_web.host.storage import SessionStore
from test_host_media_hooks import NamedFixtureLoader

NAMESPACE = host_media.NAMESPACE


class ProspectiveChildFactory:
    """Private test assembly owner, never recovered from a catalog or JSON flag."""
    def __init__(self):
        lifetime = pytest.importorskip("amplifier_foundation.session.transcript_lifetime")
        self.binding_type = lifetime.VersionedSessionBinding
        self.conflict_type = lifetime.VersionedSessionConflict
        self.bindings = []
        self.openings = []

    def latest(self, session_id):
        return next(binding for binding in reversed(self.bindings)
                    if binding.session_id == session_id)

    def __call__(self, directory, *, session_id, resumed):
        directory = Path(directory).absolute()
        if resumed:
            previous = self.latest(session_id)
            with pytest.raises(self.conflict_type, match="closed"):
                previous.check()
            # Exact object from our completed owner, not a deserialized report.
            expected = previous.expected
            binding = self.binding_type.resume(directory, session_id=session_id, expected=expected)
            assert binding.expected == expected
        else:
            assert not directory.exists(), "Prospective enrollment must precede native child opens"
            binding = self.binding_type.create_new(directory, session_id=session_id,
                operation_id="create-" + session_id, admit=self.admit_empty_source)
        binding.check()
        _assert_legacy_fences(directory)
        self.bindings.append(binding)
        self.openings.append((session_id, resumed))
        return binding

    @staticmethod
    def admit_empty_source():
        # create_new commits zero canonical rows: no image asset is reachable.
        # Later Children checkpoints must supply their actual media store.
        return None


def _assert_legacy_fences(directory):
    for name in ("transcript.jsonl", "transcript.jsonl.backup"):
        fence = directory / name
        assert fence.is_dir()
        marker = json.loads((fence / "format.json").read_text())
        assert marker["v"] == 2 and marker["status"] == "unsupported-by-legacy-entrypoint"
        with pytest.raises(IsADirectoryError):
            fence.open("ab")
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / ".versioned-owner").stat().st_mode) == 0o600


class ChildRouteProvider:
    """No SDK/network: inspect the real host's private count/send projection."""
    default_model = "fixture"
    name = "fixture"
    config = {}

    def __init__(self, coordinator, pixels, binding=None):
        self.coordinator = coordinator
        self.pixels = pixels
        self.binding = binding
        self.counted = []
        self.sent = []
        self.digests = []
        self.count_receipts = []
        self.cleanup_checked = False

    def get_media_request_support(self, **kwargs):
        return {"v": 1, "positions": ["tool"]}

    async def request_budget(self, request, *, request_options=None, **kwargs):
        if self.binding is not None:
            from amplifier_foundation.session.transcript_lifetime import (
                VersionedSessionBinding, VersionedSessionConflict,
            )
            from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt
            self.binding.check()
            receipt = self.binding.last_receipt
            assert type(receipt) is VersionedCheckpointReceipt
            facts = receipt.verify()  # After actual write/ACK/fence exit.
            assert facts["kind"] == "versioned-checkpoint-backed-v1"
            assert facts["session_id"] == self.coordinator.session_id
            assert facts["operation_id"] == self.binding.expected.operation_id
            assert self.binding.last_result.commit == self.binding.expected
            refs = list(receipt.iter_refs())
            assert len(refs) == 1 and refs[0]["session_id"] == self.coordinator.session_id
            assert self.coordinator.get_capability("web.media.store").validate_ref(refs[0]) == refs[0]
            # The enrolled prospective FD lease remains held during SDK work.
            with pytest.raises(VersionedSessionConflict, match="retains the source lease"):
                VersionedSessionBinding.resume(self.binding.directory,
                    session_id=self.binding.session_id, expected=self.binding.expected)
            self.count_receipts.append(receipt)
        self.coordinator.get_capability(NAMESPACE).validate(
            request, purpose="count", request_options=request_options or {})
        self.counted.append(request)
        return {"input_tokens": 7}

    async def complete(self, request, **kwargs):
        self.coordinator.get_capability(NAMESPACE).validate(
            request, purpose="transport", request_options=kwargs)
        assert request is self.counted[-1]
        self.sent.append(request)
        images = [block for message in request.messages
                  if isinstance(message.content, list) for block in message.content
                  if block.type == "image"]
        assert len(images) == 1
        raw = base64.b64decode(images[0].source["data"])
        assert raw == self.pixels
        self.digests.append(hashlib.sha256(raw).hexdigest())
        if getattr(self, "failure", None) == "cancel":
            raise asyncio.CancelledError("fixture provider cancelled")
        if getattr(self, "failure", None) == "error":
            raise RuntimeError("fixture provider failed")
        return "private image inspected"

    async def observe_cleanup(self):
        if self.binding is not None:
            self.binding.check()  # Children must not retire the lease before native cleanup.
            self.cleanup_checked = True


class ChildRouteLoop:
    """Finite source fixture, not a replacement for Children.execute machinery."""
    def __init__(self):
        self.config = {"inherit_effective_model": True}
        self.root_provider = None

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        _assert_qualified(coordinator)
        rows = await context.get_messages()
        before = copy.deepcopy(rows)
        refs = [ref for row in rows for ref in
                (row.get("metadata", {}).get(NAMESPACE, {}).get("refs", []))]
        assert refs and all(ref["session_id"] == coordinator.session_id for ref in refs)
        selected = self.root_provider or providers["fixture"]
        # Observe an already-canonical carrier through the actual public bridge.
        # These hooks must see refs, not the later private provider projection.
        await hooks.emit("tool:pre", {"tool_name": "fixture", "tool_call_id": "inspect-refs"})
        settled = await coordinator.get_capability("session.media.tool_post")({
            "tool_name": "fixture", "tool_call_id": "inspect-refs",
            "result": {"success": True, "output": {NAMESPACE: {"refs": refs}}}})
        assert settled["complete"] is True
        assert settled["result"]["output"][NAMESPACE]["refs"] == refs
        request = ChatRequest(messages=[Message.model_validate(row) for row in rows])
        canonical = request.model_dump_json()
        await selected.request_budget(request)
        assert await selected.complete(request) == "private image inspected"
        assert request.model_dump_json() == canonical
        assert await context.get_messages() == before
        assert "base64" not in canonical
        # Exercise the actual nested registry while this child's own store lives.
        if prompt == "nested":
            registry = coordinator.get_capability("live.children")
            result = await registry.spawn("worker", "grandchild", registry.sessions[
                coordinator.session_id], sub_session_id="grandchild", parent_messages=rows)
            assert result["session_id"] == "grandchild"
        await context.add_message({"role": "assistant", "content": "private image inspected"})
        return "private image inspected"


class ChildRouteLoader(NamedFixtureLoader):
    """Reuse maintained mount/ready/post-observer fixture, with a finite loop."""
    async def post(self, event, data):
        if self.sentinel in str(data):
            raise RuntimeError("Private pixels reached a child post observer")
        self.observed.append(str(data))
        return HookResult()

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        if module_id in {"fixture-loop", "fixture-custom-loop"}:
            async def mount(current):
                await current.mount("orchestrator", ChildRouteLoop())
                current.get_capability(NAMESPACE).report(
                    "fixture-loop", event="consumer-ready", outcome="ready",
                    details={"role": "loop", "contract": 1})
            return mount
        return await super().load(module_id, config, source_hint=source_hint,
                                  coordinator=coordinator)


@dataclass
class ChildRoutePrepared(PreparedBundle):
    state: object = None

    async def create_session(self, *, before_initialize=None, **kwargs):
        async def initialize(session):
            # Keep Foundation's actual constructor, resolver, initialize and ready
            # phases. Substitute only its module loader, before the host binder.
            coordinator = session.coordinator
            factory = self.state["factory"]
            binding = factory.latest(session.session_id) if factory and kwargs.get("parent_id") else None
            provider = ChildRouteProvider(coordinator, self.state["pixels"], binding)
            provider.failure = self.state.get("provider_failure")
            if binding is not None:
                binding.check()
                _assert_legacy_fences(binding.directory)
                coordinator.register_cleanup(provider.observe_cleanup)
            loader = ChildRouteLoader(provider, SimpleContextManager(),
                                      sentinel=self.state["sentinel"])
            loader.observed = []
            coordinator.loader = loader
            self.state["selection"].mint_owner(
                loader, kind="child-route-loader", construction=coordinator)
            self.state["sessions"].append(session)
            self.state["providers"].append(provider)
            self.state["loaders"].append(loader)
            self.state["creations"].append((kwargs["session_id"], kwargs.get("parent_id"),
                                            kwargs.get("is_resumed", False)))
            if before_initialize is not None:
                await before_initialize(session)
        session = await super().create_session(before_initialize=initialize, **kwargs)
        assert type(session) is AmplifierSession
        return session


def _assert_qualified(coordinator):
    selection = coordinator.get_capability("web.media.graph_selection")
    graph = coordinator.get_capability("web.media.hook_graph")
    assert selection is not None and graph is not None
    evidence = graph.participants_evidence()
    rejected = [getattr(row[key], "__qualname__", repr(row[key]))
                for section, key in (("loads", "mount"), ("ready", "callback"),
                                     ("registrations", "original"))
                for row in evidence[section]
                if not selection.accepts_callable(row[key])]
    assert not rejected, "Named child source join rejected actual callbacks: " + repr(rejected)
    bridge = coordinator.get_capability("web.media.hook_bridge")
    join = coordinator.get_capability("web.media.source_join")
    assert join is not None and join[2] is bridge.graph_receipt
    assert bridge.validate_receipt(join[2]) is True
    assert coordinator.get_capability(NAMESPACE).enabled is True


@pytest.fixture(params=[False, True], ids=["legacy", "prospective-t2"])
async def child_routes(tmp_path, monkeypatch, request):
    """Explicit source selection, never a Boolean or a synthetic seal receipt."""
    from PIL import Image
    bridge = pytest.importorskip("amplifier_foundation.session.media_hook_bridge")
    if not hasattr(bridge.MediaHookBridge, "begin_participating_graph"):
        pytest.skip("Requires the paired positive Foundation bridge")
    if request.param and sys.platform != "linux":
        pytest.skip("Prospective source lifetime is qualified on Linux only")
    factory = ProspectiveChildFactory() if request.param else None
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    raw = io.BytesIO()
    Image.new("RGB", (2, 2), (19, 71, 149)).save(raw, format="PNG")
    pixels = raw.getvalue()
    plan = {"session": {"orchestrator": {"module": "fixture-loop"},
                        "context": {"module": "fixture-context"}},
            "providers": [{"module": "fixture-provider"}],
            "hooks": [{"module": "fixture-hooks"}],
            "agents": {"worker": {}}}
    child_plan = host_children.child_plan(plan, {})
    custom_plan = host_children.child_plan(plan, {
        "session": {"orchestrator": {"module": "fixture-custom-loop"}}})
    # These are assembly-selected bodies, not arbitrary runtime evidence hashes.
    paths = [Path(__file__), Path(__file__).with_name("test_host_media_hooks.py"),
             Path(host_children.__file__), Path(host_media.__file__)]
    from amplifier_web.host import prompt_events
    from amplifier_web import provider_recording
    paths.extend([Path(prompt_events.__file__), Path(provider_recording.__file__)])
    selection = NamedMediaGraph("actual-core-foundation-children-private-png",
        plans=[plan, child_plan, custom_plan],
        sources={str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in paths},
        dependencies=(HookResult, ChildRouteLoop),
        owner_rules=(("child-route-loader", ChildRouteLoader,
                      ("sentinel", "provider", "context", "tool")),
                     ("coordinator", ModuleCoordinator, ()),
                     ("media-source-policy", host_media.SourcePolicy, ("coordinator",))),
        environments={"NamedFixtureLoader.load.<locals>.mount": [
            {"module_id": name} for name in ("fixture-context", "fixture-provider", "fixture-hooks")]})
    state = {"selection": selection, "pixels": pixels,
             "sentinel": base64.b64encode(pixels).decode(), "sessions": [], "providers": [],
             "loaders": [], "creations": [], "factory": factory}
    prepared = ChildRoutePrepared(copy.deepcopy(plan), BundleModuleResolver({}),
        Bundle(name="child-routes", session=plan["session"], agents=plan["agents"]), state=state)
    store = SessionStore(tmp_path / "sessions")
    async def initialize(session):
        host_media.bind_media(session, store, selection=selection)
    parent = await prepared.create_session(session_id="parent", session_cwd=tmp_path,
                                           before_initialize=initialize)
    async def checkpoint():
        rows = await parent.coordinator.get("context").get_messages()
        canonical = store.save("parent", rows, {})
        host_media.acknowledge_media(parent.coordinator, store, "parent", canonical)
    parent.coordinator.register_capability("session.durable_checkpoint", checkpoint)
    assert host_media.seal_hook_graph(parent.coordinator) is True
    _assert_qualified(parent.coordinator)
    cap = parent.coordinator.get_capability(NAMESPACE)
    origin = {"kind": "upload", "session_id": "parent", "id": "upload-1"}
    ingested = await cap.ingest(pixels, source_format="image-bytes", origin=origin, policy=None)
    ref = ingested["value"]
    await cap.publish([ref], origin=origin, disposition="retain")
    rows = [{"role": "tool", "name": "fixture", "tool_call_id": "call-1",
             "content": "Inspect the inherited private image", "metadata": {NAMESPACE: {"refs": [ref]}}}]
    await parent.coordinator.get("context").set_messages(rows)
    await checkpoint()
    rows = await parent.coordinator.get("context").get_messages()
    registry = await host_children.install_children(parent, prepared, Runtime("parent"), store, None,
                                                   canonical_binding_factory=factory)
    assert registry.canonical_binding_factory is factory
    try:
        yield parent, registry, store, rows, state
    finally:
        await parent.cleanup()


@pytest.mark.parametrize("route", ["finite", "selected", "custom", "nested", "resumed"])
async def test_actual_children_routes_use_own_acked_refs_and_private_count_send(child_routes, route):
    parent, registry, store, rows, state = child_routes
    cap = parent.coordinator.get_capability(NAMESPACE)
    if route == "selected":
        parent.coordinator.get("orchestrator").root_provider = SelectedProvider(
            parent.coordinator.get("providers")["fixture"],
            {"instance": "fixture", "model": "fixture"})
    configs = ({"worker": {"session": {"orchestrator": {"module": "fixture-custom-loop"}}}}
               if route == "custom" else None)
    result = await registry.spawn("worker", "nested" if route == "nested" else "Inspect",
        parent, agent_configs=configs, sub_session_id="child", parent_messages=rows)
    assert result["output"] == "private image inspected"
    if route == "resumed":
        assert (await registry.resume("child", "Continue", parent))["output"] == "private image inspected"
    expected = 2 if route in {"nested", "resumed"} else 1
    assert len(state["sessions"]) == expected + 1
    assert state["creations"][:2] == [("parent", None, False), ("child", "parent", False)]
    if route == "resumed":
        assert state["creations"][-1] == ("child", "parent", True)
    elif route == "nested":
        assert state["creations"][-1] == ("grandchild", "child", False)
    if route == "selected":
        child_loop = state["sessions"][1].coordinator.get("orchestrator")
        assert isinstance(child_loop.root_provider, SelectedProvider)
        assert child_loop.root_provider.selection == {"instance": "fixture", "model": "fixture"}
    assert len({id(session.coordinator.get_capability(NAMESPACE))
                for session in state["sessions"]}) == expected + 1
    assert all(session.coordinator.get_capability(NAMESPACE) is not cap
               for session in state["sessions"][1:])
    providers = state["providers"][1:]
    assert all(len(provider.counted) == len(provider.sent) == 1 for provider in providers)
    assert all(provider.counted[0] is provider.sent[0] and provider.sent[0].messages == []
               for provider in providers)
    assert all(provider.digests == [hashlib.sha256(state["pixels"]).hexdigest()]
               for provider in providers)
    factory = state["factory"]
    if factory is not None:
        expected_openings = [("child", False)]
        if route == "nested":
            expected_openings.append(("grandchild", False))
        elif route == "resumed":
            expected_openings.append(("child", True))
        assert factory.openings == expected_openings
        assert len({id(binding) for binding in factory.bindings}) == expected
        for provider in providers:
            assert provider.cleanup_checked
            assert len(provider.count_receipts) == 1
        for binding in factory.bindings:
            with pytest.raises(factory.conflict_type, match="closed"):
                binding.check()
    assert not state["providers"][0].counted and not state["providers"][0].sent
    assert await parent.coordinator.get("context").get_messages() == rows
    for loader in state["loaders"][1:]:
        assert len(loader.observed) == 2  # One mount observer plus one ready observer.
        assert all(NAMESPACE in value and state["sentinel"] not in value
                   for value in loader.observed)
    for session_id in {"child", "grandchild"} if route == "nested" else {"child"}:
        if factory is None:
            saved, metadata = store.load(session_id)
        else:
            closed = factory.latest(session_id)
            # Successful fresh acquisition proves physical retirement, not merely
            # a cleared Python flag. Read through the selected store, under custody.
            reopened = factory.binding_type.resume(closed.directory, session_id=session_id,
                                                   expected=closed.expected)
            try:
                selected_store = SessionStore(store.base_dir, canonical_binding=reopened)
                saved, metadata = selected_store.load(session_id)
                assert saved == reopened.load_messages()
                assert closed.last_receipt.verify()["operation_id"] == reopened.expected.operation_id
                _assert_legacy_fences(closed.directory)
            finally:
                await reopened.aclose()
        assert metadata["status"] == "completed"
        refs = [ref for row in saved for ref in
                row.get("metadata", {}).get(NAMESPACE, {}).get("refs", [])]
        assert refs and all(ref["session_id"] == session_id for ref in refs)
        assert (store.directory(session_id) / "media").is_dir()
    for path in store.directory("parent").parent.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl"}:
            assert state["sentinel"].encode() not in path.read_bytes()
    assert not registry.admissions
    assert set(registry.sessions) == {"parent"}


@pytest.mark.parametrize("child_routes", [True], indirect=True, ids=["prospective-t2"])
async def test_prospective_child_precheckpoint_failure_cleans_up_before_closing_owner(child_routes):
    parent, registry, store, rows, state = child_routes
    parent.coordinator.get("orchestrator").root_provider = SelectedProvider(
        parent.coordinator.get("providers")["fixture"],
        {"instance": "missing-child-account", "model": "fixture"})
    with pytest.raises(ValueError, match="inherited provider instance is unavailable"):
        await registry.spawn("worker", "must not count or send", parent,
                             sub_session_id="rejected-child", parent_messages=rows)
    factory = state["factory"]
    binding = factory.latest("rejected-child")
    assert factory.openings == [("rejected-child", False)]
    assert state["creations"][-1] == ("rejected-child", "parent", False)
    provider = state["providers"][-1]
    assert provider.cleanup_checked and not provider.counted and not provider.sent
    with pytest.raises(factory.conflict_type, match="closed"):
        binding.check()
    # Admission failed before context restoration/checkpoint readiness. Do not
    # claim the inherited ref was committed, or publish a partial child context.
    assert binding.expected.message_count == 0 and binding.last_receipt is None
    reopened = factory.binding_type.resume(store.directory("rejected-child"),
        session_id="rejected-child", expected=binding.expected)
    try:
        assert reopened.load_messages() == []
        _assert_legacy_fences(reopened.directory)
    finally:
        await reopened.aclose()
    assert registry.rows["rejected-child"]["status"] == "error"
    assert not registry.admissions and set(registry.sessions) == {"parent"}
    assert await parent.coordinator.get("context").get_messages() == rows


@pytest.mark.parametrize("child_routes", [True], indirect=True, ids=["prospective-t2"])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_prospective_child_provider_terminal_failure_retires_physical_owner(child_routes, failure):
    parent, registry, store, rows, state = child_routes
    state["provider_failure"] = failure
    with pytest.raises(BaseException):
        await registry.spawn("worker", "Inspect", parent, sub_session_id="child", parent_messages=rows)
    factory = state["factory"]
    binding = factory.latest("child")
    assert not registry.admissions and set(registry.sessions) == {"parent"}
    provider = state["providers"][-1]
    assert provider.cleanup_checked
    assert len(provider.counted) == len(provider.sent) == 1
    assert provider.sent[0].messages == []
    with pytest.raises(factory.conflict_type, match="closed"):
        binding.check()
    reopened = factory.binding_type.resume(binding.directory, session_id="child", expected=binding.expected)
    try:
        assert reopened.load_messages()
        assert binding.last_receipt.verify()["operation_id"] == reopened.expected.operation_id
    finally:
        await reopened.aclose()
    assert not registry.admissions and set(registry.sessions) == {"parent"}
    assert await parent.coordinator.get("context").get_messages() == rows
