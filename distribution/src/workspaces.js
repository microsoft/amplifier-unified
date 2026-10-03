import {mkdir,writeFile,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createWorkspaceCapabilities} from '@amplifier/unified-workspace-capability';

/** Workspace authority and derived discovery share a public catalog connection. */
export async function composeWorkspaces(config,{directory,catalog,roots,defaultRoot,onInvalidate}){
 if(!catalog)throw Error('Workspace management requires the shared session catalog');
 let owner=config.owner;
 if(!owner){
  if(!config.python&&!config.command)throw Error('Workspace management requires an installed Python or owner executable');
  const stateDirectory=join(directory,'workspaces'),path=join(directory,'workspaces-launch.json');
  await mkdir(stateDirectory,{recursive:true,mode:0o700});const temporary=path+'.'+randomUUID();
  await writeFile(temporary,JSON.stringify({stateDirectory,allowedRoots:roots,defaultRoot:config.defaultRoot??defaultRoot}),{mode:0o600});await rename(temporary,path);
  owner={command:config.command??config.python,args:config.command?['--config',path]:['-I','-m','amplifier_unified_workspaces.server','--config',path],env:config.env};
 }
 const capability=createWorkspaceCapabilities({owner,catalog,onInvalidate});
 let closing;const close=capability.close;
 capability.close=()=>closing??=close();
 return capability;
}
