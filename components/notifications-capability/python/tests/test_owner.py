import asyncio
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import pytest
from amplifier_unified_notifications.owner import Owner,post_ntfy

FENCE={'fenceId':'fence-one','commandId':'update-one','purpose':'distribution-update','instanceId':'before','dataScope':'owned'}
PROOF={'verified':True,**FENCE,'outcome':'unchanged','receiptId':'no-change-confirmed'}

def config(tmp_path):return {'stateDirectory':str(tmp_path/'state')}
async def action(owner,operation,args={},command='settings-one'):
    return await owner.request('action',{'operation':'notifications.'+operation,'args':args,'commandId':command})
async def enable(owner,**patch):
    current=await owner.request('snapshot',{})
    return await action(owner,'save',{'expectedRevision':current['revision'],'patch':{'enabled':True,'topic':'PRIVATE_TOPIC','token':'PRIVATE_TOKEN',**patch}},'enable-'+str(current['revision']))
def event(i='one',**overrides):return {'session':'ahp-session:/selected','eventId':i,'kind':'response','title':'Selected title','text':'PRIVATE_COMPLETION',**overrides}
async def settle(owner):
    await asyncio.gather(*list(owner.tasks))

async def test_disabled_never_reads_credentials_and_public_settings_cas_redact(tmp_path):
    calls=[]
    async def deliver(*args):calls.append(args);return 200
    owner=Owner(config(tmp_path),transport=deliver)
    try:
        original=owner.private
        owner.private=lambda: (_ for _ in ()).throw(AssertionError('Credentials must remain cold'))
        assert (await action(owner,'get'))=={'revision':0,'enabled':False,'server':'https://ntfy.sh','preview':False,'topicConfigured':False,'tokenConfigured':False}
        disabled=await owner.request('completion',event());assert disabled['status']=='disabled' and disabled['executed'] is False
        assert not owner.tasks and calls==[]
        owner.private=original
        saved=await enable(owner,server='https://notifications.example.org/private/base')
        assert saved['status']=='completed' and saved['revision']==1
        repeated=await action(owner,'save',{'expectedRevision':0,'patch':{'enabled':True,'topic':'PRIVATE_TOPIC','token':'PRIVATE_TOKEN','server':'https://notifications.example.org/private/base'}},'enable-0')
        assert repeated['replayed'] is False and repeated['revision']==1
        stale=await action(owner,'save',{'expectedRevision':0,'patch':{'preview':True}},'stale')
        assert stale['status']=='rejected' and stale['executed'] is False
        saved=await action(owner,'save',{'expectedRevision':1,'patch':{'topic':'','token':'','desktop':False}},'blank')
        assert saved['revision']==2 and owner.private()[1]['topic']=='PRIVATE_TOPIC'
        assert 'desktop' not in owner.public()
        assert 'PRIVATE_' not in json.dumps([await action(owner,'get'),await action(owner,'receipt',{'commandId':'enable-0'})])
        with pytest.raises(ValueError,match='identity changed'):await action(owner,'save',{'expectedRevision':2,'patch':{'enabled':False}},'enable-0')
        assert (await action(owner,'save',{'expectedRevision':2,'patch':{'enabled':False,'clearToken':True,'clearTopic':True}},'clear'))['status']=='completed'
        assert not owner.public()['tokenConfigured'] and not owner.public()['topicConfigured']
    finally:await owner.close()

@pytest.mark.parametrize('server',['http://ntfy.sh','https://user:secret@ntfy.sh','https://ntfy.sh?a=b','https://ntfy.sh/#secret','https://ntfy.sh:bad','https://'])
async def test_invalid_endpoint_known_refusal_no_send(tmp_path,server):
    owner=Owner(config(tmp_path),transport=lambda *args:pytest.fail('Save cannot send'))
    try:
        result=await enable(owner,server=server)
        assert result['status']=='rejected' and result['executed'] is False and owner.public()['revision']==0
        assert 'secret' not in json.dumps(result)
    finally:await owner.close()

