"""Private host media projection, without inference or desktop capture."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message

from amplifier_web.host.media import NAMESPACE, MediaOnlyDelivery, execution_providers
from amplifier_web.host.session import SelectedProvider
from amplifier_web.surface_delivery import SurfaceProvider


def request():
    return ChatRequest(messages=[
        Message(role="assistant", content="Inspecting", tool_calls=[
            {"id": "call-1", "name": "read_file", "arguments": {}}]),
        Message(role="tool", tool_call_id="call-1", name="read_file", content="Image",
                metadata={NAMESPACE: {"refs": [{"fixture": "ref-only"}]},
                          "opaque": {"signature": "unchanged"}}),
    ])


class Prepared:
    def __init__(self, value, options):
        self.request = value.model_copy(deep=True)
        self.options = copy.deepcopy(options)
        self.released, self.valid = False, True
        self.request.messages[-1].content = [
            {"type": "text", "text": "Image"},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": "PIXEL_SENTINEL"}},
        ]
        # model_copy does not validate update values; replace through Core here.
        self.request = ChatRequest.model_validate(self.request.model_dump())

    def validate(self, *, request_options=None):
        assert not self.released
        if not self.valid:
            raise ValueError("Media expired")
        assert request_options == self.options

    def release(self):
        self.released = True
        self.request = None


def case(*, work=False):
    prepared, measured, sent, compacted = [], [], [], []
    caps = {}
    scope = {"generationId": "generation-1", "inputIds": ["input-1"], "stopRevision": 0}
    caps["web.work_origin_scope"] = lambda: copy.deepcopy(scope)

    async def prepare(value, **kwargs):
        assert kwargs["provider_identity"]["media_support"] == {"v": 1, "positions": ["tool"]}
        item = Prepared(value, kwargs["request_options"])
        prepared.append(item)
        return item
    caps[NAMESPACE] = SimpleNamespace(version=1, prepare_request=prepare)
    coordinator = SimpleNamespace(get_capability=caps.get, get=lambda _: None,
                                  register_capability=lambda k, v: caps.update({k: v}))

    class Provider:
        default_model = "fixture-model"

        def get_media_request_support(self, *, model, request_options=None):
            assert model == "fixture-model"
            return {"v": 1, "positions": ["tool"]}

        async def request_budget(self, value, *, request_options=None, **kwargs):
            measured.append(value)
            return {"input_tokens": 100}

        async def complete(self, value, **kwargs):
            sent.append(value)
            return "done"

        async def stream(self, value, **kwargs):
            sent.append(value)
            yield "started"
            await asyncio.Event().wait()

        async def compact_context(self, value, **kwargs):
            compacted.append(value)
            return {"summary": "refs-only"}

    class Delivery(MediaOnlyDelivery):
        def __init__(self):
            self.lookups, self.commits = 0, 0
            self.binding = {"executionScope": scope}

        async def prepare(self, value, provider):
            self.lookups += 1
            return value

        async def revalidate(self, value):
            return value.model_copy(deep=True)

        def work_request(self, value):
            return self.binding if work else None

        async def finalize_work(self, value, *, fitted, binding):
            assert value.model_dump_json() == fitted
            assert binding is (self.binding if work else None)
            return value

        def commit(self, value):
            self.commits += 1

    raw, delivery = Provider(), Delivery()
    adapter = SurfaceProvider(raw, delivery, coordinator=coordinator)
    return SimpleNamespace(adapter=adapter, raw=raw, delivery=delivery, caps=caps,
                           coordinator=coordinator, prepared=prepared, measured=measured,
                           sent=sent, compacted=compacted, scope=scope)


@pytest.mark.parametrize("work", [False, True])
async def test_exact_private_projection_count_send_and_canonical_privacy(work):
    c = case(work=work)
    value = request()
    canonical = value.model_dump_json()
    await c.adapter.request_budget(value, request_options={"temperature": 0.2})
    assert value.model_dump_json() == canonical
    assert "PIXEL_SENTINEL" not in canonical
    assert await c.adapter.complete(value, temperature=0.2) == "done"
    assert c.measured[0] is c.sent[0]
    assert c.sent[0].messages[-1].tool_call_id == "call-1"
    assert c.sent[0].messages[-1].metadata["opaque"] == {"signature": "unchanged"}
    assert len(c.prepared) == 1 and c.prepared[0].released
    assert not c.adapter.prepared and not c.adapter._active_media
    assert c.delivery.lookups == c.delivery.commits == 1


@pytest.mark.parametrize("mutation", ["options", "source", "capability", "scope", "expired", "outer-list"])
async def test_fitted_media_mutation_refuses_before_transport(mutation):
    c = case()
    value = request()
    await c.adapter.request_budget(value, request_options={"temperature": 0.2})
    kwargs = {"temperature": 0.2}
    if mutation == "options":
        kwargs["temperature"] = 0.3
    elif mutation == "source":
        value.messages[-1].metadata["opaque"]["signature"] = "changed"
    elif mutation == "capability":
        c.caps[NAMESPACE] = object()
    elif mutation == "scope":
        c.scope["stopRevision"] += 1
    elif mutation == "outer-list":
        value.messages.append(Message(role="user", content="Later input"))
    else:
        c.prepared[0].valid = False
    with pytest.raises(ValueError):
        await c.adapter.complete(value, **kwargs)
    assert not c.sent
    assert c.prepared[0].released


@pytest.mark.parametrize("cancel", [False, True])
async def test_stream_close_and_cancel_release_private_buffers(cancel):
    c = case()
    value = request()
    await c.adapter.request_budget(value)
    iterator = c.adapter.stream(value)
    assert await anext(iterator) == "started"
    if cancel:
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        await iterator.aclose()
    assert c.prepared[0].released
    assert not c.adapter._active_media
    assert "PIXEL_SENTINEL" not in value.model_dump_json()


async def test_expiry_after_stream_event_cannot_prevent_underlying_stream_close():
    c = case()
    closed = []
    async def stream(request, **kwargs):
        try:
            yield "started"
            yield "never transported"
        finally:
            closed.append(True)
    c.raw.stream = stream
    value = request()
    await c.adapter.request_budget(value)
    iterator = c.adapter.stream(value)
    assert await anext(iterator) == "started"
    c.prepared[0].valid = False
    with pytest.raises(ValueError, match="expired"):
        await anext(iterator)
    assert closed == [True] and c.prepared[0].released


async def test_selected_compaction_has_no_interactive_refresh_and_keeps_reservation():
    c = case()
    selected = SelectedProvider(c.raw, {"model": "fixture-model", "max_output_tokens": 9999},
                                lambda _: c.adapter)
    value = request().model_copy(update={"metadata": {"purpose": "context-compaction"},
                                        "max_output_tokens": 321})
    await selected.request_budget(value)
    assert await selected.compact_context(value) == {"summary": "refs-only"}
    assert c.compacted[0] is c.measured[0]
    assert c.compacted[0].max_output_tokens == 321
    assert c.delivery.lookups == c.delivery.commits == 0
    assert c.prepared[0].released
    assert not c.adapter._capture_tickets


async def test_media_work_refusal_refits_exact_request_once_without_retaining_pixels():
    from amplifier_web.request_refit import RequestFitChanged
    c = case(work=True)
    attempts = []
    original = c.delivery.finalize_work
    async def finalize(value, **kwargs):
        if not attempts:
            attempts.append(True)
            raise RequestFitChanged("content_changed")
        return await original(value, **kwargs)
    c.delivery.finalize_work = finalize
    c.delivery.check_execution_scope = lambda scope: None
    c.delivery.current_full = lambda scope: True
    c.delivery.discard_work = lambda binding: None
    value = request()
    await c.adapter.request_budget(value)
    with pytest.raises(RequestFitChanged) as refused:
        await c.adapter.complete(value)
    assert c.prepared[0].released and not c.sent
    assert all("PIXEL_SENTINEL" not in str(row) for row in c.adapter.refused)
    assert await c.adapter.refit_request(value.model_copy(), error=refused.value) is None
    assert await c.adapter.refit_request(value, error=refused.value) is not None
    assert await c.adapter.complete(value) == "done"
    assert len(c.measured) == 2 and len(c.sent) == 1
    assert all(item.released for item in c.prepared)


async def test_budget_error_releases_and_does_not_pollute_canonical_request():
    c = case()
    async def fail(*args, **kwargs):
        raise asyncio.CancelledError()
    c.raw.request_budget = fail
    value = request()
    with pytest.raises(asyncio.CancelledError):
        await c.adapter.request_budget(value)
    assert c.prepared[0].released and not c.adapter.prepared
    assert "PIXEL_SENTINEL" not in value.model_dump_json()


async def test_custom_child_provider_map_installs_actual_preparation_once():
    c = case()
    providers = execution_providers(c.coordinator, {"fixture": c.raw})
    again = execution_providers(c.coordinator, providers)
    assert again["fixture"] is providers["fixture"]
    value = request()
    await providers["fixture"].request_budget(value)
    await providers["fixture"].complete(value)
    assert c.sent[0] is c.measured[0]
    assert c.prepared[0].released


def test_media_adapter_does_not_invent_optional_provider_methods():
    provider = SurfaceProvider(object(), MediaOnlyDelivery())
    for name in ("stream", "request_budget", "compact_context"):
        assert not hasattr(provider, name)