// Reading positions contain identifiers and offsets only, never message text.
const STORAGE_KEY='amplifier.chat-reading.v1';
export function readingPositions(storage){
 let rows={};try{rows=JSON.parse(storage?.getItem(STORAGE_KEY)||'{}')}catch{}
 if(!rows||typeof rows!=='object'||Array.isArray(rows))rows={};
 return {
  get(id){return rows[id]},
  set(id,value){if(!id)return;rows[id]={...value,seenAt:Date.now()};rows=Object.fromEntries(Object.entries(rows).sort((a,b)=>b[1].seenAt-a[1].seenAt).slice(0,100));try{storage?.setItem(STORAGE_KEY,JSON.stringify(rows))}catch{}},
 };
}
export function createChatScroll(pane,following={current:true},onAway=()=>{}){
 const view=pane.ownerDocument.defaultView;
 let storage;try{storage=view.sessionStorage}catch{}
 const positions=readingPositions(storage);
 let frame=0,disposed=false,sessionId=null,ready=false,restore=null,submitted=null,snapshot=null,saveTimer=0,initial=false,resuming=false;
 const messages=()=>[...pane.querySelectorAll('[data-message-id]')];
 const capture=()=>{
  const top=pane.getBoundingClientRect().top,rows=messages();
  const anchor=rows.find(node=>node.getBoundingClientRect().bottom>top)||rows.at(-1);
  snapshot={top:pane.scrollTop,messageId:anchor?.dataset.messageId,offset:anchor?anchor.getBoundingClientRect().top-top:0};
 };
 // A reply anchor is different from deliberate scrollback. Preserve that intent
 // across chat switches, even when the reply had not arrived before leaving.
 const persist=()=>{view.clearTimeout(saveTimer);if(ready&&snapshot)positions.set(sessionId,{...snapshot,submittedId:following.current?submitted:null})};
 const report=()=>{onAway(pane.scrollHeight-pane.scrollTop-pane.clientHeight>80);if(ready){capture();view.clearTimeout(saveTimer);saveTimer=view.setTimeout(persist,200)}};
 const flush=()=>{
  frame=0;if(disposed||!ready)return;
  if(restore){
   if(restore.messageId&&!messages().length)return;
   const anchor=messages().find(node=>node.dataset.messageId===restore.messageId);
   if(anchor)pane.scrollTop+=anchor.getBoundingClientRect().top-pane.getBoundingClientRect().top-restore.offset;
   else pane.scrollTop=Math.min(restore.top||0,pane.scrollHeight);
   restore=null;initial=false;
  }else if(submitted&&following.current){
   const anchor=messages().find(node=>node.dataset.inputId===submitted||node.dataset.messageId===submitted);
   if(anchor){resuming=false;const end=pane.scrollTop+anchor.getBoundingClientRect().bottom-pane.getBoundingClientRect().top;pane.scrollTop=Math.max(0,end-16)}
   else if(resuming&&messages().length){resuming=false;submitted=null;following.current=false;pane.scrollTop=pane.scrollHeight}
  }else if(initial){pane.scrollTop=pane.scrollHeight;initial=false;following.current=false}
  report();
 };
 const update=()=>{if(!frame&&!disposed)frame=view.requestAnimationFrame(flush)};
 const stop=()=>{submitted=null;following.current=false;restore=null;initial=false};
 const wheel=()=>stop();
 const keyDown=e=>{if(!e.target.closest('input,textarea,select,[contenteditable="true"]')&&['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key))stop()};
 const scroll=()=>{if(ready&&!restore)report()};
 const resize=new view.ResizeObserver(update),observed=new Set();
 const observeChildren=()=>{
  for(const child of observed)if(child.parentElement!==pane){resize.unobserve(child);observed.delete(child)}
  for(const child of pane.children)if(!observed.has(child)){resize.observe(child);observed.add(child)}
 };
 const mutations=new view.MutationObserver(()=>{observeChildren();update()});
 resize.observe(pane);observeChildren();mutations.observe(pane,{subtree:true,childList:true,characterData:true});
 pane.addEventListener('scroll',scroll,{passive:true});pane.addEventListener('wheel',wheel,{passive:true});pane.addEventListener('touchmove',stop,{passive:true});pane.addEventListener('pointerdown',stop);pane.addEventListener('keydown',keyDown);view.addEventListener('pagehide',persist);
 return {
  update,
  select(id,isReady=true){
   if(id!==sessionId){
    persist();const sendingNewChat=sessionId===null&&submitted;
    sessionId=id;snapshot=null;restore=positions.get(id)||null;
    submitted=sendingNewChat||(typeof restore?.submittedId==='string'?restore.submittedId:null);
    resuming=!!submitted&&!sendingNewChat;
    following.current=!!submitted;
    if(submitted)restore=null;
    initial=!restore&&!submitted;onAway(false);
   }
   ready=isReady;update();
  },
  submitted(id){submitted=id;resuming=false;restore=null;initial=false;following.current=true;update()},
  jump(id){stop();const node=messages().find(row=>row.dataset.messageId===id);if(node){node.tabIndex=-1;pane.scrollTop+=node.getBoundingClientRect().top-pane.getBoundingClientRect().top-16;node.focus({preventScroll:true});report()}},
  reveal(){stop();pane.scrollTop=pane.scrollHeight;report()},
  dispose(){persist();disposed=true;view.cancelAnimationFrame(frame);view.clearTimeout(saveTimer);resize.disconnect();mutations.disconnect();pane.removeEventListener('scroll',scroll);pane.removeEventListener('wheel',wheel);pane.removeEventListener('touchmove',stop);pane.removeEventListener('pointerdown',stop);pane.removeEventListener('keydown',keyDown);view.removeEventListener('pagehide',persist)},
 };
}
