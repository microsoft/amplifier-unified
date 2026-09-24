import React from 'react';
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

export function NewChatSetup(){
 return <section className="a-new-chat-setup" aria-label="New chat"><img src="/branding/icons/amplifier-icon-128.png" alt=""/><h2>What shall we work on?</h2><p>Bring an idea, a question, or a file.</p></section>;
}
