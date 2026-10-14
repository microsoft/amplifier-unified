import {useEffect,useRef} from 'react';
import {waitForSnapshot} from './wait-for-snapshot';

const same=(a,b)=>Array.isArray(a)&&a.length===b.length&&a.every((id,index)=>id===b[index]);
const scope=state=>JSON.stringify([state.generation,state.selectedSessionId,state.selectedWorkspaceId,state.view?.navPinnedPage??0]);

// The command receipt commits canonical pin intent. Its independently produced
// query page may arrive later; keep ReorderList's preview and busy state until it
// reflects that intent. No command replay or extra read request is involved.
export function usePinOrderSettlement(host){
 const owner=useRef({alive:true,wait:null});
 useEffect(()=>{const current=owner.current;current.alive=true;return()=>{current.alive=false;current.wait?.abort()}},[host]);
 return async(dispatch,previous,expected,visible)=>{
  const initial=host?.getSnapshot(),scoped=initial?.navigationResources?.includes('pinnedNavigation');
  const identity=initial&&scope(initial),controller=new AbortController();owner.current.wait?.abort();owner.current.wait=controller;
  try{
   const response=await dispatch();
   const receipt=response?.result?.accepted!==undefined?response.result:response;
   if(receipt?.accepted===false)throw Error(receipt.error||'Could not reorder pins.');
   // Explicit/custom full snapshots do not use the asynchronous resource owner.
   if(!scoped)return;
   if(!owner.current.alive)controller.abort();
   await waitForSnapshot(host,current=>{
    if(scope(current)!==identity)throw Error('Pinned view changed before the order was displayed.');
    const canonical=current.pinnedItemIds||[];
    if(!same(canonical,previous)&&!same(canonical,expected))throw Error('Pins changed elsewhere. The current order is shown.');
    const pending=current.navigationResourcePending?.pinnedNavigation;
    if(pending?.status==='retry')throw Error('Pin order was saved, but its display could not be refreshed. Retry the pinned list.');
    return same(canonical,expected)&&!pending&&same(current.pinnedNavigation?.items?.map(item=>item.id),visible)?current:null;
   },{signal:controller.signal,timeoutMessage:'Pin order was saved, but its display has not arrived. Reconnect to read the current order.'});
  }finally{if(owner.current.wait===controller)owner.current.wait=null}
 };
}
