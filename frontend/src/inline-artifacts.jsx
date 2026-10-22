import React,{useEffect,useId,useRef,useState} from 'react';
import {FileText,Globe,Maximize2,Image as ImageIcon} from 'lucide-react';
import {request} from './api';
import {chatArtifacts} from './canvas-library';
import {Renderer} from './canvas-workspace';
import {ImageActions} from './image-actions';
import {CanvasControlsHost} from './canvas-controls';
import {imageGalleryEntries,imageJobLabel} from './image-generation';
import {hasLocalCanvasEdits} from './canvas-reload-guard';
import './inline-artifacts.css';

const supported=new Set(['markdown','text','code','json','jsonl','image','html','babylon','mermaid','dot']);
const labels={markdown:'Document',text:'Text',code:'Code',json:'JSON',jsonl:'JSON lines',image:'Image',html:'HTML',babylon:'3D scene',mermaid:'Diagram',dot:'Diagram'};
const targetOf=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
const titleOf=({row,version})=>(row.app?.versions||row.versions||[]).find(v=>v.version===version)?.title||row.title;

// Body downloads start only near the viewport. Once opened, a document stays
// mounted until explicitly closed or its conversation leaves the screen.
function Preview({item,messageId,state,dispatch,fullView,imageFrame=false,presentationRequest=false}){
 const {row,version}=item,root=useRef(),run=useRef(dispatch);run.current=dispatch;
 const [near,setNear]=useState(false),[attempt,setAttempt]=useState(0);
 const [loaded,setLoaded]=useState(null),[error,setError]=useState('');
 useEffect(()=>{
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setNear(true);observer.disconnect()}},{rootMargin:'160px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  if(!near)return;
  let active=true,owned;const controller=new AbortController();setError('');setLoaded(null);
  const release=()=>owned&&run.current('canvas.views.release',targetOf(owned)).catch(()=>{});
  run.current('canvas.views.inline',{resourceId:row.id,version,messageId,previewId:crypto.randomUUID(),presentationRequest}).then(async result=>{
   owned=result.result;
   if(!active){release();return}
   const canvas=await request(`/api/canvas/views/${owned.viewId}/resource?`+new URLSearchParams(targetOf(owned)),{signal:controller.signal});
   if(active)setLoaded({view:owned,canvas});
  }).catch(error=>{if(active&&error.name!=='AbortError')setError(error.message)});
  return()=>{active=false;controller.abort();release()};
 },[near,attempt,row.id,version,messageId,presentationRequest]);
 const view=loaded&&(state.canvasWorkspace?.views||[]).find(view=>view.viewId===loaded.view.viewId)||loaded?.view;
 const canvas=loaded&&{...loaded.canvas,...targetOf(view),view:view.view,renderReports:view.renderReports,document:view.document,interaction:view.interaction,events:view.events};
 return <div ref={root} data-full-artifact-preview={row.id} data-artifact-version={version}>
  <div className={imageFrame?"a-gallery-image-frame":"a-inline-document-frame"}>{error?<div role="status" className="a-inline-artifact-notice"><p>{error}</p><button type="button" className="a-soft" onClick={()=>setAttempt(value=>value+1)}>Try preview again</button><button type="button" className="a-link" onClick={fullView}>Open in Canvas</button></div>:!canvas?<div className="a-inline-artifact-placeholder" role="status">{near?'Loading preview…':'Saved artifact'}</div>:canvas.kind==='image'?<button type="button" className="a-inline-image" data-action="canvas.select" aria-label={`Open ${titleOf(item)} in canvas`} onClick={fullView}><img src={canvas.content} alt={titleOf(item)} onError={()=>setError('This image could not be displayed. Your saved artifact is still available.')}/></button>:<CanvasControlsHost.Provider value={null}><div className="a-inline-artifact-body"><Renderer key={view.resourceRevision+':'+view.generation} view={view} canvas={canvas} dispatch={dispatch}/></div></CanvasControlsHost.Provider>}</div>
  {canvas?.kind==='image'&&<div className={imageFrame?'a-gallery-image-footer':undefined}><ImageActions canvas={canvas} dispatch={dispatch} compact={imageFrame}/></div>}
 </div>;
}

function Generation({job}){
 return <div className="a-image-generation" data-running={job.phase==='running'||undefined} role="status"><ImageIcon aria-hidden="true"/><span>{imageJobLabel(job)}</span></div>;
}

