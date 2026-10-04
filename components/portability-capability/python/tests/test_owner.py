import asyncio,base64,hashlib,json,shutil,subprocess,uuid
from pathlib import Path
import pytest
from amplifier_unified_portability.owner import Owner
from amplifier_portability.evidence import envelope,decode_body
from amplifier_worktrees.git import atomic,snapshot

def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],stderr=subprocess.DEVNULL,text=True).strip()
def repo(path):
    path.mkdir();git(path,'init');git(path,'config','user.email','fixture@example.test');git(path,'config','user.name','Fixture');(path/'a.txt').write_text('preserved source\n');git(path,'add','.');git(path,'commit','-m','fixture');return path

class Host:
    def __init__(self,sid,cwd,uri):
        self.session={'uri':uri,'nativeSessionId':sid,'workingDirectory':str(cwd),'historyHome':str(cwd),'executionDirectory':str(cwd),'executionRevision':0,'engineId':'native','title':'Source'}
        self.calls=[];self.fail_adoption=False;self.adoptions={};self.proof={};self.frames={}
        self.files={'transcript.jsonl':b'{"role":"user","content":"Never replay saved text"}\n','metadata.json':json.dumps({'session_id':sid}).encode(),'events.jsonl':b'{"type":"historical"}\n'}
    async def __call__(self,method,p):
        self.calls.append((method,p))
        if method=='payloadCapabilities':return None
        if method=='inspectSession':return {**self.session,'uri':p['session']}
        if method=='authorizeTransfer':return {'approved':True,'fixture':True}
        if method in {'beginTransfer','commitTransfer','cancelTransfer'}:return {'admitted':True,'fixture':True}
        if method=='exportTransferEvidence':return {'evidence':[envelope('operations',1,{'records':[{'status':'unknown','input':'retained'}]},omissions=[{'kind':'fixture-history-window','omitted':2}])],'omissions':[{'kind':'external-attachment-bodies','included':False}]}
        if method=='stageTransferEvidence':
            for value in p['evidence']:assert decode_body(value,accept_partial=p['acceptPartial'])['records'][0]['status']=='unknown'
            return {'receipts':[{'owner':'operations','executionAuthority':False,'staged':True}]}
        if method=='activateTransferEvidence':return {'receipts':[{'owner':'operations','executionAuthority':False,'replayed':False}]}
        if method=='adoptTransferredSession':
            self.adoptions.setdefault(p['commandId'],{'uri':p['session'],'created':True})
            if self.fail_adoption:self.fail_adoption=False;raise ConnectionError('lost adoption response')
            return self.adoptions[p['commandId']]
        if method=='nativeTransfer':
            op=p['operation'];args=p['args'];identity=args['transferId']
            if op=='source.capture':return {'files':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in self.files.items()},'intent':{'bundle':'fixture','selection':{'provider':'fixture','model':'offline'}},'origin':{'historyHome':self.session['historyHome'],'executionDirectory':self.session['executionDirectory']},'omissions':[{'kind':'external-attachment-bodies','included':False}]}
            if op=='source.read':
                data=self.files[args['name']];offset=args['offset'];chunk=data[offset:offset+args['limit']]
                return {'data':base64.b64encode(chunk).decode(),'nextOffset':offset+len(chunk) if offset+len(chunk)<len(data) else None}
            if op=='destination.policy':return {'policy':{'offlineFixture':True},'policyHash':'offline-fixture'}
            if op=='destination.check':return {'runtimeVerified':True,'accountVerified':True,'nativeFenceVerified':True,'credentialsOrigin':'destination','fixtureOnly':True}
            if op=='destination.activate':self.proof={'phase':'activated','releaseHash':'fixture-proof'};return self.proof
            if op=='inspect':return {'receipt':self.proof,'fence':None}
            return {'fixtureOnly':True}
        raise AssertionError(method)
async def notify(*args):pass
async def action(owner,scope,op,args,command):return await owner.request('action',{'session':scope,'operation':'portability.'+op,'args':args,'commandId':command,'origin':'ui'})
def config(root):
    root.mkdir();(root/'work').mkdir()
    return {'dataDir':str(root/'owner'),'exchangeDir':str(root/'exchange'),'stageDir':str(root/'work/stages'),'workspaceRoots':[str(root/'work')],'label':root.name}

