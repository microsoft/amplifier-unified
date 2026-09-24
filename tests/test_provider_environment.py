import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.provider_environment import (
    materialize_bundle_providers,
    materialize_provider_config,
)


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
