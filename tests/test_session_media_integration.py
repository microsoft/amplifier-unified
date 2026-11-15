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


async def test_readiness_requires_positive_loop_report_and_actual_checkpoint(session):
    from amplifier_web.host.media import enable_media
    cap = session.caps[NAMESPACE]
    session.caps["session.durable_checkpoint"] = lambda: None
    cap.report("fixture-loop", event="consumer-ready", outcome="ready",
               details={"role": "loop", "contract": 1})
    enable_media(session.coordinator)
    assert cap.enabled is False  # Handler names/readiness cannot qualify a graph.
    session.caps["web.media.graph_qualified"] = True  # Obsolete booleans remain inert.
    for outcome in ("failed", "rejected", "ready"):
        cap.report("fixture-loop", event="consumer-ready", outcome=outcome,
                   details={"role": "loop", "contract": 1})
        enable_media(session.coordinator)
        assert cap.enabled is False  # Requires a named source join + issuer receipt.
    session.caps.pop("session.durable_checkpoint")
    enable_media(session.coordinator)
    assert cap.enabled is False


async def test_attachment_encoder_is_bound_to_this_coordinator_before_initialize(session, tmp_path, monkeypatch):
    from amplifier_web import attachments
    home = tmp_path / "app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    row = attachments.save(home, "image.png", base64.b64encode(session.raw).decode())
    cap = session.caps[NAMESPACE]
    cap.enabled = True
    encoded = await session.caps["live.attachments.encode"](
        SimpleNamespace(id="input-1", text="Inspect", attachments=[row]))
    ref = encoded["metadata"][NAMESPACE]["refs"][0]
    assert ref["session_id"] == session.native.session_id
    assert base64.b64encode(session.raw).decode() not in str(encoded)


async def test_unqualified_platform_binds_neutral_reader_without_creating_media_files(tmp_path, monkeypatch):
    from amplifier_web.host import media as host
    monkeypatch.setattr(host, "_durable_media_supported", lambda: False)
    caps, cleanup = {}, []
    coordinator = SimpleNamespace(session_id="neutral", config={}, get=lambda _: {},
        get_capability=caps.get, register_capability=lambda k, v: caps.update({k: v}),
        register_cleanup=cleanup.append, hooks=SimpleNamespace(register=lambda *args, **kw: None))
    store = SessionStore(tmp_path / "sessions")
    bind_media(SimpleNamespace(session_id="neutral", coordinator=coordinator), store)
    cap = caps[NAMESPACE]
    assert cap.enabled is False
    assert not (store.directory("neutral") / "media").exists()
    value = ChatRequest(messages=[Message(role="user", content="Text works")])
    class Provider:
        default_model = "text"
        def get_media_request_support(self, **kwargs):
            return {"v": 1, "positions": []}
        async def complete(self, request, **kwargs):
            cap.validate(request, purpose="transport", request_options=kwargs)
            return request.messages[0].content
    from amplifier_web.host.media import MediaOnlyDelivery
    assert await SurfaceProvider(Provider(), MediaOnlyDelivery(), coordinator=coordinator).complete(value) == "Text works"
    for callback in cleanup:
        await callback()

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

        async def stream(self, request, **kwargs):
            yield await self.complete(request, **kwargs)
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


@pytest.mark.parametrize("selected", [False, True], ids=["surface-root", "selected-root"])
async def test_repeated_private_fit_and_reuse_keep_canonical_refs_only(session, selected):
    """Actual Surface/selection seams: two counts, send, and a new fit of SAME input."""
    from amplifier_web.host.session import SelectedProvider
    value = await accepted(session)
    canonical = value.model_dump_json()
    wrapped, seen = provider(session)
    if selected:
        wrapped = SelectedProvider(wrapped, {"model": "fixture"})
    held = []
    for turn in range(2):
        start = len(seen.count)
        await wrapped.request_budget(value)
        await wrapped.request_budget(value)
        private = seen.count[start]
        held.append(private)
        assert seen.count[start + 1] is private
        assert private is not value
        assert base64.b64encode(session.raw).decode() in private.model_dump_json()
        assert value.model_dump_json() == canonical
        assert await wrapped.complete(value) == "done"
        assert seen.sent[turn] is private
        assert private.messages == []
        assert value.model_dump_json() == canonical
        saved, _ = session.store.load("root")
        assert saved[0]["metadata"][NAMESPACE]["refs"] == value.messages[0].metadata[NAMESPACE]["refs"]
        assert base64.b64encode(session.raw).decode() not in str(saved)
    assert held[0] is not held[1], "A released private request must not be resurrected"
    for path in session.store.directory("root").rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl"}:
            assert base64.b64encode(session.raw) not in path.read_bytes()


