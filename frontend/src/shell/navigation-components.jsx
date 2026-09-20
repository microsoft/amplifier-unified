import React,{useEffect,useRef,useState} from 'react';
import {FolderOpen,FolderPlus,MessageCircle,Search,Pencil,Trash2,X,Check,ChevronRight,Pin,RefreshCw,LoaderCircle,AlertCircle} from 'lucide-react';
import {chatPage,visibleWorkspaces} from '../chat-navigation';
import {WorkspaceExplorer} from '../workspace-explorer';
import {AttentionBadge} from '../attention';
import {PathField} from '../settings-ui';
import {useNavigation} from '../../../packages/shell-sdk/index.js';
const patch=(act,value)=>act('view.update',{patch:value});
export function ChatRename({chat,act,cancel,inputId="nav-workspace-name"}){
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
 return <form aria-busy={saving} className="a-nav-chat a-nav-chat-rename" data-session-id={chat.id} onSubmit={submit}><input id={inputId} maxLength={200} aria-label={`New name for ${chat.title||'conversation'}`} value={name} disabled={saving} required autoFocus onChange={e=>setName(e.target.value)}/><button type="submit" className="a-icon a-nav-chat-edit" aria-label="Save conversation name" disabled={saving||!name.trim()} data-action="session.rename"><Check/></button><button type="button" className="a-icon a-nav-chat-edit" aria-label="Cancel conversation rename" disabled={saving} data-action="view.update" onClick={cancel}><X/></button>{error&&<small role="alert" className="a-danger">{error}</small>}</form>;
}
function useNavigationController(host,kind){
 const state=useNavigation(React,host),act=host.dispatch,session={id:state.selectedSessionId},prefix=host.instanceId==='workspaces'?'nav-workspace':'nav-'+host.instanceId;
 const view=state.view||{},savedDraft=view.workspaceDraft||{},draft=(kind==='chats'?savedDraft.mode?.startsWith('chat-'):!savedDraft.mode?.startsWith('chat-'))?savedDraft:{};
 const form=useRef(null),draftRef=useRef(draft),submitting=useRef(false);
 const [saving,setSaving]=useState(false),[formError,setFormError]=useState('');
 useEffect(()=>{draftRef.current=draft},[draft]);
 const history=state.sharedHistory||{},refreshing=!!(history.loading||history.refreshing);
 const workspaces=visibleWorkspaces(state),workspace=workspaces.find(w=>w.id===state.selectedWorkspaceId);
 const workspaceCount=state.library?.bounded?state.library.workspaceCount:workspaces.length;
 const allChats=view.navChatScope==='all',chats=chatPage(state,workspace);
 const groups=[{name:'Pinned',items:chats.items.filter(chat=>chat.pinned)},{name:'Recent',items:chats.items.filter(chat=>!chat.pinned)}];
 const changePage=index=>patch(act,{navChatPage:{...chats.scope,index}});
 const setDraft=value=>{draftRef.current=value;setFormError('');patch(act,{workspaceDraft:value})};
 const updateDraft=value=>setDraft({...draft,...value});
 const choose=id=>act('session.select',{id});

 const submit=async e=>{
  e.preventDefault();if(submitting.current)return;submitting.current=true;setSaving(true);setFormError('');
  try{
   let result;
   if(draft.mode==='add')result=await act('workspace.create',{path:draft.path||'',...(draft.name?.trim()?{name:draft.name.trim()}:{})});
   else if(draft.mode==='rename')result=await act('workspace.rename',{id:draft.id,name:draft.name||''});
   else if(draft.mode==='chat-rename')result=await act('session.rename',{id:draft.id,title:draft.name||''});
   else if(draft.mode==='chat-delete')result=await act('session.delete',{id:draft.id});
   else if(draft.mode==='remove')result=await act('workspace.remove',{id:draft.id});
   if(result&&result.accepted!==false)setDraft({});
   else setFormError(result?.error||'Could not save. Check the folder or name and try again.');
  }catch(error){setFormError(error.message||'Could not save. Please try again.')}
  finally{submitting.current=false;setSaving(false)}
 };
 useEffect(()=>{if(!draft.mode)return;form.current?.scrollIntoView?.({block:'nearest'});form.current?.querySelector?.('input')?.focus()},[draft.mode,draft.id]);
 return {state,act,session,prefix,view,draft,form,saving,formError,history,refreshing,workspace,workspaceCount,allChats,chats,groups,changePage,setDraft,updateDraft,choose,submit};
}
function NavigationEditor({model}){
 const {state,act,prefix,draft,form,saving,formError,setDraft,updateDraft,submit}=model;
 return <>    {draft.mode&&draft.mode!=='chat-rename'&&<form ref={form} className="a-nav-form" aria-busy={saving} onSubmit={submit}>
     <div className="a-nav-form-title"><strong>{draft.mode==='add'?'New workspace':draft.mode==='rename'?'Rename workspace':draft.mode==='chat-rename'?'Rename chat':draft.mode==='remove'?'Remove workspace?':'Remove chat?'}</strong><button className="a-icon" type="button" aria-label="Cancel navigation edit" data-action="view.update" onClick={()=>setDraft({})}><X/></button></div>
     {draft.mode==='add'&&<><p>Choose an existing folder, or enter a path to create one. Your first chat opens here.</p><label htmlFor={prefix+'-path'}>Folder</label><PathField id={prefix+'-path'} value={draft.path||''} onChange={path=>updateDraft({path})} directory state={state} act={act} placeholder="~/Projects/my-project"/></>}
     {['add','rename','chat-rename'].includes(draft.mode)?<><label htmlFor={prefix+'-name'}>{draft.mode==='add'?'Name (optional)':'Name'}</label><input id={prefix+'-name'} maxLength={200} value={draft.name||''} required={draft.mode!=='add'} data-action="view.update" onChange={e=>updateDraft({name:e.target.value})}/></>:<p>{draft.mode==='remove'?`Remove ${draft.name} from this list? Its files and chats will stay on disk.`:`Remove ${draft.name} from this list? Its shared history stays on disk. Any work in progress in this app will stop.`}</p>}
     <button type="submit" disabled={saving||(draft.mode==='add'&&!draft.path?.trim())} className={['remove','chat-delete'].includes(draft.mode)?'a-soft a-danger':'a-primary'} data-action={draft.mode==='add'?'workspace.create':draft.mode==='rename'?'workspace.rename':draft.mode==='remove'?'workspace.remove':draft.mode==='chat-delete'?'session.delete':'session.rename'}>{saving?'Saving…':draft.mode==='add'?'Create workspace':draft.mode==='remove'?'Remove registration':draft.mode==='chat-delete'?'Remove chat':'Save name'}</button>
     {formError&&<p role="alert" className="a-danger">{formError}</p>}
    </form>}
</>;
}
export function WorkspaceManager({host}){
 const model=useNavigationController(host,'workspaces');
 const {state,act,allChats,workspace,workspaceCount,setDraft}=model;
 return <><NavigationEditor model={model}/>    {!allChats&&<WorkspaceExplorer state={state} act={act}/>}
    {!allChats&&workspace&&<div className="a-nav-workspace-path"><span className="a-workspace-full-path" title={workspace.path||'Project folder unavailable'}>{workspace.path||'Project folder unavailable'}</span><button type="button" className="a-icon" aria-label="Rename workspace" data-action="view.update" onClick={()=>setDraft({mode:'rename',id:workspace.id,name:workspace.name})}><Pencil/></button><button type="button" className="a-icon" aria-label="Remove workspace registration" disabled={workspaceCount<2} data-action="view.update" onClick={()=>setDraft({mode:'remove',id:workspace.id,name:workspace.name})}><Trash2/></button></div>}
</>;
}
export function ConversationList({host}){
 const model=useNavigationController(host,'chats');
 const {state,act,session,prefix,view,draft,form,saving,formError,history,refreshing,workspace,workspaceCount,allChats,chats,groups,changePage,setDraft,updateDraft,choose,submit}=model;
 return <>    <div className="a-nav-scope" role="group" aria-label="Chat view"><button type="button" aria-pressed={!allChats} data-action="view.update" onClick={()=>patch(act,{navChatScope:'workspace'})}><FolderOpen aria-hidden="true"/>Workspaces</button><button type="button" aria-pressed={allChats} data-action="view.update" onClick={()=>patch(act,{navChatScope:'all'})}><MessageCircle aria-hidden="true"/>All chats</button></div>
<NavigationEditor model={model}/>    <div className="a-nav-search"><Search/><input aria-label="Filter conversations" maxLength={500} type="search" value={view.navFilter||''} placeholder="Find chats · * ? patterns" data-action="view.update" onChange={e=>patch(act,{navFilter:e.target.value})}/></div>
    <div className="a-nav-eyebrow a-nav-conversations"><span className="a-nav-chat-heading">{allChats?'All chats':workspace?`Chats in ${workspace.name}`:'Conversations'}</span> <span>{chats.pending?'…':chats.total}</span><button type="button" className="a-icon" aria-label="Refresh workspaces and chats" title="CLI projects and chats appear automatically. Refresh now." data-action="history.refresh" disabled={refreshing} onClick={()=>act('history.refresh',{})}><RefreshCw className={refreshing?'a-progress-spinner':undefined}/></button></div>
    {chats.pending&&<p className="a-nav-history-status" role="status"><LoaderCircle className="a-progress-spinner"/>Loading conversations…</p>}
    {history.loading&&<p className="a-nav-history-status" role="status"><LoaderCircle className="a-progress-spinner"/>Finding projects and chats…</p>}
    {history.error&&<p className="a-nav-history-status a-danger" role="alert"><AlertCircle/><span>{history.error}</span></p>}
    <div className="a-nav-chats">{groups.filter(group=>group.items.length).map(group=><section className="a-nav-chat-group" aria-label={group.name+' chats'} key={group.name}><h4 className="a-nav-chat-group-title">{group.name}</h4>{group.items.map(chat=>draft.mode==='chat-rename'&&draft.id===chat.id?<ChatRename inputId={prefix+'-name'} key={chat.id} chat={chat} act={act} cancel={()=>setDraft({})}/>:<div className={`a-nav-chat ${chat.id===session?.id?'is-selected':''}`} key={chat.id} data-session-id={chat.id}>
     <button className="a-nav-chat-select" type="button" data-action="session.select" aria-current={chat.id===session?.id?'page':undefined} title={[chat.title,chat.description,allChats?chat.workspace:null].filter(Boolean).join(' — ')} onClick={()=>choose(chat.id)}><MessageCircle/><span className="a-nav-chat-label"><span>{chat.title||'Untitled conversation'}</span>{allChats&&<small className="a-nav-chat-workspace">{chat.workspace}</small>}</span><AttentionBadge state={state} count={state.attention?.sessions?.[chat.id]||0}/>{['running','working','starting'].includes(chat.status)&&<span className="a-nav-busy" aria-label="Working"/>}</button>
     <button type="button" className={`a-icon a-nav-chat-edit a-nav-chat-pin ${chat.pinned?'is-pinned':''}`} aria-label={`${chat.pinned?'Unpin':'Pin'} ${chat.title||'conversation'}`} aria-pressed={!!chat.pinned} title={chat.pinned?'Unpin chat':'Pin chat'} data-action="session.pin" onClick={()=>act('session.pin',{id:chat.id,pinned:!chat.pinned})}><Pin fill={chat.pinned?'currentColor':'none'}/></button>
     <button type="button" className="a-icon a-nav-chat-edit" aria-label={`Rename ${chat.title||'conversation'}`} data-action="view.update" onClick={()=>{setDraft({mode:'chat-rename',id:chat.id,name:chat.title||''})}}><Pencil/></button><button type="button" className="a-icon a-nav-chat-edit" aria-label={`Remove ${chat.title||'conversation'} from list`} data-action="view.update" onClick={()=>{setDraft({mode:'chat-delete',id:chat.id,name:chat.title||'conversation'})}}><Trash2/></button>
    </div>)}</section>)}{!chats.total&&!chats.pending&&!history.loading&&<p className="a-nav-empty">{view.navFilter?'No matching chats.':allChats?'Your conversations will appear here.':!workspace?'Choose a workspace to see its conversations.':'Your conversations will appear here.'}</p>}</div>
    {chats.pages>1&&<div className="a-nav-pagination"><span>{chats.start+1}–{chats.end} of {chats.total}</span><div><button type="button" className="a-link" data-action="view.update" aria-label="Show previous conversations" disabled={chats.index===0} onClick={()=>changePage(chats.index-1)}>Previous</button><button type="button" className="a-link" data-action="view.update" aria-label="Show more conversations" disabled={chats.index===chats.pages-1} onClick={()=>changePage(chats.index+1)}>More chats<ChevronRight/></button></div></div>}</>;
}
