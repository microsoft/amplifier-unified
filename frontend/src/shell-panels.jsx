import {CanvasTabs,SavedArtifacts,BrowserAddress,BrowserPreview,chatArtifacts} from './canvas-library';
import React,{useEffect,useRef,useState} from 'react';
import {FolderOpen,MessageCircle,Plus,PanelLeft,PanelRight,Search,Pencil,Trash2,X,Check,FileText,ChevronRight,Library,Globe,Maximize2,Minimize2,SlidersHorizontal,Pin,RefreshCw,LoaderCircle,AlertCircle} from 'lucide-react';
import {CanvasViewer} from './canvas-viewer';
import {McpAppViewer} from './mcp-app-viewer';
import {PaneResizer,usePanelLayout} from './panel-layout';
import {chatPage} from './chat-navigation';
import {AttentionBadge} from './attention';
import {PathField} from './settings-ui';

const patch=(act,value)=>act('view.update',{patch:value});
export function reopenCanvas(state,act){
 return act('canvas.reopen',{});
}
export function CanvasToggle({state,act}){
 return <button type="button" className="a-icon" aria-label={state.canvas?.open?'Close canvas':'Open canvas'} aria-pressed={!!state.canvas?.open} data-action={state.canvas?.open?'canvas.close':'canvas.reopen'} onClick={()=>state.canvas?.open?act('canvas.close',{}):reopenCanvas(state,act)}><PanelRight/></button>;
}
export function ChatRename({chat,act,cancel}){
 const [name,setName]=useState(chat.title||''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const submitting=useRef(false);
 useEffect(()=>setName(chat.title||''),[chat.title]);
 const submit=async e=>{
  e.preventDefault();
  const title=name.trim();
  if(!title||submitting.current)return;
  submitting.current=true;setSaving(true);setError('');
  try{
   const result=await act('session.rename',{id:chat.id,title});
   if(result&&result.accepted!==false)cancel();
   else setError(result?.error||'Could not rename. Your text is kept; try again.');
  }catch(error){setError(error.message||'Could not rename. Please try again.')}
  finally{submitting.current=false;setSaving(false)}
 };
 return <form aria-busy={saving} className="a-nav-chat a-nav-chat-rename" onSubmit={submit}><input id="nav-workspace-name" aria-label={`New name for ${chat.title||'conversation'}`} value={name} disabled={saving} required autoFocus onChange={e=>setName(e.target.value)}/><button type="submit" className="a-icon a-nav-chat-edit" aria-label="Save conversation name" disabled={saving||!name.trim()} data-action="session.rename"><Check/></button><button type="button" className="a-icon a-nav-chat-edit" aria-label="Cancel conversation rename" disabled={saving} data-action="view.update" onClick={cancel}><X/></button>{error&&<small role="alert" className="a-danger">{error}</small>}</form>;
}
export function WorkspaceRail({state,session,act,selectSession,newSession}){
 const layout=usePanelLayout(state,act);
 const view=state.view||{},pinned=!!view.navPinned,expanded=pinned||!!view.navExpanded,draft=view.workspaceDraft||{};
 const form=useRef(null),draftRef=useRef(draft);
 useEffect(()=>{draftRef.current=draft},[draft]);
 const history=state.sharedHistory||{},refreshing=!!(history.loading||history.refreshing);
 const workspaces=state.workspaces||[],workspace=workspaces.find(w=>w.id===state.selectedWorkspaceId)||workspaces[0];
 const chats=chatPage(state,workspace);
 const changePage=index=>patch(act,{navChatPage:{...chats.scope,index}});
 const setDraft=value=>{draftRef.current=value;patch(act,{workspaceDraft:value})};
 const updateDraft=value=>setDraft({...draft,...value});
 const expand=value=>{if(!pinned&&!!view.navExpanded!==value)patch(act,{navExpanded:value})};
 const choose=id=>{(selectSession||((id)=>act('session.select',{id})))(id);if(!pinned)patch(act,{navExpanded:false})};
 const add=()=>{(newSession||((path)=>act('session.create',{workspace:path})))(workspace?.path);if(!pinned)patch(act,{navExpanded:false})};
 const submit=async e=>{e.preventDefault();let result;if(draft.mode==='add')result=await act('workspace.add',{path:draft.path||'',...(draft.name?.trim()?{name:draft.name.trim()}:{})});else if(draft.mode==='rename')result=await act('workspace.rename',{id:draft.id,name:draft.name||''});else if(draft.mode==='chat-rename')result=await act('session.rename',{id:draft.id,title:draft.name||''});else if(draft.mode==='chat-delete')result=await act('session.delete',{id:draft.id});else if(draft.mode==='remove')result=await act('workspace.remove',{id:draft.id});if(result?.accepted!==false&&result)setDraft({})};
 useEffect(()=>{if(!draft.mode)return;form.current?.scrollIntoView?.({block:'nearest'});form.current?.querySelector?.('input')?.focus()},[draft.mode,draft.id]);
 return <aside className={`a-nav-slot ${pinned?'is-pinned':''} ${expanded?'is-expanded':''}`} data-docked={layout.docked} data-part="navigation" aria-label="Workspaces and conversations" onPointerEnter={e=>{if(e.pointerType!=='touch')expand(true)}} onPointerLeave={e=>{if(e.pointerType!=='touch'&&!draftRef.current.mode)expand(false)}}>
  <div className="a-nav-rail">
   <div className="a-nav-head"><button className="a-icon" type="button" aria-label={pinned?'Unpin navigation':'Pin navigation open'} title={pinned?'Unpin sidebar':'Pin sidebar open'} aria-pressed={pinned} aria-expanded={expanded} data-action="view.update" onClick={()=>patch(act,{navPinned:!pinned,navExpanded:!pinned})}><PanelLeft/><AttentionBadge state={state} section="chats"/></button><strong className="a-nav-reveal">Your work</strong></div>
   <button className="a-nav-main" type="button" onClick={add} data-action="session.create" disabled={workspace?.available===false} aria-label="New chat in workspace" title={workspace?.available===false?'Workspace folder unavailable':'New chat'}><Plus/><span className="a-nav-reveal">New chat</span></button>
   <button className="a-nav-main" type="button" onClick={()=>{expand(true);setDraft(draft.mode==='add'?{}:{mode:'add',path:'',name:''})}} data-action="view.update" aria-label="Add workspace" title="Add workspace"><FolderOpen/><span className="a-nav-reveal">Add workspace</span></button>
   <div className="a-nav-content a-nav-reveal">
    <label className="a-nav-eyebrow" htmlFor="nav-workspace">Workspace</label><select id="nav-workspace" value={workspace?.id||''} data-action="workspace.select" onChange={e=>act('workspace.select',{id:e.target.value})}>{workspaces.map(w=><option key={w.id} value={w.id}>{w.name}{state.attention?.workspaces?.[w.id]?` · ${state.attention.workspaces[w.id]} ready`:''}</option>)}</select>
    {workspace&&<div className="a-nav-workspace-path"><span title={workspace.path||'Project folder unavailable'}>{workspace.path||'Project folder unavailable'}</span><button type="button" className="a-icon" aria-label="Rename workspace" data-action="view.update" onClick={()=>setDraft({mode:'rename',id:workspace.id,name:workspace.name})}><Pencil/></button><button type="button" className="a-icon" aria-label="Remove workspace registration" disabled={workspaces.length<2} data-action="view.update" onClick={()=>setDraft({mode:'remove',id:workspace.id,name:workspace.name})}><Trash2/></button></div>}
    {draft.mode&&draft.mode!=='chat-rename'&&<form ref={form} className="a-nav-form" onSubmit={submit}>
     <div className="a-nav-form-title"><strong>{draft.mode==='add'?'Add workspace':draft.mode==='rename'?'Rename workspace':draft.mode==='chat-rename'?'Rename chat':draft.mode==='remove'?'Remove workspace?':'Remove chat?'}</strong><button className="a-icon" type="button" aria-label="Cancel navigation edit" data-action="view.update" onClick={()=>setDraft({})}><X/></button></div>
     {draft.mode==='add'&&<><label htmlFor="nav-workspace-path">Folder</label><PathField id="nav-workspace-path" value={draft.path||''} onChange={path=>updateDraft({path})} directory state={state} act={act} placeholder="~/Projects/my-project"/></>}
     {['add','rename','chat-rename'].includes(draft.mode)?<><label htmlFor="nav-workspace-name">{draft.mode==='add'?'Name (optional)':'Name'}</label><input id="nav-workspace-name" value={draft.name||''} required={draft.mode!=='add'} data-action="view.update" onChange={e=>updateDraft({name:e.target.value})}/></>:<p>{draft.mode==='remove'?`Remove ${draft.name} from this list? Its files and chats will stay on disk.`:`Remove ${draft.name} from this list? Its shared history stays on disk. Any work in progress in this app will stop.`}</p>}
     <button type="submit" className={['remove','chat-delete'].includes(draft.mode)?'a-soft a-danger':'a-primary'} data-action={draft.mode==='add'?'workspace.add':draft.mode==='rename'?'workspace.rename':draft.mode==='remove'?'workspace.remove':draft.mode==='chat-delete'?'session.delete':'session.rename'}>{draft.mode==='add'?'Add workspace':draft.mode==='remove'?'Remove registration':draft.mode==='chat-delete'?'Remove chat':'Save name'}</button>
    </form>}
    <div className="a-nav-search"><Search/><input aria-label="Filter conversations" type="search" value={view.navFilter||''} placeholder="Find chats · * ? patterns" data-action="view.update" onChange={e=>patch(act,{navFilter:e.target.value})}/></div>
    <div className="a-nav-eyebrow a-nav-conversations">Conversations <span>{chats.total}</span><button type="button" className="a-icon" aria-label="Refresh workspaces and chats" title="CLI projects and chats appear automatically. Refresh now." data-action="history.refresh" disabled={refreshing} onClick={()=>act('history.refresh',{})}><RefreshCw className={refreshing?'a-progress-spinner':undefined}/></button></div>
    {history.loading&&<p className="a-nav-history-status" role="status"><LoaderCircle className="a-progress-spinner"/>Finding projects and chats…</p>}
    {history.error&&<p className="a-nav-history-status a-danger" role="alert"><AlertCircle/><span>{history.error}</span></p>}
    <div className="a-nav-chats">{chats.items.map(chat=>draft.mode==='chat-rename'&&draft.id===chat.id?<ChatRename key={chat.id} chat={chat} act={act} cancel={()=>setDraft({})}/>:<div className={`a-nav-chat ${chat.id===session?.id?'is-selected':''}`} key={chat.id}><button className="a-nav-chat-select" type="button" data-action="session.select" aria-current={chat.id===session?.id?'page':undefined} title={[chat.title,chat.description].filter(Boolean).join(' — ')} onClick={()=>choose(chat.id)}><MessageCircle/><span>{chat.title||'Untitled conversation'}</span><AttentionBadge state={state} count={state.attention?.sessions?.[chat.id]||0}/>{['running','working','starting'].includes(chat.status)&&<span className="a-nav-busy" aria-label="Working"/>}</button><button type="button" className="a-icon a-nav-chat-edit" aria-label={`Rename ${chat.title||'conversation'}`} data-action="view.update" onClick={()=>{expand(true);setDraft({mode:'chat-rename',id:chat.id,name:chat.title||''})}}><Pencil/></button><button type="button" className="a-icon a-nav-chat-edit" aria-label={`Remove ${chat.title||'conversation'} from list`} data-action="view.update" onClick={()=>{expand(true);setDraft({mode:'chat-delete',id:chat.id,name:chat.title||'conversation'})}}><Trash2/></button></div>)}{!chats.total&&!history.loading&&<p className="a-nav-empty">{view.navFilter?'No matching chats.':'Your conversations will appear here.'}</p>}</div>
    {chats.pages>1&&<div className="a-nav-pagination"><span>{chats.start+1}–{chats.end} of {chats.total}</span><div><button type="button" className="a-link" data-action="view.update" aria-label="Show previous conversations" disabled={chats.index===0} onClick={()=>changePage(chats.index-1)}>Previous</button><button type="button" className="a-link" data-action="view.update" aria-label="Show more conversations" disabled={chats.index===chats.pages-1} onClick={()=>changePage(chats.index+1)}>More chats<ChevronRight/></button></div></div>}
   </div>
   <div className="a-nav-bottom"><button className="a-nav-main" type="button" data-action="canvas.reopen" aria-label="Open workspace canvas" title="Canvas" onClick={()=>reopenCanvas(state,act)}><PanelRight/><span className="a-nav-reveal">Canvas</span></button></div>
  </div>
  {layout.docked&&<PaneResizer layout={layout} pane="nav"/>}
 </aside>;
}

export function SessionHistoryControls({session,act,onLoadEarlier}){
 if(!session)return null;
 const unavailable=session.workspaceAvailable===false,readOnly=session.historyReadOnlyReason;
 const pending=!!session.historyLoading||session.historyLoaded===false&&!session.historyError,earlier=Number(session.sharedHistoryOffset)>0;
 const retry=()=>act('session.select',{id:session.id});
 const loadEarlier=()=>{onLoadEarlier?.();return act('session.history',{id:session.id,before:session.sharedHistoryOffset,limit:100})};
 if(!pending&&!session.historyError&&!earlier&&!unavailable&&!readOnly)return null;
 return <div className="a-session-history" data-part="session-history">
  {(unavailable||readOnly)&&<p role="status"><FolderOpen/>{readOnly||'This workspace folder is unavailable. You can read its saved chats here.'}</p>}
  {pending&&<p role="status"><LoaderCircle className="a-progress-spinner"/>Loading conversation…</p>}
  {session.historyError&&<div className="a-session-history-error" role="alert"><AlertCircle/><span>{session.historyError}</span><button type="button" className="a-link" data-action="session.select" disabled={pending} onClick={retry}>Try again</button></div>}
  {earlier&&<button type="button" className="a-soft" data-action="session.history" disabled={pending} onClick={loadEarlier}>Load earlier messages</button>}
 </div>;
}

export function A2UISurface({surface,act}){
 const rows=new Map((surface?.components||[]).map(row=>[row.id,row]));
 const render=(id,ancestors=[])=>{
  if(ancestors.length>20||ancestors.includes(id))return null;
  const row=rows.get(id);if(!row)return null;
  const [kind,props]=Object.entries(row.component||{})[0]||[];if(!props)return null;
  const children=()=> (props.children?.explicitList||[]).map(child=><React.Fragment key={child}>{render(child,[...ancestors,id])}</React.Fragment>);
  if(kind==='Text'){const Tag=['h1','h2','h3','h4','h5'].includes(props.usageHint)?props.usageHint:'p';return <Tag className="a-canvas-text">{props.text?.literalString||''}</Tag>}
  if(kind==='Row'||kind==='Column')return <div className={`a-canvas-${kind.toLowerCase()}`}>{children()}</div>;
  if(kind==='Card')return <div className="a-canvas-card">{render(props.child,[...ancestors,id])}</div>;
  if(kind==='Divider')return <hr/>;
  if(kind==='Button')return <button type="button" className="a-soft" data-action="canvas.event" onClick={()=>act('canvas.event',{surfaceId:surface.surfaceId,componentId:id,name:props.action.name})}>{render(props.child,[...ancestors,id])}</button>;
  return null;
 };
 return <div className="a-canvas-surface" data-part="agent-surface">{render(surface?.root)}</div>;
}
export function AgentCanvas({state,act}){
 const canvas=state.canvas||{},view=state.view||{},draft=view.canvasDraft||{},layout=usePanelLayout(state,act),panel=useRef(null),actRef=useRef(act);
 actRef.current=act;
 const focused=!!canvas.open&&!!view.canvasFocused,controls=!!view.canvasControlsPinned||!!view.canvasControlsExpanded;
 const changeDraft=value=>patch(act,{canvasDraft:{...draft,...value},canvasControlsExpanded:true});
 const controlRegions='.a-canvas-chrome,.a-canvas-toolbar,.a-canvas-result.success,.a-browser-note';
 const inControls=target=>!!target?.closest?.(controlRegions)&&!!panel.current?.contains(target);
 const collapseControls=()=>{if(controls&&!view.canvasControlsPinned&&!draft.open&&!draft.browser)patch(act,{canvasControlsExpanded:false})};
 useEffect(()=>{
  if(!focused||!panel.current)return;
  const previous=document.activeElement,el=panel.current,app=el.closest('#amp-one'),hidden=[];
  // Keep the live viewer mounted. Only the surrounding app becomes inert.
  for(const child of [...(app?.children||[]),...(el.parentElement?.children||[])]){
   if(child===el||child.contains(el)||child.matches('style,.a-overlay,[role="dialog"]'))continue;
   hidden.push([child,child.inert]);child.inert=true;
  }
  el.querySelector('[aria-label="Exit canvas focus"]')?.focus({preventScroll:true});
  const escape=e=>{if(e.key==='Escape'&&!document.querySelector('.a-overlay')){e.preventDefault();patch(actRef.current,{canvasFocused:false})}};
  document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('keydown',escape);for(const [child,inert] of hidden)child.inert=inert;if(previous?.isConnected)previous.focus?.({preventScroll:true})};
 },[focused]);
 if(!canvas.open)return null;
 const latestEvent=canvas.events?.at(-1);
 return <aside ref={panel} className="a-canvas-panel" data-part="canvas" data-focused={focused} data-controls={controls} data-pinned={!!view.canvasControlsPinned} aria-label="Agent canvas" role={focused?'dialog':undefined} aria-modal={focused||undefined}
  onPointerOut={e=>{if(e.pointerType!=='touch'&&inControls(e.target)&&!inControls(e.relatedTarget)&&!inControls(document.activeElement))collapseControls()}}
  onBlur={e=>{if(inControls(e.target)&&!inControls(e.relatedTarget)&&!panel.current?.querySelector(controlRegions.split(',').map(selector=>selector+':hover').join(',')))collapseControls()}}>
  {!focused&&!layout.overlay&&<PaneResizer layout={layout} pane="canvas"/>}
  <div className="a-canvas-chrome" onPointerEnter={e=>{if(e.pointerType!=='touch'&&!controls)patch(act,{canvasControlsExpanded:true})}}>
   <header className="a-canvas-head"><FileText/><strong title={canvas.title||'Canvas'}>{canvas.title||'Canvas'}</strong>
    <button type="button" className="a-icon" aria-label="Canvas controls" aria-expanded={controls} data-action="view.update" onClick={()=>patch(act,{canvasControlsExpanded:!controls,canvasControlsPinned:false,canvasDraft:{...draft,open:false,browser:false}})}><SlidersHorizontal/></button>
    <button type="button" className="a-icon" aria-label={focused?'Exit canvas focus':'Focus canvas'} aria-pressed={focused} data-action="view.update" onClick={()=>patch(act,{canvasFocused:!focused})}>{focused?<Minimize2/>:<Maximize2/>}</button>
    <button type="button" className="a-icon" aria-label="Close canvas panel" data-action="canvas.close" onClick={()=>act('canvas.close',{})}><X/></button>
   </header>
   <div className="a-canvas-controls" inert={!controls}>
    <div className="a-canvas-actions"><button type="button" className="a-soft" aria-label={`Saved artifacts (${chatArtifacts(state).length})`} aria-pressed={!!draft.library} data-action="view.update" onClick={()=>changeDraft({library:!draft.library,open:false,browser:false})}><Library/>Library</button><button type="button" className="a-soft" aria-label="Open a website in canvas" aria-expanded={!!draft.browser} data-action="view.update" onClick={()=>changeDraft({browser:!draft.browser,open:false,library:false})}><Globe/>Website</button><button type="button" className="a-soft" aria-label="Open a file in canvas" aria-expanded={!!draft.open} data-action="view.update" onClick={()=>changeDraft({open:!draft.open,browser:false,library:false})}><FolderOpen/>File</button><button type="button" className="a-icon" aria-label={view.canvasControlsPinned?'Unpin canvas controls':'Pin canvas controls'} aria-pressed={!!view.canvasControlsPinned} data-action="view.update" onClick={()=>patch(act,{canvasControlsPinned:!view.canvasControlsPinned,canvasControlsExpanded:!view.canvasControlsPinned})}><Pin/></button></div>
  <CanvasTabs state={state} act={act}/>
  {draft.browser&&<BrowserAddress state={state} act={act}/>}
  {draft.open&&<form className="a-canvas-file-form" onSubmit={e=>{e.preventDefault();act('canvas.show',{kind:draft.kind||'auto',path:draft.path||''})}}><label htmlFor="canvas-file-path">File in this workspace</label><PathField id="canvas-file-path" value={draft.path||''} onChange={path=>changeDraft({path})} state={state} act={act} placeholder="README.md or a full path"/><div className="a-canvas-file-actions"><select aria-label="Canvas file format" value={draft.kind||'auto'} data-action="view.update" onChange={e=>changeDraft({kind:e.target.value})}><option value="auto">Detect automatically</option><option value="html">HTML</option><option value="babylon">3D · Babylon.js</option><option value="mermaid">Mermaid</option><option value="dot">Graphviz DOT</option><option value="json">JSON</option><option value="jsonl">JSONL</option><option value="text">Text</option><option value="markdown">Markdown</option><option value="code">Code</option><option value="image">Image</option></select><button type="submit" className="a-primary" data-action="canvas.show" disabled={!draft.path?.trim()}>Open file<ChevronRight/></button></div></form>}
  {canvas.path&&<div className="a-canvas-file-path" title={canvas.path}>{canvas.path}</div>}
   </div>
  </div>
  <div className="a-canvas-body">{draft.library||canvas.placeholder?<SavedArtifacts state={state} act={act}/>:canvas.kind==='mcp-app'?<McpAppViewer key={canvas.id} canvas={canvas} act={act}/>:canvas.kind==='browser'?<BrowserPreview key={canvas.id} canvas={canvas} act={act}/>:canvas.kind==='a2ui'?<A2UISurface surface={canvas.surface} act={act}/>:canvas.kind?<CanvasViewer key={canvas.id} canvas={canvas} act={act}/>:<div className="a-canvas-empty"><PanelRight/><h2>A little more room to work</h2><p>Preview a workspace file here, or ask your agent to display a document or interactive view.</p><button type="button" className="a-soft" data-action="view.update" onClick={()=>changeDraft({open:true})}><FolderOpen/>Open a file</button></div>}</div>
  {latestEvent&&<div className="a-canvas-event" role="status"><Check/><span>Response recorded · {latestEvent.name}</span><small>The agent can see this response in app state.</small></div>}
 </aside>;
}