@pytest.mark.parametrize("mode", ["complete", "stream", "captured-stream", "compact", "captured-compact"])
async def test_selected_execution_owner_change_rejects_uncounted_private_send(session, mode):
    from amplifier_web.host.session import SelectedProvider
    value = await accepted(session)
    canonical = value.model_dump_json()
    original, counted = provider(session)
    replacement, foreign = provider(session)
    if "compact" in mode:
        async def compact(request, **kwargs):
            foreign.sent.append(request)
            return "uncounted"
        original.original.compact_context = compact
        replacement.original.compact_context = compact
    active = [original]
    selected = SelectedProvider(original.original, {}, execution_adapter=lambda _: active[0])
    await selected.request_budget(value)
    private = counted.count[0]
    captured_stream = selected.stream if mode == "captured-stream" else None
    captured_compact = selected.compact_context if mode == "captured-compact" else None
    active[0] = replacement
    with pytest.raises(ValueError, match="execution provider changed"):
        if mode == "complete":
            await selected.complete(value)
        elif "compact" in mode:
            await (captured_compact or selected.compact_context)(value)
        else:
            async for _ in (captured_stream or selected.stream)(value):
                pass
    assert not counted.sent and not foreign.sent and not foreign.count
    assert private.messages == [] and original.prepared == []
    assert value.model_dump_json() == canonical
    assert selected._selected_requests == selected._release_bindings == []


async def test_cached_pretransport_refusal_drops_serialized_private_frame(session):
    value = await accepted(session)
    wrapped, seen = provider(session)
    await wrapped.request_budget(value)
    private = seen.count[0]
    session.caps["web.worker_run"] = "changed-generation"
    with pytest.raises(ValueError) as failed:
        await wrapped.complete(value)
    sentinel = base64.b64encode(session.raw).decode()
    frame = failed.value.__traceback__
    while frame is not None:
        if frame.tb_frame.f_code.co_filename.endswith("surface_delivery.py"):
            locals_ = frame.tb_frame.f_locals
            assert sentinel not in str(locals_.get("existing")), "Cached fitted JSON leaked in failure frame"
        frame = frame.tb_next
    assert private.messages == [] and not seen.sent and wrapped.prepared == []


@pytest.mark.parametrize("stream", [False, True])
async def test_changed_dispatch_options_do_not_retain_private_tuple_in_any_surface_frame(session, stream):
    value = await accepted(session)
    canonical = value.model_dump_json()
    wrapped, seen = provider(session)
    await wrapped.request_budget(value, request_options={"temperature": 0.1})
    private = seen.count[0]
    with pytest.raises(ValueError, match="options changed") as failed:
        if stream:
            async for _ in wrapped.stream(value, temperature=0.9):
                pass
        else:
            await wrapped.complete(value, temperature=0.9)
    sentinel = base64.b64encode(session.raw).decode()
    def private_text(item):
        if isinstance(item, str):
            return sentinel in item
        if isinstance(item, (list, tuple)):
            return any(private_text(part) for part in item)
        if isinstance(item, dict):
            return any(private_text(part) for pair in item.items() for part in pair)
        return False
    frame = failed.value.__traceback__
    while frame:
        if frame.tb_frame.f_code.co_filename.endswith("surface_delivery.py"):
            assert not any(private_text(item) for item in frame.tb_frame.f_locals.values()), (
                "Known serialized private bytes retained in a SurfaceProvider traceback")
        frame = frame.tb_next
    assert private.messages == [] and not seen.sent and wrapped.prepared == []
    assert value.model_dump_json() == canonical


