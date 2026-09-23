import React,{useEffect,useRef,useState} from 'react';
import {Plus,ArrowLeft,RefreshCw,Sparkles,ExternalLink} from 'lucide-react';
import {SettingsLink} from './settings-everyday';
import {SettingsActions} from './settings-layout';
import {ResultNotice} from './settings-ui';
import {ModelSelect} from './model-select';
import {modelOptions,safeLoginUrl} from './setup-data';
import {aiServices,aiService,matchingSetupOperation,setupPending} from './ai-connections-data';

export function AIConnections({state,session,act,navigate}){
 const setup=state.setup||{},providers=(setup.providers||[]).filter(p=>p.enabled!==false);
 const d=state.view?.aiConnectionEditor||{},step=d.step||'list',service=aiService(d.module),selected=providers.find(p=>p.id===d.id);
 const [key,setKey]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[pending,setPending]=useState(null);
 const sending=useRef(false),context=useRef(session?.workspace),requestVersion=useRef(0),alive=useRef(true);
 const edit=patch=>act('view.update',{patch:{aiConnectionEditor:{...d,...patch}}});
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;requestVersion.current++}},[]);
 useEffect(()=>{if(context.current!==session?.workspace){requestVersion.current++;context.current=session?.workspace;setKey('');setPending(null);sending.current=false;edit({step:'list',id:'',model:''});}act('providers.list',session?{sessionId:session.id}:{});act('routing.list');},[session?.workspace]);
 async function run(action,args={},next=''){
  if(sending.current)return;
  sending.current=true;const version=++requestVersion.current;setError('');setNotice('');setPending({action,id:args.id||'',commandId:null,next});
  try{
   const receipt=await act(action,{...(session?{sessionId:session.id}:{}),...args});
   if(!alive.current||version!==requestVersion.current)return;
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'The request could not be confirmed. Please try again.');
   setPending({action,id:args.id||'',commandId:receipt.operationId,next});
  }catch(e){if(alive.current&&version===requestVersion.current){setError(e.message);setPending(null);sending.current=false;}}
 }
 const operation=matchingSetupOperation(state,pending),busy=!!pending;
 useEffect(()=>{
  if(!operation||setupPending(operation))return;
  const next=pending.next,action=pending.action;setPending(null);sending.current=false;
  if(operation.phase==='error'){setError(operation.error||'The request did not finish. Your choices are kept.');return;}
  if(operation.phase!=='ready')return;
  if(action==='providers.save'){
   setKey('');edit({saved:true,...(next==='models'?{step:'model'}:{})});
   if(next==='signin')run('providers.login',{id:d.id});
   else if(next==='models')run('providers.models',{id:d.id});
  }else if(action==='providers.finishSetup'){
   edit({step:'list',saved:false});setNotice(setup.setupCompletion?.routingCreated?'Connection and model saved for new conversations.':'Model saved. Existing model rules are unchanged.');
  }else if(action==='providers.models'){setNotice('Model list checked. No chat request was sent.');}
 },[operation?.commandId,operation?.phase]);
 const login=setup.login?.providerId===d.id?setup.login:null,loginUrl=safeLoginUrl(login?.url);
 const catalog=setup.providerCatalogs?.[d.id],models=modelOptions(catalog?.models||setup.modelCatalogs?.[d.id]||[]);
 const modelBusy=setupPending(setup.operations?.['providers.models:'+d.id])||catalog?.phase==='working';
 const choose=s=>{if(sending.current)return;setKey('');setError('');setNotice('');edit({step:'connect',module:s.module,id:s.module.replace('provider-','')+'-'+crypto.randomUUID().slice(0,8),model:'',scope:'global',saved:false,initializeRouting:providers.length===0});};
 const saveConnection=()=>run('providers.save',{id:d.id,module:d.module,config:selected?.config||{},scope:d.scope||'global',...(service.auth==='signin'?{}:{apiKey:key})},service.auth==='signin'?'signin':'models');
 const back=()=>{setError('');edit({step:step==='services'||step==='detail'?'list':step==='model'?'detail':'services'});};
 const advanced=()=>navigate('providers');
 return <section data-part="ai-connections" className="a-ai-form">
  {step==='list'?<>
   <p className="a-everyday-intro">Connect the AI services Amplifier can use for your work.</p>
   {!providers.length?<div className="a-ai-welcome"><Sparkles aria-hidden="true"/><h4>Start with one connection</h4><p>Choose a service, connect your account, and choose a model. You can add more later.</p><button className="a-primary" data-action="view.update" disabled={busy} onClick={()=>edit({step:'services'})}><Plus/>Connect AI</button></div>:<><div className="a-everyday-list">{providers.map(p=><SettingsLink key={p.id} disabled={busy} title={aiService(p.module).name+(providers.filter(row=>row.module===p.module).length>1?' · '+p.id:'')} description={(p.config?.default_model||p.config?.model||'Choose a model')+' · '+(p.credentialsConfigured?'Credentials saved':p.keySource==='provider-managed'?'Account sign-in':'Connection needs setup')} onClick={()=>{setError('');setNotice('');edit({step:'detail',id:p.id,module:p.module,model:p.config?.default_model||p.config?.model||'',scope:'global',saved:true,initializeRouting:false})}}/>)}</div><button className="a-soft" data-action="view.update" disabled={busy} onClick={()=>edit({step:'services'})}><Plus/>Connect another service</button></>}
   {setup.active&&providers.length>0&&<div className="a-ai-hint">Your model rules are saved. <button className="a-link" data-action="view.update" onClick={()=>navigate('routing')}>Advanced model rules</button></div>}
   <div className="a-everyday-footer"><p className="a-caption">Connections are stored on the Amplifier host. Existing conversations keep their settings.</p><button className="a-link" data-action="view.update" onClick={advanced}>Advanced connection settings<ExternalLink/></button></div>
  </>:<>
   <button className="a-link" data-action="view.update" disabled={busy} onClick={back}><ArrowLeft/>Back</button>
   {step==='services'?<><h4>Which service would you like to use?</h4><div className="a-everyday-list">{aiServices.map(s=><SettingsLink key={s.module} disabled={busy} title={s.name} description={s.description} onClick={()=>choose(s)}/>)}</div><button className="a-link" data-action="view.update" onClick={advanced}>Other provider or custom endpoint</button></>:<>
    <p className="a-ai-step">{step==='connect'?'1 · Connect':step==='model'?'2 · Choose a model':'Saved connection'}</p><h4>{service.name}</h4>
    {step==='detail'?<><p>{selected?.credentialsConfigured?'Credentials saved on this Amplifier host.':'Review your account connection before choosing a model.'}</p><p>Model: <strong>{selected?.config?.default_model||selected?.config?.model||'Not selected'}</strong></p><div className="a-dialog-actions"><button className="a-primary" data-action="providers.models" disabled={busy} onClick={()=>{edit({step:'model'});run('providers.models',{id:d.id})}}>Choose model</button><button className="a-soft" data-action="view.update" disabled={busy} onClick={()=>edit({step:'connect'})}>{service.auth==='signin'?'Sign in':'Update key'}</button><button className="a-soft" data-action="providers.test" disabled={busy} onClick={()=>run('providers.test',{id:d.id})}>Check connection</button></div><p className="a-caption">Checking verifies the model catalog, without sending a chat request.</p><button className="a-link" data-action="view.update" onClick={advanced}>Full connection settings</button></>:step==='connect'?<>
     {service.auth==='signin'?<p>Sign in on your provider’s page, then return here to choose a model.</p>:<><label htmlFor="ai-private-key">{service.auth==='token'?'GitHub token':'API key'}</label><input id="ai-private-key" type="password" autoComplete="off" spellCheck={false} value={key} onChange={e=>setKey(e.target.value)} disabled={busy}/><p className="a-caption">Stored privately on the Amplifier host. Excluded from shared configuration exports.</p></>}
     <SettingsActions><button className="a-primary" data-action="providers.save" disabled={busy||(service.auth!=='signin'&&!key.trim())} onClick={saveConnection}>{busy?'Connecting…':service.auth==='signin'?'Continue to ChatGPT':'Save and check connection'}</button></SettingsActions>
     {login&&<div className="a-ai-hint" role="status"><strong>Sign-in: {login.status||login.phase}</strong>{(login.instructions||[]).map((text,i)=><p key={i}>{text}</p>)}{loginUrl&&<a className="a-link" href={loginUrl} target="_blank" rel="noopener noreferrer">Open secure sign-in<ExternalLink/></a>}{login.error&&<p role="alert">{login.error}</p>}<div className="a-dialog-actions"><button className="a-soft" disabled={busy} data-action="providers.loginStatus" onClick={()=>run('providers.loginStatus',{id:d.id})}>Check sign-in</button>{login.status==='completed'&&<button className="a-primary" disabled={busy} data-action="providers.models" onClick={()=>{edit({step:'model'});run('providers.models',{id:d.id})}}>Choose model</button>}{['waiting','starting'].includes(login.status)&&<button className="a-soft" data-action="providers.loginCancel" disabled={busy} onClick={()=>run('providers.loginCancel',{id:d.id})}>Cancel sign-in</button>}</div></div>}
    </>:step==='model'?<>
     <p>Choose from the models available through this connection.</p>
     {models.length>0?<><label htmlFor="ai-model">Model</label><ModelSelect id="ai-model" label="Model" value={d.model||''} catalogKey={d.id} entry={{...catalog,models,phase:'ready'}} state={state} act={act} onChange={model=>edit({model})}/></>:<p role="status">{modelBusy?'Loading models…':'No model list is available yet. Retry or enter a model ID under More options.'}</p>}
     {modelBusy&&models.length>0&&<p className="a-caption" role="status">Refreshing in the background. Saved models remain available.</p>}
     {catalog?.phase==='error'&&<ResultNotice phase="error" message={catalog.error||'Could not refresh the model list. Saved choices are kept.'}/>}
     <button className="a-link" data-action="providers.models" disabled={busy||modelBusy} onClick={()=>run('providers.models',{id:d.id,refresh:true})}><RefreshCw/>Refresh models</button>
     <div className="a-ai-hint">{d.initializeRouting?'For a new setup, this model will be used for general and quick work. Any existing custom rules are preserved.':'This changes the connection’s default model. Existing model rules may choose a different model.'}</div>
     <SettingsActions><button className="a-primary" disabled={busy||!d.model?.trim()} data-action="providers.finishSetup" onClick={()=>run('providers.finishSetup',{id:d.id,model:d.model,scope:d.scope||'global',initializeRouting:!!d.initializeRouting})}>{busy?'Saving…':'Finish setup'}</button></SettingsActions>
    </>:null}
    <details className="a-everyday-disclosure"><summary>More options</summary><div><label htmlFor="ai-scope">Save for</label><select id="ai-scope" value={d.scope||'global'} disabled={busy} data-action="view.update" onChange={e=>edit({scope:e.target.value})}><option value="global">All workspaces</option><option value="local">This workspace on this host</option><option value="project">This project (shareable configuration)</option></select>{step==='model'&&<><label htmlFor="ai-model-manual">Model ID</label><input id="ai-model-manual" value={d.model||''} disabled={busy} data-action="view.update" onChange={e=>edit({model:e.target.value})}/></>}<button className="a-link" data-action="view.update" onClick={advanced}>Full provider configuration</button></div></details>
   </>}
  </>}
  {error&&<ResultNotice phase="error" message={error}/>}
  {notice&&<ResultNotice phase="ready" message={notice}/>}
  {busy&&<ResultNotice phase="working" message="Working on this connection…"/>}
  {!busy&&setup.test?.providerId===d.id&&step==='detail'&&<ResultNotice phase={setup.test.reachable===false?'error':'ready'} message={setup.test.reachable===false?'Connection check failed.':'Model catalog check passed.'}/>}
 </section>;
}
