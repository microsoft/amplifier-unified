"""Real public ACP transport and host choices, with synthetic effects only."""
import asyncio
import copy

import pytest

from amplifier_web.service import AppError
from test_automatic_history import app_factory
from test_acp_participant import operations, peer
from test_external_conversation import create, until, send


async def offer(app, session, text='permission-options'):
    count = len(session['approvals'])
    await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': text})
    await until(lambda: len(session['approvals']) > count)
    return session['approvals'][-1]


def choice(session, card, option='allow-this', **changes):
    return {'sessionId': session['id'], 'id': card['id'],
            'decision': 'deny' if option == 'deny-this' else 'allow',
            'optionId': option, 'expectedRequestHash': card['requestHash'], **changes}


async def full(app, session):
    report = (await app.dispatch('access.get', {'sessionId': session['id']}))['result']
    await app.dispatch('access.set', {'sessionId': session['id'], 'scope': 'session',
                       'mode': 'full', 'expectedRevision': report['revision']})


@pytest.mark.parametrize('selected', ['allow-this', 'remember-this', 'deny-this'])
async def test_actual_offered_choice_and_scope_reach_same_agent_once(app_factory, tmp_path, selected):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    assert card['source'] == 'external' and card['agentId'] == 'fixture'
    assert card['policyDescription'] == 'Write the same reviewed file.'
    assert card['authorityLifetime'] == 'request'
    assert card['toolCall']['rawInput'] == {'path': str(tmp_path / 'peer/reviewed.txt')}
    assert [row['optionId'] for row in card['options']] == ['remember-this', 'allow-this', 'deny-this']
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
    result = await app.dispatch('approval.respond', choice(session, card, selected))
    assert result['accepted']
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert [row['outcome'] for row in operations(tmp_path) if row['kind'] == 'permission'] == [
        {'outcome': 'selected', 'optionId': selected}]
    assert card['selectedOptionId'] == selected
    with pytest.raises(AppError, match='no longer pending'):
        await app.dispatch('approval.respond', choice(session, card, selected))
    assert app.runtime.sent == [] and app.runtime.started == []


@pytest.mark.parametrize('invalid', ['hash', 'option', 'origin', 'comment', 'decision', 'session', 'endpoint'])
async def test_invalid_or_stale_choice_never_reaches_peer(app_factory, tmp_path, invalid):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    values, origin = choice(session, card), 'ui'
    if invalid == 'hash': values['expectedRequestHash'] = '0' * 64
    if invalid == 'option': values['optionId'] = 'invented-always'
    if invalid == 'origin': origin = 'agent'
    if invalid == 'comment': values['comment'] = 'unmapped separate instruction'
    if invalid == 'decision': values['decision'] = 'deny'
    if invalid == 'session':
        other = await app.dispatch('session.create', {'workspace': app.default_workspace})
        values['sessionId'] = other['result']['sessionId']
    if invalid == 'endpoint': app.server_config['externalAgents'][0]['args'].append('--changed')
    with pytest.raises(AppError):
        await app.dispatch('approval.respond', values, origin=origin)
    assert card['status'] == 'pending'
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
    await app.dispatch('conversation.stop', {'sessionId': session['id']})
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert card['status'] == 'expired'


async def test_full_selects_once_even_when_always_is_first(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    await full(app, session)
    await send(app, session, 'permission-options', 'full-options')
    assert [row['outcome'] for row in operations(tmp_path) if row['kind'] == 'permission'] == [
        {'outcome': 'selected', 'optionId': 'allow-this'}]
    assert session['approvals'] == []
    card = await offer(app, session, 'permission-always-only')
    assert card['status'] == 'pending'
    await app.dispatch('approval.respond', choice(session, card, 'remember-this'))
    await until(lambda: session['id'] not in app.external_conversations.turns)


async def test_full_set_while_waiting_only_selects_current_once(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    await full(app, session)
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert card['selectedOptionKind'] == 'allow_once'
    assert [row['outcome'] for row in operations(tmp_path) if row['kind'] == 'permission'] == [
        {'outcome': 'selected', 'optionId': 'allow-this'}]


@pytest.mark.parametrize('text', ['permission-withdraw', 'permission-changed'])
async def test_peer_withdrawal_or_changed_request_expires_card_without_selection(app_factory, tmp_path, text):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session, text)
    (tmp_path / 'peer/withdraw').touch()
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert card['status'] == 'expired'
    assert [row['outcome'] for row in operations(tmp_path) if row['kind'] == 'permission'] == [
        {'outcome': 'cancelled'}]
    with pytest.raises(AppError, match='no longer pending'):
        await app.dispatch('approval.respond', choice(session, card, 'remember-this'))


async def test_durable_stop_before_callback_delivery_blocks_old_always(app_factory, tmp_path, monkeypatch):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app._stop
    async def held(sid, boundary, current=None):
        assert callable(current), 'The captured Stop guard must reach the held callback'
        entered.set()
        await release.wait()
        await original(sid, boundary, current)
    monkeypatch.setattr(app, '_stop', held)
    stop = asyncio.create_task(app.dispatch('conversation.stop', {'sessionId': session['id']}))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(AppError, match='no longer pending'):
            await app.dispatch('approval.respond', choice(session, card, 'remember-this'))
    finally:
        release.set()
        await stop
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert card['status'] == 'expired'
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission'
                and row['outcome'].get('outcome') == 'selected']


