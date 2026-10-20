"""Native Core rollback and Foundation routing; no network or provider requests.

Run explicitly with the qualified Core >=2.0.2 wheel and routing package installed.
The parent release lane owns provisioning. The old-wheel strict path is covered
separately; absence of these prerequisites is an explicit prerequisite failure.
"""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_core import AmplifierSession, ProviderInfo
from amplifier_core.loader import ModuleLoader
from amplifier_foundation import ProviderPreference, apply_provider_preferences_with_resolution
from amplifier_web.provider_isolation import ProviderIsolation, ProviderUnavailable, UnavailableProvider, require_available, require_selected


class Healthy:
    priority = 20
    name = "fixture"
    config = {"priority": 20, "default_model": "model-v1"}
    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Fixture", defaults={"model": "model-v1"})
    async def list_models(self):
        return [SimpleNamespace(id="model-v1")]
    async def complete(self, *args, **kwargs):
        raise AssertionError("Admission qualification must never invoke a model")


@pytest.fixture
async def native_accounts(request):
    from amplifier_web.provider_isolation import supported
    assert supported(), "This lane requires the actual Core>=2.0.2 wheel; old Core cannot qualify rollback"
    policy = ProviderIsolation()
    rows = [
        {"module": "provider-fixture", "id": "fixture", "config": {"priority": 20, "default_model": "model-v1"}},
        {"module": "provider-fixture", "instance_id": "broken", "config": {"priority": 1, "default_model": "model-v1"}},
    ]
    config = {"session": {"orchestrator": "probe", "context": "probe"}, "providers": rows}
    session = AmplifierSession(config)
    healthy = Healthy()
    attempts = []
    async def load(module, config=None, **_):
        if module != "provider-fixture":
            return AsyncMock(return_value=None)
        async def mount(coordinator):
            attempts.append(config["priority"])
            if config["priority"] == 1 and getattr(request, "param", None) == "silent-skip":
                return None  # Real provider mounts can decline missing config.
            await coordinator.mount("providers", healthy if config["priority"] == 20 else object(), name="fixture")
            if config["priority"] == 1:
                await coordinator.mount("providers", object(), name="leaked")
                raise ValueError("secret-bearing mount failure")
        return mount
    loader = MagicMock(spec=ModuleLoader)
    loader.load = AsyncMock(side_effect=load)
    loader.get_on_session_ready_queue.return_value = []
    session.coordinator.loader = loader
    await policy.before_initialize(session)
    original = copy.deepcopy(config)
    await session.initialize()
    assert config == original
    assert list(session.coordinator.get("providers")) == ["fixture", "broken"]
    assert session.coordinator.get("providers")["fixture"] is healthy
    assert isinstance(session.coordinator.get("providers")["broken"], UnavailableProvider)
    assert attempts == [20, 1]
    yield session, policy, healthy
    await session.cleanup()


async def test_native_partial_mount_restores_same_module_default_before_marker(native_accounts):
    session, policy, healthy = native_accounts
    require_available(session.coordinator, "fixture")
    with pytest.raises(ProviderUnavailable) as error:
        require_available(session.coordinator, "broken")
    assert error.value.failures[0]["instance_id"] == "broken"
    assert error.value.failures[0]["reason_code"] == "provider_mount_failed"
    assert "secret-bearing" not in str(error.value)
    assert policy.outcomes(session.coordinator) == error.value.failures


@pytest.mark.parametrize("native_accounts", ["silent-skip"], indirect=True)
async def test_native_silent_named_mount_cannot_steal_other_account(native_accounts):
    session, _, healthy = native_accounts
    assert session.coordinator.get("providers")["fixture"] is healthy
    with pytest.raises(ProviderUnavailable):
        require_available(session.coordinator, "broken")


async def test_real_default_selector_keeps_broken_priority_and_explicit_healthy_selection(native_accounts):
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    session, _, healthy = native_accounts
    loop = BundleLiveOrchestrator({})
    await session.coordinator.mount("orchestrator", loop)
    with pytest.raises(ProviderUnavailable):
        require_selected(session.coordinator)
    loop.root_provider = healthy
    require_selected(session.coordinator)


