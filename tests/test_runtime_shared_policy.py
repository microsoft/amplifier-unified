"""Exact shared source copies must not turn branch followers into standing pins."""
import json
from pathlib import Path
import subprocess

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web.runtime_qualification import installed_graph


@pytest.fixture
def shared_build(tmp_path, monkeypatch):
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    home = tmp_path / 'app'
    generation = 'a' * 32
    receipt = environments.receipt_directory(home, generation)
    receipt.mkdir(parents=True)
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    project = environments.prepare_project(home, generation)
    build = home / 'source-store/builds' / ('b' * 64)
    build.mkdir(parents=True)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=build, text=True, stderr=subprocess.DEVNULL).strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (build / 'module.py').write_text('version = 1\n')
    git('add', 'module.py')
    git('commit', '-m', 'fixture')
    revision = git('rev-parse', 'HEAD')
    url = 'https://example.invalid/fixture'
    (build / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': url, 'ref': revision, 'commit': revision, 'buildInput': True}))
    dist = project / '.venv/lib/python3.13/site-packages/amplifier_fixture-1.dist-info'
    dist.mkdir(parents=True)
    (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: amplifier-fixture\nVersion: 1\n')
    (dist / 'direct_url.json').write_text(json.dumps({'url': build.as_uri(), 'dir_info': {}}))
    bindings = receipt / 'foundation/cache/.source-bindings'
    bindings.mkdir(parents=True)
    binding = bindings / 'source.json'
    binding.write_text(json.dumps({'git_url': url, 'ref': 'main', 'commit': revision}))
    return home, generation, receipt, project, build, binding, revision


def dependency(home):
    return next(row for row in environments.inventory(home) if row.get('package') == 'amplifier-fixture')


def test_owned_shared_build_follows_exact_generation_binding_without_rewriting_receipts(shared_build):
    home, generation, receipt, project, build, binding, revision = shared_build
    graph = installed_graph(project)
    before = binding.read_bytes()
    assert graph[0]['cacheSource']['ref'] == revision
    row = dependency(home)
    assert row['ref'] == 'main' and row['eligible'] and row['current'] == revision
    policies = environments.shared_build_policies(home, generation, graph)
    assert policies['amplifier-fixture']['revision'] == revision
    (receipt / 'runtime-sources.json').write_text(json.dumps(policies))
    assert dependency(home)['ref'] == 'main'
    assert installed_graph(project) == graph and binding.read_bytes() == before


@pytest.mark.parametrize('case', ['pin', 'ambiguous', 'different-revision', 'dirty', 'local-checkout', 'other-generation'])
def test_uncertain_or_explicit_sources_are_not_unpinned(shared_build, case):
    home, generation, receipt, project, build, binding, revision = shared_build
    data = json.loads(binding.read_text())
    if case == 'pin':
        data['ref'] = revision
    elif case == 'ambiguous':
        binding.with_name('second.json').write_text(json.dumps({**data, 'ref': 'stable'}))
    elif case == 'different-revision':
        data['commit'] = 'c' * 40
    elif case == 'dirty':
        (build / 'module.py').write_text('version = 2\n')
    elif case == 'local-checkout':
        local = home.parent / 'local'
        build.rename(local)
        dist = next(project.glob('.venv/lib/python*/site-packages/*.dist-info'))
        (dist / 'direct_url.json').write_text(json.dumps({'url': local.as_uri(), 'dir_info': {}}))
    elif case == 'other-generation':
        other = environments.receipt_directory(home, 'd' * 32) / 'foundation/cache/.source-bindings'
        other.mkdir(parents=True)
        binding.rename(other / binding.name)
        assert not dependency(home)['eligible']
        return
    binding.write_text(json.dumps(data))
    assert not dependency(home)['eligible']


def test_recorded_explicit_pin_wins_over_shared_binding(shared_build):
    home, generation, receipt, project, build, binding, revision = shared_build
    policy = environments.shared_build_policies(home, generation, installed_graph(project))
    policy['amplifier-fixture']['ref'] = revision
    (receipt / 'runtime-sources.json').write_text(json.dumps(policy))
    assert not dependency(home)['eligible']
