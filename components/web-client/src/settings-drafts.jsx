import React,{createContext,useCallback,useContext,useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';

const DraftContext=createContext(null);
const focusable='input,textarea,select,button,a[href],summary,[tabindex]';
function usableFocus(node){
 if(!node?.isConnected||!node.focus||node.ownerDocument?.visibilityState==='hidden'
  ||node.closest('[hidden],[inert],[aria-hidden="true"]')||node.matches(':disabled,[aria-disabled="true"]'))return false;
 const style=node.ownerDocument.defaultView.getComputedStyle(node);
 if(style.visibility==='hidden'||style.visibility==='collapse'||node.getClientRects().length===0)return false;
 for(let parent=node;parent;parent=parent.parentElement)if(node.ownerDocument.defaultView.getComputedStyle(parent).opacity==='0')return false;
 return true;
}
function focusScope(owner){return owner?.closest('.a-dialog')||owner}
function inOwner(node,owner){return !!node&&!!owner&&focusScope(owner)?.contains(node)}
function fallbackFocus(owner){
 return [...owner.querySelectorAll('h2[tabindex],h3[tabindex],h4[tabindex],'+focusable)].find(usableFocus);
}
// Workspace settings has no modal wrapper. Register its actual current section,
// not the app root or another retained/global Settings instance.
export function useSettingsDraftOwner(){
 const registry=useContext(DraftContext),node=useRef(null);
 return useCallback(value=>{
  if(registry?.ownerRef?.current===node.current)registry.ownerRef.current=null;
  node.current=value;if(value&&registry)registry.ownerRef.current=value;
 },[registry]);
}
export function useSettingsDraft(id,entry){
 const registry=useContext(DraftContext);
 // Navigation can run as soon as the edited field is committed. Publish its
 // guard before paint, rather than leaving a gap until passive effects run.
 useLayoutEffect(()=>{registry?.register(id,entry)});
 useLayoutEffect(()=>()=>registry?.remove(id),[registry,id]);
}

export function useSettingsDrafts({ownerRef:providedOwner,scope=null}={}){
 const entries=useRef(new Map()),waiting=useRef(null);
 const localOwner=useRef(null),ownerRef=providedOwner||localOwner,options=useRef(null),focusIntent=useRef(null),promptNode=useRef(null);
 const reviewedFocus=useRef(null);
 options.current={ownerRef,scope};
 const[count,setCount]=useState(0),[prompt,setPrompt]=useState(null),[,focusCommit]=useState(0);
 const recount=useCallback(()=>setCount([...entries.current.values()].filter(entry=>entry.dirty).length),[]);
 const register=useCallback((id,entry)=>{const changed=!!entries.current.get(id)?.dirty!==!!entry.dirty;entries.current.set(id,entry);if(changed)recount()},[recount]);
 const remove=useCallback(id=>{if(entries.current.delete(id))recount()},[recount]);
 const hasUnsaved=useCallback(()=>[...entries.current.values()].some(entry=>entry.dirty),[]);
 const confirmClose=useCallback(()=>{
  const dirty=[...entries.current.values()].filter(entry=>entry.dirty);
  if(!dirty.length)return Promise.resolve(true);
  if(waiting.current)return waiting.current.promise;
  const owner=options.current.ownerRef.current,doc=owner?.ownerDocument;
  const focus={owner,scope:options.current.scope,ownerScope:owner?.getAttribute('data-settings-focus-scope'),invoker:doc?.activeElement};
  focusIntent.current=null;reviewedFocus.current=null;
  let resolve;const promise=new Promise(done=>{resolve=done});waiting.current={promise,resolve,focus};setPrompt(dirty);return promise;
 },[]);
 const context=useMemo(()=>({register,remove,hasUnsaved,confirmClose,ownerRef}),[register,remove,hasUnsaved,confirmClose,ownerRef]);
 const settle=(accepted,reviewTarget=null,reviewResult=null)=>{
  const pending=waiting.current;waiting.current=null;
  // The portal's focused button must first leave the committed DOM. No timeout,
  // animation-frame guess or unmount cleanup should compete with navigation.
  const intent=!accepted&&pending?{...pending.focus,reviewTarget,dialog:promptNode.current,reviewPending:!!reviewResult?.then}:null;
  focusIntent.current=intent;
  // A returned navigation receipt bounds the only case that can span commits.
  // Observe its completion, not a timeout/frame heuristic; a newer focus or
  // prompt/owner lifetime invalidates this exact request before it can restore.
  if(intent?.reviewPending)Promise.resolve(reviewResult).catch(()=>{}).finally(()=>{
   if(focusIntent.current!==intent)return;
   intent.reviewPending=false;focusCommit(value=>value+1);
  });
  setPrompt(null);pending?.resolve(accepted);
 };
 useLayoutEffect(()=>{
  const intent=focusIntent.current;if(prompt||!intent)return;
  const {owner,invoker,reviewTarget}=intent;
  if(owner!==options.current.ownerRef.current||intent.scope!==options.current.scope
   ||intent.ownerScope!==owner?.getAttribute('data-settings-focus-scope')||!usableFocus(owner)){focusIntent.current=null;return}
  const active=owner.ownerDocument.activeElement;
  // A review/navigation callback may have deliberately focused the destination.
  // Never replace meaningful focus in this or another surface with stale input.
  if(active!==owner.ownerDocument.body&&active!==owner.ownerDocument.documentElement
   &&!intent.dialog?.contains(active)&&usableFocus(active)){
   reviewedFocus.current=reviewTarget&&inOwner(active,owner)?active:null;
   focusIntent.current=null;return;
  }
  let target;
  if(reviewTarget){
   const requested=reviewTarget();
   const candidates=Array.isArray(requested)?requested:[requested];
   target=candidates.find(node=>inOwner(node,owner)&&usableFocus(node));
   if(!target&&intent.reviewPending)return;
  }else if(inOwner(invoker,owner)&&usableFocus(invoker))target=invoker;
  target??=fallbackFocus(owner);
  focusIntent.current=null;
  target?.focus();
  reviewedFocus.current=reviewTarget&&owner.ownerDocument.activeElement===target?target:null;
 });
 useEffect(()=>{
  const intent=focusIntent.current;
  if(prompt||!intent?.reviewPending||!intent.owner)return;
  const doc=intent.owner.ownerDocument;
  const cancel=event=>{if(!intent.dialog?.contains(event.target)&&focusIntent.current===intent)focusIntent.current=null};
  doc.addEventListener('focusin',cancel);
  return()=>doc.removeEventListener('focusin',cancel);
 },[prompt]);
 const takeReviewFocus=useCallback(onReviewFocus=>{
  const target=reviewedFocus.current;reviewedFocus.current=null;
  const focused=inOwner(target,options.current.ownerRef.current)&&usableFocus(target)&&target.ownerDocument.activeElement===target;
  if(focused)onReviewFocus?.(target);
  return focused;
 },[]);
 useEffect(()=>{
  if(!count)return;
  const unload=event=>{event.preventDefault();event.returnValue=''};
  window.addEventListener('beforeunload',unload);return()=>window.removeEventListener('beforeunload',unload);
 },[count]);
 useEffect(()=>()=>{focusIntent.current=null;reviewedFocus.current=null;waiting.current?.resolve(false)},[]);
 const dialog=prompt&&<div className="a-settings-draft-overlay"><div ref={promptNode} className="a-settings-draft-confirm" role="alertdialog" aria-modal="true" aria-labelledby="settings-unsaved-title" onKeyDown={event=>{
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();settle(false)}
  if(event.key==='Tab'){const buttons=[...event.currentTarget.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);event.preventDefault();event.stopPropagation();buttons[(index+(event.shiftKey?-1:1)+buttons.length)%buttons.length]?.focus()}
 }}>
  <h3 id="settings-unsaved-title">Unsaved settings changes</h3>
  <p>Your changes have not been saved. Review them before closing Settings, or discard them.</p>
  <ul>{prompt.map(entry=><li key={entry.label}>{entry.label}</li>)}</ul>
  <div className="a-dialog-actions">
   <button type="button" className="a-primary" autoFocus onClick={()=>{const result=prompt[0].review();settle(false,prompt[0].reviewTarget||(()=>null),result)}}>Review and save</button>
   <button type="button" className="a-soft a-danger" onClick={()=>{
    // An in-flight or unconfirmed save can retain the only safe recovery token.
    // Such an editor may refuse discard until its exact outcome is known.
    const discarded=prompt.map(entry=>entry.discard());settle(discarded.every(value=>value!==false));
   }}>Discard changes</button>
   <button type="button" className="a-soft" onClick={()=>settle(false)}>Keep settings open</button>
  </div>
 </div></div>;
 return {context,confirmClose,hasUnsaved,count,dialog,takeReviewFocus};
}
export function SettingsDraftProvider({value,children}){return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>}

