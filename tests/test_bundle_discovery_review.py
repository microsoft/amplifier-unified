"""Discovery is a review receipt, not a permanent source pin."""
import asyncio
import hashlib
import subprocess

import pytest

from amplifier_web import bundles


def git(path, *args):
    return subprocess.check_output(['git', *args], cwd=path, stderr=subprocess.DEVNULL, text=True).strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    remote = tmp_path / 'remote'
    remote.mkdir()
    git(remote, 'init', '-b', 'main')
    git(remote, 'config', 'user.name', 'Fixture')
    git(remote, 'config', 'user.email', 'fixture@example.invalid')
    document = b'---\nbundle:\n  name: reviewed\n  description: Original reviewed description\n---\nInstructions.\n'
    (remote / 'bundle.md').write_bytes(document)
    git(remote, 'add', '.')
    git(remote, 'commit', '-m', 'reviewed')
    url = 'https://example.invalid/amplifier-reviewed'
    calls = []
    async def local_git(*args, cwd=None, timeout=60):
        # Real clone/fetch/blob/ls-remote, restricted to this synthetic fixture.
        mapped = [str(remote) if arg == url else arg for arg in args]
        calls.append(args)
        process = await asyncio.create_subprocess_exec('git', *mapped, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, error = await process.communicate()
        if process.returncode:
            raise ValueError(error.decode())
        return output
    monkeypatch.setattr(bundles, 'git', local_git)
    return remote, url, document, calls


async def test_branch_registration_records_exact_blob_without_freezing_future_source(repository, tmp_path):
    remote, url, document, calls = repository
    manager = bundles.BundleManager(tmp_path / 'app')
    result = await manager.perform('bundle.discover', {'url': url})
    candidate = result['discovery']['candidates'][0]
    assert candidate['uri'] == 'git+' + url + '@main#subdirectory=bundle.md'
    assert candidate['revision'] == git(remote, 'rev-parse', 'HEAD')
    assert candidate['blob'] == git(remote, 'rev-parse', 'HEAD:bundle.md')
    assert candidate['sha256'] == hashlib.sha256(document).hexdigest()
    assert candidate['ref'] == 'main' and candidate['inspectedAt']
    added = await manager.perform('bundles.add', {**candidate, 'workspace': str(tmp_path), 'role': 'standalone'})
    saved = next(row for row in added['bundles'] if row['uri'] == candidate['uri'])
    assert saved['sourceReview']['revision'] == candidate['revision']
    assert saved['sourceReview']['sha256'] == candidate['sha256']
    assert manager.store.read(tmp_path)['bundle']['added']['reviewed'] == candidate['uri']
    assert any(args[0] == 'ls-remote' for args in calls)


async def test_branch_drift_between_inspect_and_add_preserves_settings(repository, tmp_path):
    remote, url, document, calls = repository
    manager = bundles.BundleManager(tmp_path / 'app')
    candidate = (await manager.discover(url))['candidates'][0]
    before = manager.store.read(tmp_path)
    (remote / 'bundle.md').write_bytes(document + b'Changed after review.\n')
    git(remote, 'commit', '-am', 'changed')
    with pytest.raises(ValueError, match='changed after browsing'):
        await manager.perform('bundles.add', {**candidate, 'workspace': str(tmp_path), 'role': 'standalone'})
    assert manager.store.read(tmp_path) == before
    with pytest.raises(ValueError, match='changed after browsing'):
        await manager.perform('bundles.add', {'uri': candidate['uri'], 'workspace': str(tmp_path), 'role': 'standalone'})
    assert manager.store.read(tmp_path) == before
    refreshed = (await manager.discover(url))['candidates'][0]
    assert refreshed['revision'] != candidate['revision']
    await manager.perform('bundles.add', {**refreshed, 'workspace': str(tmp_path), 'role': 'standalone'})


async def test_review_token_cannot_authorize_another_uri_or_survive_missing_receipt(repository, tmp_path):
    remote, url, document, calls = repository
    manager = bundles.BundleManager(tmp_path / 'app')
    candidate = (await manager.discover(url))['candidates'][0]
    before = manager.store.read(tmp_path)
    with pytest.raises(ValueError, match='Browse this repository again'):
        await manager.perform('bundles.add', {**candidate, 'uri': 'git+' + url + '@other#subdirectory=bundle.md'})
    restarted = bundles.BundleManager(tmp_path / 'app')
    with pytest.raises(ValueError, match='Browse this repository again'):
        await restarted.perform('bundles.add', candidate)
    assert manager.store.read(tmp_path) == before


async def test_explicit_user_commit_and_tag_are_retained(repository, tmp_path):
    remote, url, document, calls = repository
    manager = bundles.BundleManager(tmp_path / 'app')
    commit = git(remote, 'rev-parse', 'HEAD')
    git(remote, 'tag', '-a', 'v1.0', '-m', 'reviewed tag')
    for ref in (commit, 'v1.0', 'refs/tags/v1.0'):
        candidate = (await manager.discover(url + '@' + ref))['candidates'][0]
        assert candidate['ref'] == ref and '@' + ref + '#' in candidate['uri']
        await manager.perform('bundles.add', {**candidate, 'role': 'behavior'})
