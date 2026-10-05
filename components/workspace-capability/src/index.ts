import {forwardAdmissionAbort,recordAdmissionRefusal} from './admission-abort.js';
import {managedParticipant} from './managed-files.js';
import {retentionParticipant} from './retention.js';
import {serviceIdentity,validateServiceRelease,evidenceKey,type ServiceIdentity,type ServiceReleaseFields} from './service-lifecycle.js';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {pathToFileURL} from 'node:url';
export type Json=Record<string,any>;
export interface Launcher {command:string;args?:string[];env?:Record<string,string>;cwd?:string;requestTimeoutMs?:number;initializeTimeoutMs?:number;}
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
/** A configured, trusted derived catalog. All authorization roots come from owner config. */
export interface WorkspaceCatalog {
 listWorkspaces:(args:Json)=>Promise<Json>;
 getWorkspace:(args:{id:string})=>Promise<Json|null>;
 projectWorkspaces:(args:Json)=>Promise<Json>;
 workspaceProjectionStatus:(args:{source:string})=>Promise<Json>;
 list:(args:{connectionId:string;limit:number;cursor?:string;workingDirectory?:string;search?:string;parentUri?:string;allowedWorkspaceRoots?:string[];includeArchive?:boolean;archive?:'active'|'all'|'archived'})=>Promise<Json>;
}
export interface FenceContext {fenceId:string;commandId:string;purpose:'recovery'|'distribution-update'|'service-stop'|'retention-hide'|'managed-files-disposal';instanceId:string;dataScope:string;serviceIdentity?:ServiceIdentity;}
export type ReleaseProof={verified:true;fenceId:string;commandId:string;outcome:'unchanged'|'ready';instanceId:string;dataScope:string;receiptId:string}&Partial<ServiceReleaseFields>;
export interface Options {owner:Launcher;catalog:WorkspaceCatalog;/** Trusted Host query callback; never a client-selected DB or authority. */libraryQuery?:(args:Json)=>Promise<Json>;onInvalidate?:(topic:string,scope:string)=>void;onMayBeIdle?:()=>void;}
const METHODS=new Set(['listWorkspaces','getWorkspace','projectWorkspaces','workspaceProjectionStatus','list','libraryQuery']);
/** Two-way owner channel. Calls are never retried after lost transport. */
export class OwnerConnection {
 private process?:ChildProcessWithoutNullStreams;private ready?:Promise<void>;private next=0;private closed=false;private pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void;timer:NodeJS.Timeout}>();
 private calls=0;private callbacks=0;private supported=false;private serviceStopSupported=false;private releases=new Map<string,string>();private held?:{context:FenceContext;phase:'checking'|'held'|'unknown'};
 constructor(private launcher:Launcher,private callback:(method:string,args:Json)=>Promise<any>,private changed:(scope:string)=>void,private mayBeIdle:()=>void=()=>{},private libraryEnabled=false){for(const timeout of [launcher.requestTimeoutMs,launcher.initializeTimeoutMs])if(timeout!==undefined&&(!Number.isInteger(timeout)||timeout<25||timeout>90000))throw Error('Owner timeout must be25–90000ms');}
 private fail(message:string){this.closed=true;if(this.held)this.held.phase='unknown';for(const entry of this.pending.values()){clearTimeout(entry.timer);entry.reject(Error(message));}this.pending.clear();}
 private write(row:Json){const data=JSON.stringify(row)+'\n';if(Buffer.byteLength(data)>2_000_000)throw Error('Owner frame exceeds2MB');if(this.closed||!this.process)throw Error('Owner unavailable; uncertain work was not replayed');this.process.stdin.write(data,error=>{if(error)this.fail('Owner transport failed; outcome unknown.');});}
 private start(){
  if(this.closed)throw Error('Owner connection is closed; no automatic replay.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stderr.on('data',()=>{});child.on('error',()=>this.fail('Workspace owner failed to start'));child.on('exit',()=>this.fail('Workspace owner exited; uncertain commands not replayed'));
   let bytes=0;child.stdout.on('data',(chunk:Buffer)=>{for(const b of chunk){bytes=b===10?0:bytes+1;if(bytes>2_000_000){this.fail('Owner frame exceeded limit');child.kill();return;}}});
   createInterface({input:child.stdout}).on('line',line=>{void this.receive(line);});
   try{const value=await this.send('initialize',this.libraryEnabled?{libraryQueryVersion:1}:{},this.launcher.initializeTimeoutMs??15000);if(value.protocolVersion!==1)throw Error('Unsupported workspace owner');this.supported=value.quiescence?.version===1&&value.quiescence?.heldIntake===true&&value.quiescence?.durableRelease===true;this.serviceStopSupported=value.quiescence?.serviceStop?.version===1;}catch(error){this.fail('Workspace owner initialization failed; no automatic retry.');child.kill();throw error;}
  })();return this.ready;
 }
 private async receive(line:string){
  let row:Json;try{row=JSON.parse(line);}catch{this.fail('Invalid owner response');this.process?.kill();return;}
  if(row.method==='owner/idle'){this.mayBeIdle();return;}
  if(row.method==='owner/changed'){this.changed(row.params.session);return;}
  if(row.method){
   const method=String(row.method).replace(/^catalog\//,'');this.callbacks++;
   try{if(!String(row.method).startsWith('catalog/')||!METHODS.has(method))throw Error('Unknown host callback');const result=await this.callback(method,row.params??{});this.write({jsonrpc:'2.0',id:row.id,result:result??null});}
   catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:String(error instanceof Error?error.message:error)}});}finally{this.callbacks--;if(!this.calls&&!this.callbacks)this.mayBeIdle();}return;
  }
  const entry=this.pending.get(row.id);if(!entry)return;this.pending.delete(row.id);clearTimeout(entry.timer);row.error?entry.reject(Object.assign(Error(row.error.message),row.error.data??{},{knownRefusal:row.error.data?.knownRefusal===true})):entry.resolve(row.result);
 }
 private send(method:string,params:Json,timeoutMs=this.launcher.requestTimeoutMs??90000){if(this.pending.size>=64)throw Error('Owner request capacity reached');const id=++this.next;return new Promise<any>((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(Object.assign(Error('Workspace owner reply timed out; outcome unknown and not replayed.'),{code:'unknown_outcome'}));},timeoutMs);timer.unref();this.pending.set(id,{resolve,reject,timer});try{this.write({jsonrpc:'2.0',id,method,params});}catch(e){clearTimeout(timer);this.pending.delete(id);reject(e as Error);}});}
 async request(method:string,args:Json){
  if(this.held&&method==='action'&&mutations.has(args.operation))throw Object.assign(Error('Workspace intake is held; no mutation admitted'),{executed:false,code:'quiescence_fenced'});
  this.calls++;try{await this.start();return await this.send(method,args);}finally{this.calls--;if(!this.calls&&!this.callbacks)this.mayBeIdle();}
 }
 private context(value:Readonly<FenceContext>):FenceContext{const result={} as FenceContext;for(const key of ['fenceId','commandId','purpose','instanceId','dataScope'] as const){if(typeof value[key]!=='string'||!value[key]||value[key].length>200||/[\x00-\x1f]/.test(value[key]))throw Error('Bounded trusted workspace fence required');(result as Json)[key]=value[key];}if(value.purpose==='service-stop'){result.serviceIdentity=serviceIdentity(value.serviceIdentity);if(result.serviceIdentity.instanceId!==value.instanceId||result.serviceIdentity.dataScope!==value.dataScope)throw Error('Service identity differs from workspace fence');}else if(value.serviceIdentity)throw Error('Service identity requires service-stop');return result;}
 inspectQuiescence=async()=>{await this.start();return this.send('quiescence/inspect',{});};
 private acquire=async(value:Readonly<FenceContext>)=>{
  const context=this.context(value);if(this.calls||this.callbacks){if(context.purpose==='distribution-update'){await this.start();await recordAdmissionRefusal((method,params)=>this.send(method,params),context);}return null;}
  if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Workspace owner is held by another fence');
  this.held={context,phase:'checking'};
  try{await this.start();if(!this.supported||(context.purpose==='service-stop'&&!this.serviceStopSupported)){this.held=undefined;return null;}const result=await this.send('quiescence/acquire',context);
   if(result.acquired!==true){if(result.executed===false){this.held=undefined;return null;}throw Error('Workspace quiescence outcome is uncertain');}
   if(result.fenceId!==context.fenceId||result.intakeClosed!==true)throw Error('Workspace owner did not confirm the exact held fence');
   this.held.phase='held';return {ownerId:'workspaces',fenceId:context.fenceId,release:(outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>this.release(context,outcome,proof,true)};
  }catch(error){if(this.held)this.held.phase='unknown';throw error;}
 };
 private release=async(value:Readonly<FenceContext>,outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'},liveRollback=false)=>{
  const context=this.context(value),evidence=evidenceKey({context,outcome,proof}),prior=this.releases.get(context.fenceId);if(prior!==undefined){if(prior!==evidence||this.held)throw Error('Workspace release receipt binds different evidence');return;}if(this.calls||this.callbacks)throw Error('Workspace owner requests are still in flight');
  if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Workspace release does not match held fence');
  const rollback=liveRollback&&this.held?.phase==='held'&&outcome==='unchanged'&&proof&&Object.keys(proof).length===1&&'kind' in proof&&proof.kind==='admission-refused';if(context.purpose==='service-stop'&&outcome!=='unknown'&&!rollback)validateServiceRelease(context,outcome,proof);const before=this.held;this.held={context,phase:'unknown'};await this.start();if(!this.supported)throw Error('Workspace owner lacks durable release proof');
  let result;try{result=await this.send('quiescence/release',{...context,outcome,proof});}catch(error){if((error as Error&{knownRefusal?:boolean}).knownRefusal)this.held=before;throw error;}
  if(outcome==='unknown')return;if(result.released!==true||result.intakeClosed!==false)throw Error('Workspace owner release is unconfirmed');this.releases.set(context.fenceId,evidence);if(this.releases.size>256)this.releases.delete(this.releases.keys().next().value!);this.held=undefined;
 };
 private abortAdmission=async(input:any)=>{const context=this.context(input);if(this.calls||this.callbacks||this.pending.size)throw Error('Workspace requests are still in flight');await this.start();const result=await forwardAdmissionAbort((method,params)=>this.send(method,params),input,'workspaces','quiescence/abortAdmission');if(this.held&&JSON.stringify(this.held.context)===JSON.stringify(context))this.held=undefined;return result;};
 get quiescenceParticipant(){return managedParticipant(retentionParticipant({id:'workspaces',serviceStop:{version:1 as const},acquire:this.acquire,abortAdmission:this.abortAdmission,reconcileRelease:(context:Readonly<FenceContext>&{outcome:'unchanged'|'ready';proof:ReleaseProof})=>this.release(context,context.outcome,context.proof)},args=>this.send('quiescence.retention',args)),args=>this.send('quiescence.managedFiles',args),async()=>{await this.start();return (await this.send('initialize',{})).quiescence?.managedFiles?.version===1});}

 async close(){if(!this.process||this.process.exitCode!==null||this.process.signalCode!==null){this.closed=true;return;}this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},4000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});this.fail('Owner closed');}
}

