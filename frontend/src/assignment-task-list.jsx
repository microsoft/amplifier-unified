import React,{useEffect,useRef,useState} from 'react';
import './assignment-task-list.css';

const emptyDraft=()=>({title:'',description:'',blockedReason:'',dependsOn:[]});
const load=key=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')||{draft:emptyDraft()}}catch{return {draft:emptyDraft()}}};

export function AssignmentTaskList(props){return <TaskList key={props.sessionId} {...props}/>}

function TaskList({dispatch,sessionId,compact=false,enabled=true,onAssignmentChange}){
 const storageKey='amplifier-assignment-tasks:'+sessionId;
 const [saved,setSaved]=useState(()=>load(storageKey)),[view,setView]=useState(null),[open,setOpen]=useState(false);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0);
 const [visible,setVisible]=useState(()=>typeof document==='undefined'||!document.hidden);
 const alive=useRef(true),inflight=useRef(false),savedRef=useRef(saved),viewRef=useRef(null),observationEpoch=useRef(0),observer=useRef(null);
 const invalidate=()=>{observationEpoch.current++;observer.current?.abort()};
 const reload=()=>{invalidate();setRefresh(n=>n+1)};
 useEffect(()=>()=>{alive.current=false;invalidate()},[]);
 useEffect(()=>{if(typeof document==='undefined')return;const changed=()=>{invalidate();setVisible(!document.hidden)};document.addEventListener('visibilitychange',changed);return()=>document.removeEventListener('visibilitychange',changed)},[]);
 const persist=next=>{savedRef.current=next;setSaved(next);try{sessionStorage.setItem(storageKey,JSON.stringify(next))}catch{setError('This browser could not save the pending task change. Keep this view open until its response is confirmed.')}};
 useEffect(()=>{
  if(!open||!enabled||!visible||busy)return;
  let active=true;const epoch=observationEpoch.current,controller=new AbortController();observer.current=controller;
  (async()=>{
   try{
    let response=await dispatch('assignment.tasks.list',{sessionId},{feedback:false,presentation:true,signal:controller.signal});
    while(active&&epoch===observationEpoch.current){
     const next=response.result,previous=viewRef.current;
     const older=previous?.assignmentId===next.assignmentId&&previous?.assignmentRevision>next.assignmentRevision;
     if(!older){if(onAssignmentChange?.(next.assignmentControl||null)===false)return;viewRef.current=next;setView(next)}
     response=await dispatch('assignment.tasks.wait',{sessionId,afterVersion:response.result.version,waitMs:30000},{feedback:false,presentation:true,signal:controller.signal});
    }
   }catch(failure){if(active&&epoch===observationEpoch.current){setError(failure.message)}}
  })();
  return()=>{active=false;controller.abort();if(observer.current===controller)observer.current=null};
 },[dispatch,sessionId,open,enabled,visible,busy,refresh,onAssignmentChange]);
 const tasks=view?.tasks||[],members=new Map((view?.members||[]).map(row=>[row.sessionId,row.label]));
 const title=id=>tasks.find(task=>task.id===id)?.title||'Unavailable prerequisite';
 const member=id=>id===sessionId?'This chat':members.get(id)||'Unavailable member';
 const draft=saved.draft||emptyDraft(),pending=saved.pending;
 const edit=task=>persist({...savedRef.current,draft:task?{title:task.title,description:task.description,blockedReason:task.blockedReason,dependsOn:task.dependsOn,taskId:task.id,expectedRevision:task.revision}:emptyDraft()});
 const field=(key,value)=>persist({...savedRef.current,draft:{...draft,[key]:value}});
 const run=async(verb,args)=>{
  if(inflight.current)return;invalidate();inflight.current=true;setBusy(true);setError('');
  const request=savedRef.current.pending||{action:'assignment.tasks.'+verb,args:{sessionId,...args},id:crypto.randomUUID()};
  persist({...savedRef.current,pending:request});
  try{
   const response=await dispatch(request.action,request.args,{id:request.id,feedback:false});
   if(response?.accepted!==true||!response.result?.task||request.args.taskId&&response.result.task.id!==request.args.taskId)throw Error('The task change has no confirmed receipt. Check the same change before retrying.');
   if(alive.current){persist({...savedRef.current,pending:null,draft:request.action.endsWith('.create')||request.action.endsWith('.update')?emptyDraft():savedRef.current.draft})}
  }catch(failure){if(alive.current){if(failure.status>=400&&failure.status<500&&failure.status!==408)persist({...savedRef.current,pending:null});setError(failure.message)}}
  finally{inflight.current=false;if(alive.current){setBusy(false);setRefresh(n=>n+1)}}
 };
 const act=(verb,task,extra={})=>run(verb,{taskId:task.id,expectedRevision:task.revision,...extra});
 const blocked=busy||!!pending,canManage=view?.canManage,active=view?.assignmentStatus==='active';
 const edited=tasks.find(task=>task.id===draft.taskId),staleEdit=draft.taskId&&edited?.revision!==draft.expectedRevision;
 return <details className={'a-assignment-tasks'+(compact?' is-compact':'')} onToggle={event=>{if(event.target===event.currentTarget){invalidate();setOpen(event.currentTarget.open)}}}>
  <summary>Shared task list</summary>
  <p>Optional work items for this assignment. Claims record responsibility; they do not start work or lock files.</p>
  <p>Worker activity is observed separately from task readiness. A paused or blocked chat keeps its responsibility.</p>
  {error&&<p role="alert">{error}</p>}
  <button className="a-link" disabled={busy} onClick={reload}>Refresh tasks</button>
  {pending&&<div role="status"><p>A change has no confirmed response. Check that same change before trying another.</p><button className="a-soft" disabled={busy} onClick={()=>run()}>Check the same task change</button><button className="a-link" disabled={busy} onClick={()=>{persist({...savedRef.current,pending:null});setError('');reload()}}>Discard local request and inspect current tasks</button></div>}
  {view&&!view.assignmentId&&<p>This independent chat has no shared task list. Start an assignment or commission a helper to use one.</p>}
  {view?.assignmentId&&<>
   {!active&&<p role="status">Assignment stopped. Claims stay visible; only explicit release or cancellation is available.</p>}
   {!tasks.length&&<p>No shared tasks yet.</p>}
   <ul className="a-shared-task-items">{tasks.map(task=>{
    const terminal=['completed','cancelled'].includes(task.status),claim=task.claim,holder=claim?.sessionId===sessionId;
    const status=task.status==='completed'?'Reported complete':task.status==='cancelled'?'Cancelled':claim?claim.status==='current'?'Claimed':'Claim needs review':task.readiness==='blocked'?'Blocked':'Ready';
    return <li key={task.id} data-task-id={task.id}>
     <strong>{task.title}</strong><span className="a-shared-task-status">{status}</span>
     {claim&&<p>{terminal?'Last responsibility: ':'Responsibility: '}{member(claim.sessionId)}{!terminal&&claim.status!=='current'?' — '+claim.detail:''}</p>}
     {!terminal&&task.worker&&<p>Worker: {task.worker.label}{task.worker.blockedReason?' — '+task.worker.blockedReason:''}</p>}
     {!!task.blockedBy.length&&<p>Waiting for: {task.blockedBy.map(title).join(', ')}</p>}
     {task.blockedReason&&<p>Blocked: {task.blockedReason}</p>}
     {task.description&&<p>{task.description}</p>}
     {task.completion&&<div><p>{task.completion.report}</p><small>Reported by {member(task.completion.reportedBy)}. Artifact verification and integration remain separate.</small>{!!task.completion.references?.length&&<details><summary>Reported references</summary><ul>{task.completion.references.map((value,index)=><li key={index}>{value}</li>)}</ul></details>}</div>}
     {!terminal&&<div className="a-shared-task-actions">
      {active&&!claim&&task.readiness==='ready'&&<button className="a-soft" disabled={blocked} onClick={()=>act('claim',task)}>Claim for this chat</button>}
      {claim&&(holder||canManage)&&<button className="a-soft" disabled={blocked} onClick={()=>act('release',task,{claimId:claim.id})}>Release claim</button>}
      {holder&&active&&claim.status==='current'&&<CompletionForm disabled={blocked} claimId={claim.id} revision={task.revision} value={saved.completions?.[task.id]} onChange={value=>persist({...savedRef.current,completions:{...savedRef.current.completions,[task.id]:value}})} onComplete={(report,binding)=>run('complete',{taskId:task.id,expectedRevision:binding.revision,claimId:binding.claimId,report})}/>}
      {canManage&&<details><summary>Manage task</summary>
       <p>Releasing, cancelling or reassigning changes responsibility only. Prior file or tool work may continue; use the existing chat or assignment Stop when needed.</p>
       {active&&!claim&&<button className="a-link" disabled={blocked} onClick={()=>edit(task)}>Edit task</button>}
       {active&&task.readiness==='ready'&&<ReassignForm key={task.revision} members={view.members} disabled={blocked} current={claim?.sessionId} onAssign={holderSessionId=>act('reassign',task,{holderSessionId})}/>}
       <button className="a-link" disabled={blocked} onClick={()=>act('cancel',task)}>Cancel task</button>
      </details>}
     </div>}
    </li>;
   })}</ul>
   {canManage&&active&&<details open={!!draft.taskId}><summary>{draft.taskId?'Edit shared task':'Add shared task'}</summary>
    <form onSubmit={event=>{event.preventDefault();const content={title:draft.title,description:draft.description,blockedReason:draft.blockedReason,dependsOn:draft.dependsOn};run(draft.taskId?'update':'create',draft.taskId?{...content,taskId:draft.taskId,expectedRevision:draft.expectedRevision}:{...content,expectedAssignmentRevision:view.assignmentRevision})}}>
     <label>Task title<input data-private="true" value={draft.title} maxLength={200} required disabled={blocked} onChange={event=>field('title',event.target.value)}/></label>
     <label>Details<textarea data-private="true" value={draft.description} maxLength={4000} disabled={blocked} onChange={event=>field('description',event.target.value)}/></label>
     <label>Blocked until<input data-private="true" value={draft.blockedReason} maxLength={1000} placeholder="Optional reason" disabled={blocked} onChange={event=>field('blockedReason',event.target.value)}/></label>
     <fieldset disabled={blocked}><legend>Wait for reported completion</legend>{tasks.filter(task=>task.id!==draft.taskId).map(task=><label key={task.id}><input type="checkbox" checked={draft.dependsOn.includes(task.id)} onChange={event=>field('dependsOn',event.target.checked?[...draft.dependsOn,task.id]:draft.dependsOn.filter(id=>id!==task.id))}/>{task.title}</label>)}</fieldset>
     {staleEdit&&<p role="alert">This task changed. Your edits are retained; inspect it again before saving.</p>}
     <button className="a-soft" disabled={blocked||staleEdit||!draft.title.trim()}>{draft.taskId?'Save task':'Add task'}</button>
     <button type="button" className="a-link" disabled={busy} onClick={()=>edit(null)}>Discard task draft</button>
    </form>
   </details>}
  </>}
 </details>;
}

