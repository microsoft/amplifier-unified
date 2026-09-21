"""One source per host-selected component, including late child declarations.

Only declared host behaviors and required host dependencies own sources. Bundle
configuration keeps normal later-overlay precedence and distinct mount instances.
This policy selects sources; Foundation activates them and Core validates imports.
"""
from __future__ import annotations

import copy
import importlib.metadata
import importlib.util
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


class HostComponentConflict(ValueError):
    def __init__(self):
        super().__init__('Host-selected components have incompatible identities or source ownership. '
                         'Check the app behavior module declarations and instance IDs; no session was started.')


def merge_modules(*groups):
    """Merge actual duplicates without losing instance_id-only declarations."""
    result, indexes = [], {}
    for rows in groups:
        if not isinstance(rows, list):
            raise HostComponentConflict()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('module'), str):
                raise HostComponentConflict()
            identity = row.get('id') or row.get('instance_id') or row['module']
            if not isinstance(identity, str) or (row.get('id') and row.get('instance_id')
                                               and row['id'] != row['instance_id']):
                raise HostComponentConflict()
            if identity in indexes:
                from amplifier_foundation import deep_merge
                index = indexes[identity]
                if result[index]['module'] != row['module']:
                    raise HostComponentConflict()
                result[index] = deep_merge(result[index], row)
            else:
                indexes[identity] = len(result)
                result.append(copy.deepcopy(row))
    return result


def declarations(plan):
    for section in ('providers', 'tools', 'hooks'):
        yield from plan.get(section, [])
    for row in plan.get('session', {}).values():
        if isinstance(row, dict) and 'module' in row:
            yield row
    yield from plan.get('spawn', {}).get('tools', [])
    for agent in plan.get('agents', {}).values():
        if isinstance(agent, dict):
            yield from declarations(agent)


def git_source(source):
    """Keep full repository identity; never infer ownership from a basename."""
    if not isinstance(source, str) or not source.startswith('git+'):
        return None
    base, _, fragment = source.partition('#')
    # Revision separators occur after the URL authority (which may contain @).
    parsed = urlsplit(base[4:])
    path, at, revision = parsed.path.rpartition('@')
    repository = base[:-(len(revision) + 1)] if at else base
    return repository.removesuffix('.git'), base, fragment


def installed_package_source(distribution_name, package_name):
    """Locate a host dependency without importing code from a cache checkout."""
    distribution = importlib.metadata.distribution(distribution_name)
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    if direct.get('dir_info', {}).get('editable') is True:
        parsed = urlsplit(direct.get('url', ''))
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            raise ValueError('The installed host component has unsupported local source metadata.')
        root = Path(unquote(parsed.path)).resolve()
        candidates = [root / package_name, root / 'src' / package_name]
    else:
        candidates = [Path(distribution.locate_file(package_name)).resolve()]
    candidates = [path for path in candidates if (path / '__init__.py').is_file()]
    spec = importlib.util.find_spec(package_name)
    if (len(candidates) != 1 or spec is None or not spec.origin
            or Path(spec.origin).resolve() != (candidates[0] / '__init__.py').resolve()):
        raise ValueError('The host component import does not match its installed distribution; its source was preserved.')
    return str(candidates[0])


