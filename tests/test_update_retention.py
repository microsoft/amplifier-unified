import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_web import update_retention as retention


def manager_fixture(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    directory = home / 'updates'
    directory.mkdir(parents=True)
    names = [str(i) * 32 for i in range(1, 5)]
    project = home / ('runtime/q-' + 'a' * 32)
    project.mkdir(parents=True)
    for name in names:
        folder = directory / 'releases' / name
        folder.mkdir(parents=True)
        (folder / 'validated.json').write_text(json.dumps({'hostVersion': '0.1.0', 'sources': 1}))
        (folder / 'runtime-installed.json').write_text('[]')
        (folder / 'runtime-project.json').write_text(json.dumps({'project': project.name}))
    (directory / 'active.json').write_text(json.dumps({'current': names[0], 'previous': names[1]}))
    state = {'updates': {}}
    lifecycle = asyncio.Lock()
    manager = SimpleNamespace(home=home, directory=directory, service=SimpleNamespace(
        state=state, runtime_lifecycle=lambda: lifecycle, runtime_lifecycle_lock=lifecycle),
        busy=lambda: False, awaiting_restart=lambda: False)
    # Keep an otherwise unprotected generation through an external reference.
    state['referenced'] = str(directory / 'releases' / names[2])
    monkeypatch.setattr(retention, 'process_references', lambda home: '')
    monkeypatch.setattr(retention, 'configuration_references', lambda manager: state['referenced'])
    async def inspect(manager, *, timeout):
        return retention.process_references(manager.home) + retention.configuration_references(manager)
    async def verify(project, folder, *, timeout):
        pass
    monkeypatch.setattr(retention, 'inspect_references', inspect)
    monkeypatch.setattr(retention, 'verify_generation', verify)
    return manager, names, project


async def test_cleanup_keeps_current_rollback_referenced_and_shared_runtime(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    result = await retention.reclaim(manager)
    assert result == {'removed': 1, 'retained': 3, 'generationCursor': names[3]}
    assert project.exists()
    for name in names[:3]:
        assert (manager.directory / 'releases' / name).exists()
    assert not (manager.directory / 'releases' / names[3]).exists()


async def test_cleanup_preview_completes_while_runtime_admission_is_occupied(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    async with manager.service.runtime_lifecycle():
        result = await asyncio.wait_for(retention.reclaim(manager, inspect_only=True), 2)
    assert result['complete'] is True
    assert [row['id'] for row in result['candidates']] == [names[3]]
    assert result['removed'] == 0
    assert all((manager.directory / 'releases' / name).exists() for name in names)
    assert project.exists()
    assert not (manager.directory / 'retired').exists()


@pytest.mark.parametrize('protection', ['active', 'configuration'])
async def test_cleanup_preview_never_authorizes_later_removal(tmp_path, monkeypatch, protection):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    preview = await retention.reclaim(manager, inspect_only=True)
    selected = {row['id'] for row in preview['candidates']}
    assert selected == {names[3]}
    if protection == 'active':
        (manager.directory / 'active.json').write_text(json.dumps({
            'current': names[3], 'previous': names[0]}))
    else:
        manager.service.state['referenced'] += '\n' + str(manager.directory / 'releases' / names[3])
    result = await retention.reclaim(manager, selected=selected)
    assert result['removed'] == 0
    assert all((manager.directory / 'releases' / name).exists() for name in names)
    assert project.exists()


async def test_unknown_references_or_modified_receipts_never_trigger_removal(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    def unavailable(home):
        raise ValueError('cannot inspect processes')
    monkeypatch.setattr(retention, 'process_references', unavailable)
    result = await retention.reclaim(manager)
    assert result['removed'] == 0
    monkeypatch.setattr(retention, 'process_references', lambda home: str(manager.directory / 'releases' / names[3]))
    assert (await retention.reclaim(manager))['removed'] == 0
    monkeypatch.setattr(retention, 'process_references', lambda home: '')
    (manager.directory / 'releases' / names[3] / 'validated.json').write_text('unknown receipt')
    assert (await retention.reclaim(manager))['removed'] == 0
    assert project.exists()


@pytest.mark.parametrize(('failure', 'stage', 'reason'), [
    (TimeoutError('private timeout detail'), 'inspection', 'timeout'),
    (retention.ProbeFailure('process-failed'), 'inspection', 'process-failed'),
    (retention.ProbeFailure('invalid-response'), 'inspection', 'invalid-response'),
    (ValueError('private configuration path'), 'inspection', 'unavailable'),
    (ValueError('private generation path'), 'protected-generations', 'unavailable'),
])
async def test_reference_failure_keeps_storage_and_reports_only_bounded_facts(
        tmp_path, monkeypatch, failure, stage, reason):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    async def inspect(*args, **kwargs):
        raise failure
    def protect(*args):
        raise failure
    monkeypatch.setattr(retention, 'inspect_references' if stage == 'inspection' else 'protected_generations',
                        inspect if stage == 'inspection' else protect)
    result = await retention.reclaim(manager, inspect_only=True)
    detail = result['referenceFailure']
    assert (detail['stage'], detail['reason']) == (stage, reason)
    assert 0 <= detail['elapsedMs'] < 2000
    assert result['complete'] is False and result['candidates'] == []
    assert 'private' not in json.dumps(result)
    assert all((manager.directory / 'releases' / name).is_dir() for name in names)
    assert project.exists()


async def test_live_generation_lease_prevents_cleanup_while_unrelated_work_continues(tmp_path, monkeypatch):
    import os
    from amplifier_web.generation_leases import acquire
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    manager.busy = lambda: True
    folder=manager.directory/'releases'/names[3]
    marker=json.loads((folder/'validated.json').read_text())
    marker['generationSchema']=1
    (folder/'validated.json').write_text(json.dumps(marker))
    lease=acquire(manager.home,names[3],os.getpid())
    assert (await retention.reclaim(manager))['removed']==0
    lease.unlink()
    assert (await retention.reclaim(manager))['removed']==1
    assert project.exists()


async def test_retained_environment_keeps_earlier_editable_sources(tmp_path, monkeypatch):
    manager,names,project=manager_fixture(tmp_path,monkeypatch)
    current=manager.directory/'releases'/names[0]
    earlier=manager.directory/'releases'/names[3]
    (current/'runtime-installed.json').write_text(json.dumps([{'path':str(earlier/'foundation/cache/module')}]))
    assert (await retention.reclaim(manager))['removed']==0
    assert earlier.exists()


async def test_slow_qualification_does_not_block_activation_and_new_pointer_is_kept(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    inspecting, proceed = asyncio.Event(), asyncio.Event()
    async def verify(project, folder, *, timeout):
        inspecting.set()
        await proceed.wait()
    monkeypatch.setattr(retention, 'verify_generation', verify)
    cleanup = asyncio.create_task(retention.reclaim(manager))
    await asyncio.wait_for(inspecting.wait(), 1)
    async with asyncio.timeout(1):
        async with manager.service.runtime_lifecycle():
            (manager.directory / 'active.json').write_text(json.dumps({
                'current': names[3], 'previous': names[0]}))
    proceed.set()
    result = await asyncio.wait_for(cleanup, 1)
    assert result['removed'] == 0
    assert (manager.directory / 'releases' / names[3]).exists()


async def test_scheduled_retirement_does_not_wrap_inspection_in_lifecycle(tmp_path, monkeypatch):
    from amplifier_web.updates import UpdateManager
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    manager.closed = False
    published = []
    async def no_delay(seconds):
        pass
    async def verify(project, folder, *, timeout):
        assert not manager.service.runtime_lifecycle_lock.locked()
    async def publish(**values):
        published.append(values)
    from amplifier_web import storage_cleanup
    async def policy_run(service, identity, *, automatic):
        assert service is manager.service and automatic
        return await retention.reclaim(manager)
    monkeypatch.setattr(storage_cleanup, 'run', policy_run)
    monkeypatch.setattr(retention, 'verify_generation', verify)
    monkeypatch.setattr(asyncio, 'sleep', no_delay)
    manager.publish = publish
    await UpdateManager.retire_storage(manager)
    assert published[0]['storage']['removed'] == 1
    assert published[0]['storage']['durationMs'] >= 0


@pytest.mark.parametrize('pending', ['pendingRelease', 'pendingRollback', 'pendingApp', 'pendingSmartTools'])
async def test_pending_update_defers_all_storage_inspection(tmp_path, monkeypatch, pending):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    manager.service.state['updates'][pending] = names[3]
    async def unexpected(*args, **kwargs):
        pytest.fail('Pending update must take precedence over historical inspection')
    monkeypatch.setattr(retention, 'inspect_references', unexpected)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert all((manager.directory / 'releases' / name).exists() for name in names)


@pytest.mark.parametrize('protection', ['pending', 'lease', 'configuration', 'process', 'graph', 'receipt'])
async def test_new_protection_during_inspection_is_rechecked_before_removal(tmp_path, monkeypatch, protection):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    async def verify(project, candidate, *, timeout):
        assert not manager.service.runtime_lifecycle_lock.locked()
        if protection == 'pending':
            manager.service.state['updates']['pendingRelease'] = names[3]
        elif protection == 'lease':
            from amplifier_web.generation_leases import acquire
            acquire(manager.home, names[3], os.getpid())
        elif protection == 'configuration':
            manager.service.state['referenced'] += str(folder)
        elif protection == 'process':
            monkeypatch.setattr(retention, 'process_references', lambda home: str(folder))
        elif protection == 'graph':
            current = manager.directory / 'releases' / names[0]
            (current / 'runtime-installed.json').write_text(json.dumps([{'path': str(folder / 'source')}]))
        else:
            (folder / 'validated.json').write_text('{}')
    monkeypatch.setattr(retention, 'verify_generation', verify)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert folder.exists()


async def test_final_reference_timeout_retains_candidate_and_releases_admission(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    async def inspect(manager, *, timeout):
        if manager.service.runtime_lifecycle_lock.locked():
            assert timeout <= retention.REFERENCE_SECONDS
            raise TimeoutError()
        return manager.service.state['referenced']
    monkeypatch.setattr(retention, 'inspect_references', inspect)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert (manager.directory / 'releases' / names[3]).exists()
    assert not manager.service.runtime_lifecycle_lock.locked()


@pytest.mark.parametrize('delayed_call', [2, 3])
async def test_delayed_final_reference_snapshot_cannot_retire_newly_used_copy(
        tmp_path, monkeypatch, delayed_call):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    calls, now = 0, 100.0
    monkeypatch.setattr(retention, 'time', SimpleNamespace(monotonic=lambda: now))

    async def inspect(manager, *, timeout):
        nonlocal calls, now
        calls += 1
        snapshot = manager.service.state['referenced']
        if calls == delayed_call:
            assert manager.service.runtime_lifecycle_lock.locked()
            # The worker completed before this reference appeared, but host
            # delivery exceeded the final check's original end-to-end budget.
            manager.service.state['referenced'] += str(folder)
            now += timeout + .1
        return snapshot

    monkeypatch.setattr(retention, 'inspect_references', inspect)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert calls == delayed_call and folder.exists() and project.exists()
    assert not manager.service.runtime_lifecycle_lock.locked()


async def test_batch_limit_advances_past_unverifiable_generations(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    manager.service.state['referenced'] = ''
    monkeypatch.setattr(retention, 'MAX_CANDIDATES', 1)
    verified = []
    async def verify(project, folder, *, timeout):
        verified.append(folder.name)
        raise TimeoutError()
    monkeypatch.setattr(retention, 'verify_generation', verify)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert verified == [names[2]]
    assert (await retention.reclaim(manager))['removed'] == 0
    assert verified == names[2:]


async def test_slow_removal_does_not_block_admission_and_cancelled_removal_resumes(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    entered, proceed = asyncio.Event(), asyncio.Event()
    remove = retention.remove_retired
    async def delayed(manager, entry, *, timeout):
        entered.set()
        await proceed.wait()
        await remove(manager, entry, timeout=timeout)
    monkeypatch.setattr(retention, 'remove_retired', delayed)
    cleanup = asyncio.create_task(retention.reclaim(manager))
    await asyncio.wait_for(entered.wait(), 2)
    async with asyncio.timeout(1):
        async with manager.service.runtime_lifecycle():
            pass  # Runtime activation remains admissible during recursive deletion.
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup
    assert not manager.service.runtime_lifecycle_lock.locked()
    assert not (manager.directory / 'releases' / names[3]).exists()
    entries = list((manager.directory / 'retired').iterdir())
    assert len(entries) == 1
    assert (entries[0] / 'payload' / 'release').is_dir()
    assert json.loads((entries[0] / 'retirement.json').read_text())['phase'] == 'retired'
    assert project.exists()  # Shared with protected generations; never renamed.
    monkeypatch.setattr(retention, 'remove_retired', remove)
    assert (await retention.reclaim(manager))['removed'] == 1
    assert not entries[0].exists()


async def test_partial_retirement_keeps_payload_and_original_runtime(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    unique = project.parent / ('q-' + 'b' * 32)
    unique.mkdir()
    (folder / 'runtime-project.json').write_text(json.dumps({'project': unique.name}))
    rename = Path.rename
    def fail_runtime(path, target):
        if path == unique:
            raise OSError('Synthetic rename failure')
        return rename(path, target)
    monkeypatch.setattr(Path, 'rename', fail_runtime)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert unique.exists()
    entries = list((manager.directory / 'retired').iterdir())
    assert len(entries) == 1
    assert (entries[0] / 'payload' / 'release').is_dir()
    assert json.loads((entries[0] / 'retirement.json').read_text())['phase'] == 'retiring'
    assert (await retention.reclaim(manager))['removed'] == 0
    assert entries[0].exists()
    assert unique.exists()


async def test_retirement_removes_only_unshared_runtime_and_keeps_protected_projects(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    unique = project.parent / ('q-' + 'b' * 32)
    unique.mkdir()
    (unique / 'payload').write_text('synthetic installed package')
    (folder / 'runtime-project.json').write_text(json.dumps({'project': unique.name}))
    result = await retention.reclaim(manager)
    assert result['removed'] == 1
    assert not folder.exists()
    assert not unique.exists()
    assert project.exists()
    assert all((manager.directory / 'releases' / name).exists() for name in names[:3])
    assert not list((manager.directory / 'retired').iterdir())


async def test_incomplete_retirements_do_not_starve_later_storage_after_restart(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    retired = manager.directory / 'retired'
    for digit in ('1', '2'):
        entry = retired / (digit * 32)
        entry.mkdir(parents=True)
        (entry / 'retirement.json').write_text(json.dumps({'schema': 1, 'phase': 'retiring'}))
    folder = manager.directory / 'releases' / names[3]
    entry = retention.retire(manager, folder, project, remove_project=False)
    # Give the complete entry a stable identity after both incomplete entries.
    entry = entry.rename(retired / ('f' * 32))
    monkeypatch.setattr(retention, 'MAX_CANDIDATES', 1)
    for _ in range(2):
        result = await retention.reclaim(manager)
        assert result['removed'] == 0
        manager.service.state['updates']['storage'] = result
        del manager._retired_cursor  # Restart reconstructs the saved cursor.
    assert (await retention.reclaim(manager))['removed'] == 1
    assert not entry.exists()
    assert (retired / ('1' * 32)).exists()
    assert (retired / ('2' * 32)).exists()


async def test_incomplete_retirements_leave_budget_for_unretired_generations(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    retired = manager.directory / 'retired'
    for index in range(retention.MAX_CANDIDATES):
        entry = retired / f'{index:032x}'
        entry.mkdir(parents=True)
        (entry / 'retirement.json').write_text(json.dumps({'schema': 1, 'phase': 'retiring'}))
    result = await retention.reclaim(manager)
    assert result['removed'] == 1
    assert not (manager.directory / 'releases' / names[3]).exists()
    assert len(list(retired.iterdir())) == retention.MAX_CANDIDATES
    assert all((manager.directory / 'releases' / name).exists() for name in names[:3])
    assert project.exists()


@pytest.mark.parametrize('late', [False, True])
async def test_modern_runtime_still_respects_external_project_references(tmp_path, monkeypatch, late):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    data = json.loads((folder / 'validated.json').read_text())
    data['generationSchema'] = 1
    (folder / 'validated.json').write_text(json.dumps(data))
    if late:
        async def verify(project, folder, *, timeout):
            manager.service.state['referenced'] += str(project)
        monkeypatch.setattr(retention, 'verify_generation', verify)
    else:
        manager.service.state['referenced'] += str(project)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert folder.exists()
    assert project.exists()


@pytest.mark.parametrize('cancel', [False, True])
async def test_readonly_probe_is_terminated_on_timeout_or_cancellation(tmp_path, monkeypatch, cancel):
    import sys
    original = asyncio.create_subprocess_exec
    created, spawned = asyncio.Event(), []
    parent_loop = asyncio.get_running_loop()
    async def create(*args, **kwargs):
        proc = await original(sys.executable, '-c', 'import time; time.sleep(60)', **kwargs)
        spawned.append(proc)
        parent_loop.call_soon_threadsafe(created.set)
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    task = asyncio.create_task(retention._probe('generation', {}, timeout=60 if cancel else 0.1))
    await asyncio.wait_for(created.wait(), 2)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await asyncio.wait_for(task, 2)
    assert spawned[0].returncode is not None


async def test_real_probe_checks_receipts_and_private_configuration(tmp_path, monkeypatch):
    home, shared = tmp_path / 'app', tmp_path / 'shared'
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    settings = home / 'config' / 'settings.yaml'
    settings.parent.mkdir(parents=True)
    paths = [home / 'updates/releases' / ('b' * 32), home / 'runtime' / ('q-' + 'c' * 32),
             home / 'smart-tools/installs' / ('d' * 24)]
    paths.append(home / 'runtime' / 'legacy.project-name')
    for path in paths:
        path.mkdir(parents=True)
    settings.write_text(json.dumps({'paths': [str(path / 'subdir') for path in paths],
                                    'private': 'unrelated-private-setting'}))
    result = await retention._probe('references', {'home': str(home), 'workspaces': []}, timeout=3)
    assert result == sorted(map(str, paths))
    folder = tmp_path / 'receipt'
    folder.mkdir()
    (folder / 'runtime-installed.json').write_text('[]')
    await retention._probe('generation', {'project': str(tmp_path / 'empty'), 'folder': str(folder)}, timeout=3)
    (folder / 'runtime-installed.json').write_text('[{"name": "missing"}]')
    with pytest.raises(ValueError, match='could not be verified'):
        await retention._probe('generation', {'project': str(tmp_path / 'empty'), 'folder': str(folder)}, timeout=3)


@pytest.mark.parametrize('plan_name', ['configuration.json', 'effective-configuration.json'])
async def test_saved_session_module_source_protects_generation_from_retirement(tmp_path, monkeypatch, plan_name):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    tools = folder / 'foundation/cache' / 'example' / 'modules/read-only'
    tools.mkdir(parents=True)
    override = manager.home / 'sessions' / 'saved-original' / plan_name
    override.parent.mkdir(parents=True)
    override.write_text(json.dumps({'tools': [{'module': 'read-only', 'source': str(tools),
                                              'config': {'private': 'not-probe-output'}}]}))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    async def actual(manager, *, timeout):
        return await retention._probe('references', {'home': str(manager.home), 'workspaces': []}, timeout=timeout)
    monkeypatch.setattr(retention, 'inspect_references', actual)
    result = await retention.reclaim(manager, selected={names[3]})
    assert result['removed'] == 0 and tools.is_dir()
    references = await actual(manager, timeout=3)
    assert str(folder) in references
    assert 'not-probe-output' not in str(references) and str(tools) not in str(references)
    # Moving the saved reference frees the old generation independently.
    override.write_text(json.dumps({'tools': [{'module': 'read-only', 'source': 'git+https://example.invalid/read-only'}]}))
    assert (await retention.reclaim(manager, selected={names[3]}))['removed'] == 1
    assert not folder.exists() and project.is_dir()


@pytest.mark.parametrize('malformed', [False, True])
async def test_release_retirement_rechecks_new_session_plan_before_removal(tmp_path, monkeypatch, malformed):
    manager, names, _ = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))

    async def actual(manager, *, timeout):
        return await retention._probe('references', {'home': str(manager.home), 'workspaces': []}, timeout=timeout)

    async def add_plan(project, candidate, *, timeout):
        assert candidate == folder
        plan = manager.home / 'sessions' / 'new-fork' / 'configuration.json'
        plan.parent.mkdir(parents=True)
        plan.write_text('private-incomplete-plan' if malformed else json.dumps({
            'hooks': [{'module': 'local-hook', 'source': (folder / 'local-hook').as_uri()}]}))

    monkeypatch.setattr(retention, 'inspect_references', actual)
    monkeypatch.setattr(retention, 'verify_generation', add_plan)
    result = await retention.reclaim(manager, selected={names[3]})
    assert result['removed'] == 0 and folder.is_dir()
    assert 'private-incomplete-plan' not in str(result)


@pytest.mark.parametrize('spelling', ['file-uri', 'file-subdirectory', 'dot-segment', 'home', 'workspace'])
async def test_plan_aliases_protect_the_exact_candidate(tmp_path, monkeypatch, spelling):
    home = tmp_path / 'app with spaces'
    workspace = tmp_path / 'workspace'
    folder = home / 'updates/releases' / ('a' * 32)
    folder.mkdir(parents=True)
    workspace.mkdir()
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    source = {
        'file-uri': (folder / 'module').as_uri(),
        'file-subdirectory': home.as_uri() + '#subdirectory=updates/releases/' + folder.name + '/module',
        'dot-segment': str(tmp_path / 'workspace' / '..' / folder.relative_to(tmp_path)),
        'home': str(Path('~') / folder.relative_to(tmp_path)),
        'workspace': os.path.relpath(folder, workspace),
    }[spelling]
    saved = home / 'sessions/original/configuration.json'
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps({'tools': [{'module': 'old-tool', 'source': source}]}))
    assert await retention._probe('references', {'home': str(home), 'workspaces': [str(workspace)]},
                                  timeout=3) == [str(folder)]


def test_case_insensitive_probe_returns_the_storage_owners_spelling(tmp_path, monkeypatch):
    import ntpath
    folder = tmp_path / 'runtime' / 'Legacy.PreQualified'
    folder.mkdir(parents=True)
    short = folder.with_name('Legacy')
    short.mkdir()
    # Test the Windows comparison policy in the POSIX qualification guest.
    monkeypatch.setattr(os.path, 'normcase', ntpath.normcase)
    monkeypatch.setattr(retention, 'process_references', lambda home: '')
    monkeypatch.setattr(retention, 'configuration_references', lambda manager: str(folder).upper() + '/source')
    assert retention.storage_references(SimpleNamespace(home=tmp_path)) == sorted(map(str, (short, folder)))


@pytest.mark.parametrize('link_target', ['old-module', 'loop'])
async def test_saved_module_link_keeps_generation_until_repointed(tmp_path, monkeypatch, link_target):
    manager, names, _ = manager_fixture(tmp_path, monkeypatch)
    old = manager.directory / 'releases' / names[3]
    module = old / 'tool'
    module.mkdir()
    link = tmp_path / 'workspace' / 'pinned-tool'
    link.parent.mkdir()
    link.symlink_to(module if link_target == 'old-module' else link, target_is_directory=True)
    plan = manager.home / 'sessions/chat/configuration.json'
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps({'tools': [{'module': 'pinned', 'source': str(link)}]}))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))

    async def actual(manager, *, timeout):
        return await retention._probe('references', {'home': str(manager.home), 'workspaces': []}, timeout=timeout)
    monkeypatch.setattr(retention, 'inspect_references', actual)
    assert (await retention.reclaim(manager, selected={old.name}))['removed'] == 0
    assert module.is_dir()
    replacement = tmp_path / 'replacement'
    replacement.mkdir()
    link.unlink()
    link.symlink_to(replacement, target_is_directory=True)
    # Unrelated data may contain an invalid link; only declared module
    # sources are resolved against the filesystem.
    unrelated = tmp_path / 'unrelated'
    unrelated.symlink_to(unrelated, target_is_directory=True)
    plan.write_text(json.dumps({'tools': [{'module': 'pinned', 'source': str(link),
                                          'config': {'path': str(unrelated)}}]}))
    assert (await retention.reclaim(manager, selected={old.name}))['removed'] == 1
    assert link.resolve() == replacement and not old.exists()
