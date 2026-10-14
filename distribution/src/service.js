import {lstat, realpath} from 'node:fs/promises';
import {isAbsolute, join, resolve} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import {connectSupervisorFile, runProductionSupervisor, token} from '@amplifier/unified-distribution-update-owner';
import {createGitSourceResolver} from './source-tracking.js';
import {readInstallationConfiguration} from './installation.js';

/** Reopening is not installation or adoption. Retained private authority binds
 * every path and identity before opening the existing supervisor's ledger. */
export async function readInstalledServiceConfiguration(directory) {
 if(typeof directory!=='string'||!isAbsolute(directory))throw Error('absolute_installation_path_required');
 const root=resolve(directory),info=await lstat(root);
 if(!info.isDirectory()||info.isSymbolicLink()||root!==await realpath(root)||
    (process.platform!=='win32'&&(info.mode&0o077)))throw Error('private_installation_directory_required');
 const [input,authority,configuration,application]=await Promise.all([
  'installer-input.json','initial-provisioning.json','supervisor-configuration.json','application.json'
 ].map(name=>readInstallationConfiguration(join(root,name))));
 const conflict=()=>{throw Error('installed_service_binding_conflict');};
 if(input.schema!=='unified-installation-v1'||input.directory!==root||input.serviceLifecycle?.enabled!==true||
    authority.schema!=='distribution-pristine-installation-v1'||authority.directory!==root||
    configuration.schema!=='distribution-supervisor-v1'||!configuration.serviceLifecycle||
    input.dataScope!==authority.dataScope||configuration.dataScope!==authority.dataScope||
    configuration.serviceLifecycle.installationId!==authority.installationId)conflict();
 token(authority.installationId);token(authority.dataScope);token(configuration.serviceLifecycle.ownerId);
 const paths={dataDirectory:'supervisor',tokenFile:'supervisor-token',discoveryFile:'supervisor.json',hostDiscoveryFile:'host-control.json',provisioningAuthorityFile:'initial-provisioning.json'};
 for(const [key,name] of Object.entries(paths))if(configuration[key]!==join(root,name))conflict();
 if(configuration.adapterModule!==undefined||configuration.adapterConfig!==undefined||
    configuration.release?.directory!==join(root,'releases')||
    !isDeepStrictEqual(configuration.release?.launchArgs,['--config',join(root,'application.json')])||
    application.stateDirectory!==join(root,'application')||
    !isDeepStrictEqual(application.supervision?.serviceLifecycle,configuration.serviceLifecycle)||
    application.supervision?.discoveryFile!==join(root,'supervisor.json')||
    application.supervision?.hostControl?.discoveryFile!==join(root,'host-control.json')||
    application.supervision?.hostControl?.tokenFile!==join(root,'host-token')||
    !isDeepStrictEqual(application.supervision?.trustedKeys,input.release?.trustedKeys)||
    !isDeepStrictEqual(configuration.initial?.identity,authority.initial))conflict();
 const {directory:releaseDirectory,launchArgs,...trust}=configuration.release;
 if(!isDeepStrictEqual(trust,input.release))conflict();
 return {directory:root,configuration,sourceTracking:input.sourceTracking};
}

export async function openInstalledService(directory) {
 const saved=await readInstalledServiceConfiguration(directory);
 // Never startInitial here. The service owner's durable stopped receipt and an
 // explicit resume command are the sole authority for a later launch.
 return runProductionSupervisor(saved.configuration,{
  resolveSources:createGitSourceResolver(saved.sourceTracking),startInitial:false,
 });
}

export async function connectInstalledService(directory) {
 const {configuration}=await readInstalledServiceConfiguration(directory);
 const client=await connectSupervisorFile(configuration.discoveryFile);
 try {
  const observed=await client.service.inspect();
  if(observed.identity&&(observed.identity.installationId!==configuration.serviceLifecycle.installationId||
    observed.identity.ownerId!==configuration.serviceLifecycle.ownerId||observed.identity.dataScope!==configuration.dataScope))
   throw Error('installed_service_binding_conflict');
  return client;
 } catch(error){client.close();throw error;}
}
