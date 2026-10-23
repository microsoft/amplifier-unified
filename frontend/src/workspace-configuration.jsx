import React from 'react';
import {createPortal} from 'react-dom';
import {ConfigurationBoundary} from './configuration-scope';
import {AIConnections} from './ai-connections';
import {ProviderSettings,RoutingSettings} from './setup';
import {RegistrySettings} from './registry';
import {SettingsPageContext} from './settings-ui';
import {SettingsDraftProvider,useSettingsDrafts,useGuardedSettingsDraft} from './settings-drafts';

function GuardedWorkspaceSettings({state,act,children}){
 const defaults={workSurface:'chat',workWorkspaceId:null,workWorkspaceTab:'chats',workWorkspaceSettings:null,panel:null,settingsCanvas:false};
 const requested=Object.fromEntries(Object.entries(defaults).map(([key,value])=>[key,state.view?.[key]??value]));
 const view=useGuardedSettingsDraft(requested,JSON.stringify(requested),patch=>act('view.update',{patch}));
 return children({...state,view:{...state.view,...view}});
}

// This owner stays mounted above workspace/tab navigation, preserving private
// inputs while an external navigation request waits for the user's decision.
export function WorkspaceSettingsBoundary({state,act,children}){
 const drafts=useSettingsDrafts();
 return <SettingsDraftProvider value={drafts.context}>
  <GuardedWorkspaceSettings state={state} act={act}>{children}</GuardedWorkspaceSettings>
  {drafts.dialog&&createPortal(drafts.dialog,document.getElementById('amp-one'))}
 </SettingsDraftProvider>;
}

export function WorkspaceConfiguration({workspace,state,act}){
 const page=state.view?.workWorkspaceSettings;
 const setPage=value=>act('view.update',{patch:{workWorkspaceSettings:value}});
 const pages=[['ai-connections','AI connections & images'],['routing','Model rules'],['registries','Modules & sources']];
 return <section aria-label="Workspace configuration" className="a-work-details">
  <h3>Workspace settings</h3><p>Overrides for {workspace.name}. Global defaults continue to apply where no override is saved. Existing chats keep their configuration.</p>
  <div className="a-work-tabs" role="group" aria-label="Workspace settings pages">{pages.map(([id,label])=><button key={id} type="button" aria-pressed={page===id} onClick={()=>setPage(page===id?null:id)}>{label}</button>)}</div>
  {page&&<ConfigurationBoundary key={workspace.id} state={state} act={act} target={{kind:'workspace',workspaceId:workspace.id}}>{props=><SettingsPageContext.Provider value={page}>
   {page==='ai-connections'?<AIConnections {...props} navigate={setPage}/>:page==='providers'?<ProviderSettings {...props} navigate={setPage}/>:page==='routing'?<RoutingSettings {...props}/>:<RegistrySettings {...props}/>}
  </SettingsPageContext.Provider>}</ConfigurationBoundary>}
 </section>;
}
