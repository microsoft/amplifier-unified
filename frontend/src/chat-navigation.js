import {filterList} from './list-filter.js';

export const CHAT_PAGE_SIZE=100;
export function visibleWorkspaces(state){
 return (state.workspaces||[]).filter(workspace=>workspace.available===true&&!!workspace.path);
}
export function workspaceLabel(workspace){
 const path=workspace.path||'',leaf=path.split(/[\\/]/).filter(Boolean).at(-1);
 return path+(workspace.name&&workspace.name!==leaf?` · ${workspace.name}`:'');
}
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
function recentActivity(chat){
 return [chat.recentActivityAt,chat.createdAt].find(value=>typeof value==='number'&&Number.isFinite(value)&&value>=0)??0;
}
function orderedChats(state,workspace,mode='workspace'){
 const workspaces=visibleWorkspaces(state),byId=new Map(workspaces.map(row=>[row.id,row])),byPath=new Map(workspaces.map(row=>[row.path,row]));
 const pinned=new Set(state.pinnedSessionIds||[]);
 return (state.sessions||[]).map((chat,position)=>({chat,position,workspace:byId.get(chat.workspaceId)||byPath.get(chat.workspace)}))
  .filter(row=>isTopLevelChat(row.chat)&&row.workspace&&(mode==='all'||row.workspace.id===workspace?.id))
  .map(row=>({...row.chat,workspace:row.workspace.path,workspaceId:row.workspace.id,workspaceName:row.workspace.name||'',pinned:pinned.has(row.chat.id),recentActivityAt:recentActivity(row.chat),position:row.position}))
  .sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b.recentActivityAt-a.recentActivityAt||a.position-b.position);
}
export function chatPage(state,workspace){
 const view=state.view||{},mode=view.navChatScope==='all'?'all':'workspace',filter=view.navFilter||'',selectedSessionId=state.selectedSessionId??null;
 workspace=visibleWorkspaces(state).find(row=>row.id===(workspace?.id??state.selectedWorkspaceId));
 const scope={mode,workspaceId:mode==='all'?null:workspace?.id??null,filter,selectedSessionId};
 const projection=state.chatNavigation;
 if(projection?.scope&&Array.isArray(projection.items)&&Object.entries(scope).every(([key,value])=>projection.scope[key]===value))return projection;
 const chats=filterList(orderedChats(state,workspace,mode),filter,chat=>[chat.title||'Untitled conversation',chat.description||'',chat.id,chat.workspace,chat.workspaceName]);
 const saved=view.navChatPage;
 const matches=saved&&Object.entries(scope).every(([key,value])=>saved[key]===value);
 const inferred=mode==='all'?0:Math.floor(Math.max(0,chats.findIndex(chat=>chat.id===selectedSessionId))/CHAT_PAGE_SIZE);
 const requested=matches&&Number.isSafeInteger(saved.index)?saved.index:inferred;
 const pages=Math.max(1,Math.ceil(chats.length/CHAT_PAGE_SIZE)),index=Math.max(0,Math.min(pages-1,requested));
 const start=index*CHAT_PAGE_SIZE,end=Math.min(chats.length,start+CHAT_PAGE_SIZE);
 return {items:chats.slice(start,end),total:chats.length,index,pages,start,end,scope};
}
export function headerChatChoices(state){
 if(!state)return {items:[],total:0};
 const workspace=state.workspaces?.find(row=>row.id===state.selectedWorkspaceId),selected=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const chats=orderedChats(state,workspace),items=chats.slice(0,CHAT_PAGE_SIZE);
 if(selected&&isTopLevelChat(selected)&&!items.some(row=>row.id===selected.id))items.splice(Math.max(0,CHAT_PAGE_SIZE-1),1,selected);
 return {items,total:chats.length};
}
