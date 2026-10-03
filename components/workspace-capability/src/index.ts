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
 list:(args:{connectionId:string;limit:number;cursor?:string;workingDirectory?:string;search?:string;parentUri?:string;allowedWorkspaceRoots?:string[];includeArchive?:boolean})=>Promise<Json>;
}
export interface Options {owner:Launcher;catalog:WorkspaceCatalog;onInvalidate?:(topic:string,scope:string)=>void;}
const METHODS=new Set(['listWorkspaces','getWorkspace','projectWorkspaces','workspaceProjectionStatus','list']);
/** Two-way owner channel. Calls are never retried after lost transport. */
export class OwnerConnection {
 private process?:ChildProcessWithoutNullStreams;private ready?:Promise<void>;private next=0;private closed=false;private pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void;timer:NodeJS.Timeout}>();
 constructor(private launcher:Launcher,private callback:(method:string,args:Json)=>Promise<any>,private changed:(scope:string)=>void){for(const timeout of [launcher.requestTimeoutMs,launcher.initializeTimeoutMs])if(timeout!==undefined&&(!Number.isInteger(timeout)||timeout<25||timeout>90000))throw Error('Owner timeout must be25–90000ms');}
 private fail(message:string){this.closed=true;for(const entry of this.pending.values()){clearTimeout(entry.timer);entry.reject(Error(message));}this.pending.clear();}
 private write(row:Json){const data=JSON.stringify(row)+'\n';if(Buffer.byteLength(data)>2_000_000)throw Error('Owner frame exceeds2MB');if(this.closed||!this.process)throw Error('Owner unavailable; uncertain work was not replayed');this.process.stdin.write(data,error=>{if(error)this.fail('Owner transport failed; outcome unknown.');});}
 private start(){
  if(this.closed)throw Error('Owner connection is closed; no automatic replay.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stderr.on('data',()=>{});child.on('error',()=>this.fail('Workspace owner failed to start'));child.on('exit',()=>this.fail('Workspace owner exited; uncertain commands not replayed'));
   let bytes=0;child.stdout.on('data',(chunk:Buffer)=>{for(const b of chunk){bytes=b===10?0:bytes+1;if(bytes>2_000_000){this.fail('Owner frame exceeded limit');child.kill();return;}}});
   createInterface({input:child.stdout}).on('line',line=>{void this.receive(line);});
   try{const value=await this.send('initialize',{},this.launcher.initializeTimeoutMs??15000);if(value.protocolVersion!==1)throw Error('Unsupported workspace owner');}catch(error){this.fail('Workspace owner initialization failed; no automatic retry.');child.kill();throw error;}
  })();return this.ready;
 }
 private async receive(line:string){
  let row:Json;try{row=JSON.parse(line);}catch{this.fail('Invalid owner response');this.process?.kill();return;}
  if(row.method==='owner/changed'){this.changed(row.params.session);return;}
  if(row.method){
   const method=String(row.method).replace(/^catalog\//,'');
   try{if(!String(row.method).startsWith('catalog/')||!METHODS.has(method))throw Error('Unknown host callback');const result=await this.callback(method,row.params??{});this.write({jsonrpc:'2.0',id:row.id,result:result??null});}
   catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:String(error instanceof Error?error.message:error)}});}return;
  }
  const entry=this.pending.get(row.id);if(!entry)return;this.pending.delete(row.id);clearTimeout(entry.timer);row.error?entry.reject(Object.assign(Error(row.error.message),row.error.data??{})):entry.resolve(row.result);
 }
 private send(method:string,params:Json,timeoutMs=this.launcher.requestTimeoutMs??90000){if(this.pending.size>=64)throw Error('Owner request capacity reached');const id=++this.next;return new Promise<any>((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(Object.assign(Error('Workspace owner reply timed out; outcome unknown and not replayed.'),{code:'unknown_outcome'}));},timeoutMs);timer.unref();this.pending.set(id,{resolve,reject,timer});try{this.write({jsonrpc:'2.0',id,method,params});}catch(e){clearTimeout(timer);this.pending.delete(id);reject(e as Error);}});}
 async request(method:string,args:Json){await this.start();return this.send(method,args);}
 async close(){if(!this.process){this.closed=true;return;}this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},4000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});this.fail('Owner closed');}
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
 'workspace.sessions':{description:'List a bounded page of root sessions in an existing workspace. Children require an explicit parent URI.',schema:schema({id,...page,parentUri:string(8192)},['id'])},
 'workspace.receipt':{description:'Inspect the exact durable workspace receipt after a lost response. Unknown mkdir outcomes are never retried.',schema:schema({commandId:string(200)},['commandId'])},
};
const mutations=new Set(['workspace.prepare','workspace.create','workspace.add','workspace.rename','workspace.remove']);
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
   case 'list':return options.catalog.list({...args,connectionId:args.connectionId,limit:args.limit});
   default:throw Error('Unknown workspace catalog callback');
  }
 },()=>options.onInvalidate?.('workspaces','host'));}
 actionSchemas=async()=>workspaceActions;
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
  if(request.operation==='workspace.sessions')result={...result,items:result.items.map((row:Json)=>({resource:row.uri,provider:row.engineId,title:row.title,status:1,createdAt:row.createdAt,modifiedAt:row.modifiedAt,workingDirectories:[pathToFileURL(row.workingDirectory).href],_meta:{'amplifier.dev/catalog':{runtimeStatus:'unverified',availability:row.availability??'available'}}}))};
  const changed=mutations.has(request.operation)&&request.operation!=='workspace.prepare';if(changed)this.options.onInvalidate?.('workspaces','host');
  return {accepted:true,result,updates:[],invalidate:changed?['workspaces']:[]};
 };
 close=()=>this.owner.close();
}
export function createWorkspaceCapabilities(options:Options){return new WorkspaceCapabilities(options);}