async def test_one_delivery_preview_and_http_boundary_no_response_reflection(tmp_path):
    calls=[]
    async def transport(value,title,message):calls.append((value,title,message));return 202
    owner=Owner(config(tmp_path),transport=transport)
    try:
        await enable(owner)
        accepted=await owner.request('completion',event());assert accepted['status']=='accepted' and 'executed' not in accepted
        await settle(owner)
        receipt=await action(owner,'receipt',{'commandId':accepted['commandId']})
        assert receipt['status']=='server-accepted' and receipt['deviceDelivery']=='unverified'
        assert calls[0][2]=='Your Amplifier response is ready.' and calls[0][0]['token']=='PRIVATE_TOKEN'
        assert (await owner.request('completion',event()))['replayed'] is False and len(calls)==1
        with pytest.raises(ValueError,match='identity changed'):await owner.request('completion',event(text='different'))
        await action(owner,'save',{'expectedRevision':1,'patch':{'preview':True}},'preview')
        second=await owner.request('completion',event('two',text='z'*3000));await settle(owner)
        assert len(calls[-1][2])==1000 and len(calls)==2
        assert 'PRIVATE_' not in json.dumps(owner.exact(second['commandId']))
        assert owner.db.execute('SELECT value FROM settings').fetchone()[0].find('PRIVATE_')==-1
        assert owner.db.execute('SELECT receipt FROM commands WHERE id=?',('enable-0',)).fetchone()[0].find('PRIVATE_')==-1
    finally:await owner.close()

@pytest.mark.parametrize('result',[401,302,'unknown'])
async def test_rejected_and_lost_delivery_never_replay_across_restart(tmp_path,result):
    calls=[]
    async def transport(*args):
        calls.append(1)
        if result=='unknown':raise ConnectionResetError('PRIVATE_TOKEN untrusted body')
        return result
    cfg=config(tmp_path);owner=Owner(cfg,transport=transport)
    await enable(owner);accepted=await owner.request('completion',event());await settle(owner)
    before=owner.exact(accepted['commandId']);assert before['status']==('unknown' if result=='unknown' else 'rejected')
    assert 'PRIVATE' not in json.dumps(before)
    await owner.close();owner=Owner(cfg,transport=transport)
    try:
        repeated=await owner.request('completion',event());assert repeated['status']==before['status'] and repeated['replayed'] is False
        assert len(calls)==1
    finally:await owner.close()