@pytest.mark.asyncio
async def test_two_signed_owners_real_git_and_lost_adoption_reconcile_no_effect_replay(tmp_path):
    ca=config(tmp_path/'a');cb=config(tmp_path/'b');source=repo(Path(ca['workspaceRoots'][0])/'repo');destination=Path(cb['workspaceRoots'][0])/'repo';subprocess.run(['git','clone',str(source),str(destination)],check=True,capture_output=True)
    sid=str(uuid.uuid4());uri='ahp-session:///'+sid;ha=Host(sid,source,uri);hb=Host(sid,destination,uri)
    a=Owner(ca,ha,notify);b=Owner(cb,hb,notify)
    atomic(a.node.directory/'peers.json',{b.node.identity['id']:b.node.identity});atomic(b.node.directory/'peers.json',{a.node.identity['id']:a.node.identity})
    try:
        revision=snapshot(source)[0]['sourceRevision'];args={'sessionId':uri,'destination':b.node.identity['id'],'sourceRevision':revision,'expectedExecutionRevision':0,'mode':'clean','reviewedContent':True}
        outgoing=await action(a,uri,'export',args,'export1');assert outgoing['phase']=='prepared'
        package=b.exchange/'package.json';shutil.copyfile(outgoing['package'],package)
        reviewed=await action(b,'host','review',{'path':str(package)},'preview');assert reviewed['capsuleHash']==outgoing['review']['capsuleHash'];assert reviewed['omissions'];assert reviewed['evidence'][0]['disposition']=='partial';assert reviewed['evidence'][0]['omissions'][0]['omitted']==2
        before=len(hb.calls)
        with pytest.raises(ValueError,match='Reviewed signed capsule'):await action(b,'host','stage',{'path':str(package),'repository':str(destination),'reviewedCapsuleHash':'0'*64},'stale-stage')
        assert not any(m=='nativeTransfer' for m,_ in hb.calls[before:])
        incoming=await action(b,'host','stage',{'path':str(package),'repository':str(destination),'reviewedCapsuleHash':outgoing['review']['capsuleHash']},'stage1');assert incoming['phase']=='ready'
        target=Path(b.node.get(incoming['id'])['destinationState']['workspace']);assert (target/'a.txt').read_text()=='preserved source\n'
        ready=a.exchange/'ready.json';shutil.copyfile(incoming['receiptPath'],ready)
        before=len(ha.calls)
        with pytest.raises(ValueError,match='Reviewed signed capsule'):await action(a,uri,'release',{'sessionId':uri,'id':outgoing['id'],'expectedRevision':outgoing['revision'],'path':str(ready),'reviewedCapsuleHash':'0'*64},'stale-release')
        assert not any(m in {'commitTransfer','nativeTransfer'} for m,_ in ha.calls[before:])
        released=await action(a,uri,'release',{'sessionId':uri,'id':outgoing['id'],'expectedRevision':outgoing['revision'],'path':str(ready),'reviewedCapsuleHash':outgoing['review']['capsuleHash']},'release1');assert released['phase']=='released'
        proof=b.exchange/'release.json';shutil.copyfile(released['receiptPath'],proof);hb.fail_adoption=True
        with pytest.raises(ConnectionError):await action(b,'host','activate',{'id':incoming['id'],'expectedRevision':incoming['revision'],'path':str(proof)},'activate1')
        assert b.node.get(incoming['id'])['phase']=='active'
        saved=await action(b,'host','command',{'commandId':'activate1'},'read1');assert saved['receipt']['state']=='unknown'
        reconciled=await action(b,'host','reconcile',{'id':incoming['id']},'reconcile1');assert reconciled['adoption']['uri']==uri and reconciled['inputsReplayed'] is False
        ops=[p['operation'] for method,p in hb.calls if method=='nativeTransfer'];assert ops.count('destination.check')==2 and ops.count('destination.install')==1 and ops.count('destination.activate')==1
        assert len(hb.adoptions)==1
        assert a.node.fenced(sid) and not b.node.fenced(sid)
        inspected=await action(a,uri,'inspect',{'sessionId':uri,'limit':1},'inspect1');assert len(inspected['receipts'])==1 and inspected['fenced'];assert inspected['source']['sourceRevision']==revision and inspected['source']['expectedExecutionRevision']==0
        # All provider callbacks here are named fixtures. The native package's
        # actual Core/Foundation tests qualify real fence/probe implementation.
    finally:await a.close();await b.close()

@pytest.mark.asyncio
async def test_unconfigured_exchange_path_has_no_native_effect(tmp_path):
    cfg=config(tmp_path/'owner');workspace=repo(Path(cfg['workspaceRoots'][0])/'repo');host=Host(str(uuid.uuid4()),workspace,'ahp-session:///one');owner=Owner(cfg,host,notify)
    try:
        outside=tmp_path/'outside.json';outside.write_text('{}')
        with pytest.raises(ValueError,match='authority'):await action(owner,'host','stage',{'path':str(outside),'repository':str(workspace),'reviewedCapsuleHash':'0'*64},'bad1')
        assert owner.node.page()['items']==[]
        assert not any(m=='nativeTransfer' for m,_ in host.calls)
        assert (await action(owner,'host','command',{'commandId':'bad1'},'read'))['receipt']['state']=='unknown'
    finally:await owner.close()


