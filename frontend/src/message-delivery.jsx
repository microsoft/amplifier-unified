import React,{useState} from 'react';
import {AlertCircle,RotateCcw,LoaderCircle} from 'lucide-react';

export function MessageDelivery({message,session,delivery,localDelivery,dispatch,retry,discard}){
 const [pending,setPending]=useState(false),[report,setReport]=useState(null),[confirm,setConfirm]=useState(false),[error,setError]=useState('');
 const inputId=message.inputId||localDelivery?.commandId;
 const blocked=session.configurationBusy||session.ownership?.status==='blocked'||session.workspaceAvailable===false||!!session.historyReadOnlyReason;
 const working=['working','starting','running','stopping'].includes(session.status);
 const check=async()=>{
  if(pending)return;setPending(true);setError('');setConfirm(false);
  try{
   if(!session.id){setReport({delivery:'not_saved',message:'This message has no confirmed conversation yet. Choose Send again to retry the original submission.'});return;}
   const result=await dispatch('conversation.delivery',{sessionId:session.id,inputId},{feedback:false});setReport(result.result);
  }catch(e){setError(e.message)}finally{setPending(false)}
 };
 const resend=async()=>{
  if(pending)return;setPending(true);setError('');
  try{
   const saved=session.messages?.some(m=>m.role==='user'&&m.inputId===inputId);
   if(saved){const result=await dispatch('conversation.retry',{sessionId:session.id,inputId,confirmUncertain:true},{feedback:false});setReport(result.result);}
   else await retry(message); // The original outbox identity deduplicates a late first receipt.
   setConfirm(false);
  }catch(e){setError(e.message)}finally{setPending(false)}
 };
 if(!delivery)return null;
 const sending=delivery.status==='sending';
 return <>
  <span className="a-message-delivery" role="status">{sending?<><LoaderCircle className="a-progress-spinner"/>Sending…</>:<><AlertCircle/>{delivery.status==='failed'?'Not sent':'Delivery not confirmed'}
   {delivery.status==='failed'&&localDelivery?<button type="button" className="a-link" data-action="conversation.send" disabled={pending||blocked} onClick={()=>retry(message)}><RotateCcw/>Retry</button>:<button type="button" className="a-link" data-action="conversation.delivery" disabled={pending||!inputId} onClick={check}><RotateCcw/>{pending?'Checking…':'Check delivery'}</button>}
  </>}</span>
  {!sending&&delivery.error&&<p role="alert" className="a-delivery-detail">{delivery.error}</p>}
  {localDelivery&&delivery.status==='failed'&&!session.messages?.some(row=>row.inputId===inputId)&&<button type="button" className="a-link" disabled={pending} onClick={()=>discard?.(message)}>Discard unsent message</button>}
  {report?.message&&<p role="status" className="a-delivery-detail">{report.message}</p>}
  {error&&<p role="alert" className="a-delivery-detail">{error}</p>}
  {!sending&&report&&!['accepted','sending'].includes(report.delivery)&&!confirm&&<button type="button" className="a-link" data-action="conversation.retry" disabled={pending||blocked||working} onClick={()=>setConfirm(true)}>Send again</button>}
  {confirm&&<div className="a-delivery-confirm" role="alert"><p>The earlier attempt may have run even though delivery was not confirmed. Sending again could repeat that work. Your saved message and history will be kept.</p><button type="button" className="a-soft" disabled={pending} onClick={()=>setConfirm(false)}>Cancel</button><button type="button" className="a-primary" data-action="conversation.retry" disabled={pending||blocked||working} onClick={resend}>{pending?'Sending…':'Send this message again'}</button></div>}
 </>;
}
