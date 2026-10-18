"""Repository-owned behavior defaults and version-matched installed resources."""
from pathlib import Path
from urllib.parse import urlsplit

SHELL_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-unified@main#subdirectory=behaviors/unified-shell.yaml"
IMAGEGEN_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-bundle-imagegen@main#subdirectory=behaviors/imagegen.yaml"
COMPUTER_USE_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-bundle-computer-use@main#subdirectory=behaviors/computer-use.yaml"
CONTEXT_INTELLIGENCE_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=behaviors/context-intelligence.yaml"
ROUTING_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main#subdirectory=behaviors/routing.yaml"
DEFAULT_BEHAVIORS = {
    SHELL_BEHAVIOR_URI: "Unified shell",
    IMAGEGEN_BEHAVIOR_URI: "Image generation",
    COMPUTER_USE_BEHAVIOR_URI: "Computer use",
    CONTEXT_INTELLIGENCE_BEHAVIOR_URI: "Context intelligence",
    ROUTING_BEHAVIOR_URI: "Model routing",
}
APPENDED_DEFAULTS = tuple(uri for uri in DEFAULT_BEHAVIORS if uri != SHELL_BEHAVIOR_URI)


def default_behavior_uri(uri):
    """Recognize a default without replacing an explicitly pinned source."""
    if not isinstance(uri, str) or not uri.startswith("git+https://github.com/"):
        return None
    def identity(value):
        parsed = urlsplit(value.removeprefix("git+"))
        path = parsed.path.rsplit("@", 1)[0].removesuffix(".git")
        return path, parsed.fragment
    return next((default for default in DEFAULT_BEHAVIORS
                 if identity(default) == identity(uri)), None)


def resource_root():
    packaged = Path(__file__).parent / "bundle_data"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parent.parent


def app_behaviors(settings):
    bundle = settings.get("bundle", {})
    metadata = settings.get("web_bundles", {})
    disabled = set(metadata.get("excluded", []))
    disabled.update(row.get("uri") for row in metadata.get("entries", [])
                    if row.get("enabled") is False)
    disabled = {default_behavior_uri(uri) or uri for uri in disabled}
    if "app" in bundle:
        behaviors, seen = [], set()
        for uri in bundle["app"]:
            canonical = default_behavior_uri(uri)
            if canonical is None or canonical not in seen:
                behaviors.append(uri)
            if canonical:
                seen.add(canonical)
        # Upgrade existing lists without writing settings, changing order/pins,
        # or restoring an opted-out capability. Explicit re-adds still win.
        for uri in APPENDED_DEFAULTS:
            if uri not in seen and uri not in disabled:
                behaviors.append(uri)
        return behaviors
    return [uri for uri in DEFAULT_BEHAVIORS if uri not in disabled]


def resolve_builtin_behavior(uri):
    if uri == SHELL_BEHAVIOR_URI:
        # Keep the repository boundary explicit for Foundation namespace lookup.
        return resource_root().as_uri() + "#subdirectory=behaviors/unified-shell.yaml"
    return uri
