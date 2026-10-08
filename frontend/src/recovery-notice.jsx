import React,{useState} from 'react';
import {DetailText} from './conversation-detail';

export function recoveryPresentation(message,session){
 const facts=message.observation?.recovery||{},callId=facts.call_id||message.observation?.call_id;
 const node=message.recoveryResult||(callId?(session?.execution?.nodes||[]).find(n=>n.kind==='tool'&&n.toolCallId===callId):null);
 const worker=callId?(session?.workers||[]).find(w=>w.callId===callId||w.toolCallId===callId):null;
 const status=({returned:'Result returned',cancelled:'Work cancelled',interrupted:'Work interrupted',failed:'Work failed',pending:'Outcome unknown'})[facts.status]||'Saved work notice';
 const uncertain=facts.outcome==='unconfirmed'||['cancelled','interrupted','pending'].includes(facts.status);
 const explanation=uncertain?'The final outcome is unconfirmed. Inspect the saved work before deciding whether to continue.':facts.reason==='restored_evidence'?'A saved call or result was restored to this chat.':facts.reason==='changed_evidence'?'A saved result replaced an earlier pending record.':'Saved work was found when this chat resumed.';
 return {facts,node,worker,status,explanation};
}

export function RecoveryNotice({message,session,state,act}){
 const [resultOpened,setResultOpened]=useState(false);
 const {facts,node,worker,status,explanation}=recoveryPresentation(message,session);
 const workerSession=worker&&(state.sessions||[]).find(s=>s.id===worker.id||s.runtimeSessionId===worker.sessionId);
 const result=node?.output,reference=node?.outputDetail;
 return <div className="a-recovery-notice">
  <p><strong>{worker?.name||worker?.agent||node?.label||'Background work'} · {status}</strong></p>
  <p>{explanation} This notice does not mean the work ran again.</p>
  {workerSession&&<button type="button" className="a-link" data-action="session.select" onClick={()=>act('session.select',{id:workerSession.id})}>Open worker chat</button>}
  {(typeof result==='string'||reference)&&<details onToggle={e=>{if(e.currentTarget.open)setResultOpened(true)}}><summary>View saved result</summary>{resultOpened&&<DetailText text={result||''} reference={reference} automatic/>}</details>}
  <details><summary>Technical details</summary>
   {facts.job_id&&<p>Job: <code>{facts.job_id}</code><br/>Call: <code>{facts.call_id}</code></p>}
   {facts.status==='returned'&&<p>A returned report does not independently verify that its claimed effects succeeded.</p>}
   <DetailText text={message.text} reference={message.textDetail}/>
   <button type="button" className="a-link" data-action="message.copy" onClick={()=>act('message.copy',{sessionId:session.id,messageId:message.id})}>Copy observation</button>
  </details>
 </div>;
}
