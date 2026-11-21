"""One public source, independent real ACP participants, no paid model."""
import asyncio
import copy
from pathlib import Path
import sys

import pytest

from amplifier_web.external_conversation import bindings, is_shared
from test_automatic_history import app_factory
from test_external_conversation import until
from test_acp_participant import operations


def configure(app, path, profile="plain"):
    app.server_config = {'externalAgents': [{'id': identity, 'label': identity.title(),
        'command': sys.executable, 'args': [str(Path(__file__).parent / 'fixtures/shared_acp_agent.py'),
                                         str(path / identity / 'peer'), identity, profile]}
        for identity in ('alpha', 'beta')]}


async def room(app, path, profile="plain"):
    configure(app, path, profile)
    result = await app.dispatch('session.create', {'workspace': app.default_workspace,
        'externalAgentIds': ['alpha', 'beta']}, command_id='shared-room')
    return app._session(result['result']['sessionId'])


async def send(app, sid, text, identity):
    receipt = await app.dispatch('conversation.send', {'sessionId': sid, 'text': text}, command_id=identity)
    assert receipt['accepted'] and receipt['delivery'] == 'accepted'
    return receipt


async def idle(app, sid):
    await until(lambda: not any(room == sid for room, _ in app.external_conversations.shared.turns)
                and not app._session(sid)['externalAgent'].get('pendingCount'))


def work(path, identity):
    return [row for row in operations(path / identity) if row['kind'] == 'work']


