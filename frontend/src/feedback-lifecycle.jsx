import React,{useEffect,useRef,useState} from 'react';

export function FeedbackReconcile({state,act}){
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[request,setRequest]=useState(null);
 const pending=useRef(false),rows=(state.feedback?.requests||[]).filter(row=>row.status==='unknown');
 const result=(state.feedback?.followups||[]).find(row=>row.requestId===request);
 async function check(feedbackId){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');
  const requestId=crypto.randomUUID();setRequest(requestId);
  try{await act('feedback.reconcile',{requestId,feedbackId})}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label="Unconfirmed feedback delivery">
  {!!rows.length&&<><h3>Unconfirmed delivery</h3><p>Check for the original report using its saved identity. Checking never sends it again.</p>{rows.map(row=><div key={row.requestId}><strong>{row.title}</strong><button type="button" className="a-soft" data-action="feedback.reconcile" disabled={busy||['queued','sending'].includes(result?.status)} onClick={()=>check(row.requestId)}>Check delivery</button></div>)}</>}
  {result&&<p role="status">{result.message}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}

export function FeedbackCorrection({state,report,act}){
 const shared=state.view?.feedbackCorrectionDraft;
 const [draft,setDraft]=useState(shared||null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const current=useRef(draft),tail=useRef(Promise.resolve()),sending=useRef(false),timer=useRef(null),dirty=useRef(false);
 useEffect(()=>()=>{clearTimeout(timer.current);if(dirty.current)void save(current.current).catch(()=>{})},[]);
 useEffect(()=>{if(!dirty.current&&!sending.current){current.current=shared||null;setDraft(shared||null)}},[shared]);
 const result=(state.feedback?.followups||[]).find(row=>row.requestId===draft?.pending?.requestId);
 const same=draft?.feedbackId===report.feedbackId,terminal=['submitted','unknown','failed'].includes(result?.status);
 function save(value){
  clearTimeout(timer.current);dirty.current=true;current.current=value;setDraft(value);
  const job=tail.current.catch(()=>{}).then(()=>act('view.update',{patch:{feedbackCorrectionDraft:value}})).then(response=>{if(response?.accepted===false)throw Error('The correction draft could not be saved.');if(current.current===value)dirty.current=false});
  tail.current=job;job.catch(error=>setError(error.message));return job;
 }
 function edit(patch){dirty.current=true;current.current={...current.current,...patch};setDraft(current.current);clearTimeout(timer.current);timer.current=setTimeout(()=>void save(current.current).catch(()=>{}),250)}
 function start(){setError('');save({feedbackId:report.feedbackId,title:report.editableTitle,body:report.editableBody,expectedRevision:report.revision})}
 async function submit(event){
  event.preventDefault();if(sending.current||terminal)return;sending.current=true;setBusy(true);setError('');
  try{
   const d=current.current,payload=d.pending||{requestId:crypto.randomUUID(),feedbackId:d.feedbackId,title:d.title,body:d.body,expectedRevision:d.expectedRevision};
   await save({...d,pending:payload});await act('feedback.update',payload);
  }catch(error){setError(error.message||'Delivery was not acknowledged. Retry only this same request.')}
  finally{sending.current=false;setBusy(false)}
 }
 if(!same)return <><button type="button" className="a-soft" onClick={start} disabled={!!draft?.body}>Draft a correction</button>{draft?.body&&<p>A correction for another report is saved. Return to that report before starting another.</p>}</>;
 return <form onSubmit={submit} aria-label="Feedback correction">
  <h4>Correction version</h4><p>The correction is added as a comment. The original report and maintainer edits stay intact.</p>
  <label htmlFor="feedback-corrected-title">Corrected title</label><input id="feedback-corrected-title" maxLength={200} required disabled={!!draft.pending||busy} value={draft.title} onChange={event=>edit({title:event.target.value})} onBlur={()=>void save(current.current).catch(()=>{})}/>
  <label htmlFor="feedback-corrected-body">Corrected description</label><textarea id="feedback-corrected-body" maxLength={16000} required disabled={!!draft.pending||busy} value={draft.body} onChange={event=>edit({body:event.target.value})} onBlur={()=>void save(current.current).catch(()=>{})}/>
  {draft.expectedRevision!==report.revision&&!draft.pending&&<><p>The report changed. Review the refreshed report before using it as the correction's starting revision.</p><button type="button" className="a-soft" onClick={()=>save({...current.current,expectedRevision:report.revision})}>Use reviewed revision</button></>}
  {!terminal&&<button className="a-primary" data-action="feedback.update" disabled={busy||['queued','sending'].includes(result?.status)||draft.expectedRevision!==report.revision||!draft.title.trim()||!draft.body.trim()}>{draft.pending?'Check correction status':'Publish correction comment'}</button>}
  {result&&<p role="status">{result.message}</p>}{result?.commentUrl&&<a href={result.commentUrl} target="_blank" rel="noopener noreferrer">View correction</a>}
  {(terminal||!draft.pending)&&<button type="button" className="a-soft" disabled={busy} onClick={()=>save(null)}>Close correction draft</button>}
  {error&&<p role="alert">{error}</p>}
 </form>;
}

export function FeedbackStateControl({state,report,act}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const sending=useRef(false),saved=state.view?.feedbackLifecycleDraft;
 const pending=saved?.feedbackId===report.feedbackId?saved:null;
 const result=(state.feedback?.followups||[]).find(row=>row.requestId===pending?.requestId);
 const unresolved=pending&&!['submitted','failed','unknown'].includes(result?.status);
 async function change(){
  if(sending.current)return;sending.current=true;setBusy(true);setError('');
  const action=report.state==='closed'?'feedback.reopen':'feedback.close';
  const payload=unresolved?pending:{action,requestId:crypto.randomUUID(),feedbackId:report.feedbackId,expectedRevision:report.revision};
  try{
   const response=await act('view.update',{patch:{feedbackLifecycleDraft:{...payload,readRequestId:report.requestId}}});
   if(response?.accepted===false)throw Error('Could not save the change request.');
   const {action,readRequestId,...args}=payload;await act(action,args);
  }catch(error){setError(error.message||'The state change was not acknowledged. Check this same request.')}
  finally{sending.current=false;setBusy(false)}
 }
 const stale=result&&['submitted','unknown'].includes(result.status)&&report.requestId===pending?.readRequestId;
 return <section aria-label="Feedback report state">
  <button type="button" className="a-soft" data-action={report.state==='closed'?'feedback.reopen':'feedback.close'} disabled={busy||['queued','sending'].includes(result?.status)||stale} onClick={change}>{unresolved?'Check state change':report.state==='closed'?'Reopen my report':'Close my report'}</button>
  {result&&<p role="status">{result.message} Refresh the report to review its current state.</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
