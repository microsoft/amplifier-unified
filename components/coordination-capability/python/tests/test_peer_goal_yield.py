import copy
import pytest
from test_peer_subscriptions import setup, subscribe, complete, S, T
from test_grants import action
from test_peer_results import reply

async def pending(tmp_path):
    host, owner, row = await setup(tmp_path)
    host.states[S].update(status='working', activeTurnId='parent-input')
    await owner.request('action', subscribe())
    await owner.request('action', reply())
    await complete(host, owner, row)
    return host, owner, owner.peer.read(owner.peer.read('request')['subscription']['continuationId'])

async def ready(owner):
    return await owner.request('peer.continuationReady', {'session': S, 'nativeSessionId': 'source', 'args': {'taskId': None, 'taskRevision': None}})

@pytest.mark.asyncio
async def test_pending_query_is_read_only_and_exact_native_root(tmp_path):
    host, owner, row = await pending(tmp_path)
    try:
        native = host.identities[S]['nativeSessionId']
        before = owner.db.execute('SELECT * FROM commands ORDER BY rowid').fetchall()
        async def inspect(who):
            return await owner.request('peer.continuationReady', {'session': S, 'nativeSessionId': who, 'args': {'taskId': None, 'taskRevision': None}})
        assert (await inspect(native)) == {'ready': True}
        assert (await inspect('foreign')) == {'ready': False}
        assert before == owner.db.execute('SELECT * FROM commands ORDER BY rowid').fetchall()
        assert len(host.submissions) == 1 and owner.peer.read(row['commandId'])['status'] == 'queued'
    finally: await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'task', 'config', 'revoked', 'unqualified', 'unknown', 'claimed', 'fence'])
async def test_changed_boundary_never_yields(tmp_path, change):
    host, owner, row = await pending(tmp_path)
    try:
        if change == 'stop': host.states[S]['interruptionRevision'] += 1
        if change == 'task': host.states[S]['task'] = {'id': 'new', 'revision': 1, 'status': 'active'}
        if change == 'config': host.states[S]['configurationHash'] = 'changed'
        if change == 'revoked': await owner.request('action', action('revoke', {'sessionId': S, 'grantId': 'grant'}, origin='ui'))
        if change == 'unqualified':
            request=owner.peer.read('request'); request['response']['qualified']=False; owner.peer.save(request)
        if change in {'unknown', 'claimed'}: row['status']='unknown' if change=='unknown' else 'submitting'; owner.peer.save(row)
        if change == 'fence':
            assert (await owner.request('quiescence.acquire', {'fenceId':'hold', 'commandId':'update', 'purpose':'distribution-update', 'instanceId':'original', 'dataScope':'owned'}))['acquired']
        assert await owner.peer.pending_continuation(S) is None
        assert len(host.submissions) == 1
    finally: await owner.close()

@pytest.mark.asyncio
async def test_sealed_result_precedes_more_than_one_page_of_ordinary_queue(tmp_path):
    host, owner, row = await pending(tmp_path)
    try:
        # Move the valid claim after a complete ordinary queue page, preserving its identity.
        with owner.db:
            owner.db.execute('DELETE FROM commands WHERE id=?', (row['commandId'],))
        for i in range(40):
            ordinary=copy.deepcopy(row); ordinary.update(commandId=f'ordinary-{i}', inputId=f'ordinary-input-{i}')
            ordinary.pop('dependencyRequestId'); owner.peer.save(ordinary)
        owner.peer.save(row)
        host.states[S].update(status='idle', activeTurnId=None)
        await owner.peer.drain(S)
        assert len(host.submissions) == 2
        assert host.submissions[-1]['input']['commandId'] == row['inputId']
        assert owner.peer.read('ordinary-0')['status'] == 'queued'
    finally: await owner.close()

@pytest.mark.asyncio
async def test_qualified_continuation_is_not_hidden_by_invalid_dependencies(tmp_path):
    host, owner, row = await pending(tmp_path)
    try:
        with owner.db:
            owner.db.execute('DELETE FROM commands WHERE id=?', (row['commandId'],))
        for i in range(260):
            invalid=copy.deepcopy(row)
            invalid.update(commandId=f'invalid-{i}', inputId=f'invalid-input-{i}', dependencyRequestId='missing')
            owner.peer.save(invalid)
        owner.peer.save(row)
        assert await owner.peer.pending_continuation(S) == row
        assert len(host.submissions) == 1
    finally: await owner.close()
