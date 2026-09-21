import React,{useEffect,useRef,useState} from 'react';
import {request} from './api';
import {Markdown} from './markdown';
import {readDetail} from './detail-read';
export {readDetail} from './detail-read';
export function DetailText({text,reference,markdown=false,automatic=false,writingContext}){
 const [loaded,setLoaded]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[visible,setVisible]=useState(false);
 const element=useRef(null),abort=useRef(null),key=reference?JSON.stringify(reference):null,current=useRef(key);current.current=key;
 const value=loaded?.key===key?loaded.value:null;
 useEffect(()=>{setLoaded(null);setError('');setBusy(false);return()=>abort.current?.abort()},[key]);
 useEffect(()=>{
  if(!automatic||!reference)return;
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setVisible(true);observer.disconnect()}},{rootMargin:'400px'});
  if(element.current)observer.observe(element.current);return()=>observer.disconnect();
 },[automatic,key]);
 async function load(){
  const captured=key,controller=new AbortController();abort.current?.abort();abort.current=controller;setBusy(true);setError('');
  try{const full=await readDetail(reference,request,controller.signal);if(!controller.signal.aborted&&current.current===captured)setLoaded({key:captured,value:full})}
  catch(e){if(!controller.signal.aborted&&current.current===captured)setError(e.message)}
  finally{if(!controller.signal.aborted&&current.current===captured)setBusy(false)}
 }
 useEffect(()=>{if(automatic&&visible&&reference)load()},[automatic,visible,key]);
 return <div className="a-detail-text" ref={element}>{markdown?<Markdown text={value??text} writingContext={writingContext}/>:<p>{value??text}</p>}{reference&&value===null&&(automatic&&!error?<span className="a-caption" role="status">{busy?'Loading complete response…':'Complete response loads when visible.'}</span>:<button type="button" className="a-link" disabled={busy} onClick={load}>{busy?'Loading full text…':error?'Retry loading full text':'Show full text'}</button>)}{error&&<p role="alert">{error}</p>}</div>;
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
  execution:{...source.execution,nodes:unique([...extra.nodes,...(source.execution?.nodes||[])]),turns:unique([...extra.turns,...(source.execution?.turns||[])]),segments:unique([...(extra.segments||[]),...(source.execution?.segments||[])])},
  executionWindow:{...source.executionWindow,...(extra.nodes.length?{offset:extra.nodeOffset,before:extra.nodes[0].id}:{})}}:source;
 async function earlier(part){
  if(busy||!session)return;const id=session.id,window=part==='messages'?session.messageWindow:session.executionWindow;
  setBusy(part);setError('');try{
   const result=await request('/api/conversation/detail?'+new URLSearchParams({sessionId:id,part,before:window.before}));
   if(current.current!==id)return;
   beforeApply?.();
   setSaved(old=>{const previous=old?.id===id?old:{id,messages:[],nodes:[],turns:[]};return {...previous,[part]:unique([...result.items,...previous[part]]),
    ...(part==='messages'?{messageOffset:result.offset,userOffset:result.userOffset}:{nodeOffset:result.offset,turns:unique([...result.turns,...previous.turns]),segments:unique([...(result.segments||[]),...(previous.segments||[])])})}});
  }catch(e){if(current.current===id)setError(e.message)}finally{if(current.current===id)setBusy('')}
 }
 const controls=<>{session?.messageWindow?.offset>0&&<button className="a-soft" type="button" disabled={!!busy} onClick={()=>earlier('messages')}>Load earlier messages</button>}{session?.executionWindow?.offset>0&&<button className="a-soft" type="button" disabled={!!busy} onClick={()=>earlier('nodes')}>Load earlier activity</button>}{busy&&<span role="status">Loading earlier {busy==='nodes'?'activity':'messages'}…</span>}{error&&<p role="alert">{error}</p>}</>;
 return {session,controls,earlier,busy};
}
