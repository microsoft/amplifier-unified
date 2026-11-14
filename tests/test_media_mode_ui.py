"""RuntimeControls UI boundary on actual modes/Core, not ModeTool requalification.

Run with the explicitly selected modes/Foundation sources on PYTHONPATH. The
named graph below selects this fixture's actual loader/callback bytes, mounted
modes components and receiver identities. It is not an F1 generation, provider,
installed-host or rendered-browser qualification. No Boolean graph grant is used.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from functools import partial
import hashlib
from importlib.metadata import version
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from amplifier_core import AmplifierSession, HookRegistry, HookResult, ModuleCoordinator

modes = pytest.importorskip("amplifier_module_hooks_mode")
tool_mode = pytest.importorskip("amplifier_module_tool_mode")
foundation = pytest.importorskip("amplifier_foundation.session.media_hook_bridge")
from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY
from amplifier_web.host import media
from amplifier_web.host.media_graph import NamedMediaGraph
from amplifier_web.runtime_controls import RuntimeControls

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        version("amplifier-core") != "2.0.2"
        or not hasattr(foundation.MediaHookBridge, "begin_participating_graph")
        or not hasattr(modes.ModeHooks, "track_transition"),
        reason="Requires selected participating modes/Foundation and actual Core 2.0.2",
    ),
]


async def mount_ui_loop(current):
    await current.mount("orchestrator", object())


async def mount_ui_context(current):
    await current.mount("context", object())


class ModeUiLoader:
    """Inert loop/context plus actual modes, through Core's public loader path."""

    def __init__(self, hook_config):
        self.hook_config = hook_config
        self.queue = []

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        return {
            "fixture-ui-loop": mount_ui_loop,
            "fixture-ui-context": mount_ui_context,
            "hooks-mode": self.mount_hooks,
            "tool-mode": self.mount_tool,
        }[module_id]

    async def mount_hooks(self, current):
        await modes.mount(current, self.hook_config)
        # The actual fixture constructor selects this exact modes receiver,
        # not any object sharing its class/name or a callback's self-report.
        selection = current.get_capability("web.media.graph_selection")
        selection.mint_owner(
            current.session_state["mode_hooks"], kind="actual-mode-hooks",
            construction=current,
        )

    async def mount_tool(self, current):
        return await tool_mode.mount(current, {"gate_policy": "auto"})

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


def policy_callback(decision):
    async def post(event, data):
        return HookResult(action=decision)
    return post


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest_asyncio.fixture
async def ui(tmp_path):
    mode_dir = tmp_path / "modes"
    mode_dir.mkdir()
    for name in ("alpha", "beta"):
        (mode_dir / f"{name}.md").write_text(
            f"---\nmode:\n  name: {name}\n  description: {name}\n"
            f"  contributes:\n    context: ['{name}.txt']\n---\n{name}\n",
            encoding="utf-8",
        )
    hook_config = {"search_paths": [str(mode_dir)]}
    plan = {
        "session": {"orchestrator": "fixture-ui-loop", "context": "fixture-ui-context"},
        "hooks": [{"module": "hooks-mode", "config": hook_config}],
        "tools": [{"module": "tool-mode", "config": {"gate_policy": "auto"}}],
    }
    loader = ModeUiLoader(hook_config)
    sources = (
        Path(__file__), Path(modes.__file__), Path(tool_mode.__file__),
        Path(foundation.__file__), Path(media.__file__),
        Path(inspect.getsourcefile(RuntimeControls)),
    )
    selection = NamedMediaGraph(
        "runtime-controls-actual-modes-ui", plans=[plan],
        sources={str(path): source_hash(path) for path in sources},
        bindings=(loader,),
        dependencies=(modes, tool_mode, modes.logger, modes.hashlib, modes.events, HookResult),
        owner_rules=(
            ("coordinator", ModuleCoordinator, ("session_id",)),
            ("actual-mode-hooks", modes.ModeHooks, ("coordinator", "discovery")),
        ),
        environments={"policy_callback.<locals>.post": {"decision": "continue"}},
    )
    session = AmplifierSession(plan, session_id="runtime-controls-actual-mode-ui")
    coordinator = session.coordinator
    coordinator.loader = loader
    media.begin_hook_graph(session, selection=selection)
    control = None
    try:
        factory = coordinator.get_capability("session.media.graph_transition")
        assert isinstance(factory, partial)
        assert factory.func is media.hook_configuration
        assert factory.args == (coordinator,)
        await session.initialize()
        media.registration_hooks(coordinator).register(
            "tool:post", policy_callback("continue"), name="ui-selected-host")
        graph = coordinator.get_capability("web.media.hook_graph")
        # Useful diagnostics if the explicitly selected source union stops joining.
        evidence = graph.participants_evidence()
        for item in evidence["loads"]:
            assert selection.accepts_callable(item["mount"]), item
        for item in evidence["registrations"]:
            assert selection.accepts_callable(item["original"]), item["original"]
        for item in evidence.get("mode_registrations", ()):
            callback = item["original"]
            assert selection.accepts_callable(callback), (
                callback, selection._binding_valid(callback.__self__),
                [(name, type(callback.__func__.__globals__[name]).__name__)
                 for name in callback.__code__.co_names if name in callback.__func__.__globals__])
        assert selection.accepts_plan(coordinator.config), coordinator.config
        assert selection.accepts_construction(graph), evidence
        assert media.seal_hook_graph(coordinator)
        bridge = coordinator.get_capability("web.media.hook_bridge")
        hooks = coordinator.session_state["mode_hooks"]
        assert type(hooks) is modes.ModeHooks
        assert type(coordinator.hooks) is HookRegistry
        assert inspect.signature(hooks.track_transition).parameters["payload"].kind is inspect.Parameter.KEYWORD_ONLY
        control = RuntimeControls(session, SimpleNamespace(generation=None, queued_inputs=0))
        checkpoints = []

        async def checkpoint():
            # A checkpoint cannot run while the operation tracker is open or
            # before hook_configuration has exited and resealed its successor.
            receipt = bridge.graph_receipt
            assert bridge.validate_receipt(receipt)
            assert coordinator.get_capability("web.media.source_join")[2] is receipt
            assert hooks._transitions == {}
            checkpoints.append(receipt)

        control.checkpoint = AsyncMock(side_effect=checkpoint)
        yield SimpleNamespace(
            session=session, control=control, coordinator=coordinator, hooks=hooks,
            bridge=bridge, selection=selection, checkpoints=checkpoints, factory=factory,
        )
    finally:
        if control is not None:
            await control.close()
        await session.cleanup()
        hooks = coordinator.session_state.get("mode_hooks")
        if hooks is not None:
            selection.release_construction(hooks.coordinator)


