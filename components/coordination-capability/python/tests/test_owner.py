import asyncio,json
from pathlib import Path
import pytest
from amplifier_unified_coordination.owner import Owner

S='ahp-session:/one';T='ahp-session:/two'
class Host:
    def __init__(self):self.calls=[];self.results=[];self.latest=0;self.watches={};self.fail=False;self.available=True;self.allowed=True
    async def __call__(self,method,args):
        self.calls.append((method,args))
        if method=='listCoordinationSessions':return {'items':[{'uri':S}],'nextCursor':'opaque','coverage':{'source':'host-indexed','nativeHistoriesRead':False}}
        if method=='readCoordinationSession':
            if not self.allowed:raise ValueError('Access denied')
            after=args['args'].get('afterSequence',0)
            return {'available':self.available,'status':'working','results':[r for r in self.results if r['sequence']>after][:args['args']['limit']],'latestSequence':self.latest,'approvalIds':[],'coverage':{'source':'host-indexed'},'attentionCoverage':{'questions':False},'canFollowup':True,'canInterrupt':True}
        if method=='readCoordinationWorkers':return {'available':True,'item':{'workerId':'child','persistent':True,'status':'idle','canFollowup':True,'canInterrupt':True},'results':self.results,'latestSequence':self.latest}
        if method=='watch':self.watches[args['token']]=args;return {}
        if method=='unwatch':self.watches.pop(args['token'],None);return {}
        if method.startswith('control'):
            if self.fail:raise ConnectionError('Lost after admission')
            return {'accepted':True,'inputId':args.get('commandId','child-input')}
        raise AssertionError(method)
async def noop(*args):pass
def action(operation,args,command='cmd',origin='ui',caller=None):return {'operation':'coordination.'+operation,'args':args,'commandId':command,'clientId':'authenticated','origin':origin,'callerSession':caller}
@pytest.mark.asyncio
async def test_metadata_list_does_not_hydrate_workers(tmp_path):
    host=Host();owner=Owner({'dataDir':str(tmp_path)},host,noop)
    try:
        value=await owner.request('action',action('list',{'limit':2}));assert value['workersNotLoaded'] and value['nextCursor']=='opaque'
        assert [m for m,a in host.calls]==['listCoordinationSessions'];assert host.calls[0][1]['args']['limit']==2
        selected=await owner.request('action',action('list',{'sessionId':S,'workerId':'child'}));assert len(selected['items'])==1 and selected['items'][0]['target']['workerId']=='child'
    finally:await owner.close()
@pytest.mark.asyncio
async def test_wait_edges_repeatable_cursors_and_cleanup(tmp_path):
    host=Host();owner=Owner({'dataDir':str(tmp_path)},host,noop)
    try:
        first=await owner.request('action',action('wait',{'targets':[{'sessionId':S}]}));row=first['targets'][0];assert row['attentionUnknown'] and row['questionIds'] is None
        assert not host.watches
        args={'targets':[{'sessionId':S,'afterCursor':row['nextCursor']}],'waitMs':1000}
        waiting=asyncio.create_task(owner.request('action',action('wait',args)))
        while not host.watches:await asyncio.sleep(0)
        await asyncio.sleep(.02);host.latest=1;host.results=[{'id':'result-1','sequence':1,'text':'actual result'}]
        await owner.request('changed',{'token':next(iter(host.watches))});result=await waiting
        assert result['changed'] and result['targets'][0]['results'][0]['id']=='result-1' and not host.watches
        repeated=await owner.request('action',action('wait',{**args,'waitMs':0}));assert repeated['targets'][0]['results']==result['targets'][0]['results']
        timeout=await owner.request('action',action('wait',{'targets':[{'sessionId':S,'afterCursor':result['targets'][0]['nextCursor']}],'waitMs':1}));assert timeout['timedOut']
    finally:await owner.close()
@pytest.mark.asyncio
async def test_wait_explicit_gaps_unavailable_and_bounds(tmp_path):
    host=Host();host.latest=50;host.results=[{'id':'retained','sequence':40,'text':'x'*20000}];owner=Owner({'dataDir':str(tmp_path)},host,noop)
    try:
        value=await owner.request('action',action('wait',{'targets':[{'sessionId':S}],'maxBytes':4096}));row=value['targets'][0]
        assert row['cursorGap'] and row['hasMore'] and row['results'][0]['textTruncated'] and len(row['results'][0]['text'])==4096
        host.available=False;missing=await owner.request('action',action('wait',{'targets':[{'sessionId':S}]}));assert missing['errors'] and not missing['targets']
        with pytest.raises(Exception):await owner.request('action',action('wait',{'targets':[{'sessionId':S}]*9}))
        assert not host.watches
    finally:await owner.close()
