import React,{useContext,useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {Check,Download,FolderOpen,Upload} from 'lucide-react';
import {SettingsLayoutContext} from './settings-layout';
import {FileDrop} from './settings-ui';
import {WorkingLabel} from './working-label';
import {clearAppearancePreview,previewAppearance} from './appearance-preview';
import {appearanceImportFileArgs,appearanceMutationTarget} from './appearance-library';
import {appearanceCatalogFor} from './appearance-catalog';
import {useThemeScheme} from './theme-presentation';
import {AppearanceThumbnail} from './appearance-thumbnail';
import {appearancePreferenceMatches,effectivePreferenceValue,reconcileAppearancePreferenceFeedback,reconcileAppearancePreferenceValues,sparseExecutionVisibilityPatch} from './appearance-preferences';
import './appearance-settings.css';

const METRIC_CHOICES=[
 ['elapsedSeconds','Elapsed time'],['grossInputTokens','Input tokens'],['outputTokens','Output tokens'],
 ['grossTotalTokens','Total tokens'],['cacheReadTokens','Cache read tokens'],['cacheWriteTokens','Cache write tokens'],
 ['reasoningTokens','Reasoning tokens'],['voiceSeconds','Voice seconds'],['costUsd','Cost'],['calls','Calls'],
];
const PREFERENCE_GROUPS=[
 {id:'visual-basics',title:'Visual basics',fields:[['appearanceId','Appearance'],['scheme','Color mode'],['layout','Layout'],['decorations','Background decorations'],['density','Density'],['accent','Accent color']]},
 {id:'conversation-display',title:'Conversation display',fields:[['interfaceDetail','Interface detail'],['conversationStatus','Conversation status'],['collapseInterimReplies','Interim updates'],['workStartView','Starting view'],['planPlacement','Plan location']]},
 {id:'work-visibility',title:'Work visibility and metrics',fields:[['executionDetail','Work detail'],['executionVisibility','Work visibility'],['executionMetrics','Work metrics']]},
];
const cloneValue=value=>value===undefined||value===null||typeof value!=='object'?value:JSON.parse(JSON.stringify(value));
function stableValue(value){
 if(Array.isArray(value))return '['+value.map(stableValue).join(',')+']';
 if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stableValue(value[key])).join(',')+'}';
 return JSON.stringify(value);
}
function resultValue(receipt){
 if(!receipt||receipt.accepted===false||receipt.ok===false)throw Object.assign(Error(receipt?.error||'The preference change was not confirmed.'),{accepted:receipt?.accepted,code:receipt?.code,status:receipt?.status});
 const result=receipt.result||receipt;
 if(['awaiting-storage','awaiting-browser','pending'].includes(result?.status))throw Error('This browser has not confirmed the preference change yet.');
 if(result?.status==='failed')throw Error(result.error||'The preference change was not confirmed.');
 return result;
}

function PreferenceFeedback({field,scope,feedback,onRetry}){
 if(!feedback)return null;
 const pending=feedback.status==='pending';
 return <span className="a-appearance-feedback" data-feedback-for={field}>
  {feedback.message&&<small className="a-appearance-field-feedback" role="status" tabIndex={-1} aria-live="polite">{feedback.message}</small>}
  {feedback.status==='failed'&&feedback.retryable!==false&&<button type="button" className="a-link a-appearance-retry" disabled={pending} data-action="appearance.preferences.retry" onClick={event=>onRetry(field,scope,event.currentTarget)}>Retry</button>}
 </span>;
}

function PreferenceRow({field,scope,label,help,feedback,onRetry,children}){
 const headingId=`appearance-preference-${field}`;
 const status=<PreferenceFeedback field={field} scope={scope} feedback={feedback} onRetry={onRetry}/>;
 if(field==='appearanceId')return <section className="a-appearance-gallery-section" data-appearance-preference={field}>
  <div className="a-appearance-gallery-heading"><div><h3 id={headingId}>{label}</h3>{help&&<p>{help}</p>}</div></div>
  {children}{status}
 </section>;
 return <div className="a-appearance-option" data-appearance-preference={field}>
  <div className="a-appearance-option-description">
   <div className="a-appearance-option-heading"><h3 id={headingId}>{label}</h3></div>
   {help&&<p>{help}</p>}{status}
  </div>
  <div className="a-appearance-control">{children}</div>
 </div>;
}

