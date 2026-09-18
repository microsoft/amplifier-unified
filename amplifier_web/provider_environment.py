"""Strict, schema-aware environment materialization for provider configs."""
from __future__ import annotations

import asyncio
import copy
import importlib
import importlib.metadata
import inspect
import re

from .host.config import expand_environment

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")


def provider_class(module_id):
    """Find the public provider class after its source has been prepared."""
    module = None
    for entry in importlib.metadata.entry_points(group="amplifier.modules"):
        if entry.name == module_id:
            loaded = entry.load()
            module = importlib.import_module(loaded.__module__)
            break
    if module is None:
        module = importlib.import_module("amplifier_module_" + module_id.replace("-", "_"))
    candidates = [getattr(module, name) for name in dir(module)
                  if name.endswith("Provider") and not name.startswith("_")]
    candidates = [candidate for candidate in candidates
                  if inspect.isclass(candidate) and callable(getattr(candidate, "get_info", None))
                  and not getattr(candidate, "_is_protocol", False)]
    if not candidates:
        raise ValueError("No public provider setup class")
    return candidates[0]


def construct_provider(cls, config):
    """Use the established provider-probe constructor contract."""
    parameters = inspect.signature(cls).parameters
    kwargs = {key: config.get(key) for key in ("api_key", "github_token") if key in parameters}
    if "config" in parameters:
        kwargs["config"] = config
    return cls(**kwargs)


async def close_provider(provider):
    close = getattr(provider, "close", None) or getattr(provider, "aclose", None)
    if callable(close):
        try:
            value = close()
            if inspect.isawaitable(value):
                await asyncio.wait_for(value, 3)
        except Exception:
            pass


async def config_schema(provider, *, info=None):
    """Read the public schema without making a model-list or inference call."""
    if info is None:
        info = provider.get_info()
        if inspect.isawaitable(info):
            info = await info
    schema_method = getattr(provider, "get_config_schema", None)
    schema = schema_method() if callable(schema_method) else {"fields": _value(info).get("config_fields", [])}
    if inspect.isawaitable(schema):
        schema = await schema
    return _value(schema)


async def provider_config_schema(module_id):
    """Construct a schema-only provider with ``config={}``, then close it."""
    provider = construct_provider(provider_class(module_id), {})
    try:
        return await config_schema(provider)
    finally:
        await close_provider(provider)


def materialize_provider_config(config, schema, *, environment=None):
    """Expand provider config strictly, blanking only declared optional nonsecrets."""
    if not isinstance(config, dict):
        raise ValueError("Provider configuration must be a mapping")
    fields = _schema_fields(schema)
    result = copy.deepcopy(config)
    values = environment
    for name, value in result.items():
        match = _ENV_REFERENCE.fullmatch(value) if isinstance(value, str) else None
        field = fields.get(name)
        if (match and field and field["required"] is False and field["field_type"] != "secret"
                and _environment_value(values, match.group(1)) is None):
            result[name] = ""
        else:
            result[name] = expand_environment(value, environment=values)
    for field in fields.values():
        if field["required"] is True and field["field_type"] == "secret" and not result.get(field["id"]):
            raise ValueError("provider_environment.required_secret:" + field["id"])
    return result


async def materialize_bundle_providers(bundle, prepared, *, environment=None, schema_loader=provider_config_schema):
    """Synchronize materialized root and agent provider plans before any mount."""
    root = {"providers": bundle.providers, "agents": bundle.agents}
    rows = [row for row in iter_provider_rows(root) if row.get("enabled", True)]
    modules = dict.fromkeys(row["module"] for row in rows)
    schemas = dict(zip(modules, await asyncio.gather(*(schema_loader(module) for module in modules))))
    for row in rows:
        row["config"] = materialize_provider_config(row.get("config", {}), schemas[row["module"]], environment=environment)
    prepared.mount_plan["providers"] = copy.deepcopy(bundle.providers)
    prepared.mount_plan["agents"] = copy.deepcopy(bundle.agents)
    return prepared


def iter_provider_rows(node):
    """Yield root and nested provider declarations, validating their container shape."""
    if not isinstance(node, dict):
        raise ValueError("Provider declarations must be a mapping")
    rows = node.get("providers", [])
    if not isinstance(rows, list):
        raise ValueError("Provider declarations must be a list")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("module"), str):
            raise ValueError("Provider declarations need a module identifier")
        yield row
    agents = node.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("Agent declarations must be a mapping")
    for agent in agents.values():
        if isinstance(agent, dict):
            yield from iter_provider_rows(agent)


def _value(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    return value


def _schema_fields(schema):
    schema = _value(schema)
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        raise ValueError("provider_environment.unsupported_schema")
    fields = {}
    for field in schema["fields"]:
        if not isinstance(field, dict) or not isinstance(field.get("id"), str) or not field["id"]:
            raise ValueError("provider_environment.unsupported_schema")
        field_type = field.get("field_type")
        required = field.get("required")
        if (field_type not in ("text", "secret", "choice", "boolean")
                or not isinstance(required, bool) or field["id"] in fields):
            raise ValueError("provider_environment.unsupported_schema")
        fields[field["id"]] = field
    return fields


def _environment_value(environment, name):
    import os
    return (os.environ if environment is None else environment).get(name)