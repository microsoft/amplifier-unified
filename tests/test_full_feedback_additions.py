"""Conversation-owned later files through shared dispatch; GitHub is intercepted."""
import asyncio
import base64
import copy
import json

import pytest

from amplifier_web import feedback
from amplifier_web.service import AppError
from test_full_child_configuration import app, mounted, dispatch
from test_full_feedback import call, mode
from test_full_feedback_continuation import settled
from test_feedback_additions import FID, URL, Wire, stage as browser_stage


@pytest.fixture
async def remote(app, monkeypatch):
    # A host receipt, without a submitting browser or native worker.
    app.feedback.accept({'requestId': FID, 'title': 'Original', 'body': 'Original',
                         'category': 'bug', 'includeDiagnostics': False})
    await app.feedback.update(FID, status='submitted', url=URL)
    wire = Wire(app)
    monkeypatch.setattr(feedback, 'github_api', wire)
    return wire


def staging(identity='full-file-stage', data=b'exact later evidence'):
    return {'requestId': identity, 'feedbackId': FID, 'name': 'later.txt',
            'base64': base64.b64encode(data).decode()}


def addition(files, identity='addition-request', **extra):
    return {'requestId': identity, 'feedbackId': FID,
            'confirmedFiles': [{'id': row['id'], 'sha256': row['sha256']} for row in files],
            'comment': 'Selected later evidence', **extra}


def retained(app, identity='addition-request'):
    return app.feedback.additions.load(identity)[1]


async def staged(app, source, *, origin='agent', identity='full-file-stage'):
    result = await call(app, source, 'feedback.attachment.add', staging(identity), origin=origin)
    return result['result']


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_addition_needs_no_browser_or_human_review_and_returns_exact_receipt(app, remote, origin):
    source = app.state['sessions'][0]
    await mode(app, source)
    row = await staged(app, source, origin=origin)
    args = addition([row])
    admitted = await call(app, source, 'feedback.attachments.add', args, origin=origin)
    assert admitted['result']['status'] == 'queued'
    await settled(app)
    original = copy.deepcopy(retained(app))
    assert original['status'] == 'submitted' and original['sourceSessionId'] == source['id']
    assert 'clientId' not in original and original['origin'] == origin
    assert original['authority']['sessionId'] == source['id']
    assert original['audit']['review']['account']['id'] == 7
    assert original['audit']['review']['repository']['id'] == 101
    assert original['audit']['manifest'][0]['sha256'] == row['sha256']
    assert 'reviewRequestId' not in app.feedback.additions.load(args['requestId'])[0]
    assert len(remote.writes) == 5
    await mode(app, source, 'ask')
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    result = await call(app, source, 'feedback.attachments.add', args, origin=origin)
    assert result['result']['status'] == 'submitted' and result['result']['commentUrl'].startswith(URL)
    assert 'authority' not in result['result'] and 'audit' not in result['result']
    assert retained(app) == original and len(remote.writes) == 5
    assert not app.clients.records and not source['approvals']
    assert remote.issue['body'].startswith('Original') and remote.issue['state'] == 'open'


