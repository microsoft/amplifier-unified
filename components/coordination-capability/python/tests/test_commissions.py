import copy
import pytest
from test_grants import S, T, action, noop
from test_peer_subscriptions import BothRoots
from amplifier_unified_coordination.owner import Owner

C = 'ahp-session:/commissioned'

class CreationHost(BothRoots):
    def __init__(self):
        super().__init__(); self.creations = []; self.creation_change = None; self.creation_lost = False
    async def __call__(self, method, args):
        if method == 'createPeerSession':
            self.creations.append(copy.deepcopy(args))
            self.identities[C] = {**self.identities[S], 'sessionId': C, 'nativeSessionId': 'commissioned', 'workspace': '/managed/new-task'}
            self.states[C] = {**self.states[T], 'nativeSessionId': 'commissioned'}
            if self.creation_change == 'revoke':
                grant = self.owner.grants.saved('grant'); grant['status'] = 'revoked'; self.owner.grants.persist(grant)
            if self.creation_change == 'stop': self.states[S]['interruptionRevision'] += 1
            if self.creation_lost: raise RuntimeError('Lost creation response')
            return {'uri': C, 'creationConfirmed': True, 'receipt': {'status': 'completed'}}
        return await super().__call__(method, args)

async def setup(tmp_path, **scope):
    host = CreationHost(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
    await owner.request('action', action('grant', {'sessionId': S, 'participants': [T], 'purpose': 'Commission a comparison', 'modes': ['notify', 'queue'], 'idleStart': True, 'allowCreate': True, **scope}, command='grant', origin='ui'))
    return host, owner

def create(**updates):
    return {**action('create', {'sessionId': S, 'grantId': 'grant', 'title': 'Compare designs', 'text': 'Compare ORBIT-572.', 'references': ['artifact:design']}, command='commission'), 'creationEnabled': True, 'deliveryEnabled': True, **updates}

@pytest.mark.asyncio
async def test_busy_source_creates_at_idle_once_with_relationship_and_brief(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        host.states[S]['status'] = 'working'
        assert (await owner.request('action', create()))['receipt']['status'] == 'queued'
        assert not host.creations and not host.submissions
        host.states[S]['status'] = 'idle'
        await owner.request('changed', {'token': next(iter(owner.peer.watches))})
        row = owner.receipt('commission'); assert row['status'] == 'created'
        assert len(host.creations) == len(host.submissions) == 1
        assert host.creations[0]['expectedConfigurationHash'] == 'config'
        request = owner.peer.read(row['initialRequestId'])
        assert request['peerEnvelope']['task']['outputNamespace'] == 'working-files/tasks/commissioned'
        assert request['peerEnvelope']['task']['creatorSessionId'] == S
        assert request['peerEnvelope']['references'] == ['artifact:design']
        grant = owner.grants.saved('grant'); assert C in grant['result']['participants']
        assert grant['participantBindings'][-1]['workspace'] == '/managed/new-task'
        assert (await owner.request('action', create()))['receipt']['status'] == 'created'
        await owner.commissions.drain(S)
        context = await owner.request('action', action('context', {'sessionId': C}, caller=C, actor='agent:commissioned'))
        assert context['commissions'][0]['createdSessionId'] == C
        assert len(host.creations) == len(host.submissions) == 1
        with pytest.raises(ValueError, match='conflicts'):
            await owner.request('action', create(args={**create()['args'], 'title': 'Different'}))
    finally: await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'config', 'task', 'move', 'revoked'])
async def test_queued_creation_rechecks_exact_source_before_effect(tmp_path, change):
    host, owner = await setup(tmp_path)
    try:
        host.states[S]['status'] = 'working'; await owner.request('action', create())
        host.states[S]['status'] = 'idle'
        if change == 'stop': host.states[S]['interruptionRevision'] += 1
        if change == 'config': host.states[S]['configurationHash'] = 'different'
        if change == 'task': host.states[S]['task'] = {'id': 'task', 'revision': 1}
        if change == 'move': host.identities[S]['locationRevision'] += 1
        if change == 'revoked':
            grant = owner.grants.saved('grant'); grant['status'] = 'revoked'; owner.grants.persist(grant)
        await owner.commissions.drain(S)
        assert owner.receipt('commission')['status'] == 'suppressed'
        assert not host.creations and not host.submissions
    finally: await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'revoke'])
async def test_created_chat_preserved_if_brief_permission_changes(tmp_path, change):
    host, owner = await setup(tmp_path)
    try:
        host.creation_change = change
        row = (await owner.request('action', create()))['receipt']
        assert row['status'] == 'created_brief_suppressed' and row['createdSessionId'] == C
        assert len(host.creations) == 1 and not host.submissions
        assert C not in owner.grants.saved('grant')['result']['participants']
    finally: await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('lost', ['creation', 'brief'])
