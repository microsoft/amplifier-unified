import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from amplifier_unified_diagnostics.owner import Owner,send_ci

FENCE={'fenceId':'diagnostic-fence','commandId':'maintenance-command','purpose':'distribution-update','instanceId':'before','dataScope':'owned'}
PROOF={'verified':True,**FENCE,'outcome':'unchanged','receiptId':'actual-settled-owner'}
def config(path):return {'stateDirectory':str(path/'owner')}
async def call(owner,operation,args=None,command='one'):
    return await owner.request('action',{'operation':'diagnostics.'+operation,'args':args or {},'commandId':command})
async def enable(owner,**patch):
    state=owner.snapshot();settings={**state['config'],'enabled':True,**patch}
    return await call(owner,'configure',{'expectedRevision':state['revision'],'config':settings},'config-'+str(state['revision']))
def destination(**patch):return {'id':'destination','name':'Fixture','url':'https://example.test/ci','enabled':True,'streams':['app'],'workspace':'label','workspacePattern':'*','includePaths':False,'authMode':'static','apiKeyEnv':'FIXTURE_DIAGNOSTIC_TOKEN','authResource':'',**patch}
def event(identity='event-one',**patch):return {'id':identity,'event':'app:action','stream':'app','session':'ahp-session:/one','workspace':'/owned/workspace','data':{'status':'completed','prompt':'DO_NOT_CAPTURE','apiKey':'DO_NOT_CAPTURE'},**patch}
async def record(owner,*items):return await owner.request('record',{'items':list(items)})
async def settle(owner):
    while owner.tasks:await asyncio.gather(*list(owner.tasks),return_exceptions=True);await asyncio.sleep(0)

async def test_disabled_is_cold_and_explicit_metadata_and_content_policy(tmp_path,monkeypatch):
    calls=[]
    async def transport(*args):calls.append(args);return {'status':'queued'}
    owner=Owner(config(tmp_path),transport=transport)
    try:
        assert (await record(owner,event()))['disabled'] is True
        assert owner.snapshot()['local']['records']==0 and calls==[]
        await enable(owner)
        assert (await record(owner,event()))['recorded']==1
        data=(await call(owner,'records'))['items'][0]['data']
        assert data['status']=='completed' and 'prompt' not in data and 'apiKey' not in data
        assert 'DO_NOT_CAPTURE' not in json.dumps(await call(owner,'records'))
        assert (await record(owner,event('conversation',stream='conversation',data={'text':'NOT_OPTED_IN'})))['recorded']==0
        await enable(owner,streams=['app','conversation'])
        monkeypatch.setenv('ANOTHER_API_KEY','FIXTURE_PRIVATE_SECRET_VALUE')
        await record(owner,event('conversation',stream='conversation',data={'text':'consented FIXTURE_PRIVATE_SECRET_VALUE','password':'secret'}))
        result=await call(owner,'records',{'stream':'conversation'})
        assert result['items'][0]['data']['text']=='consented [REDACTED]'
        assert 'secret' not in json.dumps(result)
        assert owner.snapshot()['capture']['historicalScan'] is False
    finally:await owner.close()

async def test_cas_receipt_duplicate_command_and_exclusive_process_owner(tmp_path):
    cfg=config(tmp_path);owner=Owner(cfg)
    state=owner.snapshot();args={'expectedRevision':0,'config':{**state['config'],'enabled':True}}
    try:
        saved=await call(owner,'configure',args,'save');assert saved['revision']==1
        assert await call(owner,'configure',args,'save')==saved
        assert (await call(owner,'configure',args,'stale'))['executed'] is False
        with pytest.raises(ValueError,match='identity changed'):await call(owner,'configure',{**args,'expectedRevision':1},'save')
        with pytest.raises(sqlite3.OperationalError):Owner(cfg)
    finally:await owner.close()
    owner=Owner(cfg)
    try:assert await call(owner,'receipt',{'commandId':'save'})==saved
    finally:await owner.close()

