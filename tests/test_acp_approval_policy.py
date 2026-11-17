"""Negotiated, canonical scopes without an engine-side persistent policy store."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.approval_policy import NAMESPACE, PRODUCER, descriptor
from amplifier_web.errors import AppError
from test_acp_server import host, Peer, finish
from test_configuration_authority import app
from test_coordination_generation import agent, source_plan, until

PROFILE = {'version': 1, 'owner': 'instance', 'instanceId': 'e085aae5-475b-4990-a675-001e1f17b2c4'}


async def connect(host, profile):
    application, client, _, path = host
    ws = await client.ws_connect('/api/acp', params={'workspace': str(path)},
        headers={'Authorization': 'Bearer ' + application['control_token']})
    peer = Peer(ws)
    result = await peer.call('initialize', protocolVersion=1, _meta={NAMESPACE: profile})
    return peer, result['result'].get('_meta', {}).get(NAMESPACE)


@pytest.mark.parametrize('profile', [None, {}, {**PROFILE, 'version': True},
    {**PROFILE, 'version': 2}, {**PROFILE, 'owner': 'connector'}, {**PROFILE, 'instanceId': 'invented'}])
async def test_unsupported_policy_negotiation_keeps_ordinary_acp(host, profile):
    peer, policy = await connect(host, profile)
    assert policy is None
    assert await peer.new(host[3])
    owner, = host[0]['service']._execution_observers.attachments
    assert not owner.approval_policy


@pytest.mark.parametrize('native', [True, False])
async def test_sdk_emits_only_negotiated_canonical_scope(host, native):
    application, _, runtime, path = host
    service = application['service']
    peer, policy = await connect(host, PROFILE)
    assert policy == {'version': 1, 'owner': 'instance', 'producer': PRODUCER, 'reusableRequests': True}
    sid = await peer.new(path)
    session = service._session(sid)
    source_plan(service, session)
    seen = []

    async def permission(params):
        seen.append(params)
        meta = params['toolCall'].get('_meta', {}).get(NAMESPACE)
        if native:
            assert meta == descriptor(service, session, session['approvals'][0])
            assert meta['authorityLifetime'] == 'conversation'
            assert meta['allowOptionId'] == 'allow' and len(meta['key']) == 64
            assert 'across this agent' in meta['description']
        else:
            assert meta is None, 'Unstructured tool metadata must not mint a reusable scope'
        assert {o['kind'] for o in params['options']} == {'allow_once', 'reject_once'}
        return {'outcome': 'selected', 'optionId': 'allow'}

    async def approve(owner, identity, decision, comment=None):
        await finish(service, owner, runtime.sent[-1][2])

    peer.permission = permission
    runtime.approval = approve
    prompt = asyncio.create_task(peer.prompt(sid, 'Synthetic policy request'))
    await until(lambda: bool(runtime.sent))
    session['collaborationGeneration'] = {'id': 'generation', 'inputIds': [runtime.sent[-1][2]], 'terminal': False}
    if native:
        grant = await request(service, session, 'native')
        event = {'sessionId': sid, 'id': grant['id']}
    else:
        event = {'sessionId': sid, 'id': 'opaque', 'prompt': 'A generic tool request',
                 'options': ['allow', 'deny'], 'policyScope': {'producer': PRODUCER, 'key': '0' * 64}}
    await service.on_runtime_event('approval.requested', event)
    assert (await prompt)['result']['stopReason'] == 'end_turn'
    assert len(seen) == len(runtime.sent) == 1
    assert not service.access_mode.full(session)


async def request(service, session, identity, **changes):
    args = {'sessionId': session['id'], 'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Reviewed setup'}
    args.update(changes)
    return (await agent(service, session, 'capability.request', args, identity, _configurationWait=True))['result']


def scope(service, session, grant):
    return descriptor(service, session, next(c for c in session['approvals'] if c['id'] == grant['id']))


async def test_keys_match_across_chats_but_bind_actual_target_and_effect(app, tmp_path):
    first, second = app.state['sessions']
    second['workspace'] = first['workspace']
    second['collaborationGeneration'] = copy.deepcopy(first['collaborationGeneration'])
    source_plan(app, second)
    a = await request(app, first, 'a')
    b = await request(app, second, 'b', reason='Different human wording', expiresAt=4_000_000_000)
    original = scope(app, first, a)
    assert original['key'] == scope(app, second, b)['key']
    for name, changes in [('effect', {'effects': ['host_defaults']}), ('scope', {'scopes': ['global']})]:
        other = await request(app, first, name, **changes)
        assert scope(app, first, other)['key'] != original['key']
    destination = tmp_path/'different-workspace'
    destination.mkdir()
    second['workspace'] = str(destination)
    changed = await request(app, second, 'changed-workspace')
    assert scope(app, second, changed)['key'] != original['key']
    opaque = await request(app, first, 'session-only', scopes=['session'])
    assert scope(app, first, opaque) is None
    assert not app.access_mode.full(first)


async def test_changed_resolved_target_refuses_before_grant(app, tmp_path):
    source = app.state['sessions'][0]
    one, two, link = tmp_path/'one', tmp_path/'two', tmp_path/'target'
    one.mkdir(); two.mkdir(); link.symlink_to(one, target_is_directory=True)
    source['workspace'] = str(link)
    grant = await request(app, source, 'linked')
    old = scope(app, source, grant)
    observed = app.configuration_authority.request_view(source, grant['id'])
    link.unlink(); link.symlink_to(two, target_is_directory=True)
    assert scope(app, source, grant)['key'] != old['key']
    with pytest.raises(AppError, match='target changed'):
        await app.dispatch('approval.respond', {'sessionId': source['id'], 'id': grant['id'],
            'decision': 'allow', 'expectedRequestHash': observed['requestHash'], 'expectedApprovalScope': old['key']})
    assert app.configuration_authority.record(grant['id'])['status'] == 'pending'


@pytest.mark.parametrize('parts', [('é' * 75, 'é' * 75), ('line\nbreak',), ('del\x7fname',)])
def test_unrepresentable_display_scope_keeps_once_only(tmp_path, parts):
    workspace = tmp_path.joinpath(*parts)
    workspace.mkdir(parents=True)
    source = {'id': 'synthetic-owner', 'workspace': str(workspace), 'interruptionRevision': 0}
    grant = {'id': 'capability:synthetic', 'status': 'pending', 'sessionId': source['id'],
             'workspace': str(workspace), 'interruptionRevision': 0, 'effects': ['file_access'], 'scopes': ['local']}
    # Test the wire-boundary producer independently of legacy project-directory
    # naming limits; SDK and real canonical-grant paths are exercised above.
    service = SimpleNamespace(data_dir=tmp_path/'app',
        configuration_authority=SimpleNamespace(record=lambda identity: grant if identity == grant['id'] else None))
    assert descriptor(service, source, {'id': grant['id'], 'status': 'pending'}) is None
    assert grant['status'] == 'pending'


async def test_global_scope_discloses_shared_settings_destination(app):
    source = app.state['sessions'][0]
    grant = await request(app, source, 'global', scopes=['global'])
    value = scope(app, source, grant)
    from amplifier_web.preferences import SettingsStore
    assert 'shared Amplifier settings' in value['description']
    assert str(SettingsStore(app.data_dir).path(source['workspace'], 'global').resolve()) in value['description']
    assert len(value['description'].encode('utf-8')) <= 512
