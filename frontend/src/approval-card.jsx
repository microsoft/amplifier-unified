import React,{useState} from 'react';
import {Check} from 'lucide-react';

export function ApprovalCard({approval,sessionId,act}){
 const [comment,setComment]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const respond=async decision=>{
  if(busy)return;
  setBusy(true);setError('');
  try{
   const result=await act('approval.respond',{sessionId,id:approval.id,decision,...(comment.trim()?{comment:comment.trim()}:{})});
   if(result?.accepted===false)throw Error(result.error||'The decision was not confirmed. Your note is kept.');
  }catch(e){setError(e.message)}finally{setBusy(false)}
 };
 return <div data-approval-id={approval.id}>
  <strong>{approval.title||approval.tool||'Approval requested'}</strong>
  <p>{approval.prompt||approval.message||approval.description}</p>
  {approval.deliveryError&&<p role="status">{approval.deliveryError}</p>}
  {approval.details&&<pre>{typeof approval.details==='string'?approval.details:JSON.stringify(approval.details,null,2)}</pre>}
  <label className="a-field">Note to Amplifier (optional)
   <textarea aria-label="Note to Amplifier (optional)" maxLength={4000} rows={3} value={comment} disabled={busy}
    onChange={e=>setComment(e.target.value)} placeholder="Explain your decision or suggest what to do next…"/>
  </label>
  <p className="a-caption">Your note goes with either decision. Choose Deny if the proposed action needs to change.</p>
  {error&&<p role="alert">{error}</p>}
  <div className="a-dialog-actions"><button className="a-primary" data-action="approval.respond" disabled={busy} onClick={()=>respond('approve')}><Check/>Allow</button><button className="a-soft" data-action="approval.respond" disabled={busy} onClick={()=>respond('deny')}>Deny</button></div>
 </div>;
}
