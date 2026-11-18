"""Ordinary Work source proof; provider bottom is deterministic, never remote."""
import copy
import json
import socket

import pytest

pytest.importorskip("amplifier_foundation.session.media_hook_bridge")
from amplifier_core import HookResult
from amplifier_web.host import media
from amplifier_web.host.media_assembly import ordinary_work_plan, root_participants, supported_plan, WORK_PARTICIPANTS
from fixtures.ordinary_work_image_fixture import compose_work, work_root
from test_narrow_tool_images import picture
from test_tool_image_consumer_join import ScriptedReadProvider


@pytest.fixture(autouse=True)
def no_remote_calls(monkeypatch):
    def refuse(*a, **k):
        pytest.fail("Ordinary Work source fixture attempted a network operation")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


async def test_ordinary_work_actual_composition_and_finite_shape(tmp_path, monkeypatch):
    _, plan, _ = await compose_work(tmp_path, monkeypatch)
    assert ordinary_work_plan(plan) and supported_plan(plan)
    assert root_participants(plan) == WORK_PARTICIPANTS
    assert len(WORK_PARTICIPANTS) == len(set(WORK_PARTICIPANTS)) == 24
    for section in ("tools", "hooks"):
        for mutation in ("extra", "duplicate", "instance"):
            changed = copy.deepcopy(plan)
            if mutation == "extra":
                changed[section].append({"module": "unreviewed"})
            elif mutation == "duplicate":
                changed[section].append(copy.deepcopy(changed[section][0]))
            else:
                changed[section][0]["id"] = "another-instance"
            assert not supported_plan(changed)
    changed = copy.deepcopy(plan)
    changed["agents"]["extra:agent"] = {}
    assert not supported_plan(changed)
    for name, config in (("tool-image", {"builtin": True}), ("tool-image", {"backend": "legacy"}),
                         ("tool-computer-use", {"target": "ssh://synthetic.invalid"}),
                         ("tool-ui-use", {"browser": {"enabled": True}})):
        changed = copy.deepcopy(plan)
        next(row for row in changed["tools"] if row["module"] == name)["config"].update(config)
        assert not supported_plan(changed)


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
@pytest.mark.parametrize("redact", [False, True])
async def test_ordinary_work_first_image_post_ack_deleted_source_reload(tmp_path, monkeypatch, format, redact):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, original = picture(workspace, format)
    posts = []
    async def post(event, data):
        posts.append(data["tool_call_id"])
        return (HookResult(action="modify", data={"result": None})
                if redact and data["tool_call_id"] == "call-first-image" else HookResult())
    provider = ScriptedReadProvider(path)
    if redact:
        text = workspace / "plaintext"
        text.write_text("Plaintext after refused image")
        provider.paths.append(text)
    session, store, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, provider, post=post)
    try:
        assert await session.execute("Read the authorized local image") == "offline completion"
        bridge, graph, _ = session.coordinator.get_capability("web.media.construction")
        assert bridge.validate_receipt(bridge.graph_receipt), graph.participants_evidence()["state"]
        evidence = graph.participants_evidence()
        print("ORDINARY_WORK_SOURCE_FACTS " + json.dumps({
            "model": provider.default_model, "auth": provider.auth_mode,
            "configured_mounts": len(evidence["loads"]),
            "participants": list(evidence["participants"]),
            "ready": [{"participant": row["participant"], "completed": row["completed"]}
                      for row in evidence["ready"]],
            "posts": [{"owner": row["participant"], "name": row["name"], "priority": row["priority"]}
                      for row in evidence["registrations"]],
            "mode_registrations": len(evidence["mode_registrations"]),
            "adapters": len(evidence["adapters"]), "receipt_valid": True,
        }, sort_keys=True))
        assert host.sealed and posts[0] == "call-first-image"
        assert checks == [("read_file", {"file_path": str(p)}) for p in provider.paths]
        assert provider.observed[-1] == ([] if redact else [("tool", "call-first-image", original)])
        rows = await checkpoint()
        before = copy.deepcopy(rows)
        await session.coordinator.get("context").set_messages(rows)
        assert await session.coordinator.get("context").get_messages() == before
        refs = [row for row in rows if media.NAMESPACE in (row.get("metadata") or {})]
        assert bool(refs) is (not redact)
        for row in refs:
            assert row["tool_call_id"] == "call-first-image"
            assert all(ref["provenance"]["expires_at"] is None
                       for ref in row["metadata"][media.NAMESPACE]["refs"])
        if redact:
            assert "Plaintext after refused image" in rows[-2]["content"]
        assert '"type": "image"' not in json.dumps(rows)
        assert not session.coordinator.get_capability("web.media.request_only").requests
        if not redact:
            adapter = session.coordinator.get_capability("web.provider_transform")(provider)
            assert not adapter._media, "Completed Work turn retained counted media candidates"
            path.unlink()
            await session.execute("Keep using the retained image; do not read it again")
            assert provider.observed[-1] == [("tool", "call-first-image", original)]
            assert checks == [("read_file", {"file_path": str(path)})]
    finally:
        await session.cleanup()
    if redact:
        return
    assert not path.exists()
    reload_provider = ScriptedReadProvider(path, read=False)
    from amplifier_web.host.storage import SessionStore
    reopened = SessionStore(store.base_dir)
    resumed, _, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, reload_provider, store=reopened)
    try:
        loaded, _ = reopened.load("ordinary-work-media")
        await resumed.coordinator.get("context").set_messages(loaded)
        await checkpoint()
        await resumed.execute("Use the retained image without rereading")
        assert host.sealed and checks == []
        assert reload_provider.observed == [[("tool", "call-first-image", original)]]
    finally:
        await resumed.cleanup()


