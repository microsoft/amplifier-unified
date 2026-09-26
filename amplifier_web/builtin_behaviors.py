"""Repository-owned behavior defaults and version-matched installed resources."""
from pathlib import Path

SHELL_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-unified@main#subdirectory=behaviors/unified-shell.yaml"
IMAGEGEN_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-bundle-imagegen@main#subdirectory=behaviors/imagegen.yaml"
DEFAULT_BEHAVIORS = {
    SHELL_BEHAVIOR_URI: "Unified shell",
    IMAGEGEN_BEHAVIOR_URI: "Image generation",
}


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
    disabled = set(metadata.get("excluded", []))
    disabled.update(row.get("uri") for row in metadata.get("entries", [])
                    if row.get("enabled") is False)
    return [uri for uri in DEFAULT_BEHAVIORS if uri not in disabled]


def resolve_builtin_behavior(uri):
    if uri == SHELL_BEHAVIOR_URI:
        # Keep the repository boundary explicit for Foundation namespace lookup.
        return resource_root().as_uri() + "#subdirectory=behaviors/unified-shell.yaml"
    return uri
