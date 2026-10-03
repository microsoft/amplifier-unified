import {lstat,readFile,mkdir,writeFile} from 'node:fs/promises';
import {dirname,isAbsolute} from 'node:path';
import {randomBytes,randomUUID} from 'node:crypto';
import * as updates from '@amplifier/unified-distribution-update-owner';
import {createDistribution} from './index.js';

const absolute=(value,name)=>{
 if(typeof value!=='string'||!isAbsolute(value))throw Error(name+' must be an absolute path');
 return value;
};
async function controlToken(path){
 await mkdir(dirname(path),{recursive:true,mode:0o700});
 try{await writeFile(path,randomBytes(32).toString('hex')+'\n',{flag:'wx',mode:0o600});}
 catch(error){if(error.code!=='EEXIST')throw error;}
 const info=await lstat(path);
 if(!info.isFile()||info.isSymbolicLink()||info.size>128||(process.platform!=='win32'&&(info.mode&0o077)))throw Error('Private host control token file required');
 const value=(await readFile(path,'utf8')).trim();
 if(!/^[a-f0-9]{64}$/.test(value))throw Error('Invalid host control token');
 return value;
}

/** The JSON launcher is a single local account deployment. Remote authentication
 * and custom permission policy are provided by the public composition API. */
export function localRecoveryAuthorization(config){
 if(!config.recovery)return undefined;
 if(config.recovery.authorization!=='local-account')throw Error('Recovery requires explicit local-account authorization in the launcher');
 return async (context,_operation,args={})=>{
  if(context.account!==config.account||!['ui','agent'].includes(context.origin??'ui'))throw Error('Recovery account is not authorized');
  if(args.includeCredentials===true&&config.recovery.credentials!==true)throw Error('Credential export is not authorized by the launcher');
  if(Array.isArray(args.parts)&&args.parts.includes('notifications.credentials')&&config.recovery.credentials!==true)throw Error('Retained app credential reset is not authorized by the launcher');
  return {accountId:config.account};
 };
}

/** Bind opt-in service authority to this verified signed launch, never to a
 * client-supplied instance or a process ID. Readiness is checked separately after
 * owner initialization; this construction itself grants no stop authority. */
export function composeServiceLifecycle(binding,runtime,supervisor,env=process.env){
 if(binding===undefined)return undefined;
 if(!binding||typeof binding!=='object'||Array.isArray(binding)||
    Object.keys(binding).some(key=>!['installationId','ownerId'].includes(key)))throw Error('Invalid service lifecycle binding');
 if(binding.installationId!==env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID||
    binding.ownerId!==env.AMPLIFIER_DISTRIBUTION_OWNER_ID)throw Error('Service lifecycle binding does not match the owned launch');
 const identity=updates.serviceIdentity({...binding,dataScope:runtime.dataScope,
  instanceId:runtime.instanceId,releaseDigest:runtime.identity.digest});
 return {identity,verifyRelease:updates.createHostServiceReleaseVerifier({
  service:supervisor.service,inspectRunningService:()=>updates.inspectRuntimeService(runtime),
 })};
}

/** Signals in the opt-in child may close only an already held update/service
 * fence for this exact instance. A stop request belongs to the external owner. */
export function assertOwnedStopAdmission(host,runtime,serviceLifecycle){
 const state=host.inspectQuiescence(),fence=state.fence;
 if(!state.intakeClosed||fence?.phase!=='held'||fence.instanceId!==runtime.instanceId||
    fence.dataScope!==runtime.dataScope||!['distribution-update','service-stop'].includes(fence.purpose))
  throw Error('owned_service_stop_requires_held_admission');
 if(fence.purpose==='service-stop'&&(!serviceLifecycle||
    !updates.sameService(fence.serviceIdentity,serviceLifecycle.identity)))
  throw Error('owned_service_stop_identity_mismatch');
}

/** Compose the actual signed CLI entrypoint with its independently lived owner.
 * Constructing the lazy supervisor never waits for initial discovery or submits
 * work. Readiness means required owners initialized, even while intake is held. */
export async function startConfiguredDistribution(configuration,{entrypointUrl}={}){
 const config={...configuration},supervision=config.supervision;
 if(!['127.0.0.1','localhost','::1'].includes(config.gateway?.host??'127.0.0.1'))throw Error('JSON launcher requires a loopback gateway; use an authentication owner for remote clients');
 if(process.env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT&&!supervision)throw Error('Signed supervisor launch requires configured supervision');
 if(config.applicationUpdates&&!supervision)throw Error('Application updates require configured supervision');
 const authorizeRecovery=localRecoveryAuthorization(config);
 let app,control,supervisor,runtime,serviceLifecycle,initialized=false,closed=false,closing;
 const idleListeners=new Set(),notifyIdle=()=>{for(const notify of idleListeners){try{notify();}catch{/* advisory only */}}};
 const close=()=>{
  if(!closing)closing=(async()=>{
   closed=true;initialized=false;
   try{await control?.close();}finally{
    try{await app?.close();}finally{supervisor?.close();idleListeners.clear();}
   }
  })();
  return closing;
 };
 try{
  if(supervision){
   const discoveryFile=absolute(supervision.discoveryFile,'supervision.discoveryFile');
   const hostDiscovery=absolute(supervision.hostControl?.discoveryFile,'supervision.hostControl.discoveryFile');
   const tokenFile=absolute(supervision.hostControl?.tokenFile,'supervision.hostControl.tokenFile');
   if(new Set([discoveryFile,hostDiscovery,tokenFile]).size!==3)throw Error('Supervisor and host control files must be distinct');
   runtime=await updates.createRuntimeIdentity({entrypointUrl,trustedKeys:supervision.trustedKeys,isReady:()=>initialized&&!closed});
   supervisor=updates.connectSupervisorFileLazy(discoveryFile);
   config.quiescence={...config.quiescence,instanceId:runtime.instanceId,dataScope:runtime.dataScope};
   config.applicationUpdates={...config.applicationUpdates};
   serviceLifecycle=composeServiceLifecycle(supervision.serviceLifecycle,runtime,supervisor);
  }else if(config.quiescence){
   config.quiescence={...config.quiescence,instanceId:randomUUID()};
  }
  app=await createDistribution(config,{
   authorizeRecovery,serviceLifecycle,
   applicationUpdateSupervisor:supervisor,
   onMayBeIdle:notifyIdle,
   verifyQuiescenceRelease:runtime?updates.createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}):undefined,
  });
  if(supervision){
   control=await updates.serveHostControl({host:app.host,inspectRunning:runtime.inspectRunning,recoveryOwners:app.quiescence.requiredOwners,
    token:await controlToken(supervision.hostControl.tokenFile),
    discovery:{file:supervision.hostControl.discoveryFile,tokenFile:supervision.hostControl.tokenFile,dataScope:runtime.dataScope},
    onMayBeIdle:notify=>{idleListeners.add(notify);return ()=>idleListeners.delete(notify);},
   });
  }
  initialized=true;
  return {...app,runtime,close,async requestStop(){
   if(serviceLifecycle)assertOwnedStopAdmission(app.host,runtime,serviceLifecycle);
   await close();
  }};
 }catch(error){await close();throw error;}
}
