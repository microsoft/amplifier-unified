import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {FileText,Globe,Code,Film,ChevronDown,ChevronUp,Maximize2,Image as ImageIcon,X} from 'lucide-react';
import {request,clientUrl} from './api';
import {chatArtifacts} from './canvas-library';
import {Renderer} from './canvas-workspace';
import {ImageActions} from './image-actions';
import {CanvasControlsHost} from './canvas-controls';
import {imageGalleryEntries,imageGallerySelection,imageJobLabel} from './image-generation';
import {artifactPublications,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,inlineViewAccess,artifactViewCanvas,requireArtifactBody,followsLatest,retainedThumbnailTuple} from './artifact-presentation';
import {mcpConnectionKey} from './mcp-app-lifecycle';
import {createArtifactOverrides,inlineOwnership,inlineEditNotice} from './conversation-navigation';
import './inline-artifacts.css';

const overrides=createArtifactOverrides();
const targetOf=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
const titleOf=({row,version})=>(row.app?.versions||row.versions||[]).find(v=>v.version===version)?.title||row.title;

// A selected gallery body keeps its exact admission until unmount. A revoked
// target never revives automatically, even if the same target reappears.
function useImageView(item,messageId,state,dispatch,enabled,attempt=0){
 const {row,version}=item,identity=JSON.stringify([row.id,version,messageId]);
 const run=useRef(dispatch),latest=useRef(state),owned=useRef(null);
 run.current=dispatch;latest.current=state;
 const [loaded,setLoaded]=useState(null),[error,setError]=useState('');
 const record=loaded?.record.identity===identity?loaded.record:null;
 const access=inlineViewAccess(record,state),revoked=access.status==='revoked';
 if(record&&access.status==='live')record.seen=true;
 if(revoked)record.revoked=true;
 useLayoutEffect(()=>{
  if(!record||!revoked&&enabled)return;
  record.abort();void record.release().catch(()=>{});
  setLoaded(old=>old?.record===record&&old.canvas?{record,canvas:null}:old);
 },[record,revoked,enabled]);
 useEffect(()=>{
  if(!enabled)return;
  let active=true,record;const controller=new AbortController();
  setLoaded(null);setError('');
  const hostInstanceId=state.client?.hostInstanceId,clientId=state.client?.id??state.client?.clientId;
  const release=async()=>{
   if(!record||record.released)return;
   // Never dispatch an old scope's target into another host/client.
   if(record.hostInstanceId!==latest.current.client?.hostInstanceId||record.clientId!==(latest.current.client?.id??latest.current.client?.clientId))return;
   if(!record.pending)record.pending=run.current('canvas.views.release',targetOf(record.view)).then(receipt=>{
    if(receipt?.accepted===false)throw Error(receipt.error||'The preview could not be closed.');
    record.released=true;
   }).finally(()=>{record.pending=null});
   await record.pending;
  };
  run.current('canvas.views.inline',{resourceId:row.id,version,messageId,previewId:crypto.randomUUID()}).then(async receipt=>{
   if(receipt?.accepted===false)throw Error(receipt.error||'Inline preview was not accepted.');
   record={identity,view:receipt.result,hostInstanceId:receipt.hostInstanceId??receipt.state?.client?.hostInstanceId??hostInstanceId,
    clientId:receipt.state?.client?.id??receipt.state?.client?.clientId??clientId,stateRevision:receipt.stateRevision??receipt.state?.revision,
    abort:()=>controller.abort(),release,released:false,seen:false,revoked:false};
   owned.current=record;
   if(!active){await release();return}
   setLoaded({record,canvas:null});
   if(inlineViewAccess(record,latest.current).status==='revoked'){record.revoked=true;await release();return}
   const canvas=await request(`/api/canvas/views/${record.view.viewId}/resource?`+new URLSearchParams(targetOf(record.view)),{signal:controller.signal});
   if(!active||record.revoked||inlineViewAccess(record,latest.current).status==='revoked'){
    record.revoked=true;await release();return;
   }
   setLoaded({record,canvas});
  }).catch(error=>{
   if(active&&!record?.revoked&&error.name!=='AbortError'){
    if(record)record.localRelease=true;
    setError(error.message);void release().catch(()=>{});
   }
  });
  return()=>{active=false;controller.abort();void release().catch(()=>{});if(owned.current===record)owned.current=null};
 },[enabled,attempt,row.id,version,messageId]);
 const view=access.view;
 const canvas=enabled&&view&&loaded?.canvas&&{...loaded.canvas,...targetOf(view),view:view.view,renderReports:view.renderReports,document:view.document,interaction:view.interaction,events:view.events};
 const replaceImageContent=content=>{
  if(!record||inlineViewAccess(record,latest.current).status!=='live')return;
  setLoaded(old=>old?.record===record&&old.canvas?{record,canvas:{...old.canvas,content}}:old);
 };
 const close=async()=>{
  const record=owned.current;if(!record)return;
  record.localRelease=true;
  try{await record.release()}catch(error){record.localRelease=false;throw error}
 };
 return {view,canvas,revoked,error,setError,replaceImageContent,release:close};
}