@pytest.mark.parametrize('kind', ['finite', 'persistent', 'nested'])
@pytest.mark.parametrize('input_count', [1, 17])
async def test_mounted_full_child_files_keep_actual_actor_and_outlive_turn(mounted, remote, monkeypatch, kind, input_count):
    from amplifier_web.host.children import _PERSISTENT
    h = mounted
    await mode(h.app, h.source)
    assigned = ['assigned-input'] if input_count == 1 else ['assigned-' + str(i) for i in range(input_count)]
    h.worker.runtime.generation['input_ids'] = list(assigned)
    h.source['collaborationGeneration']['inputIds'] = list(assigned)
    h.worker.context_inputs = list(assigned)
    h.worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in assigned}
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(endpoint, payload, **kwargs):
        result = await remote(endpoint, payload, **kwargs)
        if endpoint == 'user':
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(feedback, 'github_api', delayed)
    actors = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        if kind == 'nested' and coordinator.session_id == 'child':
            await h.registry.spawn('worker', 'publish later files', h.registry.sessions['child'], sub_session_id='grandchild')
            return 'delegated'
        control = tools['app_control']
        selected = await dispatch(control, 'feedback.attachment.add', staging(), 'child-stage')
        assert selected.success, selected.error
        row = selected.output['result']
        result = await dispatch(control, 'feedback.attachments.add', addition([row]), 'child-add')
        assert result.success, result.error
        assert result.output['result']['status'] == 'queued'
        actors.append(coordinator.session_id)
        return 'accepted'

    h.install_execute(execute)
    token = _PERSISTENT.set(kind == 'persistent')
    try:
        await h.registry.spawn('worker', 'publish later files', h.parent, sub_session_id='child')
        await asyncio.wait_for(entered.wait(), 5)
        h.source['collaborationGeneration']['terminal'] = True
        h.source['status'] = 'idle'
    finally:
        release.set()
        _PERSISTENT.reset(token)
    await settled(h.app)
    receipt = retained(h.app)
    assert receipt['status'] == 'submitted'
    assert actors == (['grandchild'] if kind == 'nested' else ['child'])
    assert receipt['runtimeSessionId'] == receipt['authority']['runtimeSessionId'] == actors[0]
    assert receipt['authority']['inputIds'] == (assigned if input_count == 1 else [])
    assert receipt['authority']['inputCoverage'] == ('complete' if input_count == 1 else 'unavailable')
    assert receipt['authority']['generationId'] == 'generation-1'
    assert receipt['sourceSessionId'] == h.source['id'] and 'clientId' not in receipt
    assert not h.app.clients.records and not h.source['approvals']
    h.approval_prompt.assert_not_awaited()
    assert len(remote.writes) == 5


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on'])
@pytest.mark.parametrize('wait_at', ['preflight', 'commit_actor'])
async def test_full_checks_after_network_wait_before_next_real_write(app, remote, monkeypatch, boundary, wait_at):
    source = app.state['sessions'][0]
    await mode(app, source)
    row = await staged(app, source)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(endpoint, payload, **kwargs):
        result = await remote(endpoint, payload, **kwargs)
        if endpoint == 'user' and wait_at == 'preflight' or '/commits/' in endpoint and payload is None and wait_at == 'commit_actor':
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(feedback, 'github_api', held)
    args = addition([row])
    await call(app, source, 'feedback.attachments.add', args)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        authority = copy.deepcopy(retained(app)['authority'])
        if boundary == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': source['id']})
        else:
            await mode(app, source, 'ask')
            if boundary == 'off-on':
                await mode(app, source)
    finally:
        release.set()
    await settled(app)
    receipt = retained(app)
    assert receipt['authority'] == authority
    assert len(remote.writes) == (0 if wait_at == 'preflight' else 3)
    assert receipt['status'] == ('failed' if wait_at == 'preflight' else 'unknown')
    assert receipt['audit']['phases']['ref']['status'] == 'not_started'
    assert receipt['audit']['phases']['comment']['status'] == 'not_started'
    before = copy.deepcopy(receipt)
    await call(app, source, 'feedback.attachments.add', args)
    assert retained(app) == before and len(remote.writes) == (0 if wait_at == 'preflight' else 3)


@pytest.mark.parametrize('phase', ['blob', 'comment'])
async def test_unknown_upload_read_only_reconciliation_after_stop_never_repeats(app, remote, phase):
    source, peer = app.state['sessions']
    await mode(app, source)
    await mode(app, peer)
    row = await staged(app, source, origin='system')
    remote.lose = 'blob:' + row['id'] if phase == 'blob' else phase
    remote.after_processing = True
    args = addition([row])
    await call(app, source, 'feedback.attachments.add', args, origin='system')
    await settled(app)
    original = copy.deepcopy(retained(app))
    assert original['status'] == ('unknown' if phase == 'blob' else 'partial')
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    await mode(app, source, 'ask')
    retry = await call(app, source, 'feedback.attachments.add', args, origin='system')
    assert retry['result']['status'] == original['status']
    with pytest.raises(AppError):
        await call(app, peer, 'feedback.attachments.add', args, origin='system')
    check = {'requestId': 'check-unknown', 'feedbackId': FID, 'additionRequestId': args['requestId']}
    with pytest.raises(AppError):
        await call(app, peer, 'feedback.attachments.reconcile', check, origin='system')
    writes = len(remote.writes)
    remote.lose = None
    await call(app, source, 'feedback.attachments.reconcile', check, origin='system')
    await settled(app)
    observed = await call(app, source, 'feedback.attachments.reconcile', check, origin='system')
    assert observed['result']['status'] == 'completed' and 'authority' not in retained(app, check['requestId'])
    final = retained(app)
    assert final['authority'] == original['authority'] and final['sourceSessionId'] == source['id']
    assert final['status'] == ('unknown' if phase == 'blob' else 'submitted')
    assert len(remote.writes) == writes
    if phase == 'blob':
        assert final['audit']['phases']['tree']['status'] == 'not_started'
        await mode(app, source)
        with pytest.raises(AppError):
            await call(app, source, 'feedback.attachments.add', {**args, 'requestId': 'do-not-retry'}, origin='system')


