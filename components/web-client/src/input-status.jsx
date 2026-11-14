import React,{useEffect,useRef,useState} from 'react';
import {inputStatus,statusLabel,workingDuration} from './input-status.js';
import {messageTime} from './message-time';
import {hostNow} from './api';
import {ConversationFailure} from './conversation-controls';
import {openProblemReport} from './feedback';

export function InputStatus({message,connected=true,current=true,act,sessionId,state}){
 const status=inputStatus(message),saved=message.displayOnly||!current,live=status?.phase==='working'&&connected&&!saved;
 const [now,setNow]=useState(hostNow);
 const [reporting,setReporting]=useState(false),[reportError,setReportError]=useState(''),reportFlight=useRef(false);
 useEffect(()=>{
  if(!live)return;
  setNow(hostNow());
  const timer=setInterval(()=>setNow(hostNow()),1000);
  return()=>clearInterval(timer);
 },[live,status?.inputId,status?.startedAt]);
 if(!status)return null;
 const generationId=status.generationId||status.failure?.generationId;
 const canReport=!!(sessionId&&status.inputId&&generationId&&!state?.view?.feedbackDraft?.pending);
 async function report(){
  if(!canReport||reportFlight.current)return;
  reportFlight.current=true;setReporting(true);setReportError('');
  try{
   await openProblemReport(state,act,{sessionId,inputId:status.inputId,generationId});
  }catch{setReportError('Could not open the report. Nothing was submitted.')}
  finally{reportFlight.current=false;setReporting(false)}
 }
 const duration=workingDuration(status,now,connected&&!saved);
 const label=status.phase==='working'&&saved?'Working last observed · saved history':statusLabel(status,connected);
 const observed=messageTime(status.startedAt||status.updatedAt);
 const explanation=status.phase==='received'?'The host received this input. Model inclusion and execution are not yet confirmed.':
  status.phase==='done'?'This input’s turn finished. This is not verification that every requested effect or task is complete.':
  status.phase==='included'?'Observed context insertion, not proof of model reading or understanding.':
  status.phase==='working'?'Execution observed for this exact input, not proof that the model read or understood it.':
  status.phase==='unknown'?'No confirmed current outcome for this input. Nothing is sent again.':
  'Status is based on observed events for this exact input.';
 return <details className="a-input-status" data-input-phase={status.phase}>
  <summary>
  {/* A nested status role excludes its text from Chromium's summary name. */}
  <span aria-live="polite" aria-atomic="true">{label}</span>
  {duration&&<span aria-live="off" className="a-input-duration" aria-label={`Observed elapsed time ${duration}`}> · {duration}</span>}
  {observed&&<time dateTime={observed.iso} aria-label={`Status observed ${observed.full}`}> · {observed.text}</time>}
  </summary>
  <span className="a-input-status-detail">{status.reason&&<>{status.reason} </>}{explanation}</span>
  {status.phase==='failed'&&status.failure&&<ConversationFailure session={{}} failure={status.failure}/>}
  {status.phase==='failed'&&act&&<><button type="button" className="a-link" data-action="view.update" disabled={reporting||!canReport} onClick={report}>{reporting?'Opening report…':'Report this problem'}</button>{!canReport&&<p className="a-caption">{state?.view?.feedbackDraft?.pending?'Finish checking the existing report before starting another.':'This saved request has no exact report reference. Its recorded details remain available above.'}</p>}{reportError&&<p role="alert">{reportError}</p>}</>}
 </details>;
}