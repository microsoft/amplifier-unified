import React from 'react';
import {ConfigurationBoundary} from './configuration-scope';
import {AIConnections} from './ai-connections';
import {ProviderSettings,RoutingSettings} from './setup';
import {RegistrySettings} from './registry';
import {SettingsPageContext} from './settings-ui';

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
