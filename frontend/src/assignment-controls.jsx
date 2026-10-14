import React,{useState} from 'react';

export function AssignmentControls({dispatch,sessionId}){
 const metric=row=>row?.value==null?'Unknown':String(row.value)+(row.status==='known'?'':` (${row.status})`);
 const key='amplifier-assignment-draft:'+sessionId;
 const [value,setValue]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [draft,setDraft]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}});
 const save=value=>{setDraft(value);try{value?sessionStorage.setItem(key,JSON.stringify(value)):sessionStorage.removeItem(key)}catch{}};
 const read=async()=>{try{const response=await dispatch('assignment.read',{sessionId},{feedback:false});setValue(response.result)}catch(error){setError(error.message)}};
 const assignment=value?.assignment,limits=draft?.limits||assignment?.limits;
 const change=(name,val)=>save({id:crypto.randomUUID(),revision:draft?.revision??assignment.revision,limits:{...limits,[name]:val}});
 const run=async(action,args,identity)=>{
  if(busy)return;setBusy(true);setError('');
  // Retain the exact action and identity through an uncertain response/reload.
  const pending=draft?.pending||{action,args,id:identity||crypto.randomUUID()};
  save({...draft,pending});
  try{
   await dispatch(pending.action,pending.args,{id:pending.id,feedback:false});
   save(null);await read();
  }catch(error){setError(error.message)}finally{setBusy(false)}
 };
 return <details className="a-assignment" onToggle={event=>{if(event.currentTarget.open&&!value)read()}}>
  <summary>Commissioned assignment</summary>
  <p>Manage this chat and its commissioned helpers together. Existing independent chats keep their own controls.</p>
  {error&&<p role="alert">{error}</p>}
  <button className="a-link" disabled={busy} onClick={read}>Refresh assignment</button>
  {draft?.pending&&<p role="status">A change has no confirmed response. <button className="a-link" disabled={busy} onClick={()=>run()}>Check the same change</button></p>}
  {value&&!assignment&&<><p>This chat has no assignment. Commissioning a helper starts one automatically.</p><button className="a-soft" disabled={busy||!!draft?.pending} onClick={()=>run('assignment.start',{sessionId})}>Start assignment</button></>}
  {assignment&&<>
   <p role="status">Assignment {assignment.status} · {Object.values(assignment.members).filter(row=>row.status!=='detached').length} members · {value.usage?.calls??0} recorded calls</p>
   <p>Total tokens: {metric(value.usage?.metrics.grossTotalTokens)} · Cost (USD): {metric(value.usage?.metrics.costUsd)} · {value.inFlightCalls??0} calls in progress</p>
   {!!value.independentWork?.requests.length&&<p role="status">{value.independentWork.detail}</p>}
   {assignment.ownerSessionId===sessionId&&assignment.status==='active'&&<>
    <button className="a-soft" disabled={busy||!!draft?.pending} onClick={()=>run('assignment.stop',{sessionId,expectedRevision:assignment.revision})}>Stop assignment</button>
    <details><summary>Assignment limits</summary><p>These limits pause future supported calls. Reservations are allowances per call; actual usage and already running effects can exceed them. Saving does not restart work.</p>
     {[['maxOpenTasks','Open helper chats'],['maxConcurrentCalls','Concurrent model calls'],['maxMembers','Total assignment members'],['maxTotalTokens','Total token limit'],['reserveTokens','Token allowance per call'],['maxCostUsd','Cost limit (USD)'],['reserveCostUsd','Cost allowance per call (USD)']].map(([name,label])=><label key={name}>{label}<input type="number" min={name.startsWith('reserve')?0:name.includes('Usd')?.000001:1} step={name.includes('Usd')?'any':1} value={limits[name]??''} disabled={busy||!!draft?.pending} onChange={event=>change(name,event.target.value===''?null:Number(event.target.value))}/></label>)}
     <label>When settled usage is unknown<select value={limits.unknownPolicy} disabled={busy||!!draft?.pending} onChange={event=>change('unknownPolicy',event.target.value)}><option value="pause">Pause future calls</option><option value="allow">Allow with incomplete totals</option></select></label>
     <label><input type="checkbox" checked={limits.includeEstimatedCost} disabled={busy||!!draft?.pending} onChange={event=>change('includeEstimatedCost',event.target.checked)}/>Include attributed cost estimates</label>
     {draft?.revision!=null&&draft.revision!==assignment.revision&&<p role="alert">Assignment changed. Your edits are retained; use the latest limits before saving again.</p>}
     <button className="a-soft" disabled={busy||!!draft?.pending||!draft?.limits||draft.revision!==assignment.revision} onClick={()=>run('assignment.set',{sessionId,expectedRevision:draft.revision,...draft.limits},draft.id)}>Save assignment limits</button>
    </details>
   </>}
   {draft&&<button className="a-link" disabled={busy} onClick={()=>{save(null);setError('')}}>Discard draft and use latest</button>}
   <details><summary>Membership and accounting details</summary><p>{value.enforcement}</p><pre>{JSON.stringify(assignment.members,null,2)}</pre><p>{value.usage?.coverage}</p><button className="a-link" disabled={busy||!!draft?.pending} onClick={()=>run('assignment.detach',{sessionId,expectedRevision:assignment.revision})}>Detach this settled chat</button></details>
  </>}
 </details>;
}
