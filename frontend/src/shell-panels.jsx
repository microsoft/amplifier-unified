import {NavigationOpen,useModalFocus} from './responsive-navigation';
import {CanvasControlsHost} from './canvas-controls';
import {CanvasWorkspace} from './canvas-workspace';
import {McpAppVisibilityProvider} from './mcp-app-lifecycle';
import {ShellModules,ShellSlot} from './shell/runtime';
export {ChatRename} from './shell/navigation-components';
import {CanvasTabs,SavedArtifacts,BrowserAddress,chatArtifacts} from './canvas-library';
import React,{useEffect,useRef,useState} from 'react';
import {FolderOpen,FolderPlus,MessageCircle,Plus,PanelLeft,PanelRight,Search,Pencil,Trash2,X,Check,FileText,ChevronRight,Library,Globe,Maximize2,Minimize2,ArrowLeft,Pin,RefreshCw,LoaderCircle,AlertCircle} from 'lucide-react';
import {PaneResizer,usePanelLayout} from './panel-layout';
import {chatPage,visibleWorkspaces} from './chat-navigation';
import {WorkspaceExplorer} from './workspace-explorer';
import {AttentionBadge} from './attention';
import {PathField} from './settings-ui';

const patch=(act,value)=>act('view.update',{patch:value});
export function reopenCanvas(state,act){
 return act('canvas.visibility',{open:true});
}
export function CanvasToggle({state,act,layout}){
 const Icon=(layout||state.view?.layout)==='work'?PanelLeft:PanelRight;
 return <button type="button" className="a-icon a-canvas-toggle" disabled={!state.selectedSessionId} title={!state.selectedSessionId?'Send a message to start a chat before opening Canvas':undefined} aria-label={state.canvas?.open?'Close canvas':'Open canvas'} aria-pressed={!!state.canvas?.open} aria-controls="workspace-canvas" data-action="canvas.visibility" onClick={()=>act('canvas.visibility',{open:!state.canvas?.open})}><Icon/></button>;
}
export function WorkspaceRail({state,session,act,selectSession,newSession,shell}){
 const layout=usePanelLayout(state,act);
 const view=state.view||{},pinned=!layout.narrow&&!!view.navPinned,expanded=(pinned||!!view.navExpanded)&&(!layout.narrow||!view.canvasFocused&&!view.panel&&!view.toolbarMenuOpen),draft=view.workspaceDraft||{};
 const nav=useRef(null),wasNarrow=useRef(layout.narrow),close=()=>patch(act,{navExpanded:false});
 useEffect(()=>{if(layout.narrow&&!wasNarrow.current&&view.navExpanded)close();wasNarrow.current=layout.narrow},[layout.narrow]);
 useModalFocus(nav,layout.narrow&&expanded,close);
 const workspaces=visibleWorkspaces(state),workspace=workspaces.find(w=>w.id===state.selectedWorkspaceId);
 const expand=value=>{if(!pinned&&!!view.navExpanded!==value)patch(act,{navExpanded:value})};
 const add=()=>{(newSession||(()=>act('session.draft',{})))();if(!pinned)patch(act,{navExpanded:false})};
 return <NavigationOpen.Provider value={expanded}>{layout.narrow&&expanded&&<div className="a-navigation-scrim" onClick={close}/>}
 <aside ref={nav} id="workspace-navigation" role={layout.narrow?'dialog':undefined} aria-modal={layout.narrow&&expanded||undefined} hidden={layout.narrow&&!expanded} inert={layout.narrow&&!expanded} className={`a-nav-slot ${pinned?'is-pinned':''} ${expanded?'is-expanded':''}`} data-docked={layout.docked} data-part="navigation" aria-label="Workspaces and conversations" onClick={e=>{if(layout.narrow&&e.target.closest('[data-navigation-select],[data-action="session.select"]'))close()}} onPointerEnter={e=>{if(!layout.narrow&&e.pointerType!=='touch')expand(true)}} onPointerLeave={e=>{if(!layout.narrow&&e.pointerType!=='touch'&&!e.currentTarget.contains(document.activeElement))expand(false)}}>
  <div className="a-nav-rail">
   <div className="a-nav-head"><button className="a-icon" type="button" aria-label={layout.narrow?'Close navigation':pinned?'Unpin navigation':'Pin navigation open'} title={layout.narrow?'Close navigation':pinned?'Unpin sidebar':'Pin sidebar open'} aria-pressed={pinned} aria-expanded={expanded} data-action="view.update" onClick={()=>layout.narrow?close():patch(act,{navPinned:!pinned,navExpanded:!pinned})}>{layout.narrow?<X/>:<PanelLeft/>}{!layout.narrow&&<AttentionBadge state={state} section="chats"/>}</button><strong className="a-nav-reveal">Your work</strong></div>
   <button className="a-nav-main" type="button" onClick={add} data-action="session.draft" aria-label="New chat" title="New chat"><Plus/><span className="a-nav-reveal">New chat</span></button>
   <div className="a-nav-content a-nav-reveal"><ShellModules shell={shell}/></div>
  </div>
  {layout.docked&&<PaneResizer layout={layout} pane="nav"/>}
 </aside></NavigationOpen.Provider>;
}

