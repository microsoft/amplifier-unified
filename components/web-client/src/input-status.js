// Presentation consumes exact host evidence, never ambient session activity.
const phases=new Set(['working','included','queued','waiting','done','failed','unknown']);
export function inputStatus(message){
 if(message.role!=='user'||message.observation||message.via==='peer'||message.inputOrigin==='peer')return null;
 if(message.nativeInputAmbiguous||message.inputId&&message.nativeInputId&&message.inputId!==message.nativeInputId)return null;
 // Saved transcript rows have no command authority. Only their exact native
 // binding and a matching host observation can supply a status.
 if(message.displayOnly&&(typeof message.nativeInputId!=='string'||!message.nativeInputId.trim()))return null;
 const inputId=message.inputId||message.nativeInputId;
 if(!inputId)return null;
 const status=message.inputStatus;
 if(status?.version===1&&status.inputId===inputId&&phases.has(status.phase)&&typeof status.basis==='string'&&status.basis.trim()&&status.basis!=='no_evidence'){
  return {...status,inputId};
 }
 if(message.displayOnly)return null;
 // Delivery remains independent of execution. A positive receipt is not "read".
 if(message.delivery?.status==='accepted')return {inputId,phase:'received',basis:'delivery'};
 if(message.localDelivery||message.delivery&&message.delivery.status!=='accepted'||message.steering)return null;
 return {inputId,phase:'unknown',basis:'missing'};
}
export function statusLabel(status,connected=true){
 if(status.phase==='working'&&!connected)return 'Working last observed · connection lost';
 return {received:'Received',queued:'Queued',included:'Added to context',working:'Working',waiting:'Waiting',done:'Turn finished',failed:'Turn failed',unknown:'Status unknown'}[status.phase];
}
export function workingDuration(status,now=Date.now()/1000,connected=true){
 if(!Number.isFinite(status.startedAt)||status.startedAt<=0)return null;
 const end=status.endedAt??(status.phase==='working'&&connected?now:status.updatedAt);
 if(!Number.isFinite(end)||end<status.startedAt)return null;
 const seconds=Math.floor(end-status.startedAt);
 return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
}