// The built-in sidebar stays visible while the main browsing surface changes.
// Custom modules retain their complete snapshot contract.
export function navigationResources(state){
 const resources=['pinnedNavigation','recentNavigation','workspaceShortcuts'];
 const view=state?.view||{},surface=view.workSurface||'chat';
 if(surface==='chats')resources.push('sidebarSearch');
 if(surface==='workspace'&&(view.workWorkspaceTab||'chats')==='chats')resources.push('sidebarWorkspace');
 if(surface==='workspaces')resources.push('workspaceExplorer');
 return resources.sort();
}

export function navigationDemandKey(state){
 const view=state?.view||{},surface=view.workSurface||'chat';
 return JSON.stringify([navigationResources(state),surface,
  surface==='workspace'?view.workWorkspaceId??null:null,
  surface==='workspace'?view.workWorkspaceTab||'chats':null]);
}

export function hasNavigationResource(snapshot,resource){
 return Array.isArray(snapshot?.navigationResources)&&snapshot.navigationResources.includes(resource);
}
