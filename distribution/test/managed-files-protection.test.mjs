import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createManagedFilesProtection} from '../src/managed-files-protection.js';
import {FacadeFence} from '../src/facade-fence.js';
const session='ahp-session:/parent',child='ahp-session:/child';
const allocation={allocationId:'01234567-1234-1234-1234-123456789abc',executionDirectory:'/owned/chats/01234567-1234-1234-1234-123456789abc/files',allocationHash:'a'.repeat(64),treeHash:'b'.repeat(64),entryCount:2,bytes:12};
const input={commandId:'effect',session,descendants:[child],operation:'dispose-owned-files',reviewHash:'c'.repeat(64),allocation};
const effect=(extra={})=>({...input,descendants:undefined,status:'completed',preservesCanonical:true,...extra});
function owner(id,{references,loseRelease=false,loseAcquire=false,acquire=null}={}){
 const calls=[],state={held:null,receipt:null};
 const release=async(context,outcome,proof)=>{
  calls.push({operation:'release',outcome,proof});if(outcome==='unknown')return;
  if(state.receipt){assert.deepEqual({context,outcome,proof},state.receipt);return;}
  assert.deepEqual(context,state.held);state.receipt={context,outcome,proof};state.held=null;
  if(loseRelease){loseRelease=false;throw Error('Lost owner acknowledgement');}
 };
 const participant={id,managedFiles:{version:1,preservesCanonical:true},async acquire(context){
  calls.push({operation:'acquire',context});if(acquire)return acquire(context);state.held=structuredClone(context);
  if(loseAcquire){loseAcquire=false;throw Error('Lost acquire acknowledgement');}
  return {ownerId:id,fenceId:context.fenceId,release:(outcome,proof)=>release(context,outcome,proof),async inspectManagedFilesReferences(args){assert.ok(state.held);calls.push({operation:'references',args});return references??{coverage:'complete',protected:[],omissions:[]};}};
 },reconcileRelease:async({outcome,proof,...context})=>release(context,outcome,proof)};
 return {participant,state,calls};
}
async function fixture(t,owners){const directory=await mkdtemp(join(tmpdir(),'managed-protection-'));let receipt=null;t.after(()=>rm(directory,{recursive:true,force:true}));return {directory,create:(instanceId='old')=>createManagedFilesProtection({directory,instanceId,dataScope:'scope',participants:owners.map(o=>o.participant),readEffectReceipt:async(s,c)=>{assert.equal(s,session);assert.equal(c,'effect');return receipt;}}),setReceipt:r=>receipt=r};}
test('exact reviewed allocation and complete family are inspected only under all owned gates',async t=>{
 const a=owner('actual:resources'),b=owner('actual:operations'),f=await fixture(t,[a,b]),p=f.create();
 try{const mutable=structuredClone(input),lease=await p.acquire(mutable);mutable.allocation.treeHash='d'.repeat(64);assert.deepEqual(lease.ownerIds,['actual:resources','actual:operations']);assert.deepEqual(b.calls.find(c=>c.operation==='references').args,{sessions:[session,child],limit:101,allocation});assert.ok(a.state.held&&b.state.held);f.setReceipt(effect());await lease.release();assert.equal(p.receipt('effect').state,'released');assert.equal(a.state.held,null);assert.equal(a.state.receipt.proof.outcome,'unchanged');assert.match(a.state.receipt.proof.receiptId,/^managed-files:/);}finally{p.close();}
});
test('retention-only marker is insufficient, and invalid selection never acquires owners',async t=>{
 const a=owner('owner'),f=await fixture(t,[a]),p=f.create();
 try{for(const bad of [{...input,operation:'hide'},{...input,descendants:{}},{...input,descendants:[session]},{...input,path:'/untrusted'},{...input,allocation:{...allocation,executionDirectory:'/owned/../else'}}])await assert.rejects(p.acquire(bad),e=>e.data.executed===false);assert.equal(a.calls.length,0);}finally{p.close();}
 delete a.participant.managedFiles;a.participant.retentionHide={version:1};const q=f.create();try{await assert.rejects(q.acquire(input),/coverage is incomplete/);assert.equal(a.calls.length,0);}finally{q.close();}
});
test('future work, external-file references and partial coverage refuse before native effect',async t=>{
 for(const references of [{coverage:'complete',protected:[{session:child,reasons:['future-schedule']}],omissions:[]},{coverage:'complete',protected:[{session,reasons:['retained-file-reference']}],omissions:[]},{coverage:'partial',protected:[],omissions:['unknown-external-files']}]){
  const a=owner('resources'),b=owner('operations',{references}),f=await fixture(t,[a,b]),p=f.create();
  try{await assert.rejects(p.acquire(input),e=>e.data.executed===false&&e.data.protectionState==='refused');assert.equal(p.receipt('effect').state,'refused');assert.deepEqual(a.state.receipt.proof,{kind:'admission-refused'});assert.equal(a.state.held,null);assert.deepEqual(p.receipt('effect').owners[1].references,references);}finally{p.close();}
 }
});
test('unknown effects retain gates on restart; changed review/allocation/hide proof never reopens them',async t=>{
 const a=owner('resources'),f=await fixture(t,[a]);let p=f.create();
 try{const lease=await p.acquire(input);f.setReceipt(effect({status:'unknown'}));await assert.rejects(lease.release(),/not conclusively settled/);assert.ok(a.state.held);p.close();p=f.create('new');
  await assert.rejects(p.acquire({...input,commandId:'new-effect'}),/not settled/);await assert.rejects(p.acquire({...input,reviewHash:'d'.repeat(64)}),/different review/);
  for(const changed of [{operation:'hide',status:'hidden'},{session:child},{preservesCanonical:false},{reviewHash:'d'.repeat(64)},{allocation:{...allocation,treeHash:'d'.repeat(64)}},{status:'refused'}]){f.setReceipt(effect(changed));await assert.rejects(p.reconcile('effect'),/not conclusively settled/);assert.ok(a.state.held);}
  f.setReceipt(effect({status:'refused',executed:false}));await p.reconcile('effect');assert.equal(a.state.held,null);assert.equal(a.state.receipt.proof.outcome,'ready');assert.equal(a.calls.filter(c=>c.operation==='acquire').length,1);
 }finally{p.close();}
});
test('lost owner release preserves exact first proof across new coordinator instances',async t=>{
 const a=owner('resources',{loseRelease:true}),f=await fixture(t,[a]);let p=f.create();
 try{const lease=await p.acquire(input);f.setReceipt(effect());await assert.rejects(lease.release(),/Unconfirmed/);const original=structuredClone(a.state.receipt.proof);p.close();p=f.create('new');
  f.setReceipt(effect({status:'refused',executed:false}));await assert.rejects(p.reconcile('effect'),/not conclusively settled/);
  f.setReceipt(effect({protectionRelease:'unconfirmed',projection:'completed'}));await p.reconcile('effect');assert.deepEqual(a.state.receipt.proof,original);assert.equal(p.receipt('effect').state,'released');assert.equal(a.calls.filter(c=>c.operation==='acquire').length,1);
 }finally{p.close();}
});
test('lost owner acquisition is retained; only the exact host no-effect receipt can reconcile it',async t=>{
 const a=owner('first'),b=owner('second',{loseAcquire:true}),f=await fixture(t,[a,b]);let p=f.create();
 try{await assert.rejects(p.acquire(input),e=>e.data.executed===false&&e.data.protectionState==='unknown');assert.ok(a.state.held&&b.state.held);p.close();p=f.create();f.setReceipt(effect({status:'refused',executed:false}));await p.reconcile('effect');assert.equal(a.state.held,null);assert.equal(b.state.held,null);assert.equal(b.calls.filter(c=>c.operation==='acquire').length,1);}finally{p.close();}
});
test('actual facade lease stays excluded through uncertain native work and requires exact persisted release',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'managed-actual-gate-'));t.after(()=>rm(dir,{recursive:true,force:true}));let gate=new FacadeFence({directory:join(dir,'facade'),id:'application-updates',managedFiles:true});const f=await fixture(t,[{participant:gate.participant}]);let p=f.create();
 try{const lease=await p.acquire(input);await assert.rejects(gate.run(false,()=>42),/intake is closed/);f.setReceipt(effect({status:'unknown'}));await assert.rejects(lease.release());p.close();gate.close();gate=new FacadeFence({directory:join(dir,'facade'),id:'application-updates',managedFiles:true});p=createManagedFilesProtection({directory:f.directory,instanceId:'new',dataScope:'scope',participants:[gate.participant],readEffectReceipt:async()=>effect()});await p.reconcile('effect');assert.equal(await gate.run(false,()=>42),42);assert.equal(p.receipt('effect').state,'released');}finally{p.close();gate.close();}
});
test('one process owns the coordinator journal; OS death releases ownership without clearing unknown work',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'managed-process-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 const module=new URL('../src/managed-files-protection.js',import.meta.url).href;
 const code=`import {createManagedFilesProtection} from ${JSON.stringify(module)};const p=createManagedFilesProtection({directory:process.argv[1],instanceId:'old',dataScope:'scope',participants:[{id:'owner',managedFiles:{version:1,preservesCanonical:true},async acquire(c){return {ownerId:'owner',fenceId:c.fenceId,release:async()=>{},inspectManagedFilesReferences:async()=>({coverage:'complete',protected:[],omissions:[]})}},reconcileRelease:async()=>{}}],readEffectReceipt:async()=>null});await p.acquire(${JSON.stringify(input)});console.log('held');setInterval(()=>void p,1000);`;
 const childProcess=spawn(process.execPath,['--input-type=module','-e',code,directory],{stdio:['ignore','pipe','pipe']});t.after(()=>childProcess.kill('SIGKILL'));await once(childProcess.stdout,'data');
 const config={directory,instanceId:'new',dataScope:'scope',participants:[owner('owner').participant],readEffectReceipt:async()=>null};assert.throws(()=>createManagedFilesProtection(config),/locked/);childProcess.kill('SIGKILL');await once(childProcess,'exit');const p=createManagedFilesProtection(config);try{assert.equal(p.receipt('effect').state,'unknown');await assert.rejects(p.acquire({...input,commandId:'second'}),/not settled/);}finally{p.close();}
});

test('25k completed receipts do not participate in unfinished-work lookup',async t=>{
 const {DatabaseSync}=await import('node:sqlite'),a=owner('owner'),f=await fixture(t,[a]);let p=f.create();p.close();
 const db=new DatabaseSync(join(f.directory,'protection.sqlite3'));try{db.exec('BEGIN');const insert=db.prepare('INSERT INTO protections VALUES(?,?,?)');for(let i=0;i<25000;i++)insert.run('old-'+i,'released','{}');db.exec('COMMIT');
  for(const query of ["SELECT body FROM protections INDEXED BY protection_unfinished WHERE state NOT IN ('released','refused') LIMIT 2","SELECT 1 FROM protections INDEXED BY protection_unfinished WHERE state NOT IN ('released','refused') LIMIT 1"]){const plan=JSON.stringify(db.prepare('EXPLAIN QUERY PLAN '+query).all());assert.match(plan,/protection_unfinished/);assert.doesNotMatch(plan,/SCAN protections(?:"|$)/);}
 }finally{db.close();}
 p=f.create();try{const held=await p.acquire(input);f.setReceipt(effect());await held.release();assert.equal(p.receipt('effect').state,'released');}finally{p.close();}
});
