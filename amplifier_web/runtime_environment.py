"""Branch-tracking worker dependencies, with a separate lock for each update.

A lock records an installed environment. It is not an update policy: the updater
compares its Git revisions with the branches in the manifest and refreshes it in
an isolated generation. Checks never create or modify an environment.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import shutil
import tomllib
from urllib.parse import parse_qs, urlsplit, urlunsplit


def manifest_path():
    return Path(__file__).with_name('runtime_deps') / 'pyproject.toml'


def receipt_directory(home, generation=None):
    root = Path(home) / 'updates'
    return root / 'releases' / generation if generation else root / 'baseline-runtime'


def manifest_content(home, generation=None):
    recorded = receipt_directory(home, generation) / 'runtime.toml'
    if recorded.exists():
        return recorded.read_bytes()
    return manifest_path().read_bytes()


def project_path(home, generation=None, *, content=None):
    digest = hashlib.sha256(manifest_content(home, generation) if content is None else content)
    if generation:
        digest.update(generation.encode())
    return Path(home) / 'runtime' / digest.hexdigest()[:16]


def prepare_project(home, generation=None, *, content=None):
    content = manifest_content(home, generation) if content is None else content
    project = project_path(home, generation, content=content)
    project.mkdir(parents=True, exist_ok=True)
    target = project / 'pyproject.toml'
    if not target.exists() or target.read_bytes() != content:
        from .deployment import write_private
        write_private(target, content.decode())
    receipt = receipt_directory(home, generation) / 'runtime.lock'
    if receipt.exists():
        lock = project / 'uv.lock'
        if lock.exists() and lock.read_bytes() != receipt.read_bytes():
            raise ValueError('The recorded worker lock changed; its update receipt was preserved.')
        if not lock.exists():
            shutil.copy2(receipt, lock)
    return project


def locked_sources(project):
    project = Path(project)
    path = project if project.is_file() else project / 'uv.lock'
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    return {row['name']: urlsplit(row['source']['git']) for row in data.get('package', [])
            if row.get('source', {}).get('git')}


def package_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def installed_sources(project):
    """Read worker metadata without importing modules or executing its Python.

    The lock cannot describe modules installed later by Foundation. Conversely a
    lock entry must not conceal an installed local override or a different Git
    revision. PEP 610 metadata is the installed source of truth when available.
    """
    from .runtime_qualification import installed_graph
    result = {}
    for record in installed_graph(project):
        name = record['name']
        if not name.startswith('amplifier-'):
            continue
        direct = record.get('directUrl') or {}
        vcs = direct.get('vcs_info') or {}
        cached = record.get('cacheSource')
        if cached:
            result[name] = {'url': cached['url'], 'ref': cached['ref'], 'current': cached['revision'],
                            'subdirectory': cached['subdirectory'] if cached['subdirectory'] != '.' else '',
                            'provenance': 'installed Foundation cache module', 'cacheManaged': True,
                            **({'override': True} if cached['dirty'] else {})}
        elif vcs.get('vcs') == 'git':
            result[name] = {'url': direct['url'], 'ref': vcs.get('requested_revision', ''),
                            'current': vcs.get('commit_id', ''),
                            'subdirectory': direct.get('subdirectory', ''), 'provenance': 'installed Git distribution'}
        else:
            result[name] = {'current': record['version'], 'provenance': 'installed local or registry override',
                            'override': True}
    return result


def inventory(home):
    from .updates import active_release, safe_label, pinned
    generation = active_release(home).get('current')
    project = project_path(home, generation)
    recorded = receipt_directory(home, generation) / 'runtime.lock'
    locked = locked_sources(project) if (project / 'uv.lock').exists() or not recorded.exists() else locked_sources(recorded)
    sources = tomllib.loads(manifest_path().read_text()).get('tool', {}).get('uv', {}).get('sources', {})
    sources = {package_name(name): source for name, source in sources.items()}
    policies = receipt_directory(home, generation) / 'runtime-sources.json'
    recorded_policies = json.loads(policies.read_text()) if policies.exists() else {}
    observed = {}
    for name, resolved in locked.items():
        name = package_name(name)
        if name not in sources and not name.startswith('amplifier-'):
            continue
        query = parse_qs(resolved.query)
        source = sources.get(name, {})
        policy = recorded_policies.get(name, {})
        if policy.get('url') != urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', '')):
            policy = {}
        observed[name] = {'url': urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', '')),
                          'ref': policy.get('ref') or next(iter(query.get('branch') or query.get('rev') or query.get('tag') or ['']), '') or source.get('branch') or source.get('rev') or '',
                          'current': resolved.fragment, 'subdirectory': next(iter(query.get('subdirectory', [''])), ''),
                          'provenance': 'worker resolution lock'}
    for name, source in installed_sources(project).items():
        resolved = locked.get(name)
        policy = recorded_policies.get(name)
        if (policy and resolved and source.get('current') == resolved.fragment
                and source.get('url') == policy.get('url') and source.get('subdirectory', '') == policy.get('subdirectory', '')):
            source['ref'] = policy['ref']
        observed[name] = source
    rows = []
    base = receipt_directory(home, generation) / 'runtime-base.toml'
    baseline = base.read_bytes() if base.exists() else manifest_content(home, generation)
    if baseline != manifest_path().read_bytes():
        rows.append({'id': 'runtime:environment', 'kind': 'runtime environment',
                     'label': 'Conversation worker environment', 'status': 'update', 'eligible': True,
                     'current': hashlib.sha256(baseline).hexdigest(),
                     'latest': hashlib.sha256(manifest_path().read_bytes()).hexdigest(),
                     'usage': 'configured', 'usageEvidence': ['Updated application dependency declarations']})
    for name, source in sorted(observed.items()):
        ref = source.get('ref', '')
        override = source.get('override', False)
        eligible = bool(not override and ref and not pinned(ref) and source.get('current'))
        rows.append({'id': 'runtime:' + name, 'package': name, 'kind': 'runtime dependency',
                     'label': safe_label(source['url']) if source.get('url') else name,
                     **source, 'ref': ref, 'status': 'not_checked' if eligible else 'local' if override else 'pinned',
                     'eligible': eligible, 'usage': 'configured',
                     'usageEvidence': ['Conversation worker environment', source['provenance']]})
    return rows


def toml_value(value):
    """Serialize the app-owned generated manifest's basic TOML values."""
    if isinstance(value, dict):
        return '{ ' + ', '.join(json.dumps(key) + ' = ' + toml_value(item) for key, item in value.items()) + ' }'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError('Unsupported generated runtime manifest value')