async def test_full_scope_cannot_take_browser_draft_or_another_conversations_files(app, remote):
    source, peer = app.state['sessions']
    await mode(app, source)
    await mode(app, peer)
    # Identical textual IDs do not collapse the two owner namespaces.
    app.clients.attach(source['id'])
    with app.clients.bind(source['id']):
        browser, _ = await browser_stage(app)
        before = copy.deepcopy(app.feedback.additions.project())
        row = await staged(app, source)
        assert app.feedback.additions.project() == before
        with pytest.raises(AppError):
            await call(app, source, 'feedback.attachment.remove', {'feedbackId': FID, 'id': browser['id']})
        with pytest.raises(AppError):
            await call(app, source, 'feedback.attachments.add', addition([browser]))
        with pytest.raises(AppError):
            await app.dispatch('feedback.attachments.add', addition([browser]))
        with pytest.raises(AppError):
            await app.dispatch('feedback.attachment.add', staging())
        with pytest.raises(AppError):
            await app.dispatch('feedback.attachment.remove', {'feedbackId': FID, 'id': row['id']})
        assert app.feedback.additions.project() == before
    with pytest.raises(AppError):
        await staged(app, peer, origin='system')
    with pytest.raises(AppError):
        await call(app, peer, 'feedback.attachments.add', addition([row]), origin='system')
    assert not remote.writes
    args = addition([row])
    await call(app, source, 'feedback.attachments.add', args)
    await settled(app)
    with app.clients.bind(source['id']):
        assert app.feedback.additions.project() == before
        with pytest.raises(AppError):
            await app.dispatch('feedback.attachments.add', args)
    assert len(remote.writes) == 5


async def test_full_optional_review_remains_exact_and_one_use(app, remote):
    source = app.state['sessions'][0]
    await mode(app, source)
    row = await staged(app, source)
    review = {'requestId': 'optional-review', 'feedbackId': FID}
    await call(app, source, 'feedback.attachments.review', review)
    await settled(app)
    inspected = await call(app, source, 'feedback.attachments.review', review)
    assert inspected['result']['review']['manifest'][0]['id'] == row['id']
    assert 'confirmation is still required' not in inspected['result']['message']
    remote.repo['id'] = 999
    args = addition([row], reviewRequestId=review['requestId'])
    await call(app, source, 'feedback.attachments.add', args)
    await settled(app)
    assert retained(app)['status'] == 'failed' and not remote.writes
    with pytest.raises(AppError):
        await call(app, source, 'feedback.attachments.add', {**args, 'requestId': 'review-reuse'})
    assert retained(app, review['requestId'])['consumedBy'] == args['requestId']


async def test_changed_or_incomplete_selected_manifest_refuses_before_acceptance(app, remote):
    source = app.state['sessions'][0]
    await mode(app, source)
    first = await staged(app, source)
    second = await staged(app, source, identity='second-file-stage')
    with pytest.raises(AppError):
        await call(app, source, 'feedback.attachments.add', addition([first]))
    with pytest.raises(AppError):
        await call(app, source, 'feedback.attachments.add', addition([first, {**second, 'sha256': '0' * 64}]))
    assert not app.db.execute('SELECT 1 FROM feedback_followups').fetchone() and not remote.writes


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_outer_command_receipts_keep_conversation_owner_after_stop_and_revocation(app, remote, origin):
    source, peer = app.state['sessions']
    await mode(app, source)
    await mode(app, peer)
    peer['collaborationGeneration'] = {'id': 'peer-generation', 'inputIds': ['peer-input'],
        'terminal': False, 'interruptionRevision': peer.get('interruptionRevision', 0)}
    accepted = []

    async def command(action, args, identity):
        result = await call(app, source, action, args, origin=origin, command=identity)
        accepted.append((action, copy.deepcopy(args), identity, copy.deepcopy(result['result'])))
        return result['result']

    row = await command('feedback.attachment.add', staging(), 'outer-stage')
    spare = await command('feedback.attachment.add', staging('spare-file-stage'), 'outer-spare')
    await command('feedback.attachment.remove', {'feedbackId': FID, 'id': spare['id']}, 'outer-remove')
    review = {'requestId': 'outer-review-request', 'feedbackId': FID}
    await command('feedback.attachments.review', review, 'outer-review')
    await settled(app)
    remote.lose, remote.after_processing = 'comment', True
    args = addition([row], reviewRequestId=review['requestId'])
    await command('feedback.attachments.add', args, 'outer-add')
    await settled(app)
    assert retained(app)['status'] == 'partial'
    original_authority = copy.deepcopy(retained(app)['authority'])
    await mode(app, source, 'ask')
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    remote.lose = None
    # The new check is observational and therefore needs no renewed generation.
    check = {'requestId': 'outer-reconcile-request', 'feedbackId': FID,
             'additionRequestId': args['requestId']}
    await command('feedback.attachments.reconcile', check, 'outer-reconcile')
    await settled(app)
    assert retained(app)['status'] == 'submitted'
    writes = len(remote.writes)
    for action, payload, identity, original_result in accepted:
        # Exercise the outer commands ledger, not only the inner requestId.
        with pytest.raises(AppError, match='command ID.*different contents'):
            await call(app, peer, action, payload, origin=origin, command=identity)
        result = await call(app, source, action, payload, origin=origin, command=identity)
        assert result['duplicate'] and result['result'] == original_result
    assert retained(app)['authority'] == original_authority
    assert len(remote.writes) == writes == 5
