import {delegationRoutingLabel} from './delegation-routing.js';
import {useOutsideDismiss} from './use-outside-dismiss';
import React,{useEffect,useState,useRef} from 'react';
import {ChevronDown,X,Paperclip} from 'lucide-react';
import {providerFields,modelOptions} from './setup-data';
import {newChatSetup,draftDefaults,draftDefaultsKey} from './new-chat';
import {useComposerPopover} from './composer-popover';
import {providerOptions} from './provider-options';
const EMPTY={};
export function AttachmentStrip({items=[],remove}){
 if(!items.length)return null;
 return <div className="a-attachments">{items.map(file=><div className="a-attachment" key={file.id}>{file.mime?.startsWith('image/')?<a href={file.url} target="_blank" rel="noopener noreferrer"><img src={file.url} alt={file.name}/></a>:<Paperclip/>}<a href={file.url} target="_blank" rel="noopener noreferrer" title={file.name}>{file.source==='clipboard-text'?(file.preview||'Pasted text'):file.name}<small>{file.source==='clipboard-text'?'Pasted text · ':''}{Math.ceil(file.size/1024)} KB</small></a>{remove&&<button type="button" className="a-icon" aria-label={'Remove '+file.name} data-action="attachment.remove" onClick={()=>remove(file.id)}><X/></button>}</div>)}</div>;
}
function configuredEffort(row,model){
 const field=providerFields(row,{model,default_model:model}).find(field=>field.id==='reasoning_effort');
 return row?.effort||row?.config?.reasoning_effort||row?.info?.defaults?.reasoning_effort||field?.default||'';
}
export function ModelControl({state,session,act,working}){
 const setup=newChatSetup(state),isDraft=!session,sessionId=session?.id||null,defaults=draftDefaults(state);
 // A bundle change validates defaults in the background. Keep the current
 // workspace's resolved choices visible until the new result is ready.
 const configurationRevision=state.configurationRevision||0;
 const defaultsContext=JSON.stringify([setup.location?.kind||'workspace',setup.workspace,configurationRevision]);
 const previousDefaults=useRef(null);
 if(isDraft&&defaults.phase==='ready')previousDefaults.current={context:defaultsContext,value:defaults};
 const resolvedDefaults=defaults.phase==='ready'||defaults.phase==='error'?defaults:previousDefaults.current?.context===defaultsContext?previousDefaults.current.value:defaults;
 const checkingDefaults=isDraft&&defaults.phase!=='ready'&&defaults.phase!=='error';
 const unavailable=session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||session?.historyLoaded===false;
 const shared=state.view?.composerModel||EMPTY,[draft,setDraft]=useState(shared),[error,setError]=useState('');
 useEffect(()=>setDraft(shared),[shared]);
 const edit=patch=>{const next={...draft,...patch};setDraft(next);act('view.update',{patch:{composerModel:next}})};
 const managed=setup.location?.kind==='managed';
 const location=managed?{location:{kind:'managed'}}:{};
 const draftCatalog=state.setup?.providersRequestedWorkspace===setup.workspace&&(state.setup?.providersLocation?.kind==='managed')===managed?state.setup:EMPTY;
 const cachedProviders=(draftCatalog.providers||[]).filter(row=>row.enabled!==false).map(row=>{
  const metadata=draftCatalog.providerCatalogs?.[row.id]?.metadata||draftCatalog.metadata?.[row.module]||{};
  return {...row,...metadata,info:{...metadata.info,defaults:{...metadata.info?.defaults,model:row.config?.default_model||row.config?.model||metadata.info?.defaults?.model}}};
 });
 const controls=state.runtimeControl?.[sessionId]||{},catalog=controls['configuration.catalog']||controls['configuration.providers'];
 const providers=isDraft?(resolvedDefaults.providers||cachedProviders):(catalog?.providers||session?.runtimeReport?.provider_choices?.map(row=>({id:row.id,sharedCatalogKey:row.sharedCatalogKey,info:{id:row.provider,display_name:row.display_name,defaults:{model:row.model,reasoning_effort:row.effort}}}))||[]);
 const pinned=isDraft?!!setup.selection?.model:(catalog?.pinned??!!(session?.runtimeReport?.selection||session?.selection));
 const initial=!catalog&&!session?.runtimeReport?session?.initialModel:undefined;
 const effective=isDraft?(pinned?setup.selection:resolvedDefaults.effective||{}):(session?.pendingModelSelection||catalog?.effective||session?.runtimeReport?.effective_selection||session?.selection||initial||{});
 const lastCall=[...(session?.execution?.nodes||[])].reverse().find(n=>n.kind==='llm'&&(n.sessionId===sessionId||(!n.sessionId&&!n.parentId)));
 const noProviders=(isDraft?defaults.phase==='ready':Array.isArray(catalog?.providers))&&!providers.length;
 const model=effective.model||(!isDraft?lastCall?.model:'')||(noProviders?'Set up a model':defaults.phase==='error'?'Model unavailable':!isDraft?'Choose a model':'Loading model…');
 const provider=effective.instance||effective.id||(!isDraft?lastCall?.provider:'')||'';
 const effectiveProvider=providers.find(row=>row.id===provider),effectiveMetadata=state.modelCatalogs?.[effectiveProvider?.sharedCatalogKey]?.metadata,effectiveEffort=effective.effort||configuredEffort(effectiveProvider,model)||configuredEffort(effectiveMetadata,model);
 const configuredProvider=(state.setup?.providers||[]).find(row=>row.id===provider);
 const providerMetadata={...state.setup?.metadata?.[configuredProvider?.module]?.info,...state.setup?.providerCatalogs?.[provider]?.metadata?.info,...effectiveProvider?.info};
 const options=providerOptions(providers);
 const providerLabel=options.find(row=>row.id===provider)?.label||providerMetadata?.display_name||providerMetadata?.id||(initial?.instance===provider?initial.providerLabel:'')||provider;
 const modelAndEffort=effectiveEffort?`${model} (${effectiveEffort})`:model;
 const usingChatGPTPlan=effectiveProvider?.info?.defaults?.auth_mode==='chatgpt_plan'||(isDraft&&effectiveProvider?.config?.auth_mode==='chatgpt_plan');
 const loadingCapabilities=!isDraft&&!noProviders&&!effectiveEffort&&!effectiveMetadata&&!effectiveProvider?.configSchema&&!effectiveProvider?.info?.config_fields;
 const modelLabel=providerLabel?`${providerLabel} · ${modelAndEffort}`:modelAndEffort;
 const open=draft.open&&(draft.sessionId??null)===sessionId,popover=useRef(null),position=useComposerPopover(open,popover);
 useOutsideDismiss(open,popover,()=>edit({open:false}));
 const selected=providers.find(row=>row.id===draft.instance),result=controls['configuration.providerModels'];
 const entry=isDraft?draftCatalog.providerCatalogs?.[draft.instance]:state.modelCatalogs?.[selected?.sharedCatalogKey]||controls.modelCatalogs?.[draft.instance]||(result&&result.provider===draft.instance?{phase:'ready',models:result.models}:null);
 const lastModels=useRef(new Map());let models=modelOptions(entry?.models||[]);
 if(models.length)lastModels.current.set((isDraft?draftDefaultsKey(setup):sessionId)+'|'+draft.instance,models);
 else models=lastModels.current.get((isDraft?draftDefaultsKey(setup):sessionId)+'|'+draft.instance)||[];
 // Model catalogs belong to a connection, even when several use the same type.
 const available=new Map(models.map(row=>[row.id,row]));
 const defaultModel=selected?.info?.defaults?.model;
 if(defaultModel&&!available.has(defaultModel))available.set(defaultModel,{id:defaultModel,name:defaultModel});
 models=[...available.values()];
 const metadata=entry?.metadata||selected||{};
 const choices=providerFields(metadata,{model:draft.model,default_model:draft.model}).find(field=>field.id==='reasoning_effort')?.choices||[];
 const effort=draft.effort||configuredEffort(selected,draft.model)||configuredEffort(metadata,draft.model);
 const effortPending=useRef(false);
 const request=useRef(''),touched=useRef(false);
 useEffect(()=>{
  if(!isDraft||(!setup.workspace&&!managed))return;
  const key=JSON.stringify([draftDefaultsKey(setup),configurationRevision]);if(request.current===key)return;
  const timer=setTimeout(()=>{request.current=key;
  // Prefetch during the draft, never on the click path and never by starting a session.
  act('configuration.defaults',{workspace:setup.workspace,bundle:setup.bundle||'',...location});
  if(draftCatalog===EMPTY)act('providers.list',{workspace:setup.workspace,...location});
  },250);return()=>clearTimeout(timer);
 },[isDraft,setup.workspace,setup.bundle,managed,configurationRevision]);
 useEffect(()=>{
  if(open&&!touched.current){const instance=effective.instance||effective.id||draft.instance||'',row=providers.find(p=>p.id===instance);edit({instance,model:effective.model||draft.model||row?.info?.defaults?.model||'',effort:effectiveEffort||draft.effort||''})}
 },[open,sessionId,providers.map(row=>row.id).join('|'),effective.instance,effective.id,effective.model,effectiveEffort]);
 function show(){touched.current=false;setError('');if(open){edit({open:false});return}
  edit({open:true,sessionId,instance:provider,model:effective.model||'',effort:effectiveEffort||''});
  if(session&&!controls['configuration.catalog'])act('runtime.control',{sessionId,operation:'configuration.catalog',args:{}});
 }
 async function apply(patch){
  touched.current=true;const next={...draft,...patch};edit(patch);setError('');
  if(!providers.some(row=>row.id===next.instance)||!next.model)return;
  const selection={instance:next.instance,model:next.model,...(next.effort?{effort:next.effort}:{})};
  try{if(isDraft)await act('view.update',{patch:{newSessionDraft:{...setup,selection}}});
   else await act('runtime.control',{sessionId,operation:'provider.queueSelection',args:selection});
  }catch(e){setError(e.message)}
 }
 function chooseProvider(value){
  if(!value)return;
  const row=providers.find(row=>row.id===value);
  if(!row)return;apply({instance:value,model:catalog?.selectionIssue?(draft.model||catalog.selection?.model||''):(row.info?.defaults?.model||''),effort:catalog?.selectionIssue?(draft.effort||catalog.selection?.effort||''):''});
  if(isDraft&&!draftCatalog.providerCatalogs?.[value])act('providers.models',{id:value,workspace:setup.workspace,...location});
  else if(!isDraft&&!state.modelCatalogs?.[row.sharedCatalogKey]&&!controls.modelCatalogs?.[value])act('runtime.control',{sessionId,operation:'configuration.catalog',args:{}});
 }
 function previewEffort(event){touched.current=true;effortPending.current=true;setDraft({...draft,effort:choices[Number(event.currentTarget.value)]});}
 function commitEffort(event){if(!effortPending.current)return;effortPending.current=false;const value=choices[Number(event.currentTarget.value)];if(value)apply({effort:value});}
 const catalogRequest=useRef('');
 useEffect(()=>{
  if(!session||unavailable)return;
  const key=sessionId+'|'+configurationRevision;
  if(catalogRequest.current===key||controls['configuration.catalog']?.configurationRevision===configurationRevision)return;
  catalogRequest.current=key;act('runtime.control',{sessionId,operation:'configuration.catalog',args:{}});
 },[sessionId,configurationRevision,unavailable]);
 const op=state.actionStatus?.[isDraft?'configuration.defaults':'runtime.control'];
 const failure=error||catalog?.error||(op?.phase==='error'?op.error:'')||defaults.error;
 return <div className="a-model-control" ref={popover}>
  <button type="button" className="a-model-trigger" aria-label="Model and reasoning settings" disabled={unavailable} aria-expanded={!!open} aria-busy={checkingDefaults||undefined} title={checkingDefaults?`${modelLabel} · Checking bundle settings…`:modelLabel} data-action="view.update" onClick={show}><span>{loadingCapabilities?'Loading model settings…':modelLabel}</span><ChevronDown/></button>
  {open&&<section className="a-model-popover a-compact-popover" style={position} aria-label="Conversation model">
   <div className="a-settings-row"><strong>Conversation model</strong><button type="button" className="a-icon" aria-label="Close model settings" data-action="view.update" onClick={()=>edit({open:false})}><X/></button></div>
   {usingChatGPTPlan&&<p className="a-caption">Using ChatGPT plan · <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Manage usage</a></p>}
   {catalog?.selectionIssue&&<p role="status">{catalog.selectionIssue}</p>}
   <label htmlFor="chat-provider">Provider</label><select id="chat-provider" value={draft.instance||''} data-action={isDraft?'view.update':'runtime.control'} disabled={working} onChange={e=>chooseProvider(e.target.value)}>
    {!draft.instance&&<option value="" disabled>{providers.length?'Choose a provider':noProviders?'No providers configured':'Loading providers…'}</option>}{draft.instance&&!selected&&<option value={draft.instance} disabled>{draft.instance} (unavailable)</option>}{options.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}
   </select>
   <label htmlFor="chat-model">Model</label><select id="chat-model" aria-label="Conversation model" value={draft.model||''} disabled={working||!selected} data-action={isDraft?'view.update':'runtime.control'} onChange={e=>apply({model:e.target.value,effort:''})}>
    {!draft.model&&<option value="">{entry?.phase==='working'?'Loading models…':'Choose a model'}</option>}{draft.model&&!models.some(row=>row.id===draft.model)&&<option value={draft.model}>{draft.model}</option>}{models.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
   </select>
   {choices.length>0?<><label htmlFor="chat-effort">Reasoning effort <strong>{effort||'Choose effort'}</strong></label><input id="chat-effort" type="range" min="0" max={choices.length-1} step="1" value={Math.max(0,choices.indexOf(effort))} aria-valuetext={effort||'Choose effort'} disabled={working||!draft.model} data-action={isDraft?'view.update':'runtime.control'} onChange={previewEffort} onPointerUp={commitEffort} onKeyUp={commitEffort} onBlur={commitEffort}/><div className="a-effort-labels"><span>{choices[0]}</span><span>{choices.at(-1)}</span></div></>:<small>{entry?.metadata||selected?.configSchema||selected?.info?.config_fields?'Reasoning effort is not configurable for this model.':'Loading model capabilities…'}</small>}
   {!isDraft&&<p className="a-muted" data-part="delegation-routing">{delegationRoutingLabel(catalog?.delegationRouting||session?.runtimeReport?.delegationRouting)}</p>}
   <div className="a-dialog-actions"><button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{composerModel:{...draft,open:false},panel:'settings',settingsSection:'setup',settingsExpanded:['ai-connections'],aiConnectionEditor:{...(state.view?.aiConnectionEditor||{}),step:'services'}}})}>Add connection</button><button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{composerModel:{...draft,open:false},panel:'settings',settingsSection:'setup',settingsExpanded:['ai-connections'],aiConnectionEditor:{...(state.view?.aiConnectionEditor||{}),step:'list'}}})}>Manage connections</button></div>
   <small>Model changes apply when you send the next message.</small><button type="button" className="a-link" onClick={()=>isDraft?act('providers.models',{id:draft.instance,workspace:setup.workspace,...location,refresh:true}):act('runtime.control',{sessionId,operation:'configuration.catalog',args:{refresh:true}})}>Refresh models</button>
   {failure&&<small className="a-danger" role="status">{failure}</small>}
   {!failure&&entry?.phase==='error'&&<small className="a-danger" role="status">The model list is unavailable. Your current selection is preserved.</small>}
  </section>}
 </div>;
}
export function readAttachment(file){return new Promise((resolve,reject)=>{if(file.size>32*1024*1024||!file.size){reject(new Error('Choose a nonempty file up to 32 MB.'));return}const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file)})}
