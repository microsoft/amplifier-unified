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


@pytest.mark.parametrize('same_tag', [False, True])
async def test_explicit_heads_ref_uses_exact_branch_and_same_name_tag(same_tag):
    calls = []
    async def runner(*args, **kwargs):
        calls.append(args)
        return 'a' * 40 + '\trefs/heads/release/custom' + ('\n' + 'b' * 40 + '\trefs/tags/release/custom' if same_tag else '')
    result = await ref_policy('https://example.invalid/repo', 'refs/heads/release/custom', runner)
    assert set(calls[0][3:]) == {'refs/heads/release/custom', 'refs/tags/release/custom'}
    assert result['kind'] == ('ambiguous' if same_tag else 'branch')