// Keep private editor memory when a shared action selects another connection.
// The same Settings dialog owns the user's discard/review decision.
export function useGuardedSettingsDraft(requested,identity,restore){
 const registry=useContext(DraftContext),[accepted,setAccepted]=useState(identity),[restoring,setRestoring]=useState(null),visible=useRef(requested),latestRestore=useRef(restore);
 latestRestore.current=restore;
 if(identity===accepted)visible.current=requested;
 useEffect(()=>{
  if(restoring||identity===accepted)return;
  if(!registry?.hasUnsaved()){setAccepted(identity);return;}
  let current=true;
  registry.confirmClose().then(discard=>{
   if(!current)return;
   if(discard){
    // Discard callbacks may first reset the old shared editor. Hold the
    // approved target through that intermediate frame and its restore receipt.
    setAccepted(identity);setRestoring({value:requested});visible.current=requested;
    Promise.resolve(latestRestore.current(requested)).catch(()=>{}).finally(()=>setRestoring(null));
   }else latestRestore.current(visible.current);
  });
  return()=>{current=false};
 },[identity,accepted,registry,restoring]);
 return restoring?.value??(identity===accepted?requested:visible.current);
}

// Shared view updates (including agent actions) must not unmount the only copy
// of a private field before the user chooses whether to discard it.
export function useGuardedSettingsPanel(requested,navigationRef,restore){
 const [visible,setVisible]=useState(requested),latestRestore=useRef(restore);
 latestRestore.current=restore;
 useEffect(()=>{
  if(requested===visible)return;
  const settings=panel=>panel==='settings'||panel==='appearance'||panel==='settings-canvas',navigation=navigationRef.current;
  if(!settings(visible)||!navigation?.hasUnsaved()) {setVisible(requested);return;}
  let current=true;
  navigation.confirmClose().then(accepted=>{if(!current)return;if(accepted)setVisible(requested);else latestRestore.current(visible)});
  return()=>{current=false};
 },[requested,visible,navigationRef]);
 return visible;
}

// Navigation from an agent or another client also preserves private editor
// memory until the user decides. Artifact panes still reset per conversation.
export function useGuardedSettingsSession(requested,navigationRef,restore){
 const [visible,setVisible]=useState(requested),latestRestore=useRef(restore);
 latestRestore.current=restore;
 useEffect(()=>{
  if(requested?.id===visible?.id)return;
  const navigation=navigationRef.current;
  if(!navigation?.hasUnsaved()){setVisible(requested);return;}
  let current=true;
  navigation.confirmClose().then(accepted=>{if(!current)return;if(accepted)setVisible(requested);else latestRestore.current(visible?.id)});
  return()=>{current=false};
 },[requested?.id,visible?.id,navigationRef]);
 return requested?.id===visible?.id?requested:visible;
}