@pytest.mark.parametrize("retry", ["complete", "stream", "compact"])
async def test_selected_owner_refusal_requires_successful_explicit_fit_before_any_retry(session, retry):
    from amplifier_web.host.session import SelectedProvider
    value = await accepted(session)
    canonical = value.model_dump_json()
    original, old = provider(session)
    replacement, new = provider(session)
    async def compact(request, **kwargs):
        new.sent.append(request)
        return "uncounted compact"
    replacement.original.compact_context = compact
    active = [original]
    selected = SelectedProvider(original.original, {}, execution_adapter=lambda _: active[0])
    await selected.request_budget(value)
    previous = old.count[0]
    active[0] = replacement
    with pytest.raises(ValueError, match="execution provider changed"):
        await selected.complete(value)
    assert previous.messages == [] and original.prepared == []
    async def dispatch():
        if retry == "complete":
            return await selected.complete(value)
        if retry == "compact":
            return await selected.compact_context(value)
        return [part async for part in selected.stream(value)]
    for _ in range(2):
        with pytest.raises(ValueError, match="explicit budget"):
            await dispatch()
    with pytest.raises((ValueError, TypeError)):
        await selected.request_budget(value, request_options=object())
    with pytest.raises(ValueError, match="explicit budget"):
        await dispatch()
    budget = replacement.original.request_budget
    async def failed_budget(*args, **kwargs):
        raise RuntimeError("Explicit fit failed")
    replacement.original.request_budget = failed_budget
    with pytest.raises(RuntimeError, match="Explicit fit failed"):
        await selected.request_budget(value)
    with pytest.raises(ValueError, match="explicit budget"):
        await dispatch()
    assert not new.sent and not old.sent
    replacement.original.request_budget = budget
    await selected.request_budget(value)
    assert await selected.complete(value) == "done"
    assert new.count[-1] is new.sent[-1] and new.sent[-1].messages == []
    assert value.model_dump_json() == canonical


async def test_selected_refusal_state_is_bounded_weak_and_saturation_requires_completed_fit():
    import gc
    import weakref
    from amplifier_web.host.session import SelectedProvider
    selected = SelectedProvider(object(), {})
    requests = [ChatRequest(messages=[]) for _ in range(17)]
    for request in requests:
        selected._mark_media_fit_refusal(request)
    assert len(selected._media_fit_refusals) == 16 and selected._media_fit_refusal_overflow
    assert all(type(ref) is weakref.ReferenceType for ref in selected._media_fit_refusals)
    value, execution = requests[-1], selected.original
    with pytest.raises(ValueError, match="successful explicit budget"):
        selected._check_fitted_execution(value, execution)
    selected._release_bindings.append((value, value, execution, {}, {}, False))
    with pytest.raises(ValueError, match="successful explicit budget"):
        selected._check_fitted_execution(value, execution)  # Merely starting a budget cannot clear it.
    selected._finish_selected_budget(value, value, execution)
    selected._check_fitted_execution(value, execution)
    selected._forget_request(value)
    with pytest.raises(ValueError, match="successful explicit budget"):
        selected._check_fitted_execution(value, execution)
    watched = weakref.ref(requests[0])
    requests.clear()
    gc.collect()
    assert watched() is None, "Refusal bookkeeping must not retain canonical/private payloads"


async def test_early_refit_scope_failure_drops_new_private_serialized_tuple(session):
    class Delivery(NoWorkDelivery):
        async def prepare_refit(self, request, provider, *, binding):
            return request
        def check_execution_scope(self, scope):
            raise ValueError("Refit scope expired")
    value = await accepted(session)
    wrapped, seen = provider(session)
    wrapped.delivery = Delivery()
    with pytest.raises(ValueError, match="Refit scope expired") as failed:
        await wrapped._prepare_inner(value, refit_binding={"executionScope": {}}, projection_out=[])
    sentinel = base64.b64encode(session.raw).decode()
    frame = failed.value.__traceback__
    while frame:
        if frame.tb_frame.f_code.co_filename.endswith("surface_delivery.py"):
            for name in ("entry", "cached", "fitted"):
                assert sentinel not in str(frame.tb_frame.f_locals.get(name))
        frame = frame.tb_next
    assert not wrapped.prepared and not seen.count and not seen.sent


async def test_selected_rebudget_retires_previous_private_execution_owner(session):
    from amplifier_web.host.session import SelectedProvider
    value = await accepted(session)
    canonical = value.model_dump_json()
    original, counted = provider(session)
    replacement, foreign = provider(session)
    active = [original]
    selected = SelectedProvider(original.original, {}, execution_adapter=lambda _: active[0])
    await selected.request_budget(value)
    previous = counted.count[0]
    active[0] = replacement
    await selected.request_budget(value)
    assert previous.messages == [] and original.prepared == [], "Re-budget stranded the prior private fit"
    assert await selected.complete(value) == "done"
    assert foreign.count[0] is foreign.sent[0]
    assert foreign.sent[0].messages == [] and not counted.sent
    assert value.model_dump_json() == canonical


