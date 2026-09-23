import {isTopLevelChat} from './chat-navigation';
import {ShellSlot,useShellContext} from './shell/runtime';
import React,{useEffect,useRef,useState} from 'react';
import {SlidersHorizontal,Palette,AudioLines,Bell,Network,Layers,Plug,Download,Activity,Archive,Settings,ArrowRight,MessageSquare,ArrowLeft,X} from 'lucide-react';
import {settingsSections,settingsLocation,settingsPatch,settingsUnread,settingsTitle,settingsParent} from './settings-navigation';
import {AIConnections} from './ai-connections';
import {PrivacySettings,AdvancedSettings} from './settings-everyday';
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
import {DesktopReadiness} from './desktop-readiness';
import {ConversationName} from './conversation-controls';
import {RecallSettings} from './recall';
import {ConversationExport} from './conversation-export.jsx';
import {ConversationLibrary,ConversationSharing} from './conversation-library.jsx';
import {OutputSettings} from './outputs.jsx';
import {PublishingSettings} from './publishing.jsx';
import {WorkspaceSettings} from './workspace-setup';
import {VoiceSettings,InstallAppSettings} from './settings-personal';
import {SettingsLayoutContext,useSettingsCompact,useSettingsHistory,useSettingsViewport} from './settings-layout';
import {settingsTrail,settingsBaseNavigation,mergeSettingsNavigation} from './settings-mobile';

