"""Decision-owned settings; pure translation, with explicit per-file migration.

Connections use the shared settings provider merge key (id, otherwise module).
A ``provider`` reference reuses an ordinary chat account, never its credentials.
Runtime projection recreates the original logical declaration, not a new account.
"""
from __future__ import annotations

from copy import deepcopy


def key(row):
    return row.get("id") or row.get("module")


def _mapping(value):
    if not isinstance(value, dict):
        raise ValueError("Decision settings must be mappings; no settings were migrated.")
    return value


def connections(settings):
    return _mapping(_mapping(settings.get("decision_models", {})).get("connections", {}))


def decision_only(row, definitions):
    definition = definitions.get(row.get("module"))
    if not definition:
        return False
    definition = definition[0]
    return (definition.get("decision_only") is True
            or bool(definition.get("decision_only_when"))
            and row.get("config", {}).get("chat_enabled") is False)


def merge_settings(base, patch):
    """Foundation's settings merge, also used by save previews and migration."""
    from .shared_settings import overlay
    result = overlay(base, patch)
    rows = deepcopy(base.get("config", {}).get("providers", []))
    for row in patch.get("config", {}).get("providers", []):
        index = next((i for i, other in enumerate(rows) if key(other) == key(row)), None)
        if index is None:
            rows.append(deepcopy(row))
        else:
            rows[index] = overlay(rows[index], row)
    if rows or "providers" in patch.get("config", {}):
        result.setdefault("config", {})["providers"] = rows
    order = list(dict.fromkeys([*base.get("decision_models", {}).get("connection_order", []),
                               *patch.get("decision_models", {}).get("connection_order", [])]))
    if order:
        result.setdefault("decision_models", {})["connection_order"] = order
    return result


def logical_rows(settings):
    """Reconstitute exact account/config declarations, without resolving secrets."""
    from .shared_settings import overlay
    rows = deepcopy(settings.get("config", {}).get("providers", []))
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(key(row), str) or key(row) in seen:
            raise ValueError("Ambiguous provider identities; no decision account was rebound.")
        seen.add(key(row))
    for identity, connection in connections(settings).items():
        if not isinstance(identity, str) or not isinstance(connection, dict):
            raise ValueError("Invalid decision connection; no account was rebound.")
        if "provider" in connection:
            matches = [row for row in rows if key(row) == connection["provider"]]
            if (connection["provider"] != identity or len(matches) != 1
                    or matches[0].get("module") != connection.get("module")
                    or set(connection) - {"provider", "module", "config"}
                    or set(_mapping(connection.get("config", {}))) - {"decisions"}):
                raise ValueError("The shared decision connection no longer matches its chat account.")
            row = matches[0]
            config = row.setdefault("config", {})
            patch = connection.get("config", {})
            if "decisions" in config and config["decisions"] != patch.get("decisions"):
                raise ValueError("Conflicting legacy and dedicated decision settings.")
            row["config"] = overlay(config, patch)
        else:
            if key(connection) != identity or identity in seen or not connection.get("module"):
                raise ValueError("Conflicting decision connection identities; no account was rebound.")
            rows.append(deepcopy(connection))
            seen.add(identity)
    order = settings.get("decision_models", {}).get("connection_order", [])
    return sorted(rows, key=lambda row: order.index(key(row)) if key(row) in order else len(order))


