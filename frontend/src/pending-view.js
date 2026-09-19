// View changes paint locally while their shared actions keep their normal order.
// Track each request, rather than each field: an older acknowledgement or error
// must never erase a newer choice for the same control.
export function createPendingView(){
 const patches=new Map();
 return {
  add(patch){const token=Symbol();patches.set(token,{...patch});return token},
  settle(token){patches.delete(token)},
  apply(state){
   if(!state||!patches.size)return state;
   let view={...state.view};
   for(const patch of patches.values())view={...view,...patch};
   return {...state,view};
  },
 };
}