@pytest.mark.parametrize('profile', ['plain', 'attention', 'instance'])
async def test_concurrent_streams_public_acceptance_stop_and_later_post(app_factory, tmp_path, profile):
    app = app_factory()
    session = await room(app, tmp_path, profile)
    sid = session['id']
    assert is_shared(session)
    await send(app, sid, 'wait-for-stop', 'first')
    await until(lambda: len(session.get('externalStreams', {})) == 2)
    assert len(app.external_conversations.shared.turns) == 2
    assert {stream['text'] for stream in session['externalStreams'].values()} == {
        'alpha unfinished draft', 'beta unfinished draft'}
    for state in (app.browser_state(sid), app.session_state(sid)):
        public = next(row for row in state['sessions'] if row['id'] == sid)
        assert {row['text'] for row in public['externalStreams'].values()} == {
            'alpha unfinished draft', 'beta unfinished draft'}
    assert all('streaming' not in row for row in bindings(session))
    await send(app, sid, 'pending while busy', 'second')
    assert session['externalAgent']['pendingCount'] == 2
    assert all(len(work(tmp_path, identity)) == 1 for identity in ('alpha', 'beta'))
    public = next(row for row in app.browser_state(sid)['sessions'] if row['id'] == sid)
    assert '_externalRoom' not in public and '_externalAgentSource' not in public
    await app.dispatch('conversation.stop', {'sessionId': sid}, command_id='stop-shared')
    await idle(app, sid)
    session = app._session(sid)
    assert not session.get('externalStreams')
    assert all(row['state'] == 'cancelled' for row in session['_externalRoom']['deliveries'])
    assert len([row for row in session['messages'] if row.get('partial')]) == 2
    assert not any(row.get('roomSequence') for row in session['messages'] if row.get('partial'))
    assert all([row['kind'] for row in operations(tmp_path / identity)].count('cancel') == 1 for identity in ('alpha', 'beta'))
    await send(app, sid, 'explicit after stop', 'third')
    await idle(app, sid)
    assert all([row['source'] for row in work(tmp_path, identity)] == ['wait-for-stop', 'explicit after stop'] for identity in ('alpha', 'beta'))
    receipt = (await app.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': 'third'}))['result']
    assert receipt['scope'] == 'conversation' and receipt['delivery'] == 'accepted' and not receipt['resent']
    assert receipt['source'] == {'sessionId': sid, 'workspace': session['workspace']}
    assert {row['agentId'] for row in receipt['participants']} == {'alpha', 'beta'}
    assert all(row['state'] == 'accepted' for row in receipt['participants'])
    duplicate = await send(app, sid, 'explicit after stop', 'third')
    assert duplicate['duplicate']
    assert all(len(work(tmp_path, identity)) == 2 for identity in ('alpha', 'beta'))
    assert app.runtime.sent == [] and app.runtime.started == []
    assert not any('decision' in row['text'] or 'Private tool' in row['text'] for row in session['messages'])


@pytest.mark.parametrize('profile', ['plain', 'attention'])
async def test_busy_peer_post_is_later_considered_once_without_forced_work(app_factory, tmp_path, profile):
    app = app_factory()
    session = await room(app, tmp_path, profile)
    sid = session['id']
    await send(app, sid, 'busy exchange', 'exchange')
    await until(lambda: any(row.get('agentId') == 'alpha' for row in session['messages']))
    await until(lambda: any(row['agentId'] == 'beta' and row['sequence'] == 2 and row['state'] == 'pending'
                           for row in session['_externalRoom']['deliveries']))
    await until(lambda: work(tmp_path, 'beta'))
    assert len(work(tmp_path, 'beta')) == 1
    (tmp_path / 'beta/peer/release').touch()
    await idle(app, sid)
    assert [row['source'] for row in work(tmp_path, 'alpha')] == ['busy exchange']
    assert [row['source'] for row in work(tmp_path, 'beta')] == ['busy exchange', 'Question for Beta: check 42']
    answers = [row for row in session['messages'] if row['role'] == 'assistant']
    assert [row['agentId'] for row in answers] == ['alpha', 'beta', 'beta']
    assert answers[-1]['text'] == 'Beta answer: 42'
    assert any(row['state'] == ('listening' if profile == 'attention' else 'accepted')
               and row['agentId'] == 'alpha' and row['sequence'] > 1 for row in session['_externalRoom']['deliveries'])
    assert not any(row['agentId'] == app.external_conversations.shared.message(session, row['sourceMessageId']).get('agentId')
                   for row in session['_externalRoom']['deliveries'])


async def test_unknown_recipient_keeps_binding_and_does_not_replay_after_restart(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    sid = session['id']
    await send(app, sid, 'drop-after-effect', 'uncertain')
    await idle(app, sid)
    original = copy.deepcopy(session['_externalRoom']['deliveries'])
    assert {row['agentId']: row['state'] for row in original} == {'alpha': 'unknown', 'beta': 'accepted'}
    remotes = {row['id']: row['sessionId'] for row in bindings(session)}
    await app.close()
    restored = app_factory()
    configure(restored, tmp_path)
    await restored.external_conversations.resume_pending()
    assert len(work(tmp_path, 'alpha')) == 1
    assert restored._session(sid)['_externalRoom']['deliveries'] == original
    await send(restored, sid, 'new explicit post', 'new')
    await idle(restored, sid)
    session = restored._session(sid)
    assert {row['id']: row['sessionId'] for row in bindings(session)} == remotes
    assert next(row for row in session['_externalRoom']['deliveries'] if row['id'] == original[0]['id'])['state'] == 'unknown'
    assert [row['source'] for row in work(tmp_path, 'alpha')] == ['drop-after-effect', 'new explicit post']
    assert [row['source'] for row in work(tmp_path, 'beta')] == ['new explicit post']


async def test_known_unsubmitted_source_resumes_once_without_history_replay(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    sid = session['id']
    async def defer(*args, **kwargs):
        return {'delivery': 'accepted', 'inputId': 'saved-only'}
    app._send = defer
    await send(app, sid, 'saved before dispatch', 'saved-only')
    assert session['externalAgent']['pendingCount'] == 2
    assert not app.external_conversations.shared.turns
    assert not (tmp_path / 'alpha/peer').exists() and not (tmp_path / 'beta/peer').exists()
    await app.dispatch('session.create', {'workspace': app.default_workspace,
        'externalAgentId': 'alpha'}, command_id='selected-other-room')
    await app.close()
    restored = app_factory()
    configure(restored, tmp_path)
    assert sid not in {row['id'] for row in restored.state['sessions']}
    assert restored.directory.resolve(sid)['externalAgent']['pendingCount'] == 2
    await restored.external_conversations.resume_pending()
    await idle(restored, sid)
    await restored.external_conversations.resume_pending()
    assert all([row['source'] for row in work(tmp_path, identity)] == ['saved before dispatch'] for identity in ('alpha', 'beta'))
    assert [row['inputId'] for row in restored._session(sid)['messages'] if row['role'] == 'user'] == ['saved-only']


@pytest.mark.parametrize('full', [False, True])
async def test_each_participant_uses_existing_full_without_private_decision_output(app_factory, tmp_path, full):
    app = app_factory()
    session = await room(app, tmp_path)
    if full:
        access = (await app.dispatch('access.get', {'sessionId': session['id']}))['result']
        await app.dispatch('access.set', {'sessionId': session['id'], 'scope': 'session', 'mode': 'full', 'expectedRevision': access['revision']})
    await send(app, session['id'], 'permission', 'permission-input')
    if not full:
        await until(lambda: len([a for a in session['approvals'] if a['status'] == 'pending']) == 2)
        assert {a['agentId'] for a in session['approvals']} == {'alpha', 'beta'}
        for card in list(session['approvals']):
            await app.dispatch('approval.respond', {'sessionId': session['id'], 'id': card['id'],
                'decision': 'allow' if card['agentId'] == 'alpha' else 'deny',
                'optionId': 'allow-this' if card['agentId'] == 'alpha' else 'deny-this',
                'expectedRequestHash': card['requestHash']})
    await idle(app, session['id'])
    for identity in ('alpha', 'beta'):
        observed = [row for row in operations(tmp_path / identity) if row['kind'] == 'permission']
        assert len(observed) == 1
        assert observed[0]['outcome'] == {'outcome': 'selected',
            'optionId': 'allow-this' if full or identity == 'alpha' else 'deny-this'}
    assert not session.get('fileAccessPending') and not session.get('configurationPending')


@pytest.mark.parametrize(('origin', 'is_agent'), [('ui', False), ('system', True), ('unverified', None)])
async def test_attention_uses_exact_wire_schema_and_true_source_provenance(app_factory, tmp_path, origin, is_agent):
    app = app_factory()
    session = await room(app, tmp_path, 'attention')
    if origin == 'ui':
        receipt = await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'strict attention'},
                                     command_id='strict-source')
    else:
        # Exercise owner projection of an already accepted synthetic source.
        # Non-UI dispatch retains its separate caller-authorization contract.
        message = app._message(session, 'user', 'strict attention', inputId='strict-source', inputOrigin=origin)
        app.external_conversations.shared.capture(session, message)
        receipt = await app.external_conversations.shared.send(session, 'strict-source')
    assert receipt['delivery'] == 'accepted'
    await idle(app, session['id'])
    source = session['messages'][0]
    for identity in ('alpha', 'beta'):
        rows = operations(tmp_path / identity)
        assert not any(row['kind'] == 'decision' for row in rows)
        offer = next(row for row in rows if row['kind'] == 'offer')
        context = offer['context']
        assert context['trigger_id'] == source['id']
        assert offer['occurredAt'] == int(source['createdAt'] * 1000)
        sender = next(row['sender'] for row in context['messages'] if row['id'] == source['id'])
        assert next(row['is_agent'] for row in context['participants'] if row['id'] == sender) is is_agent
        assert len(work(tmp_path, identity)) == 1


async def test_attention_unicode_projection_preserves_trigger_within_wire_byte_limit(app_factory, tmp_path):
    import json
    app = app_factory()
    session = await room(app, tmp_path)
    owner = app.external_conversations.shared
    message = app._message(session, 'user', '漢🙂' * 12000, inputId='unicode', inputOrigin='ui')
    owner.capture(session, message)
    context, coverage = owner._context(session, session['_externalRoom']['deliveries'][0])
    assert len(json.dumps(context, ensure_ascii=False, separators=(',', ':')).encode()) <= 32768
    assert context['trigger_id'] == message['id'] and context['messages'][0]['id'] == message['id']
    assert message['text'].startswith(context['messages'][0]['text']) and context['messages'][0]['text']
    assert coverage['truncatedMessageIds'] == [message['id']]
    assert len(message['text']) == 24000  # Canonical source remains complete.


@pytest.mark.parametrize('text', ['silent-after-decision', 'silent-empty'])
@pytest.mark.parametrize('profile', ['plain', 'attention'])
async def test_execution_may_stay_silent_without_publishing_or_inviting_peers(app_factory, tmp_path, text, profile):
    app = app_factory()
    session = await room(app, tmp_path, profile)
    await send(app, session['id'], text, 'silent')
    await idle(app, session['id'])
    assert not any(row['role'] == 'assistant' for row in session['messages'])
    assert all(len(work(tmp_path, identity)) == 1 for identity in ('alpha', 'beta'))
    assert len(session['_externalRoom']['deliveries']) == 2
    assert all(row['state'] == 'accepted' for row in session['_externalRoom']['deliveries'])


async def test_plain_participation_uses_one_ordinary_prompt_not_a_private_preflight(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    # Old values remain inert; new participation must not rewrite saved records.
    bindings(session)[0]['lastOfferedSequence'] = 17
    await send(app, session['id'], 'ordinary contribution', 'ordinary')
    await idle(app, session['id'])
    for identity in ('alpha', 'beta'):
        rows = operations(tmp_path / identity)
        assert not any(row['kind'] == 'decision' for row in rows)
        assert len([row for row in rows if row['kind'] == 'work']) == 1
        assert len([row for row in rows if row['kind'] == 'prompt']) == 2  # Own source and the other's public reply.
    assert bindings(session)[0]['lastOfferedSequence'] == 17
    assert 'lastOfferedSequence' not in bindings(session)[1]


@pytest.mark.parametrize('profile', ['attention-error', 'attention-malformed'])
async def test_advertised_attention_failure_never_sends_a_fallback_prompt(app_factory, tmp_path, profile):
    app = app_factory()
    session = await room(app, tmp_path, profile)
    await send(app, session['id'], 'do not retry', 'failed-offer')
    await idle(app, session['id'])
    for identity in ('alpha', 'beta'):
        rows = operations(tmp_path / identity)
        assert [row['kind'] for row in rows] == ['new', 'offer']
    assert all(row['state'] == 'unknown' for row in session['_externalRoom']['deliveries'])
    assert len(session['messages']) == 1


async def test_split_silence_chunks_and_private_thoughts_never_reach_public_views(app_factory, tmp_path):
    import json
    app = app_factory()
    session = await room(app, tmp_path)
    await send(app, session['id'], 'silent-split-wait', 'split-silent')
    await until(lambda: len(session.get('externalStreams', {})) == 2)
    from amplifier_web.browser_detail import project
    for state in (app.browser_state(session['id']), app.session_state(session['id']), project(session)):
        public = json.dumps(state)
        assert '[[PA' not in public and 'Private reasoning' not in public
    assert len(session['messages']) == 1
    for identity in ('alpha', 'beta'):
        (tmp_path / identity / 'peer/release').touch()
    await idle(app, session['id'])
    assert len(session['messages']) == 1 and not session.get('externalStreams')


@pytest.mark.parametrize('text', ['stream-error', 'pass-prefix-error'])
async def test_failed_stream_keeps_only_real_partial_reply_and_never_invites_peers(app_factory, tmp_path, text):
    app = app_factory()
    session = await room(app, tmp_path)
    await send(app, session['id'], text, 'failed-stream')
    await idle(app, session['id'])
    replies = [row for row in session['messages'] if row['role'] == 'assistant']
    assert len(replies) == (2 if text == 'stream-error' else 0)
    assert all(row['partial'] and 'roomSequence' not in row for row in replies)
    assert len(session['_externalRoom']['deliveries']) == 2
    assert all(row['state'] == 'unknown' for row in session['_externalRoom']['deliveries'])
    assert all(len(work(tmp_path, identity)) == 1 for identity in ('alpha', 'beta'))


async def test_stop_while_ordinary_turn_waits_for_permission_revokes_late_choices(app_factory, tmp_path):
    from amplifier_web.errors import AppError
    app = app_factory()
    session = await room(app, tmp_path)
    await send(app, session['id'], 'permission', 'stop-permission')
    await until(lambda: len([row for row in session['approvals'] if row['status'] == 'pending']) == 2)
    cards = copy.deepcopy(session['approvals'])
    await app.dispatch('conversation.stop', {'sessionId': session['id']}, command_id='stop-wait')
    await idle(app, session['id'])
    for card in cards:
        with pytest.raises(AppError, match='no longer pending'):
            await app.dispatch('approval.respond', {'sessionId': session['id'], 'id': card['id'],
                'decision': 'allow', 'optionId': 'allow-this', 'expectedRequestHash': card['requestHash']})
    assert not app.external_conversations.approvals.pending
    assert not any(row['role'] == 'assistant' for row in session['messages'])


@pytest.mark.parametrize('change', ['stop', 'source', 'endpoint'])
async def test_attention_wait_rechecks_current_stop_and_source_before_prompt(app_factory, tmp_path, monkeypatch, change):
    from amplifier_web.acp_participant import ACPParticipant
    arrived, release = asyncio.Event(), asyncio.Event()
    peers = set()
    attention = ACPParticipant.attention
    async def held(self, context, occurred_at):
        offer = await attention(self, context, occurred_at)
        peers.add(self)
        if len(peers) == 2:
            arrived.set()
        await release.wait()
        return offer
    monkeypatch.setattr(ACPParticipant, 'attention', held)
    app = app_factory()
    session = await room(app, tmp_path, 'attention')
    await send(app, session['id'], 'before wait', 'freshness')
    await arrived.wait()
    if change == 'stop':
        # Exercise the durable boundary before the waiting response resumes.
        session['interruptionRevision'] += 1
    elif change == 'source':
        session['messages'][0]['text'] = 'changed source'
    else:
        for endpoint in app.server_config['externalAgents']:
            endpoint['args'].append('changed endpoint')
    release.set()
    await idle(app, session['id'])
    assert all(not any(row['kind'] == 'prompt' for row in operations(tmp_path / identity))
               for identity in ('alpha', 'beta'))
    assert len(session['messages']) == 1


async def test_failed_awareness_stays_unknown_without_a_second_offer(app_factory, tmp_path, monkeypatch):
    from amplifier_web.acp_participant import ACPParticipant
    async def uncertain(self, text, *, input_id, metadata=None):
        assert len(text) < 25000 and 'preserve the active requester' in text
        return {'status': 'failed', '_meta': {'steering': {'inputId': input_id}}}
    monkeypatch.setattr(ACPParticipant, 'observe', uncertain)
    app = app_factory()
    session = await room(app, tmp_path)
    await send(app, session['id'], 'busy exchange', 'aware-source')
    await until(lambda: any(row['agentId'] == 'beta' and row['sequence'] == 2 and row['state'] == 'unknown'
                           for row in session['_externalRoom']['deliveries']))
    (tmp_path / 'beta/peer/release').touch()
    await idle(app, session['id'])
    assert [row['source'] for row in work(tmp_path, 'beta')] == ['busy exchange']
    assert not any(row['kind'] == 'prompt' and row['source'] == 'Question for Beta: check 42'
                   for row in operations(tmp_path / 'beta'))


@pytest.mark.parametrize('text', ['unfinished public draft', '[[PA', '[[PASS]]', '  '])
async def test_interrupted_participant_stream_and_unknown_receipt_remain_visible(app_factory, tmp_path, text):
    app = app_factory()
    session = await room(app, tmp_path)
    source = app._message(session, 'user', 'interrupted source', inputId='interrupted', inputOrigin='ui')
    app.external_conversations.capture_recipient(session, source)
    delivery = session['_externalRoom']['deliveries'][0]
    delivery['state'] = 'sending'
    binding = bindings(session)[0]
    binding.update(status='working', activeInputId='interrupted', activeSourceMessageId=source['id'])
    session['externalStreams'] = {'alpha': {'id': 'partial', 'text': text,
                        'inputId': 'interrupted', 'sourceMessageId': source['id']}}
    app._publish_changes(sessions={session['id']})
    await app.close()
    restored = app_factory()._session(session['id'])
    assert bindings(restored)[0]['status'] == 'unknown'
    assert 'unknown' in bindings(restored)[0]['error']
    assert bindings(restored)[0]['lastReceipt']['state'] == 'unknown'
    replies = [row for row in restored['messages'] if row['role'] == 'assistant']
    assert len(replies) == (1 if text == 'unfinished public draft' else 0)
    if replies:
        assert replies[0]['partial'] and replies[0]['agentId'] == 'alpha'
        assert 'roomSequence' not in replies[0]
    assert not restored.get('externalStreams')


async def test_mention_is_context_not_exclusive_routing_and_other_rooms_stay_private(app_factory, tmp_path):
    app = app_factory()
    first = await room(app, tmp_path)
    second_result = await app.dispatch('session.create', {'workspace': app.default_workspace,
        'externalAgentIds': ['alpha', 'beta']}, command_id='second-shared-room')
    second = app._session(second_result['result']['sessionId'])
    await app.dispatch('conversation.send', {'sessionId': first['id'], 'text': 'room one only',
        'recipientAgentId': 'alpha'}, command_id='mentioned-public')
    await send(app, second['id'], 'room two only', 'other-public')
    await asyncio.gather(idle(app, first['id']), idle(app, second['id']))
    assert all(len([row for row in session['messages'] if row['role'] == 'assistant']) == 2
               for session in (first, second))
    for identity in ('alpha', 'beta'):
        assert next(row for row in bindings(first) if row['id'] == identity)['sessionId'] != next(
            row for row in bindings(second) if row['id'] == identity)['sessionId']
        prompts = [row for row in operations(tmp_path / identity) if row['kind'] == 'prompt']
        for prompt in prompts:
            context = prompt['context']
            other_text = 'room two only' if context['conversation_id'] == first['id'] else 'room one only'
            assert not any(other_text in row['text'] for row in context['messages'])
        addressed = next(row['context'] for row in prompts if row['source'] == 'room one only')
        assert addressed['messages'][0]['recipients'] == ['alpha']


async def test_negotiated_public_instance_preserves_original_input_source_and_exact_resume(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path, 'instance')
    sid = session['id']
    await send(app, sid, 'original source', 'original-input')
    await idle(app, sid)
    saved = copy.deepcopy(bindings(session))
    for binding in saved:
        assert binding['publicAgentId'] == 'public:' + binding['id']
        assert binding['instanceSource'] == {'sourceId': 'urn:amplifier:conversation:' + sid, 'conversationId': sid}
        assert binding['instanceInterruptionRevision'] == 7
        inputs = [row['metadata']['input'] for row in operations(tmp_path / binding['id']) if row['kind'] == 'instance-input']
        assert inputs[0]['id'] == 'original-input'
        # Other participant's reply keeps its own canonical message identity,
        # not the triggering human input or JSON-RPC transport request ID.
        peer_message = next(row for row in session['messages'] if row.get('agentId') not in {None, binding['id']})
        assert inputs[1]['id'] == peer_message['id']
    await app.close()
    restored = app_factory()
    configure(restored, tmp_path, 'instance')
    await send(restored, sid, 'later source', 'later-input')
    await idle(restored, sid)
    for previous, current in zip(saved, bindings(restored._session(sid))):
        assert previous['sessionId'] == current['sessionId']
        assert previous['publicAgentId'] == current['publicAgentId']
        assert len([row for row in operations(tmp_path / current['id']) if row['kind'] == 'resume']) == 1


async def test_malformed_instance_ack_retains_allocated_identity_without_prompt_or_replacement(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path, 'instance-malformed')
    await send(app, session['id'], 'not sent', 'bad-instance')
    await idle(app, session['id'])
    identities = [row['sessionId'] for row in bindings(session)]
    await send(app, session['id'], 'explicit second', 'bad-instance-next')
    await idle(app, session['id'])
    assert [row['sessionId'] for row in bindings(session)] == identities
    for identity in ('alpha', 'beta'):
        kinds = [row['kind'] for row in operations(tmp_path / identity)]
        assert kinds == ['new', 'instance-session', 'resume', 'instance-session']


@pytest.mark.parametrize('change', ['change-identity', 'hide-profile'])
async def test_public_identity_change_or_missing_profile_never_resumes_or_replaces_binding(app_factory, tmp_path, change):
    app = app_factory()
    session = await room(app, tmp_path, 'instance')
    await send(app, session['id'], 'first identity', 'first-identity')
    await idle(app, session['id'])
    saved = copy.deepcopy(bindings(session))
    await app.close()
    for identity in ('alpha', 'beta'):
        (tmp_path / identity / 'peer' / change).touch()
    restored = app_factory()
    configure(restored, tmp_path, 'instance')
    await send(restored, session['id'], 'must not rebind', 'identity-replaced')
    await idle(restored, session['id'])
    for previous, current in zip(saved, bindings(restored._session(session['id']))):
        assert (previous['sessionId'], previous['publicAgentId']) == (current['sessionId'], current['publicAgentId'])
        rows = operations(tmp_path / current['id'])
        assert len([row for row in rows if row['kind'] == 'new']) == 1
        assert not any(row['kind'] == 'resume' or row.get('source') == 'must not rebind' for row in rows)


async def test_unicode_ordinary_public_reply_and_unsupported_media_stay_in_existing_boundary(app_factory, tmp_path):
    from amplifier_web.errors import AppError
    app = app_factory()
    session = await room(app, tmp_path)
    with pytest.raises(AppError, match='ordinary text'):
        await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'media',
                                                'attachmentIds': ['not-a-media-grant']}, command_id='no-media')
    assert not session['messages']
    await send(app, session['id'], '漢🙂' * 100, 'unicode-ordinary')
    await idle(app, session['id'])
    assert [row['text'] for row in session['messages'] if row['role'] == 'assistant'] == [
        identity + ' public answer: ' + '漢🙂' * 100 for identity in
        [row['agentId'] for row in session['messages'] if row['role'] == 'assistant']]


