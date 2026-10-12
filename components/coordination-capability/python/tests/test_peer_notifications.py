import copy
import pytest
from test_grants import Host, S, T, action, noop
from test_peer import PeerHost
from amplifier_unified_coordination.owner import Owner


def notification(command='notify'):
    return {**action('send', {'sessionId': S, 'recipientSessionId': T, 'grantId': 'grant',
                             'mode': 'notify', 'text': 'ORBIT **572** is ready'}, command=command),
            'deliveryEnabled': True}


async def setup(tmp_path, host_type=Host):
    host = host_type()
    owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    await owner.request('action', action('grant', {'sessionId': S, 'participants': [T],
        'purpose': 'Share review notes', 'modes': ['notify'], 'idleStart': False}, command='grant', origin='ui'))
    return host, owner


@pytest.mark.asyncio
async def test_notify_never_starts_work_survives_restart_and_ack_is_recipient_scoped(tmp_path):
    host, owner = await setup(tmp_path)
    receipt = (await owner.request('action', notification()))['receipt']
    assert receipt['status'] == 'notified'
    assert (await owner.request('action', notification()))['receipt'] == receipt
    messages = await owner.request('peer.messages', {'session': T})
    assert len(messages['notifications']) == 1
    assert not messages['notifications'][0]['contextDelivered']
    assert not (await owner.request('peer.messages', {'session': S}))['notifications']
    await owner.close()
    owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try:
        events = (await owner.request('peer.notifications', {'session': T, 'args': {}}))['notifications']
        assert [row['id'] for row in events] == [receipt['inputId']]
        assert owner.receipt('notify')['status'] == 'notified'
        with pytest.raises(ValueError, match='recipient'):
            await owner.request('peer.notifications', {'session': S, 'args': {'acknowledge': [receipt['inputId']]}})
        with pytest.raises(ValueError, match='recipient'):
            await owner.request('peer.notifications', {'session': T, 'args': {'acknowledge': [receipt['inputId'], 'not-saved']}})
        assert not owner.receipt('notify').get('contextDelivered')
        for _ in range(2):
            result = await owner.request('peer.notifications', {'session': T, 'args': {'acknowledge': [receipt['inputId']]}})
            assert result == {'notifications': [], 'executionStarted': False}
        assert (await owner.request('peer.messages', {'session': T}))['notifications'][0]['contextDelivered']
        # Host raises on every unlisted callback, including admission/watch/start.
        assert set(method for method, _ in host.calls) == {'inspectCoordinationIdentity'}
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoked', 'location', 'scope'])
async def test_permissions_rechecked_before_context_delivery(tmp_path, change):
    host, owner = await setup(tmp_path)
    try:
        await owner.request('action', notification())
        if change == 'revoked':
            await owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'grant'}, command='revoke', origin='ui'))
        elif change == 'location': host.identities[T]['locationRevision'] += 1
        else: host.identities[T]['workspace'] = '/moved'
        assert not (await owner.request('peer.notifications', {'session': T, 'args': {}}))['notifications']
        retained = (await owner.request('peer.messages', {'session': T}))['notifications']
        assert len(retained) == 1 and retained[0]['contextSuppressed']
    finally: await owner.close()


@pytest.mark.asyncio
async def test_notify_requires_mode_permission_and_quiescence_allows_reads_only(tmp_path):
    host, owner = await setup(tmp_path, PeerHost)
    try:
        queued = copy.deepcopy(notification()); queued['args']['mode'] = 'queue'
        with pytest.raises(ValueError): await owner.request('action', queued)
        fence = {'fenceId': 'held', 'commandId': 'update', 'purpose': 'distribution-update', 'instanceId': 'original', 'dataScope': 'owned'}
        assert (await owner.request('quiescence.acquire', fence))['acquired']
        assert (await owner.request('peer.messages', {'session': T}))['notifications'] == []
        with pytest.raises(ValueError, match='intake is closed'): await owner.request('action', notification())
        with pytest.raises(ValueError, match='intake is closed'): await owner.request('peer.notifications', {'session': T, 'args': {}})
    finally: await owner.close()
