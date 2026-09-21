import React from 'react';
import {FolderOpen,SlidersHorizontal} from 'lucide-react';
import {PathField,BundlePicker} from './settings-ui';
import './new-chat.css';

export function newChatSetup(state){
 const workspace=state?.workspaces?.find(row=>row.id===state.selectedWorkspaceId);
 return {title:'',workspace:workspace?.path||state?.settings?.workspace||'',bundle:'',selection:{},...state?.view?.newSessionDraft};
}

export function NewChatSetup({state,act}){
 const setup=newChatSetup(state),choice=setup.selection||{};
 const edit=patch=>act('view.update',{patch:{newSessionDraft:{...setup,...patch}}});
 const select=patch=>edit({selection:{...choice,...patch}});
 return <section className="a-new-chat-setup" aria-label="New chat settings">
  <h2>New chat</h2><p>Choose where and how to work. Your chat starts when you send a message.</p>
  <div className="a-new-chat-workspace"><label htmlFor="new-chat-workspace"><FolderOpen/>Workspace</label>
   <PathField id="new-chat-workspace" value={setup.workspace} directory state={state} act={act} onChange={workspace=>edit({workspace})}/>
  </div>
  <details className="a-new-chat-options"><summary><SlidersHorizontal/>Chat settings</summary>
   <label htmlFor="new-chat-name">Name <span className="a-caption">optional</span></label>
   <input id="new-chat-name" maxLength={200} value={setup.title} placeholder="Name this chat automatically" data-action="view.update" onChange={e=>edit({title:e.target.value})}/>
   <label className="a-inline-checkbox"><input type="checkbox" checked={!setup.bundle} data-action="view.update" onChange={e=>edit({bundle:e.target.checked?'':state.bundleDefaults?.effective||state.settings?.bundle||'work'})}/>Use the workspace’s default bundle</label>
   {!!setup.bundle&&<><label htmlFor="new-chat-bundle">Bundle</label><BundlePicker id="new-chat-bundle" value={setup.bundle} state={state} act={act} onChange={bundle=>edit({bundle})}/></>}
   <label className="a-inline-checkbox"><input type="checkbox" checked={!Object.keys(choice).length} data-action="view.update" onChange={e=>edit({selection:e.target.checked?{}:{instance:'',model:''}})}/>Use the bundle’s model</label>
   {!!Object.keys(choice).length&&<div className="a-new-chat-model">
    <div><label htmlFor="new-chat-provider">Provider</label><input id="new-chat-provider" value={choice.instance||''} placeholder="Provider configured in this bundle" data-action="view.update" onChange={e=>select({instance:e.target.value})}/></div>
    <div><label htmlFor="new-chat-model">Model</label><input id="new-chat-model" value={choice.model||''} placeholder="Model name" data-action="view.update" onChange={e=>select({model:e.target.value})}/></div>
    <div><label htmlFor="new-chat-effort">Reasoning effort <span className="a-caption">optional</span></label><input id="new-chat-effort" value={choice.effort||''} placeholder="Provider default" data-action="view.update" onChange={e=>select({effort:e.target.value})}/></div>
   </div>}
  </details>
 </section>;
}
