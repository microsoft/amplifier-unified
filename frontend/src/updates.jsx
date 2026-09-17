import {useListFilter} from './list-filter.jsx';
import {ResultNotice} from './settings-ui';
import React from 'react';
import {RefreshCw,Download,Undo2} from 'lucide-react';
const labels={update:'Update available',current:'Current',pinned:'Pinned',check_failed:'Check failed',local_changes:'Local changes',not_checked:'Not checked',release_channel_needed:'Release channel not configured'};
function SourceList({items,state,act,id}){
 const [shown,filter]=useListFilter(state,act,id,items,row=>[row.label,row.id,row.kind,row.status,row.ref,row.detail],'Filter update sources');
 return <>{filter}<ul className="a-update-list">{shown.map(item=><li key={item.id}><strong>{item.label}</strong><ResultNotice phase={item.status==='check_failed'?'error':['not_checked','pinned','local_changes'].includes(item.status)?'neutral':'ready'} message={(labels[item.status]||item.status)+(item.ref?' · '+item.ref:'')}/>{item.current&&<small>{item.current.slice(0,8)}{item.latest&&item.latest!==item.current?' → '+item.latest.slice(0,8):''}</small>}{item.detail&&<small>{item.detail}</small>}</li>)}</ul></>;
}
export function UpdateSettings({state,act}){
 const updates=state.updates||{},options=state.settings?.updates||{},busy=['checking','staging','validating','activating'].includes(updates.phase);
 const items=updates.items||[],available=items.filter(item=>item.status==='update').sort((a,b)=>(a.kind==='app'?-1:0)-(b.kind==='app'?-1:0)||a.label.localeCompare(b.label)),failed=items.filter(item=>item.status==='check_failed').length;
 const expanded=!!state.view?.maintenanceDraft?.updatesExpanded;
 const change=patch=>act('settings.update',{patch:{updates:patch}});
 return <section className="a-updates" data-part="updates">
  <h3>Updates {available.length>0&&<span className="a-update-count">{available.length} available</span>}</h3>
  <p className="a-caption">Check cached community bundles and modules. Updates are prepared separately, validated, and activated when conversations and calls are idle.</p>
  <div data-part="available-updates"><h4>Available updates</h4>{available.length?<SourceList items={available} state={state} act={act} id="available-updates"/>:<p className="a-caption">{busy?'Checking or preparing updates…':updates.lastCheck?'No updates available from the last check.':'Check for updates to see what’s new.'}</p>}{failed>0&&<p className="a-caption">{failed} {failed===1?'source could':'sources could'} not be checked. See all sources below for details.</p>}</div>
  <div className="a-update-options">
   <label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false} onChange={e=>change({autoCheck:e.target.checked,...(!e.target.checked?{autoInstall:false}:{})})}/>Automatically check for updates</label>
   <label><input type="checkbox" data-action="settings.update" disabled={!options.autoCheck} checked={!!options.autoInstall} onChange={e=>change({autoInstall:e.target.checked})}/>Automatically install eligible updates when idle</label>
   <label htmlFor="update-frequency">Check every</label><select id="update-frequency" data-action="settings.update" value={options.intervalHours||24} onChange={e=>change({intervalHours:Number(e.target.value)})}><option value="1">Hour</option><option value="6">6 hours</option><option value="24">Day</option><option value="168">Week</option></select>
  </div>
  <div className="a-dialog-actions"><button className="a-soft" disabled={busy||!!updates.pendingRelease} data-action="updates.check" onClick={()=>act('updates.check')}><RefreshCw/>Check now</button><button className="a-primary" disabled={busy||!available.length||!!updates.pendingRelease} data-action="updates.install" onClick={()=>act('updates.install')}><Download/>Install available</button>{updates.canRollback&&<button className="a-soft" disabled={busy} data-action="updates.rollback" onClick={()=>act('updates.rollback')}><Undo2/>Roll back</button>}</div>
  <ResultNotice phase={updates.error||failed?'error':busy?'working':'ready'} message={updates.error||(busy?updates.detail||'Checking updates…':updates.lastCheck?(failed?`${failed} sources could not be checked`:'Update check complete'):'')} detail={updates.detail}/>
  {updates.lastCheck&&<p className="a-caption">Last checked: {new Date(updates.lastCheck*1000).toLocaleString()}</p>}
  <p className="a-caption">Automatic checks run while this app is open. Version pins and local edits are preserved. Core, Foundation’s host library, and patched loop-live move with tested app releases from your private amplifier-unified repository. App updates restart the host when idle. GitHub sign-in is required for private releases.</p>
  {!!items.length&&<div><button className="a-link" aria-expanded={expanded} data-action="view.update" onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updatesExpanded:!expanded}}})}>{expanded?'Hide all sources':'Show all '+items.length+' sources'}</button>{expanded&&<SourceList items={items} state={state} act={act} id="update-sources"/>}</div>}
 </section>;
}
