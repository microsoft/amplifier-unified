import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
export type Json=Record<string,any>;
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
export interface Launcher {command:string;args?:string[];env?:Record<string,string>;cwd?:string;}
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
 authorizeTransfer?:(args:Json)=>Promise<Json>;
 onInvalidate?:(topic:string,scope:string)=>void;
}
const METHODS=new Set(['inspectSession','beginTransfer','commitTransfer','cancelTransfer','adoptTransferredSession','nativeTransfer','exportTransferEvidence','stageTransferEvidence','activateTransferEvidence','authorizeTransfer']);
const actions=['inspect','review','export','stage','release','activate','cancel','discard','evidence','receipt','command','reconcile'].map(name=>'portability.'+name);
/** Two-way owner channel. Calls are never retried after lost transport. */
export class OwnerConnection {
 private process?:ChildProcessWithoutNullStreams;private ready?:Promise<void>;private next=0;private closed=false;private pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void}>();
 constructor(private launcher:Launcher,private callback:(method:string,args:Json)=>Promise<any>,private changed:(scope:string)=>void){}
 private fail(message:string){this.closed=true;for(const entry of this.pending.values())entry.reject(Error(message));this.pending.clear();}
 private write(row:Json){const data=JSON.stringify(row)+'\n';if(Buffer.byteLength(data)>48_000_000)throw Error('Owner frame exceeds48MB');if(this.closed||!this.process)throw Error('Owner unavailable; uncertain work was not replayed');this.process.stdin.write(data,error=>{if(error)this.fail('Owner transport failed; outcome unknown.');});}
 private start(){
  if(this.closed)throw Error('Owner connection is closed; no automatic replay.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stderr.on('data',()=>{});child.on('error',()=>this.fail('Portability owner failed to start'));child.on('exit',()=>this.fail('Portability owner exited; uncertain commands not replayed'));
   let bytes=0;child.stdout.on('data',(chunk:Buffer)=>{for(const b of chunk){bytes=b===10?0:bytes+1;if(bytes>48_000_000){this.fail('Owner frame exceeded limit');child.kill();return;}}});
   createInterface({input:child.stdout}).on('line',line=>{void this.receive(line);});
   const value=await this.send('initialize',{});if(value.protocolVersion!==1)throw Error('Unsupported portability owner');
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
export class PortabilityCapabilities {
 readonly manifest={version:1,topics:{portability:{uri:'amplifier-capability://portability/portability',version:1,watch:true,scope:'host'}},actions:Object.fromEntries(actions.map(operation=>[operation,{topic:'portability',operation,method:'x-amplifier/capabilityAction'}]))};
 private owner:OwnerConnection;private revision=0;
 constructor(private options:Options){this.owner=new OwnerConnection(options.owner,async(method,params)=>{
  if(method==='inspectSession')return options.inspectSession(params.session);
  if(['beginTransfer','commitTransfer','cancelTransfer'].includes(method)){const {session,...args}=params;return options[method as 'beginTransfer'](session,args);}
  if(method==='authorizeTransfer'){
   if(options.authorizeTransfer)return options.authorizeTransfer(params);
   if(params.origin==='ui')return {approved:true,kind:'explicit-ui',commandId:params.commandId};
   throw Error('Explicit transfer approval is required for agent-origin requests');
  }
  if(['adoptTransferredSession','nativeTransfer','exportTransferEvidence','stageTransferEvidence','activateTransferEvidence'].includes(method))return options[method as 'nativeTransfer'](params);
  throw Error('Unknown portability authority callback');
 },_scope=>options.onInvalidate?.('portability','host'));}
 private async scope(channel:string,context:Context){
  if(channel==='ahp-root://' || channel==='host')return 'host';
  if(!channel?.startsWith('ahp-session:/'))throw Error('Authenticated root or session scope required');
  const selected=typeof context.session==='string'?context.session:context.session?.uri;
  if(selected&&selected!==channel)throw Error('Session scope mismatch');
  await this.options.inspectSession(channel,{clientId:context.clientId});return channel;
 }
 actionSchemas=async()=>this.owner.request('actions',{});
 read=async(request:{uri:string;topic:string;scope:string;clientId:string})=>{
  const url=new URL(request.uri);url.search='';url.hash='';
  if(request.topic!=='portability'||url.href!==this.manifest.topics.portability.uri)throw Error('Unknown portability topic');
  const scope=await this.scope(request.scope,{clientId:request.clientId});const data=await this.owner.request('snapshot',{session:scope});
  if(Buffer.byteLength(JSON.stringify(data))>768*1024)throw Error('Transfer page exceeds topic bound');
  return {topic:'portability',scope,revision:++this.revision,data:{portability:data}};
 };
 action=async(request:Json,context:Context)=>{
  if(request.version!==1||request.topic!=='portability'||!this.manifest.actions[request.operation])throw Error('Unadvertised portability operation');
  const selected=request.args?.sessionId;
  const channel=(request.channel==='ahp-root://' || request.channel==='host') && selected!==undefined ? selected : request.channel;
  const scope=await this.scope(channel,context);
  const result=await this.owner.request('action',{session:scope,operation:request.operation,args:request.args??{},commandId:request.commandId,origin:context.origin??'ui',clientId:context.clientId});
  if(scope!=='host')this.options.onInvalidate?.('portability','host');
  return {accepted:true,result,updates:[],invalidate:['portability']};
 };
 close=async()=>this.owner.close();
}
export function createPortabilityCapabilities(options:Options){return new PortabilityCapabilities(options);}
export {TransferConnection} from './native.js';
