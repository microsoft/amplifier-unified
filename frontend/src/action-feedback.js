// Associate a user gesture with its shared action, without changing action
// ordering or attaching background refreshes to an unrelated focused button.
import {activityRegion,beginRegionActivity} from './activity-feedback.js';
export function createActionFeedback(){
 let gesture=null;
 const pending=new Map();
 function capture(event){
  const button=event.submitter||event.target?.closest?.('button,select,input[type=checkbox],input[type=radio],input[type=file]');
  if(!button)return;
  // A change/submit is part of the initiating click, not another click.
  if(event.type==='click'&&pending.has(button)){event.preventDefault();event.stopPropagation();return}
  const current={button,action:button.dataset.action||button.closest('[data-action]')?.dataset.action};
  gesture=current;
  // React may handle the gesture on an outer delegated listener. A microtask
  // can run between native listeners, before React has dispatched the action.
  setTimeout(()=>{if(gesture===current)gesture=null},0);
 }
 function begin(action){
  // Local navigation and edits already paint immediately. Do not lock them
  // behind persistence (closing a panel must always remain available).
  if(action==='view.update')return ()=>{};
  const button=gesture?.action===action?gesture.button:null;
  if(!button)return ()=>{};
  const finishRegion=action==='view.update'?()=>{}:beginRegionActivity(activityRegion(button));
  const record=pending.get(button)||{count:0,busy:button.getAttribute('aria-busy')};
  record.count++;pending.set(button,record);
  button.setAttribute('aria-busy','true');
  button.setAttribute('data-action-pending','');
  let done=false;
  return ()=>{
   if(done)return;done=true;
   finishRegion();
   if(--record.count)return;
   pending.delete(button);
   for(const [name,value] of [['aria-busy',record.busy]]){
    if(value===null)button.removeAttribute(name);else button.setAttribute(name,value);
   }
   button.removeAttribute('data-action-pending');
  };
 }
 function attach(root){
  root.addEventListener('click',capture,true);root.addEventListener('submit',capture,true);root.addEventListener('change',capture,true);
  return ()=>{root.removeEventListener('click',capture,true);root.removeEventListener('submit',capture,true);root.removeEventListener('change',capture,true)};
 }
 return {attach,begin};
}
