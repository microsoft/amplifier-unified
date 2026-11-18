"""Actual loop/Unified source join, not installed Worker or provider-wire proof."""
import asyncio
import base64
from contextlib import contextmanager
import copy
import hashlib
from pathlib import Path
import sys

import pytest
from amplifier_core import AmplifierSession, HookResult, ProviderInfo
from amplifier_core.message_models import ChatResponse, ToolCall, ToolCallBlock, TextBlock

from amplifier_web.host import media
from amplifier_web.host.storage import SessionStore
from amplifier_web.app_guidance import install_app_access
from amplifier_web.read_policy import mounted_read_file
from amplifier_module_loop_live.host import HostAdapter
from fixtures.narrow_tool_image_fixture import ActualConsumerLoader
from test_narrow_tool_images import picture


class ScriptedReadProvider:
    """Deterministic bottom: returns one actual read_file call, then text."""
    name = "openai"
    default_model = "gpt-6.1-sol"
    config = {}

    def __init__(self, path, *, read=True):
        self.path, self.read = path, read
        self.paths = [path] if read else []
        self.calls = 0
        self.counted = None
        self.capability = None
        self.observed = []
        self.checkpoint_seen = None

    def get_info(self):
        return ProviderInfo(id=self.name, display_name=self.name, defaults={"model": self.default_model})

    def get_media_request_support_for_request(self, *, request, **kwargs):
        return {"v": 2, "positions": [
            {"message_index": i, "role": m.role} for i, m in enumerate(request.messages)
            if m.role in {"tool", "user"}], "mime_types": ["image/png", "image/jpeg"]}

    async def request_budget(self, request, *, request_options=None, **kwargs):
        self.counted = request
        if any(media.NAMESPACE in (m.metadata or {}) for m in request.messages):
            self.capability.validate(request, purpose="count", request_options=request_options or {})
        return {"estimated_input_tokens": 10, "input_limit_tokens": 10000, "context_token_budget": 9000}

    async def complete(self, request, **kwargs):
        if any(media.NAMESPACE in (m.metadata or {}) for m in request.messages):
            assert request is self.counted, "Image count/send must share one private request"
            self.capability.validate(request, purpose="transport", request_options=kwargs)
            assert self.checkpoint_seen()
        images = [(m.role, m.tool_call_id, base64.b64decode(b.source["data"]))
                  for m in request.messages if isinstance(m.content, list)
                  for b in m.content if b.type == "image"]
        self.observed.append(images)
        self.calls += 1
        if self.calls <= len(self.paths):
            return ChatResponse(content=[ToolCallBlock(
                id="call-first-image" if self.calls == 1 else "call-followup-text",
                name="read_file", input={"file_path": str(self.paths[self.calls - 1])})])
        return ChatResponse(content=[TextBlock(text="offline completion")])

    def parse_tool_calls(self, response):
        return [ToolCall(id=b.id, name=b.name, arguments=b.input)
                for b in response.content if b.type == "tool_call"]


class FiniteSourceHost(HostAdapter):
    """Public late-registration protocol used by Unified's Worker adapter.

    This is a source integration fixture, not a substitute Worker harness.
    It invokes production app-access/Work wrappers and media registration gates.
    """
    def __init__(self, native):
        self.native = native
        self.sealed = False

    @contextmanager
    def execution_registration(self, coordinator, hooks):
        assert coordinator is self.native
        with media.hook_configuration(coordinator):
            view = media.registration_coordinator(coordinator)
            yield view, view.hooks
        self.sealed = media.seal_hook_graph(coordinator)

    async def prepare_execution(self, loop, coordinator, providers):
        from amplifier_web.host.session import SelectedProvider
        transform = self.native.get_capability("web.provider_transform")
        return self.native.get_capability("live.runtime"), {
                      name: SelectedProvider(provider, {"model": provider.default_model}, transform)
                      for name, provider in providers.items()}, None


