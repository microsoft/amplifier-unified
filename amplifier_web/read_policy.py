"""Compose read-only search boundaries with canonical filesystem policy."""
from __future__ import annotations

import copy
from pathlib import Path

READ_KEYS = ('allowed_read_paths', 'denied_read_paths')
SEARCH_KEYS = ('allowed_paths', 'denied_paths')


def policy_paths(values, working_dir):
    from .host.config import expand_environment
    values = expand_environment(values)
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('File-access paths must be lists of strings.')
    root = Path(working_dir).expanduser().resolve()
    return [(path if path.is_absolute() else root / path).resolve()
            for value in values for path in (Path(value).expanduser(),)]


def search_policy(config, tool=None):
    """Module and per-tool declarations are both boundaries in Unified."""
    if not isinstance(config, dict):
        raise ValueError('Search configuration must be a dictionary.')
    translated = {}
    for source, target in zip(SEARCH_KEYS, READ_KEYS):
        if source in config:
            value = config[source]
            translated[target] = [str(value)] if isinstance(value, (str, Path)) else value
    result = [translated]
    if tool is not None and tool in config:
        result += search_policy(config[tool])
    return result


def restrict_search(config, *, files=(), searches=(), paths):
    """Preserve tuning knobs; intersect allowlists and union denies per operation."""
    from .host.config import expand_environment
    result = copy.deepcopy(config)
    def resolve(values):
        return paths(expand_environment(values))
    for name in (None, 'grep', 'glob'):
        policies = [*files, *(policy for value in [*searches, config] for policy in search_policy(value, name))]
        limits = [resolve(policy['allowed_read_paths']) for policy in policies
                  if policy.get('allowed_read_paths') is not None]
        allowed = None
        if limits:
            allowed = limits[0]
            for limit in limits[1:]:
                allowed = [a if a.is_relative_to(b) else b for a in allowed for b in limit
                           if a.is_relative_to(b) or b.is_relative_to(a)]
        denied = [path for policy in policies for path in resolve([] if policy.get('denied_read_paths') is None else policy['denied_read_paths'])]
        target = result if name is None else result.setdefault(name, {})
        target['allowed_paths'] = None if allowed is None else list(dict.fromkeys(str(path) for path in allowed))
        target['denied_paths'] = list(dict.fromkeys(str(path) for path in denied))
    return result


def restore_search_declaration(config, original):
    """Generic settings merges must not erase a bundle's explicit boundaries."""
    for name in (None, 'grep', 'glob'):
        target = config if name is None else config.setdefault(name, {})
        declared = original if name is None else original.get(name, {})
        for key in SEARCH_KEYS:
            target.pop(key, None)
            if key in declared:
                target[key] = copy.deepcopy(declared[key])


def validate_mounted_read_policy(coordinator):
    """Reject unsupported recorded modules; do not substitute enforcement."""
    for tool in (coordinator.get('tools') or {}).values():
        module = type(tool).__module__
        config = getattr(tool, 'config', {})
        if module in {'amplifier_module_tool_search.grep', 'amplifier_module_tool_search.glob'}:
            policy = getattr(tool, 'path_policy', None)
            unsupported = (policy is None or not hasattr(policy, 'allowed_roots')
                or (config.get('allowed_paths', ['.']) is None and policy.allowed_roots is not None)
                or (bool(config.get('denied_paths')) and not getattr(policy, 'denied_roots', ())))
        elif module == 'amplifier_module_tool_filesystem.read':
            unsupported = bool(config.get('denied_read_paths')) and not getattr(tool, 'denied_read_paths', ())
        else:
            continue
        if unsupported:
            raise ValueError('The recorded file/search module cannot enforce this read policy. '
                             'Use Settings > Updates to check and install component updates before starting this worker; '
                             'a configuration permission grant is not needed for ordinary reads.')
