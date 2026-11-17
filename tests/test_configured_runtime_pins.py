"""Explicit module pins replace generated retention, never authored restrictions."""
import hashlib
import json
import os
import shutil
import sys
import tomllib
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment as runtime, runtime_policy_reconciliation as policy
from amplifier_web import runtime_qualification, updates
from amplifier_web.host.config import HostConfig
from amplifier_web.runtime_source_alignment import verify_checked_selection
from test_runtime_source_policy_resolver import BACKEND, git

NAME = 'fixture-component'
URL = 'https://example.invalid/configured-component'


async def value(item):
    return item


@pytest.fixture
async def configured(tmp_path, monkeypatch):
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('This resolver regression requires uv')
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    git(repo, 'remote', 'add', 'origin', URL)
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture-component"\nversion="1.0"\n'
        '[project.entry-points."amplifier.modules"]\nfixture="fixture:mount"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (repo / 'backend.py').write_text(BACKEND)
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'old')
    old = git(repo, 'rev-parse', 'HEAD')
    (repo / 'revision.txt').write_text('new')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'new')
    new = git(repo, 'rev-parse', 'HEAD')
    git_config = tmp_path / 'gitconfig'
    git_config.write_text('[url "' + repo.as_uri() + '"]\n\tinsteadOf = ' + URL + '\n')
    for key, val in {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                     'UV_NO_INDEX': '1', 'UV_PYTHON_DOWNLOADS': 'never',
                     'UV_CACHE_DIR': str(tmp_path / 'uv-cache'), 'PYTHONDONTWRITEBYTECODE': '1'}.items():
        monkeypatch.setenv(key, val)
    for key in ('UV_OFFLINE', 'UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT',
                'UV_BUILD_CONSTRAINT', 'AMPLIFIER_SOURCE_STORE'):
        monkeypatch.delenv(key, raising=False)
    original_process = updates.process
    async def process(*args, **kwargs):
        if args[0] == uv:
            kwargs['env'] = {**os.environ, **kwargs.get('env', {}), 'GIT_CONFIG_GLOBAL': str(git_config)}
        return await original_process(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    home = tmp_path / 'app'
    stage = runtime.receipt_directory(home, 'c' * 32)
    root = stage / 'foundation/cache/fixture'
    shutil.copytree(repo, root)
    (root / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'git_url': URL, 'ref': new, 'commit': new}))
    settings = {'sources': {'modules': {'fixture': 'git+' + URL + '@' + new}}}
    config = HostConfig(stage, stage, settings, stage / 'foundation')
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: config)
    baseline = b'[project]\nname="worker"\nversion="1"\n'
    rows = [{'kind': 'runtime dependency', 'package': NAME, 'url': URL, 'ref': old,
             'current': old, 'subdirectory': '', 'policyOrigin': {'kind': 'owned-shared-binding'}}]
    monkeypatch.setattr(runtime, 'update_inventory', lambda _: value(rows))
    project = runtime.prepare_project(home, 'c' * 32, content=runtime.augmented_manifest(baseline, rows))
    await updates.process(uv, 'sync', '--project', str(project), '--python', sys.executable, timeout=90)
    checked = {NAME: {'url': URL, 'ref': old, 'revision': old, 'subdirectory': '',
                     'policyOrigin': {'kind': 'owned-shared-binding'}}}
    (stage / 'runtime-sources.json').write_text(json.dumps(checked))
    old_receipt = runtime.receipt_directory(home, 'a' * 32)
    old_receipt.mkdir()
    (old_receipt / 'runtime-sources.json').write_text(json.dumps(checked))
    (home / 'updates/active.json').write_text(json.dumps({'current': 'a' * 32}))
    collector = policy.Collector()
    collector.observe(settings['sources']['modules']['fixture'], config)
    batch = SimpleNamespace(names={NAME: root})
    return SimpleNamespace(home=home, stage=stage, project=project, root=root, old=old, new=new,
        config=config, collector=collector, batch=batch, checked=checked,
        old_bytes=(old_receipt / 'runtime-sources.json').read_bytes(), old_receipt=old_receipt,
        manager=SimpleNamespace(home=home))


async def test_explicit_configured_pin_updates_candidate_and_preserves_old_generation(configured):
    s = configured
    async def collect():
        return await s.collector.report(s.batch, s.config)
    first = await collect()
    with pytest.raises(runtime.ProtectedRuntimeSource):
        verify_checked_selection(s.checked, first)
    await policy.reconcile(s.manager, s.stage, s.project, collect)
    checked = json.loads((s.stage / 'runtime-sources.json').read_text())
    verify_checked_selection(checked, await collect(), exact=True)
    assert checked[NAME]['ref'] == checked[NAME]['revision'] == s.new
    installed = next(row for row in runtime_qualification.installed_graph(s.project) if row['name'] == NAME)
    assert installed['directUrl']['vcs_info']['commit_id'] == s.new
    generated = tomllib.loads((s.project / 'pyproject.toml').read_text())
    override = generated['tool']['amplifier-unified']['generated-source-overrides'][NAME]
    assert override.endswith('@' + s.new)
    assert override in generated['tool']['uv']['override-dependencies']
    assert (s.old_receipt / 'runtime-sources.json').read_bytes() == s.old_bytes
    assert json.loads((s.home / 'updates/active.json').read_text()) == {'current': 'a' * 32}
    assert checked[NAME]['policyOrigin']['manifestSha256'] == hashlib.sha256(
        (s.root / 'pyproject.toml').read_bytes()).hexdigest()


