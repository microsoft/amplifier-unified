"""A durable receipt is not proof that its owner's placement transaction committed."""
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_handoff_refusal import checkout
from test_worktrees import fixture, settle

from amplifier_web.service import AppError, AppService


async def completed(app, sid):
    target = await checkout(app, sid)
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
        'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
    await settle(app)
    return target, app.worktrees.read(sid, receipt['id'])


@pytest.mark.parametrize('phase', ['pending', 'applied'])
@pytest.mark.parametrize('legacy', [False, True])
async def test_recovery_preflight_failure_retains_fence_and_explicit_reconciliation(tmp_path, monkeypatch, phase, legacy):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    target, record = await completed(app, sid)
    record.update(phase=phase, step='released')
    if legacy:
        record.pop('placementVersion')
    # Persist the exact crash window: receipt advanced, owner still at source.
    app.worktrees.save(record)
    session = app._session(sid)
    session.update(workingDirectory=str(root), executionRevision=0)
    session.pop('executionHandoffId', None)
    app.worktrees.changed()
    await app.close()
    Path(target['path']).rename(tmp_path / 'retained-checkout')
    runtime.quiesce_for_handoff.reset_mock()
    runtime.collaboration_input = AsyncMock()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        # The recovery fence must exist before the asynchronous retry begins.
        assert app.worktrees.read(sid, record['id'])['phase'] == 'pending'
        assert app.worktrees.execution_state(sid)['fenced']
        app.worktrees.start()
        await settle(app)
        saved = app.worktrees.read(sid, record['id'])
        assert saved['phase'] == 'unknown'
        assert 'target folder is unavailable' in saved['detail']
        assert app.worktrees.execution_state(sid)['fenced']
        assert app._session(sid)['configurationBusy']
        owner = app.worktrees.saved_owner(sid, durable=True)
        assert owner['workingDirectory'] == str(root) and owner['executionRevision'] == 0
        runtime.quiesce_for_handoff.assert_not_awaited()
        runtime.collaboration_input.assert_not_awaited()
        with pytest.raises(AppError, match=r'\AApplying or reconciling conversation settings; new input must wait\.\Z') as blocked:
            await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'must remain fenced'})
        assert blocked.value.status == 409
        response = await app.dispatch('worktree.reconcile', {'sessionId': sid,
            'id': saved['id'], 'expectedRevision': saved['revision'],
            'destination': 'source', 'evidence': 'The original folder is intact; keep it.'}, command_id='reconcile')
        await settle(app)
        assert app.worktrees.read(sid, response['result']['id'])['phase'] == 'applied'
        assert app.worktrees.read(sid, saved['id'])['phase'] == 'reconciled'
        assert not app.worktrees.execution_state(sid)['fenced']
        assert not app._session(sid)['configurationBusy']
        assert app._session(sid)['messages'] == []
        assert (tmp_path / 'retained-checkout/a.txt').read_text() == 'original\n'
    finally:
        await app.close()


@pytest.mark.parametrize('failure', ['before-placement-commit', 'after-placement-commit', 'notification'])
async def test_only_committed_placement_survives_later_delivery_failure(tmp_path, monkeypatch, failure):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    changed = app.worktrees.changed
    failed = []
    runtime.collaboration_input = AsyncMock()
    def publish(*args, **kwargs):
        current = app._session(sid)
        if failure != 'notification' and current.get('workingDirectory') == target['path'] and not failed:
            failed.append(True)
            # The resident target is not yet a saved placement proof.
            owner = app.worktrees.saved_owner(sid, durable=True)
            assert (owner.get('workingDirectory') or owner['workspace']) == str(root)
            assert owner.get('executionRevision', 0) == 0
            if failure == 'after-placement-commit':
                changed(*args, **kwargs)
            raise OSError('controlled publication failure')
        return changed(*args, **kwargs)
    monkeypatch.setattr(app.worktrees, 'changed', publish)
    if failure == 'notification':
        def notification(_record):
            raise OSError('controlled notification failure')
        monkeypatch.setattr(app.worktrees, 'enqueue_continuation', notification)
    try:
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        owner = app.worktrees.saved_owner(sid, durable=True)
        if failure == 'before-placement-commit':
            assert saved['phase'] == 'unknown'
            assert app.worktrees.execution_state(sid)['fenced']
            assert (owner.get('workingDirectory') or owner['workspace']) == str(root)
            assert owner.get('executionRevision', 0) == 0
            assert not owner.get('executionHandoffId')
            assert (app._session(sid).get('workingDirectory') or app._session(sid)['workspace']) == str(root)
        else:
            assert saved['phase'] == 'applied' and saved['continuation']['status'] == 'waiting'
            assert owner['workingDirectory'] == target['path'] and owner['executionRevision'] == 1
            assert owner['executionHandoffId'] == saved['id']
            assert not app.worktrees.execution_state(sid)['fenced']
            assert not app._session(sid)['configurationBusy']
        assert app._session(sid)['messages'] == []
        runtime.collaboration_input.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('change', ['handoff-id', 'canonical-home', 'execution-host'])
