import copy
import pytest
from test_grants import Host, S, T, action, noop
from test_peer import send
from test_peer_results import reply, finished
from amplifier_unified_coordination.owner import Owner


class BothRoots(Host):
    def __init__(self):
        super().__init__()
        self.states = {sid: {'available': True, 'status': 'idle', 'nativeSessionId': self.identities[sid]['nativeSessionId'],
            'nativeLocationRevision': 0, 'executionRevision': 0, 'interruptionRevision': 0,
            'configurationHash': 'config', 'task': None} for sid in (S, T)}
        self.submissions = []; self.owner = None; self.lost = False
    async def __call__(self, method, args):
        sid = args.get('session')
        if method == 'inspectPeerRecipient': return copy.deepcopy(self.states[sid])
        if method in {'watch', 'unwatch'}: return {}
        if method == 'readActivePeerInput':
            return {'sessionId': T, 'nativeSessionId': 'peer', 'inputId': args['inputId'],
                    'activeTurnId': args['inputId'], 'generationId': 'generation-1', 'grantId': 'grant', 'interruptionRevision': 0}
        if method == 'submitPeerInput':
            self.submissions.append(copy.deepcopy(args))
            if self.lost: raise RuntimeError('Lost admission response')
            data = args['input']; state = self.states[sid]
            state.update(executionRevision=state['executionRevision']+1, activeTurnId=data['commandId'], status='working')
            state['peerAdmission'] = {'inputId': data['commandId'], 'grantId': data['grantId'], 'executionRevision': data['expectedExecutionRevision'], 'admittedExecutionRevision': state['executionRevision']}
            proof = await self.owner.request('peer.admission', {'session': sid, 'args': {k: data[k] for k in ('grantId', 'taskId', 'taskRevision')} | {'inputId': data['commandId']}})
            assert proof['admitted'], proof
            return {'accepted': True}
        return await super().__call__(method, args)


async def setup(tmp_path):
    host = BothRoots(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
    await owner.request('action', action('grant', {'sessionId': S, 'participants': [T], 'purpose': 'Compare saved plans', 'modes': ['queue'], 'idleStart': True}, command='grant', origin='ui'))
    row = (await owner.request('action', send()))['receipt']
    return host, owner, row


def subscribe(**changes):
    return {**action('subscribe', {'sessionId': S, 'requestId': 'request', 'grantId': 'grant'}, command='subscription'), 'deliveryEnabled': True, 'resultsEnabled': True, **changes}


async def complete(host, owner, row, **changes):
    host.states[T].update(status='idle', activeTurnId=None, peerAdmission=None)
    await owner.request('peer.settled', finished(row, **changes))


@pytest.mark.asyncio
@pytest.mark.parametrize('late', [False, True])
async def test_exact_sealed_result_resumes_once_and_passive_reads_do_not(tmp_path, late):
    host, owner, row = await setup(tmp_path)
    try:
        if not late: await owner.request('action', subscribe())
        await owner.request('action', reply())
        await complete(host, owner, row)
        if late:
            assert len(host.submissions) == 1
            await owner.request('action', action('result', {'sessionId': S, 'requestId': 'request'}))
            assert len(host.submissions) == 1
            await owner.request('action', subscribe())
        assert len(host.submissions) == 2 and host.submissions[-1]['session'] == S
        continuation = owner.peer.read(owner.peer.read('request')['subscription']['continuationId'])
        assert continuation['status'] == 'accepted'
        assert continuation['peerEnvelope']['replyToRequestId'] == row['inputId']
        assert continuation['peerEnvelope']['references'][0] == 'native:answer'
        assert 'Independently check' in continuation['text']
        await complete(host, owner, row)
        await owner.request('action', subscribe())
        assert len(host.submissions) == 2
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        assert owner.peer.read(continuation['commandId'])['status'] == 'unknown'
        assert len(host.submissions) == 2
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoked', 'stopped', 'task', 'config', 'moved', 'partial', 'failed', 'missing'])
async def test_no_continuation_on_changed_boundary_or_unqualified_result(tmp_path, change):
    host, owner, row = await setup(tmp_path)
    try:
        await owner.request('action', subscribe())
        await owner.request('action', reply(outcome='partial' if change == 'partial' else 'success'))
        if change == 'revoked': await owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'grant'}, origin='ui'))
        if change == 'stopped': host.states[S]['interruptionRevision'] += 1
        if change == 'task': host.states[S]['task'] = {'id': 'changed', 'revision': 1}
        if change == 'config': host.states[S]['configurationHash'] = 'changed'
        if change == 'moved': host.identities[S]['locationRevision'] += 1
        await complete(host, owner, row, **({'status': 'failed'} if change == 'failed' else {'peerTerminals': []} if change == 'missing' else {}))
        assert len(host.submissions) == 1
        assert owner.peer.read('request')['subscription']['status'] in {'suppressed', 'needs_attention'}
    finally: await owner.close()


