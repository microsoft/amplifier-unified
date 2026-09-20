// Public host fixtures for the extracted builtins. Production snapshots come
// from the bounded /api/shell projection; these small fixtures are synthetic.
export function shellFor(state,act){
 const composition={instances:[{id:'workspaces',package:'builtin.workspaces'},{id:'chats',package:'builtin.chats'}],presentation:{}};
 const snapshots=Object.fromEntries(composition.instances.map(instance=>{
  const draft=state.view?.workspaceDraft||{},chat=draft.mode?.startsWith('chat-');
  const view={...state.view,workspaceDraft:(instance.id==='chats'?chat:!chat)?draft:{}};
  return [instance.id,{...state,view}];
 }));
 return {ready:true,composition,data:{revision:0,snapshots,packages:{}},recover:()=>{},report:()=>{},
  hostFor:instance=>({apiVersion:'1.0',clientId:'test-client',instanceId:instance.id,subscribe:()=>()=>{},getSnapshot:()=>snapshots[instance.id],dispatch:act,setDirty:async()=>{}})};
}
