import {managedParticipant} from './managed-files.js';
import {retentionParticipant} from './retention.js';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {serviceIdentity,validateServiceRelease,evidenceKey,type ServiceIdentity,type ServiceReleaseFields} from './service-lifecycle.js';
export type {ServiceIdentity,ServiceReleaseFields} from './service-lifecycle.js';
export type Json=Record<string,any>;
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
export interface Launcher {command:string;args?:string[];env?:Record<string,string>;cwd?:string;requestTimeoutMs?:number;initializeTimeoutMs?:number;onMayBeIdle?:()=>void;ownerId?:string;}
export interface FenceContext {fenceId:string;commandId:string;purpose:'recovery'|'distribution-update'|'service-stop'|'retention-hide'|'managed-files-disposal';instanceId:string;dataScope:string;serviceIdentity?:ServiceIdentity;}
export type ReleaseProof={verified:true;fenceId:string;commandId:string;outcome:'unchanged'|'ready';instanceId:string;dataScope:string;receiptId:string}&Partial<ServiceReleaseFields>;
export interface HeldLease {inspectRetentionReferences?:(args:any)=>Promise<any>;inspectManagedFilesReferences?:(args:any)=>Promise<any>;ownerId:string;fenceId:string;release:(outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>Promise<void>;}
export interface Participant {managedFiles?:{version:1;preservesCanonical:true};retentionHide?:{version:1};id:string;serviceStop?:{version:1};acquire:(context:Readonly<FenceContext>)=>Promise<HeldLease|null>;reconcileRelease:(context:Readonly<FenceContext>&{outcome:'unchanged'|'ready';proof:ReleaseProof})=>Promise<void>;}
export interface Options {
 owner:Launcher;
 inspectSession:(uri:string,context?:{clientId:string})=>Promise<Json>;
 beginTransfer:(session:string,args:Json)=>Promise<Json>;
 commitTransfer:(session:string,args:Json)=>Promise<Json>;
 cancelTransfer:(session:string,args:Json)=>Promise<Json>;
 adoptTransferredSession:(args:Json)=>Promise<Json>;
 nativeTransfer:(args:Json)=>Promise<Json>;
 exportTransferEvidence:(args:Json)=>Promise<Json>;
 stageTransferEvidence:(args:Json)=>Promise<Json>;
 activateTransferEvidence:(args:Json)=>Promise<Json>;
 /** Optional explicitly negotiated immutable historical resource payload ports. */
 resourcePayloads?:{metadata:(args:Json)=>Promise<Json>;readSource:(args:Json)=>Promise<Json>;stage:(args:Json)=>Promise<Json>;};
 authorizeTransfer?:(args:Json)=>Promise<Json>;
 onInvalidate?:(topic:string,scope:string)=>void;
 onMayBeIdle?:()=>void;
 /** Actual configured passive-native peers; absent proof refuses maintenance. */
 nativeParticipants?:ReadonlyArray<Participant>;
}
const METHODS=new Set(['inspectSession','beginTransfer','commitTransfer','cancelTransfer','adoptTransferredSession','nativeTransfer','exportTransferEvidence','stageTransferEvidence','activateTransferEvidence','authorizeTransfer','payloadCapabilities','readTransferAttachmentMetadata','readTransferPayloadSource','stageTransferPayloads']);
const readActions=new Set(['portability.inspect','portability.review','portability.command','portability.receipt','portability.evidence']);
const mutations=new Set(['portability.export','portability.stage','portability.release','portability.activate','portability.cancel','portability.discard','portability.reconcile']);
const actions=['inspect','review','export','stage','release','activate','cancel','discard','evidence','receipt','command','reconcile'].map(name=>'portability.'+name);
/** Two-way owner channel. Calls are never retried after lost transport. */
export class OwnerConnection {
 private process?:ChildProcessWithoutNullStreams;private ready?:Promise<void>;private next=0;private closed=false;private pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void;timer:NodeJS.Timeout}>();
 private calls=0;private callbacks=0;private supported=false;private serviceStopSupported=false;private held?:{context:FenceContext;phase:'checking'|'held'|'unknown'};private releases=new Map<string,string>();
 constructor(private launcher:Launcher,private callback:(method:string,args:Json)=>Promise<any>,private changed:(scope:string)=>void,private mayBeIdle:()=>void=()=>{}){for(const timeout of [launcher.requestTimeoutMs,launcher.initializeTimeoutMs])if(timeout!==undefined&&(!Number.isInteger(timeout)||timeout<25||timeout>90000))throw Error('Owner timeout must be25–90000ms');}
 private idle(){try{this.mayBeIdle();}catch{/* Advisory only. */}}
 private fail(message:string){this.closed=true;if(this.held)this.held.phase='unknown';for(const entry of this.pending.values()){clearTimeout(entry.timer);entry.reject(Error(message));}this.pending.clear();}
 private write(row:Json){const data=JSON.stringify(row)+'\n';if(Buffer.byteLength(data)>48_000_000)throw Error('Owner frame exceeds48MB');if(this.closed||!this.process)throw Error('Owner unavailable; uncertain work was not replayed');this.process.stdin.write(data,error=>{if(error)this.fail('Owner transport failed; outcome unknown.');});}
 private start(){
  if(this.closed)throw Error('Owner connection is closed; no automatic replay.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stderr.on('data',()=>{});child.on('error',()=>this.fail('Portability owner failed to start'));child.on('exit',()=>this.fail('Portability owner exited; uncertain commands not replayed'));
   let bytes=0;child.stdout.on('data',(chunk:Buffer)=>{for(const b of chunk){bytes=b===10?0:bytes+1;if(bytes>48_000_000){this.fail('Owner frame exceeded limit');child.kill();return;}}});
   createInterface({input:child.stdout}).on('line',line=>{void this.receive(line);});
   try{const value=await this.send('initialize',{},this.launcher.initializeTimeoutMs??15000);if(value.protocolVersion!==1)throw Error('Unsupported portability owner');this.supported=value.quiescence?.version===1&&value.quiescence?.heldIntake===true&&value.quiescence?.durableRelease===true;this.serviceStopSupported=value.quiescence?.serviceStop?.version===1;}catch(error){this.fail('Portability owner initialization failed; no automatic retry.');child.kill();throw error;}
  })();return this.ready;
 }
 private async receive(line:string){
  let row:Json;try{row=JSON.parse(line);}catch{this.fail('Invalid owner response');this.process?.kill();return;}
  if(row.method==='owner/idle'){this.idle();return;}
  if(row.method==='owner/changed'){this.changed(row.params.session);return;}
  if(row.method){
   const method=String(row.method).replace(/^host\//,'');this.callbacks++;
   try{if(!String(row.method).startsWith('host/')||!METHODS.has(method))throw Error('Unknown host callback');const result=await this.callback(method,row.params??{});this.write({jsonrpc:'2.0',id:row.id,result:result??null});}
   catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:String(error instanceof Error?error.message:error)}});}finally{this.callbacks--;if(!this.calls&&!this.callbacks)this.idle();}return;
  }
  const entry=this.pending.get(row.id);if(!entry)return;this.pending.delete(row.id);clearTimeout(entry.timer);row.error?entry.reject(Object.assign(Error(row.error.message),row.error.data??{})):entry.resolve(row.result);
 }
 private send(method:string,params:Json,timeoutMs=this.launcher.requestTimeoutMs??90000){if(this.pending.size>=64)throw Error('Owner request capacity reached');const id=++this.next;return new Promise<any>((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(Object.assign(Error('Portability owner reply timed out; outcome unknown and not replayed.'),{code:'unknown_outcome'}));},timeoutMs);timer.unref();this.pending.set(id,{resolve,reject,timer});try{this.write({jsonrpc:'2.0',id,method,params});}catch(e){clearTimeout(timer);this.pending.delete(id);reject(e as Error);}});}
 async request(method:string,args:Json){
  if(this.held&&method==='action'&&mutations.has(args.operation))throw Object.assign(Error('Portability intake is held; no mutation admitted'),{executed:false,code:'quiescence_fenced'});
  this.calls++;try{await this.start();return await this.send(method,args);}finally{this.calls--;if(!this.calls&&!this.callbacks)this.idle();}
 }
 private context(value:Readonly<FenceContext>):FenceContext{const result={} as FenceContext;for(const key of ['fenceId','commandId','purpose','instanceId','dataScope'] as const){if(typeof value[key]!=='string'||!value[key]||value[key].length>200||/[\x00-\x1f]/.test(value[key]))throw Error('Bounded trusted portability fence required');(result as Json)[key]=value[key];}if(value.purpose==='service-stop'){result.serviceIdentity=serviceIdentity(value.serviceIdentity);if(result.serviceIdentity.instanceId!==value.instanceId||result.serviceIdentity.dataScope!==value.dataScope)throw Error('Service identity must bind portability fence');}else if(value.serviceIdentity)throw Error('Service identity requires service-stop');return result;}
 inspectQuiescence=async()=>{await this.start();return this.send('quiescence/inspect',{});};
 private acquire=async(value:Readonly<FenceContext>)=>{
  const context=this.context(value);if(this.calls||this.callbacks)return null;
  if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Portability owner is held by another fence');if(this.held)return null;
  this.held={context,phase:'checking'};
  try{await this.start();if(!this.supported||(context.purpose==='service-stop'&&!this.serviceStopSupported)){this.held=undefined;return null;}const result=await this.send('quiescence/acquire',context);
   if(result.acquired!==true){if(result.executed===false){this.held=undefined;return null;}throw Error('Portability quiescence outcome is uncertain');}
   if(result.fenceId!==context.fenceId||result.intakeClosed!==true)throw Error('Portability owner did not confirm the exact held fence');
   this.held.phase='held';return {ownerId:'portability-owner',fenceId:context.fenceId,release:(outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>this.release(context,outcome,proof,true)};
  }catch(error){if(this.held)this.held.phase='unknown';throw error;}
 };
 private release=async(value:Readonly<FenceContext>,outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'},liveRollback=false)=>{
  const context=this.context(value),evidence=evidenceKey({context,outcome,proof}),prior=this.releases.get(context.fenceId);if(prior!==undefined){if(prior!==evidence||this.held)throw Error('Portability release receipt binds different evidence');return;}if(this.calls||this.callbacks)throw Error('Portability owner requests are still in flight');
  if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Portability release does not match held fence');
  const rollback=liveRollback&&this.held?.phase==='held'&&outcome==='unchanged'&&proof&&Object.keys(proof).length===1&&'kind' in proof&&proof.kind==='admission-refused';
  if(context.purpose==='service-stop'&&outcome!=='unknown'&&!rollback)validateServiceRelease(context,outcome,proof);
  this.held={context,phase:'unknown'};await this.start();if(!this.supported)throw Error('Portability owner lacks durable release proof');
  const result=await this.send('quiescence/release',{...context,outcome,proof});
  if(outcome==='unknown')return;if(result.released!==true||result.intakeClosed!==false)throw Error('Portability owner release is unconfirmed');this.releases.set(context.fenceId,evidence);if(this.releases.size>256)this.releases.delete(this.releases.keys().next().value!);this.held=undefined;
 };
 get quiescenceParticipant(){return managedParticipant(retentionParticipant({id:'portability-owner',serviceStop:{version:1 as const},acquire:this.acquire,reconcileRelease:(context:Readonly<FenceContext>&{outcome:'unchanged'|'ready';proof:ReleaseProof})=>this.release(context,context.outcome,context.proof)},args=>this.send('quiescence.retention',args)),args=>this.send('quiescence.managedFiles',args),async()=>{await this.start();return (await this.send('initialize',{})).quiescence?.managedFiles?.version===1});}

 async close(){if(!this.process||this.process.exitCode!==null||this.process.signalCode!==null){this.closed=true;return;}this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},4000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});this.fail('Owner closed');}
}

