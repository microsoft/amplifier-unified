import asyncio
import json

import pytest

from amplifier_web import updates
from amplifier_web.update_checks import CheckCache, checking, git_revision, ref_policy, moving_revision
from test_updates import app, repo, prepare, git


@pytest.mark.parametrize('topology', ['tag-only', 'same-name', 'main-branch', 'offline'])
async def test_real_cache_inventory_check_never_offers_an_ambiguous_ref(app, repo, monkeypatch, topology):
    manager, root = await prepare(app, repo)
    ref = 'main' if topology in {'main-branch', 'offline'} else 'stable'
    if topology in {'tag-only', 'same-name'}:
        git(repo[0], 'tag', 'stable', repo[1])
    if topology == 'same-name':
        git(repo[0], 'branch', 'stable', repo[2])
    meta = root / '.amplifier_cache_meta.json'
    data = json.loads(meta.read_text()); data['ref'] = ref; meta.write_text(json.dumps(data))
    original, calls = updates.process, []
    async def runner(*args, **kwargs):
        if args[:2] == ('git', 'ls-remote'):
            calls.append(args)
            if topology == 'offline':
                raise RuntimeError('offline')
            args = (*args[:2], str(repo[0]), *args[3:])
        return await original(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', runner)
    before = (git(root, 'rev-parse', 'HEAD'), meta.read_bytes())
    await manager.check(tier='other')
    row = next(row for row in manager.inventory if row.get('kind') == 'bundle / module')
    if topology == 'main-branch':
        assert row['status'] == 'update' and row['latest'] == repo[2]
    elif topology == 'offline':
        assert row['status'] == 'check_failed'
    else:
        assert row['status'] == 'pinned' and not row['eligible'] and 'latest' not in row
    assert len(calls) == 1
    assert set(calls[0][3:]) == {'refs/heads/' + ref, 'refs/tags/' + ref}
    assert (git(root, 'rev-parse', 'HEAD'), meta.read_bytes()) == before
    if topology == 'same-name':
        git(repo[0], 'tag', '-d', 'stable')
        await manager.check(tier='other')
        current = next(row for row in manager.inventory if row.get('kind') == 'bundle / module')
        assert current['status'] == 'update' and current['latest'] == repo[2]
        assert current['eligible'] and len(calls) == 2
        assert (git(root, 'rev-parse', 'HEAD'), meta.read_bytes()) == before


async def test_same_scope_joins_proof_but_old_branch_only_cache_is_not_proof(tmp_path):
    cache, calls = CheckCache(tmp_path), []
    async def runner(*args, **kwargs):
        calls.append(args)
        return '\n'.join('a' * 40 + '\t' + name for name in args[3:])
    async with checking(cache):
        assert await git_revision('https://example.invalid/repo', 'custom', runner) == 'a' * 40
        results = await asyncio.gather(*(ref_policy('https://example.invalid/repo', 'custom', runner) for _ in range(3)))
        assert results == [{'kind': 'ambiguous', 'revision': 'a' * 40}] * 3
        assert await moving_revision('https://example.invalid/repo', 'custom', runner) is None
    assert len(calls) == 2
    await cache.close()


async def test_proof_cache_obeys_git_access_and_fresh_refresh(tmp_path):
    cache, calls = CheckCache(tmp_path), []
    async def runner(*args, env, **kwargs):
        calls.append(env)
        revision = ('a' if env['GIT_CONFIG_GLOBAL'] == 'one' else 'b') * 40
        return revision + '\trefs/heads/custom'
    async with checking(cache):
        values = await asyncio.gather(*(ref_policy('https://example.invalid/repo', 'custom', runner,
            {'GIT_CONFIG_GLOBAL': value}) for value in ('one', 'two')))
    assert [value['revision'] for value in values] == ['a' * 40, 'b' * 40]
    assert len(calls) == 2
    async with checking(cache, fresh=False):
        await ref_policy('https://example.invalid/repo', 'custom', runner, {'GIT_CONFIG_GLOBAL': 'one'})
    assert len(calls) == 2
    async with checking(cache, fresh=True):
        await ref_policy('https://example.invalid/repo', 'custom', runner, {'GIT_CONFIG_GLOBAL': 'one'})
    assert len(calls) == 3
    assert 'GIT_CONFIG_GLOBAL' not in cache.path.read_text()
    await cache.close()


async def test_failed_fresh_proof_does_not_reuse_old_success(tmp_path):
    cache = CheckCache(tmp_path)
    async def runner(*args, **kwargs):
        return 'a' * 40 + '\trefs/heads/custom'
    async def failed(*args, **kwargs):
        raise RuntimeError('offline')
    async with checking(cache):
        await ref_policy('https://example.invalid/repo', 'custom', runner)
    async with checking(cache):
        with pytest.raises(RuntimeError):
            await ref_policy('https://example.invalid/repo', 'custom', failed)
    assert not cache.entries
    await cache.close()


@pytest.mark.parametrize('ref,kind', [('refs/heads/release/custom', 'branch'),
                                    ('refs/tags/release/custom', 'tag'),
                                    ('release/custom', 'ambiguous')])
async def test_real_explicit_ref_namespace_is_authoritative(repo, ref, kind):
    git(repo[0], 'branch', 'release/custom', repo[2])
    git(repo[0], 'tag', 'release/custom', repo[1])
    calls = []
    async def runner(*args, **kwargs):
        calls.append(args)
        return await updates.process(*args, **kwargs)
    result = await ref_policy(repo[0].as_uri(), ref, runner)
    assert result['kind'] == kind
    assert result['revision'] == (repo[2] if kind == 'branch' else repo[1])
    from amplifier_web.runtime_policy_reconciliation import moving_revision as legacy_revision
    assert await legacy_revision(repo[0].as_uri(), ref, updates.process) == (repo[2] if kind == 'branch' else None)
    assert set(calls[0][3:]) == ({ref} if ref.startswith('refs/') else
        {'refs/heads/release/custom', 'refs/tags/release/custom'})


@pytest.mark.parametrize('topology,authored', [('tag-only', False), ('same-name', False),
                                                ('main-branch', False), ('same-name', True)])
async def test_real_direct_installed_git_check_respects_provenance(app, repo, monkeypatch, topology, authored):
    import shutil
    import sys
    from amplifier_web import runtime_environment as environments
    from amplifier_web import runtime_qualification as qualification
    from test_runtime_source_policy_resolver import BACKEND
    uv = shutil.which('uv')
    assert uv
    remote = repo[0]
    (remote / 'pyproject.toml').write_text('[project]\nname="fixture-component"\nversion="1.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (remote / 'backend.py').write_text(BACKEND)
    git(remote, 'add', '.'); git(remote, 'commit', '-m', 'installable fixture')
    installed_revision = git(remote, 'rev-parse', 'HEAD')
    ref = 'main' if topology == 'main-branch' else 'stable'
    if topology != 'main-branch':
        git(remote, 'tag', ref)
    if topology == 'same-name':
        git(remote, 'branch', ref)
    manifest = remote.parent / 'worker.toml'
    manifest.write_text('[project]\nname="worker"\nversion="1"\n' +
        ('dependencies=["fixture-component"]\n[tool.uv.sources]\nfixture-component={git="' + remote.as_uri() + '",branch="stable"}\n' if authored else ''))
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    monkeypatch.setenv('UV_CACHE_DIR', str(remote.parent / 'uv-cache'))
    generation = 'd' * 32 if authored else None
    project = environments.prepare_project(app.data_dir, generation)
    await updates.process(uv, 'sync', '--project', str(project), '--python', sys.executable, timeout=90)
    if not authored:
        await updates.process(uv, 'pip', 'install', '--python', str(project / '.venv/bin/python'),
            'git+' + remote.as_uri() + '@' + ref, timeout=90)
    else:
        project = await qualification.freeze(app.update_manager, generation, project)
        (app.data_dir / 'updates/active.json').write_text(json.dumps({'current': generation}))
    installed = qualification.installed_graph(project)
    item = next(item for item in installed if item['name'] == 'fixture-component')
    assert item['directUrl']['vcs_info']['requested_revision'] == (installed_revision if authored else ref)
    assert item['directUrl']['vcs_info']['commit_id'] == installed_revision
    if topology == 'same-name':
        git(remote, 'checkout', 'stable')
    (remote / 'bundle.md').write_text('new upstream commit')
    git(remote, 'commit', '-am', 'new upstream commit')
    latest = git(remote, 'rev-parse', 'HEAD')
    before = next(row for row in environments.inventory(app.data_dir) if row.get('package') == 'fixture-component')
    assert before['eligible'] and not before.get('cacheManaged')
    if authored:
        assert before['policyOrigin']['kind'] == 'prepared-manifest'
    else:
        assert not before.get('policyOrigin')
    assert before['provenance'] == 'installed Git distribution'
    await app.update_manager.check(tier='other')
    row = next(row for row in app.update_manager.inventory if row.get('package') == 'fixture-component')
    if topology == 'main-branch' or authored:
        assert row['status'] == 'update' and row['latest'] == latest
    else:
        assert row['status'] == 'pinned' and not row['eligible'] and 'latest' not in row
    assert qualification.installed_graph(project) == installed
