"""Proactive selected-model scope fence; deterministic source fixtures only."""
import copy

import pytest

pytest.importorskip("amplifier_foundation.session.media_host")
from amplifier_core.message_models import ChatRequest, Message
from amplifier_web.host.session import SelectedProvider
from fixtures.narrow_tool_image_fixture import RecordingProvider
from test_narrow_tool_images import picture, read, root

MODEL = "gpt-6.1-sol"


async def selected_fixture(tmp_path, *, model=MODEL, provider_name="openai"):
    path, original = picture(tmp_path)
    provider = RecordingProvider()
    provider.name = provider_name
    provider.default_model = model
    current = await root(tmp_path, provider=provider)
    _, _, _, rows = await read(current, path)
    current.provider.capability = None  # Raw endpoint below never acts as media authority.
    seen = []
    async def budget(request, *, request_options=None, **kwargs):
        seen.append(("count", request, copy.deepcopy(request_options)))
        return {"input_tokens": 7}
    async def complete(request, **kwargs):
        seen.append(("send", request, copy.deepcopy(kwargs)))
        return "offline completion"
    current.provider.request_budget = budget
    current.provider.complete = complete
    selected = SelectedProvider(current.provider, {"model": model} if model is not None else {},
                                lambda provider: current.wrapper)
    request = ChatRequest(messages=[Message(**row) for row in rows])
    return current, selected, request, original, seen


def images(request):
    return [block for message in request.messages if isinstance(message.content, list)
            for block in message.content if block.type == "image"]


@pytest.mark.parametrize("support", ["absent", "empty"])
async def test_unsupported_request_is_unmarked_warning_before_asset_access(tmp_path, monkeypatch, support):
    current, selected, request, _, seen = await selected_fixture(tmp_path)
    def empty(**kwargs):
        return {"v": 2, "positions": [], "mime_types": ["image/png", "image/jpeg"]}
    if support == "absent":
        # Hide the class-level method so getattr(provider, name, default) sees
        # a legacy provider, not a malformed declaration of indexed support.
        monkeypatch.delattr(type(current.provider), "get_media_request_support_for_request")
    else:
        current.provider.get_media_request_support_for_request = empty
    try:
        monkeypatch.setattr(current.coordinator.get_capability("web.media.store"), "resolve_ref",
                            lambda *a, **k: pytest.fail("Empty support opened stored pixels"))
        before = request.model_dump_json()
        await selected.request_budget(request)
        private = seen[-1][1]
        assert not images(private)
        assert all("experimental.media.v1" not in (m.metadata or {}) for m in private.messages)
        assert "Image unavailable" in private.model_dump_json()
        await selected.complete(request)
        assert seen[-1][1] is private
        assert request.model_dump_json() == before
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("fault", ["noncallable", "malformed", "async", "request-mutation",
    "options-mutation", "getter-mutation"])
async def test_invalid_indexed_support_does_not_fallback_or_hydrate(tmp_path, monkeypatch, fault):
    current, selected, request, _, seen = await selected_fixture(tmp_path)
    def broken(*, request, request_options, **kwargs):
        if fault == "request-mutation":
            request.messages.clear()
        if fault == "options-mutation":
            request_options["model"] = "latest"
        if fault == "getter-mutation":
            current.provider.get_media_request_support_for_request = lambda **kwargs: {
                "v": 2, "positions": [], "mime_types": []}
        return {"v": 2, "positions": [], "mime_types": []} if fault != "malformed" else {"v": 2}
    async def asynchronous(**kwargs):
        return {"v": 2, "positions": [], "mime_types": []}
    current.provider.get_media_request_support_for_request = (
        False if fault == "noncallable" else asynchronous if fault == "async" else broken)
    try:
        monkeypatch.setattr(current.coordinator.get_capability("web.media.store"), "resolve_ref",
                            lambda *a, **k: pytest.fail("Invalid support opened stored pixels"))
        before = request.model_dump_json()
        with pytest.raises(ValueError):
            await selected.request_budget(request)
        assert not seen and request.model_dump_json() == before
        assert not current.wrapper._media
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("change", ["empty", "equal-replacement"])
async def test_positive_fit_cannot_downgrade_or_replace_indexed_getter(tmp_path, change):
    current, selected, request, _, seen = await selected_fixture(tmp_path)
    try:
        await selected.request_budget(request)
        private = seen[-1][1]
        assert images(private)
        previous = current.provider.get_media_request_support_for_request
        def replacement(**kwargs):
            value = previous(**kwargs)
            if change == "empty":
                value["positions"] = []
            return value
        current.provider.get_media_request_support_for_request = replacement
        with pytest.raises(ValueError):
            await selected.complete(request)
        assert not any(phase == "send" for phase, *_ in seen)
        assert not current.wrapper._media and private.messages == []
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize(("provider_name", "model"), [
    ("openai", MODEL),
    ("fixture-vision", "fixture-vision-model"),
])
async def test_selected_request_count_send_same_originals(tmp_path, provider_name, model):
    import base64
    current, selected, request, original, seen = await selected_fixture(
        tmp_path, model=model, provider_name=provider_name)
    try:
        before = request.model_dump_json()
        await selected.request_budget(request)
        private = seen[-1][1]
        assert private.model == model and seen[-1][2]["model"] == model
        assert base64.b64decode(images(private)[0].source["data"]) == original
        await selected.complete(request)
        assert seen[-1][1] is private and seen[-1][2]["model"] == model
        assert private.messages == [] and not current.wrapper._media
        assert request.model_dump_json() == before
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("change", ["provider", "default", "selection", "options", "nested-options", "provider-options"])
async def test_fitted_scope_mutation_refuses_before_send_and_cleans_pixels(tmp_path, change):
    current, selected, request, _, seen = await selected_fixture(tmp_path)
    if change == "options":
        selected, request.model = current.wrapper, MODEL
    try:
        await selected.request_budget(request, context_estimate=100)
        private = seen[-1][1]
        assert images(private)
        options = {}
        if change == "provider":
            current.provider.name = "openai-chatgpt"
        elif change == "default":
            current.provider.default_model = "latest"
        elif change == "selection":
            selected.selection["model"] = "gpt-5.3-codex"
        elif change == "options":
            options["model"] = "gpt-5.3-codex"
        elif change == "nested-options":
            options["extra_body"] = {"model": "gpt-5.3-codex"}
        else:
            current.provider.extra_request_params = {"model": "gpt-5.3-codex"}
        with pytest.raises(ValueError):
            await selected.complete(request, **options)
        assert not any(row[0] == "send" for row in seen)
        assert private.messages == [] and not current.wrapper._media
        assert not current.coordinator.get_capability("web.media.request_only").requests
        plain = ChatRequest(messages=[Message(role="user", content="Continue in text")])
        await selected.request_budget(plain)
        assert await selected.complete(plain) == "offline completion"
    finally:
        await current.session.cleanup()
