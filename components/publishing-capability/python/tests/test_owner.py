import json
from pathlib import Path
import tempfile
from urllib.request import urlopen
import pytest
from amplifier_publishing import PublishingError
from amplifier_publishing.remote import SSHClient,unix_request
from amplifier_publishing.service import PublishingService
from amplifier_unified_publishing.owner import Owner

SID='ahp-session:/publishing-fixture'
@pytest.fixture
async def owner(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir();(workspace/'dist').mkdir();(workspace/'dist/index.html').write_text('<h1>One</h1>')
    calls=[]
    async def host(method,args):
        calls.append((method,args))
        if method=='inspectSession':return {'uri':args['session'],'workingDirectory':str(workspace),'executionDirectory':str(workspace),'executionRevision':0}
        raise ValueError('Explicit approval missing')
    async def notify(*_):pass
    value=Owner({'dataDir':str(tmp_path/'owner')},host,notify);value.fixture_workspace=workspace;value.fixture_calls=calls
    yield value
    await value.close()

def call(owner,name,args=None,origin='ui',command=None,sid=SID):
    owner.test_command_counter=getattr(owner,'test_command_counter',0)+1
    return owner.request('action',{'session':sid,'operation':'publishing.'+name,'args':args or {},'origin':origin,'commandId':command or name+':'+str(owner.test_command_counter),'clientId':'fixture'})
async def build(owner,rid='build',site='site'):
    return await call(owner,'build',{'siteId':site,'sourcePath':'dist','requestId':rid})

async def test_lazy_schema_and_bounds(owner):
    assert owner.store is None
    actions=await owner.request('actions',{})
    assert actions['publishing.deploy']['schema']['required'] and owner.store is None and not owner.fixture_calls
    with pytest.raises(ValueError,match='trusted context'):await call(owner,'build',{'sessionId':'ahp-session:/other','siteId':'site','sourcePath':'dist','requestId':'bad'})
    with pytest.raises(ValueError,match='inside'):await call(owner,'build',{'siteId':'site','sourcePath':'../','requestId':'bad'})
    outside=owner.fixture_workspace.parent/'outside';outside.mkdir();(outside/'index.html').write_text('secret');(owner.fixture_workspace/'link').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symbolic'):await call(owner,'build',{'siteId':'site','sourcePath':'link','requestId':'symlink'})
    assert (await call(owner,'list'))['releases']==[]

async def test_real_filesystem_full_lifecycle_and_approval(owner):
    release=await build(owner);assert release['sessionId']==SID
    with pytest.raises(PublishingError,match='Review'):await call(owner,'deploy',{'siteId':'site','releaseId':release['id'],'expectedRevision':0,'requestId':'no-review'})
    await call(owner,'review',{'releaseId':release['id'],'requestId':'review','note':'Fixture HTML inspected'})
    with pytest.raises(ValueError,match='approval'):await call(owner,'deploy',{'siteId':'site','releaseId':release['id'],'expectedRevision':0,'requestId':'agent-denied'},origin='agent')
    assert owner.targets.lookup_request(owner.scope(SID),'agent-denied') is None
    deployed=await call(owner,'deploy',{'siteId':'site','releaseId':release['id'],'expectedRevision':0,'requestId':'deploy'})
    assert urlopen(deployed['result']['url']).read()==b'<h1>One</h1>'
    (owner.fixture_workspace/'dist/index.html').write_text('<h1>Two</h1>');second=await build(owner,'build2')
    await call(owner,'review',{'releaseId':second['id'],'requestId':'review2','note':'Second checked'})
    update=await call(owner,'deploy',{'siteId':'site','releaseId':second['id'],'expectedRevision':1,'requestId':'update'})
    assert update['result']['url']==deployed['result']['url'] and urlopen(update['result']['url']).read()==b'<h1>Two</h1>'
    await call(owner,'rollback',{'siteId':'site','releaseId':release['id'],'expectedRevision':2,'requestId':'rollback'})
    assert urlopen(update['result']['url']).read()==b'<h1>One</h1>'
    await call(owner,'stop',{'siteId':'site','expectedRevision':3,'requestId':'stop'})
    await call(owner,'remove',{'siteId':'site','expectedRevision':4,'requestId':'remove'})
    assert (await call(owner,'status',{'siteId':'site'}))['status']=='removed'

async def test_exact_retry_after_source_removed_and_unknown_capture_never_replays(owner):
    release=await build(owner)
    (owner.fixture_workspace/'dist/index.html').unlink();(owner.fixture_workspace/'dist').rmdir()
    assert await build(owner)==release
    with pytest.raises(PublishingError,match='different'):await call(owner,'build',{'siteId':'elsewhere','sourcePath':'dist','requestId':'build'})
    sid=owner.scope(SID);args={'sessionId':sid,'siteId':'site','sourcePath':'dist','requestId':'lost'}
    owner.targets.bind_request(sid,'publishing.build',args)
    owner.build_source(sid,args,await owner.inspect(SID),owner.publisher())
    with pytest.raises(PublishingError,match='not be replayed'):await build(owner,'lost')
    assert (await call(owner,'receipt',{'requestId':'lost'}))['state']=='unknown'

async def test_pages_and_selected_manifest(owner):
    for i in range(7):
        (owner.fixture_workspace/'dist/index.html').write_text(str(i));await build(owner,f'build{i}')
    page=await call(owner,'list',{'collection':'releases','limit':2});assert len(page['items'])==2 and all('files' not in r for r in page['items'])
    assert (await call(owner,'release',{'releaseId':page['items'][0]['id']}))['files']
    seen=[];cursor=None
    while True:
        page=await call(owner,'list',{'collection':'receipts','limit':2,**({'cursor':cursor} if cursor else {})});seen.extend(r['requestId'] for r in page['items']);cursor=page['nextCursor']
        if not cursor:break
    assert len(seen)==len(set(seen))==7
    other=await call(owner,'list',sid='ahp-session:/other');assert not other['receipts']

async def test_agent_approval_is_exact_and_durable(owner):
    release=await build(owner);await call(owner,'review',{'releaseId':release['id'],'requestId':'review','note':'Checked'})
    host=owner.host;approvals=[]
    async def approved(method,args):
        if method=='authorizePublication':approvals.append(args);return {'approved':True,'approvalId':'approved-exact-release'}
        return await host(method,args)
    owner.host=approved;args={'siteId':'site','releaseId':release['id'],'expectedRevision':0,'requestId':'deploy'}
    first=await call(owner,'deploy',args,origin='agent');assert await call(owner,'deploy',args,origin='agent')==first and len(approvals)==1
    with pytest.raises(PublishingError,match='changed'):await call(owner,'deploy',{**args,'expectedRevision':1},origin='agent')

async def test_actual_service_transport_identity_and_lost_reply(owner):
    with tempfile.TemporaryDirectory(prefix='cap-pub-',dir='/tmp') as temporary:
        directory=Path(temporary).resolve()
        with PublishingService(directory/'store',directory/'admin'/'s.sock') as service:
            send={'lose':False,'mutations':0}
            def factory(**options):
                client=SSHClient(**options)
                def transport(request):
                    result=unix_request(service.socket_path,request)
                    if request['method'] in {'import','deploy'}:
                        send['mutations']+=1
                        if send['lose']:send['lose']=False;raise PublishingError('unknown_outcome','Fixture lost response after durable service success')
                    return result
                client._send=transport;return client
            owner.targets.client_factory=factory
            await call(owner,'target.save',{'targetId':'remote','expectedRevision':0,'label':'Fixture','hostname':'fixture','python':'/python','socketPath':'/fixture','expectedBind':'127.0.0.1'})
            inspected=await call(owner,'target.inspect',{'targetId':'remote','expectedRevision':1});target=next(t for t in inspected['targets'] if t['id']=='remote')
            await call(owner,'target.select',{'targetId':'remote','expectedRevision':1,'serviceId':target['inspection']['serviceId']})
            args={'targetId':'remote','targetRevision':1,'serviceId':target['inspection']['serviceId'],'siteId':'remote-site','sourcePath':'dist','requestId':'remote-build'}
            send['lose']=True
            with pytest.raises(PublishingError) as lost:await call(owner,'build',args)
            assert lost.value.code=='unknown_outcome' and send['mutations']==1
            release=await call(owner,'build',args);assert release['sessionId']==SID and send['mutations']==1
            receipt=await call(owner,'receipt',{'targetId':'remote','requestId':'remote-build'});assert receipt['state']=='succeeded' and receipt['remoteReceiptVerified']
            await call(owner,'target.select',{'targetId':'loopback','expectedRevision':0})
            assert await call(owner,'build',args)==release
            with pytest.raises(PublishingError,match='retargeted'):await call(owner,'build',{**args,'targetId':'loopback'})
            data=await call(owner,'list',{'targetId':'remote'});assert len(data['releases'])==1 and len(data['receipts'])==1

async def test_target_command_receipt_exact_retry_conflict_and_unknown(owner):
    args={'targetId':'remote','expectedRevision':0,'label':'Fixture','hostname':'fixture','python':'/python','socketPath':'/fixture','expectedBind':'127.0.0.1'}
    first=await call(owner,'target.save',args,command='save-exact')
    assert await call(owner,'target.save',args,command='save-exact')==first
    receipt=await call(owner,'command',{'commandId':'save-exact'})
    assert receipt['state']=='succeeded' and receipt['result']==first
    with pytest.raises(PublishingError,match='different'):await call(owner,'target.save',{**args,'label':'Changed'},command='save-exact')
    sid=owner.scope(SID)
    from amplifier_publishing.remote import canonical,digest
    private={**args,'sessionId':sid};signature=digest({'operation':'publishing.target.save','args':private,'origin':'ui'})
    lost={'commandId':'lost-target','sessionId':sid,'operation':'publishing.target.save','state':'unknown','result':None,'error':{'code':'unknown_outcome','message':'No replay'}}
    owner.db.execute('INSERT INTO commands VALUES (?,?,?,?)',(sid,'lost-target',signature,canonical(lost)));owner.db.commit()
    with pytest.raises(PublishingError,match='No replay'):await call(owner,'target.save',args,command='lost-target')
    assert (await call(owner,'command',{'commandId':'lost-target'}))['state']=='unknown'


async def test_cancelled_waiter_retains_worker_and_busy_wakes_only_after_thread(owner):
    import asyncio,threading
    entered=threading.Event();finish=threading.Event();events=[]
    async def notify(method,args):events.append(method)
    owner.notify=notify
    original=owner.publisher().build
    def held(*args,**kwargs):
        entered.set();finish.wait(5)
        return original(*args,**kwargs)
    owner.store.build=held
    work=asyncio.create_task(build(owner,'held-build'))
    await asyncio.to_thread(entered.wait,2)
    assert entered.is_set()
    context=dict(fenceId='fence',commandId='update',purpose='distribution-update',instanceId='host',dataScope='scope')
    try:
        work.cancel();await asyncio.sleep(0)
        assert not (await owner.request('quiescence.acquire',context))['acquired']
        assert owner.intake.calls==1
        finish.set()
        with pytest.raises(asyncio.CancelledError):await work
        assert events.count('owner/idle')==1
        assert (await owner.request('quiescence.acquire',context))['acquired']
        assert await call(owner,'command',{'commandId':'missing'}) is None
        with pytest.raises(ValueError,match='intake is closed'):await build(owner,'blocked')
        root=owner.root;host=owner.host
        await owner.close()
        replacement=Owner({'dataDir':str(root)},host,notify)
        try:
            assert (await replacement.request('quiescence.inspect',{}))['intakeClosed']
            proof={**context,'verified':True,'outcome':'unchanged','receiptId':'trusted'}
            params={**context,'outcome':'unchanged','proof':proof}
            assert (await replacement.request('quiescence.release',params))['released']
            assert (await replacement.request('quiescence.release',params))['released']
            receipt=await call(replacement,'receipt',{'requestId':'held-build'})
            assert receipt['state']=='succeeded'
        finally:await replacement.close()
    finally:finish.set()

async def test_live_local_listener_blocks_restart_until_explicit_stop(owner):
    events=[]
    async def notify(method,args):events.append(method)
    owner.notify=notify
    release=await build(owner)
    preview=await call(owner,'preview',{'releaseId':release['id'],'requestId':'preview'})
    context=dict(fenceId='listener',commandId='update',purpose='distribution-update',instanceId='host',dataScope='scope')
    value=await owner.request('quiescence.acquire',context)
    assert not value['acquired'] and 'listeners' in value['reason']
    assert urlopen(preview['result']['url']).read()==b'<h1>One</h1>'
    await call(owner,'stop',{'siteId':'site','expectedRevision':0,'requestId':'stop'})
    assert events.count('owner/idle')==1
    assert (await owner.request('quiescence.acquire',context))['acquired']


# Startup authority controls use only inert callbacks and tiny owned SQLite files.
import hashlib
import os
import sqlite3
import subprocess
import sys

STARTUP_TABLES=('scopes','builds','approvals','commands','publishing_targets','publishing_target_selection','publishing_target_requests')

async def _startup_forbidden(*args,**kwargs):
    raise AssertionError('Startup fixture must not invoke network, publication or account callbacks')

def _startup_image(root):
    image={}
    for name in ('admission.sqlite3','intake.sqlite3'):
        for suffix in ('','-wal'):
            path=root/(name+suffix)
            if not path.exists() or not path.is_file():continue
            data=path.read_bytes()
            if suffix and not data:continue
            image[name+suffix]=(len(data),hashlib.sha256(data).hexdigest())
    return image

@pytest.mark.parametrize('table',STARTUP_TABLES)
async def test_startup_missing_authority_refuses_before_intake_recovery(tmp_path,table):
    root=tmp_path/'owner';value=Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    sid=value.scope(SID)
    value.db.execute('INSERT INTO commands VALUES(?,?,?,?)',(sid,'original','sig',json.dumps({'state':'unknown'})));value.db.commit()
    fence=dict(fenceId='original',commandId='original',purpose='distribution-update',instanceId='fixture',dataScope='fixture')
    assert (await value.request('quiescence.acquire',fence))['acquired'];await value.close()
    db=sqlite3.connect(root/'admission.sqlite3');db.execute('DROP TABLE '+table);db.commit();db.close()
    before=_startup_image(root)
    for _ in range(2):
        with pytest.raises(ValueError,match='authority schema is incomplete'):
            Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    assert _startup_image(root)==before

@pytest.mark.parametrize('table,column',[
    ('scopes','uri'),('builds','source'),('approvals','body'),('commands','signature'),
    ('publishing_targets','body'),('publishing_target_selection','body'),('publishing_target_requests','signature')])
async def test_startup_missing_columns_refuse_read_only(tmp_path,table,column):
    root=tmp_path/'owner';value=Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden);await value.close()
    db=sqlite3.connect(root/'admission.sqlite3');db.execute('ALTER TABLE '+table+' RENAME COLUMN '+column+' TO lost_column');db.commit();db.close()
    before=_startup_image(root)
    with pytest.raises(ValueError,match='authority schema is incomplete'):
        Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    assert _startup_image(root)==before