async def test_sdk_request_cancellation_expires_card_before_prompt_finishes(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session, 'permission-cancel-request')
    (tmp_path / 'peer/withdraw').touch()
    await until(lambda: card['status'] == 'expired')
    assert session['id'] in app.external_conversations.turns
    with pytest.raises(AppError, match='no longer pending'):
        await app.dispatch('approval.respond', choice(session, card, 'remember-this'))
    (tmp_path / 'peer/release').touch()
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
    assert len([row for row in operations(tmp_path) if row['kind'] == 'permission-cancelled']) == 1


@pytest.mark.parametrize('saved_choice', [False, True])
async def test_stop_does_not_send_a_permission_refusal_ahead_of_cancel(
        app_factory, tmp_path, monkeypatch, saved_choice):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    approvals = app.external_conversations.approvals
    future = approvals.pending[card['id']][-2]
    participant = app.external_conversations.turns[session['id']].participant
    cancel_entered, release_cancel = asyncio.Event(), asyncio.Event()
    original_cancel = participant.cancel
    async def held_cancel():
        cancel_entered.set()
        await release_cancel.wait()
        await original_cancel()
    monkeypatch.setattr(participant, 'cancel', held_cancel)
    delivery_entered, release_delivery = asyncio.Event(), asyncio.Event()
    original_deliver = approvals.deliver
    async def held_delivery(*args):
        delivery_entered.set()
        await release_delivery.wait()
        await original_deliver(*args)
    selected = None
    if saved_choice:
        monkeypatch.setattr(approvals, 'deliver', held_delivery)
        selected = asyncio.create_task(app.dispatch('approval.respond', choice(session, card)))
        await asyncio.wait_for(delivery_entered.wait(), 5)
    stopped = asyncio.create_task(app.dispatch('conversation.stop', {'sessionId': session['id']}))
    try:
        await asyncio.wait_for(cancel_entered.wait(), 5)
        release_delivery.set()
        if selected is not None:
            await selected
        assert card['status'] == 'expired'
        assert not future.done(), 'Stop must not complete permission RPC before session/cancel'
        assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
    finally:
        release_delivery.set()
        release_cancel.set()
        await stopped
        if selected is not None:
            await selected
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert len([row for row in operations(tmp_path) if row['kind'] == 'cancel']) == 1
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']