function CompletionForm({disabled,claimId,revision,value,onChange,onComplete}){
 const report=value?.report||'',binding=value?.binding||{claimId,revision};
 const stale=binding.claimId!==claimId||binding.revision!==revision;
 return <details><summary>Report completion</summary><form onSubmit={event=>{event.preventDefault();if(!stale)onComplete(report,binding)}}><label>Completion report<textarea data-private="true" required maxLength={4000} value={report} disabled={disabled} onChange={event=>onChange({report:event.target.value,binding})}/></label><p>This reports the holder’s result; it does not verify artifacts or run dependent tasks.</p>{stale&&<p role="alert">Responsibility changed. Your report is retained for the earlier claim. Review it before using it for the current claim. <button type="button" className="a-link" disabled={disabled} onClick={()=>onChange({report,binding:{claimId,revision}})}>Use reviewed report for current claim</button></p>}<button className="a-soft" disabled={disabled||stale||!report.trim()}>Mark reported complete</button></form></details>;
}
function ReassignForm({members,disabled,current,onAssign}){
 const [holder,setHolder]=useState(current||'');
 return <form onSubmit={event=>{event.preventDefault();onAssign(holder)}}><label>Assign responsibility<select value={holder} disabled={disabled} onChange={event=>setHolder(event.target.value)}><option value="">Choose a member</option>{members.map(row=><option key={row.sessionId} value={row.sessionId}>{row.label}</option>)}</select></label><button className="a-soft" disabled={disabled||!holder}>Reassign task</button></form>;
}
