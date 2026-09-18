import React from 'react';
import {AlertCircle,Check,ChevronDown,ChevronRight,Clock3} from 'lucide-react';
const phaseLabels={'candidate-install':'Prepare application','candidate-probe':'Check candidate package','candidate-version':'Check candidate version','candidate-record':'Save candidate validation','target-discovery':'Find active installation','runtime-close':'Stop idle runtimes','recovery-record':'Save recovery reference','replacement-install':'Install application','replacement-probe':'Check installed package','replacement-version':'Check installed version','restart-configuration':'Prepare restart settings','restart-helper-file':'Prepare restart helper','restart-helper':'Start restart helper','service-restart':'Restart system service','restart-request':'Request restart','restart-ack':'Confirm restarted version','activation-validation':'Check staged release','ecosystem-copy':'Prepare ecosystem copy','ecosystem-fetch':'Fetch source update','ecosystem-checkout':'Switch staged source','ecosystem-probe':'Check bundles and modules','ecosystem-activation':'Activate ecosystem','ecosystem-stage':'Prepare ecosystem','stage':'Prepare application update'};
const statuses={started:'Running',succeeded:'Succeeded',failed:'Failed',interrupted:'Interrupted'};
const label=phase=>phaseLabels[phase]||phase?.replaceAll('-',' ')||'Update';
export function diagnosticReceipt(diagnostics){
 const fields=['id','at','attemptId','kind','phase','status','revision','commandId','durationMs','exitCode','stdoutBytes','stderrBytes','errorType','timedOut','expectedVersion','observedVersion'];
 const clean=event=>{
  if(!event)return undefined;
  const result=Object.fromEntries(fields.filter(key=>event[key]!==undefined).map(key=>[key,event[key]]));
  if(event.probe)result.probe=Object.fromEntries(['ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent','cliAbsent','stage','errorType','version','pythonVersion'].filter(key=>event.probe[key]!==undefined).map(key=>[key,event.probe[key]]));
  return result;
 };
 return JSON.stringify({attemptId:diagnostics.attemptId,lastFailure:clean(diagnostics.lastFailure),events:(diagnostics.events||[]).slice(-50).map(clean)},null,2);
}
function facts(event){
 const parts=[];
 if(event.probe?.errorType)parts.push(event.probe.errorType);
 else if(event.errorType)parts.push(event.errorType);
 if(event.probe?.stage&&event.probe.stage!=='complete')parts.push('Probe: '+event.probe.stage);
 if(event.timedOut)parts.push('Timed out');
 if(event.exitCode!==undefined)parts.push('Exit '+event.exitCode);
 if(event.expectedVersion)parts.push('Expected '+event.expectedVersion);
 if(event.observedVersion)parts.push('Found '+event.observedVersion);
 if(event.durationMs!==undefined)parts.push(event.durationMs<1000?event.durationMs+' ms':(event.durationMs/1000).toFixed(1)+' s');
 return parts.join(' · ');
}
export function UpdateDiagnostics({state,act}){
 const diagnostics=state.updates?.diagnostics||{},failure=diagnostics.lastFailure,events=diagnostics.events||[];
 if(!events.length&&!failure)return null;
 const expanded=!!state.view?.maintenanceDraft?.updateDiagnosticsExpanded;
 const selected=events.filter(event=>event.attemptId===diagnostics.attemptId);
 const rows=selected.filter(event=>event.status!=='started'||!selected.some(later=>later.commandId&&later.commandId===event.commandId&&later.status!=='started'));
 const firstFailure=events.find(event=>event.attemptId===(failure?.attemptId||diagnostics.attemptId)&&['failed','interrupted'].includes(event.status))||failure;
 return <div className="a-update-diagnostics" data-part="update-diagnostics">
  {failure&&<div className="a-update-failure" role="status"><AlertCircle aria-hidden="true"/><div><strong>Last update issue: {label(failure.phase)}</strong><p>{facts(failure)||'The phase did not finish. Review the receipt before retrying.'}</p><span className="a-caption">Attempt <code>{failure.attemptId||'Not recorded'}</code></span></div></div>}
  <button type="button" className="a-link a-update-diagnostics-toggle" data-action="view.update" aria-expanded={expanded} onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updateDiagnosticsExpanded:!expanded}}})}>{expanded?<ChevronDown/>:<ChevronRight/>}{expanded?'Hide update details':'View update details'}</button>
  {expanded&&<div className="a-update-diagnostic-details">
   {firstFailure&&firstFailure.id!==failure?.id&&<p className="a-caption">First recorded issue: {label(firstFailure.phase)} · {facts(firstFailure)}</p>}
   {diagnostics.attemptId&&<p className="a-caption">Latest attempt <code>{diagnostics.attemptId}</code></p>}
   <ol className="a-update-phase-list">{rows.map(event=>{const Icon=event.status==='succeeded'?Check:['failed','interrupted'].includes(event.status)?AlertCircle:Clock3;return <li key={event.id} className={event.status}><div><Icon aria-hidden="true"/><span>{label(event.phase)}</span><span className="a-update-phase-status">{statuses[event.status]||event.status}</span></div>{facts(event)&&<p className="a-caption">{facts(event)}</p>}</li>})}</ol>
   <label className="a-caption" htmlFor="update-diagnostic-receipt">Sanitized receipt — select and copy to share</label><textarea id="update-diagnostic-receipt" className="a-update-receipt" readOnly rows={5} spellCheck={false} value={diagnosticReceipt(diagnostics)}/>
   <p className="a-caption">Includes phase, timing, exit code and probe results. Command output, credentials, environment values and local paths are excluded.</p>
  </div>}
 </div>;
}
