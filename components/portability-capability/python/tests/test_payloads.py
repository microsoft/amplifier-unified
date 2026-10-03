import base64,copy,hashlib,json,shutil,uuid
from pathlib import Path
import pytest
from amplifier_portability.evidence import envelope
from amplifier_unified_portability.owner import Owner
from amplifier_unified_portability.payloads import CAPABILITIES
from amplifier_worktrees.git import atomic,snapshot
from test_owner import Host,repo,config,notify,action

class PayloadHost(Host):
    def __init__(self,*args):
        super().__init__(*args);self.resource_id=str(uuid.uuid4());self.duplicate_id=str(uuid.uuid4());self.raw=(b'\0\xffexact\r\n'*1100000)+b'end';self.revision='resource-snapshot-1';self.owner=None;self.staged=None
    def descriptor(self):
        from urllib.parse import urlencode
        sha=hashlib.sha256(self.raw).hexdigest()
        return {'id':self.resource_id,'name':'large.bin','contentType':'application/octet-stream','size':len(self.raw),'sha256':sha,'etag':'"sha256:'+sha+'"','resourceUri':'amplifier-attachment://'+self.resource_id+'/body?'+urlencode({'session':self.session['uri']}),'status':'committed'}
    async def __call__(self,method,p):
        if method=='payloadCapabilities':return CAPABILITIES
        if method=='readTransferAttachmentMetadata':
            if p.get('expectedRevision') not in (None,self.revision):raise ValueError('Source metadata revision changed')
            return {'owner':'unified.resources','version':1,'session':p['session'],'revision':self.revision,'items':[self.descriptor(),{**self.descriptor(),'id':self.duplicate_id,'resourceUri':self.descriptor()['resourceUri'].replace(self.resource_id,self.duplicate_id)}],'nextCursor':None,'coverage':'partial','omissions':[{'collection':'attachment-bodies','count':1,'reason':'unfinished attachments remain on source'}]}
        if method=='readTransferPayloadSource':
            assert p['resourceId'] in (self.resource_id,self.duplicate_id)
            return {'encoding':'base64','data':base64.b64encode(self.raw[p['offset']:p['offset']+p['maxBytes']]).decode()}
        if method=='exportTransferEvidence':return {'evidence':[envelope('unified.resources','owner-revision-1',{'historicalOnly':True,'records':[]})],'omissions':[]}
        if method=='stageTransferEvidence':return {'receipts':[]}
        if method=='stageTransferPayloads':
            verified=await self.owner.request('payload/verify',{k:v for k,v in p.items() if k!='bodies'})
            assert verified['bodies'][0]['sha256']==hashlib.sha256(self.raw).hexdigest()
            assert Path(p['bodies'][0]['path']).read_bytes()==self.raw
            assert len(verified['bodies'])==2 and len(p['bodies'])==2
            assert p['bodies'][0]['path']==p['bodies'][1]['path']
            self.staged=p;return {'staged':True,'executionAuthority':False}
        if method=='activateTransferEvidence':
            assert p['payloadPlanHash']==self.staged['planHash'];return {'receipts':[{'replayed':False,'executionAuthority':False}]}
        return await super().__call__(method,p)

