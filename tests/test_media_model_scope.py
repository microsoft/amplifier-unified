"""Proactive selected-model scope fence; deterministic source fixtures only."""
import copy

import pytest

pytest.importorskip("amplifier_foundation.session.media_hook_bridge")
from amplifier_core.message_models import ChatRequest, Message
from amplifier_web.host.media_assembly import select_root, supported_plan
from amplifier_web.host.session import SelectedProvider
from test_narrow_tool_images import picture, read, root

MODEL = "gpt-6.1-sol"


def test_scope_uses_the_exact_existing_qualified_model_not_a_new_catalog_choice():
    from amplifier_web.host.media_assembly import MEDIA_MODEL
    assert MEDIA_MODEL == MODEL == "gpt-6.1-sol"


def plan():
    return {"session": {"orchestrator": "loop-live", "context": "context-simple"},
            "tools": [{"module": "tool-filesystem"}],
            "providers": [{"module": "provider-openai", "config": {"default_model": MODEL}}]}


@pytest.mark.parametrize("change", ["missing-config", "codex-auth", "plan-auth",
    "implicit-model", "latest", "other-model", "other-provider"])
def test_unqualified_plan_is_not_selected(monkeypatch, change):
    value = plan()
    spec = value["providers"][0]
    if change == "missing-config":
        spec.pop("config")
    elif change == "codex-auth":
        spec["config"]["auth_mode"] = "chatgpt_codex"
    elif change == "plan-auth":
        spec["config"]["auth_mode"] = "chatgpt_plan"
    elif change == "implicit-model":
        spec["config"].pop("default_model")
    elif change == "other-provider":
        spec["module"] = "provider-openai-chatgpt"
    else:
        spec["config"]["default_model"] = {"latest": "latest", "other-model": "gpt-5.3-codex"}[change]
    assert not supported_plan(value)
    # The source manifest is not even consulted for an unsupported model/auth.
    with monkeypatch.context() as patch:
        from pathlib import Path
        patch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("Unqualified plan reached source selection"))
        assert select_root(value) is None
    assert supported_plan(plan())


async def selected_fixture(tmp_path, *, model=MODEL):
    path, original = picture(tmp_path)
    current = await root(tmp_path)
    current.provider.default_model = MODEL
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


async def test_valid_empty_indexed_support_is_unmarked_warning_before_asset_access(tmp_path, monkeypatch):
    current, selected, request, _, seen = await selected_fixture(tmp_path)
    def empty(**kwargs):
        return {"v": 2, "positions": [], "mime_types": ["image/png", "image/jpeg"]}
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


@pytest.mark.parametrize("fault", ["missing", "malformed", "async", "request-mutation",
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
        None if fault == "missing" else asynchronous if fault == "async" else broken)
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


@pytest.mark.parametrize("change", ["implicit-selection", "latest-selection", "other-selection",
    "default-mismatch", "default-latest", "wrong-provider", "missing-provider", "option-override",
    "nested-option-override", "provider-override", "provider-plan-mismatch"])
async def test_unsupported_initial_scope_warns_without_hydrating_and_text_recovers(tmp_path, monkeypatch, change):
    selected_model = {"implicit-selection": None, "latest-selection": "latest",
                      "other-selection": "gpt-5.3-codex"}.get(change, MODEL)
    current, selected, request, _, seen = await selected_fixture(tmp_path, model=selected_model)
    options = {}
    if change == "default-mismatch":
        current.provider.default_model = "gpt-5.3-codex"
        assert selected.default_model == MODEL  # The selected wrapper cannot bless the actual default.
    elif change == "default-latest":
        current.provider.default_model = "latest"
    elif change == "wrong-provider":
        current.provider.name = "openai-chatgpt"
    elif change == "missing-provider":
        current.provider.name = None
    elif change == "option-override":
        # Check options reaching the private boundary, not SelectedProvider's
        # preexisting preference-over-stale-keyword normalization.
        selected, request.model = current.wrapper, MODEL
        options = {"model": "gpt-5.3-codex"}
    elif change == "nested-option-override":
        options = {"extra_body": {"model": "gpt-5.3-codex"}}
    elif change == "provider-override":
        current.provider.extra_request_params = {"model": "gpt-5.3-codex"}
    elif change == "provider-plan-mismatch":
        current.coordinator.get_capability("web.media.construction")[2].plan["providers"][0]["config"]["auth_mode"] = "chatgpt_plan"
    try:
        monkeypatch.setattr(current.coordinator.get_capability("web.media.store"), "resolve_ref",
                            lambda *a, **k: pytest.fail("Unsupported scope opened stored pixels"))
        before = request.model_dump_json()
        await selected.request_budget(request, request_options=options)
        private = seen[-1][1]
        assert not images(private)
        assert "Image unavailable" in private.model_dump_json()
        assert request.model_dump_json() == before
        # Dispatch exactly the measured unsupported view; no retry of a provider operation.
        await selected.complete(request, **options)
        assert not images(seen[-1][1])
        plain = ChatRequest(messages=[Message(role="user", content="Continue in text")])
        await selected.request_budget(plain)
        assert await selected.complete(plain) == "offline completion"
        assert not current.wrapper._media
        assert not current.coordinator.get_capability("web.media.request_only").requests
    finally:
        await current.session.cleanup()


async def test_explicit_matching_selected_request_count_send_same_originals(tmp_path):
    import base64
    current, selected, request, original, seen = await selected_fixture(tmp_path)
    try:
        before = request.model_dump_json()
        await selected.request_budget(request)
        private = seen[-1][1]
        assert private.model == MODEL and seen[-1][2]["model"] == MODEL
        assert base64.b64decode(images(private)[0].source["data"]) == original
        await selected.complete(request)
        assert seen[-1][1] is private and seen[-1][2]["model"] == MODEL
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
