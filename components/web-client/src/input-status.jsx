import React,{useEffect,useState} from 'react';
import {inputStatus,statusLabel,workingDuration} from './input-status.js';
import {messageTime} from './message-time';
import {hostNow} from './api';

export function InputStatus({message,connected=true,current=true}){
 const status=inputStatus(message),saved=message.displayOnly||!current,live=status?.phase==='working'&&connected&&!saved;
 const [now,setNow]=useState(hostNow);
 useEffect(()=>{
  if(!live)return;
  setNow(hostNow());
  const timer=setInterval(()=>setNow(hostNow()),1000);
  return()=>clearInterval(timer);
 },[live,status?.inputId,status?.startedAt]);
 if(!status)return null;
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
 </details>;
}