import React from 'react';
import {RefreshCw,Download,Undo2} from 'lucide-react';
const labels={update:'Update available',current:'Current',pinned:'Pinned',check_failed:'Check failed',local_changes:'Local changes',not_checked:'Not checked',release_channel_needed:'Release channel not configured'};
export function UpdateSettings({state,act}){
 const updates=state.updates||{},options=state.settings?.updates||{},busy=['checking','staging','validating','activating'].includes(updates.phase);
 const expanded=!!state.view?.maintenanceDraft?.updatesExpanded;
 const change=patch=>act('settings.update',{patch:{updates:patch}});
 return <section className="a-updates" data-part="updates">
  <h3>Updates {updates.available>0&&<span className="a-update-count">{updates.available} available</span>}</h3>
  <p className="a-caption">Check cached community bundles and modules. Updates are prepared separately, validated, and activated when conversations and calls are idle.</p>
  <div className="a-update-options">
   <label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false} onChange={e=>change({autoCheck:e.target.checked,...(!e.target.checked?{autoInstall:false}:{})})}/>Automatically check for updates</label>
   <label><input type="checkbox" data-action="settings.update" disabled={!options.autoCheck} checked={!!options.autoInstall} onChange={e=>change({autoInstall:e.target.checked})}/>Automatically install eligible updates when idle</label>
   <label htmlFor="update-frequency">Check every</label><select id="update-frequency" data-action="settings.update" value={options.intervalHours||24} onChange={e=>change({intervalHours:Number(e.target.value)})}><option value="1">Hour</option><option value="6">6 hours</option><option value="24">Day</option><option value="168">Week</option></select>
  </div>
  <div className="a-dialog-actions"><button className="a-soft" disabled={busy||!!updates.pendingRelease} data-action="updates.check" onClick={()=>act('updates.check')}><RefreshCw/>Check now</button><button className="a-primary" disabled={busy||!updates.available||!!updates.pendingRelease} data-action="updates.install" onClick={()=>act('updates.install')}><Download/>Install available</button>{updates.canRollback&&<button className="a-soft" disabled={busy} data-action="updates.rollback" onClick={()=>act('updates.rollback')}><Undo2/>Roll back</button>}</div>
  <p role="status">{updates.detail||'No check has run yet.'}</p>{updates.error&&<p role="alert">{updates.error}</p>}
  {updates.lastCheck&&<p className="a-caption">Last checked: {new Date(updates.lastCheck*1000).toLocaleString()}</p>}
  <p className="a-caption">Automatic checks run while this app is open. Version pins and local edits are preserved. Core, Foundation’s host library, and patched loop-live move with tested app releases from your private amplifier-unified repository. App updates restart the host when idle. GitHub sign-in is required for private releases.</p>
  {!!updates.items?.length&&<div><button className="a-link" aria-expanded={expanded} data-action="view.update" onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updatesExpanded:!expanded}}})}>{expanded?'Hide':'Show'} {updates.items.length} sources and compatibility pins</button>{expanded&&<ul className="a-update-list">{updates.items.map(item=><li key={item.id}><strong>{item.label}</strong><span>{labels[item.status]||item.status}{item.ref?' · '+item.ref:''}</span>{item.current&&<small>{item.current.slice(0,8)}{item.latest&&item.latest!==item.current?' → '+item.latest.slice(0,8):''}</small>}{item.detail&&<small>{item.detail}</small>}</li>)}</ul>}</div>}
 </section>;
}
