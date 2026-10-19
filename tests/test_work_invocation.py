"""Proposed commissioned-input Work association contract: authored, not executed.

Use the real AppService, receipt transactions and existing synthetic transport
fixtures. These tests do not pretend to be native worker, tool:pre, nested-tool or
post-truncation delivery acceptance. No successful association is fabricated.
"""
import asyncio
import copy
import hashlib
import json
import sqlite3

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, Runtime, agent_action, generation, until
from test_peer_creation_operation import commission, initial_accepted, plan
from test_workspace_work import create, change_work, read, revise, source, workspace_id


async def prepared_work(app, owner, identity, **content):
    item = (await create(app, owner, command='capture-' + identity,
                         intent=identity, **content))['result']['item']
    return (await change_work(app, owner, 'link', item, 'builder-' + identity,
        linkedSessionId=owner['id'], role='executor', candidate='b' * 40))['result']['item']


def reference(item, **patch):
    return {'workId': item['id'], 'expectedRevision': item['revision'],
            'promiseRevision': item['promiseRevision'], 'candidate': item['candidate'],
            'role': 'executor', **patch}


def transport(actor, *, inputs=None, generation_id=None, principal=None):
    current = actor['collaborationGeneration']
    return {'_runtimeSessionId': principal or actor.get('runtimeSessionId') or actor['id'],
            '_generationId': generation_id or current['id'],
            '_inputBindings': [{'inputId': identity, 'clientId': None}
                               for identity in (current.get('inputIds', []) if inputs is None else inputs)]}


async def current_work(app, actor, **binding):
    value = await app.app_bridge('coordination.current', transport(actor, **binding), actor['id'])
    return value['work']


async def revalidate(app, actor, version):
    return await app.app_bridge('work.invocation.revalidate', {
        'version': version, **transport(actor)}, actor['id'])


async def commissioned(app, owner, item, identity='bound-commission'):
    plan(app, owner)
    created = await commission(app, owner, identity=identity,
                               workReferences=[reference(item)], returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    child = app._session(created['sessionId'])
    await generation(app, child, 'generation-' + identity, [created['initialInputId']])
    return created, child


async def test_commission_seals_work_association_before_native_admission(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'bound')
    original = app.runtime.collaboration_input
    observed = []

    async def inspect_transaction(session, args, guard, emit):
        # A second connection sees only committed host receipts.
        with sqlite3.connect(app.db.execute('PRAGMA database_list').fetchone()[2]) as db:
            creation = json.loads(db.execute(
                "SELECT receipt FROM commands WHERE id='bound-commission'").fetchone()[0])
            request = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                           (args['inputId'],)).fetchone()[0])
            association = creation['workAssociation']
            assert association['version'] == 1
            assert association['requestId'] == creation['requestId']
            assert association['initialInputId'] == args['inputId']
            assert association['targetRootId'] == session['id']
            assert association['sourceRootId'] == owner['id']
            assert request['workAssociationRef']['requestId'] == creation['requestId']
            assert request['workAssociationRef']['version'] == 1
            bound = association['references'][0]
            assert bound['workId'] == item['id'] and bound['promiseRevision'] == 1
            assert bound['candidate'] == item['candidate']
            event = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                         (bound['linkEventId'],)).fetchone()[0])
            assert event['commandAction'] == 'workspace.work.event'
            assert event['workItemId'] == item['id']
            assert event['link']['sessionId'] == session['id']
            record = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                          (item['id'],)).fetchone()[0])['result']
            assert record['ownerSessionId'] == owner['id']
            assert any(link['sessionId'] == session['id'] for link in record['links'])
            observed.append(association['requestId'])
        return await original(session, args, guard, emit)

    app.runtime.collaboration_input = inspect_transaction
    created, child = await commissioned(app, owner, item)
    assert observed == [created['requestId']]
    repeated = await commission(app, owner, identity='bound-commission',
                                workReferences=[reference(item)], returnNotification=False)
    assert repeated['duplicate']
    assert len(app.runtime.inputs) == 1 and len(observed) == 1
    assert not (await current_work(app, child))['required']['effectAuthorized']


