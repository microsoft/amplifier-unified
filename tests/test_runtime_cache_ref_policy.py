"""Bounded diagnostic probe; no application source or active generation changes."""
import json
import shutil
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_qualification as qualification
from amplifier_web import updates
from amplifier_foundation.sources.shared import SharedSourceStore, build_view
from test_runtime_source_policy_resolver import BACKEND, Diagnostics, git


@pytest.mark.parametrize('route', ['legacy-cache', 'shared-build'])
@pytest.mark.parametrize('topology', ['tag-only', 'same-name', 'main-branch'])
async def test_owned_cache_ref_needs_branch_kind(tmp_path, monkeypatch, route, topology):
    uv = shutil.which('uv')
    assert uv
    repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture'); git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture-component"\nversion="1.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (repo / 'backend.py').write_text(BACKEND + '\nbuild_editable = build_wheel\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'fixture')
    revision = git(repo, 'rev-parse', 'HEAD')
    if topology != 'main-branch':
        git(repo, 'tag', 'stable')
    if topology == 'same-name':
        git(repo, 'branch', 'stable')
    ref = 'main' if topology == 'main-branch' else 'stable'
    url = repo.as_uri()
    home, generation = tmp_path / 'app', 'c' * 32
    cache = environments.receipt_directory(home, generation) / 'foundation/cache'
    cache.mkdir(parents=True)
    if route == 'shared-build':
        store = SharedSourceStore(home / 'source-store')
        source = await store.bind(cache, url, ref, revision, existing=repo)
        installed, _ = build_view(source)
    else:
        installed = cache / 'fixture'
        shutil.copytree(repo, installed)
        (installed / '.amplifier_cache_meta.json').write_text(json.dumps({
            'git_url': url, 'ref': ref, 'commit': revision}))
    data = {'project': {'name': 'worker', 'version': '1', 'dependencies': ['fixture-component']},
            'tool': {'uv': {'sources': {'fixture-component': {'path': str(installed), 'editable': True}}}}}
    content = ('\n'.join(json.dumps(key) + ' = ' + environments.toml_value(value)
                         for key, value in data.items()) + '\n').encode()
    manifest = tmp_path / 'authored.toml'; manifest.write_bytes(content)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(home / 'source-store'))
    project = environments.prepare_project(home, generation)
    await updates.process(uv, 'sync', '--project', str(project), '--python', sys.executable, timeout=90)
    graph = qualification.installed_graph(project)
    row = next(row for row in graph if row['name'] == 'fixture-component')
    assert row['cacheSource']['revision'] == revision and not row['cacheSource']['dirty']
    final = await qualification.freeze(SimpleNamespace(home=home, diagnostics=Diagnostics()), generation, project)
    receipt = environments.receipt_directory(home, generation)
    qualification.verify_recorded(final, receipt)
    saved = json.loads((receipt / 'runtime-sources.json').read_text())['fixture-component']
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    offered = next(row for row in environments.inventory(home) if row.get('package') == 'fixture-component')
    assert saved['revision'] == revision
    assert offered['eligible'] is (topology == 'main-branch'), (route, topology, saved, offered['eligible'])


@pytest.mark.parametrize('outcome,expected', [('branch', 'verified-cache-branch'), ('changed', 'unresolved-reference'),
    ('tag', 'tag-reference'), ('ambiguous', 'unresolved-reference'), ('offline', 'unresolved-reference')])
async def test_qualification_groups_source_proof_and_preserves_installed_revision(outcome, expected):
    from amplifier_web.runtime_source_policy import qualify_cache_policies, with_origin
    revision, url = 'a' * 40, 'https://example.invalid/repo'
    policies = {name: with_origin({'url': url, 'ref': 'custom', 'revision': revision, 'subdirectory': name},
                                 'owned-cache-declaration') for name in ('one', 'two')}
    calls = []
    async def runner(*args, **kwargs):
        calls.append(args)
        if outcome == 'offline':
            raise RuntimeError('offline')
        branch = ('b' * 40 if outcome == 'changed' else revision) + '\trefs/heads/custom'
        tag = revision + '\trefs/tags/custom'
        return tag if outcome == 'tag' else branch + '\n' + tag if outcome == 'ambiguous' else branch
    result = await qualify_cache_policies(policies, {}, runner)
    assert len(calls) == 1 and set(calls[0][3:]) == {'refs/heads/custom', 'refs/tags/custom'}
    for name, policy in result.items():
        assert policy['revision'] == revision and policy['subdirectory'] == name
        assert policy['ref'] == ('custom' if outcome == 'branch' else revision)
        assert policy['policyOrigin']['kind'] == expected


async def test_qualification_does_not_reinterpret_existing_checked_policy():
    from amplifier_web.runtime_source_policy import qualify_cache_policies, with_origin
    saved = with_origin({'url': 'https://example.invalid/repo', 'ref': 'stable', 'revision': 'a' * 40, 'subdirectory': ''},
                        'owned-shared-binding')
    async def forbidden(*args, **kwargs):
        raise AssertionError('Checked policy must not be reinterpreted')
    assert await qualify_cache_policies({'one': saved}, {'one': saved}, forbidden) == {'one': saved}


async def test_cancelled_qualification_cancels_owned_ref_queries():
    import asyncio
    from amplifier_web.runtime_source_policy import qualify_cache_policies, with_origin
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def runner(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    policies = {'one': with_origin({'url': 'https://example.invalid/repo', 'ref': 'custom',
        'revision': 'a' * 40, 'subdirectory': ''}, 'owned-cache-declaration')}
    task = asyncio.create_task(qualify_cache_policies(policies, {}, runner))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_qualification_uses_existing_five_probe_bound_without_check_scope():
    import asyncio
    from amplifier_web.update_checks import GIT_CHECK_CONCURRENCY
    from amplifier_web.runtime_source_policy import qualify_cache_policies, with_origin
    revision, entered, release = 'a' * 40, asyncio.Event(), asyncio.Event()
    active = maximum = started = 0
    async def runner(*args, **kwargs):
        nonlocal active, maximum, started
        active += 1; started += 1; maximum = max(maximum, active)
        if active == GIT_CHECK_CONCURRENCY:
            entered.set()
        try:
            await release.wait()
            return revision + '\trefs/heads/custom'
        finally:
            active -= 1
    policies = {str(i): with_origin({'url': 'https://example.invalid/repo' + str(i),
        'ref': 'custom', 'revision': revision, 'subdirectory': ''}, 'owned-cache-declaration') for i in range(9)}
    task = asyncio.create_task(qualify_cache_policies(policies, {}, runner))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        assert active == started == GIT_CHECK_CONCURRENCY == 5
    finally:
        release.set()
    result = await task
    assert started == len(result) == 9 and maximum == 5 and active == 0
