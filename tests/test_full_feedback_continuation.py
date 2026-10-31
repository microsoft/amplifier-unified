"""Standing Full text follow-ups preserve source authority and uncertain effects."""
import asyncio
import copy
import json

import pytest

from amplifier_web import feedback
from amplifier_web.feedback_lifecycle import revision
from amplifier_web.service import AppError
from test_coordination_generation import app, agent
from test_feedback_followup import FEEDBACK_ID, URL, seed
from test_full_feedback import call, mode


def record(app, identity):
    row = app.db.execute('SELECT fingerprint,payload,receipt FROM feedback_followups WHERE id=?', (identity,)).fetchone()
    return row[0], json.loads(row[1]), json.loads(row[2])


async def settled(app):
    async with asyncio.timeout(5):
        while app.db.execute("SELECT 1 FROM feedback_followups WHERE "
                "json_extract(receipt,'$.status') IN ('queued','sending') LIMIT 1").fetchone():
            await asyncio.sleep(.01)


@pytest.fixture
def remote(monkeypatch):
    issue = {'id': 77, 'number': 42, 'html_url': URL, 'user': {'id': 7, 'login': 'owner'},
        'title': 'Original report', 'body': 'Original body\n<!-- amplifier-feedback:' + FEEDBACK_ID + ' -->',
        'state': 'open', 'updated_at': '2026-10-30T00:00:00Z'}
    calls, hooks, comments = [], {}, []

    async def api(endpoint, payload, *, method=None):
        calls.append((endpoint, copy.deepcopy(payload), method))
        if hook := hooks.get(endpoint):
            await hook()
        if endpoint == 'user':
            return {'id': 7, 'login': 'owner'}
        if endpoint.startswith('search/issues?'):
            found = [copy.deepcopy(issue)] if 'microsoft' in endpoint else []
            return {'items': found, 'total_count': len(found), 'incomplete_results': False}
        if payload is None:
            return copy.deepcopy(comments if '/comments?' in endpoint else issue)
        if method == 'PATCH':
            issue['state'] = payload['state']
            return copy.deepcopy(issue)
        result = {'id': 123 + len(comments), 'html_url': URL + '#issuecomment-' + str(123 + len(comments)),
            'user': {'id': 7}, 'body': payload['body'], 'created_at': '2026-10-30T00:01:00Z'}
        comments.append(result)
        return copy.deepcopy(result)

    monkeypatch.setattr(feedback, 'github_api', api)
    return issue, calls, hooks


def request(action, issue, identity='full-followup'):
    value = {'requestId': identity, 'feedbackId': FEEDBACK_ID}
    if action in {'feedback.update', 'feedback.close', 'feedback.reopen'}:
        value['expectedRevision'] = revision(issue)
    if action in {'feedback.comment', 'feedback.update'}:
        value['body'] = 'Autonomous continuation with exact text.'
    if action == 'feedback.update':
        value['title'] = 'Corrected title'
    return value


