import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.provider_environment import (
    HOST_CREDENTIAL,
    credential_bindings,
    materialize_bundle_providers,
    materialize_provider_config,
    rebind_provider_credentials,
)
from test_host_session_resume import mounted_host


SCHEMA = {"fields": [
    {"id": "api_key", "field_type": "secret", "required": True},
    {"id": "base_url", "field_type": "text", "required": False},
]}


def test_optional_exact_reference_blanks_only_declared_nonsecret_and_never_mutates_input():
    source = {
        "api_key": "${PROVIDER_KEY}",
        "base_url": "${OPTIONAL_BASE_URL}",
        "literal": "https://configured.test",
        "fallback": "${OPTIONAL_FALLBACK:-https://fallback.test}",
    }
    result = materialize_provider_config(source, SCHEMA, environment={"PROVIDER_KEY": "key"})
    assert result == {
        "api_key": "key",
        "base_url": "",
        "literal": "https://configured.test",
        "fallback": "https://fallback.test",
    }
    assert source["base_url"] == "${OPTIONAL_BASE_URL}"


@pytest.mark.parametrize(("config", "identifier"), [
    ({"api_key": "${MISSING_KEY}"}, "MISSING_KEY"),
    ({"api_key": "${EMPTY_KEY}"}, "provider_environment.required_secret:api_key"),
])
def test_required_secret_reference_fails_without_using_unrelated_ambient_credentials(config, identifier):
    environment = {"OPENAI_API_KEY": "unrelated", "EMPTY_KEY": ""}
    with pytest.raises(ValueError, match=identifier) as error:
        materialize_provider_config(config, SCHEMA, environment=environment)
    assert "unrelated" not in str(error.value)


def test_unknown_and_embedded_references_remain_strict():
    with pytest.raises(ValueError, match="UNKNOWN"):
        materialize_provider_config({"api_key": "key", "unknown": "${UNKNOWN}"}, SCHEMA, environment={})
    with pytest.raises(ValueError, match="OPTIONAL_BASE_URL"):
        materialize_provider_config(
            {"api_key": "key", "base_url": "prefix-${OPTIONAL_BASE_URL}"},
            SCHEMA,
            environment={},
        )


@pytest.mark.asyncio
async def test_root_and_nested_agents_materialize_together_and_disabled_provider_is_untouched():
    bundle = SimpleNamespace(
        providers=[
            {"module": "provider-test", "config": {"api_key": "${KEY}", "base_url": "${ROOT_URL}"}},
            {"module": "provider-disabled", "enabled": False, "config": {"api_key": "${MISSING}"}},
        ],
        agents={"worker": {"providers": [
            {"module": "provider-test", "config": {"api_key": "${KEY}", "base_url": "${CHILD_URL}"}},
        ]}},
    )
    prepared = SimpleNamespace(mount_plan={})
    calls = []

    async def schema_loader(module):
        calls.append(module)
        return SCHEMA

    await materialize_bundle_providers(
        bundle, prepared, environment={"KEY": "key"}, schema_loader=schema_loader
    )
    assert calls == ["provider-test"]
    assert bundle.providers[0]["config"]["base_url"] == ""
    assert bundle.agents["worker"]["providers"][0]["config"]["base_url"] == ""
    assert prepared.mount_plan["providers"] == bundle.providers
    assert prepared.mount_plan["agents"] == bundle.agents
    assert prepared.mount_plan["providers"] is not bundle.providers
    assert bundle.providers[1]["config"]["api_key"] == "${MISSING}"


@pytest.mark.asyncio
async def test_distinct_provider_schemas_load_concurrently():
    bundle = SimpleNamespace(
        providers=[
            {"module": "provider-first", "config": {"api_key": "first"}},
            {"module": "provider-second", "config": {"api_key": "second"}},
        ],
        agents={},
    )
    prepared = SimpleNamespace(mount_plan={})
    started = []
    ready = asyncio.Event()

    async def schema_loader(module):
        started.append(module)
        if len(started) == 2:
            ready.set()
        await ready.wait()
        return SCHEMA

    await asyncio.wait_for(
        materialize_bundle_providers(bundle, prepared, schema_loader=schema_loader), 1
    )
    assert started == ["provider-first", "provider-second"]


