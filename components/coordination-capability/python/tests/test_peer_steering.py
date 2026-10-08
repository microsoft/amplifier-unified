import copy
import pytest
from amplifier_unified_coordination.owner import Owner
from test_peer import PeerHost, send
from test_grants import S, T, action, noop


class SteeringHost(PeerHost):
    def __init__(self):
        super().__init__()
        self.target.update(status='working', activeTurnId='human-turn',
            activeSteering={'supported': True, 'activeTurnId': 'human-turn', 'generationId': 'generation'})
        self.proof = {}; self.change = None

    async def __call__(self, method, args):
        if method == 'inspectPeerSteering':
            return copy.deepcopy(self.proof)
        if method == 'submitPeerSteering':
            self.submissions.append(copy.deepcopy(args))
            data = args['input']
            self.proof = {'admitted': True, 'disposition': 'dispatching', 'inputId': data['commandId'],
                'grantId': data['grantId'], 'activeTurnId': data['activeTurnId'],
                'targetGenerationId': data['targetGenerationId'], 'executionRevision': data['expectedExecutionRevision']}
            if self.change == 'generation': self.target['activeSteering']['generationId'] = 'later'
            if self.change == 'turn': self.target['activeTurnId'] = 'later'
            if self.change == 'stop': self.target['interruptionRevision'] += 1
            if self.change == 'proof': self.proof['inputId'] = 'foreign'
            if self.change == 'revoke': await self.owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'grant'}, origin='ui'))
            proof = await self.owner.request('peer.admission', {'session': T, 'args': {
                'inputId': data['commandId'], 'grantId': data['grantId'], 'taskId': None, 'taskRevision': None,
                'activeInputId': data['activeTurnId'], 'targetGenerationId': data['targetGenerationId']}})
            self.proofs.append(proof)
            if self.lost: raise RuntimeError('lost steering response')
            if proof['admitted']: self.proof['disposition'] = 'applied'
            return {'accepted': proof['admitted'], 'executed': False}
        return await super().__call__(method, args)


async def setup(tmp_path):
    host = SteeringHost(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
    await owner.request('action', action('grant', {'sessionId': S, 'participants': [T],
        'purpose': 'Correct current work', 'modes': ['steer'], 'idleStart': False}, command='grant', origin='ui'))
    params = {**send(), 'steeringEnabled': True}
    params['args']['mode'] = 'steer'
    return host, owner, params


@pytest.mark.asyncio
async def test_exact_generation_steer_is_attributed_without_idle_start(tmp_path):
    host, owner, params = await setup(tmp_path)
    try:
        result = await owner.request('action', params)
        assert result['receipt']['status'] == 'applied' and not result['executionStarted']
        assert len(host.submissions) == 1 and host.proofs[0]['message']['peerEnvelope']['mode'] == 'steer'
        assert host.target['activeTurnId'] == 'human-turn' and host.target['executionRevision'] == 0
        assert (await owner.request('action', params))['receipt']['status'] == 'applied'
        assert len(host.submissions) == 1
        row = owner.peer.context(S)['requests'][0]
        assert not row['canResume'] and not row['canCancel']
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['generation', 'turn', 'stop', 'proof', 'revoke'])
async def test_native_boundary_rechecks_steering_authority(tmp_path, change):
    host, owner, params = await setup(tmp_path); host.change = change
    try:
        result = await owner.request('action', params)
        assert result['receipt']['status'] == 'suppressed'
        assert host.proofs[0]['admitted'] is False
        assert not owner.peer.watches
    finally: await owner.close()


@pytest.mark.asyncio
async def test_idle_recipient_is_not_started_or_queued(tmp_path):
    host, owner, params = await setup(tmp_path); host.target.update(status='idle', activeTurnId=None)
    try:
        result = await owner.request('action', params)
        assert result['receipt']['status'] == 'suppressed' and not host.submissions
    finally: await owner.close()


@pytest.mark.asyncio
async def test_restart_never_replays_unknown_steer_and_exact_host_proof_can_resolve_it(tmp_path):
    host, owner, params = await setup(tmp_path); host.lost = True
    try:
        with pytest.raises(RuntimeError, match='lost'): await owner.request('action', params)
        assert owner.receipt('request')['status'] == 'unknown'
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        await owner.request('action', params)
        assert len(host.submissions) == 1
        host.proof['disposition'] = 'applied'
        request = {**action('result', {'sessionId': S, 'requestId': 'request'}), 'steeringEnabled': True}
        result = await owner.request('action', request)
        assert result['receipt']['status'] == 'applied' and result['qualified'] is False
        assert len(host.submissions) == 1
    finally: await owner.close()
