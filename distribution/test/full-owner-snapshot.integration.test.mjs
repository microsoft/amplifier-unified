/** Actual public composition. Synthetic private records, no account/device work. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,realpath,access} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {createMediaCapability} from '@amplifier/unified-media-capability';
import {createDistribution} from '../src/index.js';
import {createManualIngressGate,connectSupervisorFileLazy} from '@amplifier/unified-distribution-update-owner';
import {artifactFixture} from './fixtures/terminal-artifact.mjs';

const python=process.env.FULL_RECOVERY_PYTHON,operationsPython=process.env.OWNER_SNAPSHOT_PYTHON;
const enabled=Boolean(python&&operationsPython),hash=v=>createHash('sha256').update(v).digest('hex');
const owners=['portability','capability:attachments','workspaces','native-admin','native-message-metadata','application-updates','capability:voice','capability:connectors','notifications','diagnostics','capability:observations','capability:coordination','capability:worktrees','capability:publishing','capability:recall','capability:feedback','recovery','history-import','history-cleanup','managed-files','manual-preview-ingress'];

async function fixture(t,{unknownSpool=false,unknownCall=false,hook=false,loseHookReply=false,priorUnknown=false,terminal=false}={}){
 const directory=await realpath(await mkdtemp(join(tmpdir(),'full-recovery-owners-'))),state=join(directory,'application'),workspace=join(state,'workspace'),home=join(directory,'native-home'),appHome=join(directory,'native-app'),web=join(directory,'web');
 for(const p of [state,workspace,home,appHome,web,join(state,'snapshots')])await mkdir(p,{recursive:true,mode:0o700});
 const sid='01111111-1111-4111-8111-111111111111',session='ahp-session:/'+sid,saved=join(home,'projects',workspace.replaceAll('/','-'),'sessions',sid);
 await mkdir(saved,{recursive:true});
 const canonical={
  'metadata.json':JSON.stringify({session_id:sid,working_dir:workspace,status:'idle',name:'Preserved recovery fixture'}),
  'transcript.jsonl':JSON.stringify({role:'user',content:'Preserved voice text',metadata:{via:'call',message_id:'voice-message'}})+'\n',
  'events.jsonl':'{"preserved":"event authority"}\n',
 };
 for(const [name,body]of Object.entries(canonical))await writeFile(join(saved,name),body);
 await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');await writeFile(join(web,'index.html'),'<!doctype html><title>Recovery fixture</title>');
 const bundle=join(directory,'inert.yaml');await writeFile(bundle,'bundle:\n  name: no-runtime-recovery-fixture\n  version: 1.0.0\nproviders: []\n');
 const nativeFile=join(directory,'native.json'),managed=join(state,'managed');
 await writeFile(nativeFile,JSON.stringify({home,appHome,bundle,workerCommand:['/impossible-full-recovery-worker'],managedSessionRoots:[managed],adminWorkspaceRoots:[workspace],adminMaintenance:true,maintenanceExternalWriters:'stopped',transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[workspace]}));
 const ownerPython=join(directory,'owner-python');await writeFile(ownerPython,'#!/bin/sh\nexec '+"'"+python.replaceAll("'","'\\''")+"'"+' -I -B "$@"\n',{mode:0o700});
 // Synthetic retained records are seeded before the real owner opens them.
 // They cannot cause a call or native transcript replay.
 const media=join(state,'capabilities/media');await mkdir(join(media,'receipts'),{recursive:true});
 const unexpected=()=>{throw Error('Recovery fixture must not start media work');};
 const seedMedia=await createMediaCapability({directory:media,inspectSession:unexpected,delegate:unexpected,recordTranscript:unexpected});
 await seedMedia.close();
 await writeFile(join(media,'receipts','historical.json'),JSON.stringify({fingerprint:'retained',operation:'delegate',outcome:'unknown',at:1}));
 const transcript=new DatabaseSync(join(media,'transcript-intents.sqlite'));
 transcript.prepare('INSERT INTO history VALUES(?,?,?,?)').run(session,'voice-message','2026-10-01',JSON.stringify({id:'voice-message',role:'user',text:'Preserved voice text',via:'call',durability:'native'}));
 if(unknownSpool)transcript.prepare('INSERT INTO intents(session,call,item,role,text,append,command,status,attempted,created) VALUES(?,?,?,?,?,?,?,?,?,?)').run(session,'old-call','item','user','Uncertain retained voice text',0,'original-voice-input','unknown',1,'2026-10-01');
 transcript.close();
 if(unknownCall){const db=new DatabaseSync(join(media,'media-quiescence.sqlite'));db.prepare('INSERT INTO calls VALUES(?,?)').run('old-call','unverified');db.close();}
 const gate=await createManualIngressGate({directory:join(state,'ingress'),id:'manual-preview-ingress'}),supervisor=connectSupervisorFileLazy(join(directory,'absent-supervisor.json'));
 const component=JSON.parse(await readFile(new URL('../components.json',import.meta.url),'utf8')).components['@amplifier/unified-distribution-update-owner'];
 const config={account:'full-recovery-fixture',stateDirectory:state,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,host:{managedSessionRoot:managed},
  engines:[{id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',nativeFile],env:{PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers'),AMPLIFIER_SOURCE_STORE:join(directory,'sources'),XDG_CACHE_HOME:join(directory,'cache')}}],
  nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{authorization:'local-account'},historyImport:{},historyCleanup:true,managedFiles:true,applicationUpdates:true,manualIngress:{stateDirectory:join(state,'ingress')},
  portability:{python:ownerPython,engines:['amplifier'],stageDir:join(workspace,'stages'),exchangeDir:join(workspace,'exchange')},quiescence:{instanceId:'full-recovery-fixture',dataScope:'full-recovery-owned',timeoutMs:30000},
  ...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(key=>[key,{python:key==='operations'?operationsPython:ownerPython,env:{PYTHONDONTWRITEBYTECODE:'1'}}])),
  catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(state,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0'],env:{PYTHONDONTWRITEBYTECODE:'1'}}};
 if(terminal){const feed=await artifactFixture(join(directory,'terminal-feed'));config.gateway={origin:'https://127.0.0.1:24449',port:0};config.terminal={origin:config.gateway.origin,artifacts:[feed.entry]};}
 let app,staged,hookCalls=0;
 t.after(async()=>{await app?.close();supervisor.close();gate.close();for(const [name,body]of Object.entries(canonical))assert.equal(hash(await readFile(join(saved,name))),hash(body));console.error('Retained full-owner fixture:',directory);});
 app=await createDistribution(config,{applicationUpdateSupervisor:supervisor,runtimeOwnerBindings:[{owner:gate.participant,storage:{packageName:'@amplifier/unified-distribution-update-owner',packageVersion:component.version,revision:component.revision,configKey:'manualIngress',rootRole:'ingress',stateDirectory:join(state,'ingress')}}],authorizeRecovery:async c=>({accountId:c.account}),...(hook?{beforeRecoveryMaintenance:async({context,stageOwnerSnapshot})=>{
  hookCalls++;assert.equal(context.purpose,'recovery');assert.equal(app.host.inspectQuiescence().fence.fenceId,context.fenceId);
  assert.deepEqual([...app.host.inspectQuiescence().fence.owners].sort(),[...owners].sort());
  if(priorUnknown){
   const selected=Object.fromEntries(['fenceId','commandId','purpose','instanceId','dataScope'].map(key=>[key,context[key]]));
   const request={ownerId:'capability:observations',context:selected,snapshotCommandId:'recovery-job-stores',privateContentReviewed:true};
   const sort=v=>Array.isArray(v)?v.map(sort):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,sort(v[k])])):v;
   const path=join(state,'snapshots',hash(request.snapshotCommandId));await mkdir(path);
   await writeFile(join(path,'staging.json'),JSON.stringify({schema:'amplifier-unified-owner-snapshot-stage-v1',...request,signature:hash(JSON.stringify(sort(request))),status:'unknown',workReplayed:false,completeProductBackup:false}));
  }
  staged=await stageOwnerSnapshot({ownerId:'capability:observations',snapshotCommandId:'recovery-job-stores',directory:join(state,'snapshots'),privateContentReviewed:true});
  if(loseHookReply)throw Error('Fixture lost staging hook acknowledgement');
 }}:{})});
 assert.deepEqual([...app.quiescence.requiredOwners].sort(),[...owners,...(terminal?['terminal']:[])].sort());
 return {app,directory,state,media,session,config,appHome,staged:()=>staged,hookCalls:()=>hookCalls};
}

test('optional Terminal joins all 22 actual owner holds and private application backup scope',{skip:!enabled,timeout:120000},async t=>{
 const f=await fixture(t,{terminal:true}),call=(operation,args,commandId)=>f.app.host.invokeCapability({channel:'ahp-root://',topic:'terminal',operation,version:1,args,commandId},{actorId:'fixture-operator',clientId:'fixture-reviewer',origin:'ui'});
 const prepared=await call('terminal.prepare',{platform:'linux-arm64',name:'Private fixture Terminal'},'original-terminal');assert.equal(prepared.result.receipt.status,'completed');assert.equal(f.app.host.diagnostics().activeAgents,0);
 const inventory=await f.app.storageInventory();const owner=inventory.owners.find(v=>v.id==='terminal');assert.deepEqual(owner.rootIds,['application']);assert.equal(owner.externalStorage,'none');assert.match(owner.revision,/^sha256:/);
 const admission=await f.app.host.admitQuiescence({commandId:'with-optional-terminal',purpose:'recovery'});assert.equal(admission.admitted,true,JSON.stringify(admission));assert.deepEqual([...f.app.host.inspectQuiescence().fence.owners].sort(),[...owners,'terminal'].sort());
 assert.equal((await call('terminal.receipt',{commandId:'original-terminal'},'passive-receipt')).result.receipt.status,'completed');assert.equal((await call('terminal.devices',{},'passive-devices')).result.items.length,0);await assert.rejects(call('terminal.prepare',{platform:'linux-arm64',name:'No new authority'},'no-admission'),/intake|quiescence|fence/i);
 assert.equal(f.app.host.diagnostics().activeAgents,0);assert.ok(f.app.terminalAccess);assert.equal(inventory.completeEligible,false,'Terminal does not turn partial native/external inventory into complete backup');
});

async function recoveryPrepare(f){
 const call=(operation,args,commandId)=>f.app.host.invokeCapability({channel:'ahp-root://',topic:'recovery',operation,version:1,args,commandId},{actorId:'fixture-operator',clientId:'fixture-reviewer',origin:'ui'});
 const submitted=await call('recovery.appReset.prepare',{parts:['notifications.settings'],privateContentReviewed:true},'real-recovery-job');
 const id=submitted.result.id;
 for(let n=0;n<400;n++){
  const job=(await call('recovery.job',{jobId:id},'read-'+n)).result;
  if(['prepared','succeeded','refused','unknown'].includes(job.state))return {job,call};
  await new Promise(resolve=>setTimeout(resolve,25));
 }
 throw Error('Recovery job did not settle in fixture bound');
}

test('all 21 actual owners hold while Operations snapshot stages; native history stays cold',{skip:!enabled,timeout:120000},async t=>{
 const f=await fixture(t),before=f.app.host.diagnostics();assert.equal(before.activeAgents,0);
 const admission=await f.app.host.admitQuiescence({commandId:'original-recovery',purpose:'recovery'});assert.equal(admission.admitted,true,JSON.stringify(admission));
 const fence=f.app.host.inspectQuiescence().fence;assert.deepEqual([...fence.owners].sort(),[...owners].sort());
 const input={ownerId:'capability:observations',fenceId:admission.fenceId,commandId:'original-recovery',snapshotCommandId:'original-seven-stores',directory:join(f.state,'snapshots'),privateContentReviewed:true};
 const staged=await f.app.stageOwnerSnapshot(input);assert.equal(staged.status,'sealed');assert.equal(staged.completeProductBackup,false);
 assert.equal((await f.app.inspectOwnerSnapshot({ownerId:input.ownerId,commandId:input.snapshotCommandId})).receipt.status,'captured');
 const inventory=await f.app.storageInventory();assert.equal(inventory.owners.length,21);assert.ok(!inventory.omissions.some(o=>o.id.startsWith('provenance:')||o.id.startsWith('runtime-provenance:')));assert.equal(inventory.completeEligible,false,'Native full authority still requires a separately held capture');
 assert.equal(f.app.host.diagnostics().activeAgents,0);assert.equal(f.app.host.inspectQuiescence().intakeClosed,true);
 await writeFile(join(f.directory,'acceptance.json'),JSON.stringify({schema:'full-owner-staging-acceptance-v1',owners:fence.owners,staged,inventory,executionAgents:0,workReplayed:false,limits:['Actual 21 owner composition; controlled fixture shutdown retains original fence.','No stopped service receipt or full product archive claimed by this staging test.','Synthetic media native-confirmed projection and inert historical unknown receipt; no provider/device calls.']},null,2));
});

test('actual media retained unverified provider call blocks all-owner recovery',{skip:!enabled,timeout:120000},async t=>{
 const f=await fixture(t,{unknownCall:true}),r=await f.app.host.admitQuiescence({commandId:'recovery-refused-call',purpose:'recovery'});assert.equal(r.admitted,false);assert.equal(r.executed,false);assert.match(JSON.stringify(r),/voice/);
});

test('actual media retained unknown transcript must block all-owner recovery',{skip:!enabled,timeout:120000},async t=>{
 const f=await fixture(t,{unknownSpool:true}),r=await f.app.host.admitQuiescence({commandId:'recovery-refused-spool',purpose:'recovery'});await writeFile(join(f.directory,'unknown-spool-admission.json'),JSON.stringify(r,null,2));assert.equal(r.admitted,false,'Unknown retained transcript requires explicit owner resolution before capture');assert.equal(r.executed,false);
 const db=new DatabaseSync(join(f.media,'transcript-intents.sqlite'),{readOnly:true});assert.deepEqual({...db.prepare('SELECT status,attempted,command,text FROM intents').get()},{status:'unknown',attempted:1,command:'original-voice-input',text:'Uncertain retained voice text'});assert.equal(db.prepare('SELECT count(*) AS n FROM history').get().n,1);db.close();
});

test('trusted snapshot hook uses genuine Recovery job and exact conclusive release',{skip:!enabled,timeout:120000},async t=>{
 const f=await fixture(t,{hook:true}),{job}=await recoveryPrepare(f);
 assert.equal(job.state,'prepared',JSON.stringify(job));assert.equal(f.staged().status,'sealed');assert.equal(f.hookCalls(),1);
 assert.equal(f.app.host.inspectQuiescence().intakeClosed,false);assert.equal(f.app.host.diagnostics().activeAgents,0);
 await writeFile(join(f.directory,'recovery-job-acceptance.json'),JSON.stringify({job,staged:f.staged(),quiescence:f.app.host.inspectQuiescence()},null,2));
});

test('lost trusted-hook reply leaves original Recovery job unknown and does not redispatch',{skip:!enabled,timeout:120000},async t=>{
 const f=await fixture(t,{hook:true,loseHookReply:true}),{job,call}=await recoveryPrepare(f);
 assert.equal(job.state,'unknown');assert.equal(f.staged().status,'sealed');assert.equal(f.app.host.inspectQuiescence().intakeClosed,true);
 await call('recovery.reconcile',{jobId:job.id},'passive-reconcile');assert.equal(f.hookCalls(),1);assert.equal(f.app.host.inspectQuiescence().intakeClosed,true);
});

test('fulfilled original unknown staging refuses downstream Recovery maintenance without replay',{skip:!enabled,timeout:120000},async t=>{
 const f=await fixture(t,{hook:true,priorUnknown:true}),{job,call}=await recoveryPrepare(f);
 assert.equal(job.state,'unknown');assert.equal(f.staged().status,'unknown');assert.equal(f.app.host.inspectQuiescence().intakeClosed,true);
 assert.equal((await f.app.inspectOwnerSnapshot({ownerId:'capability:observations',commandId:'recovery-job-stores'})).receipt,null,'No capture redispatch from retained root intent');
 await assert.rejects(access(join(f.appHome,'maintenance','receipts.sqlite3')),/ENOENT/,'No downstream native maintenance owner constructed');
 await call('recovery.reconcile',{jobId:job.id},'unknown-passive-reconcile');assert.equal(f.hookCalls(),1);assert.equal(f.app.host.inspectQuiescence().intakeClosed,true);
});
