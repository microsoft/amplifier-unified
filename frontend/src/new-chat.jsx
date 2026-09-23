import React from 'react';
import {ChatLocation} from './chat-location';
import './new-chat.css';

export function newChatSetup(state){
 const workspace=state?.workspaces?.find(row=>row.id===state.selectedWorkspaceId);
 const current=state?.sessions?.find(row=>row.id===state.selectedSessionId);
 const managed=(current?.location?.kind==='managed'&&!state.selectedWorkspaceId)||(state?.view?.navChatScope==='all'&&state?.view?.navLocationFilter==='managed');
 if(managed&&!state?.view?.newSessionDraft)return {title:'',workspace:'',location:{kind:'managed'},bundle:'',selection:{}};
 return {title:'',workspace:workspace?.path||state?.settings?.workspace||'',bundle:'',selection:{},...state?.view?.newSessionDraft};
}

export function draftDefaultsKey(setup){
 return JSON.stringify([setup.workspace,setup.bundle||'',...(setup.location?.kind==='managed'?['managed']:[])]);
}

export function draftDefaults(state){
 const setup=newChatSetup(state);
 return state.draftDefaults?.[draftDefaultsKey(setup)]||{};
}

export function NewChatSetup({state,act}){
 const setup=newChatSetup(state);
 const edit=patch=>act('view.update',{patch:{newSessionDraft:{...setup,...patch}}});
 return <section className="a-new-chat-setup" aria-label="New chat settings">
  <h2>New chat</h2><p>Choose where and how to work. Your chat starts when you send a message.</p>
  <ChatLocation state={state} act={act} setup={setup} onChange={edit}/>
 </section>;
}
