import {useListFilter} from './list-filter.jsx';
import {ResultNotice} from './settings-ui';
import React from 'react';
import {RefreshCw,Download,Undo2,Check,ArrowUpCircle,AlertCircle,Pin,Clock3} from 'lucide-react';
import './updates.css';
import {UpdateDiagnostics} from './update-diagnostics.jsx';
import {ReleaseHistory,ReleaseNotices} from './release-notes.jsx';
const labels={update:'Update available',current:'Current',pinned:'Pinned',check_failed:'Check failed',local_changes:'Local changes',not_checked:'Not checked',release_channel_needed:'Release channel not configured'};
function SourceList({items,state,act,id}){
 const [shown,filter,query]=useListFilter(state,act,id,items,row=>[row.label,row.id,row.kind,row.status,row.ref,row.detail,row.usage,...(row.usageEvidence||[])],'Filter update sources');
 return <>{(items.length>6||query)&&filter}<ul id={id+'-list'} className="a-update-list a-source-list">{shown.map(item=>{
 const issue=['check_failed','local_changes'].includes(item.status),Icon=issue?AlertCircle:item.status==='update'?ArrowUpCircle:item.status==='pinned'?Pin:Check;
 const unread=state.attention?.items?.some(i=>i.id==='source:'+item.id&&!i.read);
 return <li key={item.id}><div className="a-source-line"><Icon className={issue?'a-source-warning':'a-source-status'} aria-label={labels[item.status]||item.status}><title>{item.detail||labels[item.status]||item.status}</title></Icon>{unread&&<span className="a-unread-dot" aria-label="Unread"/>}<strong title={item.label}>{item.label.split('/').pop().replace(/^amplifier-(bundle-|module-)/,'')}</strong>{item.cacheCopies>1&&<span title="Identical cached copies">×{item.cacheCopies}</span>}<span className="a-source-ref">{item.ref||item.kind}</span><code title={item.current&&item.latest?item.current+' → '+item.latest:undefined}>{item.current?.slice(0,7)}{item.latest&&item.latest!==item.current?' → '+item.latest.slice(0,7):''}</code></div>{item.usage&&<p className="a-caption">{item.usage==='configured'?'Configured: '+(item.usageEvidence||[]).join(', '):'Usage unknown; it may still be needed.'}</p>}{issue&&<ResultNotice phase={item.status==='check_failed'?'error':'neutral'} message={item.detail||labels[item.status]}/>}</li>;
 })}</ul></>;
}
export function UpdateSettings({state,act}){
 const updates=state.updates||{},options=state.settings?.updates||{},busy=['checking','staging','validating','activating'].includes(updates.phase);
 const application=updates.application||(updates.items||[]).find(item=>item.kind==='app')||{};
 const items=(updates.items||[]).filter(item=>item.kind!=='app'&&item.id!=='application');
 const available=items.filter(item=>item.status==='update').sort((a,b)=>a.label.localeCompare(b.label)),issues=items.filter(item=>['check_failed','local_changes'].includes(item.status));
 const unknown=items.filter(item=>item.usage==='unknown'),configuredIssues=issues.filter(item=>item.usage!=='unknown');
 const unknownIssues=unknown.filter(item=>['check_failed','local_changes'].includes(item.status));
 const unknownSummary=[['check_failed','check failed'],['local_changes','with local changes'],['update','with updates'],['pinned','pinned'],['current','current'],['not_checked','not checked']].map(([status,label])=>[unknown.filter(item=>item.status===status).length,label]).filter(([count])=>count).map(([count,label])=>`${count} ${label}`).join(', ');
 const appAvailable=application.status==='update',pending=updates.pendingApp||updates.pendingRelease||updates.pendingRestart;
 const restartFailed=!!updates.pendingRestart&&(!!updates.error||['error','interrupted'].includes(updates.phase)||updates.pendingRestart.requestStatus==='rejected');
 const restartUncertain=updates.pendingRestart?.requestStatus==='uncertain';
 const appState=updates.pendingRestart?(restartFailed?'failed':restartUncertain?'restart_pending':'restarting'):updates.pendingApp?(updates.error?'failed':'staged'):application.status==='check_failed'?'failed':application.releaseBehind?'ahead':application.status||'not_checked';
 const appLabels={restarting:'Restarting',restart_pending:'Awaiting restarted host',staged:'Ready to restart',update:'Update available',current:'Latest release installed',failed:'Needs attention',ahead:'Ahead of published release',not_checked:'Not checked',release_channel_needed:'Release channel not configured'};
 const AppIcon=appState==='current'?Check:appState==='update'?ArrowUpCircle:appState==='failed'?AlertCircle:Clock3;
 const appDetail=updates.pendingRestart?(updates.error||updates.detail||(restartFailed?'The app installed, but its restart did not complete. Restart Amplifier Unified manually to continue.':'The app is installed. Waiting for a healthy restarted host before resuming work.')):updates.pendingApp?updates.error||'The app passed validation and will restart when conversations, worker lanes, smart tools, and calls are idle.':application.detail;
 const installLabel=updates.pendingRestart?(restartFailed?'Restart needs attention':restartUncertain?'Awaiting restart…':'Restarting…'):updates.pendingApp?'Apply app update':updates.pendingRelease?'Apply ecosystem update':appAvailable?'Install app update':'Install available';
 const resultPhase=restartFailed||updates.error||['error','interrupted'].includes(updates.phase)?'error':updates.pendingRestart||busy?'working':updates.phase==='installed'?'success':'neutral';
 const resultMessage=updates.error||(['error','interrupted'].includes(updates.phase)?updates.detail||'The update did not finish.':busy||pending||updates.phase==='installed'?updates.detail:'');
 const expanded=!!state.view?.maintenanceDraft?.updatesExpanded;
 const change=patch=>act('settings.update',{patch:{updates:patch}});
 return <section className="a-updates" data-part="updates">
  <div className="a-app-update" data-part="application-update" aria-label="Application release status">
   <div className="a-app-update-heading"><h4>Amplifier Unified</h4><span className={'a-app-update-status '+appState}><AppIcon aria-hidden="true"/>{appLabels[appState]||appState}</span></div>
   <dl className="a-app-update-versions"><div><dt>Installed</dt><dd>{application.current||'Not reported'}</dd></div><div><dt>Latest release</dt><dd>{application.latest||'Not checked'}</dd></div></dl>
   <p className="a-caption a-app-update-channel">Published GitHub releases · app updates restart the local server.</p>
   {appDetail&&(appState==='failed'?<ResultNotice phase="error" message={appDetail}/>:<p className="a-caption a-app-update-detail">{appDetail}</p>)}
  </div>
  <ReleaseNotices application={application} state={state} act={act}/>
  <div data-part="available-updates"><h4>Ecosystem updates {available.length>0&&<span className="a-update-count">{available.length} available</span>}</h4><p className="a-caption">Bundles, modules, and libraries load on the next resumed turn after activation.</p>{available.length?<SourceList items={available} state={state} act={act} id="available-updates"/>:<p className="a-caption">{busy?'Checking or preparing updates…':updates.lastCheck?(issues.length?'No installable updates found; unresolved source conditions are listed below.':'No updates available from the last check.'):'Check for updates to see what’s new.'}</p>}</div>
  {!!configuredIssues.length&&<div><h4>Needs attention</h4><SourceList items={configuredIssues} state={state} act={act} id="update-issues"/></div>}
  {!!unknown.length&&<div><h4>Other cached sources</h4><p className="a-caption">{unknown.length} {unknown.length===1?'source':'sources'}: {unknownSummary}.</p><p className="a-caption">Usage is unknown. These sources may still be needed by your bundles. Checks and updates continue; cached files are kept.</p>{!!unknownIssues.length&&<SourceList items={unknownIssues} state={state} act={act} id="unknown-source-issues"/>}</div>}
  <div className="a-dialog-actions"><button className="a-soft" disabled={busy||!!pending} data-action="updates.check" onClick={()=>act('updates.check')}><RefreshCw/>Check now</button><button className="a-primary" disabled={busy||!!updates.pendingRestart||(!available.length&&!appAvailable&&!pending)} data-action="updates.install" onClick={()=>act('updates.install')}><Download/>{installLabel}</button>{updates.canRollback&&<button className="a-soft" disabled={busy||!!pending} data-action="updates.rollback" onClick={()=>act('updates.rollback')}><Undo2/>Roll back ecosystem</button>}</div>
  {(appAvailable||updates.pendingApp)&&available.length>0&&<p className="a-caption">The app updates first and restarts the server. Ecosystem updates can be installed afterward.</p>}
  <ResultNotice phase={resultPhase} message={resultMessage}/>
  <ReleaseHistory application={application} state={state}/>
  <UpdateDiagnostics state={state} act={act}/>
  {updates.lastCheck&&<p className="a-caption a-check-inline"><Check/>Last checked {new Date(updates.lastCheck*1000).toLocaleString()}</p>}
  <div className="a-update-options">
   <label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false} onChange={e=>change({autoCheck:e.target.checked,...(!e.target.checked?{autoInstall:false}:{})})}/>Automatically check for updates</label>
   <label><input type="checkbox" data-action="settings.update" disabled={options.autoCheck===false} checked={!!options.autoInstall} onChange={e=>change({autoInstall:e.target.checked})}/>Automatically install eligible updates when idle</label>
   <label htmlFor="update-frequency">Check every</label><select id="update-frequency" data-action="settings.update" value={options.intervalHours||24} onChange={e=>change({intervalHours:Number(e.target.value)})}><option value="1">Hour</option><option value="6">6 hours</option><option value="24">Day</option><option value="168">Week</option></select>
  </div>
  <p className="a-caption">Checks include the app and ecosystem while the local server is running. Updates activate when work and calls are idle. Automatic app installation includes a server restart. Pins and local edits stay unchanged.</p>
  {!!items.length&&<div><button className="a-link" aria-expanded={expanded} data-action="view.update" onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updatesExpanded:!expanded}}})}>{expanded?'Hide all sources':'Show all '+items.length+' '+(items.length===1?'source':'sources')}</button>{expanded&&<><p className="a-caption">Source inventory for troubleshooting version pins and checks.</p><SourceList items={items} state={state} act={act} id="update-sources"/></>}</div>}
 </section>;
}