class HostComponents:
    def __init__(self, sources=None, installed=None):
        self.sources = dict(sources or {})
        self.installed = dict(installed or {})
        self.repositories = {}
        self.roots = {}
        self.path_roots = {}

    def select(self, module, source):
        if module in self.installed:
            return
        if not isinstance(source, str) or not source:
            return
        self.sources[module] = source
        git = git_source(source)
        if git:
            self.repositories[git[0]] = git[1]
            for identity, value in self.sources.items():
                sibling = git_source(value)
                if sibling and sibling[0] == git[0]:
                    self.sources[identity] = git[1] + ('#' + sibling[2] if sibling[2] else '')

    def select_bundle(self, bundle, overrides):
        for row in declarations(bundle.to_mount_plan()):
            source = overrides.get(row['module']) or row.get('source')
            if isinstance(source, str) and source.startswith('./') and bundle.base_path:
                source = str((bundle.base_path / source).resolve())
            self.select(row['module'], source)
        self.roots.update(bundle.source_base_paths)
        if bundle.name and bundle.base_path:
            self.roots.setdefault(bundle.name, bundle.base_path)

    def source(self, module, source, *, installed=False):
        if installed and module in self.installed:
            return self.installed[module]()
        if module in self.sources:
            return self.sources[module]
        git = git_source(source)
        target = self.repositories.get(git[0]) if git else None
        if target:
            return target + ('#' + git[2] if git[2] else '')
        if isinstance(source, str):
            parsed = urlsplit(source)
            path = Path(unquote(parsed.path)) if parsed.scheme == 'file' else Path(source)
            if parsed.scheme in ('', 'file') and path.is_absolute():
                for old, chosen in self.path_roots.items():
                    if path.is_relative_to(old):
                        replacement = chosen / path.relative_to(old)
                        return (replacement.as_uri() + ('#' + parsed.fragment if parsed.fragment else '')
                                if parsed.scheme == 'file' else str(replacement))
        return source

    def owns(self, module, source):
        git = git_source(source)
        return module in self.sources or bool(git and git[0] in self.repositories) or self.source(module, source) != source

    def normalize(self, plan):
        result = copy.deepcopy(plan)
        def visit(node):
            for section in ('providers', 'tools', 'hooks'):
                if section in node:
                    node[section] = merge_modules(node[section])
            if isinstance(node.get('spawn', {}).get('tools'), list):
                node['spawn']['tools'] = merge_modules(node['spawn']['tools'])
            for agent in node.get('agents', {}).values():
                if isinstance(agent, dict):
                    visit(agent)
        visit(result)
        for row in declarations(result):
            selected = self.source(row['module'], row.get('source'))
            if selected is not None:
                row['source'] = selected
        return result

    def apply(self, bundle):
        old_roots = dict(getattr(bundle, 'source_base_paths', {}))
        for namespace, root in self.roots.items():
            old = old_roots.get(namespace)
            if old and Path(old) != Path(root):
                self.path_roots[Path(old)] = Path(root)
        plan = self.normalize(bundle.to_mount_plan())
        for key in ('providers', 'tools', 'hooks', 'session', 'spawn', 'agents'):
            if key in plan:
                setattr(bundle, key, plan[key])
        # Foundation composes namespaces first-wins. Host-selected behaviors
        # must also own their package/resource roots, not only module source rows.
        for namespace, root in self.roots.items():
            old = old_roots.get(namespace)
            if old and Path(old) != Path(root):
                for key, path in bundle.context.items():
                    if key.startswith(namespace + ':') and Path(path).is_relative_to(old):
                        bundle.context[key] = Path(root) / Path(path).relative_to(old)
        if self.roots:
            bundle.source_base_paths.update(self.roots)
        bundle._host_components = self
        return bundle


def compose_bundles(base, overlay):
    """Retain Foundation resources/provenance and host instance merge semantics."""
    result = base.compose(overlay)
    for section in ('providers', 'tools', 'hooks'):
        setattr(result, section, merge_modules(getattr(base, section), getattr(overlay, section)))
    return result


class ComponentResolver:
    """Apply the same source rule when a dynamic child lazily adds a module."""
    def __init__(self, resolver, components):
        self.resolver, self.components = resolver, components

    def __getattr__(self, name):
        return getattr(self.resolver, name)

    def resolve(self, module_id, source_hint=None, profile_hint=None):
        hint = profile_hint if profile_hint is not None else source_hint
        return self.resolver.resolve(module_id, self.components.source(module_id, hint, installed=True))

    async def async_resolve(self, module_id, source_hint=None, profile_hint=None):
        hint = profile_hint if profile_hint is not None else source_hint
        return await self.resolver.async_resolve(module_id, self.components.source(module_id, hint, installed=True))
