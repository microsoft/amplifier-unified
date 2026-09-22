import {CanvasControl} from './canvas-controls';
import {clientUrl} from './api';
import React,{useEffect,useRef,useState} from 'react';
import {AppBridge,PostMessageTransport} from '@modelcontextprotocol/ext-apps/app-bridge';
import {AlertCircle,Check,Loader,RefreshCw} from 'lucide-react';
import {request} from './api';
import {useMcpAppTheme} from './mcp-app-theme';
import {MCP_VISIBILITY,useMcpAppVisibility} from './mcp-app-lifecycle';
import {createMcpReadGate} from './mcp-app-reads';

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

function useHostTheme(scheme){
 const media=()=>window.matchMedia('(prefers-color-scheme: dark)');
 const [systemTheme,setSystemTheme]=useState(()=>media().matches?'dark':'light');
 useEffect(()=>{
  if(scheme!=='system')return;
  const query=media(),change=event=>setSystemTheme(event.matches?'dark':'light');
  setSystemTheme(query.matches?'dark':'light');
  query.addEventListener('change',change);
  return()=>query.removeEventListener('change',change);
 },[scheme]);
 return scheme==='dark'||scheme==='light'?scheme:systemTheme;
}

export function McpAppViewer({canvas,act}){
 const frame=useRef(null),current=useRef(canvas),bridgeRef=useRef(null),hostContext=useRef(null),themeRef=useRef(),[status,setStatus]=useState({phase:'loading',text:'Connecting tool view…'});
 const theme=useHostTheme(useMcpAppTheme());
 const visible=useMcpAppVisibility(canvas.open!==false&&!canvas.visibilityPending),visibleRef=useRef(visible);
 visibleRef.current=visible;
 current.current=canvas;
 themeRef.current=theme;
 useEffect(()=>{
  const bridge=bridgeRef.current,previous=hostContext.current;
  if(!bridge||!previous||previous.theme===theme&&previous[MCP_VISIBILITY]===visible)return;
  const next={...previous,theme,[MCP_VISIBILITY]:visible};
  hostContext.current=next;
  bridge.setHostContext(next);
 },[theme,visible]);
 useEffect(()=>{
  const controller=new AbortController();let bridge,live=true,initialized=false,lastReport='';
  const reads=createMcpReadGate({isVisible:()=>visibleRef.current});
  let catalog;
  const listTools=()=>catalog||(catalog=request(`/api/canvas/${canvas.id}/tools`,{signal:controller.signal}).catch(error=>{catalog=null;throw error}));
  const report=(phase,text)=>{if(!live||lastReport===phase+text)return;lastReport=phase+text;setStatus({phase,text});act('canvas.report',{id:canvas.id,part:'mcp-app',status:phase==='ready'?'ready':phase==='error'?'error':'pending',message:text})};
  const start=async()=>{
   hostContext.current={theme:themeRef.current,[MCP_VISIBILITY]:visibleRef.current,displayMode:'inline',availableDisplayModes:['inline'],locale:navigator.language};
   bridge=new AppBridge(null,{name:'Amplifier Unified',version:'0.6.0'},
    {serverTools:{},serverResources:{},updateModelContext:{text:{},structuredContent:{}},sandbox:{permissions:{},csp:{connectDomains:[],resourceDomains:[],frameDomains:['blob:'],baseUriDomains:[]}}},
    {hostContext:hostContext.current});
   bridge.oncalltool=async params=>{
    try{
     // Metadata is an optimization hint, not a new admission dependency. The
     // server still checks grants and schemas when an unknown call is sent.
     const tools=await listTools().catch(error=>{if(controller.signal.aborted)throw error;return {tools:[]}});
     const definition=tools.tools?.find(tool=>tool.name===params.name);
     const run=()=>callTool(canvas.id,params,controller.signal);
     const readOnly=definition?.annotations?.readOnlyHint===true&&definition?.annotations?.destructiveHint!==true;
     const result=await (readOnly?reads.run(params,run):run());
     // The tool owns its progress UI. Routine calls (including typing) must
     // not toggle host controls or publish a render report on every batch.
     if(result?.isError)report('error','The tool reported an error. See its result below.');
     else report('ready','Tool view connected');
     return result;
    }catch(error){if(!error.backgroundReadPaused)report('error',error.message);throw error}
   };
   bridge.onlisttools=listTools;
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
   if(live){
    bridgeRef.current=bridge;
    const next={...hostContext.current,theme:themeRef.current,[MCP_VISIBILITY]:visibleRef.current};
    hostContext.current=next;
    bridge.setHostContext(next);
    frame.current.src=clientUrl(`/api/canvas/${canvas.id}/document`);
   }
  };
  report('loading','Connecting tool view…');start().catch(error=>report('error',error.message));
  const timeout=setTimeout(()=>{if(live&&!initialized)setStatus(s=>s.phase==='loading'?{phase:'error',text:'The tool view has not connected. Check that this server supplies a self-contained MCP App.'}:s)},15000);
  return()=>{live=false;clearTimeout(timeout);reads.close();controller.abort();if(bridgeRef.current===bridge)bridgeRef.current=null;hostContext.current=null;bridge?.close().catch(()=>{})};
 },[canvas.id,canvas.view?.reload]);
 return <div className="a-mcp-app-viewer" style={{display:'flex',flexDirection:'column',height:'100%',minHeight:0}}>
  <CanvasControl inline={status.phase==='error'}><div data-phase={status.phase} className={`a-mcp-status a-canvas-result ${status.phase==='error'?'error':status.phase==='ready'?'success':''}`} role="status">
   {status.phase==='error'?<AlertCircle/>:status.phase==='ready'?<Check/>:<Loader className="a-progress-spinner"/>}<span>{status.text}</span>
   <button type="button" className="a-icon" aria-label="Reconnect tool server" data-action="smartTools.connect" onClick={async()=>{try{await command('smartTools.connect',{id:canvas.mcp.serverId});await act('canvas.view',{id:canvas.id,patch:{reload:Date.now()}})}catch(error){setStatus({phase:'error',text:error.message})}}}><RefreshCw/></button>
  </div></CanvasControl>
  {canvas.sharedToolView&&<p className="a-caption">This view shares tool work with the original conversation.</p>}
  <iframe ref={frame} title={canvas.title||'Interactive tool'} className="a-canvas-html" style={{flex:1,minHeight:0}} sandbox="allow-scripts allow-downloads" referrerPolicy="no-referrer"/>
 </div>;
}