export function SessionHistoryControls({session,act,onLoadEarlier}){
 if(!session)return null;
 const unavailable=session.workspaceAvailable===false,readOnly=session.historyReadOnlyReason;
 const pending=!!session.historyLoading||session.historyLoaded===false&&!session.historyError,earlier=Number(session.sharedHistoryOffset)>0;
 const notices=session.historyActivity?.diagnostics||[],partial=notices.some(item=>['activity_scan_limit','scan_limit','invalid_event','incomplete_event','unreadable_file'].includes(item.code)),recovered=notices.some(item=>item.code==='recovered_backup');
 const retry=()=>act('session.select',{id:session.id});
 const loadEarlier=()=>{onLoadEarlier?.();return act('session.history',{id:session.id,before:session.sharedHistoryOffset,limit:100})};
 if(!pending&&!session.historyError&&!earlier&&!unavailable&&!readOnly&&!partial&&!recovered)return null;
 return <div className="a-session-history" data-part="session-history">
  {(unavailable||readOnly)&&<p role="status"><FolderOpen/>{readOnly||'This workspace folder is unavailable. You can read its saved chats here.'}</p>}
  {recovered&&<p role="status"><AlertCircle/>Showing a recovered history backup. Original files are unchanged.</p>}
  {partial&&<p role="status"><AlertCircle/>Some saved activity is unavailable or outside the loaded window. Conversation text comes from the saved transcript.</p>}
  {pending&&!session.messages?.length&&<p role="status"><LoaderCircle className="a-progress-spinner"/>Loading conversation…</p>}
  {session.historyError&&<div className="a-session-history-error" role="alert"><AlertCircle/><span>{session.historyError}</span><button type="button" className="a-link" data-action="session.select" disabled={pending} onClick={retry}>Try again</button></div>}
  {earlier&&<button type="button" className="a-soft" data-action="session.history" disabled={pending} onClick={loadEarlier}>{pending?'Loading earlier messages…':'Load earlier messages'}</button>}
 </div>;
}

