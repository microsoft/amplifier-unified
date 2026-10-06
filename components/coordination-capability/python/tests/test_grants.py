import asyncio
import copy
import pytest
from amplifier_unified_coordination.owner import Owner

S, T = 'ahp-session:/source', 'ahp-session:/peer'


class Host:
    def __init__(self):
        self.identities = {sid: {'sessionId': sid, 'nativeSessionId': sid.rsplit('/', 1)[1], 'workspace': '/workspace', 'kind': 'root', 'locationRevision': 0, 'interruptionRevision': 0} for sid in (S, T)}
        self.message = {'id': 'human-1', 'role': 'user', 'inputOrigin': 'user', 'text': 'Ask my other chat about ORBIT 572', 'sourceDigest': 'exact-source', 'provenance': {'source': 'host-admission'}}
        self.active = True; self.review = {'pending': True}; self.calls = []; self.gate = None; self.reviewing = asyncio.Event()

    async def __call__(self, method, args):
        self.calls.append((method, copy.deepcopy(args)))
        if method == 'inspectCoordinationIdentity':
            return copy.deepcopy(self.identities[args['session']])
        if method == 'readCoordinationInput':
            if args['messageId'] != self.message['id'] or args.get('active') and (not self.active or args['actorId'] != 'agent:source'):
                raise ValueError('Not a current delivered input')
            value = copy.deepcopy(self.message)
            if args.get('active'):
                value['delivery'] = {'nativeSessionId': 'source', 'interruptionRevision': self.identities[S]['interruptionRevision']}
            return value
        if method == 'reviewCoordinationGrant':
            self.reviewing.set()
            if self.gate:
                await self.gate.wait()
            if isinstance(self.review, Exception):
                raise self.review
            return self.review
        if method == 'readCoordinationSession':
            return {'available': True}
        raise AssertionError('Unexpected side effect: '+method)


async def noop(*args): pass


def action(op, args=None, command='proposal-1', origin='agent', caller=S, actor='agent:source'):
    return {'operation': 'coordination.'+op, 'args': args or {'sessionId': S}, 'commandId': command, 'clientId': 'human-browser' if origin == 'ui' else '', 'actorId': actor, 'origin': origin, 'callerSession': caller}


def proposal(**updates):
    return {'sessionId': S, 'participants': [T], 'purpose': 'Compare the two existing plans', 'modes': ['notify', 'queue'], 'idleStart': True, 'sourceMessageId': 'human-1', **updates}


@pytest.mark.asyncio
async def test_pending_survives_restart_decision_requires_human_and_never_runs_work(tmp_path):
    host = Host(); owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    first = (await owner.request('action', action('grant', proposal())))['receipt']
    assert first['status'] == 'pending' and not first['accepted'] and not first['executionStarted']
    await owner.close(); host.active = False; owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try:
        state = await owner.request('action', action('context'))
        assert state['proposals'][0]['commandId'] == 'proposal-1' and not state['grants']
        assert not state['delivery']['supported']
        human_view = await owner.request('action', action('context', origin='ui'))
        assert human_view['proposals'][0]['reviewSource']['text'] == host.message['text']
        assert 'reviewSource' not in state['proposals'][0]
        args = {'sessionId': S, 'proposalId': 'proposal-1', 'decision': 'allow'}
        with pytest.raises(ValueError, match='Only a human'):
            await owner.request('action', action('decide', args))
        allowed = await owner.request('action', action('decide', args, origin='ui'))
        assert allowed['receipt']['accepted']
        assert (await owner.request('action', action('decide', args, origin='ui')))['receipt'] == allowed['receipt']
        duplicate = await owner.request('action', action('grant', proposal()))
        assert duplicate['receipt']['accepted'] and duplicate['replayed'] is False
        assert len([m for m, _ in host.calls if m == 'reviewCoordinationGrant']) == 1
        revoked = await owner.request('action', action('revoke', {'sessionId': T, 'grantId': 'proposal-1'}, caller=T, actor='agent:peer'))
        assert revoked['receipt']['status'] == 'revoked'
        assert (await owner.request('action', action('grant', proposal())))['receipt']['status'] == 'revoked'
        host.active = True
        with pytest.raises(ValueError, match='already funds'):
            await owner.request('action', action('grant', proposal(), command='new-proposal'))
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['workspace', 'nativeSessionId', 'interruptionRevision', 'sourceDigest', 'locationRevision'])
async def test_saved_allow_revalidates_source_and_each_participant(tmp_path, change):
    host = Host(); owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try:
        await owner.request('action', action('grant', proposal()))
        if change == 'sourceDigest': host.message[change] = 'edited'
        elif change == 'locationRevision': host.identities[T][change] = 1
        else: host.identities[S][change] = 1 if change == 'interruptionRevision' else 'changed'
        with pytest.raises(ValueError):
            await owner.request('action', action('decide', {'sessionId': S, 'proposalId': 'proposal-1', 'decision': 'allow'}, origin='ui'))
        assert owner.receipt('proposal-1')['status'] == 'pending'
        if change == 'sourceDigest':
            context = await owner.request('action', action('context', origin='ui'))
            assert context['proposals'][0]['reviewSource'] == {'unavailable': True}
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['scheduled', 'question', 'coordination', 'unknown', 'observation'])
async def test_generated_user_role_never_authorizes_a_proposal(tmp_path, origin):
    host = Host(); host.message['inputOrigin'] = origin; owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try:
        with pytest.raises(ValueError, match='retained human'):
            await owner.request('action', action('grant', proposal()))
        assert owner.receipt('proposal-1') is None
        assert not host.reviewing.is_set()
    finally: await owner.close()


