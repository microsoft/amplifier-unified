// Composition candidate. Place in the final signed distribution src/ tree.
// Normal invocation refuses unresolved/unreviewed configuration before imports,
// private-key reads, owner creation or listeners. This file does not bootstrap
// an old process and never fabricates pristine-installation authority.
import {readFile,open,mkdir,writeFile,realpath} from 'node:fs/promises';
import {constants} from 'node:fs';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {bindReleaseConfiguration} from './release-runtime.mjs';
import {isDeepStrictEqual} from 'node:util';
import {createSecureContext} from 'node:tls';
import {requireLaunchConfig,assertOwnerCensus,FRESH_COMPOSITION_SCHEMA,assertFreshInstallationLayout} from './validate-config.mjs';
const configurationBytes=await readFile(process.argv[2]);
let c=requireLaunchConfig(JSON.parse(configurationBytes));
const source=process.env.UNIFIED_MANUAL_SOURCE==='1';
if(process.env.UNIFIED_MANUAL_SOURCE&&!source)throw Error('invalid_launch_mode');
if(source&&c.schema===FRESH_COMPOSITION_SCHEMA)throw Error('fresh_signed_supervision_required');
if(c.authority.installationId!==process.env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID||c.authority.ownerId!==process.env.AMPLIFIER_DISTRIBUTION_OWNER_ID||c.authority.dataScope!==process.env.AMPLIFIER_DISTRIBUTION_DATA_SCOPE)throw Error('service_identity_binding_mismatch');
const launchBytes=async(path,max,publicCertificate=false)=>{
 const fd=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{const s=await fd.stat(),invalid=publicCertificate?'public_certificate_file_required':'private_launch_file_required';if(!s.isFile()||s.size>max||(s.mode&(publicCertificate?0o022:0o077))||s.uid!==process.getuid())throw Error(invalid);const bytes=await fd.readFile();if(bytes.length>max)throw Error(invalid);return bytes;}finally{await fd.close();}
};
const privateBytes=(path,max=1048576)=>launchBytes(path,max);
if(c.schema===FRESH_COMPOSITION_SCHEMA&&
   (await realpath(process.argv[2])!==process.argv[2]||!configurationBytes.equals(await privateBytes(process.argv[2]))))
 throw Error('private_launch_configuration_changed');
