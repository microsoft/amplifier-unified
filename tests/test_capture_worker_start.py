"""Actual Worker.start wiring with local acquisition and offline SDK transport."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_provider_openai import OpenAIProvider
from amplifier_module_loop_live.runtime import Input
from amplifier_web import runtime_worker
from amplifier_web.host.session import SelectedProvider
from amplifier_web.host.children import StandaloneHostAdapter
from amplifier_web.shared_state import ActivationGate
from test_context_inspection import config, evidence


@pytest.mark.parametrize('restore',[False,True])
async def test_full_worker_start_capture(tmp_path,monkeypatch,restore):
    import socket
    def forbidden(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    home=tmp_path/'home';workspace=tmp_path/'workspace';workspace.mkdir();config(home,recording=False)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(home))
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'community'))
    # Actual provider serialization/count/complete, no sockets/accounts.
    paths=[]
    def transport(req):
        body=json.loads(req.content);paths.append({'path':req.url.path,'bytes':len(req.content),'inputHash':hashlib.sha256(json.dumps(body.get('input'),sort_keys=True).encode()).hexdigest()})
        if req.url.path.endswith('/input_tokens'):return httpx.Response(200,json={'input_tokens':100})
        assert req.url.path=='/v1/responses'
        return httpx.Response(200,json={'id':'r','object':'response','created_at':1,'model':'gpt-6-astra','status':'completed',
            'output':[{'type':'message','id':'m','role':'assistant','content':[{'type':'output_text','text':'Done','annotations':[]}]}],
            'usage':{'input_tokens':100,'output_tokens':1,'total_tokens':101}})
    client=AsyncOpenAI(api_key='fixture',max_retries=0,http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    raw=OpenAIProvider(api_key='fixture',client=client,config={'default_model':'gpt-6-astra','use_streaming':False,'raw':False,'max_retries':0})
    modules={n:Path(importlib.util.find_spec('amplifier_module_'+n.replace('-','_')).origin).parent for n in ('loop-live','context-managed')}
    plan={'session':{'orchestrator':{'module':'loop-live','config':{'max_iterations':2}},'context':{'module':'context-managed','config':{'token_meter':'actual'}}},'providers':[]}
    sid='worker-capture';prepared=PreparedBundle(plan,BundleModuleResolver(modules),Bundle(name='fixture',session=plan['session']))
    session=await prepared.create_session(session_id=sid);c=session.coordinator
    await c.mount('providers',raw,name='configured')
    selection={'instance':'configured','model':'gpt-6-astra','effort':'high'}
    c.get('orchestrator').root_provider=SelectedProvider(raw,selection)
    async def instructions():return 'W'*1_000_100
    await c.get('context').set_system_prompt_factory(instructions)
    finished=asyncio.Event();published=[]
    def publish(row):
        published.append(row)
        if row.get('type') in {'generation.finished','generation.failed','generation.detached','runtime.error'}:finished.set()
    monkeypatch.setattr(runtime_worker,'publish',publish)
    async def acquire(workspace,*,runtime,**kw):
        c.register_capability('live.runtime',runtime)
        c.register_capability('live.host',StandaloneHostAdapter())
        c.register_capability('live.checkpoint',AsyncMock())
        return session,runtime,{'history_source':'native','config_inputs':[]}
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager',acquire)
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides',lambda *a:None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending',lambda *a:None)
    monkeypatch.setattr('amplifier_web.host.naming.LiveSessionNaming',lambda *a:None)
    monkeypatch.setattr('amplifier_web.shared_state.configuration_stamp',lambda *a,**kw:())
    monkeypatch.setattr('amplifier_web.support_diagnostics.runtime_versions',lambda:{})
    worker=runtime_worker.Worker();worker.shared_store=worker.shared_handle=SimpleNamespace()
    worker.shared_store_stamp=lambda *a:();worker.activation_gate=ActivationGate()
    worker.preparation_progress=AsyncMock();worker.history_stamp=lambda:();worker.ownership.register=AsyncMock()
    worker.park=AsyncMock()
    async def bridge(operation,args):
        if operation=='capacity.admit':return {'allowed':True,'budgetRevision':0,'admittedAt':1}
        if operation=='context.prepare':return {}
        if operation=='context.manifest':return {'surfaces':[],'inputIds':[]}
        return {}
    worker.bridge=bridge
    if restore:
        path=home/'sessions'/sid/'control-state.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'selection':selection,'budget':{'maxOutputTokens':321}}))
    try:
        await worker.start({'id':sid,'workspace':str(workspace),'selection':selection},raise_errors=True,recover_bundle=False)
        await worker.runtime.submit(Input('user','Complete without tools.',activation=worker.activation))
        await asyncio.wait_for(finished.wait(),20)
        assert any(row.get('type')=='generation.finished' for row in published), published
        await worker.runtime.submit(Input('stop',activation=worker.activation))
        await worker.execution
        observer=worker.telemetry.context_inspectors[sid];await observer.drain();row=observer.store.read()['selected']
        evidence('full-worker-'+str(restore),{'header':row,'requests':paths,'workerStart':True,'rootType':type(c.get('orchestrator').root_provider).__name__})
        assert row['budgetStatus']=='captured'
        assert row['budget']['measurement']['input_tokens']==100
    finally:
        evidence('full-worker-lifecycle-'+str(restore),{'events':published,'requests':paths})
        if worker.execution and not worker.execution.done():
            await worker.runtime.submit(Input('stop',activation=worker.activation));await asyncio.wait_for(worker.execution,5)
        if worker.controls:await worker.controls.close()
        await session.cleanup();await client.close()
