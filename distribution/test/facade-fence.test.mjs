import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm,readFile,readdir,writeFile,symlink,readlink,lstat} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {createHash} from 'node:crypto';import {FacadeFence} from '../src/facade-fence.js';
test('retention coverage requires explicit support and a currently held idle forwarding lease',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'retention-facade-'));
 let owner=new FacadeFence({directory,id:'facade'});
 const ctx={fenceId:'retention',commandId:'hide',purpose:'retention-hide',instanceId:'old',dataScope:'scope'};
 try{
  await assert.rejects(owner.participant.acquire(ctx),/not configured/);
  owner.close();owner=new FacadeFence({directory,id:'facade',retentionHide:true});
  assert.deepEqual(owner.participant.retentionHide,{version:1});
  let finish;const running=owner.run(false,()=>new Promise(resolve=>finish=resolve));
  assert.equal(await owner.participant.acquire(ctx),null);finish();await running;
  const lease=await owner.participant.acquire(ctx);
  const family={sessions:['ahp-session:/one','ahp-session:/child'],limit:101};
  assert.deepEqual(await lease.inspectRetentionReferences(family),{coverage:'complete',protected:[],omissions:[]});
  await assert.rejects(lease.inspectRetentionReferences({...family,limit:102}),/Bounded/);
  await assert.rejects(owner.run(false,()=>42),/intake is closed/);
  await lease.release('unknown');
  await assert.rejects(lease.inspectRetentionReferences(family),/held facade/);
  await lease.release('unchanged',{verified:true,...ctx,outcome:'unchanged',receiptId:'exact-native-hidden'});
  await assert.rejects(lease.inspectRetentionReferences(family),/held facade/);
  assert.equal(await owner.run(false,()=>42),42);
 }finally{owner.close();await rm(directory,{recursive:true,force:true});}
});
test('facade keeps forwarding owned, persists fence, excludes competitors and replays only release proof',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'facade-intake-'));let idle=0,finish;let owner=new FacadeFence({directory,id:'updates',onMayBeIdle:()=>idle++});const ctx={fenceId:'one',commandId:'update',purpose:'distribution-update',instanceId:'old',dataScope:'scope'};
 try{
  assert.throws(()=>new FacadeFence({directory,id:'other'}),/locked/);
  const running=owner.run(false,()=>new Promise(resolve=>finish=resolve));assert.equal(await owner.participant.acquire({...ctx,fenceId:'busy-attempt'}),null);assert.throws(()=>owner.close(),/active/);finish();await running;assert.equal(idle,1);
  const held=await owner.participant.acquire(ctx);await held.release('unknown');owner.close();owner=new FacadeFence({directory,id:'updates'});
  await assert.rejects(owner.run(false,()=>{throw Error('must not forward')}),/intake is closed/);assert.equal(await owner.run(true,()=>42),42);await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{kind:'admission-refused'}}),/Pre-effect/);
  const proof={verified:true,...ctx,outcome:'ready',instanceId:'new',receiptId:'supervisor-exact'};await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});owner.close();owner=new FacadeFence({directory,id:'updates'});await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});
  assert.equal(await owner.run(false,()=>1),1);await assert.rejects(owner.participant.acquire(ctx),/cannot be reused/);
 }finally{owner.close();await rm(directory,{recursive:true,force:true});}
});