async def test_selected_rebudget_preserves_custody_if_previous_release_refuses(session, monkeypatch):
    from amplifier_web.host.session import SelectedProvider
    value = await accepted(session)
    original, counted = provider(session)
    replacement, foreign = provider(session)
    active = [original]
    selected = SelectedProvider(original.original, {}, execution_adapter=lambda _: active[0])
    await selected.request_budget(value)
    previous = counted.count[0]
    release = original.release_media_request
    monkeypatch.setattr(original, "release_media_request", lambda *args, **kwargs: False)
    active[0] = replacement
    with pytest.raises(ValueError, match="could not be released"):
        await selected.request_budget(value)
    assert selected._release_bindings[0][2] is original
    assert previous.messages and not foreign.count and not foreign.sent
    monkeypatch.setattr(original, "release_media_request", release)
    await selected.request_budget(value)
    assert previous.messages == []
    assert await selected.complete(value) == "done"
    assert foreign.count[0] is foreign.sent[0] and foreign.sent[0].messages == []


async def test_selected_invalid_rebudget_options_leave_existing_fit_releasable(session):
    from amplifier_web.host.session import SelectedProvider
    value = await accepted(session)
    canonical = value.model_dump_json()
    surface, seen = provider(session)
    selected = SelectedProvider(surface, {})
    await selected.request_budget(value)
    private = seen.count[0]
    with pytest.raises((ValueError, TypeError)):
        await selected.request_budget(value, request_options=object())
    assert selected._release_bindings and selected._release_bindings[0][2] is surface
    assert not seen.sent and private.messages
    assert selected.release_media_request(value, request_options={}) is True
    assert private.messages == [] and surface.prepared == []
    assert value.model_dump_json() == canonical


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


@pytest.mark.parametrize("mode", ["admission-revoke", "sdk-retry"])
async def test_request_only_consent_is_rechecked_after_admission_and_never_renews_for_sdk_retry(session, mode):
    from amplifier_web.host.media import record_request_only
    from amplifier_web.execution_events import ExecutionEvents
    value = await accepted(session)
    state = {"allowed": True, "attempts": 0}
    overlay = Message(role="user", content=[{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(session.raw).decode()}}])
    class Delivery(NoWorkDelivery):
        async def prepare(self, request, provider):
            record_request_only(overlay)
            return request.model_copy(update={"messages": [*request.messages, overlay]})
        async def revalidate(self, request):
            if not state["allowed"]:
                raise ValueError("Original consent ended")
            return request.model_copy(deep=True)
    class Provider:
        default_model = "fixture"
        config = {}
        def get_info(self):
            return {"id": "fixture", "defaults": {"model": "fixture"}}
        def get_media_request_support(self, **kwargs):
            return {"v": 1, "positions": ["user", "tool"]}
        async def request_budget(self, request, **kwargs):
            return {"input_tokens": 42}
        async def complete(self, request, **kwargs):
            for _ in range(2 if mode == "sdk-retry" else 1):
                session.caps[NAMESPACE].validate(request, purpose="transport", request_options=kwargs)
                state["attempts"] += 1
                await asyncio.sleep(0)  # Simulated provider retry backoff.
            return "unexpected"
    events = ExecutionEvents("root", lambda row: None)
    if mode == "admission-revoke":
        async def admission(row):
            state["allowed"] = False
        events.admission_guard = admission
    wrapped = SurfaceProvider(events.instrument_provider("root", Provider()), Delivery(),
                              coordinator=session.coordinator)
    await wrapped.request_budget(value)
    with pytest.raises(ValueError, match="(consent ended|fresh host consent)"):
        await wrapped.complete(value)
    assert state["attempts"] == (1 if mode == "sdk-retry" else 0)
    assert not session.caps["web.media.request_only"].requests
    assert not wrapped._active_media


