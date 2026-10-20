import React,{useContext,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,Search,Plus,SlidersHorizontal,CheckCircle2,Inbox,Folder,X} from 'lucide-react';
import {WorkNavigationContext} from './work-navigation';
import {useNavigationController} from './shell/navigation-components';
import {NavigationResourceStatus} from './shell/navigation-resource-status';
import {hasNavigationResource} from './shell/navigation-demand';
import {useChatTitle} from './use-chat-title';
import {useActivityClock} from './navigation-details';
import {readItems} from './attention';
import {relativeActivity,activityFor} from './navigation-presentation';
import {startingWorkView,currentWorkPage,currentWorkContext,workViews,workReadingKey,rememberHomeScroll,readFocusCursor,saveFocusCursor,focusPosition,stepFocus} from './adaptive-work.js';
import './adaptive-work.css';

const groups={
 attention:{title:'Needs you',empty:'No decisions waiting.',verb:'Review request',meaning:'chats needing your attention'},
 unread:{title:'Ready for you',empty:'No unread responses.',verb:'Read response',meaning:'chats with unread responses'},
 working:{title:'Working',empty:'Nothing running right now.',verb:'View progress',meaning:'chats currently running'},
 idle:{title:'Pick up again',empty:'Your saved chats will appear here.',verb:'Continue chat',meaning:'other saved chats'}
};

export function WorkViewSwitcher({value,onChange,quiet=false}){
 return quiet?<label className="a-work-view-quiet"><span>View</span><select aria-label="Work view" value={value} onChange={event=>onChange(event.target.value)}>{workViews.map(key=><option key={key} value={key}>{key[0].toUpperCase()+key.slice(1)}</option>)}</select></label>:
  <div className="a-work-view-switch" role="group" aria-label="Work view">{workViews.map(key=><button key={key} type="button" aria-pressed={value===key} onClick={()=>onChange(key)}>{key[0].toUpperCase()+key.slice(1)}</button>)}</div>;
}

function WorkCard({chat,kind,state,act,hero=false}){
 const title=useChatTitle(chat).title,activity=activityFor(chat,state),now=useActivityClock();
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const receipt=(state.attention?.items||[]).find(item=>item.sessionId===chat.id&&!item.read&&item.id==='completion:'+chat.id);
 const at=chat.recentActivityAt||chat.navigationActivityAt||chat.createdAt,time=relativeActivity(at,now);
 const verb=kind==='attention'&&activity.label==='Answer requested'?'Review options':groups[kind].verb;
 async function review(){if(!receipt||busy)return;setBusy(true);setError('');try{await readItems(act,[receipt])}catch(error){setError(error.message)}finally{setBusy(false)}}
 return <article className={'a-work-card'+(hero?' is-hero':'')} data-session-id={chat.id} data-kind={kind}>
  <div className="a-work-card-meta"><span>{chat.location?.kind==='managed'?'On its own':chat.workspaceName||'Project'}</span>{at>0&&<time dateTime={new Date(at*1000).toISOString()} title={'Last activity: '+time.long}>Updated {time.short==='now'?'just now':time.short+' ago'}</time>}</div>
  <button className="a-work-card-open" type="button" onClick={()=>act('session.select',{id:chat.id})} data-action="session.select"><h3>{title}</h3>{chat.description&&<p>{chat.description}</p>}<span className={'a-work-card-cta'+(hero?' a-primary':'')}>{verb}<ArrowRight size={16}/></span></button>
  <div className="a-work-card-foot"><span className="a-work-status" data-kind={activity.kind}><i/>{activity.kind==='idle'?'Saved conversation':activity.label}</span>{receipt&&<button type="button" disabled={busy} aria-label={'Mark response reviewed: '+title} onClick={review}>{busy?'Saving…':'Mark read'}<CheckCircle2 size={14}/></button>}</div>
  {error&&<p role="alert">{error}</p>}
 </article>;
}