// Validate and retain these exact bytes before runtime identity, one-shot source
// authority, owner allocation or listeners. Public certificates need integrity,
// while keys, tokens, trust and access codes retain private-file requirements.
const keys=JSON.parse(await privateBytes(c.release.trustedKeysFile));
if(Object.keys(keys).length===0)throw Error('publisher_trust_required');
const hostToken=(await privateBytes(c.authority.hostTokenFile,128)).toString().trim();
if(!/^[a-f0-9]{64}$/.test(hostToken))throw Error('host_control_token_invalid');
const accessMaterial={key:await privateBytes(c.access.keyFile),cert:await launchBytes(c.access.certFile,1048576,true),accessCode:(await privateBytes(c.access.codeFile,1024)).toString().trim()};
if(accessMaterial.accessCode.length<40)throw Error('preview_access_code_invalid');
try{createSecureContext({key:accessMaterial.key,cert:accessMaterial.cert});}catch{throw Error('preview_tls_material_invalid');}
const api=await import('@amplifier/unified-distribution-update-owner');
let ready=false,closing,app,control,access,gate,wrapper,bootstrapRecovery;
const idle=new Set(),mayBeIdle=()=>{for(const notify of idle){try{notify();}catch{}}};
let releaseBinding;
const runtime=await api.createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:keys,isReady:async()=>{await releaseBinding?.verify();return ready;},observeReady:()=>ready});
let installationInitial;
if(c.schema===FRESH_COMPOSITION_SCHEMA){
 const installation=await api.inspectPristineInstallation(join(dirname(c.authority.supervisorDirectory),'initial-provisioning.json'));
 assertFreshInstallationLayout(c,installation.directory);
 if(installation.installationId!==c.authority.installationId||installation.dataScope!==runtime.dataScope||
    process.env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT!==join(installation.releaseDirectory,'releases',runtime.identity.digest,'receipt.json')||
    fileURLToPath(new URL('../',import.meta.url))!==join(installation.releaseDirectory,'releases',runtime.identity.digest,'package')+'/')
  throw Error('fresh_installation_binding_mismatch');
 installationInitial=installation.initial;
}
releaseBinding=await bindReleaseConfiguration({configuration:c,configurationBytes,runtime,releaseRoot:fileURLToPath(new URL('../',import.meta.url)),source,installationInitial});
c=requireLaunchConfig(releaseBinding.configuration);
// Only an exact signed v3 profile may extend the effective census. Keep the original
// private 20-owner configuration intact for source/bootstrap and rollback.
const expectedOwners=releaseBinding.expectedOwners??c.expectedOwners;
const {createDistribution}=await import('@amplifier/unified');
const {createPreviewAccess}=await import('./preview-access.mjs');
const expected=api.serviceIdentity({...c.authority.serviceBinding,installationId:c.authority.installationId,ownerId:c.authority.ownerId,dataScope:runtime.dataScope,instanceId:runtime.instanceId,releaseDigest:runtime.identity.digest});
const supervisor=api.connectSupervisorFileLazy(c.authority.supervisorDiscoveryFile);
const close=()=>closing??=(async()=>{
 ready=false;
 // A successfully held full-owner fence precedes normal service closure.
 // Startup failures retain one-shot authority and diagnostics; no forced retry.
 await access?.close();await app?.close();await control?.close();gate?.close();supervisor.close();idle.clear();
})();
const requireStop=()=>{
 const s=app?.host.inspectQuiescence(),f=s?.fence;
 if(!s?.intakeClosed||f?.phase!=='held'||f.instanceId!==runtime.instanceId||f.dataScope!==runtime.dataScope||!['service-stop','distribution-update'].includes(f.purpose))throw Error('owned_service_stop_requires_held_admission');
 if(f.purpose==='service-stop'&&!api.sameService(f.serviceIdentity,expected))throw Error('owned_service_stop_identity_mismatch');
};
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{
 if(source){process.stderr.write('manual_source_requires_authenticated_handoff\n');return;}
 void(async()=>{requireStop();await close();process.exit(0);})().catch(()=>process.stderr.write('owned_service_stop_refused\n'));
});
try{
 if(source&&c.bootstrapRecovery){
  // This aggregate must be part of the signed composition. It re-inspects real
  // stopped owner/effect state; a copied JSON receipt is not a qualifier.
  // Missing support refuses before source authority or app owners are created.
  const {createBootstrapRecoveryOptions}=await import('./bootstrap-recovery-qualification.mjs');
  bootstrapRecovery=await createBootstrapRecoveryOptions({configuration:c,expected,api});
  if(!bootstrapRecovery||typeof bootstrapRecovery.qualifyStoppedState!=='function'||typeof bootstrapRecovery.assertExclusionHeld!=='function'||
    bootstrapRecovery.successor?.directory!==c.authority.sourceDirectory||
    bootstrapRecovery.successor?.unit!==c.sourceUnit||
    !isDeepStrictEqual(bootstrapRecovery.successor?.expected,expected)||
    !isDeepStrictEqual(bootstrapRecovery.successor?.bindings,c.bindings))
    throw Error('bootstrap_recovery_composition_unqualified');
 }
 if(source)wrapper=await api.createManualSystemdHandoffLauncher({
  directory:c.authority.sourceDirectory,expected,bindings:c.bindings,
  recovery:bootstrapRecovery,
  observer:api.createLinuxSystemdSourceObserver({unit:c.sourceUnit,python:c.observerPython}),
  qualifyCurrent:async()=>{const actual=await runtime.inspectRunning();if(!isDeepStrictEqual(actual.identity,c.release.prepared.identity))throw Error('source_identity_changed');return c.release.prepared;},
 });
 const lifecycle=source?wrapper.serviceLifecycle:{identity:expected,verifyRelease:api.createHostServiceReleaseVerifier({service:supervisor.service,inspectRunningService:()=>api.inspectRuntimeService(runtime)})};
 gate=await api.createManualIngressGate({directory:c.application.manualIngress.stateDirectory,id:'manual-preview-ingress',onMayBeIdle:mayBeIdle});
 const components=JSON.parse(await readFile(new URL('../components.json',import.meta.url),'utf8')).components;
 const owner=components['@amplifier/unified-distribution-update-owner'];
 if(!owner?.revision||owner.revision!==c.release.updateOwnerRevision||owner.version!==c.release.updateOwnerVersion)throw Error('ingress_provenance_mismatch');
 const authorizeRecovery=async(context,_operation,args={})=>{
  if(context.account!==c.application.account||!['ui','agent'].includes(context.origin??'ui'))throw Error('account_not_authorized');
  if(args.includeCredentials===true||args.parts?.includes('notifications.credentials'))throw Error('credential_export_not_authorized');
  return {accountId:context.account};
 };
 await bootstrapRecovery?.assertExclusionHeld();
 app=await createDistribution({...c.application,quiescence:{...c.application.quiescence,instanceId:runtime.instanceId,dataScope:runtime.dataScope}},{
  serviceLifecycle:lifecycle,applicationUpdateSupervisor:supervisor,onMayBeIdle:mayBeIdle,authorizeRecovery,
  verifyQuiescenceRelease:api.createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}),
  runtimeOwnerBindings:[{owner:gate.participant,storage:{packageName:'@amplifier/unified-distribution-update-owner',packageVersion:owner.version,revision:owner.revision,configKey:'manualIngress',rootRole:'service-ingress',stateDirectory:c.application.manualIngress.stateDirectory}}],
 });
 assertOwnerCensus(app.quiescence.requiredOwners,expectedOwners);
 control=await api.serveHostControl({host:app.host,inspectRunning:runtime.inspectRunning,observeRuntime:runtime.observeStatus,recoveryOwners:app.quiescence.requiredOwners,
  token:hostToken,
  discovery:{file:c.authority.hostDiscoveryFile,tokenFile:c.authority.hostTokenFile,dataScope:runtime.dataScope},
  onMayBeIdle:notify=>{idle.add(notify);return ()=>idle.delete(notify);},
 });
 const inventory=await app.storageInventory(c.storageInventory);
 // A startup inventory is an omissions report, never a stopped native archive.
 if(inventory.omissions.some(x=>x.id.includes('manual-preview-ingress')))throw Error('ingress_storage_uncovered');
 await mkdir(c.receiptDirectory,{recursive:true,mode:0o700});
 await writeFile(join(c.receiptDirectory,runtime.instanceId+'-storage.json'),JSON.stringify(inventory)+'\n',{flag:'wx',mode:0o600});
 if(source)await wrapper.attach({host:app.host,requiredOwners:app.quiescence.requiredOwners,expectedOwners,close,exit:()=>process.exit(0)});
 // Keep the operator-owned start gates closed through owner acquisition.
 await bootstrapRecovery?.assertExclusionHeld();
 access=await createPreviewAccess({...c.access,...accessMaterial,ingressGate:gate,terminalAccess:app.terminalAccess});
 ready=true;
 await writeFile(join(c.receiptDirectory,runtime.instanceId+'-ready.json'),JSON.stringify({schema:'full-owner-ready-v1',mode:source?'instrumented-source':'supervised',identity:expected,owners:app.quiescence.requiredOwners,storageComplete:inventory.complete===true,releaseBinding:releaseBinding.binding})+'\n',{flag:'wx',mode:0o600});
 process.stdout.write('full_owner_ready\n');
}catch(error){
 ready=false;
 // Generic stderr cannot disclose a path, credential or arbitrary owner error.
 process.stderr.write('full_owner_startup_unconfirmed\n');
 try{await close();}catch{process.stderr.write('owned_closure_unconfirmed\n');}
 process.exitCode=1;
 throw Error('full_owner_startup_unconfirmed');
}
