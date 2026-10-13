import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {FileText,Globe,Code,Film,ChevronDown,ChevronUp,Maximize2,Image as ImageIcon,X} from 'lucide-react';
import {request} from './api';
import {chatArtifacts} from './canvas-library';
import {Renderer} from './canvas-workspace';
import {ImageActions} from './image-actions';
import {CanvasControlsHost} from './canvas-controls';
import {imageGalleryEntries,imageGallerySelection,imageJobLabel} from './image-generation';
import {artifactPublications,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,inlineViewAccess} from './artifact-presentation';
import {createArtifactOverrides,inlineOwnership,inlineEditNotice} from './conversation-navigation';
import './inline-artifacts.css';

const overrides=createArtifactOverrides();
const targetOf=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
const titleOf=({row,version})=>(row.app?.versions||row.versions||[]).find(v=>v.version===version)?.title||row.title;

// A gallery body/thumbnail keeps its exact admission until unmount. A revoked
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

// Each visible rail item owns a short-lived view of its exact saved version.
// Loading thumbnails must not require selecting (or replacing) the main preview.
function Thumbnail({item,index,messageId,state,dispatch}){
 const root=useRef();
 const [near,setNear]=useState(false);
 const {canvas,error,setError,replaceImageContent}=useImageView(item,messageId,state,dispatch,near);
 useEffect(()=>{
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setNear(true);observer.disconnect()}},{rootMargin:'80px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[]);
 const shrink=event=>{
  const image=event.currentTarget;if(image.dataset.small)return;
  try{
   const small=document.createElement('canvas'),scale=Math.min(1,80/Math.max(image.naturalWidth,image.naturalHeight));
   small.width=Math.max(1,Math.round(image.naturalWidth*scale));small.height=Math.max(1,Math.round(image.naturalHeight*scale));
   small.getContext('2d').drawImage(image,0,0,small.width,small.height);
   image.dataset.small='true';replaceImageContent(small.toDataURL('image/png'));
  }catch{setError('Thumbnail unavailable')}
 };
 return <span ref={root} className="a-gallery-thumbnail">{canvas?.kind==='image'&&!error?<img alt="" src={canvas.content} onLoad={shrink} onError={()=>setError('Thumbnail unavailable')}/>:<><ImageIcon/><span>{index+1}</span></>}</span>;
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
 const root=useRef(null),owned=useRef(null),run=useRef(dispatch),changing=useRef(false),opening=useRef(null),mounted=useRef(false);
 run.current=dispatch;
 const authorized=loaded&&loaded.hostInstanceId===state.client?.hostInstanceId&&loaded.clientId===(state.client?.id??state.client?.clientId)&&
  (state.canvasWorkspace?.views||[]).find(value=>value.viewId===loaded.view.viewId&&value.resourceId===loaded.view.resourceId&&value.resourceRevision===loaded.view.resourceRevision&&value.generation===loaded.view.generation);
 const view=authorized||loaded?.view;
 const revoked=!!loaded&&!authorized;
 if(owned.current&&view)owned.current.view=view;
 const release=async record=>{
  if(!record||record.released)return;
  if(inlineOwnership.blocksTarget(record.view))throw Error(inlineEditNotice);
  if(!record.pending)record.pending=run.current('canvas.views.release',targetOf(record.view)).then(receipt=>{
   if(receipt?.accepted===false)throw Error(receipt.error||'The preview could not be closed.');
   record.released=true;
   record.unregister?.();
  }).finally(()=>{record.pending=null});
  await record.pending;
 };
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{
  if(!revoked)return;
  owned.current?.unregister?.();setNotice('The host removed access to this preview. Private input could not be retained.');
 },[revoked]);
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
  let active=true,record;const controller=new AbortController();
  run.current('canvas.views.inline',{resourceId:row.id,version,messageId,previewId:crypto.randomUUID()}).then(async receipt=>{
   if(receipt?.accepted===false)throw Error(receipt.error||'Inline preview was not accepted.');
   record={view:receipt.result,released:false};owned.current=record;
   if(!active){await release(record);return}
   const canvas=await request(`/api/canvas/views/${record.view.viewId}/resource?`+new URLSearchParams(targetOf(record.view)),{signal:controller.signal});
   if(active){
    record.unregister=inlineOwnership.register(state,record.view,{sessionId:row.sessionId,messageId,version});
    setLoaded({view:record.view,canvas,hostInstanceId:state.client?.hostInstanceId,clientId:state.client?.id??state.client?.clientId});
   }
  }).catch(error=>{
   if(active&&error.name!=='AbortError'){
    // Capacity/resource refusal leaves a useful compact handoff, not an empty
    // inline body. No other mounted view is evicted and no override is erased.
    setRefusal(error.message);setNotice(error.message);
   }
  });
  return()=>{active=false;controller.abort();record?.unregister?.();void release(record).catch(()=>{})};
 // Capability refreshes do not tear down a live editor. A newly available
 // capability can be tried explicitly; publication/mode changes use release.
 },[near,mode,refusal,attempt,row.id,version,messageId]);
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
 const canvas=loaded&&!revoked&&{...loaded.canvas,...targetOf(view),view:view.view,renderReports:view.renderReports,document:view.document,interaction:view.interaction,events:view.events};
 return <section ref={root} className="a-general-artifact a-inline-artifact" data-artifact-id={row.id} data-artifact-version={version} data-chat-mode={inline?'inline':'compact'} aria-label={titleOf(item)}>
  <div className="a-general-artifact-heading">
   <Icon aria-hidden="true"/><span className="a-general-artifact-title"><strong>{titleOf(item)}</strong><small>{row.kind} · Version {version}</small></span>
   <button type="button" className="a-soft" aria-label={`Open ${titleOf(item)} in Canvas`} data-action={canvasOpenCommand(item,messageId,globalThis.window?.amplifier?.getActions?.()||[]).action} disabled={busy||!caps.canvas} aria-busy={busy||undefined} onClick={open}><Maximize2 aria-hidden="true"/><span>Open in Canvas</span></button>
   {inline?<button type="button" className="a-soft" aria-label={`Collapse ${titleOf(item)}`} disabled={busy} aria-expanded={true} onClick={()=>changeMode('compact',true)}><ChevronUp aria-hidden="true"/>Collapse</button>:caps.inline&&<button type="button" className="a-soft" aria-label={`Expand ${titleOf(item)} in chat`} disabled={busy} aria-expanded={false} onClick={()=>changeMode('inline',true)}><ChevronDown aria-hidden="true"/>{refusal?'Retry preview':'Expand'}</button>}
  </div>
  {(notice||!caps.inline&&caps.reason||!caps.canvas)&&<p className="a-inline-artifact-notice" role="status">{notice||(!caps.canvas?'Canvas is unavailable for this artifact.':caps.reason)}</p>}
  {inline&&<div className="a-general-artifact-body" data-kind={row.kind}>
   {revoked?<p role="status">Preview access was removed by the host.</p>:canvas?<CanvasControlsHost.Provider value={undefined}><Renderer key={view.viewId+':'+view.resourceRevision+':'+view.generation} view={view} canvas={canvas} dispatch={dispatch}/></CanvasControlsHost.Provider>:<div className="a-inline-artifact-placeholder" role="status">{near?'Loading preview…':'Saved artifact'}</div>}
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
