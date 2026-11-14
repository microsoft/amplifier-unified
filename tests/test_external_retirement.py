"""Ordinary public inputs retain identity across conditional attachment cleanup."""
import asyncio
from pathlib import Path
import sys

import pytest

from test_automatic_history import app_factory  # noqa: F401
from test_acp_participant import operations
from test_external_conversation import send, until
from test_shared_conversation import send as post, idle, work

FIXTURE = str(Path(__file__).parent / 'fixtures/retiring_acp_agent.py')


async def create(app, path, *, shared=False):
    identities = ['alpha', 'beta'] if shared else ['one']
    app.server_config = {'externalAgents': [{'id': identity, 'label': identity,
        'command': sys.executable, 'args': [FIXTURE, str(path / identity / 'peer'),
                                            *([identity] if shared else [])]}
        for identity in identities]}
    result = await app.dispatch('session.create', {'workspace': app.default_workspace,
        **({'externalAgentIds': identities} if shared else {'externalAgentId': 'one'})})
    return app._session(result['result']['sessionId'])


def configure(path, identity, name, text=''):
    home = path / identity / 'peer'
    home.mkdir(parents=True, exist_ok=True)
    (home / name).write_text(text)


def recorded(path, identity, kind):
    file = path / identity / 'peer/operations.jsonl'
    return [row for row in operations(path / identity) if row['kind'] == kind] if file.exists() else []


async def test_singleton_cleanup_and_next_input_resume_exact_identity_once(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'first', 'first')
    identity = session['externalAgent']['sessionId']
    assert app.external_conversations.participants and not recorded(tmp_path, 'one', 'retire')
    configure(tmp_path, 'one', 'notify')
    await until(lambda: not app.external_conversations.participants and not app.external_conversations.retirements)
    await send(app, session, 'second', 'second')
    configure(tmp_path, 'one', 'notify')
    await until(lambda: not app.external_conversations.participants and not app.external_conversations.retirements)
    assert session['externalAgent']['sessionId'] == identity
    assert len(recorded(tmp_path, 'one', 'new')) == 1
    assert recorded(tmp_path, 'one', 'resume') == [{'kind': 'resume', 'sessionId': identity}]
    assert [row['text'] for row in recorded(tmp_path, 'one', 'prompt')] == ['first', 'second']
    assert [row['text'] for row in session['messages']] == ['first', 'Reply: first', 'second', 'Reply: second']


@pytest.mark.parametrize('unsupported', ['no-resume', 'legacy-notification'])
async def test_unsupported_retirement_keeps_the_warm_connection(app_factory, tmp_path, unsupported):
    configure(tmp_path, 'one', unsupported)
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'first', 'first')
    peer = app.external_conversations.participants[(session['id'], 'one')]
    assert not peer.supports_retirement and peer.process.returncode is None
    await send(app, session, 'second', 'second')
    configure(tmp_path, 'one', 'notify')
    await until(lambda: not (tmp_path / 'one/peer/notify').exists())
    assert len(recorded(tmp_path, 'one', 'new')) == 1
    assert not recorded(tmp_path, 'one', 'retire') and not recorded(tmp_path, 'one', 'resume')
    assert [row['text'] for row in recorded(tmp_path, 'one', 'prompt')] == ['first', 'second']


async def test_supported_peer_reuses_warm_connection_until_policy_requests_retirement(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'first', 'first')
    peer = app.external_conversations.participants[(session['id'], 'one')]
    process = peer.process
    await send(app, session, 'second', 'second')
    assert peer.process is process and process.returncode is None
    assert not recorded(tmp_path, 'one', 'retire') and not recorded(tmp_path, 'one', 'resume')
    assert len(recorded(tmp_path, 'one', 'new')) == 1
    configure(tmp_path, 'one', 'notify')
    await until(lambda: not app.external_conversations.participants and not app.external_conversations.retirements)
    assert process.returncode is not None


@pytest.mark.parametrize('disposition', ['busy', 'unknown', 'malformed', 'silent'])
async def test_unconfirmed_cleanup_retains_transport_then_reconciles_without_replay(app_factory, tmp_path, monkeypatch, disposition):
    import amplifier_web.acp_participant as transport
    monkeypatch.setattr(transport, 'RETIREMENT_TIMEOUT', .1)
    configure(tmp_path, 'one', 'retirement', disposition)
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'first', 'first')
    configure(tmp_path, 'one', 'notify')
    await until(lambda: recorded(tmp_path, 'one', 'retire') and not app.external_conversations.retirements)
    key = (session['id'], 'one')
    peer = app.external_conversations.participants[key]
    identity, process = peer.session_id, peer.process
    assert process.returncode is None
    assert len(recorded(tmp_path, 'one', 'prompt')) == 1
    configure(tmp_path, 'one', 'retirement', 'retired')
    if disposition == 'busy':
        configure(tmp_path, 'one', 'notify')
        await until(lambda: key not in app.external_conversations.participants)
    await send(app, session, 'second', 'second')
    configure(tmp_path, 'one', 'notify')
    await until(lambda: not app.external_conversations.retirements and key not in app.external_conversations.participants)
    assert process.returncode is not None and session['externalAgent']['sessionId'] == identity
    assert len(recorded(tmp_path, 'one', 'new')) == 1
    assert [row['text'] for row in recorded(tmp_path, 'one', 'prompt')] == ['first', 'second']


