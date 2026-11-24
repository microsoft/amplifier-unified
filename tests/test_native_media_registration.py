"""Native adapter construction through the actual Core/Foundation media graph.

No inference or external transport: wire scenarios live in immediate-direction.
"""
import asyncio
from types import SimpleNamespace

import pytest
from amplifier_core import AmplifierSession
from amplifier_module_provider_openai import OpenAIProvider

from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.host import media
from amplifier_web.host.children import StandaloneHostAdapter
from amplifier_web.host.storage import SessionStore
from amplifier_web.native_provider import install_native
from amplifier_web.runtime_worker import Worker
from fixtures.narrow_tool_image_fixture import ActualConsumerLoader


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("Native construction qualification cannot use the network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


async def session_with_media(tmp_path, *, model="gpt-6.1-terra", enabled=True,
                             session_id="native-media-registration", with_media=True):
    original = OpenAIProvider(api_key="synthetic-only", config={"default_model": model})
    plan = {"session": {"orchestrator": {"module": "loop-live", "config": {
                "configured_bundle": True, "native_provider": enabled}},
            "context": {"module": "context-simple"}},
        "providers": [{"module": "provider-openai", "config": {"default_model": model}}]}
    session = AmplifierSession(plan, session_id=session_id)
    session.coordinator.loader = ActualConsumerLoader(original)
    if with_media:
        media.bind_media(session, SessionStore(tmp_path / "sessions"))
    await session.initialize()
    async def checkpoint():
        return []
    session.coordinator.register_capability("session.durable_checkpoint", checkpoint)
    worker = Worker()
    worker.session = session
    worker.runtime = SimpleNamespace(session_id=session.session_id, generation=None)
    worker.telemetry = ExecutionEvents(session.session_id, lambda event: None)
    return session, original, worker


async def stop_loop(runtime, waiter, owner=None):
    """Stop the actual loop; cancelling a Core awaitable is not retirement."""
    from amplifier_module_loop_live.runtime import Input
    if not runtime.closed and not runtime.stop_requested:
        await runtime.submit(Input("stop"))
    tasks = {waiter}
    if owner is not None:
        tasks.add(owner)
    done, pending = await asyncio.wait(tasks, timeout=5)
    assert not pending, "Actual loop did not finish after Stop"
    for task in done:
        if not task.cancelled():
            task.result()


async def test_hooks_precede_native_but_provider_observation_follows_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    session, original, worker = await session_with_media(tmp_path)
    c = session.coordinator
    loop = c.get("orchestrator")
    try:
        worker.install_activity(c, observe_providers=False)
        handlers = c.hooks.list_handlers()
        assert "amplifier-web-activity-tool:post" in handlers["tool:post"]
        assert "complete" not in original.__dict__
        assert not getattr(original, "_amplifier_web_observed", False)
        with media.hook_configuration(c):
            await StandaloneHostAdapter().prepare_execution(loop, c, c.get("providers"))
            native = c.get_capability("web.native_provider")
            assert native.status()["supported"]
            assert c.get("providers")["offline"] is native.provider
            worker.install_activity(c)
            observed = native.provider.complete
            worker.install_activity(c)
            assert native.provider.complete is observed
            await media.flush_adapters(c)
            assert await loop._media_consumer.refresh()
        assert c.hooks.list_handlers() == handlers
        assert "complete" not in original.__dict__
        assert native.provider._amplifier_web_observed is True
        assert media.seal_hook_graph(c) is True
        assert not c.get_capability("web.media.adapters")
        assert original._client is None and native.provider._client is None
    finally:
        await session.cleanup()


async def test_existing_instance_wrapper_is_still_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    session, original, worker = await session_with_media(tmp_path)
    c = session.coordinator
    try:
        # This remains forbidden, regardless of whether our observer installed it.
        worker.install_activity(c)
        wrapped = original.complete
        with media.hook_configuration(c):
            await install_native(c.get("orchestrator"), c, c.get("providers"))
        native = c.get_capability("web.native_provider")
        assert native.provider is None and "instance wrapper" in native.reason
        assert c.get("providers")["offline"] is original and original.complete is wrapped
        assert original._client is None
    finally:
        await session.cleanup()


@pytest.mark.parametrize("mode", ["finite", "disabled", "unsupported", "endpoint"])
async def test_non_native_routes_keep_actual_provider_observation(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    session, original, worker = await session_with_media(tmp_path,
        model="gpt-5.4" if mode == "unsupported" else "gpt-6.1-terra",
        enabled=mode != "disabled")
    c = session.coordinator
    try:
        if mode == "finite":
            c.register_capability("live.child_mode", "finite")
        if mode == "endpoint":
            original.base_url = "https://not-openai.invalid/v1"
        worker.install_activity(c, observe_providers=False)
        with media.hook_configuration(c):
            _, providers, scope = await StandaloneHostAdapter().prepare_execution(
                c.get("orchestrator"), c, c.get("providers"))
            worker.install_activity(c)
            await media.flush_adapters(c)
        native = c.get_capability("web.native_provider")
        assert native is None if mode == "finite" else native.provider is None
        assert scope == (c.session_id if mode == "finite" else None)
        assert providers["offline"] is c.get("providers")["offline"] is original
        assert original._amplifier_web_observed and original._client is None
    finally:
        await session.cleanup()


@pytest.mark.parametrize("change", [
    "foreign-owner", "canonical-spoof", "foreign-view", "changed-registration",
    "changed-construction", "changed-runtime", "stopped", "closed", "not-running",
])
async def test_actual_loop_issued_view_is_exact_and_expires(tmp_path, monkeypatch, change):
    """Run actual loop startup with production Worker host, but no model input."""
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_module_loop_live.scope import LIVE_OWNER
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    monkeypatch.setattr("amplifier_web.runtime_worker.publish", lambda event: None)
    session, original, worker = await session_with_media(tmp_path)
    c = session.coordinator
    loop = c.get("orchestrator")
    ready = asyncio.get_running_loop().create_future()
    def observe(event):
        if event["type"] == "session.ready" and not ready.done():
            ready.set_result(event)
    runtime = Runtime(session_id=session.session_id, observer=observe)
    worker.runtime = runtime
    c.register_capability("live.runtime", runtime)
    adapter = worker.execution_host(StandaloneHostAdapter())
    c.register_capability("live.host", adapter)
    worker.install_activity(c, observe_providers=False)
    task = asyncio.create_task(session.execute(""))
    try:
        done, _ = await asyncio.wait((task, ready), timeout=5,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()
        assert ready in done, "Actual loop did not reach ready"
        assert ready.result()["native"] is True
        native = c.get_capability("web.native_provider")
        assert native.provider._amplifier_web_observed
        assert not adapter.registrations  # Configuration is already closed.
        view = media.registration_coordinator(c)
        state = c.get_capability("web.media.construction")
        assert loop.coordinator is view and view is not c
        assert state.bridge.validate_receipt(state.bridge.graph_receipt)
        assert c.get_capability(media.NAMESPACE).enabled
        assert "complete" not in original.__dict__
        token = LIVE_OWNER.set(loop)
        try:
            assert native.provider.owner_getter() is loop
            # Another object forwarding the same canonical identity has no grant.
            if change == "foreign-owner":
                LIVE_OWNER.set(SimpleNamespace(coordinator=view))
            elif change == "canonical-spoof":
                LIVE_OWNER.set(SimpleNamespace(coordinator=c))
            elif change == "foreign-view":
                loop.coordinator = media.HostRegistrationView(c, view.participant)
            elif change == "changed-registration":
                c.register_capability("web.media.registration",
                                      media.HostRegistrationView(c, view.participant))
            elif change == "changed-construction":
                c.register_capability("web.media.construction", object())
            elif change == "changed-runtime":
                c.register_capability("live.runtime", object())
            elif change == "stopped":
                runtime.stop_requested = True
            elif change == "closed":
                runtime.closed = True
            else:
                loop.running = False
            assert native.provider.owner_getter() is None
        finally:
            LIVE_OWNER.reset(token)
            loop.coordinator = view
            loop.running = True
            runtime.closed = runtime.stop_requested = False
            c.register_capability("web.media.registration", view)
            c.register_capability("web.media.construction", state)
            c.register_capability("live.runtime", runtime)
        with pytest.raises(ValueError, match="exact issued"):
            native.bind_execution(loop, media.HostRegistrationView(c, view.participant))
        with pytest.raises(ValueError, match="already has"):
            native.bind_execution(loop, view)
        owner_task = native.execution_binding[-1]
        await stop_loop(runtime, task, owner_task)
        assert owner_task.done() and runtime.closed and not loop.running
        assert loop.runtime is None
        assert native.execution_binding is None
        token = LIVE_OWNER.set(loop)
        try:
            assert native.provider.owner_getter() is None
            # Replaying the former canonical object cannot revive a sealed binding.
            loop.coordinator = c
            assert native.provider.owner_getter() is None
        finally:
            LIVE_OWNER.reset(token)
        assert original._client is None and native.provider._client is None
    finally:
        try:
            native = c.get_capability("web.native_provider")
            binding = native.execution_binding if native else None
            await stop_loop(runtime, task, binding[-1] if binding else None)
        finally:
            await session.cleanup()


@pytest.mark.parametrize("mode", ["native", "ordinary", "finite"])
async def test_children_install_retains_observed_adapter(tmp_path, monkeypatch, mode):
    """The real registry installs its explicit adapter, not an inherited fallback."""
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import Children
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    monkeypatch.setattr("amplifier_web.runtime_worker.publish", lambda event: None)
    root, _, worker = await session_with_media(tmp_path / "root", session_id="parent")
    child, original, _ = await session_with_media(tmp_path / "child", session_id="child",
        model="gpt-5.4" if mode == "ordinary" else "gpt-6.1-terra", with_media=False)
    task = None
    try:
        registry = Children(worker.runtime, SessionStore(tmp_path / "children"), None)
        root.coordinator.register_capability("live.children", registry)
        root.coordinator.register_capability("live.host", registry.host_adapter)
        worker.install_execution_observation()
        c = child.coordinator
        c.register_capability("live.child_mode", "finite" if mode == "finite" else "persistent")
        c.register_capability("web.worker_run", "child-run")
        # This is the actual inherited callback used before Children.install.
        install = root.coordinator.get_capability("web.activity.install")
        c.register_capability("web.activity.install", install)
        install(c)
        assert bool(getattr(original, "_amplifier_web_observed", False)) is (mode == "finite")
        await registry.install(child, SimpleNamespace())
        assert c.get_capability("live.host") is root.coordinator.get_capability("live.host")
        assert c.get_capability("live.host") is registry.host_adapter
        if mode == "finite":
            # Custom finite loops need no late preparation for observation.
            assert original._amplifier_web_observed
            assert c.get_capability("web.native_provider") is None
            return
        ready = asyncio.get_running_loop().create_future()
        def observe(event):
            if event["type"] == "session.ready" and not ready.done():
                ready.set_result(event)
        runtime = Runtime(session_id=child.session_id, observer=observe)
        c.register_capability("live.runtime", runtime)
        task = asyncio.create_task(child.execute(""))
        done, _ = await asyncio.wait((task, ready), timeout=5,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()
        assert ready in done
        native = c.get_capability("web.native_provider")
        assert ready.result()["native"] is (mode == "native")
        mounted = c.get("providers")["offline"]
        assert mounted._amplifier_web_observed
        assert (mounted is native.provider) if mode == "native" else (mounted is original)
        assert original._client is None and mounted._client is None
    finally:
        try:
            if task is not None:
                await stop_loop(runtime, task)
        finally:
            await child.cleanup()
            await root.cleanup()
