import React,{useEffect,useState,useRef} from 'react';
import {request} from './api';
import './operations.css';

const labels={running:'Running',queued:'Queued',cancel_requested:'Stopping',completed:'Completed',failed:'Failed',cancelled:'Stopped',outcome_unknown:'Outcome unknown',waiting_input:'Waiting for input'};
const call=(action,args,signal)=>request('/api/actions',{method:'POST',body:{action,args},signal}).then(value=>value.result);

export function OperationsPanel({sessionId}){
 const activeSession=useRef(sessionId);activeSession.current=sessionId;
 const [command,setCommand]=useState(''),[terminal,setTerminal]=useState(false),[questions,setQuestions]=useState([]),[dependencies,setDependencies]=useState([]),[input,setInput]=useState(''),[closeInput,setCloseInput]=useState(false),[requests,setRequests]=useState([]),[receipt,setReceipt]=useState(null);
 const activeOperation=useRef(null);
 const [open,setOpen]=useState(false),[rows,setRows]=useState([]),[selected,setSelected]=useState(null),[detail,setDetail]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[pageCursor,setPageCursor]=useState(0);
 activeOperation.current=selected;
 useEffect(()=>{setInput('');setCloseInput(false)},[selected,sessionId]);
 useEffect(()=>{setOpen(false);setRows([]);setSelected(null);setDetail(null);setError('');setCommand('');setInput('');setDependencies([]);setQuestions([]);setRequests([]);setReceipt(null);setBusy(false)},[sessionId]);
 useEffect(()=>{
  if(!open||!sessionId)return;
  const controller=new AbortController();
  call('operations.list',{sessionId},controller.signal).then(value=>{setRows(value.operations);setRequests(value.requests||[])}).catch(e=>{if(e.name!=='AbortError')setError(e.message)});
  call('question.list',{sessionId},controller.signal).then(value=>setQuestions(value.items||[])).catch(e=>{if(e.name!=='AbortError')setError(e.message)});
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
  const owner=sessionId,target=selected;setBusy(true);setError('');
  try{await call('operations.cancel',{sessionId:owner,id:target});const value=await call('operations.read',{sessionId:owner,id:target});if(activeSession.current===owner&&activeOperation.current===target)setDetail(value)}
  catch(e){if(activeSession.current===owner)setError(e.message)}finally{if(activeSession.current===owner)setBusy(false)}
 }
 async function mutate(action,args){
  const owner=sessionId,previousSelection=selected,requestId=crypto.randomUUID();setBusy(true);setError('');setReceipt(null);
  try{
   const value=await call(action,{sessionId,requestId,...args});if(activeSession.current!==owner)return;setReceipt(value);
   if(value.state==='accepted'){
    if(action==='operations.submit')setCommand('');else setInput('');
    const listing=await call('operations.list',{sessionId});if(activeSession.current!==owner)return;setRows(listing.operations);setRequests(listing.requests||[]);
    if(value.operationId&&activeOperation.current===previousSelection){const output=await call('operations.read',{sessionId,id:value.operationId});if(activeSession.current===owner&&activeOperation.current===previousSelection){setPageCursor(0);setSelected(value.operationId);setDetail(output)}}
   }else if(value.state==='rejected')setError(value.error?.message||'The request was denied.');
  }catch(e){
   if(activeSession.current!==owner)return;setError(e.message);
   try{const saved=await call('operations.request',{sessionId,requestId});if(activeSession.current!==owner)return;setReceipt(saved);setRequests(values=>[saved,...values.filter(item=>item.requestId!==requestId)])}catch{}
  }finally{if(activeSession.current===owner)setBusy(false)}
 }
 if(!sessionId)return null;
 return <section className="a-operations" aria-label="Operations">
  <button type="button" onClick={()=>setOpen(!open)} aria-expanded={open}>Operations</button>
  {open&&<div className="a-operations-body">
   <p>Saved execution evidence for this conversation.</p><button type="button" data-action="operations.list" onClick={()=>call('operations.list',{sessionId}).then(value=>{setRows(value.operations);setRequests(value.requests||[])}).catch(e=>setError(e.message))}>Refresh operations</button>
   {error&&<p role="alert">{error}</p>}
   {receipt&&<p role="status">Request: {receipt.state.replaceAll('_',' ')}{receipt.message&&` — ${receipt.message}`}</p>}
   {requests.filter(item=>['admitting','outcome_unknown'].includes(item.state)).map(item=><p key={item.requestId}>A command or input request has {item.state==='admitting'?'not yet confirmed admission':'an unknown outcome'}. It has not been replayed. <button type="button" data-action="operations.request" onClick={()=>call('operations.request',{sessionId,requestId:item.requestId}).then(value=>{setReceipt(value);setRequests(values=>values.map(row=>row.requestId===value.requestId?value:row))}).catch(e=>setError(e.message))}>Check request</button></p>)}
   <form data-action="operations.submit" onSubmit={event=>{event.preventDefault();mutate('operations.submit',{command,pty:terminal,questionIds:dependencies})}}>
    <label>Command<textarea aria-label="Command to start" value={command} onChange={event=>setCommand(event.target.value)} disabled={busy}/></label>
    <label><input type="checkbox" checked={terminal} onChange={event=>setTerminal(event.target.checked)} disabled={busy}/>Use a terminal when enabled by this host</label>
    {questions.length>0&&<fieldset><legend>Answers this command depends on</legend>{questions.map(question=><label key={question.id}><input type="checkbox" checked={dependencies.includes(question.id)} onChange={event=>setDependencies(values=>event.target.checked?[...values,question.id]:values.filter(id=>id!==question.id))} disabled={busy}/>{question.prompt} · {question.status}</label>)}</fieldset>}
    <button type="submit" disabled={busy||!command.trim()}>Start command</button>
   </form>
   {!rows.length?<p>No operations have been recorded.</p>:<ul>{rows.map(row=><li key={row.id}><button type="button" data-action="operations.read" onClick={()=>{setPageCursor(0);setSelected(row.id)}} aria-pressed={selected===row.id}>{row.kind==='process'?'Command':row.kind==='worker'?'Worker':row.kind==='kernel-cell'?'Computation cell':row.kind==='kernel'?'Computation runtime':'Smart Tool'} · {labels[row.state]||row.state}</button></li>)}</ul>}
   {detail&&<div aria-live="polite"><strong>{labels[detail.state]||detail.state}</strong>
    {detail.returncode!=null&&<span> · Exit code {detail.returncode}</span>}
    {detail.state==='outcome_unknown'&&<p>The host could not confirm the outcome. Work has not been replayed.</p>}
    {(detail.cursorGap||detail.captureComplete===false)&&<p>Some output is unavailable. This evidence is incomplete.</p>}
    <pre>{detail.chunks?.map(chunk=>chunk.text).join('')}{detail.evidence&&JSON.stringify(detail.evidence,null,2)}</pre>
    {detail.hasMore&&<button type="button" data-action="operations.read" onClick={()=>setPageCursor(detail.nextCursor)}>Next output page</button>}
    {detail.kind==='process'&&detail.controlAvailable&&!detail.stdinClosed&&<form data-action="operations.write" onSubmit={event=>{event.preventDefault();mutate('operations.write',{id:selected,stdin:input,closeStdin:closeInput})}}>
     <label>Input for this command<textarea aria-label="Command input" value={input} onChange={event=>setInput(event.target.value)} disabled={busy||!detail.stdinAllowed}/></label>
     {!detail.stdinAllowed&&<p>Input is disabled by this command's host policy.</p>}
     <label><input type="checkbox" checked={closeInput} onChange={event=>setCloseInput(event.target.checked)} disabled={busy}/>Send end of input{detail.pty?' (terminal EOF)':''}</label>
     <button type="submit" disabled={busy||(!input&&!closeInput)}>Send command input</button>
    </form>}
    {detail.controlAvailable&&<button type="button" data-action="operations.cancel" disabled={busy} onClick={cancel}>{detail.kind==='kernel-cell'?'Interrupt cell':'Stop command'}</button>}
   </div>}
  </div>}
 </section>;
}
