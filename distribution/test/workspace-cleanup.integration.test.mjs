import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,realpath,access,symlink,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join} from 'node:path';import {randomUUID} from 'node:crypto';import {execFileSync} from 'node:child_process';import {once} from 'node:events';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const python=process.env.UNIFIED_OWNERS_PYTHON;
for(const restart of [false,true])test(`installed workspace cleanup preserves native histories${restart?' across a replacement process':''}`,{skip:!python,timeout:120000},async()=>{
const root=await realpath(await mkdtemp(join(tmpdir(),'parity-cleanup-installed-'))),workspace=join(root,'default'),target=join(root,'reviewed'),home=join(root,'home'),state=join(root,'state'),web=join(root,'web'),catalog=join(root,'catalog.sqlite');
for(const p of [workspace,target,home,web])await mkdir(p,{recursive:true});
await writeFile(join(web,'index.html'),'<html><head></head><body>Cleanup qualification</body></html>');
await writeFile(join(target,'uncommitted.txt'),'Only this reviewed work is removed');await writeFile(join(root,'outside.txt'),'Keep outside link target');await symlink(join(root,'outside.txt'),join(target,'link'));
const rootUri='ahp-session:/'+randomUUID(),childUri='ahp-session:/'+randomUUID();
const identities=[{uri:rootUri,nativeSessionId:randomUUID(),kind:'root'},{uri:childUri,nativeSessionId:randomUUID(),kind:'child',parentUri:rootUri},{nativeSessionId:randomUUID(),kind:'unindexed'}];
const seeded=JSON.parse(execFileSync(python,['-I','-B','-c',[
'import json,sys',
'from pathlib import Path',
'from amplifier_acp.native.session_files import project_slug',
'from amplifier_session_catalog import Catalog',
'c=json.load(sys.stdin);catalog=Catalog(c["catalog"]);files=[]',
'for row in c["identities"]:',
' p=Path(c["home"])/"projects"/project_slug(c["target"])/"sessions"/row["nativeSessionId"];p.mkdir(parents=True)',
' (p/"metadata.json").write_text(json.dumps({"session_id":row["nativeSessionId"],"cwd":c["target"]}))',
' (p/"transcript.jsonl").write_text(json.dumps({"role":"user","content":"Retain canonical history"})+"\\n")',
' files += [str(p/"metadata.json"),str(p/"transcript.jsonl")]',
' if row.get("uri"): catalog.upsert({**row,"engineId":"native","workingDirectory":c["target"],"storagePath":str(p),"title":"Retained cleanup conversation","createdAt":"2026-10-01T00:00:00Z","modifiedAt":"2026-10-01T00:00:00Z"})',
'print(json.dumps(files))'
].join('\n')],{input:JSON.stringify({home,target,catalog,identities}),encoding:'utf8'}));
const originals=await Promise.all(seeded.map(p=>readFile(p,'utf8')));
const native=join(root,'native.json');await writeFile(native,JSON.stringify({home,appHome:join(root,'native-app'),bundle:'fixture',workerCommand:['/must-not-start-cleanup-worker'],adminWorkspaceRoots:[root],adminMaintenance:true,maintenanceExternalWriters:'stopped',transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[root]}));
let app,socket;
try{
 const launch=async(instanceId='cleanup-original')=>createDistribution({account:'cleanup-qualification',stateDirectory:state,defaultWorkspace:workspace,allowedWorkspaceRoots:[root],webDirectory:web,engines:[{id:'native',command:python,args:['-I','-B','-m','amplifier_acp','--config',native]}],nativeAdmin:{engine:'native'},portability:{python,engines:['native'],stageDir:join(root,'stages'),exchangeDir:join(root,'exchange')},recovery:{},quiescence:{instanceId,dataScope:'cleanup-private',timeoutMs:30000},operations:{python},notifications:{python},coordination:{python},recall:{python},publishing:{python},worktrees:{python},feedback:{python},workspaces:{python},mcp:{python},media:{python},catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',catalog,'--home',home,'--app-home',join(root,'native-app'),'--engine','native','--scan-interval','0','--workspace-check-interval','0']}},{authorizeRecovery:async c=>({accountId:c.account})});
 app=await launch();
 const connect=async()=>{
 socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let seq=0;const pending=new Map();
 socket.on('message',bytes=>{const r=JSON.parse(bytes),p=pending.get(r.id);if(p){pending.delete(r.id);clearTimeout(p.timer);r.error?p.reject(Error(JSON.stringify(r.error))):p.resolve(r.result);}});
 const request=(method,params)=>new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>reject(Error('Timed out '+method)),30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 await request('initialize',{channel:'ahp-root://',clientId:'cleanup-qualification',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
 return request;};let request=await connect();
 const call=async(topic,operation,args={},commandId=randomUUID())=>(await request('x-amplifier/capabilityAction',{version:1,topic,channel:'ahp-root://',operation,args,commandId})).result;
 const row=(await call('workspaces','workspace.add',{path:target,name:'Reviewed workspace'})).workspace;
 const settle=async id=>{for(let n=0;n<1200;n++){const j=await call('recovery','recovery.job',{jobId:id});if(['prepared','succeeded','refused','unknown'].includes(j.state))return j;await new Promise(r=>setTimeout(r,25));}throw Error('Cleanup did not settle');};
  if(!restart){
   const snapshots=[];
   for(let n=0;n<2;n++){
    const review=await settle((await call('recovery','recovery.prepare',{sessions:[rootUri],parts:['session-history'],privateContentReviewed:true})).id);
    assert.equal(review.state,'prepared',JSON.stringify(review));
    const snapshot=await settle((await call('recovery','recovery.snapshot',{preparedJobId:review.id,previewHash:review.previewHash})).id);
    assert.equal(snapshot.state,'succeeded',JSON.stringify(snapshot));snapshots.push(snapshot.result);
   }
   const backups=await settle((await call('recovery','recovery.backups.inspect')).id);
   assert.equal(backups.state,'succeeded',JSON.stringify(backups));assert.equal(backups.result.items.length,2);assert.equal(backups.result.items[0].removable,false);
   const review=await settle((await call('recovery','recovery.backups.prepare',{artifactId:snapshots[0].artifactId,reviewed:true})).id);assert.equal(review.state,'prepared',JSON.stringify(review));
   const removed=await settle((await call('recovery','recovery.backups.remove',{preparedJobId:review.id,previewHash:review.previewHash,confirmationId:snapshots[0].artifactId})).id);
   assert.equal(removed.state,'succeeded',JSON.stringify(removed));assert.equal(removed.result.canonicalFilesChanged,0);
   await assert.rejects(access(join(root,'native-app/maintenance/artifacts',snapshots[0].artifactId)));
   await access(join(root,'native-app/maintenance/artifacts',snapshots[1].artifactId,'snapshot.tar'));
   assert.deepEqual(await Promise.all(seeded.map(p=>readFile(p,'utf8'))),originals);assert.equal(app.host.inspectQuiescence().intakeClosed,false);
  }
  const prepared=await settle((await call('recovery','recovery.workspace.prepare',{workspaceId:row.id,reviewed:true})).id);assert.equal(prepared.state,'prepared',JSON.stringify(prepared));
  const preview=await call('recovery','recovery.preview',{jobId:prepared.id});await writeFile(join(root,'preview.json'),JSON.stringify(preview,null,2));assert.equal(app.host.inspectQuiescence().intakeClosed,false);
  if(restart)app.host.archiveWorkspaceCleanup=async()=>{throw Error('Fixture: lost process before metadata archival');};
  let removed=await settle((await call('recovery','recovery.workspace.remove',{preparedJobId:prepared.id,previewHash:prepared.previewHash,confirmationPath:target})).id);
  if(restart){
   assert.equal(removed.state,'unknown',JSON.stringify(removed));await assert.rejects(access(target));
   socket.terminate();await app.close();app=await launch('cleanup-replacement');request=await connect();
   assert.equal(app.host.inspectQuiescence().intakeClosed,true);
   removed=await call('recovery','recovery.workspace.finishArchival',{jobId:removed.id});
  }
  assert.equal(removed.state,'succeeded',JSON.stringify(removed));await assert.rejects(access(target));
  assert.deepEqual(await Promise.all(seeded.map(p=>readFile(p,'utf8'))),originals);assert.equal(await readFile(join(root,'outside.txt'),'utf8'),'Keep outside link target');
  assert.equal(app.host.inspectQuiescence().intakeClosed,false);assert.equal(app.host.diagnostics().activeAgents,0);
  const archived=JSON.parse(execFileSync(python,['-I','-B','-c','import sys,json;from amplifier_session_catalog import Catalog;c=Catalog(sys.argv[1]);print(json.dumps([c.get(sys.argv[2]),c.get(sys.argv[3])]))',catalog,rootUri,childUri],{encoding:'utf8'}));
  assert.equal(archived[0].isArchived,true);assert.notEqual(archived[1].isArchived,true);
 }finally{socket?.terminate();await app?.close();await rm(root,{recursive:true,force:true});}
});
