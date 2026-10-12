import copy
import pytest
from test_peer_notifications import setup, notification
from test_grants import S, T
from amplifier_unified_coordination.attribution import binding
from amplifier_unified_coordination.owner import Owner
from test_grants import noop


@pytest.mark.asyncio
async def test_origin_is_saved_by_owner_and_read_without_delivery_or_replay(tmp_path):
    host, owner = await setup(tmp_path)
    for origin in ('ui', 'agent'):
        args = notification(origin); args['origin'] = origin; args['clientId'] = 'test-client'
        row = (await owner.request('action', args))['receipt']
        assert row['sourceOrigin'] == origin
    before = dict(owner.db.execute('SELECT id,body FROM commands'))
    value = await owner.request('peer.messages', {'session': T})
    assert {row['origin'] for row in value['attributions']} == {'ui', 'agent'}
    assert not (await owner.request('peer.messages', {'session': S}))['attributions']
    assert dict(owner.db.execute('SELECT id,body FROM commands')) == before
    assert {method for method, _ in host.calls} == {'inspectCoordinationIdentity'}
    await owner.close()
    owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try: assert (await owner.request('peer.messages', {'session': T}))['attributions'] == value['attributions']
    finally: await owner.close()


@pytest.mark.asyncio
async def test_unproven_origins_conflicting_receipts_and_unadmitted_requests_stay_neutral(tmp_path):
    _, owner = await setup(tmp_path)
    try:
        row = (await owner.request('action', notification()))['receipt']
        for patch in ({'sourceOrigin': None}, {'sourceOrigin': 'user-prose'}, {'mode': 'queue', 'peerEnvelope': {**row['peerEnvelope'], 'mode': 'queue'}}, {'peerEnvelope': {**row['peerEnvelope'], 'recipientSessionId': S}}):
            owner.peer.save({**row, **patch})
            assert not (await owner.request('peer.messages', {'session': T}))['attributions']
        owner.peer.save(row)
        duplicate = copy.deepcopy(row); duplicate['commandId'] = 'duplicate'
        owner.peer.save(duplicate)
        assert not (await owner.request('peer.messages', {'session': T}))['attributions']
    finally: await owner.close()


def test_binding_is_exact_and_unicode_preserving():
    assert binding('Ready ♥', {'mode': 'notify', 'version': 1}) == '829d96b7272c2d67457e4a1b3f2457a986eb11ada9f40f2dc4b5f5b707832c35'
    assert binding('Ready ♥', {'mode': 'notify', 'version': 1}) != binding('Ready', {'mode': 'notify', 'version': 1})


@pytest.mark.asyncio
async def test_proofs_are_bounded_and_duplicates_outside_window_are_refused(tmp_path):
    _, owner = await setup(tmp_path)
    try:
        original = (await owner.request('action', notification()))['receipt']
        for index in range(401):
            row = copy.deepcopy(original)
            row['commandId'] = f'extra-{index}'
            row['inputId'] = original['inputId'] if index == 400 else f'peer:extra-{index}'
            row['peerEnvelope']['requestId'] = row['inputId']
            owner.peer.save(row)
        value = await owner.request('peer.messages', {'session': T})
        assert value['attributionsTruncated'] is True
        assert len(value['attributions']) == 399
        assert original['inputId'] not in {row['inputId'] for row in value['attributions']}
    finally: await owner.close()
