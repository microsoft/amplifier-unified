import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
export type Json=Record<string,any>;
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
export interface Launcher {command:string;args?:string[];env?:Record<string,string>;cwd?:string;}
export interface Options {
 owner:Launcher;
 withSessionWorkspace:<T>(uri:string,expected:Json,callback:(context:Json)=>Promise<T>)=>Promise<T>;
 inspectSession:(uri:string,context?:{clientId:string})=>Promise<Json>;
 authorizePublication?:(request:{session:string;operation:string;args:Json;commandId:string;clientId:string})=>Promise<{approved:boolean;approvalId?:string}>;
 onInvalidate?:(topic:string,scope:string)=>void;
}
const METHODS=new Set(['inspectSession','authorizePublication']);
const actions=['list','build','preview','review','deploy','rollback','status','logs','stop','remove','receipt','release','command','target.list','target.save','target.inspect','target.select','target.remove'].map(name=>'publishing.'+name);
/** Two-way owner channel. Calls are never retried after lost transport. */
export class OwnerConnection {
 private process?:ChildProcessWithoutNullStreams;private ready?:Promise<void>;private next=0;private closed=false;private pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void}>();
 constructor(private launcher:Launcher,private callback:(method:string,args:Json)=>Promise<any>,private changed:(scope:string)=>void){}
 private fail(message:string){this.closed=true;for(const entry of this.pending.values())entry.reject(Error(message));this.pending.clear();}
 private write(row:Json){const data=JSON.stringify(row)+'\n';if(Buffer.byteLength(data)>4_000_000)throw Error('Owner frame exceeds4MB');if(this.closed||!this.process)throw Error('Owner unavailable; uncertain work was not replayed');this.process.stdin.write(data,error=>{if(error)this.fail('Owner transport failed; outcome unknown.');});}
 private start(){
  if(this.closed)throw Error('Owner connection is closed; no automatic replay.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stderr.on('data',()=>{});child.on('error',()=>this.fail('Publishing owner failed to start'));child.on('exit',()=>this.fail('Publishing owner exited; uncertain commands not replayed'));
   let bytes=0;child.stdout.on('data',(chunk:Buffer)=>{for(const b of chunk){bytes=b===10?0:bytes+1;if(bytes>4_000_000){this.fail('Owner frame exceeded limit');child.kill();return;}}});
   createInterface({input:child.stdout}).on('line',line=>{void this.receive(line);});
   const value=await this.send('initialize',{});if(value.protocolVersion!==1)throw Error('Unsupported publishing owner');
  })();return this.ready;
 }
 private async receive(line:string){
  let row:Json;try{row=JSON.parse(line);}catch{this.fail('Invalid owner response');this.process?.kill();return;}
  if(row.method==='owner/changed'){this.changed(row.params.session);return;}
  if(row.method){
   const method=String(row.method).replace(/^host\//,'');
   try{if(!String(row.method).startsWith('host/')||!METHODS.has(method))throw Error('Unknown host callback');const result=await this.callback(method,row.params??{});this.write({jsonrpc:'2.0',id:row.id,result:result??null});}
   catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:String(error instanceof Error?error.message:error)}});}return;
  }
  const entry=this.pending.get(row.id);if(!entry)return;this.pending.delete(row.id);row.error?entry.reject(Object.assign(Error(row.error.message),row.error.data??{})):entry.resolve(row.result);
 }
 private send(method:string,params:Json){if(this.pending.size>=64)throw Error('Owner request capacity reached');const id=++this.next;return new Promise<any>((resolve,reject)=>{this.pending.set(id,{resolve,reject});try{this.write({jsonrpc:'2.0',id,method,params});}catch(e){this.pending.delete(id);reject(e as Error);}});}
 async request(method:string,args:Json){await this.start();return this.send(method,args);}
 async close(){if(!this.process){this.closed=true;return;}this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},4000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});this.fail('Owner closed');}
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
 },scope=>options.onInvalidate?.('publishing',scope));}
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
  return {accepted:true,result,updates:[],invalidate:[{topic:'publishing',scope:request.channel}]};
 };
 close=async()=>this.owner.close();
}
export function createPublishingCapabilities(options:Options){return new PublishingCapabilities(options);}
