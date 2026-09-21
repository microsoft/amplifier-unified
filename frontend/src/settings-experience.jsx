import {ShellSlot,useShellContext} from './shell/runtime';
import React,{useEffect,useRef,useState} from 'react';
import {SlidersHorizontal,Palette,AudioLines,Bell,Network,Layers,Plug,Download,Activity,Archive,Settings,ArrowRight,MessageSquare,ArrowLeft,X} from 'lucide-react';
import {settingsSections,settingsLocation,settingsPatch,settingsUnread} from './settings-navigation';
import {SettingsPageContext} from './settings-ui';
import {AttentionBadge,AttentionReview} from './attention';
import {ProviderSettings,RoutingSettings} from './setup';
import {BundleSettings} from './bundles';
import {BundleDefaults,BundleControl} from './bundle-controls';
import {SmartToolsSettings} from './smart-tools';
import {MaintenanceSettings} from './maintenance';
import {UpdateSettings} from './updates';
import {WorkerRetentionSettings} from './worker-retention';
import {RuntimeSettings} from './runtime-settings';
import {ConversationName} from './conversation-controls';
import {ConversationExport} from './conversation-export.jsx';
import {VoiceSettings,InstallAppSettings} from './settings-personal';
import {SettingsLayoutContext,useSettingsCompact,useSettingsHistory,useSettingsViewport} from './settings-layout';
import {settingsTrail,settingsBaseNavigation,mergeSettingsNavigation} from './settings-mobile';

