import React,{useContext,useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Folder,Plus,Search,ChevronRight,ArrowLeft,Settings,Info,MoreHorizontal,Pin,Archive,Bug,ScanEye,PanelLeft,FileText,Copy,X,AudioLines,Mic,MicOff,Bell} from 'lucide-react';
import {WorkNavigationContext,workSurface} from './work-navigation';
import {useNavigationController,ChatList,NavigationEditor,PinnedChats} from './shell/navigation-components';
import {WorkspaceExplorer} from './workspace-explorer';
import {WorkspaceForm,WorkspacePicker} from './workspace-setup';
import {newChatSetup} from './new-chat';
import {useActivityClock,CopyDetail} from './navigation-details';
import {useModalFocus} from './responsive-navigation';
import {ShellSlot} from './shell/runtime';
import {CanvasToggle} from './shell-panels';
import {AttentionBadge} from './attention';
import {chatPage} from './chat-navigation';
import {sessionStatus} from './session-status';
import {ownershipState} from './ownership';

export function WorkDialog({label,onClose,children,initialFocus}){
 const ref=useRef(null);useModalFocus(ref,true,onClose);
 useEffect(()=>{if(initialFocus)ref.current?.querySelector(initialFocus)?.focus()},[initialFocus]);
 return createPortal(<div className="a-work-dialog-backdrop" onSubmit={e=>e.stopPropagation()} onPaste={e=>e.stopPropagation()} onDrop={e=>e.stopPropagation()} onClick={e=>{if(e.target===e.currentTarget)onClose()}}><section ref={ref} role="dialog" aria-modal="true" aria-label={label} className="a-work-dialog"><div className="a-work-dialog-head"><strong>{label}</strong><button type="button" className="a-icon" aria-label={'Close '+label} onClick={onClose}><X/></button></div>{children}</section></div>,document.getElementById('amp-one'));
}
export function ComposerWorkspace({state,act}){
 const [open,setOpen]=useState(false),trigger=useRef(null),setup=newChatSetup(state),workspace=state.workspaces?.find(row=>row.path===setup.workspace);
 const close=()=>{setOpen(false);requestAnimationFrame(()=>trigger.current?.focus())};
 return <><button type="button" ref={trigger} className="a-composer-workspace" aria-label="Choose workspace" aria-expanded={open} onClick={event=>{event.currentTarget.focus();setOpen(true)}}><Folder/><span>{setup.location?.kind==='managed'||!setup.workspace?'No workspace':workspace?.name||setup.workspace.split('/').at(-1)}</span><ChevronRight/></button>{open&&<WorkDialog initialFocus='input[type="search"]' label="Choose a workspace" onClose={close}><WorkspacePicker state={state} act={act} setup={setup} onChange={async value=>{const result=await act('view.update',{patch:{newSessionDraft:{...setup,...value}}});if(!result?.accepted)throw Error(result?.error||'Could not select this workspace.');close()}}/></WorkDialog>}</>;
}
export function WorkspaceCreation({state,act,onClose}){
 const nav=useContext(WorkNavigationContext);
 return <WorkDialog label="Workspace" onClose={onClose}><WorkspaceForm state={state} act={act} fromDraft={!state.selectedSessionId} onCancel={onClose} onDone={result=>{onClose();if(result?.workspaceId)nav.browse('workspace',result.workspaceId);else nav.browse('workspaces')}}/></WorkDialog>;
}
function ActionMenu({label,children,icon=<MoreHorizontal/>,badge}){
 const ref=useRef(null),[open,setOpen]=useState(false);
 useEffect(()=>{if(!open)return;ref.current?.querySelector('.a-work-menu-items button')?.focus();const dismiss=e=>{if(!ref.current?.contains(e.target))setOpen(false)};const escape=e=>{if(e.key==='Escape'){setOpen(false);ref.current?.querySelector('button')?.focus()}};document.addEventListener('pointerdown',dismiss);document.addEventListener('keydown',escape);return()=>{document.removeEventListener('pointerdown',dismiss);document.removeEventListener('keydown',escape)}},[open]);
 return <div className="a-work-menu" ref={ref} onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))setOpen(false)}}><button type="button" className="a-icon" aria-label={label} aria-expanded={open} onClick={()=>setOpen(!open)}>{icon}{badge}</button>{open&&<div className="a-work-menu-items" role="group" aria-label={label} onClickCapture={()=>ref.current?.querySelector('button')?.focus()} onClick={e=>{if(e.target.closest('button'))setOpen(false)}}>{children}</div>}</div>;
}
export function AppFooter({state,connected,open,act}){
 return <div className="a-work-footer"><ShellSlot name="app.status"><span className={`a-dot ${connected?'':'pending'}`} title={connected?'Connected':'Reconnecting'}/></ShellSlot><span className="a-nav-reveal">{connected?'Connected':'Reconnecting…'}</span><ShellSlot name="app.actions"><ActionMenu label="App options" icon={<Settings/>} badge={<AttentionBadge state={state}/>}><button onClick={()=>open('activity')}><Bell/>Ready for you<AttentionBadge state={state}/></button><button onClick={()=>open('settings')}><Settings/>Settings<AttentionBadge state={state} settings/></button><button onClick={()=>open('feedback')}><Bug/>Send feedback</button><button onClick={()=>open('agent')}><ScanEye/>What the agent sees</button></ActionMenu></ShellSlot></div>;
}
export function WorkHeader({state,session,act,open,narrow,presentation}){
 const nav=useContext(WorkNavigationContext),surface=workSurface(state),browsing=surface!=='chat';
 const setup=!session&&!browsing?newChatSetup(state):null;
 const workspace=state.workspaces?.find(row=>setup?setup.location?.kind!=='managed'&&row.path===setup.workspace:row.id===(browsing?state.view.workWorkspaceId:state.selectedWorkspaceId));
 const title=surface==='workspaces'?'All workspaces':surface==='chats'?'All chats':surface==='workspace'?workspace?.name||'Workspace':session?.title||'New chat';
 return <header className="a-work-header" data-part="header">
  <div className="a-brand a-work-brand"><img src="/branding/icons/amplifier-icon-128.png" alt=""/><span>Amplifier</span></div>
  {(narrow||!state.view?.navPinned&&!state.view?.navExpanded)&&<button type="button" className="a-icon a-work-nav-toggle" aria-label="Open navigation" aria-expanded={false} aria-controls="workspace-navigation" data-action="view.update" onClick={()=>act('view.update',{patch:{navExpanded:true,...(!narrow?{navPinned:true}:{}),toolbarMenuOpen:false}})}><PanelLeft/><AttentionBadge state={state}/></button>}
  <ShellSlot name="conversation.header"><div className="a-work-heading">{surface==='chat'&&workspace&&session?.location?.kind!=='managed'&&<><button type="button" className="a-work-crumb" onClick={()=>nav.browse('workspace',workspace.id)}><Folder/>{workspace.name}</button><ChevronRight/></>}<strong title={title}>{title}</strong></div></ShellSlot>
  <div className="a-work-header-actions">
   {browsing&&<button type="button" className="a-link" onClick={()=>nav.browse('chat')}><ArrowLeft/>{session?'Back to chat':'Back'}</button>}
   {!browsing&&session&&<><ActionMenu label="Chat actions"><button onClick={()=>open('session-details')}><Info/>Chat details</button><button data-action="session.pin" onClick={()=>act('session.pin',{id:session.id,pinned:!(state.pinnedSessionIds||[]).includes(session.id)})}><Pin/>{(state.pinnedSessionIds||[]).includes(session.id)?'Unpin':'Pin chat'}</button><button data-action="session.archive" onClick={()=>act('session.archive',{id:session.id})}><Archive/>Archive</button></ActionMenu><CanvasToggle state={state} act={act} layout={presentation.layout}/></>}
  </div>
 </header>;
}
function WorkspaceFiles({workspace,state,act}){
 const [path,setPath]=useState(workspace.path),[error,setError]=useState(''),[loading,setLoading]=useState(false),[copied,setCopied]=useState('');
 const controlId='workspace-files-'+workspace.id,listing=state.locationListing?.controlId===controlId?state.locationListing:null;
 const status=state.actionStatus?.['locations.list'],operation=status?.target?.controlId===controlId?status:null;
 const busy=loading||['queued','working'].includes(operation?.phase),failure=error||(operation?.phase==='error'?operation.error:'');
 useEffect(()=>{let live=true;setLoading(true);setError('');Promise.resolve(act('locations.list',{controlId,path,directoriesOnly:false})).then(result=>{if(result?.accepted===false&&live)setError(result.error||'Could not list files.')}).catch(e=>{if(live)setError(e.message)}).finally(()=>{if(live)setLoading(false)});return()=>{live=false}},[path,workspace.id]);
 const selected=state.sessions?.find(row=>row.id===state.selectedSessionId),canOpen=selected?.workspace===workspace.path;
 return <section className="a-work-files" aria-label="Workspace files"><p className="a-caption">Files on {state.workspaceDefaults?.hostLabel||'this host'}. Viewing this list does not add files to a message.</p><div className="a-work-file-path"><button className="a-icon" type="button" disabled={path===workspace.path} aria-label="Parent folder" onClick={()=>setPath(listing?.parent||workspace.path)}><ArrowLeft/></button><code>{path}</code></div>{busy&&<p role="status">Loading files…</p>}{failure&&<p role="alert">{failure}</p>}{listing?.path===path&&listing.entries.map(row=><div className="a-work-file-row" key={row.path}>{row.directory?<Folder/>:<FileText/>}<button type="button" disabled={!row.directory&&!canOpen} onClick={()=>row.directory?setPath(row.path):act('canvas.openFile',{sessionId:selected.id,workspace:workspace.path,path:row.path}).then(result=>{if(result?.accepted)act('view.update',{patch:{workSurface:'chat'}})})}>{row.name}</button><button type="button" className="a-icon" aria-label={'Copy path for '+row.name} onClick={()=>navigator.clipboard.writeText(row.path).then(()=>setCopied(row.name),()=>setError('Clipboard unavailable.'))}><Copy/></button></div>)}{listing?.truncated&&<p>Showing the first 300 entries. Browse a subfolder to see more.</p>}{!canOpen&&<p className="a-caption">Open a chat in this workspace to preview a file beside it.</p>}{copied&&<p role="status">Path copied: {copied}</p>}</section>;
}
function WorkBrowser({host,workspaceHost,state,act}){
 const model=useNavigationController(host,'chats'),workspaceModel=useNavigationController(workspaceHost||host,'workspaces'),now=useActivityClock(),nav=useContext(WorkNavigationContext);
 const surface=workSurface(state),workspace=workspaceModel.workspace,tab=state.view.workWorkspaceTab||'chats';
 const setTab=tab=>act('view.update',{patch:{workWorkspaceTab:tab}});
 const view=surface==='chats'?{...model.view,...model.state.sidebarNavigation?.recentView,...model.view.navRecentView,navChatScope:'all'}:{...model.view,navChatScope:'workspace'};
 const page=chatPage({...model.state,view},model.workspace,{section:surface==='chats'?'recent':'workspace'});
 const viewAct=(name,args)=>{if(name!=='view.update'||surface!=='chats')return model.act(name,args);const next={...model.state.sidebarNavigation?.recentView,...model.view.navRecentView,...args.patch};if(!Object.hasOwn(args.patch,'navChatPage'))delete next.navChatPage;return model.act(name,{patch:{navRecentView:next}})};
 return <div className="a-work-browser" data-part="work-browser"><NavigationEditor model={model}/><NavigationEditor model={workspaceModel}/>
  <div className="a-work-browser-heading"><div><h1>{surface==='workspace'?workspace?.name||'Workspace unavailable':surface==='workspaces'?'Your workspaces':'Your chats'}</h1><p>{surface==='workspace'?'A place for related chats and files.':surface==='workspaces'?'Keep related work together. Each workspace has its own chats and files.':'Find a conversation and pick up where you left off.'}</p></div><button type="button" className="a-primary" onClick={()=>surface==='workspaces'?nav.create():nav.newChat(surface==='workspace'?workspace?.path:undefined)}><Plus/>{surface==='workspaces'?'New workspace':'New chat'}</button></div>
  {surface==='workspaces'?<WorkspaceExplorer state={workspaceModel.state} act={workspaceModel.act} onEdit={workspaceModel.setDraft} heading={false} onSelect={row=>nav.browse('workspace',row.workspaceId)}/>:<>
   {surface==='workspace'&&workspace&&<><div className="a-work-tabs" role="group" aria-label="Workspace view">{['chats','files','details'].map(key=><button key={key} type="button" aria-pressed={tab===key} data-action="view.update" onClick={()=>setTab(key)}>{key[0].toUpperCase()+key.slice(1)}</button>)}</div>{tab==='details'&&<div className="a-work-details"><CopyDetail label="Folder" value={workspace.path}/><p>Chats are discovered from native Amplifier history for this folder. Files stay in place and are visible from your terminal.</p><button className="a-soft" onClick={()=>workspaceModel.setDraft({mode:'rename',id:workspace.id,name:workspace.name})}>Rename workspace</button></div>}{tab==='files'&&<WorkspaceFiles key={workspace.id} workspace={workspace} state={state} act={act}/>}</>}
   {surface==='chats'&&model.state.sidebarNavigation?.pinned?.total>0&&<section className="a-browser-pins" aria-label="Pinned chats"><h2>Pinned</h2><PinnedChats page={model.state.sidebarNavigation.pinned} model={model} now={now}/></section>}
   {(surface==='chats'||tab==='chats')&&<ChatList page={page} model={model} view={view} viewAct={viewAct} now={now} showLocation={surface==='chats'}/>}
  </>}
 </div>;
}
export function WorkSurface({shell,state,act}){
 const chats=shell.composition.instances.find(row=>row.package==='builtin.chats'),workspaces=shell.composition.instances.find(row=>row.package==='builtin.workspaces');
 if(!chats)return <div className="a-work-browser"><p>Your custom navigation is available in the sidebar. This browser uses the standard Chats component.</p><a href="?shell=recovery">Open with standard navigation</a></div>;
 return <WorkBrowser host={shell.hostFor(chats)} workspaceHost={workspaces?shell.hostFor(workspaces):undefined} state={state} act={act}/>;
}

