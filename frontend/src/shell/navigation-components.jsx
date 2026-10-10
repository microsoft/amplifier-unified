import {useChatTitle} from '../use-chat-title';
import {WorkNavigationContext} from '../work-navigation';
import React,{useEffect,useLayoutEffect,useRef,useState,useContext} from 'react';
import {MessageCircle,Search,Pencil,Trash2,X,Check,ChevronRight,Pin,RefreshCw,LoaderCircle,AlertCircle,ArrowLeft,ArrowUpRight,Folder,MoreHorizontal,ArrowUp,ArrowDown,Plus} from 'lucide-react';
import {chatPage,visibleWorkspaces,movePin,recentLimit,recentPageMatches} from '../chat-navigation';
import {followLaterHistory} from '../history-scroll';
import {request} from '../api';
import {NavigationRow,NavigationStatus,ActivityTime,CopyDetail,WorkspaceDetails,useActivityClock} from '../navigation-details';
import {activityFor,relativeActivity,compactParent,sessionIdentity} from '../navigation-presentation';
import {ReorderList} from '../reorder-list';
import '../sidebar-navigation.css';
import {WorkspaceForm} from '../workspace-setup';
import {WorkspaceExplorer} from '../workspace-explorer';
import {LibraryFilters} from '../conversation-library';
import {ChatDelete} from '../chat-delete';
import {PathField} from '../settings-ui';
import {readItems} from '../attention';
import {useNavigation} from '../../../packages/shell-sdk/index.js';
const patch=(act,value)=>act('view.update',{patch:value});
export function ChatRename({chat,act,cancel,inputId="nav-workspace-name"}){
 const [name,setName]=useState(chat.title||''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const submitting=useRef(false),dirty=useRef(false);
 const naming=chat.naming?.status==='working';
 const busy=chat.configurationBusy;
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
  <input id={inputId} maxLength={200} aria-label={`New name for ${chat.title||'conversation'}`} value={name} disabled={saving} required autoFocus onChange={e=>{dirty.current=e.target.value!==(chat.title||'');setName(e.target.value);setError('')}}/>
  <button type="submit" className="a-icon a-nav-chat-edit" aria-label="Save conversation name" disabled={saving||!name.trim()} data-action="session.rename"><Check/></button>
  <button type="button" className="a-icon a-nav-chat-edit" aria-label="Cancel conversation rename" disabled={saving} data-action="view.update" onClick={cancel}><X/></button>
  <button type="button" className="a-link" data-action="session.naming" disabled={saving||naming||busy||name!==(chat.title||'')} onClick={generate}><RefreshCw/>{naming?'Naming…':'Auto name now'}</button>
  <small className="a-nav-name-help">Generate one name. Future automatic naming stays {automatic?'on':'off'}.{busy?' Finish changing the conversation configuration first.':name!==(chat.title||'')?' Save or cancel your edit first.':''}</small>
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
 const [pinError,setPinError]=useState(''),[reviewError,setReviewError]=useState(''),[reviewing,setReviewing]=useState(false);
 const errorItem=state.attention?.items?.find(item=>item.id==='session:'+chat.id&&item.sessionId===chat.id&&!item.read);
 const review=async()=>{if(reviewing||!errorItem)return;setReviewing(true);setReviewError('');try{await readItems(act,[errorItem])}catch(error){setReviewError(error.message||'Could not mark this error reviewed.')}finally{setReviewing(false)}};
 const move=async direction=>{try{setPinError('');await act('session.pinOrder',{ids:movePin(pins,chat.id,pins[pinIndex+direction])})}catch(error){setPinError(error.message)}};
 return <><div className="a-navigation-detail-heading"><MessageCircle/>Chat details</div><h3>{title}</h3>
  {chat.titlePreviewSource==='first-message'&&<p className="a-caption">Preview from the first message. Rename this chat to give it a permanent name.</p>}
  <div className="a-navigation-detail-status"><NavigationStatus activity={activity}/><strong>{activity.label}</strong></div>
  <dl><dt>Last activity</dt><dd>{relativeActivity(chat.recentActivityAt,now).long}</dd><dt>Workspace</dt><dd>{managed?'No workspace':chat.workspaceName||chat.workspace?.split(/[\\/]/).filter(Boolean).at(-1)}</dd></dl>
  <CopyDetail label={managed?"Chat files":"Full workspace path"} value={chat.workspace||'Unavailable'}/>
  <CopyDetail label="Session ID" value={sessionIdentity(chat)}/>
  <p className="a-caption">{managed?'Files stored in this chat’s managed folder.':'Discovered in this folder’s Amplifier history. Chats started from the CLI or other tools can appear here too.'}</p>
  {draft.mode==='chat-rename'&&draft.id===chat.id?<ChatRename inputId={prefix+'-name'} chat={chat} act={act} cancel={()=>setDraft({})}/>:<div className="a-navigation-actions">
   <button type="button" className="a-link" data-action="session.select" onClick={()=>{close();choose(chat.id)}}><ArrowUpRight/>Open chat</button>
   {errorItem&&<button type="button" className="a-link" data-action="attention.read" disabled={reviewing} onClick={review}>{reviewing?'Marking reviewed…':'Mark error reviewed'}</button>}
   {reviewError&&<span role="alert">{reviewError}</span>}
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
 const preview=useChatTitle(chat);
 chat={...chat,title:preview.title,titlePreviewSource:preview.source};
 const {state,choose}=model,title=chat.title,activity=activityFor(chat,state);
 const revealTitle=()=>{
  const viewport=preview.ref.current,text=viewport?.firstElementChild;
  if(!text)return;
  const distance=Math.max(0,text.scrollWidth-viewport.clientWidth);
  viewport.dataset.overflow=String(distance>1);
  viewport.style.setProperty('--title-overflow',`${-distance}px`);
  viewport.style.setProperty('--title-duration',`${Math.max(3,distance/28)}s`);
 };
 const content=<>{handle}<button className="a-nav-chat-select" type="button" data-tooltip="off" onPointerEnter={revealTitle} onFocus={revealTitle} data-navigation-select data-action="session.select" aria-current={chat.id===state.selectedSessionId?'page':undefined} aria-label={title} onClick={()=>choose(chat.id)}><NavigationStatus activity={activity}/><span className="a-nav-chat-label"><span ref={preview.ref} className="a-nav-chat-title"><span>{title}</span></span><small className="a-nav-chat-workspace" title={showLocation?chat.workspace:undefined}>{showLocation?(chat.workspaceLabel||chat.workspace):activity.label}</small></span><ActivityTime at={chat.recentActivityAt} now={now}/></button></>;
 const className='a-nav-chat '+(chat.id===state.selectedSessionId?'is-selected':'');
 // The drag preview uses the exact same contents without interactive flyouts.
 if(floating)return <div className={className}>{content}<MoreHorizontal className="a-navigation-more"/></div>;
 return <NavigationRow className={className} data-session-id={chat.id} label={title} details={({close})=><ChatDetails chat={chat} model={model} now={now} close={close}/>}>{content}</NavigationRow>;
}
function ChatPagination({page,onChange,label='conversations'}){
 if(page.pages<2)return null;
 return <div className="a-nav-pagination"><span>{page.start+1}–{page.end} of {page.total}</span><div><button type="button" className="a-link" data-action="view.update" aria-label={'Show previous '+label} disabled={page.index===0} onClick={()=>onChange(page.index-1)}>Previous</button><button type="button" className="a-link" data-action="view.update" aria-label={'Show more '+label} disabled={page.index===page.pages-1} onClick={()=>onChange(page.index+1)}>More<ChevronRight/></button></div></div>;
}

export function useRecentShortcuts(host,state,act){
 const initial={limit:recentLimit(state.recentNavigation?.scope?.limit??state.view?.navRecentLimit),showAgentCreated:state.view?.navShowAgentCreated===true};
 const [settings,setSettings]=useState(initial),[page,setPage]=useState(state.recentNavigation||{items:[],total:0,remaining:0,limit:initial.limit});
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const accepted=useRef(page),intent=useRef(null),sequence=useRef(0),mounted=useRef(true),waiting=useRef(false),uncertain=useRef(false);
 const latest=useRef(null);latest.current={host,state,act};
 const scopeFor=current=>{
  const mode=current.sidebarNavigation?.pinned?.scope?.mode||'all';
  return {...(current.recentScope||{mode,workspaceId:mode==='all'?null:current.selectedWorkspaceId??null,filter:''}),
   ...(current.recentScope?{
    clientId:latest.current.host.clientId??current.recentScope.clientId,
    instanceId:latest.current.host.instanceId??current.recentScope.instanceId,
    generation:current.generation??current.recentScope.generation
   }:{}),
   selectedSessionId:current.selectedSessionId??null};
 };
 const desired=(value,current=latest.current.state)=>({...scopeFor(current),...value});
 const newer=candidate=>(candidate?.scope?.viewRevision??0)>=(accepted.current?.scope?.viewRevision??0)
  &&((candidate?.scope?.viewRevision??0)>(accepted.current?.scope?.viewRevision??0)
   ||(candidate?.dataRevision??0)>=(accepted.current?.dataRevision??0));
 const accept=candidate=>{
  candidate={...candidate,items:[...new Map(candidate.items.map(row=>[row.id,row])).values()]};
  accepted.current=candidate;uncertain.current=false;setPage(candidate);
  setSettings({limit:candidate.scope.limit,showAgentCreated:candidate.scope.showAgentCreated});
 };
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;sequence.current++}},[]);
 useLayoutEffect(()=>{
  const candidate=state.recentNavigation;
  if(waiting.current||!candidate||!newer(candidate))return;
  const pending=intent.current,wanted=pending?.wanted||{
   limit:recentLimit(state.view?.navRecentLimit??candidate.scope.limit),
   showAgentCreated:state.view?.navShowAgentCreated===true
  };
  if(recentPageMatches(candidate,desired(wanted))
   &&(!pending||Object.entries(pending.scope).every(([key,value])=>candidate.scope[key]===value)
    &&candidate.scope.viewRevision>=pending.minRevision)){
   accept(candidate);
   if(intent.current){intent.current=null;setError('')}
  }
 },[state.recentNavigation,state.recentScope,state.selectedSessionId,busy]);
 const change=async value=>{
  // This ref is set synchronously, before React renders or dispatch can await.
  // Scroll, fallback, toggle and Retry must all share the same flight.
  if(waiting.current||uncertain.current)return;
  const wanted={limit:accepted.current.limit,showAgentCreated:accepted.current.scope?.showAgentCreated,...value},ticket=++sequence.current;
  const scope=scopeFor(latest.current.state),minRevision=(accepted.current.scope?.viewRevision??0)+1;
  intent.current={wanted,scope,minRevision};waiting.current=true;setSettings(wanted);setBusy(true);setError('');
  try{
   const receipt=await patch(act,{navRecentLimit:wanted.limit,navShowAgentCreated:wanted.showAgentCreated});
   if(receipt?.accepted===false)throw Error(receipt.error||'Recent settings were not saved.');
   if(!mounted.current||ticket!==sequence.current)return;
   const current=latest.current.host.getSnapshot(),candidate=current.recentNavigation;
   if(!recentPageMatches(candidate,desired(wanted,current))
    ||!Object.entries(scope).every(([key,value])=>candidate.scope[key]===value)
    ||!(candidate.scope.viewRevision>=minRevision)||!newer(candidate))
    throw Error('Recent could not be refreshed. The previous chats are kept.');
   accept(candidate);intent.current=null;
  }catch(error){if(mounted.current&&ticket===sequence.current){uncertain.current=true;setError(error.message||'Recent could not be refreshed. The previous chats are kept.')}}
  finally{if(mounted.current&&ticket===sequence.current){waiting.current=false;setBusy(false)}}
 };
 const retry=async()=>{
  // Reconcile only. Never replay an uncertain view write (or task/input work).
  if(waiting.current)return;
  const ticket=++sequence.current;waiting.current=true;setBusy(true);
  const scope=scopeFor(latest.current.state);
  try{
   const receipt=await request('/api/actions',{method:'POST',body:{id:crypto.randomUUID(),action:'shell.query',args:{clientId:host.clientId,instanceId:host.instanceId}}});
   if(!mounted.current||ticket!==sequence.current)return;
   const current=latest.current.host.getSnapshot(),read=receipt.result?.recentNavigation,live=current.recentNavigation;
   // A shell snapshot received during this read may already be newer. Prefer
   // that evidence, never restore the delayed response over it.
   const after=(a,b)=>(a?.scope?.viewRevision??0)>(b?.scope?.viewRevision??0)
    ||(a?.scope?.viewRevision??0)===(b?.scope?.viewRevision??0)&&(a?.dataRevision??0)>(b?.dataRevision??0);
   const savedSettings=(value,snapshot)=>({
    limit:snapshot?.view?.navRecentLimit??value?.scope?.limit,
    showAgentCreated:snapshot?.view&&Object.hasOwn(snapshot.view,'navShowAgentCreated')
     ?snapshot.view.navShowAgentCreated===true:value?.scope?.showAgentCreated
   });
   const matching=(value,snapshot)=>recentPageMatches(value,desired(savedSettings(value,snapshot),current));
   const useLive=matching(live,current)&&(!matching(read,receipt.result)||after(live,read));
   const candidate=useLive?live:read,saved=savedSettings(candidate,useLive?current:receipt.result);
   if(!recentPageMatches(candidate,desired(saved,current))
    ||!Object.entries(scope).every(([key,value])=>candidate.scope[key]===value)||!newer(candidate))
    throw Error('Recent changed during the read. Retry to read the current list.');
   const unsaved=intent.current&&!recentPageMatches(candidate,desired(intent.current.wanted,current));
   accept(candidate);intent.current=null;
   setError(unsaved?'The setting was not saved. Choose it again; no request was replayed.':'');
  }catch(error){if(mounted.current&&ticket===sequence.current)setError(error.message||'Recent is unavailable. The previous chats are kept.')}
  finally{if(mounted.current&&ticket===sequence.current){waiting.current=false;setBusy(false)}}
 };
 return {page,settings,busy,error,blocked:uncertain.current,change,retry};
}

