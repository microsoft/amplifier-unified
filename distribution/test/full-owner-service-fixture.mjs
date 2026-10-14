import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import * as api from '@amplifier/unified-distribution-update-owner';
import {createDistribution} from './index.js';
import {createCountedIngress} from './counted-ingress-fixture.mjs';

const c=JSON.parse(await readFile(process.argv[2],'utf8'));
const source=process.env.UNIFIED_MANUAL_SOURCE==='1';
let ready=false,app,control,access,gate,wrapper,closing;
const diagnostic=async error=>{await writeFile(join(c.root,'fixture-startup-error.txt'),String(error.stack??error),{mode:0o600});throw error;};
const runtime=await api.createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:c.keys,isReady:()=>ready}).catch(diagnostic);
const expected=api.serviceIdentity({installationId:c.installationId,ownerId:c.ownerId,dataScope:runtime.dataScope,instanceId:runtime.instanceId,releaseDigest:runtime.identity.digest});
const supervisor=api.connectSupervisorFileLazy(c.supervisorFile);
const idle=new Set(),mayBeIdle=()=>{for(const callback of idle)callback();};
if(source){
 const observer=api.createLinuxSystemdSourceObserver({unit:c.unit,python:c.observerPython});
 wrapper=await api.createManualSystemdHandoffLauncher({directory:c.sourceDirectory,expected,bindings:c.bindings,observer,
  qualifyCurrent:async()=>{const actual=await runtime.inspectRunning();assert.deepEqual(actual.identity,c.target.identity);return c.target;}});
}
const serviceLifecycle=source?wrapper.serviceLifecycle:{identity:expected,verifyRelease:api.createHostServiceReleaseVerifier({service:supervisor.service,inspectRunningService:()=>api.inspectRuntimeService(runtime)})};
gate=await api.createManualIngressGate({directory:c.application.manualIngress.stateDirectory,id:'manual-preview-ingress',onMayBeIdle:mayBeIdle}).catch(diagnostic);
const components=JSON.parse(await readFile(new URL('../components.json',import.meta.url),'utf8')).components;
const component=components['@amplifier/unified-distribution-update-owner'];assert.equal(component.version,'0.16.1');
const close=()=>{
 if(!closing)closing=(async()=>{ready=false;await control?.close();await access?.close();await app?.close();gate.close();supervisor.close();})();
 return closing;
};
try{
 app=await createDistribution({...c.application,quiescence:{instanceId:runtime.instanceId,dataScope:runtime.dataScope,timeoutMs:30000}},{
  serviceLifecycle,applicationUpdateSupervisor:supervisor,onMayBeIdle:mayBeIdle,
  authorizeRecovery:async context=>{assert.equal(context.account,c.application.account);return {accountId:context.account};},
  verifyQuiescenceRelease:api.createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}),
  runtimeOwnerBindings:[{owner:gate.participant,storage:{packageName:'@amplifier/unified-distribution-update-owner',packageVersion:component.version,revision:component.revision,configKey:'manualIngress',rootRole:'service-ingress',stateDirectory:c.application.manualIngress.stateDirectory}}],
 });
 assert.deepEqual([...app.quiescence.requiredOwners].sort(),[...c.expectedOwners].sort());
 access=await createCountedIngress(gate);access.forward(app.url);
 control=await api.serveHostControl({host:app.host,inspectRunning:runtime.inspectRunning,recoveryOwners:app.quiescence.requiredOwners,token:c.hostToken,
  discovery:{file:c.hostFile,tokenFile:c.hostTokenFile,dataScope:runtime.dataScope},
  onMayBeIdle:callback=>{idle.add(callback);return ()=>idle.delete(callback);}});
 const paths=await app.storageInventory({externalCoverage:{'manual-preview-ingress':'declared'},externalRoots:[{id:'ingress',ownerIds:['manual-preview-ingress'],path:c.application.manualIngress.stateDirectory,coverage:'authoritative',capture:'tree'}]});
 assert.equal(paths.omissions.some(row=>row.id.includes('manual-preview-ingress')),false);
 if(source)await wrapper.attach({host:app.host,requiredOwners:app.quiescence.requiredOwners,expectedOwners:c.expectedOwners,close,exit:()=>process.exit(0)});
 ready=true;
 await writeFile(join(c.root,source?'source-ready.json':'destination-ready.json'),JSON.stringify({expected,owners:app.quiescence.requiredOwners,url:access.url,history:await readFile(c.historyFile,'utf8'),nativeStarts:app.host.diagnostics().activeAgents}),{mode:0o600});
 process.on('SIGTERM',()=>{void(async()=>{
  const fence=app.host.inspectQuiescence().fence;
  if(!fence||fence.phase!=='held'||fence.instanceId!==runtime.instanceId||!['service-stop','distribution-update'].includes(fence.purpose)){process.stderr.write('Held service admission required\n');return;}
  await close();process.exit(0);
 })().catch(error=>{process.stderr.write(String(error)+'\n');});});
}catch(error){await writeFile(join(c.root,'fixture-startup-error.txt'),String(error.stack??error),{mode:0o600});await close();throw error;}