async def test_required_set_is_delivered_association_not_all_owned_work(app, monkeypatch):
    owner = app.state['sessions'][0]
    first = await prepared_work(app, owner, 'governing')
    unrelated = await prepared_work(app, owner, 'unrelated')
    created, child = await commissioned(app, owner, first)
    # A contributor relationship alone must not replace invocation provenance.
    await change_work(app, owner, 'link', unrelated, 'unrelated-contribution',
                     linkedSessionId=child['id'], role='reviewer', candidate=unrelated['candidate'])
    calls = copy.deepcopy(app.runtime.inputs)
    def forbidden(*args, **kwargs):
        pytest.fail('Invocation inspection hydrated history or started a worker')
    monkeypatch.setattr(app.history, 'ensure_loaded', forbidden)
    monkeypatch.setattr(app, 'ensure_runtime', forbidden)
    work = await current_work(app, child)
    assert work['available']
    assert {row['id'] for row in work['discovery']['items']} == {first['id'], unrelated['id']}
    required = work['required']
    assert required['status'] == 'available' and required['workCount'] == 1
    assert required['associationRequestIds'] == [created['requestId']]
    page = (await agent_action(app, child, 'workspace.work.invocation', {
        'sessionId': child['id'], 'expectedVersion': required['version']}, 'manifest'))['result']
    assert {row['workId'] for row in page['items']} == {first['id']}
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 12 * 1024
    assert app.runtime.inputs == calls


@pytest.mark.parametrize('patch', [
    {'expectedRevision': 999}, {'promiseRevision': 999}, {'candidate': 'c' * 40},
    {'workId': 'work:missing'}, {'role': 'administrator'}, {'hostObserved': True},
])
async def test_commission_rejects_unresolved_or_caller_forged_work_reference(app, patch):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'guarded')
    plan(app, owner)
    before = await read(app, owner, item['id'])
    count = len(app.state['sessions'])
    with pytest.raises(AppError):
        await commission(app, owner, identity='invalid-work-commission',
                         workReferences=[reference(item, **patch)])
    assert len(app.state['sessions']) == count and not app.runtime.inputs
    assert await read(app, owner, item['id']) == before
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='invalid-work-commission'").fetchone()


async def test_linked_nonowner_cannot_commission_as_work_owner(app):
    owner, peer = app.state['sessions']
    item = await prepared_work(app, owner, 'owned')
    item = (await change_work(app, owner, 'link', item, 'peer-reviewer',
        linkedSessionId=peer['id'], role='reviewer', candidate=item['candidate']))['result']['item']
    await generation(app, peer, 'peer-generation', ['peer-input'])
    plan(app, peer)
    before = await read(app, owner, item['id'])
    with pytest.raises(AppError, match='owner'):
        await commission(app, peer, identity='not-owner-commission', workReferences=[reference(item)])
    assert await read(app, owner, item['id']) == before and not app.runtime.inputs


@pytest.mark.parametrize('change', ['promise', 'source_stop'])
async def test_creation_revalidates_work_after_await_before_initial_input(app, monkeypatch, change):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'changing-before-input')
    plan(app, owner)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.collaboration._finish_create
    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(app.collaboration, '_finish_create', delayed)
    created = await commission(app, owner, identity='delayed-work-commission',
                               workReferences=[reference(item)], returnNotification=False)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if change == 'source_stop':
            await app.dispatch('conversation.stop', {'sessionId': owner['id']},
                               command_id='stop-during-work-creation')
        else:
            await revise(app, owner, item, 'scope-before-initial-input',
                         source=source(app, owner, 'The promise changed before admission.'),
                         constraints=['New current condition.'])
    finally:
        release.set()
    await until(lambda: app.collaboration.receipt(created['requestId'])['delivery']
                in {'suppressed', 'unknown'})
    retained = app.collaboration.receipt(created['requestId'])
    assert retained['delivery'] == 'suppressed'
    assert not app.runtime.inputs
    initial = app.db.execute('SELECT receipt FROM commands WHERE id=?',
                             (created['initialInputId'],)).fetchone()
    if initial:
        assert json.loads(initial[0]).get('delivery') not in {'accepted', 'applied', 'submitting'}
    assert retained['requestId'] == created['requestId']  # No replacement request.


