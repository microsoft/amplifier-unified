// Fresh installation only. Existing state, stopped service authority and unknown
// launches are never adopted, reset, deleted or replayed by this entrypoint.
import {constants} from 'node:fs';
import {open,lstat,realpath,rename,mkdtemp} from 'node:fs/promises';
import {dirname,join,isAbsolute,resolve,relative} from 'node:path';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {createSecureContext} from 'node:tls';
import {isDeepStrictEqual} from 'node:util';
import {SignedReleaseAdapter,createPristineInstallation,runProductionSupervisor,token} from '@amplifier/unified-distribution-update-owner';
import {createGitSourceResolver} from './source-tracking.js';
import {FRESH_COMPOSITION_SCHEMA,requireLaunchConfig,assertFreshInstallationLayout} from './validate-config.mjs';
import {bindReleaseConfiguration} from './release-runtime.mjs';
import {validatePortabilityStage} from './portability-preflight.js';

const exact=(value,fields)=>{
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!==[...fields].sort().join(','))throw Error('invalid_full_owner_installation');
};
const absolute=p=>{
 if(typeof p!=='string'||!isAbsolute(p)||resolve(p)!==p)throw Error('canonical_installation_path_required');
 return p;
};
const inside=(parent,p)=>{const r=relative(parent,p);return !r||(!r.startsWith('../')&&r!=='..'&&!isAbsolute(r));};
async function bytes(path,max=1048576,certificate=false){
 absolute(path);
 if(await realpath(path)!==path)throw Error('canonical_installation_path_required');
 const fd=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{
  const stat=await fd.stat();
  if(!stat.isFile()||stat.size>max||stat.uid!==process.getuid()||(stat.mode&(certificate?0o022:0o077)))throw Error('private_installation_input_required');
  const value=await fd.readFile();if(value.length>max)throw Error('installation_input_limit');return value;
 }finally{await fd.close();}
}
async function absent(path){try{await lstat(path);}catch(e){if(e.code==='ENOENT')return;throw e;}throw Error('installation_already_exists');}
async function syncDirectory(path){const fd=await open(path,constants.O_RDONLY);try{await fd.sync();}finally{await fd.close();}}
async function writeOnce(path,value){
 const fd=await open(path,'wx',0o600);
 try{await fd.writeFile(value);await fd.sync();}finally{await fd.close();}
 await syncDirectory(dirname(path));
}
const jsonOnce=(p,value)=>writeOnce(p,JSON.stringify(value)+'\n');
const projection=value=>Object.fromEntries(['id','version','revision'].map(k=>[k,value[k]]));

/** Read and validate caller-owned inputs without creating an installation or
 * opening owners/listeners. Downloaded/runtime binding is separately checked
 * in a private preparation namespace before pristine installation allocation. */
