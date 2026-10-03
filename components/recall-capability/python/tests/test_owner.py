import asyncio
import copy
import json
from pathlib import Path
import pytest
from amplifier_unified_recall.owner import Owner
from amplifier_recall.store import digest
SID='ahp-session:/one'

class Host:
    def __init__(self):
        self.calls=[];self.revision='r1';self.message={'id':'typed','role':'user','text':'Always keep the cobalt headings concise for this project.','createdAt':'2026-10-01T00:00:00Z','inputOrigin':'user','provenance':{'source':'host-admission','complete':True}}
        self.model_calls=0;self.model_error=False;self.change=False;self.missing=False
    async def call(self,method,args):
        self.calls.append((method,args))
        if method=='inspectSession':return {'historyHome':'/workspace','workingDirectory':'/workspace','executionDirectory':'/workspace'}
        if method=='listRecallSources' and self.missing:return {'items':[]}
        if method=='listRecallSources':return {'items':[{'id':SID,'title':'Owned','workspace':'/workspace','kind':'root'}],'coverage':{'source':'fixture','children':False}}
        if method=='inspectRecallSource':
            if self.missing:raise ValueError('Source directory no longer visible')
            return {'id':SID,'title':'Owned','workspace':'/workspace','kind':'root','revision':self.revision}
        if method=='readRecallSource':
            result={'rows':[{**self.message,'id':'native:typed','inputOrigin':'unknown','authorization':'unverified-native-history','sourceKind':'native-history'}],'revision':self.revision}
            if self.change:self.revision='r2'
            return result
        if method=='readSessionContext':return {'messages':[self.message]}
        if method=='readUserMessage':
            if args['messageId']!=self.message['id']:raise ValueError('not-indexed')
            return copy.deepcopy(self.message)
        if method=='nativeControlExisting':
            self.model_calls+=1
            if self.model_error:raise ValueError('lost model confirmation')
            return {'text':json.dumps([{'text':'Keep cobalt headings concise.','quote':self.message['text'],'supersedes':[]}]),'provider':'fixture','model':'offline'}
        raise ValueError(method)

@pytest.fixture
async def owner(tmp_path):
    host=Host();events=[]
    async def notify(method,params):events.append((method,params))
    owner=Owner({'dataDir':str(tmp_path)},host.call,notify)
    yield owner,host,events
    await owner.close()

async def action(owner,name,args={},origin='ui',identity=None):return await owner.request('action',{'session':SID,'operation':name,'args':args,'origin':origin,'commandId':identity or name})
async def settled(owner):
    while owner.tasks:
        await asyncio.gather(*list(owner.tasks.values()));await asyncio.sleep(0)

@pytest.mark.asyncio
async def test_passive_defaults_and_scoped_atomic_indexing(owner):
    item,host,events=owner
    assert (await item.request('initialize',{}))['protocolVersion']==1
    assert not host.calls
    snapshot=await item.request('snapshot',{'session':SID});assert not snapshot['recall'][SID]['memory']['settings']['contribute'];assert host.model_calls==0
    assert not any(name in {'readRecallSource','listRecallSources'} for name,_ in host.calls)
    await action(item,'recall.refresh');await settled(item)
    result=await action(item,'recall.search',{'query':'cobalt'});assert len(result['items'])==1
    row=result['items'][0];read=await action(item,'recall.read',{'sourceSessionId':SID,'messageId':row['messageId'],'sourceRevision':row['sourceRevision']});assert read['text']==host.message['text']
    host.revision='r2'
    with pytest.raises(ValueError,match='changed since indexing'):await action(item,'recall.read',{'sourceSessionId':SID,'messageId':row['messageId'],'sourceRevision':row['sourceRevision']})
    assert host.model_calls==0

@pytest.mark.asyncio
async def test_changed_source_does_not_publish_partial_generation_and_hidden_sources_are_not_returned(owner):
    item,host,_=owner;await action(item,'recall.refresh');await settled(item);before=item.store.signature(SID)
    host.revision='new';host.change=True;await action(item,'recall.refresh');await settled(item)
    assert item.progress(SID)['status']=='partial' and item.store.signature(SID)==before
    host.missing=True;assert (await action(item,'recall.search',{'query':'cobalt'}))['items']==[]
    assert item.store.db.execute('SELECT COUNT(*) FROM staged_sources').fetchone()[0]==0

