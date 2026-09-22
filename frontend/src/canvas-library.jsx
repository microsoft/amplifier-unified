import {CanvasControl} from './canvas-controls';
import React,{useEffect,useRef,useState} from 'react';
import {FileText,Globe,X,ExternalLink,RotateCw,ArrowRight} from 'lucide-react';
import {filterList} from './list-filter';
const inChat=(state,row)=>row.sessionId===state.selectedSessionId&&row.workspaceId===state.selectedWorkspaceId;
export function chatArtifacts(state){return (state.canvasArtifacts||[]).filter(r=>inChat(state,r))}
export function ArtifactLinks({state,message,act}){
 const rows=chatArtifacts(state).filter(r=>r.messageId===message.id);
 return rows.length?<div className="a-chat-artifacts" aria-label="Saved artifacts for this turn">{rows.map(row=><button type="button" key={row.id} className="a-artifact-link" data-action="canvas.select" onClick={()=>act('canvas.select',{id:row.id})}>{row.kind==='browser'?<Globe/>:<FileText/>}<span>{row.title}</span><ArrowRight/></button>)}</div>:null;
}
export function CanvasTabs({state,act}){
 const rows=chatArtifacts(state).filter(r=>r.tabOpen);
 return rows.length?<div className="a-canvas-tabs" role="tablist" aria-label="Open canvas items">{rows.map(r=><div className={`a-canvas-tab ${r.id===state.canvas?.id?'is-active':''}`} key={r.id}><button type="button" role="tab" aria-selected={r.id===state.canvas?.id} data-action="canvas.select" onClick={()=>act('canvas.select',{id:r.id})}>{r.kind==='browser'?<Globe/>:<FileText/>}<span>{r.title}</span></button><button type="button" className="a-icon" aria-label={`Close tab ${r.title}`} data-action="canvas.tabClose" onClick={()=>act('canvas.tabClose',{id:r.id})}><X/></button></div>)}</div>:null;
}
export function SavedArtifacts({state,act}){
 const draft=state.view?.canvasDraft||{},rows=chatArtifacts(state),filtered=filterList(rows,draft.filter||'',r=>[r.title,r.kind,r.path||'',r.url||'']);
 return <section className="a-canvas-library" aria-label="Saved canvas artifacts"><h3>Saved in this chat <small>{rows.length}</small></h3><input type="search" aria-label="Filter saved artifacts" placeholder="Find artifacts · * ? patterns" value={draft.filter||''} data-action="view.update" onChange={e=>act('view.update',{patch:{canvasDraft:{...draft,filter:e.target.value}}})}/>{filtered.slice().reverse().map(r=><button key={r.id} type="button" className="a-saved-artifact" data-action="canvas.select" onClick={async()=>{await act('canvas.select',{id:r.id});act('view.update',{patch:{canvasDraft:{...draft,library:false}}})}}>{r.kind==='browser'?<Globe/>:<FileText/>}<span><strong>{r.title}</strong><small>{r.kind} · {r.kind==='browser'?'Saved address':r.app?'Interactive surface':'Saved snapshot'}</small></span><ArrowRight/></button>)}{!filtered.length&&<p>{rows.length?'No matching artifacts.':'Files and visuals you open will stay here, even after closing their tabs.'}</p>}</section>;
}
export function BrowserAddress({state,act}){
 const draft=state.view?.canvasDraft||{},[url,setUrl]=useState(draft.url||''),pending=useRef(null);
 useEffect(()=>{if(pending.current===null||draft.url===pending.current){setUrl(draft.url||'');pending.current=null}},[draft.url]);
 return <form className="a-canvas-address" onSubmit={e=>{e.preventDefault();act('canvas.show',{kind:'browser',url})}}><label htmlFor="canvas-browser-url">App or website address</label><div><input id="canvas-browser-url" type="url" required placeholder="http://localhost:3000" value={url} data-action="view.update" onChange={e=>{setUrl(e.target.value);pending.current=e.target.value;act('view.update',{patch:{canvasDraft:{...draft,url:e.target.value}}})}}/><button className="a-primary" type="submit" data-action="canvas.show" disabled={!url.trim()}><ArrowRight/>Open</button></div></form>;
}
export function browserPreviewPolicy(url,hostUrl){
 const target=new URL(url),host=new URL(hostUrl);
 const loopback=name=>name==='localhost'||name.endsWith('.localhost')||name==='[::1]'||/^127\.\d+\.\d+\.\d+$/.test(name);
 const blocked=host.protocol==='https:'&&target.protocol==='http:'&&!loopback(target.hostname);
 const localWarning=loopback(target.hostname)&&!loopback(host.hostname);
 return {blocked,localWarning,message:blocked?'This HTTP page cannot be embedded inside the HTTPS app. Open it in your browser, or serve the page over HTTPS.':localWarning?'This address points to the device viewing the app. For a service running on the Amplifier host, use that host’s LAN or Tailnet address.':'Some sites block embedding or require a separate browser tab. The app cannot verify the contents of this external frame.'};
}
export function BrowserPreview({canvas,act}){
 const policy=browserPreviewPolicy(canvas.url,typeof location==='undefined'?'http://localhost':location.href),action=useRef(act);action.current=act;
 useEffect(()=>{
  const report=(status,message)=>action.current('canvas.report',{id:canvas.id,part:'preview',status,message});
  if(policy.blocked){report('error',policy.message);return}
  report('pending','Opening external preview; page contents cannot be verified.');
  const timer=setTimeout(()=>report('unverified','Preview could not be verified. If it is blank, open it in your browser or check the address and site embedding policy.'),12000);
  return()=>clearTimeout(timer);
 },[canvas.id,canvas.view?.reload,policy.blocked,policy.message]);
 return <div className="a-browser-preview"><CanvasControl><div className="a-canvas-toolbar"><Globe/><span className="a-browser-url" title={canvas.url}>{canvas.url}</span><button type="button" className="a-icon" aria-label="Reload browser preview" data-action="canvas.view" onClick={()=>act('canvas.view',{id:canvas.id,patch:{reload:Date.now()}})}><RotateCw/></button></div></CanvasControl>
 {policy.blocked?<div className="a-browser-blocked" role="alert"><Globe/><h3>Open this page in your browser</h3><p>{policy.message}</p><a className="a-soft" data-action="canvas.openExternal" href={canvas.url} target="_blank" rel="noopener noreferrer">Open in browser <ExternalLink size={16}/></a></div>:<iframe key={`${canvas.id}-${canvas.view?.reload||0}`} title={canvas.title||'App preview'} className="a-canvas-html" sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" src={canvas.url} onLoad={()=>act('canvas.report',{id:canvas.id,part:'preview',status:'unverified',message:'Frame navigation finished. Browser security prevents confirming whether this external page loaded or permits embedding.'})}/>}
 <div className="a-browser-help"><a data-action="canvas.openExternal" href={canvas.url} target="_blank" rel="noopener noreferrer">Open in browser <ExternalLink size={13}/></a><button className="a-link" type="button" data-action="canvas.view" aria-expanded={!!canvas.view?.help} onClick={()=>act('canvas.view',{id:canvas.id,patch:{help:!canvas.view?.help}})}>Preview help</button>{(canvas.view?.help||policy.localWarning)&&<p>{policy.message} The service must stay running and be reachable from this device. Authentication, frame policies, redirects, and sandbox restrictions can require opening it separately.</p>}</div></div>;
}
