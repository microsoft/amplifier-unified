"""Explicit repository selection and safe handoff from multi-repository homes."""
import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_worktrees.git import MAX_FILES, git
from amplifier_worktrees import GitWorktrees
from test_worktrees import repository, settle


@pytest.mark.parametrize('action', ['create', 'attach'])
def test_command_identity_cannot_race_across_different_repository_locks(tmp_path, action):
    roots = []
    for name in ('one', 'two'):
        parent = tmp_path / name; parent.mkdir()
        roots.append(repository(parent))
    store = tmp_path / 'managed'
    revisions = [GitWorktrees(store).inspect(root)['sourceRevision'] for root in roots]
    barrier = threading.Barrier(2)
    def compete(index):
        manager = GitWorktrees(store)
        barrier.wait(timeout=5)
        try:
            if action == 'create':
                return manager.create(roots[index], command_id='same-command', expected_revision=revisions[index], session_id=str(index))
            return manager.attach(roots[index], source=roots[index], command_id='same-command', session_id=str(index))
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, range(2)))
    accepted = [row for row in outcomes if isinstance(row, dict)]
    refused = [row for row in outcomes if isinstance(row, str)]
    assert len(accepted) == len(refused) == 1
    assert 'command ID' in refused[0]
    assert GitWorktrees(store).records() == accepted
    assert accepted[0]['status'] == 'ready'


@pytest.fixture
async def multi(tmp_path, monkeypatch):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    repos = []
    for name in ('api', 'client'):
        parent = workspace / name
        parent.mkdir()
        root = repository(parent)
        repos.append(root)
    runtime = SimpleNamespace(close=AsyncMock(), quiesce_for_handoff=AsyncMock(return_value={'quiesced': True, 'inputsReplayed': False}))
    app = AppService(tmp_path / 'app', runtime, workspace=workspace)
    await app.dispatch('session.create', {'title': 'Two repositories'})
    sid = app._session()['id']
    yield app, sid, workspace, repos
    await app.close()


async def inspect(app, sid, path):
    return (await app.dispatch('worktree.inspect', {'sessionId': sid, 'repositoryPath': str(path)}))['result']


async def create(app, sid, path, identity, **options):
    source = (await inspect(app, sid, path))['repository']
    return (await app.dispatch('worktree.create', {'sessionId': sid, 'repositoryPath': source['root'],
        'sourceRevision': source['sourceRevision'], **options}, command_id=identity))['result']


async def test_two_child_repositories_keep_separate_identity_and_dirty_work(multi):
    app, sid, workspace, repos = multi
    assert not (workspace / '.git').exists()
    before = copy.deepcopy(app._session(sid))
    (repos[0] / 'a.txt').write_text('Uncommitted API work')
    first = await create(app, sid, 'api/repo', 'api-copy')
    second = await create(app, sid, 'client/repo', 'client-copy')
    assert first['repository'] != second['repository']
    assert first['source'] == str(repos[0]) and second['source'] == str(repos[1])
    for row in (first, second):
        assert (Path(row['path']) / 'a.txt').read_text() == 'original\n'
    assert (repos[0] / 'a.txt').read_text() == 'Uncommitted API work'
    state = (await app.dispatch('worktree.list', {'sessionId': sid}))['result']
    assert state['historyHome'] == state['executionDirectory'] == str(workspace)
    assert state['executionRevision'] == 0 and len(state['items']) == 2
    assert app._session(sid)['messages'] == before['messages']
    assert not app.runtime.quiesce_for_handoff.called


async def test_selected_repo_copy_is_revision_checked_independently(multi):
    app, sid, _, repos = multi
    inspected = (await inspect(app, sid, repos[0]))['repository']
    (repos[0] / 'a.txt').write_text('Changed since inspection')
    with pytest.raises(AppError, match='source checkout changed'):
        await app.dispatch('worktree.create', {'sessionId': sid, 'repositoryPath': str(repos[0]),
            'sourceRevision': inspected['sourceRevision']}, command_id='stale')
    copied = await create(app, sid, repos[0], 'carry', mode='carry_dirty')
    assert (Path(copied['path']) / 'a.txt').read_text() == 'Changed since inspection'
    assert (repos[1] / 'a.txt').read_text() == 'original\n'