export {A2UISurface} from './a2ui';
export function AgentCanvas({state,act,dispatch=act}){
 const canvas=state.canvas||{},view=state.view||{},draft=view.canvasDraft||{},layout=usePanelLayout(state,act),panel=useRef(null),actRef=useRef(act);
 actRef.current=act;
 const [controlsHost,setControlsHost]=useState(null);
 const artifacts=chatArtifacts(state),hasContent=!!state.canvasWorkspace?.views?.length&&!canvas.placeholder,hasArtifacts=artifacts.length>0;
 useEffect(()=>{
  if(!canvas.open&&panel.current?.contains(document.activeElement))document.querySelector('.a-canvas-toggle')?.focus({preventScroll:true});
 },[canvas.open]);
 const mounted=useRef(false);
 if(canvas.open)mounted.current=true;
 const focused=!!canvas.open&&!!view.canvasFocused,controls=(!!view.canvasControlsPinned||!!view.canvasControlsExpanded)&&(hasContent||hasArtifacts||!!draft.open||!!draft.browser);
 const changeDraft=value=>patch(act,{canvasDraft:{...draft,...value},canvasControlsExpanded:true});
 const controlRegions='.a-canvas-chrome,.a-canvas-toolbar,.a-canvas-result.success,.a-browser-note';
 const inControls=target=>!!target?.closest?.(controlRegions)&&!!panel.current?.contains(target);
 const collapseControls=()=>{if(controls&&!view.canvasControlsPinned&&!draft.open&&!draft.browser)patch(act,{canvasControlsExpanded:false})};
 useModalFocus(panel,focused,()=>patch(actRef.current,{canvasFocused:false}));
 useEffect(()=>{if(focused)panel.current?.querySelector('[aria-label="Exit canvas focus"]')?.focus({preventScroll:true})},[focused]);
 if(!mounted.current)return null;
 const latestEvent=canvas.events?.at(-1);
 return <CanvasControlsHost.Provider value={controlsHost}><aside ref={panel} id="workspace-canvas" className="a-canvas-panel" hidden={!canvas.open||!state.selectedSessionId} inert={!canvas.open||!state.selectedSessionId} aria-hidden={!canvas.open||!state.selectedSessionId||undefined} data-part="canvas" data-focused={focused} data-controls={controls} data-pinned={!!view.canvasControlsPinned} aria-label="Agent canvas" role={focused?'dialog':undefined} aria-modal={focused||undefined}
  onPointerOut={e=>{if(e.pointerType!=='touch'&&inControls(e.target)&&!inControls(e.relatedTarget)&&!inControls(document.activeElement))collapseControls()}}
  onBlur={e=>{if(inControls(e.target)&&!inControls(e.relatedTarget)&&!panel.current?.querySelector(controlRegions.split(',').map(selector=>selector+':hover').join(',')))collapseControls()}}>
  {!focused&&!layout.overlay&&<PaneResizer layout={layout} pane="canvas"/>}
  <div className="a-canvas-chrome" onFocus={()=>{if(!controls)patch(act,{canvasControlsExpanded:true})}} onPointerEnter={e=>{if(e.pointerType!=='touch'&&!controls)patch(act,{canvasControlsExpanded:true})}}>
   <header className="a-canvas-head">{layout.narrow&&<button type="button" className="a-canvas-back a-soft" data-action="canvas.visibility" onClick={()=>act('canvas.visibility',{open:false})}><ArrowLeft/>Back to chat</button>}<FileText/><strong title={canvas.title||'Canvas'}>{canvas.title||'Canvas'}</strong>
    {(hasContent||hasArtifacts)&&<button type="button" className="a-icon" aria-label={view.canvasControlsPinned?'Unpin canvas controls':'Pin canvas controls'} aria-pressed={!!view.canvasControlsPinned} data-action="view.update" onClick={()=>patch(act,{canvasControlsPinned:!view.canvasControlsPinned,canvasControlsExpanded:!view.canvasControlsPinned})}><Pin/></button>}
    <button type="button" className="a-icon" aria-label={focused?'Exit canvas focus':'Focus canvas'} aria-pressed={focused} data-action="view.update" onClick={()=>patch(act,{canvasFocused:!focused})}>{focused?<Minimize2/>:<Maximize2/>}</button>
    <button type="button" className="a-icon" aria-label="Close canvas panel" data-action="canvas.visibility" onClick={()=>act('canvas.visibility',{open:false})}><X/></button>
   </header>
   <div className="a-canvas-controls" inert={!controls}>
    {(hasContent||hasArtifacts)&&<div className="a-canvas-actions"><ShellSlot name="canvas.toolbar"><button type="button" className="a-soft" aria-label={`Saved artifacts (${chatArtifacts(state).length})`} aria-pressed={!!draft.library} data-action="view.update" onClick={()=>changeDraft({library:!draft.library,open:false,browser:false})}><Library/>Library</button><button type="button" className="a-soft" aria-label="Open a website in canvas" aria-expanded={!!draft.browser} data-action="view.update" onClick={()=>changeDraft({browser:!draft.browser,open:false,library:false})}><Globe/>Website</button><button type="button" className="a-soft" aria-label="Open a file in canvas" aria-expanded={!!draft.open} data-action="view.update" onClick={()=>changeDraft({open:!draft.open,browser:false,library:false})}><FolderOpen/>File</button></ShellSlot></div>}
  <CanvasTabs state={state} act={act}/>
  {draft.browser&&<BrowserAddress state={state} act={act}/>}
  {draft.open&&<form className="a-canvas-file-form" onSubmit={e=>{e.preventDefault();act('canvas.show',{kind:draft.kind||'auto',path:draft.path||''})}}><label htmlFor="canvas-file-path">File in this workspace</label><PathField id="canvas-file-path" value={draft.path||''} onChange={path=>changeDraft({path})} state={state} act={act} placeholder="README.md or a full path"/><div className="a-canvas-file-actions"><select aria-label="Canvas file format" value={draft.kind||'auto'} data-action="view.update" onChange={e=>changeDraft({kind:e.target.value})}><option value="auto">Detect automatically</option><option value="html">HTML</option><option value="babylon">3D · Babylon.js</option><option value="mermaid">Mermaid</option><option value="dot">Graphviz DOT</option><option value="json">JSON</option><option value="jsonl">JSONL</option><option value="text">Text</option><option value="markdown">Markdown</option><option value="code">Code</option><option value="image">Image</option></select><button type="submit" className="a-primary" data-action="canvas.show" disabled={!draft.path?.trim()}>Open file<ChevronRight/></button></div></form>}
  {canvas.path&&<div className="a-canvas-file-path" title={canvas.path}>{canvas.path}</div>}
  <div ref={setControlsHost} hidden={!!draft.library||!hasContent}/>
   </div>
  </div>
  <div className="a-canvas-body">
   {!!state.canvasWorkspace?.views?.length&&<McpAppVisibilityProvider visible={!!canvas.open&&!draft.library}><CanvasWorkspace state={state} dispatch={dispatch} hidden={!!draft.library}/></McpAppVisibilityProvider>}
   {hasArtifacts&&(draft.library||!hasContent)?<SavedArtifacts state={state} act={act}/>:!hasContent&&<div className="a-canvas-empty"><PanelRight/><h2>Nothing in Canvas yet</h2><p>Ask Amplifier to create something here, or open a file or website.</p><div className="a-canvas-empty-actions"><button type="button" className="a-soft" data-action="view.update" onClick={()=>changeDraft({open:true,browser:false,library:false})}><FolderOpen/>Open a file</button><button type="button" className="a-soft" data-action="view.update" onClick={()=>changeDraft({browser:true,open:false,library:false})}><Globe/>Open a website</button></div></div>}
  </div>
  {latestEvent&&<div className="a-canvas-event" role="status"><Check/><span>Response recorded · {latestEvent.name}</span><small>The agent can see this response in app state.</small></div>}
 </aside></CanvasControlsHost.Provider>;
}
