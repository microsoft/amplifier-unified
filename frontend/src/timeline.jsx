import {DetailText,readDetail} from './conversation-detail';
import {request,hostNow} from './api';
import React,{useEffect,useState} from 'react';
import {ChevronRight,GitBranch,Wrench,MessageCircle,Check,Circle,Square,AlertCircle,Copy} from 'lucide-react';
import {treeForTurn,usageLabel,isRunning,elapsedLabel,detailLinks} from './timeline-data';

function Usage({value,pending=false}){const label=usageLabel(value,{pending});return label?<span className="a-execution-usage" title={label.title}>{label.text}</span>:null}
function Status({status}){return ['error','failed','cancelled','interrupted'].includes(status)?<AlertCircle className="a-execution-warning"/>:['complete','completed','success','done'].includes(status)?<Check className="a-execution-complete"/>:isRunning({phase:status})?<span className="a-execution-dot"/>:<Circle/>}
function ExecutionField({label,text,reference}){
 const [open,setOpen]=useState(false),[loaded,setLoaded]=useState(null),[error,setError]=useState(''),[copied,setCopied]=useState(false);
 const key=reference?.digest||text,value=loaded&&loaded.key===key?loaded.value:null;
 useEffect(()=>{
  setError('');setCopied(false);if(!open||!reference)return;
  const controller=new AbortController();
  readDetail(reference,request,controller.signal).then(value=>{if(!controller.signal.aborted)setLoaded({key,value})}).catch(e=>{if(!controller.signal.aborted)setError(e.message)});
  return()=>controller.abort();
 },[open,key]);
 if(!text&&!reference)return null;
 return <details className="a-execution-field" onToggle={e=>setOpen(e.currentTarget.open)}><summary>{label}</summary>{open&&<><pre><code>{value??text}</code></pre>{detailLinks(value??text).map(url=><a key={url} className="a-execution-result-link" href={url} target="_blank" rel="noopener noreferrer">{url}</a>)}{reference&&value===null&&!error&&<small role="status">Loading complete {label.toLowerCase()}…</small>}{error&&<p role="alert">{error} Close and reopen to retry.</p>}<button className="a-link" type="button" disabled={!!reference&&value===null} onClick={async()=>{try{await navigator.clipboard.writeText(value??text);setCopied(true)}catch{setError('Could not copy. Select the text to copy it manually.')}}}><Copy/>{copied?'Copied':`Copy ${label.toLowerCase()}`}</button></>}</details>;
}
const stamp=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString():null;
function Timing({record,now}){
 const start=stamp(record.startedAt),end=stamp(record.endedAt),elapsed=elapsedLabel(record,now);
 return start||end?<dl className="a-execution-timing">{start&&<><dt>Started</dt><dd>{start}</dd></>}{end&&<><dt>Ended</dt><dd>{end}</dd></>}{elapsed&&<><dt>Elapsed</dt><dd>{elapsed}</dd></>}</dl>:null;
}
function ExecutionNode({node,depth=0,ancestors=[],tree,expanded,toggle,act,now}){
 if(depth>20||ancestors.includes(node.id))return null;
 const children=tree.children.get(node.id)||[],isOpen=expanded.has(node.id),Icon=node.kind==='worker'?GitBranch:node.kind==='llm'?MessageCircle:Wrench;
 const summary=typeof node.summary==='string'?node.summary:typeof node.detail==='string'?node.detail:'',phase=node.status||node.phase,elapsed=elapsedLabel(node,now),usage=node.aggregateUsage||node.usage;
 const preview=summary.replace(/\s+/g,' ').slice(0,180),label=node.label||node.tool||node.model||node.kind;
 return <div className="a-execution-node" data-kind={node.kind}>
  <button className="a-execution-line" data-action="view.update" aria-expanded={isOpen} onClick={()=>toggle(node.id)}><ChevronRight className={`a-execution-chevron ${isOpen?'open':''}`}/><Icon/><span className="a-execution-label"><span>{label}</span>{preview&&<small title={preview}>{preview}</small>}</span><span className="a-execution-phase">{phase}{elapsed&&` · ${elapsed}`}</span><Usage value={usage} pending={node.kind==='llm'&&isRunning(node)}/><Status status={phase}/></button>
  {isOpen&&<div className="a-execution-body">
   {summary&&<DetailText text={summary} reference={node.summaryDetail||node.detailDetail} markdown/>}
   {node.model&&<small>{[node.provider,node.model].filter(Boolean).join(' · ')}</small>}
   <Timing record={node} now={now}/>
   {node.toolCallId&&<small className="a-execution-id">Call: {node.toolCallId}</small>}
   {usageLabel(usage,{pending:node.kind==='llm'&&isRunning(node)})&&<p className="a-caption">{usageLabel(usage,{pending:node.kind==='llm'&&isRunning(node)}).title}</p>}
   {node.kind==='llm'&&<small>Only public purpose, timing and usage are shown. Private model reasoning is excluded.</small>}
   {node.kind==='tool'&&<><ExecutionField label="Input" text={node.input} reference={node.inputDetail}/><ExecutionField label="Result" text={node.output} reference={node.outputDetail}/><ExecutionField label="Error" text={node.error} reference={node.errorDetail}/>{!node.input&&!node.output&&!node.error&&<small>No public input or result was retained for this step.</small>}</>}
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
 return <section className="a-execution-turn" data-part="execution" data-turn-id={turnId} aria-label="Execution details"><button className="a-execution-line a-execution-turn-line" data-action="view.update" aria-expanded={open} onClick={()=>toggle(summaryId)}><ChevronRight className={`a-execution-chevron ${open?'open':''}`}/><span className="a-execution-label" title={detail}>{label}</span><Usage value={turn.aggregateUsage||turn.usage} pending={isRunning(turn)}/><Status status={turn.status||turn.phase}/></button>{open&&<div className="a-execution-roots"><Timing record={turn} now={now}/>{tree.roots.map(node=><ExecutionNode key={node.id} node={node} tree={tree} expanded={expanded} toggle={toggle} act={act} now={now}/>)}</div>}</section>;
}
