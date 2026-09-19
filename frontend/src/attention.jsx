import React from 'react';
export function AttentionBadge({state,section,page,count:explicit,settings=false}){
 const count=explicit??(settings?state.attention?.settingsUnread:page?state.attention?.pages?.[page]:section?state.attention?.sections?.[section]:state.attention?.unread);
 return count>0?<span className="a-attention-badge" role="status" aria-label={`${count} unread items`}>{count}</span>:null;
}
export function AttentionReview({state,act,page}){
 const items=(state.attention?.items||[]).filter(i=>i.page===page&&!i.read);
 if(!items.length)return null;
 return <div className="a-attention-review"><div className="a-settings-row"><span>{items.length} new {items.length===1?'item':'items'} to review</span><button type="button" className="a-link" data-action="attention.read" onClick={()=>act('attention.read',{ids:items.map(i=>i.id)})}>Mark reviewed</button></div>{page!=='updates'&&items.map(item=><p key={item.id}><strong>{item.title}</strong>{item.detail&&<> · {item.detail}</>}</p>)}</div>;
}

export const readItems=(act,items)=>act('attention.read',{ids:items.map(i=>i.id),fingerprints:Object.fromEntries(items.map(i=>[i.id,i.fingerprint]))});
export function ActivityPanel({state,act}){
 const items=(state.attention?.items||[]).filter(i=>!i.read);
 async function open(item){
  if(item.sessionId){await act('session.select',{id:item.sessionId});await act('view.update',{patch:{panel:null}})}
  else if(item.requestId)await act('view.update',{patch:{panel:'feedback'}});
  else await act('view.update',{patch:{panel:'settings',settingsSection:item.section,settingsExpanded:[item.page]}});
 }
 return <section className="a-activity-inbox" aria-label="Activity to review"><p>Finished work and items that need your attention. Read status is shared with your agent and other devices.</p>{items.length?<><button className="a-link" type="button" data-action="attention.read" onClick={()=>readItems(act,items)}>Mark all reviewed</button>{items.map(item=><div className="a-attention-item" key={item.id}><button className="a-attention-open" type="button" data-action={item.sessionId?'session.select':'view.update'} onClick={()=>open(item)}><strong>{item.title}</strong><span>{item.label||item.detail}</span></button><button type="button" className="a-link" aria-label={`Mark reviewed: ${item.label||item.title}`} data-action="attention.read" onClick={()=>readItems(act,[item])}>Mark reviewed</button></div>)}</>:<p>All caught up.</p>}</section>;
}

// Viewing the end of a chat in a focused tab acknowledges that exact completion.
// Selection alone (by an agent, another device, or a hidden tab) never does.
export function completionToRead(state){
 return state?.attention?.items?.find(item=>item.id==='completion:'+state.selectedSessionId&&!item.read);
}
export function useReadCompletion(state,act,pane){
 const item=completionToRead(state);
 const blocked=!!state?.view?.panel||!!state?.view?.canvasFocused;
 React.useEffect(()=>{
  if(!item||blocked||!pane.current)return;
  const element=pane.current;let timer;
  const visible=()=>!document.hidden&&document.hasFocus()&&element.clientHeight>0&&element.scrollHeight-element.scrollTop-element.clientHeight<40;
  const check=()=>{clearTimeout(timer);if(visible())timer=setTimeout(()=>{if(visible())readItems(act,[item])},900)};
  element.addEventListener('scroll',check,{passive:true});document.addEventListener('visibilitychange',check);window.addEventListener('focus',check);window.addEventListener('blur',check);
  const resize=new ResizeObserver(check);resize.observe(element);for(const child of element.children)resize.observe(child);
  check();return()=>{clearTimeout(timer);resize.disconnect();element.removeEventListener('scroll',check);document.removeEventListener('visibilitychange',check);window.removeEventListener('focus',check);window.removeEventListener('blur',check)};
 },[item?.id,item?.fingerprint,blocked,act,pane]);
}
