"""Initial Full publication through shared actions; intercepted GitHub only."""
import asyncio
import base64
import copy
import hashlib
import json

import pytest

from amplifier_web import attachments, feedback
from amplifier_web.service import AppError
from test_coordination_generation import app, agent
from test_feedback_attachments import submission


async def settle(app):
    """Await the domain result, independent of canceled UI progress tasks."""
    async with asyncio.timeout(5):
        while app.db.execute("SELECT 1 FROM feedback_requests WHERE "
                "json_extract(receipt,'$.status') IN ('queued','sending') LIMIT 1").fetchone():
            await asyncio.sleep(.01)


async def mode(app, source, value='full'):
    current = (await app.dispatch('access.get', {'sessionId': source['id']}))['result']
    await app.dispatch('access.set', {'sessionId': source['id'], 'scope': 'session',
        'mode': value, 'expectedRevision': current['revision']})


async def call(app, source, action, args, *, origin='agent', command=None):
    if origin == 'system':
        return await app.dispatch(action, args, origin=origin, caller_session_id=source['id'], command_id=command)
    return await agent(app, source, action, args, command, _inputClients=[], _responseMode='receipt')


def staged(data=b'exact reproduction bytes', identity='full-file-stage'):
    return {'requestId': identity, 'name': 'reproduction.txt', 'base64': base64.b64encode(data).decode()}


def saved(app, identity='attachment-feedback-1'):
    row = app.db.execute('SELECT fingerprint,payload,receipt FROM feedback_requests WHERE id=?', (identity,)).fetchone()
    return row[0], json.loads(row[1]), json.loads(row[2])


@pytest.fixture
def github(monkeypatch):
    calls = []
    target = {'full_name': feedback.REPOSITORY, 'private': False, 'id': 101}
    hooks = {}

    async def api(endpoint, payload):
        calls.append((endpoint, copy.deepcopy(payload)))
        hook = hooks.get(endpoint)
        if hook:
            await hook()
        if payload is None:
            return copy.deepcopy(target)
        if endpoint.endswith('/issues'):
            return {'html_url': feedback.ISSUES_URL + '/42'}
        if endpoint.endswith('/git/refs'):
            return {'object': {'sha': 'c' * 40}}
        return {'sha': 'c' * 40 if endpoint.endswith('/git/commits') else 'a' * 40}

    monkeypatch.setattr(feedback, 'github_api', api)
    monkeypatch.setattr(feedback.shutil, 'which', lambda _: '/fixture/gh')
    return calls, target, hooks


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_text_only_skips_upload_metadata_lookup(app, github, origin):
    source = app.state['sessions'][0]
    await mode(app, source)

    async def forbidden():
        raise AssertionError('Text-only feedback must not read upload metadata')

    github[2]['repos/' + feedback.REPOSITORY] = forbidden
    payload = submission(includeDiagnostics=False)
    await call(app, source, 'feedback.submit', payload, origin=origin)
    await settle(app)
    _, frozen, receipt = saved(app)
    assert receipt['status'] == 'submitted'
    assert receipt['authority']['origin'] == origin
    assert receipt['authority']['sessionId'] == source['id']
    assert receipt['authority'] == frozen['_authority']
    assert frozen['_attachments'] == []
    assert 'destination' not in receipt
    assert github[0] == [('repos/' + feedback.REPOSITORY + '/issues', {
        'title': payload['title'], 'body': payload['body'] + '\n\n---\nCategory: Bug report'
        + '\n\n<!-- amplifier-feedback:' + payload['requestId'] + ' -->'})]


