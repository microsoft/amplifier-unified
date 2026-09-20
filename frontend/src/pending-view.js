// View changes paint locally while their shared actions keep their normal order.
// Track each request, rather than each field: an older acknowledgement or error
// must never erase a newer choice for the same control.
export function createPendingView(){
 const patches=new Map();
 return {
  add(patch,sessionId){const token=Symbol();patches.set(token,{patch:{...patch},sessionId});return token},
  settle(token){patches.delete(token)},
  bindDraft(token,sessionId){const entry=patches.get(token);if(entry&&Object.hasOwn(entry.patch,'draft'))entry.sessionId=sessionId},
  apply(state){
   if(!state||!patches.size)return state;
   let view={...state.view};
   for(const {patch,sessionId} of patches.values()){const values={...patch};if(sessionId!==undefined&&sessionId!==state.selectedSessionId)delete values.draft;view={...view,...values}};
   return {...state,view};
  },
 };
}
