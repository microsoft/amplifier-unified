import React,{useEffect,useState} from 'react';
import {ChevronRight,GitBranch,Copy,Check} from 'lucide-react';
import {DetailText,readDetail} from './conversation-detail';
import {QuoteCard,MessageInteractions} from './message-interactions';
import {AttachmentStrip} from './chat-controls';
import './peer-message.css';

// Host metadata determines presentation; prose resembling a peer envelope does not.
export const isPeerMessage=message=>message.role==='user'&&(message.inputOrigin==='peer'||message.via==='peer');

export function PeerMessage({message,session,state,act,dispatch,date,label,attribution,newest}){
 const [open,setOpen]=useState(false),[copying,setCopying]=useState(false),[copied,setCopied]=useState(false),[error,setError]=useState('');
 const focus=state.view?.messageFocus;
 const focused=focus?.sessionId===session.id&&[message.id,message.nativeMessageId].includes(focus.messageId);
 useEffect(()=>{if(focused)setOpen(true)},[focused,focus?.revision]);
 const receipt=state.view?.messageCopy;
 const copyResult=receipt?.sessionId===session.id&&receipt?.messageId===message.id?receipt:null;
 async function copy(){
  setCopying(true);setError('');
  try{if(!message.displayOnly)await act('message.copy',{sessionId:session.id,messageId:message.id});
   else{await navigator.clipboard.writeText(message.textDetail?await readDetail(message.textDetail):message.text||'');setCopied(true)}}
  catch(e){setError(e.message)}finally{setCopying(false)}
 }
 return <article className="a-message a-external-message a-peer-message" data-message-id={message.id} data-input-id={message.inputId||message.commandId} data-newest={newest||undefined} aria-label={label}>
  <button type="button" className="a-peer-summary" aria-expanded={open} onClick={()=>setOpen(value=>!value)}>
   <ChevronRight className={open?'open':''}/><GitBranch/>
   <span>{attribution?<span className="a-message-attribution">{attribution}</span>:'Message from another chat'}</span>
   {date&&<time className="a-message-date" aria-label={date.full} dateTime={date.iso}>{date.text}</time>}
  </button>
  {open&&<div className="a-execution-payload a-delegate-message a-peer-content">
   <header><span>Incoming message</span><button type="button" className="a-link" aria-label="Copy peer message as Markdown" disabled={copying} onClick={copy}>{copied||copyResult?.status==='ready'?<Check/>:<Copy/>}{copying?'Copying…':'Copy'}</button></header>
   <QuoteCard quote={message.replyTo} sessionId={session.id} dispatch={dispatch}/>
   <AttachmentStrip items={message.attachments} act={act} sessionId={session.id}/>
   <div className="a-delegate-markdown"><DetailText text={message.text||''} reference={message.textDetail} markdown userContent/></div>
   {!message.displayOnly&&<div className="a-message-actions"><MessageInteractions message={message} sessionId={session.id} dispatch={dispatch}/></div>}
   {(error||copyResult?.status==='error')&&<p role="alert">{error||copyResult.message||'Could not copy this message.'}</p>}
   {(copied||copyResult?.status==='ready')&&<p role="status" className="a-caption">Copied Markdown</p>}
  </div>}
 </article>;
}
