import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
export type Json=Record<string,any>;
export interface Launcher {command:string;args?:string[];env?:Record<string,string>;cwd?:string;requestTimeoutMs?:number;initializeTimeoutMs?:number;}
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
export interface Options {
 owner:Launcher;
 listCoordinationSessions:(args:Json)=>Promise<Json>;
 readCoordinationSession:(session:string,args:Json)=>Promise<Json>;
 readCoordinationWorkers:(session:string,args:Json)=>Promise<Json>;
 /** Optional genuine questions/task-owner overlay. Missing authority remains explicit. */
 readCoordinationAttention?:(session:string,context:{clientId:string})=>Promise<Json>;
 controlCoordinationWorker:(session:string,operation:string,args:Json,context:{clientId:string})=>Promise<Json>;
 controlCoordinationSession:(args:Json)=>Promise<Json>;
 observeSession:(session:string,listener:()=>void|Promise<void>)=>Promise<()=>void>;
 onInvalidate?:(topic:string,scope:string)=>void;
 onMayBeIdle?:()=>void;
}
const METHODS=new Set(['listCoordinationSessions','readCoordinationSession','readCoordinationWorkers','controlCoordinationWorker','controlCoordinationSession','watch','unwatch']);
/** Two-way owner channel. Calls are never retried after lost transport. */
export class OwnerConnection {
 private process?:ChildProcessWithoutNullStreams;private ready?:Promise<void>;private next=0;private closed=false;private pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void;timer:NodeJS.Timeout}>();
 constructor(private launcher:Launcher,private callback:(method:string,args:Json)=>Promise<any>,private changed:(scope:string)=>void,private idle:()=>void=()=>{}){for(const timeout of [launcher.requestTimeoutMs,launcher.initializeTimeoutMs])if(timeout!==undefined&&(!Number.isInteger(timeout)||timeout<25||timeout>90000))throw Error('Owner timeout must be25–90000ms');}
 private fail(message:string){this.closed=true;for(const entry of this.pending.values()){clearTimeout(entry.timer);entry.reject(Error(message));}this.pending.clear();}
 private write(row:Json){const data=JSON.stringify(row)+'\n';if(Buffer.byteLength(data)>2_000_000)throw Error('Owner frame exceeds2MB');if(this.closed||!this.process)throw Error('Owner unavailable; uncertain work was not replayed');this.process.stdin.write(data,error=>{if(error)this.fail('Owner transport failed; outcome unknown.');});}
 private start(){
  if(this.closed)throw Error('Owner connection is closed; no automatic replay.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stderr.on('data',()=>{});child.on('error',()=>this.fail('Coordination owner failed to start'));child.on('exit',()=>this.fail('Coordination owner exited; uncertain commands not replayed'));
   let bytes=0;child.stdout.on('data',(chunk:Buffer)=>{for(const b of chunk){bytes=b===10?0:bytes+1;if(bytes>2_000_000){this.fail('Owner frame exceeded limit');child.kill();return;}}});
   createInterface({input:child.stdout}).on('line',line=>{void this.receive(line);});
   try{const value=await this.send('initialize',{},this.launcher.initializeTimeoutMs??15000);if(value.protocolVersion!==1)throw Error('Unsupported coordination owner');}catch(error){this.fail('Coordination owner initialization failed; no automatic retry.');child.kill();throw error;}
  })();return this.ready;
 }
 private async receive(line:string){
  let row:Json;try{row=JSON.parse(line);}catch{this.fail('Invalid owner response');this.process?.kill();return;}
  if(row.method==='owner/idle'){this.idle();return;}
  if(row.method==='owner/changed'){this.changed(row.params.session);return;}
  if(row.method){
   const method=String(row.method).replace(/^host\//,'');
   try{if(!String(row.method).startsWith('host/')||!METHODS.has(method))throw Error('Unknown host callback');const result=await this.callback(method,row.params??{});this.write({jsonrpc:'2.0',id:row.id,result:result??null});}
   catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:String(error instanceof Error?error.message:error)}});}return;
  }
  const entry=this.pending.get(row.id);if(!entry)return;this.pending.delete(row.id);clearTimeout(entry.timer);row.error?entry.reject(Object.assign(Error(row.error.message),row.error.data??{})):entry.resolve(row.result);
 }
 private send(method:string,params:Json,timeoutMs=this.launcher.requestTimeoutMs??90000){if(this.pending.size>=64)throw Error('Owner request capacity reached');const id=++this.next;return new Promise<any>((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(Object.assign(Error('Coordination owner reply timed out; outcome unknown and not replayed.'),{code:'unknown_outcome'}));},timeoutMs);timer.unref();this.pending.set(id,{resolve,reject,timer});try{this.write({jsonrpc:'2.0',id,method,params});}catch(e){clearTimeout(timer);this.pending.delete(id);reject(e as Error);}});}
 async request(method:string,args:Json){await this.start();return this.send(method,args);}
 async close(){if(!this.process){this.closed=true;return;}this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},4000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});this.fail('Owner closed');}
}