async def actual_root(tmp_path, provider, *, post=None, store=None, deny_work=False):
    plan = {"session": {"orchestrator": {"module": "loop-live", "config": {
                "configured_bundle": True, "max_iterations": 4}},
            "context": {"module": "context-simple"}},
        "providers": [{"module": "provider-openai", "config": {
            "default_model": "gpt-6.1-sol"}}],
        "tools": [{"module": "tool-filesystem", "config": {
            "working_dir": str(tmp_path), "allowed_read_paths": [str(tmp_path)]}}]}
    session = AmplifierSession(plan, session_id="root-consumer-join")
    coordinator = session.coordinator
    coordinator.loader = ActualConsumerLoader(provider)
    store = store or SessionStore(tmp_path / "sessions")
    media.bind_media(session, store)
    try:
        await session.initialize()
        loop = coordinator.get("orchestrator")
        if not hasattr(loop, "_media_consumer"):
            pytest.fail("Selected actual loop must initialize its own media consumer")
        cap = coordinator.get_capability(media.NAMESPACE)
        provider.capability = cap
        report = cap.report("test", event="readiness", outcome="inspect")
        assert report["consumers"]["loop-live"]["ready"] is False
        assert coordinator.get_capability("session.durable_checkpoint") is None
        snapshots = []
        async def checkpoint():
            canonical = store.save(session.session_id,
                await coordinator.get("context").get_messages(), {})
            media.acknowledge_media(coordinator, store, session.session_id, canonical)
            snapshots.append(copy.deepcopy(canonical))
            return canonical
        coordinator.register_capability("session.durable_checkpoint", checkpoint)
        from amplifier_web.attachments import install_media_context
        await install_media_context(coordinator)
        provider.checkpoint_seen = lambda: bool(snapshots and any(
            media.NAMESPACE in (row.get("metadata") or {}) for row in snapshots[-1]))
        checks = []
        async def work_check(name, arguments):
            checks.append((name, copy.deepcopy(arguments)))
            # Synthetic host decision; the mounted Work wrapper is real.
            return {"allowed": not (deny_work and arguments.get("file_path") == str(provider.path))}
        async def app_bridge(operation, args):
            if operation == "context.prepare":
                return {}
            if operation == "context.manifest":
                return {"surfaces": [], "inputIds": []}
            return {}
        coordinator.register_capability("web.work_effect_check", work_check)
        await install_app_access(coordinator, app_bridge, execution_role="root")
        await media.flush_adapters(coordinator)
        dispatch = coordinator.get_capability("tools.dispatch")
        assert type(dispatch).__name__ == "BoundDispatch"
        assert dispatch is not loop.approved_dispatch
        assert mounted_read_file(coordinator) is not None
        if post:
            media.registration_hooks(coordinator).register("tool:post", post, priority=900, name="join-post")
        host = FiniteSourceHost(coordinator)
        coordinator.register_capability("live.host", host)
        return session, store, checkpoint, snapshots, checks, host
    except BaseException:
        await session.cleanup()
        raise


