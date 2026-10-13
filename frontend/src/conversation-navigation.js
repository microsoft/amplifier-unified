// Tab-local overrides survive reload without sharing writes with another client.
// sessionStorage copies on tab duplication, then each tab evolves independently.
export function createArtifactOverrides(storage=()=>sessionStorage,limit=128){
 const name='amplifier.artifactPresentation.v1';let rows;
 const load=()=>{
  if(rows)return;
  rows=new Map();
  try{const saved=JSON.parse(storage().getItem(name)||'[]');
   if(Array.isArray(saved))for(const entry of saved.slice(-limit)){
    if(Array.isArray(entry)&&typeof entry[0]==='string'&&['compact','inline'].includes(entry[1]))rows.set(...entry);
   }
  }catch{}
 };
 return {
  get(identity){load();return rows.get(identity)},
  set(identity,mode){
   load();if(!['compact','inline'].includes(mode))return false;
   rows.delete(identity);rows.set(identity,mode);
   while(rows.size>limit)rows.delete(rows.keys().next().value);
   try{storage().setItem(name,JSON.stringify([...rows]));return true}catch{return false}
  }
 };
}

export const inlineEditNotice='Finish or cancel the inline preview edit before leaving these messages.';
const targetFields=['viewId','resourceId','resourceRevision','generation'];
const sameTarget=(a,b)=>targetFields.every(field=>a?.[field]===b?.[field]);
const scopeOf=state=>JSON.stringify([state?.client?.hostInstanceId??null,state?.client?.id??state?.client?.clientId??null]);

// Only live mounts and edit intent, never private text or a second artifact store.
// The publication anchor belongs to the mount, not to the resource's first row.
export function createInlineOwnership(){
 const mounts=new Set(),listeners=new Set();
 const notify=()=>listeners.forEach(listener=>listener());
 return {
  subscribe(listener){listeners.add(listener);return ()=>listeners.delete(listener)},
  register(state,target,owner){
   const record={scope:scopeOf(state),target:{...target},...owner,dirty:!!target.dirty,sequence:0};
   mounts.add(record);return ()=>mounts.delete(record);
  },
  hasMounts:()=>mounts.size>0,
  dirty:()=>[...mounts].some(record=>record.dirty),
  intent(target,dirty){
   const tickets=[];
   for(const record of mounts)if(sameTarget(record.target,target)){
    const sequence=++record.sequence;
    // A clean intent is not a clean receipt. Rejection/uncertainty stays dirty.
    record.dirty=true;tickets.push({record,sequence,dirty});
   }
   return tickets;
  },
  settle(tickets,receipt){
   if(receipt?.accepted!==true)return;
   let changed=false;
   for(const {record,sequence,dirty} of tickets)if(mounts.has(record)&&record.sequence===sequence){
    changed ||= record.dirty!==!!dirty;record.dirty=!!dirty;
   }
   if(changed)notify();
  },
  blocksTarget:target=>[...mounts].some(record=>record.dirty&&sameTarget(record.target,target)),
  protectsPublication(owner){
   return [...mounts].some(record=>record.dirty&&record.sessionId===owner.sessionId&&record.messageId===owner.messageId&&
    record.version===owner.version&&record.target.resourceId===owner.resourceId);
  },
  blocksWindow(sessionId,messages){
   const ids=new Set((messages||[]).map(row=>row.id));
   return [...mounts].some(record=>record.dirty&&(record.sessionId!==sessionId||!ids.has(record.messageId)));
  },
  reconcile(state){
   let revoked=false;
   for(const record of mounts){
    if(record.scope!==scopeOf(state)||!(state?.canvasWorkspace?.views||[]).some(view=>sameTarget(record.target,view))){
     revoked ||= record.dirty;mounts.delete(record);
    }
   }
   return revoked;
  }
 };
}
export const inlineOwnership=createInlineOwnership();
export function normalizedAction(action,args={}){
 return action==='shell.command'?{action:args.action,args:args.args||{}}:{action,args};
}
export function leavesSettings(action,args={}){
 const command=normalizedAction(action,args);
 return ['session.select','session.draft','workspace.select','session.create','canvas.select','canvas.tabClose','canvas.show','canvas.openFile','canvas.openAttachment','canvas.openGif'].includes(command.action)||
  command.action==='canvas.visibility'&&command.args.open===false||
  command.action==='canvas.present'&&command.args.presentation?.canvas==='open';
}

// A bounded per-client display cache. Server state and execution stay authoritative.
export function createConversationNavigation(limit=32){
 const cache=new Map(),pending=new Map();let epoch;
 const selected=state=>state?.sessions?.find(row=>row.id===state.selectedSessionId);
 function remember(state){
  const nextEpoch=state?.client?.hostInstanceId;
  if(epoch!==undefined&&nextEpoch!==epoch){cache.clear();pending.clear()}
  epoch=nextEpoch;
  const row=selected(state);if(!row)return;
  cache.delete(row.id);cache.set(row.id,{session:{id:row.id,workspaceId:row.workspaceId,title:row.title,draft:row.draft}});
  while(cache.size>limit)cache.delete(cache.keys().next().value);
 }
 function begin(state,id,hint){
  // A dirty renderer must remain mounted until the server accepts navigation.
  if(inlineOwnership.hasMounts()||state?.canvasWorkspace?.views?.some(row=>row.dirty))return null;
  const row=state?.sessions?.find(row=>row.id===id)||cache.get(id)?.session||(hint?.id===id?hint:null);
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
  const {messages,workers,workerSummary,approvals,execution,streaming,historyActivity,messageWindow,executionWindow,runtimeEvents,generations,messageAnnotations,messageQuotes,...metadata}=summary;
  const session={...metadata,draft:saved?.session.draft??summary.draft,historyLoading:true,messages:[],workers:[],approvals:[]};
  return {...state,selectedSessionId:id,selectedWorkspaceId:session.workspaceId??state.selectedWorkspaceId,navigationPending:true,
   sessions:[...(state.sessions||[]).filter(row=>row.id!==id),session],
   view:{...state.view,draft:session.draft||''},
   canvas:{open:false},canvasWorkspace:{views:[]},canvasArtifacts:[]};
 }
 return {remember,begin,apply,settle:token=>pending.delete(token)};
}
