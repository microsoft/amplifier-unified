import React,{useEffect,useRef,useState} from 'react';
import './automation-hub.css';

const label=value=>({active:'Scheduled',due:'Due',blocked:'Blocked',needs_review:'Needs review',paused:'Paused',cancelled:'Cancelled',owner_unavailable:'Owner unavailable',owner_stopped:'Owner stopped',owner_stopping:'Owner stopping',owner_interrupted:'Owner interrupted',owner_error:'Owner needs attention'}[value]||value);
const when=(seconds,zone)=>seconds?new Date(seconds*1000).toLocaleString(undefined,zone?{timeZone:zone}:{}):'No future occurrence';
const recurrence=spec=>spec.kind==='interval'?`Every ${spec.intervalSeconds/60} ${spec.intervalSeconds===60?'minute':'minutes'}`:spec.kind==='once'?`Once, ${new Date(spec.startAt).toLocaleString(undefined,{timeZone:spec.timezone})}`:spec.kind==='weekly'?`${spec.weekdays.map(day=>['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][day]).join(', ')} at ${spec.localTime}`:`Daily at ${spec.localTime}`;
const model=selection=>selection?[selection.instance,selection.model,selection.effort].filter(Boolean).join(' · '):'Bundle defaults';
export function AutomationHub({state,dispatch,isCurrent}){
 const [page,setPage]=useState(null),[filter,setFilter]=useState('current'),[offset,setOffset]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[review,setReview]=useState(null),[ack,setAck]=useState(false);
 const version=useRef(0),active=useRef(true),mutation=useRef(false);
 useEffect(()=>{active.current=true;return()=>{active.current=false;version.current++}},[]);
 async function request(action,args){const value=await dispatch(action,args,{feedback:false});if(value?.accepted!==true)throw Error(value?.error||'The result could not be confirmed. Refresh before trying again.');return value.result}
 async function refresh(start=offset,scope=filter){
  const current=++version.current;setBusy(true);setError('');
  try{const result=await request('schedule.overview',{offset:start,limit:30,status:scope});if(active.current&&current===version.current)setPage(result)}
  catch(e){if(active.current&&current===version.current)setError(e.message)}finally{if(active.current&&current===version.current)setBusy(false)}
 }
 useEffect(()=>{refresh(offset,filter)},[offset,filter]);
 async function manage(id){
  if(mutation.current)return;mutation.current=true;setBusy(true);setError('');
  try{if(id!==state.selectedSessionId)await request('session.select',{id});if(active.current&&isCurrent(id))await request('view.update',{patch:{panel:'chat-work'}})}
  catch(e){if(active.current)setError(e.message)}finally{mutation.current=false;if(active.current)setBusy(false)}
 }
 async function preview(row){
  if(mutation.current)return;mutation.current=true;setBusy(true);setError('');setReview(null);setAck(false);
  try{const value=await request('schedule.repairPreview',{sessionId:row.sessionId,id:row.id,expectedRevision:row.revision});if(active.current)setReview({row,...value})}
  catch(e){if(active.current)setError(e.message)}finally{mutation.current=false;if(active.current)setBusy(false)}
 }
 async function resume(){
  if(mutation.current||!review)return;mutation.current=true;setBusy(true);setError('');
  // Close the one-use review even if its response is lost. A fresh read/preview
  // is required; never leave a blind mutation retry button behind.
  const value=review;setReview(null);
  try{await request('schedule.repairResume',{sessionId:value.row.sessionId,id:value.row.id,expectedRevision:value.scheduleRevision,previewHash:value.previewHash,acknowledgeStop:ack});await refresh()}
  catch(e){if(active.current)setError(e.message)}finally{mutation.current=false;if(active.current)setBusy(false)}
 }
 return <section className="a-automation" aria-label="Automation overview">
  <p>Scheduled work stays with its owning chat. Review upcoming runs and open that chat to create or manage its schedules.</p>
  <div className="a-automation-actions"><button className="a-primary" disabled={busy||!state.selectedSessionId} onClick={()=>manage(state.selectedSessionId)}>Create in current chat</button><button className="a-soft" disabled={busy} onClick={()=>refresh()}>Refresh automations</button><label>Show <select aria-label="Show automations" value={filter} disabled={busy} onChange={event=>{setReview(null);setOffset(0);setFilter(event.target.value)}}><option value="current">Current schedules</option><option value="all">All schedules</option></select></label></div>
  {!state.selectedSessionId&&<p>Choose or create a chat to add scheduled work.</p>}
  {error&&<p role="alert">{error}</p>}
  {page&&<p className="a-caption">Schedule snapshot · {when(page.readAt)} · {page.total} schedules</p>}
  {!page&&!error&&<p role="status">Loading automations…</p>}
  {page?.items.length===0&&<p>No saved schedules in this view.</p>}
  {review&&<section className="a-selection-card a-automation-review" aria-label="Review future scheduled work"><h3>Review future scheduled work</h3><p>{review.binding.prompt}</p><dl><dt>Owning chat</dt><dd>{review.row.owner?.title||'Untitled chat'}</dd><dt>Workspace</dt><dd>{review.binding.newTaskConfiguration.workspace}</dd><dt>Bundle</dt><dd>{review.binding.newTaskConfiguration.bundle}</dd><dt>Model</dt><dd>{model(review.binding.newTaskConfiguration.selection)}</dd><dt>Destination</dt><dd>New task for each run · {review.binding.newTaskTitle}</dd><dt>Maximum turns</dt><dd>{review.binding.newTaskMaxTurns}</dd><dt>Repeats</dt><dd>{recurrence(review.binding.spec)} · {review.binding.spec.timezone}</dd><dt>Missed runs</dt><dd>{review.binding.missedRunPolicy==='skip'?'Skip occurrences more than a minute late':'Run only the latest missed occurrence'}</dd><dt>Notifications</dt><dd>{review.binding.notificationPolicy==='changes'?'Changes and failures':review.binding.notificationPolicy==='always'?'Every result and failure':'Failures only'}</dd></dl><p>Upcoming occurrences ({review.binding.spec.timezone}):</p><ul>{review.occurrences.map(item=><li key={item.utc}>{when(item.dueAt,review.binding.spec.timezone)}</li>)}</ul><p>Only future runs are enabled. This does not restart the owning chat or replay previous work. Normal file and tool permissions still apply.</p>{review.requiresStopAcknowledgement&&<label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/> Allow future scheduled tasks after my Stop</label>}<div className="a-automation-actions"><button className="a-primary" disabled={busy||!review.occurrences.length||(review.requiresStopAcknowledgement&&!ack)} onClick={resume}>Resume future runs</button><button className="a-soft" disabled={busy} onClick={()=>setReview(null)}>Not now</button></div></section>}
  <div className="a-automation-list">{page?.items.map(row=><article className="a-selection-card" key={row.id} data-automation-id={row.id}><h3>{row.prompt}</h3><p><strong>{label(row.displayState)}</strong> · {row.owner?.title||'Unavailable chat'}</p>{row.reason&&<p>{row.reason}</p>}<p>{row.destination==='new_task'?'New task for each run':'Continues the owning task'} · {row.spec?.timezone}</p><p>{recurrence(row.spec)}</p><p>Next: {when(row.nextDue,row.spec?.timezone)}</p>{row.owner?.status==='error'&&<p>Owning chat needs attention. Review future new tasks here without restarting the chat.</p>}{row.lastRun&&<p className="a-caption">Last run: {label(row.lastRun.phase)} · {when(row.lastRun.dueAt)}. {row.lastRun.detail}</p>}<div className="a-automation-actions"><button className="a-soft" disabled={busy||!row.owner} onClick={()=>manage(row.sessionId)}>Open owning chat</button>{row.owner&&row.destination==='new_task'&&row.status!=='cancelled'&&<button className="a-soft" disabled={busy} onClick={()=>preview(row)}>Review future runs</button>}</div></article>)}</div>
  {page&&<div className="a-automation-actions"><button className="a-soft" disabled={busy||offset===0} onClick={()=>{setReview(null);setOffset(Math.max(0,offset-30))}}>Previous schedules</button><span>{page.total?`${page.offset+1}–${page.offset+page.items.length} of ${page.total}`:'0 schedules'}</span><button className="a-soft" disabled={busy||!page.hasMore} onClick={()=>{setReview(null);setOffset(offset+30)}}>More schedules</button></div>}
 </section>
}
