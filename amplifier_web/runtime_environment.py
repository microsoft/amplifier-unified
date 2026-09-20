"""Branch-tracking worker dependencies, with a separate lock for each update.

A lock records an installed environment. It is not an update policy: the updater
compares its Git revisions with the branches in the manifest and refreshes it in
an isolated generation. Checks never create or modify an environment.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tomllib
from urllib.parse import urlsplit


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


def project_path(home, generation=None):
    digest = hashlib.sha256(manifest_content(home, generation))
    if generation:
        digest.update(generation.encode())
    return Path(home) / 'runtime' / digest.hexdigest()[:16]


def prepare_project(home, generation=None):
    project = project_path(home, generation)
    project.mkdir(parents=True, exist_ok=True)
    content = manifest_content(home, generation)
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


def inventory(home):
    from .updates import active_release, safe_label
    generation = active_release(home).get('current')
    project = project_path(home, generation)
    recorded = receipt_directory(home, generation) / 'runtime.lock'
    locked = locked_sources(project) if (project / 'uv.lock').exists() or not recorded.exists() else locked_sources(recorded)
    sources = tomllib.loads(manifest_path().read_text())['tool']['uv']['sources']
    rows = []
    if manifest_content(home, generation) != manifest_path().read_bytes():
        rows.append({'id': 'runtime:environment', 'kind': 'runtime environment',
                     'label': 'Conversation worker environment', 'status': 'update', 'eligible': True,
                     'current': hashlib.sha256(manifest_content(home, generation)).hexdigest(),
                     'latest': hashlib.sha256(manifest_path().read_bytes()).hexdigest(),
                     'usage': 'configured', 'usageEvidence': ['Updated application dependency declarations']})
    for name, source in sources.items():
        ref = source.get('branch') or source.get('rev')
        if not source.get('git') or not ref:
            continue
        installed = locked.get(name)
        if not installed:
            continue  # A cold environment downloads its branches on first use.
        rows.append({'id': 'runtime:' + name, 'package': name, 'kind': 'runtime dependency',
                     'label': safe_label(source['git']), 'url': source['git'], 'ref': ref,
                     'current': installed.fragment, 'status': 'not_checked',
                     'eligible': True, 'usage': 'configured',
                     'usageEvidence': ['Conversation worker environment']})
    return rows


async def stage(manager, generation, candidates):
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
    project = prepare_project(manager.home, generation)
    receipt = receipt_directory(manager.home, generation) / 'runtime.lock'
    baseline = receipt if receipt.exists() else previous_receipt if previous_receipt.exists() else current / 'uv.lock'
    if baseline.exists():
        shutil.copy2(baseline, project / 'uv.lock')
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv to prepare the Amplifier runtime.')
    # Revalidating a rollback or an older staged generation must not upgrade it.
    selected = [] if receipt.exists() else candidates
    baseline_sources = locked_sources(project)
    for row in selected:
        if row.get('kind') == 'runtime dependency':
            installed = baseline_sources.get(row['package'])
            if not installed or installed.fragment != row['current']:
                raise ValueError('Runtime dependencies changed since checking; check for updates again.')
    packages = sorted({row['package'] for row in selected if row.get('kind') == 'runtime dependency'})
    # A new generation also needs a lock when only bundle/module caches changed.
    flags = ['--locked'] if receipt.exists() else [arg for name in packages for arg in ('--upgrade-package', name, '--refresh-package', name)]
    await manager.diagnostics.run('ecosystem-runtime-lock', process, uv, 'lock', '--project', str(project),
                                  '--python', '3.13', *flags, timeout=900)
    locked = locked_sources(project)
    for row in selected:
        if row.get('kind') == 'runtime dependency':
            installed = locked.get(row['package'])
            if not installed or installed.fragment != row['latest']:
                raise ValueError('A runtime branch moved after checking; check for updates again.')
    if not receipt.exists():
        shutil.copy2(project / 'pyproject.toml', receipt.with_name('runtime.toml'))
        shutil.copy2(project / 'uv.lock', receipt)
    return project
