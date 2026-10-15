import {conversationHistoryPending} from './history-window.js';
import React,{useLayoutEffect,useRef,useState,useEffect} from 'react';
import {SlidersHorizontal,X} from 'lucide-react';
import {ModelControl} from './chat-controls';
import {BundleControl} from './bundle-controls';
import {useComposerPopover} from './composer-popover';
import {useOutsideDismiss} from './use-outside-dismiss';

export function ComposerOptions({state,session,act,working}){
 const ref=useRef(null),[narrow,setNarrow]=useState(false),[open,setOpen]=useState(false);
 const {style,panelRef,renderPopover}=useComposerPopover(open,ref);
 const close=reason=>{setOpen(false);if(reason!=='outside')ref.current?.querySelector('button')?.focus()};
 useOutsideDismiss(open,ref,close,panelRef);
 useEffect(()=>{if(open&&style.visibility==='visible')(panelRef.current?.querySelector('select:not(:disabled)')||panelRef.current?.querySelector('button'))?.focus()},[open,style.visibility]);
 useLayoutEffect(()=>{
  const pane=ref.current?.closest('.a-conversation');if(!pane)return;
  const update=()=>setNarrow(pane.getBoundingClientRect().width<=520);
  update();const observer=new ResizeObserver(update);observer.observe(pane);return()=>observer.disconnect();
 },[]);
 useEffect(()=>{setOpen(false)},[narrow,session?.id,state.view?.panel]);
 const previousNarrow=useRef(narrow);
 useEffect(()=>{
  if(previousNarrow.current===narrow)return;previousNarrow.current=narrow;
  if(state.view?.composerModel?.open||state.view?.composerBundle?.open)act('view.update',{patch:{composerModel:{...state.view?.composerModel,open:false},composerBundle:{...state.view?.composerBundle,open:false}}});
 },[narrow]);
 const unavailable=session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||conversationHistoryPending(session);
 const props={state,session,act,working};
 return <div className="a-composer-options" ref={ref}>
  {narrow?<><button type="button" className="a-composer-options-trigger" aria-label="Chat options" aria-expanded={open} aria-haspopup="dialog" disabled={unavailable} onClick={()=>setOpen(!open)}><SlidersHorizontal/><span>Options</span></button>
   {open&&renderPopover(<section ref={panelRef} className="a-model-popover a-compact-popover a-composer-options-panel" style={style} role="dialog" aria-label="Chat options">
    <div className="a-settings-row"><strong>Chat options</strong><button type="button" className="a-icon" aria-label="Close chat options" onClick={close}><X/></button></div>
    <ModelControl {...props} embedded/><BundleControl {...props} compact embedded/>
    <button type="button" className="a-primary a-composer-options-done" onClick={close}>Done</button>
   </section>)}</>:<><ModelControl {...props}/><BundleControl {...props} compact/></>}
 </div>;
}
