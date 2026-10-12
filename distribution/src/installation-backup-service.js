import {constants} from 'node:fs';
import {mkdir,open,realpath,lstat,writeFile} from 'node:fs/promises';
import {join,isAbsolute} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash,randomBytes} from 'node:crypto';
import {readInstalledServiceConfiguration} from './service.js';
import {readInstallationConfiguration} from './installation.js';
import {createInstallationBackupCoordinator} from './installation-backup-coordinator.js';
import {serveInstallationBackupControl} from './installation-backup-control.js';
import {acquireInstallationBackupLease} from './installation-backup-lease.js';

/** Operator-owned composition, loaded only in the external installation process.
 * The replaceable app receives only private discovery, not adapters or paths.
 * Starting this controller never resumes a stopped app or repeats old work. */
export async function startInstallationBackupService({directory,configurationFile,getSupervisor,replaceSupervisor}){
 const config=await readInstallationConfiguration(configurationFile),saved=await readInstalledServiceConfiguration(directory);
 if(Object.keys(config).sort().join(',')!=='adapterModule,adapterSha256,schema,stateDirectory'||config.schema!=='unified-installation-backup-service-v1'||
  !isAbsolute(config.stateDirectory)||!isAbsolute(config.adapterModule)||!/^([a-f0-9]{64})$/.test(config.adapterSha256??'')||
  saved.application.recovery?.authorization!=='local-account'||saved.application.recovery?.credentials!==true||
  saved.application.installationBackup?.discoveryFile!==join(config.stateDirectory,'control.json'))throw Error('backup_service_configuration_invalid');
 if(await realpath(config.adapterModule)!==config.adapterModule)throw Error('backup_adapter_invalid');
 const h=await open(config.adapterModule,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{
  const s=await h.stat();if(!s.isFile()||s.nlink!==1||s.mode&0o077||s.size>1048576||createHash('sha256').update(await h.readFile()).digest('hex')!==config.adapterSha256)throw Error('backup_adapter_invalid');
 }finally{await h.close();}
 await mkdir(config.stateDirectory,{mode:0o700}).catch(e=>{if(e.code!=='EEXIST')throw e;});
 const stat=await lstat(config.stateDirectory);
 if(!stat.isDirectory()||stat.mode&0o077||await realpath(config.stateDirectory)!==config.stateDirectory)throw Error('backup_private_directory_required');
 const lease=acquireInstallationBackupLease(config.stateDirectory,{installationId:saved.configuration.serviceLifecycle.installationId,dataScope:saved.configuration.dataScope});
 let coordinator,control,adapters;
 try{
  const factory=(await import(pathToFileURL(config.adapterModule).href+'?sha256='+config.adapterSha256)).createBackupAdapters;
  if(typeof factory!=='function')throw Error('backup_adapter_invalid');
  adapters=await factory({directory,configuration:saved.configuration,application:saved.application,getSupervisor});
  if(!adapters||Object.keys(adapters).some(k=>!['readCompositionInventory','acquireWriterExclusion','nativeCaptures','close'].includes(k)))throw Error('backup_adapter_invalid');
  coordinator=await createInstallationBackupCoordinator({directory,stateDirectory:config.stateDirectory,outlivesDistribution:true,getSupervisor,replaceSupervisor,...adapters});
  const tokenFile=join(config.stateDirectory,'token');
  await writeFile(tokenFile,randomBytes(32).toString('hex')+'\n',{flag:'wx',mode:0o600}).catch(e=>{if(e.code!=='EEXIST')throw e;});
  control=await serveInstallationBackupControl({coordinator,tokenFile,discoveryFile:join(config.stateDirectory,'control.json'),installationId:saved.configuration.serviceLifecycle.installationId,dataScope:saved.configuration.dataScope});
  return {coordinator,async close(){await coordinator.close();await control.close();try{await adapters.close?.();}finally{lease.close();}}};
 }catch(error){
  try{await control?.close();}finally{try{await coordinator?.close();}finally{try{await adapters?.close?.();}finally{lease.close();}}}
  throw error;
 }
}
