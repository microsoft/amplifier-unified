import {WorkNavigationContext} from '../work-navigation';
import React,{useEffect,useRef,useState,useContext} from 'react';
import {MessageCircle,Search,Pencil,Trash2,X,Check,ChevronRight,Pin,RefreshCw,LoaderCircle,AlertCircle,ArrowLeft,ArrowUpRight,Folder,MoreHorizontal,ArrowUp,ArrowDown,Plus} from 'lucide-react';
import {chatPage,visibleWorkspaces,movePin} from '../chat-navigation';
import {NavigationRow,NavigationStatus,ActivityTime,CopyDetail,WorkspaceDetails,useActivityClock} from '../navigation-details';
import {activityFor,relativeActivity,compactParent,sessionIdentity} from '../navigation-presentation';
import {ReorderList} from '../reorder-list';
import '../sidebar-navigation.css';
import {WorkspaceForm} from '../workspace-setup';
import {WorkspaceExplorer} from '../workspace-explorer';
import {LibraryFilters} from '../conversation-library';
import {ChatDelete} from '../chat-delete';
import {PathField} from '../settings-ui';
import {useNavigation} from '../../../packages/shell-sdk/index.js';
const patch=(act,value)=>act('view.update',{patch:value});
export function ChatRename({chat,act,cancel,inputId="nav-workspace-name"}){
 const [name,setName]=useState(chat.title||''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const submitting=useRef(false),dirty=useRef(false);
 const naming=chat.naming?.status==='working';
 const busy=['starting','working','running','stopping'].includes(chat.status)||chat.configurationBusy;
 const automatic=chat.autoName??(chat.titleSource!=='manual'&&chat.nativeNameSource!=='manual');
 useEffect(()=>{if(!dirty.current)setName(chat.title||'')},[chat.title]);
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
 async function generate(){
  if(submitting.current||naming||busy||name!==(chat.title||''))return;
  submitting.current=true;setSaving(true);setError('');
  try{
   const result=await act('session.naming',{id:chat.id,regenerate:true});
   if(!result||result.accepted===false)throw Error(result?.error||'Could not generate a name. Your current name is kept.');
  }catch(error){setError(error.message)}finally{submitting.current=false;setSaving(false)}
 }
 return <form aria-busy={saving||naming} className="a-nav-chat a-nav-chat-rename" data-session-id={chat.id} onSubmit={submit}>
  <input id={inputId} maxLength={200} aria-label={`New name for ${chat.title||'conversation'}`} value={name} disabled={saving} required autoFocus onChange={e=>{dirty.current=true;setName(e.target.value);setError('')}}/>
  <button type="submit" className="a-icon a-nav-chat-edit" aria-label="Save conversation name" disabled={saving||!name.trim()} data-action="session.rename"><Check/></button>
  <button type="button" className="a-icon a-nav-chat-edit" aria-label="Cancel conversation rename" disabled={saving} data-action="view.update" onClick={cancel}><X/></button>
  <button type="button" className="a-link" data-action="session.naming" disabled={saving||naming||busy||name!==(chat.title||'')} onClick={generate}><RefreshCw/>{naming?'Naming…':'Auto name now'}</button>
  <small className="a-nav-name-help">Generate one name. Future automatic naming stays {automatic?'on':'off'}.{busy?' Wait for current work to finish.':name!==(chat.title||'')?' Save or cancel your edit first.':''}</small>
  {naming&&<small role="status">Generating chat name…</small>}
  {(error||chat.naming?.error)&&<small role="alert" className="a-danger">{error||chat.naming.error}</small>}
 </form>;
}
export function useNavigationController(host,kind){
 const state=useNavigation(React,host),act=host.dispatch,prefix=host.instanceId==='workspaces'?'nav-workspace':'nav-'+host.instanceId;
 const view=state.view||{},savedDraft=view.workspaceDraft||{},draft=(kind==='chats'?savedDraft.mode?.startsWith('chat-'):!savedDraft.mode?.startsWith('chat-'))?savedDraft:{};
 const form=useRef(null),draftRef=useRef(draft),draftVersion=useRef(0),submitting=useRef(false);
 const [saving,setSaving]=useState(false),[formError,setFormError]=useState('');
 useEffect(()=>{draftRef.current=draft},[draft]);
 const history=state.sharedHistory||{},refreshing=!!(history.loading||history.refreshing);
 const workspaces=visibleWorkspaces(state),workspace=workspaces.find(w=>w.id===state.selectedWorkspaceId);
 const workspaceCount=state.library?.bounded?state.library.workspaceCount:workspaces.length;
 const allChats=view.navChatScope==='all';
 const setDraft=value=>{draftVersion.current++;draftRef.current=value;setFormError('');patch(act,{workspaceDraft:value})};
 const updateDraft=value=>setDraft({...draft,...value});
 const choose=id=>act('session.select',{id});

 const submit=async e=>{
  e.preventDefault();if(submitting.current)return;const version=draftVersion.current;submitting.current=true;setSaving(true);setFormError('');
  try{
   let result;
   if(draft.mode==='add')result=await act('workspace.create',{path:draft.path||'',...(draft.name?.trim()?{name:draft.name.trim()}:{})});
   else if(draft.mode==='rename')result=await act('workspace.rename',{id:draft.id,name:draft.name||''});
   else if(draft.mode==='chat-rename')result=await act('session.rename',{id:draft.id,title:draft.name||''});
   else if(draft.mode==='remove')result=await act('workspace.remove',{id:draft.id});
   if(result&&result.accepted!==false){if(draftVersion.current===version)setDraft({});}
   else setFormError(result?.error||'Could not save. Check the folder or name and try again.');
  }catch(error){setFormError(error.message||'Could not save. Please try again.')}
  finally{submitting.current=false;setSaving(false)}
 };
 useEffect(()=>{if(!draft.mode)return;form.current?.scrollIntoView?.({block:'nearest'});form.current?.querySelector?.('input')?.focus()},[draft.mode,draft.id]);
 return {state,act,prefix,view,draft,form,saving,formError,history,refreshing,workspace,workspaceCount,allChats,setDraft,updateDraft,choose,submit};
}
export function NavigationEditor({model}){
 const {state,act,prefix,draft,form,saving,formError,setDraft,updateDraft,submit}=model;
 if(draft.mode==='add')return <WorkspaceForm state={state} act={act} onDone={()=>setDraft({})} onCancel={()=>setDraft({})}/>;
 return <>{draft.mode==='chat-delete'&&<div className="a-nav-form"><strong>Delete chat?</strong><ChatDelete key={draft.id} id={draft.id} act={act} cancel={()=>setDraft({})}/></div>}    {draft.mode&&draft.mode!=='chat-rename'&&draft.mode!=='chat-delete'&&<form ref={form} className="a-nav-form" aria-busy={saving} onSubmit={submit}>
     <div className="a-nav-form-title"><strong>{draft.mode==='add'?'New workspace':draft.mode==='rename'?'Rename workspace':draft.mode==='chat-rename'?'Rename chat':'Remove workspace?'}</strong><button className="a-icon" type="button" aria-label="Cancel navigation edit" data-action="view.update" onClick={()=>setDraft({})}><X/></button></div>
     {draft.mode==='add'&&<><p>Choose an existing folder, or enter a path to create one. Your first chat opens here.</p><label htmlFor={prefix+'-path'}>Folder</label><PathField id={prefix+'-path'} value={draft.path||''} onChange={path=>updateDraft({path})} directory state={state} act={act} placeholder="~/Projects/my-project"/></>}
     {['add','rename','chat-rename'].includes(draft.mode)?<><label htmlFor={prefix+'-name'}>{draft.mode==='add'?'Name (optional)':'Name'}</label><input id={prefix+'-name'} maxLength={200} value={draft.name||''} required={draft.mode!=='add'} data-action="view.update" onChange={e=>updateDraft({name:e.target.value})}/></>:<p>Remove {draft.name} from this list? Its files and chats will stay on disk.</p>}
     <button type="submit" disabled={saving||(draft.mode==='add'&&!draft.path?.trim())} className={draft.mode==='remove'?'a-soft a-danger':'a-primary'} data-action={draft.mode==='add'?'workspace.create':draft.mode==='rename'?'workspace.rename':draft.mode==='remove'?'workspace.remove':'session.rename'}>{saving?'Saving…':draft.mode==='add'?'Create workspace':draft.mode==='remove'?'Remove registration':'Save name'}</button>
     {formError&&<p role="alert" className="a-danger">{formError}</p>}
    </form>}
</>;
}
export function WorkspaceManager({host,paired=false}){
 const model=useNavigationController(host,'workspaces');
 const {state,act,allChats,workspace,workspaceCount,setDraft}=model;
 if(paired)return <NavigationEditor model={model}/>;
 return <><NavigationEditor model={model}/>    {!allChats&&<WorkspaceExplorer state={state} act={act}/>}
    {!allChats&&workspace&&<div className="a-nav-workspace-path"><span className="a-workspace-full-path" title={workspace.path||'Project folder unavailable'}>{workspace.path||'Project folder unavailable'}</span><button type="button" className="a-icon" aria-label="Rename workspace" data-action="view.update" onClick={()=>setDraft({mode:'rename',id:workspace.id,name:workspace.name})}><Pencil/></button><button type="button" className="a-icon" aria-label="Remove workspace registration" disabled={workspaceCount<2} data-action="view.update" onClick={()=>setDraft({mode:'remove',id:workspace.id,name:workspace.name})}><Trash2/></button></div>}
</>;
}
function PairedWorkspaceBrowser({host,onOpen}){
 const model=useNavigationController(host,'workspaces');
 return <WorkspaceExplorer state={model.state} act={model.act} onOpen={onOpen} onEdit={model.setDraft} heading={false} previewLimit={8}/>;
}
export function ChatDetails({chat,model,now,close}){
 const {state,act,draft,setDraft,choose,prefix}=model,activity=activityFor(chat,state);
 const title=chat.title||'Untitled conversation',managed=chat.location?.kind==='managed';
 const pins=state.pinnedSessionIds||[],pinIndex=pins.indexOf(chat.id);
 const [pinError,setPinError]=useState('');
 const move=async direction=>{try{setPinError('');await act('session.pinOrder',{ids:movePin(pins,chat.id,pins[pinIndex+direction])})}catch(error){setPinError(error.message)}};
 return <><div className="a-navigation-detail-heading"><MessageCircle/>Chat details</div><h3>{title}</h3>
  <div className="a-navigation-detail-status"><NavigationStatus activity={activity}/><strong>{activity.label}</strong></div>
  <dl><dt>Last activity</dt><dd>{relativeActivity(chat.recentActivityAt,now).long}</dd><dt>Workspace</dt><dd>{managed?'No workspace':chat.workspaceName||chat.workspace?.split(/[\\/]/).filter(Boolean).at(-1)}</dd></dl>
  <CopyDetail label={managed?"Chat files":"Full workspace path"} value={chat.workspace||'Unavailable'}/>
  <CopyDetail label="Session ID" value={sessionIdentity(chat)}/>
  <p className="a-caption">{managed?'Files stored in this chat’s managed folder.':'Saved in this folder’s native Amplifier history.'}</p>
  {draft.mode==='chat-rename'&&draft.id===chat.id?<ChatRename inputId={prefix+'-name'} chat={chat} act={act} cancel={()=>setDraft({})}/>:<div className="a-navigation-actions">
   <button type="button" className="a-link" data-action="session.select" onClick={()=>{close();choose(chat.id)}}><ArrowUpRight/>Open chat</button>
   <button type="button" aria-label={`${chat.pinned?'Unpin':'Pin'} ${title}`} aria-pressed={!!chat.pinned} data-action="session.pin" onClick={()=>act('session.pin',{id:chat.id,pinned:!chat.pinned})}><Pin/>{chat.pinned?'Unpin':'Pin'}</button>
   {chat.pinned&&<><button type="button" aria-label={'Move '+title+' up'} data-action="session.pinOrder" disabled={pinIndex<1} onClick={()=>move(-1)}><ArrowUp/>Move up</button><button type="button" aria-label={'Move '+title+' down'} data-action="session.pinOrder" disabled={pinIndex<0||pinIndex===pins.length-1} onClick={()=>move(1)}><ArrowDown/>Move down</button>{pinError&&<span role="alert">{pinError}</span>}</>}
   <button type="button" aria-label={'Rename '+title} data-action="view.update" onClick={()=>setDraft({mode:'chat-rename',id:chat.id,name:title})}><Pencil/>Rename</button>
   <button type="button" aria-label={(chat.archived?'Restore ':'Archive ')+title} data-action={chat.archived?'session.restore':'session.archive'} onClick={()=>act(chat.archived?'session.restore':'session.archive',{id:chat.id})}>{chat.archived?'Restore':'Archive'}</button>
   {managed&&!chat.parentId&&<button type="button" className="a-danger" aria-label={'Delete '+title} data-action="view.update" onClick={()=>{close();setDraft({mode:'chat-delete',id:chat.id,name:title})}}><Trash2/>Delete</button>}
  </div>}
 </>;
}
function SidebarSection({id,title,count,model,actions,children}){
 const contentId=React.useId(),collapsed=model.view.navSectionsCollapsed||[],closed=collapsed.includes(id);
 return <section className="a-sidebar-section" aria-label={title==='Pinned'?'Pinned chats':title==='Recent'?'Recent chats':title} data-sidebar-section={id}>
  <div className="a-sidebar-section-heading"><button type="button" aria-expanded={!closed} aria-controls={contentId} data-action="view.update" onClick={()=>patch(model.act,{navSectionsCollapsed:closed?collapsed.filter(key=>key!==id):[...collapsed,id]})}><span>{title}</span><ChevronRight className={closed?'':'is-open'}/>{count!=null&&<small>{count}</small>}</button>{actions}</div>
  <div id={contentId} hidden={closed}>{children}</div>
 </section>;
}
function ChatRow({chat,model,now,showLocation=true,handle,floating=false}){
 const {state,choose}=model,title=chat.title||'Untitled conversation',activity=activityFor(chat,state);
 const content=<>{handle}<button className="a-nav-chat-select" type="button" data-navigation-select data-action="session.select" aria-current={chat.id===state.selectedSessionId?'page':undefined} aria-label={title} onClick={()=>choose(chat.id)}><NavigationStatus activity={activity}/><span className="a-nav-chat-label"><span>{title}</span><small className="a-nav-chat-workspace" title={showLocation?chat.workspace:undefined}>{showLocation?(chat.workspaceLabel||chat.workspace):activity.label}</small></span><ActivityTime at={chat.recentActivityAt} now={now}/></button></>;
 const className='a-nav-chat '+(chat.id===state.selectedSessionId?'is-selected':'');
 // The drag preview uses the exact same contents without interactive flyouts.
 if(floating)return <div className={className}>{content}<MoreHorizontal className="a-navigation-more"/></div>;
 return <NavigationRow className={className} data-session-id={chat.id} label={title} details={({close})=><ChatDetails chat={chat} model={model} now={now} close={close}/>}>{content}</NavigationRow>;
}
function ChatPagination({page,onChange,label='conversations'}){
 if(page.pages<2)return null;
 return <div className="a-nav-pagination"><span>{page.start+1}–{page.end} of {page.total}</span><div><button type="button" className="a-link" data-action="view.update" aria-label={'Show previous '+label} disabled={page.index===0} onClick={()=>onChange(page.index-1)}>Previous</button><button type="button" className="a-link" data-action="view.update" aria-label={'Show more '+label} disabled={page.index===page.pages-1} onClick={()=>onChange(page.index+1)}>More<ChevronRight/></button></div></div>;
}
export function PinnedChats({page,model,now}){
 const [error,setError]=useState('');
 const items=page.items.map(chat=>({...chat,label:chat.title||'Untitled conversation'})),visible=items.map(chat=>chat.id);
 const reorder=async(next,{id,to})=>{
  setError('');
  try{
   const response=await model.act('session.pinOrder',{ids:movePin(model.state.pinnedSessionIds||[],id,visible[to])});
   const receipt=response?.result?.accepted!==undefined?response.result:response;
   if(receipt?.accepted===false)throw Error(receipt.error||'Could not reorder pins.');
  }catch(err){setError(err.message||'Could not reorder pins. Please try again.');throw err}
 };
 return <><ReorderList items={items} ids={visible} onChange={reorder} className="a-pinned-chats" floatingClassName="a-pinned-drag" action="session.pinOrder" renderItem={(chat,{handle,floating})=><ChatRow chat={chat} model={model} now={now} handle={handle} floating={floating}/>}/>
  {error&&<p role="alert" className="a-danger">{error}</p>}
  {!page.total&&<p className="a-nav-empty">Pin a chat from its actions menu to keep it here.</p>}
  <ChatPagination page={page} label="pinned chats" onChange={index=>patch(model.act,{navPinnedPage:index})}/>
 </>;
}
export function ChatList({page,model,view,viewAct,now,showLocation}){
 const counts=page.activityCounts||{};
 return <>
  <div className="a-nav-search"><Search/><input aria-label="Filter conversations" maxLength={500} type="search" value={view.navFilter||''} placeholder={showLocation?'Find chats or paths · * ?':'Find chats · * ? patterns'} data-action="view.update" onChange={e=>patch(viewAct,{navFilter:e.target.value})}/></div>
  <details className="a-sidebar-filters"><summary>Filters{view.navFilter||view.navArchive&&view.navArchive!=='active'||view.navStatusFilter&&view.navStatusFilter!=='all'||view.navLocationFilter==='managed'?' · active':''}</summary>
   <LibraryFilters state={{...model.state,view}} act={viewAct}/>
   <label className="a-managed-chat-filter">Sort<select aria-label="Sort conversations" value={view.navSort||'activity'} data-action="view.update" onChange={e=>patch(viewAct,{navSort:e.target.value})}><option value="activity">Recent activity</option><option value="created">Newest created</option><option value="name">Name</option></select></label>
   {showLocation&&<label className="a-managed-chat-filter">Location<select aria-label="Filter chats by location" value={view.navLocationFilter||'all'} data-action="view.update" onChange={e=>patch(viewAct,{navLocationFilter:e.target.value})}><option value="all">All locations</option><option value="managed">No workspace</option></select></label>}
   <div className="a-navigation-filters" role="group" aria-label="Conversation activity filters">
    <button type="button" aria-pressed={view.navStatusFilter==='attention'} data-action="view.update" onClick={()=>patch(viewAct,{navStatusFilter:view.navStatusFilter==='attention'?'all':'attention'})}><AlertCircle/>{counts.attention??0} need attention</button>
    <button type="button" aria-pressed={view.navStatusFilter==='working'} data-action="view.update" onClick={()=>patch(viewAct,{navStatusFilter:view.navStatusFilter==='working'?'all':'working'})}><LoaderCircle/>{counts.working??0} working</button>
    {view.navStatusFilter&&view.navStatusFilter!=='all'&&<button type="button" className="a-link" data-action="view.update" onClick={()=>patch(viewAct,{navStatusFilter:'all'})}>Clear</button>}
   </div>
  </details>
  {page.pending&&<p role="status">Loading conversations…</p>}
  <div className="a-nav-chats">{page.items.map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now} showLocation={showLocation}/>)}</div>
  {!page.total&&!page.pending&&<p className="a-nav-empty">{view.navFilter||view.navStatusFilter&&view.navStatusFilter!=='all'?'No matching chats.':'Your conversations will appear here.'}</p>}
  <ChatPagination page={page} onChange={index=>patch(viewAct,{navChatPage:{...page.scope,index}})}/>
 </>;
}
const HOME_FILTERS={navArchive:'active',navCollection:null,navFilter:'',navStatusFilter:'all',navLocationFilter:'all',navSort:'activity'};
function LegacyConversationList({host,workspaceHost}){
 const model=useNavigationController(host,'chats'),{state,act,view,workspace,history,refreshing}=model;
 const workspaceState=useNavigation(React,workspaceHost||host),now=useActivityClock(),workspaceAct=workspaceHost?.dispatch||act;
 const [creating,setCreating]=useState(false),createButton=useRef(null);
 const closeCreate=()=>{setCreating(false);requestAnimationFrame(()=>createButton.current?.focus())};
 const sidebar=state.sidebarNavigation;
 const homeView={...HOME_FILTERS,navChatScope:sidebar?.pinned?.scope.mode||'all',navPinnedPage:view.navPinnedPage};
 const recentView={...HOME_FILTERS,...sidebar?.recentView,...view.navRecentView};
 const pins=chatPage({...state,view:homeView},workspace,{section:'pinned'});
 const recent=chatPage({...state,view:{...recentView,navChatScope:homeView.navChatScope}},workspace,{section:'recent'});
 const workspaceChats=chatPage({...state,view:{...view,navChatScope:'workspace'}},workspace,{section:'workspace'});
 const recentAct=(name,args)=>{
  if(name!=='view.update')return act(name,args);
  const next={...recentView,...args.patch};
  if(!Object.hasOwn(args.patch,'navChatPage'))delete next.navChatPage;
  return patch(act,{navRecentView:next});
 };
 const browsing=view.navWorkspaceList!==false||!workspace;
 const browseWorkspaces=()=>patch(act,{navWorkspaceList:true});
 const openWorkspace=()=>patch(act,{navWorkspaceList:false,navChatScope:'workspace',navFilter:'',navStatusFilter:'all',navArchive:'active',navLocationFilter:'all'});
 const selectedWorkspace=workspaceState.workspaceExplorer?.selected;
 return <div className="a-sidebar-navigation">
  <NavigationEditor model={model}/>
  <SidebarSection id="pinned" title="Pinned" count={pins.total} model={model}><PinnedChats page={pins} model={model} now={now}/></SidebarSection>
  <SidebarSection id="workspaces" title="Workspaces" count={workspaceState.workspaceExplorer?.totalWorkspaces} model={model} actions={<button ref={createButton} type="button" className="a-icon" aria-label="New workspace" data-action="view.update" onClick={()=>{setCreating(true);patch(act,{navSectionsCollapsed:(view.navSectionsCollapsed||[]).filter(key=>key!=='workspaces')})}}><Plus/></button>}>
   {creating&&<WorkspaceForm state={workspaceState} act={workspaceAct} onDone={closeCreate} onCancel={closeCreate}/>}
   {browsing?(workspaceHost?<PairedWorkspaceBrowser host={workspaceHost} onOpen={openWorkspace}/>:<WorkspaceExplorer state={state} act={act} onOpen={openWorkspace} onEdit={model.setDraft} heading={false} previewLimit={8}/>):<div className="a-workspace-chat-view" aria-label={'Chats in '+workspace.name}>
    <button type="button" className="a-navigation-back" data-action="view.update" onClick={browseWorkspaces}><ArrowLeft/>All workspaces</button>
    <NavigationRow className="a-navigation-workspace" label={workspace.name} details={({close})=><WorkspaceDetails row={selectedWorkspace||{...workspace,chatCount:workspaceChats.total}} now={now} actions={<><button type="button" data-action="view.update" onClick={()=>{close();workspaceAct('view.update',{patch:{workspaceDraft:{mode:'rename',id:workspace.id,name:workspace.name}}})}}><Pencil/>Rename</button><button type="button" className="a-danger" disabled={workspaceState.library?.workspaceCount<2} data-action="view.update" onClick={()=>{close();workspaceAct('view.update',{patch:{workspaceDraft:{mode:'remove',id:workspace.id,name:workspace.name}}})}}><Trash2/>Remove registration</button></>}/>}><Folder/><div><strong>{workspace.name}</strong><small title={workspace.path}>{compactParent(workspace.path)}</small></div></NavigationRow>
    <div className="a-sidebar-workspace-actions"><span>{workspaceChats.total} chats</span><button type="button" className="a-link" aria-label={'New chat in '+workspace.name} data-action="session.draft" onClick={()=>act('session.draft',{workspace:workspace.path})}><Plus/>New chat</button></div>
    <ChatList page={workspaceChats} model={model} view={view} viewAct={act} now={now} showLocation={false}/>
   </div>}
  </SidebarSection>
  <SidebarSection id="recent" title="Recent" count={recent.total} model={model} actions={<button type="button" className="a-icon" aria-label="Refresh workspaces and chats" data-action="history.refresh" disabled={refreshing} onClick={()=>act('history.refresh',{})}><RefreshCw className={refreshing?'a-progress-spinner':undefined}/></button>}>
   <ChatList page={recent} model={model} view={recentView} viewAct={recentAct} now={now} showLocation/>
  </SidebarSection>
  {history.loading&&<p role="status">Finding existing chats…</p>}{history.error&&<p role="alert" className="a-danger">{history.error}</p>}
  {!!history.issueCount&&<p className="a-caption" role="status">Some saved folders or chats need attention.</p>}
 </div>;
}

