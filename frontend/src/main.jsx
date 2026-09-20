import {SettingsExperience} from './settings-experience';
import {useMessageOutbox,outboxMessages} from './message-outbox';
import {clientId,clientUrl,attachClient} from './api';
import {checkpointSurfaces} from './surface-checkpoint';
import {useConversationDetail} from './conversation-detail';
import {OperationsPanel} from './operations';
import {ComputationPanel} from './computation';
import {trackAction} from './feedback-diagnostics';
import {ConversationError,ConversationSelect} from './conversation-controls';
import {useShell} from './shell/runtime';
import {ownershipState,actionErrorMessage} from './ownership.js';
import {ArtifactLinks} from './canvas-library';
import {MessageEntry,completedTurnEnds} from './message-actions';
import {WorkspaceRail,AgentCanvas,CanvasToggle,SessionHistoryControls} from './shell-panels';
import {MoreAppActions} from './app-toolbar';
import {WorkspaceLayout} from './panel-layout';
import {AttachmentStrip,ModelControl,readAttachment} from './chat-controls';
import {BundleControl} from './bundle-controls';
import {CoordinationPanel} from './coordination';
import {AttentionBadge,ActivityPanel,useReadCompletion} from './attention';
import {FileDrop,PathField,BundlePicker} from './settings-ui';
import React,{useState,useEffect,useLayoutEffect,useRef,useCallback} from 'react';
import {createRoot} from 'react-dom/client';
import {Phone,MessageCircle,Bell,ArrowUp,Plus,Settings,X,Square,GitBranch,Check,Download,FileText,ChevronRight,Loader,Volume2,Mic,MicOff,RefreshCw,Paperclip,Info,AudioLines} from 'lucide-react';
import {request,download,visibleView,applyIconTooltips} from './api';
import {createPendingView} from './pending-view';
import {createConversationNavigation} from './conversation-navigation';
import {messageTextForCopy} from './message-copy';
import {deliverConversationExport} from './conversation-export.js';
import {VoiceClient} from './voice';
import {VoiceVisualClient} from './voice-visual';
import {VoiceVisualControls} from './voice-visual.jsx';
import {sessionStatus} from './session-status';
import {Markdown} from './markdown';
import {FeedbackPanel,FeedbackNotice} from './feedback';
import {RuntimeSettings} from './runtime-settings';
import {notificationBody,desktopNotificationsEnabled,notificationMessages} from './notifications';
import {TurnTimeline} from './timeline';
import {executionData,turnPlacements} from './timeline-data';
import {liveActivity} from './activity';
import {resizeComposer} from './composer';
import {ComposerOwnership} from './composer-ownership';
import {Questions} from './questions';
import {createActionFeedback} from './action-feedback';
import './action-feedback.css';
import {createChatScroll} from './chat-scroll';
import {followEarlierHistory} from './history-scroll';
import {headerChatChoices,isTopLevelChat} from './chat-navigation';
import {SubagentHistory,SubagentHistoryButton} from './subagent-history';
import {McpAppThemeProvider} from './mcp-app-theme';
const logo='/branding/icons/amplifier-icon-128.png';
import defaultSkin from './unified.css?raw';
import './base.css';
import './unified.css';
import './settings-experience.css';

