"""Real ACP SDK and canonical host decisions, with controlled native I/O."""
import asyncio

import pytest

from amplifier_web.errors import AppError
from test_acp_server import host, connect, finish
from test_coordination_generation import agent, source_plan, until


@pytest.mark.parametrize('choice', ['allow', 'deny', 'cancelled'])
async def test_configuration_wait_reaches_acp_and_exact_decision_wakes_it(host, choice):
    application, _, runtime, path = host
    service = application['service']
    peer = await connect(host)
    sid = await peer.new(path)
    session = service._session(sid)
    source_plan(service, session)
    seen, decisions = [], []
    async def permission(params):
        seen.append(params)
        assert params['sessionId'] == sid
        assert {o['kind'] for o in params['options']} == {'allow_once', 'reject_once'}
        assert 'This conversation only, until Stop' in str(params['toolCall'])
        return {'outcome': 'cancelled'} if choice == 'cancelled' else {'outcome': 'selected', 'optionId': choice}
    peer.permission = permission
    async def decide(owner, identity, decision, comment=None):
        decisions.append((owner, identity, decision))
        await finish(service, owner, runtime.sent[-1][2])
    runtime.approval = decide
    prompt = asyncio.create_task(peer.prompt(sid, 'A fresh synthetic request'))
    await until(lambda: bool(runtime.sent))
    session['collaborationGeneration'] = {'id': 'fixture-generation', 'inputIds': [runtime.sent[-1][2]], 'terminal': False}
    grant = (await agent(service, session, 'capability.request', {'sessionId': sid,
        'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Synthetic workspace setup'},
        'fixture-request', _configurationWait=True))['result']
    await service.on_runtime_event('approval.requested', {'sessionId': sid, 'id': grant['id']})
    assert (await prompt)['result']['stopReason'] == 'end_turn'
    assert len(seen) == len(decisions) == len(session['approvals']) == 1
    expected = 'deny' if choice in {'deny', 'cancelled'} else 'allow'
    assert decisions == [(sid, grant['id'], expected)]
    current = service.configuration_authority.record(grant['id'])
    assert current['status'] == ('active' if expected == 'allow' else 'denied')
    assert not service.access_mode.full(session)
    assert len(runtime.sent) == 1 and not runtime.stopped
    assert not service.state.get('managementResults'), 'The decision must not run a settings mutation'


async def test_acp_reply_after_stop_cannot_grant(host):
    application, _, runtime, path = host
    service = application['service']
    peer = await connect(host)
    sid = await peer.new(path)
    session = service._session(sid)
    source_plan(service, session)
    entered, release = asyncio.Event(), asyncio.Event()
    async def permission(params):
        entered.set()
        await release.wait()
        return {'outcome': 'selected', 'optionId': 'allow'}
    peer.permission = permission
    calls = []
    async def approval(*args): calls.append(args)
    runtime.approval = approval
    prompt = asyncio.create_task(peer.prompt(sid, 'A fresh synthetic request'))
    await until(lambda: bool(runtime.sent))
    await service.on_runtime_event('approval.requested', {'sessionId': sid, 'id': 'tool-request',
        'prompt': 'Write reviewed file', 'options': ['allow', 'deny']})
    await entered.wait()
    await service.dispatch('conversation.stop', {'sessionId': sid})
    release.set()
    await until(lambda: not service._execution_observers.attachments or
        all(not owner.pending for owner in service._execution_observers.attachments))
    await prompt
    assert not calls
    assert runtime.stopped == [sid]


@pytest.mark.parametrize('change', ['request', 'execution', 'unoffered'])
async def test_changed_request_or_unoffered_reply_cannot_authorize(host, change):
    application, _, runtime, path = host
    service = application['service']
    peer = await connect(host)
    sid = await peer.new(path)
    session = service._session(sid)
    source_plan(service, session)
    calls = []

    async def approval(*args):
        calls.append(args)

    async def permission(params):
        if change == 'request':
            session['approvals'][0]['prompt'] = 'A different request'
        elif change == 'execution':
            session['executionRevision'] = session.get('executionRevision', 0) + 1
        return {'outcome': 'selected', 'optionId': 'allow_always' if change == 'unoffered' else 'allow'}

    runtime.approval = approval
    peer.permission = permission
    prompt = asyncio.create_task(peer.prompt(sid, 'A fresh synthetic request'))
    await until(lambda: bool(runtime.sent))
    await service.on_runtime_event('approval.requested', {'sessionId': sid, 'id': 'request',
        'prompt': 'Write the reviewed file', 'options': ['allow', 'deny']})
    result = await prompt
    assert 'error' in result
    assert not calls and len(runtime.sent) == 1
    assert session['approvals'][0]['status'] == 'pending'
    assert not service.access_mode.full(session)


async def test_duplicate_native_notification_preserves_the_host_scope(host):
    application, _, runtime, path = host
    service = application['service']
    peer = await connect(host)
    sid = await peer.new(path)
    session = service._session(sid)
    source_plan(service, session)
    # No active ACP input: this checks the canonical card/event boundary alone.
    session['collaborationGeneration'] = {'id': 'fixture-generation', 'inputIds': ['fixture-input'], 'terminal': False}
    grant = (await agent(service, session, 'capability.request', {'sessionId': sid,
        'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Synthetic setup'},
        'scope-owner', _configurationWait=True))['result']
    original = dict(session['approvals'][0])
    for _ in range(2):
        await service.on_runtime_event('approval.requested', {'sessionId': sid,
            'id': grant['id'], 'prompt': 'Spoofed replacement', 'status': 'allow',
            'effects': ['host_defaults'], 'scopes': ['global']})
    assert session['approvals'] == [original]
    assert service.configuration_authority.record(grant['id'])['status'] == 'pending'
    assert not runtime.sent
