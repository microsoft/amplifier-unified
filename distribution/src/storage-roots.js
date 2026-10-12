import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
/** Composition declares only paths it owns. Categories confer no deletion rights. */
export function storageRoots(stateDirectory,native,managedSessionRoot){
 const app={id:'app',scope:'app',label:'Amplifier Unified',path:stateDirectory};
 const roots=[app,...(native?.roots??[])];
 const contentRoots=[{...app,category:'other'},...(native?.contentVersion===1?native.contentRoots??[]:(native?.roots??[]).map(root=>({...root,category:root.scope==='history'?'sessions':'other'})))];
 const groups={sessions:['host','resources','capabilities/media','capabilities/recall','capabilities/coordination','capabilities/feedback/uploads'],workspaces:['managed','workspaces','capabilities/worktrees/git/checkouts'],backups:['backups','reset-backups'],runtime:['runtime','updates'],cache:['cache','logs']};
 const labels={host:'Conversation records',resources:'Files and outputs','capabilities/media':'Voice conversations','capabilities/recall':'Saved memory','capabilities/coordination':'Delegated work','capabilities/feedback/uploads':'Feedback attachments',managed:'Managed conversation files',workspaces:'Workspace folders','capabilities/worktrees/git/checkouts':'Managed project copies',backups:'Private backups','reset-backups':'Recovery copies',runtime:'Runtime environments',updates:'Application updates',cache:'Cached data',logs:'Diagnostic logs'};
 for(const [category,paths] of Object.entries(groups))for(const relative of paths)contentRoots.push({id:'unified:'+relative,scope:'app',label:labels[relative],path:join(stateDirectory,relative),category,optional:true});
 if(managedSessionRoot&&managedSessionRoot!==join(stateDirectory,'managed'))contentRoots.push({id:'managed-conversations',scope:'app',label:'Managed conversation files',path:managedSessionRoot,category:'workspaces',optional:true});
 contentRoots.push({id:'installed-runtime',scope:'app',label:'Installed application',path:fileURLToPath(new URL('..',import.meta.url)),category:'runtime'});
 return {roots,contentVersion:1,contentRoots,...(!native?{omissions:['Shared native history storage is unavailable from this host']}: {})};
}
