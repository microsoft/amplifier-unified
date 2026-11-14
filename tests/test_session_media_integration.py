"""Actual Foundation assets/checkpoints and Unified provider boundary, no inference."""
import asyncio
import base64
import copy
import hashlib
import io
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message

media = pytest.importorskip("amplifier_foundation.session.media")
pytest.importorskip("amplifier_foundation.session.media_projection")

from amplifier_web.host.media import NAMESPACE, acknowledge_media, bind_media
from amplifier_web.host.storage import SessionStore
from amplifier_web.surface_delivery import SurfaceProvider
from test_provider_capture import NoWorkDelivery


@pytest.fixture
def session(tmp_path):
    if not hasattr(SessionStore, "media"):
        pytest.skip("Requires the paired Unified media storage API")
    from PIL import Image
    pixels = io.BytesIO()
    Image.new("RGB", (2, 2), (37, 83, 149)).save(pixels, format="PNG")
    raw = pixels.getvalue()
    caps, cleanup = {}, []
    coordinator = SimpleNamespace(
        session_id="root", config={},
        get=lambda _: {}, get_capability=caps.get,
        register_capability=lambda key, value: caps.update({key: value}),
        register_cleanup=cleanup.append,
        hooks=SimpleNamespace(register=lambda *args, **kwargs: None),
    )
    store = SessionStore(tmp_path / "sessions")
    native = SimpleNamespace(session_id="root", coordinator=coordinator)
    bind_media(native, store)
    yield SimpleNamespace(caps=caps, coordinator=coordinator, store=store,
                          raw=raw, native=native)
    for callback in cleanup:
        asyncio.run(callback())


async def accepted(session, *, checkpoint=True):
    cap = session.caps[NAMESPACE]
    # This fixture explicitly qualifies the synthetic producer, not a live graph.
    cap.enabled = True
    origin = {"kind": "upload", "session_id": "root", "id": "input-1"}
    ingested = await cap.ingest(session.raw, source_format="image-bytes",
                                origin=origin, policy=None)
    ref = ingested["value"]
    await cap.publish([ref], origin=origin, disposition="retain")
    rows = [{"role": "tool", "tool_call_id": "call-1", "name": "fixture",
             "content": "Image observed", "metadata": {
                 NAMESPACE: {"refs": [ref]}, "opaque": {"signature": "preserve"}}}]
    canonical = session.store.save("root", rows, {})
    if checkpoint:
        acknowledge_media(session.coordinator, session.store, "root", canonical)
    return ChatRequest(messages=[Message.model_validate(row) for row in rows])


def provider(session, *, support=True):
    seen = SimpleNamespace(count=[], sent=[], digests=[], state={"support": support})
    class Provider:
        default_model = "fixture"

        def get_media_request_support(self, *, model, request_options=None):
            assert model == "fixture"
            return {"v": 1, "positions": ["tool", "user"] if seen.state["support"] else []}

        async def request_budget(self, request, *, request_options=None, **kwargs):
            session.caps[NAMESPACE].validate(request, purpose="count",
                                           request_options=request_options or {})
            seen.count.append(request)
            return {"measurement": {"method": "provider_count", "input_tokens": 42}}

        async def complete(self, request, **kwargs):
            session.caps[NAMESPACE].validate(request, purpose="transport", request_options=kwargs)
            seen.sent.append(request)
            seen.digests.extend(hashlib.sha256(base64.b64decode(block.source["data"])).hexdigest()
                               for message in request.messages if isinstance(message.content, list)
                               for block in message.content if block.type == "image")
            return "done"
    raw = Provider()
    wrapped = SurfaceProvider(raw, NoWorkDelivery(), coordinator=session.coordinator)
    return wrapped, seen


@pytest.mark.parametrize("support", [False, True])
async def test_actual_asset_checkpoint_count_send_and_release(session, support):
    value = await accepted(session)
    canonical = value.model_dump_json()
    wrapped, seen = provider(session, support=support)
    await wrapped.request_budget(value)
    private = seen.count[0]
    before = private.model_dump_json()
    if support:
        assert base64.b64encode(session.raw).decode() in before
    else:
        assert "Image unavailable" in before and "no pixels supplied" in before
    assert value.model_dump_json() == canonical
    assert await wrapped.complete(value) == "done"
    assert seen.sent[0] is private
    assert seen.digests == ([hashlib.sha256(session.raw).hexdigest()] if support else [])
    # Foundation clears the private request and borrowed image block sources.
    assert private.messages == []
    for path in session.store.directory("root").rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl"}:
            assert base64.b64encode(session.raw) not in path.read_bytes()


async def test_asset_publication_without_settled_checkpoint_refuses_count(session):
    value = await accepted(session, checkpoint=False)
    wrapped, seen = provider(session)
    with pytest.raises(ValueError):
        await wrapped.request_budget(value)
    assert not seen.count and not seen.sent