async def move(app, sid, checkout, command_id, *, agent=False):
    args = {'sessionId': sid, 'id': checkout['id'] if checkout else None,
        'expectedExecutionRevision': app._session(sid).get('executionRevision', 0)}
    if agent:
        pending = (await app.app_bridge('dispatch', {'action': 'worktree.handoff',
            'id': command_id, 'args': args}, sid))['result']
    else:
        pending = (await app.dispatch('worktree.handoff', args, command_id=command_id))['result']
    await settle(app)
    return app.worktrees.read(sid, pending['id'])


@pytest.mark.parametrize('agent', [False, True])
async def test_non_git_home_moves_between_selected_repositories_and_back_without_changing_work(multi, agent):
    app, sid, workspace, repos = multi
    original = repos[0]
    (original / 'a.txt').write_text('staged\n'); git(original, 'add', 'a.txt')
    (original / 'a.txt').write_text('staged\nunstaged\n')
    (original / 'untracked.txt').write_text('keep untracked')
    (original / 'ignored.txt').write_text('keep ignored')
    before = [app.worktrees.git.inspect(root)['sourceRevision'] for root in repos]
    carried = await create(app, sid, original, 'dirty-copy', mode='carry_dirty')
    other = await create(app, sid, repos[1], 'other-copy')
    carried_path = Path(carried['path'])
    carried_before = app.worktrees.git.inspect(carried_path)['sourceRevision']
    retained = copy.deepcopy(app._session(sid))
    for revision, checkout in enumerate((carried, other, None), 1):
        receipt = await move(app, sid, checkout, f'move-{revision}', agent=agent)
        expected = checkout['path'] if checkout else str(workspace)
        assert receipt['phase'] == 'applied', receipt
        assert receipt['target'] == app._session(sid)['workingDirectory'] == expected
        assert app._session(sid)['executionRevision'] == revision
        assert app._session(sid)['workspace'] == str(workspace)
        assert app._session(sid)['messages'] == retained['messages']
        assert receipt['inputsReplayed'] is False
    assert not (workspace / '.git').exists()
    assert app.runtime.quiesce_for_handoff.await_count == 3
    assert [app.worktrees.git.inspect(root)['sourceRevision'] for root in repos] == before
    assert app.worktrees.git.inspect(carried_path)['sourceRevision'] == carried_before
    assert git(carried_path, 'show', ':a.txt') == b'staged\n'
    assert (carried_path / 'a.txt').read_text() == 'staged\nunstaged\n'
    assert (carried_path / 'untracked.txt').read_text() == 'keep untracked'
    assert (original / 'ignored.txt').read_text() == 'keep ignored'


@pytest.mark.parametrize('side', ['source', 'target'])
async def test_handoff_keeps_large_retained_history_without_building_a_manifest(multi, side, monkeypatch):
    from amplifier_web.runtime import RuntimeManager

    app, sid, workspace, repos = multi
    source = await create(app, sid, repos[0], 'history-source')
    target = await create(app, sid, repos[0], 'history-target')
    assert (await move(app, sid, source, 'enter-history-source'))['phase'] == 'applied'
    paths = {name: Path(checkout['path']) for name, checkout in (('source', source), ('target', target))}
    retained = paths[side] / 'retained-history'
    retained.mkdir()
    for index in range(MAX_FILES + 1):
        (retained / f'{index}.txt').write_bytes(f'Retained evidence {index}\n'.encode())
    # Manifest-producing inspection still refuses. Only placement is exempt
    # from copy limits; no new ignore rule, copying or history cleanup is needed.
    with pytest.raises(ValueError, match='Too many untracked files'):
        app.worktrees.git.inspect(paths[side])
    before = {name: {str(path.relative_to(root)): path.read_bytes()
                     for path in root.rglob('*') if path.is_file()}
              for name, root in paths.items()}
    session = app._session(sid)
    session['messages'] = [{'id': 'retained-message', 'role': 'user', 'text': 'Keep this evidence'}]
    messages = copy.deepcopy(session['messages'])

    # Exercise the supported action through the real idle-runtime release and
    # Foundation ownership store, not a mocked successful release response.
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(workspace / 'session-state'))
    app.runtime = RuntimeManager()
    app.worktrees.bind_runtime()
    receipt = await move(app, sid, target, 'leave-history-source', agent=True)
    assert receipt['phase'] == 'applied', receipt
    assert receipt['release']['quiesced'] and receipt['release']['releaseOwner']['acquisition_id']
    assert receipt['release']['releasedOwner'] is None  # No active model writer in this synthetic smoke.
    assert not receipt['inputsReplayed'] and not app.runtime.workers
    assert session['workingDirectory'] == target['path'] and session['executionRevision'] == 2
    assert session['workspace'] == str(workspace) and session['messages'] == messages
    assert {name: {str(path.relative_to(root)): path.read_bytes()
                   for path in root.rglob('*') if path.is_file()}
            for name, root in paths.items()} == before
    with pytest.raises(ValueError, match='Too many untracked files'):
        app.worktrees.git.inspect(paths[side])


