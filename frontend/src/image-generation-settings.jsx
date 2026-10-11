import React,{useEffect,useRef,useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {ImageConnectionForm} from './image-connection-form';
import {aiServices,setupPending} from './ai-connections-data';

// Capability and catalog evidence belongs to the installed connection, not a
// hard-coded list of brands. Image understanding is not image generation.
export function availableImageConnections(providers,catalogs){
 return providers.filter(p=>{
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
 const requestedId=draft.imageProviderId??current[0]?.id??available[0]?.id??'';
 const selected=available.find(p=>p.id===requestedId);
 const catalog=selected?catalogs[selected.id]:null,config=selected?.config?.image_generation||{};
 const model=draft.imageModel??config.model??catalog?.metadata?.imageGeneration?.automaticModel??'';
 const enabled=draft.imagesEnabled??config.enabled??true;
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
  <p>Choose one connection for images. You can use any of your connections for chat.</p>
  {current.length>0&&<p className="a-caption">Currently using {current.map(p=>labels.get(p.id)||p.id).join(', ')}.</p>}
  <label htmlFor="image-provider">Image provider</label>
  <select id="image-provider" value={selected?.id||''} disabled={busy||!available.length} onChange={e=>change({imageProviderId:e.target.value,imageModel:null,imagesEnabled:true})}>
   <option value="" disabled>{checking?'Checking available connections…':'Choose an available connection'}</option>
   {available.map(p=><option key={p.id} value={p.id}>{labels.get(p.id)||p.id}</option>)}
  </select>
  <p className="a-caption">Only connected providers with supported image models are listed.</p>
  {checking&&<p role="status">Checking image connections…</p>}
  {discoveryError&&<p className="a-danger" role="alert">{discoveryError}</p>}
  {!checking&&!available.length&&<p role="status">No image connection is available. Connect a service that supports image generation, or check its credentials and refresh.</p>}
  {!checking&&current.length>0&&!available.some(p=>p.id===current[0].id)&&<p role="status">The saved image connection is unavailable. Choose an available connection to replace it.</p>}
  <button type="button" className="a-link" disabled={busy||checking} onClick={()=>discover(true)}><RefreshCw/>Refresh image connections</button>
  {selected&&<ImageConnectionForm catalog={catalog} model={model} enabled={enabled} busy={busy} saving={pending?.action==='providers.configureImages'} loading={false}
   hasConfiguration={!!Object.keys(config).length} hideHeading hideRefresh onChange={change} onRefresh={()=>discover(true)}
   onSave={()=>run('providers.configureImages',{id:selected.id,enabled,exclusive:true,model,scope:draft.scope||'global'})}/>}
  {current.length>0&&!selected&&<button type="button" className="a-soft" disabled={busy} onClick={()=>run('providers.configureImages',{id:current[0].id,enabled:false,exclusive:true,scope:draft.scope||'global'})}>Turn off image generation</button>}
  <details className="a-everyday-disclosure"><summary>More options</summary><div>
   <label htmlFor="image-scope">Save for</label><select id="image-scope" value={draft.scope||'global'} disabled={busy} onChange={e=>change({scope:e.target.value})}>
    <option value="global">All workspaces</option><option value="local">This workspace on this host</option><option value="project">This project (shareable configuration)</option>
   </select>
   <button type="button" className="a-link" onClick={()=>navigate('providers')}>Advanced connection settings</button>
  </div></details>
 </section>;
}
