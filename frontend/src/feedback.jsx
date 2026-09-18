import React,{useEffect,useRef,useState} from 'react';
import {ExternalLink,Send,Plus} from 'lucide-react';
import {ResultNotice} from './settings-ui';
import './feedback.css';

const empty=()=>({title:'',body:'',category:'bug',includeDiagnostics:false});
export function FeedbackPanel({state,act}){
 const shared=state.view?.feedbackDraft;
 const [draft,setDraft]=useState(()=>shared||empty()),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const current=useRef(draft),submitting=useRef(false);
 useEffect(()=>{if(shared){current.current=shared;setDraft(shared)}},[shared]);
 const requests=state.feedback?.requests||[],pending=draft.pending;
 const result=pending&&requests.find(item=>item.requestId===pending.requestId);
 const shown=pending||draft;
 useEffect(()=>{if(result&&['submitted','failed','unknown'].includes(result.status))setError('')},[result?.status]);
 const working=busy||['queued','sending'].includes(result?.status),done=['submitted','failed','unknown'].includes(result?.status);
 const issues=state.feedback?.issuesUrl||'https://github.com/bkrabach/amplifier-unified/issues';
 function save(next){current.current=next;setDraft(next);return act('view.update',{patch:{feedbackDraft:next}})}
 function edit(patch){setError('');save({...current.current,...patch}).catch(()=>setError('The draft could not be saved. Reconnect and try again.'))}
 async function submit(event){
  event.preventDefault();if(submitting.current||working||done)return;
  submitting.current=true;setBusy(true);setError('');
  // Freeze both text and identity before the first await. A lost response must
  // never generate a second GitHub issue or submit newly edited text as a retry.
  const source=current.current,payload=source.pending||{requestId:crypto.randomUUID(),title:source.title,body:source.body,category:source.category,includeDiagnostics:!!source.includeDiagnostics};
  try{await save({...source,pending:payload});await act('feedback.submit',payload)}
  catch{setError('The submission was not acknowledged. Check its status using the same request below; this will not post it twice.')}
  finally{submitting.current=false;setBusy(false)}
 }
 async function startNew(){if(working)return;setError('');try{await save(empty())}catch{setError('Could not start a new draft. Reconnect and try again.')}}
 const facts=state.feedback?.diagnostics||{};
 return <section className="a-feedback" data-part="feedback">
  <p>Send a bug report, idea, or question to <a href={issues} target="_blank" rel="noopener noreferrer">bkrabach/amplifier-unified</a>. Uses the host’s GitHub sign-in.</p>
  {(result||error)&&<div className="a-feedback-result"><ResultNotice phase={result?.status==='submitted'?'success':['failed','unknown'].includes(result?.status)?'error':'working'} message={result?.message}/>{error&&<ResultNotice phase="error" message={error}/>} {result?.url&&<a className="a-feedback-link" href={result.url} target="_blank" rel="noopener noreferrer">View issue <ExternalLink size={14}/></a>} {result?.status==='unknown'&&<a href={issues} target="_blank" rel="noopener noreferrer">Check repository issues <ExternalLink size={14}/></a>}</div>}
  <form onSubmit={submit} data-action="feedback.submit">
   <label htmlFor="feedback-category">Type</label><select id="feedback-category" data-action="view.update" value={shown.category||'bug'} disabled={!!pending} onChange={e=>edit({category:e.target.value})}><option value="bug">Bug report</option><option value="idea">Feature idea</option><option value="question">Question</option><option value="other">Other feedback</option></select>
   <label htmlFor="feedback-title">Title</label><input id="feedback-title" data-action="view.update" maxLength={200} required disabled={!!pending} value={shown.title||''} onChange={e=>edit({title:e.target.value})} placeholder="A short description"/>
   <label htmlFor="feedback-body">Details</label><textarea id="feedback-body" data-action="view.update" maxLength={16000} rows={7} required disabled={!!pending} value={shown.body||''} onChange={e=>edit({body:e.target.value})} placeholder="What happened, what you expected, or what you’d like to do…"/>
   <label className="a-feedback-checkbox"><input type="checkbox" data-action="view.update" checked={!!shown.includeDiagnostics} disabled={!!pending} onChange={e=>edit({includeDiagnostics:e.target.checked})}/>Include app version and operating system</label>
   {shown.includeDiagnostics&&<p className="a-caption">Included: app {facts.appVersion||'version not reported'} · {facts.osFamily||'OS not reported'}</p>}
   <p className="a-caption">Only your title, details, feedback type, and a submission reference are sent. Optional diagnostics add the two facts above. Chats, files, paths, provider settings, and credentials are not attached.</p>
   <div className="a-dialog-actions">
    {!done&&<button type="submit" className="a-primary" data-action="feedback.submit" disabled={working||!shown.title?.trim()||!shown.body?.trim()}><Send/>{working?'Sending…':pending?'Check submission':'Send feedback'}</button>}
    {done&&<button type="button" className="a-soft" data-action="view.update" onClick={startNew}><Plus/>New feedback</button>}
   </div>
  </form>
  {requests.filter(item=>item.url&&item.requestId!==pending?.requestId).length>0&&<div className="a-feedback-recent"><h3>Recently sent</h3>{requests.filter(item=>item.url&&item.requestId!==pending?.requestId).slice(0,5).map(item=><a key={item.requestId} href={item.url} target="_blank" rel="noopener noreferrer">{item.title}<ExternalLink size={14}/></a>)}</div>}
 </section>;
}
