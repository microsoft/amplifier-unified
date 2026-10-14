import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {createAuthority,readAuthority,authorityKey,writeAuthority} from '../dist/manual-authority.js';
import {prepareFailedBootstrapRecovery,createManualSystemdHandoffLauncher,inspectExistingStateBindings,witnessDigest} from '../dist/index.js';
const hash=b=>createHash('sha256').update(b).digest('hex');
async function fixture(t){
 const root=await realpath(await mkdtemp(join(tmpdir(),'failed-bootstrap-')));
 t.after(()=>rm(root,{recursive:true,force:true}));
 const data=join(root,'data');await mkdir(data);
 await writeFile(join(data,'history'),'preserved canonical history');
 const oldConfig=join(root,'old.json'),nextConfig=join(root,'next.json');
 await writeFile(oldConfig,'{"certificate":"old"}');await writeFile(nextConfig,'{"certificate":"reviewed-copy"}');
 const bindings=[{id:'data',kind:'directory',path:data},{id:'config',kind:'file',path:oldConfig}];
 const nextBindings=[{id:'data',kind:'directory',path:data},{id:'config',kind:'file',path:nextConfig}];
 const expected={installationId:'fixture',ownerId:'fixture',dataScope:'fixture',instanceId:'failed-source',releaseDigest:'a'.repeat(64)};
 const target={identity:{id:'fixture',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},handle:'fixture'};
 const witness={unit:'failed-bootstrap-fixture.service',pid:99999999,bootId:'fixture-boot',startTicks:'1',invocationId:'a'.repeat(32),cgroup:'/fixture',unitDigest:'a'.repeat(64)};
 const sourceDirectory=join(root,'source'),claimDirectory=join(root,'claim'),supervisorDirectory=join(root,'supervisor');
 const state={schema:'manual-systemd-source-v1',phase:'ready',expected,target,witness,dataBindingDigest:await inspectExistingStateBindings(bindings),owners:['fixture-owner'],updatedAt:1};
 const key=await createAuthority(sourceDirectory,state);
 const next={...expected,instanceId:'successor'};
 const options={sourceDirectory,claimDirectory,supervisorDirectory,predecessorBindings:bindings,expectedOwners:['fixture-owner'],
  successor:{directory:join(root,'successor'),unit:'next-bootstrap-fixture.service',expected:next,bindings:nextBindings},
  // Contract fixture: the separate Linux observer supplies actual kernel checks.
  assertExclusionHeld:async()=>{},
  observer:{confirmFailedBootstrapExited:async w=>({schema:'failed-bootstrap-exit-v1',witness:w,exitCode:1})},
  qualifyStoppedState:async()=>({schema:'failed-bootstrap-stopped-state-v1',predecessor:expected,witnessDigest:witnessDigest(witness),
   predecessorBindingDigest:await inspectExistingStateBindings(bindings),successorBindingDigest:await inspectExistingStateBindings(nextBindings),
   inventoryDigest:hash(await readFile(join(data,'history'))),evidenceDigest:'b'.repeat(64),owners:['fixture-owner'],coverage:'complete',
   activeWriters:0,pendingEffects:0,unaccountedEffects:0,historicalUnknown:{count:5,digest:'c'.repeat(64)},publicReadyObserved:false,admissionObserved:false})};
 const launch=()=>createManualSystemdHandoffLauncher({directory:options.successor.directory,expected:next,bindings:nextBindings,
  qualifyCurrent:async()=>target,observer:{capture:async()=>({...witness,unit:options.successor.unit,pid:process.pid,invocationId:process.env.INVOCATION_ID??witness.invocationId})},recovery:options});
 return {root,data,bindings,nextBindings,options,state,key,launch};
}
test('explicit recovery preserves failed evidence and binds one successor before app creation',async t=>{
 const f=await fixture(t),before=await readFile(join(f.options.sourceDirectory,'authority.json'));
 const permit=await prepareFailedBootstrapRecovery(f.options);assert.equal(permit.schema,'failed-bootstrap-recovery-permit-v1');
 assert.equal('stopped' in permit,false);assert.equal('handoffRetired' in permit,false);
 await f.launch();
 assert.deepEqual(await readFile(join(f.options.sourceDirectory,'authority.json')),before);
 const next=await readAuthority(f.options.successor.directory,await authorityKey(f.options.successor.directory));
 assert.equal(next.phase,'starting');assert.equal(next.recovery.predecessor,f.options.sourceDirectory);assert.equal(next.recovery.permitDigest,permit.digest);
 await assert.rejects(f.launch(),/authority_exists|EEXIST/);
 await assert.rejects(prepareFailedBootstrapRecovery(f.options),/authority_exists|EEXIST/);
 assert.equal(await readFile(join(f.data,'history'),'utf8'),'preserved canonical history');
});
test('retirement, admission, and pending authorities cannot be reclassified as failed bootstrap',async t=>{
 for(const kind of ['retired','admission_requested','closed','fence','claim-field','claim-directory','supervisor-directory']){
  await t.test(kind,async t=>{
   const f=await fixture(t);
   if(kind.endsWith('directory'))await mkdir(kind==='claim-directory'?f.options.claimDirectory:f.options.supervisorDirectory);
   else {const state={...f.state};if(kind==='fence')state.fenceId='f';else if(kind==='claim-field')state.claim={};else state.phase=kind;
    await writeAuthority(f.options.sourceDirectory,f.key,state);}
   await assert.rejects(prepareFailedBootstrapRecovery(f.options),/not_eligible|authority_exists/);
   await assert.rejects(readFile(join(f.options.sourceDirectory,'failed-bootstrap-recovery','authority.json')),e=>e.code==='ENOENT');
  });
 }
});
test('owner effects and readiness must be completely qualified',async t=>{
 for(const [key,value] of [['coverage','unknown'],['activeWriters',1],['pendingEffects',1],['unaccountedEffects',1],['historicalUnknown',{count:-1,digest:'c'.repeat(64)}],['publicReadyObserved',true],['admissionObserved',true],['owners',['different-owner']],['inventoryDigest','unknown']]){
  await t.test(key,async t=>{
   const f=await fixture(t),qualify=f.options.qualifyStoppedState;
   f.options.qualifyStoppedState=async()=>({...await qualify(),[key]:value});
   await assert.rejects(prepareFailedBootstrapRecovery(f.options),/unqualified/);
  });
 }
});
test('dead-process observer refusal preserves source and issues no permit',async t=>{
 const f=await fixture(t);
 f.options.observer.confirmFailedBootstrapExited=async()=>{throw Error('live_or_changed_process');};
 await assert.rejects(prepareFailedBootstrapRecovery(f.options),/live_or_changed_process/);
 await assert.rejects(readFile(join(f.options.sourceDirectory,'failed-bootstrap-recovery','authority.json')),e=>e.code==='ENOENT');
});
test('changed stopped state after preparation cannot consume the successor',async t=>{
 const f=await fixture(t);await prepareFailedBootstrapRecovery(f.options);
 await writeFile(join(f.data,'history'),'new work from restored old service');
 await assert.rejects(f.launch(),/review_stale/);
 await assert.rejects(readFile(join(f.options.successor.directory,'authority.json')),e=>e.code==='ENOENT');
});
test('new claim after preparation cannot consume the successor',async t=>{
 const f=await fixture(t);await prepareFailedBootstrapRecovery(f.options);await mkdir(f.options.claimDirectory);
 await assert.rejects(f.launch(),/authority_exists/);
});
test('old config mutation and data-root rebinding refuse',async t=>{
 await t.test('old file',async t=>{const f=await fixture(t);await writeFile(f.bindings[1].path,'changed');await assert.rejects(prepareFailedBootstrapRecovery(f.options),/original_bindings_changed/);});
 await t.test('directory',async t=>{const f=await fixture(t);f.options.successor.bindings[0].path=f.root;await assert.rejects(prepareFailedBootstrapRecovery(f.options),/data_roots_changed/);});
});
test('authentication tamper refuses',async t=>{
 const f=await fixture(t);const path=join(f.options.sourceDirectory,'authority.json'),v=JSON.parse(await readFile(path));v.text+=' ';
 await writeFile(path,JSON.stringify(v));await assert.rejects(prepareFailedBootstrapRecovery(f.options),/authentication/);
});
test('concurrent preparation and consumption each admit exactly one',async t=>{
 const f=await fixture(t);
 const prepared=await Promise.allSettled([prepareFailedBootstrapRecovery(f.options),prepareFailedBootstrapRecovery(f.options)]);
 assert.equal(prepared.filter(x=>x.status==='fulfilled').length,1);
 const launched=await Promise.allSettled([f.launch(),f.launch()]);
 assert.equal(launched.filter(x=>x.status==='fulfilled').length,1);
});
test('successor identity cannot be changed after permit',async t=>{
 const f=await fixture(t);await prepareFailedBootstrapRecovery(f.options);
 f.options.successor.expected={...f.options.successor.expected,instanceId:'third-generation'};
 await assert.rejects(f.launch(),/permit_mismatch/);
});

