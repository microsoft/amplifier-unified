import {filterList} from './list-filter.js';
import {activityFor,sessionIdentity} from './navigation-presentation.js';

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
 const organization=state.conversationOrganization||{},archived=organization.archived||{},view=state.view||{},archive=view.navArchive||'active';
 const pinOrder=new Map((state.pinnedSessionIds||[]).map((id,index)=>[id,index]));
 return (state.sessions||[]).map((chat,position)=>({chat,position,workspace:byId.get(chat.workspaceId)||byPath.get(chat.workspace)}))
  .filter(row=>isTopLevelChat(row.chat)&&(row.workspace||row.chat.location?.kind==='managed')&&(mode==='all'||row.chat.location?.kind!=='managed'&&row.workspace?.id===workspace?.id))
  .filter(({chat})=>mode!=='all'||view.navLocationFilter!=='managed'||chat.location?.kind==='managed')
  .filter(({chat})=>(archive==='all'||(archive==='archived')===Object.hasOwn(archived,chat.id)))
  .map(row=>({...row.chat,workspace:row.workspace?.path||row.chat.workspace,workspaceId:row.chat.location?.kind==='managed'?null:row.workspace.id,workspaceName:row.chat.location?.kind==='managed'?'No workspace':row.workspace.name||'',workspaceLabel:row.chat.location?.kind==='managed'?'No workspace':row.workspace?.label,pinned:pinned.has(row.chat.id),recentActivityAt:recentActivity(row.chat),position:row.position}))
  .sort((a,b)=>Number(b.pinned)-Number(a.pinned)||(a.pinned&&state.pinOrderCustomized?pinOrder.get(a.id)-pinOrder.get(b.id):b.recentActivityAt-a.recentActivityAt)||a.position-b.position);
}
export function chatPage(state,workspace){
 const view=state.view||{},mode=view.navChatScope==='all'?'all':'workspace',filter=view.navFilter||'',selectedSessionId=state.selectedSessionId??null;
 workspace=visibleWorkspaces(state).find(row=>row.id===(workspace?.id??state.selectedWorkspaceId));
 const scope={mode,workspaceId:mode==='all'?null:workspace?.id??null,filter,selectedSessionId};
 if(mode==='all'&&view.navLocationFilter==='managed')scope.locationFilter='managed';
 const statusFilter=view.navStatusFilter||'all';
 if(statusFilter!=='all')scope.statusFilter=statusFilter;
 if(view.navArchive&&view.navArchive!=='active')scope.archive=view.navArchive;
 const projection=state.chatNavigation;
 const sameLocation=value=>(value?.locationFilter||'all')===(scope.locationFilter||'all');
 const saved=view.navChatPage,matches=saved&&sameLocation(saved)&&Object.entries(scope).every(([key,value])=>saved[key]===value);
 const requestedIndex=matches&&Number.isSafeInteger(saved.index)?Math.max(0,Math.min((projection?.pages||1)-1,saved.index)):null;
 if(projection?.scope&&sameLocation(projection.scope)&&Array.isArray(projection.items)&&Object.entries(scope).every(([key,value])=>projection.scope[key]===value)&&(requestedIndex===null||projection.index===requestedIndex))return projection;
 // A bounded snapshot cannot answer a different search or page locally. The
 // control updates immediately, while the shared action fetches its real rows.
 if(state.library?.bounded)return {items:[],total:0,index:0,pages:1,start:0,end:0,scope,pending:true};
 let chats=filterList(orderedChats(state,workspace,mode),filter,chat=>[chat.title||'Untitled conversation',chat.description||'',chat.id,sessionIdentity(chat),chat.workspace,chat.workspaceName]);
 const activityCounts={attention:0,working:0,unread:0,idle:0};
 chats=chats.map(chat=>({...chat,activity:activityFor(chat,state)}));
 for(const chat of chats)activityCounts[chat.activity.kind]++;
 if(statusFilter!=='all')chats=chats.filter(chat=>chat.activity.kind===statusFilter);
 const inferred=mode==='all'?0:Math.floor(Math.max(0,chats.findIndex(chat=>chat.id===selectedSessionId))/CHAT_PAGE_SIZE);
 const requested=matches&&Number.isSafeInteger(saved.index)?saved.index:inferred;
 const pages=Math.max(1,Math.ceil(chats.length/CHAT_PAGE_SIZE)),index=Math.max(0,Math.min(pages-1,requested));
 const start=index*CHAT_PAGE_SIZE,end=Math.min(chats.length,start+CHAT_PAGE_SIZE);
 return {items:chats.slice(start,end),total:chats.length,index,pages,start,end,scope,activityCounts};
}
export function headerChatChoices(state){
 if(!state)return {items:[],total:0};
 if(Array.isArray(state.headerChatNavigation?.items))return state.headerChatNavigation;
 const workspace=state.workspaces?.find(row=>row.id===state.selectedWorkspaceId),selected=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const chats=orderedChats({...state,view:{...state.view,navArchive:'active',navCollection:null}},workspace),items=chats.slice(0,CHAT_PAGE_SIZE);
 if(selected&&isTopLevelChat(selected)&&!items.some(row=>row.id===selected.id))items.splice(Math.max(0,CHAT_PAGE_SIZE-1),1,selected);
 return {items,total:chats.length};
}

export function subagentCount(state,parent){
 if(Number.isSafeInteger(parent?.subagentCount)&&parent.subagentCount>=0)return parent.subagentCount;
 const projection=state.subagentNavigation;
 if(parent&&projection?.scope?.sessionId===parent.id&&Number.isSafeInteger(projection.unfilteredTotal))return projection.unfilteredTotal;
 return directSubagentChats(state.sessions,parent).length;
}
export function subagentPage(state,parent){
 const saved=state.view?.subagentHistory||{},filter=saved.filter||'',scope={sessionId:parent?.id,filter},projection=state.subagentNavigation;
 const requested=Number.isSafeInteger(saved.index)?saved.index:0;
 if(projection?.scope&&Array.isArray(projection.items)&&Object.entries(scope).every(([key,value])=>projection.scope[key]===value)&&projection.index===Math.max(0,Math.min(projection.pages-1,requested)))return projection;
 if(state.library?.bounded)return {items:[],total:0,unfilteredTotal:subagentCount(state,parent),index:0,pages:1,start:0,end:0,scope,pending:true};
 const children=directSubagentChats(state.sessions,parent),rows=filterList(children,filter,row=>[row.title,row.description,row.id,row.nativeIdentity]);
 const pages=Math.max(1,Math.ceil(rows.length/50)),index=Math.max(0,Math.min(pages-1,requested)),start=index*50,end=Math.min(rows.length,start+50);
 return {items:rows.slice(start,end),total:rows.length,unfilteredTotal:children.length,index,pages,start,end,scope};
}
