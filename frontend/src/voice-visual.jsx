import React from 'react';
export function VoiceVisualControls({voice,visual,client,dispatch,onError}) {
 const run=fn=>Promise.resolve().then(fn).catch(onError);
 if(voice.status!=='connected')return null;
 return <section className="a-card" aria-label="Voice visual context">
  <strong>Screen context for this call</strong>
  <p className="a-caption">Choose a window, tab, display, or the named desktop host. Only explicit captures are sent. Amplifier may capture that source when you ask about it during this call. Saved snapshots stay in the conversation.</p>
  <div className="a-dialog-actions"><button className="a-soft" onClick={()=>run(()=>client.current.checkNative())}>Check desktop host</button></div>
  {visual.native&&<div role="status">
   <p>Desktop host: {visual.native.host.label}. This is the server's desktop, which may differ from this browser's device.</p>
   {visual.native.available?<button className="a-soft" onClick={()=>run(()=>client.current.chooseNative())}>Allow foreground snapshots from {visual.native.host.label} for this call</button>:<><p>Native observation is unavailable ({visual.native.code||visual.native.status}). {visual.native.code==='backend_not_installed'?'The optional native-desktop library is missing from the serving app environment; a worker installation does not supply it.':visual.native.code==='unsupported'?'The native source requires a local macOS host. Choose a browser screen source instead.':'Check the named host and its Screen Recording permission. Checking does not request permission or capture the screen.'}</p><button className="a-soft" data-action="view.update" onClick={()=>run(()=>dispatch('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:['desktop']}}))}>Open desktop setup</button></>}
  </div>}
  {visual.source&&<p>Selected {visual.source.kind}: {visual.source.label}</p>}
  <div className="a-dialog-actions">
   <button className="a-soft" disabled={visual.status==='choosing'} onClick={()=>{client.current.choose().catch(onError)}}>Choose screen source</button>
   <button className="a-soft" data-action="voice.visual.capture" disabled={visual.status!=='ready'} onClick={()=>run(()=>dispatch('voice.visual.capture',{sessionId:voice.sessionId,callId:voice.id}))}>Capture screen</button>
   <button className="a-soft" data-action="voice.visual.revoke" disabled={visual.status!=='ready'} onClick={()=>run(async()=>{client.current.stop();await dispatch('voice.visual.revoke',{sessionId:voice.sessionId,callId:voice.id})})}>Stop screen sharing</button>
  </div>
  {visual.capturedAt&&<p className="a-caption">Last captured {new Date(visual.capturedAt*1000).toLocaleTimeString()}. Available for the next spoken request for 30 seconds.</p>}
  {visual.error&&<p role="status">{visual.error}</p>}
  <p className="a-caption">Native foreground snapshots identify the visible window on the explicitly selected host. Desktop control is not granted.</p>
 </section>
}