async def test_scoped_keyset_pages_precede_limits_and_retention_is_bounded(tmp_path):
    owner=Owner(config(tmp_path))
    try:
        await enable(owner,maxRecords=100)
        for n in range(135):await record(owner,event(str(n),session='ahp-session:/even' if n%2==0 else 'ahp-session:/odd'))
        assert owner.snapshot()['local']['records']==100
        ids=[];before=None
        while True:
            page=await call(owner,'records',{'sessionId':'ahp-session:/even','stream':'app','limit':7,**({'before':before} if before else {})})
            assert len(page['items'])<=7
            ids.extend(row['id'] for row in page['items']);before=page['nextBefore']
            if before is None:break
        assert len(ids)==50 and len(set(ids))==50 and all(int(i)%2==0 for i in ids)
        plans=owner.db.execute('EXPLAIN QUERY PLAN SELECT seq FROM records WHERE session=? AND stream=? AND seq<? ORDER BY seq DESC LIMIT 8',('x','app',100)).fetchall()
        assert any('record_scope' in row[3] for row in plans)
        for bad in ({'limit':51},{'stream':'*?'},{'before':-1}):
            with pytest.raises(ValueError):await call(owner,'records',bad)
    finally:await owner.close()

async def test_route_save_does_not_backfill_and_unknown_never_retries(tmp_path):
    calls=[]
    async def transport(dest,payload,probe=False):calls.append(copy.deepcopy(payload));raise ConnectionResetError('PRIVATE_URL_AND_TOKEN')
    owner=Owner(config(tmp_path),transport=transport)
    try:
        await enable(owner);await record(owner,event('before-route'))
        await enable(owner,destinations=[destination()]);await settle(owner);assert calls==[]
        await record(owner,event('routed'));await settle(owner)
        assert len(calls)==1 and 'working_dir' not in calls[0]
        assert owner.snapshot()['destinations'][0]['counts']=={'unknown':1}
        assert 'PRIVATE_' not in json.dumps(owner.snapshot())
        retried=await call(owner,'retry',{'id':'destination'},'retry');await settle(owner)
        assert retried['queued']==0 and len(calls)==1
        await record(owner,event('routed'));await settle(owner);assert len(calls)==1
    finally:await owner.close()
    owner=Owner(config(tmp_path),transport=transport)
    try:await owner.request('initialize',{});await settle(owner);assert len(calls)==1
    finally:await owner.close()

async def test_saved_destination_test_has_exact_receipt_and_no_response_reflection(tmp_path):
    calls=[]
    async def transport(dest,payload,probe=False):calls.append((payload,probe));return {'status':'queued','arbitrary':'PRIVATE_RESPONSE'}
    owner=Owner(config(tmp_path),transport=transport)
    try:
        await enable(owner,destinations=[destination(enabled=False)])
        result=await call(owner,'test',{'id':'destination'},'probe');assert result['status']=='completed'
        assert calls[0][1] is True and calls[0][0]['data']['synthetic'] is True
        assert 'PRIVATE_' not in json.dumps(result)
        assert await call(owner,'test',{'id':'destination'},'probe')==result and len(calls)==1
        assert await call(owner,'receipt',{'commandId':'probe'})==result
    finally:await owner.close()

