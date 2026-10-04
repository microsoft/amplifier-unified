import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createStoppedStateQualifier} from '../dist/index.js';
const digest='a'.repeat(64);
async function fixture(t){
 const path=await realpath(await mkdtemp(join(tmpdir(),'stopped-aggregate-')));
 t.after(()=>rm(path,{recursive:true,force:true}));
 const roots=[{id:'application',path}],bindings=[{id:'application',path,kind:'directory'}];
 const options={
 predecessor:{installationId:'i',ownerId:'o',dataScope:'s',instanceId:'old',releaseDigest:digest},
 witness:{unit:'failed-fixture.service',pid:123,bootId:'boot',startTicks:'1',invocationId:'a'.repeat(32),cgroup:'/fixture',unitDigest:digest},
 predecessorBindings:bindings,successorBindings:bindings,roots,expectedOwners:[],inspectors:[],
 inspectWriterExclusion:async()=>({schema:'stopped-writer-exclusion-v1',coverage:'complete',activeWriters:0,publicReadyObserved:false,admissionObserved:false,digest}),
 inspectStorageInventory:async()=>({schema:'stopped-storage-inventory-v1',coverage:'complete',roots,digest}),
 };
 for(let n=0;n<20;n++){
  const ownerId='owner-'+String(n).padStart(2,'0'),artifact={packageName:'@fixture/owner-'+n,version:'1.0.0',revision:digest,digest};
  const historicalUnknown={count:n===0?5:0,digest};
  const v={schema:'owner-stopped-state-v1',ownerId,artifact,roots,coverage:'complete',
   activeWriters:0,pendingEffects:0,unaccountedEffects:0,historicalUnknown,admissionObserved:false,evidenceDigest:digest};
  options.expectedOwners.push(ownerId);options.inspectors.push({ownerId,artifact,roots,historicalBaseline:historicalUnknown,inspectStoppedState:async()=>structuredClone(v)});
 }
 return options;
}
test('twenty owner aggregate recomputes read-only evidence and preserves historical unknowns',async t=>{
 const o=await fixture(t),calls=[];
 for(const i of o.inspectors){const f=i.inspectStoppedState;i.inspectStoppedState=async()=>{calls.push(i.ownerId);return f();};}
 const q=createStoppedStateQualifier(o),one=await q(),two=await q();
 assert.deepEqual(one,two);assert.equal(one.historicalUnknown.count,5);assert.equal(one.unaccountedEffects,0);
 assert.equal(calls.length,40);assert.equal(one.owners.length,20);
});
test('owner inspections are concurrent without bypassing writer and inventory checks',async t=>{
 const o=await fixture(t);let started=0;let release;
 const allStarted=new Promise(r=>release=r);
 for(const i of o.inspectors){const f=i.inspectStoppedState;i.inspectStoppedState=async()=>{started++;if(started===20)release();await allStarted;return f();};}
 const q=createStoppedStateQualifier(o);await q();assert.equal(started,20);
});
test('active restored service refuses before any owner reader',async t=>{
 const o=await fixture(t);let count=0;
 o.inspectWriterExclusion=async()=>({schema:'stopped-writer-exclusion-v1',coverage:'complete',activeWriters:1,publicReadyObserved:false,admissionObserved:false,digest});
 o.inspectors[0].inspectStoppedState=async()=>{count++;throw Error('must not inspect');};
 await assert.rejects(createStoppedStateQualifier(o)(),/writers_unqualified/);assert.equal(count,0);
});
test('missing or duplicate owner and uncovered roots fail closed',async t=>{
 for(const kind of ['missing','duplicate','unowned','binding']){
  const o=await fixture(t);
  if(kind==='missing')o.inspectors.pop();
  if(kind==='duplicate')o.inspectors[1]=o.inspectors[0];
  if(kind==='unowned')o.roots=[...o.roots,{id:'new-root',path:join(o.roots[0].path,'extra')}];
  if(kind==='binding')o.successorBindings=[{id:'outside',kind:'directory',path:join(o.roots[0].path,'outside')}];
  assert.throws(()=>createStoppedStateQualifier(o),/owner_census|root_unowned|binding_uncovered/);
 }
});
test('artifact, roots, active effects, coverage, and historical changes refuse',async t=>{
 for(const [key,value] of [
  ['artifact',{packageName:'wrong',version:'1',revision:digest,digest}],['roots',[{id:'wrong',path:'/wrong'}]],
  ['coverage','unsupported'],['activeWriters',1],['pendingEffects',1],['unaccountedEffects',1],
  ['admissionObserved',true],['historicalUnknown',{count:4,digest}],['historicalUnknown',{count:5,digest:'b'.repeat(64)}],
  ['evidenceDigest','not-a-digest'],
 ]){
  const o=await fixture(t),f=o.inspectors[0].inspectStoppedState;
  o.inspectors[0].inspectStoppedState=async()=>({...await f(),[key]:value});
  await assert.rejects(createStoppedStateQualifier(o)(),/owner_unqualified|historical_evidence_changed/);
 }
});
test('inventory or writer-state drift during inspection refuses',async t=>{
 for(const key of ['inspectStorageInventory','inspectWriterExclusion']){
  const o=await fixture(t),f=o[key];let n=0;
  o[key]=async()=>({...await f(),digest:(n++?'b':'a').repeat(64)});
  await assert.rejects(createStoppedStateQualifier(o)(),/changed_during_inspection/);
 }
});
test('owner exceptions refuse; no fallback or retries',async t=>{
 const o=await fixture(t);let calls=0;
 o.inspectors[0].inspectStoppedState=async()=>{calls++;throw Error('inspection_unsupported');};
 await assert.rejects(createStoppedStateQualifier(o)(),/inspection_unsupported/);assert.equal(calls,1);
});
test('copied receipt cannot replace an actual inspector function',async t=>{
 const o=await fixture(t);o.inspectors[0].inspectStoppedState={coverage:'complete'};
 await assert.rejects(createStoppedStateQualifier(o)(),/not a function/);
});