@pytest.mark.parametrize('stop', [False, True])
async def test_new_singleton_input_waits_cleanup_and_stop_never_closes_its_background_transport(app_factory, tmp_path, stop):
    configure(tmp_path, 'one', 'hold-retire')
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'first', 'first')
    configure(tmp_path, 'one', 'notify')
    await until(lambda: recorded(tmp_path, 'one', 'retire'))
    peer = app.external_conversations.participants[(session['id'], 'one')]
    process = peer.process
    receipt = await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'second'}, command_id='second')
    assert receipt['accepted'] and len(recorded(tmp_path, 'one', 'prompt')) == 1
    if stop:
        await app.dispatch('conversation.stop', {'sessionId': session['id']})
        assert process.returncode is None and app.external_conversations.retirements
    configure(tmp_path, 'one', 'release-retire')
    await until(lambda: not app.external_conversations.retirements and session['id'] not in app.external_conversations.turns)
    assert [row['text'] for row in recorded(tmp_path, 'one', 'prompt')] == (['first'] if stop else ['first', 'second'])
    assert len(recorded(tmp_path, 'one', 'new')) == 1
    assert not recorded(tmp_path, 'one', 'cancel')


async def test_shared_cleanup_fence_keeps_public_acceptance_and_other_peer_progress(app_factory, tmp_path):
    configure(tmp_path, 'alpha', 'hold-retire')
    app = app_factory()
    session = await create(app, tmp_path, shared=True)
    sid = session['id']
    await post(app, sid, 'first', 'first')
    await idle(app, sid)
    configure(tmp_path, 'alpha', 'notify')
    await until(lambda: recorded(tmp_path, 'alpha', 'retire'))
    original = {row['id']: row['sessionId'] for row in session['externalAgent']['participants']}
    await post(app, sid, 'second', 'second')
    await until(lambda: any(row['source'] == 'second' for row in work(tmp_path, 'beta')))
    assert [row['source'] for row in work(tmp_path, 'alpha')] == ['first']
    assert any(row['agentId'] == 'alpha' and row['inputId'] == 'second' and row['state'] == 'pending'
               for row in session['_externalRoom']['deliveries'])
    configure(tmp_path, 'alpha', 'release-retire')
    await idle(app, sid)
    for identity in ('alpha', 'beta'):
        configure(tmp_path, identity, 'notify')
    await until(lambda: not app.external_conversations.retirements and not app.external_conversations.participants)
    assert {row['id']: row['sessionId'] for row in session['externalAgent']['participants']} == original
    assert all([row['source'] for row in work(tmp_path, identity)] == ['first', 'second'] for identity in original)
    assert len([row for row in session['messages'] if row['role'] == 'user']) == 2


async def test_shared_observation_outliving_prompt_prevents_retirement(app_factory, tmp_path):
    configure(tmp_path, 'alpha', 'hold-output')
    configure(tmp_path, 'beta', 'awareness')
    app = app_factory()
    session = await create(app, tmp_path, shared=True)
    sid = session['id']
    await post(app, sid, 'busy exchange', 'exchange')
    await until(lambda: recorded(tmp_path, 'beta', 'work'))
    configure(tmp_path, 'alpha', 'release-output')
    await until(lambda: recorded(tmp_path, 'beta', 'observing'))
    configure(tmp_path, 'beta', 'release')
    await until(lambda: (sid, 'beta') not in app.external_conversations.shared.turns)
    assert any(row['agentId'] == 'beta' and row['state'] == 'observing' for row in session['_externalRoom']['deliveries'])
    configure(tmp_path, 'beta', 'notify')
    await until(lambda: not (tmp_path / 'beta/peer/notify').exists())
    assert not recorded(tmp_path, 'beta', 'retire')
    configure(tmp_path, 'beta', 'release-awareness')
    await until(lambda: not any(row['agentId'] == 'beta' and row['state'] == 'observing'
                               for row in session['_externalRoom']['deliveries']))
    configure(tmp_path, 'beta', 'notify')
    await until(lambda: recorded(tmp_path, 'beta', 'retire'))
    await idle(app, sid)
    assert [row['source'] for row in work(tmp_path, 'beta')] == ['busy exchange']
