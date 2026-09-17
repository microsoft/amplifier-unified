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
 return <section className="a-canvas-library" aria-label="Saved canvas artifacts"><h3>Saved in this chat <small>{rows.length}</small></h3><input type="search" aria-label="Filter saved artifacts" placeholder="Find artifacts · * ? patterns" value={draft.filter||''} data-action="view.update" onChange={e=>act('view.update',{patch:{canvasDraft:{...draft,filter:e.target.value}}})}/>{filtered.slice().reverse().map(r=><button key={r.id} type="button" className="a-saved-artifact" data-action="canvas.select" onClick={async()=>{await act('canvas.select',{id:r.id});act('view.update',{patch:{canvasDraft:{...draft,library:false}}})}}>{r.kind==='browser'?<Globe/>:<FileText/>}<span><strong>{r.title}</strong><small>{r.kind} · {r.kind==='browser'?'Saved address':'Saved snapshot'}</small></span><ArrowRight/></button>)}{!filtered.length&&<p>{rows.length?'No matching artifacts.':'Files and visuals you open will stay here, even after closing their tabs.'}</p>}</section>;
}
export function BrowserAddress({state,act}){
 const draft=state.view?.canvasDraft||{},[url,setUrl]=useState(draft.url||''),pending=useRef(null);
 useEffect(()=>{if(pending.current===null||draft.url===pending.current){setUrl(draft.url||'');pending.current=null}},[draft.url]);
 return <form className="a-canvas-address" onSubmit={e=>{e.preventDefault();act('canvas.show',{kind:'browser',url})}}><label htmlFor="canvas-browser-url">App or website address</label><div><input id="canvas-browser-url" type="url" required placeholder="http://localhost:3000" value={url} data-action="view.update" onChange={e=>{setUrl(e.target.value);pending.current=e.target.value;act('view.update',{patch:{canvasDraft:{...draft,url:e.target.value}}})}}/><button className="a-primary" type="submit" data-action="canvas.show" disabled={!url.trim()}><ArrowRight/>Open</button></div></form>;
}
export function BrowserPreview({canvas,act}){
 useEffect(()=>{act('canvas.report',{id:canvas.id,part:'preview',status:'pending',message:'Browser preview requested; page contents are not verified.'})},[canvas.id,canvas.view?.reload]);
 return <div className="a-browser-preview"><div className="a-canvas-toolbar"><Globe/><span className="a-browser-url" title={canvas.url}>{canvas.url}</span><button type="button" className="a-icon" aria-label="Reload browser preview" data-action="canvas.view" onClick={()=>act('canvas.view',{id:canvas.id,patch:{reload:Date.now()}})}><RotateCw/></button><a className="a-icon" data-action="canvas.openExternal" href={canvas.url} target="_blank" rel="noopener noreferrer" title="Open in browser" aria-label="Open in browser"><ExternalLink/></a></div><iframe key={`${canvas.id}-${canvas.view?.reload||0}`} title={canvas.title||'App preview'} className="a-canvas-html" sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" src={canvas.url} onLoad={()=>act('canvas.report',{id:canvas.id,part:'preview',status:'ready',message:'Frame navigation finished. The browser does not confirm whether this site permits embedding or loaded successfully.'})}/><p className="a-browser-note">Live preview · the app must stay running. Blank or not working? <a data-action="canvas.openExternal" href={canvas.url} target="_blank" rel="noopener noreferrer">Open in browser</a>. Some apps require it.</p></div>;
}
