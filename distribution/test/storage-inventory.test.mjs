import test from 'node:test';
import assert from 'node:assert/strict';
import {createStorageInventory,validateStorageInventory,storageInventoryDigest} from '../src/storage-inventory.js';
const base=()=>({namespace:'owned',account:'test',applicationStateDirectory:'/owned/application',owners:[{id:'resources',schemaVersion:1,revision:'a'.repeat(40),participantId:'resources',rootIds:['app'],externalStorage:'none'}],roots:[{id:'app',ownerIds:['resources'],path:'/owned/application',coverage:'authoritative',capture:'tree'}]});
const resign=v=>{delete v.digest;v.digest=storageInventoryDigest(v);return v;};
test('canonical private inventory is stable and independent of property insertion order',()=>{
 const v=createStorageInventory(base());assert.equal(v.completeEligible,true);assert.deepEqual(validateStorageInventory(v),v);
 const reversed=Object.fromEntries(Object.entries(v).reverse());assert.equal(validateStorageInventory(reversed).digest,v.digest);
 const tampered=structuredClone(v);tampered.account='other';assert.throws(()=>validateStorageInventory(tampered),/digest/);
});
test('unknown owners and uncovered authority remain explicit incomplete snapshots',()=>{
 const b=base();b.owners[0].externalStorage='unresolved';let v=createStorageInventory(b);assert.equal(v.completeEligible,false);
 v.completeEligible=true;assert.throws(()=>validateStorageInventory(resign(v)),/eligibility/);
 const c=base();c.omissions=[{id:'unclassified-engine',reason:'External engine authority was not declared',blocksComplete:true}];assert.equal(createStorageInventory(c).completeEligible,false);
});
test('native coverage requires exact roots and retained external writer evidence',()=>{
 const b=base();b.owners[0].rootIds.push('native');b.owners[0].externalStorage='declared';b.roots.push({id:'native',ownerIds:['resources'],path:'/owned/native',coverage:'authoritative',capture:'native-artifact'});
 assert.equal(createStorageInventory(b).completeEligible,false);
 const a={id:'native-snapshot',engineId:'amplifier',artifactId:'f'.repeat(32),sha256:'a'.repeat(64),manifestDigest:'b'.repeat(64),declaredRootIds:['native'],completeNativeAuthority:true,externalWritersExcluded:true};b.nativeArtifacts=[a];
 assert.throws(()=>createStorageInventory(b),/attestation/);a.externalWriterEvidence={policy:'operator-attested-stopped',cooperativeLeaseHeld:true};assert.equal(createStorageInventory(b).completeEligible,true);
 a.completeNativeAuthority=false;assert.equal(createStorageInventory(b).completeEligible,false);
});
test('overlap, guessed relative paths, duplicate ownership and unexpected fields refuse',()=>{
 const b=base();b.roots[0].path='../application';assert.throws(()=>createStorageInventory(b),/normalized/);
 const c=base();c.owners[0].rootIds.push('nested');c.roots.push({id:'nested',ownerIds:['resources'],path:'/owned/application/private',coverage:'authoritative',capture:'tree'});assert.throws(()=>createStorageInventory(c),/Overlapping/);
 const v=createStorageInventory(base());v.roots[0].ownerIds.push('resources');assert.throws(()=>validateStorageInventory(resign(v)),/Duplicate/);
 const t=createStorageInventory(base());t.autoResume=true;assert.throws(()=>validateStorageInventory(resign(t)),/Unexpected/);
});
test('omitted derived data does not omit authority and the manifest is bounded',()=>{
 const b=base();b.owners[0].rootIds.push('catalog');b.roots.push({id:'catalog',ownerIds:['resources'],path:'/owned/index.sqlite',coverage:'derived-rebuildable',capture:'omit',reason:'Rebuild from native metadata without replay'});assert.equal(createStorageInventory(b).completeEligible,true);
 b.roots[1].coverage='authoritative';assert.equal(createStorageInventory(b).completeEligible,false);
 const c=base();c.owners[0].revision='x'.repeat(2000000);assert.throws(()=>createStorageInventory(c),/1 MiB/);
});

test('configured census retains unknown engines, custom owners and external transfer authority',async()=>{
 const {createConfiguredStorageInventory}=await import('../src/storage-inventory.js');
 const config={account:'a',stateDirectory:'/owned/app',engines:[{id:'native'}],portability:{stageDir:'/external/stage',exchangeDir:'/external/exchange'}};
 const options={namespace:'ns',quiescence:{requiredOwners:['resources','portability']},components:{'@amplifier/unified-resources-capability':{revision:'a'.repeat(40)},'@amplifier/unified-portability-capability':{revision:'b'.repeat(40)}}};
 const v=createConfiguredStorageInventory(config,options);assert.equal(v.completeEligible,false);assert.deepEqual(v.owners.map(o=>o.participantId),['resources','portability']);
 assert.deepEqual(v.omissions.map(o=>o.id),['portability-stage','portability-exchange','engine:native']);
 const unknown=createConfiguredStorageInventory({...config,engines:[],portability:undefined},{...options,quiescence:{requiredOwners:['community-owner']}});
 assert.equal(unknown.owners[0].externalStorage,'unresolved');assert.equal(unknown.completeEligible,false);
 assert.throws(()=>createConfiguredStorageInventory(config,{...options,quiescence:undefined}),/census/);
});

test('real composed participant IDs retain factory provenance instead of guessed owner labels',async()=>{
 const {createConfiguredStorageInventory}=await import('../src/storage-inventory.js');
 const v=createConfiguredStorageInventory({account:'a',stateDirectory:'/owned/app',engines:[]},{namespace:'n',quiescence:{requiredOwners:['capability:canvas','history-import']},components:{'@amplifier/unified-resources-capability':{revision:'a'},'@amplifier/unified-history-capability':{revision:'b'}},ownerProvenance:{'capability:canvas':{packageName:'@amplifier/unified-resources-capability',configKey:'resources'},'history-import':{packageName:'@amplifier/unified-history-capability',configKey:'historyImport'}}});
 assert.equal(v.completeEligible,true);assert.deepEqual(v.owners.map(o=>[o.id,o.participantId,o.revision]),[['capability:canvas','capability:canvas','a'],['history-import','history-import','b']]);
});

test('managed chat allocations outside application state are never silently omitted',async()=>{
 const {createConfiguredStorageInventory}=await import('../src/storage-inventory.js');
 const config={account:'a',stateDirectory:'/owned/app',engines:[],host:{managedSessionRoot:'/owned/managed'}},options={namespace:'n',quiescence:{requiredOwners:['resources']},components:{'@amplifier/unified-resources-capability':{revision:'a'}}};
 assert.ok(createConfiguredStorageInventory(config,options).omissions.some(o=>o.id==='managed-session-files'&&o.blocksComplete));
 assert.equal(createConfiguredStorageInventory({...config,host:{managedSessionRoot:'/owned/app/managed'}},options).completeEligible,true);
 const externalRoots=[{id:'managed-files',ownerIds:['resources'],path:'/owned/managed',coverage:'authoritative',capture:'tree'}];
 assert.equal(createConfiguredStorageInventory(config,{...options,externalRoots}).omissions.some(o=>o.id==='managed-session-files'),false);
});
