import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHost} from '@amplifier/unified-host';
import {composeQuiescence} from '../src/quiescence.js';
import {installProductionDistribution} from '../src/installation.js';

test('admission abort authority is supplied only by the trusted launcher, never JSON configuration',()=>{
 const verify=async()=>{},configured={instanceId:'actual',dataScope:'owned',verifyAdmissionAbort:verify};
 assert.equal(composeQuiescence(configured,[]).verifyAdmissionAbort,undefined);
 assert.equal(composeQuiescence(configured,[],{verifyAdmissionAbort:verify}).verifyAdmissionAbort,verify);
 assert.throws(()=>composeQuiescence(configured,[],{verifyAdmissionAbort:true}),/Trusted/);
});

test('composition carries trusted authority without inventing required service-stop coverage',async()=>{
 const identity={installationId:'install',ownerId:'owner',instanceId:'actual',dataScope:'owned',releaseDigest:'a'.repeat(64)};
 let acquisitions=0;
 const participant={id:'fixture',acquire:async()=>{acquisitions++;throw Error('must not acquire');}};
 const serviceLifecycle={identity,verifyRelease:async()=>{throw Error('not used');}};
 const owner={manifest:{topics:{fixture:{}}},quiescenceParticipant:participant};
 const config=composeQuiescence({instanceId:'actual',dataScope:'owned',serviceLifecycle:{untrusted:true}},[owner],{nativeHost:false,serviceLifecycle,verifyRelease:async()=>{throw Error('not used');}});
 assert.equal(config.serviceLifecycle,serviceLifecycle);assert.equal(config.participants[0].serviceStop,undefined);
 assert.equal(composeQuiescence({instanceId:'actual',dataScope:'owned',serviceLifecycle},[owner]).serviceLifecycle,undefined);
 const directory=await mkdtemp(join(tmpdir(),'service-coverage-'));let host;
 try{
  host=await createHost({stateDirectory:directory,allowedWorkspaceRoots:[directory],engines:[{id:'unused',command:process.execPath,args:['-e','throw Error("must not start")']}],quiescence:config});
  const receipt=await host.admitServiceStop({commandId:'stop',expected:identity});
  // Ordinary shared admission drains admitted work without requiring legacy
  // facade service markers or acquiring facade leases. There is no work here.
  assert.equal(receipt.admitted,true);assert.equal(receipt.reason,undefined);assert.equal(receipt.intakeClosed,true);assert.equal(acquisitions,0);
 }finally{await host?.close();await rm(directory,{recursive:true,force:true});}
});

test('installer service enablement is explicit and refuses caller identity before filesystem allocation',async()=>{
 for(const serviceLifecycle of [{enabled:false},{enabled:true,installationId:'injected'},null,true])
  await assert.rejects(installProductionDistribution({schema:'unified-installation-v1',serviceLifecycle}),/service_lifecycle/);
});
