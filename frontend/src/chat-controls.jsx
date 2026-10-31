import {conversationHistoryPending} from './history-window.js';
import {configurationHeld} from './configuration-recovery';
import {ownershipState} from './ownership.js';
import {delegationRoutingLabel} from './delegation-routing.js';
import {useOutsideDismiss} from './use-outside-dismiss';
import React,{useEffect,useState,useRef,useId} from 'react';
import {ChevronDown,ChevronRight,ArrowLeft,X,Paperclip,Download} from 'lucide-react';
import {providerFields,modelOptions} from './setup-data';
import {newChatSetup,draftDefaults,draftDefaultsKey} from './new-chat';
import {useComposerPopover} from './composer-popover';
import {providerOptions} from './provider-options';
import {useDraftDiscovery} from './use-draft-discovery';
import {AttachmentPreview} from './attachment-preview';
import {imageAttachment} from './attachment-view.js';
const EMPTY={};
export function AttachmentStrip({items=[],remove,act,sessionId,privateDraft=false}){
 const [error,setError]=useState(''),[pending,setPending]=useState(null),[image,setImage]=useState(null);
 useEffect(()=>{if(image&&(image.sessionId!==sessionId||!items.some(file=>file.id===image.file.id)))setImage(null)},[sessionId,items,image]);
 const showImage=(file,event)=>setImage({file,sessionId,opener:event.currentTarget});
 const imageVisible=image&&image.sessionId===sessionId&&items.some(file=>file.id===image.file.id);
 async function preview(file){
  if(pending)return;setPending(file.id);setError('');
  try{await act('canvas.openAttachment',{sessionId,id:file.id})}
  catch(error){setError(error.message||'Could not open this attachment. You can still download it.')}
  finally{setPending(null)}
 }
 if(!items.length)return null;
 return <div className="a-attachments" data-private={privateDraft||undefined}>{items.map(file=><div className="a-attachment" key={file.id} data-attachment-id={file.id}>{imageAttachment(file)?<button type="button" className="a-attachment-preview-button" aria-haspopup="dialog" aria-label={'Preview '+file.name} onClick={e=>showImage(file,e)}><img src={file.url} alt={file.name}/></button>:<Paperclip/>}{imageAttachment(file)?<button type="button" className="a-attachment-preview-button a-attachment-preview-name" aria-haspopup="dialog" aria-label={'Preview '+file.name} title={file.name} onClick={e=>showImage(file,e)}>{file.name}<small>{Math.ceil(file.size/1024)} KB</small></button>:act&&sessionId&&file.mime==='text/plain'&&file.size<=1000000?<button type="button" className="a-link" disabled={!!pending} data-action="canvas.openAttachment" onClick={()=>preview(file)}>{file.source==='clipboard-text'?(file.preview||'Pasted text'):file.name}<small>{Math.ceil(file.size/1024)} KB</small></button>:<a href={file.url} target="_blank" rel="noopener noreferrer" title={file.name}>{file.source==='clipboard-text'?(file.preview||'Pasted text'):file.name}<small>{file.source==='clipboard-text'?'Pasted text · ':''}{Math.ceil(file.size/1024)} KB</small></a>}{act&&sessionId&&<a href={file.url} download={file.name} aria-label={"Download "+file.name}><Download size={14}/></a>}{remove&&<button type="button" className="a-icon" aria-label={'Remove '+file.name} data-action="attachment.remove" onClick={()=>remove(file.id)}><X/></button>}</div>)}{error&&<span role="alert">{error}</span>}{imageVisible&&<AttachmentPreview key={image.file.id} {...image} privateDraft={privateDraft} onClose={()=>setImage(null)}/>}</div>;
}
function configuredEffort(row,model){
 const field=providerFields(row,{model,default_model:model}).find(field=>field.id==='reasoning_effort');
 return row?.effort||row?.config?.reasoning_effort||row?.info?.defaults?.reasoning_effort||field?.default||'';
}
export function nextMessageBlocked(session,working=false){
 const generation=session?.collaborationGeneration;
 return !!(configurationHeld(session)||ownershipState(session).blocked||
  (generation?.id&&!generation.terminal)||
  (session?.messages||[]).some(row=>row.delivery?.status==='sending')||
  (session?.workers||[]).some(row=>['queued','starting','working','running','stopping'].includes(row.status))||
  ['working','running','busy','stopping'].includes(session?.status)||(working&&session?.status!=='starting'));
}
// Bridge only our acknowledged saves while their state update is in flight.
// Never replace an action's captured revision with a later poll/client revision.
export function useNextMessageConfiguration(session,act,sharedControl=null){
 const sessionId=session?.id,revision=session?.nextMessageRevision||0,owner=useRef(null);
 const [saving,setSaving]=useState(null);
 if(!owner.current||owner.current.sessionId!==sessionId)owner.current={sessionId,pending:false,ack:null};
 async function configure(patch,capturedRevision=revision){
  const request=owner.current;
  if(request.sessionId!==sessionId||request.pending)return false;
  const ack=request.ack,expectedRevision=ack&&capturedRevision>=ack.from&&capturedRevision<=ack.to?ack.to:capturedRevision;
  request.pending=true;setSaving(request);
  try{
   const receipt=await act('conversation.configure',{sessionId,expectedRevision,...patch});
   const next=receipt?.result?.revision;
   if(receipt?.accepted!==true||!Number.isInteger(next)||next!==expectedRevision+1)
    throw Error(receipt?.error||'Next message settings were not confirmed. Review the saved choices before trying again.');
   request.ack={from:ack?.to===expectedRevision?ack.from:expectedRevision,to:next};
   return true;
  }finally{request.pending=false;setSaving(current=>current===request?null:current)}
 }
 return sharedControl||{configure,pending:saving===owner.current};
}
export function ModelControl({state,session,act,working,embedded=false,compact=false,children,nextMessageControl,detail='standard'}){
 const descriptionId=useId();
 const setup=newChatSetup(state),isDraft=!session,sessionId=session?.id||null,defaults=draftDefaults(state);
 const nextMessage=useNextMessageConfiguration(session,act,nextMessageControl);
 // A different bundle has different defaults. Until its probe resolves, do not
 // present the previous bundle's connection as the next request's destination.
 const configurationRevision=state.configurationRevision||0,nextMessageRevision=session?.nextMessageRevision||0;
 const resolvedDefaults=defaults;
 const checkingDefaults=isDraft&&defaults.phase!=='ready'&&defaults.phase!=='error';
 const unavailable=!!state.navigationPending||session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||conversationHistoryPending(session);
 const blocked=unavailable||nextMessageBlocked(session,working)||nextMessage.pending;
 const shared=state.view?.composerModel||EMPTY,[draft,setDraft]=useState(shared),[error,setError]=useState(''),effortPending=useRef(null);
 useEffect(()=>{
  const pending=effortPending.current;
  // A poll can replace an equal view object during a slider gesture. Preserve
  // that gesture only within its original chat/provider/model selection.
  if(pending&&pending.sessionId===sessionId&&pending.instance===shared.instance&&pending.model===shared.model)
   setDraft({...shared,effort:pending.effort});
  else{effortPending.current=null;setDraft(shared)}
 },[shared,sessionId]);
 const edit=patch=>{const next={...draft,...patch,...(embedded?{open:true,sessionId}:{})};setDraft(next);act('view.update',{patch:{composerModel:next}})};
 const managed=setup.location?.kind==='managed';
 const location=managed?{location:{kind:'managed'}}:{};
 const draftCatalog=state.setup?.providersRequestedWorkspace===setup.workspace&&(state.setup?.providersLocation?.kind==='managed')===managed?state.setup:EMPTY;
 const cachedProviders=(draftCatalog.providers||[]).filter(row=>row.enabled!==false).map(row=>{
  const metadata=draftCatalog.providerCatalogs?.[row.id]?.metadata||draftCatalog.metadata?.[row.module]||{};
  return {...row,...metadata,info:{...metadata.info,defaults:{...metadata.info?.defaults,model:row.config?.default_model||row.config?.model||metadata.info?.defaults?.model}}};
 });
 const controls=state.runtimeControl?.[sessionId]||{},catalog=controls['configuration.catalog']||controls['configuration.providers'];
 const catalogIsCurrent=catalog?.configurationRevision===configurationRevision&&(catalog?.nextMessageRevision||0)===nextMessageRevision;
 const pendingBundle=session?.pendingBundleSelection;
 const providers=isDraft?(resolvedDefaults.providers||cachedProviders):(catalog?.providers||session?.runtimeReport?.provider_choices?.map(row=>({id:row.id,sharedCatalogKey:row.sharedCatalogKey,info:{id:row.provider,display_name:row.display_name,defaults:{model:row.model,reasoning_effort:row.effort}}}))||[]);
 const pinned=isDraft?!!setup.selection?.model:(catalog?.pinned??!!(session?.runtimeReport?.selection||session?.selection));
 const initial=!catalog&&!session?.runtimeReport?session?.initialModel:undefined;
 const effective=isDraft?(pinned?setup.selection:resolvedDefaults.effective||{}):(session?.pendingModelSelection||
  (pendingBundle?(!pendingBundle.resetModel&&session?.selection)||(catalogIsCurrent&&catalog?.effective)||{}:
   catalog?.effective||session?.runtimeReport?.effective_selection||session?.selection||initial||{}));
 const lastCall=[...(session?.execution?.nodes||[])].reverse().find(n=>n.kind==='llm'&&(n.sessionId===sessionId||(!n.sessionId&&!n.parentId)));
 const noProviders=(isDraft?defaults.phase==='ready':(!pendingBundle||catalogIsCurrent)&&Array.isArray(catalog?.providers))&&!providers.length;
 const model=effective.model||(!isDraft&&!pendingBundle?lastCall?.model:'')||(noProviders?'Set up a model':pendingBundle?'Bundle default':defaults.phase==='error'?'Model unavailable':!isDraft?'Choose a model':'Loading model…');
 const provider=effective.instance||effective.id||(!isDraft&&!pendingBundle?lastCall?.provider:'')||'';
 const effectiveProvider=providers.find(row=>row.id===provider),effectiveMetadata=state.modelCatalogs?.[effectiveProvider?.sharedCatalogKey]?.metadata,effectiveEffort=effective.effort||configuredEffort(effectiveProvider,model)||configuredEffort(effectiveMetadata,model);
 const configuredProvider=(state.setup?.providers||[]).find(row=>row.id===provider);
 const providerMetadata={...state.setup?.metadata?.[configuredProvider?.module]?.info,...state.setup?.providerCatalogs?.[provider]?.metadata?.info,...effectiveProvider?.info};
 const options=providerOptions(providers);
 const providerLabel=options.find(row=>row.id===provider)?.label||providerMetadata?.display_name||providerMetadata?.id||(initial?.instance===provider?initial.providerLabel:'')||provider;
 const modelAndEffort=effectiveEffort?`${model} (${effectiveEffort})`:model;
 const usingChatGPTPlan=effectiveProvider?.info?.defaults?.auth_mode==='chatgpt_plan'||(isDraft&&effectiveProvider?.config?.auth_mode==='chatgpt_plan');
 const modelLabel=providerLabel?`${providerLabel} · ${modelAndEffort}`:modelAndEffort;
 const open=embedded||(draft.open&&(draft.sessionId??null)===sessionId),popover=useRef(null),{style:position,panelRef,renderPopover}=useComposerPopover(open&&!embedded,popover);
 // The narrow-screen menu is locally mounted, but catalog demand is shared
 // with the server just like the desktop popover.
 useEffect(()=>{
  if(!embedded)return;
  act('view.update',{patch:{composerModel:{...shared,open:true,sessionId}}});
  return()=>{act('view.update',{patch:{composerModel:{open:false,sessionId}}})};
 },[embedded,sessionId]);
 const [page,setPage]=useState('overview'),focusRequested=useRef(false),returnRow=useRef(null);
 const navigate=next=>{returnRow.current=next==='overview'?page:null;focusRequested.current=compact;setPage(next)};
 const close=reason=>{focusRequested.current=false;edit({open:false});if(compact&&reason!=='outside')popover.current?.querySelector('button')?.focus()};
 useOutsideDismiss(open&&!embedded,popover,close,panelRef);
 useEffect(()=>{
  if(compact&&open&&position.visibility==='visible'&&focusRequested.current){
   focusRequested.current=false;
   const panel=panelRef.current;
   const preferred=returnRow.current?panel?.querySelector(`[data-composer-option="${returnRow.current}"]`):null;
   const visible=[...(panel?.querySelectorAll('select:not(:disabled),input:not(:disabled),button:not(:disabled)')||[])].filter(node=>!node.closest('[hidden]'));
   (preferred||(page!=='overview'?panel?.querySelector('[aria-label="Back to chat options"]'):null)||visible.find(node=>node.closest('[data-composer-page]'))||visible[0])?.focus();
   returnRow.current=null;
  }
 },[compact,open,page,position.visibility]);
 useEffect(()=>{focusRequested.current=false;setPage('overview')},[sessionId]);
 const previousPanel=useRef(state.view?.panel);
 useEffect(()=>{
  if(previousPanel.current===state.view?.panel)return;
  previousPanel.current=state.view?.panel;
  if(compact&&open)close('outside');
 },[state.view?.panel]);
 const selected=providers.find(row=>row.id===draft.instance),result=controls['configuration.providerModels'];
 const entry=isDraft?draftCatalog.providerCatalogs?.[draft.instance]:state.modelCatalogs?.[selected?.sharedCatalogKey]||controls.modelCatalogs?.[draft.instance]||(result&&result.provider===draft.instance?{phase:'ready',models:result.models}:null);
 const lastModels=useRef(new Map()),modelCacheKey=JSON.stringify([isDraft?draftDefaultsKey(setup):sessionId,configurationRevision,draft.instance,selected?.sharedCatalogKey||entry?.sharedCatalogKey||null]);let models=modelOptions(entry?.models||[]);
 // An explicit successful empty list replaces old choices. Deferred/loading
 // views can reuse them; the saved/default selection is retained separately.
 if(models.length||(entry?.phase==='ready'&&!entry.modelsDeferred&&Array.isArray(entry.models))){lastModels.current.delete(modelCacheKey);lastModels.current.set(modelCacheKey,models);while(lastModels.current.size>16)lastModels.current.delete(lastModels.current.keys().next().value)}
 else models=lastModels.current.get(modelCacheKey)||[];
 // Model catalogs belong to a connection, even when several use the same type.
 const available=new Map(models.map(row=>[row.id,row]));
 const defaultModel=selected?.info?.defaults?.model;
 if(defaultModel&&!available.has(defaultModel))available.set(defaultModel,{id:defaultModel,name:defaultModel});
 models=[...available.values()];
 const metadata=entry?.metadata||selected||{};
 const choices=providerFields(metadata,{model:draft.model,default_model:draft.model}).find(field=>field.id==='reasoning_effort')?.choices||[];
 const effort=draft.effort||configuredEffort(selected,draft.model)||configuredEffort(metadata,draft.model);
 const touched=useRef(false),draftRevision=useRef({sessionId,revision:nextMessageRevision});
 if(draftRevision.current.sessionId!==sessionId){touched.current=false;draftRevision.current={sessionId,revision:nextMessageRevision}}
 const discovery=useDraftDiscovery({enabled:isDraft,setup,defaults,configurationRevision,hostInstanceId:state.hostInstanceId,act,needsProviders:draftCatalog===EMPTY});
 useEffect(()=>{
  if(open&&!touched.current){draftRevision.current={sessionId,revision:nextMessageRevision};const instance=effective.instance||effective.id||draft.instance||'',row=providers.find(p=>p.id===instance);edit({instance,model:effective.model||draft.model||row?.info?.defaults?.model||'',effort:effectiveEffort||draft.effort||''})}
 },[open,sessionId,nextMessageRevision,providers.map(row=>row.id).join('|'),effective.instance,effective.id,effective.model,effectiveEffort]);
 function show(){touched.current=false;draftRevision.current={sessionId,revision:nextMessageRevision};effortPending.current=null;setError('');if(open){close();return}
  setPage('overview');returnRow.current=null;
  focusRequested.current=compact;
  edit({open:true,sessionId,instance:provider,model:effective.model||'',effort:effectiveEffort||''});
 }
 async function apply(patch,capturedRevision=draftRevision.current.revision){
  if(blocked)return;
  touched.current=true;const next={...draft,...patch};edit(patch);setError('');
  if(!providers.some(row=>row.id===next.instance)||!next.model)return;
  const selection={instance:next.instance,model:next.model,...(next.effort?{effort:next.effort}:{})};
  try{if(isDraft)await act('view.update',{patch:{newSessionDraft:{...setup,selection}}});
   else await nextMessage.configure({selection},capturedRevision);
  }catch(e){setError(e.message)}
 }
 function chooseProvider(value){
  if(!value||blocked)return;
  const row=providers.find(row=>row.id===value);
  if(!row)return;apply({instance:value,model:catalog?.selectionIssue?(draft.model||catalog.selection?.model||''):(row.info?.defaults?.model||''),effort:catalog?.selectionIssue?(draft.effort||catalog.selection?.effort||''):''});
  if(isDraft&&!draftCatalog.providerCatalogs?.[value])act('providers.models',{id:value,workspace:setup.workspace,...location});
  else if(!isDraft&&!state.modelCatalogs?.[row.sharedCatalogKey]&&!controls.modelCatalogs?.[value])act('runtime.control',{sessionId,operation:'configuration.catalog',args:{}});
 }
 function previewEffort(event){if(blocked)return;const value=choices[Number(event.currentTarget.value)];touched.current=true;effortPending.current={sessionId,instance:draft.instance,model:draft.model,effort:value,revision:draftRevision.current.revision};setDraft({...draft,effort:value});}
 function commitEffort(){const pending=effortPending.current;effortPending.current=null;if(pending&&pending.sessionId===sessionId&&pending.instance===draft.instance&&pending.model===draft.model&&choices.includes(pending.effort))apply({effort:pending.effort},pending.revision);}
 const catalogRequest=useRef('');
 const missingCatalogs=providers.filter(row=>row.sharedCatalogKey&&!state.modelCatalogs?.[row.sharedCatalogKey]).map(row=>row.sharedCatalogKey).join('|');
 useEffect(()=>{
  if(!open||!session||unavailable)return;
  const key=sessionId+'|'+configurationRevision+'|'+nextMessageRevision+'|'+missingCatalogs;
  if(catalogRequest.current===key||catalogIsCurrent&&!missingCatalogs)return;
  catalogRequest.current=key;act('runtime.control',{sessionId,operation:'configuration.catalog',args:{}});
 },[open,sessionId,configurationRevision,nextMessageRevision,unavailable,missingCatalogs]);
 const op=state.actionStatus?.[isDraft?'configuration.defaults':'runtime.control'];
 const failure=error||session?.nextMessageError||discovery.error||catalog?.error||(!isDraft&&op?.phase==='error'?op.error:'')||defaults.error;
 const delayed=isDraft&&checkingDefaults&&discovery.delayed;
 const renderPanel=embedded?content=>content:renderPopover,Advanced=embedded?'details':React.Fragment;
 const paged=compact&&!embedded;
 // These text rows already expose their purpose/value. An icon-label tooltip
 // on focus covers the compact menu heading instead of adding useful help.
 const option=(id,label,value,sub)=> <button type="button" className="a-composer-option" data-tooltip="off" data-composer-option={id} aria-label={label} aria-describedby={`${descriptionId}-${id}`} onClick={()=>navigate(id)}><span id={`${descriptionId}-${id}`}>{value}{sub&&<small>{sub}</small>}</span><ChevronRight/></button>;
 return <div className="a-model-control" ref={popover}>
  {!embedded&&<button type="button" className="a-model-trigger" aria-label="Model and reasoning settings" aria-haspopup={compact?'dialog':undefined} disabled={unavailable} aria-expanded={!!open} aria-busy={checkingDefaults||undefined} title={checkingDefaults?`${modelLabel} · Checking bundle settings…`:modelLabel} data-action="view.update" onClick={show}><span>{!effective.model&&(delayed||failure)?'Model settings unavailable':compact?modelAndEffort:modelLabel}</span>{(session?.pendingModelSelection||pendingBundle)&&<small>Next message</small>}<ChevronDown/></button>}
  {open&&renderPanel(<section ref={panelRef} className={embedded?"a-embedded-model":`a-model-popover a-compact-popover${compact?' a-composer-options-panel':''}`} style={embedded?undefined:position} role={compact?'dialog':undefined} aria-label={compact?'Chat options':'Conversation model'} aria-describedby={paged?`${descriptionId}-heading`:undefined}>
   {!embedded&&<div className="a-settings-row a-composer-options-heading">{paged&&page!=='overview'&&<button type="button" className="a-icon" aria-label="Back to chat options" onClick={()=>navigate('overview')}><ArrowLeft/></button>}<strong id={`${descriptionId}-heading`}>{paged?({overview:'Model and effort',model:'Model',effort:'Reasoning effort',provider:'Provider',bundle:'Agent bundle'})[page]:compact?'Chat options':'Conversation model'}</strong><button type="button" className="a-icon" aria-label={compact?'Close chat options':'Close model settings'} data-action="view.update" onClick={close}><X/></button></div>}
   {paged&&<div data-composer-page="overview" hidden={page!=='overview'}>
    {option('model','Choose model',model,detail==='minimal'?'':providerLabel)}
    {option('effort','Choose reasoning effort',`Effort · ${effectiveEffort||'Model default'}`)}
    {option('provider','Choose provider',`Provider · ${providerLabel||'Choose a provider'}`)}
   </div>}
   <div data-composer-page="provider" hidden={paged&&page!=='provider'}>
   <label className={paged?'a-sr-only':undefined} htmlFor="chat-provider">Provider</label><select id="chat-provider" value={draft.instance||''} data-action={isDraft?'view.update':'conversation.configure'} disabled={blocked} onChange={e=>chooseProvider(e.target.value)}>
    {!draft.instance&&<option value="" disabled>{providers.length?'Choose a provider':noProviders?'No providers configured':failure||delayed?'Providers unavailable':'Loading providers…'}</option>}{draft.instance&&!selected&&<option value={draft.instance} disabled>{draft.instance} (unavailable)</option>}{options.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}
   </select>
   </div>
   <div data-composer-page="model" hidden={paged&&page!=='model'}>
   <label className={paged?'a-sr-only':undefined} htmlFor="chat-model">Model</label><select id="chat-model" aria-label="Conversation model" value={draft.model||''} disabled={blocked||!selected} data-action={isDraft?'view.update':'conversation.configure'} onChange={e=>apply({model:e.target.value,effort:''})}>
    {!draft.model&&<option value="">{entry?.phase==='working'?'Loading models…':'Choose a model'}</option>}{draft.model&&!models.some(row=>row.id===draft.model)&&<option value={draft.model}>{draft.model}</option>}{models.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
   </select>
   </div>
   <Advanced>{embedded&&<summary>Advanced model options</summary>}
   <div data-composer-page="effort" hidden={paged&&page!=='effort'}>
   {choices.length>0?<><label htmlFor="chat-effort">Reasoning effort <strong>{effort||'Choose effort'}</strong></label><input id="chat-effort" type="range" min="0" max={choices.length-1} step="1" value={Math.max(0,choices.indexOf(effort))} aria-valuetext={effort||'Choose effort'} disabled={blocked||!draft.model} data-action={isDraft?'view.update':'conversation.configure'} onChange={previewEffort} onPointerUp={commitEffort} onKeyUp={commitEffort} onBlur={commitEffort}/><div className="a-effort-labels"><span>{choices[0]}</span><span>{choices.at(-1)}</span></div></>:<small>{entry?.metadata||selected?.configSchema||selected?.info?.config_fields?'Reasoning effort is not configurable for this model.':failure||delayed?'Model capabilities unavailable.':'Loading model capabilities…'}</small>}
   </div>
   {!isDraft&&<p hidden={paged&&(page!=='model'||detail==='minimal')} className="a-muted" data-part="delegation-routing">{delegationRoutingLabel(catalog?.delegationRouting||session?.runtimeReport?.delegationRouting)}</p>}
   <div hidden={paged&&page!=='provider'}>
   <div className="a-dialog-actions"><button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{composerModel:{...draft,open:false},panel:'settings',settingsSection:'setup',settingsExpanded:['ai-connections'],aiConnectionEditor:{...(state.view?.aiConnectionEditor||{}),step:'services'}}})}>Add connection</button><button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{composerModel:{...draft,open:false},panel:'settings',settingsSection:'setup',settingsExpanded:['ai-connections'],aiConnectionEditor:{...(state.view?.aiConnectionEditor||{}),step:'list'}}})}>Manage connections</button></div>
   </div>
   {!paged&&<small>Model changes apply when you send the next message.</small>}<button type="button" className="a-link" hidden={paged&&page!=='model'} onClick={()=>isDraft&&!draft.instance?discovery.retry():isDraft?act('providers.models',{id:draft.instance,workspace:setup.workspace,...location,refresh:true}):act('runtime.control',{sessionId,operation:'configuration.catalog',args:{refresh:true}})}>Refresh models</button>
   </Advanced>
   {typeof children==='function'?children({page,navigate}):children}
   {usingChatGPTPlan&&<p className="a-caption">Using ChatGPT plan · <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Manage usage</a></p>}
   {catalog?.selectionIssue&&<p role="status">{catalog.selectionIssue}</p>}
   {isDraft&&(failure||delayed)&&<div className="a-discovery-recovery" role="status">{delayed&&!failure&&<p>Model settings are taking longer than expected. You can retry without refreshing or losing your draft.</p>}<button type="button" className="a-soft" onClick={discovery.retry}>Retry model settings</button></div>}
   {failure&&<small className="a-danger" role="status">{failure}</small>}
   {!failure&&entry?.phase==='error'&&<small className="a-danger" role="status">The model list is unavailable. Your current selection is preserved.</small>}
   <p className="a-caption" role="status">{nextMessage.pending?'Saving Next message settings…':!isDraft&&(session?.pendingModelSelection||pendingBundle)?'Next message · Saved; not applied yet.':'Next message · Changes apply when you send.'}</p>
   {paged&&detail==='detailed'&&page==='overview'&&<p className="a-caption">Saved choices affect the next message, not running work. Bundles change tools and instructions.</p>}
  </section>)}
 </div>;
}
export function readAttachment(file){return new Promise((resolve,reject)=>{if(file.size>32*1024*1024||!file.size){reject(new Error('Choose a nonempty file up to 32 MB.'));return}const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.onabort=()=>reject(new Error('Reading '+file.name+' was cancelled. Nothing was uploaded.'));reader.readAsDataURL(file)})}