const icons={call:Phone,text:Bell,chat:MessageCircle};
const pretty=v=>JSON.stringify(v,null,2);
const nowLabel=value=>{try{return new Date(typeof value==='number'&&value<1e12?value*1000:value).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}catch{return ''}};
const initialSetup={title:'A new conversation',bundle:'work',workspace:''};
function App(){
 const actionFeedback=useRef(createActionFeedback()),outsidePointer=useRef(false),panelReturnFocus=useRef(null);
 const [state,setState]=useState(null),[catalog,setCatalog]=useState([]),[error,setError]=useState(''),[connected,setConnected]=useState(false),[busy,setBusy]=useState(false),[bootAttempt,setBootAttempt]=useState(0),[draft,setDraft]=useState(''),[setup,setSetup]=useState(initialSetup),[workerDraft,setWorkerDraft]=useState(''),[themeDraft,setThemeDraft]=useState(defaultSkin),[themeName,setThemeName]=useState('Amplifier Unified'),[preview,setPreview]=useState(false),[agentAction,setAgentAction]=useState('view.update'),[agentArgs,setAgentArgs]=useState('{"patch":{"mode":"chat"}}'),[voice,setVoice]=useState({status:'idle'}),[activityClock,setActivityClock]=useState(Date.now()),[uploading,setUploading]=useState(false),[dragOver,setDragOver]=useState(false);
 const visualClient=useRef(null);const [visual,setVisual]=useState({status:'idle'});
 const outbox=useMessageOutbox(),deliveries=useRef(new Set()),sendQueue=useRef(Promise.resolve()),draftSaves=useRef(new WeakMap());
 const creatingSession=useRef(null),uploadQueue=useRef(Promise.resolve()),uploadCount=useRef(0),fileInput=useRef(null),messagesPane=useRef(null),stickToBottom=useRef(true),chatScroll=useRef(null),historyScrollAnchor=useRef(null),composerRef=useRef(null),root=useRef(null),latest=useRef(null),voiceClient=useRef(null),messagesEnd=useRef(null),draftTimer=useRef(),stagedDraft=useRef(null),stagedDraftPayload=useRef(null),lastNotify=useRef(new Set()),loadedTheme=useRef(null),lastDraft=useRef(''),commandQueue=useRef(Promise.resolve()),navigationQueue=useRef(Promise.resolve()),canvasDirtyBarrier=useRef(null),reviewQueue=useRef(Promise.resolve()),stateListeners=useRef(new Set()),seenEffects=useRef(new Set()),effectHandler=useRef(()=>{}),pendingView=useRef(createPendingView()),serverState=useRef(null),conversationNavigation=useRef(createConversationNavigation());
 const handleEffects=useCallback(effects=>{
  for(const effect of effects||[]){
   if(effect.id&&seenEffects.current.has(effect.id))continue;
   if(effect.id)seenEffects.current.add(effect.id);
   if(effect.createdAt&&Date.now()-new Date(typeof effect.createdAt==='number'&&effect.createdAt<1e12?effect.createdAt*1000:effect.createdAt).getTime()>60000)continue;
   Promise.resolve().then(async()=>{
    if(effect.type==='download')download(effect.filename||'amplifier-export.json',effect.content??effect.data,effect.mimeType||effect.mime||'application/json');
    if(effect.type==='conversation.export')await deliverConversationExport(effect,{request,download,clipboard:navigator.clipboard});
    if(effect.type==='message.copy'){
     let status='ready',message='Copied Markdown';
     try{await navigator.clipboard.writeText(await messageTextForCopy(latest.current,effect,request))}
     catch(error){status='error';message='Could not copy: '+error.message}
     await request('/api/actions',{method:'POST',body:{action:'message.copyResult',args:{requestId:effect.requestId,status,message}}});
    }
    if(effect.type==='download.url'){const link=document.createElement('a');link.href=effect.url;link.download=effect.filename;link.click()}
    if(effect.type==='browser.open')window.open(effect.url,'_blank','noopener,noreferrer');
    if(['clipboard.write','clipboard.url'].includes(effect.type)){
     try{let content=effect.content;if(effect.type==='clipboard.url'){const response=await fetch(effect.url);if(!response.ok)throw Error('Source unavailable');content=await response.text()}await navigator.clipboard.writeText(content);await request('/api/actions',{method:'POST',body:{action:'canvas.report',args:{id:effect.canvasId,part:'clipboard',status:'ready',message:'Source copied'}}})}
     catch(e){await request('/api/actions',{method:'POST',body:{action:'canvas.report',args:{id:effect.canvasId,part:'clipboard',status:'error',message:'Clipboard unavailable: '+e.message}}});throw e}
    }
    if(effect.type==='call.start'){try{await voiceClient.current.start(effect.args||{})}catch(error){await request('/api/voice/end',{method:'POST',body:{id:null}}).catch(()=>{});throw error}}
    if(effect.type==='canvas.checkpoint')await checkpointSurfaces(effect.sessionId,effect.id);
    if(effect.type==='voice.visual.capture')await visualClient.current?.capture(effect);
    if(effect.type==='call.end')await voiceClient.current.end();
    if(effect.type==='call.mute')voiceClient.current.setMuted(effect.muted??effect.args?.muted??true);
    if(['notification.request','notification-permission'].includes(effect.type)&&'Notification'in window){const permission=await Notification.requestPermission();await request('/api/view',{method:'POST',body:{clientId,notificationPermission:permission}})}
   }).catch(e=>setError(actionErrorMessage(e)));
  }
 },[]);
 effectHandler.current=handleEffects;
 const acceptState=useCallback(next=>{
  if(!next||typeof next!=='object')return;
  if(serverState.current && next.client?.hostInstanceId===serverState.current.client?.hostInstanceId && next.revision<serverState.current.revision)return;
  if(!latest.current){for(const effect of next.deviceCommands||[])if(effect.id)seenEffects.current.add(effect.id);for(const m of notificationMessages(next))lastNotify.current.add(m.id)}
  else effectHandler.current(next.deviceCommands);
  const previousSession=latest.current?.sessions?.find(s=>s.id===latest.current.selectedSessionId),nextSession=next.sessions?.find(s=>s.id===next.selectedSessionId);
  if(previousSession?.id===nextSession?.id&&Number(nextSession?.sharedHistoryOffset)<Number(previousSession?.sharedHistoryOffset)&&messagesPane.current){
   const pane=messagesPane.current,top=pane.getBoundingClientRect().top,anchor=[...pane.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>top);
   if(anchor){historyScrollAnchor.current={sessionId:nextSession.id,messageId:anchor.dataset.messageId,top:anchor.getBoundingClientRect().top};stickToBottom.current=false}
  }
  serverState.current=next;conversationNavigation.current.remember(next);
  latest.current=conversationNavigation.current.apply(next);setState(pendingView.current.apply(latest.current));stateListeners.current.forEach(fn=>fn(latest.current));
 },[]);
 const dispatch=useCallback((action,args={},meta={})=>{
  if(['conversation.send','conversation.stop','worker.spawn','worker.stop','worker.steer','approval.respond','attachment.add','attachment.remove'].includes(action))args={sessionId:latest.current?.selectedSessionId,...args};
  if(action==='conversation.send'&&!meta.checkpointed)return checkpointSurfaces(args.sessionId).then(()=>dispatch(action,args,{...meta,checkpointed:true}));
  if(action==='view.update'&&Object.hasOwn(args.patch||{},'draft'))args={sessionId:latest.current?.selectedSessionId,...args};
  const selectedId=action==='session.select'?args.id:action==='shell.command'&&args.action==='session.select'?args.args?.id:null;
  const navigationToken=selectedId&&!canvasDirtyBarrier.current?conversationNavigation.current.begin(pendingView.current.apply(latest.current),selectedId):null;
  if(navigationToken&&serverState.current){latest.current=conversationNavigation.current.apply(serverState.current);setState(pendingView.current.apply(latest.current))}
  const pending=action==='view.update'?(meta.pendingViewToken??pendingView.current.add(args.patch||{},args.sessionId)):null;
  if(pending&&latest.current)setState(pendingView.current.apply(latest.current));
  const settleTracking=trackAction(),settleFeedback=meta.feedback===false?()=>{}:actionFeedback.current.begin(action==='shell.command'?args.action:action);
  const dirtyBarrier=canvasDirtyBarrier.current;
  const execute=async()=>{
   // Chat navigation has its own queue; it must not overtake a declared edit.
   if(navigation&&dirtyBarrier)await dirtyBarrier;
   const result=await request('/api/actions',{signal:meta.signal,method:'POST',body:{action,args,id:meta.id||crypto.randomUUID(),...(meta.expectedRevision!==undefined?{expectedRevision:meta.expectedRevision}:{})}});
   if(pending)pendingView.current.settle(pending);
   if(navigationToken)conversationNavigation.current.settle(navigationToken);
   if(result.state)acceptState(result.state);
   if(pending&&latest.current)setState(pendingView.current.apply(latest.current));
   handleEffects(result.effects);return result;
  };
  const navigation=['session.select','workspace.select'].includes(action)||(action==='shell.command'&&['session.select','workspace.select'].includes(args.action));
  // Reviewing exact item fingerprints is independent of send admission and view changes.
  const exactReview=action==='attention.read'&&Array.isArray(args.ids)&&args.ids.length>0&&args.ids.every(id=>typeof args.fingerprints?.[id]==='string');
  const queue=action.startsWith('coordination.')?{current:Promise.resolve()}:navigation?navigationQueue:exactReview?reviewQueue:['conversation.send','message.edit','question.answer'].includes(action)?sendQueue:commandQueue;
  const promise=queue.current.then(execute,execute).catch(error=>{if(navigationToken){conversationNavigation.current.settle(navigationToken);if(serverState.current){latest.current=conversationNavigation.current.apply(serverState.current);setState(pendingView.current.apply(latest.current))}}if(error.state)acceptState(error.state);if(pending){pendingView.current.settle(pending);if(latest.current)setState(pendingView.current.apply(latest.current))}throw error}).finally(()=>{settleTracking();settleFeedback()});queue.current=promise.catch(()=>{});
  if(action==='canvas.views.dirty'){
   canvasDirtyBarrier.current=promise;
   const settled=()=>{if(canvasDirtyBarrier.current===promise)canvasDirtyBarrier.current=null};
   promise.then(settled,settled);
  }
  return promise;
 },[acceptState,handleEffects]);
 const shell=useShell(state,dispatch,clientId);
 useEffect(()=>root.current?actionFeedback.current.attach(root.current):undefined,[!!state]);
 const act=useCallback((name,args={})=>dispatch(name,args).catch(e=>setError(actionErrorMessage(e))),[dispatch]);
 const publishView=useCallback(()=>{if(latest.current)request('/api/view',{method:'POST',body:{...visibleView(root.current,clientId),voice:voiceClient.current?.state,notificationPermission:'Notification'in window?Notification.permission:'unsupported'}}).catch(()=>{});},[]);
 useEffect(()=>{
  let alive=true,source;const controller=new AbortController();setError('');
  const timer=setTimeout(()=>{controller.abort();if(alive&&!latest.current)setError('The workspace is taking too long to respond. You can retry the connection.')},10000);
  attachClient(controller.signal).then(()=>{
   if(!alive)return;
   request('/api/state',{signal:controller.signal}).then(data=>{if(alive){acceptState(data.state||data);setConnected(true)}}).catch(e=>{if(alive&&e.name!=='AbortError'&&!latest.current)setError(actionErrorMessage(e))}).finally(()=>clearTimeout(timer));
   request('/api/actions',{signal:controller.signal}).then(actions=>{if(alive)setCatalog(Array.isArray(actions)?actions:actions.actions||[])}).catch(()=>{});
   source=new EventSource(clientUrl('/api/events'));source.addEventListener('state',e=>{if(!alive)return;try{acceptState(JSON.parse(e.data));setConnected(true);setError('');clearTimeout(timer)}catch{}});
   source.addEventListener('shell',e=>{try{window.dispatchEvent(new CustomEvent('amplifier-shell',{detail:JSON.parse(e.data)}))}catch{}});
   source.onopen=()=>setConnected(true);source.onerror=()=>setConnected(false);
  }).catch(e=>{clearTimeout(timer);if(alive&&e.name!=='AbortError')setError(actionErrorMessage(e))});
  return()=>{alive=false;clearTimeout(timer);controller.abort();source?.close()};
 },[acceptState,bootAttempt]);
 useEffect(()=>{window.amplifier=Object.freeze({shellClientId:clientId,getShellState:()=>shell.data,getState:()=>({...latest.current,renderedView:visibleView(root.current,clientId)}),getActions:()=>catalog,dispatch,subscribe:fn=>{stateListeners.current.add(fn);return()=>stateListeners.current.delete(fn)}});return()=>{delete window.amplifier}},[catalog,dispatch,shell.data]);
 useEffect(()=>{const element=root.current;if(!element)return;const sync=()=>applyIconTooltips(element),observer=new MutationObserver(sync);sync();observer.observe(element,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-label']});return()=>observer.disconnect()},[!!state]);
 useEffect(()=>{const timer=setTimeout(publishView,300);return()=>clearTimeout(timer)},[state,draft,themeDraft,preview,voice,publishView]);
 useEffect(()=>{let timer;const schedule=()=>{clearTimeout(timer);timer=setTimeout(publishView,200)};document.addEventListener('selectionchange',schedule);document.addEventListener('focusin',schedule);document.addEventListener('input',schedule);window.addEventListener('resize',schedule);return()=>{clearTimeout(timer);document.removeEventListener('selectionchange',schedule);document.removeEventListener('focusin',schedule);document.removeEventListener('input',schedule);window.removeEventListener('resize',schedule)}},[publishView]);
 useEffect(()=>{if(!state)return;const serverDraft=state.view?.draft||'';if(serverDraft!==lastDraft.current){setDraft(serverDraft);lastDraft.current=serverDraft}},[state?.view?.draft,state?.selectedSessionId]);
 useEffect(()=>{if(!state)return;const key=state.theme?.name+'::'+state.theme?.css;if(key!==loadedTheme.current){setThemeDraft(state.theme?.css||defaultSkin);setThemeName(state.theme?.name||'Amplifier Unified');setPreview(false);loadedTheme.current=key}},[state?.theme]);
 useEffect(()=>{const v=state?.view;if(!v)return;if(v.sessionSetup)setSetup(v.sessionSetup);if(v.workerDraft!==undefined)setWorkerDraft(v.workerDraft);if(v.themeDraft!==undefined)setThemeDraft(v.themeDraft);if(v.themeDraftName!==undefined)setThemeName(v.themeDraftName);if(v.themePreview!==undefined)setPreview(v.themePreview);if(v.agentAction!==undefined)setAgentAction(v.agentAction);if(v.agentArgs!==undefined)setAgentArgs(v.agentArgs)},[state?.view?.sessionSetup,state?.view?.workerDraft,state?.view?.themeDraft,state?.view?.themeDraftName,state?.view?.themePreview,state?.view?.agentAction,state?.view?.agentArgs]);
 const detail=useConversationDetail(state?.sessions?.find(s=>s.id===state.selectedSessionId),()=>{stickToBottom.current=false;const pane=messagesPane.current;if(!pane)return;const top=pane.getBoundingClientRect().top,anchor=[...pane.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>top);if(anchor)historyScrollAnchor.current={sessionId:state.selectedSessionId,messageId:anchor.dataset.messageId,top:anchor.getBoundingClientRect().top}});
 const session=detail.session,view=state?.view||{},mode=view.mode||'chat',panel=view.panel,activity=sessionStatus(session),working=activity.busy,messages=outboxMessages(session?.messages||[],outbox.entries,session?.id),historyPending=session?.historyLoaded===false||!!session?.historyLoading&&!messages.length,ownership=ownershipState(session),executionUnavailable=session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||ownership.blocked;
 useEffect(()=>{const title=session?.title?.trim();document.title=title?title+' - Amplifier':'Amplifier'},[session?.id,session?.title]);
 useReadCompletion(state,act,messagesPane);
 const live=liveActivity(session,activityClock),execution=executionData(session);
 const catalogWorkspace=useRef(null);
 useEffect(()=>{if(session?.historyManaged)return;const workspace=session?.workspace||state?.settings?.workspace;if(!workspace||catalogWorkspace.current===workspace)return;catalogWorkspace.current=workspace;if(state.setup?.providersWorkspace!==workspace||!state.setup?.providersLoadedAt)act('providers.list',session?{sessionId:session.id}:{})},[session?.workspace,session?.historyManaged,state?.settings?.workspace]);
 const availableAttachments=(session?.draftAttachments||[]).filter(file=>!outbox.entries.some(row=>row.sessionId===session?.id&&row.attachmentIds.includes(file.id)));
 useEffect(()=>{for(const entry of outbox.entries){const saved=state?.sessions?.find(row=>row.id===entry.sessionId)?.messages?.find(row=>row.inputId===entry.commandId);if(saved?.delivery?.status==='accepted')outbox.update(entry.id,null)}},[state]);
 const turnEnds=completedTurnEnds(session);
 const workPlacement=turnPlacements(messages,execution);
 useEffect(()=>{if(!live)return;const timer=setInterval(()=>setActivityClock(Date.now()),1000);return()=>clearInterval(timer)},[!!live,session?.id]);
 useLayoutEffect(()=>{if(!messagesPane.current)return;const scroll=createChatScroll(messagesPane.current,stickToBottom);chatScroll.current=scroll;return()=>{scroll.dispose();chatScroll.current=null}},[!!state]);
 useLayoutEffect(()=>{chatScroll.current?.reveal()},[session?.id]);
 useEffect(()=>{
  const pane=messagesPane.current;if(!pane||!session)return;
  return followEarlierHistory(pane,()=>!detail.busy&&!session.historyLoading&&(session.messageWindow?.offset>0||session.sharedHistoryOffset>0),()=>{
   stickToBottom.current=false;
   return session.messageWindow?.offset>0?detail.earlier('messages'):act('session.history',{id:session.id,before:session.sharedHistoryOffset,limit:100});
  });
 },[session?.id,session?.messageWindow?.offset,session?.sharedHistoryOffset,session?.historyLoading,detail.busy,act]);
 useLayoutEffect(()=>{const saved=historyScrollAnchor.current;if(!saved)return;historyScrollAnchor.current=null;if(saved.sessionId!==session?.id)return;const pane=messagesPane.current,anchor=[...(pane?.querySelectorAll('[data-message-id]')||[])].find(node=>node.dataset.messageId===saved.messageId);if(anchor)pane.scrollTop+=anchor.getBoundingClientRect().top-saved.top},[session?.id,session?.sharedHistoryOffset,session?.messageWindow?.offset,session?.executionWindow?.offset]);
 useLayoutEffect(()=>{resizeComposer(composerRef.current);chatScroll.current?.update()},[draft,state?.selectedSessionId]);
 useEffect(()=>{const resize=()=>resizeComposer(composerRef.current);window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize)},[]);
 useEffect(()=>{
  if(!state)return;
  for(const m of notificationMessages(state)){
   if(lastNotify.current.has(m.id))continue;
   lastNotify.current.add(m.id);
   if(desktopNotificationsEnabled(state.notificationSettings) && connected && 'Notification'in window && Notification.permission==='granted' && (document.hidden||m.sessionId!==state.selectedSessionId)){
    const notice=new Notification('Amplifier',{body:notificationBody(m,state.notificationSettings),icon:logo,tag:m.id});
    notice.onclick=()=>{window.focus();act('session.select',{id:m.sessionId});act('view.update',{patch:{mode:'text'}})};
   }
  }
 },[state,connected,act]);
 useEffect(()=>{voiceClient.current=new VoiceClient({request,onState:value=>{setVoice(value);if(value.status!=='connected')visualClient.current?.stop()},onError:e=>setError(e.message||String(e))});visualClient.current=new VoiceVisualClient({request,onState:setVisual,getVoice:()=>({...voiceClient.current?.state,sessionId:latest.current?.voice?.sessionId})});return()=>{visualClient.current?.dispose();voiceClient.current?.dispose()}},[]);
 useEffect(()=>{visualClient.current?.sync(voice,connected,state?.voice?.visual)},[voice,connected,state?.voice?.visual]);
 const modeChange=m=>act('view.update',{patch:{mode:m}});
 const open=p=>{if(!latest.current?.view?.panel)panelReturnFocus.current=document.activeElement;setError('');if(p==='new-session'){const next={title:'A new conversation',bundle:state.settings?.bundle||'work',workspace:state.settings?.workspace||''};setSetup(next);act('view.update',{patch:{panel:p,sessionSetup:next,toolbarMenuOpen:false}})}else act('view.update',{patch:{panel:p,toolbarMenuOpen:false}})};
 const close=()=>{setPreview(false);act('view.update',{patch:{panel:null}})};
 useEffect(()=>{
  if(!panel)return;
  const previous=panelReturnFocus.current||document.activeElement;
  const dialog=root.current?.querySelector('[role=dialog]');
  const controls=()=>[...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]')].filter(el=>el.getClientRects().length);
  controls()[0]?.focus();
  const handler=e=>{
   if(e.key==='Escape'){setPreview(false);act('view.update',{patch:{panel:null}})}
   if(e.key==='Tab'){const items=controls(),first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}
  };
  window.addEventListener('keydown',handler);
  return()=>{window.removeEventListener('keydown',handler);panelReturnFocus.current=null;if(previous?.isConnected)previous.focus()};
 },[panel,act]);
 function preserveOtherDraft(sessionId){
  const payload=stagedDraftPayload.current;
  if(!stagedDraft.current||payload?.sessionId===sessionId)return;
  // A new chat's edit/send must not cancel the previous chat's unsaved debounce.
  // Queue its explicitly bound save without blocking independent navigation.
  clearTimeout(draftTimer.current);saveDraft(payload,stagedDraft.current).catch(e=>setError(actionErrorMessage(e)));
  stagedDraft.current=null;
 }
 function saveDraft(payload,token){
  if(draftSaves.current.has(payload))return draftSaves.current.get(payload);
  const save=(async()=>{
  // Autosave may become ready before the first conversation has an ID. Keep its
  // original optimistic position, then save against the conversation it created.
  if(payload.sessionId===null&&creatingSession.current){const current=await creatingSession.current;payload.sessionId=current.id;pendingView.current.bindDraft(token,current.id)}
  try{return await dispatch('view.update',payload,{pendingViewToken:token})}
  finally{if(stagedDraft.current===token)stagedDraft.current=null}
  })();
  draftSaves.current.set(payload,save);save.catch(()=>draftSaves.current.delete(payload));return save;
 }
 function editDraft(value){const sessionId=latest.current?.selectedSessionId??null;preserveOtherDraft(sessionId);setDraft(value);lastDraft.current=value;pendingView.current.settle(stagedDraft.current);const token=pendingView.current.add({draft:value},sessionId),payload={patch:{draft:value},sessionId};stagedDraft.current=token;stagedDraftPayload.current=payload;if(latest.current)setState(pendingView.current.apply(latest.current));clearTimeout(draftTimer.current);draftTimer.current=setTimeout(()=>{saveDraft(payload,token).catch(e=>setError(actionErrorMessage(e)))},220)}
 async function ensureSession(){const current=latest.current?.sessions?.find(row=>row.id===latest.current?.selectedSessionId);if(current)return current;if(!creatingSession.current)creatingSession.current=dispatch('session.create',{}).then(result=>{
  const created=result.state.sessions.find(row=>row.id===result.state.selectedSessionId),payload=stagedDraftPayload.current;
  if(stagedDraft.current&&payload?.sessionId===null){payload.sessionId=created.id;pendingView.current.bindDraft(stagedDraft.current,created.id);clearTimeout(draftTimer.current);saveDraft(payload,stagedDraft.current).catch(e=>setError(actionErrorMessage(e)));if(latest.current)setState(pendingView.current.apply(latest.current))}
  return created;
 }).finally(()=>{creatingSession.current=null});return creatingSession.current}
 function addFiles(files){if(!files?.length||executionUnavailable||historyPending)return;const target=ensureSession();uploadCount.current++;setUploading(true);setError('');const run=async()=>{try{const current=await target;for(const file of files)await dispatch('attachment.add',{sessionId:current.id,name:file.name,base64:await readAttachment(file)})}catch(error){setError(error.message)}finally{uploadCount.current--;setUploading(uploadCount.current>0)}};uploadQueue.current=uploadQueue.current.then(run,run)}
 async function deliver(entry){
  if(deliveries.current.has(entry.id))return;
  deliveries.current.add(entry.id);outbox.update(entry.id,{status:'sending',error:''});
  try{
   if(!entry.sessionId){const current=await ensureSession();entry=outbox.update(entry.id,{sessionId:current.id});}
   const result=await dispatch('conversation.send',{sessionId:entry.sessionId,text:entry.text,attachmentIds:entry.attachmentIds,via:entry.via,preserveDraft:true},{id:entry.commandId,feedback:false});
   outbox.update(entry.id,['sending','unknown'].includes(result.delivery)?{status:'unknown',error:'Delivery has not been confirmed. Checking again will not run it twice.'}:null);
  }catch(error){
   const received=latest.current?.sessions?.find(row=>row.id===entry.sessionId)?.messages?.some(row=>row.inputId===entry.commandId&&row.delivery?.status==='accepted');
   if(received&&error.code!=='session_busy')outbox.update(entry.id,null);
   else outbox.update(entry.id,{status:error.status>=400&&error.status<500&&error.status!==408?'failed':'unknown',error:actionErrorMessage(error)});
  }finally{deliveries.current.delete(entry.id)}
 }
 async function retryMessage(message,text){
  const previous=outbox.current.current.find(row=>row.id===(message.localDelivery?.id||message.id));if(!previous||previous.status==='sending')return;
  if(previous.status==='unknown'&&text!==undefined)return;
  const next=outbox.update(previous.id,{...(previous.status==='failed'?{commandId:crypto.randomUUID(),...(text!==undefined?{text}:{})}:{}),status:'sending',error:''});
  if(text!==undefined)act('view.update',{patch:{messageEdit:null}});
  await deliver(next);
 }
 async function send(e){
  e?.preventDefault();if((!draft.trim()&&!availableAttachments.length)||uploadCount.current||busy||historyPending||executionUnavailable)return;
  preserveOtherDraft(session?.id??null);
  const submittedText=draft,id=crypto.randomUUID(),attachments=availableAttachments;
  if(!submittedText.trim()&&!attachments.length)return;
  chatScroll.current?.reveal();setError('');editDraft('');clearTimeout(draftTimer.current);
  const blankToken=stagedDraft.current;
  let entry=outbox.update(id,{commandId:id,sessionId:session?.id??null,text:submittedText,via:mode==='text'?'text':'chat',attachmentIds:attachments.map(file=>file.id),attachments,status:'sending',createdAt:Date.now()/1000});
  try{
   await saveDraft(stagedDraftPayload.current,blankToken);
   const current=session||await ensureSession();entry=outbox.update(id,{sessionId:current.id});
   await deliver(entry);
  }catch(error){outbox.update(id,{status:'failed',error:actionErrorMessage(error)});}
  finally{pendingView.current.settle(blankToken);if(stagedDraft.current===blankToken)stagedDraft.current=null;if(latest.current)setState(pendingView.current.apply(latest.current));}
 }
 async function create(e){e.preventDefault();setBusy(true);try{await dispatch('session.create',setup);await dispatch('view.update',{patch:{panel:null}})}catch(e){setError(actionErrorMessage(e))}finally{setBusy(false)}}
 async function startCall(){setError('');try{await ensureSession();await dispatch('view.update',{patch:{mode:'call'}});await dispatch('call.start',{})}catch(e){setError(actionErrorMessage(e))}}
 const callActive=!['idle','ended','error'].includes(voice.status||'idle');
 const activeCss=preview?themeDraft:state?.theme?.css||'';
 const presentation=shell.composition.presentation;
 const requestedScheme=presentation.scheme||view.scheme;
 const scheme=requestedScheme==='system'?'light dark':requestedScheme||'light';
 const conversationChoices=headerChatChoices(state);
 const sendingHere=outbox.entries.some(row=>row.sessionId===(session?.id??null)&&row.status==='sending');
 if(!state)return <div className="boot"><img src={logo}/><h1>Amplifier</h1><p>{error||'Connecting to your workspace…'}</p>{error&&<button onClick={()=>setBootAttempt(value=>value+1)}>Retry connection</button>}</div>;
 return <div id="amp-one" className="a-chat-shell" ref={root} data-layout={presentation.layout||view.layout||'balanced'} data-density={presentation.density||'comfortable'} style={{colorScheme:scheme,...(presentation.accent?{'--a-accent':presentation.accent}:{})}} data-part="app">
  {activeCss&&<style>{activeCss}</style>}
  <header className="a-top" data-part="header"><div className="a-brand" data-part="brand"><img src={logo} alt="Amplifier logo"/><span>Amplifier</span></div><span className={`a-dot ${connected?'':'pending'}`} title={connected?'Connected to your local Amplifier':'Reconnecting…'}/><ConversationSelect state={state} session={session} choices={conversationChoices} onSelect={id=>{stickToBottom.current=true;act('session.select',{id})}}/><div className="a-top-end"><button className="a-soft a-new-chat" onClick={()=>{stickToBottom.current=true;act('session.create',{})}} data-action="session.create" disabled={state.workspaces?.find(w=>w.id===state.selectedWorkspaceId)?.available===false} aria-label="New conversation"><Plus/><span>New conversation</span></button><button className="a-icon" aria-label="Session details" data-action="view.update" onClick={()=>open('session-details')}><Info/></button><button className="a-icon" onClick={()=>open('activity')} data-action="view.update" aria-label="Activity"><Bell/><AttentionBadge state={state}/></button><button className="a-icon" onClick={()=>open('settings')} data-action="view.update" aria-label="Settings"><Settings/><AttentionBadge state={state} settings/></button><MoreAppActions state={state} act={act} openPanel={open}/><CanvasToggle state={state} act={act} layout={presentation.layout}/></div></header>
  {error&&<div className="a-alert" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={()=>{setError('');act('view.update',{patch:{notice:null}})}} data-action="view.update"><X/></button></div>}
  <ConversationError state={state} session={session} act={act}/>{!state.runtime?.available&&<div className="a-alert a-runtime"><span><strong>Connect Amplifier to get started.</strong> {state.runtime?.error||'The Amplifier runtime is not installed. Install this app with its runtime dependencies, then relaunch.'}</span><button className="a-link" onClick={()=>open('settings')} data-action="view.update">Setup details <ChevronRight/></button></div>}
  <WorkspaceLayout state={state} act={act} presentation={presentation}><WorkspaceRail shell={shell} state={state} session={session} act={act} selectSession={id=>{stickToBottom.current=true;act('session.select',{id})}} newSession={workspace=>{stickToBottom.current=true;act('session.create',workspace?{workspace}:{})}}/><section className={`a-conversation ${messages.length||historyPending||session?.historyError||executionUnavailable?'has-messages':'is-empty'}`} data-part="conversation" aria-label="Shared conversation">
   {callActive&&<div className="a-call-strip" data-part="voice"><AudioLines/><span>{voice.model||'Voice'} · {voice.status}</span><button className="a-icon" aria-label={voice.muted?'Unmute microphone':'Mute microphone'} data-action="call.mute" onClick={()=>act('call.mute',{muted:!voice.muted})}>{voice.muted?<MicOff/>:<Mic/>}</button><button className="a-soft" data-action="call.end" onClick={()=>act('call.end')}>End call</button>{voice.fallbackReason&&<small>{voice.fallbackReason}</small>}</div>}
   <ComputationPanel sessionId={session?.id}/><OperationsPanel sessionId={session?.id}/>
   <VoiceVisualControls voice={{...voice,sessionId:state.voice?.sessionId}} visual={visual} client={visualClient} dispatch={dispatch} onError={e=>setError(e.message||String(e))}/>
   <div className="a-messages" ref={messagesPane} data-part="messages" role="log" aria-label="Conversation messages" aria-live="polite" aria-busy={!!session?.historyLoading}><div className="a-session-history">{detail.controls}</div><SessionHistoryControls session={session?.messageWindow?.offset>0?{...session,sharedHistoryOffset:0}:session} act={act} onLoadEarlier={()=>{stickToBottom.current=false}}/>{session?.parentId&&<div className="a-chat-origin"><GitBranch/><span>{!isTopLevelChat(session)?'Subagent conversation':session.editOrigin?'Continued from an edited message':'Forked conversation'}</span><button type="button" className="a-link" data-action="session.select" disabled={!state.sessions.some(s=>s.id===session.parentId)} onClick={()=>act('session.select',{id:session.parentId})}>{!isTopLevelChat(session)?'Open parent chat':'Open original chat'}</button></div>}{workPlacement.before.map(turnId=><TurnTimeline key={turnId} data={execution} turnId={turnId} state={state} act={act}/>)}{messages.length===0&&!historyPending&&!session?.historyError&&!executionUnavailable?<div className="a-empty"><img src={logo} alt=""/><h2>What shall we work on?</h2><p>Bring an idea, a question, or a file.</p></div>:messages.map(m=><React.Fragment key={m.id}><MessageEntry message={m} session={session||{id:null}} state={state} act={act} stamp={nowLabel} working={working} forkTurn={turnEnds.get(m.id)} retry={retryMessage} dispatch={dispatch}/>{(workPlacement.after.get(m.id)||[]).map(turnId=><TurnTimeline key={turnId} data={execution} turnId={turnId} state={state} act={act}/>)}<ArtifactLinks state={state} message={m} act={act}/></React.Fragment>)}{session?.streaming&&<article className="a-message a-assistant"><div className="a-msg-meta"><strong>Amplifier</strong><span>Working…</span></div><Markdown text={session.streaming}/></article>}<div ref={messagesEnd}/></div>
   {live&&<div className="a-live-activity" data-part="activity" role="status" aria-live="polite"><span className="a-activity-pulse" aria-hidden="true"><b/><b/><b/></span><div><strong>{live.label}</strong>{(live.toolLabels.length>0||live.lastTool||live.workerCount>0)&&<small>{live.toolLabels.join(' · ')}{live.lastTool&&`Last tool: ${live.lastTool}`}{live.workerCount>0&&live.phase!=='workers'?`${live.toolLabels.length||live.lastTool?' · ':''}${live.workerCount} active ${live.workerCount===1?'worker':'workers'}`:''}</small>}</div>{live.elapsed&&<span className="a-activity-elapsed" role="timer" aria-live="off" aria-label={`Elapsed ${live.elapsed}`}>{live.elapsed}</span>}</div>}
   {!!session?.approvals?.filter(a=>a.status==='pending'||!a.status).length&&<section className="a-card a-approvals" data-part="approvals"><div className="a-card-header"><h2>Needs your attention</h2></div>{session.approvals.filter(a=>a.status==='pending'||!a.status).map(a=><div key={a.id} data-approval-id={a.id}><strong>{a.title||a.tool||'Approval requested'}</strong><p>{a.prompt||a.message||a.description}</p>{a.details&&<pre>{typeof a.details==='string'?a.details:pretty(a.details)}</pre>}<div className="a-dialog-actions"><button className="a-primary" data-action="approval.respond" onClick={()=>act('approval.respond',{id:a.id,decision:'approve'})}><Check/>Allow</button><button className="a-soft" data-action="approval.respond" onClick={()=>act('approval.respond',{id:a.id,decision:'deny'})}>Deny</button></div></div>)}</section>}
   <Questions session={session} dispatch={dispatch}/>
   <form className={`a-composer ${dragOver?'drag-over':''}`} data-part="composer" data-action="conversation.send" onSubmit={send} onDragOver={e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();if(!executionUnavailable)setDragOver(true)}}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget))setDragOver(false)}} onDrop={e=>{if(e.dataTransfer.files.length){e.preventDefault();setDragOver(false);addFiles([...e.dataTransfer.files])}}} onPaste={e=>{if(executionUnavailable){e.preventDefault();return}const files=[...e.clipboardData.items].filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);if(files.length){e.preventDefault();const text=e.clipboardData.getData('text/plain');if(text){const input=composerRef.current,start=input?.selectionStart??draft.length,end=input?.selectionEnd??start;editDraft(draft.slice(0,start)+text+draft.slice(end))}addFiles(files)}}}>
    {outbox.storageError&&outbox.entries.length>0&&<p role="alert" className="a-upload-status">This browser could not save the pending message. Keep this tab open until delivery is confirmed.</p>}
    <ComposerOwnership session={session} runtimeAvailable={state.runtime?.available!==false} dispatch={dispatch}>
    <AttachmentStrip items={availableAttachments} remove={id=>act('attachment.remove',{sessionId:session.id,id})}/>{uploading&&<p role="status" className="a-upload-status">Uploading attachments…</p>}{dragOver&&<p className="a-upload-status">Drop files or images to attach</p>}
    <textarea ref={composerRef} rows={1} disabled={executionUnavailable} value={draft} onChange={e=>editDraft(e.target.value)} data-action="view.update" aria-label="Message Amplifier" placeholder="Ask Amplifier anything…" onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/>
    <div className="a-compose-bottom">{sendingHere&&<span role="status" className="a-caption">Sending message…</span>}<div className="a-compose-tools"><input ref={fileInput} type="file" multiple className="a-file-input" aria-label="Attach files" data-action="attachment.add" onChange={e=>{addFiles([...e.target.files]);e.target.value=''}}/><button type="button" className="a-icon" aria-label="Add attachments" title="Attach files or images · up to 8 MB each" disabled={uploading||executionUnavailable||historyPending} data-action="attachment.add" onClick={()=>fileInput.current.click()}><Plus/></button><button type="button" className={`a-icon ${mode==='text'?'selected':''}`} aria-label="Notify me when ready" title="Notify me when the response is ready" aria-pressed={mode==='text'} data-action="view.update" onClick={()=>{modeChange(mode==='text'?'chat':'text');if(mode!=='text'&&'Notification'in window&&Notification.permission==='default')act('notification.request')}}><Bell/></button><ModelControl state={state} session={session} act={act} ensureSession={ensureSession} working={working}/><BundleControl state={state} session={session} act={act} working={working}/></div><div className="a-compose-tools">{working&&<button type="button" className="a-icon" aria-label="Stop response" data-action="conversation.stop" onClick={()=>act('conversation.stop')}><Square/></button>}<button type="button" className={`a-icon a-call-button ${callActive?'selected':''}`} aria-label={callActive?'End voice call':'Start voice call'} disabled={!callActive&&(historyPending||executionUnavailable)} data-action={callActive?'call.end':'call.start'} onClick={callActive?()=>act('call.end'):startCall}><AudioLines/></button><button className="a-send" aria-label={working?'Send a correction':'Send message'} disabled={(!draft.trim()&&!availableAttachments.length)||busy||uploading||historyPending||executionUnavailable}><ArrowUp/></button></div></div>
    </ComposerOwnership>
   </form>
  </section><McpAppThemeProvider scheme={requestedScheme}><AgentCanvas state={state} act={act} dispatch={dispatch}/></McpAppThemeProvider></WorkspaceLayout>
  <FeedbackNotice state={state} act={act}/>
  {panel&&<div className={`a-overlay ${(panel==='settings'||panel==='appearance')?'a-settings-overlay a-settings-redesign-overlay':''}`} data-part="overlay" onPointerDown={e=>{outsidePointer.current=e.target===e.currentTarget}} onClick={e=>{if(outsidePointer.current&&e.target===e.currentTarget)close()}}><section role="dialog" aria-modal="true" aria-labelledby="panel-title" className={`a-dialog ${panel==='appearance'||panel==='agent'||panel==='runtime'||panel==='settings'?'wide':''}`} data-part="dialog"><div className="a-dialog-head"><h2 id="panel-title">{{'new-session':'Start a conversation',appearance:'Settings',agent:'What the agent sees',settings:'Settings',activity:'Ready for you',feedback:'Send feedback',worker:'Give it a worker lane','delete-session':'Remove chat?',runtime:'Session controls','session-details':'This conversation','subagent-history':'Subagent history',coordination:'Tasks and workers'}[panel]||panel}</h2><button className="a-icon" data-action="view.update" aria-label="Close panel" onClick={close}><X/></button></div>
   {error&&<div className="a-alert" role="alert"><span>{error}</span></div>}{panel==='new-session'&&<form onSubmit={create} data-action="session.create"><p>Your bundle brings the tools, agents, and instructions. Your workspace is where the work happens.</p>{Object.keys(initialSetup).map(key=><React.Fragment key={key}><label htmlFor={'setup-'+key}>{key[0].toUpperCase()+key.slice(1)}</label>{key==='workspace'?<PathField id={'setup-'+key} value={setup[key]} directory state={state} act={act} onChange={value=>{const next={...setup,[key]:value};setSetup(next);act('view.update',{patch:{sessionSetup:next}})}}/>:key==='bundle'?<BundlePicker id={'setup-'+key} value={setup[key]} state={state} act={act} onChange={value=>{const next={...setup,[key]:value};setSetup(next);act('view.update',{patch:{sessionSetup:next}})}}/>:<input id={'setup-'+key} value={setup[key]} data-action="view.update" onChange={e=>{const next={...setup,[key]:e.target.value};setSetup(next);act('view.update',{patch:{sessionSetup:next}})}} placeholder={key==='workspace'?'/absolute/path/to/your/project':key==='bundle'?'anchors or git+https://…':''}/>}</React.Fragment>)}<p className="a-caption">Use a community bundle name, local bundle file, or git URL supported by Amplifier Foundation.</p><div className="a-dialog-actions"><button className="a-primary" disabled={busy}>{busy?<Loader/>:<Plus/>}Create conversation</button></div></form>}
   {panel==='session-details'&&<><dl className="a-meta-grid"><div><dt>Bundle</dt><dd>{session?.bundle||state.settings.bundle}</dd></div><div><dt>Workspace</dt><dd>{session?.workspace||state.settings.workspace}</dd></div><div><dt>Orchestrator</dt><dd>{session?.orchestrator||'Bundle orchestrator'}</dd></div></dl><div className="a-dialog-actions"><button className="a-soft" data-action="view.update" onClick={()=>open('runtime')}><Settings/>Session controls</button><SubagentHistoryButton state={state} session={session} act={act}/><button className="a-soft" data-action="view.update" onClick={()=>open('coordination')}><GitBranch/>Tasks and workers</button><button className="a-soft" data-action="view.update" onClick={()=>open('worker')}><GitBranch/>Delegate work</button><button className="a-soft" data-action="view.update" onClick={()=>open('new-session')}><Plus/>New conversation options</button></div></>}
   {panel==='coordination'&&<CoordinationPanel dispatch={dispatch}/>}
   {panel==='subagent-history'&&<SubagentHistory state={state} session={session} act={act}/>}
   {panel==='activity'&&<ActivityPanel state={state} act={act}/>}
   {panel==='feedback'&&<FeedbackPanel state={state} act={dispatch}/>}
   {panel==='runtime'&&<RuntimeSettings state={state} session={session} act={act}/>}
   {panel==='worker'&&<form data-action="worker.spawn" onSubmit={async e=>{e.preventDefault();try{await dispatch('worker.spawn',{instruction:workerDraft});setWorkerDraft('');close()}catch(error){setError(error.message)}}}><p>Tell the worker what to investigate or build. Results return to this conversation.</p><label htmlFor="worker-instruction">Work to delegate</label><textarea id="worker-instruction" value={workerDraft} data-action="view.update" onChange={e=>{setWorkerDraft(e.target.value);act('view.update',{patch:{workerDraft:e.target.value}})}} placeholder="Review the project and propose a focused implementation plan…"/><div className="a-dialog-actions"><button className="a-primary" disabled={!workerDraft.trim()}><GitBranch/>Start worker</button></div></form>}

   {panel==='agent'&&<><p>Every app control uses the same action interface. The agent can read the selected conversation, worker lanes, drafts, open panels, and the controls currently on screen.</p><div className="a-form-grid"><div><label>Current state</label><pre className="a-state-view">{pretty({...state,devices:Object.fromEntries(Object.entries(state.devices||{}).map(([id,device])=>[id,{clientId:device.clientId,viewport:device.viewport,controls:device.controls?.length,visibleTextLength:device.visibleText?.length,voice:device.voice,notificationPermission:device.notificationPermission}])),deviceCommands:(state.deviceCommands||[]).map(({id,type,origin,createdAt})=>({id,type,origin,createdAt})),theme:{name:state.theme?.name,css:`${state.theme?.css?.length||0} characters`},view:{...view,themeDraft:view.themeDraft?`${view.themeDraft.length} characters`:undefined}})}</pre></div><div><label>Visible controls</label><pre className="a-state-view">{pretty(visibleView(root.current,clientId).controls?.map(c=>({label:c.label,action:c.action,disabled:c.disabled})))}</pre></div></div><label htmlFor="agent-action">Run an app action</label><select id="agent-action" value={agentAction} data-action="view.update" onChange={e=>{setAgentAction(e.target.value);act('view.update',{patch:{agentAction:e.target.value}})}}>{catalog.map(a=><option key={a.name||a.action} value={a.name||a.action}>{a.name||a.action}</option>)}</select><label htmlFor="agent-args">Arguments (JSON)</label><textarea id="agent-args" className="a-css-editor" style={{minHeight:100}} value={agentArgs} data-action="view.update" onChange={e=>{setAgentArgs(e.target.value);act('view.update',{patch:{agentArgs:e.target.value}})}}/><div className="a-dialog-actions"><button className="a-primary" data-action={agentAction} onClick={()=>{try{act(agentAction,JSON.parse(agentArgs))}catch(e){setError(actionErrorMessage(e))}}}>Run action</button><button className="a-soft" data-action="state.export" onClick={()=>act('state.export')}><Download/>Export app state</button></div><p className="a-caption">Also available to integrations as window.amplifier.getState(), getActions(), and dispatch().</p></>}
   {panel==='delete-session'&&<><p>Remove {session?.title} from this list? Its shared history stays on disk. Any work in progress in this app will stop.</p><div className="a-dialog-actions"><button className="a-primary" data-action="session.delete" onClick={async()=>{await act('session.delete',{id:session.id});close()}}>Remove chat</button><button className="a-soft" data-action="view.update" onClick={close}>Keep conversation</button></div></>}{(panel==='settings'||panel==='appearance')&&<SettingsExperience state={state} session={session} act={act} open={open} appearance={<><p>A complete skin in one CSS file. Edit its colors, typography, layout, backgrounds, or embedded artwork, then share it with someone else.</p><div className="a-form-grid"><div><label htmlFor="theme-name">Skin name</label><input id="theme-name" value={themeName} data-action="view.update" onChange={e=>{setThemeName(e.target.value);act('view.update',{patch:{themeDraftName:e.target.value}})}}/></div><div><label htmlFor="scheme">Appearance</label><select id="scheme" value={presentation.scheme||view.scheme||'system'} data-action="shell.changes.apply" onChange={e=>shell.setPresentation({scheme:e.target.value}).catch(e=>setError(e.message))}><option value="light">Light</option><option value="dark">Dark</option><option value="system">Follow device</option></select></div></div><label htmlFor="layout">Layout</label><select id="layout" value={presentation.layout||view.layout||'balanced'} data-action="shell.changes.apply" onChange={e=>shell.setPresentation({layout:e.target.value}).catch(e=>setError(e.message))}><option value="balanced">Conversation with context alongside</option><option value="conversation">More room for conversation</option><option value="work">Context on the left</option></select><label htmlFor="theme-css">The whole skin</label><textarea id="theme-css" spellCheck={false} className="a-css-editor" value={themeDraft} data-action="view.update" onChange={e=>{setThemeDraft(e.target.value);setPreview(false);act('view.update',{patch:{themeDraft:e.target.value,themePreview:false}})}}/><div className="a-dialog-actions"><button className="a-primary" data-action="theme.apply" onClick={async()=>{await act('theme.apply',{name:themeName,css:themeDraft});setPreview(false)}}><Check/>Apply skin</button><button className="a-soft" data-action="view.update" onClick={()=>{setPreview(!preview);act('view.update',{patch:{themePreview:!preview,themeDraft,themeDraftName:themeName}})}}>{preview?'End preview':'Preview'}</button><button className="a-soft" data-action="view.update" onClick={()=>{setThemeDraft(state.theme?.css||defaultSkin);setThemeName(state.theme?.name||'Amplifier Unified');setPreview(false);act('view.update',{patch:{themePreview:false,themeDraft:state.theme?.css||defaultSkin,themeDraftName:state.theme?.name||'Amplifier Unified'}})}}>Revert edits</button><button className="a-soft" data-action="theme.export" onClick={()=>act('theme.export')}><Download/>Export skin</button></div><label htmlFor="theme-import">Import a shared CSS skin</label><FileDrop id="theme-import" accept=".css,text/css" action="view.update" label="Choose a skin" hint="Drop a CSS skin here · up to 250 KB" onFile={async file=>{if(!file)return;if(file.size>250000)throw new Error('Please choose a skin smaller than 250 KB.');const css=await file.text();setThemeDraft(css);setThemeName(file.name.replace(/\.amplifier\.css$|\.css$/,''));setPreview(false);act('view.update',{patch:{themeDraft:css,themeDraftName:file.name,themePreview:false}})}}/><div className="a-dialog-actions"><button className="a-link" data-action="theme.reset" onClick={()=>act('theme.reset')}><RefreshCw/>Restore default skin</button></div></>}/> }
  </section></div>}
 </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
