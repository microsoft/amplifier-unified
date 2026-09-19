import React,{useEffect,useRef,useState} from 'react';
import {AppBridge,PostMessageTransport} from '@modelcontextprotocol/ext-apps/app-bridge';
import {AlertCircle,Check,Loader,RefreshCw} from 'lucide-react';
import {request} from './api';

// The iframe can call tools only through its saved server binding. No host
// credentials, app_control handle, or server selector crosses postMessage.
async function command(action,args,signal){
 const accepted=await request('/api/actions',{method:'POST',body:{id:crypto.randomUUID(),action,args},signal});
 if(!accepted.operationId)return accepted;
 const deadline=Date.now()+310000;
 while(Date.now()<deadline){
  if(signal?.aborted)throw new Error('This view closed. Tool work has not been cancelled.');
  const op=await request(`/api/smart-tools/operations/${encodeURIComponent(accepted.operationId)}`,{signal});
  if(op.status==='completed')return op.result;
  if(op.status==='failed'&&op.result?.isError)return op.result;
  if(['failed','interrupted'].includes(op.status))throw new Error(op.error||'Tool work was interrupted; it was not replayed.');
  await new Promise(resolve=>setTimeout(resolve,350));
 }
 throw new Error('No completion received. Inspect Smart Tools activity before retrying.');
}

async function callTool(canvasId,params,signal){
 const id=crypto.randomUUID(),deadline=Date.now()+310000;
 let op=await request(`/api/canvas/${encodeURIComponent(canvasId)}/tools/call`,{method:'POST',body:{id,name:params.name,arguments:params.arguments||{}},signal});
 // A long-running call may outlive the HTTP wait. Inspect the same receipt;
 // never retry the mutation if the connection itself was lost.
 while(['pending','running','queued'].includes(op.status)){
  if(signal?.aborted||Date.now()>=deadline)throw new Error('No completion received. Inspect Smart Tools activity before retrying; input was not replayed.');
  await new Promise(resolve=>setTimeout(resolve,350));
  op=await request(`/api/smart-tools/operations/${encodeURIComponent(id)}`,{signal});
 }
 if(op.status==='completed'||op.result?.isError)return op.result;
 throw new Error(op.error||'Tool work was interrupted; it was not replayed.');
}

export function McpAppViewer({canvas,act}){
 const frame=useRef(null),current=useRef(canvas),[status,setStatus]=useState({phase:'loading',text:'Connecting tool view…'});
 current.current=canvas;
 useEffect(()=>{
  const controller=new AbortController();let bridge,live=true,initialized=false,lastReport='';
  const report=(phase,text)=>{if(!live||lastReport===phase+text)return;lastReport=phase+text;setStatus({phase,text});act('canvas.report',{id:canvas.id,part:'mcp-app',status:phase==='ready'?'ready':phase==='error'?'error':'pending',message:text})};
  const start=async()=>{
   bridge=new AppBridge(null,{name:'Amplifier Unified',version:'0.6.0'},
    {serverTools:{},serverResources:{},updateModelContext:{text:{},structuredContent:{}},sandbox:{permissions:{},csp:{connectDomains:[],resourceDomains:[],frameDomains:['blob:'],baseUriDomains:[]}}},
    {hostContext:{theme:document.querySelector('#amp-one')?.dataset.scheme==='dark'?'dark':'light',displayMode:'inline',availableDisplayModes:['inline'],locale:navigator.language}});
   bridge.oncalltool=async params=>{
    try{
     const result=await callTool(canvas.id,params,controller.signal);
     // The tool owns its progress UI. Routine calls (including typing) must
     // not toggle host controls or publish a render report on every batch.
     if(result?.isError)report('error','The tool reported an error. See its result below.');
     else report('ready','Tool view connected');
     return result;
    }catch(error){report('error',error.message);throw error}
   };
   bridge.onlisttools=()=>request(`/api/canvas/${canvas.id}/tools`,{signal:controller.signal});
   const resource=(kind,params={})=>request(`/api/canvas/${canvas.id}/resources?${new URLSearchParams({kind,...params})}`,{signal:controller.signal});
   bridge.onreadresource=params=>resource('read',{uri:params.uri});
   bridge.onlistresources=params=>resource('list',params?.cursor?{cursor:params.cursor}:{});
   bridge.onlistresourcetemplates=params=>resource('templates',params?.cursor?{cursor:params.cursor}:{});
   bridge.onupdatemodelcontext=async context=>{
    await command('smartTools.context',{canvasId:canvas.id,context},controller.signal);return {};
   };
   bridge.oninitialized=async()=>{
    initialized=true;
    try{
     await bridge.sendToolInput({arguments:current.current.mcp?.toolArguments||{}});
     const operationId=current.current.mcp?.operationId;
     const result=operationId?(await request(`/api/smart-tools/operations/${encodeURIComponent(operationId)}`,{signal:controller.signal})).result:current.current.mcp?.result;
     if(result)await bridge.sendToolResult(result);
     report('ready','Tool view connected');
    }catch(error){report('error',error.message)}
   };
   bridge.onerror=error=>report('error',error.message);
   await bridge.connect(new PostMessageTransport(frame.current.contentWindow,frame.current.contentWindow));
   // Connect before navigation so even a fast inline App.initialize is heard.
   if(live)frame.current.src=`/api/canvas/${canvas.id}/document`;
  };
  report('loading','Connecting tool view…');start().catch(error=>report('error',error.message));
  const timeout=setTimeout(()=>{if(live&&!initialized)setStatus(s=>s.phase==='loading'?{phase:'error',text:'The tool view has not connected. Check that this server supplies a self-contained MCP App.'}:s)},15000);
  return()=>{live=false;clearTimeout(timeout);controller.abort();bridge?.close().catch(()=>{})};
 },[canvas.id,canvas.view?.reload]);
 return <div className="a-mcp-app-viewer" style={{display:'flex',flexDirection:'column',height:'100%',minHeight:0}}>
  <div data-phase={status.phase} className={`a-mcp-status a-canvas-result ${status.phase==='error'?'error':status.phase==='ready'?'success':''}`} role="status">
   {status.phase==='error'?<AlertCircle/>:status.phase==='ready'?<Check/>:<Loader className="a-progress-spinner"/>}<span>{status.text}</span>
   <button type="button" className="a-icon" aria-label="Reconnect tool server" data-action="smartTools.connect" onClick={async()=>{try{await command('smartTools.connect',{id:canvas.mcp.serverId});await act('canvas.view',{id:canvas.id,patch:{reload:Date.now()}})}catch(error){setStatus({phase:'error',text:error.message})}}}><RefreshCw/></button>
  </div>
  {canvas.sharedToolView&&<p className="a-caption">This view shares tool work with the original conversation.</p>}
  <iframe ref={frame} title={canvas.title||'Interactive tool'} className="a-canvas-html" style={{flex:1,minHeight:0}} sandbox="allow-scripts allow-downloads" referrerPolicy="no-referrer"/>
 </div>;
}