export async function inspectFullOwnerInstallation(input){
 exact(input,['schema','directory','compositionFile']);
 if(input.schema!=='unified-full-owner-installation-v1')throw Error('invalid_full_owner_installation');
 const directory=absolute(input.directory),compositionFile=absolute(input.compositionFile);
 if(await realpath(dirname(directory))!==dirname(directory)||inside(directory,compositionFile))throw Error('canonical_installation_path_required');
 await absent(directory);
 const configurationBytes=await bytes(compositionFile),c=requireLaunchConfig(JSON.parse(configurationBytes));
 if(c.schema!==FRESH_COMPOSITION_SCHEMA)throw Error('fresh_composition_required');
 assertFreshInstallationLayout(c,directory);
 token(c.authority.ownerId);token(c.authority.dataScope);
 const a=c.application,access=c.access,release=c.release;
 if(!access||typeof a.account!=='string'||!a.account||!Array.isArray(a.engines)||!a.engines.length||
    !Array.isArray(a.allowedWorkspaceRoots)||!a.allowedWorkspaceRoots.length||a.supervision||a.quiescence||
    !Number.isInteger(a.gateway.port)||a.gateway.port<1024||a.gateway.port>65535||
    access.backendPort!==a.gateway.port||access.port===a.gateway.port||!Number.isInteger(access.port)||access.port<1024||access.port>65535||
    typeof access.host!=='string'||!access.host)throw Error('invalid_full_owner_installation');
 const origin=new URL(access.origin);
 if(origin.protocol!=='https:'||origin.origin!==access.origin||Number(origin.port||443)!==access.port||a.gateway.origin!==access.origin)throw Error('invalid_full_owner_installation');
 for(const p of [a.defaultWorkspace,...a.allowedWorkspaceRoots]){
  absolute(p);if(await realpath(p)!==p||!(await lstat(p)).isDirectory())throw Error('canonical_workspace_required');
 }
 if(!a.allowedWorkspaceRoots.some(p=>inside(p,a.defaultWorkspace)))throw Error('workspace_outside_roots');
 await validatePortabilityStage(a);
 const trustedKeysBytes=await bytes(release.trustedKeysFile),trustedKeys=JSON.parse(trustedKeysBytes);
 if(!trustedKeys||typeof trustedKeys!=='object'||Array.isArray(trustedKeys)||!Object.keys(trustedKeys).length)throw Error('publisher_trust_required');
 const key=await bytes(access.keyFile),cert=await bytes(access.certFile,1048576,true),code=await bytes(access.codeFile,1024);
 if(code.toString().trim().length<40)throw Error('preview_access_code_invalid');
 try{createSecureContext({key,cert});}catch{throw Error('preview_tls_material_invalid');}
 if(!Array.isArray(release.allowedArtifactOrigins)||!release.allowedArtifactOrigins.length||
    (release.allowLoopbackHttp!==undefined&&typeof release.allowLoopbackHttp!=='boolean'))throw Error('invalid_release_configuration');
 for(const origin of release.allowedArtifactOrigins){const url=new URL(origin);if(url.origin!==origin)throw Error('invalid_release_configuration');}
 const resolveSources=createGitSourceResolver({sources:c.sourcePolicy});
 const releaseOptions={channelUrl:release.channelUrl,trustedKeys,accessScope:release.accessScope,
  allowedArtifactOrigins:release.allowedArtifactOrigins,...(release.allowLoopbackHttp===undefined?{}:{allowLoopbackHttp:release.allowLoopbackHttp}),
  directory:join(directory,'releases'),launchArgs:[compositionFile]};
 const adapter=new SignedReleaseAdapter({...releaseOptions,resolveSources});
 const context={commandId:'full-owner-install-selection',signal:new AbortController().signal};
 const catalog=await adapter.check({...context,fresh:true});
 const selected=catalog.releases.find(r=>isDeepStrictEqual(projection(r),c.release.initial));
 if(!selected)throw Error('initial_release_not_found');
 // Inputs are retained byte-for-byte. Re-read before allocation and before
 // launching, so slow network/runtime checks do not bless changed private input.
 const unchanged=async()=>{
  await validatePortabilityStage(a);
  for(const [p,b,publicCert] of [[compositionFile,configurationBytes,false],[release.trustedKeysFile,trustedKeysBytes,false],
    [access.keyFile,key,false],[access.certFile,cert,true],[access.codeFile,code,false]])
   if(!b.equals(await bytes(p,p===access.codeFile?1024:1048576,publicCert)))throw Error('installation_input_changed');
 };
 await unchanged();
 return {directory,compositionFile,configuration:c,configurationBytes,selected,releaseOptions,resolveSources,adapter,context,unchanged};
}

/** Run in the long-lived external supervisor process, using its current Node.
 * Never invoke this wrapper from the replaceable signed application tree. */