async def test_failure_traceback_does_not_retain_serialized_private_pixels(session):
    value = await accepted(session)
    wrapped, seen = provider(session)
    async def failure(request, **kwargs):
        raise RuntimeError("Unknown provider outcome")
    wrapped.original.complete = failure
    await wrapped.request_budget(value)
    with pytest.raises(RuntimeError) as failed:
        await wrapped.complete(value)
    sentinel = base64.b64encode(session.raw).decode()
    frame = failed.value.__traceback__
    while frame is not None:
        if frame.tb_frame.f_code.co_filename.endswith("surface_delivery.py"):
            for local in frame.tb_frame.f_locals.values():
                assert not isinstance(local, str) or sentinel not in local
                assert not isinstance(local, list) or all(
                    not isinstance(item, str) or sentinel not in item for item in local)
        frame = frame.tb_next
    assert not wrapped._active_media


async def test_count_failure_traceback_does_not_retain_fitted_row_pixels(session):
    value = await accepted(session)
    wrapped, _ = provider(session)
    async def failure(*args, **kwargs):
        raise RuntimeError("Count failed")
    wrapped.original.request_budget = failure
    with pytest.raises(RuntimeError) as failed:
        await wrapped.request_budget(value)
    sentinel = base64.b64encode(session.raw).decode()
    frame = failed.value.__traceback__
    while frame is not None:
        if frame.tb_frame.f_code.co_filename.endswith("surface_delivery.py"):
            assert frame.tb_frame.f_locals.get("row") is None
            assert sentinel not in str(frame.tb_frame.f_locals.get("request"))
        frame = frame.tb_next
    assert wrapped.prepared == []


async def test_stream_advance_does_not_rearm_request_only_transport_without_new_admission(session):
    from amplifier_web.host.media import record_request_only
    value = await accepted(session)
    wrapped, _ = provider(session)
    attempts = []
    overlay = Message(role="user", content=[{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(session.raw).decode()}}])
    class Delivery(NoWorkDelivery):
        async def prepare(self, request, provider):
            record_request_only(overlay)
            return request.model_copy(update={"messages": [*request.messages, overlay]})
    wrapped.delivery = Delivery()
    async def stream(request, **kwargs):
        session.caps[NAMESPACE].validate(request, purpose="transport", request_options=kwargs)
        attempts.append(True)
        yield "started"
        session.caps[NAMESPACE].validate(request, purpose="transport", request_options=kwargs)
        attempts.append(True)
        yield "unexpected"
    wrapped.original.stream = stream
    iterator = wrapped.stream(value)
    assert await anext(iterator) == "started"
    with pytest.raises(ValueError, match="fresh host consent"):
        await anext(iterator)
    assert attempts == [True]
    assert not wrapped._active_media


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


async def test_request_only_scope_never_admits_inherited_task_or_copied_marker():
    from amplifier_web.host.media import record_request_only, request_only_scope, RequestOnlyRegistry
    image = Message(role="user", content=[{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "SENTINEL"}}])
    registry = RequestOnlyRegistry()
    with request_only_scope() as issued:
        await asyncio.create_task(asyncio.sleep(0))
        async def foreign():
            record_request_only(image)
        await asyncio.create_task(foreign())
        assert issued == []
        record_request_only(image)
    value = ChatRequest(messages=[image])
    registry.bind(value, issued)
    assert registry.validate(value, message_index=0, block_index=0, purpose="model")
    copied = value.model_copy(deep=True)
    assert not registry.validate(copied, message_index=0, block_index=0, purpose="model")
    async def foreign_validate():
        assert not registry.validate(value, message_index=0, block_index=0, purpose="model")
    await asyncio.create_task(foreign_validate())
    registry.release(value)
    assert not registry.requests


@pytest.mark.parametrize("mutate", [False, True])
async def test_compaction_observation_passes_exact_options_to_pretransport_validation(session, mutate):
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.host.media import MediaOnlyDelivery
    value = (await accepted(session)).model_copy(update={"metadata": {"purpose": "context-compaction"}})
    options = {"native": {"temperature": 0.2}}
    calls = []
    class Provider:
        default_model = "fixture"
        config = {}
        def get_info(self):
            return {"id": "fixture", "defaults": {"model": "fixture"}}
        def get_media_request_support(self, **kwargs):
            return {"v": 1, "positions": ["tool"]}
        async def complete(self, request, **kwargs):
            return "unused"
        async def compact_context(self, request, **kwargs):
            calls.append((request.model_copy(deep=True), copy.deepcopy(kwargs)))
            return "summary"
    events = ExecutionEvents("root", lambda row: None)
    if mutate:
        async def admission(row):
                options["native"]["temperature"] = 0.3
        events.admission_guard = admission
    observed = events.instrument_provider("root", Provider())
    wrapped = SurfaceProvider(observed, MediaOnlyDelivery(), coordinator=session.coordinator)
    if mutate:
        with pytest.raises(ValueError, match="options changed"):
            await wrapped.compact_context(value, **options)
        assert calls == []
    else:
        assert await wrapped.compact_context(value, **options) == "summary"
        assert calls[0][1] == options
        assert calls[0][0].messages[0].content[-1].type == "image"