@pytest.mark.parametrize("model", ["model-v1", "model-*"])
async def test_native_foundation_named_and_glob_never_substitute_undeclared_account(native_accounts, model):
    session, _, _ = native_accounts
    plan = copy.deepcopy(session.config)
    before = copy.deepcopy(plan)
    with pytest.raises(ProviderUnavailable):
        await apply_provider_preferences_with_resolution(plan,
            [ProviderPreference(provider="broken", model=model)], session.coordinator)
    assert plan == before
    selected = await apply_provider_preferences_with_resolution(plan,
        [ProviderPreference(provider="broken", model=model), ProviderPreference(provider="fixture", model="model-v1")],
        session.coordinator)
    assert selected["providers"][0]["module"] == "provider-fixture"
    assert selected["providers"][0].get("instance_id") != "broken"


@pytest.mark.parametrize("model", ["model-v1", "model-*"])
async def test_real_role_resolver_propagates_failure_unless_next_candidate_declared(native_accounts, model):
    from amplifier_module_hooks_routing.resolver import resolve_model_role
    session, _, _ = native_accounts
    matrix = {"coding": {"candidates": [{"provider": "broken", "model": model}]}}
    with pytest.raises(ProviderUnavailable):
        await resolve_model_role(["coding"], matrix, session.coordinator.get("providers"), coordinator=session.coordinator)
    matrix["coding"]["candidates"].append({"provider": "fixture", "model": "model-v1"})
    selected = await resolve_model_role(["coding"], matrix, session.coordinator.get("providers"), coordinator=session.coordinator)
    assert selected[0]["provider"] == "fixture"


async def test_native_preflight_refuses_loader_for_missing_secret():
    policy = ProviderIsolation()
    row = {"module": "provider-broken", "instance_id": "account", "config": {"priority": 1}}
    policy.record(row, "provider_configuration_failed")
    session = AmplifierSession({"session": {"orchestrator": "probe", "context": "probe"}, "providers": [row]})
    async def load(module, *args, **kwargs):
        assert module != "provider-broken", "Invalid configuration reached provider load"
        return AsyncMock(return_value=None)
    loader = MagicMock(spec=ModuleLoader)
    loader.load = AsyncMock(side_effect=load)
    loader.get_on_session_ready_queue.return_value = []
    session.coordinator.loader = loader
    await policy.before_initialize(session)
    await session.initialize()
    with pytest.raises(ProviderUnavailable):
        require_available(session.coordinator, "account")
    await session.cleanup()


async def test_real_foundation_prepare_keeps_failed_provider_but_not_failed_tool(tmp_path, monkeypatch):
    from amplifier_foundation import Bundle
    policy = ProviderIsolation()
    rows = [{"module": "provider-good", "source": "good", "instance_id": "good"},
            {"module": "provider-bad", "source": "bad", "instance_id": "bad"}]
    class Activator:
        bundle_package_paths = []
        def __init__(self, **kwargs):
            assert kwargs["strict"] and not kwargs["install_deps"]
        async def activate_all(self, rows, **kwargs):
            if rows:
                raise ValueError("non-provider source failed")
            return {}
        async def activate(self, module, source, **kwargs):
            if source == "bad":
                raise ValueError("private source credential must not escape")
            return tmp_path
        def finalize(self):
            pass
    monkeypatch.setattr("amplifier_foundation.modules.activator.ModuleActivator", Activator)
    bundle = Bundle(name="isolation", providers=copy.deepcopy(rows))
    prepared = await bundle.prepare(strict=True, install_deps=False,
                                    provider_failure_policy=policy.preparation_failed)
    assert bundle.providers == rows and prepared.mount_plan["providers"] == rows
    assert len(prepared.provider_preparation_failures) == 1
    assert policy.failure(rows[1])["reason_code"] == "provider_preparation_failed"
    assert "private" not in str(policy.failure(rows[1]))
    bundle.tools = [{"module": "tool-bad", "source": "bad"}]
    with pytest.raises(ValueError, match="non-provider source failed"):
        await bundle.prepare(strict=True, install_deps=False,
                             provider_failure_policy=policy.preparation_failed)


