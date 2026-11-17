"""Honor an explicit module commit in an unpublished update candidate.

Installed pins remain retention policy unless current configuration selects an
exact commit of the same package/repository. Selected manifests, not module-name
guessing or a neighboring checkout, bind that choice to its distribution.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tomllib


def choices(config):
    from .selected_runtime_dependencies import git_source
    result = {}
    for module, reference in config.module_sources.items():
        source = git_source(reference) if isinstance(reference, str) else None
        if source and re.fullmatch('[a-f0-9]{40}', source['ref']):
            result[module] = source
    return result


def same_source(left, right):
    from .updates import source_key
    return (source_key(left['url'], 'HEAD')[0] == source_key(right['url'], 'HEAD')[0]
            and left.get('subdirectory', '') == right.get('subdirectory', ''))


def configured_packages(config, packages, manifests):
    """Called only with the collector's verified root manifests and identities."""
    requested = choices(config)
    result = {}
    for name, content in manifests.items():
        source = packages[name]
        if (source.get('status') != 'explicit-pin' or source.get('sourceKind') == 'dependency'
                or not re.fullmatch('[a-f0-9]{40}', source.get('ref', ''))
                or source['ref'] != source.get('revision')):
            continue
        entries = tomllib.loads(content.decode()).get('project', {}).get('entry-points', {}).get('amplifier.modules', {})
        modules = sorted(module for module in entries if module in requested
                         and same_source(requested[module], source)
                         and requested[module]['ref'] == source['ref'])
        if modules:
            result[name] = modules
    return result


async def reconcile(manager, stage, project, collect):
    from .deployment import write_private
    from .host.config import read_config
    from .runtime_policy_reconciliation import configuration
    from .runtime_source_policy import external_resolver, with_origin
    from .selected_runtime_dependencies import refresh_generated_sources
    from .updates import active_release

    home, stage, project = Path(manager.home), Path(stage), Path(project)
    if external_resolver(project):
        return None
    config = read_config(home, home=home, global_only=True)
    requested = choices(config)
    if not requested:
        return None
    path = stage / 'runtime-sources.json'
    original = path.read_bytes()
    checked = json.loads(original)
    # No collection or installer for unchanged pins or unselected repositories.
    candidates = {name: old for name, old in checked.items()
                  if re.fullmatch('[a-f0-9]{40}', old.get('ref', ''))
                  and old['ref'] == old.get('revision')
                  and any(same_source(choice, old) and choice['ref'] != old['ref']
                          for choice in requested.values())}
    if not candidates:
        return None
    key, generation = configuration(config), active_release(home).get('current')
    first = await collect()
    if first.get('configuration') != key:
        raise ValueError('Configured module sources changed during preparation.')
    selected = {}
    for name, modules in first.get('configuredPins', {}).items():
        source, old = first['packages'][name], candidates.get(name)
        if (old and modules and same_source(source, old) and source['revision'] != old['revision']):
            selected[name] = {**source, 'previousRevision': old['revision']}
    if not selected:
        return first

    def unchanged():
        if (path.read_bytes() != original or active_release(home).get('current') != generation
                or configuration(read_config(home, home=home, global_only=True)) != key):
            raise ValueError('Configured module sources changed during preparation.')
    unchanged()
    # This updates only app-generated candidate entries, verifies their old
    # values, and refuses authored overrides, recorded generations and drift.
    await refresh_generated_sources(manager, stage, project, selected)
    unchanged()
    for name, source in selected.items():
        checked[name] = with_origin({field: source[field] for field in
            ('url', 'ref', 'revision', 'subdirectory')}, 'configured-module-pin',
            manifestSha256=source['manifestSha256'])
    saved = json.dumps(checked, indent=2) + '\n'
    write_private(path, saved, owned_root=home)
    second = await collect()
    if (path.read_bytes() != saved.encode() or second.get('configuration') != key
            or active_release(home).get('current') != generation
            or configuration(read_config(home, home=home, global_only=True)) != key
            or any(second.get('packages', {}).get(name) != first['packages'][name]
                   or second.get('configuredPins', {}).get(name) != first['configuredPins'][name]
                   for name in selected)):
        raise ValueError('Configured module sources changed after their checked refresh.')
    write_private(stage / 'configured-runtime-pins.json', json.dumps({
        'version': 1, 'configuration': key, 'previousGeneration': generation,
        'previousPolicySha256': hashlib.sha256(original).hexdigest(),
        'selected': {name: {'previous': candidates[name], 'current': checked[name],
                           'modules': first['configuredPins'][name]} for name in selected},
    }, indent=2) + '\n', owned_root=home)
    return second
