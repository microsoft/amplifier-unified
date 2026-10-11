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
 const model=draft.imageModel??config.model??catalog?.metadata?.imageGeneration?.automaticModel??'';

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
 return <section aria-label="Image generation settings">
  <h4>Image generation</h4>
  <p>Images work automatically with your available AI connections.</p>
  <label htmlFor="image-choice">Use for images</label>
  <select id="image-choice" value={mode} disabled={busy} onChange={e=>change({imageMode:e.target.value})}>
   <option value="automatic">Automatic · provider priority</option><option value="selected">Choose a connection</option><option value="off">Off</option>
  </select>
  {mode==='automatic'&&<p role="status">{available.length?`Currently would use ${labels.get(available[0].id)||available[0].id}.` :checking?'Checking available connections…':'No image connection is available yet.'} Uses the latest supported model, following your connection priority.</p>}
  {mode==='selected'&&<>
   <label htmlFor="image-provider">Image provider</label>
   <select id="image-provider" value={selected?.id||''} disabled={busy||!available.length} onChange={e=>change({imageProviderId:e.target.value,imageModel:null})}>
    <option value="" disabled>{checking?'Checking available connections…':'Choose an available connection'}</option>
    {available.map(p=><option key={p.id} value={p.id}>{labels.get(p.id)||p.id}</option>)}
   </select>
   <p className="a-caption">Only connected services that support image generation are listed.</p>
  </>}
  {discoveryError&&<p className="a-danger" role="alert">{discoveryError}</p>}
  {mode!=='off'&&!checking&&!available.length&&<p>Connect a service that supports images, or check its credentials.</p>}
  <p className="a-caption">Creating an image may use your provider’s paid allowance.</p>
  <button type="button" className="a-primary" disabled={busy||(mode==='selected'&&(!selected||!model))} onClick={()=>run('providers.configureImages',mode==='selected'?{id:selected.id,enabled:true,exclusive:true,model,scope:draft.scope||'global'}:{mode,scope:draft.scope||'global'})}>{pending?.action==='providers.configureImages'?'Saving…':'Save image settings'}</button>
  <details className="a-everyday-disclosure"><summary>Advanced preferences</summary><div>
   {mode==='selected'&&selected&&<><label htmlFor="ai-image-model">Image model</label><select id="ai-image-model" value={model} disabled={busy} onChange={e=>change({imageModel:e.target.value})}>
    {catalog.metadata?.imageGeneration?.automaticModel&&<option value={catalog.metadata.imageGeneration.automaticModel}>Automatic · latest supported</option>}
    {catalog.models.map(row=><option key={row.id} value={row.id}>{row.display_name||row.id}</option>)}
   </select></>}
   <button type="button" className="a-link" disabled={busy||checking} onClick={()=>discover(true)}><RefreshCw/>Refresh image connections</button>
   <label htmlFor="image-scope">Save for</label><select id="image-scope" value={draft.scope||'global'} disabled={busy} onChange={e=>change({scope:e.target.value})}>
    <option value="global">All workspaces</option><option value="local">This workspace on this host</option><option value="project">This project (shareable configuration)</option>
   </select>
   <button type="button" className="a-link" onClick={()=>navigate('providers')}>Advanced connection settings</button>
  </div></details>
 </section>;
}
