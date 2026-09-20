import React from 'react';
export function NativeProviderSettings({results,run,busy,active}){
 const status=results['native.status']||results['native.compact'];
 return <section className="a-settings-section" data-part="native-provider-settings">
  <h3>Live direction & saved context</h3>
  <p>Check whether this model can apply new direction during a response. Other models receive it at the next request.</p>
  <button className="a-soft" disabled={busy} data-action="runtime.control" onClick={()=>run('native.status')}>Check provider support</button>
  {status&&<><p>{status.supported?'Native direction is available for this selected model.':status.reason||'Direction is delivered at the next request.'}</p>
   {status.receipt?.outcome==='unknown'&&<p role="alert">The previous request’s outcome is unknown. It was not replayed.</p>}
   {status.checkpoint&&<p>Saved provider context: {status.checkpoint}.</p>}
   <button className="a-soft" disabled={busy||active||!status.compactAvailable} data-action="runtime.control" onClick={()=>run('native.compact')}>Compact provider context</button>
   <p className="a-caption">Original conversation history stays intact. Compaction makes a model request; its private provider state is never displayed.</p>
  </>}
 </section>;
}