@pytest.mark.parametrize('repository_id', [101, 999])
async def test_full_text_only_honors_explicit_destination(app, github, repository_id):
    source = app.state['sessions'][0]
    await mode(app, source)
    destination = {'repository': feedback.REPOSITORY, 'repositoryId': repository_id, 'visibility': 'public'}
    await call(app, source, 'feedback.submit', submission(includeDiagnostics=False, uploadDestination=destination))
    await settle(app)
    _, frozen, receipt = saved(app)
    assert frozen['_attachments'] == []
    assert frozen['uploadDestination'] == destination
    assert [path for path, payload in github[0] if payload is None] == ['repos/' + feedback.REPOSITORY]
    assert receipt['status'] == ('submitted' if repository_id == 101 else 'failed')
    assert len([1 for _, payload in github[0] if payload is not None]) == int(repository_id == 101)


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_initial_files_without_browser_or_human_confirmation(app, github, origin):
    source = app.state['sessions'][0]
    await mode(app, source)
    before = copy.deepcopy(app.state['view'])
    args = staged()
    row = (await call(app, source, 'feedback.attachment.add', args, origin=origin))['result']
    assert row['sha256'] == hashlib.sha256(b'exact reproduction bytes').hexdigest()
    assert not app.clients.records
    assert (await call(app, source, 'feedback.attachment.add', args, origin=origin))['result'] == row
    payload = submission(attachmentIds=[row['id']], includeDiagnostics=False)
    await call(app, source, 'feedback.submit', payload, origin=origin)
    await settle(app)
    fingerprint, frozen, receipt = saved(app)
    assert receipt['status'] == 'submitted'
    assert receipt['authority']['origin'] == origin
    assert receipt['authority']['sessionId'] == source['id']
    if origin == 'agent':
        assert receipt['authority']['runtimeSessionId'] == source['id']
        assert receipt['authority']['generationId'] == source['collaborationGeneration']['id']
        assert receipt['authority']['inputIds'] == ['incoming-peer']
    else:
        assert 'generationId' not in receipt['authority']
    assert receipt['destination'] == {'repository': feedback.REPOSITORY, 'repositoryId': 101, 'visibility': 'public'}
    assert 'uploadConsent' not in frozen['_attachments'][0]
    assert not any(key in frozen for key in ('confirmExcerpts', 'confirmedFiles', 'confirmedExcerpts'))
    blobs = [value for path, value in github[0] if path.endswith('/git/blobs')]
    assert len(blobs) == 1 and base64.b64decode(blobs[0]['content']) == b'exact reproduction bytes'
    assert len([1 for path, _ in github[0] if path == 'repos/' + feedback.REPOSITORY]) == 1
    assert app.state['view'] == before
    count = len(github[0])
    await mode(app, source, 'ask')
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    await call(app, source, 'feedback.submit', payload, origin=origin)
    await settle(app)
    assert saved(app) == (fingerprint, frozen, receipt)
    assert len(github[0]) == count
    with pytest.raises(AppError, match='different text'):
        await call(app, source, 'feedback.submit', {**payload, 'body': 'Different intent'}, origin=origin)


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_exact_excerpt_selection_has_no_browser_or_acknowledgement(app, github, origin):
    source = app.state['sessions'][0]
    source['messages'] = [{'id': 'one', 'role': 'user', 'text': 'Selected report'},
                          {'id': 'two', 'role': 'assistant', 'text': 'Selected response'},
                          {'id': 'three', 'role': 'user', 'text': 'UNSELECTED PRIVATE MESSAGE'}]
    app._publish()
    await mode(app, source)
    exported = await call(app, source, 'session.export', {'id': source['id'], 'format': 'markdown',
        'destination': 'none', 'scope': 'range', 'minimal': True, 'fromMessageId': 'one', 'throughMessageId': 'two'}, origin=origin)
    review = (await call(app, source, 'feedback.excerpt.review', {'id': source['id'],
        'snapshotId': exported['result']['snapshotId']}, origin=origin))['result']
    app.state['selectedSessionId'] = app.state['sessions'][1]['id']
    row = (await call(app, source, 'feedback.excerpt.stage', {'id': source['id'],
        'reviewId': review['reviewId'], 'requestId': 'full-excerpt-stage'}, origin=origin))['result']
    assert row['sha256'] == review['sha256']
    await call(app, source, 'feedback.submit', submission(attachmentIds=[row['id']], includeDiagnostics=False), origin=origin)
    await settle(app)
    blob = next(value for path, value in github[0] if path.endswith('/git/blobs'))
    assert base64.b64decode(blob['content']).decode() == review['text']
    assert 'Selected report' in review['text'] and 'UNSELECTED PRIVATE MESSAGE' not in review['text']
    assert saved(app)[2]['status'] == 'submitted'
    assert not app.clients.records
    assert not app.state['view'].get('feedbackDraft')


