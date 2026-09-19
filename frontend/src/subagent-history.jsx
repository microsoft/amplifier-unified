import React from 'react';
import {GitBranch,ChevronRight,ChevronLeft} from 'lucide-react';
import {directSubagentChats} from './chat-navigation';
import {filterList} from './list-filter';

const PAGE_SIZE=50;
export function SubagentHistoryButton({state,session,act}){
 const count=directSubagentChats(state.sessions,session).length;
 if(!count)return null;
 return <button type="button" className="a-soft" data-action="view.update" onClick={()=>act('view.update',{patch:{panel:'subagent-history',subagentHistory:{sessionId:session.id,filter:'',index:0}}})}><GitBranch/>Subagent history ({count})</button>;
}
export function SubagentHistory({state,session,act}){
 const saved=state.view?.subagentHistory||{},parent=state.sessions?.find(row=>row.id===saved.sessionId)||session;
 const filter=saved.filter||'',children=directSubagentChats(state.sessions,parent),rows=filterList(children,filter,row=>[row.title,row.description,row.id,row.nativeIdentity]);
 const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE)),index=Math.max(0,Math.min(pages-1,Number.isSafeInteger(saved.index)?saved.index:0));
 const update=patch=>act('view.update',{patch:{subagentHistory:{sessionId:parent?.id,filter,index,...patch}}});
 const choose=async id=>{const result=await act('session.select',{id});if(result&&result.accepted!==false)await act('view.update',{patch:{panel:null}})};
 return <div data-part="subagent-history">
  <p>Saved subagent conversations from {parent?.title||'this conversation'}. Open one to read its work.</p>
  <label htmlFor="subagent-history-filter">Find subagent conversations</label><input id="subagent-history-filter" type="search" value={filter} placeholder="Search or pattern, e.g. *research*" data-action="view.update" onChange={event=>update({filter:event.target.value,index:0})}/>
  <p className="a-caption" role="status">{rows.length} of {children.length} subagent conversations</p>
  <div className="a-subagent-history-list">{rows.slice(index*PAGE_SIZE,(index+1)*PAGE_SIZE).map(row=><button key={row.id} type="button" className="a-soft" data-action="session.select" onClick={()=>choose(row.id)} title={row.description||row.nativeIdentity||row.id}><GitBranch/><span>{row.title||'Subagent conversation'}</span><ChevronRight/></button>)}</div>
  {!rows.length&&<p>{filter?'No matching subagent conversations.':'No saved subagent conversations for this chat.'}</p>}
  {pages>1&&<div className="a-dialog-actions"><button type="button" className="a-soft" data-action="view.update" disabled={index===0} onClick={()=>update({index:index-1})}><ChevronLeft/>Previous subagents</button><span className="a-caption">{index*PAGE_SIZE+1}–{Math.min(rows.length,(index+1)*PAGE_SIZE)} of {rows.length}</span><button type="button" className="a-soft" data-action="view.update" disabled={index===pages-1} onClick={()=>update({index:index+1})}>More subagents<ChevronRight/></button></div>}
 </div>;
}