// Each visible rail item owns a short-lived view of its exact saved version.
// Loading thumbnails must not require selecting (or replacing) the main preview.
function Thumbnail({item,index,messageId,dispatch}){
 const root=useRef(),run=useRef(dispatch);run.current=dispatch;
 const [near,setNear]=useState(false),[src,setSrc]=useState(null);
 useEffect(()=>{
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setNear(true);observer.disconnect()}},{rootMargin:'80px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  if(!near)return;
  let active=true,owned,released=false;const controller=new AbortController();
  const release=()=>{if(owned&&!released){released=true;run.current('canvas.views.release',targetOf(owned)).catch(()=>{})}};
  run.current('canvas.views.inline',{resourceId:item.row.id,version:item.version,messageId,previewId:crypto.randomUUID()}).then(async result=>{
   owned=result.result;
   if(!active)return;
   const canvas=await request(`/api/canvas/views/${owned.viewId}/resource?`+new URLSearchParams(targetOf(owned)),{signal:controller.signal});
   if(active&&canvas.kind==='image')setSrc(canvas.content);
  }).catch(()=>{}).finally(release);
  return()=>{active=false;controller.abort();release()};
 },[near,item.row.id,item.version,messageId]);
 const shrink=event=>{
  const image=event.currentTarget;if(image.dataset.small)return;
  try{
   const small=document.createElement('canvas'),scale=Math.min(1,80/Math.max(image.naturalWidth,image.naturalHeight));
   small.width=Math.max(1,Math.round(image.naturalWidth*scale));small.height=Math.max(1,Math.round(image.naturalHeight*scale));
   small.getContext('2d').drawImage(image,0,0,small.width,small.height);
   image.dataset.small='true';setSrc(small.toDataURL('image/png'));
  }catch{setSrc(null)}
 };
 return <span ref={root} className="a-gallery-thumbnail">{src?<img alt="" src={src} onLoad={shrink} onError={()=>setSrc(null)}/>:<><ImageIcon/><span>{index+1}</span></>}</span>;
}

function ImageGallery({entries,...props}){
 const [selected,setSelected]=useState(null);
 const entry=entries.find(entry=>entry.id===selected)||entries[0];
 const title=entry=>entry.item?titleOf(entry.item):imageJobLabel(entry.job);
 return <div className="a-inline-gallery a-image-gallery-stable" aria-label={`Image gallery, ${entries.length} ${entries.length===1?'image':'images'}`}>
  {entry.item?<DocumentOutput key={entry.id+':'+entry.item.row.id+':'+entry.item.version} item={entry.item} {...props} imageFrame/>:<section className="a-inline-artifact" data-image-frame>
   <div className="a-inline-artifact-heading"><strong>{entry.job.operation==='edit'?'Editing image':'Generated image'}</strong></div>
   <div className="a-gallery-image-frame"><Generation job={entry.job}/></div>
   <div className="a-gallery-image-footer"/>
  </section>}
  {entries.length>1&&<div className="a-inline-gallery-choices" role="group" aria-label="Choose an image">{entries.map((choice,index)=><button type="button" key={choice.id} aria-label={`Image ${index+1}: ${title(choice)}`} aria-pressed={choice.id===entry.id} data-running={!choice.item&&choice.job.phase==='running'||undefined} onClick={()=>setSelected(choice.id)}>{choice.item?<Thumbnail key={choice.item.row.id+':'+choice.item.version} item={choice.item} index={index} messageId={props.messageId} dispatch={props.dispatch}/>:<><ImageIcon/><span>{index+1}</span></>}</button>)}</div>}
 </div>;
}