const icons={overview:SlidersHorizontal,appearance:Palette,voice:AudioLines,notifications:Bell,models:Network,bundles:Layers,'smart-tools':Plug,updates:Download,diagnostics:Activity,history:Archive,advanced:Settings};
export function SettingsExperience({state,session,act,open,close=()=>act('view.update',{patch:{panel:null}}),navigationRef,appearance}){
 const shell=useShellContext();
 const extensions=(shell?.data?.resolvedInstances||[]).filter(item=>item.slot==='settings.section');
 const sections=[...settingsSections,...extensions.map(item=>({id:'shell:'+item.id,title:shell.data.packages[item.package]?.manifest?.label||item.id,group:'Extensions',scope:'This interface',pages:[['shell:'+item.id,shell.data.packages[item.package]?.manifest?.label||item.id]]}))];
 const {page,section}=settingsLocation(state.view,sections),heading=useRef(null),body=useRef(null),root=useRef(null),[footer,setFooter]=useState(null);
 const compact=useSettingsCompact(root),trail=settingsTrail(state.view,state,sections),route=trail.at(-1),index=compact&&route.key==='index',detail=compact&&trail.length>2;
 useSettingsViewport(root,compact);
 const navigation=useSettingsHistory({compact,trail,view:state.view||{},act,close,body,navigationRef});
 const navigate=page=>{navigation.rememberScroll();act('view.update',{patch:compact?mergeSettingsNavigation(state.view||{},settingsBaseNavigation(page,sections)):settingsPatch(page,sections)});};
 useEffect(()=>{heading.current?.focus({preventScroll:true});if(!compact&&body.current)body.current.scrollTop=0;},[page,route.key,compact]);
 const props={state,session,act};
 // Keep visited editors mounted while the dialog is open. Private fields stay
 // in component memory, never in shared view state, when changing sections.
 const editors=useRef(new Map());
 const family=page=>['app-bundles','add-bundles','loaded-modules','share-bundle','registries'].includes(page)?'bundles':['notifications','diagnostics','history','permissions','automation','repair','reset'].includes(page)?'maintenance':page;
 const active=family(page);if(!index)editors.current.set(active,page);
 function renderPage(page){
 let content;
 if(page==='overview')content=<SettingsOverview {...props} navigate={navigate}/>;
 else if(page==='appearance')content=<ShellSlot name="settings.appearance">{appearance}</ShellSlot>;
 else if(page.startsWith('shell:'))content=<ShellSlot name="settings.section" instanceId={page.slice(6)}/>;
 else if(page==='voice')content=<VoiceSettings {...props}/>;
 else if(page==='providers')content=<ProviderSettings {...props}/>;
 else if(page==='routing')content=<RoutingSettings {...props}/>;
 else if(page==='defaults')content=<BundleDefaults {...props}/>;
 else if(['app-bundles','add-bundles','loaded-modules','share-bundle','registries'].includes(page))content=<BundleSettings {...props}/>;
 else if(page==='smart-tools')content=<SmartToolsSettings {...props}/>;
 else if(page==='updates')content=<UpdateSettings {...props}/>;
 else if(page==='ready-conversations')content=state.runtime?.retention?<WorkerRetentionSettings {...props}/>:<p>This host does not expose conversation readiness settings.</p>;
 else if(page==='install-app')content=<InstallAppSettings/>;
 else if(page==='runtime')content=<RuntimeSettings {...props}/>;
 else if(page==='conversation')content=<>{session?<><ConversationName key={session.id} session={session} act={act}/><ConversationExport {...props}/><div className="a-dialog-actions"><button type="button" className="a-soft a-danger" data-action="session.delete" onClick={()=>open('delete-session')}>Remove chat</button></div><h3>Conversation bundle</h3></>:<p>Choose a bundle to start a conversation.</p>}<BundleControl {...props} working={['working','starting','running','stopping','busy'].includes(session?.status)}/></>;
 else content=<MaintenanceSettings {...props}/>;
 return content;
 }
 return <div ref={root} className="a-settings-experience" data-part="settings-experience" data-settings-page={page} data-compact={compact} data-settings-index={index} data-settings-detail={detail} data-settings-route={route.key}>
  <header className="a-settings-mobile-head">
   {index?<span/>:<button type="button" className="a-link" data-action="view.update" aria-label={'Back to '+(trail.at(-2)?.title||'Settings')} onClick={navigation.back}><ArrowLeft/><span>{trail.length>2?'Back':'Settings'}</span></button>}
   <h3 ref={compact?heading:undefined} tabIndex={-1}>{route.title}</h3>
   <button type="button" className="a-icon" data-action="view.update" aria-label="Close settings" onClick={navigation.dismiss}><X/></button>
  </header>
  <nav className="a-settings-sidebar" aria-label="Settings sections">{sections.map((item,index)=>{const Icon=icons[item.id]||Layers;return <React.Fragment key={item.id}>{item.group!==sections[index-1]?.group&&<div className="a-settings-category">{item.group}</div>}<button type="button" data-action="view.update" data-settings-section={item.id} aria-current={item.id===section.id?'page':undefined} onClick={()=>navigate(item.pages[0][0])}><Icon aria-hidden="true"/><span>{item.title}</span><AttentionBadge count={settingsUnread(state,item)}/></button></React.Fragment>})}</nav>
  <div ref={body} onScroll={navigation.rememberScroll} className="a-settings-content" role="region" aria-label="Settings content">
   <header className="a-settings-page-heading"><div><span className="a-settings-scope">{section.scope}</span><h3 ref={!compact?heading:undefined} tabIndex={-1}>{section.title}</h3></div>{session&&['loaded-modules','conversation','runtime'].includes(page)&&<span className="a-settings-conversation"><MessageSquare aria-hidden="true"/>{session.title}</span>}</header>
   {section.pages.length>1&&(!compact||!detail)&&(compact&&section.pages.length>2?<label className="a-settings-section-picker">Section<select aria-label={section.title+' section'} value={page} data-action="view.update" onChange={event=>navigate(event.target.value)}>{section.pages.map(([id,title])=><option key={id} value={id}>{title}{settingsUnread(state,{pages:[[id,title]]})?' · Needs attention':''}</option>)}</select></label>:<nav className="a-settings-subnav" aria-label={section.title+' sections'}>{section.pages.map(([id,title])=><button type="button" key={id} data-action="view.update" data-settings-destination={id} aria-current={id===page?'page':undefined} onClick={()=>navigate(id)}>{title}<AttentionBadge state={state} page={id}/></button>)}</nav>)}
   <AttentionReview state={state} act={act} page={page}/>
   {state.management?.error&&page!=='add-bundles'&&<div className="a-alert" role="alert">{state.management.error}</div>}
   <>{[...editors.current].map(([key,editorPage])=><SettingsPageContext.Provider key={key} value={editorPage}><SettingsLayoutContext.Provider value={{compact,active:key===active&&!index,footer}}><div className="a-settings-page-content" hidden={key!==active}>{renderPage(editorPage)}</div></SettingsLayoutContext.Provider></SettingsPageContext.Provider>)}</>
  </div>
  <div ref={setFooter} className="a-settings-mobile-actions"/>
 </div>;
}
function SettingsOverview({state,session,navigate}){
 const providers=state.setup?.providers||[],entries=Array.isArray(state.bundles)?state.bundles:state.bundles?.entries||[],servers=state.smartTools?.servers||[];
 const cards=[['providers','models','Models & routing',state.setup?.providersLoadedAt?`${providers.length} saved connections${state.setup?.active?' · '+state.setup.active+' routing':''}`:'Connections, credentials, and model routing'],['app-bundles','bundles','Bundles & modules',entries.length?`${entries.length} configured entries · defaults and conversation modules`:'Configured capabilities, aliases, and defaults'],['smart-tools','smart-tools','Smart Tools',`${servers.length} connections · discover more in the catalog`],['updates','updates','Updates','App releases, ecosystem sources, and automatic updates']];
 return <><div className="a-settings-welcome"><h4>A setup that works for you.</h4><p>Personal preferences, connected services, and the configuration behind each conversation.</p><p className="a-caption">Runtime: {state.runtime?.available?'Available':state.runtime?.error||'Not installed'} · Conversation: {session?.status||'None selected'}</p></div><div className="a-settings-start-grid">{cards.map(([page,id,title,detail])=>{const Icon=icons[id];return <button key={page} type="button" data-action="view.update" onClick={()=>navigate(page)}><Icon aria-hidden="true"/><strong>{title}</strong><span>{detail}</span><ArrowRight aria-hidden="true"/></button>})}</div><div className="a-settings-current"><div><MessageSquare aria-hidden="true"/><strong>{session?.title||'No selected conversation'}</strong></div><button type="button" className="a-link" data-action="view.update" onClick={()=>navigate('loaded-modules')}>Inspect conversation modules</button></div></>;
}
