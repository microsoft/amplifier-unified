import React,{useEffect,useMemo,useRef,useState} from 'react';
import {PanelsTopLeft,X} from 'lucide-react';
import {request} from './api';
import {CanvasAppViewer} from './canvas-app-viewer';
import {CanvasViewer} from './canvas-viewer';
import {BrowserPreview} from './canvas-library';
import {McpAppViewer} from './mcp-app-viewer';
import {A2UISurface} from './a2ui';
import './canvas-workspace.css';

const Surface=({canvas,act})=><A2UISurface surface={canvas.surface} act={act}/>;
// Every built-in format and every contributed renderer enters the same host.
const builtins=Object.fromEntries(['markdown','text','code','json','jsonl','image','html','babylon','mermaid','dot'].map(kind=>['builtin.canvas.'+kind,CanvasViewer]));
Object.assign(builtins,{'builtin.canvas.browser':BrowserPreview,'builtin.canvas.mcp-app':McpAppViewer,'builtin.canvas.a2ui':Surface});
const targetOf=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
const factoryCache=new Map();
async function loadRenderer(url){
 if(!factoryCache.has(url))factoryCache.set(url,import(/* @vite-ignore */ url).then(module=>{
  const Component=module.default({React});if(typeof Component!=='function')throw Error('The renderer did not export a component.');return Component;
 }).catch(error=>{factoryCache.delete(url);throw error}));
 return factoryCache.get(url);
}

class RendererBoundary extends React.Component{
 state={error:null};
 static getDerivedStateFromError(error){return {error}}
 componentDidCatch(error){this.props.onError(error.message)}
 render(){return this.state.error?this.props.fallback:this.props.children}
}
function Mounted({report}){useEffect(()=>{report('ready','Renderer mounted')},[report]);return null}

function Renderer({view,canvas,dispatch,recovery}){
 const targetKey=JSON.stringify(targetOf(view));
 const target=useMemo(()=>JSON.parse(targetKey),[targetKey]);
 const listeners=useRef(new Set()),snapshot=useMemo(()=>({viewId:view.viewId,resource:{id:canvas.id,kind:canvas.kind,title:canvas.title,content:canvas.content,surface:canvas.surface,url:canvas.url,path:canvas.path,revision:view.resourceRevision},view:canvas.view||{}}),[canvas,view.viewId,view.resourceRevision]);
 const latest=useRef(snapshot);latest.current=snapshot;
 useEffect(()=>{listeners.current.forEach(listener=>listener())},[snapshot]);
 const host=useMemo(()=>Object.freeze({apiVersion:'1.0',instanceId:target.viewId,viewId:target.viewId,
  getSnapshot:()=>latest.current,subscribe:listener=>{listeners.current.add(listener);return()=>listeners.current.delete(listener)},
  readSource:()=>request(`/api/canvas/views/${target.viewId}/source?`+new URLSearchParams(target)).then(result=>result.content),
  dispatch:(action,args={})=>{
   const mapped={'view.update':'canvas.view','view.report':'canvas.report'}[action];
   if(!mapped)return Promise.reject(Error('Unsupported renderer capability.'));
   return dispatch('canvas.views.command',{...target,action:mapped,args});
  },setDirty:dirty=>dispatch('canvas.views.dirty',{...target,dirty:!!dirty})
 }),[target,dispatch]);
 const report=useMemo(()=> (status,message)=>{dispatch('canvas.views.status',{...target,status,message:String(message).slice(0,2000)}).catch(()=>{})},[target,dispatch]);
 const bypassed=recovery&&!view.renderer.startsWith('builtin.');
 const reportMounted=useMemo(()=>(status,message)=>report(bypassed||!view.available?'error':status,bypassed?'Recovery mode is displaying the standard viewer.':!view.available?'The saved renderer is unavailable; displaying the standard viewer.':message),[report,bypassed,view.available]);
 const act=useMemo(()=> (action,args)=>dispatch('canvas.views.command',{...target,action,args}).catch(()=>{}),[target,dispatch]);
 const builtin='builtin.canvas.'+canvas.kind,Default=builtins[builtin];
 const choice=view.choices.find(choice=>choice.id===view.renderer);
 const selected=recovery||!choice?builtin:view.renderer;
 const [external,setExternal]=useState(null),[error,setError]=useState('');
 useEffect(()=>{
  let current=true;setExternal(null);setError('');
  if(builtins[selected])return;
  report('loading','Loading renderer');
  loadRenderer(choice.url).then(Component=>{if(current)setExternal(()=>Component)}).catch(error=>{if(current){setError(error.message);report('error',error.message)}});
  return()=>{current=false};
 },[selected,choice?.url,report]);
 const fail=message=>{setError(message);report('error',message)};
 const fallback=Default?<Default canvas={canvas} act={act}/>:<p role="alert">No standard viewer supports this artifact.</p>;
 const Builtin=builtins[selected],Component=external;
 return <>
  {(error||!choice||recovery)&&<div role="status" className="a-renderer-notice">{recovery?'Recovery mode uses standard viewers.':error?`This renderer needs attention: ${error}`:'The saved renderer is unavailable. Showing the standard viewer; your artifact is retained.'}<button type="button" className="a-link" onClick={()=>dispatch('canvas.views.recover',target)}>Use standard viewer</button></div>}
  {Builtin?<><Builtin canvas={canvas} act={act}/><Mounted report={reportMounted}/></>:Component?<RendererBoundary onError={fail} fallback={fallback}><Component host={host}/><Mounted report={reportMounted}/></RendererBoundary>:error?fallback:<p role="status">Loading viewer…</p>}
 </>;
}

