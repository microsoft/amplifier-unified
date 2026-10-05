"""Outcome tests: definitions persist, exact plans provision, existing work survives."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.workspace_starters import StarterCatalog, definition
from amplifier_web import workspace_provisioning as provisioning
from amplifier_web.bundle_selection import defaults


@pytest.fixture
async def service(tmp_path):
    folder = tmp_path / 'initial'
    folder.mkdir()
    value = AppService(tmp_path / 'data', workspace=folder)
    yield value
    await value.close()


async def settle(service):
    for _ in range(300):
        row = next(w for w in service.state['workspaces'] if w['id'] == service.state['selectedWorkspaceId'])
        if row.get('setup', {}).get('status') not in {'pending', 'running'}:
            return row
        await asyncio.sleep(.01)
    raise AssertionError('Starter setup did not settle')


async def create(service, starter='development', name='My work'):
    prepared = await service.dispatch('workspace.prepare', {'name': name, 'starterId': starter})
    plan = prepared['result']
    result = await service.dispatch('workspace.create', {'planId': plan['planId'], 'fromDraft': True}, command_id='create-'+name)
    return result, await settle(service)


def test_catalog_duplicate_edit_conflict_delete_restart(tmp_path):
    catalog = StarterCatalog(tmp_path)
    assert [row['id'] for row in catalog.listing()['items']] == ['blank', 'development', 'amplifier-development']
    copy = catalog.command('workspace.starters.duplicate', {'id': 'development'}, 'duplicate')
    assert catalog.command('workspace.starters.duplicate', {'id': 'development'}, 'duplicate') == copy
    fields = {key: value for key, value in copy.items() if key not in {'id', 'revision', 'builtIn'}}
    fields.update(name='My starter', bundle='anchors')
    args = {'id': copy['id'], 'expectedRevision': 1, 'starter': fields}
    updated = catalog.command('workspace.starters.save', args, 'edit')
    assert StarterCatalog(tmp_path).snapshot(copy['id']) == updated
    assert catalog.snapshot('development')['bundle'] == ''
    with pytest.raises(ValueError, match='changed'):
        catalog.command('workspace.starters.save', args, 'stale-edit')
    with pytest.raises(ValueError, match='Built-in'):
        catalog.command('workspace.starters.save', {**args, 'id': 'development'}, 'builtin-edit')
    catalog.command('workspace.starters.remove', {'id': copy['id'], 'expectedRevision': 2}, 'remove')
    assert len(StarterCatalog(tmp_path).listing()['items']) == 3


@pytest.mark.parametrize('repo', [
    {'url': 'https://user:secret@example.com/repo'}, {'url': 'file:///tmp/repo'},
    {'url': 'https://example.com/repo', 'directory': '../outside'},
    {'url': 'https://example.com/repo', 'ref': '--upload-pack=bad'},
    {'url': 'https://example.com/repo?token=secret'},
])
def test_repository_inputs_fail_closed(repo):
    with pytest.raises(ValueError):
        definition({'name': 'Bad', 'repositories': [repo]})


async def test_blank_stays_empty_and_development_ready_without_git(service):
    _, blank = await create(service, 'blank', 'Blank work')
    assert list(Path(blank['path']).iterdir()) == []
    assert 'setupId' not in blank
    _, row = await create(service)
    folder = Path(row['path'])
    assert row['setup']['status'] == 'ready'
    assert (folder / '.amplifier/AGENTS.md').is_file()
    assert not (folder / '.git').exists()
    assert not (folder / 'SCRATCH.md').exists()
    assert service.state['sessions'] == []
    assert row['resourceTracking'] is True
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_web.host.mentions import include_instruction_files
    bundle = include_instruction_files(Bundle(name='fixture'))
    prepared = PreparedBundle(bundle.to_mount_plan(), BundleModuleResolver({}), bundle)
    session = SimpleNamespace(coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock())))
    render = prepared.create_system_prompt_factory(session, session_cwd=folder)
    assert 'This workspace is a lasting home' in await render()


async def test_prepared_snapshot_survives_starter_edit_and_delete(service):
    saved = await service.dispatch('workspace.starters.save', {'starter': {'name': 'Custom', 'instructions': 'Original', 'bundle': 'anchors'}})
    starter = saved['result']
    plan = (await service.dispatch('workspace.prepare', {'name': 'Snapshot', 'starterId': starter['id']}))['result']
    await service.dispatch('workspace.starters.remove', {'id': starter['id'], 'expectedRevision': starter['revision']})
    await service.dispatch('workspace.create', {'planId': plan['planId']}, command_id='snapshot-create')
    row = await settle(service)
    assert (Path(row['path']) / '.amplifier/AGENTS.md').read_text() == 'Original\n'
    assert defaults(service.data_dir, row['path'])['effective'] == 'anchors'
    await service.dispatch('session.create', {})
    assert service._session()['bundle'] == 'anchors'


async def test_attach_and_collision_never_scaffold(service, tmp_path):
    foreign = tmp_path / 'borrowed'
    foreign.mkdir()
    (foreign / 'keep.txt').write_text('mine')
    await service.dispatch('workspace.add', {'path': str(foreign)})
    assert sorted(p.name for p in foreign.iterdir()) == ['keep.txt']
    plan = (await service.dispatch('workspace.prepare', {'name': 'borrowed', 'root': str(tmp_path), 'starterId': 'development'}))['result']
    assert plan['disposition'] == 'open'
    with pytest.raises(AppError, match='already exists'):
        await service.dispatch('workspace.create', {'planId': plan['planId']}, command_id='collision')
    assert sorted(p.name for p in foreign.iterdir()) == ['keep.txt']


async def test_creation_retry_does_not_rewrite_instructions_or_clone(service):
    result, row = await create(service)
    folder = Path(row['path'])
    instructions = folder / '.amplifier/AGENTS.md'
    instructions.write_text('User edit')
    plan_id = result['result']['setupId']
    again = await service.dispatch('workspace.create', {'planId': plan_id, 'fromDraft': True}, command_id='create-My work')
    assert again['duplicate']
    await service.dispatch('workspace.create', {'planId': plan_id}, command_id='new-transport')
    assert instructions.read_text() == 'User edit'
    assert len([w for w in service.state['workspaces'] if w['path'] == str(folder)]) == 1


async def test_background_setup_does_not_hold_app_lock_or_change_newer_draft(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = provisioning.run
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(provisioning, 'run', delayed)
    plan = (await service.dispatch('workspace.prepare', {'name': 'Delayed', 'starterId': 'development'}))['result']
    await service.dispatch('workspace.create', {'planId': plan['planId'], 'fromDraft': True}, command_id='delay-create')
    await asyncio.to_thread(entered.wait, 2)
    await asyncio.wait_for(service.dispatch('view.update', {'patch': {'draft': 'newer edit'}}), 1)
    with pytest.raises(AppError, match='still preparing'):
        await service.dispatch('session.create', {})
    with pytest.raises(AppError, match='still preparing'):
        await service.dispatch('session.create', {'workspace': plan['path'] + '/.'})
    release.set()
    await settle(service)
    assert service.state['view']['draft'] == 'newer edit'


async def test_existing_instruction_and_symlink_preserved(service, monkeypatch, tmp_path):
    original = provisioning.run
    def race(home, identity, progress=None):
        row = provisioning.inspect(home, identity)
        folder = Path(row['path']) / '.amplifier'
        folder.mkdir()
        (folder / 'AGENTS.md').write_text('User directions')
        return original(home, identity, progress)
    monkeypatch.setattr(provisioning, 'run', race)
    _, row = await create(service)
    assert (Path(row['path']) / '.amplifier/AGENTS.md').read_text() == 'User directions'
    assert row['setup']['files'][0]['status'] == 'preserved'


async def test_resource_records_persist_and_observation_is_not_teardown(service):
    _, row = await create(service)
    args = {'workspaceId': row['id'], 'kind': 'container', 'resourceId': 'example', 'owner': 'other-system'}
    resource = (await service.dispatch('workspace.resources.add', args, command_id='resource-add'))['result']
    await service.dispatch('workspace.resources.update', {'workspaceId': row['id'], 'id': resource['id'], 'expectedRevision': 1, 'status': 'observed_absent', 'evidence': 'Owner confirmed missing'})
    await service.dispatch('workspace.remove', {'id': row['id']})
    assert Path(row['path']).is_dir()
    from amplifier_web.workspace_resources import command
    inventory = command(service.data_dir, row['id'], 'workspace.resources.list', {})
    assert inventory['resources'][0]['status'] == 'observed_absent'
    assert inventory['resources'][0]['owner'] == 'other-system'


def local_git(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-b', 'trunk', str(path)], check=True, capture_output=True)
    (path / 'source.txt').write_text('version one')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Initial'], check=True, capture_output=True)
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD']).decode().strip()


async def test_real_git_import_default_branch_partial_retry_keeps_success(service, tmp_path, monkeypatch):
    source = tmp_path / 'origin'
    expected_sha = local_git(source)
    source2 = tmp_path / 'later'
    # Rewrite fixture URLs through Git's configuration, not production validation.
    config = tmp_path / 'gitconfig'
    config.write_text(f'[url "{tmp_path}/"]\n insteadOf = https://fixture.invalid/\n[protocol "file"]\n allow = always\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    original_git = provisioning._git
    def fixture_git(fd, *args):
        # Production forbids file transport; this test uses real local Git only.
        if args[0] == 'clone':
            args = tuple(str(tmp_path / arg.removeprefix('https://fixture.invalid/')) if arg.startswith('https://fixture.invalid/') else arg for arg in args)
            cwd = f'/proc/self/fd/{fd}'
            return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd, pass_fds=(fd,), stderr=subprocess.DEVNULL).decode().strip()
        return original_git(fd, *args)
    monkeypatch.setattr(provisioning, '_git', fixture_git)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Two repos', 'repositories': [
        {'url': 'https://fixture.invalid/origin', 'directory': 'one'}, {'url': 'https://fixture.invalid/later', 'directory': 'two'}]}}))['result']
    # The failed process raises a domain failure like production; no mocked success.
    real = provisioning._git
    def checked(fd, *args):
        try: return real(fd, *args)
        except subprocess.CalledProcessError: raise ValueError('Fixture repository is not accessible yet')
    monkeypatch.setattr(provisioning, '_git', checked)
    _, row = await create(service, starter['id'])
    assert row['setup']['status'] == 'partial'
    assert row['setup']['repositories'][0]['sha'] == expected_sha
    assert row['setup']['repositories'][0]['branch'] == 'trunk'
    (Path(row['path']) / 'one/source.txt').write_text('User work')
    local_git(source2)
    await service.dispatch('workspace.setup.retry', {'workspaceId': row['id'], 'expectedRevision': row['setup']['revision']}, command_id='retry-two')
    row = await settle(service)
    assert row['setup']['status'] == 'ready'
    assert (Path(row['path']) / 'one/source.txt').read_text() == 'User work'
    assert (Path(row['path']) / 'two/source.txt').read_text() == 'version one'
    assert not (Path(row['path']) / '.git').exists()


async def test_restart_reads_completed_receipt_without_replaying(service):
    _, row = await create(service)
    identity = row['setupId']
    folder = Path(row['path'])
    (folder / '.amplifier/AGENTS.md').write_text('User edit')
    # Emulate a crash after the durable setup result but before state publication.
    row['setup'] = {**row['setup'], 'status': 'pending'}
    service._save()
    await service.close()
    restored = AppService(service.data_dir)
    try:
        saved = next(w for w in restored.state['workspaces'] if w.get('setupId') == identity)
        assert saved['setup']['status'] == 'ready'
        assert (folder / '.amplifier/AGENTS.md').read_text() == 'User edit'
        assert not restored.tasks
    finally:
        await restored.close()


async def test_restart_marks_unowned_running_setup_interrupted(service):
    _, row = await create(service)
    target = provisioning.receipt_path(service.data_dir, row['setupId'])
    durable = json.loads(target.read_text())
    durable['status'] = 'running'
    from amplifier_worktrees.git import atomic
    atomic(target, durable)
    await service.close()
    restored = AppService(service.data_dir)
    try:
        saved = next(w for w in restored.state['workspaces'] if w.get('setupId') == row['setupId'])
        assert saved['setup']['status'] == 'interrupted'
        assert not restored.tasks
    finally:
        await restored.close()


async def test_scaffold_symlink_cannot_write_outside_workspace(service, tmp_path, monkeypatch):
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = provisioning.run
    def swap(home, identity, progress=None):
        setup = provisioning.inspect(home, identity)
        (Path(setup['path']) / '.amplifier').symlink_to(outside, target_is_directory=True)
        return original(home, identity, progress)
    monkeypatch.setattr(provisioning, 'run', swap)
    _, row = await create(service)
    assert row['setup']['status'] == 'failed'
    assert list(outside.iterdir()) == []
    with pytest.raises(AppError, match='still preparing'):
        await service.dispatch('session.create', {})


def test_git_observation_disables_monitor_and_filters(tmp_path):
    repo = tmp_path / 'repo'
    local_git(repo)
    sentinel = tmp_path / 'executed'
    helper = tmp_path / 'monitor'
    helper.write_text(f'#!/bin/sh\ntouch \"{sentinel}\"\n')
    helper.chmod(0o755)
    subprocess.run(['git', '-C', str(repo), 'config', 'core.fsmonitor', str(helper)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'filter.bad.clean', f'touch \"{sentinel}\"; cat'], check=True)
    (repo / '.gitattributes').write_text('source.txt filter=bad\n')
    (repo / 'source.txt').write_text('Changed')
    fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert 'source.txt' in provisioning._git(fd, 'status', '--porcelain')
    finally:
        os.close(fd)
    assert not sentinel.exists()


async def test_scaffold_progress_refreshes_defaults_before_import_finishes(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = provisioning.run
    def gated(home, identity, progress=None):
        def publish(value):
            if progress:
                progress(value)
            entered.set()
            assert release.wait(5)
        return original(home, identity, publish)
    monkeypatch.setattr(provisioning, 'run', gated)
    starter = (await service.dispatch('workspace.starters.save', {'starter': {'name': 'Default', 'bundle': 'anchors'}}))['result']
    plan = (await service.dispatch('workspace.prepare', {'name': 'Defaults', 'starterId': starter['id']}))['result']
    key = json.dumps([plan['path'], ''], separators=(',', ':'))
    service.state['draftDefaults'] = {key: {'bundle': 'work', 'phase': 'ready'}}
    revision = service.state.get('configurationRevision', 0)
    await service.dispatch('workspace.create', {'planId': plan['planId']}, command_id='defaults-create')
    await asyncio.to_thread(entered.wait, 2)
    try:
        assert key not in service.state.get('draftDefaults', {})
        assert service.state['configurationRevision'] > revision
        await service.dispatch('session.create', {})
        assert service._session()['bundle'] == 'anchors'
    finally:
        release.set()
    await settle(service)
