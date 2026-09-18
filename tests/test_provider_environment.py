import asyncio
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