@pytest.mark.asyncio
async def test_endpoint_constructor_metadata_is_bound_to_each_instance_without_credentials(monkeypatch):
    from amplifier_web import provider_environment as environment

    constructed, closed = [], []
    class EndpointProvider:
        def __init__(self, base_url=None, *, api_key=None, config=None):
            assert api_key is None and config == {}
            if base_url is None:
                raise ValueError("base_url or client must be provided for API calls")
            self.endpoint = base_url
            constructed.append(base_url)

        def get_info(self):
            return {"config_fields": [{"id": "base_url", "field_type": "text", "required": True}]}

        async def list_models(self):
            raise AssertionError("Metadata must never query models")

        async def close(self):
            closed.append(self.endpoint)

    monkeypatch.setattr(environment, "provider_class", lambda module: EndpointProvider)
    first, second = "https://first.test/v1/", "https://second.test/v1/?path=%2F"
    bundle = SimpleNamespace(providers=[
        {"module": "provider-endpoint", "instance_id": "first", "config": {"base_url": "${FIRST_ENDPOINT}", "api_key": "private-first"}},
        {"module": "provider-endpoint", "instance_id": "second", "config": {"base_url": second, "api_key": "private-second"}},
    ], agents={"worker": {"providers": [
        {"module": "provider-endpoint", "instance_id": "first", "config": {"base_url": "${FIRST_ENDPOINT}"}},
    ]}})
    prepared = SimpleNamespace(mount_plan={})
    await materialize_bundle_providers(bundle, prepared, environment={"FIRST_ENDPOINT": first})
    assert constructed == closed == [first, second]
    assert [row["instance_id"] for row in bundle.providers] == ["first", "second"]
    assert [row["config"]["base_url"] for row in bundle.providers] == [first, second]
    assert prepared.mount_plan["agents"]["worker"]["providers"][0]["config"]["base_url"] == first


@pytest.mark.asyncio
async def test_failed_preflight_is_atomic_and_preserves_named_account_priority_and_role_preferences():
    from amplifier_web.module_failures import ConfiguredModuleError

    bundle = SimpleNamespace(providers=[
        {"module": "provider-healthy", "instance_id": "healthy", "config": {"base_url": "${HEALTHY_ENDPOINT}", "priority": 20}},
        {"module": "provider-broken", "instance_id": "named-account", "config": {"priority": 1}},
    ], agents={"worker": {"model_role": "reasoning", "provider_preferences": [{"provider": "named-account", "model": "exact-model"}],
                           "providers": [{"module": "provider-healthy", "config": {"base_url": "${HEALTHY_ENDPOINT}"}}]}})
    prepared = SimpleNamespace(mount_plan={"providers": [{"module": "existing-plan"}], "agents": {"existing": {}}})
    original, original_plan = copy.deepcopy(vars(bundle)), copy.deepcopy(prepared.mount_plan)

    async def schema_loader(module):
        if module == "provider-broken":
            return {"fields": [{"id": "api_key", "field_type": "secret", "required": True}]}
        return {"fields": []}

    with pytest.raises(ConfiguredModuleError) as failure:
        await materialize_bundle_providers(bundle, prepared, schema_loader=schema_loader,
                                           environment={"HEALTHY_ENDPOINT": "https://healthy.test"})
    assert vars(bundle) == original and prepared.mount_plan == original_plan
    assert failure.value.failures[0]["instance_id"] == "named-account"
    assert failure.value.failures[0]["reason_code"] == "provider_configuration_failed"
    assert "required credentials" in str(failure.value)


@pytest.mark.asyncio
async def test_nested_failure_leaves_successful_root_and_prepared_plan_unchanged():
    from amplifier_web.module_failures import ConfiguredModuleError

    bundle = SimpleNamespace(providers=[{"module": "provider-good", "config": {"base_url": "${ENDPOINT}"}}],
        agents={"worker": {"providers": [{"module": "provider-bad", "instance_id": "child-account", "config": {}}]}})
    prepared = SimpleNamespace(mount_plan={"unchanged": True})
    original = copy.deepcopy(vars(bundle))
    async def schema_loader(module):
        return {"fields": []} if module == "provider-good" else {"fields": [{"id": "key", "required": True, "field_type": "secret"}]}
    with pytest.raises(ConfiguredModuleError) as failure:
        await materialize_bundle_providers(bundle, prepared, environment={"ENDPOINT": "https://healthy.test"}, schema_loader=schema_loader)
    assert failure.value.failures[0]["instance_id"] == "child-account"
    assert vars(bundle) == original and prepared.mount_plan == {"unchanged": True}


