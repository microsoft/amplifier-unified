import React,{useCallback,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {foldInterimRows,groupForMessage,interimResponse,interimOpen} from './interim-replies.js';
import {WorkTimeline} from './timeline';
import {MessageEntry} from './message-actions';
import {InlineMedia} from './inline-media';
import {ArtifactLinks} from './inline-artifacts';

function reading(pane){
 if(!pane)return false;
 const doc=pane.ownerDocument,selection=doc.getSelection();
 return pane.scrollHeight-pane.scrollTop-pane.clientHeight>80||
  pane.contains(doc.activeElement)||!!(selection&&!selection.isCollapsed&&pane.contains(selection.anchorNode));
}

// Only the transition is deferred. Scrolling through already folded history
// must not unfold everything. No reading state is sent to the runtime.
export function useInterimReplies(rows,data,enabled,sessionId,pane,expanded,act){
 const saved=useRef(null),[revision,refresh]=useState(0);
 if(saved.current?.sessionId!==sessionId)saved.current={sessionId,seen:new Set(),deferred:new Set()};
 const state=saved.current;
 const candidates=useMemo(()=>new Set(rows.flatMap(row=>{
  const response=row.kind==='message'&&interimResponse(row.message);
  return response?[response.generationId]:[];
 })),[rows]);
 useLayoutEffect(()=>{
  let changed=false;
  if(!enabled){if(state.seen.size||state.deferred.size){state.seen.clear();state.deferred.clear();changed=true}}
  else for(const id of candidates)if(!state.seen.has(id)){
   state.seen.add(id);if(reading(pane.current))state.deferred.add(id);changed=true;
  }
  if(changed)refresh(value=>value+1);
 },[candidates,enabled,state,pane]);
 useLayoutEffect(()=>{
  const element=pane.current;if(!element)return;
  const release=()=>{if(state.deferred.size&&!reading(element)){state.deferred.clear();refresh(value=>value+1)}};
  element.addEventListener('scroll',release);element.ownerDocument.addEventListener('selectionchange',release);
  element.addEventListener('focusout',release);
  return()=>{element.removeEventListener('scroll',release);element.ownerDocument.removeEventListener('selectionchange',release);element.removeEventListener('focusout',release)};
 },[state,pane,enabled]);
 // Until the first layout effect has checked reading intent, retain live rows.
 const deferred=new Set([...state.deferred,...[...candidates].filter(id=>!state.seen.has(id))]);
 const folded=useMemo(()=>foldInterimRows(rows,data,enabled,{deferred}),[rows,data,enabled,state,revision]);
 const ensureVisible=useCallback(async id=>{
  const row=groupForMessage(folded,id);if(!row)return false;
  const opened=new Set(expanded||[]);if(interimOpen(opened,row))return false;
  opened.delete('closed:'+row.id);opened.add(row.id);
  await act('view.update',{patch:{executionExpanded:[...opened]}});
  return true;
 },[folded,expanded,act]);
 return {rows:folded,ensureVisible};
}

export function ConversationWork({row,data,state,act,dispatch,session,working}){
 return <>
  <WorkTimeline row={row} items={row.items} data={data} state={state} act={act}
   renderMessage={message=><MessageEntry message={message} session={session} state={state} act={act} dispatch={dispatch} working={working} omitMedia/>}/>
  {row.interim&&row.items.filter(item=>item.message).map(({message})=>
   <div key={message.id} data-interim-output={message.id}>
    <InlineMedia sessionId={session.id} messageId={message.id} mediaRevision={session.mediaRevision||0} dispatch={dispatch}/>
    <ArtifactLinks state={state} message={message} act={act} dispatch={dispatch}/>
   </div>)}
 </>;
}