export class PortabilityCapabilities {
 readonly manifest={version:1,topics:{portability:{uri:'amplifier-capability://portability/portability',version:1,watch:true,scope:'host'}},actions:Object.fromEntries(actions.map(operation=>[operation,{topic:'portability',operation,method:'x-amplifier/capabilityAction'}]))};
 private owner:OwnerConnection;private revision=0;private active=0;
 constructor(private options:Options){if(options.resourcePayloads&&['metadata','readSource','stage'].some(key=>typeof (options.resourcePayloads as Json)[key]!=='function'))throw Error('Detached resource payloads require configured metadata/read/stage ports');this.owner=new OwnerConnection(options.owner,async(method,params)=>{
  if(method==='payloadCapabilities')return options.resourcePayloads?{version:1,owner:'unified.resources',maxRecords:500,maxIdCodeUnits:200,chunkBytes:262144,maxBodyBytes:67108864,maxTotalBytes:1073741824}:null;
  if(method==='readTransferAttachmentMetadata'){if(!options.resourcePayloads)throw Error('Historical resource metadata port unavailable');return options.resourcePayloads.metadata(params);}
  if(method==='readTransferPayloadSource'){if(!options.resourcePayloads)throw Error('Historical resource source port unavailable');return options.resourcePayloads.readSource(params);}
  if(method==='stageTransferPayloads'){if(!options.resourcePayloads)throw Error('Historical resource import port unavailable');return options.resourcePayloads.stage(params);}
  if(method==='inspectSession')return options.inspectSession(params.session);
  if(['beginTransfer','commitTransfer','cancelTransfer'].includes(method)){const {session,...args}=params;return options[method as 'beginTransfer'](session,args);}
  if(method==='authorizeTransfer'){
   if(options.authorizeTransfer)return options.authorizeTransfer(params);
   if(params.origin==='ui')return {approved:true,kind:'explicit-ui',commandId:params.commandId};
   throw Error('Explicit transfer approval is required for agent-origin requests');
  }
  if(['adoptTransferredSession','nativeTransfer','exportTransferEvidence','stageTransferEvidence','activateTransferEvidence'].includes(method))return options[method as 'nativeTransfer'](params);
  throw Error('Unknown portability authority callback');
 },_scope=>options.onInvalidate?.('portability','host'),()=>this.idle());}
 readonly quiescenceAccess=Object.fromEntries([...readActions].map(operation=>[operation,'read' as const]));
 private idle(){try{this.options.onMayBeIdle?.();}catch{/* Advisory only. */}}
 private async tracked<T>(work:()=>Promise<T>){this.active++;try{return await work();}finally{this.active--;if(!this.active)this.idle();}}
 get quiescenceParticipant():Participant{return {id:'portability',serviceStop:{version:1 as const},retentionHide:{version:1 as const},managedFiles:{version:1 as const,preservesCanonical:true as const},acquire:async context=>{
  if(this.active)return null;
  const native=['retention-hide','managed-files-disposal'].includes(context.purpose)?[]:this.options.nativeParticipants;if(!native||!['retention-hide','managed-files-disposal'].includes(context.purpose)&&!native.length)return null;
  if(context.purpose==='service-stop'&&native.some(p=>p.serviceStop?.version!==1))return null;
  if(new Set(native.map(p=>p.id)).size!==native.length)throw Error('Distinct configured native participant identities required');
  const leases:HeldLease[]=[];const own=await this.owner.quiescenceParticipant.acquire(context);if(!own)return null;leases.push(own);
  for(const participant of native){const lease=await participant.acquire(context);if(!lease){for(const held of [...leases].reverse())await held.release('unchanged',{kind:'admission-refused'});return null;}leases.push(lease);}
  return {ownerId:'portability',fenceId:context.fenceId,inspectRetentionReferences:(args:any)=>(own as any).inspectRetentionReferences(args),inspectManagedFilesReferences:(args:any)=>(own as any).inspectManagedFilesReferences(args),release:async(outcome,proof)=>{for(const held of [...leases].reverse())await held.release(outcome,proof);}};
 },reconcileRelease:async context=>{
  if(this.active)throw Error('Portability effects remain active');
  if(!['retention-hide','managed-files-disposal'].includes(context.purpose)){if(!this.options.nativeParticipants?.length)throw Error('Native transfer quiescence coverage unavailable');for(const participant of [...this.options.nativeParticipants].reverse())await participant.reconcileRelease(context);}
  await this.owner.quiescenceParticipant.reconcileRelease(context);
 }};}
 /** Trusted composition-only verifier; never advertised as a client action. */
 verifyTransferPayloadPlan=(args:Json)=>this.tracked(()=>this.owner.request('payload/verify',args));
 inspectQuiescence=()=>this.owner.inspectQuiescence();
 private async scope(channel:string,context:Context){
  if(channel==='ahp-root://' || channel==='host')return 'host';
  if(!channel?.startsWith('ahp-session:/'))throw Error('Authenticated root or session scope required');
  const selected=typeof context.session==='string'?context.session:context.session?.uri;
  if(selected&&selected!==channel)throw Error('Session scope mismatch');
  await this.options.inspectSession(channel,{clientId:context.clientId});return channel;
 }
 actionSchemas=async()=>this.owner.request('actions',{});
 read=async(request:{uri:string;topic:string;scope:string;clientId:string})=>this.tracked(async()=>{
  const url=new URL(request.uri);url.search='';url.hash='';
  if(request.topic!=='portability'||url.href!==this.manifest.topics.portability.uri)throw Error('Unknown portability topic');
  const scope=await this.scope(request.scope,{clientId:request.clientId});const data=await this.owner.request('snapshot',{session:scope});
  if(Buffer.byteLength(JSON.stringify(data))>768*1024)throw Error('Transfer page exceeds topic bound');
  return {topic:'portability',scope,revision:++this.revision,data:{portability:data}};
 });
 action=async(request:Json,context:Context)=>this.tracked(async()=>{
  if(request.version!==1||request.topic!=='portability'||!this.manifest.actions[request.operation])throw Error('Unadvertised portability operation');
  const selected=request.args?.sessionId;
  const channel=(request.channel==='ahp-root://' || request.channel==='host') && selected!==undefined ? selected : request.channel;
  const scope=await this.scope(channel,context);
  let result:Json;try{result=await this.owner.request('action',{session:scope,operation:request.operation,args:request.args??{},commandId:request.commandId,origin:context.origin??'ui',clientId:context.clientId});}catch(error){if((error as Json).executed===false)return {accepted:false,result:{executed:false,reason:(error as Error).message},updates:[],invalidate:[]};throw error;}
  if(scope!=='host')this.options.onInvalidate?.('portability','host');
  return {accepted:true,result,updates:[],invalidate:['portability']};
 });
 close=async()=>this.owner.close();
}
export function createPortabilityCapabilities(options:Options){return new PortabilityCapabilities(options);}
export {TransferConnection} from './native.js';
