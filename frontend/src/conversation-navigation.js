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
 const matching=target=>[...mounts].filter(record=>sameTarget(record.target,target));
 const held=record=>!!record.transition;
 const protectedRecord=record=>record.dirty||held(record);
 const releaseTransition=hold=>{
  if(hold.closed)return;
  hold.closed=true;hold.adapter.release(hold.id);
  for(const record of mounts)if(record.transition===hold){delete record.transition;record.onTransition?.(null)}
  for(const done of hold.waiters)done();hold.waiters.clear();notify();
 };
 const successor=(hold,view)=>view?.viewId===hold.target.viewId&&view.resourceId===hold.target.resourceId&&
  view.publishedVersion===hold.version&&view.followLatest===hold.followLatest&&
  view.selectedVersion===(hold.followLatest?null:hold.version);
 return {
  subscribe(listener){listeners.add(listener);return ()=>listeners.delete(listener)},
  register(state,target,owner){
   const record={scope:scopeOf(state),target:{...target},...owner,dirty:!!target.dirty,sequence:0};
   mounts.add(record);return ()=>mounts.delete(record);
  },
  registerRenderer(target,adapter){
   const record={target:{...target},adapter,renderer:true,dirty:false};
   mounts.add(record);return ()=>{mounts.delete(record)};
  },
  hasMounts:()=>[...mounts].some(record=>!record.renderer),
  dirty:()=>[...mounts].some(protectedRecord),
  transition:target=>matching(target).find(held)?.transition,
  async prepareTransition(target,id,followLatest,state){
   const records=matching(target),existing=records.find(held)?.transition;
   if(existing){
    if(existing.id!==id||existing.followLatest!==followLatest)throw Error('Reconcile the existing version change before starting another.');
    return existing; // Explicit exact retry, never a new switch or replayed tool.
   }
   const owner=records.find(record=>!record.renderer),adapter=records.find(record=>record.adapter)?.adapter;
   const view=state?.canvasWorkspace?.views?.find(row=>sameTarget(row,target));
   if(!owner||!adapter||!view||owner.scope!==scopeOf(state)||view.dirty||records.some(record=>record.dirty))throw Error(inlineEditNotice);
   const hold={id,target:{...target},followLatest,version:owner.version,scope:owner.scope,
    sessionId:owner.sessionId,adapter,waiters:new Set(),closed:false};
   for(const record of records){record.transition=hold;record.onTransition?.(hold)}
   notify();
   try{hold.proof=await adapter.prepare(id);return hold}
   catch(error){releaseTransition(hold);throw error}
  },
  validateTransition(hold,state){
   if(hold.closed||hold.scope!==scopeOf(state)||hold.sessionId!==state?.selectedSessionId)throw Error('This preview scope changed.');
   // An uncertain retry only reconciles the identical admitted command. The
   // old frame is still frozen; its target can already have an SSE successor.
   if(hold.sent)return;
   const view=state?.canvasWorkspace?.views?.find(row=>sameTarget(row,hold.target));
   if(!view||view.dirty||matching(hold.target).some(record=>record.dirty)||!hold.adapter.validate(hold.id,hold.proof))throw Error(inlineEditNotice);
  },
  refuseTransition:releaseTransition,
  adoptTransition(target,id){
   const hold=matching(target).find(held)?.transition;
   if(hold?.id===id&&hold.reconciled)releaseTransition(hold);
  },
  async completeTransition(hold,receipt,state){
   const next=receipt?.result;
   if(receipt?.accepted!==true||!successor(hold,next))throw Object.assign(Error('The host did not confirm the exact publication view.'),{unconfirmed:true});
   hold.next={...next};
   if(state?.canvasWorkspace?.views?.some(view=>sameTarget(view,next))){hold.reconciled=true;return}
   // Receipt is not successor replication. Keep the old DOM frozen if state
   // recovery cannot establish the exact successor; no compensating follow.
   await new Promise((resolve,reject)=>{
    const done=()=>{clearTimeout(timer);resolve()};
    const timer=setTimeout(()=>{hold.waiters.delete(done);reject(Object.assign(Error('Version change accepted; waiting for its exact view state. Retry the same change to reconcile.'),{unconfirmed:true}))},1800);
    hold.waiters.add(done);
   });
   if(hold.closed)throw Error('Preview access was removed during the version change.');
  },
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
  blocksTarget:target=>matching(target).some(protectedRecord),
  protectsPublication(owner){
   return [...mounts].some(record=>protectedRecord(record)&&record.sessionId===owner.sessionId&&record.messageId===owner.messageId&&
    record.version===owner.version&&record.target.resourceId===owner.resourceId);
  },
  blocksWindow(sessionId,messages){
   const ids=new Set((messages||[]).map(row=>row.id));
   return [...mounts].some(record=>!record.renderer&&protectedRecord(record)&&(record.sessionId!==sessionId||!ids.has(record.messageId)));
  },
  reconcile(state){
   let revoked=false;
   for(const record of mounts){
    const views=state?.canvasWorkspace?.views||[],hold=record.transition;
    const scopeMatches=record.renderer||record.scope===scopeOf(state)&&
     (state?.selectedSessionId===undefined||record.sessionId===state.selectedSessionId);
    if(scopeMatches&&hold&&views.some(view=>sameTarget(record.target,view)||successor(hold,view))){
     if(hold.next&&views.some(view=>sameTarget(hold.next,view))){
      hold.reconciled=true;for(const done of hold.waiters)done();hold.waiters.clear();
     }
     continue;
    }
    if(!scopeMatches||!views.some(view=>sameTarget(record.target,view))){
     revoked ||= protectedRecord(record);if(hold)releaseTransition(hold);mounts.delete(record);
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
