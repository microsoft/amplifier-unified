import React,{useContext,useEffect,useRef,useState} from 'react';
import {ArrowUpRight,ArrowRight,Search,Plus,SlidersHorizontal,PanelLeft,Home,Clock,CheckCircle2,Inbox,Folder,ArrowLeft,Pin} from 'lucide-react';
import {WorkNavigationContext} from './work-navigation';
import {useNavigationController} from './shell/navigation-components';
import {NavigationResourceStatus} from './shell/navigation-resource-status';
import {hasNavigationResource} from './shell/navigation-demand';
import {useChatTitle} from './use-chat-title';
import {readItems} from './attention';
import {relativeActivity,activityFor} from './navigation-presentation';
import {startingWorkView,currentWorkPage,workViews} from './adaptive-work.js';
import './adaptive-work.css';

const groups={attention:{title:'Needs you',empty:'No decisions waiting.',verb:'Take a look'},unread:{title:'Ready for you',empty:'No unread responses.',verb:'Read response'},working:{title:'In motion',empty:'Nothing running right now.',verb:'Follow along'},idle:{title:'Pick up again',empty:'Your saved chats will appear here.',verb:'Continue'}};
const filters=[['all','All work'],['attention','Needs you'],['unread','Ready'],['working','Working'],['idle','Recent']];

export function WorkViewSwitcher({value,onChange,quiet=false}){
 return quiet?<label className="a-work-view-quiet"><span>View</span><select aria-label="Work view" value={value} onChange={event=>onChange(event.target.value)}>{workViews.map(key=><option key={key} value={key}>{key[0].toUpperCase()+key.slice(1)}</option>)}</select></label>:
  <div className="a-work-view-switch" role="group" aria-label="Work view">{workViews.map(key=><button key={key} type="button" aria-pressed={value===key} onClick={()=>onChange(key)}>{key[0].toUpperCase()+key.slice(1)}</button>)}</div>;
}

function WorkCard({chat,kind,state,act,hero=false}){
 const title=useChatTitle(chat).title,activity=activityFor(chat,state),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const receipt=(state.attention?.items||[]).find(item=>item.sessionId===chat.id&&!item.read&&item.id==='completion:'+chat.id);
 const time=relativeActivity(chat.recentActivityAt||chat.navigationActivityAt||chat.createdAt);
 async function review(){if(!receipt||busy)return;setBusy(true);setError('');try{await readItems(act,[receipt])}catch(error){setError(error.message)}finally{setBusy(false)}}
 return <article className={'a-work-card'+(hero?' is-hero':'')} data-session-id={chat.id} data-kind={kind}>
  <div className="a-work-card-meta"><span>{chat.location?.kind==='managed'?'On its own':chat.workspaceName||'Project'}</span><time title={time.long}>{time.short}</time></div>
  <button className="a-work-card-open" type="button" onClick={()=>act('session.select',{id:chat.id})} data-action="session.select"><h3>{title}</h3>{chat.description&&<p>{chat.description}</p>}<span className="a-work-card-cta">{groups[kind].verb}<ArrowUpRight size={16}/></span></button>
  <div className="a-work-card-foot"><span className="a-work-status" data-kind={activity.kind}><i/>{activity.kind==='idle'?'Saved conversation':activity.label}</span>{receipt&&<button type="button" disabled={busy} aria-label={'Mark response reviewed: '+title} onClick={review}>{busy?'Saving…':'Reviewed'}<CheckCircle2 size={14}/></button>}</div>
  {error&&<p role="alert">{error}</p>}
 </article>;
}

