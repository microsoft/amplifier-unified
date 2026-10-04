import assert from 'node:assert/strict';
import {mock} from 'node:test';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

// Exercise createDistribution's actual composition and failure cleanup without
// opening a real native process or giving the fixture any business authority.
const root=await mkdtemp(join(tmpdir(),'message-admission-composition-'));
let mode,options,closes;
mock.module('@amplifier/unified-native-capabilities',{namedExports:{
 AdminConnection:class {quiescenceParticipant={id:'native-admin'};async close(){closes.admin++;}},
 createNativeCapabilities:()=>({
  negotiateBundleCommands:async()=>{},
  close:async()=>{closes.native++;},
 }),
 createPermissionsCapabilities:()=>{throw Error('Unexpected permissions construction');},
 createMessageCapabilities:value=>{
  options=value;
  if(mode==='construction')throw Error('Fixture bridge construction failed');
  return {connection:{close:async()=>{closes.messages++;}},capabilities:{negotiate:async()=>{throw Error('Fixture bridge negotiation failed');}}};
 },
}});
try{
 const {createDistribution}=await import('../../src/index.js');
 for(mode of ['construction','negotiation']){
  closes={admin:0,native:0,messages:0,prior:0};
  const stateDirectory=join(root,mode,'application'),config={
   account:'fixture-account',stateDirectory,defaultWorkspace:root,allowedWorkspaceRoots:[root],webDirectory:root,
   engines:[{id:'native',command:'never-started',admissionDirectory:join(root,'engine-spoof')}],
   nativeAdmin:{engine:'native',admissionDirectory:join(root,'admin-spoof')},
  };
  await assert.rejects(createDistribution(config,{capabilityOwners:[{close:async()=>{closes.prior++;}}]}),new RegExp('Fixture bridge '+mode+' failed'));
  assert.equal(options.admissionDirectory,join(stateDirectory,'capabilities','native-messages'));
  assert.equal(config.engines[0].admissionDirectory,join(root,'engine-spoof'),'Composition does not mutate engine settings');
  assert.deepEqual(closes,{admin:1,native:1,messages:mode==='negotiation'?1:0,prior:1});
 }
 console.log('message-admission-composition-passed');
}finally{mock.restoreAll();await rm(root,{recursive:true,force:true});}