def augmented_manifest(content, rows):
    """Carry actual transitive Git sources into a new generation's resolver.

    Sources remain branches. Resolved revisions live only in the lock/receipt.
    Explicit installed Git overrides retain their source/ref. Configured local
    modules still belong to the bundle loader; never guess an upstream for them.
    """
    data = tomllib.loads(content.decode())
    sources = data.get('tool', {}).get('uv', {}).get('sources', {})
    declared = {package_name(name): name for name in sources}
    references, changed = [], False
    for row in rows:
        if row.get('kind') != 'runtime dependency':
            continue
        name = row['package']
        if row.get('override') and (name in declared or row.get('cacheManaged')):
            raise ValueError('A declared worker dependency has an installed local or registry override; preserve its source configuration before updating.')
        if row.get('override') or not row.get('url') or not row.get('ref'):
            continue
        if name in declared:
            source = sources[declared[name]]
            # Preserve a concrete installed override rather than silently
            # replacing it with the app's default repository/branch.
            if row.get('provenance') == 'installed Git distribution' and (
                source.get('git') != row['url'] or (source.get('branch') or source.get('rev')) != row['ref']
                or source.get('subdirectory', '') != row.get('subdirectory', '')
            ):
                sources[declared[name]] = {'git': row['url'], 'rev': row['ref'],
                                          **({'subdirectory': row['subdirectory']} if row.get('subdirectory') else {})}
                changed = True
            continue
        uri = 'git+' + row['url'] + '@' + row['ref']
        if row.get('subdirectory'):
            uri += '#subdirectory=' + row['subdirectory']
        references.append(name + ' @ ' + uri)
    if references:
        uv = data.setdefault('tool', {}).setdefault('uv', {})
        # This is the app-owned runtime manifest, never a user's project.
        if 'dependency-groups' in data or 'default-groups' in uv:
            raise ValueError('The runtime manifest already manages dependency groups; review its update policy.')
        uv['default-groups'] = ['unified-managed-runtime']
        data['dependency-groups'] = {'unified-managed-runtime': sorted(set(references))}
        changed = True
    if not changed:
        return content
    return ('# Generated worker sources; resolved revisions are recorded separately.\n' +
            '\n'.join(json.dumps(key) + ' = ' + toml_value(value) for key, value in data.items()) + '\n').encode()


