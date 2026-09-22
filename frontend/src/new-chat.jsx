import React from 'react';
import {FolderOpen} from 'lucide-react';
import {PathField} from './settings-ui';
import './new-chat.css';

export function newChatSetup(state){
 const workspace=state?.workspaces?.find(row=>row.id===state.selectedWorkspaceId);
 return {title:'',workspace:workspace?.path||state?.settings?.workspace||'',bundle:'',selection:{},...state?.view?.newSessionDraft};
}

export function draftDefaults(state){
 const setup=newChatSetup(state);
 return state.draftDefaults?.[JSON.stringify([setup.workspace,setup.bundle||''])]||{};
}

export function NewChatSetup({state,act}){
 const setup=newChatSetup(state);
 const edit=patch=>act('view.update',{patch:{newSessionDraft:{...setup,...patch}}});
 return <section className="a-new-chat-setup" aria-label="New chat settings">
  <h2>New chat</h2><p>Choose where and how to work. Your chat starts when you send a message.</p>
  <div className="a-new-chat-workspace"><label htmlFor="new-chat-workspace"><FolderOpen/>Workspace</label>
   <PathField id="new-chat-workspace" value={setup.workspace} directory state={state} act={act} onChange={workspace=>edit({workspace})}/>
  </div>
 </section>;
}