async def test_bounded_background_delivery_held_fence_restart_and_exact_release(tmp_path):
    release=asyncio.Event();entered=0;idle=[]
    async def transport(*args):
        nonlocal entered;entered+=1;await release.wait();return 200
    async def on_idle():idle.append(1)
    cfg=config(tmp_path);owner=Owner(cfg,transport=transport,idle=on_idle);await enable(owner)
    try:
        for i in range(32):assert (await owner.request('completion',event(str(i))))['status']=='accepted'
        await asyncio.sleep(0)
        assert entered==4 and owner.intake.background==32
        refused=await owner.request('completion',event('over'));assert refused['accepted'] is False and refused['executed'] is False
        assert (await owner.request('quiescence/acquire',FENCE))['acquired'] is False
        release.set();await settle(owner);assert owner.intake.background==0 and len(idle)>=32
        assert (await owner.request('quiescence/acquire',FENCE))['intakeClosed'] is True
        assert (await owner.request('completion',event('held')))['executed'] is False
        assert (await action(owner,'get'))['enabled'] is True
    finally:await owner.close()
    owner=Owner(cfg,transport=transport)
    try:
        assert (await owner.request('quiescence/inspect',{}))['intakeClosed'] is True
        with pytest.raises(ValueError):await owner.request('quiescence/release',{**FENCE,'outcome':'ready','proof':PROOF})
        assert not (await owner.request('quiescence/release',{**FENCE,'outcome':'unknown'}))['released']
        assert (await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':PROOF}))['released'] is True
    finally:await owner.close()
    owner=Owner(cfg,transport=transport)
    try:
        assert (await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':PROOF}))['released'] is True
        with pytest.raises(ValueError,match='exact retained'):await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':{**PROOF,'receiptId':'different'}})
    finally:await owner.close()

async def test_actual_process_sigkill_delivery_unknown_no_replay_and_os_exclusive(tmp_path):
    cfg=config(tmp_path);owner=Owner(cfg);await enable(owner);await owner.close()
    entered=tmp_path/'entered'
    script='''import asyncio,json,sys
from pathlib import Path
from amplifier_unified_notifications.owner import Owner
async def transport(*args):
 Path(sys.argv[2]).write_text('POST has begun')
 await asyncio.Event().wait()
async def main():
 owner=Owner(json.loads(sys.argv[1]),transport=transport)
 await owner.request('completion',json.loads(sys.argv[3]))
 await asyncio.Event().wait()
asyncio.run(main())'''
    child=subprocess.Popen([sys.executable,'-I','-c',script,json.dumps(cfg),str(entered),json.dumps(event())],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            if entered.exists():break
            assert child.poll() is None
            await asyncio.sleep(.01)
        assert entered.exists()
        with pytest.raises(sqlite3.OperationalError,match='locked'):Owner(cfg)
        child.send_signal(signal.SIGKILL);await asyncio.to_thread(child.wait,5)
        async def never(*args):pytest.fail('Unknown delivery must not replay')
        owner=Owner(cfg,transport=never)
        try:
            receipt=await owner.request('completion',event());assert receipt['status']=='unknown' and receipt['replayed'] is False
            assert not owner.tasks
        finally:await owner.close()
    finally:
        if child.poll() is None:child.kill();child.wait()
        child.stdout.close();child.stderr.close()

async def test_explicit_legacy_import_preserves_source_and_no_delivery(tmp_path):
    legacy=tmp_path/'notifications.json';text=json.dumps({'desktop':False,'push':True,'server':'https://ntfy.example.test/path','topic':'OLD_PRIVATE_TOPIC','token':'OLD_PRIVATE_TOKEN','preview':True});legacy.write_text(text)
    cfg={**config(tmp_path),'legacySettingsPath':str(legacy)};owner=Owner(cfg,transport=lambda *args:pytest.fail('Import cannot send'))
    assert owner.public()['enabled'] and owner.public()['preview'] and 'desktop' not in owner.public()
    assert owner.private()[1]['token']=='OLD_PRIVATE_TOKEN' and not owner.tasks
    await owner.close();legacy.write_text('{}');owner=Owner(cfg)
    try:assert owner.public()['enabled'] and legacy.read_text()=='{}'
    finally:await owner.close()

async def test_actual_http_transport_fixed_shape_no_redirect_or_body_read(monkeypatch):
    calls=[]
    class Response:
        status=307
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def text(self):pytest.fail('Untrusted response must not be read')
    class Client:
        def __init__(self,**args):assert args['timeout'].total==15
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def post(self,*args,**kwargs):calls.append((args,kwargs));return Response()
    monkeypatch.setattr('amplifier_unified_notifications.owner.aiohttp.ClientSession',Client)
    assert await post_ntfy({'server':'https://self.host/ntfy/','topic':'private','token':'secret'},'title','body')==307
    assert calls==[(('https://self.host/ntfy',),{'headers':{'Authorization':'Bearer secret'},'json':{'topic':'private','title':'title','message':'body'},'allow_redirects':False})]

async def test_owned_local_https_fixture_fixed_post_and_redirect_refusal(tmp_path,monkeypatch):
    import ssl
    import aiohttp
    from aiohttp import web
    cert=tmp_path/'cert.pem';key=tmp_path/'key.pem'
    result=subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],capture_output=True)
    assert result.returncode==0,result.stderr.decode()
    server_ssl=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_ssl.load_cert_chain(cert,key)
    client_ssl=ssl.create_default_context(cafile=str(cert));captured=[];followed=[]
    async def accept(request):
        captured.append({'method':request.method,'path':request.path,'authorization':request.headers.get('Authorization'),'body':await request.json()})
        if len(captured)==2:return web.Response(status=307,headers={'Location':'/do-not-follow'},text='PRIVATE_SERVER_BODY')
        return web.Response(status=202,text='PRIVATE_SERVER_BODY')
    async def redirect_target(request):followed.append(True);return web.Response(status=200)
    app=web.Application();app.router.add_post('/self-host/ntfy',accept);app.router.add_route('*','/do-not-follow',redirect_target)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0,ssl_context=server_ssl);await site.start();port=site._server.sockets[0].getsockname()[1]
    real_session=aiohttp.ClientSession
    monkeypatch.setattr('amplifier_unified_notifications.owner.aiohttp.ClientSession',lambda **kwargs:real_session(connector=aiohttp.TCPConnector(ssl=client_ssl),**kwargs))
    owner=Owner(config(tmp_path))
    try:
        await enable(owner,server=f'https://127.0.0.1:{port}/self-host/ntfy',preview=True)
        accepted=await owner.request('completion',event());await settle(owner)
        assert owner.exact(accepted['commandId'])['status']=='server-accepted'
        rejected=await owner.request('completion',event('redirect'));await settle(owner)
        receipt=owner.exact(rejected['commandId']);assert receipt['status']=='rejected' and receipt['httpStatus']==307
        assert captured==[{'method':'POST','path':'/self-host/ntfy','authorization':'Bearer PRIVATE_TOKEN','body':{'topic':'PRIVATE_TOPIC','title':'Selected title','message':'PRIVATE_COMPLETION'}}]*2
        assert followed==[] and 'PRIVATE_SERVER_BODY' not in json.dumps(receipt)
    finally:await owner.close();await runner.cleanup()