async def test_matching_path_and_revision_do_not_prove_unrelated_placement(tmp_path, monkeypatch, change):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    target, record = await completed(app, sid)
    session = app._session(sid)
    if change == 'handoff-id':
        session['executionHandoffId'] = str(uuid.uuid4())
    elif change == 'canonical-home':
        record['historyHome'] = str(tmp_path / 'different-home')
        app.worktrees.save(record)
    else:
        session['executionHost'] = {**session['executionHost'], 'id': 'different-host'}
    app.worktrees.changed()
    await app.close()
    runtime.quiesce_for_handoff.reset_mock()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app._session(sid)['workingDirectory'] == target['path']
        assert app._session(sid)['executionRevision'] == record['executionRevision'] + 1
        assert app.worktrees.read(sid, record['id'])['phase'] == 'unknown'
        assert app.worktrees.execution_state(sid)['fenced']
        assert app.worktrees.recoverable == []
        runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        await app.close()


async def test_failed_reconciliation_publication_keeps_original_unknown_receipt(tmp_path, monkeypatch):
    app, _runtime, _root, sid = await fixture(tmp_path, monkeypatch)
    target, record = await completed(app, sid)
    record.update(phase='unknown')
    app.worktrees.save(record)
    app.worktrees.changed()
    changed = app.worktrees.changed
    failed = []
    def publish(*args, **kwargs):
        if app._session(sid).get('executionRevision') == 2 and not failed:
            failed.append(True)
            raise OSError('controlled reconciliation publication failure')
        return changed(*args, **kwargs)
    monkeypatch.setattr(app.worktrees, 'changed', publish)
    try:
        response = await app.dispatch('worktree.reconcile', {'sessionId': sid,
            'id': record['id'], 'expectedRevision': record['revision'],
            'destination': 'source', 'evidence': 'Keep the original folder.'}, command_id='reconcile')
        await settle(app)
        assert app.worktrees.read(sid, response['result']['id'])['phase'] == 'unknown'
        assert app.worktrees.read(sid, record['id'])['phase'] == 'unknown'
        assert app.worktrees.saved_owner(sid, durable=True)['workingDirectory'] == target['path']
        assert app.worktrees.execution_state(sid)['fenced']
    finally:
        await app.close()


