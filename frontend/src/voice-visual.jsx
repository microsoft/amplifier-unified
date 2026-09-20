import React from 'react';
export function VoiceVisualControls({voice,visual,client,dispatch,onError}) {
 const run=fn=>Promise.resolve().then(fn).catch(onError);
 if(voice.status!=='connected')return null;
 return <section className="a-card" aria-label="Voice visual context">
  <strong>Screen context for this call</strong>
  <p className="a-caption">Choose a window, tab, or display. Only explicit captures are sent. Amplifier may capture that source when you ask about it during this call. Saved snapshots stay in the conversation.</p>
  {visual.source&&<p>Selected {visual.source.kind}: {visual.source.label}</p>}
  <div className="a-dialog-actions">
   <button className="a-soft" disabled={visual.status==='choosing'} onClick={()=>{client.current.choose().catch(onError)}}>Choose screen source</button>
   <button className="a-soft" data-action="voice.visual.capture" disabled={visual.status!=='ready'} onClick={()=>run(()=>dispatch('voice.visual.capture',{sessionId:voice.sessionId,callId:voice.id}))}>Capture screen</button>
   <button className="a-soft" data-action="voice.visual.revoke" disabled={visual.status!=='ready'} onClick={()=>run(async()=>{client.current.stop();await dispatch('voice.visual.revoke',{sessionId:voice.sessionId,callId:voice.id})})}>Stop screen sharing</button>
  </div>
  {visual.capturedAt&&<p className="a-caption">Last captured {new Date(visual.capturedAt*1000).toLocaleTimeString()}. Available for the next spoken request for 30 seconds.</p>}
  {visual.error&&<p role="status">{visual.error}</p>}
  <p className="a-caption">Native foreground application identity and desktop control are unavailable here.</p>
 </section>
}
