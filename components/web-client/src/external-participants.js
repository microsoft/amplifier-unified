export function draftExternalAgentIds(draft){
 return Array.isArray(draft?.externalAgentIds)&&draft.externalAgentIds.length?draft.externalAgentIds:draft?.externalAgentId?[draft.externalAgentId]:[];
}

export function externalParticipants(session){
 const agent=session?.externalAgent;
 return Array.isArray(agent?.participants)?agent.participants:agent?.id?[agent]:[];
}

export function isSharedExternalConversation(session){
 return session?.externalAgent?.participation==='shared';
}

export function externalAgentLabel(session,id,savedLabel){
 return savedLabel||externalParticipants(session).find(agent=>agent.id===id)?.label||id||'External agent';
}

export function externalMessageAttribution(message,session){
 if(message.role==='user')return message.recipientAgentId?`To ${externalAgentLabel(session,message.recipientAgentId,message.recipientAgentLabel)}`:'';
 if(message.role!=='assistant'||!session?.externalAgent)return '';
 const id=message.agentId||(!session.externalAgent.participants?session.externalAgent.id:null);
 return externalAgentLabel(session,id,message.agentLabel);
}
