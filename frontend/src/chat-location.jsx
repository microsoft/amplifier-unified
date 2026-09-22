import React from 'react';
import {FolderOpen} from 'lucide-react';
import {PathField} from './settings-ui';
import './chat-location.css';

export function ChatLocation({state,act,setup,onChange}){
 const kind=setup.location?.kind||'workspace';
 const change=location=>onChange({location:{kind:location},workspace:location==='managed'?'':state.workspaces?.find(row=>row.id===state.selectedWorkspaceId)?.path||state.settings?.workspace||''});
 return <div className="a-chat-location">
  <div role="group" aria-label="Chat location" className="a-location-options">
   <button type="button" aria-pressed={kind==='managed'} data-action="view.update" onClick={()=>change('managed')}>No workspace</button>
   <button type="button" aria-pressed={kind==='workspace'} data-action="view.update" onClick={()=>change('workspace')}>Workspace</button>
  </div>
  {kind==='managed'?<p className="a-caption">Files created in this chat are saved by Amplifier.</p>:<div className="a-new-chat-workspace"><label htmlFor="new-chat-workspace"><FolderOpen/>Workspace</label>
   <PathField id="new-chat-workspace" value={setup.workspace} directory state={state} act={act} onChange={workspace=>onChange({workspace})}/>
  </div>}
 </div>;
}