def matches_resolution(resolved, row):
    if resolved is None or resolved.fragment != row['latest']:
        return False
    query = parse_qs(resolved.query)
    ref = next(iter(query.get('branch') or query.get('rev') or query.get('tag') or ['']), '')
    url = urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', ''))
    return url == row['url'] and ref == row['ref'] and next(iter(query.get('subdirectory', [''])), '') == row.get('subdirectory', '')


async def stage(manager, generation, candidates, *, finalize=True):
    """Refresh the new environment only; retain the old lock for rollback."""
    from .updates import active_release, process
    previous = active_release(manager.home).get('current')
    current = project_path(manager.home, previous)
    previous_receipt = receipt_directory(manager.home, previous) / 'runtime.lock'
    # The pre-updater environment is also a rollback target (pointer = null).
    if previous is None and not previous_receipt.exists() and (current / 'uv.lock').exists():
        previous_receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(current / 'pyproject.toml', previous_receipt.with_name('runtime.toml'))
        shutil.copy2(current / 'uv.lock', previous_receipt)
    receipt = receipt_directory(manager.home, generation) / 'runtime.lock'
    observed = inventory(manager.home) if not receipt.exists() else []
    content = manifest_content(manager.home, generation) if receipt.exists() else augmented_manifest(manifest_path().read_bytes(), observed)
    project = prepare_project(manager.home, generation, content=content)
    baseline = receipt if receipt.exists() else previous_receipt if previous_receipt.exists() else current / 'uv.lock'
    if baseline.exists():
        shutil.copy2(baseline, project / 'uv.lock')
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv to prepare the Amplifier runtime.')
    # Revalidating a rollback or an older staged generation must not upgrade it.
    selected = [] if receipt.exists() else candidates
    baseline_sources = {row['package']: row for row in observed if row.get('kind') == 'runtime dependency'}
    for row in selected:
        if row.get('kind') == 'runtime dependency':
            installed = baseline_sources.get(row['package'])
            if not installed or any(installed.get(key, '') != row.get(key, '') for key in ('current', 'url', 'ref', 'subdirectory')):
                raise ValueError('Runtime dependencies changed since checking; check for updates again.')
    from .updates import pinned
    declared = tomllib.loads(content.decode()).get('tool', {}).get('uv', {}).get('sources', {})
    packages = sorted({row['package'] for row in observed if row.get('kind') == 'runtime dependency' and row.get('eligible')} |
                      {name for name, source in declared.items() if source.get('git') and not pinned(source.get('branch') or source.get('rev') or '')})
    # A new generation also needs a lock when only bundle/module caches changed.
    flags = ['--locked'] if receipt.exists() else [arg for name in packages for arg in ('--upgrade-package', name, '--refresh-package', name)]
    await manager.diagnostics.run('ecosystem-runtime-lock', process, uv, 'lock', '--project', str(project),
                                  '--python', '3.13', *flags, timeout=900)
    locked = locked_sources(project)
    for row in selected:
        if row.get('kind') == 'runtime dependency':
            installed = locked.get(row['package'])
            if not matches_resolution(installed, row):
                raise ValueError('A runtime branch moved after checking; check for updates again.')
    if finalize and not receipt.exists():
        receipt.with_name('runtime-base.toml').write_bytes(manifest_path().read_bytes())
        shutil.copy2(project / 'pyproject.toml', receipt.with_name('runtime.toml'))
        shutil.copy2(project / 'uv.lock', receipt)
    return project
