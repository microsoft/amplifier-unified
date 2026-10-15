"""Model inheritance policy independent of the runtime implementation."""
import copy


async def prepare_default_routing(session):
    """Keep declared pins intact; resolve only the requested child at spawn."""
    session.coordinator.register_capability("routing.defer_agent_resolution", True)
    session.coordinator.register_capability("web.default_routing_policy", True)


async def child_preferences(coordinator, overlay, requested):
    """Explicit selections outrank role defaults, including direct spawn calls."""
    if requested:
        return requested
    if overlay.get("provider_preferences"):
        return copy.deepcopy(overlay["provider_preferences"])
    if overlay.get("providers") or not overlay.get("model_role"):
        return []
    resolver = coordinator.get_capability("model_role_resolver")
    if resolver is None:
        raise ProviderSelectionError("The requested child model role has no mounted resolver.")
    resolved = await resolver.resolve(overlay["model_role"])
    if not resolved:
        raise ProviderSelectionError("The requested child model role has no available candidates; no default was substituted.")
    return resolved


def pinned_delegate_input(coordinator, supplied):
    """Do not let a default call-role resolver overwrite a named agent pin."""
    result = dict(supplied)
    if result.get("session_id") or result.get("provider_preferences"):
        return result
    agent = coordinator.config.get("agents", {}).get(result.get("agent"), {})
    if isinstance(agent, dict) and agent.get("provider_preferences"):
        result["provider_preferences"] = copy.deepcopy(agent["provider_preferences"])
    elif isinstance(agent, dict) and agent.get("providers"):
        # Provider declarations already specify this specialist's selection.
        result.pop("model_role", None)
    return result


def prioritize_agent_providers(plan, declared):
    """An explicit agent provider list takes precedence over inherited defaults."""
    plan = copy.deepcopy(plan)
    identity = lambda row: row.get("instance_id") or row.get("id") or row["module"]
    chosen = {identity(row) for row in declared}
    providers = plan.get("providers", [])
    selected = [row for row in providers if identity(row) in chosen]
    if not selected:
        raise ProviderSelectionError("The agent's declared provider is unavailable.")
    lowest = max(row.get("config", {}).get("priority", 100) for row in selected)
    for row in providers:
        if identity(row) not in chosen:
            config = row.setdefault("config", {})
            config["priority"] = max(config.get("priority", 100), lowest + 1)
    return plan


class ProviderSelectionError(ValueError):
    """A saved or requested connection needs an explicit user replacement."""


def inherited_selection(parent, overlay, preferences, saved=None):
    """Snapshot an opted-in parent's UI model choice unless a specialist wins."""
    if preferences or overlay.get("providers") or overlay.get("model_role"):
        return None
    if saved and saved.get("effective_selection"):
        return copy.deepcopy(saved["effective_selection"])
    loop = parent.coordinator.get("orchestrator")
    if not (getattr(loop, "config", {}) or {}).get("inherit_effective_model"):
        return None
    selected = getattr(loop, "root_provider", None)
    return copy.deepcopy(getattr(selected, "selection", None))

def delegation_routing(coordinator):
    """Describe mounted policy without resolving models or claiming a restriction.

    A resolver can deliberately select another mounted provider. This read-only
    report must not imply the root selector is an enforced billing boundary.
    Read optional provenance from the resolver that actually mounted, not files.
    """
    loop = coordinator.get("orchestrator")
    resolver = coordinator.get_capability("model_role_resolver")
    result = {
        "modelInheritance": "conversation_when_unspecified" if
            (getattr(loop, "config", {}) or {}).get("inherit_effective_model") else "bundle",
        "crossProviderRestriction": "not_enforced",
        "resolverActive": resolver is not None,
    }
    name = getattr(resolver, "name", None)
    if isinstance(name, str) and name:
        result["resolverName"] = name[:160]
    source = getattr(resolver, "matrix_source", None)
    if source in ("user", "bundle"):
        result["matrixSource"] = source
    return result


def child_routing(parent, overlay, preferences, selection):
    result = delegation_routing(parent.coordinator)
    result["selectionSource"] = (
        "delegation_preferences" if preferences else
        "agent_provider" if overlay.get("providers") else
        "agent_model_role" if overlay.get("model_role") else
        "inherited_conversation" if selection else "session_defaults"
    )
    return result


def public_routing(value):
    """Only known routing metadata crosses the public execution boundary."""
    if not isinstance(value, dict):
        return {}
    enums = {
        "modelInheritance": {"conversation_when_unspecified", "bundle"},
        "crossProviderRestriction": {"not_enforced"},
        "matrixSource": {"user", "bundle"},
        "selectionSource": {"delegation_preferences", "agent_provider", "agent_model_role",
                            "inherited_conversation", "session_defaults"},
    }
    result = {key: value[key] for key, choices in enums.items()
              if isinstance(value.get(key), str) and value[key] in choices}
    if isinstance(value.get("resolverActive"), bool):
        result["resolverActive"] = value["resolverActive"]
    if isinstance(value.get("resolverName"), str):
        result["resolverName"] = value["resolverName"][:160]
    return result