@pytest.mark.parametrize('case', ['unused-module', 'unchanged-pin', 'external-resolver', 'local-source'])
async def test_unproven_or_unchanged_configuration_cannot_replace_retained_pin(configured, monkeypatch, case):
    s = configured
    if case == 'unused-module':
        s.config.settings['sources']['modules'] = {'other-module': 'git+' + URL + '@' + s.new}
    elif case == 'unchanged-pin':
        s.config.settings['sources']['modules']['fixture'] = 'git+' + URL + '@' + s.old
    elif case == 'external-resolver':
        monkeypatch.setenv('UV_CONSTRAINT', '/unread/external-policy.txt')
    else:
        s.collector.local_sources.add(s.root)
    before = (s.project / 'pyproject.toml').read_bytes()
    await policy.reconcile(s.manager, s.stage, s.project, lambda: s.collector.report(s.batch, s.config))
    assert json.loads((s.stage / 'runtime-sources.json').read_text()) == s.checked
    assert (s.project / 'pyproject.toml').read_bytes() == before
    assert not (s.stage / 'configured-runtime-pins.json').exists()


async def test_authored_resolver_override_is_not_replaced(configured):
    s = configured
    path = s.project / 'pyproject.toml'
    data = tomllib.loads(path.read_text())
    del data['tool']['amplifier-unified']['generated-source-overrides']
    path.write_text('\n'.join(json.dumps(k) + '=' + runtime.toml_value(v) for k, v in data.items()))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='authored or changed resolver override'):
        await policy.reconcile(s.manager, s.stage, s.project, lambda: s.collector.report(s.batch, s.config))
    assert path.read_bytes() == before
    assert json.loads((s.stage / 'runtime-sources.json').read_text()) == s.checked


@pytest.mark.parametrize('drift', ['configuration', 'generation', 'checked-policy'])
async def test_changes_during_collection_refuse_before_candidate_install(configured, drift):
    s = configured
    before = (s.project / 'pyproject.toml').read_bytes()
    async def collect():
        report = await s.collector.report(s.batch, s.config)
        if drift == 'configuration':
            s.config.settings['sources']['modules']['fixture'] = 'git+' + URL + '@' + s.old
        elif drift == 'generation':
            (s.home / 'updates/active.json').write_text(json.dumps({'current': 'd' * 32}))
        else:
            (s.stage / 'runtime-sources.json').write_text('{}')
        return report
    with pytest.raises(ValueError, match='changed during preparation'):
        await policy.reconcile(s.manager, s.stage, s.project, collect)
    assert (s.project / 'pyproject.toml').read_bytes() == before
    assert not (s.stage / 'configured-runtime-pins.json').exists()


@pytest.mark.parametrize('drift', ['source', 'checked-policy'])
async def test_changed_selection_after_install_refuses_final_receipt(configured, drift):
    s = configured
    calls = 0
    async def collect():
        nonlocal calls
        calls += 1
        if calls == 2 and drift == 'source':
            path = s.root / 'pyproject.toml'
            path.write_text(path.read_text() + '\n# changed after candidate install\n')
        elif calls == 2:
            (s.stage / 'runtime-sources.json').write_text('{}')
        return await s.collector.report(s.batch, s.config)
    with pytest.raises(ValueError, match='source.*changed'):
        await policy.reconcile(s.manager, s.stage, s.project, collect)
    assert not (s.stage / 'configured-runtime-pins.json').exists()
    assert (s.old_receipt / 'runtime-sources.json').read_bytes() == s.old_bytes
    assert json.loads((s.home / 'updates/active.json').read_text()) == {'current': 'a' * 32}


async def test_recorded_generation_cannot_be_rewritten(configured):
    s = configured
    (s.stage / 'runtime.lock').write_text('qualified')
    before = (s.project / 'pyproject.toml').read_bytes()
    with pytest.raises(ValueError, match='recorded generation'):
        await policy.reconcile(s.manager, s.stage, s.project, lambda: s.collector.report(s.batch, s.config))
    assert (s.project / 'pyproject.toml').read_bytes() == before
    assert json.loads((s.stage / 'runtime-sources.json').read_text()) == s.checked
