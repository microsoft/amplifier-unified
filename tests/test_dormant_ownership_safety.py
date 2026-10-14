"""Nonresident history cannot remove placement or transfer ownership guards."""
import copy
from pathlib import Path
import sqlite3
import uuid

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.state_records import load_owner
from test_worktrees import create, fixture


@pytest.mark.parametrize('action', ['worktree.archive', 'worktree.remove'])
@pytest.mark.parametrize('placement', ['execution', 'workspace'])
async def test_dormant_owner_still_blocks_checkout_retirement(tmp_path, monkeypatch, action, placement):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    record = create(app.worktrees.git, root, session_id=sid)
    alias = tmp_path / 'checkout-alias'
    alias.symlink_to(record['path'], target_is_directory=True)
    dormant = app._new_session({'title': 'Dormant owner'})
    dormant['workingDirectory' if placement == 'execution' else 'workspace'] = str(alias)
    dormant['messages'] = [{'id': 'retained', 'role': 'user', 'text': 'Original history'}]
    dormant_id = dormant['id']
    app._state['sessions'].append(dormant)
    app._save()
    await app.close()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert dormant_id not in {row['id'] for row in app._state['sessions']}
        before = copy.deepcopy(load_owner(app.db, dormant_id))
        with pytest.raises(AppError, match='still uses'):
            await app.dispatch(action, {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']})
        assert Path(record['path']).is_dir()
        assert app.worktrees.git.get(record['id'])['status'] == 'ready'
        assert dormant_id not in {row['id'] for row in app._state['sessions']}
        assert load_owner(app.db, dormant_id) == before
        assert not app.worktrees._moving_paths
        runtime.quiesce_for_handoff.assert_not_called()
    finally:
        await app.close()


async def test_dormant_applied_handoff_survives_restart_without_admission(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    dormant = app._new_session({'title': 'Completed handoff'})
    dormant_id = dormant['id']
    target = tmp_path / 'execution'; target.mkdir()
    dormant.update(workingDirectory=str(target), executionRevision=1)
    app._state['sessions'].append(dormant)
    record = {'id': str(uuid.uuid4()), 'sessionId': dormant_id, 'revision': 2,
              'phase': 'applied', 'source': str(root), 'target': str(target),
              'historyHome': str(root), 'executionRevision': 0, 'createdAt': 1}
    app.worktrees.save(record)
    app._save()
    await app.close()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app.worktrees.read(dormant_id, record['id']) == record
        assert dormant_id not in {row['id'] for row in app._state['sessions']}
        assert app.worktrees.saved_owner(dormant_id)['executionRevision'] == 1
        runtime.quiesce_for_handoff.assert_not_called()
    finally:
        await app.close()


async def test_dormant_unresolved_handoff_blocks_retirement(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        record = create(app.worktrees.git, root, session_id=sid)
        # The durable authority can predate any admitted projection.
        receipt = {'id': str(uuid.uuid4()), 'sessionId': 'dormant-owner', 'revision': 2,
                   'phase': 'unknown', 'source': str(root), 'target': record['path'],
                   'historyHome': str(root), 'executionRevision': 0, 'createdAt': 1}
        app.worktrees.save(receipt)
        with pytest.raises(AppError, match='unresolved handoff'):
            await app.dispatch('worktree.archive', {'sessionId': sid, 'id': record['id'],
                                                   'expectedRevision': record['revision']})
        assert Path(record['path']).is_dir()
        assert app.worktrees.read('dormant-owner', receipt['id']) == receipt
    finally:
        await app.close()


async def test_legacy_or_missing_dormant_placement_never_permits_retirement(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    record = create(app.worktrees.git, root, session_id=sid)
    dormant = app._new_session({'title': 'Older saved placement'})
    dormant.update(workingDirectory=record['path'], executionRevision=1)
    dormant_id = dormant['id']
    app._state['sessions'].append(dormant)
    receipt = {'id': str(uuid.uuid4()), 'sessionId': dormant_id, 'revision': 2,
               'phase': 'applied', 'source': str(root), 'target': record['path'],
               'historyHome': str(root), 'executionRevision': 0, 'createdAt': 1}
    app.worktrees.save(receipt)
    app._save()
    await app.close()
    with sqlite3.connect(tmp_path / 'app' / 'app.sqlite3') as db:
        db.execute("UPDATE history_local_rows SET summary=json_remove(summary,'$.workingDirectory','$.executionRevision') WHERE id=?", (dormant_id,))
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app.worktrees.read(dormant_id, receipt['id']) == receipt
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        with pytest.raises(AppError, match='still uses'):
            await app.dispatch('worktree.archive', args)
        assert dormant_id not in {row['id'] for row in app._state['sessions']}
        # Losing the compact record is uncertainty, never permission to move
        # files while a durable directory owner still claims the checkout.
        app.db.execute("DELETE FROM state_records WHERE kind='session' AND id=?", (dormant_id,))
        app.db.commit()
        with pytest.raises(AppError, match='placement is unavailable'):
            await app.dispatch('worktree.remove', args)
        assert Path(record['path']).is_dir() and not app.worktrees._moving_paths
        assert app.worktrees.git.get(record['id'])['status'] == 'ready'
    finally:
        await app.close()


async def test_transfer_staging_rejects_dormant_writable_owner(tmp_path, monkeypatch):
    from test_portability import setup_hosts, host_env, export

    app, target, source, destination, root, destrepo, sid, _, _ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        receipt, _ = await export(app, target, sid, root)
        host_env(monkeypatch, destination)
        owner = target._new_session({'title': 'Existing local task'})
        owner.update(id=sid, messages=[{'id': 'local', 'role': 'user', 'text': 'Retain this destination work'}])
        target._state['sessions'].append(owner)
        target._save()
        before = copy.deepcopy(load_owner(target.db, sid))
        await target.close()
        target = AppService(destination / 'app', workspace=destrepo)
        assert sid not in {row['id'] for row in target._state['sessions']}
        assert not list((destination / 'native' / 'projects').glob('*/sessions/' + sid + '/transcript.jsonl'))
        with pytest.raises(AppError, match='already has a writable task'):
            await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)})
        assert sid not in {row['id'] for row in target._state['sessions']}
        assert load_owner(target.db, sid) == before
        assert not target.portability.node.records()
    finally:
        await target.close()
        await app.close()
