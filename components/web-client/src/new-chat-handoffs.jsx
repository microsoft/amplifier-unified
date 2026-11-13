import React,{useLayoutEffect,useRef,useState} from 'react';
import './new-chat-handoffs.css';

// Only phase-complete settled receipts disappear. Missing, contradictory or unfamiliar
// creation/delivery fields must not turn uncertainty into a success.
function isSettled(receipt){
 if(receipt.status==='accepted')return receipt.creation==='created'&&receipt.delivery==='accepted';
 if(receipt.status==='cancelled')return receipt.creation==='not_started'&&receipt.delivery==='not_started';
 return false;
}

// Omission is not a newer receipt. Retain only a settlement revision and
// safety flags in this source view, never the evicted brief or old feedback.
function omittedInteraction(interaction){
 if(Object.keys(interaction).every(key=>['settledRevision','unconfirmed','busy'].includes(key)))return interaction;
 const {settledRevision,unconfirmed,busy}=interaction;
 return {
  ...(settledRevision===undefined?{}:{settledRevision}),
  ...(unconfirmed?{unconfirmed:true}:{}),...(busy?{busy:true}:{}),
 };
}

function HandoffCard({receipt,interaction={},perform,open}){
 const {busy=false,error='',unconfirmed=false}=interaction;
 const pending=receipt.status==='pending';
 const model=[receipt.selection?.instance,receipt.selection?.model,receipt.selection?.effort].filter(Boolean).join(' · ')||'Workspace / bundle default';
 const review=<div className="a-handoff-review">
  <dl><dt>Chat</dt><dd>{receipt.title}</dd><dt>Folder</dt><dd>{receipt.workspace}</dd><dt>Bundle</dt><dd>{receipt.bundle}</dd><dt>Model</dt><dd>{model}</dd></dl>
  <p className="a-caption">Uses this folder’s configuration and file access.</p>
  <div className="a-handoff-brief"><strong>Opening message</strong><pre>{receipt.text}</pre></div>
 </div>;
 const check=(unconfirmed||!pending)&&<button type="button" className="a-link" data-action="session.handoff.read" disabled={busy} onClick={()=>perform('read',receipt)}>Check delivery</button>;
 const openChat=receipt.creation==='created'&&receipt.targetSessionId&&<button type="button" className="a-link" data-action="session.select" disabled={busy} onClick={()=>open(receipt)}>Open chat</button>;
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
 // Exact receipts can lead the stream. Keep their revision and unresolved
 // interaction feedback together; nothing is persisted or replayed.
 const [interactions,setInteractions]=useState({});
 const running=useRef(new Set()),focused=useRef(null);
 const mounted=useRef(false),projected=useRef(new Set());
 useLayoutEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useLayoutEffect(()=>{
  projected.current=new Set(rows.map(row=>row.requestId));
  const kept=Object.entries(interactions).flatMap(([id,interaction])=>{
   const retained=projected.current.has(id)||running.current.has(id)?interaction:omittedInteraction(interaction);
   return Object.keys(retained).length?[[id,retained]]:[];
  });
  // A no-op updater dispatched during layout can still cause a React commit.
  // Refresh the projection ref, but dispatch state only for actual compaction.
  if(kept.length!==Object.keys(interactions).length||kept.some(([id,interaction])=>interaction!==interactions[id]))setInteractions(Object.fromEntries(kept));
 },[rows,interactions]);
 const entries=rows.map(row=>{
  const interaction=interactions[row.requestId]||{},read=interaction.read;
  const receipt=read&&read.revision>row.revision?read:row;
  return {receipt,interaction};
 }).filter(({receipt,interaction})=>(!isSettled(receipt)&&!(receipt.revision<interaction.settledRevision))||interaction.busy||interaction.error||interaction.unconfirmed);
 const update=(id,patch)=>{
  if(!mounted.current)return;
  setInteractions(previous=>{
   const prior=previous[id]||{},next={...prior,...(typeof patch==='function'?patch(prior):patch)};
   // A late result must not restore an evicted receipt body. Preserve an already
   // observed settlement revision and the current uncertainty/duplicate fences.
   if(!projected.current.has(id)){
    const retained=omittedInteraction({...prior,busy:next.busy,unconfirmed:next.unconfirmed});
    if(Object.keys(retained).length)return {...previous,[id]:retained};
    if(!Object.hasOwn(previous,id))return previous;
    const kept={...previous};delete kept[id];return kept;
   }
   return {...previous,[id]:next};
  });
 };
 async function perform(action,receipt){
  const id=receipt.requestId;
  if(!mounted.current||!projected.current.has(id)||running.current.has(id))return;
  running.current.add(id);update(id,{busy:true});
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
    update(id,prior=>{
     const latest=prior.read?.revision>read.revision?prior.read:read;
     return {read:latest,settledRevision:isSettled(latest)?Math.max(prior.settledRevision??-1,latest.revision):prior.settledRevision,error:'',unconfirmed:false};
    });
   }
  }catch(e){update(id,{error:e.message||'The result could not be confirmed. Check delivery before taking another action.',...(['confirm','cancel'].includes(action)?{unconfirmed:true}:{})})}
  finally{running.current.delete(id);update(id,{busy:false})}
 }
 useLayoutEffect(()=>{
  const prior=focused.current;
  if(!prior||prior.isConnected)return;
  focused.current=null;
  // Continue at the existing composer only if removal left focus on the body,
  // never if the user moved elsewhere while the receipt was pending.
  const doc=prior.ownerDocument;
  if(doc.activeElement===doc.body)doc.querySelector('#amp-one [role="textbox"][aria-label="Message Amplifier"]')?.focus({preventScroll:true});
 });
 const render=entry=><HandoffCard key={entry.receipt.requestId} {...entry} perform={perform} open={receipt=>perform('open',receipt)}/>;
 if(!entries.length)return null;
 return <section className="a-handoffs" aria-label="New chat requests" onFocusCapture={event=>{
  focused.current=event.target;
 }} onBlurCapture={event=>{if(event.relatedTarget)focused.current=null}}>
  {entries.map(render)}
 </section>;
}

export function NewChatHandoffs({state,dispatch}){
 const rows=(state.handoffs||[]).filter(row=>row.sourceSessionId===state.selectedSessionId);
 return <HandoffList key={JSON.stringify([state.client?.historyScope,state.client?.hostInstanceId,state.client?.id,state.selectedSessionId])} rows={rows} dispatch={dispatch}/>;
}