test('starting source can recover only with the explicit complete owner census',async t=>{
 const f=await fixture(t);await writeAuthority(f.options.sourceDirectory,f.key,{...f.state,phase:'starting',owners:[]});
 await prepareFailedBootstrapRecovery(f.options);await f.launch();
});
test('consumed permit with interrupted source creation remains permanently consumed',async t=>{
 const f=await fixture(t);await prepareFailedBootstrapRecovery(f.options);
 const {consumeFailedBootstrapRecovery}=await import('../dist/manual-bootstrap-recovery.js');
 await consumeFailedBootstrapRecovery(f.options,{directory:f.options.successor.directory,expected:f.options.successor.expected,
  witness:{...f.state.witness,pid:process.pid,unit:f.options.successor.unit},bindingDigest:await inspectExistingStateBindings(f.nextBindings)});
 // Simulate a crash before new authority creation. Absence is not retry consent.
 await assert.rejects(readFile(join(f.options.successor.directory,'authority.json')),e=>e.code==='ENOENT');
 await assert.rejects(f.launch(),/EEXIST/);
});
test('overlapping authority paths cannot bypass predecessor evidence',async t=>{
 const f=await fixture(t);f.options.successor.directory=join(f.options.sourceDirectory,'nested');
 await assert.rejects(prepareFailedBootstrapRecovery(f.options),/authority_overlap/);
});