@pytest.mark.asyncio
async def test_signed_detached_payload_stage_and_verifier_rejects_forged_binding(tmp_path):
    ca,cb=config(tmp_path/'a'),config(tmp_path/'b');source=repo(Path(ca['workspaceRoots'][0])/'repo');target=Path(cb['workspaceRoots'][0])/'repo';shutil.copytree(source,target)
    sid=str(uuid.uuid4());uri='ahp-session:///'+sid;ha,hb=PayloadHost(sid,source,uri),PayloadHost(sid,target,uri);a,b=Owner(ca,ha,notify),Owner(cb,hb,notify);ha.owner=a;hb.owner=b;hb.resource_id=ha.resource_id;hb.duplicate_id=ha.duplicate_id
    atomic(a.node.directory/'peers.json',{b.node.identity['id']:{**b.node.identity,'payloadCapabilities':CAPABILITIES}});atomic(b.node.directory/'peers.json',{a.node.identity['id']:{**a.node.identity,'payloadCapabilities':CAPABILITIES}})
    try:
        outgoing=await action(a,uri,'export',{'sessionId':uri,'destination':b.node.identity['id'],'sourceRevision':snapshot(source)[0]['sourceRevision'],'expectedExecutionRevision':0,'mode':'clean','reviewedContent':True,'includeResourcePayloads':True},'export')
        assert outgoing['review']['sourceSelection']['omissions'];assert outgoing['review']['resourcePayload']['bytes']==2*len(ha.raw)
        incoming=b.exchange/'incoming.json';shutil.copyfile(outgoing['package'],incoming);sidecar=b.exchange/'incoming.payloads';shutil.copytree(outgoing['payloadDirectory'],sidecar)
        args={'path':str(incoming),'repository':str(target),'reviewedCapsuleHash':outgoing['review']['capsuleHash']}
        with pytest.raises(ValueError,match='sidecar'):await action(b,'host','stage',args,'missing-sidecar')
        result=await action(b,'host','stage',{**args,'payloadDirectory':str(sidecar)},'stage');assert result['phase']=='ready'
        valid={k:v for k,v in hb.staged.items() if k!='bodies'}
        for key in ['transferId','sourceHost','destinationHost','ownerRevision','evidenceHash','planHash']:
            with pytest.raises((ValueError,FileNotFoundError)):await b.request('payload/verify',{**valid,key:'forged'})
        bad=copy.deepcopy(valid);bad['capsule']['signature']=base64.b64encode(b'0'*64).decode()
        with pytest.raises(ValueError,match='authentic'):await b.request('payload/verify',bad)
        bad=copy.deepcopy(valid);bad['plan']['bodyJson']+=' '
        with pytest.raises(ValueError,match='binding'):await b.request('payload/verify',bad)
        ready=a.exchange/'ready.json';shutil.copyfile(result['receiptPath'],ready)
        released=await action(a,uri,'release',{'sessionId':uri,'id':outgoing['id'],'expectedRevision':outgoing['revision'],'path':str(ready),'reviewedCapsuleHash':outgoing['review']['capsuleHash']},'release')
        certificate=b.exchange/'release.json';shutil.copyfile(released['receiptPath'],certificate)
        activated=await action(b,'host','activate',{'id':result['id'],'expectedRevision':result['revision'],'path':str(certificate)},'activate')
        assert activated['phase']=='active' and activated['inputsReplayed'] is False
    finally:await a.close();await b.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['missing-chunk','corrupt-chunk','lost-import-reply'])
async def test_invalid_or_uncertain_sidecar_stays_fenced_without_replay(tmp_path,failure):
    ca,cb=config(tmp_path/'a'),config(tmp_path/'b');source=repo(Path(ca['workspaceRoots'][0])/'repo');target=Path(cb['workspaceRoots'][0])/'repo';shutil.copytree(source,target)
    sid=str(uuid.uuid4());uri='ahp-session:///'+sid;ha,hb=PayloadHost(sid,source,uri),PayloadHost(sid,target,uri);ha.raw=hb.raw=b'bounded resource'
    a,b=Owner(ca,ha,notify),Owner(cb,hb,notify);ha.owner=a;hb.owner=b;hb.resource_id=ha.resource_id;hb.duplicate_id=ha.duplicate_id
    atomic(a.node.directory/'peers.json',{b.node.identity['id']:{**b.node.identity,'payloadCapabilities':CAPABILITIES}});atomic(b.node.directory/'peers.json',{a.node.identity['id']:{**a.node.identity,'payloadCapabilities':CAPABILITIES}})
    try:
        outgoing=await action(a,uri,'export',{'sessionId':uri,'destination':b.node.identity['id'],'sourceRevision':snapshot(source)[0]['sourceRevision'],'expectedExecutionRevision':0,'mode':'clean','reviewedContent':True,'includeResourcePayloads':True},'export')
        incoming=b.exchange/'incoming.json';shutil.copyfile(outgoing['package'],incoming);sidecar=b.exchange/'incoming.payloads';shutil.copytree(outgoing['payloadDirectory'],sidecar)
        chunk=sidecar/hashlib.sha256(ha.raw).hexdigest()
        if failure=='missing-chunk':chunk.unlink()
        if failure=='corrupt-chunk':chunk.write_bytes(b'corrupt')
        calls=[];original_host=hb.__call__
        async def channel(method,p):
            calls.append(method)
            if method=='stageTransferPayloads' and failure=='lost-import-reply':raise ConnectionError('import outcome unknown')
            return await original_host(method,p)
        b.host=channel
        args={'path':str(incoming),'repository':str(target),'reviewedCapsuleHash':outgoing['review']['capsuleHash'],'payloadDirectory':str(sidecar)}
        with pytest.raises((ValueError,FileNotFoundError,ConnectionError)):await action(b,'host','stage',args,'stage')
        row=b.node.get(outgoing['id']);assert row['phase']=='unknown' and b.node.fenced(sid)
        assert hb.staged is None and not any(p['operation']=='destination.install' for m,p in hb.calls if m=='nativeTransfer')
        if failure!='lost-import-reply':assert 'stageTransferPayloads' not in calls
        before=list(calls);retry=await action(b,'host','stage',args,'stage');assert retry['commandReceipt']['state']=='unknown' and retry['replayed'] is False;assert calls==before
        assert a.node.get(outgoing['id'])['phase']=='prepared' and a.node.fenced(sid)
    finally:await a.close();await b.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('boundary',['before-activation','interrupted-inspect'])