async def test_conversation_staging_is_separate_from_other_owner_and_browser(app, github):
    source, other = app.state['sessions']
    other['collaborationGeneration'] = {'id': 'other-turn', 'inputIds': ['other-input'], 'terminal': False}
    await mode(app, source)
    await mode(app, other)
    app.clients.attach('private-browser')
    with app.clients.bind('private-browser'):
        await app.dispatch('session.select', {'id': other['id']})
        await app.dispatch('feedback.attachment.add', staged(b'PRIVATE BROWSER BYTES', 'browser-stage'))
        app.state['view']['feedbackDraft'].update(title='PRIVATE DRAFT', pending={'requestId': 'attachment-feedback-1'})
        app.state['view']['panel'] = 'feedback'
        browser_before = copy.deepcopy(app.state['view'])
    first = (await call(app, source, 'feedback.attachment.add', staged()))['result']
    second = (await call(app, other, 'feedback.attachment.add', staged(b'OTHER OWNER', 'other-stage')))['result']
    with pytest.raises(AppError, match='attachment'):
        await call(app, source, 'feedback.submit', submission(attachmentIds=[second['id']]))
    with pytest.raises(AppError, match='owner'):
        await call(app, other, 'feedback.attachment.add', staged())
    destination = await call(app, source, 'feedback.destination', {})
    assert destination['files'] == [{'id': first['id'], 'sha256': first['sha256']}]
    with app.clients.bind('private-browser'):
        await call(app, source, 'feedback.submit', submission(attachmentIds=[first['id']], includeDiagnostics=False), origin='system')
        assert app.state['view'] == browser_before
    await settle(app)
    blobs = [base64.b64decode(value['content']) for path, value in github[0] if path.endswith('/git/blobs')]
    assert blobs == [b'exact reproduction bytes']
    assert not any('PRIVATE BROWSER BYTES' in json.dumps(payload) for _, payload in github[0])


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'revoke-reenable'])
@pytest.mark.parametrize('after_write', [False, True])
async def test_retained_boundary_fences_writes_during_await(app, github, boundary, after_write):
    source = app.state['sessions'][0]
    await mode(app, source)
    row = (await call(app, source, 'feedback.attachment.add', staged()))['result']
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause():
        entered.set()
        await release.wait()

    endpoint = 'repos/' + feedback.REPOSITORY + ('/git/blobs' if after_write else '')
    github[2][endpoint] = pause
    payload = submission(attachmentIds=[row['id']], includeDiagnostics=False)
    await call(app, source, 'feedback.submit', payload)
    await asyncio.wait_for(entered.wait(), 5)
    original_hash, original_payload, _ = saved(app)
    if boundary == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': source['id']})
    else:
        await mode(app, source, 'ask')
        if boundary == 'revoke-reenable':
            await mode(app, source)
    release.set()
    await settle(app)
    fingerprint, frozen, receipt = saved(app)
    assert (fingerprint, frozen) == (original_hash, original_payload)
    writes = [(path, value) for path, value in github[0] if value is not None]
    assert len(writes) == int(after_write)
    assert receipt['status'] == ('unknown' if after_write else 'failed')
    before = len(github[0])
    await call(app, source, 'feedback.submit', payload)
    await settle(app)
    assert len(github[0]) == before


@pytest.mark.parametrize('change', ['bytes', 'hash', 'destination'])
async def test_exact_preconditions_remain_effective_under_full(app, github, change):
    source = app.state['sessions'][0]
    await mode(app, source)
    row = (await call(app, source, 'feedback.attachment.add', staged()))['result']
    payload = submission(attachmentIds=[row['id']], includeDiagnostics=False)
    if change == 'bytes':
        path, _ = attachments.file_path(app.data_dir, row['id'])
        path.write_bytes(b'changed staged contents')
    elif change == 'hash':
        payload['confirmedFiles'] = [{'id': row['id'], 'sha256': '0' * 64}]
    else:
        payload['uploadDestination'] = {'repository': feedback.REPOSITORY, 'repositoryId': 999, 'visibility': 'public'}
    if change != 'destination':
        with pytest.raises(AppError):
            await call(app, source, 'feedback.submit', payload)
    else:
        await call(app, source, 'feedback.submit', payload)
        await settle(app)
        assert saved(app)[2]['status'] == 'failed'
    assert not any(value is not None for _, value in github[0])


async def test_lost_issue_response_retains_unknown_and_exact_retry_without_replay(app, github):
    source = app.state['sessions'][0]
    await mode(app, source)

    async def lost():
        raise ConnectionError('Fixture response lost after request')

    github[2]['repos/' + feedback.REPOSITORY + '/issues'] = lost
    payload = submission(includeDiagnostics=False)
    await call(app, source, 'feedback.submit', payload)
    await settle(app)
    original = saved(app)
    assert original[2]['status'] == 'unknown'
    await call(app, source, 'feedback.submit', payload)
    await settle(app)
    assert saved(app) == original
    assert len([1 for _, value in github[0] if value is not None]) == 1


async def test_stale_root_and_child_cannot_gain_initial_full_authority(app, github):
    source = app.state['sessions'][0]
    await mode(app, source)
    for transport in ({'_generationId': 'old-generation'}, {'_runtimeSessionId': 'unqualified-child'}):
        with pytest.raises(AppError, match='current transport-bound root'):
            await agent(app, source, 'feedback.submit', submission(), None, **transport)
    assert not github[0]


async def test_fresh_trusted_system_can_publish_after_stop_without_reusing_old_authority(app, github):
    source = app.state['sessions'][0]
    await mode(app, source)
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    assert source['interruptionRevision'] > 0
    await call(app, source, 'feedback.submit', submission(includeDiagnostics=False), origin='system')
    await settle(app)
    receipt = saved(app)[2]
    assert receipt['status'] == 'submitted'
    assert receipt['authority']['origin'] == 'system'
    assert receipt['authority']['interruptionRevision'] == source['interruptionRevision']
    assert 'generationId' not in receipt['authority']