async def test_duplicate_work_references_do_not_create_duplicate_association(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'duplicate')
    plan(app, owner)
    before = await read(app, owner, item['id'])
    with pytest.raises(AppError, match='distinct|duplicate'):
        await commission(app, owner, identity='duplicate-work-references',
                         workReferences=[reference(item), reference(item)])
    assert await read(app, owner, item['id']) == before
    assert not app.runtime.inputs


@pytest.mark.parametrize('binding', [
    {'principal': 'synthetic-child'}, {'generation_id': 'old-generation'},
])
async def test_invocation_read_rejects_borrowed_or_stale_root_binding(app, binding):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'bound')
    _, child = await commissioned(app, owner, item)
    inputs = copy.deepcopy(app.runtime.inputs)
    with pytest.raises(AppError):
        await app.app_bridge('dispatch', {'action': 'workspace.work.invocation',
            'args': {'sessionId': child['id']}, **transport(child, **binding)}, child['id'])
    assert app.runtime.inputs == inputs


async def test_transport_input_not_observed_delivered_cannot_select_relevant_work(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'bound')
    _, child = await commissioned(app, owner, item)
    required = (await current_work(app, child, inputs=['not-delivered']))['required']
    assert required['status'] == 'unavailable'
    assert required['reason'] and required['version'] is None
    assert required['effectAuthorized'] is False


async def test_large_promise_read_is_exact_paged_unicode_and_stale_read_refuses(app):
    owner = app.state['sessions'][0]
    constraints = ['Current condition ' + str(i) + ': ' + '\u03bb' * 900 for i in range(30)]
    item = await prepared_work(app, owner, 'large', constraints=constraints)
    _, child = await commissioned(app, owner, item)
    current = (await read(app, owner, item['id']))['item']
    args = {'workspaceId': workspace_id(app, owner), 'id': item['id'],
            'expectedRevision': current['revision'], 'expectedPromiseRevision': current['promiseRevision'],
            'limit': 1000}
    parts, offset, digest = [], 0, None
    for _ in range(100):
        page = (await agent_action(app, child, 'workspace.work.promise',
                                  {**args, 'offset': offset}, 'promise-page'))['result']
        assert page['offset'] == offset
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 12 * 1024
        digest = digest or page['sha256']
        assert page['sha256'] == digest and page['revision'] == current['revision']
        parts.append(page['text'])
        if page['nextOffset'] is None:
            break
        assert page['nextOffset'] > offset
        offset = page['nextOffset']
    else:
        pytest.fail('Promise pagination made no bounded completion')
    text = ''.join(parts)
    assert len(parts) > 1 and hashlib.sha256(text.encode()).hexdigest() == digest
    assert json.loads(text)['constraints'] == constraints
    await revise(app, owner, current, 'change-current-condition',
                 source=source(app, owner, 'A corrected current condition.'), constraints=['Corrected scope.'])
    with pytest.raises(AppError, match='changed|revision'):
        await agent_action(app, child, 'workspace.work.promise', args, 'stale-promise-page')


async def test_required_source_manifest_keeps_original_and_earlier_corrections(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'corrected')
    for n in range(4):
        item = (await revise(app, owner, item, 'correction-' + str(n),
            source=source(app, owner, 'Exact correction ' + str(n)),
            constraints=['Current condition ' + str(n)]))['result']['item']
    created, child = await commissioned(app, owner, item)
    header = (await current_work(app, child))['required']
    args = {'sessionId': child['id'], 'expectedVersion': header['version'], 'limit': 2}
    requirements = []
    for _ in range(10):
        page = (await agent_action(app, child, 'workspace.work.invocation', args, 'requirements'))['result']
        requirements.extend(page['items'])
        if not page['nextAfter']:
            break
        args['after'] = page['nextAfter']
    else:
        pytest.fail('Requirement manifest did not terminate')
    sources = [row for row in requirements if row['kind'] == 'source']
    assert len(sources) == 5
    assert len({row['readRef']['args']['sourceId'] for row in sources}) == 5
    bodies = [(await agent_action(app, child, row['readRef']['action'], row['readRef']['args'],
                                 'required-source'))['result']['source']['text'] for row in sources]
    assert 'Keep this request after the worker stops.' in bodies
    assert all('Exact correction ' + str(n) in bodies for n in range(4))
    assert header['inputIds'] == [created['initialInputId']]


