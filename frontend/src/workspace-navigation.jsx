import React,{useRef,useState} from 'react';
import {Folder,Plus,Search,ChevronDown,ChevronRight,MoreHorizontal} from 'lucide-react';
import {NavigationRow,NavigationStatus,useActivityClock,WorkspaceDetails} from './navigation-details';
import {activityFor} from './navigation-presentation';
import {WorkspaceForm} from './workspace-setup';
import './workspace-setup.css';

export function SimpleNavigation({model,workspaceHost,ChatDetails}){
 const {state,act,view,workspace,chats,choose,setDraft}=model,now=useActivityClock();
 const createButton=useRef(null);
 const closeCreate=()=>{setCreating(false);requestAnimationFrame(()=>createButton.current?.focus())};
 const [creating,setCreating]=useState(false),[error,setError]=useState('');
 const patch=value=>act('view.update',{patch:value});
 const home=state.homeNavigation||chats,workspaces=state.workspaceOverview?.items||state.workspaces||[];
 const pins=(home.items||[]).filter(row=>row.pinned),recent=(home.items||[]).filter(row=>!row.pinned).slice(0,12);
 const expanded=!view.navWorkspaceList?workspace?.id:null;
 const hostAct=workspaceHost?.dispatch||act;
 const open=async row=>{try{setError('');await hostAct('workspace.select',{id:row.id});await patch({navWorkspaceList:false,navChatScope:'workspace',navFilter:'',navStatusFilter:'all',navArchive:'active',navLocationFilter:'all'})}catch(err){setError(err.message)}};
 const renderChat=chat=><NavigationRow key={chat.id} className={`a-nav-chat ${chat.id===state.selectedSessionId?'is-selected':''}`} data-session-id={chat.id} label={chat.title||'Untitled conversation'} details={({close})=><ChatDetails chat={chat} model={model} now={now} close={close}/>}>
  <button className="a-nav-chat-select" type="button" data-navigation-select data-action="session.select" aria-current={chat.id===state.selectedSessionId?'page':undefined} onClick={()=>choose(chat.id)}><NavigationStatus activity={activityFor(chat,state)}/><span className="a-nav-chat-label"><span>{chat.title||'Untitled conversation'}</span></span></button>
 </NavigationRow>;
 const labels=new Map();for(const row of workspaces)labels.set(row.name,(labels.get(row.name)||0)+1);
 return <div className="a-simple-navigation">
  <button type="button" className="a-nav-main" data-action="view.update" onClick={()=>patch({navSimple:false,navChatScope:'all',navFilter:''})}><Search/>Search chats</button>
  {pins.length>0&&<section aria-label="Pinned chats"><h4 className="a-nav-chat-group-title">Pinned</h4>{pins.map(renderChat)}</section>}
  <section aria-label="Workspaces"><div className="a-simple-nav-heading"><h4 className="a-nav-chat-group-title">Workspaces</h4><button type="button" className="a-icon" ref={createButton} aria-label="New workspace" onClick={()=>setCreating(true)}><Plus/></button></div>
   {creating&&<WorkspaceForm state={state} act={hostAct} onCancel={closeCreate} onDone={closeCreate}/>}
   {workspaces.map(row=><React.Fragment key={row.id}><NavigationRow className="a-simple-workspace" label={row.name} details={({close})=><WorkspaceDetails row={{...row,chatCount:row.id===workspace?.id?chats.total:undefined}} now={now} actions={<button type="button" onClick={()=>{close();workspaceHost?.dispatch('view.update',{patch:{workspaceDraft:{mode:'rename',id:row.id,name:row.name}}})}}>Rename workspace</button>}/>}>
    <button type="button" className="a-nav-chat-select" aria-expanded={expanded===row.id} data-action="workspace.select" onClick={()=>expanded===row.id?patch({navWorkspaceList:true}):open(row)}><Folder/><span className="a-nav-chat-label"><span>{row.name}</span>{(state.settings?.workspaces?.showPaths||labels.get(row.name)>1)&&<small title={row.path}>{row.path}</small>}</span>{expanded===row.id?<ChevronDown/>:<ChevronRight/>}</button>
   </NavigationRow>{expanded===row.id&&<div className="a-simple-workspace-chats"><button type="button" className="a-link" aria-label={'New chat in '+row.name} data-action="session.draft" onClick={()=>act('session.draft',{workspace:row.path})}><Plus/>New chat</button>{chats.items.filter(chat=>!chat.pinned).map(renderChat)}{!chats.total&&<p className="a-caption">No chats yet</p>}{chats.pages>1&&<button type="button" className="a-link" onClick={()=>patch({navSimple:false})}>All chats in this workspace</button>}</div>}</React.Fragment>)}
   {state.workspaceOverview?.nextOffset!=null&&<button type="button" className="a-link" onClick={()=>patch({navSimple:false,navWorkspaceList:true,navChatScope:'workspace'})}>All workspaces</button>}
  </section>
  {recent.some(chat=>expanded!==chat.workspaceId)&&<section aria-label="Recent chats"><h4 className="a-nav-chat-group-title">Recent</h4>{recent.filter(chat=>expanded!==chat.workspaceId).map(renderChat)}</section>}
  {state.sharedHistory?.loading&&<p className="a-caption" role="status">Finding existing chats…</p>}{(error||state.sharedHistory?.error)&&<p role="alert" className="a-danger">{error||state.sharedHistory.error}</p>}
  {!!state.sharedHistory?.issueCount&&<p className="a-caption" role="status">Some saved folders or chats need attention.</p>}
  <button type="button" className="a-link a-simple-all-chats" onClick={()=>patch({navSimple:false,navChatScope:'all'})}><MoreHorizontal/>All chats</button>
 </div>;
}
