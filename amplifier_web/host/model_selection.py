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