@pytest.mark.parametrize("route", ["ordinary", "custom-finite", "compaction"])
async def test_actual_prefit_attachment_locator_after_origin_is_unavailable(tmp_path, monkeypatch, route):
    from pathlib import Path
    from amplifier_web import attachments, session_assets
    from amplifier_web.host.media import execution_providers, MediaOnlyDelivery
    origin, destination = tmp_path / "origin", tmp_path / "destination"
    data = b"%PDF-offline-transfer-fixture"
    row = attachments.save(origin, "report.pdf", base64.b64encode(data).decode())
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(origin))
    encoded = await attachments.encode(SimpleNamespace(id="input", text="Read", attachments=[row]))
    selected = session_assets.upload_rows(visible=[{"attachments": [row]}])
    session_assets.install_uploads(destination, session_assets.capture_uploads(origin, selected), selected)
    origin.rename(tmp_path / "origin-preserved")  # Never delete fixture evidence.
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(destination))
    canonical = ChatRequest(messages=[Message(role="user", **encoded)])
    before = canonical.model_dump_json()
    counted, sent = [], []
    class Provider:
        async def request_budget(self, request, **kwargs):
            counted.append(request)
            return {"input_tokens": 1}
        async def complete(self, request, **kwargs):
            sent.append(request)
            return "done"
        async def compact_context(self, request, **kwargs):
            sent.append(request)
            return "summary"
    raw = Provider()
    if route == "custom-finite":
        caps = {}
        coordinator = SimpleNamespace(get_capability=caps.get, get=lambda _: None,
                                      register_capability=lambda k, v: caps.update({k: v}))
        wrapped = execution_providers(coordinator, {"provider": raw})["provider"]
    else:
        wrapped = SurfaceProvider(raw, MediaOnlyDelivery())
    if route == "compaction":
        canonical.metadata = {"purpose": "context-compaction"}
        before = canonical.model_dump_json()
    await wrapped.request_budget(canonical)
    if route == "compaction":
        await wrapped.compact_context(canonical)
    else:
        await wrapped.complete(canonical)
    assert sent[0] is counted[0]
    locator = sent[0].messages[0].content[-1].text.split("\nLocal file: ", 1)[1]
    assert Path(locator).read_bytes() == data
    assert str(origin) not in locator and str(destination) in locator
    assert canonical.model_dump_json() == before


@pytest.mark.parametrize("mode", ["allowed", "empty", "mutated", "changed"])
async def test_indexed_support_binds_exact_postoverlay_source_without_v1_fallback(session, mode):
    pytest.importorskip("amplifier_foundation.session.media_projection").validate_media_request_support
    original = await accepted(session)
    counted, sent, facts = [], [], {"allowed": mode != "empty"}
    class Delivery(NoWorkDelivery):
        async def prepare(self, request, provider):
            return request.model_copy(update={"messages": [
                Message(role="user", content="owned overlay before image"), *request.messages]})
    class Provider:
        default_model = "fixture"
        def get_media_request_support(self, **kwargs):
            pytest.fail("Indexed presence must not broaden through v1")
        def get_media_request_support_for_request(self, *, request, model, request_options=None):
            assert request.messages[0].role == "user"
            assert request.messages[1].metadata[NAMESPACE]["refs"]
            assert "base64" not in request.model_dump_json()
            if mode == "mutated":
                request.messages.clear()
            return {"v": 2, "positions": [{"message_index": 1, "role": "tool"}]
                    if facts["allowed"] else [], "mime_types": ["image/png"]}
        async def request_budget(self, request, **kwargs):
            counted.append(request.model_copy(deep=True))
            return {"input_tokens": 5}
        async def complete(self, request, **kwargs):
            sent.append(request.model_copy(deep=True))
            return "done"
    wrapped = SurfaceProvider(Provider(), Delivery(), coordinator=session.coordinator)
    if mode == "mutated":
        with pytest.raises(ValueError, match="mutated"):
            await wrapped.request_budget(original)
        assert not counted and not sent
        return
    await wrapped.request_budget(original)
    if mode == "changed":
        facts["allowed"] = False
        with pytest.raises(ValueError):
            await wrapped.complete(original)
        assert not sent
    else:
        assert await wrapped.complete(original) == "done"
        images = [block for message in sent[-1].messages
                  for block in message.content if isinstance(message.content, list)
                  if getattr(block, "type", None) == "image"]
        assert bool(images) is (mode == "allowed")
    assert len(original.messages) == 1 and original.messages[0].role == "tool"

