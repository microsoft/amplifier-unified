import {DetailText,readDetail} from './conversation-detail';
import React,{useEffect,useRef,useState} from 'react';
import {Copy,Check,Pencil,GitBranch,ArrowUp,AlertCircle,RotateCcw,LoaderCircle} from 'lucide-react';
import {AttachmentStrip} from './chat-controls';

export function completedTurnEnds(session){
 const messages=session?.messages||[],ends=new Map();let turn=Number(session?.sharedHistoryUserTurnOffset)||0;
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
export function MessageEntry({message:m,session,state,act,stamp,working,forkTurn,retry,dispatch=act}){
 const [saving,setSaving]=useState(false),[localCopied,setLocalCopied]=useState(false),[detailError,setDetailError]=useState(''),edit=state.view?.messageEdit,editing=edit?.sessionId===session.id&&edit?.messageId===m.id;
 const [text,setText]=useState(editing?edit.text:''),pendingText=useRef(null);
 useEffect(()=>{if(!editing){pendingText.current=null;return}if(pendingText.current===null||edit.text===pendingText.current){setText(edit.text||'');pendingText.current=null}},[editing,edit?.text]);
 const copy=state.view?.messageCopy,copied=copy?.sessionId===session.id&&copy?.messageId===m.id?copy:null;
 const blocked=working||session.configurationBusy||session.workspaceAvailable===false||!!session.historyReadOnlyReason;
 const patch=value=>act('view.update',{patch:{messageEdit:value}});
 const localDelivery=m.localDelivery,delivery=localDelivery||(m.delivery?.status&&m.delivery.status!=='accepted'?m.delivery:null),deliveryPending=delivery?.status==='sending';
 const submit=async e=>{e.preventDefault();if(saving||blocked||!text.trim())return;setSaving(true);try{if(localDelivery)await retry(m,text);else await dispatch('message.edit',{sessionId:session.id,messageId:m.id,text,mode:edit?.fork?'fork':'current'})}catch(error){setDetailError(error.message)}finally{setSaving(false)}};
 if(m.observation)return <article className="a-message a-assistant" data-message-id={m.id}><details><summary>{m.observation.source==='local-job-recovery'?'Recovered work update':['amplifier-delegate','amplifier-child','amplifier-child-lifecycle'].includes(m.observation.source)?'Delegated work update':'Service observation'} · Details</summary><DetailText text={m.text} reference={m.textDetail} markdown/><button type="button" className="a-link" onClick={()=>act('message.copy',{sessionId:session.id,messageId:m.id})}>Copy observation</button></details></article>;
 return <article className={`a-message a-${m.role==='user'?'user':'assistant'}`} data-message-id={m.id}>
  <div className="a-msg-meta"><strong>{m.role==='user'?'You':m.role==='assistant'?'Amplifier':m.role}</strong><span>{m.via&&`via ${m.via} · `}{stamp(m.createdAt)}</span></div>
  <AttachmentStrip items={m.attachments}/>{detailError&&<p role="alert">{detailError}</p>}
  {editing?<form className="a-message-editor" onSubmit={submit}>
   <textarea autoFocus aria-label="Edit your message" value={text} data-action="view.update" onChange={e=>{setText(e.target.value);pendingText.current=e.target.value;patch({...edit,text:e.target.value})}} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();patch(null)}if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();e.currentTarget.form.requestSubmit()}}}/>
   <p className="a-caption">{localDelivery?'Update this unsent message and try again.':'Continue from this point. Later messages leave the active conversation; saved event history and earlier tool effects remain.'}</p>
   {!localDelivery&&<label className="a-inline-checkbox"><input type="checkbox" checked={!!edit?.fork} data-action="view.update" onChange={e=>patch({...edit,text,fork:e.target.checked})}/>Start a new conversation instead</label>}
   <div className="a-message-edit-actions"><button className="a-soft" type="button" disabled={saving} data-action="view.update" onClick={()=>patch(null)}>Cancel</button><button className="a-primary" type="submit" disabled={saving||blocked||!text.trim()} data-action="message.edit" data-operation-pending={saving||undefined} aria-busy={saving||undefined}><ArrowUp/>{saving?'Starting…':'Save & regenerate'}</button></div>
  </form>:<DetailText text={m.text|| (working?'…':'')} reference={m.textDetail} markdown={m.role!=='user'}/>}
  {!editing&&<div className="a-message-actions">
   <button type="button" className="a-icon" title="Copy as Markdown" aria-label="Copy message as Markdown" data-action="message.copy" onClick={async()=>{if(!localDelivery){await act('message.copy',{sessionId:session.id,messageId:m.id});return}try{await navigator.clipboard.writeText(m.text);setLocalCopied(true)}catch(error){setDetailError(error.message)}}}>{copied?.status==='ready'||localCopied?<Check/>:<Copy/>}</button>
   {m.role==='user'&&<button type="button" className="a-icon" title={session.historyReadOnlyReason|| (session.workspaceAvailable===false?'Workspace folder unavailable':blocked?'Wait for the current work to finish':'Edit message')} aria-label="Edit message" disabled={blocked||saving||!!localDelivery&&delivery.status!=='failed'} data-operation-pending={saving||undefined} aria-busy={saving||undefined} data-action="view.update" onClick={async()=>{setSaving(true);try{patch({sessionId:session.id,messageId:m.id,text:m.textDetail?await readDetail(m.textDetail):m.text,fork:false})}catch(e){setDetailError(e.message)}finally{setSaving(false)}}}><Pencil/></button>}
   {delivery&&<span className="a-message-delivery" role="status" title={delivery.error||undefined}>{deliveryPending?<><LoaderCircle className="a-progress-spinner"/>Sending…</>:<><AlertCircle/>{delivery.status==='failed'?'Not sent':'Delivery not confirmed'}{localDelivery&&<button type="button" className="a-link" data-action="conversation.send" onClick={()=>retry(m)} disabled={session.configurationBusy||session.ownership?.status==='blocked'}><RotateCcw/>{delivery.status==='failed'?'Retry':'Check delivery'}</button>}</>}</span>}
   {forkTurn&&<ForkTurn session={session} turn={forkTurn} act={act} working={working}/>}
   {copied?.status==='ready'&&<span role="status" className="a-copy-result success">Copied Markdown</span>}
   {copied?.status==='error'&&<span role="alert" className="a-copy-result error"><AlertCircle/>{copied.message||'Could not copy'}</span>}
  </div>}
 </article>;
}
export function ForkTurn({session,turn,act,working}){
 return <button type="button" className="a-icon" title={session.historyReadOnlyReason|| (session.workspaceAvailable===false?'Workspace folder unavailable':working?'Wait for the current work to finish':'Fork a new chat from here')} aria-label={`Fork conversation after turn ${turn}`} disabled={working||session.configurationBusy||session.workspaceAvailable===false||!!session.historyReadOnlyReason} data-action="session.fork" onClick={()=>act('session.fork',{id:session.id,turn})}><GitBranch/></button>;
}
