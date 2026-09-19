"""Real Git checkouts: generated cache differences are not source edits."""
import importlib._bootstrap_external
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from amplifier_web.host.config import _import_global
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager, active_release, cache_changes, foundation_home


def git(path, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=path, stderr=subprocess.DEVNULL).decode().strip()


def pyc(source):
    return importlib._bootstrap_external._code_to_timestamp_pyc(compile(source, 'module.py', 'exec'), 0, len(source))


@pytest.fixture
def repository(tmp_path):
    root = tmp_path/'repository'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'cache-test@example.invalid')
    git(root, 'config', 'user.name', 'Cache fixture')
    (root/'bundle.py').write_text('value = 1\n')
    (root/'CLAUDE.md').write_text('Original instructions\n')
    (root/'AGENTS.md').symlink_to('CLAUDE.md')
    (root/'__pycache__').mkdir()
    (root/'__pycache__/module.cpython-313.pyc').write_bytes(pyc('value = 1'))
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Original')
    return root


def cache_artifacts(root):
    (root/'__pycache__/module.cpython-313.pyc').write_bytes(pyc('value = 2'))
    (root/'AGENTS.md').unlink()
    (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())


class Runtime:
    async def close(self):pass


@pytest.fixture
async def service(tmp_path, monkeypatch, repository):
    from amplifier_web import updates
    original = updates.process
    async def process(*args, **kwargs):
        if 'fetch' in args:
            args = tuple(str(repository) if arg == 'https://example.invalid/repo' else arg for arg in args)
        return await original(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path/'no-import'))
    app = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    app.state['settings']['bundle'] = 'git+https://example.invalid/repo@main'
    app.update_manager = UpdateManager(app)
    yield app
    await app.close()