@pytest.mark.parametrize('change', ['promise', 'metadata', 'stop'])
async def test_currentness_revalidation_detects_changed_facts_without_authorizing_effect(app, change):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'revalidate')
    _, child = await commissioned(app, owner, item)
    version = (await current_work(app, child))['required']['version']
    before = await revalidate(app, child, version)
    assert before['current'] is True and before['effectAuthorized'] is False
    await asyncio.sleep(0)  # An actual request boundary, not a fabricated grant.
    current = (await read(app, owner, item['id']))['item']
    if change == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': child['id']}, command_id='stop-bound-child')
    else:
        patch = ({'source': source(app, owner, 'Correct the current conditions.'),
                  'constraints': ['New required condition']} if change == 'promise'
                 else {'blockedReason': 'A new blocker must remain current.'})
        await revise(app, owner, current, 'revise-before-effect', **patch)
    after = await revalidate(app, child, version)
    assert after['current'] is False and after['effectAuthorized'] is False
    assert len(app.runtime.inputs) == 1  # No input/effect replay by a read.


async def test_required_manifest_cursor_rejects_new_scope(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'cursor')
    _, child = await commissioned(app, owner, item)
    version = (await current_work(app, child))['required']['version']
    args = {'sessionId': child['id'], 'expectedVersion': version, 'limit': 1}
    first = (await agent_action(app, child, 'workspace.work.invocation', args, 'first-manifest'))['result']
    assert first['nextAfter']
    item = (await read(app, owner, item['id']))['item']
    await revise(app, owner, item, 'correction-between-pages',
                 source=source(app, owner, 'Change after page one.'), constraints=['Changed condition.'])
    with pytest.raises(AppError, match='changed|version'):
        await agent_action(app, child, 'workspace.work.invocation',
                           {**args, 'after': first['nextAfter']}, 'old-manifest-tail')


async def test_legacy_commission_has_unavailable_not_empty_authority(app):
    owner = app.state['sessions'][0]
    plan(app, owner)
    created = await commission(app, owner, returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    child = app._session(created['sessionId'])
    await generation(app, child, 'legacy-generation', [created['initialInputId']])
    required = (await current_work(app, child))['required']
    assert required['status'] == 'unavailable' and required['reason']
    assert required['version'] is None and required['effectAuthorized'] is False


async def test_missing_association_receipt_stays_unavailable_without_replay(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'missing')
    created, child = await commissioned(app, owner, item)
    app.db.execute('DELETE FROM commands WHERE id=?', (created['requestId'],))
    app.db.commit()  # Synthetic missing-receipt fault, not a public deletion route.
    required = (await current_work(app, child))['required']
    assert required['status'] == 'unavailable' and required['reason']
    assert required['effectAuthorized'] is False and len(app.runtime.inputs) == 1


async def test_restart_retains_lineage_but_does_not_replay_or_reuse_old_generation(app, monkeypatch):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'restart')
    created, child = await commissioned(app, owner, item)
    before = copy.deepcopy(app.collaboration.receipt(created['requestId'])['workAssociation'])
    binding = transport(child)
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('Restart inspection hydrated history or started a worker')
        monkeypatch.setattr(restored.history, 'ensure_loaded', forbidden)
        monkeypatch.setattr(restored, 'ensure_runtime', forbidden)
        assert restored.collaboration.receipt(created['requestId'])['workAssociation'] == before
        result = await restored.app_bridge('coordination.current', binding, child['id'])
        assert result['work']['required']['status'] == 'unavailable'
        assert result['work']['required']['effectAuthorized'] is False
        assert not restored.runtime.inputs
    finally:
        await restored.close()