@pytest.mark.asyncio
async def test_schema_errors_are_collected_per_instance_without_exposing_exception_or_credentials():
    from amplifier_web.module_failures import ConfiguredModuleError

    bundle = SimpleNamespace(providers=[
        {"module": "provider-broken", "instance_id": "one", "config": {"api_key": "synthetic-secret"}},
        {"module": "provider-broken", "instance_id": "two", "config": {}},
    ], agents={})
    async def schema_loader(module):
        raise ValueError("synthetic-secret https://account:credential@private.invalid/config")

    with pytest.raises(ConfiguredModuleError) as failure:
        await materialize_bundle_providers(bundle, SimpleNamespace(mount_plan={}), schema_loader=schema_loader)
    assert [row["instance_id"] for row in failure.value.failures] == ["one", "two"]
    assert {row["reason_code"] for row in failure.value.failures} == {"provider_schema_failed"}
    assert "synthetic-secret" not in str(failure.value) + repr(failure.value.failures)
    assert "private.invalid" not in str(failure.value) + repr(failure.value.failures)
    assert failure.value.__cause__ is None


@pytest.mark.parametrize("endpoint", [None, "${MISSING_ENDPOINT}"])
@pytest.mark.asyncio
async def test_missing_endpoint_never_borrows_another_instance_or_ambient_default(monkeypatch, endpoint):
    from amplifier_web import provider_environment as environment
    from amplifier_web.module_failures import ConfiguredModuleError

    class EndpointProvider:
        def __init__(self, base_url=None, *, config=None):
            if base_url is None:
                raise ValueError("endpoint required")
        def get_info(self):
            return {"config_fields": []}
    monkeypatch.setattr(environment, "provider_class", lambda module: EndpointProvider)
    bundle = SimpleNamespace(providers=[
        {"module": "provider-endpoint", "instance_id": "healthy", "config": {"base_url": "https://healthy.test"}},
        {"module": "provider-endpoint", "instance_id": "missing", "config": {"base_url": endpoint}},
    ], agents={})
    with pytest.raises(ConfiguredModuleError) as failure:
        await materialize_bundle_providers(bundle, SimpleNamespace(mount_plan={}), environment={"VLLM_BASE_URL": "https://ambient.test"})
    assert len(failure.value.failures) == 1
    assert failure.value.failures[0]["instance_id"] == "missing"


@pytest.mark.asyncio
async def test_cancelled_schema_preflight_is_not_converted_to_configuration_error():
    async def schema_loader(module):
        raise asyncio.CancelledError()
    bundle = SimpleNamespace(providers=[{"module": "provider-test", "config": {}}], agents={})
    with pytest.raises(asyncio.CancelledError):
        await materialize_bundle_providers(bundle, SimpleNamespace(mount_plan={}), schema_loader=schema_loader)


def test_missing_schema_fails_loudly():
    with pytest.raises(ValueError, match="provider_environment.unsupported_schema"):
        materialize_provider_config({"api_key": "key"}, {})


@pytest.mark.parametrize("field", [
    {"id": "endpoint", "field_type": "future-or-invalid", "required": False},
    {"id": "endpoint", "field_type": "", "required": False},
    {"id": "endpoint", "field_type": [], "required": False},
    {"id": "", "field_type": "text", "required": False},
])
def test_unknown_schema_cannot_authorize_empty_optional_reference(field):
    with pytest.raises(ValueError, match="provider_environment.unsupported_schema"):
        materialize_provider_config({"endpoint": "${MISSING}"}, {"fields": [field]}, environment={})


