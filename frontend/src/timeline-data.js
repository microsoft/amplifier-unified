import {visibleWorkers} from './activity.js';
export function executionData(session){
 if(session?.execution?.nodes?.length){const nodes=session.execution.nodes,turns=[...(session.execution.turns||[])];for(const node of nodes)if(node.turnId&&!turns.some(turn=>turn.id===node.turnId))turns.push({id:node.turnId});return {nodes,turns}}
 const workers=visibleWorkers(session?.workers||[]),events=session?.runtimeEvents||[];
 if(!workers.length&&!events.some(e=>e.type==='runtime.tool'||String(e.type).startsWith('tool.')))return {nodes:[],turns:[]};
 const turnId='observed-activity',nodes=[],tools=new Map();
 for(const event of events){
  const kind=event.type==='runtime.tool'?event.phase:String(event.type).replace(/^tool[.:]/,'');
  if(event.type!=='runtime.tool'&&!/^tool[.:]/.test(event.type||''))continue;
  const call=event.callId||event.call_id||event.tool_call_id;if(!call)continue;
  const previous=tools.get(call)||{id:`tool:${call}`,kind:'tool',turnId,label:event.tool||event.tool_name||'Tool call',callId:call,startedAt:event.at||event.time};
  previous.status=['post','end','finished'].includes(kind)?'completed':['error','failed'].includes(kind)?'error':'running';
  previous.actualSessionId=event.actualSessionId||previous.actualSessionId;
  tools.set(call,previous);
 }
 const workersBySession=new Map(workers.map(worker=>[worker.sessionId||worker.id,worker]));
 for(const tool of tools.values()){if(workersBySession.has(tool.actualSessionId))tool.parentId=`worker:${tool.actualSessionId}`;nodes.push(tool)}
 for(const worker of workers){
  const id=worker.sessionId||worker.id,call=worker.callId||worker.call_id;
  const parentWorker=workersBySession.get(worker.parentSessionId);
  nodes.push({id:`worker:${id}`,kind:'worker',turnId,label:worker.name||worker.agent||worker.title||'Worker',status:worker.status,phase:worker.phase,summary:worker.detail||worker.report||worker.result,summaryDetail:worker.detail?worker.detailDetail:worker.report?worker.reportDetail:worker.resultDetail,startedAt:worker.startedAt,endedAt:worker.endedAt,workerId:worker.id||id,parentId:tools.has(call)?`tool:${call}`:parentWorker?`worker:${parentWorker.sessionId||parentWorker.id}`:null});
 }
 return {nodes,turns:[{id:turnId,label:'Recent execution activity',status:nodes.some(n=>['running','working','starting','queued','pending','retrying','idle'].includes(n.status))?'running':'completed'}]};
}
export function messageTurnId(message,turns){
 if(message.role!=='user')return null;
 const turn=turns.find(turn=>turn.messageId===message.id||turn.userMessageId===message.id||(message.inputId&&(turn.inputId===message.inputId||turn.id===message.inputId)));
 return turn?.id||null;
}
export function turnPlacements(messages,data){
 const after=new Map(),before=[],ids=new Set(messages.map(m=>m.id));
 for(const turn of data.turns){
  let anchor=turn.anchorMessageId;
  if(!Object.hasOwn(turn,'anchorMessageId')){
   const exact=messages.find(m=>m.role==='user'&&((turn.messageId&&turn.messageId===m.id)||(turn.userMessageId&&turn.userMessageId===m.id)||(m.inputId&&(turn.inputId===m.inputId||turn.id===m.inputId))));
   const times=data.nodes.filter(n=>n.turnId===turn.id&&Number.isFinite(n.startedAt)).map(n=>n.startedAt);
   const started=Number.isFinite(turn.startedAt)?turn.startedAt:times.length?Math.min(...times):null;
   anchor=exact?.id||(started!==null?messages.findLast(m=>Number.isFinite(m.createdAt)&&m.createdAt<=started)?.id:null);
  }
  if(anchor&&ids.has(anchor)){if(!after.has(anchor))after.set(anchor,[]);after.get(anchor).push(turn.id)}else before.push(turn.id);
 }
 return {after,before};
}
export function compactTokens(value){return value>=1000000?`${(value/1000000).toFixed(1).replace(/\.0$/,'')}m`:value>=1000?`${(value/1000).toFixed(1).replace(/\.0$/,'')}k`:String(value)}
export function usageLabel(usage){
 if(!usage)return null;
 if(usage.calls===0&&!usage.totalTokens&&!usage.inputTokens&&!usage.outputTokens&&!usage.costUsd)return null;
 const tokens=usage.totalTokens??((usage.inputTokens!=null||usage.outputTokens!=null)?(usage.inputTokens||0)+(usage.outputTokens||0):null);
 const type=usage.costType||((usage.costUsd!=null&&usage.unknownCalls===0)?'reported':'unavailable');
 const cost=typeof usage.costUsd==='number'&&type!=='unavailable'?`${type==='estimated'||usage.estimatedCalls>0?'≈':''}$${usage.costUsd<.01?usage.costUsd.toFixed(6).replace(/0+$/,'').replace(/\.$/,'.00'):usage.costUsd.toFixed(3)}${type==='partial'?'+':''}`:null;
 const pieces=[];
 if(usage.calls>0&&usage.tokenUnknownCalls>=usage.calls)pieces.push('tokens unavailable');else if(tokens!=null)pieces.push(`${compactTokens(tokens)} tokens${usage.tokenUnknownCalls?' + ?':''}`);
 if(cost)pieces.push(cost);else if(usage.calls||tokens!=null)pieces.push('cost unavailable');
 if(!pieces.length)return null;
 const breakdown=[usage.inputTokens!=null?`${usage.inputTokens} input tokens`:null,usage.outputTokens!=null?`${usage.outputTokens} output tokens`:null,usage.cacheReadTokens?`${usage.cacheReadTokens} cache-read tokens`:null,usage.cacheWriteTokens?`${usage.cacheWriteTokens} cache-write tokens`:null,type==='partial'?`Partial cost: ${usage.pricedCalls||0} of ${usage.calls||'?'} calls priced`:type==='estimated'?'Cost is estimated':type==='reported'?'Cost reported by the provider':'Cost is unavailable'].filter(Boolean).join(' · ');
 return {text:pieces.join(' · '),title:breakdown};
}
export function treeForTurn(data,turnId){
 const nodes=data.nodes.filter(node=>node.turnId===turnId),byId=new Map(nodes.map(node=>[node.id,node]));
 const children=new Map();for(const node of nodes){const parent=node.parentId&&node.parentId!==node.id&&byId.has(node.parentId)?node.parentId:null;if(!children.has(parent))children.set(parent,[]);children.get(parent).push(node)}
 // Orphan and malformed cyclic source records remain inspectable without recursion.
 const roots=children.get(null)||[],covered=new Set();
 const mark=node=>{if(covered.has(node.id))return;covered.add(node.id);for(const child of children.get(node.id)||[])mark(child)};
 roots.forEach(mark);for(const node of nodes)if(!covered.has(node.id)){roots.push(node);mark(node)}
 return {roots,children};
}