@pytest.mark.asyncio
async def test_agent_scope_ui_authority_and_exact_duplicate(tmp_path):
    host=Host();owner=Owner({'dataDir':str(tmp_path)},host,noop)
    try:
        args={'sessionId':T,'text':'explicit message'}
        with pytest.raises(ValueError,match='explicit user'):await owner.request('action',action('followup',args,origin='agent',caller=S))
        sent=await owner.request('action',action('followup',args));assert sent['receipt']['status']=='accepted'
        repeated=await owner.request('action',action('followup',args));assert repeated['replayed'] is False
        controls=[a for m,a in host.calls if m=='controlCoordinationSession'];assert len(controls)==1 and controls[0]['origin']=='ui'
        with pytest.raises(ValueError,match='conflicts'):await owner.request('action',action('followup',{**args,'text':'different'}))
        await owner.request('action',action('followup',{'sessionId':S,'text':'own continuation'},command='agent1',origin='agent',caller=S))
        assert host.calls[-1][1]['origin']=='agent'
    finally:await owner.close()
@pytest.mark.asyncio
async def test_unknown_persists_without_replay_and_receipt_access_is_checked(tmp_path):
    host=Host();host.fail=True;args=action('followup',{'sessionId':S,'workerId':'child','text':'one'});owner=Owner({'dataDir':str(tmp_path)},host,noop)
    with pytest.raises(ConnectionError):await owner.request('action',args)
    await owner.close();owner=Owner({'dataDir':str(tmp_path)},host,noop)
    try:
        repeated=await owner.request('action',args);assert repeated['receipt']['status']=='unknown'
        assert len([m for m,a in host.calls if m=='controlCoordinationWorker'])==1
        exact=await owner.request('action',action('command',{'commandId':'cmd'}));assert exact['receipt']['target']['workerId']=='child'
        host.allowed=False
        with pytest.raises(ValueError,match='denied'):await owner.request('action',action('command',{'commandId':'cmd'}))
    finally:await owner.close()

@pytest.mark.asyncio
async def test_explicit_unknown_native_receipt_is_never_a_known_rejection(tmp_path):
    host=Host()
    async def callback(method,args):
        if method=='controlCoordinationWorker':return {'disposition':'unknown','accepted':False,'replayed':False}
        return await host(method,args)
    owner=Owner({'dataDir':str(tmp_path)},callback,noop)
    try:
        value=await owner.request('action',action('followup',{'sessionId':S,'workerId':'child','text':'one'}))
        assert value['receipt']['status']=='unknown'
    finally:await owner.close()

@pytest.mark.asyncio
async def test_quiescence_preserves_passive_waits_blocks_controls_and_recovers_exactly(tmp_path):
    host=Host();entered=asyncio.Event();finish=asyncio.Event()
    async def callback(method,args):
        if method=='controlCoordinationSession':entered.set();await finish.wait()
        return await host(method,args)
    owner=Owner({'dataDir':str(tmp_path)},callback,noop)
    context={'fenceId':'fence','commandId':'update','purpose':'distribution-update','instanceId':'original','dataScope':'owned'}
    active=asyncio.create_task(owner.request('action',action('followup',{'sessionId':S,'text':'once'})))
    try:
        await entered.wait();assert (await owner.request('quiescence.acquire',context))['acquired'] is False
        finish.set();await active
        assert (await owner.request('quiescence.acquire',context))['acquired'] is False
        context={**context,'fenceId':context['fenceId']+'-fresh','commandId':context['commandId']+'-fresh'}
        assert (await owner.request('quiescence.acquire',context))['acquired'] is True
        with pytest.raises(ValueError,match='intake is closed'):await owner.request('action',action('interrupt',{'sessionId':S},command='never'))
        assert (await owner.request('action',action('command',{'commandId':'cmd'})))['receipt']['status']=='accepted'
        assert (await owner.request('action',action('wait',{'targets':[{'sessionId':S}],'waitMs':0})))['targets']
        await owner.close();owner=Owner({'dataDir':str(tmp_path)},callback,noop)
        assert (await owner.request('quiescence.inspect',{}))['intakeClosed']
        with pytest.raises(ValueError,match='intake is closed'):await owner.request('action',action('followup',{'sessionId':S,'text':'blocked'},command='never'))
        proof={**context,'verified':True,'outcome':'unchanged','receiptId':'authenticated'}
        await owner.request('quiescence.release',{**context,'outcome':'unchanged','proof':proof})
        assert (await owner.request('action',action('followup',{'sessionId':S,'text':'after release'},command='new')))['receipt']['status']=='accepted'
        assert len([m for m,a in host.calls if m=='controlCoordinationSession'])==2
    finally:finish.set();await asyncio.gather(active,return_exceptions=True);await owner.close()