FENCE={'fenceId':'maintenance-fence','commandId':'maintenance-command','purpose':'recovery','instanceId':'old-launch','dataScope':'owned'}
def release():return {**FENCE,'outcome':'unchanged','proof':{'verified':True,**FENCE,'outcome':'unchanged','receiptId':'owner-known-nochange'}}

@pytest.mark.asyncio
async def test_held_intake_covers_evidence_callbacks_and_cancelled_complete_effect(tmp_path):
    cfg=config(tmp_path/'a');cwd=repo(Path(cfg['workspaceRoots'][0])/'repo');uri='ahp-session:///one';host=Host(str(uuid.uuid4()),cwd,uri)
    entered=asyncio.Event();finish=asyncio.Event();signals=[]
    async def delayed(method,args):
        if method=='exportTransferEvidence':entered.set();await finish.wait()
        return await host(method,args)
    async def notification(method,args):signals.append(method)
    owner=Owner(cfg,delayed,notification)
    # Export does not require a destination account or native runtime; the source
    # proof/capture callbacks are deterministic, explicitly named fixtures.
    peer=Owner(config(tmp_path/'b'),host,notify);atomic(owner.node.directory/'peers.json',{peer.node.identity['id']:peer.node.identity})
    args={'sessionId':uri,'destination':peer.node.identity['id'],'sourceRevision':snapshot(cwd)[0]['sourceRevision'],'expectedExecutionRevision':0,'mode':'clean','reviewedContent':True}
    request=asyncio.create_task(action(owner,uri,'export',args,'export-held'))
    try:
        await asyncio.wait_for(entered.wait(),15);request.cancel();await asyncio.sleep(0)
        assert (await owner.request('quiescence/acquire',FENCE))['acquired'] is False
        assert (await owner.request('quiescence/inspect',{}))['calls']==1
        finish.set();await asyncio.gather(request,return_exceptions=True)
        assert owner.command(uri,'export-held')['state']=='completed'
        assert (await owner.request('quiescence/acquire',FENCE))['acquired'] is True
        assert 'owner/idle' in signals
        from amplifier_unified_portability.owner import IntakeHeld
        with pytest.raises(IntakeHeld):await action(owner,uri,'export',args,'never')
        assert owner.command(uri,'never') is None
        assert (await action(owner,uri,'command',{'commandId':'export-held'},'read'))['receipt']['state']=='completed'
        assert (await owner.request('quiescence/release',{**FENCE,'outcome':'unknown'}))['intakeClosed']
        await owner.close();owner=Owner(cfg,host,notify);assert (await owner.request('quiescence/inspect',{}))['intakeClosed']
        await owner.request('quiescence/release',release());await owner.close();owner=Owner(cfg,host,notify)
        assert (await owner.request('quiescence/release',release()))['released']
        with pytest.raises(ValueError,match='release receipt'):await owner.request('quiescence/release',{**release(),'proof':{**release()['proof'],'receiptId':'changed'}})
        assert len([p for m,p in host.calls if m=='nativeTransfer' and p['operation']=='source.capture'])==1
    finally:finish.set();await owner.close();await peer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('table',['commands','bindings'])
async def test_missing_adapter_authority_refuses_before_recovery(tmp_path,monkeypatch,table):
    import sqlite3
    from amplifier_unified_portability import owner as module
    cfg=config(tmp_path/'fixture');host=Host('native-original',Path(cfg['workspaceRoots'][0]),'ahp-session:///original')
    owner=Owner(cfg,host,notify)
    owner.save_command('host','original','hash',{'state':'unknown'})
    owner.bind('transfer','ahp-session:///original','native-original',cfg['workspaceRoots'][0],'original-engine')
    await owner.close()
    path=Path(cfg['dataDir'])/'owner.sqlite3'
    with sqlite3.connect(path) as db:db.execute('DROP TABLE '+table)
    before=path.read_bytes()
    def deny(*args,**kwargs):raise AssertionError('Adapter validation must precede transfer recovery')
    with monkeypatch.context() as patch:
        patch.setattr(module,'TransferNode',deny)
        with pytest.raises(ValueError,match='requires inspection'):Owner(cfg,host,notify)
    assert path.read_bytes()==before and host.calls==[]
    # Failed startup releases its lease; repeated inspection cannot become busy.
    with pytest.raises(ValueError,match='requires inspection'):Owner(cfg,host,notify)