@pytest.mark.asyncio
async def test_memory_consent_requires_host_admission_and_mutations_are_idempotent(owner):
    item,host,_=owner;host.message['provenance']['source']='host-projection'
    with pytest.raises(ValueError,match='host-admitted'):await action(item,'memory.configure',{'expectedRevision':0,'contribute':True,'authorizationMessageId':'typed'},'agent')
    with pytest.raises(ValueError):await action(item,'memory.create',{'scope':'workspace','text':'cobalt','authorizationMessageId':'native:typed'},'agent')
    host.message['provenance']['source']='host-admission'
    note=await action(item,'memory.create',{'scope':'workspace','text':'cobalt','authorizationMessageId':'typed'},'agent',identity='authorized-create')
    assert item.store.memory(note['id'])['provenance']['authorizationSha256']==digest(host.message['text'])
    args={'id':note['id'],'expectedRevision':1}
    first=await action(item,'memory.delete',args,identity='delete');assert first==await action(item,'memory.delete',args,identity='delete')
    assert not item.store.list_memories(None)['items']

@pytest.mark.asyncio
async def test_opt_in_model_budget_quote_verification_and_context(owner):
    item,host,_=owner
    with pytest.raises(ValueError,match='not enabled'):await action(item,'memory.consolidate')
    await action(item,'memory.configure',{'expectedRevision':0,'contribute':True,'use':True,'maxCallsPerDay':1})
    await action(item,'memory.consolidate',identity='first');await settled(item)
    notes=(await action(item,'memory.list'))['items'];assert len(notes)==1 and notes[0]['source']['quote']==host.message['text'];assert host.model_calls==1
    result=await action(item,'memory.context');assert len(result['items'])==1;assert 'quote' not in result['items'][0]['source']
    await item.request('context',{'session':SID,'expected':result});assert item.status(await item.session(SID))['lastContext']['items']
    await action(item,'memory.consolidate',identity='second');await settled(item);assert host.model_calls==1
    host.message['text']='A changed cobalt statement without the prior quotation.'
    assert (await action(item,'memory.context'))['items']==[]

@pytest.mark.asyncio
async def test_uncertain_model_attempt_is_never_replayed_after_restart(tmp_path):
    host=Host();host.model_error=True
    async def notify(*args):pass
    item=Owner({'dataDir':str(tmp_path)},host.call,notify)
    await action(item,'memory.configure',{'expectedRevision':0,'contribute':True});await action(item,'memory.consolidate');await settled(item);assert host.model_calls==1;await item.close()
    item=Owner({'dataDir':str(tmp_path)},host.call,notify)
    try:
        await action(item,'memory.consolidate',identity='another');await settled(item);assert host.model_calls==1
        assert item.policy.attempts('/workspace')[0]['status']=='unknown'
    finally:await item.close()

@pytest.mark.asyncio
async def test_agent_and_session_scope_are_checked_before_stores(owner):
    item,host,_=owner
    with pytest.raises(ValueError,match='trusted scope'):await action(item,'memory.list',{'sessionId':'ahp-session:/other'})
    with pytest.raises(ValueError,match='not advertised'):await action(item,'recall.search',{'query':'anything','includeChildren':True})

@pytest.mark.asyncio
async def test_search_reauthorizes_metadata_without_native_stat_and_exact_receipts_are_scoped(owner):
    item,host,_=owner;await action(item,'recall.refresh');await settled(item);host.calls.clear()
    assert (await action(item,'recall.search',{'query':'cobalt'}))['items']
    assert not any(name in {'inspectRecallSource','readRecallSource'} for name,_ in host.calls)
    host.missing=True;assert (await action(item,'recall.search',{'query':'cobalt'}))['items']==[]
    note=await action(item,'memory.create',{'scope':'task','text':'Saved once'},identity='once')
    receipt=await action(item,'memory.command',{'commandId':'once'});assert receipt['state']=='succeeded' and receipt['result']==note
    with pytest.raises(ValueError,match='another conversation'):
        await item.request('action',{'session':'ahp-session:/other','operation':'memory.command','args':{'commandId':'once'},'origin':'ui','commandId':'read-other'})

