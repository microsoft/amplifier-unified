import React from 'react';
import {ChevronRight,GitBranch,Wrench,MessageCircle,Check,Circle,Loader,Square,AlertCircle,Layers} from 'lucide-react';
import {Markdown} from './markdown';
import {treeForTurn,usageLabel} from './timeline-data';

const liveStates=new Set(['running','working','starting','queued','pending','retrying','idle']);
function Usage({value}){const label=usageLabel(value);return label?<span className="a-execution-usage" title={label.title}>{label.text}</span>:null}
function Status({status}){return ['error','failed','cancelled','interrupted'].includes(status)?<AlertCircle className="a-execution-warning"/>:['complete','completed','success','done'].includes(status)?<Check className="a-execution-complete"/>:liveStates.has(status)?<span className="a-execution-dot"/>:<Circle/>}
function ExecutionNode({node,depth=0,ancestors=[],tree,expanded,toggle,act}){
  if(depth>20||ancestors.includes(node.id))return null;
  const children=tree.children.get(node.id)||[],isOpen=expanded.has(node.id),Icon=node.kind==='worker'?GitBranch:node.kind==='llm'?MessageCircle:Wrench;
  const summary=typeof node.summary==='string'?node.summary:typeof node.detail==='string'?node.detail:'';
  return <div className="a-execution-node" data-kind={node.kind}>
   <button className="a-execution-line" data-action="view.update" aria-expanded={isOpen} onClick={()=>toggle(node.id)}><ChevronRight className={`a-execution-chevron ${isOpen?'open':''}`}/><Icon/><span className="a-execution-label">{node.label||node.tool||node.model||node.kind}</span><span className="a-execution-phase">{node.phase||node.status}</span><Usage value={node.aggregateUsage||node.usage}/><Status status={node.status||node.phase}/></button>
   {isOpen&&<div className="a-execution-body">{summary&&<Markdown text={summary} className="a-execution-summary"/>}{node.model&&<small>{[node.provider,node.model].filter(Boolean).join(' · ')}</small>}{node.kind==='llm'&&!usageLabel(node.aggregateUsage||node.usage)&&<small>Usage has not been reported for this call.</small>}{node.kind==='worker'&&liveStates.has(node.status||node.phase)&&(node.workerId||node.sessionId)&&<button className="a-link a-danger" data-action="worker.stop" onClick={()=>act('worker.stop',{id:node.workerId||node.sessionId})}><Square/>Stop worker</button>}{children.length>0?<div className="a-execution-children">{children.map(child=><ExecutionNode key={child.id} node={child} depth={depth+1} ancestors={[...ancestors,node.id]} tree={tree} expanded={expanded} toggle={toggle} act={act}/>)}</div>:!summary&&node.kind!=='llm'&&<small>No additional details reported.</small>}</div>}
  </div>;
 }
export function TurnTimeline({data,turnId,state,act}){
 const turn=data.turns.find(turn=>turn.id===turnId)||{id:turnId,label:'Execution'};
 const tree=treeForTurn(data,turnId);
 if(!tree.roots.length)return null;
 const expanded=new Set(state.view?.executionExpanded||[]);
 const toggle=id=>{const next=new Set(expanded);next.has(id)?next.delete(id):next.add(id);act('view.update',{patch:{executionExpanded:[...next]}})};
 const summaryId=`turn:${turnId}`,open=expanded.has(summaryId);
 const toolCount=data.nodes.filter(node=>node.turnId===turnId&&node.kind==='tool').length;
 const workers=data.nodes.filter(node=>node.turnId===turnId&&node.kind==='worker').length;
 const label=(turnId==='observed-activity'?turn.label:null)||[toolCount?`${toolCount} ${toolCount===1?'tool call':'tool calls'}`:null,workers?`${workers} ${workers===1?'worker':'workers'}`:null].filter(Boolean).join(' · ')||'Execution details';

 return <section className="a-execution-turn" data-part="execution" aria-label="Execution details"><button className="a-execution-line a-execution-turn-line" data-action="view.update" aria-expanded={open} onClick={()=>toggle(summaryId)}><ChevronRight className={`a-execution-chevron ${open?'open':''}`}/><Layers/><span className="a-execution-label">{label}</span><Usage value={turn.aggregateUsage||turn.usage}/><Status status={turn.status||turn.phase}/></button>{open&&<div className="a-execution-roots">{tree.roots.map(node=><ExecutionNode key={node.id} node={node} tree={tree} expanded={expanded} toggle={toggle} act={act}/>)}</div>}</section>;
}