@pytest.mark.parametrize('suffix',('','-wal','-shm','-journal'))
@pytest.mark.parametrize('dangling',(False,True))
async def test_startup_orphan_and_dangling_paths_never_initialize(tmp_path,suffix,dangling):
    root=tmp_path/'owner';root.mkdir();main=root/'admission.sqlite3';path=Path(str(main)+suffix);target=root/'missing-target'
    if dangling:path.symlink_to(target)
    else:path.write_bytes(b'')
    (root/'sentinel').write_text('preserve')
    for _ in range(2):
        with pytest.raises((ValueError,sqlite3.Error)):
            Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    assert not os.path.lexists(root/'intake.sqlite3')
    if suffix:assert not os.path.lexists(main)
    if dangling:assert path.is_symlink() and path.readlink()==target and not os.path.lexists(target)
    else:assert path.read_bytes()==b''
    assert (root/'sentinel').read_text()=='preserve'

async def test_startup_interrupted_wal_and_orphan_preserve_authority(tmp_path):
    root=tmp_path/'owner';root.mkdir()
    code="""
import asyncio,json,os,signal,sqlite3,sys
from pathlib import Path
from amplifier_unified_publishing.owner import Owner
async def forbidden(*args):raise AssertionError('no effects')
async def main():
 root=Path(sys.argv[1]);owner=Owner({'dataDir':str(root)},forbidden,forbidden)
 sid=owner.scope('ahp-session:/crash-fixture')
 owner.db.execute('INSERT INTO commands VALUES(?,?,?,?)',(sid,'original','sig',json.dumps({'state':'running'})));owner.db.commit()
 db=sqlite3.connect(root/'admission.sqlite3');db.execute('DROP TABLE commands');db.commit()
 os.kill(os.getpid(),signal.SIGKILL)
asyncio.run(main())
"""
    child=subprocess.run([sys.executable,'-B','-c',code,str(root)],capture_output=True,text=True,timeout=15)
    assert child.returncode==-9,child.stderr
    assert (root/'admission.sqlite3-wal').stat().st_size>0
    before=_startup_image(root)
    for _ in range(2):
        with pytest.raises(ValueError,match='authority schema is incomplete'):
            Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    assert _startup_image(root)==before
    (root/'admission.sqlite3').unlink();orphan=_startup_image(root)
    with pytest.raises(ValueError,match='main database is missing'):
        Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    assert not os.path.lexists(root/'admission.sqlite3') and _startup_image(root)==orphan

