import {join} from 'node:path';
import {createRecoveryCapabilities} from '@amplifier/unified-recovery-capability';

/** Selected recovery delegates all native bytes and lease evidence to ACP owners. */
export function composeRecovery(config,context,{admin,host,engineId,nativeAuthority,authorize}){
 if(!admin||typeof authorize!=='function')throw Error('Recovery requires native administration and explicit account authorization');
 const owner=createRecoveryCapabilities({
  directory:join(context.directory,'recovery'),nativeAuthority,leaseSeconds:config.leaseSeconds,
  nativeAdmin:(operation,args,caller)=>admin.perform(operation,args,{...caller,session:undefined}),
  authorize:async(caller,operation,args)=>{
   if(caller.account!==context.account)throw Error('Recovery account mismatch');
   const identity=await authorize(caller,operation,args);
   if(identity?.accountId!==context.account)throw Error('Recovery approval must bind the authenticated account');
   return identity;
  },
  resolveSession:async(session,caller)=>{
   const selected=await context.inspectSession(session,caller);
   if(selected.engineId!==engineId||!selected.nativeSessionId||!selected.workingDirectory)throw Error('Recovery requires a session owned by the configured native engine');
   // workingDirectory is immutable native history authority. Relocation changes
   // executionDirectory independently and must never redirect recovery.
   return {nativeSessionId:selected.nativeSessionId,historyCwd:selected.workingDirectory,nativeAuthority};
  },
  quiescence:{
   admitQuiescence:input=>host().admitQuiescence(input),
   inspectQuiescence:()=>host().inspectQuiescence(),
   quiescenceReceipt:id=>host().quiescenceReceipt(id),
   releaseQuiescence:input=>host().releaseQuiescence(input),
   withQuiescenceMaintenance:(input,work)=>host().withQuiescenceMaintenance(input,()=>admin.withMaintenanceFence(input,work)),
  },
  onInvalidate:context.onInvalidate,
 });
 owner.resourceProvider={scheme:'amplifier-recovery',read:(params,caller)=>owner.resourceRead(params,{...caller,account:context.account})};
 return owner;
}