const icons={ai:Network,privacy:Archive,workspaces:Layers,overview:SlidersHorizontal,appearance:Palette,voice:AudioLines,notifications:Bell,models:Network,bundles:Layers,'smart-tools':Plug,updates:Download,diagnostics:Activity,history:Archive,advanced:Settings};
export function SettingsExperience({state,session,act,dispatch,open,close=()=>act('view.update',{patch:{panel:null}}),navigationRef,appearance}){
 const shell=useShellContext();
 const extensions=(shell?.data?.resolvedInstances||[]).filter(item=>item.slot==='settings.section');
 const sections=settingsSections.map(section=>section.id==='advanced'?{...section,pages:[...section.pages,...extensions.map(item=>['shell:'+item.id,shell.data.packages[item.package]?.manifest?.label||item.id])]}:section);
 const {page,section}=settingsLocation(state.view,sections),heading=useRef(null),body=useRef(null),root=useRef(null),[footer,setFooter]=useState(null);
 const compact=useSettingsCompact(root),trail=settingsTrail(state.view,state,sections),route=trail.at(-1),index=compact&&route.key==='index',detail=compact&&trail.length>(settingsParent(page,sections)?3:2);
 useSettingsViewport(root,compact);
 const navigation=useSettingsHistory({compact,trail,view:state.view||{},act,close,body,navigationRef});
 const navigate=(page,reset=false)=>{navigation.rememberScroll();act('view.update',{patch:{...mergeSettingsNavigation(state.view||{},settingsBaseNavigation(page,sections)),...(reset?{settingsRootVisit:crypto.randomUUID()}:{})}});};
 useEffect(()=>{if(!state.view?.settingsRootVisit)return;const frame=requestAnimationFrame(()=>{if(body.current)body.current.scrollTop=0;for(const item of root.current?.querySelectorAll('.a-settings-page-content:not([hidden]) details[open]')||[])item.open=false;});return()=>cancelAnimationFrame(frame)},[state.view?.settingsRootVisit]);
 useEffect(()=>{heading.current?.focus({preventScroll:true});if(!compact&&body.current)body.current.scrollTop=0;},[page,route.key,compact]);
 const props={state,session,act};
 // Keep visited editors mounted while the dialog is open. Private fields stay
 // in component memory, never in shared view state, when changing sections.
 const editors=useRef(new Map());
 const family=page=>['ai-connections','overview'].includes(page)?'ai-connections':['appearance','custom-appearance'].includes(page)?'appearance':['smart-tools','tool-connections'].includes(page)?'smart-tools':['app-bundles','add-bundles','loaded-modules','share-bundle','registries'].includes(page)?'bundles':['notifications','diagnostics','history','permissions','automation','repair','reset'].includes(page)?'maintenance':page;
 const active=family(page);if(!index)editors.current.set(active,page);
 function renderPage(page){
 let content;
 if(['overview','ai-connections'].includes(page))content=<AIConnections {...props} act={dispatch||act} navigate={navigate}/>;
 else if(page==='privacy')content=<PrivacySettings state={state} navigate={navigate}/>;
 else if(page==='advanced')content=<AdvancedSettings {...props} sections={sections} navigate={navigate}/>;
 else if(page==='appearance')content=<ShellSlot name="settings.appearance">{React.isValidElement(appearance)?React.cloneElement(appearance,{simple:true,onAdvanced:()=>navigate('custom-appearance')}):appearance}</ShellSlot>;
 else if(page==='custom-appearance')content=React.isValidElement(appearance)?React.cloneElement(appearance,{advancedOnly:true}):appearance;
 else if(page.startsWith('shell:'))content=<ShellSlot name="settings.section" instanceId={page.slice(6)}/>;
 else if(page==='workspaces')content=<WorkspaceSettings {...props}/>;
 else if(page==='voice')content=<><p className="a-everyday-intro">Choose how your conversations sound.</p><VoiceSettings {...props}/></>;
 else if(page==='providers')content=<ProviderSettings {...props}/>;
 else if(page==='routing')content=<RoutingSettings {...props}/>;
 else if(page==='defaults')content=<BundleDefaults {...props}/>;
 else if(['app-bundles','add-bundles','loaded-modules','share-bundle','registries'].includes(page))content=<BundleSettings {...props}/>;
 else if(['smart-tools','tool-connections'].includes(page))content=<SmartToolsSettings {...props} simple={page==='smart-tools'} navigateSettings={navigate}/>;
 else if(page==='updates')content=<UpdateSettings {...props}/>;
 else if(page==='ready-conversations')content=state.runtime?.retention?<WorkerRetentionSettings {...props}/>:<p>This host does not expose conversation readiness settings.</p>;
 else if(page==='install-app')content=<InstallAppSettings/>;
 else if(page==='runtime')content=<RuntimeSettings {...props}/>;
 else if(page==='desktop')content=<DesktopReadiness {...props}/>;
 else if(page==='recall')content=<RecallSettings key={session?.id||'none'} {...props}/>;
 else if(page==='outputs')content=<OutputSettings key={session?.id} {...props}/>;
 else if(page==='publishing')content=<PublishingSettings key={session?.id} {...props} act={dispatch||act}/>;
 else if(page==='conversation')content=<>{session?<><ConversationName key={session.id} session={session} act={act}/><ConversationExport {...props}/>{session.location?.kind==='managed'&&isTopLevelChat(session)&&<div className="a-dialog-actions"><button type="button" className="a-soft a-danger" data-action="view.update" onClick={()=>open('delete-session')}>Delete chat</button></div>}<h3>Conversation bundle</h3></>:<p>Choose a bundle to start a conversation.</p>}<BundleControl {...props} working={['working','starting','running','stopping','busy'].includes(session?.status)}/></>;
 else content=<MaintenanceSettings {...props}/>;
 if(page==='conversation'&&session)content=<>{content}<ConversationLibrary key={'library-'+session.id} {...props}/><ConversationSharing key={'sharing-'+session.id} {...props}/></>;
 return content;
 }
 return <div ref={root} className="a-settings-experience a-settings-everyday" data-part="settings-experience" data-settings-page={page} data-compact={compact} data-settings-index={index} data-settings-detail={detail} data-settings-route={route.key}>
  <header className="a-settings-mobile-head">
   {index?<span/>:<button type="button" className="a-link" data-action="view.update" aria-label={'Back to '+(trail.at(-2)?.title||'Settings')} onClick={navigation.back}><ArrowLeft/><span>{trail.length>2?'Back':'Settings'}</span></button>}
   <h3 ref={compact?heading:undefined} tabIndex={-1}>{route.title}</h3>
   <button type="button" className="a-icon" data-action="view.update" aria-label="Close settings" onClick={navigation.dismiss}><X/></button>
  </header>
  <nav className="a-settings-sidebar" aria-label="Settings sections">{sections.map((item,index)=>{const Icon=icons[item.id]||Layers;return <React.Fragment key={item.id}>{item.group&&item.group!==sections[index-1]?.group&&<div className="a-settings-category">{item.group}</div>}<button type="button" data-action="view.update" data-settings-section={item.id} aria-current={item.id===section.id?'page':undefined} onClick={()=>navigate(item.pages[0][0],true)}><Icon aria-hidden="true"/><span>{item.title}</span><AttentionBadge count={settingsUnread(state,item)}/></button></React.Fragment>})}</nav>
  <div ref={body} onScroll={navigation.rememberScroll} className="a-settings-content" role="region" aria-label="Settings content">
   <header className="a-settings-page-heading"><div><h3 ref={!compact?heading:undefined} tabIndex={-1}>{settingsTitle(page,sections)}</h3></div>{session&&['loaded-modules','conversation','runtime'].includes(page)&&<span className="a-settings-conversation"><MessageSquare aria-hidden="true"/>{session.title}</span>}</header>
   {settingsParent(page,sections)&&<button type="button" className="a-link a-settings-breadcrumb" data-action="view.update" onClick={()=>navigate(settingsParent(page,sections))}><ArrowLeft/>Back to {section.title}</button>}
   {page!=='updates'&&<AttentionReview state={state} act={act} page={page}/>}
   {state.management?.error&&page!=='add-bundles'&&<div className="a-alert" role="alert">{state.management.error}</div>}
   <>{[...editors.current].map(([key,editorPage])=><SettingsPageContext.Provider key={key} value={editorPage}><SettingsLayoutContext.Provider value={{compact,active:key===active&&!index,footer}}><div className="a-settings-page-content" hidden={key!==active}>{renderPage(editorPage)}</div></SettingsLayoutContext.Provider></SettingsPageContext.Provider>)}</>
  </div>
  <div ref={setFooter} className="a-settings-mobile-actions"/>
 </div>;
}
