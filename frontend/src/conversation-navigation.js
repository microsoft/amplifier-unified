// A bounded per-client display cache. Server state and execution stay authoritative.
export function createConversationNavigation(limit=32){
 const cache=new Map(),pending=new Map();let epoch;
 const selected=state=>state?.sessions?.find(row=>row.id===state.selectedSessionId);
 function remember(state){
  const nextEpoch=state?.client?.hostInstanceId;
  if(epoch!==undefined&&nextEpoch!==epoch){cache.clear();pending.clear()}
  epoch=nextEpoch;
  const row=selected(state);if(!row)return;
  cache.delete(row.id);cache.set(row.id,{session:row,canvas:state.canvas,canvasWorkspace:state.canvasWorkspace,canvasArtifacts:state.canvasArtifacts});
  while(cache.size>limit)cache.delete(cache.keys().next().value);
 }
 function begin(state,id){
  // A dirty renderer must remain mounted until the server accepts navigation.
  if(state?.canvasWorkspace?.views?.some(row=>row.dirty))return null;
  const row=state?.sessions?.find(row=>row.id===id)||cache.get(id)?.session;
  if(!row)return null;
  const current=selected(state);
  if(current)remember({...state,sessions:state.sessions.map(row=>row.id===current.id?{...row,draft:state.view?.draft||''}:row)});
  const token=Symbol();pending.set(token,{id,row});return token;
 }
 function apply(state){
  if(!state||!pending.size)return state;
  const {id,row}=Array.from(pending.values()).at(-1);
  if(state.selectedSessionId===id)return {...state,navigationPending:true};
  const saved=cache.get(id),summary=state.sessions?.find(row=>row.id===id)||row;
  const {messages,workers,approvals,...metadata}=summary;
  const full=summary.messageWindow||summary.executionWindow||summary.messages?.length||summary.approvals?.length||summary.streaming;
  const session=full?summary:saved?{...saved.session,...metadata}:{...summary,historyLoading:true,messages:[],workers:[],approvals:[]};
  return {...state,selectedSessionId:id,selectedWorkspaceId:session.workspaceId??state.selectedWorkspaceId,navigationPending:true,
   sessions:[...(state.sessions||[]).filter(row=>row.id!==id),session],
   view:{...state.view,draft:session.draft||''},
   canvas:saved?.canvas||{open:false},canvasWorkspace:saved?.canvasWorkspace||{views:[]},canvasArtifacts:saved?.canvasArtifacts||[]};
 }
 return {remember,begin,apply,settle:token=>pending.delete(token)};
}
