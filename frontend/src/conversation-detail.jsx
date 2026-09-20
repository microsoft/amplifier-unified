import React,{useEffect,useRef,useState} from 'react';
import {request} from './api';
import {Markdown} from './markdown';
import {readDetail} from './detail-read';
export {readDetail} from './detail-read';
export function DetailText({text,reference,markdown=false}){
 const [value,setValue]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const key=reference?.digest;
 useEffect(()=>{setValue(null);setError('');setBusy(false)},[key]);
 const current=useRef(key);current.current=key;
 async function load(){const captured=key;setBusy(true);setError('');try{const full=await readDetail(reference);if(current.current===captured)setValue(full)}catch(e){if(current.current===captured)setError(e.message)}finally{if(current.current===captured)setBusy(false)}}
 return <>{markdown?<Markdown text={value??text}/>:<p>{value??text}</p>}{reference&&value===null&&<button type="button" className="a-link" disabled={busy} onClick={load}>{busy?'Loading full text…':'Show full text'}</button>}{error&&<p role="alert">{error}</p>}</>;
}
const unique=rows=>[...new Map(rows.map(row=>[row.id,row])).values()];
export function useConversationDetail(source,beforeApply){
 const [saved,setSaved]=useState(null),[busy,setBusy]=useState(''),[error,setError]=useState('');
 const current=useRef(source?.id);current.current=source?.id;
 const previous=useRef(source?.messageWindow?.total);
 useEffect(()=>{setSaved(null);setError('');setBusy('')},[source?.id,source?.sharedHistoryOffset]);
 useEffect(()=>{if(source?.messageWindow?.total<previous.current)setSaved(null);previous.current=source?.messageWindow?.total},[source?.messageWindow?.total]);
 const extra=saved?.id===source?.id?saved:null;
 const session=source&&extra?{...source,messages:unique([...extra.messages,...source.messages]),sharedHistoryUserTurnOffset:extra.userOffset??source.sharedHistoryUserTurnOffset,
  messageWindow:{...source.messageWindow,...(extra.messages.length?{offset:extra.messageOffset,before:extra.messages[0].id}:{} )},
  execution:{...source.execution,nodes:unique([...extra.nodes,...(source.execution?.nodes||[])]),turns:unique([...extra.turns,...(source.execution?.turns||[])])},
  executionWindow:{...source.executionWindow,...(extra.nodes.length?{offset:extra.nodeOffset,before:extra.nodes[0].id}:{})}}:source;
 async function earlier(part){
  if(busy||!session)return;const id=session.id,window=part==='messages'?session.messageWindow:session.executionWindow;
  setBusy(part);setError('');try{
   const result=await request('/api/conversation/detail?'+new URLSearchParams({sessionId:id,part,before:window.before}));
   if(current.current!==id)return;
   beforeApply?.();
   setSaved(old=>{const previous=old?.id===id?old:{id,messages:[],nodes:[],turns:[]};return {...previous,[part]:unique([...result.items,...previous[part]]),
    ...(part==='messages'?{messageOffset:result.offset,userOffset:result.userOffset}:{nodeOffset:result.offset,turns:unique([...result.turns,...previous.turns])})}});
  }catch(e){if(current.current===id)setError(e.message)}finally{if(current.current===id)setBusy('')}
 }
 const controls=<>{session?.messageWindow?.offset>0&&<button className="a-soft" type="button" disabled={!!busy} onClick={()=>earlier('messages')}>Load earlier messages</button>}{session?.executionWindow?.offset>0&&<button className="a-soft" type="button" disabled={!!busy} onClick={()=>earlier('nodes')}>Load earlier activity</button>}{busy&&<span role="status">Loading earlier {busy==='nodes'?'activity':'messages'}…</span>}{error&&<p role="alert">{error}</p>}</>;
 return {session,controls,earlier,busy};
}
