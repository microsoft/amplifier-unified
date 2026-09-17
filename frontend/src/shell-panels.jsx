import React,{useEffect,useRef,useState} from 'react';
import {FolderOpen,MessageCircle,Plus,Pin,PinOff,PanelLeft,PanelRight,Search,Pencil,Trash2,X,Check,FileText,ChevronRight} from 'lucide-react';
import {CanvasViewer} from './canvas-viewer';
import {filterList} from './list-filter';
import {PathField} from './settings-ui';

const patch=(act,value)=>act('view.update',{patch:value});
export function reopenCanvas(state,act){
 const c=state.canvas||{};
 return act('canvas.show',{kind:c.kind||'text',title:c.title||'Canvas',...(c.kind==='a2ui'?{surface:c.surface}:c.path?{path:c.path}:{content:c.content||''})});
}
export function CanvasToggle({state,act}){
 return <button type="button" className="a-icon" aria-label={state.canvas?.open?'Close canvas':'Open canvas'} aria-pressed={!!state.canvas?.open} data-action={state.canvas?.open?'canvas.close':'canvas.show'} onClick={()=>state.canvas?.open?act('canvas.close',{}):reopenCanvas(state,act)}><PanelRight/></button>;
}
export function WorkspaceRail({state,session,act,selectSession,newSession}){
 const view=state.view||{},pinned=!!view.navPinned,expanded=pinned||!!view.navExpanded,draft=view.workspaceDraft||{};
 const workspaces=state.workspaces||[],workspace=workspaces.find(w=>w.id===state.selectedWorkspaceId)||workspaces[0];
 const sessions=(state.sessions||[]).filter(s=>!workspace||s.workspace===workspace.path);
 const chats=filterList(sessions,view.navFilter||'',s=>[s.title||'Untitled conversation',s.id]);
 const setDraft=value=>patch(act,{workspaceDraft:value});
 const updateDraft=value=>setDraft({...draft,...value});
 const expand=value=>{if(!pinned&&!!view.navExpanded!==value)patch(act,{navExpanded:value})};
 const choose=id=>{(selectSession||((id)=>act('session.select',{id})))(id);if(!pinned)patch(act,{navExpanded:false})};
 const add=()=>{(newSession||((path)=>act('session.create',{workspace:path})))(workspace?.path);if(!pinned)patch(act,{navExpanded:false})};
 const submit=async e=>{e.preventDefault();let result;if(draft.mode==='add')result=await act('workspace.add',{path:draft.path||'',...(draft.name?.trim()?{name:draft.name.trim()}:{})});else if(draft.mode==='rename')result=await act('workspace.rename',{id:draft.id,name:draft.name||''});else if(draft.mode==='chat-rename')result=await act('session.rename',{id:draft.id,title:draft.name||''});else if(draft.mode==='chat-delete')result=await act('session.delete',{id:draft.id});else if(draft.mode==='remove')result=await act('workspace.remove',{id:draft.id});if(result?.accepted!==false&&result)setDraft({})};
 return <aside className={`a-nav-slot ${pinned?'is-pinned':''} ${expanded?'is-expanded':''}`} data-part="navigation" aria-label="Workspaces and conversations" onPointerEnter={e=>{if(e.pointerType!=='touch')expand(true)}} onPointerLeave={e=>{if(e.pointerType!=='touch'&&!draft.mode)expand(false)}}>
  <div className="a-nav-rail">
   <div className="a-nav-head"><button className="a-icon" type="button" aria-label={expanded?'Collapse navigation':'Expand navigation'} aria-expanded={expanded} data-action="view.update" onClick={()=>patch(act,{navExpanded:!expanded,...(expanded?{navPinned:false}:{})})}><PanelLeft/></button><strong className="a-nav-reveal">Your work</strong><button className="a-icon a-nav-reveal" type="button" aria-label={pinned?'Unpin navigation':'Pin navigation open'} aria-pressed={pinned} data-action="view.update" onClick={()=>patch(act,{navPinned:!pinned,navExpanded:true})}>{pinned?<PinOff/>:<Pin/>}</button></div>
   <button className="a-nav-main" type="button" onClick={add} data-action="session.create" aria-label="New chat in workspace" title="New chat"><Plus/><span className="a-nav-reveal">New chat</span></button>
   <button className="a-nav-main" type="button" onClick={()=>{expand(true);setDraft(draft.mode==='add'?{}:{mode:'add',path:'',name:''})}} data-action="view.update" aria-label="Add workspace" title="Add workspace"><FolderOpen/><span className="a-nav-reveal">Add workspace</span></button>
   <div className="a-nav-content a-nav-reveal">
    <label className="a-nav-eyebrow" htmlFor="nav-workspace">Workspace</label><select id="nav-workspace" value={workspace?.id||''} data-action="workspace.select" onChange={e=>act('workspace.select',{id:e.target.value})}>{workspaces.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select>
    {workspace&&<div className="a-nav-workspace-path"><span title={workspace.path}>{workspace.path}</span><button type="button" className="a-icon" aria-label="Rename workspace" data-action="view.update" onClick={()=>setDraft({mode:'rename',id:workspace.id,name:workspace.name})}><Pencil/></button><button type="button" className="a-icon" aria-label="Remove workspace registration" disabled={workspaces.length<2} data-action="view.update" onClick={()=>setDraft({mode:'remove',id:workspace.id,name:workspace.name})}><Trash2/></button></div>}
    {draft.mode&&<form className="a-nav-form" onSubmit={submit}>
     <div className="a-nav-form-title"><strong>{draft.mode==='add'?'Add workspace':draft.mode==='rename'?'Rename workspace':draft.mode==='chat-rename'?'Rename chat':draft.mode==='remove'?'Remove workspace?':'Delete conversation?'}</strong><button className="a-icon" type="button" aria-label="Cancel navigation edit" data-action="view.update" onClick={()=>setDraft({})}><X/></button></div>
     {draft.mode==='add'&&<><label htmlFor="nav-workspace-path">Folder</label><PathField id="nav-workspace-path" value={draft.path||''} onChange={path=>updateDraft({path})} directory state={state} act={act} placeholder="~/Projects/my-project"/></>}
     {['add','rename','chat-rename'].includes(draft.mode)?<><label htmlFor="nav-workspace-name">{draft.mode==='add'?'Name (optional)':'Name'}</label><input id="nav-workspace-name" value={draft.name||''} required={draft.mode!=='add'} data-action="view.update" onChange={e=>updateDraft({name:e.target.value})}/></>:<p>{draft.mode==='remove'?`Remove ${draft.name} from this list? Its files and chats will stay on disk.`:`Delete ${draft.name}? This removes its conversation history and stops any work in progress.`}</p>}
     <button type="submit" className={['remove','chat-delete'].includes(draft.mode)?'a-soft a-danger':'a-primary'} data-action={draft.mode==='add'?'workspace.add':draft.mode==='rename'?'workspace.rename':draft.mode==='remove'?'workspace.remove':draft.mode==='chat-delete'?'session.delete':'session.rename'}>{draft.mode==='add'?'Add workspace':draft.mode==='remove'?'Remove registration':draft.mode==='chat-delete'?'Delete conversation':'Save name'}</button>
    </form>}
    <div className="a-nav-search"><Search/><input aria-label="Filter conversations" type="search" value={view.navFilter||''} placeholder="Find chats · * ? patterns" data-action="view.update" onChange={e=>patch(act,{navFilter:e.target.value})}/></div>
    <div className="a-nav-eyebrow">Conversations <span>{chats.length}</span></div>
    <div className="a-nav-chats">{chats.map(chat=><div className={`a-nav-chat ${chat.id===session?.id?'is-selected':''}`} key={chat.id}><button className="a-nav-chat-select" type="button" data-action="session.select" aria-current={chat.id===session?.id?'page':undefined} title={chat.title} onClick={()=>choose(chat.id)}><MessageCircle/><span>{chat.title||'Untitled conversation'}</span>{['running','working','starting'].includes(chat.status)&&<span className="a-nav-busy" aria-label="Working"/>}</button><button type="button" className="a-icon a-nav-chat-edit" aria-label={`Rename ${chat.title||'conversation'}`} data-action="view.update" onClick={()=>setDraft({mode:'chat-rename',id:chat.id,name:chat.title||''})}><Pencil/></button><button type="button" className="a-icon a-nav-chat-edit" aria-label={`Delete ${chat.title||'conversation'}`} data-action="view.update" onClick={()=>setDraft({mode:'chat-delete',id:chat.id,name:chat.title||'conversation'})}><Trash2/></button></div>)}{!chats.length&&<p className="a-nav-empty">{view.navFilter?'No matching chats.':'Your conversations will appear here.'}</p>}</div>
   </div>
   <div className="a-nav-bottom"><button className="a-nav-main" type="button" data-action="canvas.show" aria-label="Open workspace canvas" title="Canvas" onClick={()=>reopenCanvas(state,act)}><PanelRight/><span className="a-nav-reveal">Canvas</span></button></div>
  </div>
 </aside>;
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
 const canvas=state.canvas||{},draft=state.view?.canvasDraft||{},sharedWidth=Number(state.view?.canvasWidth)||440,[width,setWidth]=useState(sharedWidth),drag=useRef(null);
 useEffect(()=>setWidth(sharedWidth),[sharedWidth]);
 const changeDraft=value=>patch(act,{canvasDraft:{...draft,...value}});
 const clamp=value=>Math.round(Math.min(900,Math.max(300,value)));
 const finish=e=>{if(!drag.current)return;const next=clamp(drag.current.width+drag.current.x-e.clientX);drag.current=null;setWidth(next);patch(act,{canvasWidth:next})};
 if(!canvas.open)return null;
 const latestEvent=canvas.events?.at(-1);
 return <aside className="a-canvas-panel" data-part="canvas" style={{'--canvas-width':`${width}px`}} aria-label="Agent canvas">
  <div className="a-canvas-resize" role="separator" aria-label="Resize canvas" aria-orientation="vertical" aria-valuemin={300} aria-valuemax={900} aria-valuenow={width} tabIndex={0} data-action="view.update" onPointerDown={e=>{e.preventDefault();drag.current={x:e.clientX,width};e.currentTarget.setPointerCapture(e.pointerId)}} onPointerMove={e=>{if(drag.current)setWidth(clamp(drag.current.width+drag.current.x-e.clientX))}} onPointerUp={finish} onPointerCancel={()=>{drag.current=null;setWidth(sharedWidth)}} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?300:e.key==='End'?900:clamp(width+(e.key==='ArrowLeft'?20:-20));setWidth(next);patch(act,{canvasWidth:next})}}}/>
  <header className="a-canvas-head"><FileText/><strong>{canvas.title||'Canvas'}</strong><button type="button" className="a-icon" aria-label="Open a file in canvas" aria-expanded={!!draft.open} data-action="view.update" onClick={()=>changeDraft({open:!draft.open})}><FolderOpen/></button><button type="button" className="a-icon" aria-label="Close canvas panel" data-action="canvas.close" onClick={()=>act('canvas.close',{})}><X/></button></header>
  {draft.open&&<form className="a-canvas-file-form" onSubmit={e=>{e.preventDefault();act('canvas.show',{kind:draft.kind||'auto',path:draft.path||''})}}><label htmlFor="canvas-file-path">File in this workspace</label><PathField id="canvas-file-path" value={draft.path||''} onChange={path=>changeDraft({path})} state={state} act={act} placeholder="README.md or a full path"/><div className="a-canvas-file-actions"><select aria-label="Canvas file format" value={draft.kind||'auto'} data-action="view.update" onChange={e=>changeDraft({kind:e.target.value})}><option value="auto">Detect automatically</option><option value="html">HTML</option><option value="mermaid">Mermaid</option><option value="dot">Graphviz DOT</option><option value="json">JSON</option><option value="jsonl">JSONL</option><option value="text">Text</option><option value="markdown">Markdown</option><option value="code">Code</option><option value="image">Image</option></select><button type="submit" className="a-primary" data-action="canvas.show" disabled={!draft.path?.trim()}>Open file<ChevronRight/></button></div></form>}
  {canvas.path&&<div className="a-canvas-file-path" title={canvas.path}>{canvas.path}</div>}
  <div className="a-canvas-body">{canvas.kind==='a2ui'?<A2UISurface surface={canvas.surface} act={act}/>:canvas.content?<CanvasViewer key={canvas.id} canvas={canvas} act={act}/>:<div className="a-canvas-empty"><PanelRight/><h2>A little more room to work</h2><p>Preview a workspace file here, or ask your agent to display a document or interactive view.</p><button type="button" className="a-soft" data-action="view.update" onClick={()=>changeDraft({open:true})}><FolderOpen/>Open a file</button></div>}</div>
  {latestEvent&&<div className="a-canvas-event" role="status"><Check/><span>Response recorded · {latestEvent.name}</span><small>The agent can see this response in app state.</small></div>}
 </aside>;
}
