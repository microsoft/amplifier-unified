import {useEffect,useRef,useState} from 'react';
import {request} from './api';

// Detailed presentation can expand many groups. Bound concurrent reads and
// defer off-screen groups rather than issuing one request per saved turn.
let active=0;
const waiting=[];
function drain(){
 while(active<4&&waiting.length){const job=waiting.shift();if(job.signal.aborted){job.reject(new DOMException('Aborted','AbortError'));continue}active++;
  request(job.path,{signal:job.signal}).then(job.resolve,job.reject).finally(()=>{active--;drain()});
 }
}
function read(path,signal){return new Promise((resolve,reject)=>{waiting.push({path,signal,resolve,reject});drain()})}

export function useWorkDetail(data,turn,open,automatic=false){
 const element=useRef(null),controller=useRef(null),[visible,setVisible]=useState(false),[saved,setSaved]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 const key=JSON.stringify([data.sessionId,turn.id,turn.detailRevision]),current=useRef(key);current.current=key;
 const enabled=!!data.detailsDeferred&&open&&(visible||!automatic)&&turn.detailRevision!=null;
 const page=saved?.key===key?saved:null;
 useEffect(()=>{
  if(!open||!automatic){setVisible(false);return}
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(entries=>setVisible(entries.some(entry=>entry.isIntersecting)),{rootMargin:'400px'});
  if(element.current)observer.observe(element.current);return()=>observer.disconnect();
 },[open,automatic]);
 async function load(before,signal){
  const captured=key;setBusy(true);setError('');
  try{
   const args={sessionId:data.sessionId,part:'nodes',group:turn.id};if(before){args.before=before;args.revision=page.revision}
   const result=await read('/api/conversation/detail?'+new URLSearchParams(args),signal);
   if(signal.aborted||current.current!==captured)return;
   setSaved(previous=>({key:captured,...result,items:before&&previous?.key===captured?[...result.items,...previous.items]:result.items}));
  }catch(e){if(!signal.aborted&&current.current===captured)setError(e.message)}
  finally{if(!signal.aborted&&current.current===captured)setBusy(false)}
 }
 useEffect(()=>{
  const abort=new AbortController();controller.current=abort;
  if(!open){setSaved(null);setError('');setBusy(false)}
  // Coalesce bursts of tool/usage events. Closed groups do no detail IO.
  const timer=enabled?setTimeout(()=>load(null,abort.signal),100):null;
  return()=>{clearTimeout(timer);abort.abort()};
 },[enabled,key,retry]);
 const nodes=data.detailsDeferred?(page?.items||[]).map(node=>({...node,turnId:turn.id})):data.nodes;
 return {element,nodes,busy:enabled&&(!page&&!error||busy),error,before:page?.before,
  reload:()=>setRetry(value=>value+1),earlier:()=>{if(!busy&&page?.before)load(page.before,controller.current.signal)}};
}
