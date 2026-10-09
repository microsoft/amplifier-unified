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
 let previous=pane.scrollTop,busy=false,disposed=false,armed=false;
 const window=pane.ownerDocument?.defaultView;
 const input=event=>{
  if(event.type==='keydown'){
   if(!['ArrowDown','PageDown','End',' '].includes(event.key)
    ||event.target?.closest?.('input,textarea,select,[contenteditable="true"]')
    ||event.key===' '&&event.target!==pane)return;
  }
  if(event.type==='pointerdown'&&event.target!==pane)return;
  if(event.type==='pointermove'&&(!(event.buttons&1)||event.target!==pane))return;
  if(!disposed&&!busy&&canLoad()){armed=true;previous=pane.scrollTop}
 };
 const reset=()=>{armed=false;previous=pane.scrollTop};
 const scroll=()=>{
  const top=pane.scrollTop,down=top>previous;previous=top;
  const intended=armed;armed=false;
  if(disposed||busy||!intended||!down||pane.scrollHeight-pane.clientHeight-top>120||!canLoad())return;
  busy=true;Promise.resolve().then(()=>{if(!disposed&&canLoad())return load()})
   .catch(()=>{}).finally(()=>{busy=false;reset()});
 };
 const events=['wheel','touchmove','keydown','pointerdown','pointermove'];
 for(const event of events)pane.addEventListener(event,input,{passive:true});
 pane.addEventListener('scroll',scroll,{passive:true});
 window?.addEventListener('resize',reset,{passive:true});
 return {
  // Clear before and after the assignment, including asynchronously delivered
  // scroll events. Only a subsequent real input may re-arm this follower.
  suppress:move=>{reset();try{return move()}finally{reset()}},
  dispose:()=>{disposed=true;reset();pane.removeEventListener('scroll',scroll);window?.removeEventListener('resize',reset);
   for(const event of events)pane.removeEventListener(event,input)}
 };
}
