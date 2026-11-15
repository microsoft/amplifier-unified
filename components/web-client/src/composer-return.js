import {composerEnter} from './markdown-composer.js';

export const RETURN_KEY_STORAGE='amplifier.composer.returnKey.v1';
const validPreference=value=>value==='automatic'||value==='newline';

// A conservative platform default, not a soft/physical keyboard detector.
// Capture it when the editor mounts: resize, pointer use and peripheral changes
// must not silently change what Return does while someone is writing.
export function automaticReturnMode(environment=globalThis){
 try{
  const nav=environment.navigator,media=query=>environment.matchMedia(query)?.matches;
  if(!nav||nav.userAgentData?.mobile||/Android|iPhone|iPad|iPod|Mobile|Tablet|Silk|Kindle/i.test(nav.userAgent||''))return 'newline';
  if(nav.maxTouchPoints!==0||(nav.msMaxTouchPoints||0)>0)return 'newline';
  if(media('(any-pointer: coarse)')!==false||media('(pointer: fine)')!==true||media('(hover: hover)')!==true)return 'newline';
  return 'desktop-send';
 }catch{return 'newline';}
}

export const returnKeyMode=(preference,automatic)=>preference==='automatic'&&automatic==='desktop-send'?'desktop-send':'newline';
export const returnKeyHelp=mode=>mode==='desktop-send'
 ?'Enter to send. Shift+Enter or Ctrl+Enter adds a new line.'
 :'Return adds a new line. Use Send to send.';
export const composingReturn=(view,event)=>view.composing||event.isComposing||event.keyCode===229;

export function composerReturnKeyDown(view,event,{editable=true,mode='newline',onSend}={}){
 if(!editable||composingReturn(view,event)||event.key!=='Enter')return false;
 event.preventDefault();
 const continueWriting=mode!=='desktop-send'||event.shiftKey||event.ctrlKey;
 if(!composerEnter(view,{continueWriting,exit:event.altKey})&&!continueWriting&&!event.repeat)onSend?.();
 return true;
}

export function composerReturnBeforeInput(view,event,{editable=true}={}){
 if(!editable||composingReturn(view,event)||!['insertParagraph','insertLineBreak'].includes(event.inputType))return false;
 // A non-cancelable edit must go through the browser/ProseMirror DOM observer.
 // Applying our own transaction as well would insert twice. Neither route sends.
 if(!event.cancelable)return false;
 event.preventDefault();
 composerEnter(view,{continueWriting:true});
 return true;
}

