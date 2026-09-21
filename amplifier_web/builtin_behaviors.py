"""Repository-owned behavior defaults and version-matched installed resources."""
from pathlib import Path

SHELL_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-unified@main#subdirectory=behaviors/unified-shell.yaml"


def resource_root():
    packaged = Path(__file__).parent / "bundle_data"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parent.parent


def app_behaviors(settings):
    # Explicit lists, including [], are authoritative shared Amplifier settings.
    bundle = settings.get("bundle", {})
    if "app" in bundle:
        return list(bundle["app"])
    metadata = settings.get("web_bundles", {})
    if SHELL_BEHAVIOR_URI in metadata.get("excluded", []):
        return []
    if any(row.get("uri") == SHELL_BEHAVIOR_URI and row.get("enabled") is False
           for row in metadata.get("entries", [])):
        return []
    return [SHELL_BEHAVIOR_URI]


def resolve_builtin_behavior(uri):
    if uri == SHELL_BEHAVIOR_URI:
        # Keep the repository boundary explicit for Foundation namespace lookup.
        return resource_root().as_uri() + "#subdirectory=behaviors/unified-shell.yaml"
    return uri
