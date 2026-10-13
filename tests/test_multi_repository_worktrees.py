"""Explicit repository selection without relocating a multi-repository chat."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_worktrees.git import git
from test_worktrees import repository


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