// Compatibility boundary for pinned prosemirror-view 1.42.6:
// Enter13 is discarded on Android (even Ctrl+Enter); iOS schedules a fallback
// that can follow an already-applied inline code newline. Route an identity-tagged
// DOM relay with no legacy key code through PM's normal composition/near-
// composition guard and pending-edit flush, without arming those Enter13 paths.
// This is not keyboard detection. Ordinary newline Return leaves KEYDOWN native
// and owns the cancelable BEFOREINPUT edit, preserving mobile keyboard defaults.
// Requalify this boundary, including IME and rich undo, on PM upgrades.
export function composerReturnEvents({editable=()=>true,mode=()=>'newline',onSend}={}){
 const relays=new WeakMap();
 let pending=null;
 const relay=(view,source,run)=>{
  const Keyboard=view.dom.ownerDocument.defaultView.KeyboardEvent;
  const input=new Keyboard('keydown',{
   key:'Enter',code:'Enter',keyCode:0,which:0,bubbles:false,cancelable:true,
   shiftKey:!!source.shiftKey,ctrlKey:!!source.ctrlKey,altKey:!!source.altKey,
   metaKey:!!source.metaKey,repeat:!!source.repeat,
  });
  const context={run,admitted:false};relays.set(input,context);
  try{view.dom.dispatchEvent(input);}finally{relays.delete(input);}
  return context.admitted;
 };
 return {
  observeKeyDown(view,event){
   if(relays.has(event))return false; // Let PM run its own pre-command guards.
   pending=null;
   if(composingReturn(view,event)){pending='native';return true;}
   if(event.key!=='Enter')return false;
   if(!editable()||event.defaultPrevented||!event.cancelable){pending='native';return true;}
   pending='native';
   relay(view,event,()=>{
    // Chords may have no native edit at all. Desktop retains the existing
    // command policy. Plain safe Return instead waits for its editing event.
    if(mode()==='desktop-send'||event.ctrlKey||event.altKey||event.metaKey){
     composerReturnKeyDown(view,event,{editable:editable(),mode:mode(),onSend});
     pending='handled';
    }else pending='edit';
   });
   return true; // Do not also pass the original Enter13 to PM's scheduler.
  },
  keyDown(view,event){
   const context=relays.get(event);
   if(context){
    if(context.admitted)return true;
    context.admitted=true;context.run();
    return true; // Admission relay itself must not reach later keymaps.
   }
   // DOM reconciliation can synthesize Enter during the relay's flush. It
   // remains editing-only and cannot acquire authority from pending input.
   return composerReturnKeyDown(view,event,{editable:editable(),mode:'newline'});
  },
  beforeInput(view,event){
   if(!['insertParagraph','insertLineBreak'].includes(event.inputType))return false;
   const ownership=pending;pending=null;
   if(!editable()||composingReturn(view,event)||!event.cancelable||ownership==='native')return false;
   if(ownership==='handled'){event.preventDefault();return true;}
   if(ownership==='edit')return composerReturnBeforeInput(view,event);
   // Bare dictation/accessibility/browser edits receive the same PM guard and
   // flush, but never desktop Send semantics, even when no keydown preceded it.
   let handled=false;
   relay(view,event,()=>{handled=composerReturnBeforeInput(view,event);});
   return handled;
  },
  release(view,event){
   // Composition/autocorrection can interleave text input or key-up between a
   // rejected confirmation and its paragraph intent. Preserve that one-shot
   // native veto until the Return edit, next keydown or explicit focus/composition
   // boundary, rather than spending PM's near-composition guard a second time.
   if(pending!=='native'||event?.type==='blur'||event?.type==='compositionstart')pending=null;
   return false;
  },
 };
}

// Deliberately independent of host/shared appearance profiles and draft state.
// A failed write still applies the safer explicit choice for this tab, visibly.
export function createReturnKeyPreference({storage=()=>globalThis.localStorage,eventTarget=globalThis.window}={}){
 let snapshot;
 const listeners=new Set(),emit=()=>{for(const listener of listeners)listener();};
 const getSnapshot=()=>{
  if(!snapshot){
   try{
    const target=storage();
    if(!target)throw Error('Storage unavailable');
    const saved=target.getItem(RETURN_KEY_STORAGE);
    snapshot={value:validPreference(saved)?saved:'automatic',error:saved!=null&&!validPreference(saved)?'The saved Return key preference is invalid. Using Automatic. Choose a preference to replace it.':''};
   }catch{snapshot={value:'automatic',error:'This browser could not read the Return key preference. Using Automatic.'};}
  }
  return snapshot;
 };
 const changed=event=>{if(event.key===RETURN_KEY_STORAGE||event.key===null){snapshot=undefined;emit();}};
 return {
  getSnapshot,
  subscribe(listener){
   if(!listeners.size)eventTarget?.addEventListener('storage',changed);
   listeners.add(listener);
   return()=>{listeners.delete(listener);if(!listeners.size)eventTarget?.removeEventListener('storage',changed);};
  },
  set(value){
   if(!validPreference(value))return;
   let error='';
   try{
    const target=storage();
    if(!target)throw Error('Storage unavailable');
    target.setItem(RETURN_KEY_STORAGE,value);
    if(target.getItem(RETURN_KEY_STORAGE)!==value)throw Error('Storage not confirmed');
   }catch{error='The Return key preference applies in this tab only. This browser could not confirm saving it for next time.';}
   snapshot={value,error};emit();
  },
 };
}