"""A same-connection model switch preserves portable history on an ordinary reply."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core import ChatRequest, Message, ProviderInfo
from amplifier_web.context_continuity import digest
from amplifier_web.host.session import SelectedProvider
from amplifier_web.surface_delivery import SurfaceProvider
from test_context_continuity import bind_continuity_recovery_guards, mount


CONFIG = {"durable_checkpoints": True, "archive_recovery": True, "foreground_continuation": True,
          "compaction_schedule": "after_turn", "max_tokens": 6000, "summarize_trigger": .4}
ROWS = [row for i in range(12) for row in (
    {"role": "user", "content": f"Research note {i}"},
    {"role": "assistant", "content": "Archived research, 月. " * 500})] + [
    {"role": "user", "content": "What was decided?"}]
NOTE = "Résumé: orbit 月."


async def session(path, model, *, selected=True, connection="physical-a", authorize=True):
    coord, raw, host, save, _ = await mount(path, ROWS, context_config=CONFIG)
    raw.name = "fixture"
    raw.get_info = lambda: ProviderInfo(id="fixture", display_name="Fixture", defaults={"model": "a"})
    raw.complete.return_value = SimpleNamespace(content=[SimpleNamespace(type="text", text=NOTE)])
    route = SelectedProvider(raw, {"instance": "fixture", "model": model}) if selected else raw
    coord.loop.root_provider = route
    coord.register_capability("web.provider_catalog", {"connectionKeys": {"fixture": connection}})
    choice = {"instance": "fixture", "model": model}
    bind_continuity_recovery_guards(coord, host, {"selection": choice})
    coord.register_capability("web.context_transition.foreground", lambda: {
        "generationId": "g", "inputId": "fresh", "selectionRevision": "choice",
        "selection": choice, "connection": connection} if authorize else None)
    return SimpleNamespace(coord=coord, host=host, route=route, raw=raw, save=save,
                           prepare=lambda: coord.context.get_messages_for_request(provider=route))


@pytest.fixture
async def saved(tmp_path):
    # The generating provider was the configured default, with no prior UI selection.
    source = await session(tmp_path, "a", selected=False)
    await source.prepare()
    await source.save()
    record = json.loads(source.host.path.read_bytes())
    assert record["summary"]["text"] == NOTE and record["unifiedPortableOrigin"]
    assert source.raw.complete.await_count > 0
    return source.host.path


@pytest.mark.parametrize("outcome", ["success", "truncated", "error", "stop", "rotated", "source_changed",
                                    "projection_removed", "writer_failed"])
async def test_only_a_confirmed_actual_target_request_can_replace_saved_note(saved, outcome, monkeypatch):
    original = saved.read_bytes()
    target = await session(saved.parent, "b")
    rows = await target.prepare()
    note = next(row["content"] for row in rows if isinstance(row.get("content"), str) and NOTE in row["content"])
    assert target.raw.complete.await_count == 0 and saved.read_bytes() == original
    response = SimpleNamespace(finish_reason="stop", metadata={"provider_response_completed": outcome != "truncated"})
    changed_file = b'{"modified":"separately"}'
    async def reply(outgoing, **kwargs):
        assert outgoing.model == "b"
        assert any(row.content == note for row in outgoing.messages) == (outcome != "projection_removed")
        if outcome == "error":
            raise RuntimeError("provider failed")
        if outcome == "stop":
            target.coord.get_capability("live.runtime").stop_requested = True
        if outcome == "rotated":
            target.coord.get_capability("web.provider_catalog")["connectionKeys"]["fixture"] = "physical-b"
        if outcome == "source_changed":
            saved.write_bytes(changed_file)
        if outcome == "writer_failed":
            def fail(*args):
                raise OSError("disk full")
            monkeypatch.setattr("amplifier_web.context_continuity.write_private", fail)
        return response
    target.raw.complete.side_effect = reply

    async def project(request, provider):
        if outcome == "projection_removed":
            return request.model_copy(update={"messages": [row for row in request.messages if row.content != note]})
        return request
    surface = SurfaceProvider(target.raw, SimpleNamespace(prepare=project, commit=lambda request: None))
    target.route.execution_adapter = lambda raw: surface
    request = ChatRequest(messages=[Message(**row) for row in rows])
    if outcome == "error":
        with pytest.raises(RuntimeError, match="provider failed"):
            await target.route.complete(request)
    else:
        assert await target.route.complete(request) is response
    assert target.raw.complete.await_count == 1
    if outcome == "success":
        accepted = json.loads(saved.read_bytes())
        assert accepted["identity"]["model"] == "b" and accepted["summary"]["text"] == NOTE
        assert accepted["unifiedPortableOrigin"] != json.loads(original)["unifiedPortableOrigin"]
        for ordinary in (target, await session(saved.parent, "b")):
            calls = ordinary.raw.complete.await_count
            assert NOTE in str(await ordinary.prepare())
            assert ordinary.raw.complete.await_count == calls
    else:
        assert saved.read_bytes() == (changed_file if outcome == "source_changed" else original)
    assert await target.coord.context.get_messages() == ROWS


@pytest.mark.parametrize("reason", ["legacy", "changed_account", "unbound_input", "corrupt", "opaque"])
async def test_old_or_unbound_sources_keep_existing_recovery(saved, reason):
    rec = json.loads(saved.read_bytes())
    if reason == "legacy":
        del rec["unifiedPortableOrigin"]
    if reason == "opaque":
        rec["summary"] = {"kind": "native", "throughMessage": rec["summary"]["throughMessage"],
                          "message": {"role": "user", "content": "opaque control"}}
    rec["sha256"] = digest({key: value for key, value in rec.items() if key != "sha256"})
    if reason == "corrupt":
        rec["summary"]["text"] += "not verified"
    saved.write_text(json.dumps(rec), encoding="utf-8")
    original = saved.read_bytes()
    target = await session(saved.parent, "b", connection="physical-b" if reason == "changed_account" else "physical-a",
                           authorize=reason != "unbound_input")
    await target.prepare()
    assert not target.route.portable_handoff and target.raw.complete.await_count > 0
    await target.save()
    assert saved.read_bytes() == original
    if reason == "legacy":
        cold = await session(saved.parent, "b")
        await cold.prepare()
        assert cold.raw.complete.await_count > 0 and saved.read_bytes() == original


def test_sparse_or_partial_exports_are_never_given_an_origin():
    from amplifier_web.portable_handoff import PortableHandoff
    portable = PortableHandoff(SimpleNamespace(_accepted_record=None))
    for record in ({}, {"summary": {"text": NOTE}}, {"identity": {}, "summary": {"text": NOTE}},
                   {"progress": {"completed": [NOTE]}, "summary": {"text": NOTE}}):
        expected = copy.deepcopy(record)
        portable.stamp(record)
        assert record == expected
