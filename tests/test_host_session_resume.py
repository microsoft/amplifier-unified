"""Native resume/admission tests use real storage and no provider calls."""
import copy
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.host import session as host
from amplifier_web.host.storage import SessionStore


@pytest.fixture
def mounted_host(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    monkeypatch.chdir(workspace)  # Also restore cwd after prepare_manager changes it.
    config = SimpleNamespace(workspace=workspace, home=home, registry_home=home,
        resolve_source=lambda value: value, registrations={}, active_bundle="anchors",
        app_bundles=[], settings={})
    monkeypatch.setattr(host, "load_config", lambda _: config)
    runtime = SimpleNamespace(session_id="native-root", observer=None)
    context = SimpleNamespace(messages=[])
    async def set_messages(messages):
        context.messages = copy.deepcopy(messages)
    async def get_messages():
        return copy.deepcopy(context.messages)
    context.set_messages, context.get_messages = set_messages, get_messages
    provider = SimpleNamespace(get_info=lambda: SimpleNamespace(id="fixture", defaults={"model": "offline"}))
    loop = SimpleNamespace(_select_provider=lambda _: provider, load_failures=[])
    capabilities = {}
    coordinator = SimpleNamespace(get=lambda key: {"context": context, "orchestrator": loop,
        "providers": {"fixture": provider}, "tools": {}}.get(key), get_capability=capabilities.get,
        register_capability=lambda key, value: capabilities.update({key: value}),
        hooks=SimpleNamespace(register=Mock()), register_cleanup=Mock())
    session = SimpleNamespace(coordinator=coordinator, config={}, cleanup=AsyncMock(), execute=AsyncMock())
    prepared = SimpleNamespace(mount_plan={"session": {"orchestrator": {"module": "loop-live"}}},
                               create_session=AsyncMock(return_value=session))
    loaded = SimpleNamespace(to_mount_plan=lambda: copy.deepcopy(prepared.mount_plan),
                             prepare=AsyncMock(return_value=prepared))
    registry = SimpleNamespace(list_registered=lambda: {}, register=Mock(), save=Mock(),
                               load=AsyncMock(return_value=loaded))
    configurator = SimpleNamespace(apply_saved_settings=AsyncMock(), take_snapshot=Mock())
    monkeypatch.setitem(sys.modules, "amplifier_foundation", SimpleNamespace(
        BundleRegistry=lambda **_: registry, SessionConfigurator=lambda *_: configurator))
    monkeypatch.setitem(sys.modules, "amplifier_module_loop_live.runtime", SimpleNamespace(Runtime=lambda: runtime))
    monkeypatch.setitem(sys.modules, "amplifier_module_loop_live.job_store", SimpleNamespace(
        JobStore=lambda _: SimpleNamespace(rows=[], close=Mock())))
    monkeypatch.setitem(sys.modules, "amplifier_core", SimpleNamespace(HookResult=lambda: None))
    monkeypatch.setitem(sys.modules, "amplifier_web.host.children", SimpleNamespace(
        install_children=AsyncMock(), StandaloneHostAdapter=lambda: object()))
    monkeypatch.setattr(host, "compose_configured_bundle", AsyncMock(return_value=loaded))
    monkeypatch.setattr(host, "is_snapshot", lambda _: False)
    monkeypatch.setattr(host, "materialize_bundle_providers", AsyncMock())
    monkeypatch.setattr(host, "apply_provider_environment", lambda _: None)
    store = SessionStore.for_app(home, workspace)
    checkpoint = tmp_path / "common.json"
    writes = []
    def write(messages, **kwargs):
        payload = {"messages": copy.deepcopy(messages), **kwargs}
        writes.append(payload)
        checkpoint.write_text(json.dumps(payload))
    held = SimpleNamespace(write=write)
    async def prepare(snapshot=None, **kwargs):
        return await host.prepare_manager(workspace, runtime=runtime, resume=True,
            shared_handle=held, shared_snapshot=snapshot, **kwargs)
    return SimpleNamespace(prepare=prepare, store=store, held=held, checkpoint=checkpoint,
        writes=writes, runtime=runtime, session=session, prepared=prepared, registry=registry,
        context=context, capabilities=capabilities, home=home,
        path=store.directory(runtime.session_id) / "transcript.jsonl")


def snapshot(messages):
    return {"messages": copy.deepcopy(messages), "metadata": {}, "bundle": "anchors"}


async def test_native_only_resume_keeps_complete_history_and_repairs_receipt_without_replay(mounted_host):
    h = mounted_host
    rows = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "second"},
            {"role": "tool", "tool_call_id": "call-1", "content": json.dumps(
                {"status": "queued", "job_id": "job-1", "call_id": "call-1"})}]
    h.store.save("native-root", rows, {"bundle": "anchors"})
    _, _, report = await h.prepare()
    assert h.context.messages[:3] == rows[:3]
    receipt = json.loads(h.context.messages[3]["content"])
    assert receipt["status"] == "interrupted" and receipt["effects"] == "not_rolled_back"
    assert h.writes[0]["messages"] == h.context.messages
    assert h.store.load("native-root")[0] == h.context.messages
    assert report["resumed"] and str(h.path) in report["config_inputs"]
    assert h.prepared.create_session.call_args.kwargs["session_id"] == "native-root"
    h.session.execute.assert_not_called()