@pytest.mark.asyncio
async def test_busy_sender_waits_and_stop_before_admission_suppresses(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        host.states[S]['status'] = 'working'
        await owner.request('action', subscribe()); await owner.request('action', reply())
        await complete(host, owner, row)
        identity = owner.peer.read('request')['subscription']['continuationId']
        assert owner.peer.read(identity)['status'] == 'queued' and len(host.submissions) == 1
        host.states[S].update(status='idle', interruptionRevision=1)
        await owner.peer.drain(S)
        assert owner.peer.read(identity)['status'] == 'suppressed' and len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
async def test_restart_preserves_wait_without_restarting_work(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        await owner.request('action', subscribe()); await owner.request('action', reply())
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        await complete(host, owner, row)
        assert owner.peer.read('request')['subscription']['status'] == 'held'
        assert len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
async def test_lost_return_admission_does_not_replay(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        await owner.request('action', subscribe()); await owner.request('action', reply())
        host.lost = True
        with pytest.raises(RuntimeError, match='Lost'): await complete(host, owner, row)
        identity = owner.peer.read('request')['subscription']['continuationId']
        assert owner.peer.read(identity)['status'] == 'unknown' and len(host.submissions) == 2
        host.lost = False
        await complete(host, owner, row)
        await owner.request('action', subscribe())
        assert len(host.submissions) == 2
    finally: await owner.close()


@pytest.mark.asyncio
async def test_subscription_requires_same_sender_grant_and_enabled_results(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        for changes in [{'resultsEnabled': False}, {'deliveryEnabled': False}, {'actorId': 'agent:child'}, {'callerSession': T}, {'origin': 'ui', 'clientId': None}]:
            with pytest.raises(ValueError): await owner.request('action', subscribe(**changes))
        grant = owner.grants.saved('grant'); grant['result']['idleStart'] = False; owner.grants.persist(grant)
        with pytest.raises(ValueError, match='delivery mode'): await owner.request('action', subscribe())
        assert len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
async def test_update_fence_records_result_but_holds_return_work(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        await owner.request('action', subscribe()); await owner.request('action', reply())
        context = {'fenceId': 'wait-fence', 'commandId': 'update', 'purpose': 'distribution-update', 'instanceId': 'original', 'dataScope': 'owned'}
        assert (await owner.request('quiescence.acquire', context))['acquired']
        await complete(host, owner, row)
        request = owner.peer.read('request')
        assert request['response']['qualified']
        pending = owner.peer.read(request['subscription']['continuationId'])
        assert pending['status'] == 'held' and len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
async def test_queued_continuation_rechecks_dependency_at_admission(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        host.states[S]['status'] = 'working'
        await owner.request('action', subscribe()); await owner.request('action', reply())
        await complete(host, owner, row)
        request = owner.peer.read('request'); identity = request['subscription']['continuationId']
        request['response']['qualified'] = False; owner.peer.save(request)
        host.states[S]['status'] = 'idle'
        await owner.peer.drain(S)
        assert owner.peer.read(identity)['status'] == 'suppressed' and len(host.submissions) == 1
    finally: await owner.close()


def wait_control(op='resume', **changes):
    return {**action(op, {'sessionId': S, 'requestId': 'subscription'},
                     command='wait-control', origin='ui'),
            'deliveryEnabled': True, 'resultsEnabled': True, **changes}


async def restart_wait(tmp_path, host, owner):
    await owner.request('action', subscribe())
    await owner.request('action', reply())
    await owner.close()
    owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
    return owner


@pytest.mark.asyncio
async def test_human_resumes_held_exact_result_once_across_restart(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        owner = await restart_wait(tmp_path, host, owner)
        await complete(host, owner, row)
        for session in (S, T):
            state = owner.peer.context(session)['requests'][0]['subscription']
            assert state['commandId'] == 'subscription'
            assert state['canResume'] is (session == S) and state['canCancel'] is (session == S)
        resumed = await owner.request('action', wait_control())
        assert resumed['subscription']['status'] == 'claimed'
        assert len(host.submissions) == 2 and host.submissions[-1]['session'] == S
        assert host.submissions[-1]['input']['peerEnvelope']['replyToRequestId'] == row['inputId']
        assert owner.receipt('wait-control')['status'] == 'accepted'
        await complete(host, owner, row)
        await owner.request('action', wait_control())
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        await owner.request('action', wait_control())
        assert len(host.submissions) == 2
        with pytest.raises(ValueError, match='conflicts'):
            await owner.request('action', wait_control('cancel'))
        for op in ('resume', 'cancel'):
            with pytest.raises(ValueError, match='cannot be changed'):
                await owner.request('action', wait_control(op, commandId='another-control'))
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('restart', [False, True])
async def test_cancel_wait_never_cancels_original_or_starts_return(tmp_path, restart):
    host, owner, row = await setup(tmp_path)
    try:
        if restart: owner = await restart_wait(tmp_path, host, owner)
        else:
            await owner.request('action', subscribe()); await owner.request('action', reply())
        original_status = owner.peer.read('request')['status']
        await owner.request('action', wait_control('cancel'))
        assert owner.peer.read('request')['status'] == original_status
        await complete(host, owner, row)
        request = owner.peer.read('request')
        assert request['response']['qualified'] and request['subscription']['status'] == 'cancelled'
        assert owner.receipt(request['subscription']['continuationId']) is None
        assert len(host.submissions) == 1
        await owner.request('action', wait_control('cancel'))
        assert len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoked', 'stopped', 'task', 'config', 'moved', 'missing', 'partial', 'fence', 'agent', 'recipient', 'results', 'claim'])
async def test_wait_recovery_rechecks_authority_and_saved_boundaries(tmp_path, change):
    host, owner, row = await setup(tmp_path)
    try:
        owner = await restart_wait(tmp_path, host, owner)
        await complete(host, owner, row)
        params = wait_control()
        if change == 'revoked': await owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'grant'}, origin='ui'))
        if change == 'stopped': host.states[S]['interruptionRevision'] += 1
        if change == 'task': host.states[S]['task'] = {'id': 'changed', 'revision': 1}
        if change == 'config': host.states[S]['configurationHash'] = 'changed'
        if change == 'moved': host.identities[S]['locationRevision'] += 1
        if change in {'missing', 'partial'}:
            saved = owner.peer.read('request')
            if change == 'missing': del saved['response']
            else: saved['response']['qualified'] = False
            owner.peer.save(saved)
        if change == 'fence':
            await owner.request('quiescence.acquire', {'fenceId': 'wait-fence', 'commandId': 'update', 'purpose': 'distribution-update', 'instanceId': 'original', 'dataScope': 'owned'})
        if change == 'agent': params.update(origin='agent', clientId='')
        if change == 'recipient': params.update(args={'sessionId': T, 'requestId': 'subscription'}, callerSession=T)
        if change == 'results': params['resultsEnabled'] = False
        if change == 'claim':
            pending = owner.peer.read('request')['subscription']['pending']
            pending.update(status='unknown', text='Uncertain previous claim'); owner.peer.save(pending)
        with pytest.raises(ValueError): await owner.request('action', params)
        assert owner.receipt('wait-control') is None
        assert owner.peer.read('request')['subscription']['status'] == 'held'
        assert len(host.submissions) == 1
    finally: await owner.close()


@pytest.mark.asyncio
async def test_lost_manual_wait_admission_keeps_exact_claim_without_replay(tmp_path):
    host, owner, row = await setup(tmp_path)
    try:
        owner = await restart_wait(tmp_path, host, owner); await complete(host, owner, row)
        host.lost = True
        with pytest.raises(RuntimeError, match='Lost'):
            await owner.request('action', wait_control())
        request = owner.peer.read('request')
        assert owner.peer.read(request['subscription']['continuationId'])['status'] == 'unknown'
        host.lost = False
        await owner.request('action', wait_control())
        await complete(host, owner, row)
        assert len(host.submissions) == 2
        state = next(x for x in owner.peer.context(S)['requests'] if x['commandId'] == 'request')['subscription']
        assert not state['canResume'] and not state['canCancel']
    finally: await owner.close()
