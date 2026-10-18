import {useEffect,useRef,useState} from 'react';
import {request} from './api';

// Closed/off-screen groups do no detail IO. Bound reads across open groups.
let active=0;
const waiting=[];
function drain(){
 while(active<4&&waiting.length){const job=waiting.shift();if(job.signal.aborted){job.reject(new DOMException('Aborted','AbortError'));continue}active++;
  request(job.path,{...job.options,signal:job.signal}).then(job.resolve,job.reject).finally(()=>{active--;drain()});
 }
}
function read(path,signal,options={}){return new Promise((resolve,reject)=>{waiting.push({path,signal,options,resolve,reject});drain()})}

// Bundled paged sync: each POST returns only an ordered subset of its <=100
// known IDs and full replacement rows for changed IDs. The first response
// establishes the revision; later chunks and the final latest GET must match.
// Nothing mutates the saved snapshot before every response has been validated.
export async function syncWorkDetail(previous,syncChunk,readLatest){
 const invalid=()=>{throw new Error('Work details changed or returned an invalid page. Reload the steps to try again.')};
 const rowsById=(rows,limit)=>{
  if(!Array.isArray(rows)||rows.length>limit)invalid();
  const result=new Map();
  for(const row of rows){
   if(!row||typeof row.id!=='string'||!row.id||typeof row.detailVersion!=='string'||result.has(row.id))invalid();
   result.set(row.id,row);
  }
  return result;
 };
 const original=rowsById(previous.items,Infinity),ordered=[],staged=new Map();
 let revision;
 const validate=result=>{
  if(!result||result.group!==previous.group||typeof result.revision!=='string'||!result.revision
   ||revision!==undefined&&result.revision!==revision)invalid();
  revision=result.revision;
  return rowsById(result.items,100);
 };
 for(let start=0;start<Math.max(1,previous.items.length);start+=100){
  const knownRows=previous.items.slice(start,start+100);
  const positions=new Map(knownRows.map((row,index)=>[row.id,index]));
  const result=await syncChunk({paged:true,known:Object.fromEntries(knownRows.map(row=>[row.id,row.detailVersion])),
   ...(revision===undefined?{}:{revision})});
  const changed=validate(result);
  if(!Array.isArray(result.order)||result.order.length>100)invalid();
  let last=-1;
  for(const id of result.order){
   const position=positions.get(id);
   if(position===undefined||position<=last)invalid();
   last=position;ordered.push(id);staged.set(id,changed.get(id)||original.get(id));
  }
  const retained=new Set(result.order);
  for(const id of changed.keys())if(!retained.has(id))invalid();
 }
 // preserve=true must retain admitted older ranges/field refs and return the
 // refreshed OLDEST cursor for the combined window, not the latest-page cursor.
 const latest=await readLatest(revision),latestRows=validate(latest);
 if(latest.before!==null&&(typeof latest.before!=='string'||!latest.before))invalid();
 const overlap=[];
 for(const [id,row] of latestRows){
  if(original.has(id)&&!staged.has(id))invalid(); // No same-revision resurrection.
  if(staged.has(id)){
   if(staged.get(id).detailVersion!==row.detailVersion)invalid();
   overlap.push(id);
  }
 }
 const suffix=ordered.slice(ordered.length-overlap.length);
 if(overlap.some((id,index)=>id!==suffix[index]))invalid();
 const items=[...ordered.filter(id=>!latestRows.has(id)).map(id=>staged.get(id)),...latest.items];
 return {...previous,...latest,items,order:items.map(row=>row.id)};
}

export function useWorkDetail(data,turn,open,automatic=false){
 const element=useRef(null),run=useRef(null),[visible,setVisible]=useState(false),[saved,setSaved]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const key=JSON.stringify([data.sessionId,turn.id,turn.windowMessageId]),current=useRef(key);current.current=key;
 const enabled=!!data.detailsDeferred&&open&&(visible||!automatic)&&turn.detailRevision!=null;
 const page=saved?.key===key?saved:null;
 const latest=useRef(turn.detailRevision);latest.current=turn.detailRevision;
 useEffect(()=>{
  if(!open||!automatic){setVisible(false);return}
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(entries=>setVisible(entries.some(entry=>entry.isIntersecting)),{rootMargin:'400px'});
  if(element.current)observer.observe(element.current);return()=>observer.disconnect();
 },[open,automatic]);
 useEffect(()=>{
  const state={abort:new AbortController(),page:page||null,busy:false,wanted:latest.current,handled:null,timer:null};run.current=state;
  if(!open){state.page=null;setSaved(null)}
  setError('');setBusy(false);
  const live=()=>!state.abort.signal.aborted&&current.current===key;
  state.schedule=()=>{if(!enabled||state.busy||state.timer||!live())return;state.timer=setTimeout(()=>{state.timer=null;state.load()},100)};
  state.load=async(before=null)=>{
   if(!live()||!enabled||state.busy)return;
   state.busy=true;setBusy(true);setError('');const target=state.wanted;
   try{
    const previous=state.page;
    const windowArgs=turn.windowMessageId?{windowMessageId:turn.windowMessageId}:{};
    const args={sessionId:data.sessionId,part:'nodes',group:turn.id,...windowArgs};if(before)args.before=before;
    const sync=previous&&!before;
    const result=sync?await syncWorkDetail(previous,
     body=>read('/api/conversation/work-sync',state.abort.signal,{method:'POST',body:{sessionId:data.sessionId,group:turn.id,...windowArgs,...body}}),
     revision=>read('/api/conversation/detail?'+new URLSearchParams({...args,preserve:true,revision}),state.abort.signal)
    ):await read('/api/conversation/detail?'+new URLSearchParams(args),state.abort.signal);
    if(!live())return;
    const items=before?[...result.items,...previous.items.filter(row=>!result.items.some(older=>older.id===row.id))]:result.items;
    state.page={key,...result,items};setSaved(state.page);
    if(!before)state.handled=target;
   }catch(e){if(live()){setError(e.message);state.handled=state.wanted}}
   finally{state.busy=false;if(live()){setBusy(false);if(state.handled!==state.wanted)state.schedule()}}
  };
  if(enabled)state.schedule();
  return()=>{clearTimeout(state.timer);state.abort.abort()};
 },[enabled,key,open]);
 useEffect(()=>{const state=run.current;if(state){state.wanted=turn.detailRevision;if(state.handled!==state.wanted)state.schedule()}},[turn.detailRevision]);
 const nodes=data.detailsDeferred?(page?.items||[]).map(node=>({...node,turnId:turn.id})):data.nodes;
 return {element,nodes,busy:enabled&&(!page&&!error||busy),initialLoading:enabled&&!page&&!error,error,before:page?.before,
  reload:()=>run.current?.schedule(),earlier:()=>{if(page?.before)run.current?.load(page.before)}};
}