async def test_restart_expires_pending_card_without_replaying_prompt(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    saved = copy.deepcopy(card)
    remote = session['externalAgent']['sessionId']
    await app.close()
    reopened = app_factory()
    reopened.server_config = {'externalAgents': [peer(tmp_path)]}
    restored = reopened._session(session['id'])
    assert restored['externalAgent']['sessionId'] == remote
    assert restored['approvals'][-1]['status'] == 'expired'
    with pytest.raises(AppError):
        await reopened.dispatch('approval.respond', choice(restored, saved, 'remember-this'))
    assert len([row for row in operations(tmp_path) if row['kind'] == 'prompt']) == 1


async def test_cancelled_prompt_retires_permission_rpc_left_open_by_peer(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session, 'permission-stop-leaves-request')
    participant = app.external_conversations.turns[session['id']].participant
    remote = participant.session_id
    await app.dispatch('conversation.stop', {'sessionId': session['id']})
    await until(lambda: session['id'] not in app.external_conversations.turns)
    await until(lambda: any(row['kind'] == 'permission-left-open-retired' for row in operations(tmp_path)))
    assert card['status'] == 'expired'
    assert card['id'] not in app.external_conversations.approvals.pending
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
    retired = next(row for row in operations(tmp_path) if row['kind'] == 'permission-left-open-retired')
    assert retired['cancelled'] or retired['error'], 'Host must cancel the RPC, not synthesize refusal'
    await send(app, session, 'next explicit message', 'next-after-cancel')
    assert participant is app.external_conversations.participants[(session['id'], 'fixture')]
    assert participant.session_id == remote


async def test_unnegotiated_metadata_does_not_claim_instance_rule_scope(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'ordinary text', 'first')
    participant = app.external_conversations.participants[(session['id'], 'fixture')]
    participant.initialization_metadata.clear()
    card = await offer(app, session)
    assert 'policyDescription' not in card and 'authorityLifetime' not in card
    assert card['details']['path'].endswith('reviewed.txt')
    # Generic ACP still presents the agent's actual offered choice, without
    # attributing its remembering semantics to an Instance policy owner.
    await app.dispatch('approval.respond', choice(session, card, 'deny-this'))
    await until(lambda: session['id'] not in app.external_conversations.turns)


async def test_invalid_lifetime_does_not_claim_instance_rule_scope(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session, 'permission-invalid-lifetime')
    assert 'policyDescription' not in card and 'authorityLifetime' not in card
    await app.dispatch('approval.respond', choice(session, card, 'deny-this'))
    await until(lambda: session['id'] not in app.external_conversations.turns)


async def test_failed_durable_save_keeps_original_choice_actionable_without_delivery(app_factory, tmp_path, monkeypatch):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    before = copy.deepcopy(card)
    save = app._save_changes
    def unavailable(**kwargs):
        raise OSError('synthetic disk full')
    monkeypatch.setattr(app, '_save_changes', unavailable)
    with pytest.raises(OSError, match='disk full'):
        await app.dispatch('approval.respond', choice(session, card, 'remember-this'), command_id='one-decision')
    assert card == before
    assert app.db.execute('SELECT 1 FROM commands WHERE id=?', ('one-decision',)).fetchone() is None
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
    monkeypatch.setattr(app, '_save_changes', save)
    args = choice(session, card, 'remember-this')
    assert (await app.dispatch('approval.respond', args, command_id='one-decision'))['accepted']
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert (await app.dispatch('approval.respond', args, command_id='one-decision'))['duplicate']
    assert [row['outcome'] for row in operations(tmp_path) if row['kind'] == 'permission'] == [
        {'outcome': 'selected', 'optionId': 'remember-this'}]


async def test_failed_full_publication_does_not_strand_selected_card(app_factory, tmp_path, monkeypatch):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    before = copy.deepcopy(card)
    with monkeypatch.context() as patch:
        patch.setattr(app.access_mode, 'full', lambda *_args: True)
        def unavailable(**kwargs):
            raise OSError('synthetic disk full')
        patch.setattr(app, '_save_changes', unavailable)
        with pytest.raises(OSError, match='disk full'):
            await app.access_mode._settle_once(session['id'])
    assert card == before
    assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
    await app.dispatch('approval.respond', choice(session, card, 'deny-this'))
    await until(lambda: session['id'] not in app.external_conversations.turns)


@pytest.mark.parametrize('full_access', [False, True])
@pytest.mark.parametrize('failure', ['after-save', 'notification', 'caller-cancelled'])
async def test_committed_choice_survives_notification_or_caller_failure(
        app_factory, tmp_path, monkeypatch, full_access, failure):
    app = app_factory()
    session = await create(app, tmp_path)
    card = await offer(app, session)
    entered, release = asyncio.Event(), asyncio.Event()
    approvals = app.external_conversations.approvals
    original = approvals.deliver
    async def held(*args):
        entered.set()
        await release.wait()
        await original(*args)
    monkeypatch.setattr(approvals, 'deliver', held)
    if full_access:
        monkeypatch.setattr(app.access_mode, 'full', lambda *_args: True)
    if failure == 'after-save':
        save = app._save_changes
        def saved_then_failed(**kwargs):
            save(**kwargs)
            raise OSError('synthetic post-commit bookkeeping failure')
        monkeypatch.setattr(app, '_save_changes', saved_then_failed)
    if failure == 'notification':
        def notification_failed(**kwargs):
            raise OSError('synthetic display unavailable')
        monkeypatch.setattr(app, '_publish_progress', notification_failed)
    args = choice(session, card)
    caller = asyncio.create_task(app.access_mode._settle_once(session['id']) if full_access else
        app.dispatch('approval.respond', args, command_id='saved-choice'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert card['status'] == 'allow'
        assert not [row for row in operations(tmp_path) if row['kind'] == 'permission']
        if failure == 'caller-cancelled':
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        release.set()
        if failure != 'caller-cancelled':
            await caller
        await until(lambda: session['id'] not in app.external_conversations.turns)
        if not full_access:
            assert (await app.dispatch('approval.respond', args, command_id='saved-choice'))['duplicate']
        assert [row['outcome'] for row in operations(tmp_path) if row['kind'] == 'permission'] == [
            {'outcome': 'selected', 'optionId': 'allow-this'}]
        assert card['status'] == 'allow'
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)
