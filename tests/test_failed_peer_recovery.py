"""A fresh human-directed reconciliation can recover a settled failed member."""
import copy
from types import SimpleNamespace

import pytest
from amplifier_foundation.session import SharedSessionStore, SessionBusyError
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError
from test_peer_resume import app, paused_member, submit
from test_collaborative_workspaces import settled


async def failed_member(app):
    source, target, human = await paused_member(app)
    target['status'] = 'error'
    target['failure'] = {'generationId': 'failed-turn', 'category': 'unknown',
                         'errorType': 'ValueError', 'recordedAt': human['createdAt'] - 1,
                         'effects': 'not_rolled_back'}
    target['collaborationGeneration'] = {'id': 'failed-turn', 'terminal': True, 'completed': False}
    runtime = RuntimeManager()
    app.runtime.stopped_boundary = runtime.stopped_boundary
    return source, target, human, runtime


async def test_failed_member_gets_one_fresh_input_without_replaying_failure(app, monkeypatch):
    source, target, human, _ = await failed_member(app)
    failure = copy.deepcopy(target['failure'])
    assignment = copy.deepcopy(app.assignments.current(source))
    publish = app.collaboration.publish_receipt
    def check_lock(receipt):
        with pytest.raises(SessionBusyError):
            SharedSessionStore(target['workspace'], target['id']).acquire(app='other-owner')
        return publish(receipt)
    with monkeypatch.context() as patch:
        patch.setattr(app.collaboration, 'publish_receipt', check_lock)
        await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    assert len(app.runtime.inputs) == 1
    assert receipt['humanResume']['failedRecovery']['workerAbsent']
    assert receipt['humanResume']['failedRecovery']['failure'] == failure
    assert target['failure'] == failure
    assert app.assignments.current(source) == assignment
    await submit(app, source, target, human)
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('block', ['native_owner', 'live_process', 'child', 'unterminated', 'missing_failure', 'safety_stop'])
async def test_failed_resume_requires_settlement_and_never_clears_safety_stop(app, block):
    source, target, human, runtime = await failed_member(app)
    held = None
    if block == 'native_owner':
        held = SharedSessionStore(target['workspace'], target['id']).acquire(app='external')
    elif block == 'live_process':
        runtime.workers[target['id']] = {'process': SimpleNamespace(returncode=None)}
    elif block == 'child': target['workers'] = [{'status': 'running'}]
    elif block == 'unterminated': target['collaborationGeneration']['terminal'] = False
    elif block == 'missing_failure': target.pop('failure')
    else: target['failure']['category'] = 'content_filter'
    try:
        with pytest.raises(AppError):
            await submit(app, source, target, human)
        assert not app.runtime.inputs
        assert not app.db.execute("SELECT 1 FROM commands WHERE id='resume-member'").fetchone()
    finally:
        if held: held.release()


async def test_new_failure_before_admission_invalidates_recovery(app):
    source, target, human, _ = await failed_member(app)
    async def delayed(session, args, guard, emit):
        target['failure']['generationId'] = 'new-failure'
        assert guard()
        return {'accepted': False, 'reason': guard()}
    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'suppressed')
    assert 'failure changed' in receipt['admission']['reason'].lower()
    assert not app.runtime.inputs