// Body downloads start only near the viewport. Once opened, a document stays
// mounted until explicitly closed or its conversation leaves the screen.
function Preview({item,messageId,state,dispatch,fullView,imageFrame=false}){
 const {version}=item,root=useRef();
 const [near,setNear]=useState(false),[closed,setClosed]=useState(false),[attempt,setAttempt]=useState(0);
 const {view,canvas,revoked,error,setError,release}=useImageView(item,messageId,state,dispatch,near&&!closed,attempt);
 useEffect(()=>{
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setNear(true);observer.disconnect()}},{rootMargin:'160px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[]);
 const close=async()=>{
  try{await release();setClosed(true)}catch(error){setError(error.message)}
 };
 return <section ref={root} className="a-inline-artifact" data-image-frame={imageFrame||undefined} aria-label={titleOf(item)}>
  <div className="a-inline-artifact-heading"><strong>{titleOf(item)}</strong><span>Version {version}</span>
   <button type="button" className="a-icon" data-action="canvas.select" aria-label={`Open ${titleOf(item)} in full view`} onClick={()=>fullView(item)}><Maximize2/></button>
   {!closed&&!revoked&&<button type="button" className="a-icon" aria-label={`Close preview of ${titleOf(item)}`} onClick={close}><X/></button>}
  </div>
  <div className={imageFrame?"a-gallery-image-frame":closed?undefined:"a-inline-document-frame"}>
   {closed?<button type="button" className="a-link a-inline-artifact-notice" onClick={()=>setClosed(false)}>Show preview</button>:
    revoked?<p role="status">Preview access was removed by the host.</p>:
    error?<div role="status" className="a-inline-artifact-notice"><p>{error}</p><button type="button" className="a-soft" onClick={()=>setAttempt(value=>value+1)}>Try preview again</button><button type="button" className="a-link" onClick={()=>fullView(item)}>Open in Canvas</button></div>:
    !canvas?<div className="a-inline-artifact-placeholder" role="status">{near?'Loading preview…':'Saved artifact'}</div>:
    canvas.kind==='image'?<button type="button" className="a-inline-image" data-action="canvas.select" aria-label={`Open ${titleOf(item)} in full view`} onClick={()=>fullView(item)}><img src={canvas.content} alt={titleOf(item)} onError={()=>setError('This image could not be displayed. Your saved artifact is still available.')}/></button>:
    <CanvasControlsHost.Provider value={null}><div className="a-inline-artifact-body"><Renderer key={view.resourceRevision+':'+view.generation} view={view} canvas={canvas} dispatch={dispatch}/></div></CanvasControlsHost.Provider>}
  </div>
  {imageFrame?<div className="a-gallery-image-footer">{canvas?.kind==='image'&&!closed&&<ImageActions canvas={canvas} dispatch={dispatch} compact/>}</div>:canvas?.kind==='image'&&!closed&&<ImageActions canvas={canvas} dispatch={dispatch}/>}
 </section>;
}

function Generation({job}){
 return <div className="a-image-generation" data-running={job.phase==='running'||undefined} role="status"><ImageIcon aria-hidden="true"/><span>{imageJobLabel(job)}</span></div>;
}

// Two slots cover the entire fetch/decode lifetime, not just drawing. Queued
// cancelled jobs are removed; no polling, automatic retries or Canvas views.
const thumbnailQueue=[];let thumbnailSlots=0;
function thumbnailWork(signal,work){
 return new Promise((resolve,reject)=>{
  const job={signal,work,resolve,reject};
  job.cancel=()=>{const at=thumbnailQueue.indexOf(job);if(at>=0){thumbnailQueue.splice(at,1);reject(new DOMException('Thumbnail cancelled','AbortError'))}};
  if(signal.aborted){reject(new DOMException('Thumbnail cancelled','AbortError'));return}
  signal.addEventListener('abort',job.cancel,{once:true});thumbnailQueue.push(job);drainThumbnails();
 });
}
function drainThumbnails(){
 while(thumbnailSlots<2&&thumbnailQueue.length){
  const job=thumbnailQueue.shift();job.signal.removeEventListener('abort',job.cancel);
  if(job.signal.aborted){job.reject(new DOMException('Thumbnail cancelled','AbortError'));continue}
  thumbnailSlots++;
  Promise.resolve().then(job.work).then(job.resolve,job.reject).finally(()=>{thumbnailSlots--;drainThumbnails()});
 }
}
async function thumbnailPixels(tuple,signal){
 let bytes,image,small;
 const check=()=>{if(signal.aborted)throw new DOMException('Thumbnail cancelled','AbortError')};
 try{
  check();
  const response=await fetch(clientUrl(tuple.url),{signal,credentials:'same-origin',redirect:'error'});
  if(!response.ok)throw Error(`Thumbnail unavailable (${response.status})`);
  if(response.headers.get('Content-Type')?.split(';')[0]!=='image/png')throw Error('Thumbnail is not a saved PNG');
  const length=Number(response.headers.get('Content-Length'));
  if(!Number.isSafeInteger(length)||length<45||length>8*1024*1024)throw Error('Thumbnail exceeds the saved PNG limit');
  bytes=await response.arrayBuffer();check();
  if(bytes.byteLength!==length)throw Error('Thumbnail bytes changed');
  const header=new Uint8Array(bytes),view=new DataView(bytes);
  if(![137,80,78,71,13,10,26,10].every((value,index)=>header[index]===value)||
   view.getUint32(8)!==13||view.getUint32(12)!==0x49484452)throw Error('Thumbnail PNG header is invalid');
  const width=view.getUint32(16),height=view.getUint32(20);
  if(width<1||height<1||width>4096||height>4096||header[24]!==8||![2,6].includes(header[25]))throw Error('Thumbnail PNG dimensions are invalid');
  const canonical=response.headers.get('X-Amplifier-Image-SHA256');
  if(canonical||tuple.sha256){
   if(canonical&&!/^[a-f0-9]{64}$/.test(canonical)||canonical&&tuple.sha256&&canonical!==tuple.sha256)throw Error('Thumbnail binding changed');
   const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(value=>value.toString(16).padStart(2,'0')).join('');
   if(digest!==(canonical||tuple.sha256))throw Error('Thumbnail hash changed');
   check();
  }
  if(globalThis.createImageBitmap)image=await createImageBitmap(new Blob([bytes],{type:'image/png'}));
  else{
   image=new Image();
   await new Promise((resolve,reject)=>{
    const clear=()=>{signal.removeEventListener('abort',cancel);image.onload=image.onerror=null};
    const cancel=()=>{clear();image.src='';reject(new DOMException('Thumbnail cancelled','AbortError'))};
    image.onload=()=>{clear();resolve()};image.onerror=()=>{clear();reject(Error('Thumbnail PNG could not be decoded'))};
    signal.addEventListener('abort',cancel,{once:true});
    let binary='';for(let at=0;at<header.length;at+=32768)binary+=String.fromCharCode(...header.subarray(at,at+32768));
    image.src='data:image/png;base64,'+btoa(binary);
    if(signal.aborted)cancel();
   });
  }
  check();
  if((image.naturalWidth??image.width)!==width||(image.naturalHeight??image.height)!==height)throw Error('Thumbnail decoded dimensions changed');
  small=document.createElement('canvas');const scale=Math.min(1,80/Math.max(width,height));
  small.width=Math.max(1,Math.round(width*scale));small.height=Math.max(1,Math.round(height*scale));
  small.getContext('2d').drawImage(image,0,0,small.width,small.height);
  const pixels=small.toDataURL('image/png');check();return pixels;
 }finally{
  bytes=null;
  if(image?.close)image.close();else if(image){image.onload=image.onerror=null;image.src=''}
  if(small){small.width=0;small.height=0}
 }
}

function Thumbnail({item,index,messageId,state}){
 const root=useRef(),current=useRef(null);
 const [near,setNear]=useState(false),[loaded,setLoaded]=useState(null),[attempt,setAttempt]=useState(0);
 const tuple=retainedThumbnailTuple(item,messageId,state),identity=tuple?.identity;
 current.current=identity;
 useEffect(()=>{
  if(!globalThis.IntersectionObserver){setNear(true);return}
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setNear(true);observer.disconnect()}},{rootMargin:'80px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  setLoaded(null);
  if(!near||!tuple)return;
  const controller=new AbortController();let active=true;
  thumbnailWork(controller.signal,()=>thumbnailPixels(tuple,controller.signal)).then(pixels=>{
   if(active&&current.current===identity)setLoaded({identity,pixels});
  }).catch(error=>{if(active&&current.current===identity&&error.name!=='AbortError')setLoaded({identity,error:true})});
  return()=>{active=false;controller.abort()};
 },[near,identity,attempt]);
 const result=loaded?.identity===identity?loaded:null;
 // The rail already owns a button: retry is a labelled keyboard/click action
 // on that choice, never invalid nested button markup or a new selection.
 const retry=event=>{
  if(!result?.error)return;
  if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
  event.preventDefault();event.stopPropagation();setLoaded(null);setAttempt(value=>value+1);
 };
 const failed=!!result?.error;
 useLayoutEffect(()=>{
  const button=root.current?.closest('button');if(!button)return;
  const label=button.getAttribute('aria-label');
  if(failed)button.setAttribute('aria-label',`Retry thumbnail for Image ${index+1}: ${titleOf(item)}`);
  const action=event=>retry(event);
  if(failed){button.addEventListener('keydown',action);button.addEventListener('click',action,true)}
  return()=>{if(failed){button.setAttribute('aria-label',label);button.removeEventListener('keydown',action);button.removeEventListener('click',action,true)}};
 },[failed,identity,index]);
 return <span ref={root} className="a-gallery-thumbnail" title={failed?'Retry thumbnail':undefined}>
  {result?.pixels?<img alt="" src={result.pixels} onError={()=>setLoaded({identity,error:true})}/>:<><ImageIcon/><span>{failed?'Retry thumbnail':index+1}</span></>}
 </span>;
}