def test_silence_projection_reads_only_current_streams_and_preserves_real_text():
    from amplifier_web.shared_conversation import public_output
    class UnreadHistory:
        def __iter__(self):
            raise AssertionError('Projection must not visit history')
    streams = {str(index): {'id': str(index), 'text': text} for index, text in enumerate(
        ['', '   ', '[[', ' [[PA', '[[PASS]] ', '漢🙂 live reply', '[[PASS]] is quoted', '[ordinary'])}
    session = {'externalAgent': {'participation': 'shared'}, 'externalStreams': streams, 'messages': UnreadHistory()}
    result = public_output(session)
    assert [row['text'] for row in result['externalStreams'].values()] == [
        '漢🙂 live reply', '[[PASS]] is quoted', '[ordinary']
    assert len(session['externalStreams']) == 8  # Canonical buffer remains untouched.


async def test_bounded_attention_projection_and_expired_receipt_details(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    owner = app.external_conversations.shared
    for number in range(140):
        source = app._message(session, 'user', 'x' * 1000, inputId=str(number), inputOrigin='unverified')
        owner.capture(session, source)
        for row in session['_externalRoom']['deliveries']:
            if row['state'] == 'pending':
                owner._record(session, row, 'listening')
    context, coverage = owner._context(session, session['_externalRoom']['deliveries'][-1])
    assert len(context['messages']) <= 32
    assert sum(len(row['text']) for row in context['messages']) <= 24000
    assert coverage['earlierMessagesOmitted']
    assert set(context) == {'conversation_id', 'thread_id', 'agent', 'participants', 'messages', 'trigger_id', 'responsibilities'}
    assert all('truncated' not in row for row in context['messages'])
    expired = owner.delivery(session, '0')
    assert expired['coverage'] == 'expired' and not expired['detailsAvailable']
    assert expired['delivery'] == 'accepted' and not expired['resent']


@pytest.mark.parametrize('stopped', [False, True])
async def test_update_hold_retains_multiple_public_sources_without_global_turn_wait(app_factory, tmp_path, stopped):
    from amplifier_web.updates import UpdateManager
    from amplifier_web import update_drain as drain
    from test_update_drain import healthy
    app = app_factory()
    session = await room(app, tmp_path)
    sid = session['id']
    app.update_manager = UpdateManager(app)
    app.state['updates']['drain'] = {
        'attemptId': 'a' * 32, 'target': {'attemptId': 'a' * 32, 'revision': 'b' * 40, 'generation': None},
        'phase': 'draining', 'hostInstanceId': app.instance_id, 'workers': {}, 'messages': []}
    for identity in ('one', 'two'):
        receipt = await app.dispatch('conversation.send', {'sessionId': sid, 'text': identity}, command_id=identity)
        assert receipt['delivery'] == 'queued_for_update'
    await app.external_conversations.resume_pending()
    assert not app.external_conversations.shared.turns
    assert not (tmp_path / 'alpha/peer').exists() and not (tmp_path / 'beta/peer').exists()
    if stopped:
        await app.dispatch('conversation.stop', {'sessionId': sid}, command_id='stop-held')
    healthy(app)
    await drain.finish(app.update_manager, 'installed')
    assert app.state['updates']['drain']['phase'] == 'released'
    await idle(app, sid)
    await drain.finish(app.update_manager, 'installed')
    assert [row['inputId'] for row in session['messages'] if row['role'] == 'user'] == ['one', 'two']
    if stopped:
        assert not (tmp_path / 'alpha/peer').exists() and not (tmp_path / 'beta/peer').exists()
        assert all(row['state'] == 'cancelled' for row in session['_externalRoom']['deliveries'])
    else:
        assert all([row['source'] for row in work(tmp_path, identity)] == ['one', 'two'] for identity in ('alpha', 'beta'))
