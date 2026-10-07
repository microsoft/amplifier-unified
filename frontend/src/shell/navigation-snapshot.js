// Selection belongs to the conversation controller, not the last shell fetch.
export function navigationSnapshot(snapshot,state){
 if(!state)return snapshot;
 const pending=!!state.navigationPending;
 if(snapshot.selectedSessionId===state.selectedSessionId&&snapshot.selectedWorkspaceId===state.selectedWorkspaceId&&!!snapshot.navigationPending===pending)return snapshot;
 return {...snapshot,selectedSessionId:state.selectedSessionId,selectedWorkspaceId:state.selectedWorkspaceId,navigationPending:pending};
}
