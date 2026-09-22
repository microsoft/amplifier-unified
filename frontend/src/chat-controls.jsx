import {useOutsideDismiss} from './use-outside-dismiss';
import React,{useEffect,useState,useRef} from 'react';
import {ChevronDown,Pin,X,Paperclip} from 'lucide-react';
import {providerFields,modelOptions} from './setup-data';
import {newChatSetup,draftDefaults} from './new-chat';
import {useComposerPopover} from './composer-popover';
const EMPTY={};
export function AttachmentStrip({items=[],remove}){
 if(!items.length)return null;
 return <div className="a-attachments">{items.map(file=><div className="a-attachment" key={file.id}>{file.mime?.startsWith('image/')?<a href={file.url} target="_blank" rel="noopener noreferrer"><img src={file.url} alt={file.name}/></a>:<Paperclip/>}<a href={file.url} target="_blank" rel="noopener noreferrer" title={file.name}>{file.name}<small>{Math.ceil(file.size/1024)} KB</small></a>{remove&&<button type="button" className="a-icon" aria-label={'Remove '+file.name} data-action="attachment.remove" onClick={()=>remove(file.id)}><X/></button>}</div>)}</div>;
}
export function providerGroups(providers){
 const groups=new Map();
 for(const row of providers){const key=row.info?.id||row.module||row.id;
  if(!groups.has(key))groups.set(key,{id:key,label:row.info?.display_name||key,rows:[]});groups.get(key).rows.push(row);
 }
 return [...groups.values()];
}
export function ModelControl({state,session,act,working}){
 const setup=newChatSetup(state),isDraft=!session,sessionId=session?.id||null,defaults=draftDefaults(state);
 const unavailable=session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||session?.historyLoaded===false;
 const shared=state.view?.composerModel||EMPTY,[draft,setDraft]=useState(shared),[error,setError]=useState('');
 useEffect(()=>setDraft(shared),[shared]);
 const edit=patch=>{const next={...draft,...patch};setDraft(next);act('view.update',{patch:{composerModel:next}})};
 const draftCatalog=state.setup?.providersRequestedWorkspace===setup.workspace?state.setup:EMPTY;
 const cachedProviders=(draftCatalog.providers||[]).filter(row=>row.enabled!==false).map(row=>{
  const metadata=draftCatalog.providerCatalogs?.[row.id]?.metadata||draftCatalog.metadata?.[row.module]||{};
  return {...row,...metadata,info:{...metadata.info,defaults:{...metadata.info?.defaults,model:row.config?.default_model||row.config?.model||metadata.info?.defaults?.model}}};
 });
 const controls=state.runtimeControl?.[sessionId]||{},catalog=controls['configuration.providers'];
 const providers=isDraft?(defaults.providers||cachedProviders):(catalog?.providers||session?.runtimeReport?.provider_choices?.map(row=>({id:row.id,info:{defaults:{model:row.model,reasoning_effort:row.effort}}}))||[]);
 const pinned=isDraft?!!setup.selection?.model:(catalog?.pinned??!!(session?.runtimeReport?.selection||session?.selection));
 const effective=isDraft?(pinned?setup.selection:defaults.effective||{}):(catalog?.effective||session?.runtimeReport?.effective_selection||session?.selection||{});
 const lastCall=[...(session?.execution?.nodes||[])].reverse().find(n=>n.kind==='llm'&&(n.sessionId===sessionId||(!n.sessionId&&!n.parentId)));
 const model=effective.model||(!isDraft?lastCall?.model:'')||(defaults.phase==='error'?'Model unavailable':'Loading model…');
 const provider=effective.instance||effective.id||(!isDraft?lastCall?.provider:'')||'';
 const open=draft.open&&(draft.sessionId??null)===sessionId,popover=useRef(null),position=useComposerPopover(open,popover);
 useOutsideDismiss(open,popover,()=>edit({open:false}));
 const groups=providerGroups(providers),group=groups.find(g=>g.rows.some(row=>row.id===draft.instance));
 const selected=providers.find(row=>row.id===draft.instance),result=controls['configuration.providerModels'];
 const entry=isDraft?draftCatalog.providerCatalogs?.[draft.instance]:controls.modelCatalogs?.[draft.instance]||(result&&result.provider===draft.instance?{phase:'ready',models:result.models}:null);
 const lastModels=useRef(new Map());let models=modelOptions(entry?.models||[]);
 if(models.length)lastModels.current.set((isDraft?setup.workspace:sessionId)+'|'+draft.instance,models);
 else models=lastModels.current.get((isDraft?setup.workspace:sessionId)+'|'+draft.instance)||[];
 // Routing aliases contribute their configured models to one provider family.
 const available=new Map(models.map(row=>[row.id,row]));
 for(const row of group?.rows||[]){
  const sibling=isDraft?draftCatalog.providerCatalogs?.[row.id]:controls.modelCatalogs?.[row.id];
  for(const model of modelOptions(sibling?.models||[]))available.set(model.id,model);
  const model=row.info?.defaults?.model;if(model&&!available.has(model))available.set(model,{id:model,name:model});
 }
 models=[...available.values()];
 const metadata=entry?.metadata||selected||{};
 const choices=providerFields(metadata,{model:draft.model,default_model:draft.model}).find(field=>field.id==='reasoning_effort')?.choices||[];
 const request=useRef(''),touched=useRef(false);
 useEffect(()=>{
  if(!isDraft||!setup.workspace)return;
  const key=JSON.stringify([setup.workspace,setup.bundle||'']);if(request.current===key)return;
  const timer=setTimeout(()=>{request.current=key;
  // Prefetch during the draft, never on the click path and never by starting a session.
  act('configuration.defaults',{workspace:setup.workspace,bundle:setup.bundle||''});
  if(draftCatalog===EMPTY)act('providers.list',{workspace:setup.workspace});
  },250);return()=>clearTimeout(timer);
 },[isDraft,setup.workspace,setup.bundle]);
 useEffect(()=>{
  if(open&&!touched.current&&providers.length){const row=providers.find(p=>p.id===(effective.instance||effective.id))||providers[0];edit({instance:row.id,model:effective.model||row.info?.defaults?.model||'',effort:effective.effort||''})}
 },[open,sessionId,providers.map(row=>row.id).join('|'),effective.model]);
 function show(){touched.current=false;setError('');if(open){edit({open:false});return}
  edit({open:true,sessionId,instance:provider,model:effective.model||'',effort:effective.effort||''});
  if(session)act('runtime.control',{sessionId,operation:'configuration.providers',args:{}});
 }
 async function apply(patch,reset=false){
  touched.current=!reset;const next={...draft,...patch};edit(patch);setError('');
  if(!reset&&(!next.instance||!next.model))return;
  const selection={instance:next.instance,model:next.model,...(next.effort?{effort:next.effort}:{})};
  try{if(isDraft)await act('view.update',{patch:{newSessionDraft:{...setup,selection:reset?{}:selection}}});
   else await act('runtime.control',{sessionId,operation:reset?'provider.reset':'provider.select',args:reset?{}:selection});
  }catch(e){setError(e.message)}
 }
 function chooseProvider(value){
  if(!value){apply({instance:effective.instance||'',model:effective.model||'',effort:''},true);return}
  const family=groups.find(g=>g.id===value),row=family?.rows.find(p=>p.id===draft.instance)||family?.rows[0];
  value=row?.id;if(!value)return;apply({instance:value,model:row.info?.defaults?.model||'',effort:''});
  if(isDraft&&!draftCatalog.providerCatalogs?.[value])act('providers.models',{id:value,workspace:setup.workspace});
  else if(!isDraft&&!controls.modelCatalogs?.[value])act('runtime.control',{sessionId,operation:'configuration.providerModels',args:{instance:value}});
 }
 const op=state.actionStatus?.[isDraft?'configuration.defaults':'runtime.control'];
 const failure=error||(op?.phase==='error'?op.error:'')||defaults.error;
 return <div className="a-model-control" ref={popover}>
  <button type="button" className="a-model-trigger" aria-label="Model and reasoning settings" disabled={unavailable} aria-expanded={!!open} data-action="view.update" onClick={show}>{pinned&&<Pin/>}<span>{model}{(provider||effective.effort)&&<small>{[effective.effort?`${effective.effort} reasoning`:'',provider].filter(Boolean).join(' · ')}</small>}</span><ChevronDown/></button>
  {open&&<section className="a-model-popover a-compact-popover" style={position} aria-label="Conversation model">
   <div className="a-settings-row"><strong>Conversation model</strong><button type="button" className="a-icon" aria-label="Close model settings" data-action="view.update" onClick={()=>edit({open:false})}><X/></button></div>
   <label htmlFor="chat-provider">Provider</label><select id="chat-provider" value={group?.id||draft.instance||''} data-action={isDraft?'view.update':'runtime.control'} disabled={working} onChange={e=>chooseProvider(e.target.value)}>
    <option value="">Use bundle default</option>{draft.instance&&!providers.some(p=>p.id===draft.instance)&&<option value={draft.instance}>{draft.instance}</option>}{groups.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}
   </select>
   <label htmlFor="chat-model">Model</label><select id="chat-model" aria-label="Conversation model" value={draft.model||''} disabled={working||!draft.instance} data-action={isDraft?'view.update':'runtime.control'} onChange={e=>{const value=e.target.value,row=group?.rows.find(p=>p.id===draft.instance&&p.info?.defaults?.model===value)||group?.rows.find(p=>p.info?.defaults?.model===value);apply({instance:row?.id||draft.instance,model:value,effort:''})}}>
    {!draft.model&&<option value="">{entry?.phase==='working'?'Loading models…':'Choose a model'}</option>}{draft.model&&!models.some(row=>row.id===draft.model)&&<option value={draft.model}>{draft.model}</option>}{models.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
   </select>
   <label htmlFor="chat-effort">Reasoning effort</label><select id="chat-effort" value={draft.effort||''} disabled={working||!draft.model||!choices.length} data-action={isDraft?'view.update':'runtime.control'} onChange={e=>apply({effort:e.target.value})}><option value="">Provider default</option>{draft.effort&&!choices.includes(draft.effort)&&<option value={draft.effort}>{draft.effort}</option>}{choices.map(value=><option key={value} value={value}>{value}</option>)}</select>
   {failure&&<small className="a-danger" role="status">{failure}</small>}
   {!failure&&entry?.phase==='error'&&<small className="a-danger" role="status">The model list is unavailable. Your current selection is preserved.</small>}
  </section>}
 </div>;
}
export function readAttachment(file){return new Promise((resolve,reject)=>{if(file.size>8*1024*1024||!file.size){reject(new Error('Choose a nonempty file up to 8 MB.'));return}const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file)})}
