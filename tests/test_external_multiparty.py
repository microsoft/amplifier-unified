"""Independent real ACP peers sharing only the host-owned public conversation."""
import json

import pytest
from acp.schema import PermissionOption

from amplifier_web.external_conversation import bindings, shared_context
from amplifier_web.service import AppError
from test_acp_participant import operations, peer
from test_automatic_history import app_factory
from test_external_conversation import until


def configure(app, path):
    app.server_config = {'externalAgents': [
        {**peer(path / identity), 'id': identity, 'label': identity.title()}
        for identity in ('alpha', 'beta')]}


async def room(app, path):
    configure(app, path)
    result = await app.dispatch('session.create', {
        'workspace': app.default_workspace, 'externalAgentIds': ['alpha', 'beta']}, command_id='room-create')
    return app._session(result['result']['sessionId'])


async def directed(app, session, agent, text, command):
    result = await app.dispatch('conversation.send', {
        'sessionId': session['id'], 'recipientAgentId': agent, 'text': text}, command_id=command)
    assert result['accepted'] and result['delivery'] == 'sending'
    await until(lambda: session['id'] not in app.external_conversations.turns)
    return result


async def test_independent_bindings_attributed_context_resume_and_exact_delivery(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    sid = session['id']
    await directed(app, session, 'alpha', 'shared result 42', 'alpha-one')
    await directed(app, session, 'beta', 'consider Alpha’s result', 'beta-one')
    identities = {row['id']: row['sessionId'] for row in bindings(session)}
    assert len(set(identities.values())) == 2
    assert [(m['role'], m.get('agentId') or m.get('recipientAgentId')) for m in session['messages']] == [
        ('user', 'alpha'), ('assistant', 'alpha'), ('user', 'beta'), ('assistant', 'beta')]
    beta = [row for row in operations(tmp_path / 'beta') if row['kind'] == 'prompt']
    assert len(beta) == 1 and beta[0]['text'] == 'consider Alpha’s result'
    context = json.loads(beta[0]['context'][0].split('\n', 1)[1])
    assert context['conversationId'] == sid
    assert context['messages'][-1]['text'] == 'Reply: shared result 42'
    assert context['messages'][-1]['agentLabel'] == 'Alpha'
    assert 'consider Alpha’s result' not in beta[0]['context'][0]
    for projection in (app.browser_state(sid), app.session_state(sid)):
        public = next(row for row in projection['sessions'] if row['id'] == sid)
        assert len(public['externalAgent']['participants']) == 2
        assert '_externalAgentSource' not in public
    await app.close()
    restored = app_factory()
    configure(restored, tmp_path)
    session = restored._session(sid)
    assert {row['id']: row['sessionId'] for row in bindings(session)} == identities
    for agent in ('alpha', 'beta'):
        await directed(restored, session, agent, 'continue', agent + '-two')
        observed = operations(tmp_path / agent)
        assert [row['kind'] for row in observed] == ['new', 'prompt', 'resume', 'prompt']
        assert all(row.get('sessionId') == identities[agent] for row in observed)
        result = (await restored.dispatch('conversation.delivery', {
            'sessionId': sid, 'inputId': agent + '-one'}))['result']
        assert result['source']['externalAgentId'] == agent
        assert result['source']['externalSessionId'] == identities[agent]
    duplicate = await restored.dispatch('conversation.send', {
        'sessionId': sid, 'recipientAgentId': 'beta', 'text': 'continue'}, command_id='beta-two')
    assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
    assert len([row for row in operations(tmp_path / 'beta') if row['kind'] == 'prompt']) == 2


async def test_room_stop_and_late_callbacks_do_not_cross_participant_bindings(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    await directed(app, session, 'beta', 'ready', 'beta-ready')
    beta_identity = bindings(session)[1]['sessionId']
    access = (await app.dispatch('access.get', {'sessionId': session['id']}))['result']
    await app.dispatch('access.set', {'sessionId': session['id'], 'scope': 'session',
        'mode': 'full', 'expectedRevision': access['revision']})
    await app.dispatch('conversation.send', {'sessionId': session['id'],
        'recipientAgentId': 'alpha', 'text': 'wait-for-stop'}, command_id='alpha-wait')
    await until(lambda: bool(session.get('streaming')))
    text = session['streaming']
    await app.external_conversations._update(session['id'], 'beta', {
        'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'wrong author'}})
    assert session['streaming'] == text
    denied = await app.external_conversations._permission(session['id'], 'beta', [
        PermissionOption(option_id='allow-this', name='Allow', kind='allow_once')])
    assert denied == {'outcome': {'outcome': 'cancelled'}}
    with pytest.raises(AppError, match='still working'):
        await app.dispatch('conversation.send', {'sessionId': session['id'],
            'recipientAgentId': 'beta', 'text': 'overlap'}, command_id='beta-overlap')
    await app.dispatch('conversation.stop', {'sessionId': session['id']}, command_id='room-stop')
    await until(lambda: session['id'] not in app.external_conversations.turns)
    session = app._session(session['id'])
    assert session['messages'][-1]['agentId'] == 'alpha', session['messages']
    assert operations(tmp_path / 'alpha')[-1]['kind'] == 'cancel'
    assert not any(row['kind'] == 'cancel' for row in operations(tmp_path / 'beta'))
    await directed(app, session, 'beta', 'after stop', 'beta-after-stop')
    assert bindings(session)[1]['sessionId'] == beta_identity
    assert session['messages'][-1]['agentId'] == 'beta'


async def test_invalid_target_and_changed_source_are_rejected_before_acceptance(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    for identity in (None, 'absent'):
        with pytest.raises(AppError, match='Choose an agent'):
            await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'invalid',
                **({'recipientAgentId': identity} if identity else {})})
    assert not session['messages']
    app.server_config['externalAgents'][0]['args'].append('--changed')
    with pytest.raises(AppError, match='endpoint changed'):
        await app.dispatch('conversation.send', {'sessionId': session['id'],
            'recipientAgentId': 'alpha', 'text': 'must not execute'})
    assert not session['messages'] and not (tmp_path / 'alpha/peer/operations.jsonl').exists()
    await directed(app, session, 'beta', 'unchanged member', 'beta-unaffected')
    assert session['messages'][-1]['agentId'] == 'beta'


async def test_unknown_input_keeps_its_recipient_and_never_replays_to_another_agent(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    await directed(app, session, 'alpha', 'drop-after-effect', 'uncertain-alpha')
    original = session['messages'][0]
    assert original['delivery']['status'] == 'unknown'
    await directed(app, session, 'beta', 'explicit next request', 'next-beta')
    assert original['delivery']['status'] == 'unknown'
    receipt = (await app.dispatch('conversation.delivery', {
        'sessionId': session['id'], 'inputId': 'uncertain-alpha'}))['result']
    assert receipt['source']['externalAgentId'] == 'alpha' and not receipt['resent']
    assert [row['text'] for row in operations(tmp_path / 'alpha') if row['kind'] == 'prompt'] == ['drop-after-effect']
    assert [row['text'] for row in operations(tmp_path / 'beta') if row['kind'] == 'prompt'] == ['explicit next request']


def test_shared_context_is_bounded_public_history_with_explicit_omission():
    messages = [{'id': str(i), 'role': 'assistant', 'text': str(i) + 'x' * 2000,
                 'agentId': 'alpha', 'agentLabel': 'Alpha', 'privateContext': 'never share'}
                for i in range(100)]
    messages.append({'id': 'current', 'role': 'user', 'text': 'current only', 'inputId': 'input'})
    session = {'id': 'room', 'externalAgent': {'participants': [{'id': 'alpha'}, {'id': 'beta'}]}, 'messages': messages}
    context = shared_context(session, 'input')
    value = json.loads(context.split('\n', 1)[1])
    assert value['earlierMessagesOmitted']
    assert len(value['messages']) < 32
    assert sum(len(row['text']) for row in value['messages']) <= 24000
    assert value['messages'][-1]['id'] == '99'
    assert 'current only' not in context and 'privateContext' not in context
    session['messages'] = [dict(messages[0], text='x' * 24001), messages[-1]]
    omitted = json.loads(shared_context(session, 'input').split('\n', 1)[1])
    assert omitted['messages'] == [] and omitted['earlierMessagesOmitted']


async def test_passive_restart_preserves_partial_author_and_unknown_recipient(app_factory, tmp_path):
    app = app_factory()
    session = await room(app, tmp_path)
    await directed(app, session, 'beta', 'establish binding', 'beta-known')
    remote = bindings(session)[1]['sessionId']
    # Persist the same host facts left by an interrupted streaming process.
    app._message(session, 'user', 'interrupted input', inputId='interrupted-beta',
                 recipientAgentId='beta', recipientAgentLabel='Beta', delivery={'status': 'sending'})
    session.update(status='working', streaming='Partial Beta answer',
                   streamingInputId='interrupted-beta', streamingId='partial-stream')
    session['externalAgent'].update(status='working', activeInputId='interrupted-beta', activeAgentId='beta')
    bindings(session)[1]['status'] = 'working'
    app._publish_changes(sessions={session['id']})
    await app.close()
    reopened = app_factory()
    configure(reopened, tmp_path)
    restored = reopened._session(session['id'])
    assert restored['messages'][-1]['partial']
    assert restored['messages'][-1]['agentId'] == 'beta'
    assert restored['messages'][-1]['agentLabel'] == 'Beta'
    assert restored['messages'][-2]['delivery']['status'] == 'unknown'
    assert bindings(restored)[1]['sessionId'] == remote
    assert bindings(restored)[1]['status'] == 'unknown'
    assert 'activeInputId' not in restored['externalAgent']
    assert [row['kind'] for row in operations(tmp_path / 'beta')] == ['new', 'prompt']
