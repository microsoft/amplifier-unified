import {useShellContext} from './shell/runtime';
import {detailOpen,toggleDetail} from './interface-detail';
import {DetailText} from './conversation-detail';
import {hostNow} from './api';
import React,{useEffect,useState} from 'react';
import {ChevronRight,GitBranch,Wrench,MessageCircle,Check,Circle,Square,AlertCircle,Terminal,FileText,FilePenLine,ListChecks} from 'lucide-react';
import {treeForTurn,usageLabel,isRunning,elapsedLabel} from './timeline-data';
import {actionContent,cleanSummary} from './execution-content.js';
import {useExecutionField,ToolContent,ModelContent} from './execution-content.jsx';
import './execution-content.css';

function Usage({value,pending=false}){const label=usageLabel(value,{pending});return label?<span className="a-execution-usage" title={label.title}>{label.text}</span>:null}
function Status({status}){return ['error','failed','cancelled','interrupted'].includes(status)?<AlertCircle className="a-execution-warning" aria-label={status}/>:['complete','completed','success','done'].includes(status)?<Check className="a-execution-complete" aria-label="Completed"/>:isRunning({phase:status})?<span className="a-execution-dot" aria-label={status}/>:<Circle aria-label={status||'Recorded'}/>}
const stamp=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString():null;
function ExecutionNode({node,depth=0,ancestors=[],tree,act,now,expanded,toggle}){
 const tool=node.kind==='tool',children=tree.children.get(node.id)||[],summary=cleanSummary(node.summary||node.detail);
 const collapsible=tool||node.kind==='llm'||!!summary||children.length>15,open=!collapsible||expanded.has(node.id),requestOpen=expanded.has(`request:${node.id}`);
 const Header=collapsible?'button':'div';
 const requestInline=node.requestDetail?.lines<=15&&node.requestDetail?.length<=3000;
 const input=useExecutionField(node,'input',open&&tool),output=useExecutionField(node,'output',open&&tool),error=useExecutionField(node,'error',open),request=useExecutionField(node,'request',open&&(requestInline||requestOpen));
 if(depth>20||ancestors.includes(node.id))return null;
 const action=tool?actionContent(node,input.value,output.value):null;
 const Icon=node.kind==='worker'?GitBranch:node.kind==='llm'?MessageCircle:({command:Terminal,read:FileText,write:FilePenLine,patch:FilePenLine,tasks:ListChecks,delegate:GitBranch}[action?.kind]||Wrench);
 const phase=node.status||node.phase,elapsed=elapsedLabel(node,now),usage=node.kind==='llm'?node.usage:null;
 const label=action?.title||node.label||node.model||node.kind,model=[node.provider,node.model].filter(Boolean).join(' · ');
 return <div className="a-execution-node" data-kind={node.kind} data-node-id={node.id} data-action-kind={action?.kind}>
  <Header className="a-execution-line a-execution-action-line" {...(collapsible?{type:"button",'data-action':'view.update','data-view-label':[label,action?.target,model,phase].filter(Boolean).join(' · '),'aria-expanded':open,onClick:()=>toggle(node.id)}:{})}>
   {collapsible&&<ChevronRight className={`a-execution-chevron ${open?'open':''}`}/>}<Icon/>
   <span className="a-execution-label" title={[label,action?.target,model,action?.preview||summary].filter(Boolean).join(' · ')}><span>{label}{action?.target&&<> <code>{action.target}</code></>}{node.kind==='llm'&&model&&<> · {model}</>}{!action?.target&&action?.preview&&<> · {action.preview}</>}{action?.kind==='patch'&&(action.added>0||action.removed>0)&&<span className="a-execution-counts"><span>+{action.added}</span><span>−{action.removed}</span></span>}</span></span>
   <span className="a-execution-phase" title={[node.toolCallId&&`Call: ${node.toolCallId}`,stamp(node.startedAt),stamp(node.endedAt)].filter(Boolean).join(" · ")}>{isRunning(node)||['error','failed','cancelled','interrupted'].includes(phase)?`${phase}${elapsed?' · ':''}`:''}{elapsed}</span><Usage value={usage} pending={node.kind==='llm'&&isRunning(node)}/><Status status={phase}/>
  </Header>
  {open&&<div className="a-execution-body">
   {tool?<ToolContent node={node} action={action} input={input} output={output} error={error}/>:node.kind==='llm'?<ModelContent node={node} request={request} error={error} requestOpen={requestOpen} requestInline={requestInline} toggleRequest={()=>toggle(`request:${node.id}`)} now={now}/>:summary&&<DetailText text={summary} reference={node.summaryDetail||node.detailDetail} automatic markdown/>}
   {node.kind==='worker'&&isRunning(node)&&(node.workerId||node.sessionId)&&<button className="a-link a-danger" data-action="worker.stop" onClick={()=>act('worker.stop',{id:node.workerId||node.sessionId})}><Square/>Stop worker</button>}
   {children.length>0&&<div className="a-execution-children">{children.map(child=><ExecutionNode key={child.id} node={child} depth={depth+1} ancestors={[...ancestors,node.id]} tree={tree} act={act} now={now} expanded={expanded} toggle={toggle}/>)}</div>}
  </div>}
 </div>;
}
export function TurnTimeline({data,turnId,state,act}){
 const shell=useShellContext(),detail=shell?.composition?.presentation?.interfaceDetail||shell?.composition?.presentation?.executionDetail||'standard';
 const turn=data.turns.find(turn=>turn.id===turnId)||{id:turnId,label:'Execution'};
 const tree=treeForTurn(data,turnId),nodes=data.nodes.filter(node=>node.turnId===turnId),ticking=isRunning(turn)||nodes.some(isRunning);
 const [now,setNow]=useState(()=>hostNow());
 useEffect(()=>{if(!ticking)return;setNow(hostNow());const timer=setInterval(()=>setNow(hostNow()),1000);return()=>clearInterval(timer)},[ticking]);
 if(!tree.roots.length&&!turn.startedAt)return null;
 const expanded=new Set(state.view?.executionExpanded||[]),summaryId=`turn:${turnId}`;
 const open=detailOpen(expanded,summaryId,detail);
 const toggle=id=>{const next=id===summaryId?toggleDetail(expanded,id,detail):new Set(expanded);if(id!==summaryId){next.has(id)?next.delete(id):next.add(id)}act('view.update',{patch:{executionExpanded:[...next]}})};
 const tools=turn.nodeCounts?.tools??nodes.filter(node=>node.kind==='tool').length,models=turn.nodeCounts?.models??nodes.filter(node=>node.kind==='llm').length;
 const elapsed=elapsedLabel(turn,now),label=isRunning(turn)?`Working${elapsed?` · ${elapsed}`:'…'}`:elapsed?`Worked for ${elapsed}`:'Work details';
 return <section className="a-execution-turn a-execution-content" data-part="execution" data-turn-id={turn.originalTurnId||turnId} data-group-id={turnId} aria-label="Execution details"><button type="button" className="a-execution-line a-execution-turn-line" data-action="view.update" data-view-label={isRunning(turn)?"Working · expand work details":"Completed work · expand work details"} aria-expanded={open} onClick={()=>toggle(summaryId)}><ChevronRight className={`a-execution-chevron ${open?'open':''}`}/><span className="a-execution-label">{label}</span><Usage value={turn.aggregateUsage||turn.usage} pending={isRunning(turn)}/><span className="a-execution-call-counts">{tools} {tools===1?'tool call':'tool calls'} · {models} {models===1?'model call':'model calls'}</span><Status status={turn.status||turn.phase}/></button>{open&&tree.roots.length>0&&<div className="a-execution-roots">{tree.roots.map(node=><ExecutionNode key={node.id} node={node} tree={tree} act={act} now={now} expanded={expanded} toggle={toggle}/>)}</div>}</section>;
}
