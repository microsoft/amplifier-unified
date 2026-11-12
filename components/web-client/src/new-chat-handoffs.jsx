import React,{useLayoutEffect,useRef,useState} from 'react';
import './new-chat-handoffs.css';

// Only known settled delivery belongs in history. Contradictory or unfamiliar
// creation/delivery fields must not turn uncertainty into a success presentation.
function isSettled(receipt){
 if(receipt.status==='accepted')return [undefined,'created'].includes(receipt.creation)&&[undefined,'accepted'].includes(receipt.delivery);
 if(receipt.status==='cancelled')return [undefined,'not_started'].includes(receipt.creation)&&[undefined,'not_started'].includes(receipt.delivery);
 return false;
}

function HandoffCard({receipt,interaction={},compact,perform,open}){
 const {busy=false,error='',unconfirmed=false}=interaction;
 const pending=receipt.status==='pending';
 const model=[receipt.selection?.instance,receipt.selection?.model,receipt.selection?.effort].filter(Boolean).join(' · ')||'Workspace / bundle default';
 const review=<div className="a-handoff-review">
  <dl><dt>Chat</dt><dd>{receipt.title}</dd><dt>Folder</dt><dd>{receipt.workspace}</dd><dt>Bundle</dt><dd>{receipt.bundle}</dd><dt>Model</dt><dd>{model}</dd></dl>
  <p className="a-caption">Uses this folder’s configuration and file access.</p>
  <div className="a-handoff-brief"><strong>Opening message</strong><pre>{receipt.text}</pre></div>
 </div>;
 const check=(unconfirmed||!['pending','cancelled'].includes(receipt.status))&&<button type="button" className="a-link" data-action="session.handoff.read" disabled={busy} onClick={()=>perform('read',receipt)}>Check delivery</button>;
 const openChat=receipt.creation==='created'&&receipt.targetSessionId&&<button type="button" className="a-link" data-action="session.select" disabled={busy} onClick={()=>open(receipt)}>Open chat</button>;
 if(compact)return <article className="a-handoff a-handoff--settled" data-handoff-id={receipt.requestId} aria-label={receipt.title}>
  <div className="a-handoff-line"><strong>{receipt.title}</strong><span className="a-caption">{receipt.status==='accepted'?'Opening brief accepted':'Cancelled'}</span>{openChat}</div>
  <details><summary>Details</summary>{review}<p>{receipt.message}</p>{check}</details>
 </article>;
 return <article className="a-handoff" data-handoff-id={receipt.requestId} aria-label="Create a new chat">
  {pending?<><h3>Create a new chat</h3>{review}</>:<details><summary>{receipt.title}</summary>{review}</details>}
  <p role="status">{receipt.message}</p>
  <div className="a-dialog-actions">
   {pending&&!unconfirmed&&<><button type="button" className="a-primary" data-action="session.handoff.confirm" disabled={busy} onClick={()=>perform('confirm',receipt)}>Create and start</button><button type="button" className="a-soft" data-action="session.handoff.cancel" disabled={busy} onClick={()=>perform('cancel',receipt)}>Not now</button></>}
   {check}{openChat}
  </div>
  {error&&<p role="alert">{error}</p>}
 </article>;
}