@pytest.mark.parametrize('destination', ['source', 'target'])
@pytest.mark.parametrize('returning', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
async def test_non_git_home_unknown_handoff_reconciles_exact_home_or_checkout(multi, destination, returning, legacy):
    app, sid, workspace, repos = multi
    checkout = await create(app, sid, repos[0], 'selected-copy')
    if returning:
        assert (await move(app, sid, checkout, 'outbound'))['phase'] == 'applied'
    app.runtime.quiesce_for_handoff.side_effect = RuntimeError('lost release acknowledgment')
    unknown = await move(app, sid, None if returning else checkout, 'uncertain-move')
    assert unknown['phase'] == 'unknown'
    assert app._session(sid)['configurationBusy']
    if legacy:
        # Durable receipts from before destination bindings still recover only
        # an exact canonical home or a currently associated checkout.
        unknown.pop('sourceBinding'); unknown.pop('targetBinding')
        app.worktrees.save(unknown)
    calls = app.runtime.quiesce_for_handoff.await_count
    app.runtime.quiesce_for_handoff.side_effect = None
    args = {'sessionId': sid, 'id': unknown['id'], 'expectedRevision': unknown['revision'],
        'destination': destination, 'evidence': 'Inspected the exact source and target; all writers released.'}
    pending = (await app.dispatch('worktree.reconcile', args, command_id='reconcile'))['result']
    await settle(app)
    result = app.worktrees.read(sid, pending['id'])
    assert result['phase'] == 'applied', result
    assert app._session(sid)['workingDirectory'] == unknown[destination]
    assert app._session(sid)['workspace'] == str(workspace)
    assert not app._session(sid)['configurationBusy']
    assert app.worktrees.read(sid, unknown['id'])['phase'] == 'reconciled'
    assert app.runtime.quiesce_for_handoff.await_count == calls + 1
    retry = (await app.dispatch('worktree.reconcile', args, command_id='reconcile'))['result']
    assert retry['duplicate'] and retry['phase'] == 'applied'
    assert app.runtime.quiesce_for_handoff.await_count == calls + 1


@pytest.mark.parametrize('destination', ['source', 'target'])
async def test_non_git_handoff_restart_preserves_bindings_and_agent_reconciliation(tmp_path, destination):
    workspace = tmp_path / 'project'; workspace.mkdir()
    repo = repository(workspace)
    runtime = SimpleNamespace(close=AsyncMock(), quiesce_for_handoff=AsyncMock(
        return_value={'quiesced': True, 'inputsReplayed': False}, side_effect=RuntimeError('lost release acknowledgment')))
    app = AppService(tmp_path / 'app', runtime, workspace=workspace)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        checkout = await create(app, sid, repo, 'selected-copy')
        unknown = await move(app, sid, checkout, 'uncertain-move')
        assert unknown['phase'] == 'unknown'
        bindings = {key: unknown[key] for key in ('sourceBinding', 'targetBinding')}
    finally:
        await app.close()
    runtime.quiesce_for_handoff.side_effect = None
    app = AppService(tmp_path / 'app', runtime, workspace=workspace)
    try:
        saved = app.worktrees.read(sid, unknown['id'])
        assert {key: saved[key] for key in bindings} == bindings
        assert saved['phase'] == 'unknown' and runtime.quiesce_for_handoff.await_count == 1
        app._session(sid)['messages'].append({'id': 'human-finding', 'role': 'user', 'inputOrigin': 'ui',
            'createdAt': time.time(), 'text': 'I inspected both folders and confirmed the writers have stopped.'})
        pending = (await app.app_bridge('dispatch', {'action': 'worktree.reconcile', 'id': 'agent-reconcile',
            'args': {'id': saved['id'], 'expectedRevision': saved['revision'], 'destination': destination,
                'evidence': 'User inspected both folders and confirmed release.', 'sourceMessageId': 'human-finding'}}, sid))['result']
        await settle(app)
        assert app.worktrees.read(sid, pending['id'])['phase'] == 'applied'
        assert app._session(sid)['workingDirectory'] == saved[destination]
        assert runtime.quiesce_for_handoff.await_count == 2
    finally:
        await app.close()


async def test_selected_checkout_replaced_by_unrelated_git_repository_rejects_before_release(multi):
    app, sid, workspace, repos = multi
    checkout = await create(app, sid, repos[0], 'selected-copy')
    # Same checkout path, different Git identity: being under the workspace
    # or a valid repository must not authorize this substituted destination.
    marker = Path(checkout['path']) / '.git'
    original = marker.read_bytes()
    try:
        marker.write_text(f'gitdir: {repos[1] / ".git"}\n')
        receipt = await move(app, sid, checkout, 'wrong-repository')
        assert receipt['phase'] == 'rejected' and receipt['step'] == 'preflight'
        assert 'recorded repository' in receipt['detail']
        assert not app.runtime.quiesce_for_handoff.called
        assert (app._session(sid).get('workingDirectory') or app._session(sid)['workspace']) == str(workspace)
        assert not app._session(sid)['configurationBusy']
    finally:
        marker.write_bytes(original)


async def test_source_checkout_repository_changed_after_receipt_rejects_before_release(multi):
    app, sid, workspace, repos = multi
    checkout = await create(app, sid, repos[0], 'source-copy')
    assert (await move(app, sid, checkout, 'outbound'))['phase'] == 'applied'
    app.runtime.quiesce_for_handoff.reset_mock()
    before = copy.deepcopy(app._session(sid))
    pending = (await app.dispatch('worktree.handoff', {
        'sessionId': sid, 'id': None, 'expectedExecutionRevision': 1,
    }, command_id='return-after-source-replacement'))['result']
    marker = Path(checkout['path']) / '.git'
    original = marker.read_bytes()
    try:
        # The receipt exists, but its background preflight has not run yet.
        marker.write_text(f'gitdir: {repos[1] / ".git"}\n')
        await settle(app)
        receipt = app.worktrees.read(sid, pending['id'])
        assert receipt['phase'] == 'rejected' and receipt['step'] == 'preflight'
        assert 'source' in receipt['detail'] and 'recorded repository' in receipt['detail']
        assert not app.runtime.quiesce_for_handoff.called
        current = app._session(sid)
        for key in ('workspace', 'workingDirectory', 'executionRevision', 'messages'):
            assert current[key] == before[key]
        assert not current['configurationBusy']
        assert not receipt['inputsReplayed']
    finally:
        marker.write_bytes(original)


async def test_legacy_reconciliation_cannot_choose_an_unassociated_repository(multi):
    app, sid, _, repos = multi
    checkout = await create(app, sid, repos[0], 'selected-copy')
    app.runtime.quiesce_for_handoff.side_effect = RuntimeError('lost release acknowledgment')
    unknown = await move(app, sid, checkout, 'uncertain-move')
    unknown.pop('sourceBinding'); unknown.pop('targetBinding')
    unknown['target'] = str(repos[1])
    app.worktrees.save(unknown)
    app.runtime.quiesce_for_handoff.reset_mock()
    with pytest.raises(AppError, match='canonical home or an associated checkout'):
        await app.dispatch('worktree.reconcile', {'sessionId': sid, 'id': unknown['id'],
            'expectedRevision': unknown['revision'], 'destination': 'target', 'evidence': 'Inspected destination'})
    assert not app.runtime.quiesce_for_handoff.called
    assert app._session(sid)['configurationBusy']


async def test_return_home_refuses_replaced_canonical_path_before_release(multi, tmp_path):
    app, sid, workspace, repos = multi
    checkout = await create(app, sid, repos[0], 'selected-copy')
    assert (await move(app, sid, checkout, 'outbound'))['phase'] == 'applied'
    app.runtime.quiesce_for_handoff.reset_mock()
    preserved = tmp_path / 'preserved-project'
    substitute = tmp_path / 'unrelated'; substitute.mkdir()
    workspace.rename(preserved)
    workspace.symlink_to(substitute, target_is_directory=True)
    try:
        receipt = await move(app, sid, None, 'return-home')
        assert receipt['phase'] == 'rejected' and receipt['step'] == 'preflight'
        assert 'recorded canonical path' in receipt['detail']
        assert app._session(sid)['workingDirectory'] == checkout['path']
        assert app._session(sid)['executionRevision'] == 1
        assert not app.runtime.quiesce_for_handoff.called
    finally:
        workspace.unlink(); preserved.rename(workspace)


async def test_attach_rejects_different_repository_and_accepts_correct_source(multi):
    app, sid, _, repos = multi
    first = await create(app, sid, repos[0], 'api-copy')
    with pytest.raises(AppError, match='must belong'):
        await app.dispatch('worktree.attach', {'sessionId': sid, 'repositoryPath': str(repos[1]),
            'path': first['path']}, command_id='wrong-repository')
    result = (await app.dispatch('worktree.attach', {'sessionId': sid, 'repositoryPath': str(repos[0]),
        'path': first['path']}, command_id='correct-repository'))['result']
    assert not result['owned'] and result['repository'] == first['repository']
    with pytest.raises(AppError, match='another checkout or source'):
        await app.dispatch('worktree.attach', {'sessionId': sid, 'repositoryPath': first['path'],
            'path': first['path']}, command_id='correct-repository')


async def test_explicit_command_and_import_paths_use_selected_copies(multi):
    app, sid, _, repos = multi
    (repos[0] / 'calculator.py').write_text('VALUE = 42\n')
    git(repos[0], 'add', 'calculator.py'); git(repos[0], 'commit', '-m', 'test module')
    (repos[1] / 'verify.py').write_text('import calculator,json,os\nprint(json.dumps(dict(value=calculator.VALUE, module=calculator.__file__, cwd=os.getcwd())))\n')
    git(repos[1], 'add', 'verify.py'); git(repos[1], 'commit', '-m', 'test client')
    first = await create(app, sid, repos[0], 'api-copy')
    second = await create(app, sid, repos[1], 'client-copy')
    (repos[0] / 'calculator.py').write_text('VALUE = 99\n')
    completed = subprocess.run([sys.executable, 'verify.py'], cwd=second['path'],
        env={**os.environ, 'PYTHONPATH': first['path']}, capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout)
    assert result == {'value': 42, 'module': str(Path(first['path']) / 'calculator.py'), 'cwd': second['path']}
    # This qualifies the explicit paths, not automatic dependency rebinding.
    assert (repos[0] / 'calculator.py').read_text() == 'VALUE = 99\n'


async def test_selection_refuses_symlink_escape_and_foreign_checkouts(multi, tmp_path):
    app, sid, workspace, _ = multi
    other = tmp_path / 'outside'; other.mkdir()
    foreign = repository(other)
    (workspace / 'escape').symlink_to(foreign, target_is_directory=True)
    for path in (foreign, workspace / 'escape', '../outside/repo'):
        with pytest.raises(AppError, match='inside this task'):
            await inspect(app, sid, path)
    assert not app.worktrees.git.records()


async def test_root_above_workspace_cannot_be_selected_implicitly(multi):
    app, sid, workspace, repos = multi
    # Explicit paths may select a subdirectory, but the actual Git root must
    # remain within one of the task's known folders.
    subdir = repos[0] / 'subdir'; subdir.mkdir()
    app._session(sid)['workspace'] = str(subdir)
    with pytest.raises(AppError, match='Git root lies outside'):
        await inspect(app, sid, '.')


@pytest.mark.parametrize('action,args', [
    ('worktree.create', {'sourceRevision': 'fixture'}),
    ('worktree.attach', {'path': 'fixture'}),
    ('worktree.remove', {'id': 'fixture', 'expectedRevision': 1}),
    ('worktree.handoff', {'id': None, 'expectedExecutionRevision': 0}),
    ('worktree.reconcile', {'id': 'fixture', 'expectedRevision': 1, 'destination': 'source', 'evidence': 'Claimed finding'}),
])
async def test_native_child_cannot_mutate_enclosing_root_worktrees(multi, action, args):
    app, sid, _, _ = multi
    before = copy.deepcopy(app._session(sid))
    with pytest.raises(AppError, match='child cannot borrow'):
        await app.app_bridge('dispatch', {'action': action, 'args': {'sessionId': sid, **args},
            '_runtimeSessionId': 'actual-native-child', 'id': action}, sid)
    assert app._session(sid) == before
    assert not app.runtime.quiesce_for_handoff.called


async def test_agent_root_and_associated_checkout_selection(multi):
    app, sid, workspace, repos = multi
    response = await app.app_bridge('dispatch', {'action': 'worktree.inspect',
        'args': {'repositoryPath': 'client/repo'}}, sid)
    assert set(response['state']) == {'revision', 'sessionId', '_stateAccess'}
    source = response['result']['repository']
    made = (await app.app_bridge('dispatch', {'action': 'worktree.create', 'id': 'agent-copy',
        'args': {'repositoryPath': source['root'], 'sourceRevision': source['sourceRevision']}}, sid))['result']
    inspected = await inspect(app, sid, made['path'])
    assert inspected['repository']['root'] == made['path']
    assert inspected['executionDirectory'] == str(workspace)
    assert git(made['path'], 'rev-parse', 'HEAD') == git(repos[1], 'rev-parse', 'HEAD')


@pytest.mark.parametrize('side', ['source', 'target'])
@pytest.mark.parametrize('stage', ['waiting', 'releasing'])
async def test_repository_replaced_during_handoff_await_never_applies(multi, side, stage):
    app, sid, workspace, repos = multi
    source = await create(app, sid, repos[0], 'source-await-copy')
    target = await create(app, sid, repos[0], 'target-await-copy')
    assert (await move(app, sid, source, 'enter-source'))['phase'] == 'applied'
    runtime = app.runtime
    runtime.quiesce_for_handoff.reset_mock()
    runtime.collaboration_input = AsyncMock()
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def boundary(*args, **kwargs):
        if kwargs.get('cancel'):
            return {'cancelled': True}
        if stage == 'waiting':
            entered.set()
            await resume.wait()
        return {'ready': True}

    async def release(*args):
        if stage == 'releasing':
            entered.set()
            await resume.wait()
        return {'quiesced': True, 'inputsReplayed': False}

    runtime.handoff_boundary = AsyncMock(side_effect=boundary)
    runtime.quiesce_for_handoff.side_effect = release
    before = copy.deepcopy(app._session(sid))
    pending = (await app.dispatch('worktree.handoff', {'sessionId': sid,
        'id': target['id'], 'expectedExecutionRevision': 1}, command_id='await-substitution'))['result']
    marker = Path((source if side == 'source' else target)['path']) / '.git'
    original = marker.read_bytes()
    try:
        await asyncio.wait_for(entered.wait(), 10)
        marker.write_text(f'gitdir: {repos[1] / ".git"}\n')
        resume.set()
        await settle(app)
        saved = app.worktrees.read(sid, pending['id'])
        assert saved['phase'] == ('rejected' if stage == 'waiting' else 'unknown')
        assert side in saved['detail'] and 'recorded repository' in saved['detail']
        current = app._session(sid)
        for key in ('workspace', 'workingDirectory', 'executionRevision', 'messages'):
            assert current[key] == before[key]
        assert not saved['inputsReplayed']
        runtime.collaboration_input.assert_not_awaited()
        if stage == 'waiting':
            runtime.quiesce_for_handoff.assert_not_awaited()
            assert not current['configurationBusy']
        else:
            runtime.quiesce_for_handoff.assert_awaited_once()
            assert saved['release']['quiesced']
            assert current['configurationBusy']
    finally:
        resume.set()
        marker.write_bytes(original)
