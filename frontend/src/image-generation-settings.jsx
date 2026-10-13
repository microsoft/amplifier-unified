import {ConfigurationScopeSelect,useConfigurationScope,useConfigurationId} from './configuration-scope';
import React,{useEffect,useRef,useState} from 'react';
import {RefreshCw} from 'lucide-react';

import {aiServices,setupPending} from './ai-connections-data';

// Capability and catalog evidence belongs to the installed connection, not a
// hard-coded list of brands. Image understanding is not image generation.
export function availableImageConnections(providers,catalogs){
 return [...providers].sort((a,b)=>(a.config?.priority??100)-(b.config?.priority??100)).filter(p=>{
  const c=catalogs?.[p.id],info=c?.metadata?.imageGeneration;
  const auth=aiServices.find(service=>service.module===p.module)?.auth;
  const connected=!auth||auth==='optional-key'||p.credentialsConfigured||p.accountConnected;
  return connected&&p.enabled!==false&&!p.authenticationRequired&&c?.supported===true&&
   info?.schemaVersion===1&&info.configKey==='image_generation'&&c.models?.length>0;
 });
}

export function ImageGenerationSettings({state,session,providers,labels,draft,edit,act,run,pending,busy,navigate}){
 const setup=state.setup||{},catalogs=setup.imageCatalogs||{},requested=useRef(new Set()),[discoveryError,setDiscoveryError]=useState('');
 const available=availableImageConnections(providers,catalogs);
 const current=providers.filter(p=>p.config?.image_generation?.enabled===true);
 const mode=draft.imageMode??setup.imagePreference??(current.length?'selected':'automatic');
 const requestedId=draft.imageProviderId??current[0]?.id??available[0]?.id??'';
 const selected=available.find(p=>p.id===requestedId);
 const catalog=selected?catalogs[selected.id]:null,config=selected?.config?.image_generation||{};
 const model=draft.imageModel??config.model??catalog?.metadata?.imageGeneration?.automaticModel??catalog?.models?.[0]?.id??'';

 const configId=useConfigurationId();
 const scope=useConfigurationScope(draft.scope);
 const checking=providers.some(p=>setupPending(setup.operations?.['providers.imageModels:'+p.id]));
 const discover=async(refresh=false)=>{
  setDiscoveryError('');
  await Promise.all(providers.filter(p=>!p.authenticationRequired).map(async p=>{
   if(!refresh&&requested.current.has(p.id))return;
   requested.current.add(p.id);
   try{
    const receipt=await act('providers.imageModels',{id:p.id,refresh,...(session?{sessionId:session.id}:{})});
    if(!receipt?.accepted)throw Error('Could not check all image connections. Refresh to try again.');
   }catch(e){setDiscoveryError(e.message)}
  }));
 };
 useEffect(()=>{discover()},[providers.map(p=>p.id).join('\n')]);
 const change=patch=>edit({...patch,imageEdited:true});
 const choice=mode==='selected'?'provider:'+requestedId:mode;
 const persist=(patch={})=>{
  const next={mode,id:requestedId,model,scope,...patch};
  run('providers.configureImages',next.mode==='selected'?{id:next.id,enabled:true,exclusive:true,model:next.model,scope:next.scope}:{mode:next.mode,scope:next.scope});
 };
 const choose=value=>{
  if(busy)return;
  const provider=available.find(p=>'provider:'+p.id===value);
  const imageMode=provider?'selected':value;
  const imageModel=provider?(provider.config?.image_generation?.model||catalogs[provider.id]?.metadata?.imageGeneration?.automaticModel||catalogs[provider.id]?.models?.[0]?.id||''):null;
  // Save to the owner of this surface; model overrides remain in Advanced.
  change({imageMode,imageProviderId:provider?.id||null,imageModel,scope});
  run('providers.configureImages',provider?{id:provider.id,enabled:true,exclusive:true,model:imageModel,scope}:{mode:imageMode,scope});
 };
 useEffect(()=>{
  if(!draft.imageFocus&&draft.step!=='images')return;
  const frame=requestAnimationFrame(()=>document.getElementById(configId('image-choice'))?.focus());
  return()=>cancelAnimationFrame(frame);
 },[draft.imageFocus,draft.step]);
 return <section aria-label="Image generation settings">
  <h4>Image generation</h4>
  <label htmlFor={configId('image-choice')}>Use for images</label>
  <select id={configId('image-choice')} value={choice} disabled={busy} onChange={e=>choose(e.target.value)}>
   <option value="automatic">Automatic · provider priority</option>
   {mode==='selected'&&!selected&&<option value={choice} disabled>{labels.get(requestedId)||requestedId||'Saved connection'} · unavailable</option>}
   {available.map(p=><option key={p.id} value={'provider:'+p.id}>{labels.get(p.id)||p.id}</option>)}
   <option value="off">Off</option>
  </select>
  {mode==='automatic'&&<p className="a-caption" role="status">{available.length?`Currently would use ${labels.get(available[0].id)||available[0].id}.` :checking?'Checking available connections…':'No image connection is available yet.'} Uses the latest supported model, following your connection priority.</p>}
  {mode==='selected'&&<p className="a-caption">Only connected services that support image generation are listed.</p>}
  {discoveryError&&<p className="a-danger" role="alert">{discoveryError}</p>}
  {mode!=='off'&&!checking&&!available.length&&<p>Connect a service that supports images, or check its credentials.</p>}
  <p className="a-caption">Creating an image may use your provider’s paid allowance.</p>
  {draft.imageEdited&&!busy&&<button type="button" className="a-primary" disabled={busy||(mode==='selected'&&(!selected||!model))} onClick={()=>run('providers.configureImages',mode==='selected'?{id:selected.id,enabled:true,exclusive:true,model,scope:scope}:{mode,scope:scope})}>Retry image settings</button>}
  <details className="a-everyday-disclosure"><summary>Advanced preferences</summary><div>
   {mode==='selected'&&selected&&<><label htmlFor={configId('ai-image-model')}>Image model</label><select id={configId('ai-image-model')} value={model} disabled={busy} onChange={e=>{change({imageModel:e.target.value});persist({model:e.target.value})}}>
    {catalog.metadata?.imageGeneration?.automaticModel&&<option value={catalog.metadata.imageGeneration.automaticModel}>Automatic · latest supported</option>}
    {catalog.models.map(row=><option key={row.id} value={row.id}>{row.display_name||row.id}</option>)}
   </select></>}
   <button type="button" className="a-link" disabled={busy||checking} onClick={()=>discover(true)}><RefreshCw/>Refresh image connections</button>
   <ConfigurationScopeSelect id={configId('image-scope')} value={scope} disabled={busy} onChange={scope=>{change({scope});persist({scope})}}/>
   <button type="button" className="a-link" onClick={()=>navigate('providers')}>Advanced connection settings</button>
  </div></details>
 </section>;
}