export function AdaptiveWorkHome({host,state,act,presentation}){
 const model=useNavigationController(host,'chats'),nav=useContext(WorkNavigationContext),view=state.view||{};
 const pinnedScope=model.state.workHomePinnedScope,value=model.state.workHome,ready=hasNavigationResource(model.state,'workHome')&&currentWorkPage(value,view,pinnedScope);
 const level=presentation.interfaceDetail||presentation.executionDetail||'standard',mode=view.workHomeView||startingWorkView(presentation);
 const scope=pinnedScope||view.workHomeScope||'all',filter=view.workHomeFilter||'all',query=view.workHomeQuery||'';
 const context=currentWorkContext(model.state.workHomeContext,view,pinnedScope),workspace=context?.workspace;
 const [search,setSearch]=useState(query),timer=useRef(null),pendingSearch=useRef(null),flush=useRef(null),searchInput=useRef(null),settingsButton=useRef(null),[settingsOpen,setSettingsOpen]=useState(false),pane=useRef(null);
 const [focusCursor,setFocusCursor]=useState(()=>readFocusCursor(window)),focusPending=useRef(null),focusRestore=useRef(null);
 useEffect(()=>{setSearch(query)},[query]);useEffect(()=>()=>flush.current?.(),[]);
 const patch=values=>act('view.update',{patch:values});
 const readingKey=workReadingKey({...view,workHomeScope:scope},mode);
 useLayoutEffect(()=>{if(ready&&pane.current)return rememberHomeScroll(pane.current,readingKey)},[ready,readingKey]);
 useEffect(()=>{if(!view.workHomeView)patch({workHomeView:mode,navPinned:level==='detailed'})},[view.workHomeView]);
 const flushSearch=()=>{clearTimeout(timer.current);const value=pendingSearch.current;pendingSearch.current=null;if(value!==null)patch({workHomeQuery:value,workHomePages:{}})};
 flush.current=flushSearch;
 const chooseScope=scope=>{if(pinnedScope)return;clearTimeout(timer.current);pendingSearch.current=null;setSearch('');patch({workHomeScope:scope,workHomeQuery:'',workHomePages:{}})};
 const changeSearch=value=>{setSearch(value);pendingSearch.current=value;clearTimeout(timer.current);timer.current=setTimeout(flushSearch,220)};
 const closeSettings=()=>{setSettingsOpen(false);settingsButton.current?.focus()};
 const pages=ready?value.groups:{},total=Object.values(pages).reduce((sum,page)=>sum+page.total,0);
 const stateForCards={...model.state,attention:{...model.state.attention,items:model.state.workHomeAttention||[]}};
 const projects=[...new Map([...(model.state.workspaceShortcuts||[]).map(row=>({...row,id:row.workspaceId||row.id,name:row.customName||row.name})),...(workspace?[workspace]:[])].filter(row=>row.id&&row.name).map(row=>[row.id,row])).values()];
 const setFilter=key=>patch({workHomeFilter:key});
 // Search/page loading must not disable a click as the search field blurs.
 // Creation needs the confirmed project, not a matching page of search results.
 const canStart=!!context&&!context.unavailable&&(scope==='all'||scope==='managed'||!!workspace?.path);
 const newChat=()=>{if(canStart)return nav.newChat(scope==='all'||scope==='managed'?{location:{kind:'managed'}}:{workspaceId:workspace.id,workspace:workspace.path,location:{kind:'workspace'}})};
 function pageGroup(kind,limit){
  const page=pages[kind];if(!page)return null;
  const items=limit?page.items.slice(0,limit):page.items;
  return <section className="a-work-group" key={kind} aria-label={groups[kind].title}><div className="a-work-group-heading"><h2>{groups[kind].title}</h2><span>{page.total}</span>{limit&&page.total>limit&&<button type="button" onClick={()=>setFilter(kind)}>See all<ArrowRight size={14}/></button>}</div>
   <div className="a-work-group-items">{items.map(chat=><WorkCard key={chat.id} chat={chat} kind={kind} state={stateForCards} act={act}/>)}</div>
   {!page.total&&<p className="a-work-empty">{groups[kind].empty}</p>}
   {!limit&&page.pages>1&&<div className="a-work-pagination"><button type="button" disabled={!page.index} onClick={()=>patch({workHomePages:{...view.workHomePages,[kind]:page.index-1}})}>Previous</button><span>{page.start+1}–{page.end} of {page.total}</span><button type="button" disabled={page.index+1>=page.pages} onClick={()=>patch({workHomePages:{...view.workHomePages,[kind]:page.index+1}})}>Next</button></div>}
  </section>;
 }
 const focusKey=JSON.stringify([scope,view.workHomeScope||'all',query]);
 const savedFocus=focusCursor?.key===focusKey?focusCursor:null;
 const focusKind=filter!=='all'?filter:pages[savedFocus?.kind]?.items.length?savedFocus.kind:['attention','unread','working','idle'].find(key=>pages[key]?.items.length);
 const focusPage=pages[focusKind],focus=focusPosition(focusPage,savedFocus?.kind===focusKind?savedFocus:null),focusChat=focus&&focusPage.items[focus.index];
 const focusMode=mode==='focus';
 const rememberFocus=value=>{setFocusCursor(value);saveFocusCursor(window,value)};
 useLayoutEffect(()=>{
  // Committed search/scope changes start a pass even with no result or while
  // another Home mode is visible. Otherwise returning revives the old key.
  if(focusCursor&&focusCursor.key!==focusKey){
   focusPending.current=null;focusRestore.current=null;rememberFocus(null);
  }
  if(!ready||!focusMode||!focus)return;
  // Do not overwrite the destination cursor while a page request is settling.
  if(focusPending.current?.key!==focusKey)focusRestore.current=null;
  if(focusPending.current?.key===focusKey&&focusPending.current.page!==(view.workHomePages?.[focusKind]||0))return;
  focusPending.current=null;
  const next={key:focusKey,kind:focusKind,...focus};
  if(JSON.stringify(next)!==JSON.stringify(focusCursor))rememberFocus(next);
  if(focusRestore.current){pane.current?.querySelector(`[data-focus-move="${focusRestore.current}"]`)?.focus();focusRestore.current=null}
 },[ready,focusMode,focusKey,focusKind,focus?.page,focus?.index,focus?.id]);
 const moveFocus=(direction,restart=false)=>{
  const next=restart?{page:0,index:0,id:focusPage.index===0?focusPage.items[0]?.id:null}:stepFocus(focusPage,focus,direction);
  if(!next)return;
  rememberFocus({key:focusKey,kind:focusKind,...next});
  if(next.page!==focusPage.index){
   focusPending.current={key:focusKey,page:next.page};focusRestore.current=direction>0?'next':'previous';
   patch({workHomePages:{...view.workHomePages,[focusKind]:next.page}});
  }
 };
 const lastFocus=focus&&focusPage.start+focus.index+1===focusPage.total;
 const filters=ready&&<div className="a-home-filters" role="group" aria-label="Filter work">
  <button type="button" aria-pressed={filter==='all'} onClick={()=>setFilter('all')}>Overview</button>
  {Object.entries(groups).map(([key,group])=><button type="button" key={key} aria-pressed={filter===key} title={`${pages[key]?.total||0} ${group.meaning} in this scope`} onClick={()=>setFilter(key)}>{group.title}<span>{pages[key]?.total||0}</span></button>)}
 </div>;
 return <div ref={pane} className="a-adaptive-home" data-view={mode} data-level={level} data-part="work-browser">
  <div className="a-home-content">
   <div className="a-home-toolbar">
    <WorkViewSwitcher value={mode} quiet={level==='minimal'} onChange={workHomeView=>patch({workHomeView})}/>
    <div className="a-home-tools">
     <button type="button" className="a-home-create a-soft" aria-label="New chat" disabled={!canStart} onClick={newChat}><Plus/><span>New chat</span></button>
     <button ref={settingsButton} type="button" className="a-home-settings a-icon" aria-label="Home appearance" title="Home appearance" aria-expanded={settingsOpen} aria-controls="home-appearance" onClick={()=>setSettingsOpen(!settingsOpen)}><SlidersHorizontal/></button>
    </div>
   </div>
   {settingsOpen&&<section id="home-appearance" className="a-home-preferences" aria-label="Home appearance" onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();closeSettings()}}}>
    <div className="a-home-preferences-heading"><strong>Make yourself at home</strong><button type="button" className="a-icon" aria-label="Close home appearance" onClick={closeSettings}><X/></button></div>
    <div className="a-home-levels" role="group" aria-label="Appearance detail">{[['minimal','Focused'],['standard','Balanced'],['detailed','Everything']].map(([key,label])=><button type="button" key={key} aria-pressed={level===key} onClick={()=>nav.setPresentation({interfaceDetail:key,executionDetail:key})}>{label}</button>)}</div>
    <label>Starting view<select aria-label="Starting work view" value={presentation.workStartView||'appearance'} onChange={event=>nav.setPresentation({workStartView:event.target.value})}><option value="appearance">Follow appearance</option>{workViews.map(key=><option key={key} value={key}>{key[0].toUpperCase()+key.slice(1)}</option>)}</select></label>
    <button className="a-link" type="button" onClick={()=>{nav.home({workHomeView:startingWorkView(presentation),workHomeFilter:'all'});closeSettings()}}>Go to starting view<ArrowRight/></button>
   </section>}
   <div className="a-home-intro"><h1>{mode==='focus'?'One thing at a time.':mode==='brief'?'Your work, at a glance.':'Everything in motion.'}</h1>{level!=='minimal'&&<p>{mode==='focus'?'Begin with what needs you.':mode==='brief'?'What needs you, what’s ready, and where you left off.':'Follow the work and find your next step.'}</p>}</div>
   <div className="a-home-query">
    <label className="a-home-search"><Search size={18}/><input ref={searchInput} type="search" aria-label="Search your work" placeholder="Find your work…" maxLength={500} value={search} onChange={event=>changeSearch(event.target.value)} onBlur={flushSearch}/>{search&&<button type="button" className="a-icon" aria-label="Clear work search" onClick={()=>{changeSearch('');searchInput.current?.focus()}}><X size={16}/></button>}</label>
    <select aria-label="Work scope" value={scope} disabled={!!pinnedScope} onChange={event=>chooseScope(event.target.value)}>{pinnedScope?<option value={scope}>{projects.find(row=>row.id===scope)?.name||'Unavailable project'}</option>:<><option value="all">All work</option><option value="managed">On its own</option>{projects.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</>}</select>
   </div>
   {workspace&&!context.unavailable&&<div className="a-home-project"><Folder/><strong>{workspace.name}</strong><button type="button" onClick={()=>nav.browse('workspace',workspace.id)}>Open project<ArrowRight/></button></div>}
   <NavigationResourceStatus state={model.state} resource="workHome" label="your work" refresh={model.refreshResources}/>
   {!ready&&!model.state.navigationResourcePending?.workHome&&<p role="status">Loading your work…</p>}
   {ready&&value.unavailable?<p role="alert">This project is unavailable. {!pinnedScope&&<button type="button" onClick={()=>chooseScope('all')}>Show all work</button>}</p>:ready&&<>
    {!focusMode&&filters}
    {!total?<div className="a-home-welcome"><Inbox size={32}/><h2>{query?'No matching work.':'A fresh place to begin.'}</h2><p>{query?'Try another phrase or a different scope.':'Bring a question, an idea, or a project. You can organize it as it grows.'}</p><button type="button" className="a-primary" onClick={()=>query?changeSearch(''):newChat()}>{query?'Clear search':'Start a chat'}<ArrowRight/></button></div>:
     focusMode?<div className="a-home-focus"><div className="a-focus-caption"><h2>{groups[focusKind].title}</h2>{focusPage.total>1&&<button type="button" onClick={()=>patch({workHomeView:'brief',workHomeFilter:focusKind})}>See all {focusPage.total}<ArrowRight size={15}/></button>}</div>{focusChat?<><WorkCard key={focusChat.id} chat={focusChat} kind={focusKind} state={stateForCards} act={act} hero/>
      {focusPage.total>1&&<nav className="a-focus-navigation" aria-label="Browse Focus items">
       <button type="button" data-focus-move="previous" aria-label="Previous Focus item" disabled={!focusPage.start&&!focus.index} onClick={()=>moveFocus(-1)}><ArrowLeft size={16}/>Previous</button>
       <span role="status" aria-live="polite" aria-atomic="true"><span>{focusPage.start+focus.index+1} of {focusPage.total}</span><span className="a-sr-only"> · {focusChat.title}</span></span>
       <button type="button" data-focus-move="next" aria-label={lastFocus?'Start Focus pass again':'Next Focus item'} title="Browse without marking read or changing the work" onClick={()=>moveFocus(1,lastFocus)}>{lastFocus?'Start again':'Next'}<ArrowRight size={16}/></button>
      </nav>}</>:<p className="a-work-empty">{groups[focusKind].empty}</p>}
     </div>:
     filter!=='all'?<div className="a-home-filtered">{pageGroup(filter)}</div>:
     mode==='brief'?<div className="a-home-brief"><div>{pages.attention.total>0&&pageGroup('attention',3)}{pageGroup('unread',3)}</div><div className="a-home-brief-side">{pageGroup('working',3)}{pageGroup('idle',3)}</div></div>:
     <div className="a-home-studio"><div>{pageGroup('attention',5)}{pageGroup('unread',5)}</div><div>{pageGroup('working',5)}</div><div>{pageGroup('idle',5)}</div></div>}
    {focusMode&&<div className="a-focus-other"><span>Explore your work</span>{filters}</div>}
   </>}
  </div>
 </div>;
}