async def test_shared_resume_accepts_cli_projection_and_keeps_shared_receipts(mounted_host):
    h = mounted_host
    rows = [{"role": "system", "content": "shared context"}, {"role": "user", "content": "first"},
            {"role": "tool", "tool_call_id": "call-1", "content": json.dumps(
                {"status": "queued", "job_id": "job-1", "call_id": "call-1"})}]
    h.store.save("native-root", rows, {})
    await h.prepare(snapshot(rows))
    assert h.context.messages == rows
    assert h.writes[0]["messages"] == rows
    assert h.store.load("native-root")[0] == rows[1:]
    h.session.execute.assert_not_called()


async def test_discovered_native_bundle_wins_over_current_app_default(mounted_host):
    h = mounted_host
    rows = [{"role": "user", "content": "native conversation"}]
    h.store.save("native-root", rows, {"bundle": "native-recorded-bundle"})
    await h.prepare(bundle="native-recorded-bundle")
    h.registry.load.assert_awaited_once_with("native-recorded-bundle")
    assert h.writes[0]["bundle"] == "native-recorded-bundle"
    assert h.context.messages == rows


async def test_divergent_cli_history_refuses_before_mount_or_either_checkpoint_write(mounted_host):
    h = mounted_host
    older = [{"role": "user", "content": "first"}]
    newer = older + [{"role": "assistant", "content": "CLI answer"}, {"role": "user", "content": "CLI next"}]
    h.store.save("native-root", newer, {"bundle": "anchors"})
    h.checkpoint.write_text(json.dumps(snapshot(older)))
    before = h.path.read_bytes(), h.checkpoint.read_bytes()
    with pytest.raises(host.NativeTranscriptConflict, match="Both histories were preserved"):
        await h.prepare(snapshot(older))
    assert before == (h.path.read_bytes(), h.checkpoint.read_bytes())
    assert h.writes == []
    h.registry.load.assert_not_called()
    h.prepared.create_session.assert_not_called()


@pytest.mark.parametrize("mutation", ["append", "delete", "replace", "symlink"])
async def test_out_of_band_native_change_blocks_later_checkpoint(mounted_host, mutation):
    h = mounted_host
    rows = [{"role": "user", "content": "first"}]
    h.store.save("native-root", rows, {})
    await h.prepare(snapshot(rows))
    original_common = h.checkpoint.read_bytes()
    original_metadata = (h.path.parent / "metadata.json").read_bytes()
    if mutation == "append":
        with h.path.open("a") as stream:
            stream.write('{"role":"assistant","content":"from CLI"}\n')
    elif mutation == "replace":
        replacement = h.path.with_suffix(".replacement")
        replacement.write_bytes(h.path.read_bytes())
        replacement.replace(h.path)
    elif mutation == "delete":
        h.path.unlink()
    else:
        target = h.path.with_suffix(".outside")
        target.write_bytes(h.path.read_bytes())
        h.path.unlink()
        h.path.symlink_to(target)
    native_after = h.path.read_bytes() if h.path.exists() else None
    h.context.messages.append({"role": "assistant", "content": "stale in-memory answer"})
    with pytest.raises(host.NativeTranscriptConflict):
        await h.capabilities["live.checkpoint"]("completed")
    assert (h.path.read_bytes() if h.path.exists() else None) == native_after
    assert h.checkpoint.read_bytes() == original_common
    assert (h.path.parent / "metadata.json").read_bytes() == original_metadata
    assert len(h.writes) == 1


async def test_missing_native_projection_uses_common_checkpoint_without_private_migration(mounted_host):
    h = mounted_host
    private = SessionStore(h.home / "sessions")
    private.save("native-root", [{"role": "user", "content": "stale private copy"}], {})
    old_private = (private.directory("native-root") / "checkpoint.json").read_bytes()
    rows = [{"role": "user", "content": "authoritative common checkpoint"}]
    await h.prepare(snapshot(rows))
    assert h.context.messages == rows and h.store.load("native-root")[0] == rows
    assert (private.directory("native-root") / "checkpoint.json").read_bytes() == old_private


async def test_corrupt_native_transcript_never_becomes_empty_history(mounted_host):
    h = mounted_host
    h.path.parent.mkdir(parents=True)
    h.path.write_text('{"role":"user"\n')
    original = h.path.read_bytes()
    with pytest.raises(ValueError):
        await h.prepare(snapshot([]))
    assert h.path.read_bytes() == original
    assert not h.writes
    h.registry.load.assert_not_called()