// Live controls stay outside the conversation, which is hidden during browsing.
export function LiveChatActivity({voice={},callSession,callActive,browsing,working,session,act,onReturn,children}){
 const callWorking=sessionStatus(callSession).busy||(callSession?.workers||[]).some(worker=>['queued','starting','working','running','stopping'].includes(worker.status));
 const callUnavailable=callSession?.workspaceAvailable===false||!!callSession?.historyReadOnlyReason||ownershipState(callSession).blocked;
 return <div className="a-work-live-controls">
  {callActive&&<div className="a-call-strip" data-part="voice"><AudioLines/><span>{voice.model||'Voice'} · {voice.status}</span><button className="a-icon" aria-label={voice.muted?'Unmute microphone':'Mute microphone'} title={voice.muted?'Unmute microphone':'Mute microphone; replies and work continue'} data-action="call.mute" onClick={()=>act('call.mute',{muted:!voice.muted})}>{voice.muted?<MicOff/>:<Mic/>}</button><button className="a-soft" title="End audio; work continues" data-action="call.end" onClick={()=>act('call.end')}>End call</button>{callWorking&&callSession&&<button type="button" className="a-soft" data-action="conversation.stop" title={'Stop work in '+(callSession.title||'this call’s conversation')+'; keep the call connected'} disabled={callSession.status==='stopping'||callUnavailable} onClick={()=>act('conversation.stop',{sessionId:callSession.id})}>{callSession.status==='stopping'?'Stopping work…':'Stop work'}</button>}{voice.muted&&<small role="status">Microphone muted. Replies and work continue.</small>}<button className="a-link" data-action="call.keepAwake" aria-pressed={voice.keepAwake!==false} onClick={()=>act('call.keepAwake',{enabled:voice.keepAwake===false})}>{voice.wakeLock==='active'?'Screen staying awake':'Keep screen awake'}</button>{voice.keepAwake!==false&&['unsupported','unavailable','released'].includes(voice.wakeLock)&&<small role="status">Screen may lock. Keep this page visible; battery-saving settings can prevent staying awake.</small>}{voice.audioSessionState==='interrupted'&&<small role="status">Call audio interrupted by your device. Waiting for audio to become available.</small>}{voice.microphoneEnded?<small role="status">Microphone stopped. End this call and start again.</small>:voice.microphoneInterrupted&&<small role="status">Microphone temporarily unavailable.</small>}{voice.playbackBlocked&&<small role="status">Voice playback paused. <button type="button" className="a-link" data-action="call.resumeAudio" onClick={()=>act('call.resumeAudio')}>Resume audio</button></small>}{voice.fallbackReason&&<small>{voice.fallbackReason}</small>}</div>}
  {children}
  {browsing&&working&&session&&<div className="a-call-strip" role="status"><span>{session.title||'Chat'} · Working…</span><button type="button" className="a-link" onClick={onReturn}>Back to chat</button><button type="button" className="a-soft" data-action="conversation.stop" onClick={()=>act('conversation.stop',{sessionId:session.id})}>Stop</button></div>}
 </div>;
}
