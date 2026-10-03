import {mkdir,writeFile,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createNotificationsCapability} from '@amplifier/unified-notifications-capability';

export async function composeNotifications(config,context){
 let owner=config.owner;
 if(!owner){
  if(!config.python&&!config.command)throw Error('Notifications require an installed Python or owner executable');
  const stateDirectory=join(context.directory,'notifications'),path=join(context.directory,'notifications-launch.json');
  await mkdir(stateDirectory,{recursive:true,mode:0o700});const temporary=path+'.'+randomUUID();
  await writeFile(temporary,JSON.stringify({stateDirectory,defaultServer:config.defaultServer,legacySettingsPath:config.legacySettingsPath}),{mode:0o600});await rename(temporary,path);
  owner={command:config.command??config.python,args:config.command?['--config',path]:['-I','-m','amplifier_unified_notifications.server','--config',path],env:config.env};
 }
 const capability=createNotificationsCapability({...context,owner});
 capability.notifySchedule=async(session,run)=>{
  if(run?.notificationDecision?.notify!==true)return {accepted:false,executed:false};
  const text=[run.phase,run.detail].filter(value=>typeof value==='string').map(value=>value.slice(0,1000)).join(': ').slice(0,1000);
  return capability.notifyAttention({session,eventId:'schedule:'+run.id,text});
 };
 return capability;
}