export class CoordinationCapabilities {
 readonly manifest={version:1,topics:{coordination:{uri:'amplifier-capability://coordination/coordination',version:1,watch:true,scope:'host'}},actions:Object.fromEntries(['list','wait','followup','interrupt','command'].map(name=>['coordination.'+name,{topic:'coordination',operation:'coordination.'+name,method:'x-amplifier/capabilityAction'}]))};
 readonly quiescenceAccess={'coordination.list':'read','coordination.wait':'read','coordination.command':'read'} as const;
 private owner:OwnerConnection;private revision=0;private watches=new Map<string,{sessions:string[];release:(()=>void)[];refresh?:Promise<void>;dirty:boolean}>();
 constructor(private options:Options){this.owner=new OwnerConnection(options.owner,(method,args)=>this.callback(method,args),()=>options.onInvalidate?.('coordination','host'),()=>options.onMayBeIdle?.());}
 private async callback(method:string,args:Json):Promise<any>{
  if(method==='listCoordinationSessions')return this.options.listCoordinationSessions(args.args);
  if(method==='readCoordinationSession'){const value=await this.options.readCoordinationSession(args.session,args.args);if(!this.options.readCoordinationAttention)return value;const attention=await this.options.readCoordinationAttention(args.session,{clientId:args.args.clientId});if(!attention||typeof attention!=='object'||Array.isArray(attention)||Buffer.byteLength(JSON.stringify(attention))>32768)throw Error('Selected attention metadata exceeds32KB');const coverage={...value.attentionCoverage,...attention.attentionCoverage,...Object.fromEntries(['approvals','approvalsTruncated'].filter(k=>k in (value.attentionCoverage??{})).map(k=>[k,value.attentionCoverage[k]]))};const complete=attention.attentionComplete===true&&coverage.approvals!==false&&!coverage.approvalsTruncated;return {...value,...Object.fromEntries(['questionIds','task','omissions'].filter(k=>k in attention).map(k=>[k,attention[k]])),attentionCoverage:coverage,attentionComplete:complete,attentionUnknown:!complete};}
  if(method==='readCoordinationWorkers'){const {clientId,...bounded}=args.args;return this.options.readCoordinationWorkers(args.session,bounded);}
  if(method==='controlCoordinationWorker')return this.options.controlCoordinationWorker(args.session,args.operation,args.args,{clientId:args.clientId});
  if(method==='controlCoordinationSession')return this.options.controlCoordinationSession(args);
  if(method==='unwatch'){const entry=this.watches.get(args.token);this.watches.delete(args.token);for(const release of entry?.release??[])release();return {};}
  if(method==='watch'){
   if(this.watches.size>=32||this.watches.has(args.token)||!Array.isArray(args.sessions)||args.sessions.length>8||args.sessions.some((s:any)=>typeof s!=='string'||!s.startsWith('ahp-session:/')))throw Error('Bounded explicit coordination watch required');
   const entry={sessions:args.sessions,release:[] as (()=>void)[],dirty:false};this.watches.set(args.token,entry);
   try{for(const session of entry.sessions)entry.release.push(await this.options.observeSession(session,()=>this.refresh(args.token)));}
   catch(error){this.watches.delete(args.token);for(const release of entry.release)release();throw error;}return {};
  }
  throw Error('Unknown coordination callback');
 }
 private refresh(token:string){const entry=this.watches.get(token);if(!entry)return;entry.dirty=true;if(entry.refresh)return entry.refresh;
  entry.refresh=(async()=>{while(entry.dirty&&this.watches.has(token)){entry.dirty=false;await this.owner.request('changed',{token});}})().catch(()=>{this.options.onInvalidate?.('coordination','host');}).finally(()=>{entry.refresh=undefined;});return entry.refresh;
 }
 /** Composition forwards native workers.changed and existing public session events. No owner starts for an unwatched event. */
 changed(session:string){for(const [token,entry] of this.watches)if(entry.sessions.includes(session))void this.refresh(token);this.options.onInvalidate?.('coordination','host');}
 actionSchemas=async()=>this.owner.request('actions',{});
 read=async(request:{uri:string;topic:string;scope:string;clientId:string})=>{const url=new URL(request.uri);url.search='';url.hash='';if(request.topic!=='coordination'||url.href!==this.manifest.topics.coordination.uri||!['host','ahp-root://'].includes(request.scope))throw Error('Host-scoped coordination topic required');const value=await this.owner.request('snapshot',{clientId:request.clientId});return {topic:'coordination',scope:'host',revision:++this.revision,data:{coordination:value}};};
 action=async(request:Json,context:Context)=>{
  if(request.version!==1||request.topic!=='coordination'||!this.manifest.actions[request.operation])throw Error('Unadvertised coordination action');
  const caller=typeof context.session==='string'?context.session:context.session?.uri;
  if(!['host','ahp-root://'].includes(request.channel)&&request.channel!==caller)throw Error('Authenticated coordination scope required');
  const result=await this.owner.request('action',{operation:request.operation,args:request.args??{},commandId:request.commandId,clientId:context.clientId,origin:context.origin??'ui',callerSession:caller});
  return {accepted:true,result,updates:[],invalidate:['coordination']};
 };
 quiescenceParticipant(ownerId:string){
  const release=async(context:Json,outcome:string,proof?:Json)=>{const result=await this.owner.request('quiescence.release',{...context,outcome,proof});if(outcome!=='unknown'&&result.released!==true)throw Error('Coordination fence release is unconfirmed');};
  return {id:ownerId,acquire:async(context:Json)=>{const exact={...context},value=await this.owner.request('quiescence.acquire',exact);if(value.acquired!==true)return null;if(value.fenceId!==exact.fenceId||value.intakeClosed!==true)throw Error('Coordination fence acquisition is unconfirmed');return {ownerId,fenceId:exact.fenceId,release:(outcome:string,proof?:Json)=>release(exact,outcome,proof)};},reconcileRelease:(context:Json)=>release(context,context.outcome,context.proof)};
 }
 close=async()=>{for(const entry of this.watches.values())for(const release of entry.release)release();this.watches.clear();await this.owner.close();};
}
export function createCoordinationCapabilities(options:Options){return new CoordinationCapabilities(options);}
