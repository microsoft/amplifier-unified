import React from 'react';
import './new-chat.css';

export function newChatSetup(state){
 const surface=state?.view?.workSurface||'chat';
 const id=surface==='workspace'?state?.view?.workWorkspaceId:surface==='chat'?state?.selectedWorkspaceId:null;
 const workspace=state?.workspaces?.find(row=>row.id===id)?.path||'';
 const draft=state?.view?.newSessionDraft;
 const path=draft?.workspace??workspace;
 return {title:'',workspace,bundle:'',selection:{},...draft,location:draft?.location||{kind:path?'workspace':'managed'}};
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
