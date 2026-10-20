"""Legacy migration uses selected declarations and a new candidate only."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_policy_reconciliation as migration
from amplifier_web import runtime_environment as environments, updates
from amplifier_web.host.config import HostConfig
from test_runtime_checked_cache import Diagnostics

URL = 'https://example.invalid/component'
OLD = 'a' * 40
NEW = 'b' * 40
NAME = 'fixture-component'


def row(**changes):
    return {'kind': 'runtime dependency', 'package': NAME, 'url': URL, 'ref': OLD,
            'current': OLD, 'subdirectory': '', 'policyOrigin': {'kind': 'unrecorded'}, **changes}


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def selected(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    stage = home / 'updates/releases' / ('c' * 32)
    root = stage / 'foundation/cache/fixture'
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'remote', 'add', 'origin', URL)
    (root / 'pyproject.toml').write_text('[project]\nname="' + NAME + '"\nversion="1"\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'old')
    old = git(root, 'rev-parse', 'HEAD')
    (root / 'version.txt').write_text('new')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'new')
    new = git(root, 'rev-parse', 'HEAD')
    repo = tmp_path / 'upstream'
    shutil.copytree(root, repo)
    git(root, 'checkout', '--detach', old)
    marker = root / '.amplifier_cache_meta.json'
    marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': old}))
    config = HostConfig(stage, stage, {}, stage / 'foundation')
    batch = SimpleNamespace(names={NAME: root})
    collector = migration.Collector()
    collector.observe('git+' + URL + '@main', config)
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    async def publish(**kw): pass
    manager.publish = publish
    manager.stage_cache_sources = lambda s, rows: updates.UpdateManager.stage_cache_sources(manager, s, rows)
    original = updates.process
    commands = []
    async def process(*args, **kwargs):
        commands.append(args)
        if 'fetch' in args or 'ls-remote' in args:
            args = tuple(str(repo) if a == URL else a for a in args)
        return await original(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    return SimpleNamespace(home=home, stage=stage, root=root, repo=repo, old=old, new=new,
        marker=marker, config=config, batch=batch, collector=collector, manager=manager, commands=commands)


@pytest.mark.parametrize('change', [{'override': True}, {'ref': 'main'}, {'ref': 'v1.2'},
    {'current': NEW}, {'policyOrigin': {'kind': 'prepared-manifest'}}, {'url': ''}])
def test_only_unrecorded_exact_commit_policies_are_candidates(change):
    assert migration.legacy_candidates([row(**change)]) == {}


async def test_actual_selected_batch_binds_package_and_branch(selected):
    s = selected
    report = await s.collector.report(s.batch, s.config)
    package = report['packages'][NAME]
    assert package['status'] == 'selected'
    assert package['revision'] == s.old
    assert package['ref'] == 'main'
    assert package['subdirectory'] == ''


@pytest.mark.parametrize('case', ['pin', 'conflict', 'unused', 'local', 'different-repository',
                                  'different-subdirectory', 'separate-config'])
async def test_explicit_or_unrelated_choices_are_not_migrated(selected, case):
    s = selected
    s.collector.references.clear()
    if case == 'pin':
        s.collector.observe('git+' + URL + '@' + s.old, s.config)
    elif case == 'conflict':
        for ref in ('main', 'stable'):
            s.collector.observe('git+' + URL + '@' + ref, s.config)
    elif case != 'unused':
        s.collector.observe('git+' + URL + '@main', s.config)
    if case == 'local':
        s.collector.observe(str(s.root), s.config, explicit_local=True)
    if case == 'separate-config':
        (s.root / 'uv.toml').write_text('')
    report = await s.collector.report(s.batch, s.config)
    old = row(ref=s.old, current=s.old)
    if case == 'different-repository': old['url'] += '-fork'
    if case == 'different-subdirectory': old['subdirectory'] = 'module'
    assert not migration.selected_policies(migration.legacy_candidates([old]), report)[0]


@pytest.mark.parametrize('case', ['dirty', 'wrong-head', 'redirected-git', 'linked-source', 'different-name'])
async def test_untrusted_cache_identity_cannot_establish_policy(selected, case, tmp_path):
    s = selected
    if case == 'dirty': (s.root / 'pyproject.toml').write_text('changed')
    elif case == 'wrong-head':
        s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': s.new}))
    elif case == 'redirected-git': git(s.root, 'config', 'core.worktree', str(s.repo))
    elif case == 'linked-source':
        external = tmp_path / 'external'
        s.root.rename(external); s.root.symlink_to(external, target_is_directory=True)
    else:
        s.batch.names = {'another-name': s.root}
    with pytest.raises(ValueError):
        await s.collector.report(s.batch, s.config)


async def test_stale_selected_root_advances_before_install_and_old_generation_is_untouched(selected, monkeypatch):
    s = selected
    oldrow = row(ref=s.old, current=s.old)
    request = {'version': 1, 'configuration': migration.configuration(s.config),
               'candidates': migration.legacy_candidates([oldrow])}
    monkeypatch.setattr(environments, 'update_inventory', lambda home: _value([oldrow]))
    monkeypatch.setattr(migration, 'pending', lambda *args: request)
    (s.stage / 'runtime-sources.json').write_text(json.dumps({NAME: {
        'url': URL, 'ref': s.old, 'revision': s.old, 'subdirectory': ''}}))
    old_receipt = s.home / 'updates/baseline-runtime'
    old_receipt.mkdir()
    (old_receipt / 'runtime-sources.json').write_text('old receipt stays unchanged')
    project = s.home / 'runtime/preparation'
    project.mkdir(parents=True)
    reports = []
    async def collect():
        result = await s.collector.report(s.batch, s.config)
        reports.append(result)
        return result
    await migration.reconcile(s.manager, s.stage, project, collect)
    saved = json.loads((s.stage / 'runtime-sources.json').read_text())[NAME]
    assert saved['revision'] == s.new and saved['ref'] == 'main'
    assert saved['policyOrigin']['kind'] == 'selected-declaration'
    assert [r['packages'][NAME]['revision'] for r in reports] == [s.old, s.new]
    assert git(s.root, 'rev-parse', 'HEAD') == s.new
    assert (old_receipt / 'runtime-sources.json').read_text() == 'old receipt stays unchanged'
    receipt = json.loads((s.stage / migration.RECEIPT).read_text())
    assert receipt['decisions'] == {NAME: 'migrated'} and not receipt['remaining']


async def _value(value): return value


def test_acknowledged_unselected_pin_does_not_prompt_again_for_same_configuration(selected, monkeypatch):
    s = selected
    oldrow = row()
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: s.config)
    receipt = s.home / 'updates/baseline-runtime'
    receipt.mkdir()
    assert migration.pending(s.home, None, [oldrow])
    receipt.joinpath(migration.RECEIPT).write_text(json.dumps({
        'version': 1, 'configuration': migration.configuration(s.config),
        'remaining': migration.legacy_candidates([oldrow])}))
    assert migration.pending(s.home, None, [oldrow]) is None
    s.config.settings['sources'] = {'modules': {'some-module': 'git+' + URL + '@main'}}
    assert migration.pending(s.home, None, [oldrow])


async def test_recorded_generation_is_never_reconciled(selected):
    selected.stage.joinpath('runtime.lock').write_text('frozen')
    with pytest.raises(ValueError, match='recorded'):
        await migration.reconcile(selected.manager, selected.stage, selected.home / 'runtime/a', lambda: None)


async def test_inactive_catalog_entries_are_never_observed(selected):
    s = selected
    s.config.settings['bundle'] = {'added': {'unused': 'git+' + URL + '@' + s.old}}
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['ref'] == 'main'


async def test_host_observes_effective_module_override(selected):
    from amplifier_web.host.session import module_source
    s = selected
    s.config._source_policy_observer = migration.Collector()
    s.config.settings['sources'] = {'modules': {'fixture': 'git+' + URL + '@' + s.old}}
    components = SimpleNamespace(source=lambda m, source, **kw: source, owns=lambda m, source: False)
    assert module_source(s.config, False, 'fixture', 'git+' + URL + '@main', components).endswith(s.old)
    assert s.config._source_policy_observer.references == {(URL, s.old)}

@pytest.mark.parametrize('case', ['tag-only', 'branch-and-tag', 'branch-only', 'missing'])
async def test_non_numeric_tags_and_same_name_ambiguity_never_become_branches(case):
    async def process(*args, **kwargs):
        lines = []
        if case in {'branch-only', 'branch-and-tag'}: lines.append(NEW + '\trefs/heads/stable')
        if case in {'tag-only', 'branch-and-tag'}: lines.append(OLD + '\trefs/tags/stable')
        return '\n'.join(lines)
    assert await migration.moving_revision(URL, 'stable', process) == (NEW if case == 'branch-only' else None)


@pytest.mark.parametrize('change', ['content', 'empty-commit'])
async def test_reconciliation_accepts_exact_new_branch_commit_even_without_content_change(selected, monkeypatch, change):
    s = selected
    if change == 'empty-commit':
        git(s.repo, 'checkout', '--detach', s.old)
        git(s.repo, 'checkout', '-B', 'main')
        git(s.repo, 'commit', '--allow-empty', '-m', 'new identity same contents')
        s.new = git(s.repo, 'rev-parse', 'HEAD')
    oldrow = row(ref=s.old, current=s.old)
    request = {'version': 1, 'configuration': migration.configuration(s.config),
               'candidates': migration.legacy_candidates([oldrow])}
    monkeypatch.setattr(environments, 'update_inventory', lambda home: _value([oldrow]))
    monkeypatch.setattr(migration, 'pending', lambda *args: request)
    (s.stage / 'runtime-sources.json').write_text(json.dumps({NAME: {
        'url': URL, 'ref': s.old, 'revision': s.old, 'subdirectory': ''}}))
    project = s.home / 'runtime/preparation'
    project.mkdir(parents=True)
    await migration.reconcile(s.manager, s.stage, project, lambda: s.collector.report(s.batch, s.config))
    assert json.loads((s.stage / 'runtime-sources.json').read_text())[NAME]['revision'] == s.new


async def test_changed_declaration_after_refresh_cannot_pass_into_install(selected, monkeypatch):
    s = selected
    oldrow = row(ref=s.old, current=s.old)
    request = {'version': 1, 'configuration': migration.configuration(s.config),
               'candidates': migration.legacy_candidates([oldrow])}
    monkeypatch.setattr(environments, 'update_inventory', lambda home: _value([oldrow]))
    monkeypatch.setattr(migration, 'pending', lambda *args: request)
    (s.stage / 'runtime-sources.json').write_text(json.dumps({NAME: {
        'url': URL, 'ref': s.old, 'revision': s.old, 'subdirectory': ''}}))
    project = s.home / 'runtime/preparation'
    project.mkdir(parents=True)
    count = 0
    async def collect():
        nonlocal count
        count += 1
        if count == 2: s.collector.references = {(URL, s.new)}
        return await s.collector.report(s.batch, s.config)
    with pytest.raises(ValueError, match='changed after'):
        await migration.reconcile(s.manager, s.stage, project, collect)
    assert not (s.stage / migration.RECEIPT).exists()


def test_optional_pin_is_protective_even_if_not_selected_to_authorize_recovery():
    raw = ('[project]\nname="another"\nversion="1"\n'
           '[project.optional-dependencies]\nunused=["' + NAME + ' @ git+' + URL + '@' + OLD + '"]\n').encode()
    assert migration.authored_constraints(raw)[NAME]['ref'] == OLD
