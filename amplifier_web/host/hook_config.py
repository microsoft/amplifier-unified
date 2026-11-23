"""Named credentials for explicitly selected Context Intelligence destinations."""
from __future__ import annotations

import re

from .config import expand_environment


MODULE = "hook-context-intelligence"
_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-?)?\}\Z")


def reference_name(value):
    match = _REFERENCE.fullmatch(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def destinations_in(config):
    destinations = config.get("destinations") if isinstance(config, dict) else None
    if isinstance(destinations, dict):
        for name, destination in destinations.items():
            if isinstance(destination, dict):
                yield name, destination


def declared_config(config):
    """Validate/expand the config now, keeping the original name in the durable plan."""
    result = expand_environment(config)
    return retain_references(config, result)


def retain_references(config, result):
    for name, destination in destinations_in(config):
        value = destination.get("api_key")
        if reference_name(value):
            result["destinations"][name]["api_key"] = value
    return result


def mounted_config(config):
    """Resolve only the exact references in this module load's configuration."""
    destinations = {}
    for name, destination in destinations_in(config):
        value = destination.get("api_key")
        if reference_name(value):
            destinations[name] = {**destination, "api_key": expand_environment(value)}
    if destinations:
        return {**config, "destinations": {**config["destinations"], **destinations}}
    return config


def omit_inherited_literals(plan):
    """A new worker cannot infer a credential's name from an old private plan."""
    from .components import declarations
    root_hooks = {id(row) for row in plan.get("hooks", []) if row.get("enabled") is not False}
    warning = False
    for row in declarations(plan):
        if row.get("module") == MODULE:
            for _, destination in destinations_in(row.get("config")):
                value = destination.get("api_key")
                if value and not reference_name(value):
                    destination.pop("api_key")
                    if (id(row) in root_hooks and destination.get("url") and destination.get("include")
                            and str(destination.get("auth_mode") or "static").strip() == "static"):
                        warning = True
    if warning:
        return {"warnings": ["A Context Intelligence destination credential could not be inherited. "
                             "If this worker should forward there, set that destination's api_key "
                             "to a named environment reference in the source conversation."]}
    return {}
