import React,{useEffect,useState} from 'react';
import {request} from './api';
import './operations.css';
const call=(action,args,signal)=>request('/api/actions',{method:'POST',body:{action,args},signal}).then(value=>value.result);
export function ComputationPanel({sessionId}){
 const [open,setOpen]=useState(false),[language,setLanguage]=useState('python'),[kernels,setKernels]=useState([]),[kernel,setKernel]=useState(null),[code,setCode]=useState(''),[cell,setCell]=useState(null),[detail,setDetail]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const refresh=()=>call('kernels.list',{sessionId}).then(value=>{setKernels(value.kernels);return value.kernels});
 useEffect(()=>{setOpen(false);setKernels([]);setKernel(null);setCell(null);setDetail(null);setCode('');setError('')},[sessionId]);
 useEffect(()=>{if(open&&sessionId)refresh().catch(e=>setError(e.message))},[open,sessionId]);
 useEffect(()=>{
  if(!cell||!sessionId)return;
  const controller=new AbortController();
  (async()=>{
   let value=await call('operations.read',{sessionId,id:cell},controller.signal);
   if(controller.signal.aborted)return;setDetail(value);
   while(!controller.signal.aborted&&['running','queued','cancel_requested'].includes(value.state)){
    value=await call('operations.wait',{sessionId,id:cell,afterRevision:String(value.revision),waitMs:25000},controller.signal);
    if(controller.signal.aborted)return;setDetail(value);
   }
   if(!controller.signal.aborted&&kernel)setKernel(await call('kernels.status',{sessionId,kernelId:kernel.id},controller.signal));
  })().catch(e=>{if(e.name!=='AbortError')setError(e.message)});
  return()=>controller.abort();
 },[cell,sessionId]);
 async function perform(action){
  setBusy(true);setError('');
  try{
   const args=action==='create'?{sessionId,language}:{sessionId,kernelId:kernel.id,generation:kernel.generation,...(action==='execute'?{code}:{})};
   const value=await call('kernels.'+action,args);
   if(action==='execute'){setCell(value.id);setDetail(null);setKernel({...kernel,state:'running'})}
   else if(action==='close'){setKernel(null);setCell(null);setDetail(null)}
   else{setKernel(value);if(action==='reset'){setCell(null);setDetail(null)}}
   await refresh();
  }catch(e){setError(e.message)}finally{setBusy(false)}
 }
 if(!sessionId)return null;
 return <section className="a-operations a-computation" aria-label="Computation">
  <button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>Computation</button>
  {open&&<div className="a-operations-body">
   <p>Keep variables between cells. Code runs with this conversation’s approved file and network access. Reset clears variables.</p>
   <label>Language <select aria-label="Computation language" value={language} onChange={e=>setLanguage(e.target.value)}><option value="python">Python</option><option value="node">Node</option></select></label>
   <button type="button" data-action="kernels.create" disabled={busy} onClick={()=>perform('create')}>Create runtime</button>
   <ul>{kernels.map(row=><li key={row.id}><button type="button" data-action="kernels.status" onClick={()=>{setKernel(row);setCell(row.cellId);setDetail(null)}}>{row.language==='node'?'Node':'Python'} · {row.state} · Generation {row.generation}</button></li>)}</ul>
   {error&&<p role="alert">{error}</p>}
   {kernel&&<div><p><strong>{kernel.language==='node'?'Node':'Python'} {kernel.runtime?.version}</strong> · {kernel.state} · Generation {kernel.generation}</p>
    {kernel.reason&&<p>{kernel.reason}</p>}
    <label>Cell code<textarea aria-label="Cell code" rows={5} value={code} onChange={e=>setCode(e.target.value)} spellCheck={false}/></label>
    <button type="button" data-action="kernels.execute" disabled={busy||kernel.state!=='idle'||!code.trim()} onClick={()=>perform('execute')}>Run cell</button>
    <button type="button" data-action="kernels.interrupt" disabled={busy||kernel.state!=='running'} onClick={()=>perform('interrupt')}>Interrupt</button>
    <button type="button" data-action="kernels.reset" disabled={busy||kernel.operationState==='outcome_unknown'} onClick={()=>perform('reset')}>Reset variables</button>
    <button type="button" data-action="kernels.close" disabled={busy||kernel.operationState==='outcome_unknown'} onClick={()=>perform('close')}>Close runtime</button>
   </div>}
   {detail&&<div aria-live="polite"><strong>{detail.state}</strong>{detail.captureComplete===false||detail.outputComplete===false&&detail.state!=='running'?<p>Some output may be unavailable.</p>:null}
    <pre>{detail.chunks?.map(row=>row.text).join('')}{detail.evidence?.result??detail.evidence?.error??''}</pre>
    {detail.hasMore&&<p>More output is saved in Operations.</p>}
   </div>}
  </div>}
 </section>;
}
