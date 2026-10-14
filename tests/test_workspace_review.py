"""Review derives evidence without turning discovery into resumed execution."""
import copy
import json

import pytest

from amplifier_web.service import AppError, AppService
from test_coordination import app, Runtime


async def read(app, sid, **args):
    return (await app.dispatch('coordination.review', {'sessionId': sid, **args}))['result']


async def test_review_is_scoped_passive_and_does_not_read_transcripts(app, monkeypatch):
    owner, peer = app.state['sessions']
    peer.update(configurationPending=True, fileAccessPending={'revision': 3},
                approvals=[{'id': 'approval', 'status': 'pending'}],
                completion={'id': 'response', 'at': 123},
                worktreeHandoffs=[{'id': 'move', 'phase': 'unknown', 'updatedAt': 120}],
                workingDirectory='/owned/copy')
    await app.dispatch('question.create', {'sessionId': peer['id'], 'prompt': 'Private prompt', 'required': False, 'dependency': 'review'})
    foreign = app._new_session({'title': 'Not in this workspace'})
    foreign['workspace'] = '/different'
    internal = app._new_session({'title': 'Internal job', 'purpose': 'diagnostic'})
    app.state['sessions'].extend([foreign, internal]);app._save()
    selected, draft = app.state['selectedSessionId'], app.state['view']['draft']
    def no_admit(*args, **kwargs):
        pytest.fail('Review tried to admit or hydrate a chat')
    monkeypatch.setattr(app, '_session', no_admit)
    monkeypatch.setattr(app.history, 'ensure_loaded', no_admit)
    result = await read(app, owner['id'])
    assert {row['sessionId'] for row in result['items']} == {owner['id'], peer['id']}
    saved = next(row for row in result['items'] if row['sessionId'] == peer['id'])
    assert saved['approvals'] == saved['questions'] == 1 and saved['configurationPending']
    assert saved['responseAt'] == 123 and saved['executionDirectory'] == '/owned/copy'
    assert saved['handoffs']['items'] == [{'id': 'move', 'phase': 'unknown', 'updatedAt': 120}]
    assert 'Private prompt' not in json.dumps(result) and 'messages' not in json.dumps(result)
    assert result['workerAdoption'] == result['sourceFreshness'] == 'not_inspected'
    assert (app.state['selectedSessionId'], app.state['view']['draft']) == (selected, draft)
    assert not app.runtime.sent and not app.runtime.messages


async def test_review_restart_keeps_dormant_owners_out_of_working_set(app, monkeypatch):
    owner, peer = app.state['sessions'];peer_id = peer['id']
    peer.update(status='idle',configurationPending=False,
                worktreeHandoffs=[{'id': 'uncertain', 'phase': 'unknown', 'updatedAt': 2}])
    app._save();await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        before = {row['id'] for row in restored.state['sessions']}
        assert peer_id not in before
        monkeypatch.setattr(restored, '_session', lambda *a, **k: pytest.fail('Dormant owner admitted'))
        result = await read(restored, owner['id'])
        saved = next(row for row in result['items'] if row['sessionId'] == peer_id)
        assert not saved['activityObserved']
        assert {row['id'] for row in restored.state['sessions']} == before
        assert not restored.runtime.sent
    finally:
        await restored.close()


async def test_cursor_survives_prior_deletion_and_rejects_scope_change(app):
    owner, peer = app.state['sessions']
    third = app._new_session({'title': 'Third'});app.state['sessions'].append(third);app._save()
    first = await read(app, owner['id'], limit=1)
    second = await read(app, owner['id'], limit=1, after=first['nextAfter'])
    assert first['items'][0]['sessionId'] != second['items'][0]['sessionId']
    # Removing a prior peer does not shift a keyset cursor and skip the next one.
    removed = second['items'][0]['sessionId']
    app.state['sessions'] = [row for row in app.state['sessions'] if row['id'] != removed]
    app.directory.sync([], app.state, deleted=[removed]);app.db.commit()
    following = await read(app, owner['id'], limit=1, after=second['nextAfter'])
    assert following['items'][0]['sessionId'] == third['id'] and not following['nextAfter']
    with pytest.raises(AppError, match='different chat or workspace'):
        await read(app, third['id'], after=first['nextAfter'])
    owner['workspace'] = '/moved'
    with pytest.raises(AppError, match='different chat or workspace'):
        await read(app, owner['id'], after=first['nextAfter'])


async def test_agent_review_matches_ui_and_cannot_read_as_other_root(app):
    owner, peer = app.state['sessions']
    ui = await read(app, owner['id'])
    result = await app.dispatch('coordination.review', {'sessionId': owner['id']}, origin='agent', caller_session_id=owner['id'])
    assert result['result']['items'] == ui['items']
    with pytest.raises(AppError, match='calling conversation'):
        await app.dispatch('coordination.review', {'sessionId': peer['id']}, origin='agent', caller_session_id=owner['id'])
    with pytest.raises(AppError):
        await app.dispatch('coordination.review', {'sessionId': owner['id'], 'limit': 21})
    before = owner['workspace']
    try:
        owner['workspace'] = None
        with pytest.raises(AppError, match='workspace scope'):
            await read(app, owner['id'])
    finally:
        owner['workspace'] = before