@pytest.mark.parametrize("change", ["forged", "cross-session", "source-policy", "epoch"])
async def test_actual_ref_mutation_or_policy_refuses_before_transport(session, change):
    value = await accepted(session)
    if change == "forged":
        value.messages[0].metadata[NAMESPACE]["refs"][0]["provenance"]["id"] = "forged"
    elif change == "cross-session":
        value.messages[0].metadata[NAMESPACE]["refs"][0]["session_id"] = "other"
    wrapped, seen = provider(session)
    if change in {"forged", "cross-session"}:
        with pytest.raises(ValueError):
            await wrapped.request_budget(value)
        assert not seen.count
        return
    await wrapped.request_budget(value)
    store = session.caps["web.media.store"]
    if change == "epoch":
        store.revoke()
    else:
        def refuse(*args, **kwargs):
            raise media.MediaRefusal("source unavailable", code="source_policy")
        store.policy_verifier = refuse
    with pytest.raises(ValueError):
        await wrapped.complete(value)
    assert not seen.sent


async def test_absent_capability_uses_validated_neutral_position_not_silent_metadata(session):
    value = await accepted(session)
    session.caps.pop(NAMESPACE)
    wrapped, seen = provider(session)
    # This unsupported route does not call a media-aware provider's own witness.
    async def budget(request, **kwargs):
        seen.count.append(request)
        return {"input_tokens": 1}
    async def complete(request, **kwargs):
        seen.sent.append(request)
        return "done"
    wrapped.original.request_budget = budget
    wrapped.original.complete = complete
    await wrapped.request_budget(value)
    private = seen.count[0]
    assert private.messages[0].role == "tool" and private.messages[0].tool_call_id == "call-1"
    assert "Image unavailable" in private.messages[0].content
    assert await wrapped.complete(value) == "done"
    assert seen.sent[0] is private


@pytest.mark.parametrize("change", ["default-model", "support", "worker-run", "config"])
async def test_provider_binding_changes_after_count_refuse_actual_transport(session, change):
    value = await accepted(session)
    wrapped, seen = provider(session)
    await wrapped.request_budget(value)
    if change == "default-model":
        wrapped.original.default_model = "other"
    elif change == "support":
        seen.state["support"] = False
    elif change == "worker-run":
        session.caps["web.worker_run"] = "new-run"
    else:
        wrapped.original.config = {"endpoint": "different"}
    with pytest.raises(ValueError):
        await wrapped.complete(value)
    assert not seen.sent


@pytest.mark.parametrize("admitted", [False, True])
async def test_request_only_issued_overlay_survives_without_admitting_canonical_inline(session, admitted):
    from amplifier_web.host.media import record_request_only
    value = await accepted(session)
    overlay = Message(role="user", content=[{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(session.raw).decode()}}],
        metadata={"ephemeral": True, "surfaceObservation": True})
    original_inline = overlay.model_copy(deep=True)
    value.messages.append(original_inline)  # Marker alone must not admit this one.
    wrapped, seen = provider(session)
    class Delivery(NoWorkDelivery):
        async def prepare(self, request, provider):
            if admitted:
                record_request_only(overlay)
            return request.model_copy(update={"messages": [*request.messages, overlay]})
    wrapped.delivery = Delivery()
    await wrapped.request_budget(value)
    private = seen.count[0]
    assert private.messages[-2].content[0].type == "text"
    assert "inline image is not admitted" in private.messages[-2].content[0].text
    assert private.messages[-1].content[0].type == ("image" if admitted else "text")
    assert await wrapped.complete(value) == "done"
    assert not session.caps["web.media.request_only"].requests
    assert value.messages[-1] is original_inline and original_inline.content[0].source["data"]


async def test_source_classification_is_owner_journal_and_resume_does_not_renew_consent(session):
    cap = session.caps[NAMESPACE]
    cap.enabled = True
    origin = {"kind": "tool", "session_id": "root", "id": "screen-call"}
    ingested = await cap.ingest(session.raw, source_format="image-bytes", origin=origin, policy=None)
    ref = ingested["value"]
    state = {"allowed": True}
    class Computer:
        def _validate_media_ref(self, value, *, purpose):
            if value == ref and state["allowed"]:
                return True
            raise ValueError("original screenshot consent ended")
    tools = {"computer": Computer()}
    session.coordinator.get = lambda name: tools if name == "tools" else {}
    assert session.caps["computer_use.media_policy"](ref, purpose="persist") is True
    await cap.publish([ref], origin=origin, disposition="retain")
    rows = [{"role": "tool", "tool_call_id": "screen-call", "content": "Screenshot",
             "metadata": {NAMESPACE: {"refs": [ref]}}}]
    saved = session.store.save("root", rows, {})
    acknowledge_media(session.coordinator, session.store, "root", saved)
    assert session.caps["web.media.store"].get_origin_classification(ref) == "computer-use"
    assert not (session.store.directory("root") / "media-sources").exists()
    wrapped, seen = provider(session)
    value = ChatRequest(messages=[Message.model_validate(row) for row in rows])
    await wrapped.request_budget(value)
    state["allowed"] = False
    with pytest.raises(ValueError):
        await wrapped.complete(value)
    assert not seen.sent
    # New canonical candidate sees a truthful fitted warning, never recapture.
    await wrapped.request_budget(value.model_copy(deep=True))
    assert all(block.type != "image" for block in seen.count[-1].messages[0].content)
    assert ref["provenance"] == rows[0]["metadata"][NAMESPACE]["refs"][0]["provenance"]


