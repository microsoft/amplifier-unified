import React from 'react';
import {ModelControl,useNextMessageConfiguration} from './chat-controls';
import {BundleControl} from './bundle-controls';
import {draftExternalAgentIds,externalParticipants,isSharedExternalConversation} from './external-participants';

/** One quiet model/effort entry below the field at every conversation width. */
export function ComposerOptions({state,session,act,working,detail,recipientAgentId,onRecipientChange}){
 // One dialog owns one acknowledged revision chain across its model and bundle.
 // External client revisions still cannot replace a touched form's captured one.
 const nextMessageControl=useNextMessageConfiguration(session,act);
 if(session?.externalAgent){
  if(isSharedExternalConversation(session))return <span className="a-caption" data-external-agent>Everyone in this chat can reply. Address an agent by name when useful.</span>;
  const agents=externalParticipants(session);
  if(agents.length>1)return <label className="a-caption" data-external-agent>Send to <select aria-label="Message recipient" value={recipientAgentId||''} disabled={working||!onRecipientChange} onChange={event=>{if(!working)onRecipientChange?.(event.target.value)}}>
   <option value="" disabled>Choose an agent</option>
   {recipientAgentId&&!agents.some(agent=>agent.id===recipientAgentId)&&<option value={recipientAgentId} disabled>{recipientAgentId} — unavailable</option>}
   {agents.map(agent=><option key={agent.id} value={agent.id}>{agent.label||agent.id}</option>)}
  </select></label>;
  return <span className="a-caption" data-external-agent>{session.externalAgent.label||session.externalAgent.id}</span>;
 }
 if(!session&&draftExternalAgentIds(state?.view?.newSessionDraft).length)return <span className="a-caption" data-external-agent>Model and tools are managed by the external agents.</span>;
 const props={state,session,act,working,nextMessageControl};
 return <ModelControl {...props} compact detail={detail}>
  <BundleControl {...props} compact embedded/>
 </ModelControl>;
}
