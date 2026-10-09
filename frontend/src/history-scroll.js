// Load a page only when the user moves toward older history, never on mount.
export function followEarlierHistory(pane,canLoad,load){
 let previous=pane.scrollTop,busy=false,disposed=false;
 const scroll=()=>{
  const top=pane.scrollTop,up=top<previous;previous=top;
  if(disposed||busy||!up||top>120||!canLoad())return;
  busy=true;Promise.resolve().then(load).finally(()=>{busy=false;previous=pane.scrollTop});
 };
 pane.addEventListener('scroll',scroll,{passive:true});
 return ()=>{disposed=true;pane.removeEventListener('scroll',scroll)};
}

// Recent grows downwards. A scroll event alone is not user intent: resize,
// browser anchoring, focus and row restoration all generate the same event.
export function followLaterHistory(pane,canLoad,load){
 let previous=pane.scrollTop,busy=false,disposed=false,armed=false,frame=null,touch=null,pointer=null;
 const document=pane.ownerDocument,window=document?.defaultView;
 const disarm=()=>{
  armed=false;previous=pane.scrollTop;
  if(frame!==null)window?.cancelAnimationFrame?.(frame);
  frame=null;
 };
 const reset=()=>{disarm();touch=null;pointer=null};
 const arm=()=>{
  disarm();
  if(disposed||busy||!canLoad())return;
  armed=true;
  // Unused input cannot remain permission for a later anchor/focus scroll.
  frame=window?.requestAnimationFrame?.(()=>{frame=null;disarm()})??null;
 };
 const input=event=>{
  if(event.type==='wheel'){
   if(!(event.deltaY>0)||event.ctrlKey||Math.abs(event.deltaX||0)>event.deltaY){reset();return}
  }
  if(event.type==='keydown'){
   if(!['ArrowDown','PageDown','End',' '].includes(event.key)
    ||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey
    ||event.target?.closest?.('input,textarea,select,[contenteditable="true"]')
    ||event.key===' '&&event.target!==pane){reset();return}
  }
  if(event.type==='touchstart'){
   reset();
   const first=event.touches?.length===1&&event.touches[0];
   if(first)touch={id:first.identifier,y:first.clientY};
   return;
  }
  if(event.type==='touchmove'){
   const next=event.touches?.length===1&&event.touches[0],last=touch;
   disarm();
   if(!next||!last||next.identifier!==last.id){touch=null;return}
   touch={id:next.identifier,y:next.clientY};
   if(!(next.clientY<last.y))return;
  }
  if(event.type==='pointerdown'){
   reset();
   const rect=pane.getBoundingClientRect?.();
   // Only the real scrollbar track/thumb, not blank content or a touch tap.
   if(event.target!==pane||event.pointerType!=='mouse'||event.button!==0||!rect
    ||pane.offsetWidth<=pane.clientWidth||event.clientX<rect.left+pane.clientWidth)return;
   pointer={id:event.pointerId,y:event.clientY};
   const thumb=pane.clientHeight*pane.clientHeight/pane.scrollHeight;
   const thumbTop=pane.scrollTop*(pane.clientHeight-thumb)/(pane.scrollHeight-pane.clientHeight);
   if(!(event.clientY-rect.top>thumbTop+thumb))return;
  }
  if(event.type==='pointermove'){
   const last=pointer;disarm();
   if(!last||event.pointerId!==last.id||!(event.buttons&1)){pointer=null;return}
   pointer={id:last.id,y:event.clientY};
   if(!(event.clientY>last.y))return;
  }
  arm();
 };
 const scroll=()=>{
  const top=pane.scrollTop,down=top>previous;previous=top;
  const intended=armed;disarm();
  if(disposed||busy||!intended||!down||pane.scrollHeight-pane.clientHeight-top>120||!canLoad())return;
  busy=true;Promise.resolve().then(()=>{if(!disposed&&canLoad())return load()})
   .catch(()=>{}).finally(()=>{busy=false;reset()});
 };
 const events=['wheel','touchstart','touchmove','keydown','pointerdown','pointermove'];
 const resets=['focusin','focusout','pointerdown','click','keydown','touchstart','visibilitychange'];
 const ends=['touchend','touchcancel'],pointerEnds=['pointerup','pointercancel'];
 const endPointer=()=>{disarm();pointer=null};
 for(const event of events)pane.addEventListener(event,input,{passive:true});
 for(const event of resets)document?.addEventListener?.(event,reset,true);
 for(const event of ends)document?.addEventListener?.(event,reset,true);
 for(const event of pointerEnds)document?.addEventListener?.(event,endPointer,true);
 pane.addEventListener('scroll',scroll,{passive:true});
 window?.addEventListener('resize',reset,{passive:true});
 window?.addEventListener('blur',reset);
 return {
  // Clear before and after the assignment, including asynchronously delivered
  // scroll events. Only a subsequent real input may re-arm this follower.
  suppress:move=>{reset();try{return move()}finally{reset()}},
  dispose:()=>{disposed=true;reset();pane.removeEventListener('scroll',scroll);window?.removeEventListener('resize',reset);
   window?.removeEventListener('blur',reset);
   for(const event of events)pane.removeEventListener(event,input);
   for(const event of resets)document?.removeEventListener?.(event,reset,true);
   for(const event of ends)document?.removeEventListener?.(event,reset,true);
   for(const event of pointerEnds)document?.removeEventListener?.(event,endPointer,true)}
 };
}
