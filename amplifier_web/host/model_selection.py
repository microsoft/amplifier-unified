"""Model inheritance policy independent of the runtime implementation."""
import copy
import logging


def routing_hooks(plan):
    return [row for row in plan.get("hooks", []) if row.get("module") == "hooks-routing"]


def explicit_routing_settings(settings, hooks):
    """Read intent before defaults erase the distinction, including balanced."""
    identities = {"hooks-routing", *(row.get("id") or row.get("instance_id") for row in hooks)}
    return bool(
        settings.get("routing")
        or any(row.get("module") == "hooks-routing" or (row.get("id") or row.get("instance_id")) in identities - {None}
               for section in ("modules", "config") for row in settings.get(section, {}).get("hooks", []))
        or any(key in settings.get("overrides", {}) for key in identities - {None})
        or "hooks-routing" in settings.get("sources", {}).get("modules", {})
    )


async def prepare_default_routing(session, *, automatic=False):
    """Keep declared pins intact; resolve only the requested child at spawn."""
    session.coordinator.register_capability("routing.defer_agent_resolution", True)
    session.coordinator.register_capability("web.default_routing_policy", True)
    if automatic:
        # Only a host callback may create this capability. Neither an overlay
        # marker nor a saved effective plan can turn explicit routing into auto.
        session.coordinator.register_capability("web.default_routing_fallback", {
            "hooks": copy.deepcopy(routing_hooks(session.coordinator.config)),
        })


def automatic_default_routing(coordinator, overlay=None, *, saved=None):
    """The exception needs both host origin and the actual shipped matrix.

    Saved child provenance can only narrow current parent authority. Old/foreign
    metadata, a user file named balanced, changed hooks and unknown origins stay
    strict. A child overlay cannot grant this capability or erase explicit intent.
    """
    origin = coordinator.get_capability("web.default_routing_fallback")
    if not isinstance(origin, dict) or not origin.get("hooks"):
        return False
    hooks = routing_hooks(coordinator.config)
    if len(hooks) != 1 or hooks != origin["hooks"] or routing_hooks(overlay or {}):
        return False
    if saved is not None and saved.get("automatic_default_routing") is not True:
        return False
    resolver = coordinator.get_capability("model_role_resolver")
    return (getattr(resolver, "name", None) == "balanced"
            and getattr(resolver, "matrix_source", None) == "bundle"
            and not coordinator.get_capability("session.routing"))


async def child_preferences(coordinator, overlay, requested, *, saved=None):
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
    if (saved and saved.get("preferences_source") == "default_fallback"
            and saved.get("effective_selection")
            and overlay["model_role"] == saved.get("agent_overlay", {}).get("model_role")
            and automatic_default_routing(coordinator, overlay, saved=saved)):
        # Like saved resolved preferences, the fallback selection is a snapshot,
        # not permission to pick a different model when resuming this child.
        return []
    resolved = await resolver.resolve(overlay["model_role"])
    if not resolved:
        if isinstance(resolved, list) and automatic_default_routing(coordinator, overlay, saved=saved):
            roles = overlay["model_role"]
            known = getattr(resolver, "known_roles", ())
            requested_roles = [roles] if isinstance(roles, str) else roles
            reason = ("unknown_role" if known and not any(role in known for role in requested_roles)
                      else "no_available_candidate")
            logging.getLogger(__name__).debug("Using conversation selection for automatic routing: %s", reason)
            return []
        raise ProviderSelectionError("The requested child model role has no available candidates; no default was substituted.")
    return resolved


def pinned_delegate_input(coordinator, supplied, *, saved=None):
    """Do not let a default call-role resolver overwrite a named agent pin."""
    result = dict(supplied)
    if result.get("provider_preferences"):
        return result
    if result.get("session_id"):
        if saved is None:
            return result
        if saved.get("preferences_source", "explicit") == "explicit" and saved.get("provider_preferences"):
            result["provider_preferences"] = copy.deepcopy(saved["provider_preferences"])
            return result
        agent = saved.get("agent_overlay", {})
    else:
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


def inherited_selection(parent, overlay, preferences, saved=None, *, default_fallback=False):
    """Snapshot an opted-in parent's UI model choice unless a specialist wins."""
    if preferences or overlay.get("provider_preferences") or overlay.get("providers"):
        return None
    if overlay.get("model_role") and not default_fallback:
        return None
    selection = (saved or {}).get("effective_selection")
    if not selection:
        loop = parent.coordinator.get("orchestrator")
        if not default_fallback and not (getattr(loop, "config", {}) or {}).get("inherit_effective_model"):
            return None
        selected = getattr(loop, "root_provider", None)
        selection = getattr(selected, "selection", None)
    if default_fallback and (not isinstance(selection, dict)
                             or not selection.get("instance") or not selection.get("model")):
        raise ProviderSelectionError(
            "The default route has no candidates and the conversation has no selected provider/model to inherit.")
    return copy.deepcopy(selection)

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


def child_routing(parent, overlay, preferences, selection, *, preferences_source=None):
    result = delegation_routing(parent.coordinator)
    result["selectionSource"] = (
        "agent_model_role" if preferences and preferences_source == "role" else
        "delegation_preferences" if preferences else
        "agent_provider" if overlay.get("providers") else
        "inherited_conversation" if selection else
        "agent_model_role" if overlay.get("model_role") else "session_defaults"
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
