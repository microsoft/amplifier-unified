import React,{useEffect,useRef,useState} from 'react';
import {Copy,Check,Pencil,GitBranch,ArrowUp,AlertCircle} from 'lucide-react';
import {Markdown} from './markdown';
import {AttachmentStrip} from './chat-controls';

export function completedTurnEnds(session){
 const messages=session?.messages||[],ends=new Map();let turn=0;
 for(let i=0;i<messages.length;i++){
  if(messages[i].role!=='user')continue;
  turn++;
  let end=i+1;while(end<messages.length&&messages[end].role!=='user')end++;
  const last=messages.slice(i+1,end).findLast(m=>m.role==='assistant');if(!last)continue;
  const record=session.execution?.turns?.find(t=>t.inputId===messages[i].inputId&&messages[i].inputId);
  const finished=record?['completed','complete','done'].includes(record.phase||record.status):end<messages.length||!['working','running','starting','stopping','error'].includes(session.status);
  if(finished)ends.set(last.id,turn);
 }
 return ends;
}
export function MessageEntry({message:m,session,state,act,stamp,working,forkTurn}){
 const [saving,setSaving]=useState(false),edit=state.view?.messageEdit,editing=edit?.sessionId===session.id&&edit?.messageId===m.id;
 const [text,setText]=useState(editing?edit.text:''),pendingText=useRef(null);
 useEffect(()=>{if(!editing){pendingText.current=null;return}if(pendingText.current===null||edit.text===pendingText.current){setText(edit.text||'');pendingText.current=null}},[editing,edit?.text]);
 const copy=state.view?.messageCopy,copied=copy?.sessionId===session.id&&copy?.messageId===m.id?copy:null;
 const blocked=working||session.configurationBusy;
 const patch=value=>act('view.update',{patch:{messageEdit:value}});
 const submit=async e=>{e.preventDefault();if(saving||blocked||!text.trim())return;setSaving(true);try{await act('message.edit',{sessionId:session.id,messageId:m.id,text})}finally{setSaving(false)}};
 return <article className={`a-message a-${m.role==='user'?'user':'assistant'}`} data-message-id={m.id}>
  <div className="a-msg-meta"><strong>{m.role==='user'?'You':m.role==='assistant'?'Amplifier':m.role}</strong><span>{m.via&&`via ${m.via} · `}{stamp(m.createdAt)}</span></div>
  <AttachmentStrip items={m.attachments}/>
  {editing?<form className="a-message-editor" onSubmit={submit}>
   <textarea autoFocus aria-label="Edit your message" value={text} data-action="view.update" onChange={e=>{setText(e.target.value);pendingText.current=e.target.value;patch({...edit,text:e.target.value})}} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();patch(null)}if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();e.currentTarget.form.requestSubmit()}}}/>
   <p className="a-caption">Continue from this edit in a new branch. Your original chat stays available. Earlier tool actions aren’t undone.</p>
   <div className="a-message-edit-actions"><button className="a-soft" type="button" disabled={saving} data-action="view.update" onClick={()=>patch(null)}>Cancel</button><button className="a-primary" type="submit" disabled={saving||blocked||!text.trim()} data-action="message.edit"><ArrowUp/>{saving?'Starting…':'Save & regenerate'}</button></div>
  </form>:m.role==='user'?<p>{m.text}</p>:<Markdown text={m.text|| (working?'…':'')}/>}
  {!editing&&<div className="a-message-actions">
   <button type="button" className="a-icon" title="Copy as Markdown" aria-label="Copy message as Markdown" data-action="message.copy" onClick={()=>act('message.copy',{sessionId:session.id,messageId:m.id})}>{copied?.status==='ready'?<Check/>:<Copy/>}</button>
   {m.role==='user'&&<button type="button" className="a-icon" title={blocked?'Wait for the current work to finish':'Edit message'} aria-label="Edit message" disabled={blocked} data-action="view.update" onClick={()=>patch({sessionId:session.id,messageId:m.id,text:m.text})}><Pencil/></button>}
   {forkTurn&&<ForkTurn session={session} turn={forkTurn} act={act} working={working}/>}
   {copied?.status==='ready'&&<span role="status" className="a-copy-result success">Copied Markdown</span>}
   {copied?.status==='error'&&<span role="alert" className="a-copy-result error"><AlertCircle/>{copied.message||'Could not copy'}</span>}
  </div>}
 </article>;
}
export function ForkTurn({session,turn,act,working}){
 return <button type="button" className="a-icon" title={working?'Wait for the current work to finish':'Fork a new chat from here'} aria-label={`Fork conversation after turn ${turn}`} disabled={working||session.configurationBusy} data-action="session.fork" onClick={()=>act('session.fork',{id:session.id,turn})}><GitBranch/></button>;
}
