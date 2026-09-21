const pick=(value,keys)=>Object.fromEntries(keys.map(key=>[key,value?.[key]]));

// Custom navigation scopes are covered by the host's complete-catalog key.
// Local selection/canvas can also change through a narrow client-only receipt.
export function shellRefreshKey(state){
 if(!state)return null;
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
 return JSON.stringify([state.shellDataKey??state.revision,state.shellChangeToken,
  state.selectedSessionId,state.selectedWorkspaceId,!!state.runtime?.available,
  pick(session,['id','title','status','autoName','bundle','workspaceId','naming']),
  pick(state.canvas,['id','kind','title','open'])]);
}