@pytest.mark.parametrize('origin', ['agent', 'system'])
@pytest.mark.parametrize('action', ['feedback.comment', 'feedback.update', 'feedback.close', 'feedback.reopen'])
async def test_full_text_followups_retain_actual_source_without_browser_or_new_human(app, remote, origin, action):
    source, other = app.state['sessions']
    await seed(app)  # A host-owned report, not private to the original chat.
    await mode(app, source)
    if action == 'feedback.reopen':
        remote[0]['state'] = 'closed'
    before = copy.deepcopy(remote[0])
    payload = request(action, remote[0])
    await call(app, source, action, payload, origin=origin)
    await settled(app)
    frozen = record(app, payload['requestId'])
    receipt = frozen[2]
    assert receipt['status'] == 'submitted'
    assert receipt['origin'] == origin and receipt['sourceSessionId'] == source['id']
    assert receipt['authority'] == frozen[1]['_authority']
    assert receipt['authority']['sessionId'] == source['id']
    assert receipt['authority']['interruptionRevision'] == 0
    if origin == 'agent':
        assert receipt['runtimeSessionId'] == source['id']
        assert receipt['authority']['inputIds'] == ['incoming-peer']
        assert receipt['authority']['inputCoverage'] == 'complete'
    else:
        assert 'runtimeSessionId' not in receipt and 'generationId' not in receipt['authority']
    assert receipt['author']['id'] == 7  # Remote account is distinct from the local caller.
    assert not app.clients.records and not source['approvals']
    writes = [row for row in remote[1] if row[1] is not None]
    assert len(writes) == 1
    assert remote[0]['title'] == before['title'] and remote[0]['body'] == before['body']
    assert 'reviewRequestId' not in frozen[1]
    await mode(app, source, 'ask')
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    count = len(remote[1])
    await call(app, source, action, payload, origin=origin)
    # A different caller can inspect the exact accepted result, not take over
    # the effect or rewrite its attribution. No fresh authority is captured.
    await call(app, other, action, payload, origin='system')
    await settled(app)
    assert len(remote[1]) == count and record(app, payload['requestId']) == frozen
    with pytest.raises(AppError, match='different contents'):
        await call(app, source, action, {**payload, 'expectedRevision': '0' * 64} if 'expectedRevision' in payload
            else {**payload, 'body': 'Different intent'}, origin=origin)


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on'])
@pytest.mark.parametrize('action', ['feedback.comment', 'feedback.update', 'feedback.close'])
async def test_full_followup_checks_captured_authority_after_remote_await(app, remote, boundary, action):
    source = app.state['sessions'][0]
    await seed(app)
    await mode(app, source)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause():
        entered.set()
        await release.wait()

    remote[2]['user'] = pause
    payload = request(action, remote[0])
    await call(app, source, action, payload)
    await asyncio.wait_for(entered.wait(), 5)
    original = record(app, payload['requestId'])[:2]
    if boundary == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': source['id']})
    else:
        await mode(app, source, 'ask')
        if boundary == 'off-on':
            await mode(app, source)
    release.set()
    await settled(app)
    current = record(app, payload['requestId'])
    assert current[:2] == original and current[2]['status'] == 'failed'
    assert not [row for row in remote[1] if row[1] is not None]
    count = len(remote[1])
    await call(app, source, action, payload)
    await settled(app)
    assert len(remote[1]) == count


async def test_full_followup_unknown_is_not_replayed_and_reads_need_no_renewed_authority(app, remote):
    source = app.state['sessions'][0]
    await seed(app)
    await mode(app, source)
    endpoint = 'repos/' + URL.removeprefix('https://github.com/') + '/comments'

    async def lost():
        raise ConnectionError('The response was lost after possible acceptance')

    remote[2][endpoint] = lost
    payload = request('feedback.comment', remote[0])
    await call(app, source, 'feedback.comment', payload)
    await settled(app)
    original = record(app, payload['requestId'])
    assert original[2]['status'] == 'unknown'
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    await mode(app, source, 'ask')
    await call(app, source, 'feedback.comment', payload)
    del remote[2][endpoint]
    await call(app, source, 'feedback.get', request('feedback.get', remote[0], 'read-after-stop'), origin='system')
    await settled(app)
    read = record(app, 'read-after-stop')[2]
    assert read['status'] == 'completed' and read['sourceSessionId'] == source['id']
    assert 'authority' not in read and record(app, payload['requestId']) == original
    assert len([row for row in remote[1] if row[1] is not None]) == 1


async def test_full_original_reconcile_after_stop_is_read_only_and_keeps_original_authority(app, remote):
    source = app.state['sessions'][0]
    await seed(app)
    await mode(app, source)
    # This is the existing unknown original-submission recovery path, not a
    # new retry or an attempt to resume the text follow-up journal.
    await app.feedback.update(FEEDBACK_ID, status='unknown', url=None)
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    payload = request('feedback.reconcile', remote[0], 'reconcile-after-stop')
    await call(app, source, 'feedback.reconcile', payload, origin='system')
    await settled(app)
    result = record(app, payload['requestId'])[2]
    assert result['status'] == 'completed' and result['outcome'] == 'found'
    assert result['sourceSessionId'] == source['id'] and 'authority' not in result
    assert all(row[1] is None for row in remote[1])


async def test_stale_full_followup_refuses_before_acceptance_without_changing_ask_flow(app, remote):
    source = app.state['sessions'][0]
    await seed(app)
    await mode(app, source)
    payload = request('feedback.comment', remote[0])
    with pytest.raises(AppError, match='current transport-bound root'):
        await agent(app, source, 'feedback.comment', payload, None, _generationId='old')
    assert not app.db.execute('SELECT 1 FROM feedback_followups').fetchone()
    await mode(app, source, 'ask')
    await app.dispatch('feedback.comment', payload, origin='system', caller_session_id=source['id'])
    await settled(app)
    result = record(app, payload['requestId'])[2]
    assert result['status'] == 'submitted' and 'authority' not in result
