import React from 'react';
export function VoiceVisualControls({voice,sessionId,visual,client,dispatch,onError}) {
 const run=fn=>Promise.resolve().then(fn).catch(onError);
 const inCall=voice?.status==='connected'&&voice.sessionId===sessionId,scope=inCall?'this call':'this chat in this browser',action=inCall?'voice.visual':'computer.visual';
 const target={sessionId,...(inCall?{callId:voice.id}:{})};
 if(!sessionId)return null;
 return <section className="a-card" aria-label="Computer use">
  <h3>Screen context for {scope}</h3>
  <p className="a-caption">Choose a window, tab, display, or the named desktop host. Only explicit captures are sent. Amplifier may capture that source when you ask about it. Access expires after 15 minutes or when you stop sharing{inCall?', end or replace the call':', switch chats, start voice here or disconnect'}. Saved snapshots stay in the conversation.</p>
  <div data-runtime-section="desktop-host" tabIndex={-1}><div className="a-dialog-actions"><button className="a-soft" onClick={()=>run(()=>client.current.checkNative())}>Check desktop host</button></div>
  {visual.native&&<div role="status">
   <p>Desktop host: {visual.native.host.label}. This is the server's desktop, which may differ from this browser's device.</p>
   {visual.native.available?<button className="a-soft" onClick={()=>run(()=>client.current.chooseNative())}>Allow foreground snapshots from {visual.native.host.label} for {scope}</button>:<><p>Native observation is unavailable ({visual.native.code||visual.native.status}). {visual.native.code==='backend_not_installed'?'The optional native-desktop library is missing from the serving app environment; a worker installation does not supply it.':visual.native.code==='unsupported'?'The native source requires a local macOS host. Choose a browser screen source instead.':'Check the named host and its Screen Recording permission. Checking does not request permission or capture the screen.'}</p><button className="a-soft" data-action="view.update" onClick={()=>run(()=>dispatch('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:['desktop']}}))}>Open desktop setup</button></>}
  </div>}
  </div>
  <div data-runtime-section="screen-source" tabIndex={-1}>
  {visual.source&&<p>Selected {visual.source.kind}: {visual.source.label}</p>}
  <div className="a-dialog-actions">
   <button className="a-soft" disabled={visual.status==='choosing'} onClick={()=>{client.current.choose().catch(onError)}}>Choose screen source</button>
   </div></div>
  <div data-runtime-section="capture" tabIndex={-1} className="a-dialog-actions"><button className="a-soft" data-action={action+'.capture'} disabled={visual.status!=='ready'} onClick={()=>run(()=>dispatch(action+'.capture',target))}>Capture screen</button>
   <button className="a-soft" data-action={action+'.revoke'} disabled={visual.status!=='ready'} onClick={()=>run(async()=>{client.current.stop();await dispatch(action+'.revoke',target)})}>Stop screen sharing</button>
  </div>
  {visual.capturedAt&&<p className="a-caption">Last captured {new Date(visual.capturedAt*1000).toLocaleTimeString()}. Available for the next {inCall?'spoken':'chat'} request for 30 seconds.</p>}
  {visual.error&&<p role="status">{visual.error}</p>}
  <p className="a-caption">Native foreground snapshots identify the visible window on the explicitly selected host. Desktop control is not granted.</p>
 </section>
}
