import {isDeepStrictEqual} from 'node:util';

/** Retire browser/device sockets only after every business owner is held and
 * Host intake is closed for this exact restart. HTTP effects still have to
 * finish naturally. This adapter never grants service-stop authority itself. */
export function restartAwareIngress(gate,{getAccess,inspectQuiescence,requiredOwners}){
 const base=gate.participant,drains=new Map();
 const finish=context=>{const drain=drains.get(context.fenceId);drain?.release();drains.delete(context.fenceId);};
 return {...base,
  async acquire(context){
   if(!['service-stop','distribution-update'].includes(context.purpose)||!getAccess())return base.acquire(context);
   const status=inspectQuiescence(),fence=status?.fence,others=requiredOwners().filter(id=>id!==base.id);
   if(!status.intakeClosed||fence?.phase!=='checking'||fence.admissionStage!=='participants'&&fence.admissionStage!=='owner-exclusion'||
    ['fenceId','commandId','purpose','instanceId','dataScope'].some(key=>fence[key]!==context[key])||
    context.purpose==='service-stop'&&!isDeepStrictEqual(fence.serviceIdentity,context.serviceIdentity)||
    others.some(id=>!fence.owners?.includes(id))||fence.owners?.at(-1)!==base.id)throw Error('ingress_restart_admission_unconfirmed');
   const drain=await getAccess().drainForRestart(context);
   if(!drain)return null;
   drains.set(context.fenceId,drain);
   const lease=await base.acquire(context);
   if(!lease){finish(context);return null;}
   return {...lease,async release(...args){await lease.release(...args);if(args[0]!=='unknown')finish(context);}};
  },
  async reconcileRelease(context){await base.reconcileRelease(context);if(context.outcome!=='unknown')finish(context);},
  async abortAdmission(context){const result=await base.abortAdmission(context);if(['released','not-acquired'].includes(result?.status))finish(context);return result;},
 };
}
