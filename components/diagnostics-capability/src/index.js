import {managedParticipant} from './managed-files.js';
import {retentionParticipant} from './retention.js';
import {randomUUID} from 'node:crypto';
import {Peer} from './connection.js';
const string=maxLength=>({type:'string',maxLength});
const object=(properties={},required=[])=>({type:'object',additionalProperties:false,properties,required});
const integer=(minimum,maximum)=>({type:'integer',minimum,maximum});
const streams={type:'array',maxItems:9,uniqueItems:true,items:{enum:['app','sessions','workers','tools','usage','canvas','smartTools','updates','conversation']}};
const destination=object({id:string(100),name:string(2000),url:string(2000),enabled:{type:'boolean'},streams,workspace:string(2000),workspacePattern:string(2000),includePaths:{type:'boolean'},authMode:{enum:['static','entra']},apiKeyEnv:string(200),authResource:string(2000)},['url']);
const configuration=object({enabled:{type:'boolean'},streams,retentionDays:integer(1,365),maxRecords:integer(100,100000),providerRequests:{const:false},destinations:{type:'array',maxItems:10,items:destination}});
export const diagnosticsActions={
 'diagnostics.get':{description:'Read bounded diagnostic policy, local counters and delivery summaries.',schema:object()},
 'diagnostics.configure':{description:'Save explicit live-capture and forwarding policy at its shared revision; never uploads historical records.',schema:object({config:configuration,expectedRevision:integer(0,Number.MAX_SAFE_INTEGER)},['config','expectedRevision'])},
 'diagnostics.records':{description:'Read a selected page of diagnostic records. Agents can read only their own conversation.',schema:object({stream:{enum:['*',...streams.items.enum]},sessionId:string(512),before:integer(1,Number.MAX_SAFE_INTEGER),limit:integer(1,50)})},
 'diagnostics.environment':{description:'Check whether a named environment credential is available; never return its value.',schema:object({name:string(200)},['name'])},
 'diagnostics.test':{description:'Authenticate and send one synthetic probe to an explicitly saved destination. Uncertain probes are never repeated.',schema:object({id:string(100)},['id'])},
 'diagnostics.retry':{description:'Explicitly retry known rejected deliveries with their retained envelopes. Unknown deliveries are not resent.',schema:object({id:string(100)},['id'])},
 'diagnostics.receipt':{description:'Read the exact original settings, probe or retry result after a lost reply.',schema:object({commandId:string(200)},['commandId'])},
};
const reads=['diagnostics.get','diagnostics.records','diagnostics.environment','diagnostics.receipt'];
const metaKeys=new Set(['id','sessionId','rootSessionId','parentId','turnId','inputId','commandId','action','origin','kind','phase','status','role','tool','tool_name','provider','model','durationMs','errorType','errorCode','input_tokens','output_tokens','total_tokens','cost_usd','usage','revision','count','size','operationId','artifactId']);
function metadata(raw){
 const result={};if(!raw||typeof raw!=='object')return result;
 for(const [key,value]of Object.entries(raw))if(metaKeys.has(key)){
  if(typeof value==='string')result[key]=value.slice(0,200);
  else if(typeof value==='number'&&Number.isFinite(value)||typeof value==='boolean'||value===null)result[key]=value;
  else if(key==='usage'&&value&&typeof value==='object')result[key]=Object.fromEntries(Object.entries(value).slice(0,32).filter(([,number])=>typeof number==='number'&&Number.isFinite(number)));
 }return result;
}

