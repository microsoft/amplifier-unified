import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,realpath,rm,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {bindRuntimeOwners,runtimeOwnerProvenance} from '../src/runtime-owners.js';
import {composeQuiescence} from '../src/quiescence.js';
import {createConfiguredStorageInventory} from '../src/storage-inventory.js';
import {createDistribution} from '../src/index.js';
import {createManualIngressGate} from '@amplifier/unified-distribution-update-owner';

const owner=()=>({id:'manual-preview-ingress',acquire:async()=>{throw Error('Inventory cannot acquire a lease');}});
const component={'@amplifier/unified-distribution-update-owner':{version:'0.15.0',revision:'a'.repeat(40)}};
const binding=(participant,directory)=>({owner:participant,storage:{packageName:'@amplifier/unified-distribution-update-owner',packageVersion:'0.15.0',revision:'a'.repeat(40),configKey:'manualIngress',rootRole:'service-ingress',stateDirectory:directory}});
async function fixture(){const root=await realpath(await mkdtemp(join(tmpdir(),'runtime-owner-')));await mkdir(join(root,'state'));await mkdir(join(root,'ingress'));return {root,config:{account:'owned',stateDirectory:join(root,'state'),engines:[],manualIngress:{stateDirectory:join(root,'ingress')}},close:()=>rm(root,{recursive:true,force:true})};}

test('runtime-only participant joins the full census without invented topics or operation markers',()=>{
 const participant=owner(),bindings=bindRuntimeOwners([binding(participant,'/owned/ingress')]);
 const graph=composeQuiescence({instanceId:'i',dataScope:'s'},[],{runtimeOwners:bindings.map(b=>b.owner)});
 assert.deepEqual(graph.requiredOwners,[participant.id]);assert.equal(graph.participants[0],participant);
 assert.deepEqual(graph.coverage.capabilities,{});assert.deepEqual(graph.coverage.nativeHostOwners,[participant.id]);
 assert.equal(graph.participants[0].serviceStop,undefined);assert.equal(graph.participants[0].managedFiles,undefined);
 assert.throws(()=>bindRuntimeOwners([bindings[0],bindings[0]]),/distinct/);
 assert.throws(()=>composeQuiescence({instanceId:'i',dataScope:'s'},[{manifest:{topics:{ingress:{}}},quiescenceParticipant:participant}],{runtimeOwners:[participant]}),/Conflicting/);
});

test('matched provenance still requires explicit external root capture; caller none cannot hide it',async()=>{
 const f=await fixture();try{
  const participant=owner(),bindings=bindRuntimeOwners([binding(participant,f.config.manualIngress.stateDirectory)]);
  const verified=await runtimeOwnerProvenance(bindings,f.config,component);
  assert.deepEqual(verified.omissions,[]);
  const options={namespace:'s',quiescence:{requiredOwners:[participant.id]},components:component,...verified};
  const missing=createConfiguredStorageInventory(f.config,{...options,externalCoverage:{[participant.id]:'none'}});
  assert.equal(missing.completeEligible,false);assert.ok(missing.omissions.some(o=>o.id==='runtime-root:'+participant.id));
  const complete=createConfiguredStorageInventory(f.config,{...options,externalCoverage:{[participant.id]:'declared'},externalRoots:[{id:'ingress',ownerIds:[participant.id],path:f.config.manualIngress.stateDirectory,coverage:'authoritative',capture:'tree'}]});
  assert.equal(complete.completeEligible,true);assert.equal(complete.owners[0].revision,'a'.repeat(40));
 }finally{await f.close();}
});