async def test_unknown_effect_never_replayed_even_after_restart(tmp_path, lost):
    host, owner = await setup(tmp_path)
    try:
        host.creation_lost = lost == 'creation'; host.lost = lost == 'brief'
        with pytest.raises(RuntimeError): await owner.request('action', create())
        assert owner.receipt('commission')['status'] == 'unknown'
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        assert (await owner.request('action', create()))['receipt']['status'] == 'unknown'
        assert len(host.creations) == 1 and len(host.submissions) == (lost == 'brief')
        with pytest.raises(ValueError):
            await owner.request('action', {**action('resume', {'sessionId': S, 'requestId': 'commission'}, command='resume', origin='ui'), 'creationEnabled': True})
    finally: await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resume', 'cancel'])
async def test_restart_holds_uncreated_request_until_human_control(tmp_path, operation):
    host, owner = await setup(tmp_path)
    try:
        host.states[S]['status'] = 'working'; await owner.request('action', create())
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, host, noop); host.owner = owner
        host.states[S]['status'] = 'idle'; await owner.commissions.drain(S)
        assert owner.receipt('commission')['status'] == 'held' and not host.creations
        control = {**action(operation, {'sessionId': S, 'requestId': 'commission'}, command='control', origin='ui'), 'creationEnabled': True}
        with pytest.raises(ValueError): await owner.request('action', {**control, 'origin': 'agent'})
        await owner.request('action', control); await owner.request('action', control)
        assert owner.receipt('commission')['status'] == ('created' if operation == 'resume' else 'cancelled')
        assert len(host.creations) == (operation == 'resume')
    finally: await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('scope', [{'allowCreate': False}, {'idleStart': False}, {'modes': ['notify']}])
async def test_create_requires_explicit_creation_and_delivery_scope(tmp_path, scope):
    host, owner = await setup(tmp_path, **scope)
    try:
        with pytest.raises(ValueError): await owner.request('action', create())
        assert not host.creations and owner.receipt('commission') is None
    finally: await owner.close()

@pytest.mark.asyncio
async def test_capacity_and_bounded_inputs_before_creation(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        host.states[S]['status'] = 'working'
        for args in [{**create()['args'], 'references': ['x'*2000]*4}, {**create()['args'], 'text': '\U0001f600'*9000}]:
            with pytest.raises(ValueError): await owner.request('action', create(args=args))
        for n in range(6): await owner.request('action', create(commandId='commission-'+str(n)))
        with pytest.raises(ValueError, match='capacity'): await owner.request('action', create())
        assert not host.creations
    finally: await owner.close()

@pytest.mark.asyncio
async def test_reply_links_validate_original_request_and_preserve_context(tmp_path):
    host, owner = await setup(tmp_path)
    try:
        row = (await owner.request('action', create()))['receipt']
        request = owner.peer.read(row['initialRequestId'])
        args = {'sessionId': C, 'recipientSessionId': S, 'grantId': 'grant', 'mode': 'notify', 'text': 'Design ready', 'replyToRequestId': request['inputId'], 'references': ['artifact:comparison']}
        reply = await owner.request('action', {**action('send', args, command='reply', caller=C, actor='agent:commissioned'), 'deliveryEnabled': True})
        assert reply['receipt']['peerEnvelope']['replyToRequestId'] == request['inputId']
        with pytest.raises(ValueError, match='exact incoming'):
            await owner.request('action', {**action('send', {**args, 'recipientSessionId': T}, command='bad-reply', caller=C, actor='agent:commissioned'), 'deliveryEnabled': True})
    finally: await owner.close()

@pytest.mark.asyncio
async def test_creation_is_counted_during_quiescence_and_fence_blocks_new_claims(tmp_path):
    import asyncio
    host, owner = await setup(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    original = owner.host
    async def gated(method, args):
        if method == 'createPeerSession': started.set(); await release.wait()
        return await original(method, args)
    owner.host = gated
    task = asyncio.create_task(owner.request('action', create()))
    context = {'fenceId': 'creation-fence', 'commandId': 'update', 'purpose': 'distribution-update', 'instanceId': 'original', 'dataScope': 'owned'}
    try:
        await started.wait()
        assert (await owner.request('quiescence.acquire', context))['acquired'] is False
        release.set(); await task
        context = {**context, 'fenceId': 'settled-creation-fence', 'commandId': 'update-after-settlement'}
        assert (await owner.request('quiescence.acquire', context))['acquired'] is True
        with pytest.raises(ValueError, match='intake is closed'):
            await owner.request('action', create(commandId='second'))
        assert len(host.creations) == 1
    finally:
        release.set(); await task; await owner.close()