export function createDiagnosticsCapability(options){
 let revision=0,closed=false,captureHeld=false,policy,lastState,ready,active=0,dropped=0;const pending=new Set();
 const peer=new Peer(options.owner,()=>options.onInvalidate?.('diagnostics','host'),()=>options.onMayBeIdle?.());
 const ensure=()=>ready??=(async()=>{const state=await peer.request('snapshot',{});lastState=state;policy=state.config;if(state.available===false)throw Error('Diagnostic owner readiness is unavailable');return state})();
 const manifest={version:1,topics:{diagnostics:{version:1,uri:'amplifier-capability://diagnostics/settings',scope:'host',watch:true}},actions:Object.fromEntries(Object.keys(diagnosticsActions).map(operation=>[operation,{topic:'diagnostics',operation,method:'x-amplifier/capabilityAction'}]))};
 const unavailable=()=>({available:false,revision:lastState?.revision??null,config:lastState?.config?{...lastState.config,enabled:false}:null,local:{records:null,storageError:true,configurationError:lastState?.local?.configurationError??false},destinations:[],streams:lastState?.streams??[],capture:{mode:'explicit-live-observations',historicalScan:false,providerRequests:false},results:{}});
 const inspect=async()=>{try{await ensure();const state=await peer.request('snapshot',{});lastState=state;policy=state.config;return state}catch(error){policy=policy?{...policy,enabled:false}:policy;return {...unavailable(),error:'Diagnostic owner read is unavailable',detail:String(error.message).slice(0,300)}}};
 const snapshot=async()=>{const state=await inspect();return {topic:'diagnostics',scope:'host',revision:++revision,data:{diagnostics:{...state,local:{...state.local,transportDropped:dropped}}}}};
 function observe(input){
  if(closed||active>=32||captureHeld||peer.held){dropped++;return false}
  // The caller supplies only this observation. Never read a chat, source log or
  // shared app state to enrich it, even when capture is explicitly enabled.
  if(policy&&(!policy.enabled||!policy.streams.includes(input.stream)))return false;
  const data=input.stream==='conversation'?Object.fromEntries(Object.entries(input.data??{}).filter(([key])=>['role','text','prompt','response'].includes(key)).map(([key,value])=>[key,typeof value==='string'?value.slice(0,16000):null])):metadata(input.data);
  const item={id:input.id??randomUUID(),stream:input.stream,session:input.session,workspace:input.workspace,event:input.event,data};
  active++;const task=(async()=>{await ensure();if(policy.enabled&&policy.streams.includes(item.stream)){const result=await peer.request('record',{items:[item]});if(result.accepted===false){dropped++;policy={...policy,enabled:false}}}})().catch(()=>{dropped++}).finally(()=>{active--;pending.delete(task);peer.signal(options.onMayBeIdle)});pending.add(task);return true;
 }
 const nativeEvent=async(context,params)=>{
  const event=params.event;if(!event||typeof event.type!=='string'||/delta|naming\.progress/.test(event.type))return;
  const payload=event.data??event.payload??event;
  const stream=event.type.startsWith('worker')?'workers':event.type.includes('tool')?'tools':/provider|usage|llm/.test(event.type)?'usage':'sessions';
  observe({stream,event:event.type,session:context.session,workspace:context.workingDirectory,data:{...metadata(payload),runtimeSessionId:context.nativeSessionId}});
 };
 return {
  manifest,actionSchemas:()=>diagnosticsActions,quiescenceAccess:Object.fromEntries(reads.map(key=>[key,'read'])),
  quiescenceParticipant:managedParticipant(retentionParticipant({id:'diagnostics',serviceStop:{version:1},acquire:async context=>{captureHeld=true;await Promise.all([...pending]);let lease;try{lease=await peer.acquire(context)}catch(error){if(!peer.held)captureHeld=false;throw error}if(!lease){captureHeld=false;return null}return {...lease,release:async(outcome,proof)=>{await lease.release(outcome,proof);if(outcome!=='unknown')captureHeld=false}}},abortAdmission:async input=>{if(pending.size)throw Error('Diagnostics capture work is still pending');const result=await peer.abortAdmission(input);captureHeld=false;return result;},reconcileRelease:async input=>{await peer.release(input,input.outcome,input.proof);if(input.outcome!=='unknown')captureHeld=false}},args=>peer.send('quiescence.retention',args)),args=>peer.send('quiescence.managedFiles',args),async()=>{await peer.start();return (await peer.send('initialize',{})).quiescence?.managedFiles?.version===1}),inspectQuiescence:peer.inspectQuiescence,
  ready:async()=>{await ensure();const state=await inspect();if(state.available===false)throw Error('Diagnostic owner readiness is unavailable');return state},observe,nativeEvent,
  read:async({topic,scope,uri})=>{const url=new URL(uri);url.search='';url.hash='';if(topic!=='diagnostics'||!['host','ahp-root://'].includes(scope)||url.href!==manifest.topics.diagnostics.uri)throw Error('Host diagnostic topic required');return snapshot()},
  action:async(request,context)=>{
   const selected=typeof context?.session==='string'?context.session:context?.session?.uri;
   if(request.version!==1||request.topic!=='diagnostics'||!diagnosticsActions[request.operation]||!['host','ahp-root://',selected].includes(request.channel)||typeof request.channel!=='string')throw Error('Unadvertised diagnostics action or scope');
   const args={...(request.args??{})};if(Buffer.byteLength(JSON.stringify(args))>32768)throw Error('Diagnostic arguments exceed32KiB');
   if(context.origin==='agent'&&request.operation==='diagnostics.records'){
    if(!selected||args.sessionId&&args.sessionId!==selected)throw Error('Agent diagnostic query must select its own conversation');args.sessionId=selected;
   }
   if(request.operation==='diagnostics.get'){const state=await inspect();return {accepted:true,result:{...state,local:{...state.local,transportDropped:dropped}},updates:[],invalidate:[]}};
   await ensure();const result=await peer.request('action',{operation:request.operation,args,commandId:request.commandId});
   if(request.operation==='diagnostics.get'&&result.local)result.local={...result.local,transportDropped:dropped};
   if(request.operation==='diagnostics.configure'&&result.status==='completed')policy=(await peer.request('snapshot',{})).config;
   return {accepted:result.accepted!==false&&result.status!=='rejected',result,updates:[],invalidate:reads.includes(request.operation)?[]:['diagnostics']};
  },
  close:async()=>{closed=true;await Promise.all([...pending]);await peer.close()},
 };
}