async def test_stale_claim_uses_canonical_rule_without_loading_holder(app, monkeypatch):
    owner, peer = app.state['sessions']
    assignment = app.assignments.ensure(owner, 'start')
    peer['assignmentId'] = assignment['id'];peer['interruptionRevision'] = 1
    assignment['members'][peer['id']] = {'status': 'bound', 'role': 'helper'}
    assignment['tasks'] = {'task': {'id': 'task', 'title': 'Retained responsibility', 'status': 'claimed', 'revision': 2,
        'claim': {'id': 'claim', 'sessionId': peer['id'], 'interruptionRevision': 0}}}
    app.assignments.save(assignment);app._save()
    before = copy.deepcopy(assignment)
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        assert peer['id'] not in {row['id'] for row in restored.state['sessions']}
        monkeypatch.setattr(restored, '_session', lambda *a, **k: pytest.fail('Claim inspection admitted a holder'))
        result = await read(restored, owner['id'])
        assert result['claims']['total'] == 1
        claim = result['claims']['items'][0]
        assert claim['status'] == 'stale' and claim['claimId'] == 'claim'
        assert restored.assignments.load(assignment['id']) == before
        assert peer['id'] not in {row['id'] for row in restored.state['sessions']}
        assert not restored.runtime.sent
    finally:
        await restored.close()


async def test_source_observations_remain_past_scoped_receipts(app):
    owner, peer = app.state['sessions']
    for number in range(8):
        app.collaboration.insert('source-'+str(number), 'test-'+str(number), {'commandAction': 'integration.check', 'result': {
            'id': 'source-'+str(number), 'sessionId': owner['id'] if number < 7 else peer['id'],
            'kind': 'check', 'status': 'mismatch', 'capturedAt': number, 'privateExtra': 'Do not copy'}})
    app.db.commit()
    result = await read(app, owner['id'])
    assert [row['id'] for row in result['sourceObservations']] == ['source-6','source-5','source-4','source-3']
    assert result['sourceObservations'][0]['status'] == 'mismatch'
    assert 'Do not copy' not in json.dumps(result)


async def test_old_claim_metadata_remains_unavailable_and_never_infers_a_current_claim(app):
    owner, peer = app.state['sessions']
    assignment = app.assignments.ensure(owner, 'old-start')
    peer['assignmentId'] = assignment['id']
    assignment['members'][peer['id']] = {'status': 'bound', 'role': 'helper'}
    assignment['tasks'] = {'task': {'id': 'task', 'title': 'Old claim', 'status': 'claimed', 'revision': 1,
        'claim': {'id': 'claim', 'sessionId': peer['id'], 'interruptionRevision': 0}}}
    app.assignments.save(assignment);app._save()
    app.state['sessions'] = [owner]
    app.db.execute("UPDATE history_local_rows SET summary=json_remove(summary,'$.assignmentId','$.interruptionRevision') WHERE id=?", (peer['id'],))
    app.db.commit()
    result = await read(app, owner['id'])
    assert result['claims']['items'][0]['status'] == 'unavailable'
    assert [row['id'] for row in app.state['sessions']] == [owner['id']]


async def test_native_root_tool_receives_compact_review_and_child_cannot_borrow_claim_scope(app):
    from test_collaborative_workspaces import agent_action, generation
    from amplifier_web.collaboration import PRINCIPAL
    source = app.state['sessions'][0]
    await generation(app, source, 'review-generation', ['review-input'])
    result = await agent_action(app, source, 'coordination.review', {'sessionId': source['id']}, 'review-read')
    assert result['accepted'] and result['result']['sessionId'] == source['id']
    assert 'sessions' not in result.get('state', {})
    token = PRINCIPAL.set('actual-child')
    try:
        with pytest.raises(AppError, match='child cannot borrow'):
            await app.dispatch('coordination.review', {'sessionId': source['id']}, origin='agent', caller_session_id=source['id'])
    finally:
        PRINCIPAL.reset(token)


async def test_expired_approval_is_a_review_item_not_a_pending_decision(app):
    owner, peer = app.state['sessions']
    peer['approvals'] = [{'id': 'old', 'status': 'expired', 'resolutionReason': 'host_restarted'}]
    result = await read(app, owner['id'])
    item = next(row for row in result['items'] if row['sessionId'] == peer['id'])
    assert item['approvals'] == 0 and item['expiredApprovals'] == 1


async def test_dormant_native_wait_is_unverified_on_passive_review_after_restart(app, monkeypatch):
    owner, peer = app.state['sessions']
    peer.update(status='idle', approvals=[{'id': 'native-wait', 'status': 'pending', 'prompt': 'Private permission'}])
    app._save();await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        assert peer['id'] not in {row['id'] for row in restored.state['sessions']}
        monkeypatch.setattr(restored, '_session', lambda *a, **k: pytest.fail('Passive review admitted dormant native waiter'))
        result = await read(restored, owner['id'])
        item = next(row for row in result['items'] if row['sessionId'] == peer['id'])
        assert item['approvals'] == 0 and item['unverifiedApprovals'] == 1
        assert peer['id'] not in {row['id'] for row in restored.state['sessions']}
        assert not restored.runtime.sent and not restored.runtime.messages
        assert 'Private permission' not in json.dumps(result)
    finally:
        await restored.close()
