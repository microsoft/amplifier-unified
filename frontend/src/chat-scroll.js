/** Follow new content until the reader scrolls back. Layout changes alone never
 * change that choice: composer, canvas, images and tool details can all resize. */
export function createChatScroll(pane,following={current:true}){
 const view=pane.ownerDocument.defaultView;
 const measure=()=>({top:pane.scrollTop,height:pane.scrollHeight,viewport:pane.clientHeight});
 let previous=measure(),frame=0,disposed=false,touchY=null;
 const flush=()=>{
  frame=0;
  if(disposed)return;
  if(following.current)pane.scrollTop=pane.scrollHeight;
  previous=measure();
 };
 const update=()=>{if(!frame&&!disposed)frame=view.requestAnimationFrame(flush)};
 const scroll=()=>{
  const next=measure(),sameLayout=next.height===previous.height&&next.viewport===previous.viewport;
  // Our own writes record their resulting position before the scroll event.
  // Browser scroll anchoring/clamping after a resize is not reader intent.
  if(sameLayout&&next.top<previous.top-1)following.current=false;
  else if(next.top>previous.top+1&&next.height-next.top-next.viewport<80)following.current=true;
  previous=next;
  if(following.current&&!sameLayout)update();
 };
 const wheel=e=>{if(e.deltaY<0)following.current=false};
 const touchStart=e=>{touchY=e.touches[0]?.clientY??null};
 const touchMove=e=>{const y=e.touches[0]?.clientY;if(touchY!==null&&y>touchY)following.current=false;touchY=y??null};
 const keyDown=e=>{
  if(e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
  if(['ArrowUp','PageUp','Home'].includes(e.key)||(e.key===' '&&e.shiftKey))following.current=false;
 };
 const resize=new view.ResizeObserver(update),observed=new Set();
 const observeChildren=()=>{
  for(const child of observed)if(child.parentElement!==pane){resize.unobserve(child);observed.delete(child)}
  for(const child of pane.children)if(!observed.has(child)){resize.observe(child);observed.add(child)}
 };
 const mutations=new view.MutationObserver(()=>{observeChildren();update()});
 resize.observe(pane);observeChildren();
 mutations.observe(pane,{subtree:true,childList:true,characterData:true});
 pane.addEventListener('scroll',scroll,{passive:true});
 pane.addEventListener('wheel',wheel,{passive:true});
 pane.addEventListener('touchstart',touchStart,{passive:true});
 pane.addEventListener('touchmove',touchMove,{passive:true});
 pane.addEventListener('keydown',keyDown);
 update();
 return {
  update,
  reveal(){following.current=true;view.cancelAnimationFrame(frame);flush();update()},
  dispose(){disposed=true;view.cancelAnimationFrame(frame);resize.disconnect();mutations.disconnect();pane.removeEventListener('scroll',scroll);pane.removeEventListener('wheel',wheel);pane.removeEventListener('touchstart',touchStart);pane.removeEventListener('touchmove',touchMove);pane.removeEventListener('keydown',keyDown)},
 };
}