async def test_retained_signed_capsule_reauthenticated_before_effects_and_recovery(tmp_path,boundary):
    ca,cb=config(tmp_path/'a'),config(tmp_path/'b');source=repo(Path(ca['workspaceRoots'][0])/'repo');target=Path(cb['workspaceRoots'][0])/'repo';shutil.copytree(source,target)
    sid=str(uuid.uuid4());uri='ahp-session:///'+sid;ha,hb=PayloadHost(sid,source,uri),PayloadHost(sid,target,uri);ha.raw=hb.raw=b'authenticated history'
    a,b=Owner(ca,ha,notify),Owner(cb,hb,notify);ha.owner=a;hb.owner=b;hb.resource_id=ha.resource_id;hb.duplicate_id=ha.duplicate_id
    atomic(a.node.directory/'peers.json',{b.node.identity['id']:{**b.node.identity,'payloadCapabilities':CAPABILITIES}});atomic(b.node.directory/'peers.json',{a.node.identity['id']:{**a.node.identity,'payloadCapabilities':CAPABILITIES}})
    try:
        out=await action(a,uri,'export',{'sessionId':uri,'destination':b.node.identity['id'],'sourceRevision':snapshot(source)[0]['sourceRevision'],'expectedExecutionRevision':0,'mode':'clean','reviewedContent':True,'includeResourcePayloads':True},'export')
        package=b.exchange/'incoming.json';shutil.copyfile(out['package'],package);sidecar=b.exchange/'incoming.payloads';shutil.copytree(out['payloadDirectory'],sidecar)
        incoming=await action(b,'host','stage',{'path':str(package),'repository':str(target),'reviewedCapsuleHash':out['review']['capsuleHash'],'payloadDirectory':str(sidecar)},'stage')
        ready=a.exchange/'ready.json';shutil.copyfile(incoming['receiptPath'],ready)
        released=await action(a,uri,'release',{'sessionId':uri,'id':out['id'],'expectedRevision':out['revision'],'path':str(ready),'reviewedCapsuleHash':out['review']['capsuleHash']},'release')
        certificate=b.exchange/'release.json';shutil.copyfile(released['receiptPath'],certificate)
        activate={'id':incoming['id'],'expectedRevision':incoming['revision'],'path':str(certificate)}
        if boundary=='interrupted-inspect':
            original=b.host
            async def interrupted(method,args):
                if method=='nativeTransfer' and args['operation']=='inspect':raise ConnectionError('lost passive inspect response')
                return await original(method,args)
            b.host=interrupted
            with pytest.raises(ConnectionError):await action(b,'host','activate',activate,'activate')
            b.host=original
            assert b.node.get(incoming['id'])['phase']=='active'
        row=b.node.get(incoming['id']);stored=Path(row['destinationState']['package']);valid=stored.read_bytes();value=json.loads(valid);value['body']['payload']['originSession']='ahp-session:///forged';stored.write_text(json.dumps(value))
        before=len(hb.calls)
        operation='activate' if boundary=='before-activation' else 'reconcile'
        with pytest.raises(ValueError,match='authentic'):await action(b,'host',operation,activate if operation=='activate' else {'id':incoming['id']},'tampered')
        assert all(m=='authorizeTransfer' for m,_ in hb.calls[before:]) and not hb.adoptions
        assert a.node.fenced(sid)
        if boundary=='interrupted-inspect':
            stored.write_bytes(valid)
            recovered=await action(b,'host','reconcile',{'id':incoming['id']},'valid-recovery');assert recovered['inputsReplayed'] is False
            assert recovered['adoption']['uri']==uri
            ops=[p['operation'] for m,p in hb.calls if m=='nativeTransfer'];assert ops.count('destination.install')==1 and ops.count('destination.activate')==1
    finally:await a.close();await b.close()
