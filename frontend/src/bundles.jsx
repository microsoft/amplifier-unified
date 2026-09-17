import React,{useState,useEffect} from 'react';
import {RegistrySettings} from './registry';
import {moduleRows,updateModule,parsePlan} from './module-config';
import {Plus,Search,ArrowUp,ArrowDown,Trash2,Download,RefreshCw,Layers,Package,Check,Code2,Settings,SlidersHorizontal,Wrench} from 'lucide-react';

const pretty=value=>JSON.stringify(value,null,2);
const sourceDefaults={source:'',selectedUri:'',alias:'',role:'behavior',exportName:'my-amplifier',exportDescription:''};
export function SettingsNavigation({state,act}){
 const selected=state.view?.settingsSection||'setup';
 return <nav className="a-settings-nav" aria-label="Settings sections">{[['setup','Setup',Settings],['capabilities','Capabilities',SlidersHorizontal],['maintenance','Maintenance',Wrench]].map(([key,label,Icon])=><button key={key} className={selected===key?'selected':''} aria-current={selected===key?'page':undefined} data-action="view.update" onClick={()=>act('view.update',{patch:{settingsSection:key}})}><Icon/>{label}</button>)}</nav>;
}
function useSharedDraft(shared,defaults,key,act){
 const [draft,setDraft]=useState({...defaults,...shared});
 useEffect(()=>{if(shared)setDraft(current=>({...current,...shared}))},[shared]);
 function edit(patch){const next={...draft,...patch};setDraft(next);act('view.update',{patch:{[key]:next}})}
 return [draft,edit];
}
export function BundleSettings({state,session,act}){
 const [draft,edit]=useSharedDraft(state.view?.bundleManager,sourceDefaults,'bundleManager',act);
 const entries=Array.isArray(state.bundles)?state.bundles:state.bundles?.entries||[];
 const discovery=state.bundleDiscovery||{};
 const candidates=discovery.candidates||[];
 const selected=candidates.find(candidate=>candidate.uri===draft.selectedUri);
 const management=state.management||{};
 const pending=['queued','running','pending','working','discovering','applying'].includes(management.phase);
 useEffect(()=>{act('bundles.list',{})},[]);
 return <div className="a-bundle-pane">
  <section className="a-settings-section"><div className="a-settings-heading"><Layers/><div><h3>Build your Amplifier</h3><p>Add capabilities from community repositories, then shape the modules they compose into.</p></div></div>
   {management.error&&<div className="a-alert" role="alert"><span>{management.error}</span></div>}
   {pending&&<div className="a-management-status" role="status"><RefreshCw className="a-progress-spinner"/>{management.detail||'Updating your configuration…'}</div>}
   {!pending&&management.detail&&<p className="a-caption" role="status">{management.detail}</p>}
   <label htmlFor="bundle-source">Repository or bundle URL</label><div className="a-inline-form"><input id="bundle-source" type="url" value={draft.source} placeholder="https://github.com/owner/amplifier-bundle" data-action="view.update" onChange={e=>edit({source:e.target.value})}/><button className="a-primary" disabled={!draft.source.trim()||pending} data-action="bundle.discover" onClick={()=>act('bundle.discover',{url:draft.source.trim()})}><Search/>Browse</button></div>
   <p className="a-caption">Browse a repository’s YAML and Markdown bundles before choosing what to add.</p>
   {candidates.length>0&&<div className="a-discovery" aria-label="Discovered bundles"><div className="a-settings-row"><strong>{candidates.length} {candidates.length===1?'bundle found':'bundles found'}</strong>{discovery.revision&&<code>{discovery.revision.slice(0,9)}</code>}</div><div className="a-candidates">{candidates.map(candidate=><label className={`a-candidate ${selected?.uri===candidate.uri?'selected':''}`} key={candidate.uri}><input type="radio" name="bundle-candidate" checked={selected?.uri===candidate.uri} data-action="view.update" onChange={()=>edit({selectedUri:candidate.uri,alias:candidate.name||'',role:candidate.kind==='standalone'?'standalone':'behavior'})}/><span><strong>{candidate.name||candidate.path}</strong><small>{candidate.path||candidate.uri}</small>{candidate.description&&<p>{candidate.description}</p>}</span><span className="a-kind-tag">{candidate.kind||'bundle'}</span></label>)}</div><p className="a-caption">Type suggestions are based on file contents. Choose how you want to use the bundle below.</p></div>}
   {(selected||draft.selectedUri)&&<div className="a-selection-card"><label htmlFor="bundle-add-role">Use this bundle as</label><select id="bundle-add-role" value={draft.role} data-action="view.update" onChange={e=>edit({role:e.target.value})}><option value="behavior">An app capability — compose into sessions</option><option value="standalone">A standalone bundle — register an alias</option></select><label htmlFor="bundle-alias">{draft.role==='standalone'?'Bundle alias':'Display name'}</label><input id="bundle-alias" value={draft.alias} data-action="view.update" onChange={e=>edit({alias:e.target.value})} placeholder="my-capabilities"/><p className="a-caption a-wrap">{draft.selectedUri}</p><div className="a-dialog-actions"><button className="a-primary" disabled={pending||!draft.selectedUri||(draft.role==='standalone'&&!draft.alias.trim())} data-action="bundles.add" onClick={()=>act('bundles.add',{uri:draft.selectedUri,name:draft.alias.trim()||undefined,role:draft.role})}><Plus/>{draft.role==='standalone'?'Register bundle':'Add capability'}</button></div></div>}
  </section>
  <section className="a-settings-section"><div className="a-settings-row"><div><h3>Your app bundles</h3><p>Enabled capabilities compose in the order shown. Standalone aliases are available when starting a conversation.</p></div><button className="a-icon" aria-label="Refresh app bundles" data-action="bundles.list" onClick={()=>act('bundles.list')}><RefreshCw/></button></div>
   {!entries.length?<div className="a-settings-empty"><Package/><span>Your app has no additional bundles yet. Browse a repository to add one.</span></div>:<div className="a-configured-bundles">{entries.map((entry,index)=><div className="a-configured-bundle" key={entry.id}><label className="a-bundle-enabled"><input type="checkbox" checked={entry.enabled!==false} data-action="bundles.toggle" aria-label={`Enable ${entry.name||entry.uri}`} onChange={e=>act('bundles.toggle',{id:entry.id,enabled:e.target.checked})}/><span><strong>{entry.name||entry.uri}</strong><small>{entry.role==='standalone'?'Standalone alias':'App capability'}</small></span></label><div className="a-bundle-controls"><button className="a-icon" aria-label={`Move ${entry.name||'bundle'} up`} disabled={index===0||pending} data-action="bundles.move" onClick={()=>act('bundles.move',{id:entry.id,direction:'up'})}><ArrowUp/></button><button className="a-icon" aria-label={`Move ${entry.name||'bundle'} down`} disabled={index===entries.length-1||pending} data-action="bundles.move" onClick={()=>act('bundles.move',{id:entry.id,direction:'down'})}><ArrowDown/></button><button className="a-icon a-danger" aria-label={`Remove ${entry.name||'bundle'}`} disabled={pending} data-action="bundles.remove" onClick={()=>act('bundles.remove',{id:entry.id})}><Trash2/></button></div><div className="a-bundle-source">{entry.uri}</div></div>)}</div>}
  </section>
  <ModuleSettings state={state} session={session} act={act}/>
  <RegistrySettings state={state} act={act}/>
  <section className="a-settings-section"><div className="a-settings-heading"><Download/><div><h3>Share your setup</h3><p>Export the current session’s bundle and applied configuration for others to load with Amplifier Foundation.</p></div></div><label htmlFor="bundle-export-name">Bundle name</label><input id="bundle-export-name" value={draft.exportName} data-action="view.update" onChange={e=>edit({exportName:e.target.value})}/><label htmlFor="bundle-export-description">Description</label><input id="bundle-export-description" value={draft.exportDescription} data-action="view.update" onChange={e=>edit({exportDescription:e.target.value})}/><div className="a-dialog-actions"><button className="a-soft" disabled={!session||pending||!draft.exportName.trim()} data-action="bundle.export" onClick={()=>act('bundle.export',{sessionId:session.id,name:draft.exportName.trim(),description:draft.exportDescription})}><Download/>Export app bundle</button></div><p className="a-caption">Exports include bundle references and configuration. Provider credentials stay on this machine.</p></section>
 </div>;
}
export function ModuleSettings({state,session,act}){
 const [draft,edit]=useSharedDraft(state.view?.moduleEditor,{sessionId:'',text:'',selectedKey:'',configText:'',name:'my-amplifier',description:'',showRaw:false},'moduleEditor',act);
 const inspected=session?.configuration||(state.configuration?.sessionId===session?.id?state.configuration:null);
 const plan=inspected?.plan;
 const management=state.management||{};
 const pending=['queued','running','pending','working','discovering','applying'].includes(management.phase);
 const active=!!session&&['working','running','busy','starting','stopping'].includes(session.status);
 useEffect(()=>{if(plan&&session&&(draft.sessionId!==session.id||!draft.text))edit({sessionId:session.id,text:pretty(plan),selectedKey:'',configText:''})},[session?.id,plan]);
 let parsed=null,parseError='';
 try{if(draft.sessionId===session?.id&&draft.text)parsed=parsePlan(draft.text)}catch(error){parseError=error.message}
 const rows=moduleRows(parsed),selected=rows.find(row=>row.key===draft.selectedKey);
 const dirty=!!parsed&&pretty(parsed)!==pretty(plan);
 function update(key,patch){try{const next=updateModule(parsed,key,patch);edit({text:pretty(next),...(key===draft.selectedKey&&patch.config?{configText:pretty(patch.config)}:{})})}catch(error){edit({parseError:error.message})}}
 function select(row){edit({selectedKey:row.key,configText:pretty(row.config),parseError:''})}
 return <section className="a-settings-section"><div className="a-settings-heading"><Code2/><div><h3>Composed session modules</h3><p>{session?`Inspect the configuration behind “${session.title}”.`:'Start or select a conversation to inspect its composed configuration.'}</p></div></div>
  {session&&<><div className="a-settings-row"><span className="a-caption a-wrap">{session.bundle}</span><button className="a-soft" disabled={pending} data-action="configuration.inspect" onClick={()=>act('configuration.inspect',{id:session.id})}><RefreshCw/>{plan?'Refresh configuration':'Load configuration'}</button></div>
  {plan&&<><p className="a-caption">The fully composed plan includes your root bundle and app capabilities. Changes are saved only when you apply them.</p>
   <div className="a-module-workspace"><div className="a-module-list" aria-label="Composed modules">{rows.map(row=><div className={`a-module-row ${row.key===draft.selectedKey?'selected':''}`} key={row.key}><label className="a-module-enable"><input type="checkbox" checked={row.enabled} disabled={row.required} aria-label={`Enable ${row.id}`} data-action="view.update" onChange={e=>update(row.key,{enabled:e.target.checked})}/></label><button className="a-module-select" data-action="view.update" onClick={()=>select(row)} aria-pressed={row.key===draft.selectedKey}><strong>{row.id}</strong><small>{row.section}{row.required?' · required':''}</small></button></div>)}</div><div className="a-module-detail">{selected?<><h4>{selected.module}</h4>{selected.source&&<p className="a-caption a-wrap">{selected.source}</p>}<label htmlFor="module-config">Module configuration (JSON)</label><textarea id="module-config" className="a-json-editor" value={draft.configText} spellCheck={false} data-action="view.update" onChange={e=>edit({configText:e.target.value,parseError:''})}/><button className="a-soft" data-action="view.update" onClick={()=>{try{const config=parsePlan(draft.configText);edit({text:pretty(updateModule(parsed,selected.key,{config})),parseError:''})}catch(error){edit({parseError:error.message})}}}><Check/>Update draft</button></>:<div className="a-settings-empty"><Code2/><span>Select a module to edit its configuration.</span></div>}</div></div>
   {(parseError||draft.parseError)&&<div className="a-alert" role="alert"><span>{parseError||draft.parseError}</span></div>}
   <button className="a-link" data-action="view.update" onClick={()=>edit({showRaw:!draft.showRaw})}>{draft.showRaw?'Hide':'Edit'} full configuration JSON</button>{draft.showRaw&&<><label htmlFor="full-module-plan">Full composed plan</label><textarea id="full-module-plan" className="a-json-editor a-full-plan" spellCheck={false} value={draft.text} data-action="view.update" onChange={e=>edit({text:e.target.value,parseError:''})}/></>}
   {active&&<p className="a-caption">You can prepare changes while work continues. Apply them when this conversation and its workers are idle.</p>}
   <div className="a-dialog-actions"><button className="a-primary" disabled={!parsed||!!parseError||active||pending||!dirty} data-action="configuration.apply" onClick={()=>act('configuration.apply',{id:session.id,config:parsed})}><Check/>Apply configuration</button><button className="a-soft" data-action="view.update" disabled={!dirty&&!draft.parseError&&!parseError} onClick={()=>edit({text:pretty(plan),selectedKey:'',configText:'',parseError:''})}>Revert draft</button><span className="a-caption">{dirty?'Unapplied changes':'Saved configuration'}</span></div>
   <div className="a-custom-bundle"><h4>Keep this as a custom bundle</h4><p>Save the applied configuration under your own name. Use Export below to share it. Apply any pending changes first.</p><div className="a-form-grid"><div><label htmlFor="custom-bundle-name">Name</label><input id="custom-bundle-name" value={draft.name} data-action="view.update" onChange={e=>edit({name:e.target.value})}/></div><div><label htmlFor="custom-bundle-description">Description</label><input id="custom-bundle-description" value={draft.description} data-action="view.update" onChange={e=>edit({description:e.target.value})}/></div></div><div className="a-dialog-actions"><button className="a-soft" disabled={!parsed||!!parseError||dirty||!draft.name.trim()||pending} data-action="bundle.save" onClick={()=>act('bundle.save',{sessionId:session.id,name:draft.name.trim(),description:draft.description})}><Download/>Save custom bundle</button></div></div>
  </>}
  </>}
 </section>;
}
