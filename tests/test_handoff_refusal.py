"""A foreign writer refusal is definite; crossing release remains uncertain."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_foundation.session import SharedSessionStore

from amplifier_web.runtime import RuntimeManager, SessionInUseError
from amplifier_web.service import AppService
from amplifier_web.update_blockers import blockers
from test_worktrees import fixture, settle


async def checkout(app, sid):
    inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
    return (await app.dispatch('worktree.create', {'sessionId': sid,
        'sourceRevision': inspected['repository']['sourceRevision']}, command_id='create'))['result']


@pytest.mark.parametrize('owner_app,known_pid', [
    ('external-fixture', None),
    ('external-fixture', os.getpid()),
    ('amplifier-unified', os.getpid() + 1),
])
async def test_foreign_writer_refusal_preserves_source_without_persistent_busy(tmp_path, monkeypatch, owner_app, known_pid):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    manager = RuntimeManager()
    manager.stop = AsyncMock()
    if known_pid is not None:
        manager.workers[sid] = {'process': SimpleNamespace(pid=known_pid)}
    runtime.quiesce_for_handoff = manager.quiesce_for_handoff
    release = AsyncMock(side_effect=AssertionError('must not release a foreign writer'))
    monkeypatch.setattr('amplifier_foundation.session.request_release', release)
    store = SharedSessionStore(root, sid)
    held = store.acquire(app=owner_app, pid=os.getpid())
    try:
        target = await checkout(app, sid)
        app._session(sid)['messages'] = [{'id': 'retained', 'role': 'user', 'text': 'Saved history'}]
        await app.dispatch('view.update', {'patch': {'draft': 'Unsent draft'}})
        args = {'sessionId': sid, 'id': target['id'], 'expectedExecutionRevision': 0}
        receipt = (await app.dispatch('worktree.handoff', args, command_id='refused'))['result']
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == 'rejected'
        assert saved['inputsReplayed'] is False
        assert 'before runtime release' in saved['detail']
        assert held.active
        release.assert_not_awaited()
        manager.stop.assert_not_awaited()
        current = app._session(sid)
        assert current.get('workingDirectory', str(root)) == str(root)
        assert current.get('executionRevision', 0) == 0
        assert current['messages'] == [{'id': 'retained', 'role': 'user', 'text': 'Saved history'}]
        assert app.state['view']['draft'] == 'Unsent draft'
        assert not current['configurationBusy']
        assert not app.worktrees.execution_state(sid)['fenced']
        assert not any(row['kind'] == 'configuration' for row in blockers(app))
        assert (await app.dispatch('worktree.handoff', args, command_id='refused'))['result']['duplicate']
        await app.close()
        app = AppService(tmp_path / 'app', runtime, workspace=root)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'rejected'
        assert not app._session(sid)['configurationBusy']
        assert not app.worktrees.execution_state(sid)['fenced']
        assert held.active
        release.assert_not_awaited()
        manager.stop.assert_not_awaited()
    finally:
        held.release()
        await app.close()
        await manager.retention.close()


@pytest.mark.parametrize('failure', ['lost-release', 'untyped-in-use', 'second-acquire'])
async def test_release_uncertainty_keeps_original_fence(tmp_path, monkeypatch, failure):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    manager = RuntimeManager()
    manager.stop = AsyncMock()
    manager.workers[sid] = {'process': SimpleNamespace(pid=os.getpid())}
    store = SharedSessionStore(root, sid)
    held = store.acquire(app='amplifier-unified', pid=os.getpid())
    owners = [held]

    async def release(*args, **kwargs):
        if failure == 'second-acquire':
            held.release()
            owners.append(store.acquire(app='racing-writer'))
            return SimpleNamespace(status='released')
        if failure == 'untyped-in-use':
            raise SessionInUseError(held.owner)
        raise RuntimeError('Lost release acknowledgment')

    release_request = AsyncMock(side_effect=release)
    monkeypatch.setattr('amplifier_foundation.session.request_release', release_request)
    runtime.quiesce_for_handoff = manager.quiesce_for_handoff
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='uncertain'))['result']
        await settle(app)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'unknown'
        assert app._session(sid)['configurationBusy']
        assert app.worktrees.execution_state(sid)['fenced']
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        assert app._session(sid).get('executionRevision', 0) == 0
        release_request.assert_awaited_once()
        manager.stop.assert_not_awaited()
        await app.close()
        app = AppService(tmp_path / 'app', runtime, workspace=root)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'unknown'
        assert app._session(sid)['configurationBusy']
        assert app.worktrees.execution_state(sid)['fenced']
        release_request.assert_awaited_once()
    finally:
        for owner in owners:
            if owner.active: owner.release()
        await app.close()
        await manager.retention.close()


async def test_reconcile_refusal_does_not_clear_prior_unknown_receipt(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    manager = RuntimeManager()
    manager.stop = AsyncMock()
    store = SharedSessionStore(root, sid)
    held = None
    try:
        target = await checkout(app, sid)
        runtime.quiesce_for_handoff.side_effect = RuntimeError('Prior unknown outcome')
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='prior'))['result']
        await settle(app)
        previous = app.worktrees.read(sid, receipt['id'])
        held = store.acquire(app='external-fixture')
        runtime.quiesce_for_handoff = manager.quiesce_for_handoff
        refused = (await app.dispatch('worktree.reconcile', {'sessionId': sid,
            'id': previous['id'], 'expectedRevision': previous['revision'],
            'destination': 'source', 'evidence': 'Inspected source; another writer still owns it.'},
            command_id='refused-reconcile'))['result']
        await settle(app)
        assert app.worktrees.read(sid, refused['id'])['phase'] == 'rejected'
        assert app.worktrees.read(sid, previous['id']) == previous
        assert app._session(sid)['configurationBusy']
        assert app.worktrees.execution_state(sid)['fenced']
        assert held.active
        manager.stop.assert_not_awaited()
    finally:
        if held is not None: held.release()
        await app.close()
        await manager.retention.close()
