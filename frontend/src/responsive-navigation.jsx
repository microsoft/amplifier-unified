import React,{createContext,useEffect,useRef,useState} from 'react';

export const NavigationOpen=createContext(true);
const inertOwners=new WeakMap();

export function useNarrowScreen(query='(max-width: 760px)'){
 const [matches,setMatches]=useState(()=>typeof matchMedia==='function'&&matchMedia(query).matches);
 useEffect(()=>{if(typeof matchMedia!=='function')return;const media=matchMedia(query),change=()=>setMatches(media.matches);change();media.addEventListener('change',change);return()=>media.removeEventListener('change',change)},[query]);
 return matches;
}
// Modal sheets and drawers retain their place in the app's DOM. Inert siblings
// prevent both keyboard and assistive-technology navigation into the background.
export function useModalFocus(ref,enabled,onClose){
 const close=useRef(onClose);close.current=onClose;
 useEffect(()=>{
  const panel=ref.current;if(!enabled||!panel)return;
  const previous=document.activeElement,hidden=[];
  let branch=panel;
  while(branch?.parentElement&&branch.id!=='amp-one'){
   for(const sibling of branch.parentElement.children){if(sibling===branch||sibling.tagName==='STYLE'||sibling.matches('.a-navigation-scrim,.a-details-scrim,.a-toolbar-scrim'))continue;const held=inertOwners.get(sibling)||{count:0,original:sibling.inert};held.count++;inertOwners.set(sibling,held);hidden.push(sibling);sibling.inert=true}
   branch=branch.parentElement;
  }
  const candidates=()=>[...panel.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]')].filter(el=>el.getClientRects().length&&!el.closest('[inert]'));
  candidates()[0]?.focus({preventScroll:true});
  const key=event=>{
   if(event.defaultPrevented)return;
   if(event.key==='Escape'){event.preventDefault();close.current();return}
   if(event.key!=='Tab')return;
   const list=candidates(),first=list[0],last=list.at(-1);
   if(!first){event.preventDefault();return}
   if(event.shiftKey&&(document.activeElement===first||!panel.contains(document.activeElement))){event.preventDefault();last.focus()}
   else if(!event.shiftKey&&(document.activeElement===last||!panel.contains(document.activeElement))){event.preventDefault();first.focus()}
  };
  document.addEventListener('keydown',key);
  return()=>{document.removeEventListener('keydown',key);for(const el of hidden){const held=inertOwners.get(el);if(held&&!--held.count){el.inert=held.original;inertOwners.delete(el)}}if(previous?.isConnected&&!previous.closest('[inert]'))previous.focus?.({preventScroll:true})};
 },[enabled,ref]);
}
export function MobileChatTitle({state,session}){
 const workspace=state.workspaces?.find(row=>row.id===state.selectedWorkspaceId);
 const path=session?.workspace||workspace?.path||'',parts=path.split(/[\\/]/).filter(Boolean);
 const label=workspace?.customName||workspace?.name||parts.at(-1);
 return <div className="a-mobile-chat-title"><strong title={session?.title||'New chat'}>{session?.title||'New chat'}</strong>{label&&<small title={path}>{label}{parts.length>1&&` · ${parts.at(-2)}`}</small>}</div>;
}