test('missing, changed version/revision/config and mismatched root remain incomplete',async()=>{
 const f=await fixture();try{
  for(const patch of [undefined,{revision:'b'.repeat(40)},{packageVersion:'0.14.0'},{configKey:'absent'},{stateDirectory:f.config.stateDirectory}]){
   const participant=owner(),b=binding(participant,f.config.manualIngress.stateDirectory);b.storage=patch===undefined?undefined:{...b.storage,...patch};
   const verified=await runtimeOwnerProvenance(bindRuntimeOwners([b]),f.config,component);
   const result=createConfiguredStorageInventory(f.config,{namespace:'s',quiescence:{requiredOwners:[participant.id]},components:component,...verified,externalCoverage:{[participant.id]:'none'}});
   assert.equal(result.completeEligible,false);assert.equal(result.owners[0].revision,'unresolved');assert.ok(result.omissions.some(o=>o.id==='runtime-provenance:'+participant.id));
  }
 }finally{await f.close();}
});

test('actual composition registers launcher infrastructure and leaves lifecycle with its caller',async()=>{
 const f=await fixture();let app;try{
  const participant=owner(),config={...f.config,defaultWorkspace:f.root,allowedWorkspaceRoots:[f.root],webDirectory:f.root,quiescence:{instanceId:'i',dataScope:'s'},engines:[{id:'unused',command:process.execPath,args:['-e','throw Error("never start")']}]};
  await assert.rejects(createDistribution({...config,quiescence:undefined},{runtimeOwnerBindings:[{owner:participant}]}),/require configured/);
  app=await createDistribution(config,{runtimeOwnerBindings:[{owner:participant}]});
  assert.ok(app.quiescence.requiredOwners.includes(participant.id));assert.ok(app.quiescence.participants.includes(participant));
  assert.equal(Object.keys(app.capabilities.manifest.topics).includes(participant.id),false);
  const inventory=await app.storageInventory();assert.ok(inventory.owners.some(o=>o.id===participant.id&&o.revision==='unresolved'));assert.equal(inventory.completeEligible,false);
  assert.equal(app.host.diagnostics().activeAgents,0);
 }finally{await app?.close();await f.close();}
});

test('installed ingress retains its own authentic markers, canonical source and private storage scope',async()=>{
 const f=await fixture();let app,gate;try{
  f.config.manualIngress.stateDirectory=join(f.config.manualIngress.stateDirectory,'gate');
  gate=await createManualIngressGate({directory:f.config.manualIngress.stateDirectory,id:'manual-preview-ingress'});
  const components=JSON.parse(await readFile(new URL('../components.json',import.meta.url),'utf8')).components;
  const source=components['@amplifier/unified-distribution-update-owner'],b=binding(gate.participant,f.config.manualIngress.stateDirectory);
  b.storage.packageVersion=source.version;b.storage.revision=source.revision;
  const config={...f.config,defaultWorkspace:f.root,allowedWorkspaceRoots:[f.root],webDirectory:f.root,quiescence:{instanceId:'i',dataScope:'s'},engines:[{id:'unused',command:process.execPath,args:['-e','throw Error("never start")']}]};
  app=await createDistribution(config,{runtimeOwnerBindings:[b]});
  assert.equal(app.quiescence.participants.find(p=>p.id===gate.participant.id),gate.participant);
  assert.deepEqual(gate.participant.managedFiles,{version:1,preservesCanonical:true});
  const missing=await app.storageInventory();assert.ok(missing.omissions.some(o=>o.id==='runtime-root:'+gate.participant.id));
  const inventory=await app.storageInventory({externalCoverage:{[gate.participant.id]:'declared'},externalRoots:[{id:'ingress',ownerIds:[gate.participant.id],path:f.config.manualIngress.stateDirectory,coverage:'authoritative',capture:'tree'}]});
  const declared=inventory.owners.find(o=>o.id===gate.participant.id);assert.equal(declared.revision,source.revision);assert.deepEqual(declared.rootIds,['application','ingress']);
  assert.equal(inventory.omissions.some(o=>o.id.includes(gate.participant.id)),false);
  assert.deepEqual(gate.inspect(),{active:0,held:null});assert.equal(app.host.diagnostics().activeAgents,0);
  await app.close();assert.deepEqual(gate.inspect(),{active:0,held:null},'The launcher still owns the gate lifetime');
 }finally{await app?.close();gate?.close();await f.close();}
});
