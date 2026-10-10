import {join} from 'node:path';
import {createHistoryCapability} from '@amplifier/unified-history-capability';

/** Native owns imported bytes; host/catalog independently verify new identity. */
export function composeHistory(config,context,{admin,host,engineId}){
 if(!admin||!engineId)throw Error('History import requires explicitly configured native administration');
 if(config.engine!==undefined&&config.engine!==engineId)throw Error('History import must use the configured native administration engine');
 const check=caller=>{if(caller.account!==context.account)throw Error('History import account mismatch')};
 return createHistoryCapability({
  directory:join(context.directory,'history-import'),engineId,
  authorizeWorkspace:async(directory,caller)=>{check(caller);return host().authorizeWorkspace(directory)},
  nativeAdmin:(operation,args,caller)=>{check(caller);return admin.importHistory(operation,args,caller.workingDirectory)},
  adoptImportedSession:input=>host().adoptImportedSession(input),
  importAdoptionReceipt:(commandId,options)=>host().importAdoptionReceipt(commandId,options),
  onInvalidate:context.onInvalidate,onMayBeIdle:context.onMayBeIdle,
 });
}
