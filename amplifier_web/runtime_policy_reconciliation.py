"""Reconcile legacy generated pins against selected authored sources once.

Runs only while preparing an unpublished generation. It never infers a branch
from an adjacent cache or rewrites a qualified/serving worker.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib

VERSION = 1
SELECTION_VERSION = 3
RECEIPT = 'runtime-policy-reconciliation.json'


def legacy_candidates(rows):
    return {row['package']: {key: row.get(key, '') for key in
                            ('url', 'ref', 'current', 'subdirectory')}
            for row in rows if row.get('kind') == 'runtime dependency'
            and not row.get('override') and row.get('url')
            and re.fullmatch('[a-f0-9]{40}', row.get('ref', ''))
            and row.get('ref') == row.get('current')
            and (row.get('policyOrigin') or {}).get('kind', 'unrecorded') in {'unrecorded', 'unresolved-reference'}}


def configuration(config):
    from .profile_catalog import configuration_key
    from .bundles import profile_candidates
    return hashlib.sha256(json.dumps({
        'sources': configuration_key(config),
        'profiles': profile_candidates(config),
    }, sort_keys=True).encode()).hexdigest()


def pending(home, generation, rows, *, honor_receipt=True):
    """Read-only update inventory. A failed probe never acknowledges the work."""
    candidates = legacy_candidates(rows)
    # Caller-supplied resolver policy is explicit source intent. Do not inspect
    # arbitrary external files or reinterpret it as an installer-generated pin.
    if any(os.environ.get(key) for key in ('UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT')):
        return None
    if not candidates:
        return None
    from .runtime_environment import receipt_directory, project_path
    from .host.config import read_config
    home = Path(home)
    # Separate resolver policy is an explicit override we cannot reconstruct.
    separate = project_path(home, generation) / 'uv.toml'
    if separate.exists() or separate.is_symlink():
        return None
    config = read_config(home, home=home, global_only=True)
    key = configuration(config)
    try:
        saved = json.loads((receipt_directory(home, generation) / RECEIPT).read_text())
        if (honor_receipt and saved.get('version') == VERSION
                and saved.get('selectionVersion') == SELECTION_VERSION and saved.get('configuration') == key
                and all(saved.get('remaining', {}).get(name) == row for name, row in candidates.items())):
            return None
    except (OSError, ValueError, TypeError):
        pass
    return {'version': VERSION, 'configuration': key, 'candidates': candidates}


def annotate_retained(home, generation, rows):
    """A matching completed selection probe narrows usage, never deletes data."""
    from .runtime_environment import receipt_directory
    from .host.config import read_config
    try:
        saved = json.loads((receipt_directory(home, generation) / RECEIPT).read_text())
        if (saved.get('version') != VERSION or saved.get('selectionVersion') != SELECTION_VERSION
                or saved.get('configuration') !=
                configuration(read_config(home, home=home, global_only=True))):
            return
        offered = saved.get('offeredPackages')
        if not isinstance(offered, list):
            return
        candidates = legacy_candidates(rows)
        for row in rows:
            name = row.get('package')
            if (name in candidates and saved.get('remaining', {}).get(name) == candidates[name]
                    and saved.get('decisions', {}).get(name) == 'unselected' and name not in offered):
                row['usage'] = 'unknown'
                row['usageEvidence'] = [
                    'Kept for existing chats; not selected by current offered profiles',
                    *row.get('usageEvidence', []),
                ]
    except (OSError, ValueError, TypeError):
        return


class Collector:
    """Observe effective root/include/module choices during collect-only prepare."""
    def __init__(self):
        self.references = set()
        self.local_sources = set()
        self.local_manifests = set()

    def observe(self, reference, config, registry=None, *, explicit_local=False):
        if not isinstance(reference, str):
            return
        from .update_sequence import git_source
        from .updates import source_key
        seen = set()
        while reference not in seen:
            seen.add(reference)
            source = git_source(reference)
            if source is not None:
                try:
                    self.references.add(source_key(source['url'], source['ref']))
                except ValueError:
                    pass
                return
            # Resolve only a reference actually used by composition, never all
            # available aliases or neighboring cached YAML files.
            registered = registry.find(reference) if registry is not None else None
            if not registered:
                registered = config.registrations.get(reference.split(':', 1)[0])
            if not isinstance(registered, str):
                if explicit_local:
                    # Use the resolver's URI grammar, including file fragments
                    # and workspace-relative paths. A local override remains
                    # local even when it points inside a branch-backed cache.
                    from amplifier_foundation.paths.resolution import parse_uri
                    parsed = parse_uri(reference)
                    if parsed.is_file:
                        path = Path(parsed.path).expanduser()
                        if not path.is_absolute():
                            path = Path(config.workspace) / path
                        if parsed.subpath:
                            path = path / parsed.subpath
                        path = path.resolve()
                        if path.is_file() and path.suffix.lower() in {'.yaml', '.yml', '.md'}:
                            self.local_manifests.add(path)
                        else:
                            self.local_sources.add(path)
                return
            reference = config.resolve_source(registered) or registered

    async def report(self, batch, config, *, allow_generated=False):
        from .runtime_environment import package_name
        from .updates import process, cache_changes, source_key
        from .staged_sources import owned_path, owned_git_checkout
        stage, cache = Path(config.home), Path(config.registry_home) / 'cache'
        store_path = os.environ.get('AMPLIFIER_SOURCE_STORE')
        store = Path(store_path) if store_path else None
        bindings = {}
        for path in cache.glob('.source-bindings/*.json'):
            owned_path(path, stage)
            data = json.loads(path.read_text())
            bindings.setdefault((data['git_url'], data['commit']), set()).add(data['ref'])
        inspected, packages, constraints, manifests = {}, {}, {}, {}
        for name, source in sorted(batch.names.items()):
            name, source = package_name(name), Path(source)
            package = {'status': 'unresolved'}
            packages[name] = package
            if any(source == path or source.is_relative_to(path) for path in self.local_sources):
                package['status'] = 'local'
                continue
            allowed = stage if source.is_relative_to(stage) else store if store and source.is_relative_to(store / 'objects') else None
            if allowed is None:
                package['status'] = 'local'
                continue
            owned_path(source, allowed)
            root = source
            while root != allowed and not (root / '.git').exists():
                root = root.parent
            if root == allowed:
                continue
            # A local bundle file inside this source repository protects that
            # source choice. Do not widen it to the file's arbitrary parent,
            # which could also contain unrelated downloaded source caches.
            if any(path.is_relative_to(root) for path in self.local_manifests):
                package['status'] = 'local'
                continue
            if root not in inspected:
                await owned_git_checkout(root, allowed)
                meta = root / '.amplifier_cache_meta.json'
                owned_path(meta, allowed)
                data = json.loads(meta.read_text())
                head = await process('git', 'rev-parse', 'HEAD', cwd=root, timeout=10)
                dirty, artifacts = await cache_changes(root)
                if dirty or (artifacts and not allow_generated) or head != data.get('commit'):
                    raise ValueError('A selected source changed during policy reconciliation.')
                remote = await process('git', 'remote', 'get-url', 'origin', cwd=root, timeout=10)
                if source_key(remote, 'HEAD')[0] != source_key(data['git_url'], 'HEAD')[0]:
                    raise ValueError('A selected source has a different repository identity.')
                inspected[root] = data
            data = inspected[root]
            manifest = source / 'pyproject.toml'
            owned_path(manifest, allowed)
            content = manifest.read_bytes()
            if package_name(tomllib.loads(content.decode()).get('project', {}).get('name', '')) != name:
                raise ValueError('A selected source changed its package identity.')
            sibling_dev = selected_sibling_dev_sources(content, source, root, batch.names)
            for dependency, declaration in authored_constraints(content).items():
                if dependency in sibling_dev:
                    continue  # --no-sources does not activate this selected sibling's dev edge.
                if dependency in constraints and constraints[dependency] != declaration:
                    constraints[dependency] = None
                else:
                    constraints[dependency] = declaration
            if any((path / 'uv.toml').exists() or (path / 'uv.toml').is_symlink() for path in (root, source)):
                package['status'] = 'separate-resolver-config'
                continue
            url = source_key(data['git_url'], 'HEAD')[0]
            refs = {ref for candidate, ref in self.references if candidate == url}
            if len(refs) != 1:
                package['status'] = 'conflicting' if refs else 'unselected'
                continue
            ref = next(iter(refs))
            from .updates import pinned
            unbound_status = 'explicit-pin' if pinned(ref) else 'unbound'
            if allowed == store:
                if ref not in bindings.get((data['git_url'], data['commit']), set()):
                    package['status'] = unbound_status
                    continue
            elif source_key(data['git_url'], data.get('ref') or 'HEAD')[1] != ref:
                package['status'] = unbound_status
                continue
            package.update(status='selected', url=data['git_url'], ref=ref,
                           subdirectory=('' if source == root else source.relative_to(root).as_posix()),
                           revision=data['commit'], manifestSha256=hashlib.sha256(content).hexdigest())
            if pinned(ref):
                # Identity can prove an unchanged install without granting
                # moving-ref recovery or permission to rewrite this checkout.
                package['status'] = 'explicit-pin'
                continue
            manifests[name] = content
        for name, package in packages.items():
            if package.get('status') != 'selected' or name not in constraints:
                continue
            constraint = constraints[name]
            if not constraint or constraint.get('refKind') == 'tag' or any(constraint.get(field, '') != package[field]
                                     for field in ('url', 'ref', 'subdirectory')):
                package.clear()
                package['status'] = 'authored-constraint'
        from .selected_runtime_dependencies import selected
        dependencies = selected({name: content for name, content in manifests.items()
                                 if packages[name].get('status') == 'selected'})
        for name, package in dependencies.items():
            packages.setdefault(name, package)
        return {'version': VERSION, 'configuration': configuration(config), 'packages': packages,
                # Private preparation evidence, never copied into update diagnostics.
                'batchPaths': {name: str(path) for name, path in batch.names.items()},
                'protectedPaths': sorted(str(path) for path in self.local_sources | self.local_manifests)}



def selected_sibling_dev_sources(content, source, root, selected):
    """Ignore only a proven selected sibling's local, dev-only uv source.

    Optional, conditional, Git, external, unselected and resolver constraints
    remain protective. This does not select a package or authorize a new ref.
    Foundation's batch installs these exact paths with --no-sources.
    """
    from packaging.requirements import Requirement, InvalidRequirement
    from .runtime_environment import package_name
    data = tomllib.loads(content.decode())
    project, uv = data.get('project', {}), data.get('tool', {}).get('uv', {})
    if ('dependencies' in project.get('dynamic', []) or 'optional-dependencies' in project.get('dynamic', [])
            or not isinstance(project.get('dependencies', []), list)):
        return set()
    groups = data.get('dependency-groups', {})
    dev = groups.get('dev', [])
    protected = list(project.get('dependencies', []))
    for values in project.get('optional-dependencies', {}).values():
        protected.extend(values)
    for group, values in groups.items():
        if group != 'dev':
            protected.extend(values)
    for key in ('override-dependencies', 'constraint-dependencies', 'build-constraint-dependencies', 'dev-dependencies'):
        protected.extend(uv.get(key, []))
    def requirement(value):
        try:
            return Requirement(value) if isinstance(value, str) else None
        except InvalidRequirement:
            return None
    parsed = [requirement(value) for value in protected]
    if any(value is None for value in parsed):
        return set()
    protected_names = {package_name(value.name) for value in parsed}
    result = set()
    for name, entry in uv.get('sources', {}).items():
        name = package_name(name)
        if (name not in selected or name in protected_names or not isinstance(entry, dict)
                or set(entry) - {'path', 'editable'} or entry.get('editable') is not True
                or not isinstance(entry.get('path'), str) or Path(entry['path']).is_absolute()):
            continue
        declarations = [requirement(value) for value in dev]
        matching = [value for value in declarations if value and package_name(value.name) == name]
        if (not matching or any(value is None for value in declarations)
                or any(value.url or value.marker or value.extras or value.specifier for value in matching)):
            continue
        target = (source / entry['path']).resolve()
        # Containment alone can include a nested, independently owned checkout.
        target_root = next((path for path in (target, *target.parents) if (path / '.git').exists()), None)
        if (target == Path(selected[name]).resolve() and target != source
                and target.is_relative_to(root) and target_root == root):
            result.add(name)
    return result


def authored_constraints(content):
    """Selected source manifests may pin another package despite a module URI.

    Unknown/conditional source declarations only restrict recovery here. They
    never authorize it; the effective bundle/module choices remain required.
    """
    from packaging.requirements import Requirement, InvalidRequirement
    from .runtime_environment import package_name
    from .runtime_source_policy import declarations
    data = tomllib.loads(content.decode())
    project, uv = data.get('project', {}), data.get('tool', {}).get('uv', {})
    values = list(project.get('dependencies', []))
    for groups in (project.get('optional-dependencies', {}), data.get('dependency-groups', {})):
        for group in groups.values():
            values.extend(group)
    for key in ('override-dependencies', 'constraint-dependencies', 'build-constraint-dependencies', 'dev-dependencies'):
        values.extend(uv.get(key, []))
    names = {package_name(name) for name in uv.get('sources', {})}
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            requirement = Requirement(value)
        except InvalidRequirement:
            continue
        if requirement.url and requirement.url.startswith('git+'):
            names.add(package_name(requirement.name))
    declared = declarations(content)
    return {name: declared.get(name) for name in names}


async def moving_revision(url, ref, process):
    """Use the shared, access-scoped reference-kind proof."""
    from .update_checks import moving_revision as checked_revision
    return await checked_revision(url, ref, process)


def selected_policies(candidates, report):
    """A selected source is evidence only for its exact distribution identity."""
    from .updates import source_key
    result, decisions = {}, {}
    for name, old in candidates.items():
        current = report.get('packages', {}).get(name, {})
        status = current.get('status', 'unselected')
        if status == 'selected':
            if (source_key(old['url'], 'HEAD')[0] != source_key(current['url'], 'HEAD')[0]
                    or old.get('subdirectory', '') != current.get('subdirectory', '')):
                status = 'different-source'
            else:
                result[name] = {key: current[key] for key in ('url', 'ref', 'subdirectory', 'revision', 'manifestSha256')}
                if current.get('sourceKind') == 'dependency':
                    if old['current'] != current['revision']:
                        decisions[name] = 'different-installed-revision'
                        result.pop(name)
                        continue
                    result[name].update(sourceKind='dependency', previousRevision=old['current'])
                status = 'selected'
        decisions[name] = status
    return result, decisions


async def reconcile(manager, stage, project, collect):
    """Collect → check exact refs → align copied roots → recollect → qualify."""
    from .deployment import write_private
    from .runtime_environment import receipt_directory, update_inventory
    from .runtime_source_policy import with_origin
    from .runtime_source_alignment import align_checked_sources
    from .updates import active_release, process
    from .staged_sources import owned_path
    home, stage, project = Path(manager.home), Path(stage), Path(project)
    owned_path(stage, home / 'updates/releases')
    if (stage / 'runtime.lock').exists():
        raise ValueError('A recorded generation cannot reconcile update policy.')
    old_generation = active_release(home).get('current')
    rows = await update_inventory(home)
    request = pending(home, old_generation, rows, honor_receipt=False)
    old_receipt = receipt_directory(home, old_generation) / RECEIPT
    if request is None:
        # Carry the acknowledgment forward when another component prompted an
        # update. Otherwise an unresolved extra would prompt again next time.
        if old_receipt.exists():
            write_private(stage / RECEIPT, old_receipt.read_text())
        return
    separate = project / 'uv.toml'
    if separate.exists() or separate.is_symlink():
        return
    first = await collect()
    if first.get('version') != VERSION or first.get('configuration') != request['configuration']:
        raise ValueError('Selected source settings changed during policy reconciliation.')
    selected, decisions = selected_policies(request['candidates'], first)
    checked_path = stage / 'runtime-sources.json'
    checked = json.loads(checked_path.read_text())
    latest = {}
    for name, source in list(selected.items()):
        key = (source['url'], source['ref'])
        if key not in latest:
            latest[key] = await moving_revision(*key, process)
        revision = latest[key]
        if revision is None:
            decisions[name] = 'fixed-or-ambiguous-ref'
            selected.pop(name)
            continue
        if not re.fullmatch('[a-f0-9]{40}', revision):
            raise ValueError('A selected source has no exact checked revision.')
        selected[name] = {**source, 'revision': revision}
        checked[name] = with_origin({key: selected[name][key] for key in
                                   ('url', 'ref', 'subdirectory', 'revision')},
                                   'selected-declaration', manifestSha256=source['manifestSha256'])
    if selected:
        write_private(checked_path, json.dumps(checked, indent=2) + '\n')
        await align_checked_sources(manager, stage, selection=first)
        from .selected_runtime_dependencies import refresh
        await refresh(manager, stage, project, selected)
        second = await collect()
        verified, _ = selected_policies({name: {**old, 'current': selected[name]['revision']}
                                        if name in selected else old
                                        for name, old in request['candidates'].items()}, second)
        if second.get('configuration') != first['configuration'] or any(
                name not in verified or any(verified[name][field] != source[field]
                    for field in ('url', 'ref', 'subdirectory', 'revision')) for name, source in selected.items()):
            raise ValueError('The selected source changed after its checked refresh.')
        # New source manifests may differ from the old cached commit. Record
        # the current authored input; the existing freeze checks installed bytes.
        for name, source in selected.items():
            checked[name]['policyOrigin']['manifestSha256'] = verified[name]['manifestSha256']
        write_private(checked_path, json.dumps(checked, indent=2) + '\n')
    if active_release(home).get('current') != old_generation:
        raise ValueError('The active generation changed during policy reconciliation.')
    result = {'version': VERSION, 'selectionVersion': SELECTION_VERSION, 'configuration': request['configuration'],
              'decisions': {name: ('migrated' if name in selected else status) for name, status in decisions.items()},
              'remaining': {name: old for name, old in request['candidates'].items() if name not in selected},
              'offeredPackages': sorted(first.get('packages', {})),
              'migrated': {name: checked[name] for name in selected}}
    write_private(stage / RECEIPT, json.dumps(result, indent=2) + '\n')
