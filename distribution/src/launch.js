import {lstat,readFile,mkdir,writeFile} from 'node:fs/promises';
import {dirname,isAbsolute} from 'node:path';
import {randomBytes,randomUUID} from 'node:crypto';
import * as updates from '@amplifier/unified-distribution-update-owner';

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
 const policy=config.recovery||config.conversationPresentation||config.host?.conversationPresentation;
 if(!policy)return undefined;
 if(policy.authorization!=='local-account')throw Error('Recovery requires explicit local-account authorization in the launcher');
 const credentials=config.recovery?.credentials===true;
 return async (context,_operation,args={})=>{
  if(context.account!==config.account||!['ui','agent'].includes(context.origin??'ui'))throw Error('Recovery account is not authorized');
  if(args.includeCredentials===true&&!credentials)throw Error('Credential export is not authorized by the launcher');
  if(Array.isArray(args.parts)&&args.parts.includes('notifications.credentials')&&!credentials)throw Error('Retained app credential reset is not authorized by the launcher');
  return {accountId:config.account};
 };
}

/** Bind opt-in service authority to this verified signed launch, never to a
 * client-supplied instance or a process ID. Readiness is checked separately after
 * owner initialization; this construction itself grants no stop authority. */
export function composeServiceLifecycle(binding,runtime,supervisor,env=process.env,{installation}={}){
 if(binding===undefined)return undefined;
 if(!binding||typeof binding!=='object'||Array.isArray(binding)||
    Object.keys(binding).some(key=>!['installationId','ownerId'].includes(key)))throw Error('Invalid service lifecycle binding');
 if(binding.installationId!==env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID||
    binding.ownerId!==env.AMPLIFIER_DISTRIBUTION_OWNER_ID)throw Error('Service lifecycle binding does not match the owned launch');
 const identity=updates.serviceIdentity({...binding,dataScope:runtime.dataScope,
  instanceId:runtime.instanceId,releaseDigest:runtime.identity.digest});
 let initial;
 if(installation){
  if(installation.installationId!==identity.installationId||installation.dataScope!==identity.dataScope)throw Error('Initial installation does not match the signed runtime');
  if(installation.initialInstanceId===identity.instanceId){
   if(!installation.initialCommandId||!runtime.identity||!installation.initial||['id','version','revision','digest'].some(key=>installation.initial[key]!==runtime.identity[key])||typeof updates.createHostServiceInitialStartVerifier!=='function')throw Error('Initial installation does not bind the exact signed target and consumed command');
   initial={initialStart:{commandId:installation.initialCommandId,identity},verifyInitialStart:updates.createHostServiceInitialStartVerifier({service:supervisor.service,inspectRunningService:()=>updates.inspectRuntimeService(runtime)})};
  }
 }
 return {identity,...initial,verifyRelease:updates.createHostServiceReleaseVerifier({
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
   const provisioningAuthority=process.env.AMPLIFIER_DISTRIBUTION_LIFECYCLE==='linux-user-unit'?absolute(process.env.AMPLIFIER_DISTRIBUTION_PROVISIONING_AUTHORITY,'owned provisioning authority'):undefined;
   runtime=await updates.createRuntimeIdentity({entrypointUrl,trustedKeys:supervision.trustedKeys,isReady:()=>initialized&&!closed,observeReady:()=>initialized&&!closed});
   supervisor=updates.connectSupervisorFileLazy(discoveryFile);
   config.quiescence={...config.quiescence,instanceId:runtime.instanceId,dataScope:runtime.dataScope};
   config.applicationUpdates={...config.applicationUpdates};
   let installation;
   if(process.env.AMPLIFIER_DISTRIBUTION_LIFECYCLE==='linux-user-unit'){
    if(!supervision.serviceLifecycle)throw Error('Linux owned launch requires service lifecycle binding');
    installation=await updates.inspectPristineInstallation(provisioningAuthority);
    if(installation.supervisorDiscoveryFile!==discoveryFile)throw Error('Initial installation discovery does not match configured supervisor');
   }
   serviceLifecycle=composeServiceLifecycle(supervision.serviceLifecycle,runtime,supervisor,process.env,{installation});
  }else if(config.quiescence){
   config.quiescence={...config.quiescence,instanceId:randomUUID()};
  }
  const {createDistribution}=await import('./index.js');
  app=await createDistribution(config,{
   authorizeRecovery,serviceLifecycle,
   applicationUpdateSupervisor:supervisor,
   onMayBeIdle:notifyIdle,
   verifyQuiescenceRelease:runtime?updates.createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}):undefined,
   verifyQuiescenceAdmissionAbort:runtime?updates.createHostAdmissionAbortVerifier?.({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}):undefined,
  });
  if(supervision){
   control=await updates.serveHostControl({host:app.host,inspectRunning:async()=>({...await runtime.inspectRunning(),...(process.env.AMPLIFIER_DISTRIBUTION_LIFECYCLE==='linux-user-unit'?{invocationId:process.env.INVOCATION_ID,intakeClosed:app.host.inspectServiceLifecycle().intakeClosed}:{})}),observeRuntime:runtime.observeStatus,recoveryOwners:app.quiescence.requiredOwners,
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
