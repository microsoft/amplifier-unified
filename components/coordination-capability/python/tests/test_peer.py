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


@pytest.mark.asyncio
async def test_terminal_wakeup_counts_as_work_until_next_admission_settles(tmp_path):
    import asyncio
    host, owner = await setup(tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()
    callback = owner.host
    async def held(method, args):
        if method == 'submitPeerInput':
            entered.set()
            await finish.wait()
        return await callback(method, args)
    owner.host = held
    active = None
    context = {'fenceId':'peer-fence','commandId':'update','purpose':'distribution-update',
               'instanceId':'original','dataScope':'owned'}
    try:
        host.target['status'] = 'working'
        await owner.request('action', send())
        host.target['status'] = 'idle'
        active = asyncio.create_task(owner.request('peer.settled', {
            'session':T, 'commandId':'preceding-human-turn', 'status':'completed'}))
        await asyncio.wait_for(entered.wait(), 2)
        assert owner.intake.calls > 0
        assert not (await owner.request('quiescence.acquire', context))['acquired']
        finish.set()
        await active
        assert owner.intake.calls == 0 and len(host.submissions) == 1
    finally:
        finish.set()
        if active: await asyncio.gather(active, return_exceptions=True)
        await owner.close()


@pytest.mark.asyncio
async def test_held_resume_is_human_exact_and_idempotent(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        host.target['status'] = 'working'
        await owner.request('action', send())
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        state = await owner.request('action', action('context'))
        assert state['requests'][0]['status'] == 'held' and state['requests'][0]['canResume']
        args = {'sessionId': S, 'requestId': 'request'}
        with pytest.raises(ValueError, match='Only a human'):
            await owner.request('action', {**action('resume', args), 'deliveryEnabled': True})
        command = {**action('resume', args, command='resume-1', origin='ui'), 'deliveryEnabled': True}
        host.target['status'] = 'idle'
        await owner.request('action', command)
        await owner.request('action', command)
        assert len(host.submissions) == 1
        assert host.submissions[0]['input']['text'] == 'Compare ORBIT 572'
        assert owner.receipt('request')['status'] == 'accepted'
        assert not (await owner.request('action', action('context')))['requests'][0]['canResume']
        with pytest.raises(ValueError, match='Only unadmitted'):
            await owner.request('action', {**command, 'commandId': 'resume-2'})
    finally: await owner.close()


@pytest.mark.asyncio
async def test_cancel_releases_watch_and_does_not_run_other_queued_work(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        host.target['status'] = 'working'
        await owner.request('action', send())
        host.target['status'] = 'idle'
        command = {**action('cancel', {'sessionId': T, 'requestId': 'request'}, origin='ui', command='cancel-1'), 'deliveryEnabled': True}
        await owner.request('action', command)
        await owner.request('action', command)
        assert owner.receipt('request')['status'] == 'cancelled' and not owner.peer.watches
        await owner.peer.drain(T)
        assert not host.submissions
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['unknown', 'accepted', 'submitting', 'completed'])
async def test_uncertain_or_admitted_requests_cannot_be_released_or_cancelled(tmp_path, status):
    host, owner = await setup(tmp_path)
    try:
        host.target['status'] = 'working'; await owner.request('action', send())
        row = owner.peer.read('request'); row['status'] = status; owner.peer.save(row)
        for op in ('resume', 'cancel'):
            with pytest.raises(ValueError, match='Only unadmitted'):
                await owner.request('action', {**action(op, {'sessionId': S, 'requestId': 'request'}, origin='ui'), 'deliveryEnabled': True})
        assert not host.submissions
    finally: await owner.close()


@pytest.mark.asyncio
async def test_context_is_bounded_and_read_only_and_resume_rechecks_stop(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        host.target['status'] = 'working'
        for i in range(34): await owner.request('action', send(str(i)))
        state = await owner.request('action', action('context'))
        assert len(state['requests']) == 32 and state['requestsTruncated'] and not host.submissions
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        host.target['interruptionRevision'] += 1
        with pytest.raises(ValueError, match='stopped'):
            await owner.request('action', {**action('resume', {'sessionId': S, 'requestId': '33'}, origin='ui'), 'deliveryEnabled': True})
        assert owner.receipt('33')['status'] == 'held' and not host.submissions
    finally: await owner.close()