async def test_real_child_resume_preserves_saved_account_and_history(tmp_path, monkeypatch):
    """Foundation creates real Core children; only the inference loop is finite."""
    import importlib.util
    from dataclasses import dataclass
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.session import SelectedProvider
    from amplifier_web.provider_environment import materialize_bundle_providers

    package = tmp_path / "amplifier_module_provider_isolation_probe"
    package.mkdir()
    (package / "__init__.py").write_text('''
from amplifier_core import ProviderInfo
class ProbeProvider:
    name = "isolation-probe"
    def __init__(self, config=None):
        self.config = config or {}
        self.priority = self.config.get("priority", 100)
    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Probe", defaults={"model": "model-v1"},
            config_fields=[{"id": "api_key", "display_name": "Key", "prompt": "Key", "field_type": "secret", "required": True}])
    async def list_models(self):
        return []
    async def complete(self, *args, **kwargs):
        raise AssertionError("No inference permitted")
    def parse_tool_calls(self, response):
        return []
async def mount(coordinator, config=None):
    provider = ProbeProvider(config)
    await coordinator.mount("providers", provider, name=provider.name)
''')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    rows = [
        {"module": "provider-isolation-probe", "instance_id": "healthy", "config": {"api_key": "key", "priority": 20, "default_model": "model-v1"}},
        {"module": "provider-isolation-probe", "instance_id": "broken", "config": {"priority": 1, "default_model": "model-v1"}},
    ]
    repaired = copy.deepcopy(rows[1])
    repaired["config"]["api_key"] = "repaired-child-key"
    invalid_child = copy.deepcopy(rows[0])
    invalid_child["config"].pop("api_key")
    plan = {"session": {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}},
            "providers": rows, "agents": {"worker": {}, "broken-worker": {"providers": [rows[1]]},
                "repaired-worker": {"providers": [repaired]}, "invalid-child": {"providers": [invalid_child]}}}
    paths = {name: __import__('pathlib').Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent
             for name in ("loop-live", "context-simple")}
    paths["provider-isolation-probe"] = package
    calls, selected_keys = [], []
    class Finite(BundleLiveOrchestrator):
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            require_selected(coordinator)
            selected = self._select_provider(providers)
            calls.append((coordinator.session_id, getattr(selected, "selection", {}).get("instance")))
            selected_keys.append(getattr(selected, "original", selected).config["api_key"])
            await context.add_message({"role": "user", "content": prompt})
            await context.add_message({"role": "assistant", "content": "Finished"})
            return "Finished"
    @dataclass
    class Prepared(PreparedBundle):
        async def create_session(self, **kwargs):
            session = await super().create_session(**kwargs)
            await session.coordinator.mount("orchestrator", Finite({"inherit_effective_model": True}))
            return session
    bundle = Bundle.from_dict(plan, base_path=tmp_path)
    prepared = Prepared(copy.deepcopy(plan), BundleModuleResolver(paths), bundle)
    policy = ProviderIsolation()
    await materialize_bundle_providers(bundle, prepared, isolation=policy)
    approvals = Approvals(None, AsyncMock(return_value="deny"))
    parent = await prepared.create_session(session_id="parent", approval_system=approvals,
                                          before_initialize=policy.before_initialize)
    parent.coordinator.get("orchestrator").root_provider = SelectedProvider(
        parent.coordinator.get("providers")["healthy"], {"instance": "healthy", "model": "model-v1"})
    store = SessionStore(tmp_path / "sessions")
    registry = await install_children(parent, prepared, Runtime("parent"), store, approvals)
    result = await registry.spawn("worker", "first", parent, sub_session_id="child")
    assert result["output"] == "Finished" and calls == [("child", "healthy")]
    resumed = await registry.resume("child", "second", parent)
    assert resumed["output"] == "Finished" and calls == [("child", "healthy"), ("child", "healthy")]
    saved, metadata = store.load("child")
    assert metadata["effective_selection"]["instance"] == "healthy"
    assert [row["content"] for row in saved] == ["first", "Finished", "second", "Finished"]
    store.save("blocked", saved, {**metadata, "session_id": "blocked", "effective_selection": {"instance": "broken", "model": "model-v1"}})
    before = {name: (store.directory("blocked") / name).read_bytes() for name in ("transcript.jsonl", "metadata.json")}
    with pytest.raises(ProviderUnavailable):
        await registry.resume("blocked", "never execute", parent)
    assert len(calls) == 2
    assert {name: (store.directory("blocked") / name).read_bytes() for name in before} == before
    with pytest.raises(ProviderUnavailable):
        await registry.spawn("broken-worker", "never execute", parent, sub_session_id="denied")
    assert len(calls) == 2
    with pytest.raises(ProviderUnavailable):
        await registry.spawn("invalid-child", "never execute", parent, sub_session_id="child-invalid")
    assert len(calls) == 2  # A healthy parent cannot grant an invalid child account.
    result = await registry.spawn("repaired-worker", "explicitly repaired", parent, sub_session_id="repaired")
    assert result["output"] == "Finished" and selected_keys == ["key", "key", "repaired-child-key"]
    await parent.cleanup()
