import {useListFilter} from './list-filter.jsx';
import {ResultNotice} from './settings-ui';
import React from 'react';
import {RefreshCw,Download,Undo2,Check,ArrowUpCircle,AlertCircle,Pin} from 'lucide-react';
const labels={update:'Update available',current:'Current',pinned:'Pinned',check_failed:'Check failed',local_changes:'Local changes',not_checked:'Not checked',release_channel_needed:'Release channel not configured'};
function SourceList({items,state,act,id}){
 const [shown,filter,query]=useListFilter(state,act,id,items,row=>[row.label,row.id,row.kind,row.status,row.ref,row.detail],'Filter update sources');
 return <>{(items.length>6||query)&&filter}<ul id={id+'-list'} className="a-update-list a-source-list">{shown.map(item=>{
 const issue=['check_failed','local_changes'].includes(item.status),Icon=issue?AlertCircle:item.status==='update'?ArrowUpCircle:item.status==='pinned'?Pin:Check;
 const unread=state.attention?.items?.some(i=>i.id==='source:'+item.id&&!i.read);
 return <li key={item.id}><div className="a-source-line"><Icon className={issue?'a-source-warning':'a-source-status'} aria-label={labels[item.status]||item.status}><title>{item.detail||labels[item.status]||item.status}</title></Icon>{unread&&<span className="a-unread-dot" aria-label="Unread"/>}<strong title={item.label}>{item.label.split('/').pop().replace(/^amplifier-(bundle-|module-)/,'')}</strong>{item.cacheCopies>1&&<span title="Identical cached copies; all are updated together">×{item.cacheCopies}</span>}<span className="a-source-ref">{item.ref||item.kind}</span><code title={item.current&&item.latest?item.current+' → '+item.latest:undefined}>{item.current?.slice(0,7)}{item.latest&&item.latest!==item.current?' → '+item.latest.slice(0,7):''}</code></div>{issue&&<ResultNotice phase={item.status==='check_failed'?'error':'neutral'} message={item.detail||labels[item.status]}/>}</li>;
 })}</ul></>;
}
export function UpdateSettings({state,act}){
 const updates=state.updates||{},options=state.settings?.updates||{},busy=['checking','staging','validating','activating'].includes(updates.phase);
 const items=updates.items||[],available=items.filter(item=>item.status==='update').sort((a,b)=>(a.kind==='app'?-1:0)-(b.kind==='app'?-1:0)||a.label.localeCompare(b.label)),issues=items.filter(item=>['check_failed','local_changes'].includes(item.status));
 const expanded=!!state.view?.maintenanceDraft?.updatesExpanded;
 const change=patch=>act('settings.update',{patch:{updates:patch}});
 return <section className="a-updates" data-part="updates">
  <p className="a-caption">Updates are prepared and validated separately, then activated when conversations, worker lanes, and calls are idle.</p>
  <div data-part="available-updates"><h4>Available updates {available.length>0&&<span className="a-update-count">{available.length} available</span>}</h4>{available.length?<SourceList items={available} state={state} act={act} id="available-updates"/>:<p className="a-caption">{busy?'Checking or preparing updates…':updates.lastCheck?'No updates available from the last check.':'Check for updates to see what’s new.'}</p>}</div>
  {!!issues.length&&<div><h4>Needs attention</h4><SourceList items={issues} state={state} act={act} id="update-issues"/></div>}
  <div className="a-dialog-actions"><button className="a-soft" disabled={busy||!!updates.pendingRelease||!!updates.pendingApp} data-action="updates.check" onClick={()=>act('updates.check')}><RefreshCw/>Check now</button><button className="a-primary" disabled={busy||!available.length||!!updates.pendingRelease||!!updates.pendingApp} data-action="updates.install" onClick={()=>act('updates.install')}><Download/>Install available</button>{updates.canRollback&&<button className="a-soft" disabled={busy} data-action="updates.rollback" onClick={()=>act('updates.rollback')}><Undo2/>Roll back</button>}</div>
  <ResultNotice phase={updates.error?'error':busy?'working':'pending'} message={updates.error||(busy||updates.pendingRelease||updates.pendingApp?updates.detail:'')}/>
  {updates.lastCheck&&<p className="a-caption a-check-inline"><Check/>Last checked {new Date(updates.lastCheck*1000).toLocaleString()}</p>}
  {updates.phase==='installed'&&<p className="a-caption">{updates.detail}</p>}
  <div className="a-update-options">
   <label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false} onChange={e=>change({autoCheck:e.target.checked,...(!e.target.checked?{autoInstall:false}:{})})}/>Automatically check for updates</label>
   <label><input type="checkbox" data-action="settings.update" disabled={options.autoCheck===false} checked={!!options.autoInstall} onChange={e=>change({autoInstall:e.target.checked})}/>Automatically install eligible updates when idle</label>
   <label htmlFor="update-frequency">Check every</label><select id="update-frequency" data-action="settings.update" value={options.intervalHours||24} onChange={e=>change({intervalHours:Number(e.target.value)})}><option value="1">Hour</option><option value="6">6 hours</option><option value="24">Day</option><option value="168">Week</option></select>
  </div>
  <p className="a-caption">Checks run while the local server is running. Bundle and module updates load on the next resumed turn after activation. App updates restart the server. Pins and local edits stay unchanged.</p>
  {!!items.length&&<div><button className="a-link" aria-expanded={expanded} data-action="view.update" onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updatesExpanded:!expanded}}})}>{expanded?'Hide all sources':'Show all '+items.length+' sources'}</button>{expanded&&<><p className="a-caption">Source inventory for troubleshooting version pins and checks.</p><SourceList items={items} state={state} act={act} id="update-sources"/></>}</div>}
 </section>;
}