def binding_plan():
    """Same family and identity in several scopes, with distinct source keys."""
    def row(identity, source, model, priority, credential):
        return {"module": "provider-anthropic", "instance_id": identity, "source": source,
                "config": {"api_key": credential, "model": model,
                           "reasoning_effort": "high", "priority": priority}}
    source = "git+https://example.invalid/provider-anthropic@root"
    return {
        "session": {"orchestrator": {"module": "loop-live"},
                    "context": {"module": "context-simple"}},
        "providers": [
            row("fable", source, "exact-default", 1, "synthetic-root-key"),
            row("alternate", "git+https://example.invalid/provider-anthropic@alternate",
                "exact-alternate", 20, "synthetic-alternate-key"),
        ],
        "tools": [], "hooks": [],
        "agents": {"worker": {
            "model_role": "reasoning",
            "provider_preferences": [{"provider": "fable", "model": "exact-worker"}],
            "providers": [row("fable", source, "exact-worker", 3, "synthetic-worker-key")],
            "agents": {"deep": {
                "providers": [row("fable", source, "exact-deep", 4, "synthetic-deep-key")],
            }},
        }},
    }


def test_credential_bindings_retains_exact_envrefs_and_only_rebinds_provider_literals():
    original = binding_plan()
    original["providers"][0]["config"]["api_key"] = "${EXACT_FABLE_KEY}"
    original["tools"] = [{"module": "tool-fixture", "config": {"password": "synthetic-tool-password"}}]
    before = copy.deepcopy(original)
    inherited = credential_bindings(original)
    assert original == before
    assert inherited["providers"][0]["config"]["api_key"] == "${EXACT_FABLE_KEY}"
    assert inherited["providers"][1]["config"]["api_key"] == HOST_CREDENTIAL
    assert inherited["agents"]["worker"]["providers"][0]["config"]["api_key"] == HOST_CREDENTIAL
    assert inherited["agents"]["worker"]["agents"]["deep"]["providers"][0]["config"]["api_key"] == HOST_CREDENTIAL
    assert inherited["tools"][0]["config"] == {}
    assert "synthetic-" not in json.dumps(inherited)
    resolved, bound = rebind_provider_credentials(inherited, original)
    assert bound
    assert resolved["providers"] == original["providers"]
    assert resolved["agents"] == original["agents"]
    assert inherited["providers"][1]["config"]["api_key"] == HOST_CREDENTIAL


