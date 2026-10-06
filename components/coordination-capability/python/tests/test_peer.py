import copy
import pytest
from test_grants import Host, S, T, action, noop
from amplifier_unified_coordination.owner import Owner


class PeerHost(Host):
    def __init__(self):
        super().__init__()
        self.target = {'available': True, 'status': 'idle', 'nativeSessionId': 'peer', 'nativeLocationRevision': 0,
                       'executionRevision': 0, 'interruptionRevision': 0, 'configurationHash': 'config', 'task': None}
        self.submissions = []; self.proofs = []; self.lost = False; self.owner = None
    async def __call__(self, method, args):
        if method == 'inspectPeerRecipient': return copy.deepcopy(self.target)
        if method in {'watch', 'unwatch'}: return {}
        if method == 'submitPeerInput':
            self.submissions.append(copy.deepcopy(args))
            if self.lost: raise RuntimeError('lost admission response')
            data = args['input']; self.target.update(executionRevision=self.target['executionRevision']+1, activeTurnId=data['commandId'], status='working')
            self.target['peerAdmission'] = {'inputId': data['commandId'], 'grantId': data['grantId'], 'executionRevision': data['expectedExecutionRevision'], 'admittedExecutionRevision': self.target['executionRevision']}
            proof = await self.owner.request('peer.admission', {'session': T, 'args': {'inputId': data['commandId'], 'grantId': data['grantId'], 'taskId': data['taskId'], 'taskRevision': data['taskRevision']}})
            self.proofs.append(proof)
            return {'accepted': proof['admitted']}
        return await super().__call__(method, args)


async def setup(tmp_path):
    host = PeerHost(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
    await owner.request('action', action('grant', {'sessionId': S, 'participants': [T], 'purpose': 'Compare saved plans', 'modes': ['queue'], 'idleStart': True}, command='grant', origin='ui'))
    return host, owner


def send(command='request'):
    return {**action('send', {'sessionId': S, 'recipientSessionId': T, 'grantId': 'grant', 'mode': 'queue', 'text': 'Compare ORBIT 572'}, command=command), 'deliveryEnabled': True}


@pytest.mark.asyncio
async def test_queue_claim_native_original_and_terminal_are_distinct(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        host.target['status'] = 'working'
        first = await owner.request('action', send()); assert first['receipt']['status'] == 'queued' and not host.submissions
        token = next(iter(owner.peer.watches)); host.target['status'] = 'idle'
        await owner.request('changed', {'token': token})
        assert len(host.submissions) == 1 and host.proofs[0]['admitted']
        assert host.proofs[0]['message']['text'] == 'Compare ORBIT 572'
        assert owner.receipt('request')['status'] == 'accepted'
        assert (await owner.request('action', send()))['receipt']['status'] == 'accepted' and len(host.submissions) == 1
        input_id = owner.receipt('request')['inputId']
        host.target.update(status='idle', activeTurnId=None, peerAdmission=None)
        await owner.request('peer.settled', {'session': T, 'commandId': input_id, 'status': 'completed'})
        result = await owner.request('action', action('result', {'sessionId': S, 'requestId': 'request'}))
        assert result['receipt']['status'] == 'completed' and not result['qualified']
        assert not owner.peer.watches
        denied = await owner.request('peer.admission', {'session': T, 'args': {'inputId': input_id, 'grantId': 'grant', 'taskId': None, 'taskRevision': None}})
        assert denied['admitted'] is False
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoke', 'stop', 'task', 'config', 'location'])
async def test_pending_peer_work_is_suppressed_after_boundary_change(tmp_path, change):
    host, owner = await setup(tmp_path)
    try:
        host.target['status'] = 'working'; await owner.request('action', send()); token = next(iter(owner.peer.watches))
        if change == 'revoke': await owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'grant'}, origin='ui'))
        if change == 'stop': host.target['interruptionRevision'] += 1
        if change == 'task': host.target['task'] = {'id': 'new', 'revision': 1}
        if change == 'config': host.target['configurationHash'] = 'new'
        if change == 'location': host.identities[T]['locationRevision'] += 1
        host.target['status'] = 'idle'; await owner.request('changed', {'token': token})
        assert owner.receipt('request')['status'] == 'suppressed' and not host.submissions
    finally: await owner.close()


@pytest.mark.asyncio
async def test_lost_admission_and_restart_never_replay_saved_work(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        host.lost = True
        with pytest.raises(RuntimeError, match='lost'): await owner.request('action', send())
        assert owner.receipt('request')['status'] == 'unknown'
        await owner.request('action', send()); assert len(host.submissions) == 1
        host.target['status'] = 'working'; await owner.request('action', send('still-queued'))
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        assert owner.receipt('still-queued')['status'] == 'held'
        assert (await owner.request('action', send('still-queued')))['receipt']['status'] == 'held'
        assert len(host.submissions) == 1 and not owner.peer.watches
    finally: await owner.close()


@pytest.mark.asyncio
async def test_peer_native_authority_requires_exact_live_host_admission(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        await owner.request('action', send()); row = owner.receipt('request')
        args = {'inputId': row['inputId'], 'grantId': row['grantId'], 'taskId': None, 'taskRevision': None}
        host.target['peerAdmission'] = None
        assert not (await owner.request('peer.admission', {'session': T, 'args': args}))['admitted']
        assert not (await owner.request('peer.admission', {'session': S, 'args': args}))['admitted']
        assert not (await owner.request('peer.admission', {'session': T, 'args': {**args, 'grantId': 'forged'}}))['admitted']
    finally: await owner.close()