function HandoffList({rows,dispatch}){
 // Transient reads and interaction feedback live together so regrouping a card
 // cannot discard an error or an unconfirmed action. Nothing is persisted/replayed.
 const [interactions,setInteractions]=useState({});
 const running=useRef(new Set()),section=useRef(null),historySummary=useRef(null),focused=useRef(null);
 const mounted=useRef(false),projected=useRef(new Set());
 useLayoutEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useLayoutEffect(()=>{
  projected.current=new Set(rows.map(row=>row.requestId));
  setInteractions(previous=>{
   const kept=Object.entries(previous).filter(([id])=>projected.current.has(id)||running.current.has(id));
   return kept.length===Object.keys(previous).length?previous:Object.fromEntries(kept);
  });
 },[rows]);
 const entries=rows.map(row=>{
  const interaction=interactions[row.requestId]||{},read=interaction.read;
  const receipt=read&&read.revision>row.revision?read:row;
  return {receipt,interaction,compact:isSettled(receipt)&&!interaction.error&&!interaction.unconfirmed};
 });
 const active=entries.filter(entry=>!entry.compact),past=entries.filter(entry=>entry.compact);
 const update=(id,patch)=>{
  if(!mounted.current)return;
  setInteractions(previous=>{
   // An evicted in-flight row stays fenced until settlement.
   // A late result must not restore its brief/error after it leaves the window.
   if(!projected.current.has(id)){
    if(running.current.has(id)||!Object.hasOwn(previous,id))return previous;
    const next={...previous};delete next[id];return next;
   }
   const prior=previous[id]||{};
   return {...previous,[id]:{...prior,...(typeof patch==='function'?patch(prior):patch)}};
  });
 };
 async function perform(action,receipt){
  const id=receipt.requestId;
  if(!mounted.current||!projected.current.has(id)||running.current.has(id))return;
  running.current.add(id);update(id,{busy:true,...(action==='confirm'?{unconfirmed:true}:{})});
  try{
   if(action==='open'){
    const response=await dispatch('session.select',{id:receipt.targetSessionId},{
     handoffIsCurrent:()=>mounted.current&&projected.current.has(id),
    });
    if(response?.accepted!==true)throw Error(response?.error||'Opening this chat could not be confirmed. Check the current selection before trying again.');
    update(id,{error:''});
   }
   else{
    const args={requestId:id,...(action==='confirm'?{reviewHash:receipt.reviewHash}:{})};
    const response=await dispatch('session.handoff.'+action,args,{feedback:false}),read=response?.result;
    if(response?.accepted!==true||read?.requestId!==id||read?.sourceSessionId!==receipt.sourceSessionId||!Number.isSafeInteger(read?.revision))
     throw Error('The result could not be confirmed. Check delivery before taking another action.');
    update(id,prior=>({read:prior.read?.revision>read.revision?prior.read:read,error:'',unconfirmed:false}));
   }
  }catch(e){update(id,{error:e.message||'The result could not be confirmed. Check delivery before taking another action.'})}
  finally{running.current.delete(id);update(id,{busy:false})}
 }
 useLayoutEffect(()=>{
  const prior=focused.current;
  if(!prior||prior.node.isConnected)return;
  focused.current=null;
  // Regrouping removes the old control. Restore keyboard position only if the
  // user has not moved focus elsewhere while the read/navigation was pending.
  if(prior.node.ownerDocument.activeElement!==prior.node.ownerDocument.body)return;
  const entry=entries.find(entry=>entry.receipt.requestId===prior.id);
  const card=[...section.current.querySelectorAll('[data-handoff-id]')].find(node=>node.dataset.handoffId===prior.id);
  (entry?.compact?historySummary.current:card?.querySelector('summary,button'))?.focus();
 });
 const render=entry=><HandoffCard key={entry.receipt.requestId} {...entry} perform={perform} open={receipt=>perform('open',receipt)}/>;
 return <section ref={section} className="a-handoffs" aria-label="New chat requests" onFocusCapture={event=>{
  const card=event.target.closest('[data-handoff-id]');
  focused.current=card?{id:card.dataset.handoffId,node:event.target}:null;
 }} onBlurCapture={event=>{if(event.relatedTarget)focused.current=null}}>
  {active.map(render)}
  {past.length>0&&<details className="a-handoffs-past">
   <summary ref={historySummary}>Past handoffs ({past.length})</summary>
   <p className="a-caption">Opening brief acceptance is not work completion.</p>
   <div className="a-handoffs-history">{past.map(render)}</div>
  </details>}
 </section>;
}

export function NewChatHandoffs({state,dispatch}){
 const rows=(state.handoffs||[]).filter(row=>row.sourceSessionId===state.selectedSessionId);
 if(!rows.length)return null;
 return <HandoffList key={JSON.stringify([state.client?.historyScope,state.client?.hostInstanceId,state.client?.id,state.selectedSessionId])} rows={rows} dispatch={dispatch}/>;
}
