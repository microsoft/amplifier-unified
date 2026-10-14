// Bundled shell resources use the existing ordered state stream. A resource
// revision changes only its bounded value; app revisions do not invalidate it.
export function shellScope(state){
 const surface=state?.view?.workSurface||'chat';
 return [state?.selectedSessionId??null,state?.selectedWorkspaceId??null,surface,
  surface==='workspace'?state.view.workWorkspaceId??null:null,
  surface==='workspace'?state.view.workWorkspaceTab||'chats':null];
}

export const sameScope=(left,right)=>Array.isArray(left)&&Array.isArray(right)
 &&left.length===5&&right.length===5&&left.every((value,index)=>value===right[index]);

export function matchesResources(envelope,bootstrap,demand){
 return !!envelope&&envelope.clientId===bootstrap?.clientId
  &&envelope.bootstrapKey===bootstrap.shellBootstrapKey
  &&envelope.hostInstanceId===bootstrap.hostInstanceId
  &&envelope.demandId===demand.id&&sameScope(envelope.scope,demand.scope);
}

const navigation=new Set(['builtin.chats','builtin.workspaces']);
function validEntry(entry){
 return Number.isSafeInteger(entry.stateRevision)&&entry.stateRevision>=0
  &&entry.pending&&typeof entry.pending==='object'&&!Array.isArray(entry.pending)
  &&Object.values(entry.pending).every(row=>row&&['pending','retry'].includes(row.status)
   &&(row.retryAfterMs===undefined||Number.isFinite(row.retryAfterMs)&&row.retryAfterMs>=0));
}
export function resourceSnapshots(bootstrap,envelope,demand,previous={}){
 if(!matchesResources(envelope,bootstrap,demand))return null;
 const snapshots={};
 for(const instance of bootstrap.resolvedInstances){
  const generation=bootstrap.instanceGenerations[instance.id];
  const entry=envelope.instances?.[instance.id];
  if(entry&&entry.generation!==generation)continue;
  if(entry&&!validEntry(entry))return null;
  if(navigation.has(instance.package)){
   const resources=entry?.resources||{},context=resources.context?.value;
   // A hidden surface can keep its private edit owner, never its old pages.
   const retained=!demand.resources.length&&previous[instance.id]?.generation===generation
    ?{view:previous[instance.id].view}:{};
   const pending=Object.fromEntries(demand.resources
    .filter(name=>entry?.pending?.[name]).map(name=>[name,{...entry.pending[name]}]));
   const snapshot={...retained,...context,generation,navigationResources:[],navigationResourcePending:pending,
    navigationResourceScope:[envelope.hostInstanceId,envelope.bootstrapKey,envelope.demandId,generation,...envelope.scope]};
   for(const name of demand.resources){
    const resource=resources[name];if(!resource)continue;
    const {sidebarNavigation,...fields}=resource.value;
    Object.assign(snapshot,fields);
    if(sidebarNavigation)snapshot.sidebarNavigation={...snapshot.sidebarNavigation,...sidebarNavigation};
    snapshot.navigationResources.push(name);
   }
   snapshots[instance.id]=snapshot;
  }else if(!instance.package.startsWith('builtin.')&&entry?.resources?.customSnapshot){
   snapshots[instance.id]={...entry.resources.customSnapshot.value,generation};
  }
 }
 return snapshots;
}

// State frames are ordered; HTTP baselines can arrive after a newer frame.
// Keep newer records only within the exact same demand and generation. An
// omitted record stays omitted so it cannot masquerade as current coverage.
export function retainResourceVersions(previous,next){
 if(!previous||previous.hostInstanceId!==next.hostInstanceId||previous.clientId!==next.clientId
  ||previous.bootstrapKey!==next.bootstrapKey||previous.demandId!==next.demandId||!sameScope(previous.scope,next.scope))return next;
 const instances={};
 for(const [id,entry] of Object.entries(next.instances||{})){
  const old=previous.instances?.[id],resources={};
  // Every entry is a complete bounded snapshot, including pending state. A
  // late HTTP response must not restore an older retry after stream completion,
  // even when the completed page's value/revision did not change.
  if(old?.generation===entry.generation&&Number.isSafeInteger(entry.stateRevision)
   &&old.stateRevision>=entry.stateRevision){instances[id]=old;continue}
  for(const [name,record] of Object.entries(entry.resources||{})){
   const prior=old?.generation===entry.generation&&old.resources?.[name];
   resources[name]=prior?.revision>record.revision?prior:record;
  }
  instances[id]={...entry,resources};
 }
 return {...next,instances};
}

export function pendingSnapshots(bootstrap,previous){
 const snapshots={};
 for(const instance of bootstrap.resolvedInstances){
  const prior=previous.snapshots?.[instance.id],generation=bootstrap.instanceGenerations[instance.id];
  if(!prior||prior.generation!==generation)continue;
  if(navigation.has(instance.package)){
   const context=previous.shellResources?.instances?.[instance.id]?.resources?.context?.value||{};
   snapshots[instance.id]={...context,view:prior.view,generation,navigationResources:[]};
  }else if(!instance.package.startsWith('builtin.'))snapshots[instance.id]=prior;
 }
 return snapshots;
}