function QuietSidebar({host,workspaceHost,navigation}){
 const model=useNavigationController(host,'chats'),now=useActivityClock();
 const workspaceState=useNavigation(React,workspaceHost||host);
 const pins=model.state.sidebarNavigation?.pinned||{items:[],total:0,pages:1};
 const recent=model.state.recentShortcuts||[];
 return <div className="a-sidebar-navigation a-quiet-sidebar">
  <NavigationEditor model={model}/>
  <button type="button" className="a-nav-search-launch" data-action="view.update" onClick={()=>navigation.browse('chats')}><Search/>Search chats</button>
  <SidebarSection id="pinned" title="Pinned" model={model}><PinnedChats page={pins} model={model} now={now}/></SidebarSection>
  <SidebarSection id="workspaces" title="Workspaces" model={model} actions={<button type="button" className="a-icon" aria-label="New workspace" onClick={()=>navigation.create()}><Plus/></button>}>
   <WorkspaceExplorer state={workspaceState} act={workspaceHost?.dispatch||model.act} compact heading={false} onSelect={row=>navigation.browse('workspace',row.workspaceId)}/>
   <button type="button" className="a-sidebar-all a-link" data-action="view.update" onClick={()=>navigation.browse('workspaces')}>All workspaces<ChevronRight/></button>
  </SidebarSection>
  <SidebarSection id="recent" title="Recent" model={model} actions={<button type="button" className="a-icon" aria-label="Refresh workspaces and chats" data-action="history.refresh" disabled={model.refreshing} onClick={()=>model.act('history.refresh',{})}><RefreshCw className={model.refreshing?'a-progress-spinner':undefined}/></button>}>
   {recent.map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now}/>)}
   {!recent.length&&<p className="a-nav-empty">Your recent chats appear here.</p>}
   <button type="button" className="a-sidebar-all a-link" data-action="view.update" onClick={()=>navigation.browse('chats')}>All chats<ChevronRight/></button>
  </SidebarSection>
  {model.history.loading&&<p className="a-caption" role="status">Finding existing chats…</p>}{!!model.history.issueCount&&<p className="a-caption" role="status">Some saved folders or chats need attention. <button className="a-link" onClick={()=>navigation.browse('chats')}>Review chats</button></p>}
  {model.history.error&&<p role="alert">{model.history.error}</p>}
 </div>;
}
export function ConversationList(props){
 const navigation=useContext(WorkNavigationContext);
 return navigation?<QuietSidebar {...props} navigation={navigation}/>:<LegacyConversationList {...props}/>;
}