test('service facade retains complete identity across restart and refuses generic or mismatched release',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'service-facade-'));
 const identity={installationId:'install',ownerId:'owner',instanceId:'old',dataScope:'scope',releaseDigest:'a'.repeat(64)};
 const ctx={fenceId:'service-one',commandId:'stop',purpose:'service-stop',instanceId:'old',dataScope:'scope',serviceIdentity:identity};
 let owner=new FacadeFence({directory,id:'updates',serviceStop:true});
 const proof={verified:true,kind:'service-lifecycle',fenceId:ctx.fenceId,commandId:ctx.commandId,dataScope:'scope',instanceId:'new',outcome:'ready',receiptId:'resume',serviceOutcome:'resumed',expected:identity,observed:{...identity,instanceId:'new'},resumeCommandId:'resume',exitReceiptId:'exit',readyReceiptId:'ready'};
 try{
  assert.deepEqual(owner.participant.serviceStop,{version:1});
  await assert.rejects(owner.participant.acquire({...ctx,serviceIdentity:undefined}),/service/);
  const lease=await owner.participant.acquire(ctx);assert.ok(lease);assert.deepEqual(owner.fence().serviceIdentity,identity);
  await lease.release('unknown');owner.close();owner=new FacadeFence({directory,id:'updates',serviceStop:true});
  assert.deepEqual(owner.fence().serviceIdentity,identity);
  await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{kind:'admission-refused'}}),/Pre-effect/);
  for(const invalid of [
   {...proof,kind:undefined},
   {...proof,expected:{...identity,installationId:'other'}},
   {...proof,observed:{...proof.observed,ownerId:'other'}},
   {...proof,observed:{...proof.observed,releaseDigest:'b'.repeat(64)}},
   {...proof,exitReceiptId:undefined},
   {...proof,serviceOutcome:'stop-refused'},
  ])await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'ready',proof:invalid}));
  await assert.rejects(owner.participant.reconcileRelease({...ctx,serviceIdentity:{...identity,ownerId:'other'},outcome:'ready',proof}),/Exact held/);
  await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});
  await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});
  assert.equal(owner.fence(),null);assert.equal(await owner.run(false,()=>42),42);
 }finally{owner.close();await rm(directory,{recursive:true,force:true});}
});

test('service gate is opt-in and definitive busy refusal restores only its unchanged held instance',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'service-facade-refusal-'));
 const identity={installationId:'install',ownerId:'owner',instanceId:'old',dataScope:'scope',releaseDigest:'a'.repeat(64)};
 const ctx={fenceId:'service-refusal',commandId:'stop',purpose:'service-stop',instanceId:'old',dataScope:'scope',serviceIdentity:identity};
 let owner=new FacadeFence({directory,id:'updates'});
 try{
  assert.equal(owner.participant.serviceStop,undefined);await assert.rejects(owner.participant.acquire(ctx),/not configured/);
  owner.close();owner=new FacadeFence({directory,id:'updates',serviceStop:true});
  const lease=await owner.participant.acquire(ctx);
  const proof={verified:true,kind:'service-lifecycle',fenceId:ctx.fenceId,commandId:ctx.commandId,dataScope:'scope',instanceId:'old',outcome:'unchanged',receiptId:'stop',serviceOutcome:'stop-refused',expected:identity,observed:identity,refusalReceiptId:'stop'};
  await assert.rejects(lease.release('unchanged',{...proof,resumeCommandId:'wrong'}));
  await lease.release('unchanged',proof);assert.equal(owner.fence(),null);
  const another=await owner.participant.acquire({...ctx,fenceId:'pre-effect',commandId:'pre-effect'});
  await another.release('unchanged',{kind:'admission-refused'});assert.equal(owner.fence(),null);
 }finally{owner.close();await rm(directory,{recursive:true,force:true});}
});


test('unknown outcome invalidates a live partial-acquisition rollback token immediately',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'facade-live-unknown-'));
 const owner=new FacadeFence({directory,id:'updates'}),ctx={fenceId:'live-unknown',commandId:'command',purpose:'distribution-update',instanceId:'old',dataScope:'scope'};
 try{
  const lease=await owner.participant.acquire(ctx);await lease.release('unknown');
  await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}),/Pre-effect/);
  await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{kind:'admission-refused'}}),/Pre-effect/);
  assert.equal(owner.fence().phase,'unknown');await assert.rejects(owner.run(false,()=>42),/intake is closed/);
  await lease.release('ready',{verified:true,...ctx,outcome:'ready',instanceId:'new',receiptId:'exact-authenticated-receipt'});
  assert.equal(owner.fence(),null);
 }finally{owner.close();await rm(directory,{recursive:true,force:true})}
});

