import {visibleWorkers} from './activity.js';
export function executionData(session){
 if(session?.execution?.nodes?.length||session?.execution?.turns?.length){const nodes=session.execution.nodes||[],turns=[...(session.execution.turns||[])];for(const node of nodes)if(node.turnId&&!turns.some(turn=>turn.id===node.turnId))turns.push({id:node.turnId});return {nodes,turns,segments:session.execution.segments||[]}}
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
export const liveStates=new Set(['running','working','starting','queued','pending','retrying','idle']);
export function isRunning(record){return liveStates.has(record?.status||record?.phase)&&!Number.isFinite(record?.endedAt)}
export function elapsedLabel(record,now=Date.now()/1000){
 if(!Number.isFinite(record?.startedAt))return null;
 const end=Number.isFinite(record.endedAt)?record.endedAt:isRunning(record)?now:null;
 if(end===null)return null;
 const duration=Math.max(0,end-record.startedAt),seconds=Math.floor(duration);
 return duration>=60?`${Math.floor(seconds/60)}m ${seconds%60}s`:isRunning(record)?`${seconds}s`:`${Number(duration.toFixed(1))}s`;
}
export function usageLabel(usage,{pending=false}={}){
 if(!usage)return pending?{text:'usage pending',title:'Usage has not been reported yet.'}:null;
 if(usage.calls===0&&!usage.totalTokens&&!usage.inputTokens&&!usage.outputTokens&&!usage.costUsd)return pending?{text:'usage pending',title:'Usage has not been reported yet.'}:null;
 const reported=usage.totalTokens??((usage.inputTokens!=null||usage.outputTokens!=null)?(usage.inputTokens||0)+(usage.outputTokens||0):null);
 const tokens=usage.grossTotalTokens??(reported===null?null:reported+(usage.cacheWriteTokens||0));
 // Older saved rows may lack per-metric pending counts. Only a live lifecycle
 // can supply that fallback; a finished call must not promise future telemetry.
 const tokenPending=usage.tokenPendingCalls??(pending?usage.tokenUnknownCalls||0:0),costPending=usage.costPendingCalls??(pending?usage.unknownCalls||0:0);
 const type=usage.costType||((usage.costUsd!=null&&usage.unknownCalls===0)?'reported':'unavailable');
 const cost=typeof usage.costUsd==='number'&&type!=='unavailable'?`${type==='estimated'||usage.estimatedCalls>0?'≈':''}$${usage.costUsd<.01?usage.costUsd.toFixed(6).replace(/0+$/,'').replace(/\.$/,'.00'):usage.costUsd.toFixed(3)}${type==='partial'&&!costPending?'+':''}`:null;
 const pieces=[];
 if(usage.calls>0&&usage.tokenUnknownCalls>=usage.calls)pieces.push(tokenPending===usage.calls?'tokens pending':tokenPending?'tokens partly pending':'tokens unavailable');
 else if(tokens!=null)pieces.push(`${compactTokens(tokens)} tokens${usage.tokenUnknownCalls?(tokenPending===usage.tokenUnknownCalls?' + pending':' + ?'):''}`);
 if(cost)pieces.push(cost+(costPending?' + pending':''));else if(usage.calls||tokens!=null)pieces.push(costPending===usage.calls?'cost pending':costPending?'cost partly pending':'cost unavailable');
 if(!pieces.length)return null;
 const breakdown=[usage.inputTokens!=null&&(usage.calls==null||usage.tokenUnknownCalls!==usage.calls)?`${usage.inputTokens} input tokens`:null,usage.outputTokens!=null&&(usage.calls==null||usage.tokenUnknownCalls!==usage.calls)?`${usage.outputTokens} output tokens`:null,usage.cacheReadTokens?`${usage.cacheReadTokens} cache-read tokens`:null,usage.cacheWriteTokens?`${usage.cacheWriteTokens} cache-write tokens`:null,tokenPending?`${tokenPending} call(s) awaiting token usage`:null,costPending?`${costPending} call(s) awaiting cost`:null,type==='partial'?`Partial cost: ${usage.pricedCalls||0} of ${usage.calls||'?'} calls priced`:type==='estimated'?'Cost is estimated':type==='reported'?'Cost reported by the provider':costPending===usage.calls?'Cost has not been reported yet':'Provider did not report a cost for completed calls',usage.estimatedCalls&&type==='partial'?'Includes estimated cost':null].filter(Boolean).join(' · ');
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

export function detailLinks(text){
 let value;try{value=JSON.parse(text)}catch{return []}
 const urls=new Set();
 function visit(item,depth=0){
  if(!item||typeof item!=='object'||depth>8)return;
  for(const [key,value] of Object.entries(item).slice(0,100)){
   if(urls.size>=8)return;
   if(['url','uri','html_url','web_url','artifact_url'].includes(key)&&typeof value==='string'){
    try{const url=new URL(value);if(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password)urls.add(url.href)}catch{}
   }else if(typeof value==='object')visit(value,depth+1);
  }
 }
 visit(value);return [...urls];
}

// Actions initially occupy one line. Do not hide a small list behind a second
// disclosure just because one of its unloaded payloads is large.
export function workSize(nodes){return nodes.length}
export function segmentUsage(nodes){
 const calls=nodes.filter(node=>node.kind==='llm'),value={calls:calls.length,pricedCalls:0,unknownCalls:0,estimatedCalls:0,tokenUnknownCalls:0,costPendingCalls:0,tokenPendingCalls:0,costUsd:0};
 for(const key of ['inputTokens','outputTokens','totalTokens','cacheReadTokens','cacheWriteTokens'])value[key]=0;
 for(const node of calls){const usage=node.usage||{},pending=isRunning(node);
  for(const key of ['inputTokens','outputTokens','totalTokens','cacheReadTokens','cacheWriteTokens'])if(Number.isFinite(usage[key]))value[key]+=usage[key];
  if(usage.totalTokens==null&&(usage.inputTokens!=null||usage.outputTokens!=null))value.totalTokens+=(usage.inputTokens||0)+(usage.outputTokens||0);
  if(usage.totalTokens==null&&usage.inputTokens==null&&usage.outputTokens==null){value.tokenUnknownCalls++;if(pending)value.tokenPendingCalls++}
  if(Number.isFinite(usage.costUsd)){value.costUsd+=usage.costUsd;value.pricedCalls++;if(usage.costType==='estimated')value.estimatedCalls++}else{value.unknownCalls++;if(pending)value.costPendingCalls++}
 }
 value.costType=!value.pricedCalls?'unavailable':value.unknownCalls?'partial':value.estimatedCalls?'estimated':'reported';return value;
}
export function splitWork(messages,data){
 const turns=[],nodes=[];
 for(const turn of data.turns){
  const source=data.nodes.filter(node=>node.turnId===turn.id),groups=new Map();
  for(const node of source){
   const at=Number.isFinite(node.startedAt)?node.startedAt:Number.isFinite(node.endedAt)?node.endedAt:turn.startedAt;
   let anchor=turn.anchorMessageId??null;
   if(Object.hasOwn(node,'anchorMessageId'))anchor=node.anchorMessageId;
   else if(Number.isFinite(at))for(const message of messages){if(message.timestampKnown!==false&&Number.isFinite(message.createdAt)&&message.createdAt<=at)anchor=message.id}
   // Explicit anchors are still useful when older logs have no timestamps.
   const id=`${turn.id}@${anchor||'start'}`;
   if(!groups.has(id))groups.set(id,{id,anchor,nodes:[]});
   groups.get(id).nodes.push({...node,turnId:id});
  }
  if(!source.length&&isRunning(turn))groups.set(`${turn.id}@${turn.anchorMessageId||'start'}`,{id:`${turn.id}@${turn.anchorMessageId||'start'}`,anchor:turn.anchorMessageId,nodes:[]});
  for(const group of groups.values()){
   const starts=group.nodes.map(node=>node.startedAt).filter(Number.isFinite),ends=group.nodes.map(node=>node.endedAt).filter(Number.isFinite);
   const running=group.nodes.some(isRunning)||(!starts.length&&!ends.length&&isRunning(turn));
   const failure=group.nodes.find(node=>['error','failed','cancelled','interrupted'].includes(node.status||node.phase));
   turns.push({...turn,id:group.id,originalTurnId:turn.id,anchorMessageId:group.anchor,startedAt:starts.length?Math.min(...starts):turn.startedAt,
    endedAt:running?undefined:ends.length?Math.max(...ends):turn.endedAt,phase:running?'running':failure?(failure.status||failure.phase):ends.length||turn.endedAt?'completed':'recorded',status:undefined,
    aggregateUsage:segmentUsage(group.nodes),nodeCounts:{tools:group.nodes.filter(n=>n.kind==='tool').length,models:group.nodes.filter(n=>n.kind==='llm').length},
    ...(data.segments||[]).find(segment=>segment.id===group.id)});
   nodes.push(...group.nodes);
  }
 }
 return {nodes,turns};
}
