import React,{useState} from 'react';
import {Copy,ChevronRight} from 'lucide-react';
import {Markdown} from './markdown';
import {useExecutionField,ExecutionBlock,FieldStatus} from './execution-content.jsx';
import {readDetail} from './detail-read';
import {request} from './api';
import {delegationRoutingLabel} from './delegation-routing.js';
import {isRunning} from './timeline-data';

export function DelegateMessage({node,field,label,direction,expanded,toggle,partial=false}){
 const id=`exchange:${node.id}:${field}`,full=expanded.has(id);
 const loaded=useExecutionField(node,field,full),[copying,setCopying]=useState(false),[copied,setCopied]=useState('');
 const value=loaded.value,reference=node[field+'Detail'];
 if(value==null&&!reference)return null;
 const long=!!reference||String(value).length>1200||String(value).split('\n').length>10;
 const preview=String(value??'').split('\n').slice(0,10).join('\n').slice(0,1200);
 async function copy(){
  setCopying(true);setCopied('');
  try{const text=loaded.incomplete?await readDetail({...reference,complete:'true'},request):String(value??'');await navigator.clipboard.writeText(text);setCopied('Copied')}
  catch(e){setCopied(`Copy failed: ${e.message}`)}finally{setCopying(false)}
 }
 return <section className="a-execution-payload a-delegate-message" aria-label={label}>
  <header><span>{label}</span><span>{direction}</span><button type="button" className="a-link" aria-label={`Copy ${partial?'recorded portion of ':''}${label.toLowerCase()}`} disabled={copying} onClick={copy}><Copy/>{copying?'Copying…':'Copy'}</button></header>
  <div className="a-delegate-markdown"><Markdown text={full?String(value??''):preview} userContent/></div>
  {partial&&<p className="a-caption">Partial recording; only the retained portion is available.</p>}
  {long&&<button type="button" className="a-link a-execution-more" aria-expanded={full} onClick={()=>toggle(id)}>{full?'Show less':`Show full ${field==='delegateRequest'?'request':'response'}`}</button>}
  {full&&<FieldStatus label={label} field={loaded}/>}
  {copied&&<p className="a-caption" role="status">{copied}</p>}
 </section>;
}

function RawExchange({node}){
 const input=useExecutionField(node,'input',true),output=useExecutionField(node,'output',true),error=useExecutionField(node,'error',true);
 return <>{[['Input',input],['Result',output],['Error',error]].map(([label,field])=><React.Fragment key={label}><ExecutionBlock label={label} text={field.value} loading={field.incomplete}/><FieldStatus label={label} field={field}/></React.Fragment>)}</>;
}

function Followup({entry,expanded,toggle}){
 const detailsId=`exchange:${entry.id}:details`,details=expanded.has(detailsId);
 const failed=['error','failed','cancelled','interrupted'].includes(entry.phase);
 return <><DelegateMessage node={entry} field="delegateRequest" label="Follow-up to delegate" direction="Parent → Delegate" expanded={expanded} toggle={toggle}/>
  <p className="a-caption">{failed?`Follow-up ${entry.phase}; delivery was not confirmed.`:['completed','complete','success'].includes(entry.phase)?'Request processed; application is not confirmed by this receipt.':'Follow-up attempted; awaiting receipt.'}</p>
  <button type="button" className="a-link" aria-expanded={details} onClick={()=>toggle(detailsId)}>Follow-up receipt</button>
  {details&&<RawExchange node={entry}/>}
 </>;
}

export function DelegateExchange({node,expanded,toggle,activity,activityOpen,toggleActivity,childrenCount=0,entries=[]}){
 const exchange=node.exchange||{id:node.id},detailsId=`exchange:${exchange.id}:details`,details=expanded.has(detailsId);
 const response=exchange.delegateResponse!=null||!!exchange.delegateResponseDetail;
 const requestPresent=exchange.delegateRequest!=null||!!exchange.delegateRequestDetail;
 const failures=['error','failed','cancelled','interrupted'],workerPhase=node.workerPhase||node.phase;
 const phase=[workerPhase,exchange.phase].find(value=>failures.includes(value))||workerPhase||exchange.phase;
 const failure=failures.includes(phase);
 return <div className="a-delegate-exchange">
  {requestPresent?<DelegateMessage node={exchange} field="delegateRequest" label="Request to delegate" direction="Parent → Delegate" expanded={expanded} toggle={toggle}/>:<p className="a-caption">Request not recorded.</p>}
  {childrenCount>0&&<><button type="button" className="a-link a-delegate-activity-toggle" aria-expanded={activityOpen} onClick={toggleActivity}><ChevronRight className={activityOpen?'open':''}/>Worker activity · {childrenCount} {childrenCount===1?'step':'steps'}{isRunning(node)?' · running':''}</button>{activityOpen&&activity}</>}
  {entries.map(entry=><React.Fragment key={entry.id}>
   {(entry.delegateRequest!=null||entry.delegateRequestDetail)&&<Followup entry={entry} expanded={expanded} toggle={toggle}/>}
   <DelegateMessage node={entry} field="delegateResponse" label={entry.report?'Update from delegate':'Response to parent'} direction="Delegate → Parent" expanded={expanded} toggle={toggle} partial={entry.partial}/>
  </React.Fragment>)}
  {response?<><DelegateMessage node={exchange} field="delegateResponse" label="Response to parent" direction="Delegate → Parent" expanded={expanded} toggle={toggle}/><p className="a-caption">Recorded delegate return. Parent consumption is not confirmed by this tool record.</p></>:exchange.delivery!=='separate'&&!failure&&<p className="a-caption" role="status">{isRunning({phase})?'Waiting for response…':'Response not recorded.'}</p>}
  {failure&&<p className="a-caption" role="status">Worker {phase}. Any earlier reports remain available; they do not establish completion.</p>}
  {exchange.delivery==='separate'&&!failure&&isRunning({phase})&&<p className="a-caption" role="status">{phase==='idle'?'Worker remains available.':'Worker is still active; earlier reports are not a final outcome.'}</p>}
  {!response&&exchange.delivery!=='separate'&&node.summary&&<><DelegateMessage node={node} field="summary" label="Recorded worker summary" direction="Delegate report" expanded={expanded} toggle={toggle} partial/><p className="a-caption">Legacy summary; not evidence of the exact response returned to the parent.</p></>}
  {exchange.delivery==='separate'&&!entries.some(entry=>entry.delegateResponse!=null||entry.delegateResponseDetail)&&<p className="a-caption">Returned messages are recorded in another segment of this delegate.</p>}
  <button type="button" className="a-link a-delegate-details-toggle" aria-expanded={details} onClick={()=>toggle(detailsId)}><ChevronRight className={details?'open':''}/>Delegation details</button>
  {details&&<div className="a-delegate-details"><p className="a-caption">{delegationRoutingLabel(node.routing)}</p><p className="a-caption">{exchange.toolCallId&&`Call: ${exchange.toolCallId}`}{node.runId&&` · Run: ${node.runId}`}</p><RawExchange node={exchange}/></div>}
 </div>;
}
