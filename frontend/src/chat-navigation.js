import {filterList} from './list-filter.js';

export const CHAT_PAGE_SIZE=100;
export function isTopLevelChat(chat){
 if(chat?.sessionKind==='root')return true;
 if(chat?.sessionKind==='worker')return false;
 if(chat?.forkTranscript?.length||chat?.editOrigin)return true;
 return !chat?.nativeParentId;
}
export function directSubagentChats(sessions=[],parent){
 if(!parent)return [];
 const nativeId=parent.nativeIdentity||parent.runtimeSessionId||parent.id;
 return sessions.filter(chat=>chat.id!==parent.id&&!isTopLevelChat(chat)&&(chat.parentId===parent.id||(
  chat.nativeParentId===nativeId&&(parent.nativeProject?chat.nativeProject===parent.nativeProject:parent.workspaceId?chat.workspaceId===parent.workspaceId:!!parent.workspace&&chat.workspace===parent.workspace)
 )));
}
export function workspaceChats(sessions=[],workspace){
 return sessions.filter(chat=>isTopLevelChat(chat)&&(!workspace||(chat.workspaceId?chat.workspaceId===workspace.id:!!workspace.path&&chat.workspace===workspace.path)));
}
export function chatPage(state,workspace){
 const view=state.view||{},filter=view.navFilter||'',selectedSessionId=state.selectedSessionId??null;
 const chats=filterList(workspaceChats(state.sessions,workspace),filter,chat=>[chat.title||'Untitled conversation',chat.description||'',chat.id]);
 const saved=view.navChatPage,scope={workspaceId:workspace?.id??null,filter,selectedSessionId};
 const matches=saved&&Object.entries(scope).every(([key,value])=>saved[key]===value);
 const inferred=Math.floor(Math.max(0,chats.findIndex(chat=>chat.id===selectedSessionId))/CHAT_PAGE_SIZE);
 const requested=matches&&Number.isSafeInteger(saved.index)?saved.index:inferred;
 const pages=Math.max(1,Math.ceil(chats.length/CHAT_PAGE_SIZE)),index=Math.max(0,Math.min(pages-1,requested));
 const start=index*CHAT_PAGE_SIZE,end=Math.min(chats.length,start+CHAT_PAGE_SIZE);
 return {items:chats.slice(start,end),total:chats.length,index,pages,start,end,scope};
}
export function headerChatChoices(state){
 if(!state)return {items:[],total:0};
 const workspace=state.workspaces?.find(row=>row.id===state.selectedWorkspaceId),selected=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const chats=workspaceChats(state.sessions,workspace),items=chats.slice(0,CHAT_PAGE_SIZE);
 if(selected&&isTopLevelChat(selected)&&!items.some(row=>row.id===selected.id))items.splice(Math.max(0,CHAT_PAGE_SIZE-1),1,selected);
 return {items,total:chats.length};
}
