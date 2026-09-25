import {ProviderMessageTest} from './provider-message-test';
import {ProviderKeyPreview} from './provider-key-preview';
import {DeviceSignIn} from './device-sign-in';
import React,{useEffect,useRef,useState,useContext} from 'react';
import {Plus,ArrowLeft,RefreshCw,Sparkles,ExternalLink,Trash2} from 'lucide-react';
import {SettingsLink} from './settings-everyday';
import {SettingsActions,SettingsLayoutContext} from './settings-layout';
import {useSettingsDraft} from './settings-drafts';
import {ResultNotice} from './settings-ui';
import {ModelSelect} from './model-select';
import {modelOptions} from './setup-data';
import {aiServices,aiService,matchingSetupOperation,setupPending} from './ai-connections-data';

export function AIConnections({state,session,act,navigate}){
 const layout=useContext(SettingsLayoutContext);
 const setup=state.setup||{},providers=(setup.providers||[]).filter(p=>p.enabled!==false);
 const d=state.view?.aiConnectionEditor||{},step=d.step||'list',service=aiService(d.module),selected=providers.find(p=>p.id===d.id);
 const [key,setKey]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[pending,setPending]=useState(null);
 const sending=useRef(false),context=useRef(session?.workspace),requestVersion=useRef(0),alive=useRef(true);
 const edit=patch=>act('view.update',{patch:{aiConnectionEditor:{...d,...patch}}});
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;requestVersion.current++}},[]);
 useEffect(()=>{if(context.current!==session?.workspace){requestVersion.current++;context.current=session?.workspace;setKey('');setPending(null);sending.current=false;edit({step:'list',id:'',model:''});}act('providers.list',session?{sessionId:session.id}:{});act('routing.list');},[session?.workspace]);
 async function run(action,args={},next=''){
  if(sending.current)return;
  sending.current=true;const version=++requestVersion.current;setError('');setNotice('');setPending({action,id:args.id||args.module||'',commandId:null,next});
  try{
   const receipt=await act(action,{...(session&&!['providers.loginCancel','providers.loginStatus'].includes(action)?{sessionId:session.id}:{}),...args});
   if(!alive.current||version!==requestVersion.current)return;
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'The request could not be confirmed. Please try again.');
   setPending({action,id:args.id||args.module||'',commandId:receipt.operationId,next});
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
  }else if(action==='providers.remove'){
   setKey('');edit({step:'list',id:'',saved:false});setNotice('Connection removed. Existing conversations keep their configuration.');
  }else if(action==='providers.finishSetup'){
   edit({step:'list',saved:false});setNotice(setup.setupCompletion?.routingSelected?'Connection saved. Balanced model rules are ready for new conversations.':'Model saved. Existing model rules are unchanged.');
  }else if(action==='providers.models'){setNotice('Model list checked. No chat request was sent.');}
 },[operation?.commandId,operation?.phase]);
 const login=setup.login?.providerId===d.id?setup.login:null;
 useEffect(()=>{
  if(layout.active&&step==='connect'&&d.saved&&login?.status==='completed'&&login.loginId!==d.previousLoginId&&!busy){
   edit({step:'model'});run('providers.models',{id:d.id});
  }
 },[step,d.saved,login?.status,login?.loginId,busy,layout.active]);
 const catalog=setup.providerCatalogs?.[d.id],models=modelOptions(catalog?.models||setup.modelCatalogs?.[d.id]||[]);
 const modelBusy=setupPending(setup.operations?.['providers.models:'+d.id])||catalog?.phase==='working';
 const credential=setup.credentialCheck?.module===d.module?setup.credentialCheck:null;
 const credentialMode=d.credentialMode==='auto'?(credential?.githubCliAvailable?'github-cli':credential?.available?'environment':'private'):(d.credentialMode||'private');
 const scanCredentials=module=>{if(aiService(module).auth!=='signin')run('providers.credentials',{module});};
 const choose=s=>{if(sending.current)return;setKey('');setError('');setNotice('');edit({step:'connect',module:s.module,id:s.module.replace('provider-','')+'-'+crypto.randomUUID().slice(0,8),model:'',baseUrl:'',previousLoginId:null,scope:'global',saved:false,credentialMode:'auto',initializeRouting:providers.length===0});scanCredentials(s.module);};
 const saveConnection=()=>run('providers.save',{id:d.id,module:d.module,config:{...selected?.config,...(service.auth==='optional-key'?{base_url:d.baseUrl?.trim()}: {})},scope:d.scope||'global',...(service.auth==='signin'?{}:credentialMode==='environment'?{apiKeyEnv:credential?.envVar}:credentialMode==='github-cli'?{useGitHubCli:true}:{apiKey:key})},service.auth==='signin'?'signin':'models');
 const back=()=>{setError('');edit({step:step==='services'||step==='detail'?'list':step==='model'?'detail':'services'});};
 const advanced=()=>navigate('providers');
 const unsaved=!!key||(step==='connect'&&!!d.baseUrl&&d.baseUrl!==(selected?.config?.base_url||''))||(step==='model'&&!!d.model&&d.model!==(selected?.config?.default_model||selected?.config?.model||''));
 useSettingsDraft('ai-connections',{label:'AI connection setup',dirty:unsaved,
  review:()=>{navigate('ai-connections');edit({step})},
  discard:()=>{setKey('');setError('');edit({step:'list',id:'',module:'',model:'',baseUrl:'',saved:false})}});
 return <section data-part="ai-connections" className="a-ai-form">
  {step==='list'?<>
   <p className="a-everyday-intro">Connect the AI services Amplifier can use for your work.</p>
   {!providers.length?<div className="a-ai-welcome"><Sparkles aria-hidden="true"/><h4>Start with one connection</h4><p>Choose a service, connect your account, and choose a model. You can add more later.</p><button className="a-primary" data-action="view.update" disabled={busy} onClick={()=>edit({step:'services'})}><Plus/>Connect AI</button></div>:<><div className="a-everyday-list">{providers.map(p=><SettingsLink key={p.id} disabled={busy} title={aiService(p.module).name+(providers.filter(row=>row.module===p.module).length>1?' · '+p.id:'')} description={(p.config?.default_model||p.config?.model||'Choose a model')+' · '+(p.accountConnected?'Account connected':p.credentialsConfigured?'Credentials available':p.keySource==='provider-managed'?'Account sign-in':'Connection needs setup')+(p.credential?.preview?.masked?' · Key '+p.credential.preview.masked:'')} onClick={()=>{setError('');setNotice('');edit({step:'detail',id:p.id,module:p.module,model:p.config?.default_model||p.config?.model||'',scope:'global',baseUrl:p.config?.base_url||'',previousLoginId:setup.login?.loginId,saved:true,initializeRouting:false})}}/>)}</div><button className="a-soft" data-action="view.update" disabled={busy} onClick={()=>edit({step:'services'})}><Plus/>Connect another service</button></>}
   {setup.active&&providers.length>0&&<div className="a-ai-hint">Your model rules are saved. <button className="a-link" data-action="view.update" onClick={()=>navigate('routing')}>Advanced model rules</button></div>}
   <div className="a-everyday-footer"><p className="a-caption">Connections are stored on the Amplifier host. Existing conversations keep their settings.</p><button className="a-link" data-action="view.update" onClick={advanced}>Advanced connection settings<ExternalLink/></button></div>
  </>:<>
   <button className="a-link" data-action="view.update" disabled={busy} onClick={back}><ArrowLeft/>Back</button>
   {step==='services'?<><h4>Which service would you like to use?</h4><div className="a-everyday-list">{aiServices.map(s=><SettingsLink key={s.module} disabled={busy} title={s.name} description={s.description} onClick={()=>choose(s)}/>)}</div><button className="a-link" data-action="view.update" onClick={advanced}>Other provider or custom endpoint</button></>:<>
    <p className="a-ai-step">{step==='connect'?'1 · Connect':step==='model'?'2 · Choose a model':'Saved connection'}</p><h4>{service.name}</h4>
    {step==='detail'?<><p>{selected?.accountConnected?'Your ChatGPT account is connected.':selected?.credentialsConfigured?'This connection has credentials available on the Amplifier host.':'This connection needs credentials before it can be used.'}</p><p>Model: <strong>{selected?.config?.default_model||selected?.config?.model||'Not selected'}</strong></p><ProviderKeyPreview credential={selected?.credential}/><button className="a-link" data-action="providers.list" disabled={busy} onClick={()=>run('providers.list')}><RefreshCw/>Refresh credentials</button><div className="a-dialog-actions"><button className="a-primary" data-action="providers.models" disabled={busy} onClick={()=>{edit({step:'model'});run('providers.models',{id:d.id})}}>Choose model</button><button className="a-soft" data-action="view.update" disabled={busy} onClick={()=>{edit({step:'connect',previousLoginId:login?.loginId,credentialMode:'private'});scanCredentials(d.module)}}>{service.auth==='signin'?(selected?.accountConnected?'Reconnect account':'Sign in'):'Change credentials'}</button><button className="a-soft" data-action="providers.test" disabled={busy} onClick={()=>run('providers.test',{id:d.id})}>Check connection</button></div><p className="a-caption">Checking verifies the model catalog, without sending a chat request.</p><ProviderMessageTest key={d.id} state={state} id={d.id} sessionId={session?.id} act={act} disabled={busy}/><div className="a-dialog-actions a-connection-secondary-actions"><button className="a-soft a-danger" data-action="providers.remove" disabled={busy} onClick={()=>run('providers.remove',{id:d.id,scope:d.scope||'global'})}><Trash2/>Remove connection</button><button className="a-link" data-action="view.update" onClick={advanced}>Full connection settings</button></div></>:step==='connect'?<>
     {service.auth==='signin'?<>
      {login&&login.loginId!==d.previousLoginId?<DeviceSignIn login={login} busy={busy} onCancel={()=>run('providers.loginCancel',{id:d.id})} onRetry={()=>run('providers.login',{id:d.id})}/>:<><p>Connect your ChatGPT subscription with a one-time sign-in code.</p><SettingsActions><button className="a-primary" data-action="providers.save" disabled={busy} onClick={saveConnection}>{busy?'Getting ready…':'Get sign-in code'}</button></SettingsActions></>}
     </>:<>
      {service.auth==='optional-key'&&<><label htmlFor="ai-base-url">API base URL</label><input id="ai-base-url" type="url" placeholder="http://localhost:1234/v1" value={d.baseUrl||''} disabled={busy} onChange={e=>edit({baseUrl:e.target.value})}/><p className="a-caption">Use an address reachable from the computer running Amplifier. Include /v1 if your service requires it.</p></>}
      {(credential?.available||credential?.githubCliAvailable)&&<fieldset className="a-credential-options"><legend>How would you like to connect?</legend>
       {credential.available&&<label><input type="radio" name="ai-credential" checked={credentialMode==='environment'} onChange={()=>{setKey('');edit({credentialMode:'environment'})}}/><span><strong>Use the key already on this host</strong><small>Found {credential.envVar}. Only its name is saved; the key stays in the environment.</small></span></label>}
       {credential.githubCliAvailable&&<label><input type="radio" name="ai-credential" checked={credentialMode==='github-cli'} onChange={()=>{setKey('');edit({credentialMode:'github-cli'})}}/><span><strong>Use GitHub CLI sign-in</strong><small>Use the host’s signed-in GitHub account. Its token is saved privately for this connection.</small></span></label>}
       <label><input type="radio" name="ai-credential" checked={credentialMode==='private'} onChange={()=>edit({credentialMode:'private'})}/><span><strong>Use a different key</strong><small>Connect another account or enter a key yourself.</small></span></label>
      </fieldset>}
      {credentialMode==='environment'&&<ProviderKeyPreview credential={credential}/>}
      {credentialMode==='private'&&<><label htmlFor="ai-private-key">{service.auth==='token'?'GitHub token':service.auth==='optional-key'?'API key (optional)':'API key'}</label><input id="ai-private-key" type="password" autoComplete="off" spellCheck={false} value={key} onChange={e=>setKey(e.target.value)} disabled={busy}/><p className="a-caption">{service.auth==='optional-key'?'Leave blank if your local service does not need a key.':'Stored privately for this connection. Other accounts keep their own keys.'}</p></>}
      {service.auth==='token'&&!credential?.githubCliAvailable&&!credential?.available&&<details className="a-everyday-disclosure"><summary>Help connecting GitHub Copilot</summary><div><p>Sign in to GitHub CLI on the computer running Amplifier, then check again. Or use a GitHub token for an account with Copilot access.</p><a href="https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli" target="_blank" rel="noreferrer">GitHub setup guide</a><button className="a-link" disabled={busy} onClick={()=>scanCredentials(d.module)}>Check host sign-in again</button></div></details>}
      <SettingsActions><button className="a-primary" data-action="providers.save" disabled={busy||(service.auth==='optional-key'&&!/^https?:\/\/[^\s]+$/.test(d.baseUrl||''))||(credentialMode==='environment'?!credential?.available:credentialMode==='github-cli'?!credential?.githubCliAvailable:service.auth!=='optional-key'&&!key.trim())} onClick={saveConnection}>{busy?'Connecting…':'Save and check connection'}</button></SettingsActions>
     </>}
    </>:step==='model'?<>
     {login?.status==='completed'&&<DeviceSignIn login={login} chooseModel/>}<p>Choose from the models available through this connection.</p>
     {models.length>0?<><label htmlFor="ai-model">Model</label><ModelSelect id="ai-model" label="Model" value={d.model||''} catalogKey={d.id} entry={{...catalog,models,phase:'ready'}} state={state} act={act} onChange={model=>edit({model})}/></>:<p role="status">{modelBusy?'Loading models…':'No model list is available yet. Retry or enter a model ID under More options.'}</p>}
     {modelBusy&&models.length>0&&<p className="a-caption" role="status">Refreshing in the background. Saved models remain available.</p>}
     {catalog?.phase==='error'&&<ResultNotice phase="error" message={catalog.error||'Could not refresh the model list. Saved choices are kept.'}/>}
     <button className="a-link" data-action="providers.models" disabled={busy||modelBusy} onClick={()=>run('providers.models',{id:d.id,refresh:true})}><RefreshCw/>Refresh models</button>
     <div className="a-ai-hint">{d.initializeRouting?'Balanced model rules choose suitable models for each task. This connection’s default is used when no rule applies.':'This changes the connection’s default model. Existing model rules may choose a different model.'}</div>
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