def translate(settings, definitions, *, inherited=None):
    """Translate one scope, preserving unknown declarations and scope fragments.

    Only installed account metadata establishes ownership. Unknown modules stay
    untouched. A differing old/new declaration is a refusal, not a precedence
    guess. Callers publish only the returned complete value.
    """
    from .shared_settings import overlay
    result = deepcopy(settings)
    _mapping(result)
    _mapping(result.get("config", {}))
    existing = connections(result)
    rows = result.get("config", {}).get("providers", [])
    if not isinstance(rows, list):
        raise ValueError("Provider declarations must be a list; no settings were migrated.")
    # Old/new duplicates are checked below before logical reconstruction.
    prior = logical_rows(inherited or {})
    by_key = {key(row): row for row in prior}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(key(row), str) or key(row) in seen:
            raise ValueError("Ambiguous provider identities; no settings were migrated.")
        seen.add(key(row))
        previous = by_key.get(key(row), {})
        if (previous and row.get("module") and previous.get("module") != row["module"]
                and (previous.get("module") in definitions or row["module"] in definitions)):
            raise ValueError("A scoped decision connection cannot change its account module.")
        by_key[key(row)] = overlay(previous, row)
    kept, moved = [], deepcopy(existing)
    for row in rows:
        identity = key(row)
        full = by_key[identity]
        definition = definitions.get(full.get("module"))
        if not definition:
            kept.append(row)
            continue
        config = _mapping(row.get("config", {}))
        previous = next((item for item in prior if key(item) == identity), None)
        if previous and decision_only(previous, definitions) != decision_only(full, definitions):
            raise ValueError("A scoped connection changes its chat role; review decision settings before migrating.")
        if decision_only(full, definitions):
            target = deepcopy(row)
        elif "decisions" in config:
            target = {"provider": identity, "module": full["module"],
                      "config": {"decisions": deepcopy(config["decisions"])}}
        else:
            kept.append(row)
            continue
        if "decisions" in config and config["decisions"] is not None:
            _mapping(config["decisions"])
        if identity in moved and moved[identity] != target:
            raise ValueError("Conflicting legacy and dedicated decision settings; no settings were migrated.")
        moved[identity] = target
        if not decision_only(full, definitions):
            ordinary = deepcopy(row)
            del ordinary["config"]["decisions"]
            kept.append(ordinary)
    if moved:
        result.setdefault("decision_models", {})["connections"] = moved
        result["decision_models"]["connection_order"] = list(dict.fromkeys([
            *(key(row) for row in prior), *result["decision_models"].get("connection_order", []),
            *(key(row) for row in rows)]))
    if rows != kept:
        result["config"]["providers"] = kept
    # Validate the complete effective scope before a caller can publish it.
    logical_rows(merge_settings(inherited or {}, result))
    return result


def read_scopes(paths, *, definitions=None):
    """Passive scoped composition. Discovery/migration never runs on a read."""
    from .shared_settings import read_yaml
    result = {}
    for path in paths:
        value = read_yaml(path)
        if definitions is not None:
            # Explicit write previews may translate with metadata already
            # obtained by their owner. Ordinary readers never request this.
            result = merge_settings(result, translate(value, definitions, inherited=result))
            continue
        # A process may read between two independently atomic scope commits.
        # Only an already-declared dedicated owner can absorb its legacy scope
        # fragment here. This is in-memory composition, never a settings write
        # or optional-package discovery.
        scoped = deepcopy(value)
        kept = []
        inherited = connections(result)
        for row in scoped.get("config", {}).get("providers", []):
            identity = key(row)
            previous = inherited.get(identity)
            if not previous or identity in connections(scoped):
                kept.append(row)
                continue
            if row.get("module", previous.get("module")) != previous.get("module"):
                kept.append(row)
                continue
            if "provider" not in previous:
                scoped.setdefault("decision_models", {}).setdefault("connections", {})[identity] = row
            elif "decisions" in row.get("config", {}):
                scoped.setdefault("decision_models", {}).setdefault("connections", {})[identity] = {
                    "provider": identity, "module": previous["module"],
                    "config": {"decisions": row["config"].pop("decisions")}}
                kept.append(row)
            else:
                kept.append(row)
        if "providers" in scoped.get("config", {}):
            scoped["config"]["providers"] = kept
        result = merge_settings(result, scoped)
    return result


def preflight_paths(paths, definitions, *, replacement=None):
    """Validate a complete known lineage without publishing any scope."""
    from .shared_settings import read_yaml
    result = {}
    for path in paths:
        value = replacement[1] if replacement and path == replacement[0] else read_yaml(path)
        result = merge_settings(result, translate(value, definitions, inherited=result))
    return result


def migrate_paths(paths, *, definitions=None):
    """Idempotent atomic cutover of existing files, one Foundation owner at a time.

    Earlier committed scopes are safe after interruption. No marker, credential
    read, backup copy, new settings file or cross-file transaction is needed.
    """
    from .decision_settings import catalogs
    from .shared_settings import read_yaml, update_settings
    definitions = catalogs(unique=True) if definitions is None else definitions
    paths = tuple(paths)
    # Preflight the whole known scope lineage before changing any file. Atomic
    # publication is still per file, not a multi-file transaction.
    preflight_paths(paths, definitions)
    inherited = {}
    for path in paths:
        before = read_yaml(path)
        after = translate(before, definitions, inherited=inherited)
        if after != before:
            def migrate(current):
                # Atomic rename can replace a POSIX read-only file in a writable
                # directory. A deliberately read-only settings owner is a refusal.
                if path.stat().st_mode & 0o222 == 0:
                    raise PermissionError("Decision settings migration needs a writable settings owner.")
                return translate(current, definitions, inherited=inherited)
            after = update_settings(path, migrate)
        inherited = merge_settings(inherited, after)
    return inherited