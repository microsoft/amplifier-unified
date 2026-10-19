"""Accepted promises survive execution loss; reads never admit a dormant worker.

Test-first contract for workspace.work. Synthetic records only. Runtime acceptance
requires the separately registered persisted browser/installed-candidate journey.
"""
import asyncio
import base64
import copy
import hashlib
import json
import time

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, Runtime, agent_action, generation


def workspace_id(app, owner):
    return next(w['id'] for w in app.state['workspaces'] if w['path'] == owner['workspace'])


def source(app, owner, text='Keep this request after the worker stops.', origin='ui'):
    message = app._message(owner, 'user', text, inputOrigin=origin)
    app._save()
    return {'sessionId': owner['id'], 'messageId': message['id']}


async def create(app, owner, original=None, command='capture-one', intent='promise-one', **patch):
    args = {'sessionId': owner['id'], 'workspaceId': workspace_id(app, owner),
            'source': original or source(app, owner), 'intentKey': intent,
            'title': 'Retain accepted request', 'commitment': 'accepted',
            'interpretation': 'Retain the promise independently of its worker.',
            'criteria': [{'id': 'retained', 'text': 'The exact request survives a cold reopen.'}],
            **patch}
    return await agent_action(app, owner, 'workspace.work.create', args, command)


async def read(app, owner, identity):
    return (await app.dispatch('workspace.work.read', {
        'workspaceId': workspace_id(app, owner), 'id': identity}))['result']


async def revise(app, owner, item, command, **patch):
    return await agent_action(app, owner, 'workspace.work.revise', {
        'sessionId': owner['id'], 'workspaceId': workspace_id(app, owner),
        'id': item['id'], 'expectedRevision': item['revision'],
        'expectedPromiseRevision': item['promiseRevision'], **patch}, command)