async def test_host_registration_view_forwards_only_its_exact_post_route(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    provider = ScriptedReadProvider(tmp_path / "image", read=False)
    session, _, _, _, _, host = await actual_root(tmp_path, provider)
    try:
        native = session.coordinator
        loop = native.get("orchestrator")
        with host.execution_registration(native, native.hooks) as (view, hooks):
            assert await loop._media_consumer.refresh()
        assert host.sealed
        event = {"tool_name": "read_file", "tool_call_id": "actual-route", "result": None}
        boundary = view.get_capability("session.media.tool_post")
        settled = await boundary(event, hooks=hooks, coordinator=view)
        assert settled["complete"] is True and settled["result"] is None
        for args in ({"hooks": native.hooks, "coordinator": view},
                     {"hooks": hooks, "coordinator": object()}):
            with pytest.raises(ValueError):
                await boundary(event, **args)
    finally:
        await session.cleanup()


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
@pytest.mark.parametrize("redact", [False, True])
async def test_actual_consumer_first_read_without_manual_readiness(tmp_path, monkeypatch, format, redact):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    path, original = picture(tmp_path, format)
    posts = []
    async def post(event, data):
        posts.append(data["tool_call_id"])
        return HookResult(action="modify", data={"result": None}) if redact else HookResult()
    provider = ScriptedReadProvider(path)
    session, store, checkpoint, snapshots, checks, host = await actual_root(tmp_path, provider, post=post)
    try:
        assert await session.execute("Inspect the authorized local image") == "offline completion"
        assert host.sealed
        assert posts == ["call-first-image"]
        assert checks == [("read_file", {"file_path": str(path)})]
        assert provider.calls == 2
        assert provider.observed[-1] == ([] if redact else [("tool", "call-first-image", original)])
        canonical = await checkpoint()
        refs = [row for row in canonical if media.NAMESPACE in (row.get("metadata") or {})]
        assert bool(refs) is (not redact)
        assert not session.coordinator.get_capability("web.media.request_only").requests
    finally:
        await session.cleanup()
    assert provider.capability.report("test", event="readiness", outcome="inspect")[
        "consumers"]["loop-live"]["ready"] is False
    if redact:
        return
    path.unlink()
    reload_provider = ScriptedReadProvider(path, read=False)
    resumed, _, checkpoint, _, checks, host = await actual_root(tmp_path, reload_provider, store=store)
    try:
        loaded, _ = store.load("root-consumer-join")
        await resumed.coordinator.get("context").set_messages(loaded)
        await checkpoint()
        await resumed.execute("Use the retained image without replay")
        assert reload_provider.observed == [[("tool", "call-first-image", original)]]
        assert checks == [] and host.sealed
    finally:
        await resumed.cleanup()


@pytest.mark.parametrize("failure", ["post-null", "read-policy", "work-denial"])
async def test_actual_loop_image_refusal_then_plaintext_recovery(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    path, _ = picture(tmp_path)
    text = tmp_path / "plaintext"
    text.write_text("plaintext after refused image")
    provider = ScriptedReadProvider(path)
    provider.paths.append(text)
    async def post(event, data):
        if failure == "post-null" and data["tool_call_id"] == "call-first-image":
            return HookResult(action="modify", data={"result": None})
        return HookResult()
    session, _, checkpoint, _, checks, host = await actual_root(
        tmp_path, provider, post=post, deny_work=failure == "work-denial")
    try:
        if failure == "read-policy":
            mounted_read_file(session.coordinator).denied_read_paths = [str(path)]
        assert await session.execute("Read image then plaintext") == "offline completion"
        canonical = await checkpoint()
        assert host.sealed and provider.calls == 3
        assert all(images == [] for images in provider.observed)
        assert not any(media.NAMESPACE in (row.get("metadata") or {}) for row in canonical)
        results = [row for row in canonical if row.get("role") == "tool"]
        assert results[0]["tool_call_id"] == "call-first-image"
        assert results[-1]["tool_call_id"] == "call-followup-text"
        assert "plaintext after refused image" in results[-1]["content"]
        assert checks[-1] == ("read_file", {"file_path": str(text)})
    finally:
        await session.cleanup()


async def test_live_composer_appends_durable_user_ref_before_transport(tmp_path, monkeypatch):
    from amplifier_module_loop_live.runtime import Input, Runtime
    from amplifier_web.attachments import encode, save

    home = tmp_path / "app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    path, original = picture(tmp_path)
    encoded = base64.b64encode(original).decode()
    upload = save(home, path.name, encoded)
    provider = ScriptedReadProvider(path, read=False)
    # A different selected model is not a host decision.
    provider.default_model = "fixture-vision"
    session, store, checkpoint, _, _, host = await actual_root(tmp_path, provider)
    runtime = Runtime(session.session_id)
    session.coordinator.register_capability("live.runtime", runtime)
    session.coordinator.register_capability("live.attachments.encode", encode)
    command = Input("user", "Inspect my upload", attachments=({"id": upload["id"]},))
    execution = asyncio.create_task(session.execute(""))
    try:
        await runtime.submit(command)
        async with asyncio.timeout(5):
            while not any(row["type"] == "generation.finished" for row in runtime.events):
                if execution.done():
                    await execution
                    pytest.fail("Live loop ended without a generation")
                await asyncio.sleep(0.01)
        assert host.sealed
        assert provider.observed == [[("user", None, original)]]
        rows = await checkpoint()
        user = next(row for row in rows if row["role"] == "user"
                    and media.NAMESPACE in (row.get("metadata") or {}))
        assert user["metadata"]["amplifier_input"]["id"] == command.id
        [ref] = user["metadata"][media.NAMESPACE]["refs"]
        assert ref["provenance"]["kind"] == "upload"
        assert encoded not in (store.directory(session.session_id) / "transcript.jsonl").read_text()
    finally:
        if not execution.done():
            await runtime.submit(Input("stop"))
        try:
            await asyncio.gather(asyncio.wait_for(execution, timeout=5), return_exceptions=True)
        finally:
            await session.cleanup()


async def test_new_process_rehydrates_checkpoint_without_source_or_tool_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    path, original = picture(tmp_path)
    provider = ScriptedReadProvider(path)
    session, store, checkpoint, _, _, _ = await actual_root(tmp_path, provider)
    try:
        await session.execute("Read the image")
        await checkpoint()
    finally:
        await session.cleanup()
    path.unlink()
    script = """
import asyncio, hashlib, sys
from pathlib import Path
sys.path.insert(0, 'tests')
from test_tool_image_consumer_join import actual_root, ScriptedReadProvider

async def main():
    root = Path(sys.argv[1])
    provider = ScriptedReadProvider(Path(sys.argv[2]), read=False)
    session, store, checkpoint, _, checks, host = await actual_root(root, provider)
    try:
        rows, _ = store.load(session.session_id)
        await session.coordinator.get('context').set_messages(rows)
        await checkpoint()
        await session.execute('Use the saved image')
        assert not checks and host.sealed
        [[(role, call_id, pixels)]] = provider.observed
        assert (role, call_id) == ('tool', 'call-first-image')
        print(hashlib.sha256(pixels).hexdigest())
    finally:
        await session.cleanup()

asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, str(tmp_path), str(path),
        cwd=Path(__file__).resolve().parents[1],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, errors = await asyncio.wait_for(process.communicate(), timeout=15)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, errors.decode()
    assert output.strip().decode() == hashlib.sha256(original).hexdigest()