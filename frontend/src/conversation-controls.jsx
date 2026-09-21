import React,{useEffect,useRef,useState} from 'react';
import {X} from 'lucide-react';
import {readItems} from './attention';
import {isTopLevelChat} from './chat-navigation';
import {sessionIdentity} from './navigation-presentation';

export function ConversationName({session,act,detailsOpen=false}){
 const [name,setName]=useState(session.title||''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const dirty=useRef(false),inFlight=useRef(false);
 useEffect(()=>{if(!dirty.current)setName(session.title||'')},[session.title]);
 async function save(event){
  event.preventDefault();if(inFlight.current||!name.trim())return;
  inFlight.current=true;setSaving(true);setError('');
  try{
   const result=await act('session.rename',{id:session.id,title:name.trim()});
   if(!result||result.accepted===false)throw Error('Could not save the name. Your edit is kept.');
   dirty.current=false;setName(name.trim());
  }catch(error){setError(error.message)}finally{inFlight.current=false;setSaving(false)}
 }
 return <><form onSubmit={save} aria-busy={saving}><label htmlFor="session-title">Chat name</label><input id="session-title" value={name} disabled={saving} required maxLength={200} onChange={event=>{dirty.current=true;setName(event.target.value);setError('')}}/><div className="a-dialog-actions"><button type="submit" className="a-soft" data-action="session.rename" disabled={saving||!name.trim()||name===session.title}>{saving?'Saving…':'Save name'}</button></div>{error&&<p role="alert" className="a-danger">{error}</p>}</form><ConversationDetails key={session.id} session={session} act={act} initiallyOpen={detailsOpen}/></>;
}

export function ConversationDetails({session,act,initiallyOpen=false}){
 const [open,setOpen]=useState(initiallyOpen),[report,setReport]=useState(session.health),[busy,setBusy]=useState(''),[error,setError]=useState(''),[copied,setCopied]=useState('');
 const inFlight=useRef(false);
 useEffect(()=>{setReport(session.health);setCopied('');setError('')},[session.id,session.health]);
 async function inspect(){
  if(inFlight.current)return;inFlight.current=true;setBusy('inspect');setError('');setOpen(true);
  try{const result=await act('session.inspect',{id:session.id});if(!result||result.accepted===false)throw Error('Could not inspect this conversation.');setReport(result.result)}catch(error){setError(error.message)}finally{inFlight.current=false;setBusy('')}
 }
 async function recover(){
  if(inFlight.current)return;inFlight.current=true;setBusy('recover');setError('');
  try{const result=await act('session.recover',{id:session.id});if(!result||result.accepted===false)throw Error('Could not create the recovery copy.')}catch(error){setError(error.message)}finally{inFlight.current=false;setBusy('')}
 }
 async function copy(value,label){try{await navigator.clipboard.writeText(value);setCopied(label)}catch{setError('Clipboard unavailable. Select and copy the session ID below.')}}
 const identity=sessionIdentity(session),failure=report?.failure||session.failure;
 const working=['working','running','starting','stopping'].includes(session.status)||session.configurationBusy||(session.workers||[]).some(worker=>['queued','starting','running','working','stopping'].includes(worker.status));
 return <div className="a-conversation-details" aria-busy={!!busy}>
  <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="session.inspect" disabled={!!busy} onClick={()=>open?setOpen(false):inspect()}>{busy==='inspect'?'Inspecting…':open?'Hide details':'Conversation details'}</button><button type="button" className="a-soft" onClick={()=>copy(identity,'Session ID copied')}>Copy session ID</button></div>
  {open&&<div><p><strong>Session ID</strong><br/><code style={{overflowWrap:'anywhere'}}>{identity}</code></p>{identity!==session.id&&<p>App ID: <code style={{overflowWrap:'anywhere'}}>{session.id}</code></p>}<p style={{overflowWrap:'anywhere'}}>{session.workspace}<br/>Bundle: {session.bundle} · Status: {session.status}</p>
   {session.error&&<><p><strong>{failure?.summary||'The turn failed. Inspect the recorded details for its cause.'}</strong></p><p>{failure?.guidance||'Work was not automatically replayed.'}</p>{failure&&<p>Recorded error: {failure.errorType}{failure.recordedAt?' · '+new Date(failure.recordedAt*1000||failure.recordedAt).toLocaleString():''}</p>}<details><summary>Runtime message</summary><p style={{overflowWrap:'anywhere'}}>{session.error}</p></details></>}
   <p>Create an independent copy with readable history. Old tool calls and image payloads stay in the original; reattach images if needed. Safety stops remain in effect. Nothing runs until you send a new message.</p>
   <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="session.recover" disabled={!!busy||working} onClick={recover}>{busy==='recover'?'Creating recovery copy…':'Create recovery copy'}</button><button type="button" className="a-soft" disabled={!report} onClick={()=>copy(JSON.stringify(report,null,2),'Diagnostics copied')}>Copy diagnostics</button></div>
   {working&&<p>Wait for the current work to stop before creating a copy.</p>}
  </div>}
  {copied&&<p role="status">{copied}</p>}{error&&<p role="alert" className="a-danger">{error}</p>}
 </div>;
}

export function ConversationError({state,session,act}){
 if(!session?.error)return session?.recovery?<p className="a-hint">Recovery copy · Readable history retained. Old tool and image payloads remain in the original conversation. No work was replayed.</p>:null;
 const item=state.attention?.items?.find(row=>row.id==='session:'+session.id);
 if(item?.read)return null;
 return <div><div className="a-alert" role="alert"><span><strong>Conversation stopped.</strong> {session.failure?.summary||'The last turn failed. Your conversation is saved; work was not automatically replayed.'}</span>{item&&<button type="button" aria-label="Dismiss conversation error" data-action="attention.read" onClick={()=>readItems(act,[item])}><X/></button>}</div><ConversationDetails key={session.id} session={session} act={act}/></div>;
}

export function ConversationSelect({state,session,choices,onSelect}){
 const [held,setHeld]=useState(null);
 const scope=state.selectedWorkspaceId;
 const rows=held?.scope===scope?held.items:choices.items;
 const hold=()=>setHeld(previous=>previous?.scope===scope?previous:{scope,items:choices.items});
 if(!rows.length)return null;
 return <select className="a-session-select" aria-label="Select conversation" data-action="session.select" value={isTopLevelChat(session)?session?.id||'':''}
  onFocus={hold} onPointerDown={hold} onBlur={()=>setHeld(null)} onKeyDown={event=>{if(event.key==='Escape')setHeld(null)}}
  onChange={event=>{const id=event.target.value;setHeld(null);onSelect(id)}}>
  {!isTopLevelChat(session)&&<option value="" disabled>{session?'Viewing subagent history':'New chat'}</option>}
  {rows.map(row=><option key={row.id} value={row.id}>{row.title}{state.attention?.sessions?.[row.id]?' · Needs attention':['working','running','starting','stopping'].includes(row.status)?' · '+(row.status==='starting'?'Preparing':row.status==='stopping'?'Stopping':'Working'):''}</option>)}
  {choices.total>rows.length&&<option disabled>Find all chats in the workspace sidebar</option>}
 </select>;
}