function RecentShortcuts({host,model,now}){
 const {page,busy,error,blocked,change,retry}=useRecentShortcuts(host,model.state,model.act);
 const root=useRef(null),more=useRef(null),anchor=useRef(null),focused=useRef(null),follower=useRef(null),latest=useRef(null);
 const capture=()=>{
  const scroll=root.current?.closest('.a-nav-content');
  if(!scroll)return;
  const top=scroll.getBoundingClientRect().top;
  const row=[...root.current.querySelectorAll('[data-session-id]')].find(row=>row.getBoundingClientRect().bottom>top);
  anchor.current={scroll,scrollTop:scroll.scrollTop,rowId:row?.dataset.sessionId,offset:row?.getBoundingClientRect().top,focus:document.activeElement};
 };
 useLayoutEffect(()=>{
  const saved=anchor.current;
  if(busy||!root.current)return;
  const displaced=!document.activeElement?.isConnected||document.activeElement===document.body;
  const restore=move=>follower.current?follower.current.suppress(move):move();
  if(!saved){
   restore(()=>{});
   if(displaced&&focused.current&&!focused.current.isConnected)(more.current||root.current)?.focus({preventScroll:true});
   return;
  }
  const row=[...root.current.querySelectorAll('[data-session-id]')].find(row=>row.dataset.sessionId===saved.rowId);
  restore(()=>{saved.scroll.scrollTop=row&&saved.offset!=null?saved.scroll.scrollTop+row.getBoundingClientRect().top-saved.offset:saved.scrollTop});
  if(displaced&&saved.focus&&!saved.focus.isConnected)(more.current||root.current)?.focus({preventScroll:true});
  anchor.current=null;
 },[page,busy]);
 const update=value=>{capture();return change(value)};
 const load=()=>{if(!busy&&!blocked&&page.remaining>0)return update({limit:page.limit+20})};
 latest.current={load,canLoad:()=>!busy&&!blocked&&!error&&page.remaining>0
  &&document.visibilityState!=='hidden'&&!!root.current?.getClientRects().length
  &&!root.current.closest('[hidden],[inert],[aria-hidden="true"]')};
 useEffect(()=>{
  const scroll=root.current?.closest('.a-nav-content');if(!scroll)return;
  const following=followLaterHistory(scroll,()=>latest.current.canLoad(),()=>latest.current.load());
  follower.current=following;
  return()=>{following.dispose();follower.current=null};
 },[host]);
 return <div ref={root} tabIndex={-1} className="a-recent-shortcuts" aria-busy={busy} onFocusCapture={e=>{focused.current=e.target}} onBlurCapture={e=>{if(e.relatedTarget&&!root.current?.contains(e.relatedTarget))focused.current=null}}>
  {page.items.map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now}/>)}
  {!page.items.length&&!busy&&!error&&<p className="a-nav-empty">Your recent chats appear here.</p>}
  {(page.remaining>0||busy||error)&&<div className="a-recent-controls">
   {busy&&<small role="status">Loading recent chats…</small>}
   {page.remaining>0&&<button ref={more} type="button" className="a-sidebar-all a-link a-recent-control" style={{minHeight:44}} aria-disabled={busy||blocked} data-action="view.update" onClick={load}>Load older chats</button>}
   {error&&<p role="alert">{error} <button type="button" className="a-link a-recent-control" aria-disabled={busy} onClick={()=>{if(busy)return;capture();return retry()}}>Retry</button></p>}
  </div>}
 </div>;
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
export function ChatSearch({page,model,view,viewAct,now}){
 const root=useRef(null),input=useRef(null),nav=useContext(WorkNavigationContext);
 const counts=page.activityCounts||{};
 const active=(view.navArchive||'active')!=='active'||(view.navStatusFilter||'all')!=='all'||view.navLocationFilter==='managed';
 useEffect(()=>{input.current?.focus()},[]);
 const update=value=>patch(viewAct,value);
 const clear=()=>{update({navFilter:'',navArchive:'active',navStatusFilter:'all',navLocationFilter:'all'});input.current?.focus()};
 const keyboard=event=>{
  if(event.key==='Escape'&&event.target===input.current){event.preventDefault();if(view.navFilter)update({navFilter:''});else nav?.browse('chat');return}
  if(!['ArrowDown','ArrowUp'].includes(event.key)||!(event.target===input.current||event.target.matches('.a-nav-chat-select')))return;
  const rows=[...root.current.querySelectorAll('.a-nav-chat-select')],index=rows.indexOf(event.target);
  event.preventDefault();
  const next=event.key==='ArrowDown'?index+1:index-1;
  (next<0?input.current:rows[Math.min(next,rows.length-1)])?.focus();
 };
 return <section className="a-chat-search-page" aria-label="Chat search" ref={root} onKeyDown={keyboard}>
  <div className="a-nav-search"><Search/><input ref={input} type="search" aria-label="Search chats" placeholder="Search chat names, workspaces and descriptions" maxLength={500} value={view.navFilter||''} data-action="view.update" onChange={e=>update({navFilter:e.target.value})}/>{view.navFilter&&<button className="a-icon" type="button" aria-label="Clear search" onClick={()=>{update({navFilter:''});input.current?.focus()}}><X/></button>}</div>
  <div className="a-search-tools">
   <details className="a-search-filters"><summary>Filters{active?' · active':''}</summary><div className="a-search-filter-fields">
    <LibraryFilters state={{...model.state,view}} act={viewAct}/>
    <label>Location<select aria-label="Filter chats by location" value={view.navLocationFilter||'all'} onChange={e=>update({navLocationFilter:e.target.value})}><option value="all">All locations</option><option value="managed">No workspace</option></select></label>
    <label>Status<select aria-label="Filter chats by status" value={view.navStatusFilter||'all'} onChange={e=>update({navStatusFilter:e.target.value})}><option value="all">Any status</option><option value="attention">Needs attention{counts.attention?` (${counts.attention})`:''}</option><option value="working">Working{counts.working?` (${counts.working})`:''}</option></select></label>
    <small>Use * and ? for patterns in names and paths.</small>
   </div></details>
   <label className="a-search-sort">Sort<select aria-label="Sort conversations" value={view.navSort||'activity'} onChange={e=>update({navSort:e.target.value})}><option value="activity">Recent activity</option><option value="created">Newest created</option><option value="name">Name</option></select></label>
  </div>
  <div className="a-search-result-summary"><span role="status">{page.pending?'Searching…':`${page.total} ${page.total===1?'chat':'chats'}${view.navFilter?' found':''}`}</span>{active&&<button className="a-link" type="button" onClick={()=>update({navArchive:'active',navStatusFilter:'all',navLocationFilter:'all'})}>Clear filters</button>}</div>
  <div className="a-nav-chats" aria-label="Search results" aria-busy={!!page.pending}>{page.items.map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now} showLocation/>)}</div>
  {!page.total&&!page.pending&&<div className="a-search-empty"><h2>No chats found</h2><p>Try another name or workspace, or clear your filters.</p><button type="button" className="a-soft" onClick={clear}>Clear search and filters</button></div>}
  <ChatPagination page={page} onChange={index=>update({navChatPage:{...page.scope,index}})}/>
 </section>;
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
 const recentView={...HOME_FILTERS,...sidebar?.recentView,...view.navRecentView,navShowAgentCreated:view.navShowAgentCreated===true};
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
 </div>;
}

