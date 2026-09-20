import React,{useEffect,useState} from 'react';
import {request} from './api';
import './operations.css';

const labels={running:'Running',queued:'Queued',cancel_requested:'Stopping',completed:'Completed',failed:'Failed',cancelled:'Stopped',outcome_unknown:'Outcome unknown',waiting_input:'Waiting for input'};
const call=(action,args,signal)=>request('/api/actions',{method:'POST',body:{action,args},signal}).then(value=>value.result);

export function OperationsPanel({sessionId}){
 const [open,setOpen]=useState(false),[rows,setRows]=useState([]),[selected,setSelected]=useState(null),[detail,setDetail]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[pageCursor,setPageCursor]=useState(0);
 useEffect(()=>{setOpen(false);setRows([]);setSelected(null);setDetail(null);setError('')},[sessionId]);
 useEffect(()=>{
  if(!open||!sessionId)return;
  const controller=new AbortController();
  call('operations.list',{sessionId},controller.signal).then(value=>setRows(value.operations)).catch(e=>{if(e.name!=='AbortError')setError(e.message)});
  return()=>controller.abort();
 },[open,sessionId]);
 useEffect(()=>{
  if(!open||!selected||!sessionId)return;
  const controller=new AbortController();setDetail(null);setError('');
  (async()=>{
   let value=await call('operations.read',{sessionId,id:selected,cursor:pageCursor},controller.signal);
   if(controller.signal.aborted)return;setDetail(value);setRows(rows=>rows.map(row=>row.id===value.id?{...row,state:value.state}:row));
   while(!controller.signal.aborted&&['running','queued','cancel_requested'].includes(value.state)){
    value=await call('operations.wait',{sessionId,id:selected,cursor:pageCursor,afterRevision:String(value.revision),waitMs:25000},controller.signal);
    if(controller.signal.aborted)return;setDetail(value);setRows(rows=>rows.map(row=>row.id===value.id?{...row,state:value.state}:row));
   }
  })().catch(e=>{if(e.name!=='AbortError')setError(e.message)});
  return()=>controller.abort();
 },[open,selected,sessionId,pageCursor]);
 async function cancel(){
  setBusy(true);setError('');
  try{await call('operations.cancel',{sessionId,id:selected});setDetail(await call('operations.read',{sessionId,id:selected}))}
  catch(e){setError(e.message)}finally{setBusy(false)}
 }
 if(!sessionId)return null;
 return <section className="a-operations" aria-label="Operations">
  <button type="button" onClick={()=>setOpen(!open)} aria-expanded={open}>Operations</button>
  {open&&<div className="a-operations-body">
   <p>Saved execution evidence for this conversation.</p><button type="button" data-action="operations.list" onClick={()=>call('operations.list',{sessionId}).then(value=>setRows(value.operations)).catch(e=>setError(e.message))}>Refresh operations</button>
   {error&&<p role="alert">{error}</p>}
   {!rows.length?<p>No operations have been recorded.</p>:<ul>{rows.map(row=><li key={row.id}><button type="button" data-action="operations.read" onClick={()=>{setPageCursor(0);setSelected(row.id)}} aria-pressed={selected===row.id}>{row.kind==='process'?'Command':row.kind==='worker'?'Worker':'Smart Tool'} · {labels[row.state]||row.state}</button></li>)}</ul>}
   {detail&&<div aria-live="polite"><strong>{labels[detail.state]||detail.state}</strong>
    {detail.returncode!=null&&<span> · Exit code {detail.returncode}</span>}
    {detail.state==='outcome_unknown'&&<p>The host could not confirm the outcome. Work has not been replayed.</p>}
    {(detail.cursorGap||detail.captureComplete===false)&&<p>Some output is unavailable. This evidence is incomplete.</p>}
    <pre>{detail.chunks?.map(chunk=>chunk.text).join('')||JSON.stringify(detail.evidence||{},null,2)}</pre>
    {detail.hasMore&&<button type="button" data-action="operations.read" onClick={()=>setPageCursor(detail.nextCursor)}>Next output page</button>}
    {detail.controlAvailable&&<button type="button" data-action="operations.cancel" disabled={busy} onClick={cancel}>Stop command</button>}
   </div>}
  </div>}
 </section>;
}