export async function installFullOwnerDistribution(input){
 const checked=await inspectFullOwnerInstallation(input);
 const {directory,compositionFile,configuration:c,configurationBytes,selected,releaseOptions,resolveSources,adapter,context,unchanged}=checked;
 // Preparing signed bytes must not consume pristine launch authority. A bad
 // archive, config binding, runtime or owner profile leaves the target absent.
 // Keep this separate private cache/receipt on interruption; it cannot authorize
 // launch and is never used as a replacement for the one-shot initial claim.
 const preparationDirectory=await mkdtemp(join(dirname(directory),'.full-owner-prepare-'));
 const stagedOptions={...releaseOptions,directory:join(preparationDirectory,'releases')};
 const staged=new SignedReleaseAdapter({...stagedOptions,resolveSources});
 await jsonOnce(join(preparationDirectory,'preparation.json'),{schema:'unified-full-owner-preparation-v1',
  directory,compositionFile,initial:selected,workReplayed:false});
 const initial=await staged.prepare(selected,{...context,commandId:'full-owner-install-prepare'});
 if(!await staged.verify(initial,context))throw Error('initial_release_unverified');
 const prepared=await staged.installed(initial);
 if(prepared.release.entrypoint!==c.release.entrypoint)throw Error('full_owner_entrypoint_required');
 await bindReleaseConfiguration({configuration:c,configurationBytes,runtime:{identity:selected},installationInitial:selected,releaseRoot:prepared.root});
 await unchanged();
 const installation=await createPristineInstallation({directory,dataScope:c.authority.dataScope,initial:selected,plannedInstallationId:c.authority.installationId});
 const attemptFile=join(directory,'installer-attempt.json');let phase='allocated';
 const record=async status=>{
  const temporary=attemptFile+'.'+randomUUID();
  await jsonOnce(temporary,{schema:'unified-full-owner-installation-attempt-v1',phase,status,
   installationId:c.authority.installationId,dataScope:installation.dataScope,initial:selected,updatedAt:Date.now(),workReplayed:false});
  await rename(temporary,attemptFile);await syncDirectory(directory);
 };
 try{
  await record('preparing');
  await jsonOnce(join(directory,'installer-input.json'),input);
  await writeOnce(join(directory,'installer-composition.json'),configurationBytes);
  await writeOnce(installation.hostTokenFile,randomBytes(32).toString('hex')+'\n');
  phase='preparing';await record('preparing');
  // Move the already verified immutable cache within this filesystem. Do not
  // redownload or run installers after allocating authority. The supervisor
  // still independently rechecks source currency and signed bytes at launch.
  await rename(stagedOptions.directory,installation.releaseDirectory);
  await syncDirectory(preparationDirectory);await syncDirectory(directory);
  if(!await adapter.verify(initial,context))throw Error('initial_release_unverified');
  const installed=await adapter.installed(initial);
  await bindReleaseConfiguration({configuration:c,configurationBytes,runtime:{identity:selected},installationInitial:selected,releaseRoot:installed.root});
  await unchanged();
  const supervisorConfiguration={schema:'distribution-supervisor-v1',
   serviceLifecycle:{installationId:c.authority.installationId,ownerId:c.authority.ownerId,
    ...(process.platform==='linux'?{platform:{kind:'linux-user-unit',
      unit:'amplifier-installation-'+createHash('sha256').update(c.authority.installationId).digest('hex').slice(0,24)+'.service',
      unitDirectory:join(installation.dataDirectory,'units')}}:{})},
   dataDirectory:installation.dataDirectory,dataScope:installation.dataScope,tokenFile:installation.supervisorTokenFile,
   discoveryFile:installation.supervisorDiscoveryFile,hostDiscoveryFile:installation.hostDiscoveryFile,
   provisioningAuthorityFile:installation.authorityFile,initial,release:releaseOptions};
  const supervisorFile=join(directory,'supervisor-configuration.json');
  await jsonOnce(supervisorFile,supervisorConfiguration);
  phase='launch_requested';await record('unknown');
  const supervisor=await runProductionSupervisor(supervisorConfiguration,{resolveSources,startInitial:true});
  phase='ready';await record('ready');
  return {installation,compositionFile,supervisorFile,supervisor,initial};
 }catch(error){
  await record(['launch_requested','ready'].includes(phase)?'unknown':'failed-before-launch').catch(()=>{});
  throw error;
 }
}
