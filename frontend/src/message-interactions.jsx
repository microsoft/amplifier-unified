import React,{useEffect,useRef,useState} from 'react';
import {Reply,Smile,X} from 'lucide-react';
import './message-interactions.css';

const reactions=[['👍','Thumbs up'],['❤️','Heart'],['😂','Laugh'],['🎉','Celebrate'],['😮','Surprised'],['😢','Sad'],['👎','Thumbs down'],['✅','Check']];
const author=quote=>quote.role==='user'?'You':'Amplifier';

export function QuoteCard({quote,sessionId,dispatch}){
 const [error,setError]=useState('');
 if(!quote)return null;
 const here=(quote.navigationSessionId||quote.sessionId)===sessionId;
 return <blockquote className="a-reply-quote"><button type="button" className="a-quoted-source" data-action="message.reveal" title={here?'Go to original message':'Open the original message in its conversation'} onClick={async()=>{setError('');try{const target=quote.navigationSessionId||quote.sessionId;await dispatch('message.reveal',{sessionId:target,messageId:quote.messageId})}catch(e){setError(e.message)}}}><strong>{author(quote)}</strong><span>{quote.excerpt}{quote.truncated?'…':''}</span></button>{!here&&<small>Quoted from the original conversation</small>}{error&&<small role="status">{error}</small>}</blockquote>;
}

export function ReplyPreview({quote,sessionId,dispatch}){
 const [error,setError]=useState('');
 if(!quote||quote.sessionId!==sessionId)return null;
 return <div className="a-reply-preview" aria-label="Quoted reply"><div><strong>Replying to {author(quote)}</strong><p>{quote.excerpt}{quote.truncated?'…':''}</p></div><button type="button" className="a-icon" aria-label="Remove quoted reply" data-action="message.replyClear" onClick={async()=>{setError('');try{await dispatch('message.replyClear',{sessionId,expectedReplyId:quote.id})}catch(e){setError(e.message)}}}><X/></button>{error&&<small role="alert">{error}</small>}</div>;
}

export function MessageInteractions({message,sessionId,dispatch}){
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const chosen=message.reactions||[];
 async function action(name,args){
  if(busy)return;setBusy(true);setError('');
  try{await dispatch(name,{sessionId,messageId:message.id,...args});if(name==='message.reply')document.querySelector('[aria-label="Message Amplifier"]')?.focus();}
  catch(e){setError(e.message)}finally{setBusy(false)}
 }
 return <><button type="button" className="a-icon" aria-label="Reply to message" title="Reply to message" data-action="message.reply" disabled={busy} onClick={()=>action('message.reply',{})}><Reply/></button><div className="a-reaction-control" onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setOpen(false)}} onKeyDown={e=>{if(e.key==='Escape'){setOpen(false);e.currentTarget.querySelector('button')?.focus()}}}>
  <button type="button" className="a-icon" aria-label="React to message" title="React to message" aria-expanded={open} onClick={()=>setOpen(!open)}><Smile/></button>
  {open&&<div className="a-reaction-picker" role="group" aria-label="Choose reaction">{reactions.map(([emoji,label])=><button key={emoji} type="button" aria-label={label} aria-pressed={chosen.includes(emoji)} disabled={busy} data-action="message.reaction" onClick={async()=>{await action('message.reaction',{emoji,present:!chosen.includes(emoji)});setOpen(false)}}>{emoji}</button>)}</div>}
 </div>{chosen.map(emoji=><button key={emoji} type="button" className="a-reaction-chip" aria-label={`Remove ${reactions.find(r=>r[0]===emoji)?.[1]||emoji} reaction`} aria-pressed="true" disabled={busy} data-action="message.reaction" onClick={()=>action('message.reaction',{emoji,present:false})}>{emoji}</button>)}{error&&<span className="a-message-interaction-error" role="alert">{error}</span>}</>;
}

export function MessageFocus({session,focus,detail}){
 const attempted=useRef(''),completed=useRef('');
 useEffect(()=>{
  if(!focus||focus.sessionId!==session?.id||detail.busy)return;
  const request=`${focus.sessionId}:${focus.revision}`;
  if(completed.current===request)return;
  const key=`${request}:${session.messageWindow?.offset??0}`;
  if(attempted.current===key)return;attempted.current=key;
  const node=[...document.querySelectorAll('[data-message-id]')].find(row=>row.dataset.messageId===focus.messageId);
  if(node){completed.current=request;node.tabIndex=-1;node.scrollIntoView({block:'center'});node.focus({preventScroll:true});return;}
  if(session.messageWindow?.offset>0)detail.earlier('messages');
 },[session?.id,session?.messages?.length,session?.messageWindow?.offset,focus?.revision,focus?.sessionId,detail.busy]);
 return null;
}