function QuietSidebar({host,workspaceHost,navigation}){
 const model=useNavigationController(host,'chats'),now=useActivityClock();
 const workspaceState=useNavigation(React,workspaceHost||host);
 const pins=model.state.sidebarNavigation?.pinned||{items:[],total:0,pages:1};
 return <div className="a-sidebar-navigation a-quiet-sidebar">
  <NavigationEditor model={model}/>
  <button type="button" className="a-nav-search-launch" data-action="view.update" onClick={()=>navigation.browse('chats')}><Search/>Search chats</button>
  <SidebarSection id="pinned" title="Pinned" model={model}><PinnedChats page={pins} model={model} now={now}/></SidebarSection>
  <SidebarSection id="workspaces" title="Workspaces" model={model} actions={<button type="button" className="a-icon" aria-label="New workspace" onClick={()=>navigation.create()}><Plus/></button>}>
   <WorkspaceExplorer state={workspaceState} act={workspaceHost?.dispatch||model.act} compact heading={false} onSelect={row=>navigation.browse('workspace',row.workspaceId)}/>
   <button type="button" className="a-sidebar-all a-link" data-action="view.update" onClick={()=>navigation.browse('workspaces')}>All workspaces<ChevronRight/></button>
  </SidebarSection>
  <SidebarSection id="recent" title="Recent" model={model} actions={<>
   {model.history.loading&&<span className="a-caption" role="status">Finding existing chats…</span>}
   {model.history.error&&<span role="alert">{model.history.error}</span>}
   <button type="button" className="a-icon" aria-label="Refresh workspaces and chats" data-action="history.refresh" disabled={model.refreshing} onClick={()=>model.act('history.refresh',{})}><RefreshCw className={model.refreshing?'a-progress-spinner':undefined}/></button>
  </>}>
   <RecentShortcuts host={host} model={model} now={now}/>
  </SidebarSection>
 </div>;
}
export function ConversationList(props){
 const navigation=useContext(WorkNavigationContext);
 return navigation?<QuietSidebar {...props} navigation={navigation}/>:<LegacyConversationList {...props}/>;
}