async def test_held_delivery_lifetime_pauses_unsent_outbox_and_exact_release(tmp_path):
    entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def transport(*args):calls.append(1);entered.set();await release.wait();return {'status':'queued'}
    owner=Owner(config(tmp_path),transport=transport)
    try:
        await enable(owner,destinations=[destination()])
        await record(owner,*[event(str(n)) for n in range(8)]);await entered.wait()
        assert owner.intake.background==4
        acquiring=asyncio.create_task(owner.request('quiescence/acquire',FENCE));await asyncio.sleep(0)
        assert not acquiring.done()
        assert (await record(owner,event('held')))['executed'] is False
        release.set();assert (await acquiring)['acquired'] is True
        assert len(calls)==4 and owner.snapshot()['destinations'][0]['counts']=={'accepted':4,'pending':4}
        assert (await call(owner,'get'))['local']['records']==8
        assert (await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':PROOF}))['released'] is True
        await settle(owner);assert len(calls)==8
    finally:await owner.close()
    owner=Owner(config(tmp_path))
    try:
        assert (await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':PROOF}))['released'] is True
        with pytest.raises(ValueError):await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':{**PROOF,'receiptId':'changed'}})
    finally:await owner.close()

async def test_pending_revocation_and_environment_never_disclose_value(tmp_path,monkeypatch):
    owner=Owner(config(tmp_path));monkeypatch.setenv('FIXTURE_DIAGNOSTIC_TOKEN','PRIVATE_FIXTURE_VALUE')
    try:
        await enable(owner,destinations=[destination()]);owner.pausing=True
        await record(owner,event('blocked'));assert owner.snapshot()['local']['records']==0
        owner.pausing=False;owner.record([event('queued')])
        await enable(owner,destinations=[destination(url='https://different.example/ci')])
        assert owner.snapshot()['destinations'][0]['counts']=={'cancelled':1}
        result=await call(owner,'environment',{'name':'FIXTURE_DIAGNOSTIC_TOKEN'})
        assert result=={'name':'FIXTURE_DIAGNOSTIC_TOKEN','available':True}
        assert 'PRIVATE_FIXTURE_VALUE' not in json.dumps(owner.snapshot())
    finally:await owner.close()

async def test_actual_sigkill_probe_is_unknown_after_restart_without_replay(tmp_path):
    cfg=config(tmp_path);owner=Owner(cfg);await enable(owner,destinations=[destination(enabled=False)]);await owner.close()
    entered=tmp_path/'entered'
    code='''import asyncio,json,sys
from pathlib import Path
from amplifier_unified_diagnostics.owner import Owner
async def transport(*args):
 Path(sys.argv[2]).write_text('probe admitted')
 await asyncio.Event().wait()
async def main():
 owner=Owner(json.loads(sys.argv[1]),transport=transport)
 await owner.request('action',{'operation':'diagnostics.test','args':{'id':'destination'},'commandId':'interrupted'})
asyncio.run(main())'''
    process=subprocess.Popen([sys.executable,'-I','-c',code,json.dumps(cfg),str(entered)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(500):
            if entered.exists():break
            await asyncio.sleep(.01)
        assert entered.exists();process.kill();process.wait(timeout=5)
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
    async def forbidden(*args):pytest.fail('Uncertain probe must not be sent again')
    owner=Owner(cfg,transport=forbidden)
    try:
        result=await call(owner,'test',{'id':'destination'},'interrupted');assert result['status']=='unknown'
        assert await call(owner,'receipt',{'commandId':'interrupted'})==result
    finally:await owner.close()

async def test_actual_sdk_http_acceptance_and_redirect_refusal(tmp_path,monkeypatch):
    requests=[];redirect=False
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"contributor_id":"owned-fixture"}')
        def do_POST(self):
            requests.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(302 if redirect else 202);self.send_header('Content-Type','application/json')
            if redirect:self.send_header('Location','http://127.0.0.1:1/forbidden')
            self.end_headers();self.wfile.write(b'{"status":"queued"}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('FIXTURE_DIAGNOSTIC_TOKEN','owned-test-token')
    owner=Owner(config(tmp_path))
    try:
        await enable(owner,destinations=[destination(url='http://127.0.0.1:'+str(server.server_port))])
        result=await call(owner,'test',{'id':'destination'},'actual-probe');assert result['status']=='completed'
        assert requests[0][0]=='/events' and requests[0][1]=='Bearer owned-test-token'
        assert requests[0][2]['idempotency_key'].startswith('aci-event-v1:')
        redirect=True;result=await call(owner,'test',{'id':'destination'},'redirect');assert result['status']=='unknown' and len(requests)==2
    finally:await owner.close();server.shutdown();server.server_close();thread.join()