const string=(maxLength:number)=>({type:'string',minLength:1,maxLength});
const schema=(properties:Json,required:string[]=[])=>({type:'object',properties,required,additionalProperties:false});
const id=string(128),page={query:string(200),cursor:string(4096),limit:{type:'integer',minimum:1,maximum:50}};
export const workspaceActions:Json={
 'workspace.list':{description:'List existing, visible workspace directories in bounded pages. Selection and editing remain local to the client.',schema:schema({...page,includeHidden:{type:'boolean'},includeUnavailable:{type:'boolean'}})},
 'workspace.inspect':{description:'Inspect an explicit indexed workspace and its present directory identity. Does not start an agent.',schema:schema({id},['id'])},
 'workspace.prepare':{description:'Prepare a name-based folder creation plan within configured roots. Existing directories require explicit attachment. Does not create a folder.',schema:schema({name:string(200),root:string(4000)},['name'])},
 'workspace.create':{description:'Create the exact prepared folder once. Unknown outcomes require receipt inspection; never replay creation.',schema:schema({planId:string(100)},['planId'])},
 'workspace.add':{description:'Register an existing directory without overwriting its contents.',schema:schema({path:string(4000),name:string(200)},['path'])},
 'workspace.rename':{description:'Change a workspace display name at the exact registration revision. Never moves or renames its directory.',schema:schema({id,name:string(200),expectedRevision:{type:'integer',minimum:0}},['id','name','expectedRevision'])},
 'workspace.remove':{description:'Hide workspace registration and ordinary session discovery. All directories and chat history are retained; explicit attachment restores discovery.',schema:schema({id,expectedRevision:{type:'integer',minimum:0}},['id','expectedRevision'])},
 'locations.list':{description:'Browse one authorized folder. Defaults to directories only; bounded pages contain no file contents or symlink targets.',schema:schema({path:string(4000),directoriesOnly:{type:'boolean'},controlId:string(200),limit:{type:'integer',minimum:1,maximum:100},cursor:string(8192)})},
 'locations.create':{description:'Create one named child directory exactly once without registering a workspace or creating a chat.',schema:schema({path:string(4000),name:string(255),controlId:string(200)},['path','name'])},
 'workspace.defaults':{description:'Read the shared default workspace root and current configuration revision.',schema:schema({})},
 'workspace.defaults.set':{description:'Set the shared authorized default root at its exact revision. Empty root resets the launcher default. Does not move existing directories.',schema:schema({defaultRoot:{type:'string',maxLength:4000},expectedConfigRevision:string(128)},['defaultRoot','expectedConfigRevision'])},
 'workspace.sessions':{description:'List a bounded page of root sessions across authorized existing visible workspaces, or one selected workspace. Children require an explicit parent URI.',schema:schema({id,...page,parentUri:string(8192),archive:{enum:['active','all','archived']},libraryQueryVersion:{const:1},sort:{enum:['activity','created','name']},activity:{enum:['all','working','attention']},location:{enum:['all','managed']}})},
 'workspace.receipt':{description:'Inspect the exact durable workspace receipt after a lost response. Unknown mkdir outcomes are never retried.',schema:schema({commandId:string(200)},['commandId'])},
};
const mutations=new Set(['locations.create','workspace.defaults.set','workspace.prepare','workspace.create','workspace.add','workspace.rename','workspace.remove']);
export class WorkspaceCapabilities {
 readonly manifest={version:1,topics:{workspaces:{uri:'amplifier-capability://workspaces/workspaces',version:1,watch:true,scope:'host'}},actions:Object.fromEntries(Object.keys(workspaceActions).map(operation=>[operation,{topic:'workspaces',operation,method:'x-amplifier/capabilityAction'}]))};
 private owner:OwnerConnection;private revision=0;
 constructor(private options:Options){this.owner=new OwnerConnection(options.owner,(method,args)=>{
  // Deliberate callbacks only; no arbitrary method reflection or host private state.
  switch(method){
   case 'listWorkspaces':return options.catalog.listWorkspaces(args);
   case 'getWorkspace':return options.catalog.getWorkspace({id:args.id});
   case 'projectWorkspaces':return options.catalog.projectWorkspaces(args);
   case 'workspaceProjectionStatus':return options.catalog.workspaceProjectionStatus({source:args.source});
   case 'libraryQuery':if(!options.libraryQuery)throw Error('Versioned library query unavailable');return options.libraryQuery(args);
   case 'list':return options.catalog.list({...args,connectionId:args.connectionId,limit:args.limit});
   default:throw Error('Unknown workspace catalog callback');
  }
 },()=>options.onInvalidate?.('workspaces','host'),()=>options.onMayBeIdle?.(),!!options.libraryQuery);}
 readonly quiescenceAccess=Object.fromEntries(['locations.list','workspace.defaults','workspace.list','workspace.inspect','workspace.sessions','workspace.receipt'].map(operation=>[operation,'read' as const]));
 get quiescenceParticipant(){return this.owner.quiescenceParticipant;}
 inspectQuiescence=()=>this.owner.inspectQuiescence();
 actionSchemas=async()=>{
  if(this.options.libraryQuery)return workspaceActions;
  const sessions=structuredClone(workspaceActions['workspace.sessions']);for(const key of ['libraryQueryVersion','sort','activity','location'])delete (sessions.schema.properties as Json)[key];
  return {...workspaceActions,'workspace.sessions':sessions};
 };
 read=async(request:{uri:string;topic:string;scope:string;clientId:string})=>{
  const url=new URL(request.uri);url.search='';url.hash='';
  if(request.topic!=='workspaces'||url.href!==this.manifest.topics.workspaces.uri||!['host','ahp-root://'].includes(request.scope))throw Error('Host-scoped workspace topic required');
  const result=await this.owner.request('snapshot',{clientId:request.clientId});return {topic:'workspaces',scope:'host',revision:++this.revision,data:{workspaces:result}};
 };
 action=async(request:Json,context:Context)=>{
  if(request.version!==1||request.topic!=='workspaces'||!this.manifest.actions[request.operation])throw Error('Unadvertised workspace action');
  const caller=typeof context.session==='string'?context.session:context.session?.uri;
  if(!['host','ahp-root://'].includes(request.channel)&&request.channel!==caller)throw Error('Authenticated workspace scope required');
  let result:Json;
  try{result=await this.owner.request('action',{operation:request.operation,args:request.args??{},commandId:request.commandId,clientId:context.clientId,origin:context.origin??'ui',callerSession:caller});}
  catch(error){const refusal=error as Error&{executed?:boolean;receipt?:Json};if(refusal.executed===false)return {accepted:false,result:{executed:false,reason:refusal.message,receipt:refusal.receipt},updates:[],invalidate:[]};throw error;}
  if(request.operation==='workspace.sessions')result={...result,items:result.items.map((row:Json)=>({resource:row.uri,provider:row.engineId,title:row.title,status:(result.version===1?(row.activity?.runtime==='idle'?1:row.activity?.runtime==='working'?8:row.activity?.runtime==='input-needed'?24:0):1)|(row.isArchived?(1<<6):0),createdAt:row.createdAt,modifiedAt:row.modifiedAt,workingDirectories:[pathToFileURL(row.workingDirectory).href],_meta:{'amplifier.dev/catalog':{runtimeStatus:result.version===1?row.activity?.runtime??'unknown':'unverified',availability:row.availability??'available',...(result.version===1?{activity:row.activity,observationFreshness:result.observationFreshness}:{})},...(row.location?{'amplifier.dev/location':row.location}:{})}}))};
  const changed=mutations.has(request.operation)&&request.operation!=='workspace.prepare';if(changed)this.options.onInvalidate?.('workspaces','host');
  return {accepted:true,result,updates:[],invalidate:changed?['workspaces']:[]};
 };
 close=()=>this.owner.close();
}
export function createWorkspaceCapabilities(options:Options){return new WorkspaceCapabilities(options);}
