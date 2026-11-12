// A panel can unmount while its preview request is in flight. Serialize only
// this client's preview requests and cleanup, including across panel mounts.
const pending=new WeakMap();
function ordered(owner,operation){
 const previous=pending.get(owner)||Promise.resolve();
 const task=previous.catch(()=>{}).then(operation);
 pending.set(owner,task);
 return task.finally(()=>{if(pending.get(owner)===task)pending.delete(owner)});
}
async function cancelOwnedPreview(owner,act){
 if(!owner.active)return {accepted:true,skipped:true};
 const receipt=await act('appearance.preview.cancel',{});
 if(receipt?.accepted!==true)throw Error(receipt?.error||'Could not confirm preview cancellation.');
 // Keep ownership after a failed or uncertain cancellation so explicit Retry
 // and section cleanup do not silently skip a preview that may still be shown.
 owner.active=false;
 return receipt;
}
export function clearAppearancePreview(owner,act){
 return ordered(owner,()=>cancelOwnedPreview(owner,act));
}
export function previewAppearance(owner,act,args,isCurrent){
 return ordered(owner,async()=>{
  if(!isCurrent())return;
  const wasActive=owner.active;
  let requested=false;
  try{
   requested=true;
   // A lost response can still have reached the host. Retain cleanup ownership
   // until cancellation is confirmed; never replay the preview automatically.
   owner.active=true;
   const receipt=await act('appearance.preview',args);
   if(receipt?.accepted!==true)throw Object.assign(Error(receipt?.error||'Could not confirm appearance preview.'),{accepted:receipt?.accepted});
   return receipt;
  }catch(error){
   // A definitive refusal did not create a new preview. A transport error is
   // different: preserve cleanup ownership for that potentially accepted request.
   if(error?.accepted===false)owner.active=wasActive;
   throw error;
  }finally{
   // Even a lost/rejected response may arrive after navigation. End only this
   // queued preview before a later appearance request is allowed to start.
   if(!isCurrent()&&requested)await cancelOwnedPreview(owner,act);
  }
 });
}
