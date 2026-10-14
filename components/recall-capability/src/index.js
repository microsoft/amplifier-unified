import {managedParticipant} from './managed-files.js';
import {retentionParticipant} from './retention.js';
import {validateServiceRelease} from './service-lifecycle.js';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
const callbacks=new Set(['inspectSession','listRecallSources','inspectRecallSource','readRecallSource','readUserMessage','readSessionContext','nativeControlExisting']);
const names=['recall.status','recall.refresh','recall.wait','recall.search','recall.read','memory.status','memory.configure','memory.consolidate','memory.context','memory.source','memory.list','memory.read','memory.create','memory.update','memory.delete','memory.command'];
class Connection{
 constructor(launch,host,changed,idle=()=>{}){this.idle=idle;this.launch=launch;this.host=host;this.changed=changed;this.pending=new Map();this.next=0;this.closed=false;}
 fail(message){this.closed=true;for(const entry of this.pending.values())entry.reject(Error(message));this.pending.clear();}
 write(row){const line=JSON.stringify(row)+'\n';if(Buffer.byteLength(line)>4_000_000)throw Error('Recall frame exceeds 4MB');if(this.closed||!this.child)throw Error('Recall owner disconnected; uncertain work was not replayed');this.child.stdin.write(line,error=>{if(error)this.fail('Recall owner disconnected; uncertain work was not replayed')});}
 send(method,params){if(this.pending.size>=64)throw Error('Recall request capacity reached');const id=++this.next;return new Promise((resolve,reject)=>{this.pending.set(id,{resolve,reject});try{this.write({jsonrpc:'2.0',id,method,params})}catch(error){this.pending.delete(id);reject(error)}});}
 async receive(line){let row;try{row=JSON.parse(line)}catch{this.fail('Invalid Recall response');this.child.kill();return;}
  if(row.method==='owner/idle'){this.idle();return;}
  if(row.method==='owner/changed'){this.changed(row.params.session);return;}
  if(row.method){try{const method=String(row.method).replace(/^host\//,'');if(!String(row.method).startsWith('host/')||!callbacks.has(method))throw Error('Unknown authority callback');const result=await this.host(method,row.params||{});this.write({jsonrpc:'2.0',id:row.id,result:result??null})}catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:error.message}})}return;}
  const pending=this.pending.get(row.id);if(!pending)return;this.pending.delete(row.id);row.error?pending.reject(Object.assign(Error(row.error.message),row.error.data||{})):pending.resolve(row.result);
 }
 async request(method,params){if(this.closed)throw Error('Recall owner closed; no replay');if(!this.ready)this.ready=(async()=>{this.child=spawn(this.launch.command,this.launch.args||[],{cwd:this.launch.cwd,env:{...process.env,...this.launch.env},stdio:'pipe'});this.child.stderr.on('data',()=>{});this.child.on('error',()=>this.fail('Recall owner failed to start'));this.child.on('exit',()=>this.fail('Recall owner exited; no replay'));let bytes=0;this.child.stdout.on('data',chunk=>{for(const byte of chunk){bytes=byte===10?0:bytes+1;if(bytes>4_000_000){this.fail('Recall frame exceeded limit');this.child.kill();return;}}});createInterface({input:this.child.stdout}).on('line',line=>void this.receive(line));const initial=await this.send('initialize',{});if(initial.protocolVersion!==1)throw Error('Unsupported Recall owner')})();await this.ready;return this.send(method,params);}
 async close(){if(this.child){this.child.stdin.end();await new Promise(resolve=>{const timer=setTimeout(()=>{this.child.kill();resolve()},5000);this.child.once('exit',()=>{clearTimeout(timer);resolve()})})}this.fail('Recall owner closed');}
}
export function createRecallCapability(options){
 const manifest={version:1,topics:{recall:{uri:'amplifier-capability://recall/recall',version:1,watch:true}},actions:Object.fromEntries(names.map(operation=>[operation,{topic:'recall',operation,method:'x-amplifier/capabilityAction'}]))};let revision=0;
 const owner=new Connection(options.owner,async(method,args)=>{switch(method){
  case 'inspectSession':return options.inspectSession(args.session);
  case 'listRecallSources':return options.listRecallSources(args);
  case 'inspectRecallSource':return options.inspectRecallSource(args.id);
  case 'readRecallSource':return options.readRecallSource(args.id,args.input);
  case 'readUserMessage':return options.readUserMessage(args.session,args.messageId);
  case 'readSessionContext':return options.readSessionContext(args.session,args.limit);
  case 'nativeControlExisting':if(args.operation!=='memory.consolidate')throw Error('Unsupported memory operation');return options.nativeControlExisting(args.session,args.operation,args.args);
  default:throw Error('Unknown callback');
 }},scope=>options.onInvalidate?.('recall',scope),()=>options.onMayBeIdle?.());
 const authorize=async(scope,context={})=>{if(!scope?.startsWith('ahp-session:/'))throw Error('Selected conversation required');const selected=typeof context.session==='string'?context.session:context.session?.uri;if(selected&&selected!==scope)throw Error('Session scope mismatch');await options.inspectSession(scope,{clientId:context.clientId});};
 const quiescenceParticipant=ownerId=>{
  const release=async(context,outcome,proof,liveRollback=false)=>{const rollback=liveRollback&&outcome==='unchanged'&&proof?.kind==='admission-refused'&&Object.keys(proof).length===1;if(context.purpose==='service-stop'&&outcome!=='unknown'&&!rollback)validateServiceRelease(context,outcome,proof);const value=await owner.request('quiescence.release',{...context,outcome,proof});if(outcome!=='unknown'&&value.released!==true)throw Error('Recall owner release is unconfirmed');};
  return managedParticipant(retentionParticipant({id:ownerId,serviceStop:{version:1},acquire:async context=>{if(context.purpose==='service-stop'&&(await owner.request('initialize',{})).quiescence?.serviceStop?.version!==1)return null;const exact=structuredClone(context),value=await owner.request('quiescence.acquire',exact);if(value.acquired!==true)return null;if(value.fenceId!==exact.fenceId||value.intakeClosed!==true)throw Error('Recall owner acquisition is unconfirmed');return {ownerId,fenceId:exact.fenceId,release:(outcome,proof)=>release(exact,outcome,proof,true)};},reconcileRelease:context=>release(context,context.outcome,context.proof)},args=>owner.request('quiescence.retention',args)),args=>owner.request('quiescence.managedFiles',args),async()=>(await owner.request('initialize',{})).quiescence?.managedFiles?.version===1);
 };
 return {manifest,quiescenceParticipant,quiescenceAccess:Object.fromEntries(['recall.status','recall.wait','recall.read','memory.status','memory.list','memory.read','memory.command'].map(name=>[name,'read'])),actionSchemas:async()=>Object.fromEntries(Object.entries(await owner.request('actions',{})).map(([name,row])=>[name,{description:row.description,schema:row.parameters}])),
  read:async request=>{const uri=new URL(request.uri);uri.search='';uri.hash='';if(request.topic!=='recall'||uri.href!==manifest.topics.recall.uri)throw Error('Unknown Recall topic');await authorize(request.scope,request);const data=await owner.request('snapshot',{session:request.scope});if(Buffer.byteLength(JSON.stringify(data))>512*1024)throw Error('Recall projection exceeded bound');return {topic:'recall',scope:request.scope,revision:++revision,data};},
  action:async(request,context)=>{if(request.version!==1||request.topic!=='recall'||!manifest.actions[request.operation])throw Error('Unadvertised Recall action');await authorize(request.channel,context);const result=await owner.request('action',{session:request.channel,operation:request.operation,args:request.args||{},commandId:request.commandId,origin:context.origin||'ui'});return {accepted:true,result,updates:[],invalidate:['recall']};},
  memoryContext:async(session,args={})=>{await authorize(session);return owner.request('context',{session,...(args.expected?{expected:args.expected}:{})});},
  idle:async session=>{await authorize(session);return owner.request('idle',{session});},
  close:()=>owner.close(),
 };
}
