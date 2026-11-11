// A panel can unmount while its preview request is in flight. Serialize only
// this client's preview requests and cleanup, including across panel mounts.
const pending=new WeakMap();
function ordered(owner,operation){
 const previous=pending.get(owner)||Promise.resolve();
 const task=previous.catch(()=>{}).then(operation);
 pending.set(owner,task);
 return task.finally(()=>{if(pending.get(owner)===task)pending.delete(owner)});
}
export function clearAppearancePreview(owner,act){
 return ordered(owner,async()=>{
  if(!owner.active)return {accepted:true,skipped:true};
  owner.active=false;
  return act('appearance.preview.cancel',{});
 });
}
export function previewAppearance(owner,act,args,isCurrent){
 return ordered(owner,async()=>{
  if(!isCurrent())return;
  let requested=false;
  try{
   requested=true;
   const receipt=await act('appearance.preview',args);
   if(receipt?.accepted===false)throw Error(receipt.error||'Could not preview appearance.');
   owner.active=true;
   return receipt;
  }finally{
   // Even a lost/rejected response may arrive after navigation. End only this
   // queued preview before a later appearance request is allowed to start.
   if(!isCurrent()&&requested){owner.active=false;await act('appearance.preview.cancel',{})}
  }
 });
}