test('managed-file coverage is separately enabled and binds one exact allocation while held',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'managed-facade-binding-'));
 const ctx={fenceId:'files',commandId:'dispose',purpose:'managed-files-disposal',instanceId:'old',dataScope:'scope'};
 const selection={sessions:['ahp-session:/one'],limit:101,allocation:{allocationId:'01234567-1234-1234-1234-123456789abc',executionDirectory:'/owned/files',allocationHash:'a'.repeat(64),treeHash:'b'.repeat(64),entryCount:1,bytes:12}};
 let owner=new FacadeFence({directory,id:'facade',retentionHide:true});
 try{await assert.rejects(owner.participant.acquire(ctx),/not configured/);owner.close();owner=new FacadeFence({directory,id:'facade',managedFiles:true});assert.deepEqual(owner.participant.managedFiles,{version:1,preservesCanonical:true});const lease=await owner.participant.acquire(ctx);assert.deepEqual(await lease.inspectManagedFilesReferences(selection),{coverage:'complete',protected:[],omissions:[]});await assert.rejects(lease.inspectManagedFilesReferences({...selection,allocation:{...selection.allocation,treeHash:'c'.repeat(64)}}),/binding changed/);await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{kind:'admission-refused'}}),/Pre-effect/);await lease.release('unknown');await assert.rejects(lease.inspectManagedFilesReferences(selection),/held facade/);await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}),/Pre-effect/);await lease.release('unchanged',{verified:true,...ctx,outcome:'unchanged',receiptId:'exact-managed-effect'});await assert.rejects(lease.inspectManagedFilesReferences(selection),/held facade/);}finally{owner.close();await rm(directory,{recursive:true,force:true});}
});
test('passive forwarding is allowed under hold but remains joined for lifetime and other acquisitions',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'facade-passive-')),owner=new FacadeFence({directory,id:'facade'});let finish;
 try{const call=owner.run(true,()=>new Promise(r=>finish=r));assert.equal(await owner.participant.acquire({fenceId:'one',commandId:'one',purpose:'recovery',instanceId:'old',dataScope:'scope'}),null);assert.throws(()=>owner.close(),/active/);let drained=false;const closing=owner.drain().then(()=>drained=true);await new Promise(r=>setImmediate(r));assert.equal(drained,false);finish();await call;await closing;}finally{finish?.();owner.close();await rm(directory,{recursive:true,force:true});}
});