@pytest.mark.asyncio
async def test_stdio_shutdown_preserves_uncertain_model_and_joins_handlers(tmp_path):
    import sys
    host=Host();config=tmp_path/'config.json';config.write_text(json.dumps({'dataDir':str(tmp_path/'state')}))
    process=await asyncio.create_subprocess_exec(sys.executable,'-I','-m','amplifier_unified_recall.server','--config',str(config),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    async def send(row):
        process.stdin.write((json.dumps({'jsonrpc':'2.0',**row})+'\n').encode());await process.stdin.drain()
    async def until_response(identity,stop_model=False):
        while True:
            row=json.loads(await asyncio.wait_for(process.stdout.readline(),5))
            if row.get('method','').startswith('host/'):
                method=row['method'][5:]
                if method=='nativeControlExisting' and stop_model:return row
                await send({'id':row['id'],'result':await host.call(method,row['params'])})
            elif row.get('id')==identity:return row
    await send({'id':'configure','method':'action','params':{'session':SID,'operation':'memory.configure','args':{'expectedRevision':0,'contribute':True},'commandId':'configure','origin':'ui'}})
    assert 'result' in await until_response('configure')
    await send({'id':'consolidate','method':'action','params':{'session':SID,'operation':'memory.consolidate','args':{},'commandId':'consolidate','origin':'ui'}})
    # Keep processing after the admission response until the actual model request is outstanding.
    while True:
        row=await until_response('never',stop_model=True)
        if row.get('method')=='host/nativeControlExisting':break
    # A concurrent request waiting on another callback exercises handler-before-store shutdown.
    await send({'id':'pending-write','method':'action','params':{'session':SID,'operation':'memory.create','args':{'scope':'task','text':'in-flight'},'commandId':'pending-write','origin':'ui'}})
    process.stdin.close();await asyncio.wait_for(process.wait(),5)
    assert process.returncode==0,(await process.stderr.read()).decode()
    assert not await process.stderr.read()
    async def notify(*args):pass
    item=Owner({'dataDir':str(tmp_path/'state')},host.call,notify)
    try:
        attempts=item.policy.attempts('/workspace');assert len(attempts)==1 and attempts[0]['status']=='unknown'
        plan=item.store.db.execute("EXPLAIN QUERY PLAN UPDATE memory_attempts SET value='{}' WHERE json_extract(value,'$.status')='claimed'").fetchall()
        assert any('memory_attempts_status' in str(row) for row in plan)
        await action(item,'memory.consolidate',identity='after-restart');await settled(item);assert host.model_calls==0
    finally:await item.close()

@pytest.mark.asyncio
async def test_background_index_holds_owner_and_settlement_wakes_without_replay(tmp_path):
    host=Host();entered=asyncio.Event();finish=asyncio.Event();events=[]
    async def callback(method,args):
        if method=='readRecallSource':entered.set();await finish.wait()
        return await host.call(method,args)
    async def notify(method,args):events.append(method)
    owner=Owner({'dataDir':str(tmp_path)},callback,notify)
    context=dict(fenceId='fence',commandId='recovery',purpose='recovery',instanceId='host',dataScope='scope')
    try:
        await action(owner,'recall.refresh');await entered.wait()
        assert (await owner.request('quiescence.acquire',context))['acquired'] is False
        finish.set();await settled(owner);await asyncio.sleep(0)
        assert events.count('owner/idle')==1
        assert (await owner.request('quiescence.acquire',context))['acquired'] is True
        with pytest.raises(ValueError,match='intake is closed'):await action(owner,'recall.refresh',identity='blocked')
        assert (await action(owner,'recall.status'))['status']=='ready'
        calls=len(host.calls);await owner.close();owner=Owner({'dataDir':str(tmp_path)},callback,notify)
        assert (await owner.request('quiescence.inspect',{}))['intakeClosed']
        assert len(host.calls)==calls
        proof={**context,'verified':True,'outcome':'unchanged','receiptId':'checked'}
        await owner.request('quiescence.release',{**context,'outcome':'unchanged','proof':proof})
        assert host.model_calls==0
    finally:finish.set();await owner.close()