function ResourceView({view,state,dispatch,recovery}){
 const [loaded,setLoaded]=useState(null),[error,setError]=useState('');
 const targetKey=JSON.stringify(targetOf(view));
 const isPrimary=view.viewId==='primary';
 useEffect(()=>{
  if(isPrimary||view.error)return;
  const controller=new AbortController();setLoaded(null);setError('');
  request(`/api/canvas/views/${view.viewId}/resource?`+new URLSearchParams(targetOf(view)),{signal:controller.signal}).then(setLoaded).catch(error=>{if(error.name!=='AbortError')setError(error.message)});
  return()=>controller.abort();
 },[targetKey,isPrimary,view.error]);
 const canvas=useMemo(()=>{
  const source=isPrimary?state.canvas:loaded;
  return source?{...source,...(view.app?{app:view.app}:{}),...targetOf(view),viewId:view.viewId,view:view.view||source.view,renderReports:view.renderReports,...(!isPrimary?{document:view.document,interaction:view.interaction,events:view.events}:{})}:null;
 },[isPrimary,state.canvas,loaded,view]);
 const run=async(action,args)=>{try{const result=await dispatch(action,args);setError(result?.result?.status==='deferred'?result.result.reason:'')}catch(error){setError(error.message)}};
 if(view.error)return <section className="a-resource-view" role="alert">{view.error}<button type="button" onClick={()=>run('canvas.views.close',targetOf(view))}>Close unavailable view</button></section>;
 return <section className="a-resource-view" data-canvas-view={view.viewId} aria-label={isPrimary?'Primary artifact view':'Secondary artifact view'}>
  <div className="a-resource-view-controls a-canvas-toolbar">
   {!isPrimary&&<strong title={view.resource.title}>{view.resource.title}</strong>}
   <label>{isPrimary?'Open with':'Secondary viewer'}<select aria-label={isPrimary?'Open with':'Open secondary with'} value={view.renderer} onFocus={()=>dispatch('canvas.views.inspect',{}).catch(()=>{})} onChange={event=>run('canvas.views.renderer',{...targetOf(view),renderer:event.target.value})}>
    {!view.available&&<option value={view.renderer}>Unavailable renderer</option>}
    {view.choices.map(choice=><option key={choice.id} value={choice.id}>{choice.label}</option>)}
   </select></label>
   {isPrimary&&view.resource.kind!=='mcp-app'&&<button type="button" className="a-icon" aria-label="Open a second view" onClick={()=>run('canvas.views.open',{resourceId:view.resourceId,sessionId:view.resource.sessionId??null})}><PanelsTopLeft/></button>}
   {!isPrimary&&<button type="button" className="a-icon" aria-label="Close secondary view" onClick={()=>run('canvas.views.close',targetOf(view))}><X/></button>}
  </div>
  {error&&<p role="alert" className="a-renderer-notice">{error}</p>}
  <div className="a-resource-renderer">{canvas?.app?<CanvasAppViewer key={view.resourceId} canvas={canvas} dispatch={dispatch}/>:canvas?<Renderer key={view.resourceId+':'+view.resourceRevision+':'+view.generation} view={view} canvas={canvas} dispatch={dispatch} recovery={recovery}/>:<p role="status">Loading saved artifact…</p>}</div>
 </section>;
}

export function CanvasWorkspace({state,dispatch,hidden=false}){
 const views=state.canvasWorkspace?.views||[];
 const recovery=typeof location!=='undefined'&&new URLSearchParams(location.search).get('shell')==='recovery';
 // Passive Library navigation must not destroy renderer-local edits or frames.
 return <div className="a-canvas-workspace" hidden={hidden} inert={hidden} data-split={views.length>1}>{views.map(view=><ResourceView key={view.viewId} view={view} state={state} dispatch={dispatch} recovery={recovery}/>)}</div>;
}
