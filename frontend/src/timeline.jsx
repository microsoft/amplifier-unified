import {DetailText} from './conversation-detail';
import {hostNow} from './api';
import React,{useEffect,useState} from 'react';
import {ChevronRight,GitBranch,Wrench,MessageCircle,Check,Circle,Square,AlertCircle,Terminal,FileText,FilePenLine,ListChecks} from 'lucide-react';
import {treeForTurn,usageLabel,isRunning,elapsedLabel} from './timeline-data';
import {actionContent,cleanSummary} from './execution-content.js';
import {useExecutionField,ToolContent,ExecutionBlock} from './execution-content.jsx';
import './execution-content.css';

function Usage({value,pending=false}){const label=usageLabel(value,{pending});return label?<span className="a-execution-usage" title={label.title}>{label.text}</span>:null}
function Status({status}){return ['error','failed','cancelled','interrupted'].includes(status)?<AlertCircle className="a-execution-warning" aria-label={status}/>:['complete','completed','success','done'].includes(status)?<Check className="a-execution-complete" aria-label="Completed"/>:isRunning({phase:status})?<span className="a-execution-dot" aria-label={status}/>:<Circle aria-label={status||'Unknown status'}/>}
const stamp=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString():null;
function Timing({record,now}){
 const start=stamp(record.startedAt),end=stamp(record.endedAt),elapsed=elapsedLabel(record,now);
 return start||end?<dl className="a-execution-timing">{start&&<><dt>Started</dt><dd>{start}</dd></>}{end&&<><dt>Ended</dt><dd>{end}</dd></>}{elapsed&&<><dt>Elapsed</dt><dd>{elapsed}</dd></>}</dl>:null;
}
function ExecutionNode({node,depth=0,ancestors=[],tree,expanded,toggle,act,now}){
 const isOpen=expanded.has(node.id),tool=node.kind==='tool';
 const input=useExecutionField(node,'input',isOpen&&tool),output=useExecutionField(node,'output',isOpen&&tool),error=useExecutionField(node,'error',isOpen&&tool);
 if(depth>20||ancestors.includes(node.id))return null;
 const children=tree.children.get(node.id)||[],action=tool?actionContent(node,input.value,output.value):null;
 const Icon=node.kind==='worker'?GitBranch:node.kind==='llm'?MessageCircle:({command:Terminal,read:FileText,write:FilePenLine,patch:FilePenLine,tasks:ListChecks,delegate:GitBranch}[action?.kind]||Wrench);
 const summary=cleanSummary(node.summary||node.detail),phase=node.status||node.phase,elapsed=elapsedLabel(node,now),usage=node.aggregateUsage||node.usage;
 const preview=tool?action.preview:node.kind==='llm'?[node.provider,node.model].filter(Boolean).join(' · '):summary.replace(/\s+/g,' ').slice(0,180),label=action?.title||node.label||node.model||node.kind;
 return <div className="a-execution-node" data-kind={node.kind} data-node-id={node.id} data-action-kind={action?.kind}>
  <button className="a-execution-line" data-action="view.update" aria-expanded={isOpen} onClick={()=>toggle(node.id)}><ChevronRight className={`a-execution-chevron ${isOpen?'open':''}`}/><Icon/><span className="a-execution-label"><span>{label}{action?.target&&<> <code>{action.target}</code></>}{action?.kind==='patch'&&<span className="a-execution-counts"><span>+{action.added}</span><span>−{action.removed}</span></span>}</span>{preview&&!isOpen&&<small title={preview}>{preview}</small>}</span><span className="a-execution-phase">{isRunning(node)||['error','failed','cancelled','interrupted'].includes(phase)?`${phase}${elapsed?' · ':''}`:''}{elapsed}</span><Usage value={usage} pending={node.kind==='llm'&&isRunning(node)}/><Status status={phase}/></button>
  {isOpen&&<div className="a-execution-body">
   {tool?<ToolContent node={node} action={action} input={input} output={output} error={error}/>:summary&&<DetailText text={summary} reference={node.summaryDetail||node.detailDetail} markdown/>}
   <details className="a-execution-metadata"><summary>Details</summary>
    {node.model&&<small>{[node.provider,node.model].filter(Boolean).join(' · ')}</small>}
    {tool&&<small>Tool: {node.tool||node.label}</small>}
    <Timing record={node} now={now}/>
    {node.toolCallId&&<small className="a-execution-id">Call: {node.toolCallId}</small>}
    {usageLabel(usage,{pending:node.kind==='llm'&&isRunning(node)})&&<p className="a-caption">{usageLabel(usage,{pending:node.kind==='llm'&&isRunning(node)}).title}</p>}
    {tool&&action.kind!=='generic'&&<details className="a-execution-raw"><summary>Full input and result</summary><ExecutionBlock label="Input" text={input.value} loading={input.incomplete}/><ExecutionBlock label="Result" text={output.value} loading={output.incomplete}/></details>}
   </details>
   {node.kind==='worker'&&isRunning(node)&&(node.workerId||node.sessionId)&&<button className="a-link a-danger" data-action="worker.stop" onClick={()=>act('worker.stop',{id:node.workerId||node.sessionId})}><Square/>Stop worker</button>}
   {children.length>0&&<div className="a-execution-children">{children.map(child=><ExecutionNode key={child.id} node={child} depth={depth+1} ancestors={[...ancestors,node.id]} tree={tree} expanded={expanded} toggle={toggle} act={act} now={now}/>)}</div>}
  </div>}
 </div>;
}
export function TurnTimeline({data,turnId,state,act}){
 const turn=data.turns.find(turn=>turn.id===turnId)||{id:turnId,label:'Execution'};
 const tree=treeForTurn(data,turnId),ticking=isRunning(turn)||data.nodes.some(node=>node.turnId===turnId&&isRunning(node));
 const [now,setNow]=useState(()=>hostNow());
 useEffect(()=>{if(!ticking)return;setNow(hostNow());const timer=setInterval(()=>setNow(hostNow()),1000);return()=>clearInterval(timer)},[ticking]);
 if(!tree.roots.length&&!turn.startedAt)return null;
 const expanded=new Set(state.view?.executionExpanded||[]);
 const toggle=id=>{const next=new Set(expanded);next.has(id)?next.delete(id):next.add(id);act('view.update',{patch:{executionExpanded:[...next]}})};
 const summaryId=`turn:${turnId}`,open=expanded.has(summaryId);
 const toolCount=turn.nodeCounts?.tools??data.nodes.filter(node=>node.turnId===turnId&&node.kind==='tool').length;
 const workers=turn.nodeCounts?.workers??data.nodes.filter(node=>node.turnId===turnId&&node.kind==='worker').length;
 const detail=(turnId==='observed-activity'?turn.label:null)||[toolCount?`${toolCount} ${toolCount===1?'tool call':'tool calls'}`:null,workers?`${workers} ${workers===1?'worker':'workers'}`:null].filter(Boolean).join(' · ')||'Execution details';
 const elapsed=elapsedLabel(turn,now),label=isRunning(turn)?`Working${elapsed?` · ${elapsed}`:'…'}`:elapsed?`Worked for ${elapsed}`:'Work details';
 return <section className="a-execution-turn a-execution-content" data-part="execution" data-turn-id={turnId} aria-label="Execution details"><button className="a-execution-line a-execution-turn-line" data-action="view.update" aria-expanded={open} onClick={()=>toggle(summaryId)}><ChevronRight className={`a-execution-chevron ${open?'open':''}`}/><span className="a-execution-label" title={detail}>{label}</span><Usage value={turn.aggregateUsage||turn.usage} pending={isRunning(turn)}/><Status status={turn.status||turn.phase}/></button>{open&&<div className="a-execution-roots">{tree.roots.map(node=><ExecutionNode key={node.id} node={node} tree={tree} expanded={expanded} toggle={toggle} act={act} now={now}/>)}<details className="a-execution-metadata a-execution-turn-details"><summary>Turn details</summary><Timing record={turn} now={now}/></details></div>}</section>;
}
