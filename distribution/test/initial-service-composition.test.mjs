import {test} from 'node:test';
import assert from 'node:assert/strict';
// Owner/listener seam only: production verifier and consumed-claim inspection
// remain the actual installed update-owner module supplied by the test runner.
import {composeServiceLifecycle,startConfiguredDistribution} from '../src/launch.js';
const target={id:'qualified',version:'1.0.0',revision:'b'.repeat(40),digest:'a'.repeat(64)};
const binding={installationId:'installation',ownerId:'owner'};
const env={AMPLIFIER_DISTRIBUTION_INSTALLATION_ID:binding.installationId,AMPLIFIER_DISTRIBUTION_OWNER_ID:binding.ownerId};
const runtime={instanceId:'actual-initial',dataScope:'data',identity:target,inspectRunning:async()=>({instanceId:'actual-initial',dataScope:'data',identity:target,ready:true})};
const installation={...binding,dataScope:'data',initial:target,initialInstanceId:runtime.instanceId,initialCommandId:'original-consumed-claim'};

test('initial composition binds only the consumed original generation and full signed target',async()=>{
 const saved={installation:process.env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID,owner:process.env.AMPLIFIER_DISTRIBUTION_OWNER_ID};
 process.env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID=binding.installationId;process.env.AMPLIFIER_DISTRIBUTION_OWNER_ID=binding.ownerId;
 let proof;const service={proof:async()=>proof};
 try{
  const lifecycle=composeServiceLifecycle(binding,runtime,{service},env,{installation});
  assert.deepEqual(lifecycle.initialStart,{commandId:installation.initialCommandId,identity:lifecycle.identity});
  assert.equal(typeof lifecycle.verifyInitialStart,'function');
  const request={purpose:'initial-start',commandId:installation.initialCommandId,fenceId:'original-fence',instanceId:runtime.instanceId,dataScope:runtime.dataScope,serviceIdentity:lifecycle.identity,evidence:{verified:true}};
  await assert.rejects(lifecycle.verifyInitialStart(request));
  proof={operation:'start',status:'ready',phase:'ready',commandId:request.commandId,fenceId:request.fenceId,expected:lifecycle.identity,observed:lifecycle.identity,initialClaim:{kind:'pristine-installation',commandId:request.commandId,...lifecycle.identity,targetDigest:target.digest},activation:{schema:'distribution-service-activation-v1',target}};
  const verified=await lifecycle.verifyInitialStart(request);assert.equal(verified.kind,'service-initial-start');assert.equal(verified.claimReceiptId,request.commandId);assert.deepEqual(verified.target,target);
  for(const changed of [{installationId:'other'},{dataScope:'other'},{initialCommandId:undefined},{initial:{...target,revision:'other'}},{initial:{...target,id:'other'}},{initial:{...target,version:'other'}},{initial:{...target,digest:'b'.repeat(64)}}])assert.throws(()=>composeServiceLifecycle(binding,runtime,{service},env,{installation:{...installation,...changed}}),/Initial installation/);
  const replacement=composeServiceLifecycle(binding,{...runtime,instanceId:'actual-B'},{service},env,{installation});assert.equal(replacement.initialStart,undefined);assert.equal(replacement.verifyInitialStart,undefined);assert.equal(typeof replacement.verifyRelease,'function');
  const legacy=composeServiceLifecycle(binding,runtime,{service},env);assert.equal(legacy.initialStart,undefined);
 }finally{for(const [key,val] of [['AMPLIFIER_DISTRIBUTION_INSTALLATION_ID',saved.installation],['AMPLIFIER_DISTRIBUTION_OWNER_ID',saved.owner]]){if(val===undefined)delete process.env[key];else process.env[key]=val;}}
});

test('selected Linux profile refuses missing consumed authority before listeners or owners',async()=>{
 const prior={profile:process.env.AMPLIFIER_DISTRIBUTION_LIFECYCLE,authority:process.env.AMPLIFIER_DISTRIBUTION_PROVISIONING_AUTHORITY};process.env.AMPLIFIER_DISTRIBUTION_LIFECYCLE='linux-user-unit';delete process.env.AMPLIFIER_DISTRIBUTION_PROVISIONING_AUTHORITY;
 try{
  await assert.rejects(startConfiguredDistribution({gateway:{host:'127.0.0.1'},supervision:{discoveryFile:'/owned/supervisor.json',hostControl:{discoveryFile:'/owned/host.json',tokenFile:'/owned/host-token'},serviceLifecycle:binding}}),/owned provisioning authority/);
 }finally{for(const [key,val] of [['AMPLIFIER_DISTRIBUTION_LIFECYCLE',prior.profile],['AMPLIFIER_DISTRIBUTION_PROVISIONING_AUTHORITY',prior.authority]]){if(val===undefined)delete process.env[key];else process.env[key]=val;}}
});
