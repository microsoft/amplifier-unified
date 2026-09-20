import React,{useEffect,useRef,useState} from 'react';
import {X} from 'lucide-react';
import {readItems} from './attention';
import {isTopLevelChat} from './chat-navigation';

export function ConversationName({session,act}){
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
 return <form onSubmit={save} aria-busy={saving}><label htmlFor="session-title">Conversation name</label><input id="session-title" value={name} disabled={saving} required maxLength={200} onChange={event=>{dirty.current=true;setName(event.target.value);setError('')}}/><div className="a-dialog-actions"><button type="submit" className="a-soft" data-action="session.rename" disabled={saving||!name.trim()||name===session.title}>{saving?'Saving…':'Save name'}</button></div>{error&&<p role="alert" className="a-danger">{error}</p>}</form>;
}

export function ConversationError({state,session,act}){
 if(!session?.error)return null;
 const item=state.attention?.items?.find(row=>row.id==='session:'+session.id);
 if(item?.read)return null;
 return <div className="a-alert" role="alert"><span><strong>Amplifier needs a little help.</strong> {session.error}</span>{item&&<button type="button" aria-label="Dismiss conversation error" data-action="attention.read" onClick={()=>readItems(act,[item])}><X/></button>}</div>;
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
  {!isTopLevelChat(session)&&<option value="" disabled>Viewing subagent history</option>}
  {rows.map(row=><option key={row.id} value={row.id}>{row.title}{state.attention?.sessions?.[row.id]?' · Needs attention':['working','running','starting','stopping'].includes(row.status)?' · '+(row.status==='starting'?'Preparing':row.status==='stopping'?'Stopping':'Working'):''}</option>)}
  {choices.total>rows.length&&<option disabled>Find all chats in the workspace sidebar</option>}
 </select>;
}