async def test_only_verified_existing_cache_artifacts_are_eligible(repository, service):
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo', 'ref':'main'}))
    cache_artifacts(cache)
    before = (cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes()
    protected, artifacts = await cache_changes(cache)
    assert protected == []
    assert set(artifacts) == {'AGENTS.md', '__pycache__/module.cpython-313.pyc'}
    rows = await service.update_manager.inventory_sources()
    assert rows[0]['eligible'] and rows[0]['status'] == 'not_checked'
    assert not (cache/'AGENTS.md').is_symlink()
    assert before == ((cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes())
    (cache/'bundle.py').write_text('real source change\n')
    row = (await service.update_manager.inventory_sources())[0]
    assert not row['eligible'] and row['status'] == 'local_changes'


@pytest.mark.parametrize('change', ['source', 'staged-bytecode', 'deleted-bytecode', 'invalid-bytecode', 'bytecode-mode', 'modified-link', 'link-mode', 'deleted-link', 'changed-link', 'changed-target', 'staged-target', 'escaping-link', 'absolute-link', 'target-chain'])
async def test_real_edits_and_unproven_links_stay_protected(repository, tmp_path, change):
    root = repository
    cache_artifacts(root)
    if change == 'source':(root/'bundle.py').write_text('user = "source edit"\n')
    elif change == 'staged-bytecode':git(root, 'add', '__pycache__/module.cpython-313.pyc')
    elif change == 'deleted-bytecode':(root/'__pycache__/module.cpython-313.pyc').unlink()
    elif change == 'invalid-bytecode':(root/'__pycache__/module.cpython-313.pyc').write_bytes(b'User-authored data, not Python bytecode')
    elif change == 'bytecode-mode':(root/'__pycache__/module.cpython-313.pyc').chmod(0o755)
    elif change == 'modified-link':(root/'AGENTS.md').write_text('User-authored instructions\n')
    elif change == 'link-mode':(root/'AGENTS.md').chmod(0o755)
    elif change == 'deleted-link':(root/'AGENTS.md').unlink()
    elif change == 'changed-link':
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').symlink_to('bundle.py')
    elif change in {'changed-target', 'staged-target'}:
        (root/'CLAUDE.md').write_text('New user-authored instructions\n')
        (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())
        if change == 'staged-target':git(root, 'add', 'CLAUDE.md')
    elif change in {'escaping-link', 'absolute-link'}:
        (tmp_path/'outside.md').write_text('Outside the repository\n')
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').symlink_to('../outside.md' if change == 'escaping-link' else tmp_path/'outside.md')
        git(root, 'add', 'AGENTS.md');git(root, 'commit', '-m', 'Tracked external link')
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').write_bytes((tmp_path/'outside.md').read_bytes())
    elif change == 'target-chain':
        (root/'CLAUDE.md').unlink();(root/'CLAUDE.md').symlink_to('bundle.py')
        git(root, 'add', 'CLAUDE.md');git(root, 'commit', '-m', 'Tracked target chain')
        (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())
    protected, _ = await cache_changes(root)
    assert protected, change


async def test_nul_paths_and_renames_cannot_be_misclassified(repository):
    name = ' instructions\twith\nwhitespace.md '
    (repository/name).symlink_to('CLAUDE.md')
    git(repository, 'add', '.');git(repository, 'commit', '-m', 'Unusual filename')
    (repository/name).unlink();(repository/name).write_bytes((repository/'CLAUDE.md').read_bytes())
    assert await cache_changes(repository) == ([], [name])
    git(repository, 'mv', 'bundle.py', ' renamed.py ')
    protected, artifacts = await cache_changes(repository)
    assert protected and artifacts == [name]


async def test_staging_normalizes_only_verified_artifacts_and_preserves_live_cache(repository, service):
    old = git(repository, 'rev-parse', 'HEAD')
    (repository/'bundle.py').write_text('value = 3\n')
    (repository/'CLAUDE.md').write_text('New upstream instructions\n')
    git(repository, 'commit', '-am', 'Updated')
    new = git(repository, 'rev-parse', 'HEAD')
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    git(cache, 'checkout', '--detach', old)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo', 'ref':'main'}))
    cache_artifacts(cache)
    # Do not remove arbitrary untracked files, including files named *.pyc.
    (cache/'__pycache__/personal-notes.pyc').write_text('keep my notes')
    before = (cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes()
    manager = service.update_manager
    manager.inventory = [{'id':'repo', 'path':'cache/example', 'url':'https://example.invalid/repo', 'label':'Fixture', 'current':old, 'latest':new, 'ref':'main', 'eligible':True, 'status':'update'}]
    async def validate(stage, release):
        staged = stage/'foundation/cache/example'
        assert git(staged, 'rev-parse', 'HEAD') == new
        assert (staged/'AGENTS.md').is_symlink()
        assert (staged/'AGENTS.md').read_text() == 'New upstream instructions\n'
        assert (staged/'__pycache__/module.cpython-313.pyc').read_bytes() == pyc('value = 1')
        assert (staged/'__pycache__/personal-notes.pyc').read_text() == 'keep my notes'
    manager.validate = validate
    await manager.install()
    assert service.state['updates']['phase'] == 'installed'
    assert foundation_home(service.data_dir) != service.data_dir/'foundation'
    assert git(cache, 'rev-parse', 'HEAD') == old
    assert not (cache/'AGENTS.md').is_symlink()
    assert before == ((cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes())


async def test_source_edit_added_after_check_blocks_staging(repository, service):
    old = git(repository, 'rev-parse', 'HEAD')
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    cache_artifacts(cache)
    assert (await cache_changes(cache))[0] == []
    (cache/'bundle.py').write_text('a later real source edit\n')
    manager = service.update_manager
    manager.inventory = [{'id':'repo', 'path':'cache/example', 'url':'https://example.invalid/repo', 'label':'Fixture', 'current':old, 'latest':old, 'ref':'main', 'eligible':True, 'status':'update'}]
    await manager.install()
    assert service.state['updates']['phase'] == 'error'
    assert not active_release(service.data_dir)
    assert (cache/'bundle.py').read_text() == 'a later real source edit\n'
    assert not (cache/'AGENTS.md').is_symlink()


def test_import_preserves_symlinks_including_external_links(tmp_path, repository):
    legacy = tmp_path/'legacy'
    shutil.copytree(repository, legacy/'cache/repository', symlinks=True)
    outside = tmp_path/'external';outside.mkdir();(outside/'notes.txt').write_text('private')
    (legacy/'cache/repository/external').symlink_to(outside, target_is_directory=True)
    home = tmp_path/'imported'
    _import_global(home, legacy)
    imported = home/'foundation/cache/repository'
    assert (imported/'AGENTS.md').is_symlink()
    assert (imported/'AGENTS.md').readlink() == Path('CLAUDE.md')
    assert (imported/'external').is_symlink()
    assert git(imported, 'status', '--porcelain', '--untracked-files=no') == ''


def cached(service, repository, name, ref='main'):
    root = service.data_dir/'foundation/cache'/name
    shutil.copytree(repository, root, symlinks=True)
    (root/'.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': 'https://example.invalid/'+name, 'ref': ref,
    }))
    return root


@pytest.mark.parametrize('ref', ['main', 'master'])
async def test_historical_registry_entries_keep_failures_with_unknown_usage(repository, service, monkeypatch, ref):
    from amplifier_web import updates, app_updates
    from amplifier_web.attention import snapshot
    root = cached(service, repository, 'retired', ref)
    registry = root.parent.parent/'registry.json'
    registry.write_text(json.dumps({'bundles': {'retired': {
        'uri': 'git+https://example.invalid/retired@'+ref,
        'is_root': True, 'local_path': str(root),
    }}}))
    remote_calls = []
    original = updates.process
    async def process(*args, **kwargs):
        if args[1] == 'ls-remote':
            remote_calls.append(args)
            raise RuntimeError('Unavailable branch')
        return await original(*args, **kwargs)
    async def application():
        return {'id': 'application', 'label': 'Application', 'status': 'current'}
    monkeypatch.setattr(updates, 'process', process)
    monkeypatch.setattr(app_updates, 'check', application)
    await service.update_manager.check()
    row = service.update_manager.inventory[0]
    assert row['status'] == 'check_failed' and row['usage'] == 'unknown'
    assert row['eligible']
    assert len(remote_calls) == 1
    assert len(snapshot(service.state)['items']) == 1
    assert root.exists() and registry.exists()


async def test_configured_sources_include_scoped_settings_and_session_choices(repository, service, tmp_path):
    import yaml
    from amplifier_web.shared_state import workspace_snapshot_path
    names = ['repo', 'app', 'module', 'skill', 'workspace', 'session', 'disabled', 'historical']
    for name in names:
        cached(service, repository, name)
    home = service.data_dir
    (home/'foundation/registry.json').write_text(json.dumps({'bundles': {
        name: {'uri': 'git+https://example.invalid/'+name+'@main'} for name in ['app', 'historical']
    }}))
    (home/'config').mkdir(exist_ok=True)
    (home/'config/settings.yaml').write_text(yaml.safe_dump({
        'bundle': {'app': ['app', 'disabled'], 'added': {'disabled': 'git+https://example.invalid/disabled@main'}},
        'web_bundles': {'excluded': ['disabled']},
        'sources': {'modules': {'tool-example': 'git+https://example.invalid/module@main'}},
        'config': {'tools': [{'module': 'tool-skills', 'config': {'skills': ['git+https://example.invalid/skill@main']}}]},
    }))
    other = tmp_path/'other-workspace'
    other.mkdir()
    snapshot = workspace_snapshot_path(other, home)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(yaml.safe_dump({'bundle': {'active': 'git+https://example.invalid/workspace@main'}}))
    service.state['workspaces'].append({'id': 'other', 'path': str(other)})
    await service.dispatch('session.create', {'workspace': str(other), 'bundle': 'git+https://example.invalid/session@main'})
    before = {p: p.read_bytes() for p in (home/'config').rglob('*') if p.is_file()}
    rows = {r['label'].split('/')[-1]: r for r in await service.update_manager.inventory_sources()}
    for name in names[:6]:
        assert rows[name]['eligible'] and rows[name]['usage'] == 'configured', name
        assert rows[name]['usageEvidence'], name
    for name in names[6:]:
        assert rows[name]['usage'] == 'unknown' and rows[name]['eligible'], name
        assert not rows[name]['usageEvidence'], name
    assert before == {p: p.read_bytes() for p in (home/'config').rglob('*') if p.is_file()}


@pytest.mark.parametrize('failure', ['alias', 'cycle', 'yaml', 'module-shape', 'recursive-yaml'])
async def test_unresolvable_configuration_does_not_hide_cache_failures(repository, service, monkeypatch, failure):
    from amplifier_web import updates, app_updates
    root = cached(service, repository, 'repo')
    service.state['settings']['bundle'] = 'missing'
    if failure == 'cycle':
        (root.parent.parent/'registry.json').write_text(json.dumps({'bundles': {'missing': {'uri': 'missing'}}}))
    if failure == 'yaml':
        (service.data_dir/'config').mkdir(exist_ok=True)
        (service.data_dir/'config/settings.yaml').write_text('bundle: [')
    elif failure in {'module-shape', 'recursive-yaml'}:
        (service.data_dir/'config').mkdir(exist_ok=True)
        (service.data_dir/'config/settings.yaml').write_text(
            'config:\n  providers:\n    - source: git+https://example.invalid/repo@main\n'
            if failure == 'module-shape' else 'config: &cycle\n  children: *cycle\n')
    original = updates.process
    async def process(*args, **kwargs):
        if args[1] == 'ls-remote':raise RuntimeError('Unavailable branch')
        return await original(*args, **kwargs)
    async def application():
        return {'id': 'application', 'label': 'Application', 'status': 'current'}
    monkeypatch.setattr(updates, 'process', process)
    monkeypatch.setattr(app_updates, 'check', application)
    await service.update_manager.check()
    rows = service.update_manager.inventory
    assert any(r['id'] == 'source-configuration' and r['status'] == 'check_failed' for r in rows)
    row = next(r for r in rows if r.get('path'))
    assert row['eligible'] and row['status'] == 'check_failed' and row['usage'] == 'unknown'
    assert root.exists()


async def test_uncertain_transitive_module_and_skill_caches_survive_staging(repository, service):
    active = cached(service, repository, 'repo')
    module = cached(service, repository, 'transitive-module')
    skill = cached(service, repository, 'transitive-skill')
    (module/'bundle.py').write_text('local edit to a transitive module\n')
    (skill/'untracked.txt').write_text('keep this too\n')
    manager = service.update_manager
    rows = await manager.inventory_sources()
    unknown = next(r for r in rows if r.get('path') == 'cache/transitive-module')
    assert unknown['status'] == 'local_changes' and unknown['usage'] == 'unknown'
    assert not unknown['eligible']
    row = next(r for r in rows if r.get('path') == 'cache/repo')
    manager.inventory = [{**row, 'latest': row['current'], 'status': 'update'}]
    async def validate(stage, release):
        assert (stage/'foundation/cache/transitive-module/bundle.py').read_bytes() == (module/'bundle.py').read_bytes()
        assert (stage/'foundation/cache/transitive-skill/untracked.txt').read_bytes() == (skill/'untracked.txt').read_bytes()
    manager.validate = validate
    await manager.install()
    assert service.state['updates']['phase'] == 'installed'
    assert active.exists() and module.exists() and skill.exists()


async def test_unregistered_transitive_modules_and_skills_are_checked_and_updated(repository, service, monkeypatch):
    from amplifier_web import app_updates, updates
    module = cached(service, repository, 'transitive-module')
    skill = cached(service, repository, 'skills/transitive-skill')
    old = git(repository, 'rev-parse', 'HEAD')
    (repository/'bundle.py').write_text('new upstream value\n')
    git(repository, 'commit', '-am', 'Updated dependency')
    new = git(repository, 'rev-parse', 'HEAD')
    calls = []
    original = updates.process
    async def process(*args, **kwargs):
        if args[1] == 'ls-remote':
            calls.append(args[2])
            return new+'\trefs/heads/main'
        if 'fetch' in args:
            args = tuple(str(repository) if str(arg).startswith('https://example.invalid/') else arg for arg in args)
        return await original(*args, **kwargs)
    async def application():
        return {'id': 'application', 'label': 'Application', 'status': 'current'}
    monkeypatch.setattr(updates, 'process', process)
    monkeypatch.setattr(app_updates, 'check', application)
    manager = service.update_manager
    await manager.check()
    assert len(calls) == 2
    assert all(r['usage'] == 'unknown' and r['eligible'] and r['status'] == 'update' for r in manager.inventory)
    assert service.state['updates']['available'] == 2
    async def validate(stage, release):
        for name in ['transitive-module', 'skills/transitive-skill']:
            assert git(stage/'foundation/cache'/name, 'rev-parse', 'HEAD') == new
    manager.validate = validate
    await manager.install()
    assert service.state['updates']['phase'] == 'installed'
    for root in [module, skill]:
        assert git(root, 'rev-parse', 'HEAD') == old
    assert all(r['status'] == 'current' for r in service.state['updates']['items'] if r.get('usage') == 'unknown')


async def test_version_stamp_is_not_exempted_by_filename_or_generated_comment(repository, service):
    stamp = repository/'package/_version.py'
    stamp.parent.mkdir()
    stamp.write_text('# generated by a build backend\n__version__ = "1.0"\n')
    git(repository, 'add', '.');git(repository, 'commit', '-m', 'Version stamp')
    root = cached(service, repository, 'repo')
    (root/'package/_version.py').write_text('# generated by a build backend\n__version__ = "1.1"\n')
    assert await cache_changes(root) == (['package/_version.py'], [])
    row = (await service.update_manager.inventory_sources())[0]
    assert row['status'] == 'local_changes' and not row['eligible']
    assert 'Tracked source changes' in row['detail']
    assert 'build' in row['detail'].lower() and 'preserved' in row['detail']


async def test_branch_identity_and_nested_copies_are_not_guessed(repository, service):
    main = cached(service, repository, 'repo')
    old = main.parent/'old-branch'
    nested = main.parent/'skills/repo'
    shutil.copytree(main, old, symlinks=True)
    shutil.copytree(main, nested, symlinks=True)
    (old/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url': 'https://example.invalid/repo.git', 'ref': 'master'}))
    rows = {r['path']: r for r in await service.update_manager.inventory_sources() if r.get('path')}
    assert rows['cache/repo']['eligible']
    assert rows['cache/skills/repo']['eligible']
    assert rows['cache/old-branch']['usage'] == 'unknown' and rows['cache/old-branch']['eligible']


async def test_live_workspace_override_is_read_without_import_or_key_loading(repository, service, tmp_path, monkeypatch):
    from amplifier_web.host import config
    for name in ['repo', 'project']:
        cached(service, repository, name)
    override = tmp_path/'.amplifier-unified'
    override.mkdir()
    (override/'settings.local.yaml').write_text('sources:\n  modules:\n    example: git+https://example.invalid/project@main\n')
    def forbidden(*args):
        pytest.fail('Inventory must not migrate config or load credentials')
    monkeypatch.setattr(config, '_load_keys', forbidden)
    monkeypatch.setattr(config, '_import_global', forbidden)
    rows = await service.update_manager.inventory_sources()
    assert all(r['eligible'] for r in rows)
    assert not (service.data_dir/'config').exists()


async def test_configured_unavailable_branch_remains_an_actionable_failure(repository, service, monkeypatch):
    from amplifier_web import app_updates, updates
    from amplifier_web.attention import snapshot
    cached(service, repository, 'repo')
    original = updates.process
    async def process(*args, **kwargs):
        if args[1] == 'ls-remote':return ''
        return await original(*args, **kwargs)
    async def application():
        return {'id': 'application', 'label': 'Application', 'status': 'current'}
    monkeypatch.setattr(updates, 'process', process)
    monkeypatch.setattr(app_updates, 'check', application)
    await service.update_manager.check()
    assert service.update_manager.inventory[0]['status'] == 'check_failed'
    assert len(snapshot(service.state)['items']) == 1


async def test_changed_cache_identity_invalidates_previously_checked_candidate(repository, service):
    root = cached(service, repository, 'repo')
    row = (await service.update_manager.inventory_sources())[0]
    service.update_manager.inventory = [{**row, 'latest': row['current'], 'status': 'update'}]
    service.state['settings']['bundle'] = 'git+https://example.invalid/replacement@main'
    (root/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url': 'https://example.invalid/replacement', 'ref': 'main'}))
    await service.update_manager.install()
    assert service.state['updates']['phase'] == 'error'
    assert not active_release(service.data_dir)


async def test_removing_configuration_after_check_does_not_block_update(repository, service):
    root = cached(service, repository, 'repo')
    row = (await service.update_manager.inventory_sources())[0]
    service.update_manager.inventory = [{**row, 'latest': row['current'], 'status': 'update'}]
    service.state['settings']['bundle'] = 'missing'
    async def validate(stage, release):
        assert git(stage/'foundation/cache/repo', 'rev-parse', 'HEAD') == row['current']
    service.update_manager.validate = validate
    await service.update_manager.install()
    assert service.state['updates']['phase'] == 'installed'
    assert git(root, 'rev-parse', 'HEAD') == row['current']


@pytest.mark.parametrize('ref', ['v1.2.3', 'a'*40])
async def test_unknown_pins_remain_pinned_and_ineligible(repository, service, ref):
    cached(service, repository, 'pinned-dependency', ref)
    row = (await service.update_manager.inventory_sources())[0]
    assert row['usage'] == 'unknown' and row['status'] == 'pinned'
    assert not row['eligible']


def test_grouping_preserves_orthogonal_usage_evidence():
    from amplifier_web.updates import group_sources
    row = {'id': 'one', 'kind': 'bundle / module', 'label': 'example.invalid/repo',
           'ref': 'main', 'current': 'old', 'status': 'check_failed',
           'usage': 'configured', 'usageEvidence': ['Selected bundle']}
    rows = group_sources([row, {**row, 'id': 'two', 'usage': 'unknown', 'usageEvidence': []},
                          {**row, 'id': 'three', 'usageEvidence': ['Enabled app bundle']}])
    assert len(rows) == 3
    assert group_sources(rows) == rows