export function AppearanceSettings({
 state,appearance=state.appearance||{},act,editor:editorState,previewOwner:clientPreviewOwner,preferenceStatus,
 onPreferenceChange=()=>{},onPreferenceSelect=()=>{},
}){
 const {active}=useContext(SettingsLayoutContext);
 const catalog=appearanceCatalogFor(state.client?.hostInstanceId);
 const cached=useSyncExternalStore(catalog.subscribe,catalog.getSnapshot,catalog.getSnapshot);
 const library=cached.library||{items:[],errors:[],directory:''},libraryError=cached.error;
 const [operations,setOperations]=useState({}),[fieldFeedback,setFieldFeedback]=useState({}),[fieldValues,setFieldValues]=useState({}),[profileFeedback,setProfileFeedback]=useState(null);
 const [error,setError]=useState(null),[notice,setNotice]=useState(''),[deleteTarget,setDeleteTarget]=useState(null);
 const operationOwners=useRef(new Map()),operationFeedbackOwner=useRef(null),fieldOwners=useRef(new Map()),profileOwner=useRef(null),profileRetry=useRef(null);
 const ownPreviewOwner=useRef({});
 const previewOwner=clientPreviewOwner||ownPreviewOwner;
 const api=useRef(act),alive=useRef(false),activeRef=useRef(active),editor=useRef(null);
 api.current=act;activeRef.current=active;
 const activeScope=appearance.activeScope==='client'||appearance.writeScope==='client'?'client':'shared';
 const preferences=appearance.effective||appearance.renderedPreferences||{};
 const thumbnailMode=useThemeScheme(effectivePreferenceValue('scheme',preferences));
 const {draft,setDraft,compareAndSetDraft,storageError}=editorState||{};
 const {entry:editing,name,css,description,dirty}=draft||{entry:null,name:'Reference',css:'',description:'',dirty:false};
 const visibleError=error?.draft===draft?error?.message:'';
 const confirmedPreview=appearance.preview?.theme||null;
 if(confirmedPreview)previewOwner.current.active=true;
 const libraryItems=library.items||[];
 const savedItems=libraryItems.filter(item=>item.source==='saved');
 const preferenceKey=stableValue([preferences,appearance.namespace,appearance.generation,appearance.local?.revision,appearance.shared?.revision]);
 const busyFor=key=>operations[key]==='pending';
 const mutationBusy=['save','update','delete','import','export','read','preview','cancel-preview','revert'].some(busyFor);
 const fieldKey=(scope,field)=>`${scope==='client'?'client':'shared'}:${field}`;
 const profileValues=scope=>{
  const values=(scope==='shared'?appearance.shared:appearance.local)?.values;
  if(values&&typeof values==='object'&&!Array.isArray(values))return values;
  return scope===activeScope?preferences:{};
 };
 const effectiveValue=(field,scope=activeScope)=>{
  const values=profileValues(scope);
  return effectivePreferenceValue(field,values,values.appearanceId??'reference');
 };
 const displayedValue=(field,scope=activeScope)=>{
  const entry=reconciledValues(fieldValues)[fieldKey(scope,field)];
  if(!entry)return effectiveValue(field,scope);
  return field==='executionVisibility'&&entry.patch?{...effectiveValue(field,scope),...entry.patch}:entry.value;
 };
 const targetForScope=scope=>{
  const profile=scope==='shared'?appearance.shared:appearance.local;
  return {namespace:appearance.namespace,generation:appearance.generation,expectedRevision:profile?.revision,
   ...(scope==='client'?{expectedStorageVersion:appearance.local?.storageVersion}:{})};
 };
 const latestAppearance=useRef(appearance),receiptRevisions=useRef(new Map());
 latestAppearance.current=appearance;
 const receiptKey=(scope,owner)=>stableValue([owner.namespace,owner.generation,scope]);
 const feedbackAppearance=()=>{
  const current=latestAppearance.current,next={...current};
  for(const scope of ['shared','client']){
   const profile=scope==='shared'?'shared':'local',revision=receiptRevisions.current.get(receiptKey(scope,current));
   if(Number.isSafeInteger(revision))next[profile]={...current[profile],revision:Math.max(current[profile]?.revision||0,revision)};
  }
  return next;
 };
 const confirmReceipt=(scope,result,target)=>{
  const confirmation={namespace:result.namespace??target.namespace,generation:result.generation??target.generation,
   revision:result.revision??(scope==='shared'?result.shared?.revision:result.local?.revision)};
  if(Number.isSafeInteger(confirmation.revision)){
   const key=receiptKey(scope,confirmation);
   receiptRevisions.current.set(key,Math.max(receiptRevisions.current.get(key)||0,confirmation.revision));
  }
  return confirmation;
 };
 const receiptStillCurrent=(scope,confirmation)=>!!reconcileAppearancePreferenceFeedback({
  receipt:{status:'stored',scope,confirmation},
 },feedbackAppearance()).receipt;
 const documentMatches=target=>latestAppearance.current.namespace===target.namespace&&latestAppearance.current.generation===target.generation;
 const reconciledValues=values=>{
  let next=reconcileAppearancePreferenceValues(values,latestAppearance.current);
  for(const [key,entry] of Object.entries(next)){
   if(entry.status==='failed'&&!Object.hasOwn(reconcileAppearancePreferenceValues({[key]:entry},feedbackAppearance()),key)){
    next={...next};
    delete next[key];
   }
  }
  return next;
 };
 const relatedPending=(field,scope)=>['appearanceId','accent'].includes(field)&&
  ['appearanceId','accent'].some(related=>fieldOwners.current.has(fieldKey(scope,related)));
 const displayCurrentId=displayedValue('appearanceId',activeScope);

 useEffect(()=>{
  alive.current=true;
  return()=>{alive.current=false;operationOwners.current.clear();fieldOwners.current.clear();profileOwner.current=null;profileRetry.current=null};
 },[]);
 useEffect(()=>{
  if(!active){
   operationOwners.current.clear();setOperations({});setError(null);setNotice('');setDeleteTarget(null);
   return;
  }
  const timer=setInterval(()=>catalog.refresh(api.current).catch(()=>{}),30000);
  catalog.refresh(api.current,{force:false}).catch(()=>{});
  return()=>{clearInterval(timer);void clearAppearancePreview(previewOwner.current,api.current).catch(()=>{})};
 },[active,catalog]);
 useEffect(()=>{
  setFieldValues(current=>reconciledValues(current));
  setFieldFeedback(current=>reconcileAppearancePreferenceFeedback(current,feedbackAppearance()));
  setProfileFeedback(current=>{
   if(!current)return current;
   return reconcileAppearancePreferenceFeedback({profile:current},feedbackAppearance()).profile||null;
  });
 },[preferenceKey,activeScope,appearance.local?.storageVersion,fieldValues,fieldFeedback,profileFeedback]);

 function fieldPending(field,scope=activeScope){return fieldFeedback[fieldKey(scope,field)]?.status==='pending';}
 function retryPreference(field,scope=activeScope){
  const key=fieldKey(scope,field);
  if(fieldOwners.current.has(key)||relatedPending(field,scope))return;
  const feedback=reconcileAppearancePreferenceFeedback(fieldFeedback,feedbackAppearance())[key];
  if(feedback?.status!=='failed'||feedback.retryable===false)return;
  const previous=fieldValues[key];
  if(previous?.status==='failed')void commitPreference(field,previous.value,scope,{retry:true,patch:previous.patch,target:previous.target});
 }
 async function commitPreference(field,value,scope=activeScope,{retry=false,patch:retryPatch,target}={}){
  const key=fieldKey(scope,field);
  if(fieldOwners.current.has(key)||relatedPending(field,scope))return null;
  const currentValue=displayedValue(field,scope);
  const previous=reconciledValues(fieldValues)[key];
  if(!retry&&appearancePreferenceMatches(field,value,currentValue)&&previous?.status!=='failed'){
   setFieldFeedback(values=>{if(!Object.hasOwn(values,key))return values;const next={...values};delete next[key];return next});
   return null;
  }
  let patch=value;
  if(field==='executionVisibility'){
   patch=retryPatch??sparseExecutionVisibilityPatch(value,currentValue);
   if(patch&&typeof patch==='object'&&!Array.isArray(patch)&&Object.keys(patch).length===0)return null;
  }
  setProfileFeedback(null);
  const targetAtGesture=target||targetForScope(scope);
  const confirmation={namespace:targetAtGesture.namespace??appearance.namespace,generation:targetAtGesture.generation??appearance.generation};
  const appearanceId=effectiveValue('appearanceId',scope);
  const token={},attempt=cloneValue(value),savedPatch=cloneValue(patch),savedTarget=cloneValue(targetAtGesture);
  const isCurrent=()=>alive.current&&fieldOwners.current.get(key)===token;
  fieldOwners.current.set(key,token);
  setFieldValues(values=>({...values,[key]:{operation:field,value:attempt,patch:savedPatch,status:'pending',scope,target:savedTarget,appearanceId}}));
  setFieldFeedback(values=>({...values,[key]:{status:'pending',message:'Saving…'}}));
  try{
   const result=resultValue(await onPreferenceChange(field,cloneValue(patch),{...savedTarget,scope,retry}));
   const confirmed=confirmReceipt(scope,result,savedTarget);
   if(isCurrent()){
    if(!receiptStillCurrent(scope,confirmed)){
     setFieldValues(values=>{const next={...values};delete next[key];return next});
     setFieldFeedback(values=>{const next={...values};delete next[key];return next});
     return result;
    }
    setFieldValues(values=>({...values,[key]:{operation:field,value:attempt,patch:savedPatch,status:'stored',scope,target:savedTarget,appearanceId,confirmation:{
     namespace:result.namespace??confirmation.namespace,generation:result.generation??confirmation.generation,
     revision:result.revision,storageVersion:result.storageVersion,
    }}}));
    setFieldFeedback(values=>{const next={...reconcileAppearancePreferenceFeedback(values,feedbackAppearance())};delete next[key];return next});
    if(field==='appearanceId'){
     const accentKey=fieldKey(scope,'accent');
     setFieldValues(values=>{const next={...values};delete next[accentKey];return next});
     setFieldFeedback(values=>{const next={...values};delete next[accentKey];return next});
    }
   }
   return result;
  }catch(exception){
   if(isCurrent()){
    const current=latestAppearance.current;
    const profile=scope==='shared'?'shared':'local';
    if(!documentMatches(confirmation)||Number.isSafeInteger(savedTarget.expectedRevision)&&
       feedbackAppearance()[profile]?.revision>savedTarget.expectedRevision||
       field==='accent'&&effectivePreferenceValue('appearanceId',(scope==='shared'?current.shared:current.local)?.values||{})!==appearanceId){
     setFieldValues(values=>{const next={...values};delete next[key];return next});
     setFieldFeedback(values=>{const next={...values};delete next[key];return next});
     return null;
    }
    setFieldValues(values=>({...values,[key]:{operation:field,value:attempt,patch:savedPatch,status:'failed',scope,target:savedTarget,appearanceId}}));
    setFieldFeedback(values=>({...values,[key]:{status:'failed',scope,
     confirmation:{...confirmation,revision:savedTarget.expectedRevision},message:exception?.message||'The preference change failed.'}}));
   }
   return null;
  }finally{
   if(fieldOwners.current.get(key)===token)fieldOwners.current.delete(key);
  }
 }
 function selectProfile(targetScope,retry=false){
  if(profileOwner.current||targetScope===activeScope)return;
  const token={},isCurrent=()=>alive.current&&profileOwner.current===token;
  const gesture={...targetForScope('client')};
  if(targetScope==='client'&&appearance.local?.values===null)gesture.expectedSharedRevision=appearance.shared?.revision;
  profileOwner.current=token;
  profileRetry.current={kind:'select',scope:targetScope};
  setProfileFeedback({scope:targetScope,status:'pending',message:'Saving…'});
  void (async()=>{
   try{
    const result=resultValue(await onPreferenceSelect(targetScope,{...gesture,retry}));
    if(isCurrent()){
     profileRetry.current=null;
     confirmReceipt('client',result,gesture);
     setProfileFeedback(null);
    }
   }catch(exception){
    if(isCurrent()){
     if(!documentMatches(gesture)){profileRetry.current=null;setProfileFeedback(null);return}
     setProfileFeedback({scope:'client',status:'failed',confirmation:{namespace:gesture.namespace,generation:gesture.generation,revision:gesture.expectedRevision},message:exception?.message||'Could not change the active appearance profile.'});
    }
   }finally{if(profileOwner.current===token)profileOwner.current=null}
  })();
 }
 function retryProfile(){
  if(profileOwner.current)return;
  const retry=profileRetry.current;
  if(retry?.kind==='select')selectProfile(retry.scope,true);
 }

 async function runOperation(key,operation){
  if(!activeRef.current||operationOwners.current.has(key))return null;
  const token={},draftAtStart=draft,isCurrent=()=>alive.current&&activeRef.current&&operationOwners.current.get(key)===token;
  operationOwners.current.set(key,token);operationFeedbackOwner.current=token;
  setOperations(values=>({...values,[key]:'pending'}));setError(null);setNotice('');
  try{return await operation(isCurrent)}catch(exception){
   // Section changes release UI locks, not an unchanged draft's failure. A newer
   // operation or persistent draft revision owns its own feedback, even on return.
   if(alive.current&&operationFeedbackOwner.current===token&&
      (!compareAndSetDraft||compareAndSetDraft(draftAtStart,value=>value)))
    setError({draft:draftAtStart,message:exception?.message||'The appearance operation failed.'});
   return null;
  }finally{if(operationOwners.current.get(key)===token){operationOwners.current.delete(key);if(alive.current)setOperations(values=>{const next={...values};delete next[key];return next})}}
 }
 async function refresh(force=true){return catalog.refresh(api.current,{force})}
 async function editItem(item){
  if(!editorState)return null;
  const original=draft;
  return runOperation('read',async isCurrent=>{
   const receipt=await api.current('theme.read',{id:item.id});
   if(receipt?.accepted!==true||!receipt.result)throw Error(receipt?.error||'Could not read this appearance.');
   if(!isCurrent())return;
   const result=receipt.result,next={entry:result,name:result.name,css:result.css,description:result.description||'',dirty:false};
   if(!compareAndSetDraft(original,next))return;
   setDeleteTarget(null);if(editor.current)editor.current.open=true;
  });
 }
 function startPreview(){return runOperation('preview',isCurrent=>previewAppearance(previewOwner.current,api.current,{name,css},isCurrent))}
 function cancelPreview(){
  if(confirmedPreview)previewOwner.current.active=true;
  return runOperation('cancel-preview',()=>clearAppearancePreview(previewOwner.current,api.current));
 }
 function changeDraft(patch){if(!setDraft)return;setDraft({...draft,...patch,dirty:true});setDeleteTarget(null);setNotice('')}
 async function saveCopy(){
  const savedDraft=draft;
  return runOperation('save',async isCurrent=>{
   const receipt=await api.current('theme.save',{name:savedDraft.name,css:savedDraft.css,description:savedDraft.description});
   if(receipt?.accepted!==true||!receipt.result?.id)throw Error(receipt?.error||'Saving this appearance was not confirmed. Check the library before trying again.');
   catalog.invalidate();
   const adopted=compareAndSetDraft(savedDraft,{...savedDraft,entry:receipt.result,dirty:false});
   await refresh();if(isCurrent()&&adopted)setNotice('Saved to your appearance library. Select it to use the saved version.');
   return receipt.result;
  });
 }
 async function updateSaved(){
  if(editing?.source!=='saved')return null;
  const savedDraft=draft,target=appearanceMutationTarget(editing);
  return runOperation('update',async isCurrent=>{
   const receipt=await api.current('theme.update',{...target,name:savedDraft.name,css:savedDraft.css,description:savedDraft.description});
   if(receipt?.accepted!==true||!receipt.result?.id)throw Error(receipt?.error||'Updating this appearance was not confirmed. Check the library before trying again.');
   catalog.invalidate();
   const adopted=compareAndSetDraft(savedDraft,{...savedDraft,entry:receipt.result,dirty:false});
   await refresh();if(isCurrent()&&adopted)setNotice('Library entry updated. Profiles selecting this entry use its updated appearance; other preferences are unchanged.');
   return receipt.result;
  });
 }
 async function revertDraft(){
  if(!setDraft)return null;
  return runOperation('revert',async isCurrent=>{
   if(editing){
    const receipt=await api.current('theme.read',{id:editing.id});
    if(receipt?.accepted!==true||!receipt.result)throw Error(receipt?.error||'Could not restore this library entry.');
    if(isCurrent())setDraft({entry:receipt.result,name:receipt.result.name,css:receipt.result.css,description:receipt.result.description||'',dirty:false});
   }else if(isCurrent())setDraft({entry:null,name:appearance.theme?.name||'Reference',css:appearance.theme?.css||'',description:'',dirty:false});
   if(isCurrent())setDeleteTarget(null);
  });
 }
 async function remove(){
  if(!deleteTarget)return null;
  const target=appearanceMutationTarget(deleteTarget);
  return runOperation('delete',async isCurrent=>{
   const receipt=await api.current('theme.delete',target);
   if(receipt?.accepted!==true)throw Error(receipt?.error||'Deleting this appearance was not confirmed. Check the library before trying again.');
   catalog.invalidate();
   if(draft?.entry?.id===target.id&&draft?.entry?.fingerprint===target.expectedFingerprint)compareAndSetDraft(draft,{...draft,entry:null,dirty:true});
   if(isCurrent()){setDeleteTarget(null);await refresh();if(isCurrent())setNotice('Deleted from your library. Profiles selecting this entry now show Reference until another appearance is chosen.');}
  });
 }
 function exportItem(item){if(!item?.id)return null;return runOperation('export',()=>api.current('theme.export',{id:item.id}))}
 async function upload(file){
  if(!file)return null;
  return runOperation('import',async isCurrent=>{
   const receipt=await api.current('theme.import',await appearanceImportFileArgs(file));
   if(receipt?.accepted!==true||!receipt.result?.id||typeof receipt.result.name!=='string')throw Error(receipt?.error||'Importing this package was not confirmed. Check the library before trying again.');
   catalog.invalidate();await refresh();if(isCurrent())setNotice(receipt.result.name+' added to your library.');return receipt.result;
  });
 }
 const row=(field,label,help,children)=> <PreferenceRow key={field} field={field} scope={activeScope} label={label} help={help} feedback={fieldFeedback[fieldKey(activeScope,field)]} onRetry={retryPreference}>{children}</PreferenceRow>;
 const change=(field,value)=>{void commitPreference(field,value,activeScope,{target:targetForScope(activeScope)})};
 const controlDisabled=field=>profileFeedback?.status==='pending'||fieldPending(field,activeScope)||
  ['appearanceId','accent'].includes(field)&&['appearanceId','accent'].some(related=>fieldPending(related,activeScope));
 const valueFor=field=>displayedValue(field,activeScope);
 const accentPickerValue=()=>{
  const stored=valueFor('accent');
  if(typeof stored==='string'&&/^#[0-9a-f]{6}$/i.test(stored))return stored;
  const fallback='#000000';
  if(typeof document==='undefined'||typeof window==='undefined')return fallback;
  const appRoot=typeof document.getElementById==='function'?document.getElementById('amp-one'):null;
  const element=appRoot||document.documentElement;
  if(!element||typeof document.createElement!=='function'||typeof element.appendChild!=='function'||
     typeof element.removeChild!=='function'||typeof window.getComputedStyle!=='function')return fallback;
  try{
   const raw=window.getComputedStyle(element)?.getPropertyValue?.('--a-accent')?.trim();
   if(!raw)return fallback;
   const probe=document.createElement('span');
   if(!probe?.style)return fallback;
   probe.style.color='var(--a-accent)';
   probe.style.position='fixed';probe.style.visibility='hidden';probe.style.pointerEvents='none';
   let appended=false;
   try{
    element.appendChild(probe);appended=true;
    const color=window.getComputedStyle(probe)?.color?.trim()||'';
    const hex=color.match(/^#([0-9a-f]{6})$/i);
    if(hex)return `#${hex[1]}`;
    const rgb=color.match(/^rgba?\(\s*(\d+(?:\.\d+)?)\s*(?:,\s*|\s+)(\d+(?:\.\d+)?)\s*(?:,\s*|\s+)(\d+(?:\.\d+)?)(?:\s*(?:,|\/)\s*(?:\d+(?:\.\d+)?%?))?\s*\)$/i);
    if(!rgb)return fallback;
    const channels=rgb.slice(1).map(value=>Math.round(Number(value)));
    if(channels.some(channel=>!Number.isFinite(channel)||channel<0||channel>255))return fallback;
    return '#'+channels.map(channel=>channel.toString(16).padStart(2,'0')).join('');
   }finally{if(appended)element.removeChild(probe)}
  }catch{return fallback}
 };
 const renderAppearanceCards=()=> <>
  <div className="a-appearance-gallery" role="radiogroup" aria-label="Available appearances">
   {libraryItems.map(item=><label className="a-appearance-card" key={item.id} data-selected={displayCurrentId===item.id} data-appearance-card={item.id}>
    <input className="a-appearance-radio-input" type="radio" name="appearanceId" aria-label={item.name} value={item.id} checked={displayCurrentId===item.id} disabled={controlDisabled('appearanceId')} data-action="appearance.preferences.update" onChange={()=>change('appearanceId',item.id)}/>
    <AppearanceThumbnail item={item} mode={thumbnailMode} catalog={catalog} act={act}/>
    <span className="a-appearance-card-caption"><strong>{item.name}</strong></span>
   </label>)}
   {!libraryItems.length&&!libraryError&&<p role="status">Loading appearances…</p>}
  </div>
  <button type="button" className="a-soft a-appearance-reset" aria-label="Restore neutral appearance" aria-pressed={displayCurrentId==='reference'} disabled={controlDisabled('appearanceId')} data-appearance-neutral={true} data-action="appearance.preferences.update" onClick={()=>change('appearanceId','reference')}>Restore neutral appearance</button>
 </>;
 const renderPreferenceGroup=group=> <section className="a-appearance-preference-group" key={group.id} aria-labelledby={`appearance-group-${group.id}`}>
  <h2 id={`appearance-group-${group.id}`}>{group.title}</h2>
  {group.id==='visual-basics'&&<>
   {row('appearanceId','Appearance','Choose an included or saved look, or restore the neutral appearance.',renderAppearanceCards())}
   {row('scheme','Color mode','Choose the color mode for this browser or workspace.',<div className="a-appearance-modes" role="radiogroup" aria-label="Color mode">{[['system','System'],['light','Light'],['dark','Dark']].map(([value,label])=><label key={value}><input type="radio" name="appearance-scheme" value={value} checked={valueFor('scheme')===value} disabled={controlDisabled('scheme')} data-action="appearance.preferences.update" onChange={()=>change('scheme',value)}/>{label}</label>)}</div>)}
   {row('layout','Layout',null,<select aria-label="Layout" value={valueFor('layout')} disabled={controlDisabled('layout')} data-action="appearance.preferences.update" onChange={event=>change('layout',event.target.value)}><option value="balanced">Canvas on the right</option><option value="conversation">More room for conversation</option><option value="work">Canvas on the left</option></select>)}
   {row('decorations','Background decorations','Decorative artwork stays behind your content.',<label className="a-appearance-toggle"><input type="checkbox" checked={valueFor('decorations')!==false} disabled={controlDisabled('decorations')} data-action="appearance.preferences.update" onChange={event=>change('decorations',event.target.checked)}/>Show background decorations</label>)}
   {row('density','Density',null,<select aria-label="Density" value={valueFor('density')} disabled={controlDisabled('density')} data-action="appearance.preferences.update" onChange={event=>change('density',event.target.value)}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select>)}
   {row('accent','Accent color','An optional override until you choose a different appearance.',<div className="a-detail-choices"><input type="color" aria-label="Accent color" value={valueFor('accent')??accentPickerValue()} disabled={controlDisabled('accent')} data-action="appearance.preferences.update" onChange={event=>change('accent',event.target.value)}/><button type="button" className="a-soft" disabled={controlDisabled('accent')} data-action="appearance.preferences.update" onClick={()=>change('accent',null)}>Use appearance accent</button></div>)}
  </>}
  {group.id==='conversation-display'&&<>
   {row('interfaceDetail','Interface detail',null,<select aria-label="Interface detail" value={valueFor('interfaceDetail')} disabled={controlDisabled('interfaceDetail')} data-action="appearance.preferences.update" onChange={event=>change('interfaceDetail',event.target.value)}><option value="minimal">Focused</option><option value="standard">Balanced</option><option value="detailed">Everything</option></select>)}
   {row('conversationStatus','Conversation status',null,<label className="a-appearance-toggle"><input type="checkbox" checked={valueFor('conversationStatus')===true} disabled={controlDisabled('conversationStatus')} data-action="appearance.preferences.update" onChange={event=>change('conversationStatus',event.target.checked)}/>Show conversation status</label>)}
   {row('collapseInterimReplies','Interim updates','After a turn’s final answer is ready, earlier updates move into work details. Nothing is deleted.',<label className="a-appearance-toggle"><input type="checkbox" checked={valueFor('collapseInterimReplies')===true} disabled={controlDisabled('collapseInterimReplies')} data-action="appearance.preferences.update" onChange={event=>change('collapseInterimReplies',event.target.checked)}/>Collapse earlier updates</label>)}
   {row('workStartView','Starting view',null,<select aria-label="Starting view" value={valueFor('workStartView')} disabled={controlDisabled('workStartView')} data-action="appearance.preferences.update" onChange={event=>change('workStartView',event.target.value)}><option value="appearance">Follow appearance</option><option value="focus">Focus</option><option value="brief">Brief</option><option value="studio">Studio</option></select>)}
   {row('planPlacement','Plan location','Choose where the agent’s task list appears.',<select aria-label="Plan location" value={valueFor('planPlacement')} disabled={controlDisabled('planPlacement')} data-action="appearance.preferences.update" onChange={event=>change('planPlacement',event.target.value)}><option value="composer">Above composer</option><option value="inline">Inline in chat</option><option value="active">Above composer while working</option><option value="overview">Chat overview only</option></select>)}
  </>}
  {group.id==='work-visibility'&&<>
   {row('executionDetail','Work detail',null,<select aria-label="Work detail" value={valueFor('executionDetail')} disabled={controlDisabled('executionDetail')} data-action="appearance.preferences.update" onChange={event=>change('executionDetail',event.target.value)}><option value="minimal">Focused</option><option value="standard">Balanced</option><option value="detailed">Everything</option></select>)}
   {row('executionVisibility','Work visibility',null,<div className="a-detail-choices">{[['step','Each step'],['turn','After each turn'],['conversation','In the conversation']].map(([scope,label])=><label key={scope}><input type="checkbox" checked={valueFor('executionVisibility')?.[scope]!==false} disabled={controlDisabled('executionVisibility')} data-action="appearance.preferences.update" onChange={event=>change('executionVisibility',{...valueFor('executionVisibility'),[scope]:event.target.checked})}/>{label}</label>)}</div>)}
   {row('executionMetrics','Work metrics','Choose which totals to show.',<div className="a-detail-choices">{METRIC_CHOICES.map(([key,label])=><label key={key}><input type="checkbox" checked={(valueFor('executionMetrics')||[]).includes(key)} disabled={controlDisabled('executionMetrics')} data-action="appearance.preferences.update" onChange={event=>{const current=valueFor('executionMetrics')||[],next=event.target.checked?[...current,key]:current.filter(item=>item!==key);change('executionMetrics',next)}}/>{label}</label>)}</div>)}
  </>}
 </section>;

 return <div className="a-appearance-settings">
  <div className="a-appearance-intro">
   <div><p>Choose how this interface looks and works.</p></div>
   <div className="a-sidebar-modes a-appearance-profile-switch">
    <div className="a-sidebar-organizations" role="group" aria-label="Appearance profile">
     <button type="button" aria-pressed={activeScope==='shared'} disabled={profileFeedback?.status==='pending'} data-action="appearance.preferences.select" onClick={()=>selectProfile('shared')}>Global</button>
     <button type="button" aria-pressed={activeScope==='client'} disabled={profileFeedback?.status==='pending'} data-action="appearance.preferences.select" onClick={()=>selectProfile('client')}>This browser</button>
    </div>
   </div>
  </div>
  {profileFeedback&&<p className="a-appearance-profile-feedback" role={profileFeedback.status==='failed'?'alert':'status'}>
   <span>{profileFeedback.message}</span>
   {profileFeedback.status==='failed'&&profileFeedback.retryable!==false&&<button type="button" className="a-link" onClick={retryProfile}>Retry</button>}
  </p>}
  {preferenceStatus?.status==='failed'&&<p role="alert" className="a-danger">{preferenceStatus.error||'This browser could not confirm its appearance preferences. Shared settings were not changed.'}</p>}
  {appearance.theme?.available===false&&appearance.theme.id===displayCurrentId&&<p role="alert" className="a-danger" data-appearance-unavailable={appearance.theme.id}>
   The selected appearance {appearance.theme.name||appearance.theme.id}{appearance.theme.name&&` (${appearance.theme.id})`} is unavailable. Your selection and other preferences are retained. {confirmedPreview?'A preview is active; Reference will be shown when it ends.':'Reference is shown instead.'} Choose another appearance or use Restore neutral appearance below.
  </p>}
  {storageError&&<p role="alert" className="a-danger">{storageError}</p>}
  {visibleError&&<p role="alert" className="a-danger">{visibleError}</p>}{libraryError&&<p role="alert" className="a-danger">{libraryError}</p>}
  {notice&&<p role="status" className="a-appearance-notice"><Check size={15}/>{notice}</p>}
  {confirmedPreview&&<div className="a-appearance-preview" role="status"><span>Previewing {confirmedPreview.name}. Appearance preferences are unchanged.</span><button type="button" className="a-soft" aria-disabled={busyFor('cancel-preview')} data-action="appearance.preview.cancel" onClick={()=>{if(!busyFor('cancel-preview'))void cancelPreview()}}>Cancel preview</button></div>}
  <div className="a-appearance-groups">{PREFERENCE_GROUPS.map(renderPreferenceGroup)}</div>
  {<section className="a-appearance-saved"><h2>Saved appearances</h2>
   {savedItems.map(item=><div className="a-appearance-saved-row" key={item.id}>
    <div><strong>{item.name}</strong>{item.description&&<p>{item.description}</p>}</div>
    <div className="a-appearance-saved-actions">
     <button type="button" className="a-soft" aria-label={'Use '+item.name} aria-disabled={controlDisabled('appearanceId')} data-action="appearance.preferences.update" onClick={()=>{if(!controlDisabled('appearanceId'))change('appearanceId',item.id)}}>Use</button>
     <button type="button" className="a-soft" aria-label={'Edit '+item.name} aria-disabled={busyFor('read')} onClick={()=>{if(!busyFor('read'))void editItem(item)}}>Edit</button>
     <button type="button" className="a-soft" aria-disabled={busyFor('export')} data-action="theme.export" onClick={()=>{if(!busyFor('export'))void exportItem(item)}}><Download/>Export ZIP</button>
     <button type="button" className="a-link" aria-label={'Delete '+item.name} aria-disabled={busyFor('delete')} onClick={()=>{if(!busyFor('delete'))setDeleteTarget(item)}}>Delete</button>
    </div>
   </div>)}
   {!savedItems.length&&<p>No saved appearances yet.</p>}
  </section>}
  {deleteTarget&&<div role="group" aria-label="Delete appearance confirmation"><p>Delete “{deleteTarget.name}” from your library? Profiles selecting this entry will show Reference until another appearance is chosen. Other preferences are retained.</p><div className="a-dialog-actions"><button type="button" className="a-soft" onClick={()=>setDeleteTarget(null)}>Cancel deletion</button><button type="button" className="a-primary" aria-disabled={busyFor('delete')} data-action="theme.delete" onClick={()=>{if(!busyFor('delete'))void remove()}}><WorkingLabel active={busyFor('delete')} working="Deleting…">Confirm delete</WorkingLabel></button></div></div>}
  {<section className="a-appearance-import"><div><h3><Upload/>Your appearance library</h3><p>Import a version-2 ZIP package based on Reference. CSS files are not imported as standalone skins.</p></div>
   <FileDrop id="theme-import" disabled={busyFor('import')} accept=".zip,application/zip" action="theme.import" label="Upload ZIP package" workingLabel="Importing appearance…" hint="Reference-based version-2 ZIP package" onFile={upload}/>
   {library.errors.map((item,index)=><p className="a-danger" key={item.path||index}>{item.message}</p>)}
   <details className="a-appearance-folder"><summary><FolderOpen/>Appearance folder</summary><p>Packages placed here appear in the library automatically.</p><code>{library.directory}</code></details>
  </section>}
  {<details ref={editor} className="a-appearance-advanced">
   <summary>Advanced customization</summary>
   <p>Edit a Reference CSS delta and preview it locally. Drafts stay private to this browser tab. Save as copy adds an unselected library entry; updating an entry changes its appearance in profiles that select it.</p>
   {editing&&<p>Editing: <strong>{editing.name}</strong>. {editing.source==='saved'?'Updates preserve this library entry’s identity.':'Included appearances are read-only; save edits as a copy.'}</p>}
   <label htmlFor="theme-name">Appearance name</label><input id="theme-name" maxLength={100} value={name||''} disabled={mutationBusy} onChange={event=>changeDraft({name:event.target.value})}/>
   <label htmlFor="theme-description">Description</label><input id="theme-description" maxLength={240} value={description||''} disabled={mutationBusy} onChange={event=>changeDraft({description:event.target.value})}/>
   <label htmlFor="theme-css">Reference CSS delta</label><textarea id="theme-css" spellCheck={false} className="a-css-editor" value={css||''} disabled={mutationBusy} onChange={event=>changeDraft({css:event.target.value})}/>
   {busyFor('save')&&<p role="status" aria-live="polite">Saving appearance…</p>}
   <div className="a-dialog-actions">
    <button type="button" className="a-primary" disabled={mutationBusy} data-action="theme.save" onClick={()=>void saveCopy()}><WorkingLabel active={busyFor('save')} working="Saving…">Save as copy</WorkingLabel></button>
    {editing?.source==='saved'&&<button type="button" className="a-soft" disabled={mutationBusy} data-action="theme.update" onClick={()=>void updateSaved()}><WorkingLabel active={busyFor('update')} working="Updating…">Update saved appearance</WorkingLabel></button>}
    <button type="button" className="a-soft" disabled={mutationBusy} data-action="appearance.preview" onClick={()=>void startPreview()}><WorkingLabel active={busyFor('preview')} working="Preparing…">Preview draft</WorkingLabel></button>
    {confirmedPreview&&<button type="button" className="a-soft" aria-disabled={busyFor('cancel-preview')} data-action="appearance.preview.cancel" onClick={()=>{if(!busyFor('cancel-preview'))void cancelPreview()}}>Cancel preview</button>}
    <button type="button" className="a-soft" disabled={mutationBusy} onClick={()=>void revertDraft()}><WorkingLabel active={busyFor('revert')} working="Reverting…">Revert editor changes</WorkingLabel></button>
    {editing?.id&&<button type="button" className="a-soft" disabled={mutationBusy} data-action="theme.export" onClick={()=>void exportItem(editing)}><Download/>Export ZIP</button>}
   </div>
  </details>}
 </div>;
}
