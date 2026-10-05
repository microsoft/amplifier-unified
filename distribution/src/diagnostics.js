import {configuredOwnerLaunch} from './owner-launch.js';
import {mkdir,writeFile,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createDiagnosticsCapability} from '@amplifier/unified-diagnostics-capability';

/** Own only live observations. Never enumerate history to populate diagnostics. */
export async function composeDiagnostics(config,context){
 let owner=config.owner;
 if(!owner){
  if(!config.python&&!config.command)throw Error('Diagnostics require an installed owner executable');
  const stateDirectory=join(context.directory,'diagnostics'),path=join(context.directory,'diagnostics-launch.json');
  await mkdir(stateDirectory,{recursive:true,mode:0o700});const temporary=path+'.'+randomUUID();
  await writeFile(temporary,JSON.stringify({stateDirectory}),{mode:0o600});await rename(temporary,path);
  owner=configuredOwnerLaunch(config,'amplifier_unified_diagnostics.server',path);
 }
 const capability=createDiagnosticsCapability({...context,owner});
 try{await capability.ready();return capability;}catch(error){await capability.close();throw error;}
}

/** Action metadata is supplied by its admission context, with no extra reads. */
export function diagnosticActionObserver(owner,defaultWorkspace){
 return ({request,context,result,failed})=>{
  const selected=context?.session;
  const session=typeof selected==='string'?selected:selected?.uri??'ahp-root://';
  const workspace=selected?.executionDirectory??selected?.workingDirectory??defaultWorkspace;
  const stream=request.topic==='canvas'?'canvas':request.topic==='connectors'?'smartTools':request.topic==='application-updates'||request.topic==='runtime-updates'?'updates':'app';
  owner.observe({stream,event:'capability.settled',session,workspace,data:{commandId:request.commandId,action:request.operation,origin:context?.origin,status:failed?'unknown':result?.accepted===false?'refused':'completed'}});
 };
}
