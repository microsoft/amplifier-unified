import React from 'react';
export function AttentionBadge({state={},section,page,count:explicit,settings=false}){
 const count=explicit??(settings?state.attention?.settingsUnread:page?state.attention?.pages?.[page]:section?state.attention?.sections?.[section]:state.attention?.unread);
 return count>0?<span className="a-attention-badge" role="status" aria-label={`${count} unread items`}>{count}</span>:null;
}
// A notice is read only after its rendered content is visible in the focused tab.
// Exact fingerprints prevent a late acknowledgement from hiding a newer notice.
export function useReadVisible(items,act,ref){
 const key=JSON.stringify(items.filter(i=>i&&!i.read).map(i=>[i.id,i.fingerprint]));
 React.useEffect(()=>{
  const pending=items.filter(i=>i&&!i.read),element=ref.current;if(!pending.length||!element)return;
  let timer,intersects=false,sent=false;
  const visible=()=>intersects&&!document.hidden&&document.hasFocus()&&element.getClientRects().length>0&&!element.closest('[inert], [hidden], details:not([open])');
  const check=()=>{clearTimeout(timer);if(!sent&&visible())timer=setTimeout(()=>{if(visible()){sent=true;Promise.resolve(readItems(act,pending)).catch(()=>{sent=false})}},750)};
  const observer=new IntersectionObserver(entries=>{intersects=entries[0].isIntersecting;check()});
  observer.observe(element);document.addEventListener('visibilitychange',check);window.addEventListener('focus',check);window.addEventListener('blur',check);
  return()=>{clearTimeout(timer);observer.disconnect();document.removeEventListener('visibilitychange',check);window.removeEventListener('focus',check);window.removeEventListener('blur',check)};
 },[key,act,ref]);
}
export function ReadWhenVisible({item,act,children,className}){
 const ref=React.useRef(null);useReadVisible(item?[item]:[],act,ref);
 return <div ref={ref} className={className}>{children}</div>;
}
export function AttentionReview({state,act,page,items:provided,showItems=false}){
 const items=provided||(state.attention?.items||[]).filter(i=>i.page===page&&!i.read);
 if(!items.length||page==='updates')return null;
 return <div className="a-attention-review">{items.map(item=><ReadWhenVisible key={item.id} item={item} act={act}><p><strong>{item.title}</strong>{item.detail&&<> · {item.detail}</>}</p></ReadWhenVisible>)}</div>;
}

export const readItems=(act,items)=>act('attention.read',{ids:items.map(i=>i.id),fingerprints:Object.fromEntries(items.map(i=>[i.id,i.fingerprint]))});
export function ActivityPanel({state,act}){
 const [showReviewed,setShowReviewed]=React.useState(false);
 const all=state.attention?.items||[],items=all.filter(i=>showReviewed||!i.read);
 async function open(item){
  if(item.sessionId){await act('session.select',{id:item.sessionId});await act('view.update',{patch:{panel:null}})}
  else if(item.requestId){await act('view.update',{patch:{panel:'feedback'}});await readItems(act,[item]);}
  else await act('view.update',{patch:{panel:'settings',settingsSection:item.section,settingsExpanded:[item.page]}});
 }
 return <section className="a-activity-inbox" aria-label="Activity to review"><p>Finished work and items that need your attention. Read status is shared with your agent and other devices.</p>{all.some(i=>i.read)&&<button className="a-link" type="button" onClick={()=>setShowReviewed(!showReviewed)}>{showReviewed?'Hide reviewed items':'Show reviewed items'}</button>}{items.length?<>{items.map(item=><div className="a-attention-item" key={item.id}><button className="a-attention-open" type="button" data-action={item.sessionId?'session.select':'view.update'} onClick={()=>open(item)}><strong>{item.title}</strong><span>{item.label||item.detail}</span></button></div>)}</>:<p>All caught up.</p>}</section>;
}

// Viewing the end of a chat in a focused tab acknowledges that exact completion.
// Selection alone (by an agent, another device, or a hidden tab) never does.
export function completionToRead(state){
 return state?.attention?.items?.find(item=>item.id==='completion:'+state.selectedSessionId&&!item.read);
}
export function useReadCompletion(state,act,pane,ready=true){
 const item=completionToRead(state);
 const blocked=!!state?.view?.panel||!!state?.view?.canvasFocused||!!state?.view?.navExpanded&&matchMedia('(max-width:760px)').matches||!!state?.canvas?.open&&matchMedia('(max-width:760px)').matches;
 React.useEffect(()=>{
  if(!ready||!item||blocked||!pane.current)return;
  const element=pane.current;let timer;
  const visible=()=>!document.hidden&&document.hasFocus()&&element.clientHeight>0&&getComputedStyle(element).visibility!=='hidden'&&!element.closest('[inert]')&&element.scrollHeight-element.scrollTop-element.clientHeight<40;
  const check=()=>{clearTimeout(timer);if(visible())timer=setTimeout(()=>{if(visible())readItems(act,[item])},900)};
  element.addEventListener('scroll',check,{passive:true});document.addEventListener('visibilitychange',check);window.addEventListener('focus',check);window.addEventListener('blur',check);
  const resize=new ResizeObserver(check);resize.observe(element);for(const child of element.children)resize.observe(child);
  check();return()=>{clearTimeout(timer);resize.disconnect();element.removeEventListener('scroll',check);document.removeEventListener('visibilitychange',check);window.removeEventListener('focus',check);window.removeEventListener('blur',check)};
 },[item?.id,item?.fingerprint,blocked,act,pane,ready]);
}