@pytest.mark.asyncio
async def test_root_scope_bounds_and_current_delivery_are_not_inferred_from_prose(tmp_path):
    host = Host(); owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try:
        with pytest.raises(ValueError, match='borrow'):
            await owner.request('action', action('grant', proposal(), actor='agent:child'))
        host.active = False
        with pytest.raises(ValueError, match='current delivered'):
            await owner.request('action', action('grant', proposal()))
        host.active = True; host.identities[T]['workspace'] = '/another-workspace'
        with pytest.raises(ValueError, match='this workspace'):
            await owner.request('action', action('grant', proposal()))
        host.identities[T]['workspace'] = '/workspace'; host.identities[T]['kind'] = 'worker'
        with pytest.raises(ValueError, match='ordinary root'):
            await owner.request('action', action('grant', proposal()))
        host.identities[T]['kind'] = 'root'
        with pytest.raises(ValueError, match='eight'):
            await owner.request('action', action('grant', proposal(participants=['ahp-session:/'+str(i) for i in range(8)])))
        assert owner.receipt('proposal-1') is None
    finally: await owner.close()


@pytest.mark.asyncio
async def test_human_wait_does_not_hold_lock_duplicate_cannot_widen_and_revoke_wins(tmp_path):
    host = Host(); host.gate = asyncio.Event(); host.review = {'decision': 'allow'}
    owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    waiting = asyncio.create_task(owner.request('action', action('grant', proposal())))
    try:
        await host.reviewing.wait()
        duplicate = await asyncio.wait_for(owner.request('action', action('grant', proposal())), 1)
        assert duplicate['receipt']['status'] == 'pending'
        with pytest.raises(ValueError, match='conflicts'):
            await owner.request('action', action('grant', proposal(allowCreate=True)))
        revoked = await asyncio.wait_for(owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'proposal-1'})), 1)
        assert revoked['receipt']['status'] == 'revoked'
        host.gate.set()
        with pytest.raises(ValueError, match='cannot be revived'): await waiting
        assert owner.receipt('proposal-1')['status'] == 'revoked'
    finally:
        host.gate.set(); await asyncio.gather(waiting, return_exceptions=True); await owner.close()


@pytest.mark.asyncio
async def test_ui_grant_deny_lost_review_and_update_fence(tmp_path):
    host = Host(); owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try:
        args = proposal(); del args['sourceMessageId']
        granted = await owner.request('action', action('grant', args, command='direct', origin='ui'))
        assert granted['receipt']['accepted'] and not host.reviewing.is_set()
        host.review = {'decision': 'deny'}
        assert (await owner.request('action', action('grant', proposal())))['receipt']['status'] == 'denied'
        host.message['id'] = 'human-2'; host.review = ConnectionError('Review lost')
        assert (await owner.request('action', action('grant', proposal(sourceMessageId='human-2'), command='lost')))['receipt']['status'] == 'pending'
        fence = {'fenceId': 'held', 'commandId': 'update', 'purpose': 'distribution-update', 'instanceId': 'original', 'dataScope': 'owned'}
        assert (await owner.request('quiescence.acquire', fence))['acquired']
        assert (await owner.request('action', action('context')))['proposals']
        with pytest.raises(ValueError, match='intake is closed'):
            await owner.request('action', action('decide', {'sessionId': S, 'proposalId': 'lost', 'decision': 'allow'}, origin='ui'))
    finally: await owner.close()


@pytest.mark.asyncio
async def test_pending_and_active_scopes_protect_all_participants_during_retention(tmp_path):
    host = Host(); owner = Owner({'dataDir': str(tmp_path)}, host, noop)
    try:
        await owner.request('action', action('grant', proposal()))
        fence = {'fenceId': 'retention', 'commandId': 'hide', 'purpose': 'retention-hide', 'instanceId': 'original', 'dataScope': 'owned'}
        assert (await owner.request('quiescence.acquire', fence))['acquired']
        report = await owner.request('quiescence.retention', {'context': fence, 'sessions': [S, T], 'limit': 2})
        assert {row['session'] for row in report['protected']} == {S, T}
        assert all('coordination-peer-scope' in row['reasons'] for row in report['protected'])
        proof = {**fence, 'outcome': 'unchanged', 'verified': True, 'receiptId': 'original-hide'}
        await owner.request('quiescence.release', {**fence, 'outcome': 'unchanged', 'proof': proof})
        await owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'proposal-1'}))
        fence = {**fence, 'fenceId': 'retention-2', 'commandId': 'hide-2'}
        assert (await owner.request('quiescence.acquire', fence))['acquired']
        assert not (await owner.request('quiescence.retention', {'context': fence, 'sessions': [S, T], 'limit': 2}))['protected']
    finally: await owner.close()