// Main and nonempty WAL contain authority; SHM is a derived SQLite index.
async function authorityImage(directory){
 const image={};
 for(const name of (await readdir(directory)).filter(name=>name==='intake.sqlite3'||name==='intake.sqlite3-wal').sort()){
  const bytes=await readFile(join(directory,name));if(name.endsWith('-wal')&&!bytes.length)continue;
  image[name]={bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
 }
 return image;
}
test('existing facade schema loss refuses without repair; new and legitimately empty fences remain valid',async()=>{
 const {DatabaseSync}=await import('node:sqlite');
 for(const table of ['fence','releases']){
  const directory=await mkdtemp(join(tmpdir(),'facade-schema-'));
  try{
   let owner=new FacadeFence({directory,id:'fixture',retentionHide:true});
   const ctx={fenceId:'original',commandId:'original',purpose:'retention-hide',instanceId:'fixture',dataScope:'fixture'};
   await owner.acquire(ctx).release('unchanged',{kind:'admission-refused'});owner.close();
   owner=new FacadeFence({directory,id:'fixture',retentionHide:true});
   assert.equal(await owner.run(false,()=>42),42);
   assert.throws(()=>owner.acquire(ctx),/cannot be reused/);owner.close();
   const db=new DatabaseSync(join(directory,'intake.sqlite3'));db.exec('DROP TABLE '+table);db.close();
   const before=await authorityImage(directory);
   for(let attempt=0;attempt<2;attempt++)assert.throws(()=>new FacadeFence({directory,id:'fixture'}),/Existing authority schema is incomplete/);
   assert.deepEqual(await authorityImage(directory),before);
   const check=new DatabaseSync(join(directory,'intake.sqlite3'),{readOnly:true});
   assert.equal(check.prepare('SELECT name FROM sqlite_master WHERE name=?').get(table),undefined);check.close();
  }finally{await rm(directory,{recursive:true,force:true});}
 }
});
test('interrupted facade missing schema in WAL refuses and preserves main database and nonempty WAL',async()=>{
 const {spawnSync}=await import('node:child_process');
 const directory=await mkdtemp(join(tmpdir(),'facade-wal-'));
 try{
  const source=new URL('../src/facade-fence.js',import.meta.url).href;
  const child=spawnSync(process.execPath,['--input-type=module','-e',`
   import {DatabaseSync} from 'node:sqlite';import {join} from 'node:path';import {createHash} from 'node:crypto';import {FacadeFence} from ${JSON.stringify(source)};
   const directory=${JSON.stringify(directory)},owner=new FacadeFence({directory,id:'fixture',retentionHide:true});
   await owner.acquire({fenceId:'original',commandId:'original',purpose:'retention-hide',instanceId:'fixture',dataScope:'fixture'}).release('unknown');
   const db=new DatabaseSync(join(directory,'intake.sqlite3'));db.exec('DROP TABLE fence');
   process.kill(process.pid,'SIGKILL');
  `],{encoding:'utf8',timeout:10000});
  assert.equal(child.signal,'SIGKILL',child.stderr);
  const before=await authorityImage(directory);assert.ok(before['intake.sqlite3-wal'].bytes>0);
  for(let attempt=0;attempt<2;attempt++)assert.throws(()=>new FacadeFence({directory,id:'fixture'}),/Existing authority schema is incomplete/);
  assert.deepEqual(await authorityImage(directory),before);
  // The same crash-left nonempty WAL must never be mistaken for a fresh store.
  const {unlink}=await import('node:fs/promises');await unlink(join(directory,'intake.sqlite3'));
  const orphan=await authorityImage(directory);
  assert.throws(()=>new FacadeFence({directory,id:'fixture'}),/main database is missing/);
  assert.deepEqual(await authorityImage(directory),orphan);
 }finally{await rm(directory,{recursive:true,force:true});}
});


test('facade missing main with surviving sidecar refuses before fresh initialization',async()=>{
 for(const suffix of ['-wal','-shm','-journal']){
  const directory=await mkdtemp(join(tmpdir(),'facade-orphan-'));
  try{
   await writeFile(join(directory,'intake.sqlite3'+suffix),'');
   assert.throws(()=>new FacadeFence({directory,id:'fixture'}),/main database is missing/);
   assert.equal((await readdir(directory)).includes('intake.sqlite3'),false);
   assert.equal((await readFile(join(directory,'intake.sqlite3'+suffix))).length,0);
  }finally{await rm(directory,{recursive:true,force:true});}
 }
});
test('facade existing store missing referenced column refuses read-only',async()=>{
 const {DatabaseSync}=await import('node:sqlite'),directory=await mkdtemp(join(tmpdir(),'facade-column-'));
 try{
  const owner=new FacadeFence({directory,id:'fixture'});owner.close();
  const db=new DatabaseSync(join(directory,'intake.sqlite3'));db.exec('ALTER TABLE fence RENAME COLUMN body TO lost_body');db.close();
  const before=await authorityImage(directory);
  assert.throws(()=>new FacadeFence({directory,id:'fixture'}),/Existing authority schema is incomplete/);
  assert.deepEqual(await authorityImage(directory),before);
 }finally{await rm(directory,{recursive:true,force:true});}
});


test('dangling facade main and sidecars are existing evidence, never fresh paths',async()=>{
 for(const suffix of ['', '-wal','-shm','-journal']){
  const directory=await mkdtemp(join(tmpdir(),'facade-dangling-'));
  try{
   const path=join(directory,'intake.sqlite3'+suffix),target=join(directory,'missing-target');
   await writeFile(join(directory,'sentinel'),'preserve');await symlink(target,path);
   for(let attempt=0;attempt<2;attempt++)assert.throws(()=>new FacadeFence({directory,id:'fixture'}));
   assert.equal(await readlink(path),target);assert.ok((await lstat(path)).isSymbolicLink());
   await assert.rejects(lstat(target),{code:'ENOENT'});
   if(suffix)await assert.rejects(lstat(join(directory,'intake.sqlite3')),{code:'ENOENT'});
   assert.equal(await readFile(join(directory,'sentinel'),'utf8'),'preserve');
  }finally{await rm(directory,{recursive:true,force:true});}
 }
});
