import React from 'react';

const reasons={
 'smart-tool-startup-failed':'The new version could not start its configured connection. Its existing connection is unchanged.',
 'missing-configured-environment':'A configured connection credential is unavailable. Check the tool’s connection settings before retrying.',
 'smart-tool-update-failed':'This tool could not finish updating. Review its diagnostic receipt before retrying.'
};

export function SmartToolUpdateResults({updates,act,busy}){
 const failures=(updates.pendingSmartTools||[]).filter(item=>item.failure);
 if(!failures.length)return null;
 const completed=[...new Set((updates.diagnostics?.events||[]).filter(event=>event.attemptId===updates.diagnostics?.attemptId&&event.phase==='smart-tool-activate'&&event.status==='succeeded').map(event=>event.component||event.installationId).filter(Boolean))];
 return <section aria-label="Smart Tool update results" data-part="smart-tool-update-results">
  <h4>Smart Tool updates</h4>
  {!!completed.length&&<p>Updated: {completed.join(', ')}.</p>}
  <ul>{failures.map(item=><li key={item.previous}>
   <strong>{item.component||item.previous}</strong>
   <p>{reasons[item.failure.reason]||reasons['smart-tool-update-failed']}</p>
   <div className="a-dialog-actions">
    <button type="button" className="a-soft" disabled={busy} data-action="updates.smartToolRetry" onClick={()=>act('updates.smartToolRetry',{id:item.previous})}>Retry this tool</button>
    <button type="button" className="a-soft" disabled={busy} data-action="updates.smartToolSkip" onClick={()=>act('updates.smartToolSkip',{id:item.previous})}>Skip this version</button>
   </div>
  </li>)}</ul>
 </section>;
}
