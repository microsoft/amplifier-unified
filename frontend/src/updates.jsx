import {AttentionReview,AttentionBadge} from './attention';
import {updateOverview} from './update-overview';
import {ActivityRegion} from './activity-region';
import {useListFilter} from './list-filter.jsx';
import {ResultNotice} from './settings-ui';
import React from 'react';
import {RefreshCw,Download,Undo2,Check,ArrowUpCircle,AlertCircle,Pin,Clock3} from 'lucide-react';
import './updates.css';
import {UpdateDiagnostics} from './update-diagnostics.jsx';
import {ReleaseHistory,ReleaseNotices} from './release-notes.jsx';
const labels={update:'Update available',current:'Current',pinned:'Pinned',check_failed:'Check failed',local_changes:'Local changes',not_checked:'Not checked',release_channel_needed:'Release channel not configured',historical:'Older saved configuration'};
function SourceList({items,state,act,id}){
 const [shown,filter,query]=useListFilter(state,act,id,items,row=>[row.label,row.id,row.kind,row.status,row.ref,row.detail,row.usage,...(row.usageEvidence||[])],'Filter update sources');
 return <>{(items.length>6||query)&&filter}<ul id={id+'-list'} className="a-update-list a-source-list">{shown.map(item=>{
 const issue=['check_failed','local_changes'].includes(item.status),Icon=item.status==='historical'?Clock3:issue?AlertCircle:item.status==='update'?ArrowUpCircle:item.status==='pinned'?Pin:Check;
 const unread=state.attention?.items?.some(i=>i.id==='source:'+item.id&&!i.read);
 return <li key={item.id}><div className="a-source-line"><Icon className={issue?'a-source-warning':'a-source-status'} aria-label={labels[item.status]||item.status}><title>{item.detail||labels[item.status]||item.status}</title></Icon>{unread&&<span className="a-unread-dot" aria-label="Unread"/>}<strong title={item.label}>{item.label.split('/').pop().replace(/^amplifier-(bundle-|module-)/,'')}</strong>{item.cacheCopies>1&&<span title="Identical cached copies">×{item.cacheCopies}</span>}<span className="a-source-ref">{item.ref||item.kind}</span><code title={item.current&&item.latest?item.current+' → '+item.latest:undefined}>{item.current?.slice(0,7)}{item.latest&&item.latest!==item.current?' → '+item.latest.slice(0,7):''}</code></div>{item.usage&&<p className="a-caption">{item.usage==='configured'?'Configured: '+(item.usageEvidence||[]).join(', '):'Usage unknown; it may still be needed.'}</p>}{(issue||item.kind==='history')&&<ResultNotice phase={item.status==='check_failed'?'error':'neutral'} message={item.detail||labels[item.status]}/>}{item.sourceIssues?.length>0&&<ul>{item.sourceIssues.map((entry,index)=><li key={index} style={{overflowWrap:'anywhere'}}><strong>{entry.reference||'Settings'}</strong><p>{entry.reason}</p>{entry.workspace&&<p>{entry.workspace}</p>}{entry.sessionId&&<p>Session ID: <code>{entry.sessionId}</code></p>}{entry.appSessionId&&<button type="button" className="a-soft" data-action="session.select" onClick={async()=>{const result=await act('session.select',{id:entry.appSessionId});if(result&&result.accepted!==false)await act('view.update',{patch:{panel:null}})}}>Open conversation</button>}</li>)}</ul>}</li>;
 })}</ul></>;
}
function ComponentSummary({updates,items,busy}){
 const sequence=updates.sequence||{};
 return <div className="a-update-summary" aria-label="Component update summary">{[['included','Included components'],['other','Other components']].map(([tier,label])=>{
  const rows=items.filter(row=>(row.updateTier||'other')===tier);
  const summary=sequence[tier]||{status:updates.lastCheck?'current':'waiting',available:rows.filter(row=>row.status==='update').length,issues:rows.filter(row=>['check_failed','local_changes'].includes(row.status)).length};
  const checking=busy&&updates.phase==='checking'&&sequence.stage===tier;
  const text=checking?'Checking…':summary.available?`${summary.available} available${summary.missing?` · ${summary.missing} to install`:''}${summary.issues?` · ${summary.issues} need attention`:''}`:summary.status==='waiting'?(sequence.stage==='application'?'After app update':tier==='other'?'After included components':'Not checked'):summary.issues?`${summary.issues} need attention`:summary.protected?`${summary.protected} kept as configured`:'Up to date';
  return <div key={tier}><span>{label}</span><strong>{text}</strong></div>;
 })}</div>;
}
export function UpdateSettings({state,act}){
 const unread=(state.attention?.items||[]).filter(item=>item.page==='updates'&&!item.read),releaseUnread=unread.filter(item=>item.id.startsWith('release-notice:')),componentUnread=unread.filter(item=>!item.id.startsWith('release-notice:'));
 const updates=state.updates||{},options=state.settings?.updates||{},replacement=updates.pendingReplacement!=null,busy=replacement||['checking','staging','validating','activating'].includes(updates.phase);
 const application=updates.application||(updates.items||[]).find(item=>item.kind==='app')||{};
 const items=(updates.items||[]).filter(item=>item.kind!=='app'&&item.id!=='application');
 const available=items.filter(item=>item.status==='update').sort((a,b)=>a.label.localeCompare(b.label)),issues=items.filter(item=>['check_failed','local_changes'].includes(item.status));
 const unknown=items.filter(item=>item.usage==='unknown'),configuredIssues=issues.filter(item=>item.usage!=='unknown');
 const unknownIssues=unknown.filter(item=>['check_failed','local_changes'].includes(item.status));
 const unknownSummary=[['check_failed','check failed'],['local_changes','with local changes'],['update','with updates'],['pinned','pinned'],['current','current'],['not_checked','not checked']].map(([status,label])=>[unknown.filter(item=>item.status===status).length,label]).filter(([count])=>count).map(([count,label])=>`${count} ${label}`).join(', ');
 const appAvailable=application.status==='update',pending=updates.pendingApp||updates.pendingRelease||updates.pendingRestart;
 const restartFailed=!!updates.pendingRestart&&(!!updates.error||['error','interrupted'].includes(updates.phase)||updates.pendingRestart.requestStatus==='rejected');
 const restartUncertain=updates.pendingRestart?.requestStatus==='uncertain';
 const appState=replacement?'failed':updates.pendingRestart?(restartFailed?'failed':restartUncertain?'restart_pending':'restarting'):updates.pendingApp?(updates.error?'failed':'staged'):application.status==='check_failed'?'failed':application.releaseBehind?'ahead':application.status||'not_checked';
 const appLabels={restarting:'Restarting',restart_pending:'Awaiting restarted host',staged:'Ready to restart',update:'Update available',current:'Latest release installed',failed:'Needs attention',ahead:'Ahead of published release',not_checked:'Not checked',release_channel_needed:'Release channel not configured',historical:'Older saved configuration'};
 const AppIcon=appState==='current'?Check:appState==='update'?ArrowUpCircle:appState==='failed'?AlertCircle:Clock3;
 const appDetail=replacement?(updates.error||updates.detail||'The installation outcome is unknown. Work remains paused until the qualified app and dependencies are verified on a new host.'):updates.pendingRestart?(updates.error||updates.detail||(restartFailed?'The app installed, but its restart did not complete. Restart Amplifier Unified manually to continue.':'The app is installed. Waiting for a healthy restarted host before resuming work.')):updates.pendingApp?updates.error||'The app passed validation and will restart when conversations, worker lanes, smart tools, and calls are idle.':application.detail;

 const resultPhase=restartFailed||updates.error||['error','interrupted'].includes(updates.phase)?'error':updates.pendingRestart||busy?'working':updates.phase==='installed'?'success':'neutral';
 const resultMessage=updates.error||(['error','interrupted'].includes(updates.phase)?updates.detail||'The update did not finish.':busy||pending||updates.phase==='installed'?updates.detail:'');
 const overview=updateOverview(updates,options);
 const expanded=!!state.view?.maintenanceDraft?.updatesExpanded;
 const change=patch=>act('settings.update',{patch:{updates:patch}});
 return <ActivityRegion as="section" name="updates" busy={busy} className="a-updates" data-part="updates">
  <div className="a-app-update" data-part="application-update" aria-label="Overall update status" role="status" aria-live="polite">
   <div className="a-update-overall">{overview.tone==='error'?<AlertCircle/>:overview.tone==='ready'?<Check/>:overview.tone==='available'?<ArrowUpCircle/>:<Clock3/>}<div><h4>{overview.title}</h4><p>{overview.detail}</p></div></div>
   {(overview.tone==='working'||overview.tone==='available')&&<div className="a-update-progress" aria-label="Update order">{[['application','App'],['included','Included components'],['other','Other components']].map(([id,label],i)=><span key={id} data-active={overview.stage===id}>{i>0&&'→ '}{label}</span>)}</div>}
   <p className="a-update-version-line">Amplifier {application.current||'version not reported'}{application.latest&&application.latest!==application.current?' · Latest '+application.latest:''}{updates.lastCheck?' · Checked '+new Date(updates.lastCheck*1000).toLocaleString():''}</p>
  </div>
  <div className="a-update-controls" data-part="update-controls">
  <div className="a-dialog-actions"><button className={overview.installable?'a-soft':'a-primary'} disabled={busy||!!pending} data-action="updates.check" onClick={()=>act('updates.check')}><RefreshCw/>Check for updates</button>{overview.installable&&(!overview.continuing||overview.tone==='error')&&<button className="a-primary" disabled={busy||overview.blocked} data-action="updates.install" onClick={()=>act('updates.install')}><Download/>Update Amplifier</button>}</div>
  <p className="a-caption">One request handles everything in order. Updates wait for work and calls to finish; an app update may restart the server.</p>
  <div className="a-update-simple-options"><label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false&&!!options.autoInstall} onChange={e=>change(e.target.checked?{autoCheck:true,autoInstall:true}:{autoInstall:false})}/>Keep Amplifier up to date automatically</label><p className="a-caption">Install eligible app and component updates when work is idle. Existing pins and local edits are kept.</p></div>
  <details className="a-everyday-disclosure"><summary>Update preferences</summary><div className="a-update-options">
   <label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false} onChange={e=>change({autoCheck:e.target.checked,...(!e.target.checked?{autoInstall:false}:{})})}/>Automatically check for updates</label>
   <label htmlFor="update-frequency">Check every</label><select id="update-frequency" data-action="settings.update" value={options.intervalHours||24} onChange={e=>change({intervalHours:Number(e.target.value)})}><option value="1">Hour</option><option value="6">6 hours</option><option value="24">Day</option><option value="168">Week</option></select>
  </div></details>
  </div>
  <details className="a-update-disclosure" data-part="ecosystem-update-details" open={overview.tone==='error'||undefined}><summary>{overview.tone==='error'?'Review update issue':'Component updates & details'}<AttentionBadge count={componentUnread.length}/></summary><div className="a-update-disclosure-body">
   <AttentionReview state={state} act={act} page="updates" items={componentUnread} showItems/>
   <ComponentSummary updates={updates} items={items} busy={busy}/>
   <div className="a-app-update-heading"><h4>App release</h4><span className={'a-app-update-status '+appState}><AppIcon aria-hidden="true"/>{appLabels[appState]||appState}</span></div>
   {appDetail&&(appState==='failed'?<ResultNotice phase="error" message={appDetail}/>:<p className="a-caption a-wrap">{appDetail}</p>)}
   <ResultNotice phase={resultPhase} message={resultMessage}/><UpdateDiagnostics state={state} act={act}/>
   {updates.canRollback&&<button className="a-soft" disabled={busy||!!pending} data-action="updates.rollback" onClick={()=>act('updates.rollback')}><Undo2/>Roll back ecosystem</button>}
   <div data-part="available-updates"><h4>Ecosystem updates {available.length>0&&<span className="a-update-count">{available.length} available</span>}</h4>{available.length?<SourceList items={available} state={state} act={act} id="available-updates"/>:<p className="a-caption">{overview.stage!=='complete'?'Remaining components are checked after the preceding update stage.':'No eligible component updates remain.'}</p>}</div>
   {!!configuredIssues.length&&<div><h4>Needs attention</h4><SourceList items={configuredIssues} state={state} act={act} id="update-issues"/></div>}
   {!!unknown.length&&<div><h4>Other cached sources</h4><p className="a-caption">{unknown.length} sources: {unknownSummary}. Usage is unknown; these files may still be needed and are kept.</p>{!!unknownIssues.length&&<SourceList items={unknownIssues} state={state} act={act} id="unknown-source-issues"/>}</div>}
   {!!items.length&&<div><button className="a-link" aria-expanded={expanded} data-action="view.update" onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updatesExpanded:!expanded}}})}>{expanded?'Hide all sources':'Show all '+items.length+' '+(items.length===1?'source':'sources')}</button>{expanded&&<SourceList items={items} state={state} act={act} id="update-sources"/>}</div>}
  </div></details>
  <details className="a-update-disclosure"><summary>Release notices<AttentionBadge count={releaseUnread.length}/></summary><div className="a-update-disclosure-body"><AttentionReview state={state} act={act} page="updates" items={releaseUnread}/><ReleaseNotices application={application} state={state} act={act}/></div></details>
  {items.some(item=>item.kind==='history')&&<details className="a-update-disclosure" data-part="historical-source-settings"><summary>Older conversation settings</summary><div className="a-update-disclosure-body"><SourceList items={items.filter(item=>item.kind==='history')} state={state} act={act} id="historical-source-settings"/></div></details>}
  <ReleaseHistory application={application} state={state}/>
 </ActivityRegion>;
}