function DocumentOutput({item,state,dispatch,messageId,imageFrame=false}){
 const {row,version}=item,bodyId=useId(),card=useRef(),toggle=useRef(),inFlight=useRef(false),restoreFocus=useRef(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[reset,setReset]=useState(null);
 useEffect(()=>{
  const supersede=event=>{if(restoreFocus.current&&!card.current?.contains(event.target))restoreFocus.current.superseded=true};
  document.addEventListener('focusin',supersede);document.addEventListener('pointerdown',supersede);
  return()=>{restoreFocus.current=null;document.removeEventListener('focusin',supersede);document.removeEventListener('pointerdown',supersede)};
 },[]);
 useEffect(()=>{
  if(busy||!restoreFocus.current)return;
  const intent=restoreFocus.current;restoreFocus.current=null;
  const active=document.activeElement;
  if(intent.accepted&&!intent.superseded&&(active===document.body||card.current?.contains(active)))toggle.current?.focus({preventScroll:true});
 },[busy]);
 const choice=state.canvasWorkspace?.presentations?.find(value=>value.id===row.id&&value.version===version);
 const primary=state.canvas,primaryOwns=primary?.id===row.id&&(primary.selectedVersion??primary.revision??1)===version&&!primary._presentationDisposed;
 // Generated images retain their established inline default. Authored artifacts
 // default to a compact card; a publication is not a request to mount a viewer.
 const expanded=!primaryOwns&&(choice?choice.presentation==='inline'&&choice.messageId===messageId:row.kind==='image'&&(row.publications||[]).find(link=>link.version===version)?.messageId===messageId);
 const change=async(presentation,resetPreview=false)=>{
  if(inFlight.current)return;
  if((presentation==='canvas'||primaryOwns)&&hasLocalCanvasEdits()){setError('Save or cancel unfinished Canvas edits before changing the preview.');return}
  restoreFocus.current=presentation==='saved'&&card.current?.contains(document.activeElement)?{accepted:false,superseded:false}:null;
  inFlight.current=true;setBusy(true);setError('');setReset(null);
  try{
   const result=await dispatch('canvas.select',{id:row.id,version,messageId,sessionId:row.sessionId,presentation,...(resetPreview?{resetPreview:true}:{})});
   if(!result?.accepted)throw Error('The presentation was not accepted.');
   if(restoreFocus.current)restoreFocus.current.accepted=true;
  }catch(error){
   setError(error.message);
   if(error.code==='canvas_preview_reset_required')setReset(presentation);
  }finally{inFlight.current=false;setBusy(false)}
 };
 const Icon=row.kind==='image'?ImageIcon:row.kind==='browser'?Globe:FileText;
 return <section ref={card} className="a-document-output a-inline-artifact" data-image-frame={imageFrame||undefined} aria-label={titleOf(item)} data-artifact-id={row.id} data-artifact-version={version}>
  <button type="button" className="a-document-output-open" data-action="canvas.select" disabled={busy} aria-label={`Open ${titleOf(item)} in canvas`} onClick={()=>change('canvas')}><Icon aria-hidden="true"/><span><strong>{titleOf(item)}</strong><small>{labels[row.kind]||row.kind} · Version {version}</small></span><Maximize2 aria-hidden="true"/></button>
  {supported.has(row.kind)&&<button ref={toggle} type="button" className="a-link a-document-preview-toggle" disabled={busy} aria-expanded={expanded} aria-controls={bodyId} onClick={()=>change(expanded?'saved':'inline')}>{expanded?'Hide preview':'Preview in chat'}</button>}
  {error&&<div role="status" className="a-inline-artifact-notice"><p>{error}</p>{reset&&<div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} onClick={()=>{setReset(null);setError('');toggle.current?.focus({preventScroll:true})}}>Keep current preview</button><button type="button" className="a-soft" disabled={busy} onClick={()=>change(reset,true)}>Reset preview and {reset==='saved'?'hide':reset==='inline'?'preview in chat':'open in Canvas'}</button></div>}</div>}
  <div id={bodyId}>{expanded&&<Preview item={item} state={state} dispatch={dispatch} messageId={messageId} imageFrame={imageFrame} presentationRequest={!!choice} fullView={()=>change('canvas')}/>}</div>
 </section>;
}

export function ArtifactLinks({state,message,act,dispatch}){
 const rows=chatArtifacts(state).flatMap(row=>(row.publications||[{messageId:row.messageId,version:row.app?.revision||row.revision||1}]).filter(link=>link.messageId===message.id).map(link=>({row,version:link.version})));
 const entries=imageGalleryEntries(state,message.id,rows.filter(item=>item.row.kind==='image'));
 if(!rows.length&&!entries.length)return null;
 const props={state,messageId:message.id,dispatch};
 return <div className="a-chat-artifacts" aria-label="Saved artifacts for this turn">
  {entries.length>0&&<ImageGallery entries={entries} {...props}/>}
  {rows.filter(item=>item.row.kind!=='image').map(item=><DocumentOutput key={item.row.id+':'+item.version} item={item} {...props}/>)}
 </div>;
}
