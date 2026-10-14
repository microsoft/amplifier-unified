import React,{useCallback,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {foldInterimRows,groupForMessage,interimResponse,interimOpen,retainInterimIds} from './interim-replies.js';
import {WorkTimeline} from './timeline';
import {MessageEntry} from './message-actions';
import {InlineMedia} from './inline-media';
import {ArtifactLinks} from './inline-artifacts';
import {inlineOwnership} from './conversation-navigation';

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
 if(!saved.current||saved.current.sessionId!==sessionId)saved.current={sessionId,seen:new Set(),deferred:new Set(),rows:[]};
 const state=saved.current;
 useLayoutEffect(()=>inlineOwnership.subscribe(()=>refresh(value=>value+1)),[]);
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
  let frame;
  const focusout=event=>{
   if(element.contains(event.relatedTarget))return;
   frame=requestAnimationFrame(release);
  };
  element.addEventListener('focusout',focusout);
  return()=>{cancelAnimationFrame(frame);element.removeEventListener('scroll',release);element.ownerDocument.removeEventListener('selectionchange',release);element.removeEventListener('focusout',focusout)};
 },[state,pane,enabled]);
 const folded=useMemo(()=>{
  // Until the first layout effect has checked reading intent, retain live rows.
  const deferred=new Set([...state.deferred,...[...candidates].filter(id=>!state.seen.has(id))]);
  // Pin only the grouping of an actual protected publication, not session
  // metadata. A final response or preference toggle must not move its DOM.
  const pinned=new Set();
  for(const row of rows){
   if(row.kind!=='message'||!inlineOwnership.protectsMessage(sessionId,row.message.id))continue;
   const previous=groupForMessage(state.rows,row.message.id),response=interimResponse(row.message);
   if(previous)pinned.add(previous.generationId);
   else if(response)deferred.add(response.generationId);
  }
  if(!enabled)for(const id of candidates)if(!pinned.has(id))deferred.add(id);
  for(const id of pinned)deferred.delete(id);
  return retainInterimIds(foldInterimRows(rows,data,enabled||pinned.size>0,{deferred}),state.rows);
 },[rows,data,enabled,state,revision,sessionId]);
 useLayoutEffect(()=>{state.rows=folded},[state,folded]);
 const ensureVisible=useCallback(async id=>{
  const row=groupForMessage(folded,id);if(!row)return false;
  const opened=new Set(expanded||[]);if(interimOpen(opened,row))return false;
  opened.delete('closed:'+row.id);opened.add(row.id);
  for(const item of row.items)if(item.message)opened.add('interim-message:'+item.message.id);
  await act('view.update',{patch:{executionExpanded:[...opened]}});
  return true;
 },[folded,expanded,act]);
 return {rows:folded,ensureVisible};
}

export function ConversationWork({row,data,state,act,dispatch,session,working}){
 const [,refresh]=useState(0),placement=useRef(null);
 useLayoutEffect(()=>inlineOwnership.subscribe(()=>refresh(value=>value+1)),[]);
 const requested=row.interim&&interimOpen(new Set(state.view?.executionExpanded||[]),row);
 const protectedOutput=row.interim&&row.items.some(item=>item.message&&inlineOwnership.protectsMessage(session.id,item.message.id));
 const open=protectedOutput&&placement.current!==null?placement.current:requested;
 useLayoutEffect(()=>{placement.current=open},[open]);
 const expanded=new Set(state.view?.executionExpanded||[]);
 if(row.interim&&open!==requested){
  expanded.delete(row.id);expanded.delete('closed:'+row.id);expanded.add(open?row.id:'closed:'+row.id);
 }
 const workState=open===requested?state:{...state,view:{...state.view,executionExpanded:[...expanded]}};
 const outputs=message=><div data-interim-output={message.id}>
  <InlineMedia sessionId={session.id} messageId={message.id} mediaRevision={session.mediaRevision||0} dispatch={dispatch} artifactRows={state.canvasArtifacts}/>
  <ArtifactLinks state={state} message={message} act={act} dispatch={dispatch}/>
 </div>;
 return <>
  <WorkTimeline row={row} items={row.items} data={data} state={workState} act={act}
   renderMessage={message=><><MessageEntry message={message} session={session} state={state} act={act} dispatch={dispatch} working={working} omitMedia/>{outputs(message)}</>}/>
  {row.interim&&!open&&row.items.filter(item=>item.message).map(({message})=>
   <React.Fragment key={message.id}>{outputs(message)}</React.Fragment>)}
 </>;
}