@pytest.fixture
def credential_host(mounted_host, monkeypatch):
    """Reuse offline host infrastructure, but run real binding/schema preparation."""
    from amplifier_web.host import session as host
    from amplifier_web import provider_environment

    h = mounted_host
    h.schema_calls, h.prepare_calls = [], []
    h.runtime.generation, h.runtime.queued_inputs = None, 0
    coordinator = h.session.coordinator
    loop, context = coordinator.get("orchestrator"), coordinator.get("context")
    loop.root_provider, loop.max_iterations, context.max_tokens = None, 10, None
    providers = {}
    coordinator.get = lambda key: {"providers": providers, "orchestrator": loop,
                                   "context": context, "tools": {}}.get(key)
    coordinator.session_state = {}
    loop._select_provider = lambda mounted: loop.root_provider or min(
        mounted.values(), key=lambda provider: provider.priority)
    from amplifier_foundation import SessionConfigurator
    SessionConfigurator(h.session, h.prepared).snapshot = lambda: {}

    class Info(SimpleNamespace):
        def model_copy(self, *, update):
            return Info(**{**vars(self), **update})

    class SchemaProvider:
        def __init__(self, *, api_key=None, config=None):
            assert api_key is None and config == {}  # Metadata receives no host secret.

        def get_info(self):
            return {"config_fields": SCHEMA["fields"]}

    def schema_class(module):
        h.schema_calls.append(module)
        return SchemaProvider

    class Root:
        def __init__(self, plan):
            for key, value in copy.deepcopy(plan).items():
                setattr(self, key, value)

        def to_mount_plan(self):
            return copy.deepcopy({key: getattr(self, key)
                                  for key in ("session", "providers", "tools", "hooks", "agents")})

        async def prepare(self, **kwargs):
            h.prepare_calls.append(kwargs)
            h.before_materialization = self.to_mount_plan()
            h.prepared.mount_plan = self.to_mount_plan()
            return h.prepared

    async def mount(**kwargs):
        h.session.session_id = kwargs["session_id"]
        coordinator.config = h.session.config = copy.deepcopy(h.prepared.mount_plan)
        providers.clear()
        for row in h.prepared.mount_plan["providers"]:
            config = row["config"]
            assert isinstance(config["api_key"], str) and config["api_key"]
            info = Info(id="fixture", defaults={"model": config["model"],
                        "reasoning_effort": config["reasoning_effort"]},
                        config_fields=SCHEMA["fields"])
            providers[row["instance_id"]] = SimpleNamespace(
                get_info=lambda info=info: info, priority=config["priority"],
                complete=AsyncMock(return_value="offline result"))
        return h.session

    def load(plan):
        root = Root(plan)
        h.loaded = root
        h.registry.load.return_value = root
        host.compose_configured_bundle.return_value = root
        return root

    h.load, h.providers, h.loop, h.Info = load, providers, loop, Info
    h.prepared.create_session.side_effect = mount
    monkeypatch.setattr(provider_environment, "provider_class", schema_class)
    monkeypatch.setattr(host, "materialize_bundle_providers", materialize_bundle_providers)
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_kind", ["literal", "envref"])
@pytest.mark.parametrize("selection", [None, {
    "instance": "fable", "model": "exact-pinned-model", "effort": "xhigh",
}])
async def test_bound_child_prepares_exact_named_sources_defaults_and_nested_keys(
        credential_host, monkeypatch, credential_kind, selection):
    from amplifier_web.host import session as host
    from amplifier_web.provider_environment import iter_provider_rows
    from amplifier_web.runtime_controls import RuntimeControls, override_path

    h = credential_host
    authorized = binding_plan()
    expected_keys = [row["config"]["api_key"] for row in iter_provider_rows(authorized)]
    if credential_kind == "envref":
        for index, row in enumerate(iter_provider_rows(authorized)):
            monkeypatch.setenv(f"EXACT_INSTANCE_KEY_{index}", expected_keys[index])
            row["config"]["api_key"] = "${EXACT_INSTANCE_KEY_" + str(index) + "}"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-unrelated-ambient")
    original = copy.deepcopy(authorized)
    child = credential_bindings(authorized)
    child["providers"].reverse()  # Match identity/source, never list position.
    path = override_path(h.runtime.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(child))
    if selection:
        path.with_name("control-state.json").write_text(json.dumps({"selection": selection}))
    snapshot = path.read_bytes()
    h.load(authorized)
    session, runtime, report = await host.prepare_manager(
        h.config.workspace, runtime=h.runtime, qualification_readonly=True, selection=selection)
    assert h.prepare_calls[0]["strict"] and not h.prepare_calls[0]["install_deps"]
    h.prepared.create_session.assert_awaited_once()
    assert h.schema_calls == ["provider-anthropic"]
    assert report["providers"] == ["alternate", "fable"]
    if selection:
        assert report["effective_selection"] == selection
    else:
        assert report["effective_selection"]["id"] == "fable"
    assert [row["config"]["api_key"] for row in h.prepared.mount_plan["providers"]] == expected_keys[1::-1]
    worker = h.prepared.mount_plan["agents"]["worker"]
    assert worker["providers"][0]["config"]["api_key"] == expected_keys[2]
    assert worker["agents"]["deep"]["providers"][0]["config"]["api_key"] == expected_keys[3]
    assert worker["model_role"] == original["agents"]["worker"]["model_role"]
    assert worker["provider_preferences"] == original["agents"]["worker"]["provider_preferences"]
    for mounted, saved in zip(h.prepared.mount_plan["providers"], reversed(original["providers"])):
        assert {key: value for key, value in mounted.items() if key != "config"} == {
            key: value for key, value in saved.items() if key != "config"}
        assert {key: value for key, value in mounted["config"].items() if key != "api_key"} == {
            key: value for key, value in saved["config"].items() if key != "api_key"}
    if credential_kind == "envref":
        assert h.before_materialization["providers"] == child["providers"]
    assert path.read_bytes() == snapshot and authorized == original
    assert all(key not in snapshot.decode() for key in expected_keys)
    controls = RuntimeControls(session, runtime)
    try:
        await controls.restore()
        controls.persist()
        effective = controls.state_path().with_name("effective-configuration.json").read_text()
        assert all(key not in effective for key in expected_keys)
        assert "synthetic-unrelated-ambient" not in effective
        current = await controls.perform("configuration.providers")
        assert current["selection"] == selection and current["pinned"] is bool(selection)
        assert current["effective"] == (selection or {
            "instance": "fable", "model": "exact-default", "effort": "high"})
        if selection:
            assert h.loop.root_provider.original is h.providers["fable"]
            await h.loop.root_provider.complete(h.Info(model=None, reasoning_effort=None))
            call = h.providers["fable"].complete.call_args
            assert call.args[0].model == selection["model"]
            assert call.args[0].reasoning_effort == selection["effort"]
            assert h.providers["fable"].get_info().defaults["model"] == "exact-default"
    finally:
        await controls.close()
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "disabled", "ambiguous", "different-source",
                                 "different-instance", "different-module", "missing-field", "missing-scope"])
