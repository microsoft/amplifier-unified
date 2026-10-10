import React,{useEffect,useRef,useState} from 'react';
import {FileText,Globe,Maximize2,Image as ImageIcon,X} from 'lucide-react';
import {request} from './api';
import {chatArtifacts} from './canvas-library';
import {Renderer} from './canvas-workspace';
import {ImageActions} from './image-actions';
import {CanvasControlsHost} from './canvas-controls';
import {imageJobs,imageJobLabel} from './image-generation';
import './inline-artifacts.css';

const supported=new Set(['markdown','text','code','json','jsonl','image','html','babylon','mermaid','dot']);
const targetOf=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
const titleOf=({row,version})=>(row.app?.versions||row.versions||[]).find(v=>v.version===version)?.title||row.title;

// Body downloads start only near the viewport. Once opened, a document stays
// mounted until explicitly closed or its conversation leaves the screen.
function Preview({item,messageId,state,dispatch,fullView,onImage}){
 const {row,version}=item,root=useRef(),run=useRef(dispatch);run.current=dispatch;
 const [near,setNear]=useState(false),[closed,setClosed]=useState(false),[attempt,setAttempt]=useState(0);
 const [loaded,setLoaded]=useState(null),[error,setError]=useState('');
 useEffect(()=>{
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setNear(true);observer.disconnect()}},{rootMargin:'160px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  if(!near||closed)return;
  let active=true,owned;const controller=new AbortController();setError('');setLoaded(null);
  const release=()=>owned&&run.current('canvas.views.release',targetOf(owned)).catch(()=>{});
  run.current('canvas.views.inline',{resourceId:row.id,version,messageId,previewId:crypto.randomUUID()}).then(async result=>{
   owned=result.result;
   if(!active){release();return}
   const canvas=await request(`/api/canvas/views/${owned.viewId}/resource?`+new URLSearchParams(targetOf(owned)),{signal:controller.signal});
   if(active)setLoaded({view:owned,canvas});
  }).catch(error=>{if(active&&error.name!=='AbortError')setError(error.message)});
  return()=>{active=false;controller.abort();release()};
 },[near,closed,attempt,row.id,version,messageId]);
 const view=loaded&&(state.canvasWorkspace?.views||[]).find(view=>view.viewId===loaded.view.viewId)||loaded?.view;
 const canvas=loaded&&{...loaded.canvas,...targetOf(view),view:view.view,renderReports:view.renderReports,document:view.document,interaction:view.interaction,events:view.events};
 const close=async()=>{
  try{if(view)await dispatch('canvas.views.release',targetOf(view));setClosed(true)}catch(error){setError(error.message)}
 };
 const thumbnail=event=>{
  if(!onImage)return;
  const image=event.currentTarget,small=document.createElement('canvas'),scale=80/Math.max(image.naturalWidth,image.naturalHeight);
  small.width=Math.max(1,Math.round(image.naturalWidth*scale));small.height=Math.max(1,Math.round(image.naturalHeight*scale));
  small.getContext('2d').drawImage(image,0,0,small.width,small.height);onImage(small.toDataURL('image/png'));
 };
 return <section ref={root} className="a-inline-artifact" aria-label={titleOf(item)}>
  <div className="a-inline-artifact-heading"><strong>{titleOf(item)}</strong><span>Version {version}</span>
   <button type="button" className="a-icon" aria-label={`Open ${titleOf(item)} in full view`} onClick={()=>fullView(item)}><Maximize2/></button>
   {!closed&&<button type="button" className="a-icon" aria-label={`Close preview of ${titleOf(item)}`} onClick={close}><X/></button>}
  </div>
  {error?<div role="status" className="a-inline-artifact-notice"><p>{error}</p><button type="button" className="a-soft" onClick={()=>setAttempt(value=>value+1)}>Try preview again</button><button type="button" className="a-link" onClick={()=>fullView(item)}>Open in Canvas</button></div>:closed?<button type="button" className="a-link a-inline-artifact-notice" onClick={()=>setClosed(false)}>Show preview</button>:!canvas?<div className="a-inline-artifact-placeholder" role="status">{near?'Loading preview…':'Saved artifact'}</div>:canvas.kind==='image'?<button type="button" className="a-inline-image" aria-label={`Open ${titleOf(item)} in full view`} onClick={()=>fullView(item)}><img src={canvas.content} alt={titleOf(item)} onLoad={thumbnail} onError={()=>setError('This image could not be displayed. Your saved artifact is still available.')}/></button>:<CanvasControlsHost.Provider value={null}><div className="a-inline-artifact-body"><Renderer key={view.resourceRevision+':'+view.generation} view={view} canvas={canvas} dispatch={dispatch}/></div></CanvasControlsHost.Provider>}
  {canvas?.kind==='image'&&!closed&&<ImageActions canvas={canvas} dispatch={dispatch}/>}
 </section>;
}

function ImageGallery({items,...props}){
 const [selected,setSelected]=useState(null),[thumbnails,setThumbnails]=useState({});
 const identity=item=>item.row.id+':'+item.version,item=items.find(item=>identity(item)===selected)||items[0];
 return <div className="a-inline-gallery" aria-label={`Image gallery, ${items.length} ${items.length===1?'image':'images'}`}>
  <Preview key={identity(item)} item={item} {...props} onImage={src=>setThumbnails(old=>old[identity(item)]===src?old:Object.fromEntries([...Object.entries(old).filter(([key])=>key!==identity(item)),[identity(item),src]].slice(-12)))}/>
  {items.length>1&&<div className="a-inline-gallery-choices" role="group" aria-label="Choose an image">{items.map((entry,index)=><button type="button" key={identity(entry)} aria-label={`Image ${index+1}: ${titleOf(entry)}`} aria-pressed={identity(entry)===identity(item)} onClick={()=>setSelected(identity(entry))}>{thumbnails[identity(entry)]?<img alt="" src={thumbnails[identity(entry)]}/>:<><ImageIcon/><span>{index+1}</span></>}</button>)}</div>}
 </div>;
}

export function ArtifactLinks({state,message,act,dispatch}){
 const rows=chatArtifacts(state).flatMap(row=>(row.publications||[{messageId:row.messageId,version:row.app?.revision||row.revision||1}]).filter(link=>link.messageId===message.id).map(link=>({row,version:link.version})));
 const jobs=imageJobs(state,message.id);
 if(!rows.length&&!jobs.length)return null;
 const images=rows.filter(item=>item.row.kind==='image');
 const fullView=async({row,version})=>{const result=await act('canvas.select',{id:row.id,version});if(result?.accepted)await act('view.update',{patch:{canvasFocused:true}})};
 const props={state,messageId:message.id,dispatch,fullView};
 return <div className="a-chat-artifacts" aria-label="Saved artifacts for this turn">
  {jobs.map(job=><div key={job.id} className="a-image-generation" data-running={job.phase==='running'||undefined} role="status"><ImageIcon aria-hidden="true"/><span>{imageJobLabel(job)}</span></div>)}
  {images.length>0&&<ImageGallery items={images} {...props}/>}
  {rows.filter(item=>item.row.kind!=='image').map(item=>supported.has(item.row.kind)?<Preview key={item.row.id+':'+item.version} item={item} {...props}/>:<button type="button" key={item.row.id+':'+item.version} className="a-artifact-link" data-action="canvas.select" onClick={()=>act('canvas.select',{id:item.row.id,version:item.version})}>{item.row.kind==='browser'?<Globe/>:<FileText/>}<span>{titleOf(item)} · Version {item.version}</span><Maximize2/></button>)}
 </div>;
}
