import React,{useEffect,useRef,useState} from 'react';
import {ResultNotice} from './settings-ui';

export function FeedbackFollowup({state,act}){
 const reports=(state.feedback?.requests||[]).filter(row=>row.status==='submitted');
 const shared=state.view?.feedbackFollowupDraft;
 const [draft,setDraft]=useState(shared||{feedbackId:'',body:''}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const current=useRef(draft),dirty=useRef(false),sending=useRef(false),timer=useRef(null),saving=useRef(null);
 useEffect(()=>()=>{clearTimeout(timer.current);if(dirty.current)void flush().catch(()=>{})},[]);
 useEffect(()=>{if(shared&&!dirty.current&&!sending.current){current.current=shared;setDraft(shared)}},[shared]);
 const pending=draft.pending,result=pending&&(state.feedback?.followups||[]).find(row=>row.requestId===pending.requestId);
 const reading=(state.feedback?.followups||[]).find(row=>row.feedbackId===draft.feedbackId&&row.requestId===state.feedback?.readRequestId);
 const report=state.feedback?.report?.feedbackId===draft.feedbackId?state.feedback.report:null;
 const working=busy||['queued','sending'].includes(result?.status),terminal=['submitted','unknown','failed'].includes(result?.status);
 function edit(patch){dirty.current=true;current.current={...current.current,...patch};setDraft(current.current);clearTimeout(timer.current);timer.current=setTimeout(()=>flush().catch(()=>setError('The draft could not be saved.')),250)}
 async function flush(){
  clearTimeout(timer.current);
  if(saving.current){await saving.current;return flush()}
  if(!dirty.current)return;
  const value=current.current,job=act('view.update',{patch:{feedbackFollowupDraft:value}});saving.current=job;
  try{const response=await job;if(!response||response.accepted===false)throw Error('The draft could not be saved.');if(current.current===value)dirty.current=false}
  finally{saving.current=null}
  if(dirty.current)return flush();
 }
 async function save(value){current.current=value;dirty.current=true;setDraft(value);await flush()}
 async function read(page=1){setError('');try{await save(current.current);await act('feedback.get',{requestId:crypto.randomUUID(),feedbackId:current.current.feedbackId,page})}catch{setError('Could not load the report. Check the connection and try again.')}}
 async function send(event){
  event.preventDefault();if(sending.current||working||terminal)return;
  sending.current=true;setBusy(true);setError('');
  const payload=current.current.pending||{requestId:crypto.randomUUID(),feedbackId:current.current.feedbackId,body:current.current.body};
  const frozen={...current.current,pending:payload};current.current=frozen;setDraft(frozen);
  try{await save(frozen);await act('feedback.comment',payload)}
  catch{setError('The comment was not acknowledged. Check the same request below; it will not post twice.')}
  finally{sending.current=false;setBusy(false)}
 }
 async function newComment(){const next={feedbackId:draft.feedbackId,body:''};try{await save(next);current.current=next;setDraft(next);setError('')}catch{setError('Could not start a new comment.')}}
 if(!reports.length)return null;
 return <section className="a-feedback-followup" aria-label="Feedback follow-up">
  <h3>Follow up on feedback</h3>
  <label htmlFor="feedback-report">Submitted report</label>
  <select id="feedback-report" data-action="view.update" disabled={!!pending||busy} value={draft.feedbackId} onChange={event=>edit({feedbackId:event.target.value})}><option value="">Choose a report</option>{reports.map(row=><option key={row.requestId} value={row.requestId}>{row.title}</option>)}</select>
  <button type="button" className="a-soft" data-action="feedback.get" disabled={!draft.feedbackId||busy||['queued','sending'].includes(reading?.status)} onClick={()=>read()}>Refresh report</button>
  {reading&&<ResultNotice phase={reading.status==='failed'?'error':reading.status==='completed'?'success':'working'} message={reading.message}/>}
  {report&&<div className="a-feedback-report"><h4>{report.title} · {report.state}</h4><a href={report.url} target="_blank" rel="noopener noreferrer">Open issue on GitHub</a><pre>{report.body}</pre>{report.comments.map(comment=><div key={comment.id}><strong>{comment.author.login}</strong><small> {comment.createdAt}</small><pre>{comment.body}</pre></div>)}<div>{report.page>1&&<button type="button" className="a-link" data-action="feedback.get" onClick={()=>read(report.page-1)}>Previous comments</button>}{report.hasMore&&<button type="button" className="a-link" data-action="feedback.get" onClick={()=>read(report.page+1)}>More comments</button>}</div></div>}
  <form onSubmit={send} data-action="feedback.comment">
   <label htmlFor="feedback-comment">Add a comment</label><textarea id="feedback-comment" data-action="view.update" maxLength={16000} required disabled={!!pending||busy} value={pending?.body??draft.body} onChange={event=>edit({body:event.target.value})} onBlur={()=>save(current.current).catch(()=>setError('The draft could not be saved.'))}/>
   <p className="a-caption">Only this text is added to the selected report, using the host’s GitHub sign-in. The report must belong to that account.</p>
   {result&&<ResultNotice phase={result.status==='submitted'?'success':['unknown','failed'].includes(result.status)?'error':'working'} message={result.message}/>}
   {error&&<ResultNotice phase="error" message={error}/>}
   {result?.commentUrl&&<a href={result.commentUrl} target="_blank" rel="noopener noreferrer">View comment</a>}
   {result?.status==='unknown'&&<a href={result.url} target="_blank" rel="noopener noreferrer">Check the issue before starting another comment</a>}
   {!terminal&&<button type="submit" className="a-primary" data-action="feedback.comment" disabled={working||!draft.feedbackId||!draft.body.trim()}>{working?'Sending…':pending?'Check comment status':'Send comment'}</button>}
   {terminal&&<button type="button" className="a-soft" onClick={newComment}>New comment</button>}
  </form>
 </section>;
}
