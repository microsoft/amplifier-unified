"""Source-group partial preparation: real Git, synthetic qualified-baseline seam.

These tests prove preparation/identity/guard plumbing, not full profile mounting.
The end-to-end qualification fixture separately owns a real working baseline.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import update_partial as partial, updates, update_health
from amplifier_web.runtime_source_alignment import verify_checked_selection
from amplifier_web.update_diagnostics import CommandFailure, CommandTimeout


def git(root, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=root, text=True,
                                   stderr=subprocess.DEVNULL).strip()


class Diagnostics:
    def __init__(self):
        self.state = {'attemptId': 'f'*32}
        self.calls = []
    async def run(self, phase, fn, *args, **kwargs):
        self.calls.append((phase, args))
        return await fn(*args, **kwargs)
    def record(self, phase, status, **facts):
        self.state['lastFailure'] = {'attemptId': 'f'*32, 'phase': phase, 'status': status, **facts}


@pytest.fixture
async def preparation(tmp_path, monkeypatch):
    home = tmp_path / 'app'; stage = home / 'updates/releases' / ('c'*32)
    stage.mkdir(parents=True)
    previous = home / 'updates/releases' / ('a'*32); previous.mkdir()
    pointer = home / 'updates/active.json'; pointer.write_text(json.dumps({'current': 'a'*32}))
    repos, old, new, rows, policies, graph = {}, {}, {}, [], {}, []
    for name in ('a', 'b', 'c'):
        repo = tmp_path / ('remote-' + name); repo.mkdir()
        url = 'https://example.invalid/' + name
        git(repo, 'init', '-b', 'stable' if name == 'b' else 'main')
        git(repo, 'config', 'user.name', 'Fixture'); git(repo, 'config', 'user.email', 'fixture@example.invalid')
        git(repo, 'remote', 'add', 'origin', url)
        def manifest(version, dependency):
            return '[project]\nname="fixture-' + name + '"\nversion="' + version + '"\ndependencies=' + json.dumps(dependency) + '\n'
        (repo / 'pyproject.toml').write_text(manifest('1.0', ['fixture-a>=1'] if name == 'c' else []))
        git(repo, 'add', '.'); git(repo, 'commit', '-m', 'old'); old[name] = git(repo, 'rev-parse', 'HEAD')
        (repo / 'pyproject.toml').write_text(manifest('2.0', ['fixture-a>=2'] if name == 'c' else []))
        git(repo, 'commit', '-am', 'new'); new[name] = git(repo, 'rev-parse', 'HEAD')
        ref = 'stable' if name == 'b' else 'main'
        for suffix in ('', '-duplicate') if name == 'a' else ('',):
            dest = stage / 'foundation/cache' / (name + suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            git(repo, 'clone', str(repo), str(dest)); git(dest, 'remote', 'set-url', 'origin', url)
            git(dest, 'checkout', '--detach', old[name])
            (dest / '.amplifier_cache_meta.json').write_text(json.dumps({'git_url': url, 'ref': ref, 'commit': old[name]}))
            rows.append({'id': name+suffix, 'kind': 'bundle / module', 'path': 'cache/'+name+suffix,
                         'url': url, 'ref': ref, 'current': old[name], 'latest': new[name],
                         'eligible': True, 'status': 'update', 'label': 'Source '+name, 'updateTier': 'included'})
        policies['fixture-'+name] = {'url': url, 'ref': ref, 'revision': old[name], 'subdirectory': ''}
        graph.append({'name': 'fixture-'+name, 'version': '1.0'})
        rows.append({'id': 'runtime:'+name, 'kind': 'runtime dependency', 'package': 'fixture-'+name,
                     'url': url, 'ref': ref, 'current': old[name], 'latest': new[name],
                     'eligible': True, 'status': 'update', 'label': 'Source '+name, 'updateTier': 'included'})
        repos[url] = repo
    (previous / 'runtime-sources.json').write_text(json.dumps(policies))
    (previous / 'runtime-installed.json').write_text(json.dumps(graph))
    # This explicit seam is not a claim of installed/profile verification.
    monkeypatch.setattr(update_health, 'active_evidence', lambda manager: {'generation': 'a'*32, 'verifiedAt': 1})
    monkeypatch.setattr(update_health, 'pointer_witness', lambda manager: json.loads(pointer.read_text()))
    async def collected(manager, stage, generation):
        import hashlib
        import tomllib
        manifests = {}
        for name in ('a', 'b', 'c'):
            content = (stage / 'foundation/cache' / name / 'pyproject.toml').read_bytes()
            manifests['fixture-'+name] = {'manifestSha256': hashlib.sha256(content).hexdigest(),
                                         'project': tomllib.loads(content.decode())['project']}
        return {'runtimeRequirements': manifests, 'batchPaths': {name: '/fixture' for name in manifests}}
    monkeypatch.setattr(partial, 'collect', collected)
    manager = SimpleNamespace(home=home, directory=home/'updates', inventory=rows, diagnostics=Diagnostics())
    async def publish(**kwargs): pass
    manager.publish = publish
    manager.stage_cache_sources = lambda stage, rows: updates.UpdateManager.stage_cache_sources(manager, stage, rows)
    original = updates.process; fetched = []
    async def process(*args, **kwargs):
        if 'fetch' in args:
            url = next(x for x in args if x in repos)
            fetched.append(url)
            if url.endswith('/a'):
                # Settled, exited read-side fetch, no checkout happened.
                raise CommandFailure({'exitCode': 128, 'durationMs': 1})
            args = tuple(str(repos[x]) if x in repos else x for x in args)
        return await original(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    return SimpleNamespace(manager=manager, stage=stage, rows=rows, old=old, new=new,
                           policies=policies, pointer=pointer, fetched=fetched, process=original)


async def test_failed_distinct_a_keeps_all_copies_holds_dependent_c_advances_b(preparation):
    p = preparation
    plan = await partial.stage_sources(p.manager, p.stage, p.rows)
    receipt = partial.read(p.stage)
    assert receipt['retained'] == {name: policy for name, policy in p.policies.items() if name != 'fixture-b'}
    assert p.fetched.count('https://example.invalid/a') == 1  # No duplicate failed acquisition.
    for name in ('a', 'a-duplicate', 'b', 'c'):
        base = name[0]
        assert git(p.stage/'foundation/cache'/name, 'rev-parse', 'HEAD') == (p.new[base] if base == 'b' else p.old[base])
    assert receipt['results']['a']['status'] == 'failed'
    assert receipt['results']['c']['status'] == 'dependency-held'
    assert receipt['results']['b']['status'] == 'awaiting-adoption'
    assert json.loads(p.pointer.read_text())['current'] == 'a'*32
    await plan.recheck()
    public = updates.group_sources(p.rows)
    applied = partial.adopted_items(public, receipt)
    assert next(r for r in applied if r['label'] == 'Source b')['current'] == p.new['b']
    assert all(r['status'] == 'check_failed' for r in applied if r['label'] in {'Source a', 'Source c'})
    assert 'https://' not in json.dumps(partial.public_results(receipt, adopted=True))


@pytest.mark.parametrize('change', ['baseline', 'dirty', 'drift', 'pin', 'unknown'])
async def test_partial_never_invents_retention_for_unsafe_inputs(preparation, monkeypatch, change):
    p = preparation
    if change == 'baseline': monkeypatch.setattr(update_health, 'active_evidence', lambda manager: {})
    if change == 'dirty': (p.stage/'foundation/cache/a/pyproject.toml').write_text('user edit')
    if change == 'drift': p.rows[0]['current'] = 'e'*40
    if change == 'pin': p.rows[0]['ref'] = p.old['a']
    if change == 'unknown':
        async def uncertain(*args, **kw):
            if 'fetch' in args: raise CommandTimeout(1)
            return await p.process(*args, **kw)
        monkeypatch.setattr(updates, 'process', uncertain)
    with pytest.raises((ValueError, CommandTimeout)):
        await partial.stage_sources(p.manager, p.stage, p.rows)
    assert not (p.stage/partial.RECEIPT).exists()
    assert json.loads(p.pointer.read_text())['current'] == 'a'*32


def test_candidate_only_exact_constraint_preserves_separate_branch_policy():
    import tomllib
    policy = {'url': 'https://example.invalid/a', 'ref': 'stable', 'revision': 'a'*40, 'subdirectory': ''}
    source = b'[project]\nname="worker"\nversion="1"\ndependencies=["a"]\n[tool.uv.sources]\na={git="https://example.invalid/a",branch="stable"}\n'
    candidate = tomllib.loads(partial.pin_manifest(source, {'a': policy}).decode())
    assert candidate['tool']['uv']['sources']['a']['rev'] == 'a'*40
    assert policy['ref'] == 'stable'
    selection = {'batchPaths': {'a': '/checked'}, 'packages': {'a': {**policy, 'status': 'selected'}}}
    verify_checked_selection({'a': policy}, selection, exact=True)
    selection['packages']['a']['revision'] = 'b'*40
    with pytest.raises(ValueError): verify_checked_selection({'a': policy}, selection, exact=True)


async def test_partial_receipt_blocks_unchanged_auto_retry_not_manual_or_new_revision(preparation):
    p = preparation
    await partial.stage_sources(p.manager, p.stage, p.rows)
    p.pointer.write_text(json.dumps({'current': p.stage.name}))
    for fresh, latest, expected in [(False, p.new['a'], 'check_failed'), (True, p.new['a'], 'update'), (False, 'e'*40, 'update')]:
        rows = [dict(r, latest=latest) for r in p.rows if r['id'] == 'a']
        partial.retained_after_check(p.manager, rows, fresh=fresh)
        assert rows[0]['status'] == expected
    assert partial.read(p.stage)['results']['a']['status'] == 'failed'