@pytest.mark.parametrize("mode", ["finite", "selected", "nested", "resumed", "persistent"])
async def test_actual_child_provider_receives_own_adopted_pixels_without_parent_capability(
        session, tmp_path, monkeypatch, mode):
    import importlib.util
    import sys
    from pathlib import Path
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children, _PERSISTENT
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.media import _provider_owner

    spec = importlib.util.spec_from_file_location("media_children_fixture",
        Path(__file__).parent / "fixtures/standalone_children_probe.py")
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    inherited = await accepted(session)
    rows = [message.model_dump() for message in inherited.messages]
    plan = {"session": {"orchestrator": {"module": "loop-live"},
                        "context": {"module": "context-simple"}},
            "agents": {"worker": {"instruction": "Inspect the inherited image"}}}
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    seen, capabilities = [], []

    class Provider:
        default_model = "fixture"
        name = "fixture"
        config = {}

        def get_media_request_support(self, **kwargs):
            return {"v": 1, "positions": ["tool"]}

        async def request_budget(self, request, *, request_options=None):
            return {"input_tokens": 42}

        async def complete(self, request, **kwargs):
            seen.append((request.model_copy(deep=True), id(request)))
            return "done"

    class Prepared(fixture.ProbePrepared):
        async def create_session(self, **kwargs):
            child = await super().create_session(**kwargs)
            await child.coordinator.mount("providers", Provider(), name="fixture")
            child.coordinator.get("orchestrator").root_provider = None
            return child

    prepared = Prepared(plan, BundleModuleResolver(paths),
                        Bundle(name="fixture", session=plan["session"], agents=plan["agents"]))
    async def initialize(constructing):
        bind_media(constructing, session.store)
    parent = await prepared.create_session(session_id="parent",
        before_initialize=initialize)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        if coordinator.session_id == "parent":
            return "unused"
        cap = coordinator.get_capability(NAMESPACE)
        capabilities.append(cap)
        assert cap is not session.caps[NAMESPACE]
        assert cap.session_id == coordinator.session_id
        source = await context.get_messages()
        request = ChatRequest(messages=[Message.model_validate(row) for row in source])
        from amplifier_web.host.session import SelectedProvider
        selected = self.root_provider or next(iter(providers.values()))
        if isinstance(selected, SelectedProvider):
            assert _provider_owner(selected.original) is _provider_owner(providers["fixture"])
        await selected.request_budget(request)
        assert await selected.complete(request) == "done"
        assert "base64" not in str(await context.get_messages())
        if mode == "nested" and coordinator.session_id == "child":
            registry = coordinator.get_capability("live.children")
            await registry.spawn("worker", "nested", registry.sessions["child"],
                                 sub_session_id="grandchild", parent_messages=source)
        return "done"

    # The parent itself does not execute; move only selected, verified root refs
    # into its own canonical store before delegating them.
    from amplifier_web.host.media import adopt_messages
    parent_rows = await adopt_messages(parent.coordinator, session.coordinator, rows)
    await parent.coordinator.get("context").set_messages(parent_rows)
    canonical = session.store.save("parent", parent_rows, {})
    acknowledge_media(parent.coordinator, session.store, "parent", canonical)
    parent.coordinator.register_capability("session.working_dir", str(tmp_path))
    if mode == "selected":
        from amplifier_web.host.session import SelectedProvider
        root_provider = parent.coordinator.get("providers")["fixture"]
        parent.coordinator.get("orchestrator").root_provider = SelectedProvider(
            root_provider, {"instance": "fixture", "model": "fixture"})
        parent.coordinator.get("orchestrator").config = {"inherit_effective_model": True}
    registry = await install_children(parent, prepared, Runtime("parent"), session.store, Approvals(None, None))
    monkeypatch.setattr(fixture.FiniteOrchestrator, "execute", execute)
    token = _PERSISTENT.set(mode == "persistent")
    try:
        await registry.spawn("worker", "Inspect", parent, sub_session_id="child",
                             parent_messages=parent_rows)
        if mode == "resumed":
            await registry.resume("child", "Continue", parent)
        expected = 2 if mode in {"nested", "resumed"} else 1
        assert len(seen) == expected
        assert len({id(cap) for cap in capabilities}) == expected
        for request, _ in seen:
            block = next(block for message in request.messages for block in message.content
                         if getattr(block, "type", None) == "image")
            assert hashlib.sha256(base64.b64decode(block.source["data"])).hexdigest() == hashlib.sha256(session.raw).hexdigest()
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()