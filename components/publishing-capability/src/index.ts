import {forwardAdmissionAbort} from './admission-abort.js';
import {managedParticipant} from './managed-files.js';
import {retentionParticipant} from './retention.js';
import {validateServiceRelease} from './service-lifecycle.js';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
export type Json=Record<string,any>;
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
export interface Launcher {command:string;args?:string[];env?:Record<string,string>;cwd?:string;requestTimeoutMs?:number;initializeTimeoutMs?:number;}
export interface Options {
 owner:Launcher;
 withSessionWorkspace:<T>(uri:string,expected:Json,callback:(context:Json)=>Promise<T>)=>Promise<T>;
 inspectSession:(uri:string,context?:{clientId:string})=>Promise<Json>;
 authorizePublication?:(request:{session:string;operation:string;args:Json;commandId:string;clientId:string})=>Promise<{approved:boolean;approvalId?:string}>;
 onMayBeIdle?:()=>void;
 onInvalidate?:(topic:string,scope:string)=>void;
}
const METHODS=new Set(['inspectSession','authorizePublication']);
const actions=['list','build','preview','review','deploy','rollback','status','logs','stop','remove','receipt','release','command','target.list','target.save','target.inspect','target.select','target.remove'].map(name=>'publishing.'+name);
/** Two-way owner channel. Calls are never retried after lost transport. */
export class OwnerConnection {
 private process?:ChildProcessWithoutNullStreams;private ready?:Promise<void>;private next=0;private closed=false;private pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void}>();
 get admissionPending(){return this.pending.size;}
 constructor(private launcher:Launcher,private callback:(method:string,args:Json)=>Promise<any>,private changed:(scope:string)=>void,private idle:()=>void=()=>{}){
  for(const timeout of [launcher.requestTimeoutMs,launcher.initializeTimeoutMs])if(timeout!==undefined&&(!Number.isInteger(timeout)||timeout<25||timeout>90000))throw Error('Owner timeout must be25–90000ms');
 }
 private fail(message:string){this.closed=true;for(const entry of this.pending.values())entry.reject(Error(message));this.pending.clear();}
 private write(row:Json){const data=JSON.stringify(row)+'\n';if(Buffer.byteLength(data)>4_000_000)throw Error('Owner frame exceeds4MB');if(this.closed||!this.process)throw Error('Owner unavailable; uncertain work was not replayed');this.process.stdin.write(data,error=>{if(error)this.fail('Owner transport failed; outcome unknown.');});}
 private start(){
  if(this.closed)throw Error('Owner connection is closed; no automatic replay.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stdin.on('error',()=>{this.fail('Owner input transport failed; outcome unknown');child.kill();});child.stderr.on('data',()=>{});child.on('error',()=>this.fail('Publishing owner failed to start'));child.on('exit',()=>this.fail('Publishing owner exited; uncertain commands not replayed'));
   let bytes=0;child.stdout.on('data',(chunk:Buffer)=>{for(const b of chunk){bytes=b===10?0:bytes+1;if(bytes>4_000_000){this.fail('Owner frame exceeded limit');child.kill();return;}}});
   createInterface({input:child.stdout}).on('line',line=>{void this.receive(line).catch(()=>{this.fail('Malformed owner response; outcome unknown, not replayed');child.kill();});});
   const value=await this.send('initialize',{},this.launcher.initializeTimeoutMs??5000);if(value?.protocolVersion!==1){this.fail('Unsupported publishing owner');child.kill();throw Error('Unsupported publishing owner');}
  })();return this.ready;
 }
 private async receive(line:string){
  let row:Json;try{row=JSON.parse(line);}catch{this.fail('Invalid owner response');this.process?.kill();return;}
  if(!row||typeof row!=='object'||Array.isArray(row)||row.jsonrpc!=='2.0')throw Error('Malformed owner envelope');
  if(row.method==='owner/idle'){this.idle();return;}
  if(row.method==='owner/changed'){
   if(!row.params||typeof row.params!=='object'||Array.isArray(row.params)||typeof row.params.session!=='string'||(row.params.topic!==undefined&&typeof row.params.topic!=='string'))throw Error('Malformed owner notification');
   this.changed(row.params.session);return;
  }
  if(row.method){
   if(typeof row.method!=='string'||!['string','number'].includes(typeof row.id)||(row.params!==undefined&&(!row.params||typeof row.params!=='object'||Array.isArray(row.params))))throw Error('Malformed owner callback');
   const method=String(row.method).replace(/^host\//,'');
   try{if(!String(row.method).startsWith('host/')||!METHODS.has(method))throw Error('Unknown host callback');const result=await this.callback(method,row.params??{});this.write({jsonrpc:'2.0',id:row.id,result:result??null});}
   catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:String(error instanceof Error?error.message:error)}});}return;
  }
  if(!Number.isSafeInteger(row.id)||Object.hasOwn(row,'result')===Object.hasOwn(row,'error')||(Object.hasOwn(row,'error')&&(!row.error||typeof row.error!=='object'||typeof row.error.message!=='string'||!Number.isInteger(row.error.code))))throw Error('Malformed owner reply');
  const entry=this.pending.get(row.id);if(!entry)return;this.pending.delete(row.id);row.error?entry.reject(Object.assign(Error(row.error.message),row.error.data??{})):entry.resolve(row.result);
 }
 private send(method:string,params:Json,timeoutMs=this.launcher.requestTimeoutMs??90000){
  if(this.closed)throw Error('Owner unavailable; uncertain work was not replayed');
  if(this.pending.size>=64)throw Error('Owner request capacity reached');
  const id=++this.next;
  return new Promise<any>((resolve,reject)=>{
   // A timed-out mutation may already have executed. Terminalize this channel;
   // retain durable receipts/fences for explicit inspection after owner restart.
   const timer=setTimeout(()=>{this.fail('Owner reply timed out; outcome unknown, not replayed');this.process?.kill();},timeoutMs);timer.unref();
   const settle={resolve:(value:any)=>{clearTimeout(timer);resolve(value);},reject:(error:Error)=>{clearTimeout(timer);reject(error);}};
   this.pending.set(id,settle);
   try{this.write({jsonrpc:'2.0',id,method,params});}catch(e){this.pending.delete(id);settle.reject(e as Error);}
  });
 }
 async request(method:string,args:Json){await this.start();return this.send(method,args);}
 async close(){
  const child=this.process;this.fail('Owner closed; uncertain work was not replayed');
  if(!child||child.exitCode!==null||child.signalCode!==null)return;
  child.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{child.kill('SIGKILL');resolve();},4000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
 }
}
export class PublishingCapabilities {
 readonly manifest={version:1,topics:{publishing:{uri:'amplifier-capability://publishing/publishing',version:1,watch:true}},actions:Object.fromEntries(actions.map(operation=>[operation,{topic:'publishing',operation,method:'x-amplifier/capabilityAction'}]))};
 private owner:OwnerConnection;private revision=0;
 constructor(private options:Options){this.owner=new OwnerConnection(options.owner,async(method,params)=>{
  if(method==='inspectSession')return options.inspectSession(params.session);
  if(method==='authorizePublication'){
   if(!options.authorizePublication)throw Error('Explicit publication approval is required; no agent publication was admitted');
   return options.authorizePublication(params as any);
  }
  throw Error('Unknown publishing authority callback');
 },scope=>options.onInvalidate?.('publishing',scope),()=>options.onMayBeIdle?.());}
 readonly quiescenceAccess={'publishing.command':'read'} as const;
 quiescenceParticipant=(ownerId:string)=>{
  const release=async(context:Json,outcome:string,proof?:Json,liveRollback=false)=>{const rollback=liveRollback&&outcome==='unchanged'&&proof?.kind==='admission-refused'&&Object.keys(proof).length===1;if(context.purpose==='service-stop'&&outcome!=='unknown'&&!rollback)validateServiceRelease(context as any,outcome as 'unchanged'|'ready',proof);const value=await this.owner.request('quiescence.release',{...context,outcome,proof});if(outcome!=='unknown'&&value.released!==true)throw Error('Publishing owner release is unconfirmed');};
  return managedParticipant(retentionParticipant({id:ownerId,serviceStop:{version:1 as const},acquire:async(context:Json)=>{if(context.purpose==='service-stop'&&(await this.owner.request('initialize',{})).quiescence?.serviceStop?.version!==1)return null;const exact=structuredClone(context),value=await this.owner.request('quiescence.acquire',exact);if(value.acquired!==true)return null;if(value.fenceId!==exact.fenceId||value.intakeClosed!==true)throw Error('Publishing owner acquisition is unconfirmed');return {ownerId,fenceId:exact.fenceId,release:(outcome:string,proof?:Json)=>release(exact,outcome,proof,true)};},abortAdmission:async(context:any)=>{if(this.owner.admissionPending)throw Error('Owner requests are still in flight');return forwardAdmissionAbort((method,params)=>this.owner.request(method,params),context,ownerId);},reconcileRelease:(context:Json)=>release(context,context.outcome,context.proof)},args=>this.owner.request('quiescence.retention',args)),args=>this.owner.request('quiescence.managedFiles',args),async()=>(await this.owner.request('initialize',{})).quiescence?.managedFiles?.version===1);
 };
 inspectQuiescence=()=>this.owner.request('quiescence.inspect',{});
 private async authorize(scope:string,context:Context){
  if(!scope?.startsWith('ahp-session:/'))throw Error('Selected conversation required');
  const selected=typeof context.session==='string'?context.session:context.session?.uri;
  if(selected&&selected!==scope)throw Error('Session scope mismatch');
  await this.options.inspectSession(scope,{clientId:context.clientId});
 }
 private record(scope:string,data:Json){if(Buffer.byteLength(JSON.stringify(data))>768*1024)throw Error('Publishing page exceeds768KiB; request a narrower page');return {topic:'publishing',scope,revision:++this.revision,data};}
 actionSchemas=async()=>this.owner.request('actions',{});
 read=async(request:{uri:string;topic:string;scope:string;clientId:string})=>{
  const identity=new URL(request.uri);identity.search='';identity.hash='';
  if(request.topic!=='publishing'||identity.href!==this.manifest.topics.publishing.uri)throw Error('Unknown publishing topic');
  await this.authorize(request.scope,{clientId:request.clientId});return this.record(request.scope,await this.owner.request('snapshot',{session:request.scope}));
 };
 action=async(request:Json,context:Context)=>{
  if(request.version!==1||request.topic!=='publishing'||!this.manifest.actions[request.operation])throw Error('Unadvertised publishing action');
  await this.authorize(request.channel,context);
  const invoke=(workspace?:Json)=>this.owner.request('action',{session:request.channel,operation:request.operation,args:request.args??{},commandId:request.commandId,origin:context.origin??'ui',clientId:context.clientId,workspace});
  const result=request.operation==='publishing.build'?await this.options.withSessionWorkspace(request.channel,{},invoke):await invoke();
  // Refresh stays an explicit scoped read; a completed mutation must not fail because a later target read lost transport.
  return {accepted:true,result,updates:[],invalidate:['publishing']};
 };
 close=async()=>this.owner.close();
}
export function createPublishingCapabilities(options:Options){return new PublishingCapabilities(options);}
