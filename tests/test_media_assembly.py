"""Offline tests for the packaged recipe, not for a fixture provider.

Run with --assert=plain in the already-prepared exact source environment and
UNIFIED_MEDIA_RECIPE_TEST=1. A skipped source-qualified test is not a passed
production graph. No test installs dependencies, invokes inference or opens
canonical history. The parent owns the normal Worker/FD-order integration.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import os
from pathlib import Path
import socket
import types

import pytest

from amplifier_web.host import media_assembly as assembly
from amplifier_web.host.media_graph import NamedMediaGraph

pytestmark = pytest.mark.skipif(os.environ.get("UNIFIED_MEDIA_RECIPE_TEST") != "1",
                                reason="requires the explicitly prepared recipe source environment")


def declaration(tmp_path):
    def source(package):
        return str(Path(importlib.util.find_spec(package).origin).parent)
    return {
        "session": {
            "orchestrator": {"module": "loop-live", "source": source("amplifier_module_loop_live")},
            "context": {"module": "context-simple", "source": source("amplifier_module_context_simple")},
        },
        "providers": [{"module": "provider-openai", "source": source("amplifier_module_provider_openai"),
                       "config": {"api_key": "offline-test-not-a-credential", "default_model": "gpt-6.1-sol"}}],
        "hooks": [{"module": "hook-context-intelligence",
                   "source": source("amplifier_module_hook_context_intelligence"),
                   "config": {"destinations": {}, "context_intelligence_server_url": "",
                              "context_intelligence_api_key": "", "base_path": str(tmp_path / "capture")}}],
    }


def expected_plan(authored, tmp_path, *, background_delegate=True):
    """Independent production transforms; never reads the selected plan."""
    from amplifier_web.host.session import live_plan
    from amplifier_web.session_files import project_slug
    from amplifier_foundation.bundle._observability import (
        FOUNDATION_OBSERVABILITY_EVENTS, inject_additional_events,
    )
    plan, _ = live_plan(authored, background_delegate)
    # materialize_bundle_providers' no-overlay agent transform.
    plan["agents"] = {}
    workspace = tmp_path / "workspace"
    plan.update(application_host="Amplifier Web", root_session_id="ordinary-root",
                project_slug=project_slug(workspace), bundle_name="my-ordinary-conversation",
                project_dir=str(workspace), project_name=workspace.name)
    inject_additional_events(plan, FOUNDATION_OBSERVABILITY_EVENTS)
    return plan


@pytest.fixture
def recipe(tmp_path):
    plan = declaration(tmp_path)
    args = {"workspace": tmp_path / "workspace", "session_id": "ordinary-root",
            "bundle_name": "my-ordinary-conversation"}
    # Fail loudly at this named qualification boundary rather than silently
    # skipping a changed source and making every negative test vacuous.
    assembly._catalog_sources()
    selected = assembly.select_from_declaration(plan, **args)
    assert type(selected) is NamedMediaGraph
    return plan, args, selected


def test_independent_plan_and_private_account_data(recipe, tmp_path):
    plan, args, selected = recipe
    before = copy.deepcopy(plan)
    expected = expected_plan(plan, tmp_path)
    assert selected.name == assembly.RECIPE
    assert selected.plans == (expected,)
    assert selected.accepts_plan(expected)
    assert not selected.accepts_plan(plan)
    assert plan == before
    assert args["workspace"].exists() is False
    catalog = Path(assembly.__file__).with_name("media_sources.json").read_text()
    assert "offline-test-not-a-credential" not in catalog
    assert "amplifier_module_provider_fixture" not in catalog
    assert "tests/fixtures" not in catalog
    assert "amplifier_module_hooks_mode" not in catalog
    assert len(selected.sources) <= 128


@pytest.mark.parametrize("change", [
    lambda p: p.update(routing={"default_matrix": "balanced"}),
    lambda p: p.update(agents={"child": {}}),
    lambda p: p.update(tools=[{"module": "tool-bash"}]),
    lambda p: p.update(spawn={}),
    lambda p: p["hooks"].append({"module": "hooks-mode"}),
    lambda p: p["providers"].append(copy.deepcopy(p["providers"][0])),
    lambda p: p["providers"][0].update(module="provider-openai-chatgpt"),
    lambda p: p["providers"][0].update(enabled=True),
    lambda p: p["providers"][0].update(instance_id="different-account"),
    lambda p: p["providers"][0].update(source="/unreviewed/custom/provider"),
    lambda p: p["session"]["orchestrator"].update(module="loop-streaming"),
    lambda p: p["session"]["orchestrator"].update(config={"background_delegate": 1}),
    lambda p: p["session"]["orchestrator"].update(config={"configured_bundle": 1}),
    lambda p: p["session"]["orchestrator"].update(config={"max_iterations": 4}),
    lambda p: p["session"]["context"].update(config={"clear": True}),
    lambda p: p["hooks"][0]["config"].update(destinations={"remote": {"url": "https://example.org"}}),
    lambda p: p["hooks"][0]["config"].update(context_intelligence_server_url="https://example.org"),
    lambda p: p["hooks"][0]["config"].update(exclude_events=[]),
    lambda p: p["hooks"][0]["config"].update(additional_events=["private:custom"]),
    lambda p: p["hooks"][0]["config"].update(destinations=False),
    lambda p: p["hooks"][0]["config"].update(base_path="${HOME}/capture"),
])
def test_unknown_or_different_graph_stays_neutral(recipe, change):
    plan, args, _ = recipe
    change(plan)
    assert assembly.select_from_declaration(plan, **args) is None


@pytest.mark.parametrize(("key", "value"), [
    ("api_key", ""), ("api_key", "${OPENAI_API_KEY}"), ("api_key", lambda: "key"),
    ("default_model", "gpt-6.1-luna"), ("base_url", "https://example.org/v1"),
    ("base_url", "https://api.openai.com/v1/"), ("use_streaming", 1),
    ("reasoning_effort", True), ("max_output_tokens", True), ("max_output_tokens", 32769),
    ("provider_runtime", {}), ("credential", {}), ("extra_request_params", {"model": "gpt-6.1-sol"}),
    ("extra_body", {}), ("recording", True), ("workload", "dynamic"),
])
def test_provider_configuration_is_exact_bounded_static(recipe, key, value):
    plan, args, _ = recipe
    plan["providers"][0]["config"][key] = value
    assert assembly.select_from_declaration(plan, **args) is None


@pytest.mark.parametrize("background", [True, False])
def test_preserves_background_optout_and_selected_provider_options(recipe, tmp_path, background):
    plan, args, _ = recipe
    plan["session"]["orchestrator"]["config"] = {"background_delegate": background}
    plan["providers"][0]["config"].update(base_url="https://api.openai.com/v1", use_streaming=False,
                                           reasoning_effort="high", max_output_tokens=4096)
    selected = assembly.select_from_declaration(plan, **args, background_delegate=background)
    assert type(selected) is NamedMediaGraph
    assert selected.plans == (expected_plan(plan, tmp_path, background_delegate=background),)


def test_platform_and_primitive_subclasses_do_not_auto_accept(recipe, monkeypatch):
    plan, args, _ = recipe
    monkeypatch.setattr(assembly.sys, "platform", "win32")
    assert assembly.select_from_declaration(plan, **args) is None
    monkeypatch.setattr(assembly.sys, "platform", "linux")
    assert assembly.select_from_declaration(plan, **args, background_delegate=1) is None
    with monkeypatch.context() as version:
        version.setattr(assembly.sys, "version_info", (3, 14))
        assert assembly.select_from_declaration(plan, **args) is None
    class Str(str):
        pass
    plan["providers"][0]["config"]["api_key"] = Str("static-key")
    assert assembly.select_from_declaration(plan, **args) is None


def test_bundle_name_is_data_not_a_profile_grant(recipe):
    plan, args, _ = recipe
    args["bundle_name"] = "/custom/ordinary-bundle.md"
    assert type(assembly.select_from_declaration(plan, **args)) is NamedMediaGraph
    plan["tools"] = [{"module": "unknown-tool"}]
    args["bundle_name"] = assembly.RECIPE
    assert assembly.select_from_declaration(plan, **args) is None


def test_source_mutation_cannot_self_bless(recipe, monkeypatch):
    plan, args, _ = recipe
    original = Path.read_bytes
    def changed(path):
        data = original(path)
        return data + b"\n# different producer\n" if path.name == "_media.py" else data
    monkeypatch.setattr(Path, "read_bytes", changed)
    assert assembly.select_from_declaration(plan, **args) is None


def test_custom_import_loader_stays_neutral(recipe, monkeypatch):
    plan, args, _ = recipe
    original = importlib.util.find_spec
    def custom(name):
        spec = original(name)
        if name == "amplifier_module_provider_openai":
            spec = copy.copy(spec)
            spec.loader = object()
        return spec
    monkeypatch.setattr(importlib.util, "find_spec", custom)
    assert assembly.select_from_declaration(plan, **args) is None


def test_never_accepts_observed_plan_or_relabels_another_root(recipe, tmp_path):
    plan, args, selected = recipe
    prepared = expected_plan(plan, tmp_path)
    assert assembly.select_from_declaration(prepared, **args) is None
    prepared["root_session_id"] = "another-root"
    assert not selected.accepts_plan(prepared)
    prepared = expected_plan(plan, tmp_path)
    prepared["providers"][0]["config"]["api_key"] = "rotated-account"
    assert not selected.accepts_plan(prepared)


def test_real_core_public_bridge_actual_openai_and_callback_environments(recipe, tmp_path, monkeypatch):
    """Real vendor mount, real Core loaders, actual Foundation issuer; no send."""
    plan, _, selected = recipe
    def forbidden(*args, **kwargs):
        raise AssertionError("Network is outside this offline recipe check")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    async def scenario():
        from amplifier_core import AmplifierSession
        from amplifier_foundation.bundle._prepared import BundleModuleResolver
        from amplifier_module_provider_openai import OpenAIProvider
        from amplifier_web.host.media import (
            begin_hook_graph, seal_hook_graph, mint_graph_owner, mount_media_adapter,
        )
        from amplifier_web.host.session import install_checkpoint_hooks
        from amplifier_web.app_guidance import install_app_access
        from amplifier_web.runtime_worker import Worker
        from amplifier_module_loop_live.runtime import Runtime
        from amplifier_web.surface_delivery import SurfaceProvider
        from functools import partial
        expected = expected_plan(plan, tmp_path)
        session = AmplifierSession(expected, session_id="ordinary-root")
        paths = {row["module"]: Path(row["source"]) for row in (
            plan["session"]["orchestrator"], plan["session"]["context"],
            plan["providers"][0], plan["hooks"][0])}
        await session.coordinator.mount("module-source-resolver", BundleModuleResolver(paths))
        session.coordinator.register_capability("session.working_dir", str(tmp_path / "workspace"))
        begin_hook_graph(session, selection=selected)
        try:
            await session.initialize()
            coordinator = session.coordinator
            provider = coordinator.get("providers")["openai"]
            assert type(provider) is OpenAIProvider
            assert provider.default_model == "gpt-6.1-sol"
            assert provider._client is None  # Constructor did not acquire/send.
            worker = Worker(media_graph_selection=selected, canonical_bootstrap=False)
            worker.session = session
            worker.runtime = Runtime(session_id="ordinary-root")
            mint_graph_owner(coordinator, worker, kind="worker",
                             associations=((worker, "session", session), (worker, "runtime", worker.runtime)))
            mint_graph_owner(coordinator, worker.runtime, kind="runtime")
            worker.install_activity(coordinator)
            async def bridge(*args):
                raise AssertionError("No app action is invoked during construction")
            await install_app_access(coordinator, bridge, execution_role="root")
            # Exercise the production checkpoint-hook factory and provider
            # wrapper/mount path. No checkpoint or provider request is invoked.
            install_checkpoint_hooks(coordinator, worker.runtime, partial(bridge))
            wrapped = coordinator.get_capability("web.provider_transform")(provider)
            assert type(wrapped) is SurfaceProvider
            await mount_media_adapter(coordinator, "providers", wrapped,
                                      previous=provider, name="openai")
            graph = coordinator.get_capability("web.media.hook_graph")
            bridge = coordinator.get_capability("web.media.hook_bridge")
            assert graph is not None and bridge.graph_receipt is None
            assert coordinator.get_capability("web.media.source_join") is None
            assert selected.bind_capability_owners(coordinator)
            evidence = graph.participants_evidence()
            rejected = [(section, getattr(row[key], "__qualname__", type(row[key]).__qualname__))
                        for section, key in (("loads", "mount"), ("ready", "callback"),
                                             ("registrations", "original"), ("contributions", "original"))
                        for row in evidence.get(section, ()) if not selected.accepts_callable(row[key])]
            assert rejected == []
            assert selected.accepts_construction(graph)
            # Every loaded capture implementation is one of the fixed source
            # identities, not an expanded runtime-discovered grant.
            import sys
            for name, module in tuple(sys.modules.items()):
                if name.startswith(("context_intelligence", "amplifier_module_hook_context_intelligence")):
                    origin = getattr(module, "__file__", None)
                    if origin and origin.endswith(".py"):
                        assert str(Path(origin).resolve()) in selected.sources, name
            # Actual issuer receipt, not a plan/source boolean. No SessionMedia
            # store was enrolled, so receipt alone still cannot release pixels.
            graph.seal()  # Let the public issuer explain any refusal directly.
            assert seal_hook_graph(coordinator)
            assert bridge.validate_receipt(bridge.graph_receipt) is True
            assert coordinator.get_capability("experimental.media.v1") is None
            loader = next(row["mount"] for row in evidence["loads"]
                          if row["mount"].__qualname__.endswith("mount_with_config_fs"))
            assert selected.accepts_callable(loader)
            def cell(value):
                return (lambda: value).__closure__[0]
            captured = {name: value.cell_contents for name, value in zip(loader.__code__.co_freevars, loader.__closure__)}
            assert "config" in captured
            changed = dict(captured)
            changed["config"] = {"configured_bundle": 1, "background_delegate": True}
            replacement = types.FunctionType(loader.__code__, loader.__globals__, loader.__name__,
                                             loader.__defaults__, tuple(cell(changed[name]) for name in loader.__code__.co_freevars))
            assert not selected.accepts_callable(replacement)
            # A known file name never excuses foreign bytecode.
            def foreign(*args):
                return None
            replacement = types.FunctionType(foreign.__code__.replace(co_filename=loader.__code__.co_filename), {})
            assert not selected.accepts_callable(replacement)
        finally:
            await session.cleanup()
    asyncio.run(scenario())