function ImageGallery({entries,...props}){
 const [selected,setSelected]=useState(()=>entries[0]?.id);
 const entry=imageGallerySelection(entries,selected);
 useEffect(()=>{if(entry&&entry.id!==selected)setSelected(entry.id)},[entry?.id,selected]);
 const title=entry=>entry.item?titleOf(entry.item):imageJobLabel(entry.job);
 return <div className="a-inline-gallery a-image-gallery-stable" aria-label={`Image gallery, ${entries.length} ${entries.length===1?'image':'images'}`}>
  {entry.item?<Preview key={entry.id+':'+entry.item.row.id+':'+entry.item.version} item={entry.item} {...props} imageFrame/>:<section className="a-inline-artifact" data-image-frame>
   <div className="a-inline-artifact-heading"><strong>{entry.job.operation==='edit'?'Editing image':'Generated image'}</strong></div>
   <div className="a-gallery-image-frame"><Generation job={entry.job}/></div>
   <div className="a-gallery-image-footer"/>
  </section>}
  {entries.length>1&&<div className="a-inline-gallery-choices" role="group" aria-label="Choose an image">{entries.map((choice,index)=><button type="button" key={choice.id} aria-label={`Image ${index+1}: ${title(choice)}`} aria-pressed={choice.id===entry.id} data-running={!choice.item&&choice.job.phase==='running'||undefined} onClick={()=>setSelected(choice.id)}>{choice.item?<Thumbnail key={choice.item.row.id+':'+choice.item.version} item={choice.item} index={index} messageId={props.messageId} state={props.state} dispatch={props.dispatch}/>:<><ImageIcon/><span>{index+1}</span></>}</button>)}</div>}
 </div>;
}

