import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,readFileSync,writeFileSync,existsSync,rmSync,unlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const configured=process.env.RECOVERY_PACKAGE_MODULE;
const module=configured?(configured.startsWith('file:')?configured:pathToFileURL(configured).href):new URL('../dist/index.js',import.meta.url).href;
const {createRecoveryCapabilities,createPresentationCapabilities}=await import(module);
const storeModule=new URL('./store.js',module).href;
const {Store}=await import(storeModule);
const context={fenceId:'fixture-fence',commandId:'fixture-fence-command',purpose:'distribution-update',instanceId:'fixture-instance',dataScope:'fixture-data'};
const next={...context,fenceId:'next-fixture-fence',commandId:'next-fixture-command'};
const command='original-fixture-command';
const job=(presentation=false)=>({id:'fixture-job',accountId:'fixture-account',commandId:command,operation:presentation?'recovery.presentation.apply':'recovery.snapshot',signature:'fixture-signature',args:{},state:'queued',createdAt:1770000000000,updatedAt:1770000000000,revision:0,sessions:[],...(presentation?{presentation:{sessionIds:['ahp-session:/fixture-session'],actor:{actorId:'fixture-account',origin:'ui'}}}:{})});
const forbidden=()=>{assert.fail('Startup and exact passive reads must not invoke native or host effects');};
function owner(directory,presentation=false){
 const authorize=async()=>({accountId:'fixture-account'});
 if(!presentation)return createRecoveryCapabilities({directory,nativeAuthority:'fixture',nativeAdmin:forbidden,resolveSession:forbidden,authorize,quiescence:{}});
 const port={presentationReset:{version:1,preservesCanonical:true,preservesAuthority:true}};
 for(const key of ['prepareConversationPresentation','readConversationPresentationReview','applyConversationPresentation','conversationPresentationReceipt','reconcileConversationPresentation','inspectConversationPresentation','rebuildConversationPresentation','reconcileConversationPresentationRebuild'])port[key]=forbidden;
 return createPresentationCapabilities({directory,authorize,conversationPresentation:port});
}
const call=(o,operation,args={})=>o.action({version:1,topic:'recovery',channel:'ahp-root://',operation,args,commandId:'passive-fixture-read'},{clientId:'fixture-client',origin:'ui'});
const temporary=()=>mkdtempSync(join(tmpdir(),'recovery-startup-'));
function authority(directory){return Object.fromEntries(['recovery.sqlite','recovery.sqlite-wal','recovery.sqlite-journal'].filter(name=>existsSync(join(directory,name))).map(name=>[name,createHash('sha256').update(readFileSync(join(directory,name))).digest('hex')]));}
function preserved(before,directory){const after=authority(directory);for(const [name,digest] of Object.entries(before))assert.equal(after[name],digest,`changed pre-existing ${name}`);for(const name of Object.keys(after))if(!Object.hasOwn(before,name)){assert.equal(name,'recovery.sqlite-wal');assert.equal(readFileSync(join(directory,name)).length,0);}}
function seed(directory,{presentation=false,sql='',held=true,release=false,crash=true,heldContext=context}={}){
 const source=`import {Store} from ${JSON.stringify(storeModule)};const store=new Store(${JSON.stringify(directory)});store.db.exec('PRAGMA wal_autocheckpoint=0');store.insert(${JSON.stringify(job(presentation))});${held?`store.setFence(${JSON.stringify(heldContext)});`:''}${release?`store.completeRelease(${JSON.stringify(context)},'fixture-signature',undefined);`:''}${sql?`store.db.exec(${JSON.stringify(sql)});`:''}${crash?"process.kill(process.pid,'SIGKILL');":"store.close();"}`;
 const result=spawnSync(process.execPath,['--input-type=module','-e',source],{encoding:'utf8'});
 if(crash)assert.equal(result.signal,'SIGKILL',result.stderr);else assert.equal(result.status,0,result.stderr);
}
for(const presentation of [false,true])test(`fresh ${presentation?'presentation-only':'native'} store initializes and reopens`,async()=>{
 const directory=temporary();let o;
 try{o=owner(directory,presentation);assert.equal((await call(o,'recovery.list')).result.coverage,presentation?'presentation-only':'native-only');await o.close();o=undefined;const db=new DatabaseSync(join(directory,'recovery.sqlite'),{readOnly:true});assert.equal(db.prepare('PRAGMA user_version').get().user_version,1);db.close();o=owner(directory,presentation);assert.equal((await call(o,'recovery.list')).result.items.length,0);}finally{await o?.close();rmSync(directory,{recursive:true,force:true});}
});
for(const presentation of [false,true])test(`intact crash preserves original unknown and held fence (${presentation?'presentation':'native'})`,async()=>{
 const directory=temporary();let o;
 try{seed(directory,{presentation});assert.ok(existsSync(join(directory,'recovery.sqlite-wal')));o=owner(directory,presentation);const r=(await call(o,'recovery.command',{commandId:command})).result;assert.equal(r.state,'unknown');assert.equal(r.reason,'owner-restarted-no-replay');assert.equal((await call(o,'recovery.list')).result.items.length,1);assert.equal(await o.quiescenceParticipant.acquire(next),null);}finally{await o?.close();rmSync(directory,{recursive:true,force:true});}
});
for(const [name,sql] of [
 ['jobs','DROP TABLE jobs'],['fence','DROP TABLE fence'],['jobs and fence','DROP TABLE jobs;DROP TABLE fence'],
 ['release receipts','DROP TABLE participant_releases'],['revision','DROP TABLE revision'],['revision singleton','DELETE FROM revision'],
 ['negative revision','UPDATE revision SET value=-1'],['noninteger revision',"UPDATE revision SET value='invalid'"],
 ['table shape','ALTER TABLE jobs ADD COLUMN unexpected TEXT'],['future version','PRAGMA user_version=99'],
 ['fence payload',"UPDATE fence SET payload='null'"],
 ])test(`missing or invalid ${name} refuses before any main/WAL change`,()=>{
 const directory=temporary();try{seed(directory,{sql});const before=authority(directory);assert.ok(before['recovery.sqlite-wal']);assert.throws(()=>owner(directory),e=>e.code==='RECOVERY_STORE_UNAVAILABLE');preserved(before,directory);assert.throws(()=>owner(directory,true),e=>e.code==='RECOVERY_STORE_UNAVAILABLE');preserved(before,directory);}finally{rmSync(directory,{recursive:true,force:true});}
});
test('complete unversioned modern schema is adopted without losing a job, hold, or exact release',async()=>{
 const directory=temporary();let o;
 try{
  const proof={verified:true,fenceId:context.fenceId,commandId:context.commandId,outcome:'unchanged',instanceId:context.instanceId,dataScope:context.dataScope,receiptId:'fixture-proof'};
  o=owner(directory);const lease=await o.quiescenceParticipant.acquire(context);await lease.release('unchanged',proof);await o.close();o=undefined;
  seed(directory,{sql:'PRAGMA user_version=0',heldContext:next});o=owner(directory);assert.equal((await call(o,'recovery.command',{commandId:command})).result.state,'unknown');assert.equal(await o.quiescenceParticipant.acquire(next),null);await o.quiescenceParticipant.reconcileRelease({...context,outcome:'unchanged',proof});
  // The older release is exact evidence only; it cannot release the newer persisted hold.
  assert.equal(await o.quiescenceParticipant.acquire(next),null);
  await o.close();o=undefined;const db=new DatabaseSync(join(directory,'recovery.sqlite'),{readOnly:true});assert.equal(db.prepare('PRAGMA user_version').get().user_version,1);assert.equal(db.prepare('SELECT count(*) AS n FROM participant_releases').get().n,1);db.close();
 }finally{await o?.close();rmSync(directory,{recursive:true,force:true});}
});
test('incomplete historical unversioned schema needs reviewed migration, with no lease file created',()=>{
 const directory=temporary();try{seed(directory,{sql:'PRAGMA user_version=0;DROP TABLE participant_releases',crash:false});unlinkSync(join(directory,'recovery-owner-lock.sqlite'));const before=authority(directory);assert.throws(()=>owner(directory),e=>e.code==='RECOVERY_STORE_UNAVAILABLE'&&e.reason==='incomplete-store-schema');preserved(before,directory);assert.equal(existsSync(join(directory,'recovery-owner-lock.sqlite')),false);}finally{rmSync(directory,{recursive:true,force:true});}
});
for(const sidecar of ['-wal','-shm','-journal'])test(`missing main with existing ${sidecar} is not fresh`,()=>{
 const directory=temporary();try{writeFileSync(join(directory,'recovery.sqlite'+sidecar),'fixture prior authority evidence');const before=readFileSync(join(directory,'recovery.sqlite'+sidecar));assert.throws(()=>owner(directory),e=>e.reason==='orphan-store-sidecars');assert.deepEqual(readFileSync(join(directory,'recovery.sqlite'+sidecar)),before);assert.equal(existsSync(join(directory,'recovery.sqlite')),false);assert.equal(existsSync(join(directory,'recovery-owner-lock.sqlite')),false);}finally{rmSync(directory,{recursive:true,force:true});}
});
test('real crash-left WAL survives refusal when main is missing',()=>{
 const directory=temporary();try{seed(directory);unlinkSync(join(directory,'recovery.sqlite'));const before=authority(directory);assert.ok(before['recovery.sqlite-wal']);assert.throws(()=>owner(directory),e=>e.reason==='orphan-store-sidecars');preserved(before,directory);assert.equal(existsSync(join(directory,'recovery.sqlite')),false);}finally{rmSync(directory,{recursive:true,force:true});}
});
test('existing empty main is unavailable, not new',()=>{
 const directory=temporary();try{writeFileSync(join(directory,'recovery.sqlite'),'');const before=authority(directory);assert.throws(()=>owner(directory),e=>e.reason==='incomplete-store-schema');preserved(before,directory);assert.equal(existsSync(join(directory,'recovery-owner-lock.sqlite')),false);}finally{rmSync(directory,{recursive:true,force:true});}
});
test('unfenced presentation-only unknown remains isolated from unrelated maintenance',async()=>{
 const directory=temporary();let o;try{seed(directory,{presentation:true,held:false});o=owner(directory,true);assert.equal((await call(o,'recovery.command',{commandId:command})).result.state,'unknown');assert.ok(await o.quiescenceParticipant.acquire(next));}finally{await o?.close();rmSync(directory,{recursive:true,force:true});}
});
test('competing owner cannot normalize a live queued job',async()=>{
 const directory=temporary();let live;
 try{
  live=new Store(directory);live.insert(job());
  const child=spawnSync(process.execPath,['--input-type=module','-e',`import {Store} from ${JSON.stringify(storeModule)};new Store(${JSON.stringify(directory)});`],{encoding:'utf8'});
  assert.notEqual(child.status,0);assert.match(child.stderr,/exclusive ownership unavailable/);assert.equal(live.get('fixture-job').state,'queued');
 }finally{live?.close();rmSync(directory,{recursive:true,force:true});}
});
test('failed normalization rolls back schema adoption marker atomically',()=>{
 const directory=temporary();try{seed(directory,{sql:"PRAGMA user_version=0;CREATE TRIGGER fixture_abort BEFORE UPDATE ON jobs BEGIN SELECT RAISE(ABORT,'fixture interrupted normalization');END",crash:false});assert.throws(()=>new Store(directory));const db=new DatabaseSync(join(directory,'recovery.sqlite'),{readOnly:true});assert.equal(db.prepare('PRAGMA user_version').get().user_version,0);assert.equal(db.prepare('SELECT state FROM jobs').get().state,'queued');db.close();}finally{rmSync(directory,{recursive:true,force:true});}
});

test('missing derived index retains supported rebuild after authority validation',async()=>{
 const directory=temporary();let o;
 try{seed(directory,{sql:'DROP INDEX jobs_unsettled'});o=owner(directory);assert.equal((await call(o,'recovery.command',{commandId:command})).result.state,'unknown');assert.equal(await o.quiescenceParticipant.acquire(next),null);await o.close();o=undefined;const db=new DatabaseSync(join(directory,'recovery.sqlite'),{readOnly:true});assert.ok(db.prepare("SELECT name FROM sqlite_schema WHERE name='jobs_unsettled' AND type='index'").get());db.close();}
 finally{await o?.close();rmSync(directory,{recursive:true,force:true});}
});