async def test_bound_child_refuses_unauthorized_credentials_before_prepare_without_fallback(
        credential_host, monkeypatch, case):
    from amplifier_web.host import session as host

    h = credential_host
    authorized = binding_plan()
    child = credential_bindings(authorized)
    if case == "missing":
        authorized["providers"].pop(0)
    elif case == "disabled":
        authorized["providers"][0]["enabled"] = False
    elif case == "ambiguous":
        authorized["providers"].append(copy.deepcopy(authorized["providers"][0]))
    elif case == "different-source":
        authorized["providers"][0]["source"] += "-changed"
    elif case == "different-instance":
        authorized["providers"][0]["instance_id"] = "renamed"
    elif case == "different-module":
        authorized["providers"][0]["module"] = "provider-other"
    elif case == "missing-field":
        del authorized["providers"][0]["config"]["api_key"]
    else:
        del authorized["agents"]["worker"]
    before = copy.deepcopy(child)
    root = h.load(authorized)
    original_root = root.to_mount_plan()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-ambient-fallback")
    with pytest.raises(ValueError, match="authorized host credential binding") as failure:
        await host.prepare_manager(h.config.workspace, runtime=h.runtime,
                                   runtime_plan=child, qualification_readonly=True)
    assert child == before and root.to_mount_plan() == original_root
    assert h.prepare_calls == [] and h.schema_calls == []
    h.prepared.create_session.assert_not_awaited()
    assert "synthetic-" not in str(failure.value)
    assert not h.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty-literal", "missing-envref", "empty-envref"])
async def test_authorized_binding_does_not_relax_required_secret_schema(
        credential_host, monkeypatch, case):
    from amplifier_web.host import session as host
    from amplifier_web.module_failures import ConfiguredModuleError

    h = credential_host
    authorized = binding_plan()
    authorized["providers"][0]["config"]["api_key"] = "" if case == "empty-literal" else "${EXACT_MISSING_KEY}"
    monkeypatch.delenv("EXACT_MISSING_KEY", raising=False)
    if case == "empty-envref":
        monkeypatch.setenv("EXACT_MISSING_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-ambient-fallback")
    h.load(authorized)
    child = credential_bindings(authorized)
    with pytest.raises(ConfiguredModuleError) as failure:
        await host.prepare_manager(h.config.workspace, runtime=h.runtime,
                                   runtime_plan=child, qualification_readonly=True)
    assert [(row["instance_id"], row["reason_code"]) for row in failure.value.failures] == [
        ("fable", "provider_configuration_failed")]
    h.prepared.create_session.assert_not_awaited()
    assert len(h.prepare_calls) == 1
    assert "synthetic-" not in str(failure.value)
    assert not h.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("edited", [False, True])
async def test_nonbound_runtime_plan_and_ordinary_default_prepare_compatibility(credential_host, edited):
    from amplifier_web.host import session as host

    h = credential_host
    authorized = binding_plan()
    child = copy.deepcopy(authorized)
    child["providers"][0]["config"].update(model="reviewed-model", reasoning_effort="low")
    root = h.load(authorized)
    session, _, report = await host.prepare_manager(
        h.config.workspace, runtime=h.runtime, qualification_readonly=True,
        **({"runtime_plan": child} if edited else {}))
    h.prepared.create_session.assert_awaited_once()
    assert not h.capabilities["web.provider_credentials_bound"]
    assert report["effective_selection"]["id"] == "fable"
    assert report["effective_selection"]["model"] == ("reviewed-model" if edited else "exact-default")
    assert root.providers[0]["config"]["api_key"] == authorized["providers"][0]["config"]["api_key"]
    assert root.providers[0]["config"]["priority"] == 1
    assert root.agents == authorized["agents"]
    session.execute.assert_not_awaited()