async def test_startup_healthy_original_profile_keeps_local_fallback_and_legacy_request(tmp_path):
    root=tmp_path/'owner';value=Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    sid=value.scope(SID)
    assert value.targets._selection(sid)['targetId']=='loopback'
    # Historical admissions lack newer RPC digest fields; startup does not invent them.
    legacy={'sessionId':sid,'requestId':'old','targetId':'loopback','state':'running','operation':'build'}
    value.db.execute('INSERT INTO publishing_target_requests VALUES(?,?,?,?)',(sid,'old','sig',json.dumps(legacy)))
    value.db.execute('INSERT INTO commands VALUES(?,?,?,?)',(sid,'old-command','sig',json.dumps({'state':'running'})));value.db.commit();await value.close()
    value=Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden)
    try:
        assert value.targets._selection(sid)['targetId']=='loopback'
        assert value.command_receipt(sid,'old-command')['state']=='unknown'
        request=value.targets.lookup_request(sid,'old')
        assert request['state']=='unknown' and 'rpcPayloadDigest' not in request
        assert value.scope(SID,create=False)==sid
    finally:await value.close()


@pytest.mark.parametrize('suffix',('','-wal','-shm','-journal'))
async def test_startup_fifo_refuses_actual_owner_without_blocking(tmp_path,suffix):
    root=tmp_path/'owner';value=Owner({'dataDir':str(root)},_startup_forbidden,_startup_forbidden);await value.close()
    path=root/('admission.sqlite3'+suffix)
    if path.exists():path.unlink()
    os.mkfifo(path)
    before=_startup_image(root) if suffix else None
    code="""
import sys
from amplifier_unified_publishing.owner import Owner
async def forbidden(*args):raise AssertionError('no effects')
try:Owner({'dataDir':sys.argv[1]},forbidden,forbidden)
except ValueError as error:
 assert 'regular file' in str(error),str(error)
else:raise AssertionError('FIFO was admitted')
"""
    child=subprocess.run([sys.executable,'-B','-c',code,str(root)],capture_output=True,text=True,timeout=3)
    assert child.returncode==0,child.stderr
    # Metadata-only assertions must never read the FIFO to obtain a hash.
    import stat
    assert stat.S_ISFIFO(path.lstat().st_mode)
    if before is not None:assert _startup_image(root)==before