async def set_mode(ui, name="alpha"):
    return await ui.control.mode("mode.set", {"name": name})


def assert_unqualified(ui, old):
    assert not ui.bridge.validate_receipt(old)
    assert not ui.bridge.validate_receipt(ui.bridge.graph_receipt)
    assert ui.coordinator.get_capability("web.media.source_join") is None
    ui.control.checkpoint.assert_not_awaited()
    assert ui.hooks._transitions == {}


async def test_ui_actual_set_change_clear_reseals_before_checkpoint(ui):
    receipts = []
    for operation, args, active, contexts in (
        ("mode.set", {"name": "alpha"}, "alpha", ["alpha.txt"]),
        ("mode.set", {"name": "beta"}, "beta", ["beta.txt"]),
        ("mode.clear", {}, None, []),
    ):
        old = ui.bridge.graph_receipt
        ui.hooks.warned_tools.add("prior-warning")
        result = await ui.control.mode(operation, args)
        assert result["active"] == active
        assert ui.coordinator.session_state["active_mode"] == active
        assert ui.coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY) == contexts
        assert ui.hooks.warned_tools == set()
        new = ui.bridge.graph_receipt
        assert new is not old and not ui.bridge.validate_receipt(old)
        assert ui.bridge.validate_receipt(new)
        receipts.append(new)
    assert ui.checkpoints == receipts
    assert ui.control.checkpoint.await_count == 3


@pytest.mark.parametrize("phase", ["activate", "change", "clear"])
@pytest.mark.parametrize("failure", ["apply-false", "swallowed-error", "cancel"])
async def test_ui_actual_mode_contribution_failure_never_checkpoints(ui, monkeypatch, phase, failure):
    if phase != "activate":
        await set_mode(ui)
    ui.control.checkpoint.reset_mock()
    old = ui.bridge.graph_receipt
    previous = ui.coordinator.session_state["active_mode"]
    ui.hooks.warned_tools.add("keep-warning")
    overlay = ui.hooks._get_or_create_overlay()
    method = "revoke" if phase == "clear" else "apply"
    if failure == "apply-false":
        replacement = AsyncMock(return_value=SimpleNamespace(success=False, error="fixture refusal"))
    else:
        error = asyncio.CancelledError() if failure == "cancel" else RuntimeError("fixture swallowed error")
        replacement = AsyncMock(side_effect=error)
    monkeypatch.setattr(overlay, method, replacement)
    operation, args = ("mode.clear", {}) if phase == "clear" else (
        "mode.set", {"name": "beta" if phase == "change" else "alpha"})
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
        await ui.control.mode(operation, args)
    replacement.assert_awaited_once()
    state = ui.coordinator.session_state
    assert state["active_mode"] == previous
    assert state["mode_transition_failure"]["previous_mode"] == previous
    assert state["mode_transition_failure"]["target_mode"] == args.get("name")
    assert state["mode_runtime_overlay"] is overlay
    assert ui.hooks.warned_tools == {"keep-warning"}
    assert_unqualified(ui, old)
    assert (await ui.hooks.handle_tool_pre("tool:pre", {"tool_name": "bash"})).action == "deny"
    assert "may be partial" in (await ui.hooks.handle_provider_request("provider:request", {})).context_injection


