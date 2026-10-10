import React from 'react';
import {RefreshCw,Trash2} from 'lucide-react';
import {ChatGPTAccount} from './chatgpt-sign-in';
import {ProviderKeyPreview} from './provider-key-preview';
import {ProviderMessageTest} from './provider-message-test';

export function AIConnectionDetail({selected,service,imageCatalog,imageMetadata,state,session,act,busy,onModel,onImages,onCredentials,onRefresh,onCheck,onRemove,onAdvanced}){
 const image=selected?.config?.image_generation;
 const unsupported=imageCatalog?.supported===false;
 const supports=imageCatalog?.supported===true||!!imageMetadata;
 const imageLabel=unsupported?'Not available through this connection':image?.enabled===false?'Off':image?.enabled===true?(image.model==='auto'?'Automatic · latest supported':image.model||'Choose an image model'):supports?'Ready to set up':'Check availability';
 return <>
  {service.auth==='signin'&&<ChatGPTAccount account={selected?.account}/>}
  <p role={selected?.authenticationRequired?'alert':undefined}>{selected?.authenticationRequired?'Reconnect this account to continue.':selected?.accountConnected||selected?.credentialsConfigured?'Connected. Choose how Amplifier uses this account.':'Add credentials to use this connection.'}</p>
  <div className="a-connection-capabilities">
   <div className="a-connection-capability"><div><strong>Chat</strong><p>{selected?.config?.default_model||selected?.config?.model||'Choose a model'}</p></div><button type="button" className="a-soft" disabled={busy} onClick={onModel}>Choose model</button></div>
   <div className="a-connection-capability"><div><strong>Images</strong><p>{imageLabel}</p><small>Image generation uses its own model. You can keep using any model for chat.</small></div><button type="button" className="a-soft" disabled={busy} onClick={onImages}>{unsupported?'Other image connections':'Image generation'}</button></div>
  </div>
  <details className="a-everyday-disclosure"><summary>Account and connection checks</summary><div>
   <ProviderKeyPreview credential={selected?.credential}/>
   <div className="a-dialog-actions">
    <button type="button" className="a-soft" disabled={busy} onClick={onCredentials}>{service.auth==='signin'?(selected?.accountConnected?'Reconnect account':'Sign in'):'Change credentials'}</button>
    <button type="button" className="a-soft" disabled={busy} onClick={onCheck}>Check connection</button>
    <button type="button" className="a-link" disabled={busy} onClick={onRefresh}><RefreshCw/>Refresh credentials</button>
   </div>
   <p className="a-caption">Checking verifies the model catalog without sending a chat request.</p>
   <ProviderMessageTest key={selected?.id} state={state} id={selected?.id} sessionId={session?.id} act={act} disabled={busy}/>
  </div></details>
  <div className="a-dialog-actions a-connection-secondary-actions"><button type="button" className="a-link" onClick={onAdvanced}>Full connection settings</button><button type="button" className="a-link a-danger" disabled={busy} onClick={onRemove}><Trash2/>Remove connection</button></div>
 </>;
}