@pytest.mark.parametrize("denial", ["work", "read-policy"])
async def test_ordinary_work_read_policy_and_work_refusal_keep_text_usable(tmp_path, monkeypatch, denial):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, _ = picture(workspace)
    text = workspace / "plaintext"
    text.write_text("Plaintext after refused image")
    provider = ScriptedReadProvider(path)
    provider.paths.append(text)
    session, _, checkpoint, _, host = await work_root(tmp_path, monkeypatch, provider, deny_work=denial == "work")
    try:
        if denial == "read-policy":
            from amplifier_web.read_policy import mounted_read_file
            mounted_read_file(session.coordinator).denied_read_paths = [str(path)]
        assert await session.execute("Read image then plaintext") == "offline completion"
        rows = await checkpoint()
        assert host.sealed and all(value == [] for value in provider.observed)
        assert "Plaintext after refused image" in rows[-2]["content"]
        assert not any(media.NAMESPACE in (row.get("metadata") or {}) for row in rows)
    finally:
        await session.cleanup()


async def test_completed_coordinator_retires_abandoned_fit_not_foreign_completion(tmp_path, monkeypatch):
    from amplifier_core.message_models import ChatRequest, Message
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, _ = picture(workspace)
    provider = ScriptedReadProvider(path)
    session, _, checkpoint, _, _ = await work_root(tmp_path, monkeypatch, provider)
    try:
        await session.execute("Read the local image")
        coordinator = session.coordinator
        wrapper = coordinator.get_capability("web.provider_transform")(provider)
        request = ChatRequest(messages=[Message(**row) for row in await checkpoint()], model="gpt-6.1-sol")
        await wrapper.request_budget(request, request_options={"model": "gpt-6.1-sol"})
        private = provider.counted
        assert wrapper._media and any(b.type == "image" for m in private.messages
                                    if isinstance(m.content, list) for b in m.content)
        await coordinator.hooks.emit("orchestrator:complete", {"session_id": "another-coordinator"})
        assert wrapper._media and private.messages
        await coordinator.hooks.emit("orchestrator:complete", {"session_id": session.session_id})
        assert not wrapper._media and private.messages == []
    finally:
        await session.cleanup()


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
async def test_real_prepared_host_child_tool_install_keeps_configurator_construction(tmp_path, monkeypatch, format):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, original = picture(workspace, format)
    provider = ScriptedReadProvider(path)
    session, _, checkpoint, checks, host = await work_root(
        tmp_path, monkeypatch, provider, prepared_host=True)
    try:
        coordinator = session.coordinator
        assert callable(coordinator.get("context").factory)
        assert await session.execute("Read the authorized local image") == "offline completion"
        assert host.sealed
        assert provider.observed[-1] == [("tool", "call-first-image", original)]
        assert checks == [("read_file", {"file_path": str(path)})]
        rows = await checkpoint()
        assert any(media.NAMESPACE in (row.get("metadata") or {}) for row in rows)
        assert not coordinator.get_capability("live.children").rows
        assert not coordinator.get_capability("live.children").admissions
    finally:
        await session.cleanup()


async def test_raw_delegate_replacement_still_refuses_configuration(tmp_path, monkeypatch):
    from amplifier_web.host.children import PersistentDelegate
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ScriptedReadProvider(workspace / "unused", read=False)
    session, _, _, _, _ = await work_root(
        tmp_path, monkeypatch, provider, prepared_host=True, configuration_only=True)
    try:
        coordinator = session.coordinator
        previous = coordinator.get("tools", "delegate")
        await coordinator.mount("tools", PersistentDelegate(
            previous, coordinator.get_capability("live.children"), coordinator), name="delegate")
        with pytest.raises(ValueError, match="complete idle open construction"):
            with media.hook_configuration(coordinator):
                pytest.fail("An unrecorded delegate replacement acquired a construction phase")
        assert provider.calls == 0
    finally:
        await session.cleanup()


async def test_real_prepared_root_configuration_phase_preserves_complete_idle_graph(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ScriptedReadProvider(workspace / "unused", read=False)
    session, _, _, _, _ = await work_root(
        tmp_path, monkeypatch, provider, prepared_host=True, configuration_only=True)
    try:
        coordinator = session.coordinator
        _, graph, _ = coordinator.get_capability("web.media.construction")
        evidence = graph.participants_evidence()
        assert evidence["state"] == "open"
        assert len(evidence["loads"]) == 23 and all(row["mounted"] for row in evidence["loads"])
        assert all(row["completed"] for row in evidence["ready"])
        assert any(row["slot"] == ("tools", "delegate")
                   and row["participant"] == "unified-host" for row in evidence["adapters"])
        assert callable(coordinator.get("context").factory)
        assert provider.calls == 0 and not coordinator.get_capability(media.NAMESPACE).enabled
        assert not coordinator.get_capability("live.children").sessions.keys() - {session.session_id}
    finally:
        await session.cleanup()


@pytest.mark.parametrize("configured", [None, "explicit-relative", "/synthetic/explicit"])
def test_web_working_directory_default_is_materialized_without_changing_explicit_choice(tmp_path, configured):
    from types import SimpleNamespace
    from amplifier_web.host.session import _apply_host_policy
    values = {} if configured is None else {"working_dir": configured}
    bundle = SimpleNamespace(tools=[{"module": "tool-web", "config": values}], agents={})
    config = SimpleNamespace(settings={}, workspace=tmp_path, access_mode="ask")
    _apply_host_policy(bundle, config)
    assert bundle.tools[0]["config"]["working_dir"] == (str(tmp_path) if configured is None else configured)