async def test_ui_successful_callback_cannot_hide_synchronous_context_exit_failure(ui):
    await set_mode(ui)
    ui.control.checkpoint.reset_mock()
    old = ui.bridge.graph_receipt
    ui.hooks.warned_tools.add("keep-warning")

    @contextmanager
    def broken_exit():
        with ui.factory():
            yield
            raise RuntimeError("fixture synchronous exit failure")

    ui.coordinator.register_capability("session.media.graph_transition", broken_exit)
    with pytest.raises(RuntimeError, match="synchronous exit failure"):
        await set_mode(ui, "beta")
    state = ui.coordinator.session_state
    assert state["active_mode"] == "alpha"
    assert state["mode_transition_failure"]["target_mode"] == "beta"
    assert ui.hooks.warned_tools == {"keep-warning"}
    # Actual beta contributions happened: restoring the alpha label is NOT a
    # rollback or permission to reuse its old receipt.
    assert ui.coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY) == ["beta.txt"]
    assert_unqualified(ui, old)


async def test_ui_missing_actual_callback_cannot_reuse_prior_completion(ui):
    await set_mode(ui)
    with ui.factory():
        media.registration_hooks(ui.coordinator).unregister("mode-overlay-change")
    old = ui.bridge.graph_receipt
    assert ui.bridge.validate_receipt(old)
    ui.control.checkpoint.reset_mock()
    with pytest.raises(RuntimeError, match="did not complete"):
        await set_mode(ui, "beta")
    assert ui.coordinator.session_state["active_mode"] == "alpha"
    assert ui.coordinator.session_state["mode_transition_failure"]["target_mode"] == "beta"
    assert_unqualified(ui, old)


async def test_ui_external_callback_swallowed_by_core_is_not_success(ui):
    def broken(event, data):
        raise RuntimeError("fixture external contribution failure")

    # This exact callback is source-selected, not an unreviewed graph injection.
    with ui.factory():
        media.registration_hooks(ui.coordinator).register(
            "mode:activated", broken, priority=50, name="ui-external-broken")
    old = ui.bridge.graph_receipt
    assert ui.bridge.validate_receipt(old)
    with pytest.raises((RuntimeError, ValueError)):
        await set_mode(ui)
    assert ui.coordinator.session_state["active_mode"] is None
    assert ui.coordinator.session_state["mode_transition_failure"]
    assert_unqualified(ui, old)


async def test_ui_failed_transition_cannot_downgrade_after_factory_removal(ui, monkeypatch):
    overlay = ui.hooks._get_or_create_overlay()
    monkeypatch.setattr(overlay, "apply", AsyncMock(return_value=SimpleNamespace(success=False, error="refusal")))
    with pytest.raises(RuntimeError):
        await set_mode(ui)
    old = ui.bridge.graph_receipt
    ui.coordinator.register_capability("session.media.graph_transition", None)
    # Observe the actual returned UI outcome, not an assertion raised by the
    # checkpoint-order probe. The failure latch must refuse before checkpoint.
    ui.control.checkpoint.side_effect = None
    with pytest.raises((RuntimeError, ValueError)):
        await set_mode(ui, "beta")
    assert ui.coordinator.session_state["active_mode"] is None
    assert_unqualified(ui, old)


async def test_ui_primary_cancellation_survives_secondary_sync_exit_error(ui, monkeypatch):
    overlay = ui.hooks._get_or_create_overlay()
    monkeypatch.setattr(overlay, "apply", AsyncMock(side_effect=asyncio.CancelledError()))
    old = ui.bridge.graph_receipt

    @contextmanager
    def broken_exit():
        try:
            with ui.factory():
                yield
        finally:
            raise RuntimeError("secondary synchronous exit error")

    ui.coordinator.register_capability("session.media.graph_transition", broken_exit)
    with pytest.raises(asyncio.CancelledError):
        await set_mode(ui)
    assert ui.coordinator.session_state["active_mode"] is None
    assert ui.coordinator.session_state["mode_transition_failure"]
    assert_unqualified(ui, old)


async def test_ui_named_reviewed_union_accepts_selected_capture_not_unreviewed_variant(ui):
    await set_mode(ui)
    with ui.factory():
        media.registration_hooks(ui.coordinator).register(
            "tool:post", policy_callback("continue"), name="ui-reviewed-policy")
    reviewed = ui.bridge.graph_receipt
    assert ui.bridge.validate_receipt(reviewed)
    assert ui.coordinator.get_capability("web.media.source_join")[2] is reviewed
    with ui.factory():
        media.registration_hooks(ui.coordinator).register(
            "tool:post", policy_callback("deny"), name="ui-unreviewed-variant")
    assert not ui.bridge.validate_receipt(reviewed)
    assert ui.coordinator.get_capability("web.media.source_join") is None
    assert media.seal_hook_graph(ui.coordinator) is False
