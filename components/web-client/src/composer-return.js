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

export function composerReturnBeforeInput(view,event,{editable=true,nativeReturnPending=false}={}){
 if(!editable||composingReturn(view,event)||!['insertParagraph','insertLineBreak'].includes(event.inputType))return false;
 // A DOM Return that ProseMirror deferred (iOS/Android) must finish through its
 // native edit/reconciliation path. A second custom edit here would race its
 // own deferred Enter fallback. That synthesized command is editing-only.
 if(nativeReturnPending)return false;
 // A non-cancelable edit must go through the browser/ProseMirror DOM observer.
 // Applying our own transaction as well would insert twice. Neither route sends.
 if(!event.cancelable)return false;
 event.preventDefault();
 composerEnter(view,{continueWriting:true});
 return true;
}

// Preserve ProseMirror's composition guards, DOM flush and deferred platform
// paths. Event identity only distinguishes observed DOM input from editor-made
// commands; it says nothing about physical versus virtual keyboards.
export function composerReturnEvents({editable=()=>true,mode=()=>'newline',onSend}={}){
 const observed=new WeakSet();
 let pending=false;
 return {
  observeKeyDown(view,event){
   pending=false;
   if(composingReturn(view,event))return true;
   observed.add(event);pending=event.key==='Enter';
   return false;
  },
  keyDown(view,event){
   const native=observed.delete(event);
   // A flush may synthesize an older edit before passing this DOM Return on to
   // platform handling. Do not let that separate command steal its native edit.
   if(native&&event.key==='Enter')pending=false;
   return composerReturnKeyDown(view,event,{editable:editable(),mode:native?mode():'newline',onSend});
  },
  beforeInput(view,event){
   const nativeReturnPending=pending;pending=false;
   return composerReturnBeforeInput(view,event,{editable:editable(),nativeReturnPending});
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