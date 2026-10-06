import copy
import pytest
from test_grants import S, T, action, noop
from test_peer import setup, send
from amplifier_unified_coordination.owner import Owner


async def started(tmp_path):
    host, owner = await setup(tmp_path)
    row = (await owner.request('action', send()))['receipt']
    binding = {'sessionId': T, 'nativeSessionId': 'peer', 'inputId': row['inputId'],
               'activeTurnId': row['inputId'], 'generationId': 'generation-1', 'grantId': 'grant', 'interruptionRevision': 0}
    async def callback(method, args):
        if method == 'readActivePeerInput': return copy.deepcopy(binding)
        return await host(method, args)
    owner.host = callback
    return host, owner, row, binding


def reply(**updates):
    return {**action('reply', {'sessionId': T, 'requestId': 'request', 'kind': 'result', 'outcome': 'success',
            'text': 'Comparison complete', 'references': ['artifact:comparison'], **updates},
            command='reply-1', caller=T, actor='agent:peer'), 'resultsEnabled': True}


def finished(row, **changes):
    return {'session': T, 'commandId': row['inputId'], 'status': 'completed', 'peerTerminals': [{
        'version': 1, 'rootSessionId': 'peer', 'generationId': 'generation-1', 'inputIds': [row['inputId']],
        'messageId': 'native:answer', 'sourceRevision': 'saved-revision', 'textDigest': 'f'*64}], **changes}


@pytest.mark.asyncio
async def test_saved_reply_stages_then_seals_once_and_survives_restart(tmp_path):
    host, owner, row, binding = await started(tmp_path)
    try:
        context = await owner.request('action', {**action('context', {'sessionId': S}), 'deliveryEnabled': True, 'resultsEnabled': True})
        assert context['delivery']['results'] == {'supported': True, 'requiresNativeCheckpoint': True, 'automaticContinuation': False}
        staged = await owner.request('action', reply())
        assert staged['response']['status'] == 'staged' and not staged['response']['qualified']
        assert (await owner.request('action', reply()))['replayed'] is False
        await owner.request('peer.settled', finished(row))
        result = await owner.request('action', {**action('result', {'sessionId': S, 'requestId': 'request'}), 'resultsEnabled': True})
        assert result['qualified'] and result['response']['status'] == 'sealed'
        assert result['response']['terminal']['messageId'] == 'native:answer'
        assert len(host.submissions) == 1
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop)
        assert (await owner.request('action', action('result', {'sessionId': S, 'requestId': 'request'})))['qualified']
        assert len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['cancelled', 'unknown', 'failed', 'missing', 'generation', 'input', 'native', 'revoked'])
async def test_mismatched_or_incomplete_work_does_not_qualify(tmp_path, change):
    host, owner, row, binding = await started(tmp_path)
    try:
        await owner.request('action', reply())
        event = finished(row)
        if change in {'cancelled', 'unknown', 'failed'}: event['status'] = change
        elif change == 'missing': event.pop('peerTerminals')
        elif change in {'generation', 'native', 'input'}:
            event['peerTerminals'][0][{'generation': 'generationId', 'native': 'rootSessionId', 'input': 'inputIds'}[change]] = [] if change == 'input' else 'another'
        else: await owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'grant'}, origin='ui'))
        await owner.request('peer.settled', event)
        assert not owner.receipt('request')['response']['qualified']
        assert len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,outcome', [('ack', 'success'), ('defer', 'unverified'), ('decline', 'failed'), ('result', 'partial')])
async def test_acknowledgements_and_partial_results_remain_distinct(tmp_path, kind, outcome):
    host, owner, row, binding = await started(tmp_path)
    try:
        await owner.request('action', reply(kind=kind, outcome=outcome))
        await owner.request('peer.settled', finished(row))
        response = owner.receipt('request')['response']
        assert response['status'] == 'sealed' and not response['qualified']
    finally: await owner.close()


@pytest.mark.asyncio
async def test_only_current_recipient_can_declare_and_ambiguous_restart_does_not_replay(tmp_path):
    host, owner, row, binding = await started(tmp_path)
    try:
        for changes in [{'origin': 'ui'}, {'actorId': 'agent:child'}, {'callerSession': S}, {'resultsEnabled': False}]:
            with pytest.raises(ValueError): await owner.request('action', {**reply(), **changes})
        binding['generationId'] = None
        with pytest.raises(ValueError): await owner.request('action', reply())
        binding['generationId'] = 'generation-1'
        await owner.request('action', reply())
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop)
        result = await owner.request('action', action('result', {'sessionId': S, 'requestId': 'request'}))
        assert result['receipt']['status'] == 'unknown' and not result['qualified']
        assert len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
async def test_steered_reply_uses_the_original_human_turn_and_exact_peer_generation(tmp_path):
    from test_peer_steering import setup as steered
    host, owner, params = await steered(tmp_path)
    try:
        row = (await owner.request('action', params))['receipt']
        async def callback(method, args):
            if method == 'readActivePeerInput':
                return {'sessionId': T, 'nativeSessionId': 'peer', 'inputId': row['inputId'], 'activeTurnId': 'human-turn',
                        'generationId': 'generation', 'grantId': 'grant', 'interruptionRevision': 0}
            return await host(method, args)
        owner.host = callback
        await owner.request('action', reply())
        event = finished(row, commandId='human-turn'); event['peerTerminals'][0]['generationId'] = 'generation'
        await owner.request('peer.settled', event)
        assert owner.receipt('request')['response']['qualified']
        assert len(host.submissions) == 1 and host.target['activeTurnId'] == 'human-turn'
    finally: await owner.close()


@pytest.mark.asyncio
async def test_native_envelope_input_identity_resolves_exact_request(tmp_path):
    host, owner, row, binding = await started(tmp_path)
    try:
        staged = await owner.request('action', reply(requestId=row['inputId']))
        assert staged['receipt']['requestId'] == 'request'
        await owner.request('peer.settled', finished(row))
        result = await owner.request('action', action('result', {'sessionId': S, 'requestId': row['inputId']}))
        assert result['qualified']
        collision = {**row, 'commandId': row['inputId'], 'inputId': 'peer:another'}
        owner.peer.save(collision)
        with pytest.raises(ValueError, match='unambiguous'):
            owner.peer.read(row['inputId'])
    finally: await owner.close()