@pytest.mark.parametrize('error', [OSError, sqlite3.OperationalError])
@pytest.mark.parametrize('stage', ['before-commit', 'after-commit'])
@pytest.mark.parametrize('recovery', ['publication', 'reconciliation'])
async def test_unreadable_owner_never_publishes_guessed_placement(tmp_path, monkeypatch, error, stage, recovery):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    other = app._new_session({'title': 'Independent owner'})
    app._state['sessions'].append(other)
    app._save()
    target = await checkout(app, sid)
    changed, saved_owner = app.worktrees.changed, app.worktrees.saved_owner
    unreadable = []
    publications = []
    def read_owner(identity, *, durable=False):
        if unreadable and durable and identity == sid:
            raise error('controlled committed-owner read failure')
        return saved_owner(identity, durable=durable)
    def publish(*args, **kwargs):
        publications.append(app._session(sid).get('workingDirectory'))
        if app._session(sid).get('workingDirectory') == target['path']:
            if stage == 'after-commit':
                changed(*args, **kwargs)
            unreadable.append(True)
            raise OSError('controlled owner publication failure')
        changed(*args, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(app.worktrees, 'saved_owner', read_owner)
            patch.setattr(app.worktrees, 'changed', publish)
            receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
                'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
            await settle(app)
            assert app.worktrees.read(sid, receipt['id'])['phase'] == 'unknown'
            assert app.worktrees.execution_state(sid)['fenced']
            assert publications.count(target['path']) == 1
            with pytest.raises(error, match='owner read failure'):
                app._publish_changes(sessions={sid})
            with pytest.raises(error, match='owner read failure'):
                app._save_full(reason='fixture unavailable placement checkpoint')
            # A failed publish retains its dirty scope; ordinary publication of
            # another owner must not smuggle the unverified one through retry.
            with pytest.raises(error, match='owner read failure'):
                app._publish_changes(sessions={other['id']})
            # The storage guard only visits the affected save scope.
            other['title'] = 'Independent publication'
            app._save_changes(sessions={other['id']})
            assert saved_owner(other['id'], durable=True)['title'] == 'Independent publication'
        expected = str(root) if stage == 'before-commit' else target['path']
        expected_revision = 0 if stage == 'before-commit' else 1
        owner = saved_owner(sid, durable=True)
        assert (owner.get('workingDirectory') or owner['workspace']) == expected
        assert owner.get('executionRevision', 0) == expected_revision
        if recovery == 'publication':
            # Ordinary publication resolves from storage before serialization.
            app._publish_changes(sessions={sid})
            expected_phase = 'unknown'
        else:
            # Explicit recovery must resolve storage before capturing its source,
            # even when no ordinary publication has happened since the failure.
            original = app.worktrees.read(sid, receipt['id'])
            response = await app.dispatch('worktree.reconcile', {'sessionId': sid,
                'id': original['id'], 'expectedRevision': original['revision'],
                'destination': 'source', 'evidence': 'Keep the original folder.'}, command_id='reconcile')
            assert response['result']['source'] == expected
            assert response['result']['executionRevision'] == expected_revision
            await settle(app)
            assert app.worktrees.read(sid, response['result']['id'])['phase'] == 'applied'
            expected, expected_revision, expected_phase = str(root), expected_revision + 1, 'reconciled'
        assert (app._session(sid).get('workingDirectory') or app._session(sid)['workspace']) == expected
        assert not app.worktrees._unverified_placements
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == expected_phase
        assert app.worktrees.execution_state(sid)['fenced'] is (recovery == 'publication')
        assert runtime.quiesce_for_handoff.await_count == (1 if recovery == 'publication' else 2)
        await app.close()
        app = AppService(tmp_path / 'app', runtime, workspace=root)
        owner = app.worktrees.saved_owner(sid, durable=True)
        assert (owner.get('workingDirectory') or owner['workspace']) == expected
        assert owner.get('executionRevision', 0) == expected_revision
        assert app.worktrees.execution_state(sid)['fenced'] is (recovery == 'publication')
        assert app.worktrees.read(sid, receipt['id'])['phase'] == expected_phase
    finally:
        await app.close()


async def test_committed_reconciliation_recovers_prior_receipt_without_requested_continuation(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    _, original = await completed(app, sid)
    original['phase'] = 'unknown'
    app.worktrees.save(original)
    app.worktrees.changed()
    reconcile_prior = app.worktrees.reconcile_prior
    def fail_after_placement(_record):
        raise OSError('controlled interruption before prior receipt completion')
    monkeypatch.setattr(app.worktrees, 'reconcile_prior', fail_after_placement)
    response = await app.dispatch('worktree.reconcile', {'sessionId': sid,
        'id': original['id'], 'expectedRevision': original['revision'],
        'destination': 'source', 'evidence': 'Keep the original folder.'}, command_id='reconcile')
    await settle(app)
    receipt = app.worktrees.read(sid, response['result']['id'])
    assert receipt['phase'] == 'applied' and not receipt['resumeRequested']
    assert app.worktrees.saved_owner(sid, durable=True)['workingDirectory'] == str(root)
    assert app.worktrees.read(sid, original['id'])['phase'] == 'unknown'
    monkeypatch.setattr(app.worktrees, 'reconcile_prior', reconcile_prior)
    await app.close()
    runtime.quiesce_for_handoff.reset_mock()
    runtime.collaboration_input = AsyncMock()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app.worktrees.recoverable == [receipt['id']]
        app.worktrees.start()
        await settle(app)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'applied'
        assert app.worktrees.read(sid, original['id'])['phase'] == 'reconciled'
        assert not app.worktrees.execution_state(sid)['fenced']
        assert not app._session(sid)['configurationBusy']
        runtime.quiesce_for_handoff.assert_not_awaited()
        runtime.collaboration_input.assert_not_awaited()
        assert app._session(sid)['messages'] == []
    finally:
        await app.close()


async def test_legacy_completed_receipt_retains_exact_saved_placement_without_new_identity(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    target, receipt = await completed(app, sid)
    receipt.pop('placementVersion')
    app.worktrees.save(receipt)
    app._session(sid).pop('executionHandoffId')
    app.worktrees.changed()
    await app.close()
    runtime.quiesce_for_handoff.reset_mock()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'applied'
        assert app.worktrees.saved_owner(sid, durable=True)['workingDirectory'] == target['path']
        assert app.worktrees.recoverable == []
        assert not app.worktrees.execution_state(sid)['fenced']
        runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('legacy', [False, True])
async def test_native_index_persists_exact_placement_identity(tmp_path, monkeypatch, legacy):
    app, _, _, sid = await fixture(tmp_path, monkeypatch)
    target, receipt = await completed(app, sid)
    try:
        app._session(sid)['historyManaged'] = True
        app.worktrees.changed()
        if legacy:
            receipt.pop('placementVersion')
            app.worktrees.save(receipt)
            app.db.execute("UPDATE state_records SET value=json_remove(value, '$.workingDirectory', "
                "'$.executionRevision', '$.executionHost', '$.executionHandoffId') WHERE kind='session' AND id=?", (sid,))
            app.db.execute("UPDATE history_local_rows SET summary=json_remove(summary, '$.executionHandoffId') WHERE id=?", (sid,))
            app.db.commit()
        owner = app.worktrees.saved_owner(sid, durable=True)
        assert owner['workingDirectory'] == target['path']
        if legacy:
            assert 'executionHandoffId' not in owner
        else:
            assert owner['executionHandoffId'] == receipt['id']
        assert app.worktrees.placement_committed(receipt, owner)
    finally:
        await app.close()
