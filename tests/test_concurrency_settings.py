"""Public admission and exact resource identity under concurrent task work."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
import threading

import pytest
from amplifier_web import task_environments as environments
from amplifier_web import test_execution_settings as settings
from amplifier_web.test_queue import TestQueue
from amplifier_web.service import AppError
from test_test_queue import app, queue, backend, submit, drive, record
from test_task_environments import scope, start, act
from test_coordination_generation import agent, until, source_plan, Transport

async def configure(app, **limits):
    current = (await app.dispatch('testing.execution.read', {}))['result']
    return (await app.dispatch('testing.execution.save', {'expectedRevision': current['revision'],
        'queue': {'maxPendingPerChat': 20}, 'profiles': {}, 'concurrency': limits}))['result']

async def test_legacy_policy_default_and_current_settings_authority(app, queue):
    path = app.data_dir / 'test-execution.json'
    path.write_text(json.dumps({'version': 1, 'queue': {}, 'profiles': {}})); path.chmod(0o600)
    before = path.read_bytes()
    assert settings.concurrency(app) == settings.CONCURRENCY_DEFAULTS
    assert path.read_bytes() == before
    current = (await app.dispatch('testing.execution.read', {}))['result']
    with pytest.raises(AppError, match='authority'):
        await agent(app, app.state['sessions'][0], 'testing.execution.save', {
            'expectedRevision': current['revision'], 'queue': {}, 'profiles': {},
            'concurrency': {'maxEnvironmentsPerChat': 3}}, 'not-authorized')
    assert path.read_bytes() == before

@pytest.mark.parametrize('bad', [-1, True, 1.2, '2'])
def test_invalid_concurrency_never_coerces(bad):
    with pytest.raises(ValueError):
        settings.validate({'version': 1, 'queue': {}, 'profiles': {}, 'concurrency': {'maxEnvironmentsPerChat': bad}})

async def test_creation_limit_configurable_above_eight_and_lowering_keeps_work(app, queue):
    source = app.state['sessions'][0]
    runtime=Transport();runtime.app=app;app.runtime=runtime;source_plan(app,source)
    children=[]
    for index in range(9):
        row=app._new_session({'title': f'Child {index}'})
        row.update(status='working', collaboration={'creatorSessionId': source['id']})
        app.state['sessions'].append(row);children.append(row)
    await configure(app, maxTaskChatsPerSource=10)
    created=await agent(app, source, 'coordination.create', {'title':'Tenth','text':'Independent task'}, 'tenth')
    assert created['delivery']=='creation_pending'
    await configure(app, maxTaskChatsPerSource=2)
    with pytest.raises(AppError, match='2 outstanding'):
        await agent(app, source, 'coordination.create', {'title':'Next','text':'Independent task'}, 'next')
    assert all(row['status']=='working' for row in children)

async def test_atomic_environment_race_limits_manual_and_queue_allocations(app, queue):
    await configure(app, maxEnvironmentsPerChat=2)
    home=app.data_dir;sid=scope(app)['sessionId'];workspace=scope(app)['workspaceId']
    barrier=threading.Barrier(6)
    def reserve(index):
        barrier.wait()
        try:
            return environments.prepare(home,sid,workspace,{'sessionId':sid},'/owned/source','backend',f'check-{index}')
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows=list(pool.map(reserve, range(6)))
    assert len([row for row in rows if row])==2
    with closing(environments.storage.connect(home)) as db:
        assert db.execute("SELECT COUNT(*) FROM allocations WHERE status!='retired'").fetchone()[0]==2

@pytest.mark.parametrize('remote', [False, True])
async def test_parallel_independent_checks_have_distinct_results_and_owned_cleanup(app, queue, backend, monkeypatch, remote):
    if remote:
        from test_per_chat_test_queue import remote_backend
        remote_backend(app, monkeypatch)
    await configure(app, maxEnvironmentsPerChat=2)
    monkeypatch.setattr(environments, 'capacity', lambda *a: {'available': True,'limits': {'max_running': 20}, 'counts': {'active': 0}})
    invoke=environments.invoke;entered=[];release=threading.Event()
    def controlled(config,home,row,action,args):
        if action=='execute':
            entered.append(row['id']);assert release.wait(10)
        value=invoke(config,home,row,action,args)
        if action=='execute': value['result']['stdout']=args['command']
        return value
    monkeypatch.setattr(environments,'invoke',controlled)
    for name in ('first','second','third'):
        await submit(app,name,independent=True,command=name)
    try:
        await asyncio.gather(queue.tick(),queue.tick())
        await until(lambda:len(entered)==2)
        assert len(set(entered))==2
        assert record(queue,app,'third')['job']['status']=='queued'
        # Lowering preserves already running owners; the third remains waiting.
        await configure(app,maxEnvironmentsPerChat=1)
        await queue.tick()
        assert len(entered)==2
    finally:
        release.set();await asyncio.gather(*queue.tasks.values())
    await drive(queue)
    for name in ('first','second','third'):
        job=record(queue,app,name)['job'];assert job['status']=='passed'
        row=environments.get(app.data_dir,scope(app)['sessionId'],job['resourceId'])
        assert row['status']=='retired'
        result=(await app.dispatch('workspace.tests.result',{**scope(app),'requestId':name}))['result']
        assert result['text']==name
    assert len({record(queue,app,name)['job']['resourceId'] for name in ('first','second','third')})==3

async def test_serial_barrier_blocks_later_independent_work(app,queue,backend):
    await configure(app,maxEnvironmentsPerChat=3)
    for name,independent in [('first',True),('barrier',False),('last',True)]:
        await submit(app,name,independent=independent)
    assert not queue.chat_available(record(queue,app,'barrier'))
    assert not queue.chat_available(record(queue,app,'last'))
    await drive(queue);await drive(queue);await drive(queue)
    assert all(record(queue,app,name)['job']['status']=='passed' for name in ('first','barrier','last'))

@pytest.mark.parametrize('limit', [0, 2])
@pytest.mark.parametrize('original_independent', [False, True])
async def test_unknown_owner_counts_after_restart_without_replaying(app,queue,backend,monkeypatch,original_independent,limit):
    await configure(app,maxEnvironmentsPerChat=limit)
    invoke=environments.invoke
    def uncertain(config,home,row,action,args):
        if action=='execute' and args['command']=='unknown':
            backend.append((action,args));raise TimeoutError('lost response')
        if action=='job_read':
            return {'ok':True,'job':{'id':args['requestId'],'state':'unknown','terminal':False}}
        return invoke(config,home,row,action,args)
    monkeypatch.setattr(environments,'invoke',uncertain)
    await submit(app,'unknown',independent=original_independent,command='unknown');await drive(queue)
    assert record(queue,app,'unknown')['job']['status']=='unknown'
    recovered=app.test_queue=TestQueue(app);recovered.start=lambda:None
    await submit(app,'independent',independent=True);await drive(recovered)
    if not original_independent:
        assert record(recovered,app,'independent')['job']['status']=='queued'
        assert 'earlier check' in record(recovered,app,'independent')['job']['detail']
        assert [args['command'] for action,args in backend if action=='execute']==['unknown']
        unknown_row=environments.get(app.data_dir,scope(app)['sessionId'],record(recovered,app,'unknown')['job']['resourceId'])
        stopped=await act(app,'stop',unknown_row,'stop-original')
        await act(app,'remove',stopped['resource'],'remove-original')
        await drive(recovered)
        assert record(recovered,app,'independent')['job']['status']=='passed'
        assert record(recovered,app,'unknown')['job']['status']=='unknown'
        assert [args['command'] for action,args in backend if action=='execute'].count('unknown')==1
        return
    assert record(recovered,app,'independent')['job']['status']=='passed'
    unknown_row=environments.get(app.data_dir,scope(app)['sessionId'],record(recovered,app,'unknown')['job']['resourceId'])
    assert unknown_row['status']!='retired'
    assert [args['command'] for action,args in backend if action=='execute'].count('unknown')==1
    await configure(app,maxEnvironmentsPerChat=1)
    await submit(app,'waiting',independent=True);await drive(recovered)
    assert record(recovered,app,'waiting')['job']['status']=='queued'

async def test_exact_recovery_allocation_survives_lower_cap(app,queue,backend):
    await configure(app,maxEnvironmentsPerChat=2)
    await submit(app,'recover',independent=True)
    receipt=record(queue,app,'recover');job=receipt['job'];config=environments.configuration(app)[0]
    row=environments.prepare(app.data_dir,receipt['senderSessionId'],scope(app)['workspaceId'],job['actor'],job['source'],job['backendKey'],receipt['requestId']+':start:0',config.get('installationId'),config,job['profileId'],job['resourceRequest'],job['timingRequest'])
    extra=environments.prepare(app.data_dir,receipt['senderSessionId'],scope(app)['workspaceId'],job['actor'],job['source'],job['backendKey'],'manual-other')
    await configure(app,maxEnvironmentsPerChat=1)
    await drive(queue)
    assert record(queue,app,'recover')['job']['resourceId']==row['id']
    assert record(queue,app,'recover')['job']['status']=='passed'
    assert environments.get(app.data_dir,receipt['senderSessionId'],extra['id'])['status']=='prepared'

async def test_environment_pages_and_cleanup_do_not_select_another_owner(app,queue,backend):
    await configure(app,maxEnvironmentsPerChat=12)
    rows=[(await start(app,f'owner-{n}'))['resource'] for n in range(12)]
    first=(await app.dispatch('workspace.environments.read',scope(app)))['result']
    second=(await app.dispatch('workspace.environments.read',{**scope(app),'offset':first['nextOffset']}))['result']
    assert len(first['items'])==10 and len(second['items'])==2 and second['nextOffset'] is None
    assert len({row['id'] for row in first['items']+second['items']})==12
    chosen=second['items'][0]
    stopped=await act(app,'stop',chosen,'stop-selected')
    await act(app,'remove',stopped['resource'],'remove-selected')
    assert environments.get(app.data_dir,scope(app)['sessionId'],chosen['id'])['status']=='retired'
    assert all(environments.get(app.data_dir,scope(app)['sessionId'],row['id'])['status']!='retired' for row in rows if row['id']!=chosen['id'])


@pytest.mark.parametrize('limit', [0, 6])
@pytest.mark.parametrize('owner_limit', ['maxOwnedResources', 'maxOwnedResourcesPerChat'])
async def test_environment_zero_count_keeps_atomic_retained_owner_bound(app, queue, limit, owner_limit):
    await configure(app, maxEnvironmentsPerChat=limit, **{owner_limit:3})
    home=app.data_dir;sid=scope(app)['sessionId'];workspace=scope(app)['workspaceId']
    barrier=threading.Barrier(6)
    def reserve(index):
        barrier.wait()
        try:
            return environments.prepare(home,sid,workspace,{'sessionId':sid},'/owned/source','backend',f'check-{index}')
        except ValueError as error:
            assert 'resource owner capacity' in str(error)
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows=list(pool.map(reserve,range(6)))
    assert len([row for row in rows if row])==3
    with closing(environments.storage.connect(home)) as db:
        assert db.execute("SELECT COUNT(*) FROM allocations WHERE status!='retired'").fetchone()[0]==3

async def test_default_source_count_accepts_above_eight_without_rewriting_saved_policy(app,queue):
    source=app.state['sessions'][0]
    runtime=Transport();runtime.app=app;app.runtime=runtime;source_plan(app,source)
    children=[]
    for index in range(12):
        child=app._new_session({'title':f'Active {index}'})
        child.update(status='working',collaboration={'creatorSessionId':source['id']})
        app.state['sessions'].append(child);children.append(child)
    assert settings.concurrency(app)['maxTaskChatsPerSource']==0
    receipt=await agent(app,source,'coordination.create',{'title':'Next','text':'Independent task'},'uncapped')
    assert receipt['delivery']=='creation_pending'
    await configure(app,maxTaskChatsPerSource=8)
    saved=(app.data_dir/'test-execution.json').read_bytes()
    with pytest.raises(AppError,match='8 outstanding'):
        await agent(app,source,'coordination.create',{'title':'Limited','text':'Independent task'},'limited')
    assert (app.data_dir/'test-execution.json').read_bytes()==saved
    assert all(child['status']=='working' for child in children)

@pytest.mark.parametrize('key', sorted(settings.CONCURRENCY_DEFAULTS))
def test_all_work_counts_allow_zero(key):
    assert settings.validate({'version':1,'queue':{},'profiles':{},'concurrency':{key:0}})['concurrency'][key]==0
