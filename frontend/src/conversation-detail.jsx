import React,{useEffect,useRef,useState} from 'react';
import {request} from './api';
import {Markdown} from './markdown';
import {readDetail} from './detail-read';
export {readDetail} from './detail-read';
export function DetailText({text,reference,markdown=false,automatic=false,writingContext,fileContext,userContent=false}){
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
 return <div className="a-detail-text" ref={element}>{markdown?<Markdown text={value??text} userContent={userContent} writingContext={writingContext} fileContext={fileContext}/>:<p>{value??text}</p>}{reference&&value===null&&(automatic&&!error?<span className="a-caption" role="status">{busy?'Loading complete response…':'Complete response loads when visible.'}</span>:<button type="button" className="a-link" disabled={busy} onClick={load}>{busy?'Loading full text…':error?'Retry loading full text':'Show full text'}</button>)}{error&&<p role="alert">{error}</p>}</div>;
}
const unique=rows=>[...new Map(rows.map(row=>[row.id,row])).values()];
export function useConversationDetail(source,beforeApply,dispatch){
 const [saved,setSaved]=useState(null),[busy,setBusy]=useState(''),[error,setError]=useState(''),[focused,setFocused]=useState(null),[jumpTarget,setJumpTarget]=useState(null);
 const current=useRef(null);
 const sourceKey=JSON.stringify([source?.id,source?.sharedHistoryOffset]);
 if(current.current?.key!==sourceKey)current.current={key:sourceKey};
 const paging=useRef(null),[frozen,setFrozen]=useState(null);
 const originalSource=source,nativeWait=useRef(null);
 useEffect(()=>{
  const wait=nativeWait.current;if(!wait)return;
  if(originalSource?.id!==wait.id)wait.finish('The conversation changed.');
  else if(originalSource?.historyError)wait.finish(originalSource.historyError);
  else if(originalSource?.sharedHistoryOffset<wait.before)wait.finish();
 },[originalSource]);
 useEffect(()=>()=>{paging.current=null;nativeWait.current?.finish('The conversation changed.')},[]);
 if(frozen?.id===source?.id)source=frozen;
 const previous=useRef(source?.messageWindow?.total);
 useEffect(()=>{if(paging.current?.id===originalSource?.id)return;setSaved(null);setError('');setBusy('');setFrozen(null)},[originalSource?.id,originalSource?.sharedHistoryOffset]);
 useEffect(()=>{setFocused(null);setJumpTarget(null)},[originalSource?.id]);
 useEffect(()=>{if(source?.messageWindow?.total<previous.current)setSaved(null);previous.current=source?.messageWindow?.total},[source?.messageWindow?.total]);
 const extra=saved?.id===source?.id?saved:null;
 const projected=source?{...source,messages:source.messages.map(row=>({...row,...source.messageAnnotations?.[row.id]}))}:source;
 const regular=source&&extra?{...source,messages:unique([...extra.messages,...source.messages]).map(row=>({...row,...source.messageAnnotations?.[row.id]})),sharedHistoryUserTurnOffset:extra.userOffset??source.sharedHistoryUserTurnOffset,
  messageWindow:{...source.messageWindow,...(extra.messages.length?{offset:extra.messageOffset,before:extra.messages[0].id}:{} )},
  execution:{...source.execution,nodes:unique([...extra.nodes,...(source.execution?.nodes||[])]),turns:unique([...extra.turns,...(source.execution?.turns||[])]),segments:unique([...(extra.segments||[]),...(source.execution?.segments||[])])},
  executionWindow:{...source.executionWindow,...extra.groupWindow,...(extra.nodes.length?{offset:extra.nodeOffset,before:extra.nodes[0].id}:{})}}:projected;
 const focusedHere=focused?.sessionId===source?.id?focused:null;
 const session=focusedHere?{...projected,messages:focusedHere.messages,streaming:'',execution:focusedHere.execution,
  sharedHistoryOffset:0,navigationOffset:focusedHere.sourceOffset??focusedHere.offset,sharedHistoryUserTurnOffset:focusedHere.userOffset,messageWindow:undefined,executionWindow:undefined}:regular;
 async function around(messageId){
  if(paging.current||!originalSource)return;
  const id=originalSource.id,token={id};paging.current=token;setBusy('conversation');setError('');
  try{
   const result=await request('/api/conversation/navigation?'+new URLSearchParams({sessionId:id,messageId,window:'true'}));
   if(!Array.isArray(result?.messages)||!result.messages.some(row=>row.id===messageId))throw Error('This message could not be loaded. Try again.');
   if(originalId.current===id&&paging.current===token){setFocused({...result,sessionId:id});setJumpTarget({id:messageId})}
  }catch(error){if(originalId.current===id)setError(error.message)}
  finally{if(paging.current===token){paging.current=null;setBusy('')}}
 }
 function latest(jump=true){paging.current=null;setFocused(null);setSaved(null);setError('');setBusy('');setJumpTarget(jump?{id:'latest'}:null)}
 async function earlier(part){
  if(paging.current||!session)return;
  const id=session.id,token={id};paging.current=token;
  const key=current.current;
  setBusy('conversation');setError('');setFrozen(session);
  const parts=part?[part]:['messages','nodes'];
  const windows=parts.map(part=>({part,window:part==='messages'?session.messageWindow:session.executionWindow})).filter(({window})=>window?.offset>0);
  try{
   // Commit the paired pages once, after both requests succeed. Current text
   // stays mounted while loading so activity cannot precede its messages.
   const results=await Promise.all(windows.map(async ({part,window})=>({part,window,result:await request('/api/conversation/detail?'+new URLSearchParams({sessionId:id,part:window.part||part,before:window.before}))})));
   if(current.current!==key)return;
   if(!part&&!session.messageWindow?.offset&&session.sharedHistoryOffset>0&&dispatch){
    let finish;
    const loaded=new Promise(resolve=>{finish=resolve});
    const timer=setTimeout(()=>finish('Loading earlier conversation took too long. Try again.'),60000);
    nativeWait.current={id,before:session.sharedHistoryOffset,finish};
    try{
     await dispatch('session.history',{id,before:session.sharedHistoryOffset,limit:100});
     const failure=await loaded;if(failure)throw Error(failure);
    }finally{clearTimeout(timer);if(nativeWait.current?.finish===finish)nativeWait.current=null}
   }
   if(paging.current!==token||originalId.current!==id)return;
   beforeApply?.();
   setSaved(old=>{
    let previous=old?.id===id?old:{id,messages:[],nodes:[],turns:[]};
    for(const {part,window,result} of results)previous={...previous,[part]:unique([...(window.part==='groups'?[]:result.items),...previous[part]]),
     ...(part==='messages'?{messageOffset:result.offset,userOffset:result.userOffset}:{nodeOffset:result.offset,turns:unique([...(result.turns||[]),...previous.turns]),segments:unique([...(window.part==='groups'?result.items:result.segments||[]),...(previous.segments||[])]),groupWindow:window.part==='groups'?{offset:result.offset,before:result.before}:previous.groupWindow})};
    return previous;
   });
  }catch(e){if(originalId.current===id)setError(e.message)}finally{if(paging.current===token){paging.current=null;setFrozen(null);setBusy('')}}
 }
 const originalId=useRef(originalSource?.id);
 if(originalId.current!==originalSource?.id){originalId.current=originalSource?.id;paging.current=null}
 const hasEarlier=session?.messageWindow?.offset>0||session?.executionWindow?.offset>0||session?.sharedHistoryOffset>0;
 const controls=focusedHere?<><div className="a-history-window-controls"><button className="a-soft" disabled={!!busy||!focusedHere.before} onClick={()=>around(focusedHere.before)}>Earlier messages</button><button className="a-soft" disabled={!!busy||!focusedHere.after} onClick={()=>around(focusedHere.after)}>Later messages</button><button className="a-link" disabled={!!busy} onClick={latest}>Return to latest</button></div>{busy&&<p role="status">Loading conversation…</p>}{error&&<p role="alert">{error}</p>}</>:<>{hasEarlier&&<button className="a-soft" type="button" disabled={!!busy} onClick={()=>earlier()}>{busy?'Loading earlier conversation…':'Load earlier conversation'}</button>}{error&&<p role="alert">{error}</p>}</>;
 return {session,controls,earlier,busy,around,latest,focused:!!focusedHere,jumpRequest:jumpTarget};
}
