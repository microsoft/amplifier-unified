import asyncio
import copy
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_worktrees.git import git
from amplifier_web.service import AppService, AppError


def repository(tmp_path):
    root = tmp_path / 'repo'; root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'a.txt').write_text('original\n')
    (root / '.gitignore').write_text('ignored.txt\n')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'base')
    return root


def create(manager, source, **kwargs):
    return manager.create(source, command_id=kwargs.pop('command_id', 'create'), expected_revision=manager.inspect(source)['sourceRevision'], **kwargs)


async def fixture(tmp_path, monkeypatch):
    root = repository(tmp_path)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    runtime = SimpleNamespace(quiesce_for_handoff=AsyncMock(return_value={'quiesced': True, 'inputsReplayed': False}), close=AsyncMock())
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    await app.dispatch('session.create', {})
    return app, runtime, root, app._session()['id']


async def settle(app):
    if app.worktrees.jobs: await asyncio.gather(*list(app.worktrees.jobs))


async def test_ui_agent_handoff_preserves_task_voice_artifacts_drafts_and_home(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        app._session(sid).update(messages=[{'id': 'spoken', 'role': 'user', 'text': 'Voice history', 'via': 'call', 'voiceId': 'voice-original'}], task={'id': 'goal', 'revision': 4}, artifactRefs=['artifact-original'])
        retained = copy.deepcopy(app._session(sid))
        await app.dispatch('view.update', {'patch': {'draft': 'Keep this unsent draft'}})
        inspected = (await app.app_bridge('dispatch', {'action': 'worktree.inspect', 'args': {}}, sid))['result']
        created = (await app.dispatch('worktree.create', {'sessionId': sid, 'sourceRevision': inspected['repository']['sourceRevision']}, command_id='create'))['result']
        command = {'sessionId': sid, 'id': created['id'], 'expectedExecutionRevision': 0}
        pending = (await app.app_bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move', 'args': command}, sid))['result']
        assert pending['phase'] == 'pending'
        await settle(app)
        session = app._session(sid)
        assert session['workspace'] == str(root) and session['workingDirectory'] == created['path']
        for key in ['messages', 'task', 'artifactRefs']: assert session[key] == retained[key]
        assert app.state['view']['draft'] == 'Keep this unsent draft'
        assert app.state['selectedSessionId'] == sid and runtime.quiesce_for_handoff.await_count == 1
        duplicate = await app.app_bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move', 'args': command}, sid)
        assert duplicate['result']['duplicate'] and runtime.quiesce_for_handoff.await_count == 1
        with pytest.raises(AppError, match='still uses'): await app.dispatch('worktree.remove', {'sessionId': sid, 'id': created['id'], 'expectedRevision': created['revision']})
        with pytest.raises(AppError, match='calling task'): await app.app_bridge('dispatch', {'action': 'worktree.list', 'args': {'sessionId': 'wrong'}}, sid)
        await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': None, 'expectedExecutionRevision': 1}, command_id='back')
        await settle(app)
        assert session['workingDirectory'] == str(root) and session['executionRevision'] == 2
        await app.dispatch('worktree.remove', {'sessionId': sid, 'id': created['id'], 'expectedRevision': created['revision']}, command_id='remove')
    finally: await app.close()


@pytest.mark.parametrize('origin', ['ui', 'agent'])
@pytest.mark.parametrize('absolute', [False, True])
async def test_canvas_file_capture_follows_execution_while_files_keep_workspace(tmp_path, monkeypatch, origin, absolute):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        workspace_id = app.state['selectedWorkspaceId']
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        created = (await app.dispatch('worktree.create', {
            'sessionId': sid, 'sourceRevision': inspected['repository']['sourceRevision']}))['result']
        await app.dispatch('worktree.handoff', {
            'sessionId': sid, 'id': created['id'], 'expectedExecutionRevision': 0})
        await settle(app)
        (root / 'report.md').write_text('# Original workspace')
        execution_file = Path(created['path']) / 'report.md'
        execution_file.write_text('# Current execution')
        await app.dispatch('view.update', {'patch': {'draft': 'Keep this draft'}})
        args = {'sessionId': sid, 'kind': 'markdown',
                'path': str(execution_file) if absolute else 'report.md'}
        if origin == 'agent':
            await app.app_bridge('dispatch', {'action': 'canvas.show', 'args': args}, sid)
        else:
            await app.dispatch('canvas.show', args)
        assert app.state['canvas']['content'] == '# Current execution'
        assert app.state['canvas']['sessionId'] == sid
        assert app.state['canvas']['workspaceId'] == workspace_id
        assert app._session(sid)['workspace'] == str(root)
        assert app.state['view']['draft'] == 'Keep this draft'
        # The explicit workspace Files route still previews the original folder.
        opened = await app.dispatch('canvas.openFile', {
            'sessionId': sid, 'workspace': str(root), 'path': 'report.md'})
        assert opened['result']['status'] == 'opened'
        assert app.state['canvas']['content'] == '# Original workspace'
        with pytest.raises(AppError, match='inside the selected workspace'):
            await app.dispatch('canvas.show', {**args, 'path': str(root / 'report.md')})
        assert app.state['canvas']['content'] == '# Original workspace'
    finally:
        await app.close()


async def test_unknown_restart_and_explicit_reconcile_never_resubmit(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    runtime.quiesce_for_handoff.side_effect = RuntimeError('lost confirmation')
    inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
    created = (await app.dispatch('worktree.create', {'sessionId': sid, 'sourceRevision': inspected['repository']['sourceRevision']}, command_id='create'))['result']
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': created['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
    await settle(app); await app.close()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        assert app._session(sid)['configurationBusy']
        assert runtime.quiesce_for_handoff.await_count == 1
        unknown = app.worktrees.read(sid, receipt['id'])
        assert unknown['phase'] == 'unknown'
        with pytest.raises(AppError, match='actual finding'):
            await app.app_bridge('dispatch', {'action': 'worktree.reconcile', 'args': {'id': unknown['id'], 'expectedRevision': unknown['revision'], 'destination': 'source', 'evidence': 'Agent guess'}}, sid)
        runtime.quiesce_for_handoff.side_effect = None
        await app.dispatch('worktree.reconcile', {'sessionId': sid, 'id': unknown['id'], 'expectedRevision': unknown['revision'], 'destination': 'source', 'evidence': 'Inspected the original workspace; retain it.'}, command_id='reconcile')
        await settle(app)
        assert not app._session(sid)['configurationBusy']
        assert app._session(sid)['workingDirectory'] == str(root)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'reconciled'
    finally: await app.close()


async def test_runtime_cooperative_release_is_confirmed_without_replay(tmp_path, monkeypatch):
    from amplifier_foundation.session import SharedSessionStore, register_release_handler, ReadyToRelease
    from amplifier_web.runtime import RuntimeManager
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'locks'))
    store = SharedSessionStore(tmp_path, 's')
    held = store.acquire(app='amplifier-unified', pid=os.getpid())
    saved = []
    async def release(request):
        saved.append('checkpoint')
        return ReadyToRelease()
    registration = await register_release_handler(held, prepare_release=release)
    manager = RuntimeManager()
    manager.workers['s'] = {'process': SimpleNamespace(pid=os.getpid())}
    manager.stop = AsyncMock()
    try:
        result = await manager.quiesce_for_handoff({'id':'s','workspace':str(tmp_path)}, 'move')
        assert result['quiesced'] and not result['inputsReplayed']
        from amplifier_web.host_identity import local_host_identity
        assert result['executionHost'] == local_host_identity()
        assert result['releaseOwner']['hostname'] == result['executionHost']['id']
        assert result['releaseOwner']['app'] == 'amplifier-unified-handoff'
        assert result['releaseOwner']['acquisition_id']
        assert 'user' not in result['releaseOwner']
        assert result['releasedOwner']['app'] == 'amplifier-unified'
        assert result['releasedOwner']['acquisition_id'] != result['releaseOwner']['acquisition_id']
        assert saved == ['checkpoint'] and not held.active
        manager.stop.assert_awaited_once_with('s')
        next_owner = store.acquire(app='next'); next_owner.release()
    finally:
        await registration.close()
        if held.active: held.release()
        await manager.retention.close()


async def test_restart_detects_commit_receipt_without_execution_view(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    record = {'id': str(__import__('uuid').uuid4()), 'sessionId': sid, 'revision': 2, 'phase': 'applied', 'source': str(root), 'target': str(root / 'missing'), 'historyHome': str(root), 'executionRevision': 0, 'createdAt': 1}
    app.worktrees.save(record)
    await app.close()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app.worktrees.read(sid, record['id'])['phase'] == 'unknown'
        assert app._session(sid)['configurationBusy']
        assert not runtime.quiesce_for_handoff.await_count
    finally: await app.close()


async def test_pending_handoff_cannot_overlap_or_delete_its_target(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    release = asyncio.Event()
    async def delayed(*_):
        await release.wait(); return {'quiesced': True}
    runtime.quiesce_for_handoff.side_effect = delayed
    try:
        revision = app.worktrees.git.inspect(root)['sourceRevision']
        record = (await app.dispatch('worktree.create', {'sessionId': sid, 'sourceRevision': revision}, command_id='create'))['result']
        args = {'sessionId': sid, 'id': record['id'], 'expectedExecutionRevision': 0}
        await app.dispatch('worktree.handoff', args, command_id='move')
        with pytest.raises(AppError, match='pending'): await app.dispatch('worktree.handoff', args, command_id='overlap')
        with pytest.raises(AppError, match='unresolved handoff'):
            await app.dispatch('worktree.remove', {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']})
        release.set(); await settle(app)
        assert app._session(sid)['executionRevision'] == 1
    finally:
        release.set(); await app.close()


async def test_local_host_association_survives_restart_and_foreign_target_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_web.host_identity.socket.gethostname', lambda: 'fixture-owner-host')
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        inspected = (await app.dispatch('worktree.inspect', {'sessionId':sid}))['result']
        host = inspected['executionHost']
        assert host['id'] == 'fixture-owner-host' and host['identitySource'] == 'foundation-owner-hostname'
        created = (await app.dispatch('worktree.create', {'sessionId':sid,'sourceRevision':inspected['repository']['sourceRevision']}, command_id='host-create'))['result']
        attached = (await app.dispatch('worktree.attach', {'sessionId':sid,'path':str(root)}, command_id='host-attach'))['result']
        assert created['executionHost'] == attached['executionHost'] == host
        receipt = (await app.dispatch('worktree.handoff', {'sessionId':sid,'id':created['id'],'expectedExecutionRevision':0}, command_id='host-move'))['result']
        await settle(app)
        assert receipt['executionHost'] == host and app._session(sid)['executionHost'] == host
    finally:
        await app.close()
    app = AppService(tmp_path/'app', runtime, workspace=root)
    try:
        assert app.worktrees.git.get(created['id'])['executionHost'] == host
        assert app.worktrees.read(sid, receipt['id'])['executionHost'] == host
        assert runtime.quiesce_for_handoff.await_count == 1
        monkeypatch.setattr('amplifier_web.host_identity.socket.gethostname', lambda: 'different-host')
        # Inspection remains available, but a hostname change cannot become an
        # implicit cross-host migration or release the wrong local owner.
        info = (await app.dispatch('worktree.inspect', {'sessionId':sid}))['result']
        assert info['currentHost']['id'] == 'different-host' and info['executionHost'] == host
        assert app.worktrees.execution_state(sid)['hostMatches'] is False
        from amplifier_web.runtime import RuntimeManager
        admission = RuntimeManager()
        admission.bind_execution_state(app.worktrees.execution_state)
        with pytest.raises(ValueError, match='saved execution host'):
            admission._check_execution(sid)
        admission._check_execution(sid, allow_fenced=True)
        await admission.retention.close()
        with pytest.raises(AppError, match='different local host'):
            await app.dispatch('worktree.handoff', {'sessionId':sid,'id':None,'expectedExecutionRevision':1}, command_id='foreign-move')
        assert runtime.quiesce_for_handoff.await_count == 1
    finally:
        await app.close()
