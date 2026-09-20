import React,{useEffect,useRef,useState} from 'react';
import {clientUrl} from './api';
import './canvas-app-viewer.css';

const targetOf=canvas=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,canvas[key]]));
const tokens=['bg','surface','soft','ink','muted','line','accent','tint','green','danger'];
function hostTheme(){
 const root=document.getElementById('amp-one'),style=getComputedStyle(root||document.documentElement);
 return {version:1,tokens:Object.fromEntries(tokens.map(name=>[name,style.getPropertyValue('--a-'+name).trim()])),
  scheme:style.colorScheme==='dark'?'dark':style.colorScheme==='light'?'light':matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light',
  reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,contrast:matchMedia('(prefers-contrast: more)').matches?'more':'normal'};
}

export function CanvasAppViewer({canvas,dispatch}){
 const [mounted,setMounted]=useState(canvas),[error,setError]=useState(''),[dirty,setDirty]=useState(false),[review,setReview]=useState(null);
 const frame=useRef(),latest=useRef(canvas),shown=useRef(mounted),dirtyRef=useRef(false),busy=useRef(0),editVersion=useRef(0),channel=useRef(crypto.randomUUID()),seen=useRef(new Map());
 latest.current=canvas;shown.current=mounted;
 const app=canvas.app;
 const mount=value=>{editVersion.current=0;channel.current=crypto.randomUUID();seen.current.clear();setReview(null);setMounted(value)};
 const send=(extra={})=>{
  const current=latest.current;
  if(current.app.revision!==shown.current.app.revision)return;
  frame.current?.contentWindow?.postMessage({type:'canvas-app-host',id:current.id,channel:channel.current,
   snapshot:{id:current.id,app:current.app,theme:hostTheme()},...extra},'*');
 };
 const declare=async value=>{
  dirtyRef.current=value;setDirty(value);
  try{await dispatch('canvas.views.dirty',{...targetOf(shown.current),dirty:value})}catch(e){if(value)setError(e.message)}
 };
 useEffect(()=>{
  if(canvas.app.revision!==mounted.app.revision&&!dirtyRef.current&&!busy.current){mount(canvas)}
  else send();
 },[canvas]);
 useEffect(()=>{
  const receive=async event=>{
   const data=event.data;
   if(event.source!==frame.current?.contentWindow||data?.type!=='canvas-app'||data.id!==latest.current.id)return;
   if(data.op==='ready'){send();return}
   if(data.channel!==channel.current)return;
   if(data.op==='status'){const status=data.status==='error'?'error':'ready',message=String(data.message||'').slice(0,1000);dispatch('canvas.views.status',{...targetOf(shown.current),status,message}).catch(()=>{});if(status==='error')setError(message);return}
   if(data.op==='editing'){if(Number.isSafeInteger(data.editVersion)&&data.editVersion>editVersion.current)editVersion.current=data.editVersion;declare(true);return}
   if(!['state','event','request','dirty'].includes(data.op)||typeof data.requestId!=='string'||data.requestId.length>80)return;
   const reply=extra=>frame.current?.contentWindow?.postMessage({type:'canvas-app-host',id:latest.current.id,channel:channel.current,requestId:data.requestId,...extra},'*');
   if(JSON.stringify(data).length>320000){reply({error:'This change exceeds the surface message size limit.'});return}
   if(data.revision!==shown.current.app.revision){reply({error:'This design changed. Inspect the current surface before retrying.'});return}
   if(seen.current.has(data.requestId)){const previous=seen.current.get(data.requestId);if(previous)reply(previous);return}
   if(seen.current.size>=128){const oldest=[...seen.current].find(([,value])=>value!==null);if(oldest)seen.current.delete(oldest[0]);else{reply({error:'Wait for pending surface changes to finish.'});return}}
   seen.current.set(data.requestId,null);busy.current++;const editing=data.editVersion;
   try{
    let result;
    if(data.op==='dirty'){await declare(!!data.args?.dirty);result={app:latest.current.app}}
    else{
     const allowed=data.op==='state'?{patch:data.args?.patch}:data.op==='event'?{name:data.args?.name,payload:data.args?.payload}:{name:data.args?.name,input:data.args?.input};
     result=(await dispatch('canvas.apps.'+data.op,{...allowed,id:shown.current.id,sessionId:shown.current.sessionId,
      expectedRevision:shown.current.app.revision,expectedStateRevision:data.stateRevision}, {id:channel.current+':'+data.requestId})).result;
     latest.current={...latest.current,app:result.app};
     if((data.op==='state'||data.op==='event')&&editing===editVersion.current)await declare(false);
    }
    const response={snapshot:{id:latest.current.id,app:result.app,theme:hostTheme()}};seen.current.set(data.requestId,response);reply(response);setError('');
   }catch(e){const response={error:e.message};seen.current.set(data.requestId,response);reply(response);setError(e.message)}
   finally{busy.current--;if(!busy.current&&!dirtyRef.current&&latest.current.app.revision!==shown.current.app.revision)mount(latest.current)}
  };
  window.addEventListener('message',receive);
  let scheduled;
  const refresh=()=>{cancelAnimationFrame(scheduled);scheduled=requestAnimationFrame(()=>send())};
  const observer=new MutationObserver(refresh);observer.observe(document.head,{subtree:true,childList:true,characterData:true});
  const root=document.getElementById('amp-one');if(root)observer.observe(root,{attributes:true,subtree:true,childList:true,characterData:true});
  const queries=['prefers-color-scheme: dark','prefers-reduced-motion: reduce','prefers-contrast: more'].map(q=>matchMedia('('+q+')'));
  queries.forEach(q=>q.addEventListener('change',refresh));
  return()=>{window.removeEventListener('message',receive);observer.disconnect();cancelAnimationFrame(scheduled);queries.forEach(q=>q.removeEventListener('change',refresh))};
 },[dispatch]);
 const args=()=>({id:canvas.id,sessionId:canvas.sessionId,expectedRevision:latest.current.app.revision,expectedStateRevision:latest.current.app.stateRevision});
 const run=async(action,extra)=>{try{const result=await dispatch(action,{...args(),...extra});setError('');return result}catch(e){setError(e.message)}};
 const preview=async request=>{const result=await dispatch('canvas.apps.inspect',{id:canvas.id,sessionId:canvas.sessionId,requestId:request.id});setReview({request,input:result.result.requestInput})};
 const url=clientUrl(`/api/canvas/${mounted.id}/document?`+new URLSearchParams(targetOf(mounted)));
 return <section className="a-canvas-app" aria-label="Interactive conversation surface">
  <div className="a-canvas-app-bar"><span>Revision {app.revision}{dirty?' · Unsaved input':''}</span>
   <details><summary>History & shared state</summary><p>Restoring a design keeps compatible current inputs and never repeats actions.</p>
    {app.versions.map(v=><button type="button" key={v.version} disabled={v.version===app.revision||dirty} onClick={()=>run('canvas.apps.restore',{version:v.version})}>Restore revision {v.version}</button>)}
    <pre aria-label="Shared surface state">{JSON.stringify(app.state,null,2)}</pre>
   </details>
  </div>
  {error&&<p role="alert">{error}</p>}
  {mounted.app.revision!==app.revision&&<p role="status">A new design is ready. Your unfinished input is still here. <button type="button" onClick={async()=>{await declare(false);mount(canvas);setError('')}}>Discard unfinished input and load revision {app.revision}</button></p>}
  {app.requests.filter(r=>r.status==='pending').map(r=><div className="a-canvas-app-request" key={r.id}><span>{r.action==='theme.preview'?'Preview on this device':r.action==='theme.apply'?'Apply to the shared shell':'Revert this device’s last theme change'}: {r.summary}</span><button type="button" onClick={()=>preview(r).catch(e=>setError(e.message))}>Review theme change</button><button type="button" onClick={()=>run('canvas.apps.resolve',{requestId:r.id,approve:false})}>Decline</button></div>)}
  {review&&app.requests.some(r=>r.id===review.request.id&&r.status==='pending')&&<section className="a-canvas-app-review" aria-label="Review requested theme change"><strong>{review.request.summary}</strong><p>{review.request.action==='theme.apply'?'Applying changes the shell for all connected clients.':'This action uses this device’s theme controls.'}</p><pre>{review.input.css||'End the preview, or undo the last applied theme if it is still current.'}</pre><button type="button" onClick={async()=>{if(await run('canvas.apps.resolve',{requestId:review.request.id,approve:true}))setReview(null)}}>Approve theme change</button><button type="button" onClick={()=>setReview(null)}>Back</button></section>}
  <iframe key={mounted.app.revision} title={canvas.title} ref={frame} src={url} sandbox="allow-scripts" onLoad={()=>send()}/>
 </section>;
}
