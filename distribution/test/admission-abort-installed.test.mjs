import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomBytes} from 'node:crypto';

// This is an explicit installed-candidate lane. The released Host does not yet
// have the new contract. Never silently substitute a source overlay or mock Host.
const installed=process.env.ADMISSION_ABORT_INSTALLED_DISTRIBUTION;
const load=path=>import(pathToFileURL(join(installed,path)).href);
const a={id:'one',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)};
const b={id:'two',version:'2.0.0',revision:'b'.repeat(40),digest:'b'.repeat(64)};
const original={identity:a,instanceId:'original',dataScope:'owned',ready:true};
const candidate=identity=>({identity,handle:identity.id});

for(const variant of ['acquired','not-acquired','lost-reply','unsupported-owner'])
test(`installed Host, supervisor and Node owners reconcile failed preparation: ${variant}`,{skip:!installed,timeout:15000},async()=>{
 const {createHost}=await load('node_modules/@amplifier/unified-host/dist/index.js');
 const api=await load('node_modules/@amplifier/unified-distribution-update-owner/dist/index.js');
 const {composeQuiescence}=await load('src/quiescence.js');
 const {createApplicationUpdateCapabilities}=await load('src/application-updates.js');
 const directory=await realpath(await mkdtemp(join(tmpdir(),'installed-admission-abort-')));
 let host,hostServer,hostClient,owner,server,client,facade,ingress,leave;
 let acquisitions=0,restarts=0,aborts=0,lose=variant==='lost-reply';
 const key=randomBytes(32).toString('hex'),supervisorKey=randomBytes(32).toString('hex');
 try{
  await writeFile(join(directory,'history-sentinel'),'original uncertain input\n');
  ingress=await api.createManualIngressGate({directory:join(directory,'ingress'),id:'manual-ingress'});
  owner=new api.DistributionUpdateOwner({directory:join(directory,'supervisor'),dataScope:'owned',initial:candidate(a),preferences:{autoCheck:false,autoInstall:false,intervalMs:60000},
   releases:{check:async()=>({releases:[a,b],recommendedId:b.id}),prepare:async identity=>candidate(identity),verify:async()=>true},
   lifecycle:{inspect:()=>hostClient.inspect(),admitRestart:request=>hostClient.admitRestart(request),restart:async()=>{restarts++;throw Error('must not restart');},
    inspectAdmissionFence:id=>hostClient.inspectAdmissionFence(id),inspectAdmissionAbort:id=>hostClient.inspectAdmissionAbort(id),
    abortAdmission:async request=>{aborts++;const receipt=await hostClient.abortAdmission(request);if(lose){lose=false;throw Error('Injected lost reply after actual Host commit');}return receipt;}}});
  server=await api.serveSupervisor({owner,token:supervisorKey});
  client=new api.SupervisorClient({url:server.url,token:supervisorKey});
  facade=createApplicationUpdateCapabilities({supervisor:client,authorize:async()=>{},directory:join(directory,'facade')});
  const faulted={...ingress.participant,acquire:async context=>{acquisitions++;await ingress.participant.acquire(context);throw Error('Injected acquisition acknowledgement loss');}};
  if(variant==='unsupported-owner')delete faulted.abortAdmission;
  host=await createHost({stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[directory],engines:[{id:'unused',command:process.execPath,args:['-e','throw Error("must not launch engine")']}],
   quiescence:composeQuiescence({instanceId:original.instanceId,dataScope:original.dataScope,timeoutMs:3000},[facade],{
    nativeHost:false,runtimeOwners:[faulted],verifyRelease:async()=>{throw Error('generic release is not abort authority');},
    verifyAdmissionAbort:api.createHostAdmissionAbortVerifier({supervisor:client.owner,inspectRunning:()=>original})})});
  hostServer=await api.serveHostControl({host,inspectRunning:()=>original,token:key});
  hostClient=new api.HostControlClient({dataScope:'owned',connect:()=>({url:hostServer.url,token:key,dataScope:'owned'})});
  owner.check('check');await owner.waitFor('check');owner.prepare('prepare',b.id);await owner.waitFor('prepare');
  if(variant==='not-acquired')leave=ingress.enter();
  owner.activate('update',{preparedCommandId:'prepare',targetDigest:b.digest,expectedCurrentId:a.id});
  const failed=await owner.waitFor('update');assert.equal(failed.status,'unknown');assert.equal(failed.phase,'admission_requested');
  const before=owner.inspect(),held=host.inspectQuiescence();assert.equal(held.intakeClosed,true);assert.equal(held.fence.admissionStage,'participants');
  await assert.rejects(host.withExternalMutation('application-updates',async()=>assert.fail('must not run')),/intake is closed/);
  leave?.();leave=undefined;
  const reconcile=()=>facade.action({version:1,topic:'application-updates',channel:'host',operation:'updates.application.reconcile',args:{commandId:'update'}},{});
  const response=await reconcile();assert.equal(response.result.replayed,false);
  if(variant==='unsupported-owner'){
   assert.equal(response.result.receipt.status,'unknown');assert.equal(host.inspectQuiescence().intakeClosed,true);assert.ok(ingress.inspect().held);
   assert.equal(host.quiescenceAdmissionAbortReceipt('update'),undefined);
  }else{
   if(variant==='lost-reply'){assert.equal(response.result.receipt.status,'unknown');await reconcile();}
   assert.equal(owner.receipt('update').phase,'admission_aborted');assert.equal(owner.receipt('update').status,'failed');
   assert.equal(owner.inspect().restartUnresolved,false);assert.equal(host.inspectQuiescence().intakeClosed,false);
   const receipt=host.quiescenceAdmissionAbortReceipt('update');
   assert.deepEqual(receipt.owners.map(o=>[o.ownerId,o.status]),[['manual-ingress',variant==='not-acquired'?'not-acquired':'released'],['application-updates','released']]);
   await host.withExternalMutation('application-updates',async()=>{});
   assert.equal(ingress.inspect().held,null);await reconcile();assert.equal(aborts,1);
  }
  assert.equal(acquisitions,1);assert.equal(restarts,0);assert.equal(host.diagnostics().activeAgents,0);
  for(const field of ['current','previous','staged'])assert.deepEqual(owner.inspect()[field],before[field]);
  assert.equal(await readFile(join(directory,'history-sentinel'),'utf8'),'original uncertain input\n');
 }finally{
  leave?.();await facade?.close();client?.close();hostClient?.close();await hostServer?.close();await host?.close();
  await server?.close();await owner?.close();ingress?.close();await rm(directory,{recursive:true,force:true});
 }
});