export function AdaptiveWorkHome({host,state,act,presentation}){
 const model=useNavigationController(host,'chats'),nav=useContext(WorkNavigationContext),view=state.view||{};
 const value=model.state.workHome,ready=hasNavigationResource(model.state,'workHome')&&currentWorkPage(value,view);
 const level=presentation.interfaceDetail||presentation.executionDetail||'standard',mode=view.workHomeView||startingWorkView(presentation);
 const scope=view.workHomeScope||'all',filter=view.workHomeFilter||'all',query=view.workHomeQuery||'';
 const [search,setSearch]=useState(query),timer=useRef(null),[settingsOpen,setSettingsOpen]=useState(false);
 useEffect(()=>{setSearch(query)},[query]);useEffect(()=>()=>clearTimeout(timer.current),[]);
 const patch=values=>act('view.update',{patch:values});
 useEffect(()=>{if(!view.workHomeView)patch({workHomeView:mode,navPinned:level==='detailed'})},[view.workHomeView]);
 const chooseScope=scope=>{clearTimeout(timer.current);setSearch('');patch({workHomeScope:scope,workHomeQuery:'',workHomePages:{}})};
 const pages=ready?value.groups:{},total=Object.values(pages).reduce((sum,page)=>sum+page.total,0);
 const stateForCards={...model.state,attention:{...model.state.attention,items:model.state.workHomeAttention||[]}};
 const projects=[...new Map([...(model.state.workspaceShortcuts||[]).map(row=>({...row,id:row.workspaceId||row.id,name:row.customName||row.name})),...(value?.workspace?[value.workspace]:[])].filter(row=>row.id&&row.name).map(row=>[row.id,row])).values()];
 const scopeLabel=scope==='managed'?'On its own':scope==='all'?'All your work':value?.workspace?.name||'Project';
 const current=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const setFilter=key=>patch({workHomeFilter:key});
 function pageGroup(kind,limit){
  const page=pages[kind];if(!page)return null;
  const items=limit?page.items.slice(0,limit):page.items;
  return <section className="a-work-group" key={kind} aria-label={groups[kind].title}><div className="a-work-group-heading"><h2>{groups[kind].title}</h2><span>{page.total}</span>{limit&&page.total>limit&&<button type="button" onClick={()=>setFilter(kind)}>See all<ArrowRight size={14}/></button>}</div>
   <div className="a-work-group-items">{items.map(chat=><WorkCard key={chat.id} chat={chat} kind={kind} state={stateForCards} act={act}/>)}</div>
   {!page.total&&<p className="a-work-empty">{groups[kind].empty}</p>}
   {!limit&&page.pages>1&&<div className="a-work-pagination"><button type="button" disabled={!page.index} onClick={()=>patch({workHomePages:{...view.workHomePages,[kind]:page.index-1}})}>Previous</button><span>{page.start+1}–{page.end} of {page.total}</span><button type="button" disabled={page.index+1>=page.pages} onClick={()=>patch({workHomePages:{...view.workHomePages,[kind]:page.index+1}})}>Next</button></div>}
  </section>;
 }
 const focusKind=['attention','unread','working','idle'].find(key=>pages[key]?.items.length);
 return <div className="a-adaptive-home" data-view={mode} data-level={level} data-part="work-browser">
  <div className="a-home-toolbar"><WorkViewSwitcher value={mode} quiet={level==='minimal'} onChange={workHomeView=>patch({workHomeView})}/><div className="a-home-tools"><button type="button" className="a-soft" aria-label="Browse your work" onClick={()=>patch({navExpanded:true,...(matchMedia('(min-width: 901px)').matches?{navPinned:true}:{})})}><PanelLeft/><span>Browse</span></button><button type="button" className="a-soft" aria-label="Home appearance" aria-expanded={settingsOpen} onClick={()=>setSettingsOpen(!settingsOpen)}><SlidersHorizontal/></button></div></div>
  {settingsOpen&&<section className="a-home-preferences" aria-label="Home appearance"><div><strong>How much would you like to see?</strong><p>Your open work stays in place. Home uses your starting view.</p></div><div className="a-home-levels" role="group" aria-label="Appearance detail">{[['minimal','Focused'],['standard','Balanced'],['detailed','Everything']].map(([key,label])=><button type="button" key={key} aria-pressed={level===key} onClick={()=>nav.setPresentation({interfaceDetail:key,executionDetail:key})}>{label}</button>)}</div><label>Start with<select aria-label="Starting work view" value={presentation.workStartView||'appearance'} onChange={event=>nav.setPresentation({workStartView:event.target.value})}><option value="appearance">Follow appearance</option>{workViews.map(key=><option key={key} value={key}>{key[0].toUpperCase()+key.slice(1)}</option>)}</select></label><button className="a-link" type="button" onClick={()=>nav.home()}>Go to starting view<ArrowRight/></button></section>}
  <div className="a-home-intro"><div><span className="a-home-eyebrow">{scopeLabel} · {mode==='focus'?'A little room to think':mode==='brief'?'Your brief':'The studio'}</span><h1>{mode==='focus'?'One thing at a time.':mode==='brief'?'Make room for what’s next.':'A place for everything in motion.'}</h1><p>{mode==='focus'?'Start with what needs you. The rest is here when you want it.':mode==='brief'?'What’s waiting, what’s moving, and where you left off.':'Follow the work, find a thread, or start something new.'}</p></div><button type="button" className="a-primary" onClick={()=>nav.newChat(value?.workspace?.path||{location:{kind:'managed'}})}><Plus/>Start something</button></div>
  <div className="a-home-query"><label className="a-home-search"><Search size={18}/><input type="search" aria-label="Search your work" placeholder="Find a chat, idea, or project…" maxLength={500} value={search} onChange={event=>{const value=event.target.value;setSearch(value);clearTimeout(timer.current);timer.current=setTimeout(()=>patch({workHomeQuery:value,workHomePages:{}}),220)}}/></label><select aria-label="Work scope" value={scope} onChange={event=>chooseScope(event.target.value)}><option value="all">All work</option><option value="managed">On its own</option>{projects.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select></div>
  {scope!=='all'&&scope!=='managed'&&ready&&value.workspace&&<div className="a-home-project"><Folder/><span><strong>{value.workspace.name}</strong> · A place for related work</span><button type="button" onClick={()=>nav.browse('workspace',value.workspace.id)}>Project work &amp; files<ArrowUpRight/></button></div>}
  {current&&<button type="button" className="a-home-return" onClick={()=>patch({workSurface:'chat'})}><ArrowLeft size={14}/>Return to {current.title||'your open chat'}{current.draft?' · Draft kept':''}</button>}
  <NavigationResourceStatus state={model.state} resource="workHome" label="your work" refresh={model.refreshResources}/>
  {!ready&&!model.state.navigationResourcePending?.workHome&&<p role="status">Loading your work…</p>}
  {ready&&value.unavailable?<p role="alert">This project is unavailable. <button type="button" onClick={()=>chooseScope('all')}>Show all work</button></p>:ready&&<>
   <div className="a-home-filters" role="group" aria-label="Filter work">{filters.map(([key,label])=><button type="button" key={key} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}<span>{key==='all'?total:pages[key]?.total||0}</span></button>)}</div>
   {!total?<div className="a-home-welcome"><Inbox size={32}/><h2>{query?'No matching work.':'A fresh place to begin.'}</h2><p>{query?'Try another phrase or a different scope.':'Bring a question, an idea, or a project. You can organize it as it grows.'}</p><button type="button" className="a-primary" onClick={()=>query?(setSearch(''),patch({workHomeQuery:'',workHomePages:{}})):nav.newChat(value.workspace?.path||{location:{kind:'managed'}})}>{query?'Clear search':'Start a chat'}<ArrowRight/></button></div>:
    filter!=='all'?<div className="a-home-filtered">{pageGroup(filter)}</div>:
    mode==='focus'?<div className="a-home-focus"><div className="a-focus-caption"><span>{groups[focusKind].title}</span><span>{pages[focusKind].total>1?`+ ${pages[focusKind].total-1} more`:''}</span></div><WorkCard chat={pages[focusKind].items[0]} kind={focusKind} state={stateForCards} act={act} hero/><div className="a-focus-next">{['attention','unread','working','idle'].filter(key=>pages[key]?.total).map(key=><button key={key} type="button" onClick={()=>setFilter(key)}><span>{groups[key].title}</span><strong>{pages[key].total}</strong><ArrowRight size={15}/></button>)}</div></div>:
    mode==='brief'?<div className="a-home-brief"><div>{pages.attention.total>0&&pageGroup('attention',3)}{pageGroup('unread',3)}</div><div className="a-home-brief-side">{pageGroup('working',3)}{pageGroup('idle',3)}</div></div>:
    <div className="a-home-studio"><div>{pageGroup('attention',5)}{pageGroup('unread',5)}</div><div>{pageGroup('working',5)}</div><div>{pageGroup('idle',5)}</div></div>}
   <div className="a-home-bottom"><span>Same work. A different way to see it.</span><button type="button" onClick={()=>nav.browse('chats')}>Search all chats<ArrowUpRight size={14}/></button><button type="button" onClick={()=>nav.browse('workspaces')}>All projects<ArrowUpRight size={14}/></button></div>
  </>}
 </div>;
}

export function AdaptiveNavLinks({state}){
 const nav=useContext(WorkNavigationContext);
 return <nav className="a-adaptive-nav" aria-label="Work navigation"><button type="button" onClick={()=>nav.home()}><Home/>Home</button>{[['attention','Needs you',Inbox],['unread','Ready for you',CheckCircle2],['working','Working',Clock],['idle','Pick up again',ArrowRight]].map(([key,label,Icon])=><button key={key} type="button" aria-current={state.view?.workSurface==='home'&&state.view?.workHomeFilter===key?'page':undefined} onClick={()=>nav.home({workHomeFilter:key})}><Icon/>{label}</button>)}</nav>;
}
