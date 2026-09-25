"""Model inheritance policy independent of the runtime implementation."""
import copy


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