async def test_bound_attachment_encoder_accepts_actual_loop_argument_without_rebinding(session, monkeypatch):
    from amplifier_web import attachments
    observed = []
    async def encode(command, coordinator):
        observed.append((command, coordinator))
        return {"content": "refs-only", "metadata": {}}
    monkeypatch.setattr(attachments, "encode", encode)
    # bind_media captures the actual attachment helper at installation, so bind
    # a fresh structural host instead of replacing its already-issued callback.
    from amplifier_web.host import media
    from types import SimpleNamespace
    capabilities = {}
    coordinator = SimpleNamespace(
        session_id=session.caps["web.media.store"].session_id,
        register_capability=capabilities.__setitem__,
        get_capability=capabilities.get,
        register_cleanup=lambda cleanup: None,
        hooks=SimpleNamespace(register=lambda *args, **kwargs: None))
    class Store:
        def media(self, *args, **kwargs):
            return session.caps["web.media.store"]
    media.bind_media(SimpleNamespace(session_id=coordinator.session_id, coordinator=coordinator), Store())
    command = SimpleNamespace(text="read")
    encoder = capabilities["live.attachments.encode"]
    assert await encoder(command, coordinator) == {"content": "refs-only", "metadata": {}}
    assert observed == [(command, coordinator)]
    with pytest.raises(ValueError, match="another construction"):
        await encoder(command, SimpleNamespace(get_capability=lambda name: object()))
    assert len(observed) == 1


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
    seen, capabilities, counted, private_requests, child_ids = [], [], [], [], []

    class Provider:
        default_model = "fixture"
        name = "fixture"
        config = {}

        def get_media_request_support(self, **kwargs):
            return {"v": 1, "positions": ["tool"]}

        async def request_budget(self, request, *, request_options=None):
            counted.append(request)
            return {"input_tokens": 42}

        async def complete(self, request, **kwargs):
            assert counted[-1] is request
            private_requests.append(request)
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
        child_ids.append(coordinator.session_id)
        assert base64.b64encode(session.raw).decode() not in str(source)
        request = ChatRequest(messages=[Message.model_validate(row) for row in source])
        from amplifier_web.host.session import SelectedProvider
        selected = self.root_provider or next(iter(providers.values()))
        if isinstance(selected, SelectedProvider):
            assert _provider_owner(selected.original) is _provider_owner(providers["fixture"])
        await selected.request_budget(request)
        assert await selected.complete(request) == "done"
        assert request.messages and all(
            getattr(block, "type", None) != "image"
            for message in request.messages
            for block in message.content if isinstance(message.content, list))
        assert private_requests[-1].messages == []
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
        assert len(counted) == len(private_requests) == expected
        assert len({id(request) for request in private_requests}) == expected
        assert len({id(cap) for cap in capabilities}) == expected
        for request, _ in seen:
            block = next(block for message in request.messages for block in message.content
                         if getattr(block, "type", None) == "image")
            assert hashlib.sha256(base64.b64decode(block.source["data"])).hexdigest() == hashlib.sha256(session.raw).hexdigest()
        for child_id in child_ids:
            child_rows, _ = session.store.load(child_id)
            assert child_rows and base64.b64encode(session.raw).decode() not in str(child_rows)
            refs = [ref for row in child_rows
                    for ref in (row.get("metadata") or {}).get(NAMESPACE, {}).get("refs", [])]
            assert refs and all(ref["session_id"] == child_id for ref in refs)
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()