// General artifacts own one exact inline view. Release must settle before
// changing mode; a dirty refusal keeps the mounted editor and its controls.
export function GeneralArtifact({item,messageId,state,dispatch}){
 const {row,version}=item,identity=artifactOverrideKey(state,item,messageId),caps=presentationCapabilities(item);
 const [override,setOverride]=useState(()=>overrides.get(identity));
 const desired=override??publicationMode(item);
 const [mode,setMode]=useState(desired),[near,setNear]=useState(false),[loaded,setLoaded]=useState(null);
 const [notice,setNotice]=useState(''),[refusal,setRefusal]=useState(''),[busy,setBusy]=useState(false),[attempt,setAttempt]=useState(0);
 const [bodyError,setBodyError]=useState(''),[bodyAttempt,setBodyAttempt]=useState(0);
 const followCommand=useRef(null),latest=useRef(state);latest.current=state;
 const root=useRef(null),owned=useRef(null),run=useRef(dispatch),changing=useRef(false),opening=useRef(null),mounted=useRef(false);
 run.current=dispatch;
 const access=inlineViewAccess(loaded,state);
 const sameClient=loaded&&loaded.hostInstanceId===state.client?.hostInstanceId&&loaded.clientId===(state.client?.id??state.client?.clientId);
 // A live canonical design can advance within the SAME admitted publication.
 // Static numbered views never adopt a different generation or primary body.
 const advancing=loaded&&!loaded.revoked&&loaded.seen&&loaded.view.followLatest===true&&followsLatest(row.kind)&&
  sameClient&&
  (state.canvasWorkspace?.views||[]).find(value=>value.viewId===loaded.view.viewId&&value.resourceId===row.id&&value.followLatest===true&&value.publishedVersion===version);
 const authorized=access.status==='live'?access.view:advancing;
 const view=authorized||loaded?.view,revoked=!!loaded&&!authorized&&(!loaded.pendingFollow||!sameClient)&&access.status==='revoked';
 if(loaded&&authorized){loaded.seen=true;loaded.view=view;loaded.lifecycle.view=view;if(owned.current===loaded)owned.current.view=view}
 if(revoked)loaded.revoked=true;
 const release=async record=>{
  const lifecycle=record?.lifecycle;
  if(!lifecycle||lifecycle.released)return;
  if(record.hostInstanceId!==latest.current.client?.hostInstanceId||record.clientId!==(latest.current.client?.id??latest.current.client?.clientId)){lifecycle.unregister?.();return}
  if(inlineOwnership.blocksTarget(lifecycle.view))throw Error(inlineEditNotice);
  if(!lifecycle.pending)lifecycle.pending=run.current('canvas.views.release',targetOf(lifecycle.view)).then(receipt=>{
   if(receipt?.accepted===false)throw Error(receipt.error||'The preview could not be closed.');
   lifecycle.released=true;
   lifecycle.unregister?.();
  }).finally(()=>{lifecycle.pending=null});
  await lifecycle.pending;
 };
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{
  if(!revoked)return;
  owned.current?.lifecycle.unregister?.();setNotice('The host removed access to this preview. Private input could not be retained.');
 },[revoked]);
 const binding=view&&JSON.stringify(targetOf(view));
 useLayoutEffect(()=>{
  if(!loaded||!authorized||revoked)return;
  loaded.lifecycle.unregister?.();
  const unregister=inlineOwnership.register(state,view,{sessionId:row.sessionId,messageId,version});
  loaded.lifecycle.unregister=unregister;
  return unregister;
 },[binding,!!authorized,revoked]);
 useEffect(()=>{
  if(!loaded||!authorized||revoked)return;
  let active=true;const controller=new AbortController(),target=targetOf(view);
  setBodyError('');
  request(`/api/canvas/views/${view.viewId}/resource?`+new URLSearchParams(target),{signal:controller.signal}).then(requireArtifactBody).then(canvas=>{
   if(active)setLoaded(old=>old===loaded?{...old,canvas,bodyBinding:binding}:old);
  }).catch(error=>{if(active&&error.name!=='AbortError')setBodyError(error.message)});
  return()=>{active=false;controller.abort()};
 // Body refresh never replaces the admitted view or runs app/tool work.
 },[binding,!!authorized,revoked,bodyAttempt]);
 useEffect(()=>{
  if(mode==='none'||!root.current)return;
  if(!globalThis.IntersectionObserver){setNear(true);return}
  const observer=new IntersectionObserver(entries=>{
   if(entries.some(entry=>entry.isIntersecting)){setNear(true);observer.disconnect()}
  },{rootMargin:'160px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[mode==='none']);
 useEffect(()=>{
  if(!near||mode!=='inline'||!caps.inline||refusal)return;
  let active=true,record;
  run.current('canvas.views.inline',{resourceId:row.id,version,messageId,previewId:crypto.randomUUID()}).then(async receipt=>{
   if(receipt?.accepted===false)throw Error(receipt.error||'Inline preview was not accepted.');
   record={view:receipt.result,lifecycle:{view:receipt.result,released:false},canvas:null,seen:false,revoked:false,
    hostInstanceId:receipt.hostInstanceId??receipt.state?.client?.hostInstanceId??state.client?.hostInstanceId,
    clientId:receipt.state?.client?.id??receipt.state?.client?.clientId??state.client?.id??state.client?.clientId,
    stateRevision:receipt.stateRevision??receipt.state?.revision};owned.current=record;
   if(!active){await release(record);return}
   setLoaded(record);
  }).catch(error=>{
   if(active&&error.name!=='AbortError'){
    // Capacity/resource refusal leaves a useful compact handoff, not an empty
    // inline body. No other mounted view is evicted and no override is erased.
    setRefusal(error.message);setNotice(error.message);
   }
  });
  return()=>{active=false;record?.lifecycle.unregister?.();void release(record).catch(()=>{})};
 // Capability refreshes do not tear down a live editor. A newly available
 // capability can be tried explicitly; publication/mode changes use release.
 },[near,mode,refusal,attempt,row.id,version,messageId]);
 // Keep cleanup/release ownership attached to the newest body record.
 if(loaded&&owned.current?.view.viewId===loaded.view.viewId)owned.current=loaded;
 async function follow(followLatest){
  if(changing.current||!loaded||!sameClient||(!authorized&&!followCommand.current)||revoked)return;
  changing.current=true;setBusy(true);setNotice('');
  try{
   if(inlineOwnership.blocksTarget(view)||view.dirty)throw Error(inlineEditNotice);
   if(!followCommand.current)followCommand.current={action:'canvas.views.follow',args:{...targetOf(view),followLatest},id:crypto.randomUUID()};
   loaded.pendingFollow=true;
   const command=followCommand.current,receipt=await run.current(command.action,command.args,{id:command.id});
   if(receipt?.accepted!==true)throw Error(receipt?.error||'The preview version change was not confirmed.');
   const next=receipt.result;
   if(next?.viewId!==view.viewId||next.resourceId!==row.id||next.publishedVersion!==version||next.followLatest!==command.args.followLatest||
    (next.followLatest?next.selectedVersion!==null:next.selectedVersion!==version))throw Error('The host did not return the exact publication view binding.');
   followCommand.current=null;
   loaded.lifecycle.unregister?.();loaded.lifecycle.view=next;
   const record={...loaded,view:next,canvas:null,bodyBinding:null,seen:false,revoked:false,pendingFollow:false,stateRevision:receipt.stateRevision??receipt.state?.revision};
   owned.current=record;
   if(mounted.current){setLoaded(record);setBodyError('');setBodyAttempt(value=>value+1)}
  }catch(error){if(!error.unconfirmed){followCommand.current=null;loaded.pendingFollow=false}if(mounted.current)setNotice(error.message)}
  finally{changing.current=false;if(mounted.current)setBusy(false)}
 }
 const changeMode=async(next,privateChoice=false)=>{
  if(changing.current)return;
  changing.current=true;setBusy(true);
  try{
   if(mode==='inline'&&next!=='inline')await release(owned.current);
   if(!mounted.current)return;
   if(next!=='inline'){owned.current=null;setLoaded(null)}
   setMode(next);setRefusal('');setNotice('');if(next==='inline')setAttempt(value=>value+1);
   if(privateChoice){
    setOverride(next);
    if(!overrides.set(identity,next))setNotice('This choice is private, but could not be saved across reload.');
   }
  }catch(error){if(mounted.current)setNotice(error.message)}
  finally{changing.current=false;if(mounted.current)setBusy(false)}
 };
 // Shared publication updates are defaults, not commands to discard dirty
 // private state. If release is refused, stay inline until an explicit retry.
 useEffect(()=>{if(desired!==mode)void changeMode(desired)},[desired]);
 async function open(){
  if(changing.current)return;
  changing.current=true;setBusy(true);setNotice('');
  try{
   if(!opening.current){
    const actions=globalThis.window?.amplifier?.getActions?.()||[];
    const command=canvasOpenCommand(item,messageId,actions);
    opening.current={...command,id:crypto.randomUUID()};
   }
   const command=opening.current;
   const receipt=await run.current(command.action,command.args,{id:command.id});
   if(receipt?.accepted===false){if(mounted.current)setNotice(receipt.error||'The Canvas request was not accepted.');return}
   opening.current=null;
   if(command.action==='canvas.present'){
    if(mounted.current)setNotice(canvasOpenNotice(receipt));
   }else if(receipt?.accepted){
    // Legacy selection still uses the normal dispatch queue and dirty guards.
    await run.current('view.update',{sessionId:row.sessionId,patch:{canvasFocused:false}});
   }else if(mounted.current)setNotice('Canvas opening could not be confirmed.');
  }catch(error){
   if(!error.unconfirmed)opening.current=null;
   if(mounted.current)setNotice(error.message);
  }finally{changing.current=false;if(mounted.current)setBusy(false)}
 }
 if(mode==='none')return null;
 const inline=mode==='inline'&&(caps.inline||!!loaded)&&!refusal;
 const Icon=row.kind==='image'?ImageIcon:['gif','mp4'].includes(row.kind)?Film:row.kind==='browser'?Globe:['code','html','json','jsonl'].includes(row.kind)?Code:FileText;
 const canvas=loaded?.canvas&&authorized&&!revoked&&
  (loaded.bodyBinding===binding||row.kind==='canvas-app'&&view.app)&&artifactViewCanvas(loaded.canvas,view);
 return <section ref={root} className="a-general-artifact a-inline-artifact" data-artifact-id={row.id} data-artifact-version={version} data-chat-mode={inline?'inline':'compact'} aria-label={titleOf(item)}>
  <div className="a-general-artifact-heading">
   <Icon aria-hidden="true"/><span className="a-general-artifact-title"><strong>{titleOf(item)}</strong><small>{row.kind} · Version {version}</small></span>
   <button type="button" className="a-soft" aria-label={`Open ${titleOf(item)} in Canvas`} data-action={canvasOpenCommand(item,messageId,globalThis.window?.amplifier?.getActions?.()||[]).action} disabled={busy||!caps.canvas} aria-busy={busy||undefined} onClick={open}><Maximize2 aria-hidden="true"/><span>Open in Canvas</span></button>
   {inline?<button type="button" className="a-soft" aria-label={`Collapse ${titleOf(item)}`} disabled={busy} aria-expanded={true} onClick={()=>changeMode('compact',true)}><ChevronUp aria-hidden="true"/>Collapse</button>:caps.inline&&<button type="button" className="a-soft" aria-label={`Expand ${titleOf(item)} in chat`} disabled={busy} aria-expanded={false} onClick={()=>changeMode('inline',true)}><ChevronDown aria-hidden="true"/>{refusal?'Retry preview':'Expand'}</button>}
   {inline&&(authorized||followCommand.current)&&followsLatest(row.kind)&&<button type="button" className="a-soft" disabled={busy} onClick={()=>follow(!view.followLatest)}>{followCommand.current?'Retry version change':view.followLatest?'Return to published version':'Go to Latest'}</button>}
  </div>
  {(notice||!caps.inline&&caps.reason||!caps.canvas)&&<p className="a-inline-artifact-notice" role="status">{notice||(!caps.canvas?'Canvas is unavailable for this artifact.':caps.reason)}</p>}
  {inline&&<div className="a-general-artifact-body" data-kind={row.kind}>
   {authorized&&followsLatest(row.kind)&&<p role="status">{view.followLatest?`Latest · Version ${view.latestVersion}`:`Published version ${version} · Read only`}</p>}
   {revoked?<p role="status">Preview access was removed by the host.</p>:bodyError?<div role="alert" className="a-inline-artifact-notice"><p>{bodyError}</p><button type="button" onClick={()=>setBodyAttempt(value=>value+1)}>Retry source</button></div>:null}
   {!revoked&&canvas?<CanvasControlsHost.Provider value={undefined}><Renderer key={row.kind==='canvas-app'?view.viewId+':'+(view.selectedVersion??'latest'):view.viewId+':'+view.resourceRevision+':'+view.generation} view={view} canvas={canvas} dispatch={dispatch} connectionKey={mcpConnectionKey(canvas,state.smartTools)}/></CanvasControlsHost.Provider>:!revoked&&!bodyError&&<div className="a-inline-artifact-placeholder" role="status">{near?'Loading preview…':'Saved artifact'}</div>}
  </div>}
 </section>;
}

export function ArtifactLinks({state,message,act,dispatch}){
 const committed=useRef([]);
 const rows=artifactPublications(chatArtifacts(state),message.id);
 let entries=imageGalleryEntries(state,message.id,rows);
 const generated=new Set(entries.filter(entry=>entry.item).map(entry=>entry.item));
 const same=(a,b)=>a.row.id===b.row.id&&a.version===b.version;
 // Unlink or reclassification can remove a publication without revoking the
 // exact live view. Keep only its mounted dirty owner, not the whole list.
 // Host reconciliation removes protection before forbidden content can render.
 const protectedItems=committed.current.filter(item=>inlineOwnership.protectsPublication({
  sessionId:item.row.sessionId,messageId:message.id,resourceId:item.row.id,version:item.version
 }));
 const general=rows.filter(item=>!generated.has(item)||protectedItems.some(previous=>same(previous,item)));
 for(const item of protectedItems)if(!general.some(current=>same(current,item)))general.push(item);
 entries=entries.filter(entry=>!entry.item||!protectedItems.some(item=>same(item,entry.item)));
 useLayoutEffect(()=>{committed.current=general},[general]);
 if(!general.length&&!entries.length)return null;
 const fullView=async({row,version})=>{const result=await act('canvas.select',{id:row.id,version});if(result?.accepted)await act('view.update',{patch:{canvasFocused:false}})};
 const props={state,messageId:message.id,dispatch,fullView};
 return <div className="a-chat-artifacts" aria-label="Saved artifacts for this turn">
  {entries.length>0&&<ImageGallery entries={entries} {...props}/>}
  {general.map(item=><GeneralArtifact key={artifactOverrideKey(state,item,message.id)} item={item} {...props}/>)}
 </div>;
}