async def test_capture_keeps_exact_source_and_does_not_create_execution(app):
    owner = app.state['sessions'][0]
    text = 'Keep punctuation, Unicode: caf\u00e9 \U0001d11e.\nDo not replace these words.'
    original = source(app, owner, text)
    before = copy.deepcopy(app.runtime.inputs)
    result = await create(app, owner, original)
    item = result['result']['item']
    detail = await read(app, owner, item['id'])
    assert detail['originalSource']['text'] == text
    assert detail['originalSource']['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert detail['originalSource']['inputOrigin'] == 'ui'
    assert detail['originalSource']['messageId'] == original['messageId']
    assert item['commitment'] == 'accepted' and item['disposition'] == 'open'
    assert item['ownerSessionId'] == owner['id']
    assert item['promiseRevision'] == item['revision'] == 1
    assert app.runtime.inputs == before
    assert app.assignments.current(owner) is None
    assert 'state' not in result or 'sessions' not in result['state']


@pytest.mark.parametrize('origin', ['peer', 'agent', 'unknown'])
async def test_user_role_without_human_origin_cannot_be_captured_as_accepted(app, origin):
    owner = app.state['sessions'][0]
    original = source(app, owner, origin=origin)
    with pytest.raises(AppError):
        await create(app, owner, original)
    proposal = (await create(app, owner, original, command='proposal', commitment='proposed'))['result']['item']
    assert proposal['commitment'] == 'proposed'
    detail = await read(app, owner, proposal['id'])
    assert detail['originalSource']['inputOrigin'] == origin
    assert detail['item']['humanDecision'] == 'not_recorded'


async def test_capture_deduplicates_command_and_exact_source_intent(app):
    owner = app.state['sessions'][0]
    original = source(app, owner)
    first = await create(app, owner, original)
    same = await create(app, owner, original)
    assert same['duplicate'] and same['result'] == first['result']
    recovered = await create(app, owner, original, command='capture-after-lost-history')
    assert recovered['result']['item']['id'] == first['result']['item']['id']
    with pytest.raises(AppError):
        await create(app, owner, original, title='Changed command payload')
    listing = (await app.dispatch('workspace.work.list', {'workspaceId': workspace_id(app, owner)}))['result']
    assert listing['total'] == 1
    assert len(listing['items']) == 1


async def test_correction_preserves_original_and_rejects_stale_write(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    original = (await read(app, owner, item['id']))['originalSource']
    correction = source(app, owner, 'Also retain paused workers; do not infer completion from idle.')
    changed = (await revise(app, owner, item, 'correct', source=correction,
        interpretation='Retain the promise for paused workers too.',
        criteria=[{'id': 'retained', 'text': 'Cold reopen retains active and paused-worker promises.'}]))['result']['item']
    assert changed['promiseRevision'] == 2 and changed['revision'] == 2
    assert (await read(app, owner, item['id']))['originalSource'] == original
    with pytest.raises(AppError):
        await revise(app, owner, item, 'late-edit', nextAction='Overwrite the correction')
    history = (await app.dispatch('workspace.work.history', {
        'workspaceId': workspace_id(app, owner), 'id': item['id']}))['result']
    assert any(row.get('source', {}).get('messageId') == correction['messageId'] for row in history['items'])
    change = next(row for row in history['items'] if row.get('source', {}).get('messageId') == correction['messageId'])
    ref = change['sourceRef']
    retained = await app.dispatch(ref['action'], ref['args'])
    assert retained['result']['source']['text'] == 'Also retain paused workers; do not infer completion from idle.'
    assert (await read(app, owner, item['id']))['item']['promiseRevision'] == 2


async def test_concurrent_edits_have_one_winner_without_silent_loss(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    replies = await asyncio.gather(
        revise(app, owner, item, 'edit-one', nextAction='Check source'),
        revise(app, owner, item, 'edit-two', nextAction='Check browser'),
        return_exceptions=True)
    assert sum(isinstance(r, dict) for r in replies) == 1
    assert sum(isinstance(r, AppError) and r.status == 409 for r in replies) == 1
    current = (await read(app, owner, item['id']))['item']
    assert current['revision'] == 2 and current['promiseRevision'] == 1
    assert current['nextAction'] in {'Check source', 'Check browser'}


async def test_cold_list_and_detail_do_not_hydrate_or_start_workers(app, monkeypatch):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    wid = workspace_id(app, owner)
    app._save()
    await app.close()
    runtime = Runtime()
    restored = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    runtime.app = restored
    try:
        before = {row['id'] for row in restored.state['sessions']}
        def forbidden(*args, **kwargs):
            pytest.fail('Work inspection hydrated history or admitted a worker')
        monkeypatch.setattr(restored.history, 'ensure_loaded', forbidden)
        monkeypatch.setattr(restored.cold_display, 'hydrate', forbidden)
        if restored.management:
            monkeypatch.setattr(restored.management, 'ensure_runtime', forbidden)
        listing = (await restored.dispatch('workspace.work.list', {'workspaceId': wid}))['result']
        detail = (await restored.dispatch('workspace.work.read', {'workspaceId': wid, 'id': item['id']}))['result']
        assert listing['items'][0]['id'] == detail['item']['id'] == item['id']
        assert detail['originalSource']['text'] == 'Keep this request after the worker stops.'
        assert {row['id'] for row in restored.state['sessions']} == before
        assert runtime.inputs == []
    finally:
        await restored.close()


async def test_independent_peer_can_read_but_cannot_revise_or_impersonate(app):
    owner, peer = app.state['sessions']
    item = (await create(app, owner))['result']['item']
    await generation(app, peer, 'peer-generation', ['peer-input'])
    args = {'workspaceId': workspace_id(app, owner), 'id': item['id']}
    observed = await agent_action(app, peer, 'workspace.work.read', args, 'read-peer')
    assert observed['result']['item']['id'] == item['id']
    with pytest.raises(AppError):
        await revise(app, peer, item, 'unauthorized-revision', nextAction='Take over')
    with pytest.raises(AppError):
        await agent_action(app, peer, 'workspace.work.revise', {
            **args, 'sessionId': owner['id'], 'expectedRevision': 1,
            'expectedPromiseRevision': 1, 'nextAction': 'Borrow owner'}, 'borrow-owner')
    assert (await read(app, owner, item['id']))['item']['revision'] == 1
    assert app.assignments.current(peer) is None


async def test_open_items_page_without_silent_omission_and_reject_mixed_versions(app):
    owner = app.state['sessions'][0]
    identities = set()
    for index in range(21):
        row = await create(app, owner, command=f'capture-{index}', intent=f'promise-{index}')
        identities.add(row['result']['item']['id'])
    args = {'workspaceId': workspace_id(app, owner), 'limit': 20}
    first = (await app.dispatch('workspace.work.list', args))['result']
    assert first['total'] == 21 and first['truncated'] and first['nextAfter']
    second = (await app.dispatch('workspace.work.list', {**args, 'after': first['nextAfter']}))['result']
    assert {r['id'] for r in first['items'] + second['items']} == identities
    assert len(first['items']) + len(second['items']) == 21
    assert not second['nextAfter']
    await create(app, owner, command='capture-new', intent='promise-new')
    with pytest.raises(AppError):
        await app.dispatch('workspace.work.list', {**args, 'after': first['nextAfter']})


async def test_context_marks_overflow_without_copying_source_or_correction_bodies(app):
    owner = app.state['sessions'][0]
    for index in range(21):
        await create(app, owner, source(app, owner, 'Private source body ' + str(index)),
            command=f'capture-{index}', intent=f'promise-{index}')
    result = (await agent_action(app, owner, 'workspace.work.context', {
        'sessionId': owner['id']}, 'context-read'))['result']
    encoded = json.dumps(result, ensure_ascii=False).encode()
    assert len(encoded) <= 12 * 1024
    assert result['total'] == 21 and result['truncated'] and result['nextAfter']
    assert len(result['items']) <= 20
    assert b'Private source body' not in encoded
    assert all(row['id'] and row['readRef'] and row['promiseRevision'] for row in result['items'])


@pytest.mark.parametrize('verb', ['list', 'read', 'source', 'history', 'context'])
async def test_child_cannot_borrow_root_for_passive_work_reads(app, verb):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    detail = await read(app, owner, item['id'])
    args = {'workspaceId': workspace_id(app, owner)}
    if verb in {'read', 'source', 'history'}:
        args['id'] = item['id']
    if verb == 'source':
        args['sourceId'] = detail['item']['originalSourceRef']
    if verb == 'context':
        args = {'sessionId': owner['id']}
    with pytest.raises(AppError, match='child'):
        await app.app_bridge('dispatch', {'action': 'workspace.work.' + verb, 'args': args,
            '_runtimeSessionId': 'native-child', '_generationId': owner['collaborationGeneration']['id']}, owner['id'])


@pytest.mark.parametrize('sequence', [-1, 2**63, True, '1'])
async def test_history_cursor_range_is_a_bounded_domain_error(app, sequence):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    wid = workspace_id(app, owner)
    cursor = base64.urlsafe_b64encode(json.dumps([wid, item['id'], sequence]).encode()).decode()
    with pytest.raises(AppError, match='cursor'):
        await app.dispatch('workspace.work.history', {'workspaceId': wid, 'id': item['id'], 'after': cursor})


@pytest.mark.parametrize('native_input,retained_input', [('peer-input', 'human-input'), (None, 'human-input')])
async def test_native_source_never_inherits_human_origin_from_index_only(app, monkeypatch, native_input, retained_input):
    owner = app.state['sessions'][0]
    owner['nativeProject'] = 'synthetic-native-project'
    app._message(owner, 'user', 'Same text does not prove identity.', inputOrigin='ui',
                 inputId=retained_input, nativeIndex=7)
    native = {'id': 'native-seven', 'role': 'user', 'text': 'Same text does not prove identity.',
              'nativeInputId': native_input, 'nativeIndex': 7}
    import amplifier_web.automatic_history as history
    monkeypatch.setattr(history, 'read_transcript', lambda *a, **k: {'messages': [native]})
    async def no_load(*a, **k):
        return None
    monkeypatch.setattr(app.history, 'ensure_loaded', no_load)
    with pytest.raises(AppError):
        await create(app, owner, {'sessionId': owner['id'], 'messageId': 'native-seven'})


async def test_stop_blocks_mutation_but_not_passive_read(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    await app.dispatch('conversation.stop', {'sessionId': owner['id']}, command_id='stop-owner')
    assert (await read(app, owner, item['id']))['item']['disposition'] == 'open'
    with pytest.raises(AppError):
        await revise(app, owner, item, 'after-stop', nextAction='Resume without authority')


async def change_work(app, actor, verb, item, identity, **extra):
    return await agent_action(app, actor, 'workspace.work.' + verb, {
        'workspaceId': workspace_id(app, actor), 'sessionId': actor['id'],
        'id': item['id'], 'expectedRevision': item['revision'], **extra}, identity)


async def test_return_is_unverified_and_cannot_close_or_claim_human_acceptance(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    reported = (await change_work(app, owner, 'report', item, 'report-result',
        promiseRevision=1, candidate='a' * 40, outcome='passed',
        report='Worker says it passed.', criteria=['retained']))['result']['item']
    assert reported['verification'] == 'not_verified'
    assert reported['execution'] == 'reported'
    assert reported['humanDecision'] == reported['delivery'] == 'not_recorded'
    with pytest.raises(AppError):
        await change_work(app, owner, 'disposition', reported, 'false-close',
            disposition='closed', reason='Worker said success', basis='satisfied')
    with pytest.raises(AppError):
        await change_work(app, owner, 'decision', reported, 'false-human',
            decision='accepted', candidate='a' * 40, promiseRevision=1)


async def test_links_do_not_enroll_or_start_peer_and_stale_return_keeps_original_scope(app):
    owner, peer = app.state['sessions']
    item = (await create(app, owner))['result']['item']
    item = (await change_work(app, owner, 'link', item, 'link-peer',
        linkedSessionId=peer['id'], role='executor', candidate='a' * 40))['result']['item']
    assert app.assignments.current(peer) is None and app.runtime.inputs == []
    await generation(app, peer, 'peer-execution', ['linked-input'])
    correction = source(app, owner, 'Include a cold reopen.')
    item = (await revise(app, owner, item, 'scope-v2', source=correction,
        criteria=[{'id': 'retained', 'text': 'The exact source survives two cold reopens.'}]))['result']['item']
    item = (await change_work(app, peer, 'report', item, 'late-report',
        promiseRevision=1, candidate='a' * 40, outcome='passed',
        report='Finished the old revision.', criteria=['retained']))['result']['item']
    assert item['promiseRevision'] == 2 and item['verification'] == 'not_verified'
    assert item['execution'] != 'reported'
    history = (await app.dispatch('workspace.work.history', {
        'workspaceId': workspace_id(app, owner), 'id': item['id']}))['result']
    late = next(row for row in history['items'] if row['kind'] == 'report')
    assert late['promiseRevision'] == 1
    assert late['evidence']['qualification'] == 'participant_report'


async def test_exact_check_receipt_and_distinct_root_are_required_for_verified_closure(app):
    owner, reviewer = app.state['sessions']
    item = (await create(app, owner))['result']['item']
    candidate = 'b' * 40
    item = (await change_work(app, owner, 'link', item, 'link-builder',
        linkedSessionId=owner['id'], role='executor', candidate=candidate))['result']['item']
    item = (await change_work(app, owner, 'link', item, 'link-reviewer',
        linkedSessionId=reviewer['id'], role='reviewer', candidate=candidate))['result']['item']
    await generation(app, reviewer, 'reviewer-turn', ['review-input'])
    # Synthetic canonical collection fixture; not evidence about any real build.
    collection = {'commandAction': 'integration.collect', 'result': {
        'id': 'collection-one', 'kind': 'collection', 'sessionId': reviewer['id'],
        'capturedAt': time.time(), 'captureId': 'candidate-capture', 'validationIds': ['validation-one'],
        'status': 'passed', 'candidateStatus': 'matched',
        'repositories': [{'repositoryPath': '/synthetic/repo', 'status': 'passed',
                          'candidateStatus': 'matched', 'resultingSource': {'head': candidate, 'dirty': False},
                          'artifacts': [{'status': 'retained', 'sha256': 'c' * 64, 'outputId': 'test-output'}],
                          'operation': {'state': 'completed', 'returncode': 0}}]}}
    app.collaboration.insert('collection-one', 'fixture', collection)
    app.collaboration.insert('validation-one', 'validation-fixture', {
        'commandAction': 'integration.validate', 'result': {
            'sessionId': reviewer['id'], 'captureId': 'candidate-capture', 'capturedAt': time.time()}})
    app.db.commit()
    with pytest.raises(AppError):
        await change_work(app, reviewer, 'report', item, 'wrong-candidate',
            promiseRevision=1, candidate='d' * 40, outcome='passed', report='Wrong source',
            criteria=['retained'], checkReceiptId='collection-one')
    checked = (await change_work(app, reviewer, 'report', item, 'verified',
        promiseRevision=1, candidate=candidate, outcome='passed', report='Checked exact source',
        criteria=['retained'], checkReceiptId='collection-one'))['result']['item']
    assert checked['verification'] == 'independent_checks_passed'
    assert checked['humanDecision'] == checked['delivery'] == 'not_recorded'
    closed = (await change_work(app, owner, 'disposition', checked, 'close',
        disposition='closed', reason='Defined criteria checked.', basis='satisfied'))['result']['item']
    reopened = (await change_work(app, owner, 'disposition', closed, 'reopen',
        disposition='open', reason='New counterevidence needs inspection.', basis='reopened'))['result']['item']
    assert reopened['disposition'] == 'open' and reopened['verification'] == 'not_verified'
    history = (await app.dispatch('workspace.work.history', {
        'workspaceId': workspace_id(app, owner), 'id': item['id']}))['result']['items']
    assert len([row for row in history if row['kind'] == 'disposition']) == 2
    corrected = (await revise(app, owner, reopened, 'correct-after-check',
        source=source(app, owner, 'Now require a second cold reopen.'),
        criteria=[{'id': 'retained', 'text': 'Two cold reopens preserve the source.'}]))['result']['item']
    corrected = (await change_work(app, owner, 'link', corrected, 'review-new-promise',
        linkedSessionId=reviewer['id'], role='reviewer', candidate=candidate))['result']['item']
    with pytest.raises(AppError, match='predates'):
        await change_work(app, reviewer, 'report', corrected, 'reuse-old-check',
            promiseRevision=2, candidate=candidate, outcome='passed', report='Reuse old proof',
            criteria=['retained'], checkReceiptId='collection-one')
    assert (await read(app, owner, item['id']))['item']['verification'] == 'not_verified'


async def test_contributor_context_uses_work_links_without_assignment_enrollment(app):
    owner, peer = app.state['sessions']
    item = (await create(app, owner))['result']['item']
    await change_work(app, owner, 'link', item, 'contributor', linkedSessionId=peer['id'],
                      role='executor', candidate='a' * 40)
    await generation(app, peer, 'contributor-turn', ['contributor-input'])
    result = await agent_action(app, peer, 'workspace.work.context', {'sessionId': peer['id']}, 'context')
    assert result['result']['total'] == 1
    assert result['result']['items'][0]['id'] == item['id']
    assert app.assignments.current(peer) is None and app.runtime.inputs == []


async def test_claim_reassignment_fences_linked_work_report(app):
    from test_assignments import child
    from test_assignment_tasks import create as create_task, change as change_task
    owner = app.state['sessions'][0]
    peer, _ = await child(app, owner, 'execution-peer')
    task = await create_task(app, owner)
    claimed = (await change_task(app, peer, 'claim', task, agent=True))['result']['task']
    item = (await create(app, owner))['result']['item']
    item = (await change_work(app, owner, 'link', item, 'claim-link',
        linkedSessionId=peer['id'], role='executor', candidate='a' * 40,
        taskId=task['id'], claimId=claimed['claim']['id']))['result']['item']
    await change_task(app, owner, 'reassign', claimed, holderSessionId=owner['id'])
    with pytest.raises(AppError, match='responsibility'):
        await change_work(app, peer, 'report', item, 'old-claim-return',
            promiseRevision=1, candidate='a' * 40, outcome='passed',
            report='Late stale holder', criteria=['retained'])
    assert (await read(app, owner, item['id']))['item']['verification'] == 'not_verified'


async def test_dependency_cycle_and_cancelled_dependency_are_not_ready(app):
    owner = app.state['sessions'][0]
    first = (await create(app, owner, command='first', intent='first'))['result']['item']
    second = (await create(app, owner, command='second', intent='second', dependsOn=[first['id']]))['result']['item']
    assert second['blockedBy'] == [first['id']]
    with pytest.raises(AppError, match='cycle'):
        await revise(app, owner, first, 'cycle', dependsOn=[second['id']])
    await change_work(app, owner, 'disposition', first, 'cancel-first',
        disposition='closed', basis='cancelled', reason='The requested work was withdrawn.')
    listed = (await app.dispatch('workspace.work.list', {'workspaceId': workspace_id(app, owner)}))['result']
    assert next(row for row in listed['items'] if row['id'] == second['id'])['blockedBy'] == [first['id']]
    assert not app.runtime.inputs


async def test_transaction_failure_preserves_current_record_and_no_partial_event(app, monkeypatch):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    before = await read(app, owner, item['id'])
    original = app.collaboration.insert
    def fail_final(identity, digest, receipt):
        if identity == 'fail-commit':
            raise OSError('Synthetic final receipt failure')
        return original(identity, digest, receipt)
    monkeypatch.setattr(app.collaboration, 'insert', fail_final)
    with pytest.raises(OSError, match='Synthetic'):
        await revise(app, owner, item, 'fail-commit', nextAction='Must roll back')
    assert (await read(app, owner, item['id'])) == before
    events = (await app.dispatch('workspace.work.history', {
        'workspaceId': workspace_id(app, owner), 'id': item['id']}))['result']
    assert events['total'] == 1 and len(events['items']) == 1
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='fail-commit'").fetchone()


async def test_index_loss_does_not_lose_work_or_replay_capture(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    for name in ('work_current_scope', 'work_source_intent', 'work_history_scope'):
        app.db.execute('DROP INDEX ' + name)
    app.db.commit()
    from amplifier_web.workspace_work import WorkspaceWork
    rebuilt = WorkspaceWork(app)
    page = rebuilt.listing({'id': workspace_id(app, owner)}, {})
    assert page['total'] == 1 and page['items'][0]['id'] == item['id']
    assert not app.runtime.inputs


async def test_large_synthetic_work_set_is_bounded_and_all_open_items_are_reachable(app):
    from amplifier_operations.coordination import fingerprint
    owner = app.state['sessions'][0]
    first = (await create(app, owner))['result']['item']
    template = (await read(app, owner, first['id']))['item']
    ids = {first['id']}
    # Synthetic retained records isolate the indexed read from capture throughput.
    # Source capture itself is tested through the action path above.
    for index in range(999):
        item = {**copy.deepcopy(template), 'id': f'work:synthetic-{index:04d}',
                'sourceKey': f'synthetic-source-{index}'}
        ids.add(item['id'])
        app.collaboration.insert(item['id'], fingerprint(item), {
            'commandAction': 'workspace.work.record', 'result': item})
    app.db.commit()
    observed = []
    args = {'sessionId': owner['id']}
    while True:
        page = (await agent_action(app, owner, 'workspace.work.context', args, 'read-context'))['result']
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 12 * 1024
        assert len(page['items']) <= 20 and page['total'] == 1000
        observed.extend(row['id'] for row in page['items'])
        if not page['nextAfter']:
            break
        assert page['truncated']
        args['after'] = page['nextAfter']
    assert len(observed) == len(set(observed)) == 1000 and set(observed) == ids


async def test_uncaptured_input_coverage_is_not_a_task_inventory_or_automatic_import(app):
    owner = app.state['sessions'][0]
    wid = workspace_id(app, owner)
    original = source(app, owner, 'Please retain the newly accepted request.')
    source(app, owner, 'Thanks; this is ordinary conversation.')
    source(app, owner, 'Peer role=user is not a human request.', origin='peer')
    before = (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']
    assert before['observedInputs'] == before['unreviewedInputs'] == 2
    assert before['historicalCoverage'] == 'incomplete'
    assert (await app.dispatch('workspace.work.list', {'workspaceId': wid}))['result']['total'] == 0
    await create(app, owner, original)
    remaining = (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']
    assert remaining['observedInputs'] == 2 and remaining['unreviewedInputs'] == 1
    row = remaining['items'][0]
    await agent_action(app, owner, 'workspace.work.reviewInput', {
        'workspaceId': wid, 'sessionId': owner['id'], 'inputId': row['id'],
        'sourceSha256': row['sourceSha256'], 'disposition': 'ordinary_conversation',
        'reason': 'Acknowledgment only; no new request identified.'}, 'review-input')
    final = (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']
    assert final['unreviewedInputs'] == 0 and final['historicalCoverage'] == 'incomplete'
    assert not app.runtime.inputs


async def test_coverage_survives_restart_without_hydration_or_peer_review_authority(app, monkeypatch):
    owner, peer = app.state['sessions']
    wid = workspace_id(app, owner)
    original = source(app, owner, 'An uncaptured source must remain visible.')
    page = (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']
    row = page['items'][0]
    await generation(app, peer, 'peer-review', ['peer-review-input'])
    with pytest.raises(AppError):
        await agent_action(app, peer, 'workspace.work.reviewInput', {
            'workspaceId': wid, 'sessionId': peer['id'], 'inputId': row['id'],
            'sourceSha256': row['sourceSha256'], 'disposition': 'ordinary_conversation',
            'reason': 'Pretend this was reviewed by its source owner.'}, 'foreign-review')
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        def forbidden(*a, **k):
            pytest.fail('Coverage hydrated history')
        monkeypatch.setattr(restored.history, 'ensure_loaded', forbidden)
        page = (await restored.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']
        assert page['unreviewedInputs'] == 1 and page['items'][0]['messageId'] == original['messageId']
    finally:
        await restored.close()


async def test_host_voice_origin_remains_human_without_promoting_peer_role(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner, source(app, owner, origin='voice')))['result']['item']
    assert (await read(app, owner, item['id']))['originalSource']['inputOrigin'] == 'voice'


async def test_ui_decision_is_retained_but_new_failure_prevents_satisfied_closure(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    item = (await change_work(app, owner, 'report', item, 'ui-source-candidate',
        promiseRevision=1, candidate='a' * 40, outcome='passed',
        report='First return', criteria=['retained']))['result']['item']
    item = (await app.dispatch('workspace.work.decision', {
        'workspaceId': workspace_id(app, owner), 'sessionId': owner['id'],
        'id': item['id'], 'expectedRevision': item['revision'], 'promiseRevision': 1,
        'candidate': 'a' * 40, 'decision': 'accepted'}, command_id='actual-ui-acceptance'))['result']['item']
    assert item['humanDecision'] == 'accepted' and item['humanDecisionCurrent']
    item = (await change_work(app, owner, 'report', item, 'later-counterevidence',
        promiseRevision=1, candidate='a' * 40, outcome='failed',
        report='A later check failed.', criteria=['retained']))['result']['item']
    assert item['humanDecision'] == 'accepted' and not item['humanDecisionCurrent']
    assert item['latestOutcome'] == 'failed'
    assert 'counterevidence_after_decision' in item['attentionReasons']
    with pytest.raises(AppError, match='closure'):
        await change_work(app, owner, 'disposition', item, 'close-with-old-decision',
            disposition='closed', basis='satisfied', reason='Old acceptance is not current.')
    # A new explicit human decision is different from silently preserving old approval.
    item = (await app.dispatch('workspace.work.decision', {
        'workspaceId': workspace_id(app, owner), 'sessionId': owner['id'],
        'id': item['id'], 'expectedRevision': item['revision'], 'promiseRevision': 1,
        'candidate': 'a' * 40, 'decision': 'accepted'}, command_id='new-ui-decision'))['result']['item']
    closed = await change_work(app, owner, 'disposition', item, 'close-after-current-human',
        disposition='closed', basis='satisfied', reason='The human explicitly decided after the retained failure.')
    assert closed['result']['item']['disposition'] == 'closed'
    assert closed['result']['item']['latestOutcome'] == 'failed'


async def test_history_paging_keeps_immutable_initial_high_water_and_reads_prior_criteria(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    item = (await revise(app, owner, item, 'metadata-change', nextAction='Inspect history'))['result']['item']
    args = {'workspaceId': workspace_id(app, owner), 'id': item['id'], 'limit': 1}
    first = (await app.dispatch('workspace.work.history', args))['result']
    await revise(app, owner, item, 'newer-change', nextAction='Still inspect old snapshot')
    second = (await app.dispatch('workspace.work.history', {**args, 'after': first['nextAfter']}))['result']
    assert second['snapshotEventCount'] == 2 and second['currentEventCount'] == 3
    assert second['snapshotIsCurrent'] is False and second['nextAfter'] is None
    event = first['items'][0]['eventRef']
    original = (await app.dispatch(event['action'], event['args']))['result']['event']
    assert original['changes']['criteria'][0]['text'] == 'The exact request survives a cold reopen.'


async def test_coverage_facts_commit_with_source_save_not_unrelated_transaction(app):
    owner = app.state['sessions'][0]
    wid = workspace_id(app, owner)
    app._message(owner, 'user', 'A pending source fact.', inputOrigin='ui')
    app.db.rollback()
    before = (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']
    assert before['observedInputs'] == 0
    app._save()
    after = (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']
    assert after['observedInputs'] == 1


async def test_resource_report_stays_owner_attributed_and_never_implies_teardown(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    assert item['cleanup']['status'] == 'not_recorded'
    item = (await change_work(app, owner, 'resource', item, 'resource-unknown',
        resourceId='synthetic-operation-one', kind='container',
        reportedState='unknown', evidenceReference='retained-original-request'))['result']['item']
    cleanup = item['cleanup']['references'][0]
    assert cleanup['ownerSessionId'] == owner['id'] and cleanup['attribution'] == 'owner_report'
    assert cleanup['reportedState'] == 'unknown' and cleanup['independentlyVerified'] is False
    assert not app.runtime.inputs


async def test_attention_filter_and_wait_use_concrete_current_observations(app):
    owner = app.state['sessions'][0]
    original = source(app, owner, origin='agent')
    await create(app, owner, original, command='proposal-only', commitment='proposed')
    item = (await create(app, owner, command='accepted-no-attention'))['result']['item']
    args = {'workspaceId': workspace_id(app, owner), 'filter': 'attention'}
    empty = (await app.dispatch('workspace.work.list', args))['result']
    assert empty['total'] == 0
    before = copy.deepcopy(app.runtime.inputs)
    waiting = asyncio.create_task(app.dispatch('workspace.work.wait', {
        **args, 'afterVersion': empty['observationVersion'], 'waitMs': 1000}))
    await asyncio.sleep(0)
    await revise(app, owner, item, 'needs-check', nextAction='Check exact candidate.')
    changed = await waiting
    assert changed['changed'] and changed['result']['items'][0]['id'] == item['id']
    assert changed['result']['items'][0]['attentionReasons'] == ['next_action']
    assert app.runtime.inputs == before


async def test_attention_page_rejects_changed_owner_membership_without_work_edits(app):
    owner, peer = app.state['sessions']
    await create(app, owner, command='first-attention', nextAction='Inspect this one.')
    await create(app, owner, command='second-attention', intent='second', nextAction='Inspect this too.')
    await generation(app, peer, 'other-owner', ['other-input'])
    await create(app, peer, command='other-owned-work')
    args = {'workspaceId': workspace_id(app, owner), 'filter': 'attention', 'limit': 1}
    first = (await app.dispatch('workspace.work.list', args))['result']
    peer['status'] = 'stopped'  # Host observation changes attention, not the Work record.
    with pytest.raises(AppError, match='attention changed'):
        await app.dispatch('workspace.work.list', {**args, 'after': first['nextAfter']})


async def test_cross_root_source_dedup_retains_origin_owner_and_no_permission_transfer(app):
    owner, peer = app.state['sessions']
    original = source(app, owner)
    first = (await create(app, owner, original))['result']['item']
    await generation(app, peer, 'peer-capture', ['peer-input'])
    same = await create(app, peer, original, command='peer-capture')
    assert same['sourceDuplicate']
    assert same['result']['item']['id'] == first['id']
    assert same['result']['item']['ownerSessionId'] == owner['id']
    assert same['result']['item']['originSessionId'] == owner['id']
    with pytest.raises(AppError, match='owner'):
        await revise(app, peer, first, 'dedup-is-not-custody', nextAction='Take over')


async def test_ui_proposal_to_accepted_and_exact_long_source_pages(app):
    owner = app.state['sessions'][0]
    wid = workspace_id(app, owner)
    proposal = source(app, owner, 'An agent suggestion', origin='agent')
    item = (await app.dispatch('workspace.work.create', {
        'workspaceId': wid, 'sessionId': owner['id'], 'source': proposal,
        'intentKey': 'ui-proposal', 'title': 'UI proposal', 'commitment': 'proposed',
        'criteria': []}, command_id='ui-create'))['result']['item']
    text = 'Exact Unicode source: \u03bb\n' * 600
    human = source(app, owner, text)
    item = (await app.dispatch('workspace.work.revise', {
        'workspaceId': wid, 'sessionId': owner['id'], 'id': item['id'],
        'expectedRevision': item['revision'], 'expectedPromiseRevision': 1,
        'source': human, 'commitment': 'accepted',
        'criteria': [{'id': 'source', 'text': 'Preserve full exact source.'}]},
        command_id='ui-accept-scope'))['result']['item']
    history = (await app.dispatch('workspace.work.history', {'workspaceId': wid, 'id': item['id']}))['result']
    correction = next(row for row in history['items'] if row['kind'] == 'revise')['sourceRef']
    first = (await app.dispatch(correction['action'], correction['args']))['result']['source']
    second = (await app.dispatch(correction['action'], {**correction['args'], 'offset': first['nextOffset']}))['result']['source']
    assert first['text'] + second['text'] == text
    assert first['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert item['commitment'] == 'accepted' and item['humanDecision'] == 'not_recorded'


async def test_failed_source_save_keeps_pending_coverage_for_successful_same_source_save(app, monkeypatch):
    import amplifier_web.state_records as records
    owner = app.state['sessions'][0]
    wid = workspace_id(app, owner)
    app._message(owner, 'user', 'Source must commit with its coverage fact.', inputOrigin='ui')
    original = records.save_working
    monkeypatch.setattr(records, 'save_working', lambda *a, **k: (_ for _ in ()).throw(OSError('Synthetic source-save failure')))
    with pytest.raises(OSError):
        app._save()
    assert (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']['observedInputs'] == 0
    monkeypatch.setattr(records, 'save_working', original)
    app._save()
    assert (await app.dispatch('workspace.work.coverage', {'workspaceId': wid}))['result']['observedInputs'] == 1
