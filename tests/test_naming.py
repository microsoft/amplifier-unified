import asyncio
import json
from types import SimpleNamespace
import pytest
from amplifier_web.service import AppService
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import normalize_event

async def test_names_persist_preserve_manual_choices_and_checkpoint_metadata(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    try:
        await app.dispatch('session.create',{})
        session=app._session();sid=session['id']
        await app.on_runtime_event('session.naming',{'sessionId':sid,'name':'Build an orbit viewer','description':'An interactive planetary model.'})
        assert session['title']=='Build an orbit viewer' and session['titleSource']=='generated'
        await app.dispatch('session.rename',{'id':sid,'title':'My solar system'})
        await app.on_runtime_event('session.naming',{'sessionId':sid,'name':'Late automatic name','description':'Now includes orbital controls.'})
        assert session['title']=='My solar system' and session['description']=='Now includes orbital controls.'
        store=SessionStore.for_app(tmp_path,tmp_path);store.save(sid,[{'role':'user','content':'test'}],{'name':'Stale checkpoint'})
        assert store.load(sid)[1]['name']=='My solar system'
        restored=AppService(tmp_path,workspace=tmp_path)
        assert restored._session()['title']=='My solar system'
        await restored.close()
        await app.on_runtime_event('session.naming.progress',{'sessionId':sid,'completedInputs':['u1','u2']})
        assert store.load(sid)[1]['naming_completed_inputs']==['u1','u2']
    finally:await app.close()


def test_worker_naming_protocol_drops_unrelated_payloads():
    assert normalize_event({'type':'session.naming','name':'Demo','description':'Scene','secret':'drop'},'sid')==('session.naming',{'sessionId':'sid','name':'Demo','description':'Scene'})

async def test_live_naming_schedule_dedupes_service_turns_resumes_and_updates(monkeypatch,tmp_path):
    import sys
    from amplifier_web.host.naming import LiveSessionNaming
    from amplifier_web.naming import persist
    calls=[]
    class Config:
        initial_trigger_turn=2;update_interval_turns=5;max_retries=3
        def __init__(self,**kwargs):self.__dict__.update(kwargs)
    class Hook:
        def __init__(self,coordinator,config):self.config=config;self._defer_counts={}
        async def _generate_name(self,sid,directory,is_update):
            calls.append(is_update)
            persist(tmp_path,{'id':sid,'title':'Generated','titleSource':'generated','description':'A useful description'})
    monkeypatch.setitem(sys.modules,'amplifier_module_hooks_session_naming',SimpleNamespace(SessionNamingHook=Hook,SessionNamingConfig=Config))
    coordinator=SimpleNamespace(config={'hooks':[{'module':'hooks-session-naming'}]},session_id='s',hooks=SimpleNamespace(register=lambda *a,**k:None),register_cleanup=lambda *a:None)
    namer=LiveSessionNaming(coordinator,tmp_path,lambda e:None)
    async def turn(n):
        namer.observe({'type':'input.delivered','input_id':str(n),'source':'user'})
        event={'type':'generation.finished','input_ids':[str(n)]}
        namer.observe(event);namer.observe(event)
        if namer.pending:await namer.pending
    await turn(1);assert not calls
    await turn(2);assert calls==[False]
    for n in range(3,6):await turn(n)
    assert calls==[False,True]
    namer.observe({'type':'input.delivered','input_id':'service','source':'amplifier-child'})
    namer.observe({'type':'generation.finished','input_ids':['service']})
    assert len(namer.completed)==5
    namer=LiveSessionNaming(coordinator,tmp_path,lambda e:None,completed_inputs=list(namer.completed))
    for n in range(6,10):await turn(n)
    assert calls==[False,True]
    await turn(10);assert calls==[False,True,True]


@pytest.mark.parametrize('foreground_status',['idle','stopped','error'])
@pytest.mark.parametrize('outcome',['completed','error','cancelled'])
async def test_instrumented_background_naming_keeps_its_own_lifecycle(monkeypatch,tmp_path,foreground_status,outcome):
    """Real naming scheduling and provider instrumentation around a delayed model."""
    import sys
    from amplifier_web.host.naming import LiveSessionNaming
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.execution import ensure_turn
    from amplifier_web.browser_detail import project
    started=asyncio.Event();release=asyncio.Event();events=[]
    app=AppService(tmp_path,workspace=tmp_path)
    namer=None
    try:
        await app.dispatch('session.create',{})
        session=app._session();sid=session['id'];ensure_turn(session,'original')
        telemetry=ExecutionEvents(sid,events.append)
        class Provider:
            def get_info(self):return SimpleNamespace(id='fixture',defaults={'model':'fixture'})
            async def complete(self,request,**kwargs):
                started.set();await release.wait()
                if outcome=='error':raise RuntimeError('Synthetic naming failure')
                return SimpleNamespace(usage={'input_tokens':2,'output_tokens':1,'cost_usd':.001})
        provider=Provider();telemetry.instrument_provider(sid,provider)
        class Config:
            initial_trigger_turn=1;update_interval_turns=5;max_retries=3
            def __init__(self,**kwargs):self.__dict__.update(kwargs)
        class Hook:
            def __init__(self,coordinator,config):self.config=config;self._defer_counts={}
            async def _generate_name(self,*args,**kwargs):await provider.complete(SimpleNamespace(model=None))
        monkeypatch.setitem(sys.modules,'amplifier_module_hooks_session_naming',SimpleNamespace(SessionNamingHook=Hook,SessionNamingConfig=Config))
        coordinator=SimpleNamespace(config={'project_dir':str(tmp_path),'hooks':[{'module':'hooks-session-naming'}]},session_id=sid,hooks=SimpleNamespace(register=lambda *a,**k:None),register_cleanup=lambda *a:None)
        namer=LiveSessionNaming(coordinator,tmp_path,events.append)
        async def flush():
            while events:
                event=normalize_event(events.pop(0),sid)
                if event:await app.on_runtime_event(*event)
        namer.observe({'type':'input.delivered','input_id':'original','source':'user'})
        namer.observe({'type':'generation.finished','input_ids':['original']})
        await asyncio.wait_for(started.wait(),2);await flush()
        if foreground_status=='error':await app.on_runtime_event('runtime.error',{'sessionId':sid,'error':'Foreground turn failed'})
        else:await app.on_runtime_event('runtime.status',{'sessionId':sid,'status':foreground_status})
        assert not namer.pending.done()
        row=session['execution']['nodes'][0]
        assert row['lifecycle']=='background' and row['phase']=='running' and not row.get('endedAt')
        assert row['aggregateUsage']['tokenPendingCalls']==row['aggregateUsage']['costPendingCalls']==1
        assert project(session)['execution']['nodes'][0]['lifecycle']=='background'
        ensure_turn(session,'next-turn')
        await app.on_runtime_event('runtime.status',{'sessionId':sid,'status':'working'})
        assert row['turnId']=='original' and row['phase']=='running' and not row.get('endedAt')
        if outcome=='cancelled':namer.pending.cancel()
        else:release.set()
        await asyncio.gather(namer.pending,return_exceptions=True);await flush()
        assert row['phase']==outcome and row['endedAt']>=row['startedAt']
        assert row['aggregateUsage']['tokenPendingCalls']==row['aggregateUsage']['costPendingCalls']==0
        original,next_turn=session['execution']['turns']
        assert original['aggregateUsage']['calls']==1 and next_turn['aggregateUsage']['calls']==0
        assert row.get('usage',{}).get('totalTokens')==(3 if outcome=='completed' else None)
    finally:
        if namer and namer.pending and not namer.pending.done():
            namer.pending.cancel();await asyncio.gather(namer.pending,return_exceptions=True)
        await app.close()