@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
@pytest.mark.parametrize('kind',['bytes','empty','dangling'])
def test_orphan_adapter_sidecar_refuses_without_open(tmp_path,monkeypatch,suffix,kind):
    from amplifier_unified_portability import owner as module
    cfg=config(tmp_path/'fixture');directory=Path(cfg['dataDir']);directory.mkdir()
    sidecar=directory/('owner.sqlite3'+suffix)
    if kind=='dangling':sidecar.symlink_to(directory/'missing')
    else:sidecar.write_bytes(b'uncertain evidence' if kind=='bytes' else b'')
    def deny(*args,**kwargs):raise AssertionError('Orphan storage must not open SQLite')
    with monkeypatch.context() as patch:
        patch.setattr(module.sqlite3,'connect',deny)
        with pytest.raises(ValueError,match='requires inspection'):Owner(cfg,None,notify)
    assert not (directory/'owner.sqlite3').exists()
    assert sidecar.is_symlink() if kind=='dangling' else sidecar.read_bytes()==(b'uncertain evidence' if kind=='bytes' else b'')


@pytest.mark.asyncio
async def test_healthy_adapter_preserves_unknown_and_binding_without_history_transfer(tmp_path):
    cfg=config(tmp_path/'fixture');host=Host('native',Path(cfg['workspaceRoots'][0]),'ahp-session:///original')
    owner=Owner(cfg,host,notify);owner.save_command('host','original','hash',{'state':'unknown'})
    owner.bind('transfer','ahp-session:///original','native',cfg['workspaceRoots'][0],'engine')
    await owner.close();owner=Owner(cfg,host,notify)
    try:
        assert owner.command('host','original')['state']=='unknown'
        with pytest.raises(ValueError,match='binding changed'):owner.bind('transfer','ahp-session:///changed','other',cfg['workspaceRoots'][0],'other-engine')
        assert host.calls==[]
    finally:await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('orphan',[False,True])
async def test_actual_crash_wal_preserves_adapter_authority(tmp_path,orphan):
    import os,sys
    import amplifier_unified_portability.owner as module
    cfg=config(tmp_path/'fixture')
    code="""import sys,json,os
sys.path.insert(0,sys.argv[1])
from amplifier_unified_portability.owner import Owner
async def callback(*args):raise AssertionError('No host callback')
cfg=json.loads(sys.argv[2]);owner=Owner(cfg,callback,callback)
owner.db.execute('PRAGMA wal_autocheckpoint=0')
owner.save_command('host','original','hash',{'state':'running'})
owner.bind('transfer','ahp-session:///original','native',cfg['workspaceRoots'][0],'engine')
os._exit(0)
"""
    subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(module.__file__).parent.parent),json.dumps(cfg)],check=True)
    path=Path(cfg['dataDir'])/'owner.sqlite3';wal=Path(str(path)+'-wal');assert wal.stat().st_size>0
    if orphan:
        path.unlink();before={p.name:p.read_bytes() for p in path.parent.iterdir() if p.name.startswith('owner.sqlite3')}
        with pytest.raises(ValueError,match='requires inspection'):Owner(cfg,None,notify)
        assert not path.exists()
        assert {p.name:p.read_bytes() for p in path.parent.iterdir() if p.name.startswith('owner.sqlite3')}==before
    else:
        owner=Owner(cfg,None,notify)
        try:
            assert owner.command('host','original')['state']=='unknown'
            with pytest.raises(ValueError,match='binding changed'):owner.bind('transfer','ahp-session:///changed','other',cfg['workspaceRoots'][0],'changed')
        finally:await owner.close()


@pytest.mark.parametrize('suffix',['','-wal','-shm','-journal'])
def test_adapter_fifo_refuses_before_sqlite_open(tmp_path,monkeypatch,suffix):
    import os,sqlite3
    cfg=config(tmp_path/'fixture');directory=Path(cfg['dataDir']);directory.mkdir()
    if suffix:
        with sqlite3.connect(directory/'owner.sqlite3') as db:
            db.executescript('CREATE TABLE commands(scope,id,signature,body);CREATE TABLE bindings(transfer,uri,native,cwd,engine)')
    path=directory/('owner.sqlite3'+suffix);os.mkfifo(path)
    def deny(*args,**kwargs):raise AssertionError('Non-regular storage must not open SQLite')
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',deny)
        with pytest.raises(ValueError,match='requires inspection'):Owner(cfg,None,notify)
    assert path.is_fifo()


@pytest.mark.parametrize('refusal',[False,True])
def test_adapter_preflight_connections_close_without_garbage_collection(tmp_path,monkeypatch,refusal):
    import sqlite3
    from amplifier_unified_portability.owner import validate_storage
    path=tmp_path/'owner.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE commands(scope,id,signature,body);CREATE TABLE bindings(transfer,uri,native,cwd,engine)')
        if refusal:db.execute('DROP TABLE commands')
    connect=sqlite3.connect;held=[]
    def tracked(*args,**kwargs):
        db=connect(*args,**kwargs);held.append(db);return db
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',tracked)
        for _ in range(12):
            if refusal:
                with pytest.raises(ValueError,match='requires inspection'):validate_storage(path)
            else:validate_storage(path)
    assert len(held)==12
    for db in held:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):db.execute('SELECT 1')