test('historical unknown receipts survive but changed or new unknown evidence refuses',async t=>{
 const f=await fixture(t),qualify=f.options.qualifyStoppedState;
 const permit=await prepareFailedBootstrapRecovery(f.options);
 assert.equal(permit.schema,'failed-bootstrap-recovery-permit-v1');
 const record=await readAuthority(permit.directory,await authorityKey(permit.directory));
 assert.equal(record.qualification.historicalUnknown.count,5);
 f.options.qualifyStoppedState=async()=>({...await qualify(),historicalUnknown:{count:5,digest:'d'.repeat(64)}});
 await assert.rejects(f.launch(),/review_stale/);
 f.options.qualifyStoppedState=async()=>({...await qualify(),unaccountedEffects:1});
 await assert.rejects(f.launch(),/unqualified/);
 await assert.rejects(readFile(join(f.options.successor.directory,'authority.json')),e=>e.code==='ENOENT');
});

test('lost held exclusion refuses before creating successor authority',async t=>{
 const f=await fixture(t);await prepareFailedBootstrapRecovery(f.options);
 f.options.assertExclusionHeld=async()=>{throw Error('exclusion_lost');};
 await assert.rejects(f.launch(),/exclusion_lost/);
 await assert.rejects(readFile(join(f.options.successor.directory,'authority.json')),e=>e.code==='ENOENT');
});
