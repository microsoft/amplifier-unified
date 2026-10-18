"""Host-owned per-instance outcomes; Core retains ownership of mount rollback."""
from __future__ import annotations

import copy
import importlib.metadata
import json
import math

from .module_failures import ConfiguredModuleError, safe_failures


def identity(spec):
    return spec.get("instance_id") or spec.get("id") or spec["module"].removeprefix("provider-")


def supported():
    # Core 2.0.2 publishes provider.before_load plus transactional mount rollback.
    # Older native wheels must retain the strict policy, not load invalid config.
    from packaging.version import Version
    return Version(importlib.metadata.version("amplifier-core")) >= Version("2.0.2")


class ProviderUnavailable(ConfiguredModuleError):
    """Typed, credential-free refusal of a particular configured connection."""


class UnavailableProvider:
    """Keep selection identity and priority, but never execute a failed account."""
    def __init__(self, spec, failure):
        self.name = spec["module"].removeprefix("provider-")
        self.instance_id = identity(spec)
        config = spec.get("config")
        config = config if isinstance(config, dict) else {}
        # A failed constructor cannot reveal a provider-specific implicit
        # priority. Unknown priority must block automatic selection rather than
        # invent an ordering which could choose a different account. Explicit
        # healthy selections bypass this marker normally.
        from .host.config import expand_environment
        try:
            priority = expand_environment(config["priority"])
            if isinstance(priority, bool) or not isinstance(priority, (int, float, str)):
                raise ValueError("Invalid priority")
            priority = float(priority)
            if not math.isfinite(priority):
                raise ValueError("Invalid priority")
        except (KeyError, ValueError, TypeError):
            priority = float("-inf")
        self.priority = priority
        self.default_model = config.get("default_model")
        self.failure = safe_failures([failure])[0]
        # No secret-bearing config survives in the marker.
        self.config = {"default_model": self.default_model}
        if math.isfinite(self.priority):
            self.config["priority"] = self.priority

    def get_info(self):
        from amplifier_core.models import ProviderInfo
        return ProviderInfo(id=self.name, display_name=self.instance_id,
                            defaults={"model": self.default_model})

    def unavailable(self):
        raise ProviderUnavailable([self.failure])

    async def list_models(self):
        self.unavailable()

    async def complete(self, *args, **kwargs):
        self.unavailable()

    async def stream(self, *args, **kwargs):
        self.unavailable()
        yield  # Preserve the streaming protocol without yielding data.

    def parse_tool_calls(self, response):
        self.unavailable()


def require_available(coordinator, instance):
    """Check exact runtime identity, including a frozen/inherited selection."""
    provider = (coordinator.get("providers") or {}).get(instance)
    if isinstance(provider, UnavailableProvider):
        provider.unavailable()
    if provider is None:
        from .host.model_selection import ProviderSelectionError
        raise ProviderSelectionError("The selected provider instance is unavailable; select a connection explicitly.")


def require_selected(coordinator):
    providers = coordinator.get("providers") or {}
    selector = getattr(coordinator.get("orchestrator"), "_select_provider", None)
    if not callable(selector):
        # Custom orchestrators own their selection policy. With failed accounts
        # present, do not claim isolation for an unqualified selector.
        for provider in providers.values():
            if isinstance(provider, UnavailableProvider):
                provider.unavailable()
        return
    selected = selector(providers)
    original = getattr(selected, "original", selected)
    if isinstance(original, UnavailableProvider):
        original.unavailable()


class ProviderIsolation:
    def __init__(self):
        self.failures = {}
        self.preparation = {}

    @staticmethod
    def key(spec):
        # Private exact configuration identity, never sent to diagnostics.
        return json.dumps(spec, sort_keys=True, default=str)

    def record(self, spec, reason, *, error=None, diagnostic=None):
        failure = {"module": spec["module"], "type": "provider", "instance_id": identity(spec),
                   "reason_code": reason}
        from .provider_failure_diagnostics import diagnose, safe_diagnostic
        stages = {'provider_preparation_failed': 'preparation', 'provider_schema_failed': 'schema',
                  'provider_configuration_failed': 'configuration', 'provider_mount_failed': 'mount'}
        if error is not None and reason in stages:
            diagnostic = diagnose(error, stage=stages[reason])
        diagnostic = safe_diagnostic(diagnostic)
        if diagnostic is not None:
            failure['diagnostic'] = diagnostic
        self.failures[self.key(spec)] = failure
        return failure

    async def preparation_failed(self, failure):
        spec = failure.provider_spec
        from .provider_failure_diagnostics import diagnose
        self.preparation[(spec["module"], spec.get("source"))] = diagnose(
            getattr(failure, 'error', None), stage='preparation')

    def failure(self, spec):
        diagnostic = self.preparation.get((spec["module"], spec.get("source")))
        return self.record(spec, 'provider_preparation_failed', diagnostic=diagnostic) if diagnostic else self.failures.get(self.key(spec))

    async def before_initialize(self, session):
        coordinator = session.coordinator
        async def before_load(_, spec):
            failure = self.failure(spec)
            if failure:
                raise ProviderUnavailable([failure])
        async def load_failed(_, spec, error):
            failure = self.failure(spec) or self.record(spec, "provider_mount_failed", error=error)
            await coordinator.mount("providers", UnavailableProvider(spec, failure), name=identity(spec))
        coordinator.register_capability("provider.before_load", before_load)
        coordinator.register_capability("provider.load_failure", load_failed)
        coordinator.register_capability("provider.check_available", lambda name: require_available(coordinator, name))
        coordinator.register_capability("web.provider_isolation", self)

    def outcomes(self, coordinator):
        return [copy.deepcopy(provider.failure) for provider in (coordinator.get("providers") or {}).values()
                if isinstance(provider, UnavailableProvider)]
