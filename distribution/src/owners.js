import {mkdir,writeFile,rename,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {worktreeExecutionDirectory} from './portability-preflight.js';
import {createOperationsCapabilities} from '@amplifier/unified-operations-capabilities';
import {createWorktreeCapability} from '@amplifier/unified-worktree-capability';
import {createPublishingCapabilities} from '@amplifier/unified-publishing-capability';
import {createRecallCapability} from '@amplifier/unified-recall-capability';

export async function launcher(name,module,config,context){
 if(config.owner)return config.owner;
 if(!config.python&&!config.command)throw Error(name+' requires an installed Python or owner executable');
 const directory=join(context.directory,name),path=join(context.directory,name+'-launch.json');
 await mkdir(directory,{recursive:true,mode:0o700});const temporary=path+'.'+randomUUID();
 await writeFile(temporary,JSON.stringify({dataDir:directory}),{mode:0o600});await rename(temporary,path);
 return {command:config.command??config.python,args:config.command?['--config',path]:['-I','-m',module,'--config',path],env:config.env};
}

export async function composeOperations(config,context){
 return createOperationsCapabilities({...context,owner:await launcher('operations','amplifier_unified_operations.server',config,context)});
}

export async function composePublishing(config,context,authorizePublication){
 return createPublishingCapabilities({...context,authorizePublication,owner:await launcher('publishing','amplifier_unified_publishing.server',config,context)});
}

export async function composeRecall(config,context){
 return createRecallCapability({...context,owner:await launcher('recall','amplifier_unified_recall.server',config,context)});
}

export async function composeWorktrees(config,context){
 if(!config.python)throw Error('Worktrees require an installed library Python executable');
 const directory=join(context.directory,'worktrees'),managed=worktreeExecutionDirectory(context.directory);
 await mkdir(managed,{recursive:true,mode:0o700});
 return {owner:createWorktreeCapability({...context,directory,python:config.python,executionHost:config.executionHost,onChanged:context.onInvalidate}),executionRoot:await realpath(managed)};
}
