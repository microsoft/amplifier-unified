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


def mounted_read_file(coordinator):
    """Inspect our actual mounted read tool, not an arbitrary .original chain."""
    from amplifier_module_tool_filesystem.read import ReadTool
    tool = (coordinator.get('tools') or {}).get('read_file')
    inspect_tool = coordinator.get_capability('web.work_inspect_tool')
    if callable(inspect_tool):
        try:
            tool = inspect_tool('read_file', tool)
        except ValueError:
            return None
    from .file_source_guard import GuardedFileTool
    if type(tool) is GuardedFileTool:
        guard = coordinator.get_capability('web.file_source_guard')
        if (guard is None or tool.guard is not guard or guard.coordinator is not coordinator
                or tool.kind != 'read'):
            return None
        tool = tool.tool
    return tool if type(tool) is ReadTool else None


def media_read_source(coordinator, original_path):
    """Resolve the current source name/policy, never open original pixel bytes.

    Nonexistent regular paths still resolve: delivery uses the owned asset. An
    alias/mention redirected to another source cannot borrow the old binding.
    """
    tool = mounted_read_file(coordinator)
    if tool is None or not isinstance(original_path, str) or not original_path:
        return None
    if original_path.startswith('@'):
        resolver = coordinator.get_capability('mention_resolver')
        resolved = resolver.resolve(original_path) if resolver is not None else None
        if resolved is None:
            return None
        original_path = str(resolved)
    path = Path(original_path).expanduser()
    if not path.is_absolute():
        if not tool.working_dir:
            return None
        path = Path(tool.working_dir) / path
    try:
        return str(path.resolve()) if tool._is_allowed(path) is True else None
    except (OSError, ValueError, RuntimeError):
        return None


def media_read_allowed(coordinator, original_path):
    return media_read_source(coordinator, original_path) is not None


def read_file_call_paths(canonical, session_id, *, artwork=None):
    """Only complete canonical assistant/tool pairs can bind source arguments.

    Duplicated IDs, missing results, wrong roles/names and producer-added paths
    are not authority. This index is used only after the primary checkpoint
    owner has saved these exact rows. It never establishes persistence itself.
    """
    import json
    from .host.media import NAMESPACE
    calls, duplicates, paths, paired = {}, set(), {}, set()
    for index, row in enumerate(canonical):
        if row.get('role') != 'assistant':
            continue
        entries = [('calls', call) for call in row.get('tool_calls') or []]
        if isinstance(row.get('content'), list):
            entries += [('content', block) for block in row['content'] if isinstance(block, dict)
                        and block.get('type') == 'tool_call']
        normalized, representations = {}, set()
        for representation, call in entries:
            if not isinstance(call, dict):
                continue
            identity = call.get('id')
            function = call.get('function', call)
            if not isinstance(function, dict):
                continue
            name = function.get('name', function.get('tool'))
            if 'name' in function and 'tool' in function and function['name'] != function['tool']:
                duplicates.add(identity)
            arguments = function.get('arguments', function.get('input'))
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = None
            value = (name, arguments)
            if ((identity, representation) in representations
                    or identity in normalized and normalized[identity] != value):
                duplicates.add(identity)
            representations.add((identity, representation))
            normalized[identity] = value
        # The real turn engine stores both a ToolCallBlock (name/input) and a
        # tool_calls entry (tool/arguments). Equal representations describe one
        # call; conflicts, repeats within either carrier or later rows do not.
        identities = list(normalized)
        end = index + 1
        results = []
        while end < len(canonical) and canonical[end].get('role') == 'tool':
            results.append(canonical[end].get('tool_call_id'))
            end += 1
        if (all(isinstance(identity, str) and identity for identity in identities)
                and len(set(identities)) == len(identities)
                and len(results) == len(identities) and set(results) == set(identities)):
            paired.update(identities)
        for identity, value in normalized.items():
            if identity in calls:
                duplicates.add(identity)
            calls[identity] = value
    seen = set()
    for row in canonical:
        if row.get('role') != 'tool':
            continue
        identity = row.get('tool_call_id')
        if identity in seen:
            duplicates.add(identity)
        seen.add(identity)
        name, arguments = calls.get(identity, (None, None))
        refs = ((row.get('metadata') or {}).get(NAMESPACE) or {}).get('refs', [])
        if identity not in paired or identity in duplicates or row.get('name', name) != name or not refs:
            continue
        if name == 'read_file' and isinstance(arguments, dict):
            path = arguments.get('file_path')
            if not isinstance(path, str) or not path or 'offset' in arguments or 'limit' in arguments:
                continue
        elif artwork is not None and name == 'codex_artwork':
            from .output_image_delivery import successful_output
            import re
            receipt = successful_output(row.get('content'))
            if not isinstance(receipt, dict):
                continue
            artifact_id = receipt.get('artifact_id')
            if not isinstance(artifact_id, str) or not re.fullmatch(r'[a-f0-9]{64}', artifact_id):
                continue
            path = str(artwork.directory / (artifact_id + '.png'))
            if (receipt.get('path') != path or len(refs) != 1
                    or refs[0].get('sha256') != receipt.get('preview_sha256')):
                continue
        else:
            continue
        if all((ref.get('provenance') or {}).get('kind') == 'tool'
               and (ref.get('provenance') or {}).get('session_id') == session_id
               and (ref.get('provenance') or {}).get('id') == identity for ref in refs):
            paths[identity] = path
    return {identity: path for identity, path in paths.items() if identity not in duplicates}
